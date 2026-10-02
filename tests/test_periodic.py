from core import worker
from core.periodic import schedule
from tests import fake_planet


def test_schedule_is_idempotent_within_a_window(conn):
    a = schedule(conn, "trust", "trust.publish", 1800, now=3600.0)
    b = schedule(conn, "trust", "trust.publish", 1800, now=3600.0 + 1799)
    c = schedule(conn, "trust", "trust.publish", 1800, now=3600.0 + 1800)
    assert a == b != c
    assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 2


def test_load_schedulers_finds_module_hooks():
    assert worker.load_schedulers(["tests.fake_planet", "tests.fake_planet_builder", ""]) == [
        fake_planet.schedule_periodic]


def test_serve_forever_runs_schedulers_each_loop(conn):
    calls = []
    ticks = iter([False, False, True])
    worker.serve_forever(conn, {}, {"fake": 1.0}, "w", sleep_s=0, stop=lambda: next(ticks),
                         schedulers=[lambda c, now: calls.append(now)])
    assert len(calls) == 2


def test_failing_scheduler_does_not_stop_the_worker(conn):
    def boom(c, now):
        raise RuntimeError("scheduler bug")

    ticks = iter([False, False, True])
    worker.serve_forever(conn, {}, {"fake": 1.0}, "w", sleep_s=0, stop=lambda: next(ticks), schedulers=[boom])
