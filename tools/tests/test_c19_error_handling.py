#!/usr/bin/env python3
"""Tests for C19 error handling improvement — silent except:pass → log output.

TDD: Tests written before implementation.
Verifies that each of the 10 except-pass sites now produces log output
when an exception occurs, without changing control flow.
"""

import asyncio
import json
import os
import sys
import tempfile
import shutil
import sqlite3
import unittest
from io import StringIO
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path

TOOLS_DIR = str(Path(__file__).resolve().parent.parent)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

FAKE_TOKEN = "FAKE_TOKEN_FOR_TESTING"


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# =============================================================================
# 1. bot_personality.py — DB close warning
# =============================================================================

class TestBotPersonalityDBCloseWarning(unittest.TestCase):
    """bot_personality.py L138: DB close failure should log debug message."""

    def test_db_close_exception_logs_debug(self):
        """When conn.close() raises, logger.debug should be called."""
        from bot_personality import _create_memory_search_fn

        tmpdir = tempfile.mkdtemp()
        try:
            # Create a dummy DB file so os.path.exists check passes
            db_path = os.path.join(tmpdir, "semantic_index.db")
            open(db_path, "w").close()

            search_fn = _create_memory_search_fn(tmpdir)

            with patch("bot_personality.logger") as mock_logger:
                mock_conn = MagicMock()
                mock_conn.execute.return_value.fetchall.return_value = []
                mock_conn.close.side_effect = Exception("close failed")
                with patch("bot_personality.sqlite3.connect", return_value=mock_conn):
                    result = search_fn("test query", 5)

                mock_logger.debug.assert_called_once()
                call_args = mock_logger.debug.call_args[0][0]
                self.assertIn("close failed", call_args)

            self.assertIsInstance(result, list)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# =============================================================================
# 2. cron_daemon.py — tick timestamp write failed
# =============================================================================

class TestCronDaemonTickTimestampWarning(unittest.TestCase):
    """cron_daemon.py L412: tick timestamp write failure should log debug."""

    def test_tick_timestamp_failure_logs_debug(self):
        from cron_daemon import CronDaemon

        tmpdir = tempfile.mkdtemp()
        try:
            daemon = CronDaemon.__new__(CronDaemon)
            daemon.logger = MagicMock()
            daemon.registry_dir = tmpdir
            daemon.registry = MagicMock()
            daemon.registry.list_enabled.return_value = []
            daemon._running = True

            with patch("cron_daemon.write_tick_timestamp", side_effect=Exception("disk full")):
                daemon.tick()

            daemon.logger.debug.assert_called()
            found = any("disk full" in str(c) for c in daemon.logger.debug.call_args_list)
            self.assertTrue(found, "Expected 'disk full' in debug log output")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# =============================================================================
# 3. cron_daemon.py — PID check warning (stop_daemon)
# =============================================================================

class TestCronDaemonPIDCheckWarning(unittest.TestCase):
    """cron_daemon.py L470: PID check failure should print to stderr."""

    def test_pid_check_exception_prints_stderr(self):
        import cron_daemon

        stderr_capture = StringIO()
        with patch.object(cron_daemon, "read_pid_file", return_value=12345), \
             patch.object(cron_daemon, "is_process_alive", return_value=True), \
             patch.object(cron_daemon, "remove_pid_file"), \
             patch.object(cron_daemon, "sys") as mock_sys:
            mock_sys.platform = "win32"
            mock_sys.stderr = stderr_capture
            with patch("subprocess.run", side_effect=Exception("wmic failed")):
                try:
                    cron_daemon.stop_daemon()
                except Exception:
                    pass

        output = stderr_capture.getvalue()
        self.assertIn("PID check warning", output)
        self.assertIn("wmic failed", output)


# =============================================================================
# 4-5. cron_mcp_server.py — running_at clear failure (source check)
# =============================================================================

