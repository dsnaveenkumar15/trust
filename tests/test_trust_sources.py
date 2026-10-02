import httpx
import pytest

from trust import store
from trust.sources import REGISTRY_URL, SourceError, registry_repos, repo_from_url


@pytest.mark.parametrize("url,repo", [
    ("https://github.com/Acme/Weather", "acme/weather"),
    ("https://github.com/acme/weather.git", "acme/weather"),
    ("https://github.com/acme/mono/tree/main/servers/x", "acme/mono"),
    ("http://www.github.com/acme/weather/", "acme/weather"),
    ("https://gitlab.com/acme/weather", None),
    ("https://github.com/acme", None),
    ("https://github.com/../etc", None),
])
def test_repo_from_url(url, repo):
    assert repo_from_url(url) == repo


def registry_client(pages):
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        cursor = request.url.params.get("cursor")
        return httpx.Response(200, json=pages[cursor])

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def server(name, repo_url=None, source="github"):
    entry = {"server": {"name": name}}
    if repo_url:
        entry["server"]["repository"] = {"url": repo_url, "source": source}
    return entry


def test_registry_repos_paginates_dedupes_and_filters():
    pages = {
        None: {"servers": [server("a", "https://github.com/acme/one"), server("b"),
                           server("c", "https://gitlab.com/x/y", "gitlab")],
               "metadata": {"nextCursor": "c1"}},
        "c1": {"servers": [server("d", "https://github.com/Acme/One.git"), server("e", "https://github.com/acme/two")],
               "metadata": {}},
    }
    client, calls = registry_client(pages)
    assert registry_repos(client) == ["acme/one", "acme/two"]
    assert calls[0]["version"] == "latest" and calls[1]["cursor"] == "c1"


def test_registry_repos_stops_at_max_pages():
    pages = {None: {"servers": [server("a", "https://github.com/acme/one")], "metadata": {"nextCursor": "c1"}},
             "c1": {"servers": [server("b", "https://github.com/acme/two")], "metadata": {"nextCursor": "c2"}}}
    client, _ = registry_client(pages)
    assert registry_repos(client, max_pages=1) == ["acme/one"]


def test_registry_outage_raises_source_error():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(SourceError):
        registry_repos(client)
    assert REGISTRY_URL.startswith("https://")


def test_server_registry_upsert_merges_sources_and_keeps_max_stars(conn):
    store.ensure_schema(conn)
    store.upsert_server(conn, "acme/one", "registry", 0, now=1.0)
    store.upsert_server(conn, "acme/one", "github", 12, now=2.0)
    store.upsert_server(conn, "acme/one", "github", 3, now=3.0)
    row = store.get_server(conn, "acme/one")
    assert (row["sources"], row["stars"], row["first_seen"]) == ("github,registry", 12, 1.0)


def test_servers_without_audit_orders_by_stars_and_skips_checked(conn):
    store.ensure_schema(conn)
    for repo, stars in (("a/low", 1), ("a/high", 50), ("a/mid", 10), ("a/checked", 99)):
        store.upsert_server(conn, repo, "github", stars, now=1.0)
    store.mark_checked(conn, "a/checked", "f" * 40, None, False, now=2.0)
    assert [r["repo"] for r in store.servers_without_audit(conn, 2)] == ["a/high", "a/mid"]


def test_tools_hash_ignores_order_and_file_positions():
    a = [{"name": "x", "description": "d", "file": "a.py", "line": 1}, {"name": "y", "description": "", "file": "b.py",
                                                                        "line": 9}]
    b = [dict(a[1], line=3), dict(a[0], file="moved.py")]
    assert store.tools_hash(a) == store.tools_hash(b)
    assert store.tools_hash(a) != store.tools_hash([a[0]])


def test_trust_still_exports_the_core_repo_from_url():
    from core.github import repo_from_url
    from trust.sources import repo_from_url as trust_repo_from_url
    assert trust_repo_from_url is repo_from_url
