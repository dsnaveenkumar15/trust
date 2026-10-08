import threading
import time

import pytest

from core.agent.config import AgentConfig
from core.agent.runs import LimitError, Runner, RunStore
from core.control import agents, store
from core.db import connect


@pytest.fixture
def setup(tmp_path):
    db = str(tmp_path / "a.db")
    conn = connect(db)
    s = RunStore("demo", 7)
    s.ensure_schema(conn)
    store.ensure(conn)
    agents.install(s, lambda: "")
    cfg = AgentConfig(enabled=True, daily_usd=0.25, run_usd=0.1, per_ip_day=3, concurrent=2, queue=3)
    return db, conn, s, cfg


def test_a_console_cap_is_used_for_admission_and_budgets(setup):
    db, conn, s, cfg = setup
    store.set(conn, "demo.per_ip_day", 1, "", 1.0, {"demo"})
    rid = s.create_checked(conn, cfg, {}, "new", "ip", time.time())
    s.finish(conn, rid, "done", {}, None, time.time())
    with pytest.raises(LimitError, match="1"):
        s.create_checked(conn, cfg, {}, "new", "ip", time.time())
    store.set(conn, "demo.run_usd", 0.02, "", 1.0, {"demo"})
    assert s.effective_config(conn, cfg).run_usd == 0.02 and cfg.run_usd == 0.1


def test_the_console_switch_turns_an_agent_off(setup):
    db, conn, s, cfg = setup
    store.set(conn, "demo.enabled", False, "", 1.0, {"demo"})
    with pytest.raises(LimitError) as e:
        s.create_checked(conn, cfg, {}, "new", "ip", time.time())
    assert e.value.status == 403


def test_stop_all_pauses_new_runs_but_a_started_one_finishes(setup):
    db, conn, s, cfg = setup
    started = threading.Event()
    release = threading.Event()

    def work(c, run_id, emit):
        started.set()
        release.wait(5)
        return "done", {"ok": True}
    rid = s.create_checked(conn, cfg, {}, "new", "ip", time.time())
    Runner(db, cfg, s, work).start(rid)
    assert started.wait(5)
    store.set(conn, "global.stop_all", True, "", 1.0, set())
    with pytest.raises(LimitError, match="Paused by the owner"):
        s.create_checked(conn, cfg, {}, "new", "ip-b", time.time())
    release.set()
    for _ in range(100):
        if s.status_of(conn, rid) == "done":
            break
        time.sleep(0.05)
    assert s.status_of(conn, rid) == "done"


def test_the_runner_honours_a_lower_runs_at_once(setup):
    db, conn, s, cfg = setup
    store.set(conn, "demo.concurrent", 1, "", 1.0, {"demo"})
    active, peak, lock = [0], [0], threading.Lock()

    def work(c, run_id, emit):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.2)
        with lock:
            active[0] -= 1
        return "done", {}

    def limit():
        c = connect(db)
        try:
            return s.effective_config(c, cfg).concurrent
        finally:
            c.close()
    runner = Runner(db, cfg, s, work, limit=limit)
    ids = [s.create_run(conn, {}, "new", f"ip{i}", time.time()) for i in range(3)]
    for rid in ids:
        runner.start(rid)
    for _ in range(200):
        if all(s.status_of(conn, r) == "done" for r in ids):
            break
        time.sleep(0.05)
    assert all(s.status_of(conn, r) == "done" for r in ids) and peak[0] == 1


def test_without_controls_the_store_behaves_as_before(tmp_path):
    conn = connect(str(tmp_path / "n.db"))
    s = RunStore("plain", 7)
    s.ensure_schema(conn)
    cfg = AgentConfig(enabled=True, daily_usd=1, per_ip_day=1)
    assert s.effective_config(conn, cfg) is cfg
    s.create_checked(conn, cfg, {}, "new", "ip", time.time())


def test_register_lists_the_agents_for_the_control_interface():
    class State:
        pass

    class App:
        state = State()
    agents.register(App, "demo", "Demo agent", RunStore("demo", 7), "demo_config")
    assert [a["prefix"] for a in App.state.control_agents] == ["demo"]


def test_the_brake_check_never_runs_inside_the_run_creation_lock(setup):
    """The brake may fetch OpenRouter's figure over the network; doing that while the admission transaction holds the
    database's write lock would stall every other write in the app for the duration of the request."""
    db, conn, s, cfg = setup
    seen = []
    real = s.control

    def spy(c, cfg_, now):
        seen.append(c.in_transaction)
        return real(c, cfg_, now)
    s.control = spy
    s.create_checked(conn, cfg, {}, "new", "ip", time.time())
    assert seen and not any(seen)
