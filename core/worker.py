"""Batch worker: claim a job, run its stages, map failures to retry / defer / dead."""
import importlib
import logging
import os
import socket
import time
from dataclasses import dataclass
from typing import Callable

import httpx

from core import jobs, scheduler
from core.db import connect
from core.llm import AuthExpired, QuotaDeferred, RateLimited
from core.log import setup_logging

log = logging.getLogger("worker")
HEARTBEAT_EVERY_S = 60
MIN_DEFER_S = 60


@dataclass(frozen=True)
class JobType:
    planet: str
    stages: list[tuple[str, Callable]]


class PermanentError(Exception):
    """Raise from a stage when retrying cannot help (bad input, unsupported language). Job goes to dead."""


def run_once(conn, registry: dict[str, JobType], weights: dict[str, float], worker_id: str,
             clock=time.time) -> str | None:
    job = jobs.claim(conn, worker_id, weights, now=clock())
    if job is None:
        return None
    job_type = registry.get(job.job_type)
    if job_type is None:
        jobs.fail_job(conn, job, f"unknown job type {job.job_type}", retryable=False, now=clock())
        return "dead"
    try:
        jobs.run_stages(conn, job, job_type.stages,
                        heartbeat=lambda: jobs.heartbeat(conn, job.id, worker_id, now=clock()))
    except (QuotaDeferred, RateLimited, AuthExpired) as exc:
        now = clock()
        # A provider that says when it resets (GitHub) wins over the Claude night window; never retry within a minute.
        until = getattr(exc, "reset_at", None) or scheduler.next_allowed_at(conn, now)
        jobs.defer_job(conn, job, max(until, now + MIN_DEFER_S), f"{type(exc).__name__}: {exc}", now=now)
        return "deferred"
    except PermanentError as exc:
        jobs.fail_job(conn, job, f"permanent: {exc}", retryable=False, now=clock())
        return "dead"
    except Exception as exc:
        log.exception("job %s (%s) failed", job.id, job.job_type)
        new_state = jobs.fail_job(conn, job, repr(exc)[:2000], now=clock())
        return "failed" if new_state == "queued" else "dead"
    jobs.complete_job(conn, job.id, now=clock())
    return "done"


def parse_weights(text: str) -> dict[str, float]:
    weights = {}
    for part in text.split(","):
        if part.strip():
            planet, value = part.split("=")
            weights[planet.strip()] = float(value)
    return weights


def load_registry(modules: list[str], conn=None) -> dict[str, JobType]:
    """Each module exposes JOB_TYPES, or build_job_types(conn) when its stages need the database."""
    registry: dict[str, JobType] = {}
    for name in modules:
        if name.strip():
            module = importlib.import_module(name.strip())
            builder = getattr(module, "build_job_types", None)
            registry.update(builder(conn) if builder else module.JOB_TYPES)
    return registry


def load_schedulers(modules: list[str]) -> list[Callable]:
    """Modules may expose schedule_periodic(conn, now) to enqueue their recurring jobs."""
    schedulers = []
    for name in modules:
        if name.strip():
            hook = getattr(importlib.import_module(name.strip()), "schedule_periodic", None)
            if hook is not None:
                schedulers.append(hook)
    return schedulers


def serve_forever(conn, registry, weights, worker_id, sleep_s: float = 30, heartbeat_url: str | None = None,
                  stop: Callable[[], bool] = lambda: False, schedulers=()) -> None:
    last_push = 0.0
    while not stop():
        for scheduler in schedulers:
            try:
                scheduler(conn, time.time())
            except Exception:
                log.exception("periodic scheduler failed")
        result = run_once(conn, registry, weights, worker_id)
        if result:
            log.info("job %s", result)
        if heartbeat_url and time.time() - last_push >= HEARTBEAT_EVERY_S:
            try:
                httpx.get(heartbeat_url, params={"status": "up", "msg": result or "idle"}, timeout=10)
            except httpx.HTTPError as exc:
                log.warning("heartbeat push failed: %s", exc)
            last_push = time.time()
        if result is None:
            time.sleep(sleep_s)


def main() -> None:
    setup_logging()
    conn = connect(os.environ.get("DB_PATH", "data/planets.db"))
    modules = os.environ.get("PLANET_MODULES", "").split(",")
    registry = load_registry(modules, conn)
    schedulers = load_schedulers(modules)
    weights = parse_weights(os.environ.get("WEIGHTS", "trust=50,radar=35,blueprints=15"))
    worker_id = os.environ.get("WORKER_ID", socket.gethostname())
    log.info("worker %s starting; job types=%s weights=%s", worker_id, sorted(registry), weights)
    serve_forever(conn, registry, weights, worker_id, heartbeat_url=os.environ.get("KUMA_PUSH_URL") or None,
                  schedulers=schedulers)


if __name__ == "__main__":
    main()
