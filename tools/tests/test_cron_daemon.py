#!/usr/bin/env python3
"""Tests for cron_daemon.py — CronDaemon class and helper functions.

Covers P1 (record_heartbeat_action, prune_heartbeat_actions, _check_rate_limit),
P2 (PID file operations, startup_recovery, tick), and
P3 (_execute_single_job, is_process_alive, stop_daemon).
"""

import json
import logging
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

TOOLS_DIR = str(Path(__file__).resolve().parent.parent)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import cron_daemon
from cron_daemon import (
    HOURLY_WINDOW_SECONDS,
    CronDaemon,
    is_process_alive,
    prune_heartbeat_actions,
    read_pid_file,
    record_heartbeat_action,
    remove_pid_file,
    setup_logging,
    stop_daemon,
    write_pid_file,
)
from cron_scheduler import (
    MAX_CONCURRENT_JOBS,
    MAX_EXECUTIONS_PER_HOUR,
    MAX_MISSED_JOBS_RECOVERY,
    CronJob,
    ExecutionResult,
)


def _make_logger():
    """Create a silent logger for testing."""
    logger = logging.getLogger(f"test_cron_daemon_{id(object())}")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    return logger


def _make_daemon(logger=None):
    """Create a CronDaemon with mocked dependencies."""
    if logger is None:
        logger = _make_logger()
    daemon = CronDaemon.__new__(CronDaemon)
    daemon.logger = logger
    daemon.registry = MagicMock()
    daemon.executor = MagicMock()
    daemon.log = MagicMock()
    daemon.notifications = MagicMock()
    daemon._running = False
    daemon._execution_timestamps = []
    daemon._concurrent_jobs = 0
    return daemon


# ═══════════════════════════════════════════════════════════════
# P1: record_heartbeat_action
# ═══════════════════════════════════════════════════════════════

class TestRecordHeartbeatAction(unittest.TestCase):
    """P1: record_heartbeat_action writes JSONL entries correctly."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_cron_daemon_")
        self.actions_file = os.path.join(self.tmpdir, "actions.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_writes_success_entry(self):
        result = ExecutionResult(success=True, output="ok", duration_ms=1234)
        record_heartbeat_action(self.actions_file, result)
        with open(self.actions_file, "r", encoding="utf-8") as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry["result"], "success")
        self.assertEqual(entry["duration_ms"], 1234)
        self.assertEqual(entry["concern"], "heartbeat")
        self.assertEqual(entry["action_taken"], "full_run")
        self.assertEqual(entry["output_preview"], "ok")

    def test_writes_error_entry(self):
        result = ExecutionResult(success=False, error="boom", duration_ms=999)
        record_heartbeat_action(self.actions_file, result)
        with open(self.actions_file, "r", encoding="utf-8") as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry["result"], "error")

    def test_truncates_output_to_200(self):
        result = ExecutionResult(success=True, output="A" * 500, duration_ms=0)
        record_heartbeat_action(self.actions_file, result)
        with open(self.actions_file, "r", encoding="utf-8") as f:
            entry = json.loads(f.readline())
        self.assertEqual(len(entry["output_preview"]), 500)

    def test_none_output_becomes_empty(self):
        result = ExecutionResult(success=True, output=None, duration_ms=0)
        # output defaults to "" in ExecutionResult, but test the (or "") guard
        result.output = None
        record_heartbeat_action(self.actions_file, result)
        with open(self.actions_file, "r", encoding="utf-8") as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry["output_preview"], "")

    def test_appends_multiple_entries(self):
        for i in range(5):
            result = ExecutionResult(success=True, output=str(i), duration_ms=i)
            record_heartbeat_action(self.actions_file, result)
        with open(self.actions_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 5)

    def test_creates_parent_dirs(self):
        nested = os.path.join(self.tmpdir, "a", "b", "c", "actions.jsonl")
        result = ExecutionResult(success=True, output="ok", duration_ms=0)
        record_heartbeat_action(nested, result)
        self.assertTrue(os.path.exists(nested))

    def test_calls_prune_after_write(self):
        """record_heartbeat_action calls prune_heartbeat_actions after writing."""
        with patch("cron_daemon.prune_heartbeat_actions") as mock_prune:
            result = ExecutionResult(success=True, output="ok", duration_ms=0)
            record_heartbeat_action(self.actions_file, result)
            mock_prune.assert_called_once_with(self.actions_file)


# ═══════════════════════════════════════════════════════════════
# P1: prune_heartbeat_actions
# ═══════════════════════════════════════════════════════════════

class TestPruneHeartbeatActions(unittest.TestCase):
    """P1: prune_heartbeat_actions keeps only the latest max_lines."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_prune_")
        self.actions_file = os.path.join(self.tmpdir, "actions.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_lines(self, n):
        with open(self.actions_file, "w", encoding="utf-8") as f:
            for i in range(n):
                f.write(json.dumps({"i": i}) + "\n")

    def test_no_prune_under_limit(self):
        self._write_lines(5)
        prune_heartbeat_actions(self.actions_file, max_lines=10)
        with open(self.actions_file, "r") as f:
            self.assertEqual(len(f.readlines()), 5)

    def test_no_prune_at_limit(self):
        self._write_lines(10)
        prune_heartbeat_actions(self.actions_file, max_lines=10)
        with open(self.actions_file, "r") as f:
            self.assertEqual(len(f.readlines()), 10)

    def test_prune_over_limit_keeps_latest(self):
        self._write_lines(15)
        prune_heartbeat_actions(self.actions_file, max_lines=10)
        with open(self.actions_file, "r") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 10)
        self.assertEqual(json.loads(lines[0])["i"], 5)
        self.assertEqual(json.loads(lines[-1])["i"], 14)

    def test_nonexistent_file_no_error(self):
        prune_heartbeat_actions(os.path.join(self.tmpdir, "nope.jsonl"), max_lines=10)

    def test_empty_file(self):
        with open(self.actions_file, "w") as f:
            pass
        prune_heartbeat_actions(self.actions_file, max_lines=10)
        with open(self.actions_file, "r") as f:
            self.assertEqual(len(f.readlines()), 0)


