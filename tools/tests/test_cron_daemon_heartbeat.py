#!/usr/bin/env python3
"""Tests for cron_daemon heartbeat action JSONL recording."""

import json
import logging
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TOOLS_DIR = str(Path(__file__).resolve().parent.parent)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from cron_daemon import CronDaemon, prune_heartbeat_actions, record_heartbeat_action
from cron_scheduler import CronJob, ExecutionResult


class TestRecordHeartbeatAction(unittest.TestCase):
    """Tests for the record_heartbeat_action function."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_hb_record_")
        self.actions_file = os.path.join(self.tmpdir, "heartbeat_actions.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_record_success(self):
        """Successful heartbeat execution is recorded."""
        result = ExecutionResult(success=True, output="All is well", duration_ms=5000)
        record_heartbeat_action(self.actions_file, result)

        with open(self.actions_file, "r") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 1)
        data = json.loads(lines[0])
        self.assertEqual(data["concern"], "heartbeat")
        self.assertEqual(data["action_taken"], "full_run")
        self.assertEqual(data["result"], "success")
        self.assertEqual(data["duration_ms"], 5000)
        self.assertIn("timestamp", data)
        self.assertEqual(data["output_preview"], "All is well")

    def test_record_error(self):
        """Failed heartbeat execution is recorded with error result."""
        result = ExecutionResult(success=False, error="timeout", duration_ms=300000)
        record_heartbeat_action(self.actions_file, result)

        with open(self.actions_file, "r") as f:
            data = json.loads(f.readline())
        self.assertEqual(data["result"], "error")
        self.assertEqual(data["duration_ms"], 300000)

    def test_output_preview_truncated_to_500(self):
        """Output preview is truncated to 500 characters."""
        long_output = "x" * 1000
        result = ExecutionResult(success=True, output=long_output, duration_ms=100)
        record_heartbeat_action(self.actions_file, result)

        with open(self.actions_file, "r") as f:
            data = json.loads(f.readline())
        self.assertEqual(len(data["output_preview"]), 500)

    def test_append_multiple_records(self):
        """Multiple records are appended, not overwritten."""
        for i in range(3):
            result = ExecutionResult(success=True, output=f"run {i}", duration_ms=i*1000)
            record_heartbeat_action(self.actions_file, result)

        with open(self.actions_file, "r") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 3)

    def test_creates_directory_if_missing(self):
        """Creates parent directory if it doesn't exist."""
        nested = os.path.join(self.tmpdir, "sub", "dir", "heartbeat_actions.jsonl")
        result = ExecutionResult(success=True, output="ok", duration_ms=100)
        record_heartbeat_action(nested, result)
        self.assertTrue(os.path.exists(nested))

    def test_timestamp_is_iso8601(self):
        """Recorded timestamp is ISO 8601 format."""
        result = ExecutionResult(success=True, output="ok", duration_ms=100)
        record_heartbeat_action(self.actions_file, result)

        with open(self.actions_file, "r") as f:
            data = json.loads(f.readline())
        # Should parse as ISO datetime
        from datetime import datetime
        dt = datetime.fromisoformat(data["timestamp"])
        self.assertIsNotNone(dt)


