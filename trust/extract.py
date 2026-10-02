from trust.extract_py import extract_python
from trust.extract_ts import TS_EXTENSIONS, extract_ts
from trust.models import ToolDef

SKIP_PARTS = {"node_modules", "dist", "build", ".venv", "venv", "__pycache__", ".git", "vendor"}


def is_source(path: str) -> bool:
    lower = path.lower()
    if any(part in SKIP_PARTS for part in lower.split("/")):
        return False
    if lower.endswith((".d.ts", ".min.js")):
        return False
    return lower.endswith((".py",) + TS_EXTENSIONS)


def language_of(path: str) -> str:
    lower = path.lower()
    if lower.endswith(".py"):
        return "python"
    return "typescript" if lower.endswith((".ts", ".mts", ".cts", ".tsx")) else "javascript"


def extract_tools(files: dict[str, str]) -> tuple[list[ToolDef], list[str]]:
    tools: list[ToolDef] = []
    languages: set[str] = set()
    for path in sorted(files):
        if not is_source(path):
            continue
        languages.add(language_of(path))
        found = extract_python(path, files[path]) if path.lower().endswith(".py") else extract_ts(path, files[path])
        tools.extend(found)
    return tools, sorted(languages)