# ═══════════════════════════════════════════════════════════════
# P1: _check_rate_limit and _record_execution
# ═══════════════════════════════════════════════════════════════

class TestCheckRateLimit(unittest.TestCase):
    """P1: _check_rate_limit uses a sliding window on _execution_timestamps."""

    def test_empty_timestamps_allows(self):
        daemon = _make_daemon()
        self.assertTrue(daemon._check_rate_limit())

    def test_under_limit_allows(self):
        daemon = _make_daemon()
        now = time.time()
        daemon._execution_timestamps = [now - 10 * i for i in range(MAX_EXECUTIONS_PER_HOUR - 1)]
        self.assertTrue(daemon._check_rate_limit())

    def test_at_limit_rejects(self):
        daemon = _make_daemon()
        now = time.time()
        daemon._execution_timestamps = [now - 10 * i for i in range(MAX_EXECUTIONS_PER_HOUR)]
        self.assertFalse(daemon._check_rate_limit())

    def test_old_timestamps_evicted(self):
        daemon = _make_daemon()
        old = time.time() - HOURLY_WINDOW_SECONDS - 100
        daemon._execution_timestamps = [old] * (MAX_EXECUTIONS_PER_HOUR + 5)
        self.assertTrue(daemon._check_rate_limit())
        # Old timestamps should have been evicted
        self.assertEqual(len(daemon._execution_timestamps), 0)

    def test_mixed_old_and_new(self):
        daemon = _make_daemon()
        now = time.time()
        old = now - HOURLY_WINDOW_SECONDS - 100
        # Fill with old timestamps + a few new ones
        daemon._execution_timestamps = [old] * 50 + [now - 5] * 3
        self.assertTrue(daemon._check_rate_limit())
        self.assertEqual(len(daemon._execution_timestamps), 3)


class TestRecordExecution(unittest.TestCase):
    """P1: _record_execution appends a timestamp."""

    def test_appends_timestamp(self):
        daemon = _make_daemon()
        self.assertEqual(len(daemon._execution_timestamps), 0)
        daemon._record_execution()
        self.assertEqual(len(daemon._execution_timestamps), 1)
        self.assertAlmostEqual(daemon._execution_timestamps[0], time.time(), delta=1)

    def test_multiple_records(self):
        daemon = _make_daemon()
        daemon._record_execution()
        daemon._record_execution()
        daemon._record_execution()
        self.assertEqual(len(daemon._execution_timestamps), 3)


# ═══════════════════════════════════════════════════════════════
# P2: PID file operations
# ═══════════════════════════════════════════════════════════════

