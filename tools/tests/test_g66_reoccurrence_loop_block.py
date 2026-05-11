"""G66 reoccurrence guard: sync tool dispatch blocks asyncio loop.

This test reproduces the 5th-reoccurrence symptom of G66, observed in session
``b253741b`` (mcp-logs ``2026-04-27T20-22-45-640Z.jsonl``):

- ``memory_search`` was async-fixed in commit b76e740 (G66 RESOLVED), but the
  remaining 37 ``@mcp.tool()`` functions are still ``sync def`` (notably
  ``session_start``).
- FastMCP's sync tool dispatch calls ``return fn(**args)`` directly on the
  asyncio main loop, blocking it for the full duration of the sync tool.
- While blocked, ``memory_search``'s 90s watchdog timer fires and produces a
  TIMEOUT response, but the STDIO writer task cannot run, so delivery is
  deferred until the sync tool yields.
- In the production incident the response sat in the write queue for 9h14m
  until the user cancelled, at which point it was flushed with an unknown
  message ID and the transport closed.

Hypothesis verification: this test issues ``session_start`` (sync, heavy) and
``memory_search`` concurrently via a single MCP ClientSession. Pre-fix, the
``memory_search`` response is delayed until ``session_start`` completes (or
much longer if subsequent sync tools chain together). Post-fix (all tools
async), both responses arrive within their individual watchdog timeouts.

MED-4 (analysis_g66_async_unification_pre_impl.md) note on flakiness: the
assertion is the **post-fix SLO only** (``elapsed_B < 100s``). Pre-fix
behaviour is documented in the run log / PR description, not asserted in CI.
"""
import asyncio
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
async def test_session_start_then_memory_search_responds_within_tolerance():
    """Concurrent session_start + memory_search must both finish under SLO.

    Single ClientSession, two ``call_tool`` awaits launched via
    ``asyncio.gather`` with task B delayed 50ms after task A so that
    ``session_start`` enters the server first.

    Post-fix SLO: ``elapsed_B < 100s`` (memory_search watchdog 90s + transport
    overhead). Pre-fix typically takes 90-200s+ depending on how long
    session_start blocks the loop and how many sync tools chain after it.
    """
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command="python",
        args=[str(SERVER_PATH)],
        env={"MEMORY_DIR": str(MEMORY_DIR), "PATH": os.environ.get("PATH", "")},
    )

    async def call_session_start(session):
        t0 = time.time()
        result = await session.call_tool("session_start", {})
        return time.time() - t0, result

    async def call_memory_search(session):
        await asyncio.sleep(0.05)
        t0 = time.time()
        result = await session.call_tool(
            "memory_search",
            {"query": "g66 regression", "limit": 2},
        )
        return time.time() - t0, result

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            (elapsed_a, result_a), (elapsed_b, result_b) = await asyncio.gather(
                call_session_start(session),
                call_memory_search(session),
            )

    text_b = result_b.content[0].text if result_b.content else ""

    # Post-fix SLO: memory_search must complete within watchdog + transport
    # overhead even when a heavy sync tool is running concurrently.
    assert elapsed_b < 100.0, (
        f"memory_search took {elapsed_b:.1f}s while session_start ran "
        f"concurrently (elapsed_a={elapsed_a:.1f}s). "
        f"Expected <100s — sync tool dispatch must not block the asyncio loop "
        f"during memory_search's watchdog window."
    )
    # If the watchdog fired, the message must be the diagnostic, not a hang
    # masquerading as a normal response.
    if elapsed_b > 60:
        assert "=== TIMEOUT ===" in text_b, (
            f"Long elapsed ({elapsed_b:.1f}s) but no TIMEOUT diagnostic; "
            f"got: {text_b[:200]}"
        )
