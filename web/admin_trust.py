"""Trust's part of the owner admin: the Red Team's spend today, its on/off switch and the recent runs."""
from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse

from trust.redteam import runs as redteam_runs
from web.admin import get_db, require_admin

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


def section(conn, now: float) -> dict:
    redteam_runs.STORE.ensure_schema(conn)
    cfg = redteam_runs.load_config()
    return {"redteam_spend": redteam_runs.STORE.day_spend(conn, now), "redteam_cap": cfg.daily_usd,
            "redteam_configured": cfg.enabled, "redteam_switch": redteam_runs.STORE.is_enabled(conn),
            "redteam_runs": redteam_runs.STORE.recent(conn, 20)}


@router.post("/redteam/toggle")
def toggle_redteam(conn=Depends(get_db)):
    redteam_runs.STORE.ensure_schema(conn)
    redteam_runs.STORE.set_enabled(conn, not redteam_runs.STORE.is_enabled(conn))
    return RedirectResponse("/admin", status_code=303)
