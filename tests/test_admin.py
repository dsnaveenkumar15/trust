import pytest
from fastapi.testclient import TestClient

from core import jobs, review
from core.db import connect
from tests.webutil import make_settings
from web.config import Settings
from web.app import create_app

AUTH = ("owner", "correct-horse-battery-staple")
HOST = f"http://{Settings.detect_planets()[0]}.localhost"     # the first planet this repo holds
ORIGIN = {"origin": HOST}


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path, admin_user="owner", admin_password="correct-horse-battery-staple")


@pytest.fixture
def c(settings):
    return TestClient(create_app(settings), base_url=HOST)


@pytest.fixture
def db(settings):
    conn = connect(settings.db_path)
    yield conn
    conn.close()


def test_admin_disabled_without_credentials_configured(tmp_path):
    c = TestClient(create_app(make_settings(tmp_path)), base_url=HOST)
    assert c.get("/admin", auth=AUTH).status_code == 404


def test_wrong_credentials_get_401_challenge(c):
    r = c.get("/admin", auth=("owner", "nope"))
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Basic")
    assert c.get("/admin").status_code == 401


def test_dashboard_lists_open_reviews_dead_jobs_and_quota(c, db):
    review.add(db, "trust", "grade_f", "server-a@abc123", {"grade": "F"})
    jobs.enqueue(db, "trust", "trust.audit", "x@1", {})
    job = jobs.claim(db, "w", {"trust": 1})
    jobs.fail_job(db, job, "boom", retryable=False)
    r = c.get("/admin", auth=AUTH)
    assert r.status_code == 200
    assert "server-a@abc123" in r.text
    assert "trust.audit" in r.text and "boom" in r.text
    assert "allowance" in r.text
    assert 'name="robots" content="noindex"' in r.text


def test_decide_review_with_same_origin(c, db):
    rid = review.add(db, "trust", "grade_f", "s@1", {})
    r = c.post(f"/admin/reviews/{rid}", data={"decision": "approved", "note": "ok"}, auth=AUTH, headers=ORIGIN,
               follow_redirects=False)
    assert r.status_code == 303
    assert review.decision_for(db, "grade_f", "s@1") == "approved"


def test_post_without_or_with_foreign_origin_is_refused(c, db):
    rid = review.add(db, "trust", "grade_f", "s@2", {})
    assert c.post(f"/admin/reviews/{rid}", data={"decision": "approved"}, auth=AUTH).status_code == 403
    assert c.post(f"/admin/reviews/{rid}", data={"decision": "approved"}, auth=AUTH,
                  headers={"origin": "http://evil.example"}).status_code == 403
    assert review.decision_for(db, "grade_f", "s@2") == "open"


def test_invalid_decision_is_400(c, db):
    rid = review.add(db, "trust", "grade_f", "s@3", {})
    r = c.post(f"/admin/reviews/{rid}", data={"decision": "maybe"}, auth=AUTH, headers=ORIGIN)
    assert r.status_code == 400


def test_retry_dead_job(c, db):
    job_id = jobs.enqueue(db, "trust", "trust.audit", "y@1", {})
    jobs.fail_job(db, jobs.claim(db, "w", {"trust": 1}), "boom", retryable=False)
    r = c.post(f"/admin/jobs/{job_id}/retry", auth=AUTH, headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303
    assert db.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()[0] == "queued"


@pytest.mark.parametrize("password", ["change-me", "short-password1"])
def test_admin_refuses_default_or_weak_password(tmp_path, password):
    c = TestClient(create_app(make_settings(tmp_path, admin_user="owner", admin_password=password)),
                   base_url=HOST)
    assert c.get("/admin", auth=("owner", password)).status_code == 404


def test_admin_requests_are_rate_limited_per_ip(c):
    codes = [c.get("/admin", auth=("owner", f"guess-{i}")).status_code for i in range(40)]
    assert codes[0] == 401
    assert 429 in codes
    assert codes[-1] == 429


def test_embargo_decision_from_admin(c, db):
    rid = review.add(db, "trust", "trust_audit", "s@4", {})
    assert 'value="embargoed"' in c.get("/admin", auth=AUTH).text
    r = c.post(f"/admin/reviews/{rid}", data={"decision": "embargoed"}, auth=AUTH, headers=ORIGIN,
               follow_redirects=False)
    assert r.status_code == 303 and review.decision_for(db, "trust_audit", "s@4") == "embargoed"
