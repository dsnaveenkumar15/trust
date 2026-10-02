"""Headless `claude -p` backend. Owner's Pro login, batch only, never tools, never public traffic."""
import json
import re
import subprocess
import tempfile

from core.llm import AuthExpired, BackendError, LLMError, Prompt, RateLimited

# Whole-word patterns: a bare "429" inside a request id must not turn a lasting 5xx into an endless deferral.
RATE_LIMIT_RE = re.compile(r"usage limit|rate[ _]limit|\boverloaded\b|\b429\b|\b529\b")
AUTH_RE = re.compile(r"/login|not logged in|invalid api key|authentication|\boauth\b|\b401\b|\b403\b")


def classify_error(text: str, status: int | None = None) -> LLMError:
    """Prefer the envelope's HTTP status; fall back to whole-word text markers."""
    if status in (429, 529):
        return RateLimited(text[:500] or f"HTTP {status}")
    if status in (401, 403):
        return AuthExpired(text[:500] or f"HTTP {status}")
    lowered = text.lower()
    if status is None and RATE_LIMIT_RE.search(lowered):
        return RateLimited(text[:500])
    if status is None and AUTH_RE.search(lowered):
        return AuthExpired(text[:500])
    return BackendError(text[:500] or "empty claude output")


def parse_cli_output(returncode: int, stdout: str, stderr: str) -> str:
    """Turn `claude -p --output-format json` output into raw JSON text, or raise a typed error."""
    try:
        envelope = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        raise classify_error(f"{stderr} {stdout}".strip())
    if not isinstance(envelope, dict):
        raise BackendError(f"unexpected claude envelope: {stdout[:200]}")
    if returncode != 0 or envelope.get("is_error"):
        status = envelope.get("api_error_status")
        raise classify_error(f"{envelope.get('result', '')} {stderr}".strip(),
                             status if isinstance(status, int) else None)
    structured = envelope.get("structured_output")
    if structured is not None:
        return json.dumps(structured)
    result = envelope.get("result")
    if not isinstance(result, str) or not result.strip():
        raise BackendError("empty result in claude output")
    return result


class ClaudeCliBackend:
    name = "claude_cli"

    def __init__(self, exe: str = "claude", model: str = "sonnet", timeout_s: int = 600, run=subprocess.run):
        self.exe, self.model, self.timeout_s, self.run = exe, model, timeout_s, run

    def build_args(self, prompt: Prompt, json_schema: dict) -> list[str]:
        return [
            self.exe, "-p",
            "--output-format", "json",
            "--model", self.model,
            "--tools", "",                       # no tools: audited content can never trigger actions
            "--strict-mcp-config",               # no MCP servers
            "--setting-sources", "project",      # skip user settings/plugins/hooks; cwd is empty
            "--no-session-persistence",
            "--system-prompt", prompt.system,
            "--json-schema", json.dumps(json_schema),
        ]

    def __call__(self, prompt: Prompt, json_schema: dict) -> str:
        with tempfile.TemporaryDirectory(prefix="planets-claude-") as cwd:
            try:
                proc = self.run(
                    self.build_args(prompt, json_schema),
                    input=prompt.user, capture_output=True, text=True, encoding="utf-8",
                    timeout=self.timeout_s, cwd=cwd,
                )
            except subprocess.TimeoutExpired as exc:
                raise BackendError(f"claude timed out after {self.timeout_s}s") from exc
            except FileNotFoundError as exc:
                raise BackendError(f"claude executable not found: {self.exe}") from exc
        return parse_cli_output(proc.returncode, proc.stdout, proc.stderr)
