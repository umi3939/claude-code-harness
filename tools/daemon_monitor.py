#!/usr/bin/env python3
"""Daemon monitoring and auto-recovery for Claude Code.

Provides health checking, automatic restart, and structured logging
for the Cron and Discord daemons. Designed to be called from Heartbeat
or manually from Claude CLI.

This is a generic Claude Code utility, not part of any specific project.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)
from datetime import datetime, timezone
from typing import List, Optional

# Add the tools directory to sys.path
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from cron_scheduler import CRON_DIR
from discord_receiver import DISCORD_DATA_DIR

# ═══════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════

MONITOR_LOG_FILE = os.path.join(DISCORD_DATA_DIR, "daemon_monitor_log.jsonl")
CRON_LAST_TICK_FILE = os.path.join(CRON_DIR, ".cron-last-tick")

DEFAULT_MAX_CONSECUTIVE_FAILURES = 3
MONITOR_LOG_MAX_LINES = 100

# PID file freshness threshold (seconds). If PID file is older than this,
# trigger cmdline verification to detect PID reuse.
PID_FRESHNESS_THRESHOLD = 300  # 5 minutes

# Wait time after launching subprocess before checking if it's alive
RESTART_WAIT_SECONDS = 3


# ═══════════════════════════════════════════════════════════════
# Unified is_process_alive (consolidates duplicates from both daemons)
# ═══════════════════════════════════════════════════════════════

def is_process_alive(pid: int) -> bool:
    """Check if a process with given PID is running.

    Unified implementation that can be imported by other modules,
    replacing the duplicated versions in cron_daemon.py and discord_daemon.py.
    """
    if pid <= 0:
        return False

    if sys.platform == "win32":
        import ctypes
        SYNCHRONIZE = 0x00100000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


# ═══════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════

@dataclass
class DaemonTarget:
    """Definition of a monitored daemon."""
    name: str
    pid_file: str
    start_command: List[str]
    cmdline_keyword: str


@dataclass
class HealthCheckResult:
    """Result of a health check on a single daemon."""
    alive: bool
    pid: Optional[int]
    reason: str


# ═══════════════════════════════════════════════════════════════
# Tick timestamp helpers (for Cron daemon self-diagnosis)
# ═══════════════════════════════════════════════════════════════

def write_tick_timestamp(path: str) -> None:
    """Write current UTC time as ISO8601 to the tick timestamp file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    with open(path, "w", encoding="utf-8") as f:
        f.write(ts)


