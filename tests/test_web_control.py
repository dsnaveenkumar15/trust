import pytest
from fastapi.testclient import TestClient

from tests.webutil import make_settings
from web.app import create_app

TOKEN = "t" * 40


@pytest.fixture
def app(tmp_path, monkeypatch):
    from core.control import brake
    from tests.trust_redteam_fakes import FakeModel, polite
    for k, v in {"CONTROL_TOKEN": TOKEN, "REDTEAM_ENABLED": "1", "OPENROUTER_API_KEY": "k", "REDTEAM_FAST_MODELS": "v"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(brake, "_fetch", lambda key, client: None)             # no network: the brake reports "unknown"
    a = create_app(make_settings(tmp_path))
    a.state.redteam_factory = lambda name: FakeModel(analyst=[RuntimeError("x")], victim=polite)
    return a


def call(app, method="get", headers=None, host="trust.localhost", **kw):
    c = TestClient(app, base_url=f"http://{host}")
    return getattr(c, method)("/internal/control", headers={"X-Control-Token": TOKEN, **(headers or {})}, **kw)


def test_the_report_lists_agents_limits_usage_and_secret_presence_only(app):
    r = call(app)
    assert r.status_code == 200
    body = r.json()
    agent = next(a for a in body["agents"] if a["prefix"] == "redteam")
    assert agent["effective"]["daily_usd"] == agent["env"]["daily_usd"] and len(agent["days"]) == 30
    assert agent["models"]["victim"] == ["v"]
    assert body["secrets"]["CONTROL_TOKEN"] == "set" and TOKEN not in r.text
    assert set(body) >= {"app", "now", "agents", "batch", "global", "worker", "secrets", "github_token_expires", "changes"}
    assert len(body["batch"]["days"]) == 30 and body["global"]["brake_pct"] == 90 and body["global"]["stop_all"] is False


@pytest.mark.parametrize("headers", [{"X-Control-Token": "wrong"}, {"X-Forwarded-For": "1.2.3.4"}, {"X-Real-IP": "1.2.3.4"},
                                     {"X-Forwarded-Host": "trust.naveenlabs.tech"}])
def test_wrong_token_or_a_proxied_request_is_a_404(app, headers):
    assert call(app, headers=headers).status_code == 404
    assert call(app, "post", headers=headers, json={"key": "global.stop_all", "value": True}).status_code == 404


def test_no_token_configured_means_no_interface(tmp_path, monkeypatch):
    monkeypatch.delenv("CONTROL_TOKEN", raising=False)
    a = create_app(make_settings(tmp_path))
    assert TestClient(a, base_url="http://trust.localhost").get("/internal/control", headers={"X-Control-Token": ""}).status_code == 404


def test_a_change_is_applied_validated_and_recorded(app):
    ok = call(app, "post", json={"key": "redteam.daily_usd", "value": 0.5, "note": "demo"})
    assert ok.status_code == 200 and ok.json()["effective"] == 0.5
    bad = call(app, "post", json={"key": "redteam.daily_usd", "value": 9})
    assert bad.status_code == 400 and "between" in bad.json()["error"]
    assert call(app, "post", json={"key": "redteam.models", "value": 1}).status_code == 400
    body = call(app).json()
    agent = next(a for a in body["agents"] if a["prefix"] == "redteam")
    assert agent["overrides"] == {"daily_usd": 0.5} and agent["effective"]["daily_usd"] == 0.5 and body["changes"][0]["note"] == "demo"
    back = call(app, "post", json={"key": "redteam.daily_usd", "value": None})
    assert back.json()["effective"] == agent["env"]["daily_usd"]


def test_the_agents_own_switch_follows_the_console(app):
    call(app, "post", json={"key": "redteam.enabled", "value": False})
    agent = next(a for a in call(app).json()["agents"] if a["prefix"] == "redteam")
    assert agent["effective"]["enabled"] is False
    call(app, "post", json={"key": "redteam.enabled", "value": None})
    agent = next(a for a in call(app).json()["agents"] if a["prefix"] == "redteam")
    assert agent["effective"]["enabled"] is True


def test_stop_all_refuses_a_visitor_run_and_clearing_restores_it(app):
    body = {"kind": "manifest", "consent": True, "text": '{"tools": [{"name": "a", "description": "b"}]}'}
    visitor = TestClient(app, base_url="http://trust.localhost")
    origin = {"Origin": "http://trust.localhost"}
    call(app, "post", json={"key": "global.stop_all", "value": True, "note": "test"})
    refused = visitor.post("/api/redteam/runs", json=body, headers=origin)
    assert refused.status_code == 503 and "Paused by the owner" in refused.json()["detail"]
    call(app, "post", json={"key": "global.stop_all", "value": None})
    assert visitor.post("/api/redteam/runs", json=body, headers=origin).status_code != 503


def test_the_interface_answers_on_any_planet_host(app):
    assert call(app, host="trust.localhost").status_code == 200


def test_a_non_ascii_token_is_a_404_not_a_server_error(app):
    c = TestClient(app, base_url="http://trust.localhost")
    r = c.get("/internal/control", headers={b"X-Control-Token": "é".encode("latin-1")})
    assert r.status_code == 404