class TestCronMCPRunningAtClearFailure(unittest.TestCase):
    """cron_mcp_server.py L549/L639: running_at clear failure should log."""

    def test_running_at_clear_failure_pattern_in_source(self):
        """Verify the source contains the expected stderr log patterns."""
        import cron_mcp_server
        import inspect
        source = inspect.getsource(cron_mcp_server)
        # Should have 2 occurrences (async L549 + sync L639)
        count = source.count('Failed to clear running_at')
        self.assertGreaterEqual(count, 2,
            "Expected at least 2 'Failed to clear running_at' patterns in cron_mcp_server.py")

    def test_async_running_at_clear_failure_logs_stderr_and_records_original_error(self):
        """When _run_in_background raises and running_at clear also fails,
        stderr should show the clear failure, and _log should record the original error."""
        import cron_mcp_server
        import threading

        # Save originals
        orig_registry = cron_mcp_server._registry
        orig_executor = cron_mcp_server._executor
        orig_log = cron_mcp_server._log

        try:
            mock_registry = MagicMock()
            mock_executor = MagicMock()
            mock_log = MagicMock()

            cron_mcp_server._registry = mock_registry
            cron_mcp_server._executor = mock_executor
            cron_mcp_server._log = mock_log

            # execute_job raises the "original" error
            original_error = Exception("original job failure")
            mock_executor.execute_job.side_effect = original_error

            # running_at clear also fails
            clear_error = Exception("registry unavailable")
            mock_registry.update.side_effect = [None, clear_error]  # first call sets running_at, second fails

            # Create a fake job
            mock_job = MagicMock()
            mock_job.name = "test-job"
            mock_job.running_at = None
            mock_registry.get.return_value = mock_job

            mock_executor.is_stuck.return_value = False

            stderr_capture = StringIO()
            with patch.object(cron_mcp_server.sys, 'stderr', stderr_capture):
                result = cron_mcp_server.persistent_cron_run(job_id="test-id", async_mode=True)

            # Wait for background thread to finish
            import time
            time.sleep(0.5)

            # Check stderr has the clear failure message
            stderr_output = stderr_capture.getvalue()
            self.assertIn("Failed to clear running_at", stderr_output)
            self.assertIn("registry unavailable", stderr_output)

            # Check _log recorded the ORIGINAL error, not the clear error
            mock_log.append.assert_called_once()
            log_entry = mock_log.append.call_args[0][0]
            self.assertEqual(log_entry.error, "original job failure")
            self.assertEqual(log_entry.status, "error")

        finally:
            cron_mcp_server._registry = orig_registry
            cron_mcp_server._executor = orig_executor
            cron_mcp_server._log = orig_log


# =============================================================================
# 6. discord_daemon.py — PID check warning
# =============================================================================

class TestDiscordDaemonPIDCheckWarning(unittest.TestCase):
    """discord_daemon.py L159: PID check failure should print to stderr."""

    def test_pid_check_exception_prints_stderr(self):
        import discord_daemon

        stderr_capture = StringIO()
        with patch.object(discord_daemon, "read_pid_file", return_value=12345), \
             patch.object(discord_daemon, "is_process_alive", return_value=True), \
             patch.object(discord_daemon, "remove_pid_file"), \
             patch.object(discord_daemon, "sys") as mock_sys:
            mock_sys.platform = "win32"
            mock_sys.stderr = stderr_capture
            with patch("subprocess.run", side_effect=Exception("wmic failed")):
                try:
                    discord_daemon.stop_daemon()
                except Exception:
                    pass

        output = stderr_capture.getvalue()
        self.assertIn("PID check warning", output)
        self.assertIn("wmic failed", output)


# =============================================================================
# 7-10. discord_receiver_gateway.py — 4 箇所
# =============================================================================

class TestGatewaySourcePatterns(unittest.TestCase):
    """Verify all 4 log patterns exist in gateway source."""

    def test_reconnect_callback_warning_appears_twice(self):
        """L199 + L234: two reconnect callback warning sites."""
        from discord_receiver_gateway import DiscordGatewayClient
        import inspect
        source = inspect.getsource(DiscordGatewayClient)
        count = source.count("Reconnect callback warning")
        self.assertGreaterEqual(count, 2)

    def test_heartbeat_send_failed_pattern(self):
        """L359: heartbeat send failure pattern."""
        from discord_receiver_gateway import DiscordGatewayClient
        import inspect
        source = inspect.getsource(DiscordGatewayClient)
        self.assertIn("Heartbeat send failed", source)

    def test_ws_close_warning_pattern(self):
        """L390: ws close warning pattern."""
        from discord_receiver_gateway import DiscordGatewayClient
        import inspect
        source = inspect.getsource(DiscordGatewayClient)
        self.assertIn("WS close warning", source)


class TestGatewayLogIntegration(unittest.TestCase):
    """Integration: verify _log is actually called when exceptions occur."""

    def test_heartbeat_exception_calls_log(self):
        """When ws.send raises during heartbeat, _log('debug', ...) is called."""
        from discord_receiver_gateway import DiscordGatewayClient
        client = DiscordGatewayClient(token=FAKE_TOKEN)
        client.logger = MagicMock()
        client._sequence = 1

        mock_ws = AsyncMock()
        mock_ws.send.side_effect = Exception("connection lost")
        client._ws = mock_ws

        run_async(client._send_heartbeat())

        client.logger.debug.assert_called()
        call_args = str(client.logger.debug.call_args)
        self.assertIn("Heartbeat send failed", call_args)

    def test_ws_close_exception_calls_log(self):
        """When ws.close() raises during close(), _log('debug', ...) is called."""
        from discord_receiver_gateway import DiscordGatewayClient
        client = DiscordGatewayClient(token=FAKE_TOKEN)
        client.logger = MagicMock()
        client._connected = True
        client._heartbeat_task = None

        mock_ws = AsyncMock()
        mock_ws.close.side_effect = Exception("already closed")
        client._ws = mock_ws

        run_async(client.close())

        client.logger.debug.assert_called()
        call_args = str(client.logger.debug.call_args)
        self.assertIn("WS close warning", call_args)


if __name__ == "__main__":
    unittest.main()
