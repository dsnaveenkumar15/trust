"""The report: the page's view model, the Markdown download and the JSON regression pack (redteam-pack@1), which
holds each scenario in a replayable form; replay() re-runs a pack against a victim."""
import json
import re

from trust.redteam import canaries as canaries_mod
from trust.redteam import checks, scenarios
from trust.redteam.scenarios import SimSpec
from trust.redteam.sim import simulate

PACK = "redteam-pack@1"
VERDICT_TEXT = {"got_through": "Got through", "partial": "Partly got through", "resisted": "Resisted", "not_run": "Not run"}


def headline(items: list[dict]) -> str:
    got = sum(s["verdict"] == "got_through" for s in items)
    part = sum(s["verdict"] == "partial" for s in items)
    text = f"{got} of {len(items)} attacks got through" + (f" and {part} partly" if part else "")
    checked = [s for s in items if s.get("fix_verdict") in ("fixed", "not_fixed")]
    if checked:
        fixed = sum(s["fix_verdict"] == "fixed" for s in checked)
        text += f"; the suggested rewrite stopped {fixed} of {len(checked)}"
    return text + "."


def _parts(text: str, secrets: list[str]) -> list[tuple[str, bool]]:
    if not secrets:
        return [(text, False)]
    rx = re.compile("(" + "|".join(re.escape(s) for s in secrets) + ")")
    return [(p, bool(rx.fullmatch(p))) for p in rx.split(text) if p]


def view(result: dict) -> dict:
    secrets = list(result.get("canaries") or [])
    out = dict(result)
    out["scenarios"] = []
    for s in result["scenarios"]:
        timeline = []
        for c in (s.get("trace") or {}).get("calls", []):
            args = json.dumps(c["args"], ensure_ascii=False)[:2000]
            timeline.append({"turn": c["turn"] + 1, "tool": c["tool"], "host": c["host"], "args": args,
                             "output": c["output"][:600], "parts": _parts(args, secrets),
                             "leaked": not c["host"] and checks.contains_canary(args, secrets)})
        out["scenarios"].append({**s, "timeline": timeline, "verdict_text": VERDICT_TEXT[s["verdict"]],
                                 "final": ((s.get("trace") or {}).get("final") or "")[:600]})
    return out


def markdown(result: dict) -> str:
    lines = ["# Red team report", "", result["headline"], ""]
    lines.append(f"Resistance grade: {result['grade']} (this run's result, not a published Trust grade)" if result["grade"]
                 else f"No grade: {result['grade_note']}")
    if result["grade_note"] and result["grade"]:
        lines.append(result["grade_note"])
    t = result["target"]
    lines += ["", f"Target: {t['repo'] + ' @ ' + (t['sha'] or '')[:12] if t['repo'] else 'pasted tool list'} "
                  f"({len(t['tools'])} tools)", ""]
    for i, s in enumerate(result["scenarios"], 1):
        lines += [f"## {i}. {s['title']} — {VERDICT_TEXT[s['verdict']]}", "", f"Tool: `{s['tool']}`",
                  f"Suspect text: {s['quote']}", f"Why: {s['why']}"]
        if s.get("reason"):
            lines.append(f"Note: {s['reason']}")
        for c in (s.get("trace") or {}).get("calls", []):
            lines.append(f"- turn {c['turn'] + 1}: {c['tool']}({json.dumps(c['args'], ensure_ascii=False)[:300]})")
        if s.get("fix_verdict"):
            lines.append(f"Suggested fix: {json.dumps(s['fix'], ensure_ascii=False)[:500]} — re-test: {s['fix_verdict']}")
        lines.append("")
    if result["drift"].get("earlier"):
        d = result["drift"]
        lines += ["## Changes since Trust's last audit", f"Added: {', '.join(d['added']) or 'none'}; removed: "
                  f"{', '.join(d['removed']) or 'none'}; changed: {', '.join(d['changed']) or 'none'}", ""]
    return "\n".join(lines)


def pack(result: dict) -> dict:
    keep = ("id", "tool", "quote", "verdict", "hits", "spec", "trace", "fix", "fix_verdict")
    return {"format": PACK, "target": result["target"], "static": result["static"], "reach": result["reach"],
            "drift": result["drift"], "grade": result["grade"],
            "scenarios": [{k: s.get(k) for k in keep} for s in result["scenarios"]]}


def replay(p: dict, gateway, rng) -> list[dict]:
    out = []
    for s in p["scenarios"]:
        if not s.get("spec"):
            continue
        spec, c = SimSpec.from_dict(s["spec"]), canaries_mod.make(rng)
        scenario = scenarios.catalog()[s["id"]]
        out.append(checks.verdict(simulate(gateway, spec, c), spec, c, scenario.strong, scenario.weak))
    return out
