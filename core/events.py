"""Event outbox: the only way planets talk to each other. At-least-once; handlers must be idempotent."""
import json
import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Event:
    id: int
    type: str
    version: int
    payload: dict


def emit(conn, type_: str, payload: dict, version: int = 1, now=None) -> int:
    cur = conn.execute(
        "INSERT INTO events(type, version, payload, created_at) VALUES (?, ?, ?, ?)",
        (type_, version, json.dumps(payload, sort_keys=True), time.time() if now is None else now),
    )
    return cur.lastrowid


def consume(conn, consumer: str, handler: Callable[[Event], None], types: list[str] | None = None,
            limit: int = 100) -> int:
    row = conn.execute("SELECT last_event_id FROM consumer_cursors WHERE consumer=?", (consumer,)).fetchone()
    sql, params = "SELECT * FROM events WHERE id > ?", [row["last_event_id"] if row else 0]
    if types:
        sql += f" AND type IN ({','.join('?' * len(types))})"
        params += list(types)
    sql += " ORDER BY id LIMIT ?"
    params.append(limit)
    handled = 0
    for r in conn.execute(sql, params).fetchall():
        handler(Event(r["id"], r["type"], r["version"], json.loads(r["payload"])))
        conn.execute(
            "INSERT INTO consumer_cursors(consumer, last_event_id) VALUES (?, ?) "
            "ON CONFLICT(consumer) DO UPDATE SET last_event_id=excluded.last_event_id",
            (consumer, r["id"]),
        )
        handled += 1
    return handled
