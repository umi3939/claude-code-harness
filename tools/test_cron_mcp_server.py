"""
Tests for cron_mcp_server.py and cron_daemon.py.

Tests the MCP tool functions directly (not via MCP transport)
and the daemon's core logic.
"""

import json
import os
import shutil
import subprocess

# Setup sys.path
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from cron_scheduler import (
    MAX_EXECUTIONS_PER_HOUR,
    MAX_MISSED_JOBS_RECOVERY,
    CronJob,
    ExecutionLog,
    JobExecutor,
    JobRegistry,
    NotificationBuffer,
)

# ═══════════════════════════════════════════════════════════════
# MCP Server Tool Tests (testing functions directly)
# ═══════════════════════════════════════════════════════════════


class MCPToolTestBase(unittest.TestCase):
    """Base class that patches MCP server module globals with temp dirs."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store_path = os.path.join(self.tmpdir, "jobs.json")
        self.log_path = os.path.join(self.tmpdir, "execution.jsonl")
        self.notif_path = os.path.join(self.tmpdir, "notifications.json")

        # Patch the module-level instances
        import cron_mcp_server as mcp_mod
        self.mcp_mod = mcp_mod
        self._orig_registry = mcp_mod._registry
        self._orig_executor = mcp_mod._executor
        self._orig_log = mcp_mod._log
        self._orig_notifications = mcp_mod._notifications

        mcp_mod._registry = JobRegistry(store_path=self.store_path)
        mcp_mod._executor = JobExecutor(registry=mcp_mod._registry)
        mcp_mod._log = ExecutionLog(log_path=self.log_path)
        mcp_mod._notifications = NotificationBuffer(path=self.notif_path)

    def tearDown(self):
        # Restore originals
        self.mcp_mod._registry = self._orig_registry
        self.mcp_mod._executor = self._orig_executor
        self.mcp_mod._log = self._orig_log
        self.mcp_mod._notifications = self._orig_notifications
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestPersistentCronAdd(MCPToolTestBase):
    """Test persistent_cron_add tool."""

    def test_add_every_job(self):
        from cron_mcp_server import persistent_cron_add
        result = persistent_cron_add(
            name="Test Job",
            prompt="echo hello",
            schedule_type="every",
            schedule_value="300",
        )
        self.assertIn("Job registered successfully", result)
        self.assertIn("Test Job", result)

    def test_add_cron_job(self):
        from cron_mcp_server import persistent_cron_add
        result = persistent_cron_add(
            name="Hourly Job",
            prompt="do stuff",
            schedule_type="cron",
            schedule_value="0 * * * *",
        )
        self.assertIn("Job registered successfully", result)
        self.assertIn("Next run:", result)

    def test_add_at_job(self):
        from cron_mcp_server import persistent_cron_add
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        result = persistent_cron_add(
            name="One-time Job",
            prompt="do once",
            schedule_type="at",
            schedule_value=future,
            one_shot=True,
        )
        self.assertIn("Job registered successfully", result)

    def test_add_with_active_hours(self):
        from cron_mcp_server import persistent_cron_add
        result = persistent_cron_add(
            name="Daytime Job",
            prompt="hello",
            schedule_type="every",
            schedule_value="3600",
            active_hours_start="08:00",
            active_hours_end="23:00",
        )
        self.assertIn("Active hours", result)

    def test_add_with_ttl(self):
        from cron_mcp_server import persistent_cron_add
        ttl = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        result = persistent_cron_add(
            name="Expiring Job",
            prompt="hello",
            schedule_type="every",
            schedule_value="3600",
            ttl=ttl,
        )
        self.assertIn("TTL:", result)

    def test_add_invalid_schedule_type(self):
        from cron_mcp_server import persistent_cron_add
        result = persistent_cron_add(
            name="Bad",
            prompt="hello",
            schedule_type="invalid",
            schedule_value="123",
        )
        self.assertIn("ERROR", result)

    def test_add_invalid_every_value(self):
        from cron_mcp_server import persistent_cron_add
        result = persistent_cron_add(
            name="Bad",
            prompt="hello",
            schedule_type="every",
            schedule_value="not-a-number",
        )
        self.assertIn("ERROR", result)


class TestPersistentCronList(MCPToolTestBase):
    """Test persistent_cron_list tool."""

    def test_list_empty(self):
        from cron_mcp_server import persistent_cron_list
        result = persistent_cron_list()
        self.assertIn("No enabled jobs", result)

    def test_list_with_jobs(self):
        from cron_mcp_server import persistent_cron_add, persistent_cron_list
        persistent_cron_add(
            name="Job A",
            prompt="a",
            schedule_type="every",
            schedule_value="300",
        )
        persistent_cron_add(
            name="Job B",
            prompt="b",
            schedule_type="every",
            schedule_value="600",
        )
        result = persistent_cron_list()
        self.assertIn("Job A", result)
        self.assertIn("Job B", result)
        self.assertIn("2 total", result)

    def test_list_hides_disabled(self):
        from cron_mcp_server import persistent_cron_add, persistent_cron_list, persistent_cron_update
        result_add = persistent_cron_add(
            name="Will Disable",
            prompt="x",
            schedule_type="every",
            schedule_value="300",
        )
        # Extract job ID
        job_id = None
        for line in result_add.split("\n"):
            if "ID:" in line:
                job_id = line.split("ID:")[1].strip()
                break

        persistent_cron_update(job_id=job_id, enabled="false")

        result = persistent_cron_list(include_disabled=False)
        self.assertIn("No enabled jobs", result)

        result2 = persistent_cron_list(include_disabled=True)
        self.assertIn("Will Disable", result2)


class TestPersistentCronGet(MCPToolTestBase):
    """Test persistent_cron_get tool."""

    def test_get_existing(self):
        from cron_mcp_server import persistent_cron_add, persistent_cron_get
        add_result = persistent_cron_add(
            name="Detail Job",
            prompt="hello",
            schedule_type="every",
            schedule_value="60",
        )
        job_id = None
        for line in add_result.split("\n"):
            if "ID:" in line:
                job_id = line.split("ID:")[1].strip()
                break

        result = persistent_cron_get(job_id)
        data = json.loads(result)
        self.assertEqual(data["name"], "Detail Job")
        self.assertIn("status_display", data)

    def test_get_nonexistent(self):
        from cron_mcp_server import persistent_cron_get
        result = persistent_cron_get("nonexistent-id")
        self.assertIn("ERROR", result)


class TestPersistentCronUpdate(MCPToolTestBase):
    """Test persistent_cron_update tool."""

    def _add_job(self):
        from cron_mcp_server import persistent_cron_add
        result = persistent_cron_add(
            name="Update Target",
            prompt="hello",
            schedule_type="every",
            schedule_value="300",
        )
        for line in result.split("\n"):
            if "ID:" in line:
                return line.split("ID:")[1].strip()
        return None

    def test_update_name(self):
        from cron_mcp_server import persistent_cron_update
        job_id = self._add_job()
        result = persistent_cron_update(job_id=job_id, name="New Name")
        self.assertIn("Job updated", result)
        self.assertIn("New Name", result)

    def test_update_enable_disable(self):
        from cron_mcp_server import persistent_cron_get, persistent_cron_update
        job_id = self._add_job()
        persistent_cron_update(job_id=job_id, enabled="false")
        data = json.loads(persistent_cron_get(job_id))
        self.assertFalse(data["enabled"])

        persistent_cron_update(job_id=job_id, enabled="true")
        data = json.loads(persistent_cron_get(job_id))
        self.assertTrue(data["enabled"])

    def test_update_nonexistent(self):
        from cron_mcp_server import persistent_cron_update
        result = persistent_cron_update(job_id="fake", name="x")
        self.assertIn("ERROR", result)

    def test_update_no_changes(self):
        from cron_mcp_server import persistent_cron_update
        job_id = self._add_job()
        result = persistent_cron_update(job_id=job_id)
        self.assertIn("No updates", result)

    def test_update_invalid_enabled(self):
        from cron_mcp_server import persistent_cron_update
        job_id = self._add_job()
        result = persistent_cron_update(job_id=job_id, enabled="maybe")
        self.assertIn("ERROR", result)

    def test_update_schedule(self):
        from cron_mcp_server import persistent_cron_update
        job_id = self._add_job()
        result = persistent_cron_update(
            job_id=job_id,
            schedule_type="every",
            schedule_value="600",
        )
        self.assertIn("Job updated", result)

    def test_update_ttl_clear(self):
        from cron_mcp_server import persistent_cron_get, persistent_cron_update
        job_id = self._add_job()
        ttl = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        persistent_cron_update(job_id=job_id, ttl=ttl)
        data = json.loads(persistent_cron_get(job_id))
        self.assertIsNotNone(data.get("ttl"))

        persistent_cron_update(job_id=job_id, ttl="none")
        data = json.loads(persistent_cron_get(job_id))
        self.assertNotIn("ttl", data)  # None values are excluded by to_dict


class TestPersistentCronRun(MCPToolTestBase):
    """Test persistent_cron_run (manual execution) tool."""

    def _add_job(self):
        from cron_mcp_server import persistent_cron_add
        result = persistent_cron_add(
            name="Run Target",
            prompt="echo hello",
            schedule_type="every",
            schedule_value="300",
        )
        for line in result.split("\n"):
            if "ID:" in line:
                return line.split("ID:")[1].strip()
        return None

    @patch("cron_scheduler.subprocess.run")
    def test_run_success(self, mock_run):
        from cron_mcp_server import persistent_cron_run
        mock_run.return_value = MagicMock(returncode=0, stdout="output", stderr="")
        job_id = self._add_job()
        result = persistent_cron_run(job_id)
        self.assertIn("success", result)
        self.assertIn("output", result)

    @patch("cron_scheduler.subprocess.run")
    def test_run_failure(self, mock_run):
        from cron_mcp_server import persistent_cron_run
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="failed")
        job_id = self._add_job()
        result = persistent_cron_run(job_id)
        self.assertIn("error", result)

    def test_run_nonexistent(self):
        from cron_mcp_server import persistent_cron_run
        result = persistent_cron_run("fake")
        self.assertIn("ERROR", result)

    def test_run_disabled_job(self):
        from cron_mcp_server import persistent_cron_run, persistent_cron_update
        job_id = self._add_job()
        persistent_cron_update(job_id=job_id, enabled="false")
        result = persistent_cron_run(job_id)
        self.assertIn("ERROR", result)
        self.assertIn("disabled", result)

    @patch("cron_scheduler.subprocess.run")
    def test_run_async_returns_immediately(self, mock_run):
        """Async run should return immediately with 'started' message."""
        from cron_mcp_server import persistent_cron_run
        # Make subprocess.run take a long time (simulated)
        def slow_run(*args, **kwargs):
            time.sleep(0.5)
            return MagicMock(returncode=0, stdout="done", stderr="")
        mock_run.side_effect = slow_run
        job_id = self._add_job()

        start = time.monotonic()
        result = persistent_cron_run(job_id, async_mode=True)
        elapsed = time.monotonic() - start

        # Should return in < 0.2s (not wait for the 0.5s execution)
        self.assertLess(elapsed, 0.3)
        self.assertIn("started", result.lower())

    @patch("cron_scheduler.subprocess.run")
    def test_run_async_updates_log_after_completion(self, mock_run):
        """Async run should eventually update execution log."""
        from cron_mcp_server import persistent_cron_logs, persistent_cron_run
        mock_run.return_value = MagicMock(returncode=0, stdout="async-output", stderr="")
        job_id = self._add_job()

        persistent_cron_run(job_id, async_mode=True)
        # Wait for background thread to finish
        time.sleep(1.0)

        logs = persistent_cron_logs()
        # Log format uses [ok] for success
        self.assertIn("[ok]", logs)
        self.assertIn("async-output", logs)

    @patch("cron_scheduler.subprocess.run")
    def test_run_async_sets_running_marker(self, mock_run):
        """Async run should set running_at marker immediately."""
        from cron_mcp_server import persistent_cron_get, persistent_cron_run
        def slow_run(*args, **kwargs):
            time.sleep(0.5)
            return MagicMock(returncode=0, stdout="done", stderr="")
        mock_run.side_effect = slow_run
        job_id = self._add_job()

        persistent_cron_run(job_id, async_mode=True)
        # Check immediately — should have running_at set
        persistent_cron_get(job_id)
        # running_at should be set (non-null in output)
        # After thread finishes, it will be cleared
        time.sleep(1.0)

    @patch("cron_scheduler.subprocess.run")
    def test_run_sync_still_works(self, mock_run):
        """Default (sync) run should still work as before."""
        from cron_mcp_server import persistent_cron_run
        mock_run.return_value = MagicMock(returncode=0, stdout="sync-output", stderr="")
        job_id = self._add_job()
        result = persistent_cron_run(job_id)
        self.assertIn("success", result)
        self.assertIn("sync-output", result)

    @patch("cron_scheduler.subprocess.run")
    def test_run_sync_error_does_not_increment_consecutive_errors(self, mock_run):
        """Sync (MCP) run errors should NOT affect consecutive_errors."""
        from cron_mcp_server import persistent_cron_get, persistent_cron_run
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=15)
        job_id = self._add_job()

        # Run and expect error
        result = persistent_cron_run(job_id)
        self.assertIn("error", result.lower())

        # consecutive_errors should still be 0
        job_info = persistent_cron_get(job_id)
        self.assertIn('"consecutive_errors": 0', job_info)


class TestPersistentCronLogs(MCPToolTestBase):
    """Test persistent_cron_logs tool."""

    def test_logs_empty(self):
        from cron_mcp_server import persistent_cron_logs
        result = persistent_cron_logs()
        self.assertIn("No execution logs", result)

    @patch("cron_scheduler.subprocess.run")
    def test_logs_after_run(self, mock_run):
        from cron_mcp_server import persistent_cron_add, persistent_cron_logs, persistent_cron_run
        mock_run.return_value = MagicMock(returncode=0, stdout="done", stderr="")

        add_result = persistent_cron_add(
            name="Log Test",
            prompt="hello",
            schedule_type="every",
            schedule_value="300",
        )
        job_id = None
        for line in add_result.split("\n"):
            if "ID:" in line:
                job_id = line.split("ID:")[1].strip()
                break

        persistent_cron_run(job_id)
        result = persistent_cron_logs()
        self.assertIn("Log Test", result)
        self.assertIn("1 entries", result)


class TestPersistentCronStatus(MCPToolTestBase):
    """Test persistent_cron_status tool."""

    def test_status_no_daemon(self):
        from cron_mcp_server import persistent_cron_status
        with patch("cron_mcp_server._check_daemon_running", return_value={"running": False, "pid": None, "reason": "no pid file"}):
            result = persistent_cron_status()
        self.assertIn("NOT RUNNING", result)
        self.assertIn("Jobs:", result)

    def test_status_with_jobs(self):
        from cron_mcp_server import persistent_cron_add, persistent_cron_status
        persistent_cron_add(
            name="Status Test",
            prompt="hello",
            schedule_type="every",
            schedule_value="300",
        )
        result = persistent_cron_status()
        self.assertIn("1 total", result)
        self.assertIn("1 enabled", result)


class TestPersistentCronNotifications(MCPToolTestBase):
    """Test persistent_cron_notifications tool."""

    def test_no_notifications(self):
        from cron_mcp_server import persistent_cron_notifications
        result = persistent_cron_notifications()
        self.assertIn("No pending notifications", result)

    @patch("cron_scheduler.subprocess.run")
    def test_notifications_after_error(self, mock_run):
        from cron_mcp_server import (
            persistent_cron_add,
            persistent_cron_notifications,
            persistent_cron_run,
        )
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")

        add_result = persistent_cron_add(
            name="Notif Test",
            prompt="fail",
            schedule_type="every",
            schedule_value="300",
        )
        job_id = None
        for line in add_result.split("\n"):
            if "ID:" in line:
                job_id = line.split("ID:")[1].strip()
                break

        persistent_cron_run(job_id)
        result = persistent_cron_notifications()
        self.assertIn("Notif Test", result)
        self.assertIn("consumed", result)

        # Second call should show no pending
        result2 = persistent_cron_notifications()
        self.assertIn("No pending", result2)


# ═══════════════════════════════════════════════════════════════
# Daemon Tests
# ═══════════════════════════════════════════════════════════════


class TestCronDaemon(unittest.TestCase):
    """Test CronDaemon core logic."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store_path = os.path.join(self.tmpdir, "jobs.json")
        self.log_path = os.path.join(self.tmpdir, "execution.jsonl")
        self.notif_path = os.path.join(self.tmpdir, "notifications.json")

        import logging
        self.logger = logging.getLogger("test_daemon")
        self.logger.setLevel(logging.DEBUG)
        self.logger.handlers = [logging.NullHandler()]

        from cron_daemon import CronDaemon
        self.daemon = CronDaemon(self.logger)
        self.daemon.registry = JobRegistry(store_path=self.store_path)
        self.daemon.executor = JobExecutor(registry=self.daemon.registry)
        self.daemon.log = ExecutionLog(log_path=self.log_path)
        self.daemon.notifications = NotificationBuffer(path=self.notif_path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _add_with_next_run(self, name, next_run, **kwargs):
        """Add a job and force a specific next_run via update."""
        job = self.daemon.registry.add(CronJob(
            name=name,
            prompt=kwargs.get("prompt", "hello"),
            schedule=kwargs.get("schedule", {"type": "every", "interval_seconds": 60}),
            **{k: v for k, v in kwargs.items() if k not in ("prompt", "schedule")},
        ))
        self.daemon.registry.update(job.id, {"next_run": next_run})
        return job

    def test_startup_recovery_clears_markers(self):
        """Startup should clear stale running_at markers."""
        old_time = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        self.daemon.registry.add(CronJob(
            name="Stuck Job",
            prompt="hello",
            running_at=old_time,
            schedule={"type": "every", "interval_seconds": 60},
        ))
        self.daemon.startup_recovery()
        jobs = self.daemon.registry.list_all()
        self.assertIsNone(jobs[0].running_at)

    @patch("cron_scheduler.subprocess.run")
    def test_startup_recovery_runs_missed(self, mock_run):
        """Startup should run missed jobs up to limit."""
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        for i in range(3):
            self._add_with_next_run(f"Missed {i}", past)
        self.daemon.startup_recovery()
        # All 3 should have been executed (under MAX_MISSED_JOBS_RECOVERY=5)
        self.assertEqual(mock_run.call_count, 3)

    @patch("cron_scheduler.subprocess.run")
    def test_startup_recovery_limits_missed(self, mock_run):
        """Startup should limit missed job recovery."""
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        for i in range(MAX_MISSED_JOBS_RECOVERY + 3):
            self._add_with_next_run(f"Missed {i}", past)
        self.daemon.startup_recovery()
        self.assertEqual(mock_run.call_count, MAX_MISSED_JOBS_RECOVERY)

    @patch("cron_scheduler.subprocess.run")
    def test_tick_executes_due_jobs(self, mock_run):
        """Tick should execute jobs that are due."""
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

        past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        self._add_with_next_run("Due Job", past)
        self.daemon.tick()
        self.assertEqual(mock_run.call_count, 1)

    def test_tick_skips_future_jobs(self):
        """Tick should not execute jobs that are not yet due."""
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        self.daemon.registry.add(CronJob(
            name="Future Job",
            prompt="hello",
            schedule={"type": "every", "interval_seconds": 60},
            next_run=future,
        ))
        self.daemon.tick()
        # No execution should happen
        entries = self.daemon.log.get_recent()
        self.assertEqual(len(entries), 0)

    def test_rate_limit(self):
        """Rate limiter should block excessive executions."""
        # Fill up the rate limit
        import time as time_mod
        now = time_mod.time()
        self.daemon._execution_timestamps = [now - i for i in range(MAX_EXECUTIONS_PER_HOUR)]
        self.assertFalse(self.daemon._check_rate_limit())

    def test_rate_limit_allows_normal(self):
        """Rate limiter should allow normal execution rate."""
        self.daemon._execution_timestamps = []
        self.assertTrue(self.daemon._check_rate_limit())

    @patch("cron_scheduler.subprocess.run")
    def test_tick_handles_ttl_expired(self, mock_run):
        """Tick should handle TTL-expired jobs."""
        past_ttl = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        past_next = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        self._add_with_next_run("Expired Job", past_next, ttl=past_ttl)
        self.daemon.tick()
        # Should not execute (TTL expired = skipped)
        mock_run.assert_not_called()
        # Job should be disabled
        jobs = self.daemon.registry.list_all()
        self.assertFalse(jobs[0].enabled)

    @patch("cron_scheduler.subprocess.run")
    def test_tick_records_notification_on_error(self, mock_run):
        """Tick should create notification on job error."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="bad")

        past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        self._add_with_next_run("Error Job", past, prompt="fail")
        self.daemon.tick()

        pending = self.daemon.notifications.get_pending()
        self.assertGreaterEqual(len(pending), 1)
        self.assertEqual(pending[0].status, "error")

    def test_tick_computes_missing_next_run(self):
        """Tick should compute next_run for jobs without one."""
        self.daemon.registry.add(CronJob(
            name="No Next Run",
            prompt="hello",
            schedule={"type": "every", "interval_seconds": 300},
            next_run=None,
        ))
        self.daemon.tick()
        jobs = self.daemon.registry.list_all()
        self.assertIsNotNone(jobs[0].next_run)


class TestDaemonPidFile(unittest.TestCase):
    """Test PID file management."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Patch PID_FILE
        import cron_daemon
        self._orig_pid = cron_daemon.PID_FILE
        cron_daemon.PID_FILE = os.path.join(self.tmpdir, "daemon.pid")

    def tearDown(self):
        import cron_daemon
        cron_daemon.PID_FILE = self._orig_pid
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_write_read_pid(self):
        from cron_daemon import read_pid_file, write_pid_file
        write_pid_file()
        pid = read_pid_file()
        self.assertEqual(pid, os.getpid())

    def test_remove_pid(self):
        from cron_daemon import read_pid_file, remove_pid_file, write_pid_file
        write_pid_file()
        remove_pid_file()
        self.assertIsNone(read_pid_file())

    def test_read_nonexistent(self):
        from cron_daemon import read_pid_file
        self.assertIsNone(read_pid_file())

    def test_is_process_alive_current(self):
        from cron_daemon import is_process_alive
        self.assertTrue(is_process_alive(os.getpid()))

    def test_is_process_alive_invalid(self):
        from cron_daemon import is_process_alive
        # Use a very high PID that almost certainly doesn't exist
        self.assertFalse(is_process_alive(99999999))


class TestDaemonCheckStatus(MCPToolTestBase):
    """Test daemon status checking from MCP server."""

    def test_check_daemon_not_running(self):
        from cron_mcp_server import _check_daemon_running
        with patch("cron_mcp_server.PID_FILE", os.path.join(self.tmpdir, "nonexistent.pid")):
            result = _check_daemon_running()
        self.assertFalse(result["running"])


class TestFormatJob(MCPToolTestBase):
    """Test job formatting for display."""

    def test_format_enabled_scheduled(self):
        from cron_mcp_server import _format_job
        job = CronJob(
            id="test",
            name="Test",
            enabled=True,
            next_run="2026-03-15T12:00:00+00:00",
        )
        d = _format_job(job)
        self.assertIn("scheduled", d["status_display"])

    def test_format_disabled(self):
        from cron_mcp_server import _format_job
        job = CronJob(id="test", name="Test", enabled=False)
        d = _format_job(job)
        self.assertEqual(d["status_display"], "disabled")

    def test_format_running(self):
        from cron_mcp_server import _format_job
        job = CronJob(
            id="test", name="Test", enabled=True,
            running_at="2026-03-14T12:00:00+00:00",
        )
        d = _format_job(job)
        self.assertEqual(d["status_display"], "running")

    def test_format_no_schedule(self):
        from cron_mcp_server import _format_job
        job = CronJob(id="test", name="Test", enabled=True)
        d = _format_job(job)
        self.assertEqual(d["status_display"], "no schedule")


if __name__ == "__main__":
    unittest.main()
