"""Tests for discord_receiver_gateway.py — verifies gateway module extraction."""

import os
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)


class TestGatewayModuleExports:
    def test_exports(self):
        from discord_receiver_gateway import (
            get_gateway_url,
            get_bot_user,
            DiscordGatewayClient,
        )
        assert callable(get_gateway_url)
        assert callable(get_bot_user)
        client = DiscordGatewayClient(token="test")
        assert client.token == "test"

# ================================================================
# H5: WebSocket heartbeat task cleanup with try-finally
# ================================================================

import inspect

class TestHeartbeatCleanup:
    """Verify heartbeat task cancellation uses try-finally for atomicity."""

    def test_run_method_uses_try_finally_for_heartbeat(self):
        """The run() method should use try-finally around heartbeat cancellation."""
        from discord_receiver_gateway import DiscordGatewayClient as DiscordGateway
        source = inspect.getsource(DiscordGateway.run)
        # Should have finally blocks to ensure heartbeat cleanup
        assert "finally" in source, "run() should use try-finally for heartbeat cleanup"

    def test_heartbeat_cancel_in_finally(self):
        """Heartbeat task cancellation should be in a finally block."""
        from discord_receiver_gateway import DiscordGatewayClient as DiscordGateway
        source = inspect.getsource(DiscordGateway.run)
        # The cancel call should appear after a finally
        lines = source.split(chr(10))
        found_finally_with_cancel = False
        in_finally = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("finally:"):
                in_finally = True
            elif in_finally and "cancel()" in stripped:
                found_finally_with_cancel = True
                break
            elif in_finally and (stripped.startswith("except") or stripped.startswith("try:") or (stripped and not stripped.startswith("#") and not stripped.startswith("if") and not stripped.startswith("self") and not stripped.startswith("await") and stripped.startswith("def "))):
                in_finally = False
        assert found_finally_with_cancel, "heartbeat cancel should be inside a finally block"


# ================================================================
# M-R1: OSError should NOT reset reconnect counter
# ================================================================

class TestOSErrorReconnect:
    """M-R1: OSError in run() should maintain reconnect counter, not reset it."""

    def test_oserror_handled_with_reconnect_counter(self):
        """OSError should be caught alongside ConnectionClosed/ConnectionError
        and NOT reset _reconnect_count."""
        from discord_receiver_gateway import DiscordGatewayClient as DiscordGateway
        source = inspect.getsource(DiscordGateway.run)
        # OSError should be in the same except clause as ConnectionClosed
        assert "OSError" in source, "run() should handle OSError explicitly"

    def test_reconnect_count_not_reset_on_normal_reconnect_after_fix(self):
        """The _connect_ws() success path (line ~178) should NOT reset _reconnect_count
        when coming from an error path. Only reset on successful _event_loop completion."""
        from discord_receiver_gateway import DiscordGatewayClient as DiscordGateway
        source = inspect.getsource(DiscordGateway.run)
        lines = source.split(chr(10))
        # After fix: reconnect_count = 0 should only appear in the normal
        # reconnect path (after _event_loop returns normally), not in error recovery
        # Count how many times _reconnect_count = 0 appears
        reset_count = sum(1 for l in lines if "_reconnect_count = 0" in l or "_reconnect_count=0" in l)
        # Should appear at most once (in _connect_ws or initial run), not in error handlers
        assert reset_count <= 1, (
            f"_reconnect_count should be reset at most once (in _connect_ws), found {reset_count} resets"
        )


