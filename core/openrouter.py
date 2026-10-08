"""Batch model calls on OpenRouter, with the same call shape as the Claude CLI backend: (prompt, json_schema) -> JSON text.
Providers that keep prompts are excluded; the reported cost of each call is kept for the daily cap."""
import json

import httpx

from core.llm import AuthExpired, BackendError, Prompt, RateLimited


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
        return text
