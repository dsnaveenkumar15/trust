from urllib.parse import unquote

import httpx
import pytest

from core import github as gh
from core.github import GitHub, GitHubRateLimited, RepoNotFound
from core.llm import RateLimited

REPO_JSON = {"default_branch": "main", "stargazers_count": 42, "license": {"spdx_id": "MIT"}, "archived": False,
             "pushed_at": "2026-09-01T00:00:00Z", "html_url": "https://github.com/acme/weather"}
FILES = {"server.py": b"print('hi')\n", "README.md": b"# hi\n", "big.py": b"x", "bin.py": b"\x00\x01",
         "latin.py": "café".encode("latin-1"), "node_modules/x.js": b"1"}


def handler(request):
    url = str(request.url)
    if url.endswith("/repos/acme/weather"):
        return httpx.Response(200, json=REPO_JSON, headers={"etag": '"r1"'})
    if url.endswith("/commits/main"):
        return httpx.Response(200, json={"sha": "abc123"})
    if "/git/trees/abc123" in url:
        tree = [{"path": p, "type": "blob", "size": 300_000 if p == "big.py" else len(c)} for p, c in FILES.items()]
        return httpx.Response(200, json={"truncated": False, "tree": tree + [{"path": "src", "type": "tree"}]})
    if url.startswith("https://raw.githubusercontent.com/acme/weather/abc123/"):
        return httpx.Response(200, content=FILES[unquote(url.split("/abc123/", 1)[1])])
    return httpx.Response(404)


def make(conn, fn=handler, token=None):
    return GitHub(conn, token=token, client=httpx.Client(transport=httpx.MockTransport(fn)))


def test_repo_meta_mapping(conn):
    assert make(conn).repo_meta("acme/weather") == {
        "default_branch": "main", "stars": 42, "license": "MIT", "archived": False,
        "pushed_at": "2026-09-01T00:00:00Z", "html_url": "https://github.com/acme/weather"}


def test_snapshot_filters_skips_and_pins_sha(conn):
    snap = make(conn).snapshot("acme/weather", wanted=lambda p: not p.startswith("node_modules"))
    assert snap["sha"] == "abc123"
    assert set(snap["files"]) == {"server.py", "README.md", "latin.py"}
    assert snap["files"]["latin.py"] == "caf�"          # one bad byte must not hide a whole file
    assert set(snap["skipped"]) == {"big.py", "bin.py"}
    assert snap["truncated"] is False and snap["meta"]["license"] == "MIT"


def test_snapshot_truncates_at_file_cap(conn, monkeypatch):
    monkeypatch.setattr(gh, "MAX_FILES", 1)
    snap = make(conn).snapshot("acme/weather", wanted=lambda p: p.endswith(".md"))
    assert snap["truncated"] is False and list(snap["files"]) == ["README.md"]
    snap = make(conn).snapshot("acme/weather", wanted=lambda p: p in ("README.md", "server.py"))
    assert snap["truncated"] is True and len(snap["files"]) == 1


def test_etag_served_from_cache_on_304(conn):
    seen = []

    def fn(request):
        seen.append(request.headers.get("if-none-match"))
        if request.headers.get("if-none-match") == '"r1"':
            return httpx.Response(304)
        return httpx.Response(200, json=REPO_JSON, headers={"etag": '"r1"'})

    client = make(conn, fn)
    first = client.repo_meta("acme/weather")
    assert client.repo_meta("acme/weather") == first
    assert seen == [None, '"r1"']


def test_rate_limit_is_a_deferrable_error(conn):
    def fn(request):
        return httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1234"})

    with pytest.raises(GitHubRateLimited) as info:
        make(conn, fn).repo_meta("acme/weather")
    assert info.value.reset_at == 1234 and isinstance(info.value, RateLimited)


def test_not_found(conn):
    with pytest.raises(RepoNotFound):
        make(conn).repo_meta("acme/missing")


@pytest.mark.parametrize("repo,sha", [("../etc", None), ("acme", None), ("a/b/c", None), ("acme/weather", "main;rm")])
def test_rejects_unsafe_repo_or_sha(conn, repo, sha):
    with pytest.raises(ValueError):
        make(conn).snapshot(repo, sha)


def test_token_sent_as_bearer(conn):
    seen = {}

    def fn(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=REPO_JSON)

    make(conn, fn, token="t0k").repo_meta("acme/weather")
    assert seen["auth"] == "Bearer t0k"


def test_search_repositories_maps_items(conn):
    seen = {}

    def fn(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"items": [
            {"full_name": "Acme/Weather", "stargazers_count": 7, "archived": False},
            {"full_name": "old/thing", "stargazers_count": 1, "archived": True}]})

    found = make(conn, fn).search_repositories("topic:mcp-server", per_page=2)
    assert found == [{"repo": "acme/weather", "stars": 7, "archived": False},
                     {"repo": "old/thing", "stars": 1, "archived": True}]
    assert "/search/repositories" in seen["url"] and "mcp-server" in seen["url"]
    assert "sort=stars" in seen["url"] and "per_page=2" in seen["url"]


def test_releases_skips_drafts_and_normalises(conn):
    def fn(request):
        assert "/repos/acme/weather/releases" in str(request.url)
        return httpx.Response(200, json=[
            {"tag_name": "v1.2.0", "name": "1.2.0", "html_url": "https://github.com/acme/weather/releases/tag/v1.2.0",
             "published_at": "2026-09-30T10:00:00Z", "body": "notes", "prerelease": False, "draft": False},
            {"tag_name": "v1.3.0", "draft": True, "html_url": "x"},
            {"tag_name": "v1.3.0rc1", "name": None, "html_url": "https://github.com/acme/weather/releases/tag/v1.3.0rc1",
             "created_at": "2026-10-01T10:00:00Z", "body": None, "prerelease": True, "draft": False}])
    releases = make(conn, fn).releases("acme/weather")
    assert [r["tag"] for r in releases] == ["v1.2.0", "v1.3.0rc1"]
    assert releases[1]["published"] == "2026-10-01T10:00:00Z" and releases[1]["body"] == "" and releases[1]["prerelease"]


def test_repo_from_url_lives_in_core():
    from core.github import repo_from_url
    assert repo_from_url("https://github.com/Acme/Weather.git") == "acme/weather"
