# core/agent/stream.py
"""Server-sent events that follow a run's event log. Waiting happens on the event loop (a thread only for each quick
read), so idle streams cost no worker threads. After `seconds` the stream closes WITHOUT an end event: the browser
reconnects and resumes from the last event id. A run that stops being queued/running ends the stream."""
import asyncio
import json
import time

from fastapi.responses import StreamingResponse

from core.db import connect


def sse(e: dict) -> str:
    return f"id: {e['id']}\ndata: {json.dumps({'type': e['type'], 'payload': e['payload']})}\n\n"


def poll(db_path: str, store, run_id: str, last: int) -> tuple[list[dict], str]:
    c = connect(db_path)
    try:
        return store.events(c, run_id, after=last), store.status_of(c, run_id)
    finally:
        c.close()


async def follow(db_path: str, store, run_id: str, last: int, seconds: float):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        events, status = await asyncio.to_thread(poll, db_path, store, run_id, last)
        for e in events:
            last = e["id"]
            yield sse(e)
        if status not in ("queued", "running"):
            tail, _ = await asyncio.to_thread(poll, db_path, store, run_id, last)      # events written just before it finished
            for e in tail:
                yield sse(e)
            yield "event: end\ndata: {}\n\n"
            return
        await asyncio.sleep(0.4)


def response(generator) -> StreamingResponse:
    return StreamingResponse(generator, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Robots-Tag": "noindex", "X-Accel-Buffering": "no"})
