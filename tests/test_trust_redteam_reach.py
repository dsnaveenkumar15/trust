from trust.redteam import reach
from trust.redteam.models import Target, Tool

SRC = '''import os, subprocess, requests

@mcp.tool()
def fetch(url: str):
    """Fetches a page."""
    return requests.get(url).text

@mcp.tool()
def run(cmd: str):
    """Runs it."""
    key = os.environ["KEY"]
    return subprocess.run(cmd, shell=True)
'''


def repo_target():
    tools = [Tool("fetch", "Fetches a page.", {}, "s.py", 3), Tool("run", "Runs it.", {}, "s.py", 8)]
    findings = [{"rule": "shell-exec", "file": "s.py", "line": 12}]
    return Target("repo", tools, {"s.py": SRC}, "a/b", "c" * 40, findings)


def test_source_lines_are_attributed_to_the_tool_that_contains_them():
    m = reach.reach_map(repo_target())
    assert m["fetch"] == ["network"]
    assert m["run"] == ["env", "shell"]


def test_tool_of_uses_the_range_up_to_the_next_tool():
    t = repo_target()
    assert reach.tool_of(t, "s.py", 5) == "fetch" and reach.tool_of(t, "s.py", 12) == "run"
    assert reach.tool_of(t, "s.py", 1) is None and reach.tool_of(t, "other.py", 5) is None


def test_manifest_reach_comes_from_parameter_names_and_mentions_of_other_tools():
    tools = [Tool("save", "Saves a file. Then call notify.", {"properties": {"path": {}, "url": {}}}),
             Tool("notify", "Sends a message.", {"properties": {"command": {}}})]
    m = reach.reach_map(Target("manifest", tools, {}))
    assert m["save"] == ["files", "network", "other_tools"] and m["notify"] == ["shell"]
