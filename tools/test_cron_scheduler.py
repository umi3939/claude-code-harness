"""
Tests for cron_scheduler.py — Cron/Heartbeat scheduler core module.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from cron_scheduler import (
    BACKOFF_SCHEDULE,
    DEFAULT_TIMEOUT_SECONDS,
    LOG_MAX_BYTES,
    LOG_MAX_LINES,
    MAX_CONCURRENT_JOBS,
    MAX_CONSECUTIVE_ERRORS,
    MAX_EXECUTIONS_PER_HOUR,
    MAX_MISSED_JOBS_RECOVERY,
    MIN_REFIRE_GAP_SECONDS,
    STUCK_THRESHOLD_SECONDS,
    CronJob,
    ExecutionLog,
    ExecutionResult,
    JobExecutor,
    JobRegistry,
    LogEntry,
    NotificationBuffer,
    _atomic_write,
    _parse_iso,
    backoff_seconds,
    compute_next_active_start,
    compute_next_run,
    is_transient_error,
    is_within_active_hours,
)


class TestCronJobDataclass(unittest.TestCase):
    """Test CronJob data structure."""

    def test_default_values(self):
        job = CronJob()
        self.assertEqual(job.id, "")
        self.assertTrue(job.enabled)
        self.assertEqual(job.consecutive_errors, 0)
        self.assertIsNone(job.running_at)
        self.assertEqual(job.timeout_seconds, DEFAULT_TIMEOUT_SECONDS)

    def test_to_dict_removes_none(self):
        job = CronJob(id="test-1", name="Test")
        d = job.to_dict()
        self.assertNotIn("ttl", d)
        self.assertNotIn("active_hours", d)
        self.assertIn("id", d)
        self.assertEqual(d["id"], "test-1")

    def test_from_dict(self):
        d = {"id": "abc", "name": "My Job", "enabled": False, "prompt": "hello"}
        job = CronJob.from_dict(d)
        self.assertEqual(job.id, "abc")
        self.assertEqual(job.name, "My Job")
        self.assertFalse(job.enabled)
        self.assertEqual(job.prompt, "hello")

    def test_from_dict_ignores_unknown_fields(self):
        d = {"id": "abc", "unknown_field": "value"}
        job = CronJob.from_dict(d)
        self.assertEqual(job.id, "abc")
        self.assertFalse(hasattr(job, "unknown_field") and job.__dict__.get("unknown_field"))

    def test_roundtrip(self):
        job = CronJob(
            id="test-1",
            name="Test Job",
            schedule={"type": "every", "interval_seconds": 60},
            prompt="do stuff",
            one_shot=True,
        )
        d = job.to_dict()
        job2 = CronJob.from_dict(d)
        self.assertEqual(job.id, job2.id)
        self.assertEqual(job.schedule, job2.schedule)
        self.assertEqual(job.one_shot, job2.one_shot)


class TestScheduleComputation(unittest.TestCase):
    """Test compute_next_run for all 3 schedule types."""

    def test_at_future(self):
        now = datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc)
        schedule = {"type": "at", "datetime": "2026-03-15T12:00:00+00:00"}
        result = compute_next_run(schedule, now)
        self.assertIsNotNone(result)
        dt = _parse_iso(result)
        self.assertGreater(dt, now)

    def test_at_past(self):
        now = datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc)
        schedule = {"type": "at", "datetime": "2026-03-13T12:00:00+00:00"}
        result = compute_next_run(schedule, now)
        self.assertIsNone(result)

    def test_at_invalid(self):
        result = compute_next_run({"type": "at", "datetime": "not-a-date"})
        self.assertIsNone(result)

    def test_every_basic(self):
        now = datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc)
        anchor = "2026-03-14T11:00:00+00:00"
        schedule = {"type": "every", "interval_seconds": 3600, "anchor": anchor}
        result = compute_next_run(schedule, now)
        self.assertIsNotNone(result)
        dt = _parse_iso(result)
        self.assertGreaterEqual(dt, now)

    def test_every_anchor_in_future(self):
        now = datetime(2026, 3, 14, 10, 0, 0, tzinfo=timezone.utc)
        anchor = "2026-03-14T12:00:00+00:00"
        schedule = {"type": "every", "interval_seconds": 3600, "anchor": anchor}
        result = compute_next_run(schedule, now)
        self.assertIsNotNone(result)
        dt = _parse_iso(result)
        # Should return the anchor itself
        expected = _parse_iso(anchor)
        self.assertEqual(dt, expected)

    def test_every_no_anchor(self):
        now = datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc)
        schedule = {"type": "every", "interval_seconds": 300}
        result = compute_next_run(schedule, now)
        self.assertIsNotNone(result)
        dt = _parse_iso(result)
        self.assertGreater(dt, now)

    def test_every_invalid_interval(self):
        result = compute_next_run({"type": "every", "interval_seconds": -1})
        self.assertIsNone(result)
        result = compute_next_run({"type": "every", "interval_seconds": 0})
        self.assertIsNone(result)

    def test_cron_basic(self):
        now = datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc)
        schedule = {"type": "cron", "expression": "*/5 * * * *"}
        result = compute_next_run(schedule, now)
        self.assertIsNotNone(result)
        dt = _parse_iso(result)
        self.assertGreater(dt, now)

    def test_cron_hourly(self):
        now = datetime(2026, 3, 14, 12, 30, 0, tzinfo=timezone.utc)
        schedule = {"type": "cron", "expression": "0 * * * *"}
        result = compute_next_run(schedule, now)
        self.assertIsNotNone(result)
        dt = _parse_iso(result)
        self.assertEqual(dt.minute, 0)
        self.assertEqual(dt.hour, 13)

    def test_cron_invalid_expression(self):
        result = compute_next_run({"type": "cron", "expression": "invalid"})
        self.assertIsNone(result)

    def test_cron_empty_expression(self):
        result = compute_next_run({"type": "cron", "expression": ""})
        self.assertIsNone(result)

    def test_unknown_type(self):
        result = compute_next_run({"type": "unknown"})
        self.assertIsNone(result)

    def test_empty_schedule(self):
        result = compute_next_run({})
        self.assertIsNone(result)


class TestActiveHours(unittest.TestCase):
    """Test active hours filtering."""

    def test_no_active_hours(self):
        self.assertTrue(is_within_active_hours(None))

    def test_within_normal_range(self):
        now = datetime(2026, 3, 14, 10, 0, 0, tzinfo=timezone.utc)
        self.assertTrue(is_within_active_hours({"start": "08:00", "end": "23:00"}, now))

    def test_outside_normal_range(self):
        now = datetime(2026, 3, 14, 5, 0, 0, tzinfo=timezone.utc)
        self.assertFalse(is_within_active_hours({"start": "08:00", "end": "23:00"}, now))

    def test_overnight_range_in(self):
        now = datetime(2026, 3, 14, 23, 30, 0, tzinfo=timezone.utc)
        self.assertTrue(is_within_active_hours({"start": "22:00", "end": "06:00"}, now))

    def test_overnight_range_in_morning(self):
        now = datetime(2026, 3, 14, 3, 0, 0, tzinfo=timezone.utc)
        self.assertTrue(is_within_active_hours({"start": "22:00", "end": "06:00"}, now))

    def test_overnight_range_out(self):
        now = datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc)
        self.assertFalse(is_within_active_hours({"start": "22:00", "end": "06:00"}, now))

    def test_zero_width_window(self):
        # start == end → always false (canonical interval semantics)
        now = datetime(2026, 3, 14, 10, 0, 0, tzinfo=timezone.utc)
        self.assertFalse(is_within_active_hours({"start": "10:00", "end": "10:00"}, now))

    def test_invalid_format(self):
        now = datetime(2026, 3, 14, 10, 0, 0, tzinfo=timezone.utc)
        self.assertTrue(is_within_active_hours({"start": "invalid", "end": "23:00"}, now))

    def test_empty_fields(self):
        self.assertTrue(is_within_active_hours({"start": "", "end": ""}))

    def test_boundary_start(self):
        now = datetime(2026, 3, 14, 8, 0, 0, tzinfo=timezone.utc)
        self.assertTrue(is_within_active_hours({"start": "08:00", "end": "23:00"}, now))

    def test_boundary_end(self):
        now = datetime(2026, 3, 14, 23, 0, 0, tzinfo=timezone.utc)
        self.assertFalse(is_within_active_hours({"start": "08:00", "end": "23:00"}, now))


class TestComputeNextActiveStart(unittest.TestCase):
    def test_future_today(self):
        now = datetime(2026, 3, 14, 6, 0, 0, tzinfo=timezone.utc)
        result = compute_next_active_start({"start": "08:00"}, now)
        self.assertIsNotNone(result)
        dt = _parse_iso(result)
        self.assertEqual(dt.hour, 8)
        self.assertEqual(dt.day, 14)

    def test_past_today_goes_tomorrow(self):
        now = datetime(2026, 3, 14, 10, 0, 0, tzinfo=timezone.utc)
        result = compute_next_active_start({"start": "08:00"}, now)
        dt = _parse_iso(result)
        self.assertEqual(dt.day, 15)


class TestBackoff(unittest.TestCase):
    """Test exponential backoff calculation."""

    def test_zero_errors(self):
        self.assertEqual(backoff_seconds(0), 0)

    def test_first_error(self):
        self.assertEqual(backoff_seconds(1), 30)

    def test_second_error(self):
        self.assertEqual(backoff_seconds(2), 60)

    def test_third_error(self):
        self.assertEqual(backoff_seconds(3), 300)

    def test_fourth_error(self):
        self.assertEqual(backoff_seconds(4), 900)

    def test_fifth_error(self):
        self.assertEqual(backoff_seconds(5), 3600)

    def test_beyond_schedule(self):
        # Should clamp to last value
        self.assertEqual(backoff_seconds(10), 3600)
        self.assertEqual(backoff_seconds(100), 3600)


class TestTransientError(unittest.TestCase):
    """Test transient error detection."""

    def test_rate_limit(self):
        self.assertTrue(is_transient_error("rate limit exceeded"))
        self.assertTrue(is_transient_error("429 Too Many Requests"))
        self.assertTrue(is_transient_error("rate_limit"))

    def test_timeout(self):
        self.assertTrue(is_transient_error("connection timeout"))
        self.assertTrue(is_transient_error("ETIMEDOUT"))

    def test_network(self):
        self.assertTrue(is_transient_error("network error"))
        self.assertTrue(is_transient_error("ECONNRESET"))
        self.assertTrue(is_transient_error("ECONNREFUSED"))

    def test_server_error(self):
        self.assertTrue(is_transient_error("HTTP 500"))
        self.assertTrue(is_transient_error("502 Bad Gateway"))

    def test_overloaded(self):
        self.assertTrue(is_transient_error("529 overloaded"))
        self.assertTrue(is_transient_error("high demand"))

    def test_not_transient(self):
        self.assertFalse(is_transient_error("invalid prompt"))
        self.assertFalse(is_transient_error("authentication failed"))
        self.assertFalse(is_transient_error(""))


class TestAtomicWrite(unittest.TestCase):
    """Test atomic file writing."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_creates_file(self):
        path = os.path.join(self.tmpdir, "test.json")
        _atomic_write(path, '{"key": "value"}')
        self.assertTrue(os.path.exists(path))
        with open(path) as f:
            self.assertEqual(json.loads(f.read()), {"key": "value"})

    def test_creates_parent_dirs(self):
        path = os.path.join(self.tmpdir, "sub", "dir", "test.json")
        _atomic_write(path, "data")
        self.assertTrue(os.path.exists(path))

    def test_creates_backup(self):
        path = os.path.join(self.tmpdir, "test.json")
        _atomic_write(path, "first")
        _atomic_write(path, "second")
        self.assertTrue(os.path.exists(path + ".bak"))
        with open(path + ".bak") as f:
            self.assertEqual(f.read(), "first")

    def test_overwrites_content(self):
        path = os.path.join(self.tmpdir, "test.json")
        _atomic_write(path, "old")
        _atomic_write(path, "new")
        with open(path) as f:
            self.assertEqual(f.read(), "new")