class TestPidFileOperations(unittest.TestCase):
    """P2: write_pid_file, read_pid_file, remove_pid_file."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_pid_")
        self.orig_pid_file = cron_daemon.PID_FILE
        self.orig_cron_dir = cron_daemon.CRON_DIR
        cron_daemon.PID_FILE = os.path.join(self.tmpdir, "daemon.pid")
        cron_daemon.CRON_DIR = self.tmpdir

    def tearDown(self):
        cron_daemon.PID_FILE = self.orig_pid_file
        cron_daemon.CRON_DIR = self.orig_cron_dir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_write_and_read(self):
        write_pid_file()
        pid = read_pid_file()
        self.assertEqual(pid, os.getpid())

    def test_read_nonexistent_returns_none(self):
        self.assertIsNone(read_pid_file())

    def test_read_invalid_content_returns_none(self):
        with open(cron_daemon.PID_FILE, "w") as f:
            f.write("not_a_number")
        self.assertIsNone(read_pid_file())

    def test_remove_existing(self):
        write_pid_file()
        self.assertTrue(os.path.exists(cron_daemon.PID_FILE))
        remove_pid_file()
        self.assertFalse(os.path.exists(cron_daemon.PID_FILE))

    def test_remove_nonexistent_no_error(self):
        remove_pid_file()  # Should not raise

    def test_write_creates_directory(self):
        nested_dir = os.path.join(self.tmpdir, "sub", "dir")
        cron_daemon.CRON_DIR = nested_dir
        cron_daemon.PID_FILE = os.path.join(nested_dir, "daemon.pid")
        write_pid_file()
        self.assertTrue(os.path.exists(cron_daemon.PID_FILE))


# ═══════════════════════════════════════════════════════════════
# P2: startup_recovery
# ═══════════════════════════════════════════════════════════════

class TestStartupRecovery(unittest.TestCase):
    """P2: startup_recovery clears stale markers and runs missed jobs."""

    def test_clears_stale_running_markers(self):
        daemon = _make_daemon()
        job1 = CronJob(id="j1", name="job1", running_at="2026-01-01T00:00:00Z")
        job2 = CronJob(id="j2", name="job2", running_at=None)
        job3 = CronJob(id="j3", name="job3", running_at="2026-01-01T01:00:00Z")
        daemon.registry.list_all.return_value = [job1, job2, job3]
        daemon.registry.list_enabled.return_value = []

        daemon.startup_recovery()

        # Should clear running_at for job1 and job3
        calls = daemon.registry.update.call_args_list
        cleared = [c for c in calls if c[0][1] == {"running_at": None}]
        self.assertEqual(len(cleared), 2)

    def test_detects_and_runs_missed_jobs(self):
        daemon = _make_daemon()
        daemon.registry.list_all.return_value = []

        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        missed_job = CronJob(id="m1", name="missed", enabled=True, next_run=past)
        future_job = CronJob(id="f1", name="future", enabled=True, next_run=future)
        daemon.registry.list_enabled.return_value = [missed_job, future_job]

        with patch.object(daemon, "_execute_single_job") as mock_exec:
            daemon.startup_recovery()
            mock_exec.assert_called_once_with(missed_job)

    def test_respects_max_missed_jobs_limit(self):
        daemon = _make_daemon()
        daemon.registry.list_all.return_value = []

        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        missed_jobs = [
            CronJob(id=f"m{i}", name=f"missed_{i}", enabled=True, next_run=past)
            for i in range(MAX_MISSED_JOBS_RECOVERY + 5)
        ]
        daemon.registry.list_enabled.return_value = missed_jobs

        with patch.object(daemon, "_execute_single_job") as mock_exec:
            daemon.startup_recovery()
            self.assertEqual(mock_exec.call_count, MAX_MISSED_JOBS_RECOVERY)

    def test_reschedules_excess_missed_jobs(self):
        daemon = _make_daemon()
        daemon.registry.list_all.return_value = []

        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        missed_jobs = [
            CronJob(
                id=f"m{i}", name=f"missed_{i}", enabled=True,
                next_run=past, schedule={"interval_minutes": 60}
            )
            for i in range(MAX_MISSED_JOBS_RECOVERY + 3)
        ]
        daemon.registry.list_enabled.return_value = missed_jobs

        with patch.object(daemon, "_execute_single_job"), \
             patch("cron_daemon.compute_next_run", return_value="2099-01-01T00:00:00Z"):
            daemon.startup_recovery()

        # The excess jobs (3) should be rescheduled via registry.update
        update_calls = daemon.registry.update.call_args_list
        reschedule_calls = [
            c for c in update_calls if "next_run" in c[0][1]
        ]
        self.assertEqual(len(reschedule_calls), 3)

    def test_skips_jobs_without_next_run(self):
        daemon = _make_daemon()
        daemon.registry.list_all.return_value = []

        no_next = CronJob(id="n1", name="no_next", enabled=True, next_run=None)
        daemon.registry.list_enabled.return_value = [no_next]

        with patch.object(daemon, "_execute_single_job") as mock_exec:
            daemon.startup_recovery()
            mock_exec.assert_not_called()


# ═══════════════════════════════════════════════════════════════
# P2: tick
# ═══════════════════════════════════════════════════════════════

class TestTick(unittest.TestCase):
    """P2: tick checks enabled jobs and executes due ones."""

    def test_executes_due_job(self):
        daemon = _make_daemon()
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        job = CronJob(id="j1", name="due_job", enabled=True, next_run=past)
        daemon.registry.list_enabled.return_value = [job]

        with patch.object(daemon, "_execute_single_job") as mock_exec, \
             patch("cron_daemon.write_tick_timestamp"):
            daemon.tick()
            mock_exec.assert_called_once_with(job)

    def test_skips_future_job(self):
        daemon = _make_daemon()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        job = CronJob(id="j1", name="future_job", enabled=True, next_run=future)
        daemon.registry.list_enabled.return_value = [job]

        with patch.object(daemon, "_execute_single_job") as mock_exec, \
             patch("cron_daemon.write_tick_timestamp"):
            daemon.tick()
            mock_exec.assert_not_called()

    def test_computes_next_run_when_missing(self):
        daemon = _make_daemon()
        job = CronJob(
            id="j1", name="no_next", enabled=True,
            next_run=None, schedule={"interval_minutes": 30}
        )
        daemon.registry.list_enabled.return_value = [job]

        with patch.object(daemon, "_execute_single_job") as mock_exec, \
             patch("cron_daemon.compute_next_run", return_value="2099-01-01T00:00:00Z") as mock_cnr, \
             patch("cron_daemon.write_tick_timestamp"):
            daemon.tick()
            mock_exec.assert_not_called()
            mock_cnr.assert_called_once()
            daemon.registry.update.assert_called_once_with("j1", {"next_run": "2099-01-01T00:00:00Z"})

    def test_recomputes_invalid_next_run(self):
        daemon = _make_daemon()
        job = CronJob(id="j1", name="bad_next", enabled=True, next_run="not-a-date")
        daemon.registry.list_enabled.return_value = [job]

        with patch.object(daemon, "_execute_single_job") as mock_exec, \
             patch("cron_daemon.compute_next_run", return_value="2099-01-01T00:00:00Z"), \
             patch("cron_daemon.write_tick_timestamp"):
            daemon.tick()
            mock_exec.assert_not_called()
            daemon.registry.update.assert_called()

    def test_handles_registry_error_gracefully(self):
        daemon = _make_daemon()
        daemon.registry.list_enabled.side_effect = RuntimeError("DB error")

        with patch("cron_daemon.write_tick_timestamp"):
            daemon.tick()  # Should not raise

    def test_writes_tick_timestamp(self):
        daemon = _make_daemon()
        daemon.registry.list_enabled.return_value = []

        with patch("cron_daemon.write_tick_timestamp") as mock_wtt:
            daemon.tick()
            mock_wtt.assert_called_once()


# ═══════════════════════════════════════════════════════════════
# P2/P3: _execute_single_job
# ═══════════════════════════════════════════════════════════════

class TestExecuteSingleJob(unittest.TestCase):
    """P2/P3: _execute_single_job handles skip, rate limit, concurrency, success, error."""

    def _make_job(self, **kwargs):
        defaults = dict(id="j1", name="test_job", enabled=True, type="standard")
        defaults.update(kwargs)
        return CronJob(**defaults)

    def test_skip_reason_returns_early(self):
        daemon = _make_daemon()
        job = self._make_job()
        daemon.executor.should_skip.return_value = "not_in_active_hours"

        daemon._execute_single_job(job)

        daemon.executor.execute_job.assert_not_called()

    def test_ttl_expired_disables_job(self):
        daemon = _make_daemon()
        job = self._make_job()
        daemon.executor.should_skip.return_value = "ttl_expired"

        daemon._execute_single_job(job)

        daemon.registry.update.assert_called_once_with("j1", {"enabled": False})
        daemon.notifications.add_notification.assert_called_once()

    def test_rate_limit_rejects(self):
        daemon = _make_daemon()
        job = self._make_job()
        daemon.executor.should_skip.return_value = None
        daemon._execution_timestamps = [time.time()] * (MAX_EXECUTIONS_PER_HOUR + 1)

        daemon._execute_single_job(job)

        daemon.executor.execute_job.assert_not_called()

    def test_concurrency_limit_rejects(self):
        daemon = _make_daemon()
        job = self._make_job()
        daemon.executor.should_skip.return_value = None
        daemon._concurrent_jobs = MAX_CONCURRENT_JOBS

        daemon._execute_single_job(job)

        daemon.executor.execute_job.assert_not_called()

    def test_successful_execution(self):
        daemon = _make_daemon()
        job = self._make_job()
        daemon.executor.should_skip.return_value = None
        result = ExecutionResult(success=True, output="done", duration_ms=100)
        daemon.executor.execute_job.return_value = result
        daemon.executor.apply_result.return_value = {
            "last_run": "now", "running_at": None,
            "last_result": "success", "consecutive_errors": 0, "next_run": None
        }

        daemon._execute_single_job(job)

        daemon.executor.execute_job.assert_called_once_with(job)
        daemon.log.append.assert_called_once()
        log_entry = daemon.log.append.call_args[0][0]
        self.assertEqual(log_entry.status, "success")

    def test_failed_execution_notifies(self):
        daemon = _make_daemon()
        job = self._make_job()
        daemon.executor.should_skip.return_value = None
        result = ExecutionResult(success=False, error="timeout", duration_ms=60000)
        daemon.executor.execute_job.return_value = result
        daemon.executor.apply_result.return_value = {
            "last_run": "now", "running_at": None,
            "last_result": "error", "consecutive_errors": 3, "enabled": True, "next_run": None
        }

        daemon._execute_single_job(job)

        daemon.notifications.add_notification.assert_called()

    def test_auto_disabled_on_consecutive_errors(self):
        daemon = _make_daemon()
        job = self._make_job()
        daemon.executor.should_skip.return_value = None
        result = ExecutionResult(success=False, error="crash", duration_ms=100)
        daemon.executor.execute_job.return_value = result
        daemon.executor.apply_result.return_value = {
            "last_run": "now", "running_at": None,
            "last_result": "error", "consecutive_errors": 5, "enabled": False, "next_run": None
        }

        daemon._execute_single_job(job)

        # Two notifications: one for the error, one for auto-disable
        self.assertEqual(daemon.notifications.add_notification.call_count, 2)

    def test_skipped_result_logs_skip(self):
        daemon = _make_daemon()
        job = self._make_job()
        daemon.executor.should_skip.return_value = None
        result = ExecutionResult(
            success=True, output="", duration_ms=0,
            skipped=True, skip_reason="heartbeat_empty"
        )
        daemon.executor.execute_job.return_value = result

        daemon._execute_single_job(job)

        daemon.log.append.assert_called_once()
        log_entry = daemon.log.append.call_args[0][0]
        self.assertEqual(log_entry.status, "skip")
        # running_at should be cleared
        daemon.registry.update.assert_any_call("j1", {"running_at": None})

    def test_skipped_with_error_notifies(self):
        daemon = _make_daemon()
        job = self._make_job()
        daemon.executor.should_skip.return_value = None
        result = ExecutionResult(
            success=True, output="", duration_ms=0,
            skipped=True, skip_reason="error reading heartbeat"
        )
        daemon.executor.execute_job.return_value = result

        daemon._execute_single_job(job)

        daemon.notifications.add_notification.assert_called_once()

    def test_skipped_with_blocked_notifies(self):
        daemon = _make_daemon()
        job = self._make_job()
        daemon.executor.should_skip.return_value = None
        result = ExecutionResult(
            success=True, output="", duration_ms=0,
            skipped=True, skip_reason="heartbeat blocked by hook"
        )
        daemon.executor.execute_job.return_value = result

        daemon._execute_single_job(job)

        daemon.notifications.add_notification.assert_called_once()

    def test_concurrent_counter_decremented_on_success(self):
        daemon = _make_daemon()
        job = self._make_job()
        daemon.executor.should_skip.return_value = None
        result = ExecutionResult(success=True, output="ok", duration_ms=50)
        daemon.executor.execute_job.return_value = result
        daemon.executor.apply_result.return_value = {
            "running_at": None, "next_run": None
        }

        daemon._execute_single_job(job)
        self.assertEqual(daemon._concurrent_jobs, 0)

    def test_concurrent_counter_decremented_on_exception(self):
        daemon = _make_daemon()
        job = self._make_job()
        daemon.executor.should_skip.return_value = None
        daemon.executor.execute_job.side_effect = RuntimeError("unexpected")

        daemon._execute_single_job(job)
        self.assertEqual(daemon._concurrent_jobs, 0)

    def test_delete_after_run(self):
        daemon = _make_daemon()
        job = self._make_job()
        daemon.executor.should_skip.return_value = None
        result = ExecutionResult(success=True, output="done", duration_ms=100)
        daemon.executor.execute_job.return_value = result
        daemon.executor.apply_result.return_value = {
            "running_at": None, "next_run": None, "_delete_after_run": True
        }

        daemon._execute_single_job(job)

        daemon.registry.remove.assert_called_once_with("j1")

    def test_heartbeat_job_records_action(self):
        daemon = _make_daemon()
        job = self._make_job(type="heartbeat")
        daemon.executor.should_skip.return_value = None
        result = ExecutionResult(success=True, output="checked", duration_ms=2000)
        daemon.executor.execute_job.return_value = result
        daemon.executor.apply_result.return_value = {
            "running_at": None, "next_run": None
        }

        with patch("cron_daemon.record_heartbeat_action") as mock_record:
            daemon._execute_single_job(job)
            mock_record.assert_called_once()


# ═══════════════════════════════════════════════════════════════
# P3: is_process_alive
# ═══════════════════════════════════════════════════════════════

class TestIsProcessAlive(unittest.TestCase):
    """P3: is_process_alive checks PID existence."""

    @patch("cron_daemon.sys")
    def test_unix_alive(self, mock_sys):
        mock_sys.platform = "linux"
        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None  # No exception = alive
            self.assertTrue(is_process_alive(1234))
            mock_kill.assert_called_once_with(1234, 0)

    @patch("cron_daemon.sys")
    def test_unix_dead(self, mock_sys):
        mock_sys.platform = "linux"
        with patch("os.kill", side_effect=OSError("No such process")):
            self.assertFalse(is_process_alive(1234))

    @patch("cron_daemon.sys")
    def test_windows_alive(self, mock_sys):
        mock_sys.platform = "win32"
        mock_ctypes = MagicMock()
        mock_ctypes.windll.kernel32.OpenProcess.return_value = 42  # non-zero handle
        with patch.dict("sys.modules", {"ctypes": mock_ctypes}):
            result = is_process_alive(999)
            self.assertTrue(result)
            mock_ctypes.windll.kernel32.OpenProcess.assert_called_once_with(
                0x00100000, False, 999
            )
            mock_ctypes.windll.kernel32.CloseHandle.assert_called_once_with(42)

    @patch("cron_daemon.sys")
    def test_windows_dead(self, mock_sys):
        mock_sys.platform = "win32"
        mock_ctypes = MagicMock()
        mock_ctypes.windll.kernel32.OpenProcess.return_value = 0  # zero handle = not found
        with patch.dict("sys.modules", {"ctypes": mock_ctypes}):
            result = is_process_alive(999)
            self.assertFalse(result)
            mock_ctypes.windll.kernel32.CloseHandle.assert_not_called()

    def test_current_process_alive(self):
        """The current process should always be alive."""
        self.assertTrue(is_process_alive(os.getpid()))


# ═══════════════════════════════════════════════════════════════
# P3: stop_daemon
# ═══════════════════════════════════════════════════════════════

class TestStopDaemon(unittest.TestCase):
    """P3: stop_daemon reads PID, checks process, and stops it."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_stop_")
        self.orig_pid_file = cron_daemon.PID_FILE
        cron_daemon.PID_FILE = os.path.join(self.tmpdir, "daemon.pid")

    def tearDown(self):
        cron_daemon.PID_FILE = self.orig_pid_file
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_pid_file_returns_1(self):
        code = stop_daemon()
        self.assertEqual(code, 1)

    def test_dead_process_cleans_up(self):
        with open(cron_daemon.PID_FILE, "w") as f:
            f.write("99999999")
        with patch("cron_daemon.is_process_alive", return_value=False):
            code = stop_daemon()
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(cron_daemon.PID_FILE))


