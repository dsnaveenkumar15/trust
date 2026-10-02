import json

import httpx
import pytest

from core.events import emit
from core.github import GitHubError, GitHubRateLimited, RepoNotFound
from core.worker import run_once
from trust import store
from trust.discovery import collect, enqueue_new
from trust.jobs import build_job_types


class FakeGitHub:
    def __init__(self, search=None, heads=None, missing=(), limit_on=None):
        self.search = search or {}
        self.heads = heads or {}
        self.missing = set(missing)
        self.limit_on = limit_on

    def search_repositories(self, query, per_page=50):
        return self.search.get(query, [])

    def repo_meta(self, repo):
        if repo in self.missing:
            raise RepoNotFound(repo)
        return {"default_branch": "main", "stars": 0, "license": "MIT", "archived": False, "pushed_at": None,
                "html_url": ""}

    def head_sha(self, repo, ref):
        if repo in getattr(self, 'blocked', ()):
            raise GitHubError('451 unavailable for legal reasons')
        if repo == self.limit_on:
            raise GitHubRateLimited("limit", 0.0)
        return self.heads.get(repo, "a" * 40)


def registry(servers=(), status=200):
    payload = {"servers": [{"server": {"repository": {"url": f"https://github.com/{r}", "source": "github"}}}
                           for r in servers], "metadata": {}}
    return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(status, json=payload)))


def queued_audits(conn):
    return [(json.loads(r["payload"])["repo"], r["priority"]) for r in
            conn.execute("SELECT payload, priority FROM jobs WHERE job_type='trust.audit' ORDER BY priority DESC")]


@pytest.fixture
def db(conn):
    store.ensure_schema(conn)
    return conn


def test_collect_merges_sources_and_skips_archived(db):
    github = FakeGitHub(search={"topic:mcp-server": [{"repo": "acme/one", "stars": 40, "archived": False},
                                                     {"repo": "acme/dead", "stars": 99, "archived": True}]})
    result = collect(db, github, registry(["acme/one", "acme/two"]), now=1.0)
    assert result == {"known": 2, "registry_error": None}
    assert store.get_server(db, "acme/one")["sources"] == "github,registry"
    assert store.get_server(db, "acme/dead") is None


def test_registry_outage_does_not_stop_discovery(db):
    github = FakeGitHub(search={"topic:mcp-server": [{"repo": "acme/one", "stars": 1, "archived": False}]})
    result = collect(db, github, registry(status=503), now=1.0)
    assert result["known"] == 1 and "503" in result["registry_error"]


def test_radar_events_feed_discovery(db):
    emit(db, "radar.mcp_server_discovered", {"repo": "Acme/Radar-Find"})
    emit(db, "radar.mcp_server_discovered", {"repo": "../etc/passwd"})
    collect(db, FakeGitHub(), registry(), now=1.0)
    assert store.get_server(db, "acme/radar-find")["sources"] == "radar"
    assert db.execute("SELECT count(*) FROM trust_servers").fetchone()[0] == 1


def test_enqueue_new_orders_by_stars_skips_missing_and_never_requeues(db):
    for repo, stars in (("a/low", 1), ("a/high", 500), ("a/gone", 50)):
        store.upsert_server(db, repo, "github", stars, now=1.0)
    result = enqueue_new(db, FakeGitHub(missing={"a/gone"}), now=2.0)
    assert result == {"queued": ["a/high", "a/low"], "missing": ["a/gone"], "errors": [], "backlog": 0}
    assert [r for r, _ in queued_audits(db)] == ["a/high", "a/low"]
    assert enqueue_new(db, FakeGitHub(), now=3.0)["queued"] == []


def test_rate_limit_mid_run_keeps_queued_work_without_duplicates(db):
    for repo, stars in (("a/first", 9), ("a/second", 1)):
        store.upsert_server(db, repo, "github", stars, now=1.0)
    with pytest.raises(GitHubRateLimited):
        enqueue_new(db, FakeGitHub(limit_on="a/second"), now=2.0)
    assert [r for r, _ in queued_audits(db)] == ["a/first"]
    assert enqueue_new(db, FakeGitHub(), now=3.0)["queued"] == ["a/second"]
    assert sorted(r for r, _ in queued_audits(db)) == ["a/first", "a/second"]


