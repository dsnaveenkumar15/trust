"""Owner review queue: harsh labels, LLM disagreements, blueprint approvals, reported errors."""
import json
import time

DECISIONS = ("approved", "rejected", "embargoed")   # embargoed = publish now, details after the planet's embargo


def add(conn, planet: str, kind: str, ref: str, payload: dict, now=None) -> int | None:
    cur = conn.execute(
        "INSERT OR IGNORE INTO review_queue(planet, kind, ref, payload, created_at) VALUES (?, ?, ?, ?, ?)",
        (planet, kind, ref, json.dumps(payload, sort_keys=True), time.time() if now is None else now),
    )
    return cur.lastrowid if cur.rowcount == 1 else None


def list_open(conn, planet: str | None = None) -> list:
    if planet:
        return conn.execute("SELECT * FROM review_queue WHERE state='open' AND planet=? ORDER BY id",
                            (planet,)).fetchall()
    return conn.execute("SELECT * FROM review_queue WHERE state='open' ORDER BY id").fetchall()


def decide(conn, review_id: int, decision: str, note: str = "", now=None) -> bool:
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}, got {decision!r}")
    cur = conn.execute(
        "UPDATE review_queue SET state=?, decision_note=?, decided_at=? WHERE id=? AND state='open'",
        (decision, note, time.time() if now is None else now, review_id),
    )
    return cur.rowcount == 1


def decision_for(conn, kind: str, ref: str) -> str | None:
    row = conn.execute("SELECT state FROM review_queue WHERE kind=? AND ref=?", (kind, ref)).fetchone()
    return row["state"] if row else None


def entry_for(conn, kind: str, ref: str):
    return conn.execute("SELECT * FROM review_queue WHERE kind=? AND ref=?", (kind, ref)).fetchone()
