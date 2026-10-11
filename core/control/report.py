"""What the console reads: per-day agent usage, worker health, GitHub token expiry."""
from datetime import datetime, timezone

import httpx

from core.control import store


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def agent_days(conn, run_store, now: float, n: int = 30) -> list[dict]:
    """Oldest first, the last entry is today. Runs, spend, tokens and distinct visitors per UTC day."""
    rows = conn.execute(f"SELECT created_at, cost_usd, tokens, ip_hash FROM {run_store.p}_runs WHERE created_at>=?",
                        (now - n * 86400,)).fetchall()
    per: dict[str, dict] = {}
    for r in rows:
        d = per.setdefault(_day(r["created_at"]), {"runs": 0, "usd": 0.0, "tokens": 0, "visitors": set()})
        d["runs"] += 1
        d["usd"] += r["cost_usd"]
        d["tokens"] += r["tokens"]
        d["visitors"].add(r["ip_hash"])
    out = []
    for i in range(n - 1, -1, -1):
        day = _day(now - i * 86400)
        d = per.get(day)
        out.append({"date": day, "runs": d["runs"] if d else 0, "usd": round(d["usd"], 6) if d else 0.0,
                    "tokens": d["tokens"] if d else 0, "visitors": len(d["visitors"]) if d else 0})
    return out


def recent_runs(conn, run_store, limit: int = 20) -> list[dict]:
    """The latest agent runs for the owner's console: when, how, outcome, duration and cost. Never the visitor's input, their
    hashed address or the run id (the id is the private result link)."""
    rows = conn.execute(f"SELECT created_at, finished_at, mode, status, cost_usd, tokens, showcase FROM {run_store.p}_runs "
                        f"ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [{"at": r["created_at"], "mode": r["mode"], "status": r["status"],
             "seconds": round(r["finished_at"] - r["created_at"], 1) if r["finished_at"] else None,
             "usd": round(r["cost_usd"] or 0.0, 6), "tokens": r["tokens"] or 0, "showcase": bool(r["showcase"])} for r in rows]


def recent_jobs(conn, limit: int = 20) -> list[dict]:
    """The latest batch jobs: type, state, attempts, when, and a short error. Never the job's key or payload."""
    try:
        rows = conn.execute("SELECT job_type, state, attempts, updated_at, last_error FROM jobs ORDER BY updated_at DESC LIMIT ?",
                            (limit,)).fetchall()
    except Exception:                                    # a database without the jobs table
        return []
    return [{"type": r["job_type"], "state": r["state"], "attempts": r["attempts"], "at": r["updated_at"],
             "error": (r["last_error"] or "")[:160] or None} for r in rows]


def worker(conn, now: float) -> dict:
    try:
        r = conn.execute("SELECT max(updated_at) AS last, sum(state='queued') AS queued, sum(state='running') AS running, "
                         "sum(state='dead' AND updated_at>=?) AS dead FROM jobs", (now - 86400,)).fetchone()
    except Exception:                                    # a database without the jobs table
        return {"last_activity_at": None, "queued": 0, "running": 0, "failed_24h": 0}
    return {"last_activity_at": r["last"], "queued": r["queued"] or 0, "running": r["running"] or 0, "failed_24h": r["dead"] or 0}


def github_expiry(conn, token: str, now: float, client: httpx.Client | None = None) -> str | None:
    """The token's expiry date as GitHub reports it in a response header, checked at most once a day."""
    if not token:
        return None
    cached = store.cache_get(conn, "github.expiry", 86400, now)
    if cached is not None:
        return cached.get("expires")
    try:
        r = (client or httpx.Client(timeout=10)).get("https://api.github.com/rate_limit",
                                                     headers={"Authorization": f"Bearer {token}", "User-Agent": "naveenlabs-control"})
        expires = r.headers.get("github-authentication-token-expiration")
    except httpx.HTTPError:
        return None
    store.cache_put(conn, "github.expiry", {"expires": expires}, now)
    return expires
