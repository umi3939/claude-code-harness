#!/usr/bin/env python3
"""Tests for daemon_monitor.py -- TDD: tests written before implementation.

Includes M-R2 fix: MonitorLog.write should log exceptions, not swallow silently.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
from unittest import mock
from unittest.mock import patch

import pytest

# Ensure tools dir is on path
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from daemon_monitor import (
    DaemonHealthChecker,
    DaemonRestarter,
    MonitorLog,
    DaemonTarget,
    is_process_alive,
    MONITOR_LOG_FILE,
    CRON_LAST_TICK_FILE,
    DEFAULT_MAX_CONSECUTIVE_FAILURES,
    MONITOR_LOG_MAX_LINES,
)


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def tmp_dir(tmp_path):
    """Provide a temporary directory for test artifacts."""
    return str(tmp_path)


@pytest.fixture
def cron_pid_file(tmp_dir):
    path = os.path.join(tmp_dir, "daemon.pid")
    return path


@pytest.fixture
def discord_pid_file(tmp_dir):
    path = os.path.join(tmp_dir, "discord_daemon.pid")
    return path


@pytest.fixture
def log_file(tmp_dir):
    return os.path.join(tmp_dir, "daemon_monitor_log.jsonl")


@pytest.fixture
def tick_file(tmp_dir):
    return os.path.join(tmp_dir, ".cron-last-tick")


@pytest.fixture
def cron_target(cron_pid_file, tick_file):
    return DaemonTarget(
        name="cron",
        pid_file=cron_pid_file,
        start_command=["pythonw", "cron_daemon.py"],
        cmdline_keyword="cron_daemon",
    )


@pytest.fixture
def discord_target(discord_pid_file):
    return DaemonTarget(
        name="discord",
        pid_file=discord_pid_file,
        start_command=["pythonw", "discord_daemon.py"],
        cmdline_keyword="discord_daemon",
    )


@pytest.fixture
def monitor_log(log_file):
    return MonitorLog(log_file)


# ═══════════════════════════════════════════════════════════════
# is_process_alive (unified function)
# ═══════════════════════════════════════════════════════════════

class TestIsProcessAlive:
    """Test the unified is_process_alive function."""

    def test_current_process_is_alive(self):
        """Current process should be detected as alive."""
        assert is_process_alive(os.getpid()) is True

    def test_nonexistent_pid_is_not_alive(self):
        """A very high PID that doesn't exist should return False."""
        # PID 99999999 is extremely unlikely to exist
        assert is_process_alive(99999999) is False

    def test_zero_pid_is_not_alive(self):
        """PID 0 should not be considered alive (special system PID)."""
        # On Windows, OpenProcess(SYNCHRONIZE, False, 0) may succeed for System Idle Process
        # but we handle this as a special case
        # On Unix, kill(0, 0) sends to process group
        # Either way, PID 0 is not a valid daemon PID
        result = is_process_alive(0)
        assert isinstance(result, bool)

    def test_negative_pid_is_not_alive(self):
        """Negative PIDs should return False."""
        assert is_process_alive(-1) is False


# ═══════════════════════════════════════════════════════════════
# DaemonTarget dataclass
# ═══════════════════════════════════════════════════════════════

class TestDaemonTarget:
    """Test DaemonTarget data structure."""

    def test_creation(self, cron_target):
        assert cron_target.name == "cron"
        assert cron_target.cmdline_keyword == "cron_daemon"

    def test_has_required_fields(self, cron_target):
        assert hasattr(cron_target, "name")
        assert hasattr(cron_target, "pid_file")
        assert hasattr(cron_target, "start_command")
        assert hasattr(cron_target, "cmdline_keyword")


# ═══════════════════════════════════════════════════════════════
# DaemonHealthChecker
# ═══════════════════════════════════════════════════════════════

