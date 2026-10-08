"""Which backend the batch jobs use: OpenRouter under the daily cap when configured, else the Claude CLI as before."""
import os

from core.claude_cli import ClaudeCliBackend
from core.control.spend import CappedBackend
from core.openrouter import OpenRouterBackend
from core.scheduler import GuardedBackend


def make_backend(conn, env=os.environ, claude_model_var: str = "TRUST_MODEL"):
    key, model = env.get("OPENROUTER_API_KEY", ""), env.get("BATCH_MODEL", "")
    if key and model:
        return CappedBackend(conn, OpenRouterBackend(model, key, env.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")),
                             key, float(env.get("BATCH_DAILY_USD", "0.03")))
    return GuardedBackend(conn, ClaudeCliBackend(model=env.get(claude_model_var, "sonnet")))
