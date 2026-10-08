"""Plug the console controls into an agent's run store: admission and budgets use the effective settings, and stop-all
or the weekly brake refuse new runs (runs already started finish)."""
from core.control import brake, store


def install(run_store, api_key) -> None:
    """`api_key` is a callable returning the OpenRouter key (read at check time, so tests and rotations need no restart)."""
    def control(conn, cfg, now):
        store.ensure(conn)
        reason = brake.pause_reason(conn, api_key(), now)
        return store.effective(conn, run_store.p, cfg), (brake.PAUSED_MESSAGES[reason] if reason else None)
    run_store.control = control


def register(app, prefix: str, label: str, run_store, config_attr: str) -> None:
    if not hasattr(app.state, "control_agents"):
        app.state.control_agents = []
    app.state.control_agents.append({"prefix": prefix, "label": label, "store": run_store, "config_attr": config_attr})
