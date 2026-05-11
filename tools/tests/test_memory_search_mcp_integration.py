"""G66 regression guard: real MCP transport integration test for memory_search.

Spawns a fresh memory_mcp_server.py subprocess via mcp.client.stdio,
calls memory_search through the actual JSON-RPC protocol, and asserts that
the response arrives within seconds of the watchdog firing.

Pre-fix (commit 3501182, sync def): the watchdog generated the TIMEOUT
string at 90s, but FastMCP's sync tool dispatch blocked the asyncio loop,
so STDIO delivery was deferred for hundreds of seconds. This test would
have caught that regression — the unit-level watchdog tests did not.

Post-fix (commit b76e740, async def + asyncio.to_thread): the loop stays
free during the watchdog wait; response delivery is on the order of
tens of milliseconds after the timeout fires.
"""
import os
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SERVER_PATH = REPO_ROOT / "tools" / "memory_mcp_server.py"
MEMORY_DIR = Path(os.environ.get("CLAUDE_PROJECT_ROOT", str(REPO_ROOT))) / "memory"


pytestmark = pytest.mark.asyncio


@pytest.mark.skipif(
    not SERVER_PATH.exists() or not MEMORY_DIR.exists(),
    reason="requires real memory_mcp_server.py and memory dir",
)
async def test_memory_search_responds_within_watchdog_plus_tolerance():
    """Watchdog timeout (90s) + transport overhead must stay under ~95s.

    Pre-fix would take 300+s due to asyncio loop being blocked.
    """
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command="python",
        args=[str(SERVER_PATH)],
        env={"MEMORY_DIR": str(MEMORY_DIR), "PATH": os.environ.get("PATH", "")},
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            t0 = time.time()
            result = await session.call_tool(
                "memory_search",
                {"query": "G66 regression guard", "limit": 2},
            )
            elapsed = time.time() - t0

    text = result.content[0].text if result.content else ""
    # Either we get a real result fast, or the watchdog fires close to 90s.
    # We never want 100s+ delivery delay.
    assert elapsed < 100.0, (
        f"memory_search took {elapsed:.1f}s through real MCP transport "
        f"(should be <100s — async loop must not be blocked during watchdog)"
    )
    # If the watchdog fired, the message should be the diagnostic, not a hang.
    if elapsed > 60:
        assert "=== TIMEOUT ===" in text, (
            f"Long elapsed ({elapsed:.1f}s) but no TIMEOUT diagnostic; got: {text[:200]}"
        )