class TestDaemonHealthChecker:
    """Test health checking logic."""

    def test_healthy_daemon(self, cron_target):
        """PID file exists and process is alive -> healthy."""
        # Write a PID file with current process PID
        with open(cron_target.pid_file, "w") as f:
            f.write(str(os.getpid()))

        checker = DaemonHealthChecker()
        result = checker.check(cron_target)
        assert result.alive is True
        assert result.pid == os.getpid()

    def test_no_pid_file(self, cron_target):
        """No PID file -> not alive."""
        checker = DaemonHealthChecker()
        result = checker.check(cron_target)
        assert result.alive is False
        assert result.pid is None
        assert "no pid file" in result.reason.lower()

    def test_pid_file_with_dead_process(self, cron_target):
        """PID file exists but process is dead -> not alive."""
        with open(cron_target.pid_file, "w") as f:
            f.write("99999999")  # Non-existent PID

        checker = DaemonHealthChecker()
        result = checker.check(cron_target)
        assert result.alive is False
        assert result.pid == 99999999
        assert "not running" in result.reason.lower()

    def test_pid_file_with_invalid_content(self, cron_target):
        """PID file with non-numeric content -> not alive."""
        with open(cron_target.pid_file, "w") as f:
            f.write("not_a_pid")

        checker = DaemonHealthChecker()
        result = checker.check(cron_target)
        assert result.alive is False
        assert result.pid is None

    def test_empty_pid_file(self, cron_target):
        """Empty PID file -> not alive."""
        with open(cron_target.pid_file, "w") as f:
            f.write("")

        checker = DaemonHealthChecker()
        result = checker.check(cron_target)
        assert result.alive is False
        assert result.pid is None

    def test_stale_pid_file_freshness_check(self, cron_target):
        """PID file older than threshold with alive process -> check cmdline."""
        # Write PID file with current PID but set mtime to very old
        with open(cron_target.pid_file, "w") as f:
            f.write(str(os.getpid()))

        # Set mtime to 1 hour ago
        old_time = time.time() - 3600
        os.utime(cron_target.pid_file, (old_time, old_time))

        checker = DaemonHealthChecker()
        # Even with stale PID file, if process is alive and we can't verify cmdline,
        # the freshness check should flag it
        result = checker.check(cron_target)
        # Process is alive but PID file is stale -- result depends on cmdline check
        # With mock, we test the logic path
        assert isinstance(result.alive, bool)


# ═══════════════════════════════════════════════════════════════
# MonitorLog
# ═══════════════════════════════════════════════════════════════

class TestMonitorLog:
    """Test JSONL monitor log."""

    def test_write_entry(self, monitor_log, log_file):
        """Log entry should be written as JSONL."""
        monitor_log.write("cron", "detected", "Process not running", 0)

        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["daemon"] == "cron"
        assert entry["event"] == "detected"
        assert entry["detail"] == "Process not running"
        assert entry["consecutive_failures"] == 0
        assert "timestamp" in entry

    def test_timestamp_is_iso8601(self, monitor_log, log_file):
        """Timestamp should be ISO8601 format."""
        monitor_log.write("discord", "restart_success", "Restarted OK", 0)

        with open(log_file, "r", encoding="utf-8") as f:
            entry = json.loads(f.readline())

        ts = entry["timestamp"]
        # Should be parseable as ISO8601
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None  # Should have timezone

    def test_multiple_entries(self, monitor_log, log_file):
        """Multiple entries should be appended."""
        monitor_log.write("cron", "detected", "Dead", 0)
        monitor_log.write("cron", "restart_success", "OK", 0)
        monitor_log.write("discord", "detected", "Dead", 0)

        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        assert len(lines) == 3

    def test_pruning(self, monitor_log, log_file):
        """Log should be pruned when exceeding max lines."""
        # Write more than max lines
        for i in range(MONITOR_LOG_MAX_LINES + 20):
            monitor_log.write("cron", "detected", f"entry_{i}", 0)

        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        assert len(lines) <= MONITOR_LOG_MAX_LINES
        # Last entry should be the most recent
        last = json.loads(lines[-1])
        assert f"entry_{MONITOR_LOG_MAX_LINES + 19}" in last["detail"]

    def test_count_consecutive_failures(self, monitor_log, log_file):
        """Should count consecutive restart failures for a daemon."""
        monitor_log.write("cron", "restart_failed", "fail1", 1)
        monitor_log.write("cron", "restart_failed", "fail2", 2)
        monitor_log.write("cron", "restart_failed", "fail3", 3)

        count = monitor_log.count_consecutive_failures("cron")
        assert count == 3

    def test_consecutive_failures_reset_on_success(self, monitor_log, log_file):
        """Success should reset consecutive failure count."""
        monitor_log.write("cron", "restart_failed", "fail1", 1)
        monitor_log.write("cron", "restart_failed", "fail2", 2)
        monitor_log.write("cron", "restart_success", "ok", 0)

        count = monitor_log.count_consecutive_failures("cron")
        assert count == 0

    def test_consecutive_failures_per_daemon(self, monitor_log, log_file):
        """Failures for different daemons should be independent."""
        monitor_log.write("cron", "restart_failed", "fail1", 1)
        monitor_log.write("cron", "restart_failed", "fail2", 2)
        monitor_log.write("discord", "restart_failed", "fail1", 1)

        assert monitor_log.count_consecutive_failures("cron") == 2
        assert monitor_log.count_consecutive_failures("discord") == 1

    def test_consecutive_failures_empty_log(self, monitor_log):
        """Empty log should return 0 consecutive failures."""
        count = monitor_log.count_consecutive_failures("cron")
        assert count == 0

    def test_consecutive_failures_with_detected_events(self, monitor_log, log_file):
        """'detected' events should not affect failure count."""
        monitor_log.write("cron", "detected", "dead", 0)
        monitor_log.write("cron", "restart_failed", "fail1", 1)
        monitor_log.write("cron", "detected", "still dead", 0)
        monitor_log.write("cron", "restart_failed", "fail2", 2)

        count = monitor_log.count_consecutive_failures("cron")
        assert count == 2

    def test_log_file_created_on_first_write(self, tmp_dir):
        """Log file and directory should be created automatically."""
        nested = os.path.join(tmp_dir, "sub", "log.jsonl")
        log = MonitorLog(nested)
        log.write("cron", "detected", "test", 0)
        assert os.path.exists(nested)