class TestPruneHeartbeatActions(unittest.TestCase):
    """Tests for pruning heartbeat_actions.jsonl to 100 lines."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_hb_prune_")
        self.actions_file = os.path.join(self.tmpdir, "heartbeat_actions.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_prune_under_100(self):
        """File with <100 lines is not pruned."""
        with open(self.actions_file, "w") as f:
            for i in range(50):
                f.write(json.dumps({"i": i}) + "\n")
        prune_heartbeat_actions(self.actions_file, max_lines=100)
        with open(self.actions_file, "r") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 50)

    def test_prune_over_100(self):
        """File with >100 lines is pruned to keep latest 100."""
        with open(self.actions_file, "w") as f:
            for i in range(150):
                f.write(json.dumps({"i": i}) + "\n")
        prune_heartbeat_actions(self.actions_file, max_lines=100)
        with open(self.actions_file, "r") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 100)
        # First remaining line should be i=50
        first = json.loads(lines[0])
        self.assertEqual(first["i"], 50)
        # Last line should be i=149
        last = json.loads(lines[-1])
        self.assertEqual(last["i"], 149)

    def test_prune_nonexistent_file(self):
        """Pruning a nonexistent file does nothing (no error)."""
        prune_heartbeat_actions(os.path.join(self.tmpdir, "nope.jsonl"), max_lines=100)

    def test_prune_exactly_100(self):
        """File with exactly 100 lines is not pruned."""
        with open(self.actions_file, "w") as f:
            for i in range(100):
                f.write(json.dumps({"i": i}) + "\n")
        prune_heartbeat_actions(self.actions_file, max_lines=100)
        with open(self.actions_file, "r") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 100)


class TestCronDaemonHeartbeatRecording(unittest.TestCase):
    """Integration test: CronDaemon._execute_single_job records heartbeat actions."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_hb_daemon_")
        self.actions_file = os.path.join(self.tmpdir, "heartbeat_actions.jsonl")
        self.logger = logging.getLogger("test_cron_daemon_hb")
        self.logger.setLevel(logging.DEBUG)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("cron_daemon.HEARTBEAT_ACTIONS_FILE")
    @patch.object(CronDaemon, "_check_rate_limit", return_value=True)
    def test_heartbeat_job_records_action(self, mock_rate, mock_actions_path):
        """After heartbeat execution, action is recorded to JSONL."""
        mock_actions_path.__str__ = lambda s: self.actions_file
        # We need to patch at module level
        import cron_daemon
        original = cron_daemon.HEARTBEAT_ACTIONS_FILE
        cron_daemon.HEARTBEAT_ACTIONS_FILE = self.actions_file

        daemon = CronDaemon(self.logger)

        job = CronJob(
            id="test-hb-1",
            name="test-heartbeat",
            type="heartbeat",
            enabled=True,
            prompt="dummy_heartbeat_path",
        )

        # Mock execute_job to return a successful non-skip result
        mock_result = ExecutionResult(success=True, output="Checked all systems", duration_ms=2000)
        with patch.object(daemon.executor, "execute_job", return_value=mock_result), \
             patch.object(daemon.executor, "should_skip", return_value=None), \
             patch.object(daemon.executor, "apply_result", return_value={"last_run": "now", "running_at": None, "last_result": "success", "consecutive_errors": 0, "next_run": None}), \
             patch.object(daemon.registry, "update"):
            daemon._execute_single_job(job)

        # Verify JSONL was written
        self.assertTrue(os.path.exists(self.actions_file))
        with open(self.actions_file, "r") as f:
            data = json.loads(f.readline())
        self.assertEqual(data["result"], "success")
        self.assertEqual(data["duration_ms"], 2000)

        cron_daemon.HEARTBEAT_ACTIONS_FILE = original

    @patch.object(CronDaemon, "_check_rate_limit", return_value=True)
    def test_non_heartbeat_job_does_not_record(self, mock_rate):
        """Standard (non-heartbeat) job does NOT record to heartbeat_actions.jsonl."""
        import cron_daemon
        original = cron_daemon.HEARTBEAT_ACTIONS_FILE
        cron_daemon.HEARTBEAT_ACTIONS_FILE = self.actions_file

        daemon = CronDaemon(self.logger)

        job = CronJob(
            id="test-std-1",
            name="test-standard",
            type="standard",
            enabled=True,
            prompt="do something",
        )

        mock_result = ExecutionResult(success=True, output="done", duration_ms=100)
        with patch.object(daemon.executor, "execute_job", return_value=mock_result), \
             patch.object(daemon.executor, "should_skip", return_value=None), \
             patch.object(daemon.executor, "apply_result", return_value={"last_run": "now", "running_at": None, "last_result": "success", "consecutive_errors": 0, "next_run": None}), \
             patch.object(daemon.registry, "update"):
            daemon._execute_single_job(job)

        # No JSONL file should be created for standard jobs
        self.assertFalse(os.path.exists(self.actions_file))

        cron_daemon.HEARTBEAT_ACTIONS_FILE = original

    @patch.object(CronDaemon, "_check_rate_limit", return_value=True)
    def test_skipped_heartbeat_does_not_record(self, mock_rate):
        """Skipped heartbeat (preprocessing skip) does NOT record to JSONL."""
        import cron_daemon
        original = cron_daemon.HEARTBEAT_ACTIONS_FILE
        cron_daemon.HEARTBEAT_ACTIONS_FILE = self.actions_file

        daemon = CronDaemon(self.logger)

        job = CronJob(
            id="test-hb-skip",
            name="test-heartbeat-skip",
            type="heartbeat",
            enabled=True,
            prompt="dummy_path",
        )

        # Skipped result
        mock_result = ExecutionResult(success=True, output="heartbeat_skip: empty", duration_ms=0, skipped=True, skip_reason="heartbeat_empty")
        with patch.object(daemon.executor, "execute_job", return_value=mock_result), \
             patch.object(daemon.executor, "should_skip", return_value=None), \
             patch.object(daemon.registry, "update"):
            daemon._execute_single_job(job)

        self.assertFalse(os.path.exists(self.actions_file))

        cron_daemon.HEARTBEAT_ACTIONS_FILE = original


if __name__ == "__main__":
    unittest.main()
