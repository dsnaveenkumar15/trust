"""Rules over source lines, dependency manifests and repository metadata."""
import json
import re
from collections import Counter, namedtuple
from datetime import datetime

from trust.extract import is_source
from trust.locate import locate
from trust.models import SECRET_RE, Finding, make_finding

MAX_PER_RULE_FILE = 3
COMMENT_PREFIXES = ("#", "//", "*", "/*")
_NOT_LOCAL = r"(?!localhost|127\.0\.0\.1)"

LineRule = namedtuple("LineRule", "rule category severity confidence lang pattern message")
LINE_RULES = [
    LineRule("shell-exec", "capabilities", "high", "high", "python",
             re.compile(r"\bsubprocess\.(?:run|Popen|call|check_call|check_output)\b|\bos\.(?:system|popen|exec\w*)\s*\("),
             "Runs shell commands or external programs."),
    LineRule("shell-exec", "capabilities", "high", "high", "js",
             re.compile(r"\bchild_process\b|(?<![.\w])(?:exec|execSync|execFile|spawn|spawnSync)\s*\("),
             "Runs shell commands or external programs."),
    LineRule("dynamic-eval", "capabilities", "high", "medium", "python",
             re.compile(r"(?<![.\w])(?:eval|exec)\s*\("), "Evaluates dynamically built code."),
    LineRule("dynamic-eval", "capabilities", "high", "medium", "js",
             re.compile(r"(?<![.\w])eval\s*\(|\bnew\s+Function\s*\("), "Evaluates dynamically built code."),
    LineRule("sensitive-path", "exfiltration", "high", "medium", "any",
             re.compile(r"[/\\]\.(?:ssh|aws|gnupg)\b|\bid_rsa\b|\bid_ed25519\b|claude_desktop_config\.json"
                        r"|[/\\]\.cursor[/\\]mcp\.json", re.I),
             "Reads or references local credential files."),
    LineRule("hardcoded-url-call", "exfiltration", "medium", "medium", "any",
             re.compile(rf"(?:requests|httpx)\.(?:get|post|put|patch|request)\s*\(\s*f?['\"]https?://{_NOT_LOCAL}"
                        rf"|\burlopen\s*\(\s*['\"]https?://{_NOT_LOCAL}"
                        rf"|(?<![.\w])fetch\s*\(\s*['\"`]https?://{_NOT_LOCAL}"
                        rf"|\baxios(?:\.\w+)?\s*\(\s*['\"`]https?://{_NOT_LOCAL}"),
             "Sends network requests to a hard-coded external URL."),
]
AUDITOR_RE = re.compile(
    r"\b(?:note|message|instructions?|attention)\s+(?:to|for)\s+(?:the\s+|any\s+)?"
    r"(?:ai\s+|llm\s+|security\s+|code\s+|automated\s+)*(?:reviewers?|auditors?|scanners?)\b"
    r"|\bmark\s+(?:this|it)\s+as\s+(?:safe|secure|benign|trusted)\b"
    r"|\b(?:reviewers?|auditors?|scanners?)\s*:\s*(?:this|the)\s+\w+\s+is\s+(?:safe|secure|harmless|benign)\b",
    re.I,
)
REQUIREMENTS_RE = re.compile(r"(^|/)requirements[^/]*\.txt$", re.I)


def code_findings(files: dict[str, str]) -> list[Finding]:
    out: list[Finding] = []
    for path in sorted(files):
        if not is_source(path):
            continue
        lang = "python" if path.lower().endswith(".py") else "js"
        counts: Counter = Counter()

        def add(rule, category, severity, confidence, line_no, quote, message):
            if counts[rule] < MAX_PER_RULE_FILE:
                counts[rule] += 1
                out.append(make_finding(rule, category, severity, confidence, path, line_no, quote, message))

        for line_no, line in enumerate(files[path].splitlines(), 1):
            stripped = line.strip()
            if not stripped:
                continue
            if AUDITOR_RE.search(line):
                add("auditor-addressed", "injection", "high", "high", line_no, stripped[:300],
                    "Text addressed to security reviewers or scanners; a known way to manipulate audits.")
            secret = SECRET_RE.search(line)
            if secret:
                # Quote stops 4 characters into the secret: verifiable evidence that never republishes the secret.
                add("hardcoded-secret", "supply_chain", "high", "high", line_no,
                    line[: secret.start() + 4].strip(), "Hard-coded credential in source.")
            if stripped.startswith(COMMENT_PREFIXES):
                continue
            seen_rules = set()
            for rule in LINE_RULES:
                if rule.lang in (lang, "any") and rule.rule not in seen_rules and rule.pattern.search(line):
                    seen_rules.add(rule.rule)
                    add(rule.rule, rule.category, rule.severity, rule.confidence, line_no, stripped[:300], rule.message)
    return out


def dependency_findings(files: dict[str, str]) -> list[Finding]:
    out: list[Finding] = []
    for path in sorted(files):
        text = files[path]
        if REQUIREMENTS_RE.search(path):
            unpinned = [l.strip() for l in text.splitlines()
                        if l.strip() and not l.strip().startswith(("#", "-")) and "==" not in l and "@" not in l]
            if unpinned:
                line, quote = locate(files, path, 1, unpinned[0])
                out.append(make_finding("unpinned-deps", "supply_chain", "low", "high", path, line, quote,
                                        f"{len(unpinned)} unpinned Python requirement(s) in {path}."))
        elif path.rsplit("/", 1)[-1] == "package.json":
            try:
                data = json.loads(text)
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue
            loose = [name for section in ("dependencies", "optionalDependencies")
                     for name, spec in (data.get(section) or {}).items()
                     if isinstance(spec, str) and (spec.strip() in ("*", "latest", "")
                                                   or spec.startswith(("git", "http", "github:", "file:")))]
            if loose:
                line, quote = locate(files, path, 1, f'"{loose[0]}"')
                out.append(make_finding("unpinned-deps", "supply_chain", "low", "high", path, line, quote,
                                        f"{len(loose)} dependencies with no version constraint in {path}."))
    return out


def meta_findings(meta: dict, files: dict[str, str], now: float) -> list[Finding]:
    if not meta:
        return []

    def repo_finding(rule, category, severity, message):
        return make_finding(rule, category, severity, "high", "(repo)", 0, "", message, source="meta")

    out = []
    if not meta.get("license"):
        out.append(repo_finding("no-license", "transparency", "low", "No license declared."))
    if meta.get("archived"):
        out.append(repo_finding("archived", "supply_chain", "medium", "Repository is archived; it receives no fixes."))
    pushed = meta.get("pushed_at")
    if pushed:
        age_days = (now - datetime.fromisoformat(pushed.replace("Z", "+00:00")).timestamp()) / 86400
        if age_days > 365:
            out.append(repo_finding("stale", "supply_chain", "low", f"No commits for {int(age_days)} days."))
    if not any(p.rsplit("/", 1)[-1].lower().startswith("readme") for p in files):
        out.append(repo_finding("no-readme", "transparency", "low", "No README describing what the server does."))
    return out
