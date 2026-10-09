# tests/test_trust_redteam_admin.py
import time

import pytest
from fastapi.testclient import TestClient

from core.db import connect
from tests.webutil import make_settings
from trust.redteam import runs
from web.app import create_app

AUTH = ("owner", "correct-horse-battery-staple")
ON = {"REDTEAM_ENABLED": "1", "OPENROUTER_API_KEY": "k", "REDTEAM_FAST_MODELS": "v", "REDTEAM_STRONG_MODELS": "a"}
SAME = {"origin": "http://trust.localhost"}


@pytest.fixture
def c(tmp_path, monkeypatch):
    for k, v in ON.items():
        monkeypatch.setenv(k, v)
    app = create_app(make_settings(tmp_path, admin_user="owner", admin_password=AUTH[1]))
    client = TestClient(app, base_url="http://trust.localhost")
    client.conn = connect(app.state.settings.db_path)
    runs.STORE.ensure_schema(client.conn)
    return client


def test_the_admin_page_shows_red_team_spend_switch_and_recent_runs(c):
    rid = runs.STORE.create_run(c.conn, {}, "paste", "x", time.time())
    html = c.get("/admin", auth=AUTH).text
    assert "Red Team" in html and rid[:8] in html and 'action="/admin/redteam/toggle"' in html
    assert "Turn new runs off" in html


def test_the_switch_stops_new_runs_and_the_page_says_so(c):
    assert c.post("/admin/redteam/toggle", auth=AUTH, headers=SAME, follow_redirects=False).status_code == 303
    assert not runs.STORE.is_enabled(c.conn)
    assert "not taking new runs" in c.get("/redteam").text
    assert "Turn new runs on" in c.get("/admin", auth=AUTH).text


def test_the_switch_turns_runs_back_on(c):
    c.post("/admin/redteam/toggle", auth=AUTH, headers=SAME)
    c.post("/admin/redteam/toggle", auth=AUTH, headers=SAME)
    assert runs.STORE.is_enabled(c.conn) and "not taking new runs" not in c.get("/redteam").text


def test_the_switch_needs_the_owner_and_the_same_site(c):
    assert c.post("/admin/redteam/toggle", headers=SAME).status_code == 401
    assert c.post("/admin/redteam/toggle", auth=AUTH, headers={"origin": "https://evil.example"}).status_code == 403
    assert runs.STORE.is_enabled(c.conn)
