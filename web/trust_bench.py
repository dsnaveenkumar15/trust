"""The public red team benchmark (spec 2026-11-11): the newest published summary, one try's trace, and the JSON.
Read-only: runs and publishing happen only through `python -m trust.redteam.bench` on the server."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from core.db import connect
from trust.redteam import bench_store as bs
from trust.redteam import render
from web.trust_site import trust_only

router = APIRouter(dependencies=[Depends(trust_only)])
LABELS = {"none": "No defence", "prompt": "Prompt warning", "guard": "Code guard"}
NOINDEX = {"X-Robots-Tag": "noindex"}


def _db(request: Request):
    conn = connect(request.app.state.settings.db_path)
    try:
        bs.ensure_schema(conn)
        yield conn
    finally:
        conn.close()


def _share(got: int, counted: int) -> str:
    return f"{got} of {counted}" + (f" ({round(100 * got / counted)}%)" if counted else "")


def _tone(cell: dict) -> str:
    if not cell["counted"]:
        return "gap"
    share = cell["got"] / cell["counted"]
    return "high" if share >= 0.67 else "mid" if share >= 0.34 else "low" if share > 0 else "none"


def view(summary: dict, condition: str) -> dict:
    rows = [{"model": m, "cells": [{**summary["cells"][m][a["id"]][condition], "attack": a["id"],
                                     "tone": _tone(summary["cells"][m][a["id"]][condition])} for a in summary["attacks"]]}
            for m in summary["models"]]
    titles = {a["id"]: a["title"] for a in summary["attacks"]}
    return {"headline": [{"key": c, "label": LABELS[c], "text": _share(**summary["headline"][c])} for c in bs.CONDITIONS],
            "rows": rows, "by_attack": [{**a, "title": titles[a["id"]], "text": _share(a["got"], a["counted"])}
                                        for a in summary["by_attack"]]}


@router.get("/benchmark", response_class=HTMLResponse)
def benchmark_page(request: Request, c: str = "none", conn=Depends(_db)):
    from web.app import render as page
    condition = c if c in bs.CONDITIONS else "none"
    found = bs.latest(conn)
    summary = found["summary"] if found else None
    return page(request, "trust_bench.html", summary=summary, condition=condition, labels=LABELS,
                view=view(summary, condition) if summary else None)


@router.get("/benchmark.json")
def benchmark_json(conn=Depends(_db)):
    found = bs.latest(conn)
    if found is None:
        raise HTTPException(404)
    return JSONResponse(found["summary"])


@router.get("/benchmark/t/{try_id}", response_class=HTMLResponse)
def try_page(try_id: int, request: Request, conn=Depends(_db)):
    from web.app import render as page
    found = bs.latest(conn)
    published = {i for row in (found["summary"]["cells"].values() if found else []) for cell in row.values()
                 for c in cell.values() for i in c["try_ids"]}
    t = bs.get_try(conn, try_id) if try_id in published else None     # only tries of the published run are public
    if t is None:
        raise HTTPException(404)
    resp = page(request, "trust_bench_try.html", t=t, label=LABELS.get(t["condition"], t["condition"]),
                timeline=render.timeline(t["trace"], t["canaries"]), final=(t["trace"].get("final") or "")[:600],
                verdict=render.VERDICT_TEXT.get(t["verdict"], t["verdict"]))
    resp.headers.update(NOINDEX)
    return resp
