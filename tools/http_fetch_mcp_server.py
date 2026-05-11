#!/usr/bin/env python3
"""MCP server for raw HTTP fetch.

Provides http_fetch tool — raw HTTP GET/POST without AI processing.
Unlike WebFetch, returns unprocessed response data for JSON APIs,
status checks, and Heartbeat external monitoring.

IMPORTANT: For stdio transport, never print() to stdout.
"""

import sys

# Add tools directory to path
import os
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from mcp.server.fastmcp import FastMCP
from http_fetch import http_fetch as _http_fetch

mcp = FastMCP("http-fetch")


@mcp.tool()
def http_fetch(
    url: str,
    method: str = "GET",
    headers: str = "",
    body: str = "",
) -> str:
    """Fetch a URL and return raw response (no AI processing).

    Unlike WebFetch which summarizes via AI, this returns the actual
    response data. Use for JSON APIs, status checks, raw HTML.

    Args:
        url: The URL to fetch (required).
        method: HTTP method — GET or POST (default: GET).
        headers: Optional headers as "Key: Value" lines (newline-separated).
        body: Optional request body (for POST).
    """
    return _http_fetch(url=url, method=method, headers=headers, body=body)


if __name__ == "__main__":
    mcp.run(transport="stdio")
