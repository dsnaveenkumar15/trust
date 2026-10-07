# tests/test_core_agent_config.py
from core.agent.config import load_agent_config


def test_each_app_reads_its_own_prefix_and_shares_the_key():
    env = {"OPENROUTER_API_KEY": "k", "ARCHITECT_ENABLED": "1", "ARCHITECT_FAST_MODELS": "a/b, c/d",
           "ARCHITECT_PUBLIC_MODELS": "free/x:free", "ARCHITECT_RUN_USD": "0.08", "ARCHITECT_FREE_CALLS_DAY": "900",
           "AGENT_ENABLED": "0"}
    cfg = load_agent_config("ARCHITECT_", env, daily_usd=0.25)
    assert cfg.enabled and cfg.fast_models == ["a/b", "c/d"] and cfg.public_models == ["free/x:free"]
    assert cfg.run_usd == 0.08 and cfg.free_calls_day == 900 and cfg.daily_usd == 0.25
    assert not load_agent_config("AGENT_", env).enabled


def test_off_without_a_key_or_a_fast_model():
    assert not load_agent_config("ARCHITECT_", {"ARCHITECT_ENABLED": "1", "ARCHITECT_FAST_MODELS": "a/b"}).enabled
    assert not load_agent_config("ARCHITECT_", {"ARCHITECT_ENABLED": "1", "OPENROUTER_API_KEY": "k"}).enabled


def test_prices_parse_model_ids_with_colons():
    cfg = load_agent_config("ARCHITECT_", {"ARCHITECT_PRICES": "free/x:free=0/0;a/b=0.2/1.1"})
    assert cfg.prices == {"free/x:free": (0.0, 0.0), "a/b": (0.2, 1.1)}


def test_a_strong_model_can_be_asked_to_think_less_and_only_the_strong_ones():
    from core.agent.gateway import chat_factory
    cfg = load_agent_config("ARCHITECT_", {"OPENROUTER_API_KEY": "k", "ARCHITECT_ENABLED": "1", "ARCHITECT_FAST_MODELS": "fast/a",
                                           "ARCHITECT_STRONG_MODELS": "strong/b", "ARCHITECT_STRONG_EFFORT": "low"})
    make = chat_factory(cfg)
    assert make("strong/b").extra_body["reasoning"] == {"effort": "low"}
    assert "reasoning" not in make("fast/a").extra_body
    assert "reasoning" not in chat_factory(load_agent_config("AGENT_", {"OPENROUTER_API_KEY": "k", "AGENT_STRONG_MODELS": "strong/b"}))("strong/b").extra_body
