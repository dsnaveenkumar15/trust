"""LLM gateway: one call shape for every backend, with caching, logging and schema validation."""
import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel, ValidationError


class LLMError(Exception):
    """Base class for every gateway failure."""


class RateLimited(LLMError):
    """Provider refused the call because a usage/rate limit was hit."""


class AuthExpired(LLMError):
    """Provider credentials are missing or expired."""


class QuotaDeferred(LLMError):
    """Our own quota scheduler refused the call; retry later."""


class BackendError(LLMError):
    """Any other backend failure (network, timeout, unparseable output)."""


class SchemaError(LLMError):
    """Backend answered but the answer does not match the schema."""


@dataclass(frozen=True)
class Prompt:
    task: str     # e.g. "trust.review"
    version: str  # e.g. "trust.review@1"; bump when the prompt text changes
    system: str
    user: str


T = TypeVar("T", bound=BaseModel)
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


def strip_fences(text: str) -> str:
    match = _FENCE.match(text)
    return match.group(1) if match else text.strip()


def input_hash(prompt: Prompt, schema: type[BaseModel]) -> str:
    blob = json.dumps([prompt.system, prompt.user, schema.model_json_schema()], sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def complete(conn, backend, prompt: Prompt, schema: type[T]) -> T:
    """Return a validated `schema` instance. Identical (backend, version, input) calls are served from cache."""
    digest = input_hash(prompt, schema)
    hit = conn.execute(
        "SELECT output FROM llm_calls WHERE backend=? AND prompt_version=? AND input_hash=? AND outcome='ok' "
        "ORDER BY id DESC LIMIT 1",
        (backend.name, prompt.version, digest),
    ).fetchone()
    if hit:
        return schema.model_validate_json(hit["output"])

    start = time.monotonic()
    output, outcome, error = None, "ok", None
    try:
        output = strip_fences(backend(prompt, schema.model_json_schema()))
        return schema.model_validate_json(output)
    except ValidationError as exc:
        outcome, error = "schema_error", str(exc)[:2000]
        raise SchemaError(error) from exc
    except LLMError as exc:
        outcome, error = type(exc).__name__, str(exc)[:2000]
        raise
    except Exception as exc:
        outcome, error = "BackendError", repr(exc)[:2000]
        raise BackendError(error) from exc
    finally:
        conn.execute(
            "INSERT INTO llm_calls(backend, task, prompt_version, input_hash, output, outcome, error, latency_ms, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (backend.name, prompt.task, prompt.version, digest, output, outcome, error,
             int((time.monotonic() - start) * 1000), time.time()),
        )


class FakeBackend:
    """Test backend: returns (or raises) the queued responses in order."""
    name = "fake"

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.calls: list[Prompt] = []

    def __call__(self, prompt: Prompt, json_schema: dict) -> str:
        self.calls.append(prompt)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response