def test_discover_job_end_to_end(db):
    github = FakeGitHub(search={"topic:mcp-server": [{"repo": "acme/one", "stars": 5, "archived": False}]})
    registry_types = build_job_types(db, github=github, use_llm=False, registry_client=registry(["acme/two"]))
    from trust.jobs import enqueue_discovery
    enqueue_discovery(db, now=0.0)
    assert run_once(db, registry_types, {"trust": 1.0}, "w") == "done"
    assert sorted(r for r, _ in queued_audits(db)) == ["acme/one", "acme/two"]


def test_one_broken_repo_does_not_block_the_rest(db):
    for repo, stars in (("a/blocked", 900), ("a/fine", 1)):
        store.upsert_server(db, repo, "github", stars, now=1.0)
    github = FakeGitHub()
    github.blocked = {"a/blocked"}
    result = enqueue_new(db, github, now=2.0)
    assert result["queued"] == ["a/fine"] and result["errors"] == ["a/blocked"]
    assert store.get_server(db, "a/blocked")["last_checked"] == 2.0


def test_backlog_cap_stops_new_discovery_queueing(db, monkeypatch):
    from trust import discovery
    monkeypatch.setattr(discovery, "MAX_QUEUED_AUDITS", 2)
    for i in range(5):
        store.upsert_server(db, f"a/r{i}", "github", 10 - i, now=1.0)
    first = enqueue_new(db, FakeGitHub(), now=2.0)
    assert len(first["queued"]) == 2
    second = enqueue_new(db, FakeGitHub(), now=3.0)
    assert second["queued"] == [] and second["backlog"] == 2


def publish_radar_digests(root, *digests):
    from core.publish import publish
    files = {f"digests/{d['date']}.json": json.dumps(d).encode() for d in digests}
    files["index.json"] = json.dumps({"digests": [{"date": d["date"]} for d in digests]}).encode()
    publish(root, "radar", "digests", files, {})


def test_published_radar_digests_feed_discovery_without_a_shared_database(db, tmp_path):
    publish_radar_digests(tmp_path, {"date": "2026-10-02", "new_servers": [{"repo": "Acme/New-One"}, {"repo": "../etc"}, {"repo": 3}, "junk"]},
                          {"date": "2026-10-01", "new_servers": [{"repo": "acme/old-one"}]})
    collect(db, FakeGitHub(), registry(), now=1.0, radar_root=tmp_path)
    assert store.get_server(db, "acme/new-one")["sources"] == "radar" and store.get_server(db, "acme/old-one")["sources"] == "radar"
    assert db.execute("SELECT count(*) FROM trust_servers").fetchone()[0] == 2
    collect(db, FakeGitHub(), registry(), now=2.0, radar_root=tmp_path)             # a second look changes nothing
    assert db.execute("SELECT count(*) FROM trust_servers").fetchone()[0] == 2


@pytest.mark.parametrize("digest", [{"date": "2026-10-02"}, {"date": "2026-10-02", "new_servers": None}, {"date": "2026-10-02", "new_servers": "x"},
                                    {"date": "2026-10-02", "new_servers": [None, 1, {"repo": None}]}])
def test_odd_or_missing_radar_data_is_ignored(db, tmp_path, digest):
    publish_radar_digests(tmp_path, digest)
    collect(db, FakeGitHub(), registry(), now=1.0, radar_root=tmp_path)
    collect(db, FakeGitHub(), registry(), now=1.0, radar_root=tmp_path / "nowhere")
    collect(db, FakeGitHub(), registry(), now=1.0, radar_root=None)
    assert db.execute("SELECT count(*) FROM trust_servers").fetchone()[0] == 0


def test_the_discover_job_reads_radar_data_from_the_configured_root(db, tmp_path, monkeypatch):
    publish_radar_digests(tmp_path, {"date": "2026-10-02", "new_servers": [{"repo": "acme/from-radar"}]})
    monkeypatch.setenv("RADAR_DATA_ROOT", str(tmp_path))
    types = build_job_types(db, github=FakeGitHub(), use_llm=False, registry_client=registry())
    from trust.jobs import enqueue_discovery
    enqueue_discovery(db, now=0.0)
    assert run_once(db, types, {"trust": 1.0}, "w") == "done"
    assert [r for r, _ in queued_audits(db)] == ["acme/from-radar"]
