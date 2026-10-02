import re
from dataclasses import asdict, dataclass

SECRET_RE = re.compile(r"AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{50,}"
                       r"|sk-(?:ant-)?[A-Za-z0-9_-]{32,}|xox[baprs]-[A-Za-z0-9-]{10,}"
                       r"|-----BEGIN [A-Z ]*PRIVATE KEY-----")
CATEGORIES = ("injection", "capabilities", "exfiltration", "supply_chain", "transparency")
SEVERITIES = ("critical", "high", "medium", "low")
CONFIDENCES = ("high", "medium", "low")
NON_DISMISSABLE_RULES = frozenset({"hidden-instructions", "invisible-unicode", "tool-shadowing", "auditor-addressed"})


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    file: str
    line: int       # 1-based line where the tool is defined (decorator line for decorated functions)
    language: str   # python | typescript | javascript
    opaque: bool = False   # description is (partly) built at runtime; only the literal parts are known

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ToolDef":
        return cls(**data)


@dataclass(frozen=True)
class Finding:
    rule: str
    category: str
    severity: str
    confidence: str
    file: str
    line: int
    quote: str
    message: str
    tool: str | None = None
    dismissable: bool = True
    source: str = "rule"       # rule | meta | llm
    explanation: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Finding":
        return cls(**data)


def make_finding(rule: str, category: str, severity: str, confidence: str, file: str, line: int, quote: str,
                 message: str, tool: str | None = None, source: str = "rule") -> Finding:
    secret = SECRET_RE.search(quote)
    if secret:
        # Every published quote stops 4 characters into a secret: still a verbatim prefix, never the secret itself.
        quote = quote[: secret.start() + 4]
    return Finding(rule, category, severity, confidence, file, line, quote, message, tool=tool,
                   dismissable=rule not in NON_DISMISSABLE_RULES, source=source)
