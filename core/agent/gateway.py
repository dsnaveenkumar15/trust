# core/agent/gateway.py
"""Every model call goes through here: primary then backup model, usage and cost, budgets checked before each call."""
import threading
import time
from dataclasses import dataclass, field


# USD per million tokens charged when the provider reports no cost and the model has no listed price:
# overcounting keeps the daily cap from failing open.
FALLBACK_PRICE = (1.0, 4.0)


class BudgetExceeded(Exception):
    pass


@dataclass
class Budget:
    max_usd: float
    max_tokens: int
    max_tool_calls: int
    deadline: float                     # time.monotonic() value
    usd: float = 0.0
    tokens: int = 0
    tool_calls: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)   # scenario threads share one budget

    def check(self) -> None:
        if self.usd >= self.max_usd:
            raise BudgetExceeded("run cost budget reached")
        if self.tokens >= self.max_tokens:
            raise BudgetExceeded("run token budget reached")
        if self.tool_calls >= self.max_tool_calls:
            raise BudgetExceeded("tool call budget reached")
        if time.monotonic() >= self.deadline:
            raise BudgetExceeded("time budget reached")

    def reserve(self, seconds: float) -> None:
        """Time the final report is always allowed, even when investigating used the whole budget."""
        self.deadline = max(self.deadline, time.monotonic() + seconds)

    def add(self, usd: float, tokens: int) -> None:
        with self._lock:
            self.usd += usd
            self.tokens += tokens


def chat_factory(config):
    """Production factory: an OpenAI-compatible chat model on OpenRouter, locked to no-data-collection providers
    that support tool calling. Imported lazily so tests never need the network stack."""
    from langchain_openai import ChatOpenAI

    def make(name: str):
        extra = {"reasoning": {"effort": config.strong_effort}} if config.strong_effort and name in config.strong_models else {}
        return ChatOpenAI(model=name, api_key=config.api_key, base_url=config.base_url, timeout=60, max_retries=0,
                          temperature=0, extra_body={"provider": {"data_collection": "allow" if name in config.public_models else "deny",
                                                                  "require_parameters": True},
                                                     "usage": {"include": True}, **extra})
    return make


class Gateway:
    def __init__(self, models_by_role, factory, prices, budget: Budget, on_usage, day_guard):
        self.models_by_role, self.factory, self.prices = models_by_role, factory, prices
        self.budget, self.on_usage, self.day_guard = budget, on_usage, day_guard
        self.failures = 0                       # calls where every model failed (bad key, no credit, provider down)

    def _cost(self, model: str, msg) -> tuple[float, int]:
        usage = getattr(msg, "usage_metadata", None) or {}
        tin, tout = int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
        reported = ((getattr(msg, "response_metadata", None) or {}).get("token_usage") or {}).get("cost")
        if reported is not None:
            return float(reported), tin + tout
        pin, pout = self.prices.get(model, FALLBACK_PRICE)
        return tin * pin / 1e6 + tout * pout / 1e6, tin + tout

    def invoke(self, messages: list, tools: list | None = None, role: str = "fast"):
        self.budget.check()
        self.day_guard()
        last_error = None
        for name in self.models_by_role.get(role) or []:
            model = self.factory(name)
            try:
                msg = (model.bind_tools(tools) if tools else model).invoke(messages)
            except BudgetExceeded:
                raise
            except Exception as exc:                 # provider down, rate limited, malformed response: try the backup
                last_error = exc
                continue
            usd, tokens = self._cost(name, msg)
            self.budget.add(usd, tokens)
            provider = str((getattr(msg, "response_metadata", None) or {}).get("model_provider") or "openrouter")
            self.on_usage(name, provider, usd, tokens)
            return msg
        self.failures += 1
        raise RuntimeError(f"all models failed for {role}: {type(last_error).__name__}")   # never echo provider text (may carry request details)
