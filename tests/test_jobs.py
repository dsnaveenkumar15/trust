import pytest

from core import jobs

W = {"trust": 50.0, "radar": 35.0}


def state(conn, job_id):
    return conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


def test_enqueue_is_idempotent(conn):
    a = jobs.enqueue(conn, "trust", "trust.audit", "repo@sha1", {"x": 1}, now=0)
    b = jobs.enqueue(conn, "trust", "trust.audit", "repo@sha1", {"x": 2}, now=1)
    assert a == b
    assert state(conn, a)["payload"] == '{"x": 1}'


def test_claim_respects_lanes_and_runnable_time(conn):
    jobs.enqueue(conn, "blueprints", "bp.gen", "k", {}, now=0)
    assert jobs.claim(conn, "w1", W, now=10) is None            # planet not in weights
    jobs.enqueue(conn, "trust", "trust.audit", "k", {}, now=100)
    assert jobs.claim(conn, "w1", W, now=50) is None            # not yet runnable
    assert jobs.claim(conn, "w1", W, now=100).planet == "trust"


def test_claim_orders_by_weight_times_priority(conn):
    jobs.enqueue(conn, "trust", "t", "a", {}, priority=1.0, now=0)
    jobs.enqueue(conn, "radar", "r", "b", {}, priority=2.0, now=0)   # 70 > 50
    assert jobs.claim(conn, "w1", W, now=0).planet == "radar"
    assert jobs.claim(conn, "w1", W, now=0).planet == "trust"


def test_leased_job_is_not_claimed_twice(conn):
    jobs.enqueue(conn, "trust", "t", "a", {}, now=0)
    assert jobs.claim(conn, "w1", W, lease_s=60, now=0) is not None
    assert jobs.claim(conn, "w2", W, lease_s=60, now=30) is None


def test_expired_lease_is_reclaimed_and_poison_job_goes_dead(conn):
    job_id = jobs.enqueue(conn, "trust", "t", "a", {}, now=0)
    assert jobs.claim(conn, "w", W, lease_s=10, now=0).attempts == 0
    assert jobs.claim(conn, "w", W, lease_s=10, now=11).attempts == 1   # worker died; reclaimed
    assert jobs.claim(conn, "w", W, lease_s=10, now=22).attempts == 2
    assert jobs.claim(conn, "w", W, lease_s=10, now=33) is None          # third expiry: poison
    assert state(conn, job_id)["state"] == "dead"


def test_run_stages_resumes_without_rerunning_completed_stages(conn):
    jobs.enqueue(conn, "trust", "t", "a", {"n": 2}, now=0)
    job = jobs.claim(conn, "w", W, now=0)
    calls = []

    def fetch(job, out):
        calls.append("fetch")
        return {"files": job.payload["n"]}

    attempt = {"n": 0}

    def review(job, out):
        calls.append("review")
        attempt["n"] += 1
        if attempt["n"] == 1:
            raise RuntimeError("crash mid-review")
        return {"seen": out["fetch"]["files"]}

    stages = [("fetch", fetch), ("review", review)]
    with pytest.raises(RuntimeError):
        jobs.run_stages(conn, job, stages)
    result = jobs.run_stages(conn, job, stages)
    assert result == {"fetch": {"files": 2}, "review": {"seen": 2}}
    assert calls == ["fetch", "review", "review"]


def test_run_stages_calls_heartbeat_after_each_stage(conn):
    jobs.enqueue(conn, "trust", "t", "a", {}, now=0)
    job = jobs.claim(conn, "w", W, now=0)
    beats = []
    jobs.run_stages(conn, job, [("a", lambda j, o: 1), ("b", lambda j, o: 2)], heartbeat=lambda: beats.append(1))
    assert len(beats) == 2


def test_fail_job_backs_off_then_dies(conn):
    job_id = jobs.enqueue(conn, "trust", "t", "a", {}, now=0)
    job = jobs.claim(conn, "w", W, now=0)
    assert jobs.fail_job(conn, job, "boom", now=0) == "queued"
    first_delay = state(conn, job_id)["next_run_at"]
    assert jobs.BASE_BACKOFF_S <= first_delay <= jobs.BASE_BACKOFF_S * 1.25
    job = jobs.claim(conn, "w", W, now=10_000)
    assert jobs.fail_job(conn, job, "boom", now=10_000) == "queued"
    assert state(conn, job_id)["next_run_at"] - 10_000 >= 2 * jobs.BASE_BACKOFF_S
    job = jobs.claim(conn, "w", W, now=20_000)
    assert jobs.fail_job(conn, job, "boom", now=20_000) == "dead"


def test_non_retryable_failure_goes_straight_to_dead(conn):
    job_id = jobs.enqueue(conn, "trust", "t", "a", {}, now=0)
    job = jobs.claim(conn, "w", W, now=0)
    assert jobs.fail_job(conn, job, "unsupported", retryable=False, now=0) == "dead"
    assert state(conn, job_id)["last_error"] == "unsupported"


def test_defer_keeps_attempts(conn):
    job_id = jobs.enqueue(conn, "trust", "t", "a", {}, now=0)
    job = jobs.claim(conn, "w", W, now=0)
    jobs.defer_job(conn, job, until=5_000, reason="quota", now=0)
    row = state(conn, job_id)
    assert (row["state"], row["attempts"], row["next_run_at"]) == ("queued", 0, 5_000)


def test_heartbeat_only_extends_own_lease(conn):
    job_id = jobs.enqueue(conn, "trust", "t", "a", {}, now=0)
    jobs.claim(conn, "w1", W, lease_s=10, now=0)
    assert jobs.heartbeat(conn, job_id, "w2", lease_s=100, now=5) is False
    assert jobs.heartbeat(conn, job_id, "w1", lease_s=100, now=5) is True
    assert state(conn, job_id)["lease_until"] == 105


def test_complete_and_retry_dead(conn):
    job_id = jobs.enqueue(conn, "trust", "t", "a", {}, now=0)
    job = jobs.claim(conn, "w", W, now=0)
    jobs.fail_job(conn, job, "x", retryable=False, now=0)
    assert jobs.retry_dead(conn, job_id, now=50) is True
    job = jobs.claim(conn, "w", W, now=50)
    assert job.attempts == 0
    jobs.complete_job(conn, job_id, now=60)
    assert state(conn, job_id)["state"] == "done"
    assert jobs.retry_dead(conn, job_id, now=70) is False
