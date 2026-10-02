"""Recurring jobs without cron: at most one job per time window, enqueued from the worker loop."""
import time

from core.jobs import enqueue


def schedule(conn, planet: str, job_type: str, every_s: float, now=None, payload: dict | None = None,
             priority: float = 1.0) -> int:
    now = time.time() if now is None else now
    return enqueue(conn, planet, job_type, f"periodic:{int(now // every_s)}", payload or {}, priority=priority, now=now)
