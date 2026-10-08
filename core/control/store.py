"""Settings an owner changes from the console. Each app keeps its own in its database; a stored value beats the .env
default until it is cleared. Only known keys within fixed bounds are accepted, and every change is recorded."""
import copy
import json
import math

SCHEMA = """
CREATE TABLE IF NOT EXISTS control_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS control_changes(id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, old TEXT, new TEXT,
  note TEXT NOT NULL DEFAULT '', at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS control_cache(key TEXT PRIMARY KEY, value TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS batch_spend(day TEXT PRIMARY KEY, usd REAL NOT NULL DEFAULT 0, tokens INTEGER NOT NULL DEFAULT 0,
  calls INTEGER NOT NULL DEFAULT 0);
"""
AGENT_FIELDS = {"enabled": bool, "daily_usd": (0, 5), "run_usd": (0, 1), "per_ip_day": (0, 50), "concurrent": (1, 5), "queue": (0, 20)}
INTEGER_FIELDS = {"per_ip_day", "concurrent", "queue", "brake_pct"}
GLOBAL = {"batch.enabled": bool, "batch.daily_usd": (0, 5), "global.brake_pct": (50, 100), "global.stop_all": bool}


def ensure(conn) -> None:
    """Statement by statement: executescript would COMMIT an open transaction, and the admission check calls this while
    it holds the run-creation lock."""
    for statement in SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def _rule(key: str, agents: set[str]):
    if key in GLOBAL:
        return GLOBAL[key], key.split(".", 1)[1]
    prefix, _, field = key.partition(".")
    if prefix in agents and field in AGENT_FIELDS:
        return AGENT_FIELDS[field], field
    raise ValueError(f"unknown setting {key!r}")


def check_key(key: str, agents: set[str]) -> None:
    _rule(key, agents)


def validate(key: str, value, agents: set[str]):
    rule, field = _rule(key, agents)
    if rule is bool:
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{key} must be a number")
    if field in INTEGER_FIELDS and value != int(value):
        raise ValueError(f"{key} must be a whole number")
    lo, hi = rule
    if not lo <= value <= hi:
        raise ValueError(f"{key} must be between {lo} and {hi}")
    return int(value) if field in INTEGER_FIELDS else float(value)


def get(conn, key: str):
    row = conn.execute("SELECT value FROM control_settings WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else None


def _record(conn, key, old, new, note, now):
    conn.execute("INSERT INTO control_changes(key, old, new, note, at) VALUES (?, ?, ?, ?, ?)",
                 (key, json.dumps(old), json.dumps(new), (note or "")[:300], now))


def set(conn, key: str, value, note: str, now: float, agents: set[str]):  # noqa: A001
    clean = validate(key, value, agents)
    old = get(conn, key)
    conn.execute("INSERT INTO control_settings(key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                 "value=excluded.value, updated_at=excluded.updated_at", (key, json.dumps(clean), now))
    _record(conn, key, old, clean, note, now)
    return clean


def clear(conn, key: str, note: str, now: float) -> None:
    old = get(conn, key)
    if old is not None:
        conn.execute("DELETE FROM control_settings WHERE key=?", (key,))
        _record(conn, key, old, None, note, now)


def overrides(conn, prefix: str) -> dict:
    rows = conn.execute("SELECT key, value FROM control_settings WHERE key LIKE ?", (prefix + ".%",)).fetchall()
    return {r["key"].split(".", 1)[1]: json.loads(r["value"]) for r in rows if r["key"].split(".", 1)[1] in AGENT_FIELDS}


def effective(conn, prefix: str, cfg):
    out = copy.copy(cfg)
    for field, value in overrides(conn, prefix).items():
        setattr(out, field, value)
    return out


def changes(conn, limit: int = 50) -> list[dict]:
    rows = conn.execute("SELECT * FROM control_changes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{"key": r["key"], "old": json.loads(r["old"]), "new": json.loads(r["new"]), "note": r["note"], "at": r["at"]}
            for r in rows]


def cache_get(conn, key: str, max_age: float, now: float):
    row = conn.execute("SELECT value, at FROM control_cache WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row and now - row["at"] <= max_age else None


def cache_put(conn, key: str, value: dict, now: float) -> None:
    conn.execute("INSERT INTO control_cache(key, value, at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                 "value=excluded.value, at=excluded.at", (key, json.dumps(value), now))
