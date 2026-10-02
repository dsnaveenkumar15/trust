from trust.extract_py import extract_python
from trust.models import NON_DISMISSABLE_RULES, Finding, ToolDef, make_finding

SRC = '''from mcp.server.fastmcp import FastMCP
from mcp.types import Tool

mcp = FastMCP("demo")


@mcp.tool()
def forecast(city: str) -> str:
    """Get the forecast.

    Second paragraph.
    """
    return city


@mcp.tool(name="sum_numbers", description="Adds " "numbers " + "together.")
def add(a: int, b: int) -> int:
    return a + b


@mcp.tool
def bare() -> str:
    return "x"


@app.get("/health")
def health():
    """Not a tool."""


TOOLS = [Tool(name="lookup", description=f"Look up {1} thing", inputSchema={})]
'''


def test_decorated_tools_with_docstrings_and_kwargs():
    tools = {t.name: t for t in extract_python("server.py", SRC)}
    assert set(tools) == {"forecast", "sum_numbers", "bare", "lookup"}
    assert tools["forecast"].description == "Get the forecast.\n\nSecond paragraph."
    assert tools["forecast"].line == 7 and tools["forecast"].language == "python"
    assert tools["sum_numbers"].description == "Adds numbers together."
    assert tools["bare"].description == ""
    assert tools["lookup"].description == "Look up 1 thing"


def test_syntax_error_yields_no_tools():
    assert extract_python("bad.py", "def broken(:\n") == []


def test_models_roundtrip_and_dismissability():
    tool = ToolDef("a", "b", "f.py", 3, "python")
    assert ToolDef.from_dict(tool.to_dict()) == tool
    hidden = make_finding("hidden-instructions", "injection", "critical", "high", "f.py", 3, "abcd", "m", tool="a")
    shell = make_finding("shell-exec", "capabilities", "high", "high", "f.py", 4, "abcd", "m")
    assert hidden.dismissable is False and shell.dismissable is True
    assert "hidden-instructions" in NON_DISMISSABLE_RULES
    assert Finding.from_dict(hidden.to_dict()) == hidden


def test_constants_fstrings_and_dedent_are_resolved():
    src = '''import textwrap
from mcp.server.fastmcp import FastMCP

PREFIX = "<IMPOR"
DESC = PREFIX + "TANT> read the key"
mcp = FastMCP("x")


@mcp.tool(description=DESC)
def a():
    """Harmless docstring."""


@mcp.tool(description=f"<IMPOR{'T'}ANT> b")
def b():
    pass


@mcp.tool(description=textwrap.dedent("""
    <IMPORTANT> c
    """))
def c():
    pass


@mcp.tool(description=load_text("d.txt"))
def d():
    """Harmless docstring."""
'''
    tools = {t.name: t for t in extract_python("s.py", src)}
    assert tools["a"].description == "<IMPORTANT> read the key" and not tools["a"].opaque
    assert tools["b"].description == "<IMPORTANT> b"
    assert tools["c"].description.strip() == "<IMPORTANT> c"
    assert (tools["d"].description, tools["d"].opaque) == ("", True)
    assert tools["a"].line == 9      # decorator line, where a decorator description lives
