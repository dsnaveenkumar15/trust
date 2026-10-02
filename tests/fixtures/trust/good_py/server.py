import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("weather")


@mcp.tool()
def forecast(latitude: float, longitude: float) -> str:
    """Get the forecast for a location from the public weather API.

    When you call this tool, pass coordinates in decimal degrees.
    """
    response = httpx.get("https://api.weather.gov/points/%s,%s" % (latitude, longitude))
    return response.text
