import pytest
from pydantic import BaseModel

from core import jobs, review
from core.llm import FakeBackend, Prompt, complete
from core.summary import build_summary


def test_add_is_idempotent_and_listable(conn):
    rid = review.add(conn, "trust", "grade_f", "server-a@sha1", {"grade": "F"}, now=1)
    assert rid is not None
    assert review.add(conn, "trust", "grade_f", "server-a@sha1", {"grade": "F"}, now=2) is None
    assert [r["ref"] for r in review.list_open(conn)] == ["server-a@sha1"]
    assert review.list_open(conn, planet="radar") == []


def test_decide_once(conn):
    rid = review.add(conn, "trust", "grade_f", "x", {}, now=1)
    assert review.decision_for(conn, "grade_f", "x") == "open"
    assert review.decide(conn, rid, "approved", "evidence checked", now=2) is True
    assert review.decide(conn, rid, "rejected", now=3) is False
    assert review.decision_for(conn, "grade_f", "x") == "approved"
    assert review.decision_for(conn, "grade_f", "missing") is None
    assert review.list_open(conn) == []


def test_invalid_decision_rejected(conn):
    rid = review.add(conn, "trust", "k", "r", {})
    with pytest.raises(ValueError):
        review.decide(conn, rid, "maybe")


class Ok(BaseModel):
    ok: bool


def test_summary_counts(conn):
    jobs.enqueue(conn, "trust", "t", "a", {}, now=100)
    jobs.enqueue(conn, "trust", "t", "b", {}, now=100)
    job = jobs.claim(conn, "w", {"trust": 1}, now=100)
    jobs.fail_job(conn, job, "x", retryable=False, now=100)
    complete(conn, FakeBackend(['{"ok": true}']), Prompt("t", "t@1", "s", "u"), Ok)
    review.add(conn, "trust", "k", "r", {})
    summary = build_summary(conn, since=50)
    assert summary["jobs"] == {"trust:dead": 1, "trust:queued": 1}
    assert summary["llm_calls"] == {"ok": 1}
    assert summary["open_reviews"] == 1
    assert summary["dead_jobs"] == 1


def test_embargo_is_a_decision_and_entry_for_returns_the_row(conn):
    rid = review.add(conn, "trust", "trust_audit", "a@b#1", {}, now=1)
    assert review.decide(conn, rid, "embargoed", "maintainer notified", now=5) is True
    entry = review.entry_for(conn, "trust_audit", "a@b#1")
    assert (entry["state"], entry["decided_at"]) == ("embargoed", 5)
    assert review.entry_for(conn, "trust_audit", "missing") is None
