from mcp.server.fastmcp import FastMCP

mcp = FastMCP("deploy")
API_KEY = "sk-thisisnotarealkeyjustafixture1234567890"


@mcp.tool()
def status() -> str:
    """Report deployment status."""
    return "ok"
