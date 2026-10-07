"""The red team pages and API (spec §8). Runs execute on background threads (core.agent.runs.Runner); the browser
follows a run over a same-origin event stream; run pages are private (noindex, no referrer); deleted after 7 days."""
import re
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from core.agent.stream import follow
from core.agent.stream import response as stream_response
from core.db import connect
from trust.redteam import render
from trust.redteam.runs import STORE, LimitError
from web.security import client_ip, same_origin
from web.trust_site import trust_only

router = APIRouter(dependencies=[Depends(trust_only)])
RUN_ID = re.compile(r"[0-9a-f]{32}")
STREAM_SECONDS = 180
PRIVATE = {"X-Robots-Tag": "noindex", "Referrer-Policy": "no-referrer"}
STEPS = [("fetch", "Fetch"), ("facts", "Static check and reach"), ("choose", "Analyst"), ("attack", "Attacks"),
         ("fixcheck", "Proof and fix check"), ("report", "Report")]


class StartRun(BaseModel):
    kind: Literal["manifest", "repo"]
    text: str = Field(min_length=1, max_length=200_000)
    consent: bool


def _db(request: Request):
    conn = connect(request.app.state.settings.db_path)
    try:
        STORE.ensure_schema(conn)
        yield conn
    finally:
        conn.close()


def _run_or_404(conn, run_id: str) -> dict:
    run = STORE.get_run(conn, run_id) if RUN_ID.fullmatch(run_id) else None
    if run is None:
        raise HTTPException(404)
    return run


@router.get("/redteam", response_class=HTMLResponse)
def redteam_page(request: Request, conn=Depends(_db)):
    from web.app import render as page
    cfg = request.app.state.redteam_config
    available = cfg.enabled and STORE.is_enabled(conn) and STORE.day_spend(conn, time.time()) < cfg.daily_usd
    return page(request, "trust_redteam.html", available=available)


@router.post("/api/redteam/runs")
def start_run(request: Request, body: StartRun, conn=Depends(_db)):
    if not same_origin(request):
        raise HTTPException(403, "cross-site request refused")
    if not body.consent:
        raise HTTPException(400, "Tick the box to confirm you may test these tools.")
    now = time.time()
    STORE.expire(conn, now)
    ip = STORE.visitor_hash(conn, client_ip(request, request.app.state.settings.trust_proxy))
    try:
        rid = STORE.create_checked(conn, request.app.state.redteam_config,
                                   {"kind": body.kind, "text": body.text.strip(), "consent": True, "consent_at": now}, "new", ip, now)
    except LimitError as exc:
        raise HTTPException(exc.status, str(exc))
    request.app.state.redteam_runner.start(rid)
    return {"id": rid}


@router.get("/api/redteam/runs/{run_id}/events")
async def events(run_id: str, request: Request, after: int = 0):
    db_path = request.app.state.settings.db_path
    conn = connect(db_path)
    try:
        STORE.ensure_schema(conn)
        _run_or_404(conn, run_id)
    finally:
        conn.close()
    header = request.headers.get("last-event-id", "")
    last = max(after, int(header)) if header.isdigit() else after
    return stream_response(follow(db_path, STORE, run_id, last, STREAM_SECONDS))


def _done(conn, run_id: str) -> dict:
    run = _run_or_404(conn, run_id)
    if run["status"] != "done":
        raise HTTPException(404)
    return run["result"]


@router.get("/redteam/r/{run_id}.md", response_class=PlainTextResponse)
def download_markdown(run_id: str, conn=Depends(_db)):
    return PlainTextResponse(render.markdown(_done(conn, run_id)), media_type="text/markdown",
                             headers={**PRIVATE, "Content-Disposition": f'attachment; filename="redteam-{run_id[:8]}.md"'})


@router.get("/redteam/r/{run_id}.json")
def download_json(run_id: str, conn=Depends(_db)):
    return JSONResponse(render.pack(_done(conn, run_id)),
                        headers={**PRIVATE, "Content-Disposition": f'attachment; filename="redteam-{run_id[:8]}.json"'})


@router.get("/redteam/r/{run_id}", response_class=HTMLResponse)
def run_page(run_id: str, request: Request, conn=Depends(_db)):
    from web.app import render as page
    STORE.expire(conn, time.time())
    run = _run_or_404(conn, run_id)
    log = STORE.events(conn, run_id)
    message = next((e["payload"].get("text") for e in reversed(log) if e["type"] == "error"), "This run stopped.")
    resp = page(request, "trust_redteam_run.html", run=run, steps=STEPS, message=message,
                run_cap=request.app.state.redteam_config.run_usd,
                view=render.view(run["result"]) if run["status"] == "done" else None)
    resp.headers.update(PRIVATE)
    return resp
