import httpx
import pytest

from core.control import brake, store
from core.db import connect


@pytest.fixture
def conn(tmp_path):
    c = connect(str(tmp_path / "b.db"))
    store.ensure(c)
    return c


def client(body=None, status=200, raw=None, calls=None):
    def handler(request):
        if calls is not None:
            calls.append(request.url.path)
        return httpx.Response(status, content=raw) if raw is not None else httpx.Response(status, json=body)
    return httpx.Client(transport=httpx.MockTransport(handler))


KEY = {"data": {"usage_weekly": 1.0, "limit": 2, "limit_reset": "weekly", "expires_at": "2027-04-04T11:33:00Z"}}


def test_under_the_threshold_is_ok_and_over_it_trips(conn):
    s = brake.key_status(conn, "k", 1000.0, client(KEY))
    assert (s["state"], s["pct"], s["threshold_pct"]) == ("ok", 50.0, 90) and s["expires_at"].startswith("2027-04-04")
    store.set(conn, "global.brake_pct", 50, "", 1.0, set())
    assert brake.key_status(conn, "k", 1000.0, client(KEY))["state"] == "tripped"       # cached figure, new threshold


def test_the_figure_is_cached_for_five_minutes(conn):
    calls = []
    brake.key_status(conn, "k", 1000.0, client(KEY, calls=calls))
    brake.key_status(conn, "k", 1200.0, client(KEY, calls=calls))
    brake.key_status(conn, "k", 1400.0, client(KEY, calls=calls))
    assert len(calls) == 2


@pytest.mark.parametrize("kw", [{"raw": b"<html>oops"}, {"body": {"data": {}}}, {"body": {}, "status": 500}])
def test_a_bad_reply_keeps_the_last_figure_for_30_minutes_then_fails_open(conn, kw):
    brake.key_status(conn, "k", 1000.0, client({"data": {"usage_weekly": 1.95, "limit": 2}}))
    assert brake.key_status(conn, "k", 1000.0 + 600, client(**kw))["state"] == "tripped"     # last figure still used
    gone = brake.key_status(conn, "k", 1000.0 + 2400, client(**kw))
    assert gone["state"] == "unknown"
    assert brake.pause_reason(conn, "k", 1000.0 + 2400, client(**kw)) is None             # fail open


def test_no_key_or_no_limit_means_no_brake(conn):
    assert brake.key_status(conn, "", 1.0)["state"] == "unknown"
    assert brake.key_status(conn, "k", 1.0, client({"data": {"usage_weekly": 5, "limit": None}}))["state"] == "ok"


def test_stop_all_beats_everything(conn):
    store.set(conn, "global.stop_all", True, "", 1.0, set())
    assert brake.pause_reason(conn, "", 1.0) == "stop_all"
    store.clear(conn, "global.stop_all", "", 2.0)
    assert brake.pause_reason(conn, "k", 3.0, client({"data": {"usage_weekly": 1.99, "limit": 2}})) == "brake"


def test_next_week_reset_is_monday_utc():
    from datetime import datetime, timezone
    thursday = datetime(2026, 10, 8, 12, tzinfo=timezone.utc).timestamp()
    assert datetime.fromtimestamp(brake.next_week_reset(thursday), timezone.utc).isoformat() == "2026-10-12T00:00:00+00:00"


def test_a_failed_fetch_is_not_retried_for_a_minute(conn):
    """With OpenRouter down, every visitor admission must not wait out a fresh timeout."""
    calls = []
    bad = client(raw=b"nope", calls=calls)
    brake.key_status(conn, "k", 1000.0, bad)
    brake.key_status(conn, "k", 1030.0, bad)
    assert len(calls) == 1
    brake.key_status(conn, "k", 1100.0, bad)
    assert len(calls) == 2
