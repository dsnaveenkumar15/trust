"""The red team pipeline (LangGraph): static facts, analyst, simulation of each pick on every victim (up to 3 picks side
by side), a fix check for each success, report. A limit reached mid-run leaves a partial report; a victim error skips one
scenario on one victim only."""
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from core.agent.gateway import BudgetExceeded
from trust.redteam import analyst, canaries, checks, grade, render, scenarios, victims
from trust.redteam import reach as reach_mod
from trust.redteam.sim import simulate

RANK = {"got_through": 3, "partial": 2, "resisted": 1, "not_run": 0}      # the worst case speaks for a scenario


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
    only: str | None = None                  # attack just this scenario id (the lean launch gate); the web form cannot set it
    fixcheck: bool = True                    # re-run each success with the suggested fix
    victims: list = field(default_factory=lambda: [victims.VICTIMS[0]])      # (key, label, gateway role), careful first
    models: dict = field(default_factory=dict)                               # victim key -> real model id (pack only)
    lock: threading.Lock = field(default_factory=threading.Lock)


def _node(ctx, name, fn):
    def wrapped(state):
        ctx.emit("node", {"name": name, "status": "start"})
        out = fn(state)
        ctx.emit("node", {"name": name, "status": "end"})
        return out
    return wrapped


def _simulate_one(ctx, state, pick, role, fix=None) -> dict:
    s = scenarios.catalog()[pick.scenario]
    spec = s.build(pick.candidate, state["target"], fix=fix)
    trace = simulate(ctx.gateway, spec, state["canaries"], role=role)
    v = checks.verdict(trace, spec, state["canaries"], s.strong, s.weak)
    return {"spec": spec.to_dict(), "trace": trace.to_dict(), **v}


def _safe_run(ctx, state, pick, role, fix=None) -> dict:
    try:
        return _simulate_one(ctx, state, pick, role, fix)
    except BudgetExceeded:
        return {"verdict": "not_run", "hits": [], "reason": "not run (limit reached)", "spec": None, "trace": None}
    except Exception:                                    # provider down or a malformed reply: skip this scenario on this victim
        return {"verdict": "not_run", "hits": [], "reason": "not run (error)", "spec": None, "trace": None}


def build_graph(ctx: Context):
    roles = {key: role for key, _, role in ctx.victims}

    def facts(state):
        t = state["target"]
        return {"reach": reach_mod.reach_map(t), "drift": ctx.drift(t), "canaries": canaries.make(ctx.rng)}

    def choose(state):
        picks, source = analyst.pick(ctx.gateway, state["target"], state["reach"])
        if ctx.only:
            picks = [p for p in picks if p.scenario == ctx.only]
        return {"picks": picks, "analyst": source}

    def attack(state):
        def one(i_pick):
            i, pick = i_pick
            runs_, spec = [], None
            for key, label, role in ctx.victims:
                out = _safe_run(ctx, state, pick, role)
                spec = spec or out["spec"]
                runs_.append({"key": key, "label": label, "verdict": out["verdict"], "hits": out["hits"],
                              "reason": out.get("reason", ""), "trace": out["trace"], "fix_verdict": None, "fix_trace": None})
            worst = max(runs_, key=lambda v: RANK[v["verdict"]])
            with ctx.lock:
                ctx.emit("scenario", {"index": i + 1, "id": pick.scenario, "tool": pick.tool, "verdict": worst["verdict"],
                                      "victims": {v["key"]: v["verdict"] for v in runs_}})
            s = scenarios.catalog()[pick.scenario]
            return {"id": s.id, "title": s.title, "kind": s.kind, "tool": pick.tool, "quote": pick.quote, "why": pick.why,
                    "risk": pick.risk, "source": pick.source, "verdict": worst["verdict"], "hits": worst["hits"],
                    "reason": worst["reason"] if worst["verdict"] == "not_run" else "", "spec": spec, "trace": worst["trace"],
                    "victims": runs_, "fix": None, "fix_verdict": None, "fix_trace": None}
        with ThreadPoolExecutor(max_workers=max(1, ctx.parallel)) as pool:
            return {"scenarios": list(pool.map(one, enumerate(state["picks"])))}

    def fixcheck(state):
        picks = {(p.scenario, p.tool): p for p in state["picks"]}
        items = [dict(s, victims=[dict(v) for v in s["victims"]]) for s in state["scenarios"]]
        for s in items:
            if s["verdict"] not in ("got_through", "partial"):
                continue
            pick = picks[(s["id"], s["tool"])]
            fix = scenarios.catalog()[s["id"]].fix(pick.candidate, state["target"])
            s["fix"] = fix
            for v in s["victims"]:
                if v["verdict"] not in ("got_through", "partial"):
                    continue
                out = _safe_run(ctx, state, pick, roles[v["key"]], fix)
                v["fix_trace"] = out["trace"]
                v["fix_verdict"] = ("not_run" if out["verdict"] == "not_run" else "fixed" if out["verdict"] == "resisted" else "not_fixed")
            tested = [v for v in s["victims"] if v["fix_verdict"]]
            s["fix_verdict"] = ("not_fixed" if any(v["fix_verdict"] == "not_fixed" for v in tested)
                                else "not_run" if all(v["fix_verdict"] == "not_run" for v in tested) else "fixed")
            first = next((v for v in tested if v["fix_verdict"] == "not_fixed"), tested[0])
            s["fix_trace"] = first["fix_trace"]
            ctx.emit("fixcheck", {"id": s["id"], "tool": s["tool"], "result": s["fix_verdict"]})
        return {"scenarios": items}

    def report(state):
        t = state["target"]
        g, note = grade.grade(state["scenarios"], state["reach"])
        per = grade.by_victim(state["scenarios"], state["reach"], [key for key, _, _ in ctx.victims])
        info = [{"key": key, "label": label, "model": ctx.models.get(key, ""), "grade": per[key]} for key, label, _ in ctx.victims]
        notes = list(t.notes)
        if state["analyst"] == "fallback":
            notes.append("The analyst was unavailable; the catalog's own rules chose the attacks.")
        result = {"target": {"kind": t.kind, "repo": t.repo, "sha": t.sha, "tools": [x.name for x in t.tools],
                             "hashes": {x.name: hashlib.sha256(x.description.encode()).hexdigest()[:16] for x in t.tools}},
                  "static": t.findings[:50], "reach": state["reach"], "drift": state["drift"], "analyst": state["analyst"],
                  "scenarios": state["scenarios"], "victims": info, "canaries": sorted(state["canaries"]["values"].values()),
                  "grade": g, "grade_note": note, "notes": notes,
                  "headline": render.headline(state["scenarios"], info) if state["scenarios"]
                  else "No known attack pattern has a foothold in these tools."}
        return {"result": result}

    def after_attack(state):
        again = ctx.fixcheck and any(s["verdict"] in ("got_through", "partial") for s in state["scenarios"])
        return "fixcheck" if again else "report"

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
