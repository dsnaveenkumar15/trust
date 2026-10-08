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
