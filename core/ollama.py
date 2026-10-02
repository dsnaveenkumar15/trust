"""Ollama backend for live features. Internal network only; Ollama has no auth of its own."""
import httpx

from core.llm import BackendError, Prompt


class OllamaBackend:
    name = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "qwen2.5:1.5b",
                 timeout_s: float = 120, client: httpx.Client | None = None):
        self.model = model
        self.client = client or httpx.Client(base_url=base_url, timeout=timeout_s)

    def __call__(self, prompt: Prompt, json_schema: dict) -> str:
        body = {
            "model": self.model,
            "stream": False,
            "format": json_schema,
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
        }
        try:
            response = self.client.post("/api/chat", json=body)
        except httpx.HTTPError as exc:
            raise BackendError(f"ollama unreachable: {exc}") from exc
        if response.status_code != 200:
            raise BackendError(f"ollama {response.status_code}: {response.text[:500]}")
        return response.json()["message"]["content"]
