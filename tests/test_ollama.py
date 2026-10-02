import json

import httpx
import pytest

from core.llm import BackendError, Prompt
from core.ollama import OllamaBackend

P = Prompt("demo", "demo@1", "sys", "user")
SCHEMA = {"type": "object"}


def backend(handler):
    client = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    return OllamaBackend(client=client)


def test_returns_message_content_and_sends_schema():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": '{"a": 1}'}})

    assert backend(handler)(P, SCHEMA) == '{"a": 1}'
    assert seen["body"]["format"] == SCHEMA
    assert seen["body"]["stream"] is False
    assert seen["body"]["options"]["temperature"] == 0
    assert [m["role"] for m in seen["body"]["messages"]] == ["system", "user"]


def test_http_error_status_is_backend_error():
    with pytest.raises(BackendError):
        backend(lambda r: httpx.Response(500, text="model not loaded"))(P, SCHEMA)


def test_connection_failure_is_backend_error():
    def handler(request):
        raise httpx.ConnectError("refused")
    with pytest.raises(BackendError):
        backend(handler)(P, SCHEMA)
