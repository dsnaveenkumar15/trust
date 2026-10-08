"""The weekly safety brake: stop starting paid work as the OpenRouter weekly limit nears, using OpenRouter's own figure
(cached 5 minutes). If the figure cannot be fetched, the last one is used for 30 minutes, then the brake fails open:
OpenRouter still enforces the hard limit, and blocking every visitor over a status hiccup would be worse."""
from datetime import datetime, timedelta, timezone

import httpx

from core.control import store

KEY_URL = "https://openrouter.ai/api/v1/key"
FRESH_S, STALE_S, RETRY_S = 300, 1800, 60
DEFAULT_PCT = 90
PAUSED_MESSAGES = {"stop_all": "Paused by the owner. Please try again later.",
                   "brake": "Paused until the weekly budget resets. Please try again later."}


def next_week_reset(now: float) -> float:
    d = datetime.fromtimestamp(now, timezone.utc)
    monday = (d - timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return (monday + timedelta(days=7)).timestamp()


def _fetch(api_key: str, client: httpx.Client | None) -> dict | None:
    try:
        c = client or httpx.Client(timeout=10)
        r = c.get(KEY_URL, headers={"Authorization": f"Bearer {api_key}"})
        d = r.json().get("data") if r.status_code == 200 else None
        if not isinstance(d, dict) or not isinstance(d.get("usage_weekly"), (int, float)):
            return None
        return {"usage_weekly": float(d["usage_weekly"]),
                "limit": float(d["limit"]) if isinstance(d.get("limit"), (int, float)) else None,
                "limit_reset": d.get("limit_reset"), "expires_at": d.get("expires_at")}
    except (httpx.HTTPError, ValueError, AttributeError):
        return None


def key_status(conn, api_key: str, now: float, client: httpx.Client | None = None) -> dict:
    threshold = store.get(conn, "global.brake_pct") or DEFAULT_PCT
    blank = {"state": "unknown", "weekly_usd": None, "limit_usd": None, "pct": None, "threshold_pct": threshold,
             "resets": None, "fetched_at": None, "expires_at": None}
    if not api_key:
        return blank
    figure = store.cache_get(conn, "openrouter.key", FRESH_S, now)
    if figure is None:
        fresh = None if store.cache_get(conn, "openrouter.key_failed", RETRY_S, now) is not None else _fetch(api_key, client)
        if fresh is not None:
            figure = {**fresh, "fetched_at": now}
            store.cache_put(conn, "openrouter.key", figure, now)
        else:
            if store.cache_get(conn, "openrouter.key_failed", RETRY_S, now) is None:
                store.cache_put(conn, "openrouter.key_failed", {"failed": True}, now)        # do not retry (and wait) for a minute
            figure = store.cache_get(conn, "openrouter.key", STALE_S, now)
    if figure is None:
        return blank
    limit, used = figure["limit"], figure["usage_weekly"]
    pct = round(100 * used / limit, 1) if limit else None
    state = "tripped" if pct is not None and pct >= threshold else "ok"
    return {"state": state, "weekly_usd": used, "limit_usd": limit, "pct": pct, "threshold_pct": threshold,
            "resets": figure.get("limit_reset"), "fetched_at": figure["fetched_at"], "expires_at": figure.get("expires_at")}


def pause_reason(conn, api_key: str, now: float, client: httpx.Client | None = None) -> str | None:
    if store.get(conn, "global.stop_all"):
        return "stop_all"
    return "brake" if key_status(conn, api_key, now, client)["state"] == "tripped" else None
