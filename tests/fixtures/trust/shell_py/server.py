import subprocess

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("runner")


@mcp.tool()
def run(command: str) -> str:
    """Run a shell command and return its output."""
    return subprocess.run(command, shell=True, capture_output=True, text=True).stdout
