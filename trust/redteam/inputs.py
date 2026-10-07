"""What the red team attacks: a pasted tools/list result or a public GitHub repository, both turned into one Target.
One function per input kind (INPUTS); a new kind (config file, OpenAPI spec) is one more function."""
import json

from core.github import GitHubError, GitHubRateLimited, RepoNotFound, repo_from_url
from trust.extract import extract_tools
from trust.models import ToolDef
from trust.pipeline import wanted_path
from trust.redteam.models import Target, Tool
from trust.rules_code import code_findings
from trust.rules_desc import description_findings

MANIFEST_FILE = "tools.json"
MAX_TEXT = 200_000
MAX_TOOLS = 200
MAX_DESCRIPTION = 20_000
GENERIC_SCHEMA = {"type": "object", "properties": {"input": {"type": "string"}}, "additionalProperties": True}


class InputError(ValueError):
    """A visitor-facing problem with the input (shown as-is)."""


def _schema(raw) -> dict:
    """A well-formed object schema (property names to dicts, required as a list of names), or the generic one: the
    schema is visitor text and later code sorts, serialises and walks it."""
    if not isinstance(raw, dict) or raw.get("type", "object") != "object" or not isinstance(raw.get("properties"), dict):
        return dict(GENERIC_SCHEMA)
    props = {str(k): (v if isinstance(v, dict) else {}) for k, v in raw["properties"].items()}
    required = raw.get("required")
    return {"type": "object", "properties": props,
            "required": [r for r in required if isinstance(r, str) and r in props] if isinstance(required, list) else []}


def from_manifest(text: str) -> Target:
    if len(text) > MAX_TEXT:
        raise InputError("That tool list is too large; paste up to 200 KB.")
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        raise InputError("The tool list is not valid JSON.") from None
    raw = data.get("tools") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise InputError('Expected a tools/list result like {"tools": [...]}.')
    if not raw:
        raise InputError("The list has no tools.")
    if len(raw) > MAX_TOOLS:
        raise InputError(f"At most {MAX_TOOLS} tools per run.")
    entries = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
            raise InputError("Every tool needs a name.")
        desc = item.get("description") if isinstance(item.get("description"), str) else ""
        entries.append({"name": item["name"][:200], "description": desc[:MAX_DESCRIPTION],
                        "inputSchema": _schema(item.get("inputSchema"))})
    pretty = json.dumps({"tools": entries}, indent=2, ensure_ascii=False)
    lines = pretty.splitlines()
    tools, defs = [], []
    for e in entries:
        needle = '"name": ' + json.dumps(e["name"], ensure_ascii=False)
        line = next((i + 1 for i, text_line in enumerate(lines) if needle in text_line), 1)
        tools.append(Tool(e["name"], e["description"], e["inputSchema"], MANIFEST_FILE, line))
        defs.append(ToolDef(e["name"], e["description"], MANIFEST_FILE, line, "manifest"))
    files = {MANIFEST_FILE: pretty}
    return Target("manifest", tools, files, findings=[f.to_dict() for f in description_findings(defs, files)])


def from_repo(github, url: str) -> Target:
    repo = repo_from_url((url or "").strip())
    if repo is None:
        raise InputError("Enter a public GitHub repository URL, like https://github.com/owner/repo.")
    try:
        snap = github.snapshot(repo, None, wanted_path)
    except RepoNotFound:
        raise InputError("Repository not found or private. Only public GitHub repositories can be read.") from None
    except GitHubRateLimited:
        raise InputError("GitHub is busy right now; paste the tool list instead.") from None
    except (GitHubError, ValueError):
        raise InputError("Could not read that repository from GitHub. Try again shortly.") from None
    defs, languages = extract_tools(snap["files"])
    if not languages:
        raise InputError("No Python or TypeScript source found in this repository.")
    if not defs:
        raise InputError("No MCP tools found in this repository's source.")
    tools = [Tool(d.name, d.description, dict(GENERIC_SCHEMA), d.file, d.line) for d in defs[:MAX_TOOLS]]
    findings = description_findings(defs, snap["files"]) + code_findings(snap["files"])
    notes = ["Some files were too large to read."] if snap["skipped"] or snap["truncated"] else []
    return Target("repo", tools, snap["files"], snap["repo"], snap["sha"], [f.to_dict() for f in findings], notes)


INPUTS = {"manifest": lambda text, github=None: from_manifest(text),
          "repo": lambda text, github=None: from_repo(github, text)}