# ═══════════════════════════════════════════════════════════════
# P3: setup_logging
# ═══════════════════════════════════════════════════════════════

class TestSetupLogging(unittest.TestCase):
    """P3: setup_logging configures file and optional console handlers."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_logging_")
        self.orig_cron_dir = cron_daemon.CRON_DIR
        self.orig_log_file = cron_daemon.DAEMON_LOG_FILE
        cron_daemon.CRON_DIR = self.tmpdir
        cron_daemon.DAEMON_LOG_FILE = os.path.join(self.tmpdir, "daemon.log")

    def tearDown(self):
        cron_daemon.CRON_DIR = self.orig_cron_dir
        cron_daemon.DAEMON_LOG_FILE = self.orig_log_file
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_background_mode_one_handler(self):
        logger = setup_logging(foreground=False)
        self.assertEqual(len(logger.handlers), 1)
        self.assertEqual(logger.level, logging.INFO)

    def test_foreground_mode_two_handlers(self):
        logger = setup_logging(foreground=True)
        self.assertEqual(len(logger.handlers), 2)


# ═══════════════════════════════════════════════════════════════
# CronDaemon.stop
# ═══════════════════════════════════════════════════════════════

class TestCronDaemonStop(unittest.TestCase):
    """CronDaemon.stop sets _running to False."""

    def test_stop_sets_running_false(self):
        daemon = _make_daemon()
        daemon._running = True
        daemon.stop()
        self.assertFalse(daemon._running)


# ═══════════════════════════════════════════════════════════════
# Race condition: _concurrent_jobs decrement under lock
# ═══════════════════════════════════════════════════════════════

class TestConcurrentJobsLockProtection(unittest.TestCase):
    """Verify _concurrent_jobs decrement is protected by _acquire_job_lock."""

    def _make_job(self):
        return CronJob(id="j1", name="test", enabled=True, type="standard")

    def test_decrement_under_lock_on_success(self):
        """After successful execution, decrement must happen under lock."""
        daemon = _make_daemon()
        job = self._make_job()
        daemon.executor.should_skip.return_value = None
        result = ExecutionResult(success=True, output="ok", duration_ms=50)
        daemon.executor.execute_job.return_value = result
        daemon.executor.apply_result.return_value = {
            "running_at": None, "next_run": None
        }

        lock_calls = []
        original_acquire = cron_daemon._acquire_job_lock

        def tracking_acquire():
            ctx = original_acquire()
            class TrackingCtx:
                def __enter__(self_inner):
                    lock_calls.append("enter")
                    return ctx.__enter__()
                def __exit__(self_inner, *args):
                    lock_calls.append("exit")
                    return ctx.__exit__(*args)
            return TrackingCtx()

        with patch("cron_daemon._acquire_job_lock", side_effect=tracking_acquire):
            daemon._execute_single_job(job)

        self.assertEqual(daemon._concurrent_jobs, 0)
        # Lock should be acquired at least twice: once for increment, once for decrement
        self.assertGreaterEqual(lock_calls.count("enter"), 2,
            "Lock must be acquired for both increment and decrement")

    def test_decrement_under_lock_on_exception(self):
        """After exception, decrement must happen under lock."""
        daemon = _make_daemon()
        job = self._make_job()
        daemon.executor.should_skip.return_value = None
        daemon.executor.execute_job.side_effect = RuntimeError("boom")

        lock_calls = []
        original_acquire = cron_daemon._acquire_job_lock

        def tracking_acquire():
            ctx = original_acquire()
            class TrackingCtx:
                def __enter__(self_inner):
                    lock_calls.append("enter")
                    return ctx.__enter__()
                def __exit__(self_inner, *args):
                    lock_calls.append("exit")
                    return ctx.__exit__(*args)
            return TrackingCtx()

        with patch("cron_daemon._acquire_job_lock", side_effect=tracking_acquire):
            daemon._execute_single_job(job)

        self.assertEqual(daemon._concurrent_jobs, 0)
        self.assertGreaterEqual(lock_calls.count("enter"), 2,
            "Lock must be acquired for both increment and decrement")


if __name__ == "__main__":
    unittest.main()
