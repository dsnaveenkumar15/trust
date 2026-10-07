"""Red team runs: the shared run store under the 'redteam' prefix (kept 7 days), one gateway per run with its budgets
(victim = REDTEAM_FAST_MODELS, analyst = REDTEAM_STRONG_MODELS), and the work function the Runner calls."""
import os
import random
import secrets
import threading
import time

from core.agent.config import AgentConfig, load_agent_config
from core.agent.gateway import Budget, BudgetExceeded, Gateway
from core.agent.runs import LimitError, RunStore  # noqa: F401
from trust.redteam import drift, graph, inputs

KEEP_DAYS = 7
MESSAGES = {"off": "The red team is switched off right now. Try again later.",
            "budget": "Today's red team budget is used up. Runs resume at 00:00 UTC.",
            "one": "You already have a run in progress: one at a time.",
            "daily": "You have used your {n} runs for today.",
            "busy": "The red team is busy. Try again in a few minutes."}
STORE = RunStore("redteam", KEEP_DAYS, holding=("queued", "running"), interrupted="interrupted", id_bytes=16, messages=MESSAGES)
DEFAULTS = dict(strong_effort="low", daily_usd=0.25, run_usd=0.10, run_tokens=1_500_000, run_tool_calls=1, run_seconds=600,
                per_ip_day=3, concurrent=2, queue=3)


def load_config(env=os.environ) -> AgentConfig:
    cfg = load_agent_config("REDTEAM_", env, **DEFAULTS)
    if env.get("REDTEAM_RUN_SECONDS"):
        cfg.run_seconds = int(env["REDTEAM_RUN_SECONDS"])
    return cfg


def roles(cfg: AgentConfig) -> dict[str, list[str]]:
    return {"victim": list(cfg.fast_models), "analyst": list(cfg.strong_models or cfg.fast_models)}


def make_gateway(cfg: AgentConfig, conn, run_id: str, emit, factory, lock=None) -> Gateway:
    """`lock` guards the run's one database connection; the caller passes the same (re-entrant) lock its `emit` takes,
    so events, usage and the daily guard never touch the connection from two scenario threads at once."""
    totals, lock = {"usd": 0.0, "tokens": 0}, lock or threading.RLock()

    def on_usage(model, provider, usd, tokens):
        with lock:
            totals["usd"] += usd
            totals["tokens"] += tokens
            STORE.add_usage(conn, run_id, usd, tokens)
            emit("usage", {"model": model, "usd": round(usd, 6), "tokens": tokens,
                           "total_usd": round(totals["usd"], 6), "total_tokens": totals["tokens"]})

    def day_guard():
        with lock:
            spent = STORE.day_spend(conn, time.time())
        if spent >= cfg.daily_usd:
            raise BudgetExceeded("daily budget reached")

    budget = Budget(cfg.run_usd, cfg.run_tokens, cfg.run_tool_calls, time.monotonic() + cfg.run_seconds)
    return Gateway(roles(cfg), factory, dict(cfg.prices), budget, on_usage, day_guard)


def error_text(exc: Exception) -> str:
    if isinstance(exc, BudgetExceeded):
        return "Today's red team budget is used up. Runs resume at 00:00 UTC."
    return "The run failed. Please try again later."


def make_work(get_config, get_factory, get_github, rng_factory=None):
    rng_factory = rng_factory or (lambda: random.Random(secrets.randbits(64)))

    def work(conn, run_id, emit):
        lock = threading.RLock()                         # scenarios run side by side on one connection: every touch takes this

        def locked_emit(type_, payload):
            with lock:
                emit(type_, payload)
        cfg = get_config()
        inp = STORE.get_run(conn, run_id)["input"]
        locked_emit("node", {"name": "fetch", "status": "start"})
        try:
            target = inputs.INPUTS[inp["kind"]](inp["text"], get_github(conn))
        except inputs.InputError as exc:
            locked_emit("error", {"text": str(exc)})
            return "stopped", {"reason": str(exc)}
        locked_emit("node", {"name": "fetch", "status": "end"})
        gw = make_gateway(cfg, conn, run_id, locked_emit, get_factory(cfg), lock)
        ctx = graph.Context(gateway=gw, emit=locked_emit, drift=lambda t: drift.changes(conn, t), rng=rng_factory())
        result = graph.run(ctx, target)
        locked_emit("result", {"headline": result["headline"], "grade": result["grade"]})
        return "done", result
    return work
