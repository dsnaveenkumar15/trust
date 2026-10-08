"""The private control interface (spec §3.3). Reached only by the console over the internal network: a proxied request
or a wrong token gets a 404, as if the address did not exist."""
import hmac
import os
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.control import brake, report, spend, store
from core.db import connect

router = APIRouter()
PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-real-ip")
SECRETS = ("OPENROUTER_API_KEY", "GITHUB_TOKEN", "KUMA_PUSH_URL", "CONTROL_TOKEN")
FIELDS = tuple(store.AGENT_FIELDS)


class Change(BaseModel):
    key: str
    value: bool | int | float | None = None
    note: str = ""


def _guard(request: Request) -> None:
    token = os.environ.get("CONTROL_TOKEN", "")
    given = request.headers.get("x-control-token", "")
    if not token or any(h in request.headers for h in PROXY_HEADERS) or not hmac.compare_digest(token.encode(), given.encode()):
        raise HTTPException(404)


def _agents(request: Request) -> list[dict]:
    return getattr(request.app.state, "control_agents", [])


def _models(cfg) -> dict:
    out = {"victim": list(getattr(cfg, "fast_models", []) or []), "analyst": list(getattr(cfg, "strong_models", []) or [])}
    if getattr(cfg, "small_models", None):
        out["small"] = list(cfg.small_models)
    return out


def _effective(conn, agent: dict, cfg) -> dict:
    eff = store.effective(conn, agent["prefix"], cfg)
    out = {f: getattr(eff, f) for f in FIELDS}
    out["enabled"] = bool(eff.enabled) and agent["store"].is_enabled(conn)
    return out


@router.get("/internal/control")
def read(request: Request):
    _guard(request)
    now = time.time()
    conn = connect(request.app.state.settings.db_path)
    try:
        store.ensure(conn)
        agents = []
        for a in _agents(request):
            a["store"].ensure_schema(conn)
            cfg = getattr(request.app.state, a["config_attr"])
            agents.append({"prefix": a["prefix"], "label": a["label"], "env": {f: getattr(cfg, f) for f in FIELDS},
                           "overrides": store.overrides(conn, a["prefix"]), "effective": _effective(conn, a, cfg),
                           "models": _models(cfg), "days": report.agent_days(conn, a["store"], now)})
        key = os.environ.get("OPENROUTER_API_KEY", "")
        cap = store.get(conn, "batch.daily_usd")
        return {"app": ",".join(sorted(request.app.state.settings.enabled_planets)), "now": now, "agents": agents,
                "batch": {"enabled": store.get(conn, "batch.enabled") is not False,
                          "daily_usd": float(os.environ.get("BATCH_DAILY_USD", "0.03")) if cap is None else cap,
                          "model": os.environ.get("BATCH_MODEL", "") or None, "days": spend.days(conn, now)},
                "global": {"brake_pct": store.get(conn, "global.brake_pct") or brake.DEFAULT_PCT,
                           "stop_all": bool(store.get(conn, "global.stop_all")), "brake": brake.key_status(conn, key, now)},
                "worker": report.worker(conn, now),
                "secrets": {s: "set" if os.environ.get(s) else "missing" for s in SECRETS},
                "github_token_expires": report.github_expiry(conn, os.environ.get("GITHUB_TOKEN", ""), now),
                "changes": store.changes(conn)}
    finally:
        conn.close()


@router.post("/internal/control")
def write(request: Request, change: Change):
    _guard(request)
    now = time.time()
    conn = connect(request.app.state.settings.db_path)
    try:
        store.ensure(conn)
        agents = {a["prefix"]: a for a in _agents(request)}
        try:
            if change.value is None:
                store.check_key(change.key, set(agents))
                store.clear(conn, change.key, change.note, now)
            else:
                store.set(conn, change.key, change.value, change.note, now, set(agents))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        prefix, _, field = change.key.partition(".")
        agent = agents.get(prefix)
        if agent and field == "enabled":                  # the agent's own on/off switch follows the console's
            agent["store"].ensure_schema(conn)
            agent["store"].set_enabled(conn, change.value is not False)
        value = store.get(conn, change.key)
        if value is None and agent:
            value = _effective(conn, agent, getattr(request.app.state, agent["config_attr"]))[field]
        return {"key": change.key, "effective": value}
    finally:
        conn.close()
