import json

import pytest
from fastapi.testclient import TestClient

from core.publish import publish
from tests.webutil import make_settings
from web.app import create_app

EVIL = "<script>alert(1)</script>"


def server_entry(repo, grade="B", **overrides):
    entry = {
        "repo": repo, "slug": repo.replace("/", "--"), "sha": "a" * 40, "status": "graded", "grade": grade,
        "score": 94, "categories": {"injection": 100, "capabilities": 68}, "audited_at": 1_790_000_000.0,
        "stale": False, "under_disclosure": False, "disclosure_ends": None,
        "counts": {"critical": 0, "high": 1, "medium": 0, "low": 0}, "stars": 10, "tools": ["run"],
        "provenance": {"audit_id": 7, "ruleset": "1.0.0", "scoring": "1.0.0", "prompt": "trust.review@1",
                       "model": "sonnet"},
        "findings": [{"rule": "shell-exec", "category": "capabilities", "severity": "high", "confidence": "high",
                      "file": "server.py", "line": 12, "quote": "subprocess.run(" + EVIL + ")",
                      "message": "Runs shell commands " + EVIL, "tool": "run", "source": "rule",
                      "explanation": "Reviewer says " + EVIL}],
    }
    entry.update(overrides)
    return entry


def publish_entries(root, entries):
    index = {"count": len(entries), "servers": [{k: e[k] for k in ("repo", "slug", "status", "grade", "score",
                                                                     "stale", "under_disclosure", "audited_at",
                                                                     "stars", "counts")} for e in entries]}
    files = {"index.json": json.dumps(index).encode(), "methodology.json": b"{}"}
    for e in entries:
        files[f"servers/{e['slug']}.json"] = json.dumps(e).encode()
    return publish(root, "trust", "directory", files, {})


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path, dispute_url="https://example.org/dispute")


def client(settings, host="trust.localhost"):
    return TestClient(create_app(settings), base_url=f"http://{host}")


def test_empty_state_before_first_publish(settings):
    r = client(settings).get("/")
    assert r.status_code == 200 and "does not list other people" in r.text


def test_directory_lists_filters_and_sorts(settings):
    publish_entries(settings.data_root, [server_entry("acme/zeta", "B"), server_entry("acme/alpha", "A"),
                                         server_entry("acme/go", None, status="unsupported", score=None)])
    html = client(settings).get("/").text
    assert html.index("acme/alpha") < html.index("acme/zeta") < html.index("acme/go")
    assert "acme/zeta" not in client(settings).get("/?grade=A").text
    assert "acme/go" in client(settings).get("/?grade=unscored").text
    assert "acme/alpha" not in client(settings).get("/?q=zet").text


def test_server_page_escapes_repo_text_and_links_evidence(settings):
    publish_entries(settings.data_root, [server_entry("acme/zeta")])
    html = client(settings).get("/servers/acme--zeta").text
    assert EVIL not in html and "&lt;script&gt;" in html
    assert 'href="https://github.com/acme/zeta/blob/' + "a" * 40 + '/server.py#L12"' in html
    assert "audit #7" in html and "https://example.org/dispute" in html
    assert '<meter min="0" max="100"' in html


def test_embargoed_server_shows_disclosure_notice(settings):
    publish_entries(settings.data_root, [server_entry("acme/zeta", findings=[], under_disclosure=True,
                                                      disclosure_ends=1_800_000_000.0)])
    html = client(settings).get("/servers/acme--zeta").text
    assert "Under disclosure" in html and "subprocess" not in html


def test_unknown_or_traversal_slug_is_404(settings):
    publish_entries(settings.data_root, [server_entry("acme/zeta")])
    c = client(settings)
    assert c.get("/servers/nope").status_code == 404
    assert c.get("/servers/..%2Findex").status_code == 404


def test_damaged_current_pointer_falls_back_to_last_good_release(settings):
    from pathlib import Path
    publish_entries(settings.data_root, [server_entry("acme/zeta")])
    (Path(settings.data_root) / "trust" / "directory" / "CURRENT").write_text("missing-release")
    assert "acme/zeta" in client(settings).get("/").text


def test_methodology_page(settings):
    html = client(settings).get("/methodology").text
    assert "hidden-instructions" in html and "0.3" in html and "precision" in html


def test_trust_routes_do_not_exist_on_other_planets(settings):
    c = client(settings, "radar.localhost")
    assert c.get("/methodology").status_code == 404
    assert c.get("/servers/acme--zeta").status_code == 404
    assert client(settings, "blueprints.localhost").get("/methodology").status_code == 404


def test_home_preview_runs_the_real_rules_on_the_example(settings):
    from web.trust_site import sample_check
    sample = sample_check()
    assert [f["rule"] for f in sample["findings"]] == ["hidden-instructions"]
    assert [t["flagged"] for t in sample["tools"]] == [False, True]
    html = client(settings).get("/").text
    assert "Hidden instructions in" in html and "<mark>&lt;IMPORTANT&gt;" in html
