"""What each tool can touch if an agent is fooled into using it: from the source lines inside the tool (repos) or from
its parameter names (pasted lists), plus mentions of other tools in its description."""
import re

from trust.redteam.models import Target

RULE_CAPS = {"shell-exec": "shell", "dynamic-eval": "eval", "sensitive-path": "files", "hardcoded-url-call": "network"}
LINE_CAPS = [("files", re.compile(r"\bopen\(|\bPath\(|\bfs\.\w+|readFile|writeFile")),
             ("network", re.compile(r"\brequests\.|\bhttpx\.|\burlopen\(|(?<![.\w])fetch\(|\baxios\b")),
             ("env", re.compile(r"\bos\.environ|\bos\.getenv|\bprocess\.env"))]
PARAM_CAPS = [("files", re.compile(r"^(path|file|filename|filepath|dir|directory|folder)s?$", re.I)),
              ("network", re.compile(r"^(url|uri|endpoint|link|host|webhook)s?$", re.I)),
              ("shell", re.compile(r"^(command|cmd|script|shell)s?$", re.I)),
              ("eval", re.compile(r"^(code|expression|expr)s?$", re.I))]


def _ranges(target: Target) -> dict[str, list[tuple[int, int, str]]]:
    by_file: dict[str, list] = {}
    for t in target.tools:
        if t.file:
            by_file.setdefault(t.file, []).append((t.line, t.name))
    out = {}
    for path, entries in by_file.items():
        entries.sort()
        ends = [line for line, _ in entries[1:]] + [10**9]
        out[path] = [(line, end, name) for (line, name), end in zip(entries, ends)]
    return out


def tool_of(target: Target, file: str, line: int) -> str | None:
    return next((name for start, end, name in _ranges(target).get(file, []) if start <= line < end), None)


def reach_map(target: Target) -> dict[str, list[str]]:
    caps = {t.name: set() for t in target.tools}
    if target.kind == "repo":
        for path, spans in _ranges(target).items():
            lines = target.files.get(path, "").splitlines()
            for start, end, name in spans:
                for text in lines[start - 1:min(end - 1, len(lines))]:
                    caps[name].update(cap for cap, rx in LINE_CAPS if rx.search(text))
        for f in target.findings:
            name = tool_of(target, f.get("file", ""), int(f.get("line") or 0))
            if name and f.get("rule") in RULE_CAPS:
                caps[name].add(RULE_CAPS[f["rule"]])
    else:
        for t in target.tools:
            for prop in (t.schema.get("properties") or {}):
                caps[t.name].update(cap for cap, rx in PARAM_CAPS if rx.match(str(prop)))
    names = [t.name for t in target.tools]
    for t in target.tools:
        if any(o != t.name and re.search(rf"\b{re.escape(o)}\b", t.description) for o in names):
            caps[t.name].add("other_tools")
    return {name: sorted(c) for name, c in caps.items()}
