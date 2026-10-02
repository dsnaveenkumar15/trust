"""Rules over tool descriptions: the text an agent reads and may obey."""
import re
import unicodedata

from trust.locate import locate
from trust.models import Finding, ToolDef, make_finding

HIDDEN_PATTERNS = [
    (re.compile(r"<\s*(important|system|instructions?|secret)\s*>", re.I), "tag-style hidden instruction block"),
    (re.compile(r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+instructions", re.I), "instruction override"),
    (re.compile(r"\b(do\s+not|don'?t|never)\s+(tell|mention|inform|reveal|notify|show)\b[^.\n]{0,40}\buser", re.I),
     "asks the model to hide behaviour from the user"),
    (re.compile(r"before\s+(using|calling|invoking)\s+this\s+tool[^.\n]{0,60}\b(read|open|send|pass|include)", re.I),
     "pre-call instruction to read or send data"),
    (re.compile(r"[/\\]\.(ssh|aws|gnupg|cursor)\b|\bid_rsa\b|\bid_ed25519\b|\bmcp\.json\b|claude_desktop_config\.json",
                re.I), "references sensitive local files"),
]
SHADOW_RE = re.compile(
    r"\b(?:when(?:ever)?|if|after|before)\s+(?:the\s+user\s+|you\s+|the\s+assistant\s+)?(?:call|use|invoke|run)s?\s+"
    r"(?:the\s+)?[`'\"]?(?P<other>[A-Za-z_][\w-]*)[`'\"]?\s+tool\b"
    r"|\binstead\s+of\s+(?:using\s+|calling\s+)?(?:the\s+)?[`'\"]?(?P<other2>[A-Za-z_][\w-]*)[`'\"]?\s+tool\b",
    re.I,
)
SELF_REFERENCES = {"this", "that", "the", "a", "an", "same", "current"}
BASE64_RE = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")
COMMON_TOOL_NAMES = {
    "read_file", "write_file", "edit_file", "create_file", "delete_file", "list_directory", "list_files",
    "search_files", "execute_command", "run_command", "run_terminal_cmd", "bash", "shell", "terminal",
    "send_email", "send_message", "fetch", "http_request", "web_search", "search", "browser_navigate",
    "get_file_contents",
}


def _invisible(text: str) -> list[str]:
    return sorted({f"U+{ord(c):04X}" for c in text
                   if unicodedata.category(c) == "Cf" or 0xE0000 <= ord(c) <= 0xE007F})


def description_findings(tools: list[ToolDef], files: dict[str, str]) -> list[Finding]:
    out: list[Finding] = []
    for tool in tools:
        desc = unicodedata.normalize("NFKC", tool.description)   # fullwidth/compatibility lookalikes -> ASCII

        def add(rule, category, severity, confidence, needle, message):
            line, quote = locate(files, tool.file, tool.line, needle)
            out.append(make_finding(rule, category, severity, confidence, tool.file, line, quote, message,
                                    tool=tool.name))

        for pattern, why in HIDDEN_PATTERNS:
            match = pattern.search(desc)
            if match:
                add("hidden-instructions", "injection", "critical", "high", match.group(0),
                    f"Tool '{tool.name}' description contains hidden instructions: {why}.")
                break
        codes = _invisible(tool.description)
        if codes:
            add("invisible-unicode", "injection", "critical", "high", "",
                f"Tool '{tool.name}' description contains invisible characters {', '.join(codes)}.")
        for match in SHADOW_RE.finditer(desc):
            other = match.group("other") or match.group("other2")
            if other.lower() in SELF_REFERENCES or other == tool.name:
                continue
            add("tool-shadowing", "injection", "critical", "high", match.group(0),
                f"Tool '{tool.name}' description gives instructions about another tool ('{other}').")
            break
        if tool.name.lower() in COMMON_TOOL_NAMES:
            add("name-collision", "injection", "medium", "medium", tool.name,
                f"Tool name '{tool.name}' collides with a common tool name; agents may call the wrong one.")
        blob = BASE64_RE.search(desc)
        if blob:
            add("encoded-blob", "injection", "high", "medium", blob.group(0)[:40],
                f"Tool '{tool.name}' description contains a long encoded blob.")
        if tool.opaque:
            add("opaque-description", "transparency", "medium", "medium", "",
                f"Tool '{tool.name}' description is built at runtime; only its literal parts could be checked.")
    return out
