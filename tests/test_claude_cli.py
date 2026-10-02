import json
import os
import subprocess
from pathlib import Path

import pytest

from core.claude_cli import ClaudeCliBackend, parse_cli_output
from core.llm import AuthExpired, BackendError, Prompt, RateLimited

P = Prompt("demo", "demo@1", "Reply with JSON.", "Say ok")
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
FIXTURE = Path(__file__).parent / "fixtures" / "claude_cli_envelope.json"


def fake_run(returncode=0, stdout="", stderr="", record=None, exc=None):
    def run(args, **kwargs):
        if record is not None:
            record.update(args=args, kwargs=kwargs)
        if exc:
            raise exc
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)
    return run


def test_args_disable_tools_and_mcp_and_use_json():
    args = ClaudeCliBackend().build_args(P, SCHEMA)
    assert args[:2] == ["claude", "-p"]
    assert args[args.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in args
    assert args[args.index("--setting-sources") + 1] == "project"
    assert args[args.index("--output-format") + 1] == "json"
    assert json.loads(args[args.index("--json-schema") + 1]) == SCHEMA
    assert args[args.index("--system-prompt") + 1] == P.system


def test_runs_in_empty_temp_dir_with_prompt_on_stdin():
    seen = {}
    envelope = json.dumps({"type": "result", "is_error": False, "result": '{"ok": true}'})
    ClaudeCliBackend(run=fake_run(stdout=envelope, record=seen))(P, SCHEMA)
    assert seen["kwargs"]["input"] == "Say ok"
    assert seen["kwargs"]["cwd"] and os.path.abspath(seen["kwargs"]["cwd"]) != os.getcwd()


def test_structured_output_preferred():
    envelope = json.dumps({"is_error": False, "result": "ignored", "structured_output": {"ok": True}})
    assert json.loads(parse_cli_output(0, envelope, "")) == {"ok": True}


def test_result_text_used_without_structured_output():
    envelope = json.dumps({"is_error": False, "result": '{"ok": true}'})
    assert parse_cli_output(0, envelope, "") == '{"ok": true}'


def test_usage_limit_is_rate_limited():
    with pytest.raises(RateLimited):
        parse_cli_output(1, "", "Claude usage limit reached. Your limit will reset at 3am.")


def test_is_error_envelope_with_login_message_is_auth_expired():
    envelope = json.dumps({"is_error": True, "result": "Invalid API key · Please run /login"})
    with pytest.raises(AuthExpired):
        parse_cli_output(1, envelope, "")


def test_digits_inside_ids_are_not_rate_limits():
    envelope = json.dumps({"is_error": True, "result": "API Error: 500 req_429abc limit reached 4011"})
    with pytest.raises(BackendError):
        parse_cli_output(1, envelope, "")


def test_api_error_status_429_is_rate_limited():
    envelope = json.dumps({"is_error": True, "result": "API Error", "api_error_status": 429})
    with pytest.raises(RateLimited):
        parse_cli_output(1, envelope, "")


def test_api_error_status_401_is_auth_expired():
    envelope = json.dumps({"is_error": True, "result": "API Error", "api_error_status": 401})
    with pytest.raises(AuthExpired):
        parse_cli_output(1, envelope, "")


def test_garbage_stdout_is_backend_error():
    with pytest.raises(BackendError):
        parse_cli_output(1, "Segmentation fault", "boom")


def test_empty_result_is_backend_error():
    with pytest.raises(BackendError):
        parse_cli_output(0, json.dumps({"is_error": False, "result": "  "}), "")


def test_timeout_and_missing_exe_are_backend_errors():
    with pytest.raises(BackendError):
        ClaudeCliBackend(run=fake_run(exc=subprocess.TimeoutExpired("claude", 1)))(P, SCHEMA)
    with pytest.raises(BackendError):
        ClaudeCliBackend(run=fake_run(exc=FileNotFoundError("claude")))(P, SCHEMA)


@pytest.mark.skipif(not FIXTURE.exists(), reason="record a real envelope first (Task 4 Step 5)")
def test_parses_recorded_real_envelope():
    data = json.loads(parse_cli_output(0, FIXTURE.read_text(encoding="utf-8"), ""))
    assert data == {"ok": True}
