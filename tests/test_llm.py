from typing import Literal

import pytest
from pydantic import BaseModel

from core.llm import (BackendError, FakeBackend, Prompt, RateLimited, SchemaError,
                      complete, strip_fences)


class Answer(BaseModel):
    verdict: Literal["yes", "no"]
    reason: str


P = Prompt(task="demo.judge", version="demo.judge@1", system="Judge.", user="Is it safe?")
OK = '{"verdict": "yes", "reason": "fine"}'


def outcomes(conn):
    return [r["outcome"] for r in conn.execute("SELECT outcome FROM llm_calls ORDER BY id")]


def test_valid_output_is_parsed_and_logged(conn):
    result = complete(conn, FakeBackend([OK]), P, Answer)
    assert result == Answer(verdict="yes", reason="fine")
    assert outcomes(conn) == ["ok"]


def test_identical_call_hits_cache(conn):
    backend = FakeBackend([OK])
    complete(conn, backend, P, Answer)
    assert complete(conn, backend, P, Answer).verdict == "yes"
    assert len(backend.calls) == 1


def test_new_prompt_version_misses_cache(conn):
    backend = FakeBackend([OK, OK])
    complete(conn, backend, P, Answer)
    complete(conn, backend, Prompt(P.task, "demo.judge@2", P.system, P.user), Answer)
    assert len(backend.calls) == 2


def test_fenced_json_is_accepted(conn):
    assert complete(conn, FakeBackend([f"```json\n{OK}\n```"]), P, Answer).verdict == "yes"


def test_schema_violation_raises_and_is_not_cached(conn):
    backend = FakeBackend(['{"verdict": "maybe", "reason": "x"}', OK])
    with pytest.raises(SchemaError):
        complete(conn, backend, P, Answer)
    assert complete(conn, backend, P, Answer).verdict == "yes"
    assert outcomes(conn) == ["schema_error", "ok"]


def test_backend_llm_error_propagates_and_is_logged(conn):
    with pytest.raises(RateLimited):
        complete(conn, FakeBackend([RateLimited("usage limit")]), P, Answer)
    assert outcomes(conn) == ["RateLimited"]


def test_unexpected_exception_becomes_backend_error(conn):
    with pytest.raises(BackendError):
        complete(conn, FakeBackend([ZeroDivisionError("bug")]), P, Answer)
    assert outcomes(conn) == ["BackendError"]


def test_strip_fences():
    assert strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_fences('```\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_fences('  {"a": 1}  ') == '{"a": 1}'
