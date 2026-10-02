from datetime import datetime

import pytest

from core import scheduler as s
from core.llm import AuthExpired, FakeBackend, Prompt, QuotaDeferred, RateLimited


def ist(h, mi=0, day=1):
    return datetime(2026, 10, day, h, mi, tzinfo=s.IST).timestamp()


def test_night_window_crosses_midnight_independent_of_server_tz():
    assert s.in_night_window(ist(23, 0))
    assert s.in_night_window(ist(5, 59))
    assert not s.in_night_window(ist(6, 0))
    assert not s.in_night_window(ist(22, 59))


def test_next_night_start():
    assert s.next_night_start(ist(14, 0)) == ist(23, 0)
    assert s.next_night_start(ist(2, 0)) == ist(2, 0)  # already night


def test_closed_in_daytime_open_at_night(conn):
    assert not s.may_call(conn, ist(12))
    assert s.may_call(conn, ist(23))


def test_allowance_exhaustion_and_reopen_at_next_window(conn):
    start = ist(23)
    for _ in range(int(s.INITIAL_ALLOWANCE)):
        assert s.may_call(conn, start)
        s.record(conn, "ok", start)
    assert not s.may_call(conn, start)
    assert s.next_allowed_at(conn, start) == start + s.WINDOW_S   # 04:00 IST, still night
    assert s.may_call(conn, start + s.WINDOW_S)


def test_aimd_additive_increase_after_clean_full_window(conn):
    start = ist(23)
    for _ in range(4):
        s.record(conn, "ok", start)
    assert s.status(conn, start + s.WINDOW_S)["allowance"] == s.INITIAL_ALLOWANCE + 1


def test_no_increase_when_window_not_fully_used(conn):
    start = ist(23)
    s.record(conn, "ok", start)
    assert s.status(conn, start + s.WINDOW_S)["allowance"] == s.INITIAL_ALLOWANCE


def test_rate_limit_halves_allowance_and_pauses_until_window_end(conn):
    start = ist(23)
    s.record(conn, "rate_limited", start)
    st = s.status(conn, start)
    assert st["allowance"] == s.INITIAL_ALLOWANCE / 2
    assert not s.may_call(conn, start + 60)
    assert s.next_allowed_at(conn, start + 60) == start + s.WINDOW_S
    assert s.status(conn, start + s.WINDOW_S)["allowance"] == s.INITIAL_ALLOWANCE / 2  # no increase after limit


def test_breaker_opens_after_consecutive_failures(conn):
    t = ist(23)
    for _ in range(s.BREAKER_THRESHOLD):
        s.record(conn, "error", t)
    assert not s.may_call(conn, t + 10)
    assert s.next_allowed_at(conn, t + 10) == t + s.BREAKER_COOLDOWN_S


def test_auth_expired_visible_in_status(conn):
    t = ist(23)
    s.record(conn, "auth_expired", t)
    st = s.status(conn, t)
    assert st["last_error"] == "auth_expired"
    assert st["may_call"] is False


def test_allowance_never_below_minimum(conn):
    t = ist(23)
    for i in range(10):
        s.record(conn, "rate_limited", t + i * s.WINDOW_S)
    assert s.status(conn, t + 10 * s.WINDOW_S)["allowance"] == s.MIN_ALLOWANCE


P = Prompt("demo", "demo@1", "s", "u")


def test_guarded_backend_refuses_when_closed_without_calling_inner(conn):
    inner = FakeBackend(["{}"])
    guarded = s.GuardedBackend(conn, inner, clock=lambda: ist(12))
    with pytest.raises(QuotaDeferred):
        guarded(P, {})
    assert inner.calls == []


def test_guarded_backend_records_outcomes(conn):
    clock = lambda: ist(23)
    s.GuardedBackend(conn, FakeBackend(["{}"]), clock=clock)(P, {})
    assert s.status(conn, ist(23))["used"] == 1
    with pytest.raises(RateLimited):
        s.GuardedBackend(conn, FakeBackend([RateLimited("limit")]), clock=clock)(P, {})
    assert s.status(conn, ist(23))["last_error"] == "rate_limited"
    with pytest.raises(AuthExpired):
        s.GuardedBackend(conn, FakeBackend([AuthExpired("login")]), clock=lambda: ist(23) + s.WINDOW_S)(P, {})
