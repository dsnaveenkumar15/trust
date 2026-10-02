"""Claude Pro quota control: night window, AIMD allowance per usage window, circuit breaker."""
import time
from datetime import datetime, timedelta, timezone

from core.llm import AuthExpired, LLMError, QuotaDeferred, RateLimited

IST = timezone(timedelta(hours=5, minutes=30))  # India has no DST; fixed offset avoids a tzdata dependency
NIGHT_START_H, NIGHT_END_H = 23, 6
WINDOW_S = 5 * 3600            # ponytail: treats Pro's rolling 5h limit as fixed windows; AIMD absorbs the error
INITIAL_ALLOWANCE = 4.0
MIN_ALLOWANCE = 1.0
MAX_ALLOWANCE = 40.0
STEP = 1.0
BREAKER_THRESHOLD = 3
BREAKER_COOLDOWN_S = 3600


def in_night_window(now: float) -> bool:
    hour = datetime.fromtimestamp(now, IST).hour
    return hour >= NIGHT_START_H or hour < NIGHT_END_H


def next_night_start(now: float) -> float:
    if in_night_window(now):
        return now
    local = datetime.fromtimestamp(now, IST)
    return local.replace(hour=NIGHT_START_H, minute=0, second=0, microsecond=0).timestamp()


def _state(conn, now: float):
    conn.execute(
        "INSERT OR IGNORE INTO quota_state(id, allowance, used, limited, window_start, consecutive_failures, "
        "breaker_open_until) VALUES (1, ?, 0, 0, ?, 0, 0)",
        (INITIAL_ALLOWANCE, now),
    )
    row = conn.execute("SELECT * FROM quota_state WHERE id=1").fetchone()
    if now - row["window_start"] >= WINDOW_S:
        allowance = row["allowance"]
        if not row["limited"] and row["used"] >= int(allowance):
            allowance = min(MAX_ALLOWANCE, allowance + STEP)
        conn.execute("UPDATE quota_state SET allowance=?, used=0, limited=0, window_start=? WHERE id=1",
                     (allowance, now))
        row = conn.execute("SELECT * FROM quota_state WHERE id=1").fetchone()
    return row


def may_call(conn, now: float) -> bool:
    row = _state(conn, now)
    return in_night_window(now) and now >= row["breaker_open_until"] and row["used"] < int(row["allowance"])


def next_allowed_at(conn, now: float) -> float:
    row = _state(conn, now)
    t = max(now, row["breaker_open_until"])
    if row["used"] >= int(row["allowance"]):
        t = max(t, row["window_start"] + WINDOW_S)
    return next_night_start(t)


def record(conn, outcome: str, now: float) -> None:
    """outcome: ok | rate_limited | auth_expired | error"""
    row = _state(conn, now)
    allowance, used, limited = row["allowance"], row["used"], row["limited"]
    failures, breaker = row["consecutive_failures"], row["breaker_open_until"]
    last_error = None
    if outcome == "ok":
        used, failures = used + 1, 0
    elif outcome == "rate_limited":
        allowance = max(MIN_ALLOWANCE, allowance / 2)
        used, limited, failures = used + 1, 1, failures + 1
        breaker = max(breaker, row["window_start"] + WINDOW_S)
        last_error = "rate_limited"
    elif outcome == "auth_expired":
        failures += 1
        breaker = max(breaker, now + BREAKER_COOLDOWN_S)
        last_error = "auth_expired"
    else:
        used, failures = used + 1, failures + 1
        last_error = outcome
    if failures >= BREAKER_THRESHOLD:
        breaker = max(breaker, now + BREAKER_COOLDOWN_S)
    conn.execute(
        "UPDATE quota_state SET allowance=?, used=?, limited=?, consecutive_failures=?, breaker_open_until=?, "
        "last_error=? WHERE id=1",
        (allowance, used, limited, failures, breaker, last_error),
    )


def status(conn, now: float) -> dict:
    row = _state(conn, now)
    return {
        "allowance": row["allowance"],
        "used": row["used"],
        "window_resets_at": row["window_start"] + WINDOW_S,
        "breaker_open_until": row["breaker_open_until"],
        "last_error": row["last_error"],
        "night": in_night_window(now),
        "may_call": may_call(conn, now),
    }


class GuardedBackend:
    """Wraps the Claude backend: refuses calls the quota cannot afford and records every outcome."""

    def __init__(self, conn, inner, clock=time.time):
        self.conn, self.inner, self.clock = conn, inner, clock
        self.name = inner.name

    def __call__(self, prompt, json_schema: dict) -> str:
        now = self.clock()
        if not may_call(self.conn, now):
            raise QuotaDeferred(f"quota closed until {next_allowed_at(self.conn, now):.0f}")
        try:
            output = self.inner(prompt, json_schema)
        except RateLimited:
            record(self.conn, "rate_limited", self.clock())
            raise
        except AuthExpired:
            record(self.conn, "auth_expired", self.clock())
            raise
        except LLMError:
            record(self.conn, "error", self.clock())
            raise
        record(self.conn, "ok", self.clock())
        return output
