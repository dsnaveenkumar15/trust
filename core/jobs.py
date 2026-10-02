"""Durable job engine: idempotent enqueue, leased claims, cached stages, backoff, dead-letter."""
import json
import random
import time
from dataclasses import dataclass
from typing import Callable

from core.db import tx

MAX_ATTEMPTS = 3
BASE_BACKOFF_S = 60
DEFAULT_LEASE_S = 900


@dataclass(frozen=True)
class Job:
    id: int
    planet: str
    job_type: str
    key: str
    payload: dict
    attempts: int


def _now(now):
    return time.time() if now is None else now


def _job(row) -> Job:
    return Job(row["id"], row["planet"], row["job_type"], row["key"], json.loads(row["payload"]), row["attempts"])


def enqueue(conn, planet: str, job_type: str, key: str, payload: dict, priority: float = 1.0, now=None) -> int:
    """Queue a job once per (job_type, key). Put content identity (e.g. commit SHA) in `key`."""
    now = _now(now)
    conn.execute(
        "INSERT INTO jobs(planet, job_type, key, payload, priority, next_run_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(job_type, key) DO NOTHING",
        (planet, job_type, key, json.dumps(payload, sort_keys=True), priority, now, now, now),
    )
    return conn.execute("SELECT id FROM jobs WHERE job_type=? AND key=?", (job_type, key)).fetchone()["id"]


def claim(conn, worker: str, weights: dict[str, float], lease_s: float = DEFAULT_LEASE_S, now=None) -> Job | None:
    """Lease the best runnable job among planets in `weights` (score = weight x priority x staleness)."""
    if not weights:
        return None
    now = _now(now)
    planets = list(weights)
    placeholders = ",".join("?" * len(planets))
    case = " ".join("WHEN ? THEN ?" for _ in planets)
    sql = (
        f"SELECT * FROM jobs WHERE state='queued' AND next_run_at <= ? AND planet IN ({placeholders}) "
        f"ORDER BY (CASE planet {case} END) * priority * (1 + (? - created_at) / 86400.0) DESC, id ASC LIMIT 1"
    )
    params = [now, *planets, *[v for p in planets for v in (p, float(weights[p]))], now]
    with tx(conn):
        # A lease that expired means the worker died mid-job: count it as an attempt so poison jobs end up dead.
        conn.execute(
            "UPDATE jobs SET state='queued', attempts=attempts+1, last_error='lease expired', "
            "lease_owner=NULL, lease_until=NULL, updated_at=? WHERE state='running' AND lease_until < ?",
            (now, now),
        )
        conn.execute("UPDATE jobs SET state='dead', updated_at=? WHERE state='queued' AND attempts >= ?",
                     (now, MAX_ATTEMPTS))
        row = conn.execute(sql, params).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE jobs SET state='running', lease_owner=?, lease_until=?, updated_at=? WHERE id=?",
                     (worker, now + lease_s, now, row["id"]))
    return _job(row)


def run_stages(conn, job: Job, stages: list[tuple[str, Callable[[Job, dict], object]]],
               heartbeat: Callable[[], object] | None = None) -> dict:
    """Run stages in order. Each stage gets (job, outputs_so_far) and must return JSON-serialisable data.
    Completed stages are loaded from `stage_artifacts`, never re-run."""
    outputs: dict = {}
    for name, fn in stages:
        row = conn.execute("SELECT output FROM stage_artifacts WHERE job_id=? AND stage=?", (job.id, name)).fetchone()
        if row:
            outputs[name] = json.loads(row["output"])
            continue
        outputs[name] = fn(job, outputs)
        conn.execute("INSERT INTO stage_artifacts(job_id, stage, output, created_at) VALUES (?, ?, ?, ?)",
                     (job.id, name, json.dumps(outputs[name]), time.time()))
        if heartbeat:
            heartbeat()
    return outputs


def complete_job(conn, job_id: int, now=None) -> None:
    conn.execute("UPDATE jobs SET state='done', lease_owner=NULL, lease_until=NULL, updated_at=? WHERE id=?",
                 (_now(now), job_id))


def fail_job(conn, job: Job, error: str, retryable: bool = True, now=None) -> str:
    now = _now(now)
    attempts = job.attempts + 1
    if not retryable or attempts >= MAX_ATTEMPTS:
        new_state, next_run = "dead", now
    else:
        new_state = "queued"
        next_run = now + BASE_BACKOFF_S * 2 ** (attempts - 1) * (1 + random.random() * 0.25)
    conn.execute(
        "UPDATE jobs SET state=?, attempts=?, next_run_at=?, last_error=?, lease_owner=NULL, lease_until=NULL, "
        "updated_at=? WHERE id=?",
        (new_state, attempts, next_run, error, now, job.id),
    )
    return new_state


def defer_job(conn, job: Job, until: float, reason: str, now=None) -> None:
    """Put back without spending an attempt (quota closed, rate-limited, auth expired)."""
    conn.execute(
        "UPDATE jobs SET state='queued', next_run_at=?, last_error=?, lease_owner=NULL, lease_until=NULL, "
        "updated_at=? WHERE id=?",
        (until, reason, _now(now), job.id),
    )


def heartbeat(conn, job_id: int, worker: str, lease_s: float = DEFAULT_LEASE_S, now=None) -> bool:
    now = _now(now)
    cur = conn.execute("UPDATE jobs SET lease_until=?, updated_at=? WHERE id=? AND lease_owner=? AND state='running'",
                       (now + lease_s, now, job_id, worker))
    return cur.rowcount == 1


def retry_dead(conn, job_id: int, now=None) -> bool:
    now = _now(now)
    cur = conn.execute("UPDATE jobs SET state='queued', attempts=0, next_run_at=?, updated_at=? "
                       "WHERE id=? AND state='dead'", (now, now, job_id))
    return cur.rowcount == 1
