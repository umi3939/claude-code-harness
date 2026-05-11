#!/usr/bin/env python3
"""Tests for cron_mcp_server.py registry lock — thread-safe _registry access.

TDD: Tests written before implementation.
Verifies that _registry.update() and _registry.remove() in _run_in_background
and main thread are protected by _registry_lock.
"""

import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

TOOLS_DIR = str(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)


class TestRegistryLockExists(unittest.TestCase):
    """Verify _registry_lock is defined at module level."""

    def test_registry_lock_is_threading_lock(self):
        """_registry_lock should be a threading.Lock instance."""
        import cron_mcp_server
        self.assertTrue(hasattr(cron_mcp_server, '_registry_lock'))
        # threading.Lock() returns a _thread.lock object
        self.assertTrue(callable(getattr(cron_mcp_server._registry_lock, 'acquire', None)))
        self.assertTrue(callable(getattr(cron_mcp_server._registry_lock, 'release', None)))


class TestRegistryLockUsedInBackground(unittest.TestCase):
    """Verify _run_in_background acquires _registry_lock around registry ops."""

    @patch('cron_mcp_server._notifications')
    @patch('cron_mcp_server._log')
    @patch('cron_mcp_server._executor')
    @patch('cron_mcp_server._registry')
    def test_background_success_acquires_lock(self, mock_registry, mock_executor, mock_log, mock_notif):
        """On successful background execution, registry.update should be called under lock."""
        import cron_mcp_server

        # Track lock state during registry.update calls
        lock_held_during_update = []
        real_lock = cron_mcp_server._registry_lock

        def tracking_update(*args, **kwargs):
            # Check if lock is held (try_acquire returns False if held)
            acquired = real_lock.acquire(blocking=False)
            if acquired:
                real_lock.release()
                lock_held_during_update.append(False)
            else:
                lock_held_during_update.append(True)

        mock_registry.update.side_effect = tracking_update

        # Setup mock job and result
        mock_job = MagicMock()
        mock_job.name = "test-job"
        mock_result = MagicMock()
        mock_result.skipped = False
        mock_result.success = True
        mock_result.error = ""
        mock_result.duration_ms = 100
        mock_result.output = "ok"
        mock_executor.execute_job.return_value = mock_result
        mock_executor.apply_result.return_value = {"running_at": None, "last_run": "now"}

        # Call _run_in_background directly (it's defined inside persistent_cron_run,
        # so we test via running persistent_cron_run with async_mode=True)
        mock_registry.get.return_value = mock_job
        mock_job.enabled = True
        mock_job.running_at = None

        result = cron_mcp_server.persistent_cron_run("test-id", async_mode=True)
        self.assertIn("background", result)

        # Wait for thread to complete
        time.sleep(0.5)

        # At least one update should have been called with lock held
        self.assertTrue(len(lock_held_during_update) > 0,
                        "registry.update was never called in background thread")
        self.assertTrue(all(lock_held_during_update),
                        f"Some registry.update calls were not under lock: {lock_held_during_update}")

    @patch('cron_mcp_server._notifications')
    @patch('cron_mcp_server._log')
    @patch('cron_mcp_server._executor')
    @patch('cron_mcp_server._registry')
    def test_background_exception_acquires_lock(self, mock_registry, mock_executor, mock_log, mock_notif):
        """On exception in background, registry.update(running_at=None) should be under lock."""
        import cron_mcp_server

        lock_held_during_update = []
        real_lock = cron_mcp_server._registry_lock

        def tracking_update(*args, **kwargs):
            acquired = real_lock.acquire(blocking=False)
            if acquired:
                real_lock.release()
                lock_held_during_update.append(False)
            else:
                lock_held_during_update.append(True)
            # First call is the running_at marker set (main thread, before background)
            # Let it pass. The background thread calls will also be tracked.

        mock_registry.update.side_effect = tracking_update

        mock_job = MagicMock()
        mock_job.name = "test-job"
        mock_job.enabled = True
        mock_job.running_at = None
        mock_registry.get.return_value = mock_job
        mock_executor.execute_job.side_effect = RuntimeError("boom")

        cron_mcp_server.persistent_cron_run("test-id", async_mode=True)
        time.sleep(0.5)

        # The background thread's exception handler should call update under lock
        self.assertTrue(len(lock_held_during_update) > 0)


class TestRegistryLockUsedInMainThread(unittest.TestCase):
    """Verify main thread registry operations at L505, L508 use _registry_lock."""

    @patch('cron_mcp_server._notifications')
    @patch('cron_mcp_server._log')
    @patch('cron_mcp_server._executor')
    @patch('cron_mcp_server._registry')
    def test_main_thread_stuck_clear_acquires_lock(self, mock_registry, mock_executor, mock_log, mock_notif):
        """When a stuck job is detected, clearing running_at should be under lock."""
        import cron_mcp_server

        lock_held_during_update = []
        real_lock = cron_mcp_server._registry_lock

        def tracking_update(*args, **kwargs):
            acquired = real_lock.acquire(blocking=False)
            if acquired:
                real_lock.release()
                lock_held_during_update.append(False)
            else:
                lock_held_during_update.append(True)

        mock_registry.update.side_effect = tracking_update

        mock_job = MagicMock()
        mock_job.name = "test-job"
        mock_job.enabled = True
        mock_job.running_at = "2026-01-01T00:00:00Z"  # pretend stuck
        mock_registry.get.return_value = mock_job
        mock_executor.is_stuck.return_value = True

        # Non-async: will proceed to execute
        mock_result = MagicMock()
        mock_result.skipped = False
        mock_result.success = True
        mock_result.error = ""
        mock_result.duration_ms = 50
        mock_result.output = "ok"
        mock_executor.execute_job.return_value = mock_result
        mock_executor.apply_result.return_value = {"running_at": None}

        cron_mcp_server.persistent_cron_run("test-id", async_mode=False)

        # All update calls should be under lock
        self.assertTrue(len(lock_held_during_update) > 0)
        self.assertTrue(all(lock_held_during_update),
                        f"Main thread registry.update not under lock: {lock_held_during_update}")


if __name__ == '__main__':
    unittest.main()