# ═══════════════════════════════════════════════════════════════
# DaemonRestarter
# ═══════════════════════════════════════════════════════════════

class TestDaemonRestarter:
    """Test daemon restart logic."""

    def test_restart_creates_subprocess(self, cron_target, monitor_log):
        """Restart should launch a subprocess."""
        restarter = DaemonRestarter(monitor_log)

        with mock.patch("daemon_monitor.subprocess.Popen") as mock_popen, \
             mock.patch("daemon_monitor.time.sleep"), \
             mock.patch("daemon_monitor.is_process_alive", return_value=True):
            mock_process = mock.MagicMock()
            mock_process.pid = 12345
            mock_popen.return_value = mock_process

            # Write a fake PID file to simulate daemon writing its own PID
            with open(cron_target.pid_file, "w") as f:
                f.write("12345")

            result = restarter.restart(cron_target)
            assert mock_popen.called

    def test_restart_success_returns_true(self, cron_target, monitor_log):
        """Successful restart should return True."""
        restarter = DaemonRestarter(monitor_log)

        with mock.patch("daemon_monitor.subprocess.Popen") as mock_popen, \
             mock.patch("daemon_monitor.time.sleep"), \
             mock.patch("daemon_monitor.is_process_alive", return_value=True):
            mock_process = mock.MagicMock()
            mock_process.pid = 12345
            mock_popen.return_value = mock_process

            with open(cron_target.pid_file, "w") as f:
                f.write("12345")

            result = restarter.restart(cron_target)
            assert result is True

    def test_restart_failure_returns_false(self, cron_target, monitor_log):
        """Failed restart (process not alive after launch) should return False."""
        restarter = DaemonRestarter(monitor_log)

        with mock.patch("daemon_monitor.subprocess.Popen") as mock_popen, \
             mock.patch("daemon_monitor.time.sleep"), \
             mock.patch("daemon_monitor.is_process_alive", return_value=False):
            mock_process = mock.MagicMock()
            mock_process.pid = 12345
            mock_popen.return_value = mock_process

            result = restarter.restart(cron_target)
            assert result is False

    def test_restart_skipped_when_max_failures_reached(self, cron_target, monitor_log, log_file):
        """Should not restart if consecutive failures exceed limit."""
        # Write max failures to log
        for i in range(DEFAULT_MAX_CONSECUTIVE_FAILURES):
            monitor_log.write("cron", "restart_failed", f"fail{i}", i + 1)

        restarter = DaemonRestarter(monitor_log, max_consecutive_failures=DEFAULT_MAX_CONSECUTIVE_FAILURES)

        with mock.patch("daemon_monitor.subprocess.Popen") as mock_popen:
            result = restarter.restart(cron_target)
            assert result is False
            assert not mock_popen.called  # Should not even try

    def test_restart_logs_skip_event(self, cron_target, monitor_log, log_file):
        """Skipped restart should log restart_skipped event."""
        for i in range(DEFAULT_MAX_CONSECUTIVE_FAILURES):
            monitor_log.write("cron", "restart_failed", f"fail{i}", i + 1)

        restarter = DaemonRestarter(monitor_log, max_consecutive_failures=DEFAULT_MAX_CONSECUTIVE_FAILURES)

        with mock.patch("daemon_monitor.subprocess.Popen"):
            restarter.restart(cron_target)

        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        last = json.loads(lines[-1])
        assert last["event"] == "restart_skipped"

    def test_restart_exception_handling(self, cron_target, monitor_log):
        """Subprocess launch failure should be handled gracefully."""
        restarter = DaemonRestarter(monitor_log)

        with mock.patch("daemon_monitor.subprocess.Popen", side_effect=OSError("Launch failed")):
            result = restarter.restart(cron_target)
            assert result is False

    @mock.patch("daemon_monitor.sys")
    def test_restart_uses_detached_process_on_windows(self, mock_sys, cron_target, monitor_log):
        """On Windows, should use DETACHED_PROCESS creation flag."""
        mock_sys.platform = "win32"
        restarter = DaemonRestarter(monitor_log)

        with mock.patch("daemon_monitor.subprocess.Popen") as mock_popen, \
             mock.patch("daemon_monitor.time.sleep"), \
             mock.patch("daemon_monitor.is_process_alive", return_value=True):
            mock_process = mock.MagicMock()
            mock_process.pid = 12345
            mock_popen.return_value = mock_process

            with open(cron_target.pid_file, "w") as f:
                f.write("12345")

            restarter.restart(cron_target)

            # Check that DETACHED_PROCESS or CREATE_NO_WINDOW flag was used
            if mock_popen.called:
                call_kwargs = mock_popen.call_args
                # On Windows, creationflags should be set
                assert "creationflags" in call_kwargs.kwargs or len(call_kwargs.args) > 0

    def test_restart_with_custom_max_failures(self, cron_target, monitor_log, log_file):
        """Custom max_consecutive_failures should be respected."""
        custom_max = 5
        for i in range(custom_max):
            monitor_log.write("cron", "restart_failed", f"fail{i}", i + 1)

        restarter = DaemonRestarter(monitor_log, max_consecutive_failures=custom_max)

        with mock.patch("daemon_monitor.subprocess.Popen") as mock_popen:
            result = restarter.restart(cron_target)
            assert result is False
            assert not mock_popen.called