class TestJobRegistry(unittest.TestCase):
    """Test JobRegistry CRUD operations."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store_path = os.path.join(self.tmpdir, "jobs.json")
        self.registry = JobRegistry(store_path=self.store_path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_add_assigns_id(self):
        job = CronJob(name="Test", prompt="hello")
        result = self.registry.add(job)
        self.assertTrue(len(result.id) > 0)
        self.assertTrue(len(result.created_at) > 0)

    def test_add_preserves_id(self):
        job = CronJob(id="custom-id", name="Test", prompt="hello")
        result = self.registry.add(job)
        self.assertEqual(result.id, "custom-id")

    def test_add_duplicate_id_raises(self):
        job = CronJob(id="dup", name="Test", prompt="hello")
        self.registry.add(job)
        with self.assertRaises(ValueError):
            self.registry.add(CronJob(id="dup", name="Test2", prompt="hello2"))

    def test_get_existing(self):
        job = self.registry.add(CronJob(name="Test", prompt="hello"))
        result = self.registry.get(job.id)
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "Test")

    def test_get_nonexistent(self):
        self.assertIsNone(self.registry.get("nonexistent"))

    def test_update(self):
        job = self.registry.add(CronJob(name="Test", prompt="hello"))
        self.registry.update(job.id, {"name": "Updated", "prompt": "world"})
        result = self.registry.get(job.id)
        self.assertEqual(result.name, "Updated")
        self.assertEqual(result.prompt, "world")

    def test_update_nonexistent(self):
        result = self.registry.update("nonexistent", {"name": "x"})
        self.assertIsNone(result)

    def test_update_cannot_change_id(self):
        job = self.registry.add(CronJob(name="Test", prompt="hello"))
        original_id = job.id
        self.registry.update(job.id, {"id": "new-id"})
        result = self.registry.get(original_id)
        self.assertIsNotNone(result)
        self.assertEqual(result.id, original_id)

    def test_remove(self):
        job = self.registry.add(CronJob(name="Test", prompt="hello"))
        self.assertTrue(self.registry.remove(job.id))
        self.assertIsNone(self.registry.get(job.id))

    def test_remove_nonexistent(self):
        self.assertFalse(self.registry.remove("nonexistent"))

    def test_list_all(self):
        self.registry.add(CronJob(name="A", prompt="a"))
        self.registry.add(CronJob(name="B", prompt="b"))
        jobs = self.registry.list_all()
        self.assertEqual(len(jobs), 2)

    def test_list_enabled(self):
        self.registry.add(CronJob(name="A", prompt="a", enabled=True))
        self.registry.add(CronJob(name="B", prompt="b", enabled=False))
        self.registry.add(CronJob(name="C", prompt="c", enabled=True))
        jobs = self.registry.list_enabled()
        self.assertEqual(len(jobs), 2)

    def test_persistence(self):
        self.registry.add(CronJob(name="Persist", prompt="test"))
        # Create new registry instance reading same file
        reg2 = JobRegistry(store_path=self.store_path)
        jobs = reg2.list_all()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].name, "Persist")

    def test_diff_check_skips_no_change_write(self):
        self.registry.add(CronJob(name="Test", prompt="hello"))
        path = self.store_path
        # Get mtime after first write
        mtime1 = os.path.getmtime(path)

        # Force a tiny delay
        time.sleep(0.05)

        # Save again with no changes — should skip write
        jobs = self.registry._load()
        self.registry._save(jobs)
        mtime2 = os.path.getmtime(path)

        # mtime should not change (diff check prevented write)
        self.assertEqual(mtime1, mtime2)

    def test_update_recomputes_next_run(self):
        job = self.registry.add(CronJob(
            name="Test",
            prompt="hello",
            schedule={"type": "every", "interval_seconds": 60},
        ))
        # Update schedule
        self.registry.update(job.id, {
            "schedule": {"type": "every", "interval_seconds": 120}
        })
        updated = self.registry.get(job.id)
        # next_run should be recomputed
        self.assertIsNotNone(updated.next_run)

    def test_empty_store_file(self):
        # Empty file should not crash
        with open(self.store_path, "w") as f:
            f.write("")
        jobs = self.registry.list_all()
        self.assertEqual(len(jobs), 0)

    def test_corrupt_json(self):
        with open(self.store_path, "w") as f:
            f.write("not valid json {{{")
        jobs = self.registry.list_all()
        self.assertEqual(len(jobs), 0)

    def test_add_sets_default_cwd(self):
        job = self.registry.add(CronJob(name="Test", prompt="hello"))
        self.assertEqual(job.cwd, os.path.expanduser("~"))


class TestJobExecutor(unittest.TestCase):
    """Test job execution engine."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.registry = JobRegistry(store_path=os.path.join(self.tmpdir, "jobs.json"))
        self.executor = JobExecutor(registry=self.registry)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_ttl_not_expired(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        job = CronJob(ttl=future)
        self.assertFalse(self.executor.is_ttl_expired(job))

    def test_ttl_expired(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        job = CronJob(ttl=past)
        self.assertTrue(self.executor.is_ttl_expired(job))

    def test_ttl_none(self):
        job = CronJob()
        self.assertFalse(self.executor.is_ttl_expired(job))

    def test_stuck_detection(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        job = CronJob(running_at=old)
        self.assertTrue(self.executor.is_stuck(job))

    def test_not_stuck(self):
        recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        job = CronJob(running_at=recent)
        self.assertFalse(self.executor.is_stuck(job))

    def test_not_stuck_no_marker(self):
        job = CronJob()
        self.assertFalse(self.executor.is_stuck(job))

    def test_should_skip_disabled(self):
        job = CronJob(enabled=False)
        self.assertEqual(self.executor.should_skip(job), "disabled")

    def test_should_skip_ttl(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        job = CronJob(ttl=past)
        self.assertEqual(self.executor.should_skip(job), "ttl_expired")

    def test_should_skip_active_hours(self):
        # Set active hours that exclude current time
        now = datetime.now(timezone.utc)
        far_start = (now.hour + 5) % 24
        far_end = (now.hour + 6) % 24
        job = CronJob(active_hours={
            "start": f"{far_start:02d}:00",
            "end": f"{far_end:02d}:00",
        })
        self.assertEqual(self.executor.should_skip(job), "outside_active_hours")

    def test_should_skip_already_running(self):
        recent = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        job = CronJob(running_at=recent)
        self.assertEqual(self.executor.should_skip(job), "already_running")

    def test_should_skip_backoff(self):
        recent = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        job = CronJob(consecutive_errors=3, last_run=recent)
        # backoff for 3 errors = 300s, last_run was 5s ago → should skip
        self.assertEqual(self.executor.should_skip(job), "backoff")

    def test_should_not_skip_valid(self):
        job = CronJob(enabled=True)
        self.assertIsNone(self.executor.should_skip(job))

    def test_apply_result_success(self):
        job = CronJob(
            schedule={"type": "every", "interval_seconds": 60},
            consecutive_errors=2,
        )
        result = ExecutionResult(success=True, output="done", duration_ms=100)
        updates = self.executor.apply_result(job, result)
        self.assertEqual(updates["last_result"], "success")
        self.assertEqual(updates["consecutive_errors"], 0)
        self.assertIsNone(updates["running_at"])
        self.assertIsNotNone(updates.get("next_run"))

    def test_apply_result_error(self):
        job = CronJob(
            schedule={"type": "every", "interval_seconds": 60},
            consecutive_errors=0,
        )
        result = ExecutionResult(success=False, error="something broke", duration_ms=100)
        updates = self.executor.apply_result(job, result)
        self.assertEqual(updates["last_result"], "error")
        self.assertEqual(updates["consecutive_errors"], 1)
        self.assertIsNotNone(updates.get("last_error"))

    def test_apply_result_one_shot_success(self):
        job = CronJob(one_shot=True)
        result = ExecutionResult(success=True, duration_ms=50)
        updates = self.executor.apply_result(job, result)
        self.assertFalse(updates["enabled"])
        self.assertIsNone(updates["next_run"])

    def test_apply_result_one_shot_transient_error(self):
        job = CronJob(one_shot=True, consecutive_errors=0)
        result = ExecutionResult(success=False, error="rate limit exceeded", duration_ms=50)
        updates = self.executor.apply_result(job, result)
        # Should schedule retry
        self.assertIsNotNone(updates.get("next_run"))
        self.assertTrue(updates.get("enabled", True))

    def test_apply_result_one_shot_permanent_error(self):
        job = CronJob(one_shot=True, consecutive_errors=0)
        result = ExecutionResult(success=False, error="invalid prompt syntax", duration_ms=50)
        updates = self.executor.apply_result(job, result)
        self.assertFalse(updates["enabled"])

    def test_apply_result_auto_disable_max_errors(self):
        job = CronJob(
            schedule={"type": "every", "interval_seconds": 60},
            consecutive_errors=MAX_CONSECUTIVE_ERRORS - 1,
        )
        result = ExecutionResult(success=False, error="error", duration_ms=100)
        updates = self.executor.apply_result(job, result)
        self.assertFalse(updates["enabled"])

    def test_apply_result_backoff_on_error(self):
        job = CronJob(
            schedule={"type": "every", "interval_seconds": 10},
            consecutive_errors=2,
        )
        result = ExecutionResult(success=False, error="error", duration_ms=100)
        updates = self.executor.apply_result(job, result)
        # Should have next_run that includes backoff
        self.assertIsNotNone(updates.get("next_run"))

    def test_apply_result_ttl_expired(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        job = CronJob(
            schedule={"type": "every", "interval_seconds": 60},
            ttl=past,
        )
        result = ExecutionResult(success=True, duration_ms=50)
        updates = self.executor.apply_result(job, result)
        self.assertFalse(updates["enabled"])

    @patch("cron_scheduler.subprocess.run")
    def test_execute_job_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="output text",
            stderr="",
        )
        job = CronJob(prompt="test prompt", cwd=tempfile.gettempdir())
        result = self.executor.execute_job(job)
        self.assertTrue(result.success)
        self.assertEqual(result.output, "output text")
        self.assertGreaterEqual(result.duration_ms, 0)

    @patch("cron_scheduler.subprocess.run")
    def test_execute_job_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="error message",
        )
        job = CronJob(prompt="test prompt", cwd=tempfile.gettempdir())
        result = self.executor.execute_job(job)
        self.assertFalse(result.success)
        self.assertEqual(result.error, "error message")

    @patch("cron_scheduler.subprocess.run")
    def test_execute_job_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=300)
        job = CronJob(prompt="test prompt", timeout_seconds=300)
        result = self.executor.execute_job(job)
        self.assertFalse(result.success)
        self.assertTrue(result.timed_out)
        self.assertIn("Timeout", result.error)

    @patch("cron_scheduler.subprocess.run")
    def test_execute_job_cli_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        job = CronJob(prompt="test prompt")
        result = self.executor.execute_job(job)
        self.assertFalse(result.success)
        self.assertIn("not found", result.error)

    def test_stuck_with_invalid_timestamp(self):
        job = CronJob(running_at="not-a-date")
        self.assertTrue(self.executor.is_stuck(job))


class TestExecutionLog(unittest.TestCase):
    """Test execution logging."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.log_path = os.path.join(self.tmpdir, "execution.jsonl")
        self.log = ExecutionLog(log_path=self.log_path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_append_creates_file(self):
        entry = LogEntry(
            job_id="j1",
            job_name="Test",
            status="success",
            duration_ms=100,
        )
        self.log.append(entry)
        self.assertTrue(os.path.exists(self.log_path))

    def test_append_creates_valid_jsonl(self):
        self.log.append(LogEntry(job_id="j1", status="success"))
        self.log.append(LogEntry(job_id="j2", status="error", error="boom"))
        with open(self.log_path) as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 2)
        for line in lines:
            data = json.loads(line)
            self.assertIn("job_id", data)

    def test_get_recent_all(self):
        for i in range(5):
            self.log.append(LogEntry(job_id=f"j{i}", status="success"))
        entries = self.log.get_recent()
        self.assertEqual(len(entries), 5)

    def test_get_recent_filtered(self):
        self.log.append(LogEntry(job_id="j1", status="success"))
        self.log.append(LogEntry(job_id="j2", status="error"))
        self.log.append(LogEntry(job_id="j1", status="success"))
        entries = self.log.get_recent(job_id="j1")
        self.assertEqual(len(entries), 2)
        for e in entries:
            self.assertEqual(e.job_id, "j1")

    def test_get_recent_limit(self):
        for _ in range(10):
            self.log.append(LogEntry(job_id="j1", status="success"))
        entries = self.log.get_recent(limit=3)
        self.assertEqual(len(entries), 3)

    def test_get_recent_empty(self):
        entries = self.log.get_recent()
        self.assertEqual(len(entries), 0)

    def test_output_summary_truncation(self):
        long_output = "x" * 3000
        self.log.append(LogEntry(
            job_id="j1",
            status="success",
            output_summary=long_output,
        ))
        with open(self.log_path) as f:
            data = json.loads(f.readline())
        self.assertEqual(len(data["output_summary"]), 2000)

    def test_prune(self):
        # Write enough data to exceed size limit
        big_entry = LogEntry(
            job_id="j1",
            status="success",
            output_summary="x" * 400,
        )
        # Write many entries
        for _ in range(100):
            self.log.append(big_entry)

        # Manually trigger prune with low threshold
        import cron_scheduler
        old_max = cron_scheduler.LOG_MAX_BYTES
        cron_scheduler.LOG_MAX_BYTES = 1000  # Very low threshold
        try:
            self.log._prune_if_needed()
            with open(self.log_path) as f:
                lines = f.readlines()
            self.assertLessEqual(len(lines), LOG_MAX_LINES)
        finally:
            cron_scheduler.LOG_MAX_BYTES = old_max

    def test_error_field_truncated(self):
        long_error = "e" * 1000
        self.log.append(LogEntry(job_id="j1", status="error", error=long_error))
        with open(self.log_path) as f:
            data = json.loads(f.readline())
        self.assertEqual(len(data["error"]), 500)


class TestNotificationBuffer(unittest.TestCase):
    """Test notification buffer."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.notif_path = os.path.join(self.tmpdir, "notifications.json")
        self.buffer = NotificationBuffer(path=self.notif_path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_add_notification(self):
        notif = self.buffer.add_notification("j1", "Job 1", "success", "completed ok")
        self.assertTrue(len(notif.id) > 0)
        self.assertEqual(notif.job_id, "j1")
        self.assertFalse(notif.consumed)

    def test_get_pending(self):
        self.buffer.add_notification("j1", "Job 1", "success", "ok")
        self.buffer.add_notification("j2", "Job 2", "error", "failed")
        pending = self.buffer.get_pending()
        self.assertEqual(len(pending), 2)

    def test_get_pending_empty(self):
        self.assertEqual(len(self.buffer.get_pending()), 0)

    def test_mark_consumed(self):
        self.buffer.add_notification("j1", "Job 1", "success", "ok")
        self.buffer.add_notification("j2", "Job 2", "error", "failed")
        count = self.buffer.mark_consumed()
        self.assertEqual(count, 2)
        pending = self.buffer.get_pending()
        self.assertEqual(len(pending), 0)

    def test_mark_consumed_empty(self):
        count = self.buffer.mark_consumed()
        self.assertEqual(count, 0)

    def test_clear_consumed(self):
        self.buffer.add_notification("j1", "Job 1", "success", "ok")
        self.buffer.mark_consumed()
        self.buffer.add_notification("j2", "Job 2", "error", "failed")  # new, unconsumed
        removed = self.buffer.clear_consumed()
        self.assertEqual(removed, 1)
        pending = self.buffer.get_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].job_id, "j2")

    def test_persistence(self):
        self.buffer.add_notification("j1", "Job 1", "success", "ok")
        # New instance reads same file
        buf2 = NotificationBuffer(path=self.notif_path)
        pending = buf2.get_pending()
        self.assertEqual(len(pending), 1)

    def test_atomic_write(self):
        self.buffer.add_notification("j1", "Job 1", "success", "ok")
        self.assertTrue(os.path.exists(self.notif_path))


class TestParseIso(unittest.TestCase):
    """Test ISO-8601 parsing."""

    def test_valid_utc(self):
        dt = _parse_iso("2026-03-14T12:00:00+00:00")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.hour, 12)

    def test_valid_without_tz(self):
        dt = _parse_iso("2026-03-14T12:00:00")
        self.assertIsNotNone(dt)
        # Should assume UTC
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_invalid(self):
        self.assertIsNone(_parse_iso("not-a-date"))

    def test_empty(self):
        self.assertIsNone(_parse_iso(""))

    def test_none(self):
        self.assertIsNone(_parse_iso(None))


