"""Find MCP servers to audit and queue the ones never audited, most-starred first."""
import logging
import math
import re

from core import events
from core.github import GitHubError, RepoNotFound
from trust import store
from trust.sources import SourceError, registry_repos

log = logging.getLogger("trust.discovery")
GITHUB_QUERIES = ("topic:mcp-server", "topic:model-context-protocol")
DISCOVER_BATCH = 25
MAX_QUEUED_AUDITS = 50   # discovery stops topping up while this many audits wait
_REPO = re.compile(r"[a-z0-9_.-]+/[a-z0-9_.-]+")


def _valid(repo) -> str | None:
    if not isinstance(repo, str):
        return None
    repo = repo.strip().lower()
    return repo if _REPO.fullmatch(repo) and ".." not in repo else None


def collect(conn, github, registry_client, now: float) -> dict:
    registry_error = None
    try:
        for repo in registry_repos(registry_client):
            store.upsert_server(conn, repo, "registry", 0, now)
    except SourceError as exc:
        registry_error = str(exc)
        log.warning("registry unavailable: %s", exc)
    for query in GITHUB_QUERIES:
        for item in github.search_repositories(query):
            if not item["archived"] and _valid(item["repo"]):
                store.upsert_server(conn, item["repo"], "github", item["stars"], now)

    def from_radar(event):
        repo = _valid(event.payload.get("repo"))
        if repo:
            store.upsert_server(conn, repo, "radar", 0, now)

    events.consume(conn, "trust.discovery", from_radar, types=["radar.mcp_server_discovered"])
    known = conn.execute("SELECT count(*) FROM trust_servers").fetchone()[0]
    return {"known": known, "registry_error": registry_error}


def enqueue_new(conn, github, now: float, batch: int = DISCOVER_BATCH) -> dict:
    """Queue audits for never-audited servers. Each repo is marked checked as soon as it is handled, so a rate
    limit part-way through loses nothing and never queues a repo twice."""
    from trust.jobs import JOB_TYPE, enqueue_audit   # local import: trust.jobs imports this module
    queued, missing, errors = [], [], []
    backlog = conn.execute("SELECT count(*) FROM jobs WHERE job_type=? AND state IN ('queued', 'running')",
                           (JOB_TYPE,)).fetchone()[0]
    for row in store.servers_without_audit(conn, max(0, min(batch, MAX_QUEUED_AUDITS - backlog))):
        repo = row["repo"]
        try:
            meta = github.repo_meta(repo)
            sha = github.head_sha(repo, meta["default_branch"])
        except GitHubError as exc:   # rate limits are not GitHubError: they propagate and defer the job
            store.mark_checked(conn, repo, None, None, False, now)
            (missing if isinstance(exc, RepoNotFound) else errors).append(repo)
            if not isinstance(exc, RepoNotFound):
                log.warning("discovery skipped %s: %s", repo, exc)
            continue
        enqueue_audit(conn, repo, sha, priority=1.0 + math.log10(row["stars"] + 1), now=now)
        store.mark_checked(conn, repo, sha, None, False, now)
        queued.append(repo)
    return {"queued": queued, "missing": missing, "errors": errors, "backlog": backlog}