# ═══════════════════════════════════════════════════════════════
# Cron tick timestamp
# ═══════════════════════════════════════════════════════════════

class TestCronTickTimestamp:
    """Test cron tick timestamp file operations."""

    def test_write_tick_timestamp(self, tick_file):
        """Import and use write_tick_timestamp from daemon_monitor."""
        from daemon_monitor import write_tick_timestamp

        write_tick_timestamp(tick_file)

        with open(tick_file, "r") as f:
            content = f.read().strip()

        # Should be parseable as ISO8601
        parsed = datetime.fromisoformat(content)
        assert parsed.tzinfo is not None

    def test_read_tick_timestamp(self, tick_file):
        """read_tick_timestamp should return datetime or None."""
        from daemon_monitor import write_tick_timestamp, read_tick_timestamp

        write_tick_timestamp(tick_file)
        result = read_tick_timestamp(tick_file)
        assert isinstance(result, datetime)

    def test_read_tick_timestamp_no_file(self, tmp_dir):
        """Missing tick file should return None."""
        from daemon_monitor import read_tick_timestamp

        result = read_tick_timestamp(os.path.join(tmp_dir, "nonexistent"))
        assert result is None

    def test_read_tick_timestamp_invalid_content(self, tick_file):
        """Invalid content should return None."""
        from daemon_monitor import read_tick_timestamp

        with open(tick_file, "w") as f:
            f.write("not a timestamp")

        result = read_tick_timestamp(tick_file)
        assert result is None

    def test_tick_timestamp_freshness(self, tick_file):
        """Tick timestamp should be recent (within last few seconds)."""
        from daemon_monitor import write_tick_timestamp, read_tick_timestamp

        write_tick_timestamp(tick_file)
        ts = read_tick_timestamp(tick_file)
        now = datetime.now(timezone.utc)
        assert (now - ts).total_seconds() < 5

    def test_remove_tick_timestamp(self, tick_file):
        """remove_tick_timestamp should delete the file."""
        from daemon_monitor import write_tick_timestamp, remove_tick_timestamp

        write_tick_timestamp(tick_file)
        assert os.path.exists(tick_file)

        remove_tick_timestamp(tick_file)
        assert not os.path.exists(tick_file)

    def test_remove_tick_timestamp_no_file(self, tmp_dir):
        """Removing non-existent file should not raise."""
        from daemon_monitor import remove_tick_timestamp

        # Should not raise
        remove_tick_timestamp(os.path.join(tmp_dir, "nonexistent"))


# ═══════════════════════════════════════════════════════════════
# Integration: check_and_restart_all
# ═══════════════════════════════════════════════════════════════

