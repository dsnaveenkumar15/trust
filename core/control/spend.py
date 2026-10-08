"""Batch model use under the console's controls: on/off, stop-all, the weekly brake and a daily dollar cap. A refused
call raises QuotaDeferred with reset_at, so the worker retries the job later instead of failing it."""
import time
from datetime import datetime, timedelta, timezone

from core.control import brake, store
from core.llm import QuotaDeferred


def _day(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")


def _tomorrow(now: float) -> float:
    d = datetime.fromtimestamp(now, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return (d + timedelta(days=1)).timestamp()


def today(conn, now: float) -> dict:
    r = conn.execute("SELECT usd, tokens, calls FROM batch_spend WHERE day=?", (_day(now),)).fetchone()
    return {"usd": r["usd"], "tokens": r["tokens"], "calls": r["calls"]} if r else {"usd": 0.0, "tokens": 0, "calls": 0}


def days(conn, now: float, n: int = 30) -> list[dict]:
    first = _day(now - (n - 1) * 86400)
    rows = {r["day"]: r for r in conn.execute("SELECT * FROM batch_spend WHERE day>=?", (first,))}
    out = []
    for i in range(n - 1, -1, -1):
        d = _day(now - i * 86400)
        r = rows.get(d)
        out.append({"date": d, "usd": r["usd"] if r else 0.0, "tokens": r["tokens"] if r else 0, "calls": r["calls"] if r else 0})
    return out


class CappedBackend:
    def __init__(self, conn, inner, api_key: str, env_daily_usd: float, clock=time.time):
        self.conn, self.inner, self.api_key, self.env_daily_usd, self.clock = conn, inner, api_key, env_daily_usd, clock
        self.name, self.model = inner.name, getattr(inner, "model", None)
        store.ensure(conn)

    def _refuse(self, reason: str, until: float):
        exc = QuotaDeferred(reason)
        exc.reset_at = until
        raise exc

    def __call__(self, prompt, json_schema: dict) -> str:
        now = self.clock()
        if store.get(self.conn, "batch.enabled") is False:
            self._refuse("batch model use is switched off", now + 3600)
        reason = brake.pause_reason(self.conn, self.api_key, now)
        if reason == "stop_all":
            self._refuse("all spending is stopped", now + 3600)
        if reason == "brake":
            self._refuse("the weekly brake is on", brake.next_week_reset(now))
        cap = store.get(self.conn, "batch.daily_usd")
        cap = self.env_daily_usd if cap is None else cap
        if today(self.conn, now)["usd"] >= cap:
            self._refuse("today's batch budget is used up", _tomorrow(now))
        text = self.inner(prompt, json_schema)
        u = getattr(self.inner, "last_usage", {"usd": 0.0, "tokens": 0})
        self.conn.execute("INSERT INTO batch_spend(day, usd, tokens, calls) VALUES (?, ?, ?, 1) ON CONFLICT(day) DO UPDATE SET "
                          "usd=usd+excluded.usd, tokens=tokens+excluded.tokens, calls=calls+1", (_day(now), u["usd"], u["tokens"]))
        return text
