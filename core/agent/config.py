# core/agent/config.py
"""Agent settings from the environment, one prefix per app (AGENT_ for Radar's upgrade agent, ARCHITECT_ for the
Blueprints architect). The OpenRouter key is shared. Off unless <PREFIX>ENABLED=1, a key and a fast model are set."""
import os
from dataclasses import dataclass, field


def env_list(raw: str) -> list[str]:
    return [p.strip() for p in (raw or "").split(",") if p.strip()]


def env_prices(raw: str) -> dict[str, tuple[float, float]]:
    """`model=in/out;...` in USD per million tokens. Model ids may contain ':' (free tiers), so split on the last '='."""
    out = {}
    for part in (raw or "").split(";"):
        if "=" in part and "/" in part.rsplit("=", 1)[1]:
            name, pair = part.rsplit("=", 1)
            a, b = pair.split("/", 1)
            out[name.strip()] = (float(a), float(b))
    return out


@dataclass
class AgentConfig:
    enabled: bool = False
    api_key: str = ""
    base_url: str = "https://openrouter.ai/api/v1"
    fast_models: list[str] = field(default_factory=list)
    strong_models: list[str] = field(default_factory=list)
    public_models: list[str] = field(default_factory=list)      # free tiers may keep prompts: only for public/consented input
    prices: dict = field(default_factory=dict)
    daily_usd: float = 1.0
    run_usd: float = 0.05
    run_tokens: int = 60_000
    run_tool_calls: int = 30
    run_seconds: int = 180
    max_candidates: int = 8
    per_ip_day: int = 3
    concurrent: int = 2
    queue: int = 3
    free_calls_day: int = 0                                     # 0 = no daily cap on free-model calls
    strong_effort: str = ""                                     # reasoning effort for strong (review) models; "" = the model's default


def load_agent_config(prefix: str, env=os.environ, **defaults) -> AgentConfig:
    get = lambda name, default="": env.get(prefix + name, default)  # noqa: E731
    cfg = AgentConfig(**defaults)
    cfg.api_key = env.get("OPENROUTER_API_KEY", "")
    cfg.base_url = env.get("OPENROUTER_BASE_URL", cfg.base_url)
    cfg.fast_models = env_list(get("FAST_MODELS"))
    cfg.strong_models = env_list(get("STRONG_MODELS"))
    cfg.public_models = env_list(get("PUBLIC_MODELS"))
    cfg.prices = env_prices(get("PRICES"))
    cfg.strong_effort = get("STRONG_EFFORT", cfg.strong_effort)
    for name, cast in (("DAILY_USD", float), ("RUN_USD", float), ("FREE_CALLS_DAY", int)):
        if get(name):
            setattr(cfg, name.lower(), cast(get(name)))
    cfg.enabled = get("ENABLED") == "1" and bool(cfg.api_key) and bool(cfg.fast_models)
    return cfg
