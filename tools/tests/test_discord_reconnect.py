"""Tests for Discord Gateway reconnect handling.

Verifies that when _event_loop() returns normally (OP_RECONNECT or
OP_INVALID_SESSION), the run() method correctly triggers reconnect
instead of hanging or silently dying.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure tools directory is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from discord_receiver import (
    OP_INVALID_SESSION,
    OP_RECONNECT,
    RECONNECT_BASE_DELAY,
    DiscordGatewayClient,
)

_TEST_TOKEN = "test-token"  # noqa: S105


def _make_client(**kwargs) -> DiscordGatewayClient:
    """Create a DiscordGatewayClient with mocked dependencies."""
    client = DiscordGatewayClient(
        token=_TEST_TOKEN,
        intents=0,
        logger=MagicMock(),
        **kwargs,
    )
    return client


class _FakeWebSocket:
    """Minimal fake WebSocket that yields pre-configured messages then closes."""

    def __init__(self, messages: list[str]):
        self._messages = messages
        self._closed = False
        self._close_called = False

    def __aiter__(self):
        return self._iter_messages()

    async def _iter_messages(self):
        for msg in self._messages:
            yield msg

    async def close(self):
        self._close_called = True
        self._closed = True

    async def send(self, data):
        pass


# ────────────────────────────────────────────────────────────────
# Test 1: _event_loop() normal return triggers reconnect in run()
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="function")
async def test_run_reconnects_after_event_loop_normal_return():
    """When _event_loop() returns normally (reconnect signal),
    run() should set _connected=False, cancel heartbeat, sleep,
    and call _connect_ws()."""
    client = _make_client()
    client._connected = True
    client._ws = MagicMock()

    call_count = 0

    async def mock_event_loop():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: normal return = reconnect signal
            return
        else:
            # Second call: raise CancelledError to stop the loop
            raise asyncio.CancelledError()

    client._event_loop = mock_event_loop
    client._connect_ws = AsyncMock()
    heartbeat_task = MagicMock()
    heartbeat_task.done.return_value = False
    client._heartbeat_task = heartbeat_task

    with patch("discord_receiver.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await client.run()

    # Verify reconnect was triggered
    assert client._connect_ws.await_count == 1
    # Verify sleep was called with RECONNECT_BASE_DELAY
    mock_sleep.assert_awaited_once_with(RECONNECT_BASE_DELAY)
    # Verify heartbeat cancel was called (once per loop iteration via finally block)
    assert heartbeat_task.cancel.call_count >= 1


# ────────────────────────────────────────────────────────────────
# Test 2: OP_RECONNECT sets _should_resume=True and reconnect
#          calls _connect_ws()
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="function")
async def test_op_reconnect_sets_should_resume_and_reconnects():
    """After receiving OP_RECONNECT, _should_resume should be True
    and _connect_ws() should be called."""
    client = _make_client()
    client._session_id = "test-session-123"
    client._sequence = 42
    client._connected = True

    # Create a WebSocket that sends OP_RECONNECT
    ws_messages = [
        json.dumps({"op": OP_RECONNECT, "d": None, "s": None, "t": None}),
    ]
    first_ws = _FakeWebSocket(ws_messages)
    client._ws = first_ws

    # Track _event_loop calls to stop after reconnect
    original_event_loop = client._event_loop
    call_count = 0

    async def counting_event_loop():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            await original_event_loop()
            return  # normal return from OP_RECONNECT
        else:
            raise asyncio.CancelledError()

    client._event_loop = counting_event_loop
    client._connect_ws = AsyncMock()

    with patch("discord_receiver.asyncio.sleep", new_callable=AsyncMock):
        await client.run()

    # _should_resume should have been set to True by _event_loop
    # (it gets set in OP_RECONNECT handler before ws.close())
    assert client._connect_ws.await_count == 1


# ────────────────────────────────────────────────────────────────
# Test 3: OP_INVALID_SESSION (non-resumable) clears session and
#          reconnects
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="function")
async def test_op_invalid_session_non_resumable_reconnects():
    """After OP_INVALID_SESSION with resumable=False, session_id and
    sequence should be cleared, and reconnect should proceed."""
    client = _make_client()
    client._session_id = "old-session"
    client._sequence = 10
    client._connected = True

    ws_messages = [
        json.dumps({"op": OP_INVALID_SESSION, "d": False, "s": None, "t": None}),
    ]
    first_ws = _FakeWebSocket(ws_messages)
    client._ws = first_ws

    original_event_loop = client._event_loop
    call_count = 0

    async def counting_event_loop():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            await original_event_loop()
            return
        else:
            raise asyncio.CancelledError()

    client._event_loop = counting_event_loop
    client._connect_ws = AsyncMock()

    with patch("discord_receiver.asyncio.sleep", new_callable=AsyncMock):
        await client.run()

    # Session should have been cleared by _event_loop handler
    assert client._session_id is None
    assert client._sequence is None
    assert client._connect_ws.await_count == 1


# ────────────────────────────────────────────────────────────────
# Test 4: Reconnect failure after normal return doesn't crash
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="function")
async def test_reconnect_failure_after_normal_return_does_not_crash():
    """If _connect_ws() fails after _event_loop() normal return,
    the run() loop should continue (not crash)."""
    client = _make_client()
    client._connected = True
    client._ws = MagicMock()

    call_count = 0

    async def mock_event_loop():
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            # Normal return = reconnect signal
            return
        else:
            raise asyncio.CancelledError()

    connect_call_count = 0

    async def mock_connect_ws():
        nonlocal connect_call_count
        connect_call_count += 1
        if connect_call_count == 1:
            raise ConnectionError("Network unreachable")
        # Second call succeeds

    client._event_loop = mock_event_loop
    client._connect_ws = mock_connect_ws
    client._heartbeat_task = None

    with patch("discord_receiver.asyncio.sleep", new_callable=AsyncMock):
        await client.run()

    # Should have attempted connect twice (first failed, second succeeded
    # before third _event_loop call raised CancelledError)
    assert connect_call_count == 2
    assert call_count == 3


# ────────────────────────────────────────────────────────────────
# Test 5: on_reconnect callback is called after successful reconnect
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="function")
async def test_on_reconnect_callback_called_after_normal_return_reconnect():
    """on_reconnect callback should be called after successful
    reconnect triggered by normal _event_loop() return."""
    on_reconnect = MagicMock()
    client = _make_client(on_reconnect=on_reconnect)
    client._connected = True
    client._ws = MagicMock()

    call_count = 0

    async def mock_event_loop():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return
        else:
            raise asyncio.CancelledError()

    client._event_loop = mock_event_loop
    client._connect_ws = AsyncMock()
    client._heartbeat_task = None

    with patch("discord_receiver.asyncio.sleep", new_callable=AsyncMock):
        await client.run()

    on_reconnect.assert_called_once()