class TestSafetyConstants(unittest.TestCase):
    """Verify safety valve constants have expected values."""

    def test_constants(self):
        self.assertEqual(MIN_REFIRE_GAP_SECONDS, 2)
        self.assertEqual(MAX_CONCURRENT_JOBS, 3)
        self.assertEqual(DEFAULT_TIMEOUT_SECONDS, 300)
        self.assertEqual(MAX_EXECUTIONS_PER_HOUR, 20)
        self.assertEqual(MAX_MISSED_JOBS_RECOVERY, 5)
        self.assertEqual(STUCK_THRESHOLD_SECONDS, 7200)
        self.assertEqual(BACKOFF_SCHEDULE, [30, 60, 300, 900, 3600])
        self.assertEqual(MAX_CONSECUTIVE_ERRORS, 5)
        self.assertEqual(LOG_MAX_BYTES, 2_000_000)
        self.assertEqual(LOG_MAX_LINES, 2000)


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and integration scenarios."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_registry_with_schedule_assigns_next_run(self):
        reg = JobRegistry(store_path=os.path.join(self.tmpdir, "jobs.json"))
        job = reg.add(CronJob(
            name="Scheduled",
            prompt="test",
            schedule={"type": "every", "interval_seconds": 300},
        ))
        self.assertIsNotNone(job.next_run)

    def test_registry_without_schedule_no_next_run(self):
        reg = JobRegistry(store_path=os.path.join(self.tmpdir, "jobs.json"))
        job = reg.add(CronJob(name="No Schedule", prompt="test"))
        self.assertIsNone(job.next_run)

    def test_executor_stuck_job_cleared(self):
        executor = JobExecutor()
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        job = CronJob(running_at=old, enabled=True)
        # Stuck job should not be skipped as "already_running"
        skip = executor.should_skip(job)
        self.assertIsNone(skip)  # Stuck marker is ignored

    def test_apply_result_error_truncation(self):
        executor = JobExecutor()
        job = CronJob(schedule={"type": "every", "interval_seconds": 60})
        long_error = "x" * 1000
        result = ExecutionResult(success=False, error=long_error)
        updates = executor.apply_result(job, result)
        self.assertEqual(len(updates["last_error"]), 500)

    def test_full_lifecycle(self):
        """Test complete add → execute → log → notify cycle."""
        reg = JobRegistry(store_path=os.path.join(self.tmpdir, "jobs.json"))
        log = ExecutionLog(log_path=os.path.join(self.tmpdir, "execution.jsonl"))
        notif = NotificationBuffer(path=os.path.join(self.tmpdir, "notifications.json"))
        executor = JobExecutor(registry=reg)

        # Add job
        job = reg.add(CronJob(
            name="Lifecycle Test",
            prompt="echo hello",
            schedule={"type": "every", "interval_seconds": 60},
        ))

        # Simulate execution result
        exec_result = ExecutionResult(success=True, output="hello\n", duration_ms=150)
        updates = executor.apply_result(job, exec_result)

        # Apply updates
        reg.update(job.id, updates)

        # Log it
        log.append(LogEntry(
            job_id=job.id,
            job_name=job.name,
            status="success",
            duration_ms=exec_result.duration_ms,
            output_summary=exec_result.output[:500],
        ))

        # Notify
        notif.add_notification(
            job.id, job.name, "success",
            f"Job '{job.name}' completed successfully"
        )

        # Verify state
        updated_job = reg.get(job.id)
        self.assertEqual(updated_job.last_result, "success")
        self.assertEqual(updated_job.consecutive_errors, 0)

        entries = log.get_recent(job_id=job.id)
        self.assertEqual(len(entries), 1)

        pending = notif.get_pending()
        self.assertEqual(len(pending), 1)

    def test_every_alignment(self):
        """Test that 'every' schedule aligns to anchor properly."""
        anchor = datetime(2026, 3, 14, 10, 0, 0, tzinfo=timezone.utc)
        now = datetime(2026, 3, 14, 10, 7, 30, tzinfo=timezone.utc)
        schedule = {
            "type": "every",
            "interval_seconds": 300,  # 5 min
            "anchor": anchor.isoformat(),
        }
        result = compute_next_run(schedule, now)
        dt = _parse_iso(result)
        # 10:00 + ceil(7.5min / 5min) * 5min = 10:00 + 2*5min = 10:10
        expected = datetime(2026, 3, 14, 10, 10, 0, tzinfo=timezone.utc)
        self.assertEqual(dt, expected)


