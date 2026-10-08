# core/agent/runs.py
"""Agent runs and their event log in SQLite, one table set per app (prefix), and the background runner. The live
trace, run pages, limits and the daily spend all read from here."""
import hashlib
import json
import re
import secrets
import threading
import time
from datetime import datetime, timezone

from core.db import connect, tx

SCHEMA = """
CREATE TABLE IF NOT EXISTS {p}_runs(id TEXT PRIMARY KEY, created_at REAL NOT NULL, input TEXT NOT NULL, mode TEXT NOT NULL,
  ip_hash TEXT NOT NULL, status TEXT NOT NULL, showcase INTEGER NOT NULL DEFAULT 0, cost_usd REAL NOT NULL DEFAULT 0,
  tokens INTEGER NOT NULL DEFAULT 0, result TEXT, error TEXT, finished_at REAL);
CREATE INDEX IF NOT EXISTS {p}_runs_created ON {p}_runs(created_at);
CREATE TABLE IF NOT EXISTS {p}_events(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, at REAL NOT NULL,
  type TEXT NOT NULL, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS {p}_events_run ON {p}_events(run_id, id);
CREATE TABLE IF NOT EXISTS {p}_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
PREFIX = re.compile(r"[a-z][a-z_]{0,30}")
RUNNING = ("queued", "running")
MESSAGES = {
    "off": "Live runs are switched off right now. Watch a recorded run instead.",
    "budget": "Today's live-run budget is used up. Live runs resume at 00:00 UTC; recorded runs are available.",
    "one": "You already have a run in progress: one run at a time.",
    "daily": "You have used your {n} live runs for today.",
    "busy": "The agent is busy. Try again in a minute, or watch a recorded run.",
}
RESTARTED = "The server restarted during this run, so it was stopped. Please run it again."


class LimitError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def midnight(now: float) -> float:
    d = datetime.fromtimestamp(now, timezone.utc)
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()


class RunStore:
    def __init__(self, prefix: str, keep_days: int, holding=RUNNING, interrupted: str = "failed", id_bytes: int = 8,
                 messages: dict | None = None):
        if not PREFIX.fullmatch(prefix):
            raise ValueError(f"bad table prefix {prefix!r}")
        self.p, self.keep_days, self.holding, self.interrupted, self.id_bytes = prefix, keep_days, tuple(holding), interrupted, id_bytes
        self.messages = {**MESSAGES, **(messages or {})}
        self.control = None             # set by core.control.agents.install: (conn, cfg, now) -> (effective cfg, paused message)

    def _controlled(self, conn, cfg, now: float):
        return (cfg, None) if self.control is None else self.control(conn, cfg, now)

    def effective_config(self, conn, cfg):
        """The agent's config with the console's settings applied (cfg itself when no controls are installed)."""
        return cfg if self.control is None else self.control(conn, cfg, time.time())[0]

    def _in(self, statuses) -> str:
        return ",".join("'" + s + "'" for s in statuses)          # code constants only, never visitor input

    def ensure_schema(self, conn) -> None:
        conn.executescript(SCHEMA.format(p=self.p))

    def create_run(self, conn, input: dict, mode: str, ip_hash: str, now: float, showcase: bool = False) -> str:
        rid = secrets.token_hex(self.id_bytes)
        conn.execute(f"INSERT INTO {self.p}_runs(id, created_at, input, mode, ip_hash, status, showcase) VALUES (?,?,?,?,?,?,?)",
                     (rid, now, json.dumps(input), mode, ip_hash, "queued", int(showcase)))
        return rid

    def create_checked(self, conn, cfg, input: dict, mode: str, ip_hash: str, now: float) -> str:
        """Limit check and run creation under one write lock, so simultaneous posts cannot both pass the limits."""
        cfg, paused = self._controlled(conn, cfg, now)       # may fetch OpenRouter's figure: never inside the write lock
        with tx(conn):
            self.check_limits(conn, cfg, ip_hash, now, paused=paused, controlled=True)
            return self.create_run(conn, input, mode, ip_hash, now)

    def set_running(self, conn, run_id: str) -> None:
        conn.execute(f"UPDATE {self.p}_runs SET status='running' WHERE id=? AND status='queued'", (run_id,))

    def requeue(self, conn, run_id: str, input: dict) -> bool:
        """A waiting run continues with new input. Only one caller can win: the status check is in the UPDATE."""
        cur = conn.execute(f"UPDATE {self.p}_runs SET status='queued', input=?, finished_at=NULL WHERE id=? AND status='waiting'",
                           (json.dumps(input), run_id))
        return cur.rowcount == 1

    def add_event(self, conn, run_id: str, type: str, payload: dict, now: float) -> int:
        return conn.execute(f"INSERT INTO {self.p}_events(run_id, at, type, payload) VALUES (?,?,?,?)",
                            (run_id, now, type, json.dumps(payload, default=str))).lastrowid

    def events(self, conn, run_id: str, after: int = 0) -> list[dict]:
        return [{"id": r["id"], "type": r["type"], "payload": json.loads(r["payload"]), "at": r["at"]}
                for r in conn.execute(f"SELECT id, type, payload, at FROM {self.p}_events WHERE run_id=? AND id>? ORDER BY id",
                                      (run_id, after))]

    def add_usage(self, conn, run_id: str, usd: float, tokens: int) -> None:
        conn.execute(f"UPDATE {self.p}_runs SET cost_usd=cost_usd+?, tokens=tokens+? WHERE id=?", (usd, tokens, run_id))

    def day_spend(self, conn, now: float) -> float:
        return float(conn.execute(f"SELECT COALESCE(SUM(cost_usd),0) FROM {self.p}_runs WHERE created_at>=?",
                                  (midnight(now),)).fetchone()[0])

    def runs_today(self, conn, ip_hash: str, now: float) -> int:
        return conn.execute(f"SELECT count(*) FROM {self.p}_runs WHERE ip_hash=? AND created_at>=? AND showcase=0 "
                            f"AND status!='interrupted'", (ip_hash, midnight(now))).fetchone()[0]

    def active_for(self, conn, ip_hash: str) -> int:
        return conn.execute(f"SELECT count(*) FROM {self.p}_runs WHERE ip_hash=? AND status IN ({self._in(self.holding)})",
                            (ip_hash,)).fetchone()[0]

    def active_count(self, conn) -> int:
        return conn.execute(f"SELECT count(*) FROM {self.p}_runs WHERE status IN ({self._in(RUNNING)})").fetchone()[0]

    def finish(self, conn, run_id: str, status: str, result, error, now: float) -> None:
        conn.execute(f"UPDATE {self.p}_runs SET status=?, result=?, error=?, finished_at=? WHERE id=?",
                     (status, json.dumps(result) if result is not None else None, error, now, run_id))

    def status_of(self, conn, run_id: str) -> str:
        r = conn.execute(f"SELECT status FROM {self.p}_runs WHERE id=?", (run_id,)).fetchone()
        return r["status"] if r else "failed"

    def get_run(self, conn, run_id: str) -> dict | None:
        r = conn.execute(f"SELECT * FROM {self.p}_runs WHERE id=?", (run_id,)).fetchone()
        if r is None:
            return None
        d = dict(r)
        d["input"] = json.loads(d["input"])
        d["result"] = json.loads(d["result"]) if d["result"] else None
        return d

    def recent(self, conn, limit: int = 20) -> list[dict]:
        return [dict(r) for r in conn.execute(f"SELECT id, created_at, mode, status, cost_usd, tokens FROM {self.p}_runs "
                                              f"ORDER BY created_at DESC LIMIT ?", (limit,))]

    def showcases(self, conn) -> list[dict]:
        return [dict(r) for r in conn.execute(f"SELECT id, input, cost_usd, tokens, created_at FROM {self.p}_runs "
                                              f"WHERE showcase=1 AND status='done' ORDER BY created_at DESC LIMIT 3")]

    def expire(self, conn, now: float) -> None:
        cutoff = now - self.keep_days * 86400
        conn.execute(f"DELETE FROM {self.p}_events WHERE run_id IN (SELECT id FROM {self.p}_runs WHERE showcase=0 AND created_at<?)",
                     (cutoff,))
        conn.execute(f"DELETE FROM {self.p}_runs WHERE showcase=0 AND created_at<?", (cutoff,))

    def expire_waiting(self, conn, now: float, seconds: float) -> None:
        for r in conn.execute(f"SELECT id FROM {self.p}_runs WHERE status='waiting' AND COALESCE(finished_at, created_at)<?",
                              (now - seconds,)).fetchall():
            self.add_event(conn, r["id"], "error", {"text": "The questions were not answered within 30 minutes, so this run ended."}, now)
            conn.execute(f"UPDATE {self.p}_runs SET status='expired', finished_at=? WHERE id=? AND status='waiting'", (now, r["id"]))

    def interrupt_running(self, conn, now: float) -> None:
        for r in conn.execute(f"SELECT id FROM {self.p}_runs WHERE status IN ({self._in(RUNNING)})").fetchall():
            self.add_event(conn, r["id"], "error", {"text": RESTARTED}, now)
        conn.execute(f"UPDATE {self.p}_runs SET status=?, error='the server restarted during the run', finished_at=? "
                     f"WHERE status IN ({self._in(RUNNING)})", (self.interrupted, now))

    def set_enabled(self, conn, enabled: bool) -> None:
        conn.execute(f"INSERT INTO {self.p}_settings(key, value) VALUES ('enabled', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     ("1" if enabled else "0",))

    def is_enabled(self, conn) -> bool:
        r = conn.execute(f"SELECT value FROM {self.p}_settings WHERE key='enabled'").fetchone()
        return r is None or r["value"] == "1"

    def visitor_hash(self, conn, ip: str) -> str:
        """A stable key for one visitor's address: SHA-256 of a random per-install salt and the address, so the key is
        not a plain hash of the IP. The salt lives in this store's settings table; it is made on first use."""
        row = conn.execute(f"SELECT value FROM {self.p}_settings WHERE key='ip_salt'").fetchone()
        if row is None:
            conn.execute(f"INSERT OR IGNORE INTO {self.p}_settings(key, value) VALUES ('ip_salt', ?)", (secrets.token_hex(16),))
            row = conn.execute(f"SELECT value FROM {self.p}_settings WHERE key='ip_salt'").fetchone()
        return hashlib.sha256((row["value"] + "|" + ip).encode()).hexdigest()[:32]

    def calls_today(self, conn, now: float, models: list[str]) -> int:
        if not models:
            return 0
        marks = ",".join("?" for _ in models)
        return conn.execute(f"SELECT count(*) FROM {self.p}_events WHERE type='usage' AND at>=? "
                            f"AND json_extract(payload, '$.model') IN ({marks})", (midnight(now), *models)).fetchone()[0]

    def check_limits(self, conn, cfg, ip_hash: str, now: float, paused: str | None = None, controlled: bool = False) -> None:
        m = self.messages
        if not controlled:                                    # called directly: apply the console's settings here
            cfg, paused = self._controlled(conn, cfg, now)
        if paused:
            raise LimitError(503, paused)
        if not cfg.enabled or not self.is_enabled(conn):
            raise LimitError(403, m["off"])
        if self.day_spend(conn, now) >= cfg.daily_usd:
            raise LimitError(503, m["budget"])
        if self.active_for(conn, ip_hash) > 0:
            raise LimitError(429, m["one"])
        if self.runs_today(conn, ip_hash, now) >= cfg.per_ip_day:
            raise LimitError(429, m["daily"].format(n=cfg.per_ip_day))
        if self.active_count(conn) >= cfg.concurrent + cfg.queue:
            raise LimitError(503, m["busy"])


