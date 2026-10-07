"""The red team pipeline (LangGraph): static facts, analyst, simulation of each pick (up to 3 side by side), a fix
check for each success, report. A limit reached mid-run leaves a partial report; a victim error skips one scenario."""
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from core.agent.gateway import BudgetExceeded
from trust.redteam import analyst, canaries, checks, grade, render, scenarios
from trust.redteam import reach as reach_mod
from trust.redteam.sim import simulate


class State(TypedDict, total=False):
    target: object
    reach: dict
    drift: dict
    picks: list
    analyst: str
    canaries: dict
    scenarios: list
    result: dict


@dataclass
class Context:
    gateway: object
    emit: Callable
    drift: Callable
    rng: object
    parallel: int = 3
    lock: threading.Lock = field(default_factory=threading.Lock)


def _node(ctx, name, fn):
    def wrapped(state):
        ctx.emit("node", {"name": name, "status": "start"})
        out = fn(state)
        ctx.emit("node", {"name": name, "status": "end"})
        return out
    return wrapped


def _simulate_one(ctx, state, pick, fix=None) -> dict:
    s = scenarios.catalog()[pick.scenario]
    spec = s.build(pick.candidate, state["target"], fix=fix)
    trace = simulate(ctx.gateway, spec, state["canaries"])
    v = checks.verdict(trace, spec, state["canaries"], s.strong, s.weak)
    return {"spec": spec.to_dict(), "trace": trace.to_dict(), **v}


def _safe_run(ctx, state, pick, fix=None) -> dict:
    try:
        return _simulate_one(ctx, state, pick, fix)
    except BudgetExceeded:
        return {"verdict": "not_run", "hits": [], "reason": "not run (limit reached)", "spec": None, "trace": None}
    except Exception:                                    # provider down or a malformed reply: skip this scenario only
        return {"verdict": "not_run", "hits": [], "reason": "not run (error)", "spec": None, "trace": None}


def build_graph(ctx: Context):
    def facts(state):
        t = state["target"]
        return {"reach": reach_mod.reach_map(t), "drift": ctx.drift(t), "canaries": canaries.make(ctx.rng)}

    def choose(state):
        picks, source = analyst.pick(ctx.gateway, state["target"], state["reach"])
        return {"picks": picks, "analyst": source}

    def attack(state):
        def one(i_pick):
            i, pick = i_pick
            out = _safe_run(ctx, state, pick)
            with ctx.lock:
                ctx.emit("scenario", {"index": i + 1, "id": pick.scenario, "tool": pick.tool, "verdict": out["verdict"]})
            s = scenarios.catalog()[pick.scenario]
            return {"id": s.id, "title": s.title, "kind": s.kind, "tool": pick.tool, "quote": pick.quote, "why": pick.why,
                    "risk": pick.risk, "source": pick.source, "reason": out.pop("reason", ""), "fix": None,
                    "fix_verdict": None, "fix_trace": None, **out}
        with ThreadPoolExecutor(max_workers=max(1, ctx.parallel)) as pool:
            return {"scenarios": list(pool.map(one, enumerate(state["picks"])))}

    def fixcheck(state):
        picks = {(p.scenario, p.tool): p for p in state["picks"]}
        items = [dict(s) for s in state["scenarios"]]
        for s in items:
            if s["verdict"] not in ("got_through", "partial"):
                continue
            pick = picks[(s["id"], s["tool"])]
            fix = scenarios.catalog()[s["id"]].fix(pick.candidate, state["target"])
            out = _safe_run(ctx, state, pick, fix)
            s["fix"] = fix
            s["fix_trace"] = out["trace"]
            s["fix_verdict"] = ("not_run" if out["verdict"] == "not_run" else
                                "fixed" if out["verdict"] == "resisted" else "not_fixed")
            ctx.emit("fixcheck", {"id": s["id"], "tool": s["tool"], "result": s["fix_verdict"]})
        return {"scenarios": items}

    def report(state):
        t = state["target"]
        g, note = grade.grade(state["scenarios"], state["reach"])
        notes = list(t.notes)
        if state["analyst"] == "fallback":
            notes.append("The analyst was unavailable; the catalog's own rules chose the attacks.")
        result = {"target": {"kind": t.kind, "repo": t.repo, "sha": t.sha, "tools": [x.name for x in t.tools],
                             "hashes": {x.name: hashlib.sha256(x.description.encode()).hexdigest()[:16] for x in t.tools}},
                  "static": t.findings[:50], "reach": state["reach"], "drift": state["drift"], "analyst": state["analyst"],
                  "scenarios": state["scenarios"], "canaries": sorted(state["canaries"]["values"].values()),
                  "grade": g, "grade_note": note, "headline": render.headline(state["scenarios"]) if state["scenarios"]
                  else "No known attack pattern has a foothold in these tools.", "notes": notes}
        return {"result": result}

    def after_attack(state):
        return "fixcheck" if any(s["verdict"] in ("got_through", "partial") for s in state["scenarios"]) else "report"

    g = StateGraph(State)
    for name, fn in (("facts", facts), ("choose", choose), ("attack", attack), ("fixcheck", fixcheck), ("report", report)):
        g.add_node(name, _node(ctx, name, fn))
    g.add_edge(START, "facts")
    g.add_edge("facts", "choose")
    g.add_edge("choose", "attack")
    g.add_conditional_edges("attack", after_attack, {"fixcheck": "fixcheck", "report": "report"})
    g.add_edge("fixcheck", "report")
    g.add_edge("report", END)
    return g.compile()


def run(ctx: Context, target) -> dict:
    return build_graph(ctx).invoke({"target": target})["result"]