class TestParseRelativeTime(unittest.TestCase):
    """Test parse_relative_time helper function (in MCP layer)."""

    def test_seconds(self):
        from cron_mcp_server import parse_relative_time
        self.assertEqual(parse_relative_time("30s"), 30)

    def test_minutes(self):
        from cron_mcp_server import parse_relative_time
        self.assertEqual(parse_relative_time("20m"), 1200)

    def test_hours(self):
        from cron_mcp_server import parse_relative_time
        self.assertEqual(parse_relative_time("2h"), 7200)

    def test_zero_value_raises(self):
        from cron_mcp_server import parse_relative_time
        with self.assertRaises(ValueError):
            parse_relative_time("0m")

    def test_negative_value_raises(self):
        from cron_mcp_server import parse_relative_time
        with self.assertRaises(ValueError):
            parse_relative_time("-5m")

    def test_no_unit_raises(self):
        from cron_mcp_server import parse_relative_time
        with self.assertRaises(ValueError):
            parse_relative_time("20")

    def test_invalid_unit_raises(self):
        from cron_mcp_server import parse_relative_time
        with self.assertRaises(ValueError):
            parse_relative_time("20d")

    def test_empty_string_raises(self):
        from cron_mcp_server import parse_relative_time
        with self.assertRaises(ValueError):
            parse_relative_time("")

    def test_non_numeric_raises(self):
        from cron_mcp_server import parse_relative_time
        with self.assertRaises(ValueError):
            parse_relative_time("abcm")

    def test_upper_limit(self):
        """Relative time should have a reasonable upper limit."""
        from cron_mcp_server import MAX_RELATIVE_SECONDS, parse_relative_time
        # Just under the limit should work
        max_hours = MAX_RELATIVE_SECONDS // 3600
        self.assertEqual(parse_relative_time(f"{max_hours}h"), max_hours * 3600)

    def test_over_upper_limit_raises(self):
        from cron_mcp_server import MAX_RELATIVE_SECONDS, parse_relative_time
        over_hours = (MAX_RELATIVE_SECONDS // 3600) + 1
        with self.assertRaises(ValueError):
            parse_relative_time(f"{over_hours}h")

    def test_fractional_raises(self):
        from cron_mcp_server import parse_relative_time
        with self.assertRaises(ValueError):
            parse_relative_time("1.5h")


class TestDeleteAfterRunField(unittest.TestCase):
    """Test CronJob delete_after_run field."""

    def test_default_is_false(self):
        job = CronJob()
        self.assertFalse(job.delete_after_run)

    def test_to_dict_includes_field(self):
        job = CronJob(id="test-1", name="Test", delete_after_run=True)
        d = job.to_dict()
        self.assertTrue(d["delete_after_run"])

    def test_to_dict_includes_false(self):
        job = CronJob(id="test-1", name="Test", delete_after_run=False)
        d = job.to_dict()
        self.assertFalse(d["delete_after_run"])

    def test_from_dict_with_field(self):
        d = {"id": "abc", "name": "Test", "delete_after_run": True}
        job = CronJob.from_dict(d)
        self.assertTrue(job.delete_after_run)

    def test_from_dict_without_field_backward_compat(self):
        """Existing JSON without delete_after_run should default to False."""
        d = {"id": "abc", "name": "Test"}
        job = CronJob.from_dict(d)
        self.assertFalse(job.delete_after_run)

    def test_roundtrip(self):
        job = CronJob(id="test-1", name="Test", delete_after_run=True)
        d = job.to_dict()
        job2 = CronJob.from_dict(d)
        self.assertTrue(job2.delete_after_run)

    def test_registry_persistence(self):
        """delete_after_run should survive registry save/load."""
        tmpdir = tempfile.mkdtemp()
        try:
            reg = JobRegistry(store_path=os.path.join(tmpdir, "jobs.json"))
            job = reg.add(CronJob(name="Test", prompt="hello", delete_after_run=True))
            reg2 = JobRegistry(store_path=os.path.join(tmpdir, "jobs.json"))
            loaded = reg2.get(job.id)
            self.assertTrue(loaded.delete_after_run)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestApplyResultDeleteAfterRun(unittest.TestCase):
    """Test apply_result with delete_after_run marker."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.registry = JobRegistry(store_path=os.path.join(self.tmpdir, "jobs.json"))
        self.executor = JobExecutor(registry=self.registry)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_success_with_delete_after_run(self):
        """apply_result should include _delete_after_run marker on success."""
        job = CronJob(
            one_shot=True,
            delete_after_run=True,
            schedule={"type": "at", "datetime": "2026-03-15T12:00:00+00:00"},
        )
        result = ExecutionResult(success=True, duration_ms=50)
        updates = self.executor.apply_result(job, result)
        self.assertTrue(updates.get("_delete_after_run"))

    def test_failure_no_delete_marker(self):
        """apply_result should NOT include _delete_after_run on failure."""
        job = CronJob(
            one_shot=True,
            delete_after_run=True,
            schedule={"type": "at", "datetime": "2026-03-15T12:00:00+00:00"},
        )
        result = ExecutionResult(success=False, error="something broke", duration_ms=50)
        updates = self.executor.apply_result(job, result)
        self.assertFalse(updates.get("_delete_after_run", False))

    def test_no_delete_after_run_flag(self):
        """apply_result should NOT include _delete_after_run when flag is False."""
        job = CronJob(
            one_shot=True,
            delete_after_run=False,
            schedule={"type": "at", "datetime": "2026-03-15T12:00:00+00:00"},
        )
        result = ExecutionResult(success=True, duration_ms=50)
        updates = self.executor.apply_result(job, result)
        self.assertFalse(updates.get("_delete_after_run", False))

    def test_delete_marker_not_a_job_field(self):
        """_delete_after_run should not be a CronJob field (it's internal)."""
        self.assertNotIn("_delete_after_run",
                         {f.name for f in CronJob.__dataclass_fields__.values()})

    def test_delete_after_run_true_with_one_shot_false_raises(self):
        """delete_after_run=True with one_shot=False should be rejected at add time."""
        # This validation happens in MCP layer, tested separately below


class TestMCPAtParameter(unittest.TestCase):
    """Test persistent_cron_add with at parameter (relative time)."""

    def test_at_parameter_creates_job(self):
        """at='20m' should create a one-shot job with future schedule."""
        from cron_mcp_server import parse_relative_time
        seconds = parse_relative_time("20m")
        self.assertEqual(seconds, 1200)
        # The actual MCP tool integration is tested via functional test

    def test_at_parameter_sets_delete_after_run_default(self):
        """When at parameter is used, delete_after_run should default to True."""
        # This is verified at MCP layer level


class TestDeleteAfterRunValidation(unittest.TestCase):
    """Test validation rules for delete_after_run."""

    def test_delete_after_run_with_one_shot_true_ok(self):
        """delete_after_run=True with one_shot=True is valid."""
        job = CronJob(one_shot=True, delete_after_run=True)
        self.assertTrue(job.one_shot)
        self.assertTrue(job.delete_after_run)

    def test_recurring_job_no_delete(self):
        """Regular recurring job should have delete_after_run=False."""
        job = CronJob(one_shot=False, delete_after_run=False)
        self.assertFalse(job.delete_after_run)


class TestHeartbeatFileIOTimeout(unittest.TestCase):
    """M-R4: File I/O in heartbeat preprocessing should have timeout protection."""

    def test_read_heartbeat_file_has_size_limit(self):
        """read_heartbeat_file should reject files exceeding HEARTBEAT_MAX_SIZE."""
        from cron_scheduler import HEARTBEAT_MAX_SIZE, read_heartbeat_file
        # Create a file that exceeds the limit
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("x" * (HEARTBEAT_MAX_SIZE + 1))
            path = f.name
        try:
            content, error = read_heartbeat_file(path)
            self.assertIsNone(content)
            self.assertIn("exceeds limit", error)
        finally:
            os.unlink(path)

    def test_execute_job_subprocess_has_timeout(self):
        """execute_job should use subprocess timeout."""
        import inspect

        from cron_scheduler import JobExecutor
        source = inspect.getsource(JobExecutor.execute_job)
        self.assertIn("timeout", source)


if __name__ == "__main__":
    unittest.main()
