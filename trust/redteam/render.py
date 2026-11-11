"""The report: the page's view model, the Markdown download and the JSON regression pack (redteam-pack@2), which holds
each scenario in a replayable form; replay() re-runs a pack against the victims the gateway has models for."""
import json
import re

from trust.redteam import canaries as canaries_mod
from trust.redteam import checks, scenarios, victims
from trust.redteam.scenarios import SimSpec
from trust.redteam.sim import simulate

PACK = "redteam-pack@2"
VERDICT_TEXT = {"got_through": "Got through", "partial": "Partly got through", "resisted": "Resisted", "not_run": "Not run"}
FIX_TEXT = {"fixed": "stopped the attack", "not_fixed": "did not stop it", "not_run": "not run"}
CAREFUL = {"key": "careful", "label": "Careful model"}


def headline(items: list[dict], labels: list[dict] | None = None) -> str:
    got = sum(s["verdict"] == "got_through" for s in items)
    part = sum(s["verdict"] == "partial" for s in items)
    text = f"{got} of {len(items)} attacks got through" + (f" and {part} partly" if part else "")
    if labels and len(labels) > 1:                       # several victims: the worst case, then each model's own count
        per = ", ".join(f"{v['label'].lower()} {sum(x['verdict'] == 'got_through' for s in items for x in s.get('victims', []) if x['key'] == v['key'])}"
                        for v in labels)
        text += f" on at least one model ({per})"
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


def _victims_of(s: dict) -> list[dict]:
    """Each victim's entry; a result stored before there were several victims is one careful victim."""
    return s.get("victims") or [{**CAREFUL, "verdict": s["verdict"], "hits": s.get("hits", []), "trace": s.get("trace"),
                                 "reason": s.get("reason", ""), "fix_verdict": s.get("fix_verdict"), "fix_trace": s.get("fix_trace")}]


def _timeline(trace: dict | None, secrets: list[str]) -> list[dict]:
    out = []
    for c in (trace or {}).get("calls", []):
        args = json.dumps(c["args"], ensure_ascii=False)[:2000]
        out.append({"turn": c["turn"] + 1, "tool": c["tool"], "host": c["host"], "args": args, "output": c["output"][:600],
                    "parts": _parts(args, secrets), "leaked": not c["host"] and checks.contains_canary(args, secrets)})
    return out


timeline = _timeline          # public name for the benchmark's try page


def view(result: dict) -> dict:
    secrets = list(result.get("canaries") or [])
    out = dict(result)
    out["scenarios"] = []
    for s in result["scenarios"]:
        views = [{"key": v["key"], "label": v["label"], "verdict": v["verdict"], "verdict_text": VERDICT_TEXT[v["verdict"]],
                  "reason": v.get("reason", ""), "timeline": _timeline(v.get("trace"), secrets),
                  "final": ((v.get("trace") or {}).get("final") or "")[:600], "fix_verdict": v.get("fix_verdict"),
                  "fix_text": FIX_TEXT.get(v.get("fix_verdict") or "", "")} for v in _victims_of(s)]
        out["scenarios"].append({**s, "timeline": _timeline(s.get("trace"), secrets), "verdict_text": VERDICT_TEXT[s["verdict"]],
                                 "final": ((s.get("trace") or {}).get("final") or "")[:600], "victim_views": views})
    return out


def markdown(result: dict) -> str:
    lines = ["# Red team report", "", result["headline"], ""]
    lines.append(f"Resistance grade: {result['grade']} (this run's result, not a published Trust grade)" if result["grade"]
                 else f"No grade: {result['grade_note']}")
    if result["grade_note"] and result["grade"]:
        lines.append(result["grade_note"])
    for v in result.get("victims") or []:
        if len(result["victims"]) > 1:
            lines.append(f"- {v['label']}: grade {v['grade'] or 'none'}")
    t = result["target"]
    lines += ["", f"Target: {t['repo'] + ' @ ' + (t['sha'] or '')[:12] if t['repo'] else 'pasted tool list'} "
                  f"({len(t['tools'])} tools)", ""]
    for i, s in enumerate(result["scenarios"], 1):
        lines += [f"## {i}. {s['title']} — {VERDICT_TEXT[s['verdict']]}", "", f"Tool: `{s['tool']}`",
                  f"Suspect text: {s['quote']}", f"Why: {s['why']}"]
        if s.get("reason"):
            lines.append(f"Note: {s['reason']}")
        for v in _victims_of(s):
            lines.append(f"- {v['label']}: {VERDICT_TEXT[v['verdict']]}")
            for c in (v.get("trace") or {}).get("calls", []):
                lines.append(f"  - turn {c['turn'] + 1}: {c['tool']}({json.dumps(c['args'], ensure_ascii=False)[:300]})")
            if v.get("fix_verdict"):
                lines.append(f"  - suggested fix re-test: {v['fix_verdict']}")
        if s.get("fix_verdict"):
            lines.append(f"Suggested fix: {json.dumps(s['fix'], ensure_ascii=False)[:500]}")
        lines.append("")
    if result["drift"].get("earlier"):
        d = result["drift"]
        lines += ["## Changes since Trust's last audit", f"Added: {', '.join(d['added']) or 'none'}; removed: "
                  f"{', '.join(d['removed']) or 'none'}; changed: {', '.join(d['changed']) or 'none'}", ""]
    return "\n".join(lines)


SECRET_MARK = "<fake-secret>"


def _scrub(value, secrets: list[str]):
    """The pack never carries the run's fake secret values: they are replaced by a mark (replay plants new ones)."""
    if not secrets:
        return value
    text = json.dumps(value, ensure_ascii=False)
    for s in secrets:
        text = text.replace(s, SECRET_MARK)
    return json.loads(text)


def pack(result: dict) -> dict:
    keep = ("id", "tool", "quote", "verdict", "hits", "spec", "trace", "fix", "fix_verdict")
    entries = result.get("victims") or [{**CAREFUL, "model": "", "grade": result.get("grade")}]
    return _scrub({"format": PACK, "target": result["target"], "static": result["static"], "reach": result["reach"],
            "drift": result["drift"], "grade": result["grade"],
            "victims": [{"key": v["key"], "label": v["label"], "model": v.get("model", ""), "grade": v.get("grade")} for v in entries],
            "scenarios": [{**{k: s.get(k) for k in keep},
                           "victims": [{k: v.get(k) for k in ("key", "verdict", "hits", "trace", "fix_verdict")} for v in s["victims"]]
                           if s.get("victims") else None} for s in result["scenarios"]]}, list(result.get("canaries") or []))


def replay(p: dict, gateway, rng) -> list[dict]:
    """Re-run each scenario on each victim the pack names (a version 1 pack, or a scenario without victims, is the careful
    one) that the gateway has models for; a fresh set of fake secrets for every attack."""
    out = []
    for s in p["scenarios"]:
        if not s.get("spec"):
            continue
        scenario = scenarios.catalog()[s["id"]]
        spec = SimSpec.from_dict(s["spec"])
        for v in s.get("victims") or [CAREFUL]:
            role = victims.ROLE[v["key"]]
            if not gateway.models_by_role.get(role):
                continue
            c = canaries_mod.make(rng)
            out.append({**checks.verdict(simulate(gateway, spec, c, role=role), spec, c, scenario.strong, scenario.weak), "victim": v["key"]})
    return out
