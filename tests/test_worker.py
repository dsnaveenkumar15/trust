import json
import logging

from core import jobs, worker
from core.llm import QuotaDeferred
from core.log import JsonFormatter
from core.worker import JobType, PermanentError, run_once

W = {"fake": 1.0}


def state(conn, job_id):
    return conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


def test_done_path(conn):
    registry = worker.load_registry(["tests.fake_planet"])
    job_id = jobs.enqueue(conn, "fake", "fake.echo", "k", {"a": 1}, now=0)
    assert run_once(conn, registry, W, "w", clock=lambda: 0) == "done"
    assert state(conn, job_id)["state"] == "done"
    assert run_once(conn, registry, W, "w", clock=lambda: 0) is None


def test_unknown_job_type_goes_dead(conn):
    job_id = jobs.enqueue(conn, "fake", "fake.nope", "k", {}, now=0)
    assert run_once(conn, {}, W, "w", clock=lambda: 0) == "dead"
    assert "unknown job type" in state(conn, job_id)["last_error"]


def test_quota_deferral_keeps_attempts_and_cached_stages(conn):
    calls = []

    def fetch(job, out):
        calls.append("fetch")
        return 1

    def review(job, out):
        raise QuotaDeferred("closed")

    registry = {"fake.llm": JobType("fake", [("fetch", fetch), ("review", review)])}
    job_id = jobs.enqueue(conn, "fake", "fake.llm", "k", {}, now=0)
    noon_ist = 6.5 * 3600  # 1970-01-01 12:00 IST: outside the night window
    assert run_once(conn, registry, W, "w", clock=lambda: noon_ist) == "deferred"
    row = state(conn, job_id)
    assert (row["state"], row["attempts"]) == ("queued", 0)
    assert row["next_run_at"] == noon_ist + 11 * 3600  # deferred to 23:00 IST
    assert conn.execute("SELECT count(*) FROM stage_artifacts WHERE job_id=?", (job_id,)).fetchone()[0] == 1


def test_permanent_error_goes_dead(conn):
    def bad(job, out):
        raise PermanentError("unsupported language")

    job_id = jobs.enqueue(conn, "fake", "fake.bad", "k", {}, now=0)
    assert run_once(conn, {"fake.bad": JobType("fake", [("s", bad)])}, W, "w", clock=lambda: 0) == "dead"
    assert state(conn, job_id)["last_error"] == "permanent: unsupported language"


def test_unexpected_error_is_retried(conn):
    def boom(job, out):
        raise RuntimeError("flaky network")

    job_id = jobs.enqueue(conn, "fake", "fake.boom", "k", {}, now=0)
    assert run_once(conn, {"fake.boom": JobType("fake", [("s", boom)])}, W, "w", clock=lambda: 0) == "failed"
    assert state(conn, job_id)["attempts"] == 1


def test_parse_weights():
    assert worker.parse_weights("trust=50, radar=35,blueprints=15") == {"trust": 50.0, "radar": 35.0,
                                                                       "blueprints": 15.0}
    assert worker.parse_weights("") == {}


def test_serve_forever_pushes_heartbeat(conn, monkeypatch):
    pushed = []
    monkeypatch.setattr(worker.httpx, "get", lambda url, **kw: pushed.append((url, kw["params"])))
    ticks = iter([False, False, True])
    worker.serve_forever(conn, {}, W, "w", sleep_s=0, heartbeat_url="http://kuma/push/abc",
                         stop=lambda: next(ticks))
    assert pushed and pushed[0][0] == "http://kuma/push/abc"
    assert pushed[0][1]["status"] == "up"


def test_json_log_formatter():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    data = json.loads(JsonFormatter().format(record))
    assert (data["level"], data["msg"], data["logger"]) == ("INFO", "hello world", "x")


def test_load_registry_passes_conn_to_builders(conn):
    registry = worker.load_registry(["tests.fake_planet_builder", "tests.fake_planet"], conn)
    assert set(registry) == {"fake.built", "fake.echo"}
    jobs.enqueue(conn, "fake", "fake.built", "k", {}, now=0)
    assert run_once(conn, registry, W, "w", clock=lambda: 0) == "done"
    assert conn.execute("SELECT count(*) FROM events WHERE type='built'").fetchone()[0] == 1


def test_github_rate_limit_defers_to_its_reset_time_not_the_claude_window(conn):
    from core.github import GitHubRateLimited
    noon_ist = 6.5 * 3600

    def fetch(job, out):
        raise GitHubRateLimited("limit", noon_ist + 900)

    job_id = jobs.enqueue(conn, "fake", "fake.gh", "k", {}, now=0)
    assert run_once(conn, {"fake.gh": JobType("fake", [("s", fetch)])}, W, "w", clock=lambda: noon_ist) == "deferred"
    assert state(conn, job_id)["next_run_at"] == noon_ist + 900


def test_deferral_never_retries_immediately(conn):
    from core.llm import RateLimited
    night_ist = 18.0 * 3600   # 1970-01-01 23:30 IST: inside the night window, quota open

    def limited(job, out):
        raise RateLimited("429")

    job_id = jobs.enqueue(conn, "fake", "fake.rl", "k", {}, now=0)
    assert run_once(conn, {"fake.rl": JobType("fake", [("s", limited)])}, W, "w", clock=lambda: night_ist) == "deferred"
    assert state(conn, job_id)["next_run_at"] >= night_ist + 60
