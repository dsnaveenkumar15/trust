import json

import httpx
import pytest

from core.control import batch, spend, store
from core.db import connect
from core.llm import AuthExpired, BackendError, Prompt, QuotaDeferred, RateLimited
from core.openrouter import OpenRouterBackend

P = Prompt("t.review", "t.review@1", "You review.", "Review this.")
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}}


def mock(status=200, content='{"ok": true}', cost=0.002, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(json.loads(request.content))
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "x"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}],
                                         "usage": {"total_tokens": 120, "cost": cost}})
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def conn(tmp_path):
    c = connect(str(tmp_path / "s.db"))
    store.ensure(c)
    return c


def test_openrouter_backend_sends_system_user_schema_and_records_usage():
    seen = []
    b = OpenRouterBackend("m/x", "k", client=mock(seen=seen))
    assert b(P, SCHEMA) == '{"ok": true}' and b.last_usage == {"usd": 0.002, "tokens": 120}
    body = seen[0]
    assert body["model"] == "m/x" and body["temperature"] == 0 and body["messages"][0]["role"] == "system"
    assert "Review this." in body["messages"][1]["content"] and body["provider"]["data_collection"] == "deny"


@pytest.mark.parametrize("status, error", [(429, RateLimited), (401, AuthExpired), (403, AuthExpired), (500, BackendError)])
def test_openrouter_errors_map_to_the_gateway_types(status, error):
    with pytest.raises(error):
        OpenRouterBackend("m", "k", client=mock(status=status))(P, SCHEMA)


def test_a_bad_body_is_a_backend_error():
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"nope")))
    with pytest.raises(BackendError):
        OpenRouterBackend("m", "k", client=bad)(P, SCHEMA)


def test_the_cap_records_spend_and_defers_until_tomorrow(conn):
    now = 1_790_000_000.0
    capped = spend.CappedBackend(conn, OpenRouterBackend("m", "k", client=mock(cost=0.02)), "", 0.03, clock=lambda: now)
    capped(P, SCHEMA)
    assert spend.today(conn, now) == {"usd": 0.02, "tokens": 120, "calls": 1}
    capped(P, SCHEMA)
    with pytest.raises(QuotaDeferred) as e:
        capped(P, SCHEMA)
    assert e.value.reset_at > now


def test_switch_stop_all_and_console_cap(conn):
    now = 1_790_000_000.0
    inner = OpenRouterBackend("m", "k", client=mock(cost=0.001))
    capped = spend.CappedBackend(conn, inner, "", 0.03, clock=lambda: now)
    store.set(conn, "batch.enabled", False, "", now, set())
    with pytest.raises(QuotaDeferred):
        capped(P, SCHEMA)
    store.clear(conn, "batch.enabled", "", now)
    store.set(conn, "global.stop_all", True, "", now, set())
    with pytest.raises(QuotaDeferred):
        capped(P, SCHEMA)
    store.clear(conn, "global.stop_all", "", now)
    store.set(conn, "batch.daily_usd", 0, "", now, set())
    with pytest.raises(QuotaDeferred):
        capped(P, SCHEMA)


def test_days_lists_thirty_days_oldest_first(conn):
    now = 1_790_000_000.0
    capped = spend.CappedBackend(conn, OpenRouterBackend("m", "k", client=mock(cost=0.01)), "", 0.03, clock=lambda: now)
    capped(P, SCHEMA)
    days = spend.days(conn, now)
    assert len(days) == 30 and days[-1]["usd"] == 0.01 and days[0]["usd"] == 0.0 and days[0]["date"] < days[-1]["date"]


def test_make_backend_prefers_openrouter_and_falls_back_to_claude(conn):
    b = batch.make_backend(conn, {"OPENROUTER_API_KEY": "k", "BATCH_MODEL": "m/x"}, "TRUST_MODEL")
    assert b.name == "openrouter" and b.model == "m/x"
    c = batch.make_backend(conn, {"TRUST_MODEL": "sonnet"}, "TRUST_MODEL")
    assert c.name == "claude_cli"


from pydantic import BaseModel, Field  # noqa: E402


class _Item(BaseModel):
    quote: str = Field(max_length=10)
    note: str | None = Field(default=None, max_length=8)


class _Out(BaseModel):
    summary: str = Field(max_length=20)
    items: list[_Item] = Field(default_factory=list, max_length=2)
    tags: list[str] = Field(default_factory=list, max_length=3)


def _long(model_json):
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
        "choices": [{"message": {"content": model_json}}], "usage": {"total_tokens": 9, "cost": 0.001}})))


def test_over_long_text_and_lists_are_cut_to_the_schemas_limits_not_rejected():
    from core.llm import Prompt
    reply = json.dumps({"summary": "s" * 50, "items": [{"quote": "q" * 30, "note": "n" * 30}, {"quote": "ok"}, {"quote": "dropped"}],
                        "tags": ["a", "b", "c", "d", "e"]})
    text = OpenRouterBackend("m", "k", client=_long(reply))(Prompt("t", "t@1", "sys", "usr"), _Out.model_json_schema())
    out = _Out.model_validate_json(text)                                      # validates: nothing to reject any more
    assert out.summary == "s" * 20 and len(out.items) == 2 and out.items[0].quote == "q" * 10 and out.items[0].note == "n" * 8
    assert out.tags == ["a", "b", "c"] and out.items[1].quote == "ok"


def test_a_cut_quote_is_still_a_verbatim_prefix_without_an_ellipsis():
    from core.llm import Prompt
    reply = json.dumps({"summary": "fine", "items": [{"quote": "abcdefghijklmnop"}]})
    out = _Out.model_validate_json(OpenRouterBackend("m", "k", client=_long(reply))(Prompt("t", "t@1", "s", "u"), _Out.model_json_schema()))
    assert out.items[0].quote == "abcdefghij" and "…" not in out.items[0].quote


def test_a_reply_that_is_not_json_is_returned_untouched_for_the_gateway_to_reject():
    from core.llm import Prompt
    assert OpenRouterBackend("m", "k", client=_long("not json at all"))(Prompt("t", "t@1", "s", "u"), _Out.model_json_schema()) == "not json at all"


def test_fenced_json_is_clipped_too():
    from core.llm import Prompt
    reply = "```json\n" + json.dumps({"summary": "x" * 99}) + "\n```"
    out = _Out.model_validate_json(OpenRouterBackend("m", "k", client=_long(reply))(Prompt("t", "t@1", "s", "u"), _Out.model_json_schema()))
    assert out.summary == "x" * 20
