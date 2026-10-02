"""Owner admin. Basic Auth in-app (defense in depth); POSTs require same-origin to stop CSRF via cached creds."""
import json
import secrets
import time

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from core import jobs, review, scheduler
from core.db import connect
from core.summary import build_summary
from web.ratelimit import enforce
from web.security import client_ip, same_origin

basic = HTTPBasic(auto_error=False)
MIN_PASSWORD_CHARS = 16
REJECTED_PASSWORDS = {"change-me"}


def admin_enabled(settings) -> bool:
    """Admin exists only with a real password: unset, default or short means it is not there at all."""
    password = settings.admin_password or ""
    return bool(settings.admin_user) and len(password) >= MIN_PASSWORD_CHARS and password not in REJECTED_PASSWORDS


def require_admin(request: Request, creds: HTTPBasicCredentials | None = Depends(basic)) -> None:
    settings = request.app.state.settings
    if not admin_enabled(settings):
        raise HTTPException(404)
    # Every admin request spends a token before credentials are checked, so guessing is capped per IP.
    enforce(request.app.state.admin_limiter, client_ip(request, settings.trust_proxy))
    valid = creds is not None and (
        secrets.compare_digest(creds.username.encode(), settings.admin_user.encode())
        & secrets.compare_digest(creds.password.encode(), settings.admin_password.encode())
    )
    if not valid:
        raise HTTPException(401, headers={"WWW-Authenticate": 'Basic realm="admin"'})
    if request.method == "POST" and not same_origin(request):
        raise HTTPException(403, "cross-origin request refused")


def get_db(request: Request):
    conn = connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


def _payload(row) -> dict:
    try:
        data = json.loads(row["payload"])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


@router.get("")
def dashboard(request: Request, conn=Depends(get_db)):
    from web.app import render  # local import: web.app includes this router
    now = time.time()
    dead = conn.execute("SELECT id, planet, job_type, key, last_error, updated_at FROM jobs WHERE state='dead' "
                        "ORDER BY updated_at DESC LIMIT 50").fetchall()
    reviews = [{**dict(r), "data": _payload(r)} for r in review.list_open(conn)]
    sections = request.app.state.admin_sections
    context: dict = {}
    for provider in sections.values():            # each present planet adds its own dashboard data
        context.update(provider(conn, now))
    return render(request, "admin.html", summary=build_summary(conn, now - 86400), quota=scheduler.status(conn, now),
                  reviews=reviews, dead=dead, sections=list(sections), **context)


@router.post("/reviews/{review_id}")
def decide(review_id: int, decision: str = Form(...), note: str = Form(""), conn=Depends(get_db)):
    if decision not in review.DECISIONS:
        raise HTTPException(400, f"decision must be one of {review.DECISIONS}")
    review.decide(conn, review_id, decision, note)
    return RedirectResponse("/admin", status_code=303)


@router.post("/jobs/{job_id}/retry")
def retry(job_id: int, conn=Depends(get_db)):
    jobs.retry_dead(conn, job_id)
    return RedirectResponse("/admin", status_code=303)
