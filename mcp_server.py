"""FreshGuard as an MCP tool (Problem 04 path A, no Go required).

Run:  python mcp_server.py
Then register it in Bifrost's MCP gateway (see docs.getbifrost.ai) using the
URL printed by the server. Any model routed through Bifrost can then call the
`verify_claims` tool to re-check time-sensitive facts against live sources.
"""
from mcp.server.fastmcp import FastMCP

from main import run_check

mcp = FastMCP("freshguard", host="0.0.0.0", port=8765)


@mcp.tool()
def verify_claims(text: str, max_claims: int = 5) -> dict:
    """Find time-sensitive claims in `text`, re-check them with live web
    search, and return a freshness score (0-100) and sources per claim."""
    return run_check(text, max_claims)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
