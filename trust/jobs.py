"""Registers Trust job types and their schedules with the worker (PLANET_MODULES=trust.jobs)."""
import os
import time

import httpx

from core.claude_cli import ClaudeCliBackend
from core.github import GitHub
from core.jobs import enqueue
from core.periodic import schedule
from core.scheduler import GuardedBackend
from core.worker import JobType
from trust import store
from trust.discovery import collect, enqueue_new
from trust.freshness import refresh
from trust.publishing import publish_directory, third_party_enabled
from trust.pipeline import stage_extract, stage_fetch, stage_record, stage_review, stage_rules, stage_score
from trust.review import PROMPT_VERSION
from trust.rules import RULESET_VERSION

JOB_TYPE = "trust.audit"
DISCOVER_JOB = "trust.discover"
DISCOVER_EVERY_S = 6 * 3600


def audit_key(repo: str, sha: str) -> str:
    return f"{repo}@{sha}#rules-{RULESET_VERSION}#{PROMPT_VERSION}"


def enqueue_audit(conn, repo: str, sha: str, priority: float = 1.0, now=None) -> int:
    return enqueue(conn, "trust", JOB_TYPE, audit_key(repo, sha), {"repo": repo, "sha": sha},
                   priority=priority, now=now)


def enqueue_discovery(conn, now=None) -> int:
    return schedule(conn, "trust", DISCOVER_JOB, DISCOVER_EVERY_S, now=now)


REFRESH_JOB = "trust.refresh"
REFRESH_EVERY_S = 6 * 3600


def enqueue_refresh(conn, now=None) -> int:
    return schedule(conn, "trust", REFRESH_JOB, REFRESH_EVERY_S, now=now)


PUBLISH_JOB = "trust.publish"
PUBLISH_EVERY_S = 1800


def schedule_periodic(conn, now: float) -> None:
    """Worker hook: idempotent per window, so calling it every loop is cheap."""
    store.ensure_schema(conn)
    if third_party_enabled():       # without it nothing about other people's projects is fetched or audited
        enqueue_discovery(conn, now)
        enqueue_refresh(conn, now)
    schedule(conn, "trust", PUBLISH_JOB, PUBLISH_EVERY_S, now=now, priority=2.0)


def build_job_types(conn, github=None, backend=None, use_llm: bool = True, registry_client=None,
                    data_root=None) -> dict[str, JobType]:
    store.ensure_schema(conn)
    github = github or GitHub(conn, token=os.environ.get("GITHUB_TOKEN") or None)
    registry_client = registry_client or httpx.Client(timeout=30)
    if not use_llm:
        backend = None
    elif backend is None:
        backend = GuardedBackend(conn, ClaudeCliBackend(model=os.environ.get("TRUST_MODEL", "sonnet")))
    model = getattr(getattr(backend, "inner", backend), "model", None) or getattr(backend, "name", None)
    audit_stages = [
        ("fetch", lambda job, out: stage_fetch(github, job.payload["repo"], job.payload["sha"])),
        ("extract", lambda job, out: stage_extract(out["fetch"])),
        ("rules", lambda job, out: stage_rules(out["fetch"], out["extract"], time.time())),
        ("review", lambda job, out: stage_review(conn, backend, out["fetch"], out["extract"], out["rules"])),
        ("score", lambda job, out: stage_score(out["extract"], out["review"])),
        ("record", lambda job, out: stage_record(conn, out, model, time.time())),
    ]
    discover_stages = [
        ("collect", lambda job, out: collect(conn, github, registry_client, time.time(),
                                             radar_root=os.environ.get("RADAR_DATA_ROOT") or data_root)),
        ("enqueue", lambda job, out: enqueue_new(conn, github, time.time())),
    ]
    refresh_stages = [("refresh", lambda job, out: refresh(conn, github, time.time()))]
    data_root = data_root or os.environ.get("DATA_ROOT", "data/published")
    publish_stages = [("publish", lambda job, out: publish_directory(conn, data_root, time.time()))]
    return {JOB_TYPE: JobType("trust", audit_stages), DISCOVER_JOB: JobType("trust", discover_stages),
            REFRESH_JOB: JobType("trust", refresh_stages), PUBLISH_JOB: JobType("trust", publish_stages)}