def _default_error(exc: Exception) -> str:
    return "The run failed. Please try again later."


class Runner:
    """Runs agents on background threads, at most `cfg.concurrent` at once; the rest wait (status queued).
    `work(conn, run_id, emit)` does the run and returns (status, result)."""

    def __init__(self, db_path: str, cfg, store: RunStore, work, error_text=_default_error, limit=None):
        self.db_path, self.cfg, self.store, self.work, self.error_text = db_path, cfg, store, work, error_text
        self.limit = limit or (lambda: cfg.concurrent)          # live: the console may change runs-at-once while the app runs
        self.cond, self.active = threading.Condition(), 0

    def start(self, run_id: str) -> None:
        threading.Thread(target=self._run, args=(run_id,), name=f"{self.store.p}-{run_id}", daemon=True).start()

    def _run(self, run_id: str) -> None:
        s = self.store
        conn = connect(self.db_path)
        try:
            with self.cond:
                while self.active >= max(1, self.limit()):
                    self.cond.wait(1)
                self.active += 1
            try:
                s.set_running(conn, run_id)
                emit = lambda type, payload: s.add_event(conn, run_id, type, payload, time.time())  # noqa: E731
                status, result = self.work(conn, run_id, emit)
                s.finish(conn, run_id, status, result, None, time.time())
            finally:
                with self.cond:
                    self.active -= 1
                    self.cond.notify_all()
        except Exception as exc:                          # visitor-facing message; details stay in the server log
            s.add_event(conn, run_id, "error", {"text": self.error_text(exc)}, time.time())
            s.finish(conn, run_id, "failed", None, repr(exc)[:2000], time.time())
        finally:
            conn.close()
