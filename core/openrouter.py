"""Batch model calls on OpenRouter, with the same call shape as the Claude CLI backend: (prompt, json_schema) -> JSON text.
Providers that keep prompts are excluded; the reported cost of each call is kept for the daily cap."""
import json
import re

import httpx

from core.llm import AuthExpired, BackendError, Prompt, RateLimited


FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


def _resolve(schema: dict, defs: dict) -> dict:
    ref = schema.get("$ref")
    return defs.get(ref.rsplit("/", 1)[-1], {}) if ref else schema


def clip_to_schema(value, schema: dict, defs: dict | None = None):
    """Cut over-long text and lists down to the limits the schema itself declares (maxLength, maxItems), the way a person
    would shorten a long answer. Models often ignore length hints; rejecting a sound answer for a long sentence would
    waste the whole call. A cut quote stays a verbatim prefix: no ellipsis is added."""
    defs = defs if defs is not None else schema.get("$defs", {})
    schema = _resolve(schema, defs)
    if "anyOf" in schema:                                   # Optional[...]: use the branch that fits the value's type
        want = {str: "string", list: "array", dict: "object"}.get(type(value))
        for branch in schema["anyOf"]:
            b = _resolve(branch, defs)
            if b.get("type") == want or (want == "object" and "properties" in b):
                return clip_to_schema(value, b, defs)
        return value
    if isinstance(value, str):
        limit = schema.get("maxLength")
        return value[:limit].rstrip() if isinstance(limit, int) and len(value) > limit else value
    if isinstance(value, list):
        limit = schema.get("maxItems")
        items = value[:limit] if isinstance(limit, int) else value
        return [clip_to_schema(v, schema.get("items", {}), defs) for v in items]
    if isinstance(value, dict):
        props = schema.get("properties", {})
        return {k: clip_to_schema(v, props[k], defs) if k in props else v for k, v in value.items()}
    return value


def _clip_reply(text: str, json_schema: dict) -> str:
    """The reply with over-long values cut to the schema; anything that is not a JSON object is returned untouched."""
    match = FENCE.match(text)
    raw = match.group(1) if match else text
    try:
        data = json.loads(raw)
    except ValueError:
        return text
    return json.dumps(clip_to_schema(data, json_schema), ensure_ascii=False) if isinstance(data, dict) else text


class OpenRouterBackend:
    name = "openrouter"

    def __init__(self, model: str, api_key: str, base_url: str = "https://openrouter.ai/api/v1", client: httpx.Client | None = None):
        self.model, self.api_key, self.base_url = model, api_key, base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=120)
        self.last_usage = {"usd": 0.0, "tokens": 0}

    def __call__(self, prompt: Prompt, json_schema: dict) -> str:
        body = {"model": self.model, "temperature": 0, "usage": {"include": True},
                "provider": {"data_collection": "deny"},
                "messages": [{"role": "system", "content": prompt.system + " Reply with one JSON object only, matching this "
                                                                         "JSON schema: " + json.dumps(json_schema, separators=(",", ":"))},
                             {"role": "user", "content": prompt.user}]}
        try:
            r = self.client.post(f"{self.base_url}/chat/completions", json=body,
                                 headers={"Authorization": f"Bearer {self.api_key}"})
        except httpx.HTTPError as exc:
            raise BackendError(f"openrouter unreachable: {type(exc).__name__}") from exc
        if r.status_code == 429:
            raise RateLimited("openrouter rate limited")
        if r.status_code in (401, 402, 403):
            raise AuthExpired(f"openrouter refused the key ({r.status_code})")
        if r.status_code != 200:
            raise BackendError(f"openrouter HTTP {r.status_code}")
        try:
            data = r.json()
            text = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise BackendError("openrouter returned an unreadable reply") from exc
        usage = data.get("usage") or {}
        self.last_usage = {"usd": float(usage.get("cost") or 0.0), "tokens": int(usage.get("total_tokens") or 0)}
        if not isinstance(text, str) or not text.strip():
            raise BackendError("openrouter returned an empty reply")
        return _clip_reply(text, json_schema)