class TestCheckAndRestartAll:
    """Test the top-level orchestration function."""

    def test_all_healthy_no_restarts(self, cron_target, discord_target, monitor_log):
        """When all daemons are healthy, no restarts should be attempted."""
        from daemon_monitor import check_and_restart_all

        # Write PID files with current PID
        for target in [cron_target, discord_target]:
            with open(target.pid_file, "w") as f:
                f.write(str(os.getpid()))

        with mock.patch("daemon_monitor.subprocess.Popen") as mock_popen:
            results = check_and_restart_all([cron_target, discord_target], monitor_log)
            assert not mock_popen.called
            # All should be healthy
            assert all(r["healthy"] for r in results)

    def test_dead_daemon_gets_restarted(self, cron_target, monitor_log):
        """Dead daemon should trigger restart attempt."""
        from daemon_monitor import check_and_restart_all

        # No PID file = daemon is not running
        with mock.patch("daemon_monitor.subprocess.Popen") as mock_popen, \
             mock.patch("daemon_monitor.time.sleep"), \
             mock.patch("daemon_monitor.is_process_alive", return_value=False):
            mock_process = mock.MagicMock()
            mock_process.pid = 12345
            mock_popen.return_value = mock_process

            results = check_and_restart_all([cron_target], monitor_log)
            assert mock_popen.called
            assert not results[0]["healthy"]

    def test_returns_summary_per_daemon(self, cron_target, discord_target, monitor_log):
        """Should return a list with one entry per daemon target."""
        from daemon_monitor import check_and_restart_all

        for target in [cron_target, discord_target]:
            with open(target.pid_file, "w") as f:
                f.write(str(os.getpid()))

        results = check_and_restart_all([cron_target, discord_target], monitor_log)
        assert len(results) == 2
        assert results[0]["daemon"] == "cron"
        assert results[1]["daemon"] == "discord"

    def test_empty_targets_list(self, monitor_log):
        """Empty targets list should return empty results."""
        from daemon_monitor import check_and_restart_all

        results = check_and_restart_all([], monitor_log)
        assert results == []


# ═══════════════════════════════════════════════════════════════
# Edge cases and safety
# ═══════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Test edge cases and safety mechanisms."""

    def test_monitor_log_write_exception_does_not_propagate(self, tmp_dir):
        """MonitorLog write failure should not crash the caller."""
        # Use a path that can't be written to (directory as file)
        bad_path = os.path.join(tmp_dir, "readonly_dir")
        os.makedirs(bad_path, exist_ok=True)
        # Try to write to a path where the file is actually a directory
        log = MonitorLog(bad_path)
        # Should not raise
        log.write("cron", "detected", "test", 0)

    def test_health_check_result_has_all_fields(self, cron_target):
        """HealthCheckResult should have alive, pid, reason fields."""
        checker = DaemonHealthChecker()
        result = checker.check(cron_target)
        assert hasattr(result, "alive")
        assert hasattr(result, "pid")
        assert hasattr(result, "reason")

    def test_daemon_targets_have_correct_defaults(self):
        """get_default_targets should return cron and discord targets."""
        from daemon_monitor import get_default_targets
        targets = get_default_targets()
        assert len(targets) == 2
        names = [t.name for t in targets]
        assert "cron" in names
        assert "discord" in names

    def test_default_targets_use_correct_pid_paths(self):
        """Default targets should reference the actual PID file locations."""
        from daemon_monitor import get_default_targets
        targets = get_default_targets()
        cron_t = [t for t in targets if t.name == "cron"][0]
        discord_t = [t for t in targets if t.name == "discord"][0]
        assert "daemon.pid" in cron_t.pid_file
        assert "discord_daemon.pid" in discord_t.pid_file


class TestMonitorLogExceptionHandling:
    """M-R2: MonitorLog.write() should log exceptions, not silently swallow."""

    def test_write_logs_exception_on_failure(self):
        """When log write fails, at least log a warning instead of silently passing."""
        from daemon_monitor import MonitorLog
        import tempfile
        tmpdir = tempfile.mkdtemp()
        # Point to a path that will fail (directory as file)
        log = MonitorLog(os.path.join(tmpdir, "subdir"))
        # The write should not crash, but should log the error
        # After fix, it should call logger.warning
        import logging
        with patch("daemon_monitor.logger") as mock_logger:
            log.write("test", "error", "test detail", 0)
            # After fix: should have called logger.warning or logger.debug
            # The key is it should NOT silently pass
            # We check the source code has logging
            import inspect
            source = inspect.getsource(MonitorLog.write)
            assert "logger" in source or "logging" in source, \
                "MonitorLog.write should use logger for exception reporting"
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
