"""Cheap nightly freshness check: re-audit only when what agents see (tool names/descriptions) changed."""
import json
import logging

from core.github import GitHubError, RepoNotFound
from trust import store
from trust.pipeline import stage_extract, wanted_path

log = logging.getLogger("trust.freshness")
CHECK_EVERY_S = 86400
REFRESH_BATCH = 40
CHANGED_PRIORITY = 5.0


def refresh(conn, github, now: float, batch: int = REFRESH_BATCH) -> dict:
    from trust.jobs import enqueue_audit   # local import: trust.jobs imports this module
    changed, unchanged, missing, errors = [], [], [], []
    for row in store.servers_due_for_check(conn, now - CHECK_EVERY_S, batch):
        repo = row["repo"]
        audit = store.latest_audit(conn, repo)
        try:
            sha = github.head_sha(repo, github.repo_meta(repo)["default_branch"])
            if sha == audit["sha"] or sha == row["head_sha"]:
                store.mark_checked(conn, repo, sha, None, bool(row["tools_changed"]), now)
                unchanged.append(repo)
                continue
            snapshot = github.snapshot(repo, sha, wanted_path)
        except GitHubError as exc:   # rate limits are not GitHubError: they propagate and defer the job
            store.mark_checked(conn, repo, row["head_sha"], None, bool(row["tools_changed"]), now)
            (missing if isinstance(exc, RepoNotFound) else errors).append(repo)
            if not isinstance(exc, RepoNotFound):
                log.warning("freshness skipped %s: %s", repo, exc)
            continue
        new_hash = store.tools_hash(stage_extract(snapshot)["tools"])
        if new_hash != store.tools_hash(json.loads(audit["tools"])):
            enqueue_audit(conn, repo, sha, priority=CHANGED_PRIORITY, now=now)
            store.mark_checked(conn, repo, sha, new_hash, True, now)
            changed.append(repo)
        else:
            store.mark_checked(conn, repo, sha, new_hash, bool(row["tools_changed"]), now)
            unchanged.append(repo)
    return {"changed": changed, "unchanged": unchanged, "missing": missing, "errors": errors}
