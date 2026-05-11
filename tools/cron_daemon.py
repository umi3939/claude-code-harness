#!/usr/bin/env python3
"""Persistent cron daemon for Claude Code.

Runs as a background process (via pythonw.exe or --foreground),
periodically checking for due jobs and executing them via
Claude CLI subprocess.

Usage:
    pythonw cron_daemon.py              # Background (no console window)
    python  cron_daemon.py --foreground # Foreground with console output
    python  cron_daemon.py --stop       # Stop a running daemon

This is a generic Claude Code utility, not part of any specific project.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Optional

# Add the tools directory to sys.path
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from cron_scheduler import (
    CRON_DIR,
    HEARTBEAT_ACTIONS_FILE,
    MAX_CONCURRENT_JOBS,
    MAX_EXECUTIONS_PER_HOUR,
    MAX_MISSED_JOBS_RECOVERY,
    CronJob,
    ExecutionLog,
    ExecutionResult,
    JobExecutor,
    JobRegistry,
    LogEntry,
    NotificationBuffer,
    _ensure_dir,
    _now_iso,
    _parse_iso,
    compute_next_run,
)
from daemon_monitor import CRON_LAST_TICK_FILE, remove_tick_timestamp, write_tick_timestamp

HEARTBEAT_ACTIONS_MAX_LINES = 100


def record_heartbeat_action(actions_file: str, result: ExecutionResult) -> None:
    """Record a heartbeat execution result to JSONL file.

    Args:
        actions_file: Path to heartbeat_actions.jsonl.
        result: The ExecutionResult from the heartbeat job.
    """
    _ensure_dir(os.path.dirname(actions_file))
    entry = {
        "timestamp": _now_iso(),
        "concern": "heartbeat",
        "action_taken": "full_run",
        "result": "success" if result.success else "error",
        "duration_ms": result.duration_ms,
        "output_preview": (result.output or "")[:500],
    }
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with open(actions_file, "a", encoding="utf-8") as f:
        f.write(line)

    prune_heartbeat_actions(actions_file)


def prune_heartbeat_actions(actions_file: str, max_lines: int = HEARTBEAT_ACTIONS_MAX_LINES) -> None:
    """Prune heartbeat actions file if it exceeds max_lines, keeping the latest."""
    try:
        with open(actions_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except (FileNotFoundError, OSError):
        return

    if len(lines) <= max_lines:
        return

    kept = lines[-max_lines:]
    with open(actions_file, "w", encoding="utf-8") as f:
        f.writelines(kept)


# Daemon files
PID_FILE = os.path.join(CRON_DIR, "daemon.pid")
DAEMON_LOG_FILE = os.path.join(CRON_DIR, "daemon.log")

# Tick interval
TICK_INTERVAL_SECONDS = 60

# Execution rate tracking
HOURLY_WINDOW_SECONDS = 3600


def setup_logging(foreground: bool = False) -> logging.Logger:
    """Configure daemon logging."""
    os.makedirs(CRON_DIR, exist_ok=True)
    logger = logging.getLogger("cron_daemon")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler with rotation (5MB max, 2 backup generations)
    file_handler = logging.handlers.RotatingFileHandler(
        DAEMON_LOG_FILE, encoding="utf-8", mode="a",
        maxBytes=5_000_000, backupCount=2,
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Console handler (foreground only)
    if foreground:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger


def write_pid_file() -> None:
    """Write current PID to pid file."""
    os.makedirs(CRON_DIR, exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def remove_pid_file() -> None:
    """Remove PID file."""
    try:
        os.unlink(PID_FILE)
    except OSError:
        pass


def read_pid_file() -> Optional[int]:
    """Read PID from pid file."""
    try:
        with open(PID_FILE, "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def is_process_alive(pid: int) -> bool:
    """Check if a process with given PID is running."""
    if sys.platform == "win32":
        import ctypes
        # SYNCHRONIZE (0x00100000) access right - sufficient to check existence
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


def is_pid_stale(pid: int, max_age_seconds: int = 300) -> bool:
    """Check if a PID file is stale (process dead or PID file too old).

    Args:
        pid: The process ID to check.
        max_age_seconds: Max age of PID file before considered stale.

    Returns:
        True if the PID is stale (process not running), False if alive.
    """
    if not is_process_alive(pid):
        return True

    # Check PID file age as additional safety
    try:
        stat = os.stat(PID_FILE)
        age = time.time() - stat.st_mtime
        if age > max_age_seconds:
            # PID file is very old but process exists - possible PID reuse
            # Log warning but don't treat as stale (process IS alive)
            pass
    except OSError:
        pass

    return False


# Job lock file path
JOB_LOCK_FILE = os.path.join(CRON_DIR, "job_execution.lock")


def _acquire_job_lock():
    """Acquire a file-based lock for job execution.

    Returns a context manager that holds the lock.
    _concurrent_jobs counter must be modified only within this lock.
    """
    import contextlib

    @contextlib.contextmanager
    def _lock():
        os.makedirs(CRON_DIR, exist_ok=True)
        lock_path = JOB_LOCK_FILE
        fd = None
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
            if sys.platform == "win32":
                import msvcrt
                for _ in range(100):
                    try:
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        time.sleep(0.01)
                else:
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            if fd is not None:
                if sys.platform == "win32":
                    import msvcrt
                    try:
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    return _lock()


class CronDaemon:
    """Main daemon class managing the job execution loop."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.registry = JobRegistry()
        self.executor = JobExecutor(registry=self.registry)
        self.log = ExecutionLog()
        self.notifications = NotificationBuffer()
        self._running = False
        self._execution_timestamps: list[float] = []  # For rate limiting
        # Concurrent job counter: reserved for future async execution support.
        # Currently, job execution is synchronous (subprocess.run in tick loop),
        # so this counter will only ever be 0 or 1. The MAX_CONCURRENT_JOBS
        # guard is a no-op in the current architecture but will become active
        # when job execution is moved to a thread/process pool.
        self._concurrent_jobs = 0
        self._job_lock_path = JOB_LOCK_FILE

    def startup_recovery(self) -> None:
        """Perform startup self-repair:
        1. Clear stale running_at markers
        2. Detect and run missed jobs (up to limit)
        """
        self.logger.info("Performing startup recovery...")

        jobs = self.registry.list_all()
        cleared = 0
        for job in jobs:
            if job.running_at:
                self.registry.update(job.id, {"running_at": None})
                cleared += 1

        if cleared:
            self.logger.info(f"Cleared {cleared} stale running markers")

        # Detect missed jobs
        now = datetime.now(timezone.utc)
        missed = []
        for job in self.registry.list_enabled():
            if not job.next_run:
                continue
            next_dt = _parse_iso(job.next_run)
            if next_dt and next_dt < now:
                missed.append(job)

        if missed:
            self.logger.info(f"Found {len(missed)} missed jobs")
            run_count = min(len(missed), MAX_MISSED_JOBS_RECOVERY)
            for job in missed[:run_count]:
                self.logger.info(f"Running missed job: {job.name} (id: {job.id})")
                self._execute_single_job(job)

            # For remaining missed jobs, just recompute next_run
            for job in missed[run_count:]:
                next_run = compute_next_run(job.schedule, timezone_str=job.timezone)
                self.registry.update(job.id, {"next_run": next_run})
                self.logger.info(f"Rescheduled missed job: {job.name} -> {next_run}")

    def _check_rate_limit(self) -> bool:
        """Check if we're within the hourly execution rate limit."""
        now = time.time()
        cutoff = now - HOURLY_WINDOW_SECONDS
        self._execution_timestamps = [
            t for t in self._execution_timestamps if t > cutoff
        ]
        return len(self._execution_timestamps) < MAX_EXECUTIONS_PER_HOUR

    def _record_execution(self) -> None:
        """Record an execution timestamp for rate limiting."""
        self._execution_timestamps.append(time.time())

    def _execute_single_job(self, job: CronJob) -> None:
        """Execute a single job with all safety checks.

        Heartbeat preprocessing is handled by JobExecutor.execute_job.
        """
        # Skip checks
        skip_reason = self.executor.should_skip(job)
        if skip_reason:
            self.logger.debug(f"Skipping job {job.name}: {skip_reason}")
            if skip_reason == "ttl_expired":
                self.registry.update(job.id, {"enabled": False})
                self.notifications.add_notification(
                    job.id, job.name, "skip",
                    "Job disabled: TTL expired"
                )
                self.logger.info(f"Job {job.name} disabled: TTL expired")
            return

        # Rate limit
        if not self._check_rate_limit():
            self.logger.warning(f"Rate limit reached ({MAX_EXECUTIONS_PER_HOUR}/hour), skipping job {job.name}")
            return

        # Concurrency limit (file-based lock for multi-instance safety)
        with _acquire_job_lock():
            if self._concurrent_jobs >= MAX_CONCURRENT_JOBS:
                self.logger.warning(f"Concurrent limit reached ({MAX_CONCURRENT_JOBS}), skipping job {job.name}")
                return
            # SAFETY: always acquire lock before modifying _concurrent_jobs
            self._concurrent_jobs += 1

        self.logger.info(f"Executing job: {job.name} (id: {job.id})")

        # Set running marker
        self.registry.update(job.id, {"running_at": _now_iso()})

        try:
            result = self.executor.execute_job(job)

            # Handle heartbeat skip (preprocessing decided not to execute)
            if result.skipped:
                self.logger.info(f"Job {job.name} skipped: {result.skip_reason}")
                self.log.append(LogEntry(
                    job_id=job.id,
                    job_name=job.name,
                    status="skip",
                    error=result.skip_reason if "error" in result.skip_reason.lower() or "blocked" in result.skip_reason.lower() else "",
                    output_summary=result.skip_reason,
                ))
                # Notify on errors/blocks
                if "error" in result.skip_reason.lower() or "blocked" in result.skip_reason.lower():
                    self.notifications.add_notification(
                        job.id, job.name, "error", result.skip_reason
                    )
                # Clear running marker
                self.registry.update(job.id, {"running_at": None})
                return

            self._record_execution()

            # Apply result
            updates = self.executor.apply_result(job, result)
            # Extract delete_after_run marker before passing to registry
            should_delete = updates.pop("_delete_after_run", False)
            self.registry.update(job.id, updates)
            # Auto-delete if marked
            if should_delete:
                try:
                    self.registry.remove(job.id)
                    self.logger.info(f"Job {job.name} auto-deleted (delete_after_run)")
                except Exception as del_e:
                    self.logger.error(f"delete_after_run failed for {job.name}: {del_e}")
                    self.log.append(LogEntry(
                        job_id=job.id,
                        job_name=job.name,
                        status="error",
                        error=f"delete_after_run failed: {del_e}",
                    ))
                    self.notifications.add_notification(
                        job.id, job.name, "error",
                        f"delete_after_run failed: {del_e}"
                    )

            # Log
            self.log.append(LogEntry(
                job_id=job.id,
                job_name=job.name,
                status="success" if result.success else "error",
                error=result.error,
                duration_ms=result.duration_ms,
                output_summary=result.output[:2000] if result.output else "",
            ))

            # Record heartbeat action history
            if job.type == "heartbeat":
                try:
                    record_heartbeat_action(HEARTBEAT_ACTIONS_FILE, result)
                except Exception as e:
                    self.logger.warning(f"Failed to record heartbeat action: {e}")

            if result.success:
                self.logger.info(
                    f"Job {job.name} completed successfully ({result.duration_ms}ms)"
                )
            else:
                self.logger.warning(
                    f"Job {job.name} failed: {result.error[:200]}"
                )
                new_errors = updates.get("consecutive_errors", 0)
                self.notifications.add_notification(
                    job.id, job.name, "error",
                    f"Job failed ({new_errors} consecutive): {result.error[:200]}"
                )

                if not updates.get("enabled", True):
                    self.logger.warning(
                        f"Job {job.name} auto-disabled after {new_errors} consecutive errors"
                    )
                    self.notifications.add_notification(
                        job.id, job.name, "error",
                        f"Job auto-disabled after {new_errors} consecutive errors"
                    )

        except Exception as e:
            self.logger.error(f"Unexpected error executing job {job.name}: {e}")
            self.registry.update(job.id, {"running_at": None})
        finally:
            with _acquire_job_lock():
                # SAFETY: always acquire lock before modifying _concurrent_jobs
                self._concurrent_jobs -= 1

    def tick(self) -> None:
        """One tick of the main loop: check all jobs and execute due ones."""
        now = datetime.now(timezone.utc)

        try:
            enabled_jobs = self.registry.list_enabled()
        except Exception as e:
            self.logger.error(f"Failed to load jobs: {e}")
            return

        for job in enabled_jobs:
            if not job.next_run:
                # Compute initial next_run
                next_run = compute_next_run(job.schedule, timezone_str=job.timezone)
                if next_run:
                    self.registry.update(job.id, {"next_run": next_run})
                continue

            next_dt = _parse_iso(job.next_run)
            if next_dt is None:
                # Invalid next_run, recompute
                next_run = compute_next_run(job.schedule, timezone_str=job.timezone)
                self.registry.update(job.id, {"next_run": next_run})
                continue

            if next_dt <= now:
                # Job is due
                self._execute_single_job(job)

        # Write tick timestamp for external liveness detection
        try:
            write_tick_timestamp(CRON_LAST_TICK_FILE)
        except Exception as e:
            self.logger.debug(f"Tick timestamp write failed: {e}")

    async def run_loop(self) -> None:
        """Main async loop."""
        self._running = True
        self.logger.info(f"Daemon loop started (tick interval: {TICK_INTERVAL_SECONDS}s)")

        while self._running:
            try:
                self.tick()
            except Exception as e:
                self.logger.error(f"Tick error: {e}")

            # Wait for next tick
            try:
                await asyncio.sleep(TICK_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                break

        self.logger.info("Daemon loop stopped")

    def stop(self) -> None:
        """Signal the loop to stop."""
        self._running = False


def stop_daemon() -> int:
    """Stop a running daemon by PID. Returns exit code."""
    pid = read_pid_file()
    if pid is None:
        print("No daemon PID file found.", file=sys.stderr)
        return 1

    if not is_process_alive(pid):
        print(f"Daemon (pid {pid}) is not running. Cleaning up PID file.", file=sys.stderr)
        remove_pid_file()
        return 0

    print(f"Stopping daemon (pid {pid})...", file=sys.stderr)
    try:
        if sys.platform == "win32":
            # Windows: verify the PID belongs to a cron_daemon process
            # before terminating to avoid killing a recycled PID
            import ctypes
            import subprocess as _sp

            is_daemon = False
            try:
                # Use wmic to check the process command line
                import shutil as _shutil
                _wmic_path = _shutil.which("wmic") or "wmic"
                result = _sp.run(
                    [_wmic_path, "process", "where", f"ProcessId={pid}",
                     "get", "CommandLine", "/VALUE"],
                    capture_output=True, text=True, timeout=5,
                )
                cmdline = result.stdout if result.returncode == 0 else ""
                if "cron_daemon" in cmdline:
                    is_daemon = True
            except Exception as e:
                print(f"PID check warning: {e}", file=sys.stderr)

            if not is_daemon:
                # Fallback: check PID file freshness vs process creation
                try:
                    pid_mtime = os.path.getmtime(PID_FILE)
                    # If PID file was written very recently (within 2x tick interval),
                    # trust it more, but still warn
                    age = time.time() - pid_mtime
                    if age < TICK_INTERVAL_SECONDS * 2:
                        is_daemon = True
                    else:
                        print(
                            f"WARNING: Cannot verify PID {pid} is a cron_daemon process. "
                            f"PID file age: {age:.0f}s. Refusing to kill -- "
                            f"remove {PID_FILE} manually if stale.",
                            file=sys.stderr,
                        )
                        return 1
                except OSError:
                    print(
                        f"WARNING: Cannot verify PID {pid} is a cron_daemon process. "
                        f"Refusing to kill. Remove {PID_FILE} manually if stale.",
                        file=sys.stderr,
                    )
                    return 1

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(1, False, pid)
            if handle:
                kernel32.TerminateProcess(handle, 0)
                kernel32.CloseHandle(handle)
            else:
                os.kill(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)

        # Wait for process to die
        for _ in range(10):
            time.sleep(0.5)
            if not is_process_alive(pid):
                break

        if is_process_alive(pid):
            print("Daemon did not stop. Forcing kill.", file=sys.stderr)
            try:
                if sys.platform == "win32":
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    # PROCESS_TERMINATE (0x0001)
                    handle = kernel32.OpenProcess(0x0001, False, pid)
                    if handle:
                        kernel32.TerminateProcess(handle, 1)
                        kernel32.CloseHandle(handle)
                else:
                    os.kill(pid, signal.SIGKILL)
            except (OSError, AttributeError):
                pass

        remove_pid_file()
        print("Daemon stopped.", file=sys.stderr)
        return 0

    except Exception as e:
        print(f"Error stopping daemon: {e}", file=sys.stderr)
        return 1


async def async_main(foreground: bool = False) -> None:
    """Async entry point for the daemon."""
    logger = setup_logging(foreground=foreground)

    # Check for already running instance
    existing_pid = read_pid_file()
    if existing_pid:
        if is_process_alive(existing_pid) and not is_pid_stale(existing_pid):
            logger.error(f"Daemon already running (pid {existing_pid}). Exiting.")
            sys.exit(1)
        else:
            logger.info(f"Stale PID file found (pid {existing_pid}), removing.")
            remove_pid_file()

    # Write PID file
    write_pid_file()
    logger.info(f"Daemon starting (pid {os.getpid()})")

    daemon = CronDaemon(logger)

    # Signal handling for graceful shutdown
    shutdown_event = asyncio.Event()

    def handle_signal(sig, frame):
        logger.info(f"Received signal {sig}, shutting down...")
        daemon.stop()
        shutdown_event.set()

    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)
    else:
        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

    try:
        # Startup recovery
        daemon.startup_recovery()

        # Run main loop
        loop_task = asyncio.create_task(daemon.run_loop())

        # Wait for shutdown
        await shutdown_event.wait()
        daemon.stop()
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt, shutting down...")
        daemon.stop()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        remove_pid_file()
        remove_tick_timestamp(CRON_LAST_TICK_FILE)
        logger.info("Daemon stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="Persistent cron daemon for Claude Code")
    parser.add_argument(
        "--foreground", action="store_true",
        help="Run in foreground with console output"
    )
    parser.add_argument(
        "--stop", action="store_true",
        help="Stop a running daemon"
    )
    args = parser.parse_args()

    if args.stop:
        sys.exit(stop_daemon())

    asyncio.run(async_main(foreground=args.foreground))


if __name__ == "__main__":
    main()