def read_tick_timestamp(path: str) -> Optional[datetime]:
    """Read the tick timestamp file. Returns datetime or None."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return None
        return datetime.fromisoformat(content)
    except (FileNotFoundError, OSError, ValueError):
        return None


def remove_tick_timestamp(path: str) -> None:
    """Remove the tick timestamp file (called on graceful stop)."""
    try:
        os.unlink(path)
    except OSError:
        pass


# ═══════════════════════════════════════════════════════════════
# MonitorLog -- JSONL structured logging
# ═══════════════════════════════════════════════════════════════

class MonitorLog:
    """Structured JSONL log for daemon monitoring events."""

    def __init__(self, log_file: str = MONITOR_LOG_FILE):
        self._log_file = log_file

    def write(self, daemon: str, event: str, detail: str,
              consecutive_failures: int) -> None:
        """Append a log entry.

        Args:
            daemon: Daemon name (e.g., "cron", "discord").
            event: Event type (detected/restart_success/restart_failed/restart_skipped).
            detail: Human-readable detail.
            consecutive_failures: Current consecutive failure count.
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "daemon": daemon,
            "event": event,
            "detail": detail,
            "consecutive_failures": consecutive_failures,
        }
        try:
            os.makedirs(os.path.dirname(self._log_file), exist_ok=True)
            line = json.dumps(entry, ensure_ascii=False) + "\n"
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(line)
            self._prune()
        except (OSError, TypeError) as e:
            # Log write failure should not crash the caller, but log it
            logger.warning("MonitorLog.write failed: %s", e)

    def _prune(self) -> None:
        """Prune log to MONITOR_LOG_MAX_LINES, keeping the latest entries."""
        try:
            with open(self._log_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) <= MONITOR_LOG_MAX_LINES:
                return
            kept = lines[-MONITOR_LOG_MAX_LINES:]
            with open(self._log_file, "w", encoding="utf-8") as f:
                f.writelines(kept)
        except (FileNotFoundError, OSError):
            pass

    def count_consecutive_failures(self, daemon: str) -> int:
        """Count consecutive restart_failed events for a daemon from the log.

        Scans from the end of the log backwards. Stops at the first
        non-failure event (restart_success or restart_skipped) for this daemon.
        'detected' events are ignored (they don't reset the count).
        """
        try:
            with open(self._log_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except (FileNotFoundError, OSError):
            return 0

        count = 0
        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if entry.get("daemon") != daemon:
                continue
            event = entry.get("event", "")
            if event == "restart_failed":
                count += 1
            elif event == "detected":
                # detected events don't affect the failure count
                continue
            else:
                # restart_success, restart_skipped, or unknown -> break
                break

        return count


# ═══════════════════════════════════════════════════════════════
# DaemonHealthChecker
# ═══════════════════════════════════════════════════════════════

class DaemonHealthChecker:
    """Check if a daemon is alive by reading its PID file and verifying the process."""

    def check(self, target: DaemonTarget) -> HealthCheckResult:
        """Perform a health check on the given daemon target.

        Steps:
        1. Read PID file
        2. Check process existence via OS API
        3. If PID file is stale, perform freshness check
        """
        # Step 1: Read PID file
        pid = self._read_pid(target.pid_file)
        if pid is None:
            return HealthCheckResult(
                alive=False,
                pid=None,
                reason="No PID file or invalid PID",
            )

        # Step 2: Check process existence
        if not is_process_alive(pid):
            return HealthCheckResult(
                alive=False,
                pid=pid,
                reason=f"Process {pid} not running",
            )

        # Step 3: PID file freshness check
        try:
            pid_mtime = os.path.getmtime(target.pid_file)
            age = time.time() - pid_mtime
            if age > PID_FRESHNESS_THRESHOLD:
                # PID file is old -- verify via cmdline keyword
                if not self._verify_cmdline(pid, target.cmdline_keyword):
                    return HealthCheckResult(
                        alive=False,
                        pid=pid,
                        reason=f"PID {pid} alive but cmdline does not match "
                               f"'{target.cmdline_keyword}' (possible PID reuse)",
                    )
        except OSError:
            pass  # Can't check mtime, trust the PID

        return HealthCheckResult(
            alive=True,
            pid=pid,
            reason="Healthy",
        )

    def _read_pid(self, pid_file: str) -> Optional[int]:
        """Read and parse PID from file."""
        try:
            with open(pid_file, "r") as f:
                content = f.read().strip()
            if not content:
                return None
            return int(content)
        except (FileNotFoundError, ValueError, OSError):
            return None

    def _verify_cmdline(self, pid: int, keyword: str) -> bool:
        """Verify that the process command line contains the expected keyword.

        Falls back to True if verification is not possible (e.g., wmic unavailable).
        """
        if sys.platform == "win32":
            try:
                result = subprocess.run(
                    ["wmic", "process", "where", f"ProcessId={pid}",  # noqa: S607
                     "get", "CommandLine", "/VALUE"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0 and keyword in result.stdout:
                    return True
                if result.returncode == 0 and keyword not in result.stdout:
                    return False
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass
            # Fallback: can't verify, trust the PID
            return True
        else:
            try:
                with open(f"/proc/{pid}/cmdline", "r") as f:
                    cmdline = f.read()
                return keyword in cmdline
            except (FileNotFoundError, OSError):
                # Can't verify, trust the PID
                return True


# ═══════════════════════════════════════════════════════════════
# DaemonRestarter
# ═══════════════════════════════════════════════════════════════

class DaemonRestarter:
    """Restart a dead daemon as a detached subprocess."""

    def __init__(self, monitor_log: MonitorLog,
                 max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES):
        self._log = monitor_log
        self._max_failures = max_consecutive_failures

    def restart(self, target: DaemonTarget) -> bool:
        """Attempt to restart a daemon.

        Returns True if restart succeeded, False otherwise.
        Skips restart if consecutive failures exceed the limit.
        """
        # Check consecutive failure count
        failures = self._log.count_consecutive_failures(target.name)
        if failures >= self._max_failures:
            self._log.write(
                target.name, "restart_skipped",
                f"Consecutive failures ({failures}) >= limit ({self._max_failures})",
                failures,
            )
            return False

        # Attempt restart
        try:
            kwargs = {}
            if sys.platform == "win32":
                # DETACHED_PROCESS = 0x00000008
                # CREATE_NO_WINDOW = 0x08000000
                kwargs["creationflags"] = 0x00000008 | 0x08000000
            else:
                kwargs["start_new_session"] = True

            subprocess.Popen(
                target.start_command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **kwargs,
            )
        except (OSError, subprocess.SubprocessError) as e:
            new_failures = failures + 1
            self._log.write(
                target.name, "restart_failed",
                f"Failed to launch: {e}",
                new_failures,
            )
            return False

        # Wait and verify
        time.sleep(RESTART_WAIT_SECONDS)

        # Check if the daemon wrote a new PID file and is alive
        pid = self._read_pid(target.pid_file)
        if pid is not None and is_process_alive(pid):
            self._log.write(
                target.name, "restart_success",
                f"Restarted successfully (new PID: {pid})",
                0,
            )
            return True
        else:
            new_failures = failures + 1
            self._log.write(
                target.name, "restart_failed",
                f"Launched but daemon not alive after {RESTART_WAIT_SECONDS}s wait",
                new_failures,
            )
            return False

    def _read_pid(self, pid_file: str) -> Optional[int]:
        """Read PID from file."""
        try:
            with open(pid_file, "r") as f:
                return int(f.read().strip())
        except (FileNotFoundError, ValueError, OSError):
            return None


# ═══════════════════════════════════════════════════════════════
# Default targets
# ═══════════════════════════════════════════════════════════════

def get_default_targets() -> List[DaemonTarget]:
    """Return the default list of daemon targets to monitor."""
    cron_pid = os.path.join(CRON_DIR, "daemon.pid")
    discord_pid = os.path.join(DISCORD_DATA_DIR, "discord_daemon.pid")

    return [
        DaemonTarget(
            name="cron",
            pid_file=cron_pid,
            start_command=["pythonw", os.path.join(TOOLS_DIR, "cron_daemon.py")],
            cmdline_keyword="cron_daemon",
        ),
        DaemonTarget(
            name="discord",
            pid_file=discord_pid,
            start_command=["pythonw", os.path.join(TOOLS_DIR, "discord_daemon.py")],
            cmdline_keyword="discord_daemon",
        ),
    ]


# ═══════════════════════════════════════════════════════════════
# Orchestration: check_and_restart_all
# ═══════════════════════════════════════════════════════════════

def check_and_restart_all(
    targets: List[DaemonTarget],
    monitor_log: MonitorLog,
    max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
) -> List[dict]:
    """Check health of all targets and restart any dead daemons.

    Returns a list of dicts with per-daemon summary:
    [{"daemon": str, "healthy": bool, "restarted": bool, "detail": str}, ...]
    """
    checker = DaemonHealthChecker()
    restarter = DaemonRestarter(monitor_log, max_consecutive_failures)
    results = []

    for target in targets:
        health = checker.check(target)

        if health.alive:
            results.append({
                "daemon": target.name,
                "healthy": True,
                "restarted": False,
                "detail": health.reason,
            })
        else:
            # Log detection
            monitor_log.write(
                target.name, "detected",
                health.reason,
                monitor_log.count_consecutive_failures(target.name),
            )

            # Attempt restart
            success = restarter.restart(target)
            results.append({
                "daemon": target.name,
                "healthy": success,
                "restarted": success,
                "detail": health.reason if not success else "Restarted",
            })

    return results
