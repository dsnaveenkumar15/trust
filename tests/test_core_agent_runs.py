# tests/test_core_agent_runs.py
import time

import pytest

from core.agent.config import AgentConfig
from core.agent.runs import LimitError, Runner, RunStore
from core.db import connect

NOW = 1_790_877_600.0


@pytest.fixture
def conn(tmp_path):
    c = connect(str(tmp_path / "a.db"))
    yield c
    c.close()


def test_two_apps_share_a_database_without_mixing_runs(conn):
    radar, arch = RunStore("agent", 30), RunStore("architect", 7, id_bytes=16)
    radar.ensure_schema(conn)
    arch.ensure_schema(conn)
    rid = arch.create_run(conn, {"d": 1}, "new", "ip", NOW)
    assert len(rid) == 32 and radar.get_run(conn, rid) is None and arch.get_run(conn, rid)["input"] == {"d": 1}
    assert {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= {
        "agent_runs", "agent_events", "agent_settings", "architect_runs", "architect_events", "architect_settings"}


def test_a_waiting_run_holds_the_visitor_but_not_a_worker_slot(conn):
    s = RunStore("architect", 7, holding=("queued", "running", "waiting"))
    s.ensure_schema(conn)
    rid = s.create_run(conn, {}, "new", "ip", NOW)
    s.finish(conn, rid, "waiting", {"questions": []}, None, NOW)
    assert s.active_for(conn, "ip") == 1 and s.active_count(conn) == 0
    assert s.requeue(conn, rid, {"phase": "design"}) and s.get_run(conn, rid)["status"] == "queued"
    assert not s.requeue(conn, rid, {"phase": "design"})          # only a waiting run can be requeued


def test_waiting_runs_expire_and_interrupted_runs_are_refunded(conn):
    s = RunStore("architect", 7, holding=("queued", "running", "waiting"), interrupted="interrupted")
    s.ensure_schema(conn)
    old = s.create_run(conn, {}, "new", "ip", NOW - 3600)
    s.finish(conn, old, "waiting", {}, None, NOW - 3600)
    s.expire_waiting(conn, NOW, 1800)
    assert s.get_run(conn, old)["status"] == "expired"
    live = s.create_run(conn, {}, "new", "ip", NOW)
    s.interrupt_running(conn, NOW)
    assert s.get_run(conn, live)["status"] == "interrupted" and s.runs_today(conn, "ip", NOW) == 1   # only `old` counts


def test_expire_deletes_runs_and_events_after_keep_days(conn):
    s = RunStore("architect", 7)
    s.ensure_schema(conn)
    rid = s.create_run(conn, {}, "new", "ip", NOW - 8 * 86400)
    s.add_event(conn, rid, "node", {}, NOW - 8 * 86400)
    s.expire(conn, NOW)
    assert s.get_run(conn, rid) is None and s.events(conn, rid) == []


def test_calls_today_counts_only_the_listed_models(conn):
    s = RunStore("architect", 7)
    s.ensure_schema(conn)
    rid = s.create_run(conn, {}, "new", "ip", NOW)
    for model in ("free/x:free", "free/x:free", "paid/y"):
        s.add_event(conn, rid, "usage", {"model": model}, NOW)
    assert s.calls_today(conn, NOW, ["free/x:free"]) == 2 and s.calls_today(conn, NOW, []) == 0


def test_limits_use_the_stores_own_messages(conn):
    s = RunStore("architect", 7, messages={"one": "One design at a time."})
    s.ensure_schema(conn)
    cfg = AgentConfig(enabled=True, per_ip_day=3, concurrent=2, queue=3)
    s.create_checked(conn, cfg, {}, "new", "ip", NOW)
    with pytest.raises(LimitError) as err:
        s.create_checked(conn, cfg, {}, "new", "ip", NOW)
    assert err.value.status == 429 and str(err.value) == "One design at a time."


def test_runner_stores_the_status_the_work_returns(tmp_path):
    db = str(tmp_path / "r.db")
    c = connect(db)
    s = RunStore("architect", 7)
    s.ensure_schema(c)
    rid = s.create_run(c, {}, "new", "ip", time.time())
    runner = Runner(db, AgentConfig(concurrent=1), s, lambda conn, run_id, emit: (emit("info", {"t": 1}), ("passed", {"ok": 1}))[1])
    runner.start(rid)
    end = time.monotonic() + 10
    while s.get_run(c, rid)["status"] in ("queued", "running") and time.monotonic() < end:
        time.sleep(0.05)
    run = s.get_run(c, rid)
    assert run["status"] == "passed" and run["result"] == {"ok": 1} and [e["type"] for e in s.events(c, rid)] == ["info"]


def test_visitor_hashes_are_salted_per_install_stable_and_not_a_plain_hash_of_the_address(conn):
    import hashlib
    s = RunStore("architect", 7)
    s.ensure_schema(conn)
    first = s.visitor_hash(conn, "203.0.113.7")
    assert first == s.visitor_hash(conn, "203.0.113.7") and first != s.visitor_hash(conn, "203.0.113.8")
    assert first != hashlib.sha256(b"203.0.113.7").hexdigest()[:32] and len(first) == 32
    other = connect(":memory:")
    s.ensure_schema(other)
    assert s.visitor_hash(other, "203.0.113.7") != first                 # another install has another salt


def test_the_salt_survives_a_reconnect(tmp_path):
    path = str(tmp_path / "salt.db")
    s = RunStore("agent", 30)
    c = connect(path)
    s.ensure_schema(c)
    one = s.visitor_hash(c, "198.51.100.1")
    c.close()
    c2 = connect(path)
    assert s.visitor_hash(c2, "198.51.100.1") == one
