#!/usr/bin/env python3
"""MCP server for persistent cron/heartbeat job scheduling.

Provides tools for managing scheduled jobs that run outside of
interactive sessions. Uses cron_scheduler.py as the core engine.

This is a generic Claude Code utility, not part of any specific project.

IMPORTANT: For stdio transport, never print() to stdout.
Use print(..., file=sys.stderr) for debug logging.
"""

import io
import json
import os
import sys
import threading

# Ensure UTF-8 stderr on Windows
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace"
    )

# Add the tools directory to sys.path
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from cron_scheduler import (
    CRON_DIR,
    CronJob,
    ExecutionLog,
    JobExecutor,
    JobRegistry,
    LogEntry,
    NotificationBuffer,
    _now_iso,
)
from mcp.server.fastmcp import FastMCP

# Prompt length limit to mitigate prompt injection risk
MAX_PROMPT_LENGTH = 10000

# Maximum relative time: 7 days in seconds
MAX_RELATIVE_SECONDS = 7 * 24 * 3600


def parse_relative_time(value: str) -> int:
    """Parse a relative time string like '20m', '2h', '30s' into seconds.

    Accepts single-unit formats only: <integer><unit>
    Units: s (seconds), m (minutes), h (hours)

    Returns integer seconds. Raises ValueError on invalid input.
    """
    if not value or not isinstance(value, str):
        raise ValueError("Invalid relative time: empty or not a string")

    value = value.strip()
    if not value:
        raise ValueError("Invalid relative time: empty string")

    unit_map = {"s": 1, "m": 60, "h": 3600}
    unit = value[-1].lower()
    if unit not in unit_map:
        raise ValueError(
            f"Invalid relative time unit '{unit}' in '{value}'. "
            f"Use 's' (seconds), 'm' (minutes), or 'h' (hours)"
        )

    num_str = value[:-1]
    if not num_str:
        raise ValueError(f"Invalid relative time: no numeric value in '{value}'")

    try:
        num = int(num_str)
    except ValueError as e:
        raise ValueError(f"Invalid relative time: '{num_str}' is not an integer") from e

    if num <= 0:
        raise ValueError(f"Invalid relative time: value must be positive, got {num}")

    total_seconds = num * unit_map[unit]
    if total_seconds > MAX_RELATIVE_SECONDS:
        raise ValueError(
            f"Relative time {value} ({total_seconds}s) exceeds maximum "
            f"({MAX_RELATIVE_SECONDS}s / {MAX_RELATIVE_SECONDS // 3600}h). "
            f"Use schedule_type='at' with an ISO-8601 datetime for longer durations."
        )

    return total_seconds

# Initialize MCP server
mcp = FastMCP("persistent-cron")

# Shared instances (initialized once per server process)
_registry = JobRegistry()
_executor = JobExecutor(registry=_registry)
_log = ExecutionLog()
_notifications = NotificationBuffer()
_registry_lock = threading.Lock()

# PID file for daemon status check
PID_FILE = os.path.join(CRON_DIR, "daemon.pid")
DAEMON_LOG_FILE = os.path.join(CRON_DIR, "daemon.log")


def _check_daemon_running() -> dict:
    """Check if the daemon process is running."""
    if not os.path.exists(PID_FILE):
        return {"running": False, "pid": None, "reason": "no pid file"}
    try:
        with open(PID_FILE, "r") as f:
            pid = int(f.read().strip())
    except (ValueError, OSError):
        return {"running": False, "pid": None, "reason": "invalid pid file"}

    # Check if process is alive
    try:
        if sys.platform == "win32":
            import ctypes
            SYNCHRONIZE = 0x00100000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return {"running": True, "pid": pid}
            return {"running": False, "pid": pid, "reason": "process not found"}
        else:
            os.kill(pid, 0)
            return {"running": True, "pid": pid}
    except OSError:
        return {"running": False, "pid": pid, "reason": "process not found"}


def _format_job(job: CronJob) -> dict:
    """Format a job for display."""
    d = job.to_dict()
    # Add human-readable status
    if not job.enabled:
        d["status_display"] = "disabled"
    elif job.running_at:
        d["status_display"] = "running"
    elif job.next_run:
        d["status_display"] = f"scheduled: {job.next_run}"
    else:
        d["status_display"] = "no schedule"
    return d


# ═══════════════════════════════════════════════════════════════
# MCP Tools (8 tools)
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
def persistent_cron_add(
    name: str,
    prompt: str,
    schedule_type: str = "",
    schedule_value: str = "",
    description: str = "",
    cwd: str = "",
    one_shot: bool = False,
    ttl: str = "",
    active_hours_start: str = "",
    active_hours_end: str = "",
    timeout_seconds: int = 300,
    job_type: str = "standard",
    permission_mode: str = "bypassPermissions",
    at: str = "",
    delete_after_run: bool = False,
    timezone: str = "",
    jitter_seconds: int = 0,
) -> str:
    """Register a new persistent cron job.

    Jobs persist across sessions and run via the cron daemon.

    Args:
        name: Human-readable job name
        prompt: The prompt string to execute via Claude CLI.
               For heartbeat jobs, this is the path to HEARTBEAT.md.
        schedule_type: One of "at", "every", or "cron". Not needed if 'at' (relative) is specified.
        schedule_value: For "at": ISO-8601 datetime. For "every": interval in seconds.
                       For "cron": cron expression (e.g. "*/5 * * * *"). Not needed if 'at' (relative) is specified.
        description: Optional job description
        cwd: Working directory for job execution (default: user home)
        one_shot: If true, job is disabled after one successful run
        ttl: Optional ISO-8601 expiry datetime (job auto-disables after this)
        active_hours_start: Optional active window start (HH:MM, e.g. "08:00")
        active_hours_end: Optional active window end (HH:MM, e.g. "23:00")
        timeout_seconds: Job execution timeout (default 300)
        job_type: "standard" or "heartbeat" (heartbeat reads HEARTBEAT.md and preprocesses)
        permission_mode: CLI permission mode ("bypassPermissions" or "plan"). Heartbeat jobs should use "plan"
        at: Relative time shorthand (e.g. "20m", "2h", "30s"). Creates a one-shot job
            at current_time + specified duration. Overrides schedule_type/schedule_value.
            Implies one_shot=True and delete_after_run=True by default.
        delete_after_run: If true, job is permanently removed after successful execution.
                         Requires one_shot=True (error if one_shot=False and delete_after_run=True).
                         Defaults to True when 'at' parameter is used.
        timezone: IANA timezone name (e.g. "Asia/Tokyo", "America/New_York").
                 Affects cron expression evaluation and active_hours.
                 Empty string = UTC (default). Validated on registration.
        jitter_seconds: Random offset range in seconds added to next_run for load spreading.
                       0 = no jitter (default). Must be non-negative and <= 3600.
    """
    try:
        # Prompt length validation
        if len(prompt) > MAX_PROMPT_LENGTH:
            return f"ERROR: Prompt too long ({len(prompt)} chars). Maximum is {MAX_PROMPT_LENGTH} chars."

        # Handle relative time 'at' parameter
        relative_original = ""
        if at:
            try:
                delta_seconds = parse_relative_time(at)
            except ValueError as e:
                return f"ERROR: {e}"

            from datetime import datetime as dt_cls
            from datetime import timedelta as td_cls
            from datetime import timezone as tz_cls
            future_time = dt_cls.now(tz_cls.utc) + td_cls(seconds=delta_seconds)
            schedule_type = "at"
            schedule_value = future_time.isoformat()
            relative_original = at
            # Implicit defaults for relative time
            one_shot = True
            if not delete_after_run:
                # Default to True for 'at' parameter unless explicitly set to False
                delete_after_run = True

        # Validate that either 'at' or schedule_type/schedule_value is provided
        if not schedule_type or not schedule_value:
            return "ERROR: Either 'at' (relative time) or both 'schedule_type' and 'schedule_value' must be provided."

        # Validate delete_after_run + one_shot combination
        if delete_after_run and not one_shot:
            return "ERROR: delete_after_run=True requires one_shot=True. A recurring job cannot be deleted after each run."

        # Build schedule dict
        schedule = {"type": schedule_type}
        if schedule_type == "at":
            schedule["datetime"] = schedule_value
        elif schedule_type == "every":
            try:
                schedule["interval_seconds"] = int(schedule_value)
            except ValueError:
                return f"ERROR: schedule_value must be an integer for 'every' type, got: {schedule_value}"
            schedule["anchor"] = _now_iso()
        elif schedule_type == "cron":
            schedule["expression"] = schedule_value
        else:
            return f"ERROR: Unknown schedule_type: {schedule_type}. Use 'at', 'every', or 'cron'"

        # Build active_hours
        active_hours = None
        if active_hours_start and active_hours_end:
            active_hours = {"start": active_hours_start, "end": active_hours_end}

        # Validate job_type
        if job_type not in ("standard", "heartbeat"):
            return f"ERROR: job_type must be 'standard' or 'heartbeat', got: {job_type}"

        # Validate permission_mode
        if permission_mode not in ("bypassPermissions", "plan", "default"):
            return f"ERROR: permission_mode must be 'bypassPermissions', 'plan', or 'default', got: {permission_mode}"

        # Validate timezone
        if timezone:
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(timezone)
            except (KeyError, Exception) as e:
                return f"ERROR: Invalid timezone '{timezone}': {e}"

        # Validate jitter_seconds (external-untrusted input)
        if not isinstance(jitter_seconds, int) or jitter_seconds < 0:
            return "ERROR: jitter_seconds must be a non-negative integer."
        if jitter_seconds > 3600:
            return "ERROR: jitter_seconds must be <= 3600 (1 hour)."

        job = CronJob(
            name=name,
            description=description,
            prompt=prompt,
            schedule=schedule,
            cwd=cwd or "",
            one_shot=one_shot,
            ttl=ttl or None,
            active_hours=active_hours,
            timeout_seconds=timeout_seconds,
            type=job_type,
            permission_mode=permission_mode,
            delete_after_run=delete_after_run,
            timezone=timezone,
            jitter_seconds=jitter_seconds,
        )

        result = _registry.add(job)

        output = [
            "Job registered successfully.",
            f"  ID: {result.id}",
            f"  Name: {result.name}",
        ]
        if relative_original:
            output.append(f"  Schedule: in {relative_original} (at {schedule_value})")
        else:
            output.append(f"  Schedule: {schedule_type} = {schedule_value}")
        if result.next_run:
            output.append(f"  Next run: {result.next_run}")
        if result.ttl:
            output.append(f"  TTL: {result.ttl}")
        if active_hours:
            output.append(f"  Active hours: {active_hours_start} - {active_hours_end}")
        if timezone:
            output.append(f"  Timezone: {timezone}")
        if delete_after_run:
            output.append("  Auto-delete after run: yes")
        if jitter_seconds > 0:
            output.append(f"  Jitter: up to {jitter_seconds}s")

        return "\n".join(output)

    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def persistent_cron_list(include_disabled: bool = False) -> str:
    """List all registered persistent cron jobs.

    Args:
        include_disabled: If true, include disabled jobs (default: only enabled)
    """
    try:
        jobs = _registry.list_all() if include_disabled else _registry.list_enabled()

        if not jobs:
            return "No jobs registered." if include_disabled else "No enabled jobs. Use include_disabled=true to see all."

        lines = [f"Persistent Cron Jobs ({len(jobs)} total):", ""]
        for job in jobs:
            status = "enabled" if job.enabled else "DISABLED"
            last = job.last_result or "never run"
            errors = f" (errors: {job.consecutive_errors})" if job.consecutive_errors > 0 else ""
            lines.append(f"  [{status}] {job.name} (id: {job.id})")
            lines.append(f"    Schedule: {json.dumps(job.schedule)}")
            lines.append(f"    Next run: {job.next_run or 'none'}")
            lines.append(f"    Last: {last}{errors}")
            if job.ttl:
                lines.append(f"    TTL: {job.ttl}")
            if job.timezone:
                lines.append(f"    Timezone: {job.timezone}")
            if job.delete_after_run:
                lines.append("    Auto-delete after run: yes")
            lines.append("")

        return "\n".join(lines)

    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def persistent_cron_get(job_id: str) -> str:
    """Get detailed information about a specific job.

    Args:
        job_id: The job's unique identifier
    """
    try:
        job = _registry.get(job_id)
        if job is None:
            return f"ERROR: Job not found: {job_id}"

        d = _format_job(job)
        return json.dumps(d, indent=2, ensure_ascii=False)

    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def persistent_cron_update(
    job_id: str,
    name: str = "",
    prompt: str = "",
    enabled: str = "",
    schedule_type: str = "",
    schedule_value: str = "",
    cwd: str = "",
    ttl: str = "",
    active_hours_start: str = "",
    active_hours_end: str = "",
    timeout_seconds: int = 0,
    timezone: str = "",
    jitter_seconds: int = -1,
) -> str:
    """Update an existing job's configuration.

    Only provided (non-empty) fields are updated.

    Args:
        job_id: The job's unique identifier
        name: New job name
        prompt: New prompt string
        enabled: "true" or "false" to enable/disable
        schedule_type: New schedule type ("at", "every", "cron")
        schedule_value: New schedule value
        cwd: New working directory
        ttl: New TTL (ISO-8601), use "none" to clear
        active_hours_start: New active window start (HH:MM), use "none" to clear
        active_hours_end: New active window end (HH:MM), use "none" to clear
        timeout_seconds: New timeout (0 = don't change)
        timezone: New IANA timezone (e.g. "Asia/Tokyo"), use "none" to clear (revert to UTC)
        jitter_seconds: New jitter value (-1 = don't change, 0 = disable jitter, >0 = enable)
    """
    try:
        updates = {}

        if name:
            updates["name"] = name
        if prompt:
            updates["prompt"] = prompt
        if enabled:
            if enabled.lower() == "true":
                updates["enabled"] = True
                updates["consecutive_errors"] = 0  # Reset on re-enable
            elif enabled.lower() == "false":
                updates["enabled"] = False
            else:
                return f"ERROR: enabled must be 'true' or 'false', got: {enabled}"

        if schedule_type and schedule_value:
            schedule = {"type": schedule_type}
            if schedule_type == "at":
                schedule["datetime"] = schedule_value
            elif schedule_type == "every":
                try:
                    schedule["interval_seconds"] = int(schedule_value)
                except ValueError:
                    return "ERROR: schedule_value must be integer for 'every'"
                schedule["anchor"] = _now_iso()
            elif schedule_type == "cron":
                schedule["expression"] = schedule_value
            else:
                return f"ERROR: Unknown schedule_type: {schedule_type}"
            updates["schedule"] = schedule

        if cwd:
            updates["cwd"] = cwd

        if ttl:
            updates["ttl"] = None if ttl.lower() == "none" else ttl

        if active_hours_start or active_hours_end:
            if (active_hours_start and active_hours_start.lower() == "none") or \
               (active_hours_end and active_hours_end.lower() == "none"):
                updates["active_hours"] = None
            elif active_hours_start and active_hours_end:
                updates["active_hours"] = {
                    "start": active_hours_start,
                    "end": active_hours_end,
                }

        if timeout_seconds > 0:
            updates["timeout_seconds"] = timeout_seconds

        if timezone:
            if timezone.lower() == "none":
                updates["timezone"] = ""
            else:
                try:
                    from zoneinfo import ZoneInfo
                    ZoneInfo(timezone)
                    updates["timezone"] = timezone
                except (KeyError, Exception) as e:
                    return f"ERROR: Invalid timezone '{timezone}': {e}"

        if jitter_seconds >= 0:
            if jitter_seconds > 3600:
                return "ERROR: jitter_seconds must be <= 3600 (1 hour)."
            updates["jitter_seconds"] = jitter_seconds

        if not updates:
            return "No updates provided."

        result = _registry.update(job_id, updates)
        if result is None:
            return f"ERROR: Job not found: {job_id}"

        return f"Job updated: {result.name} (id: {result.id})\nNext run: {result.next_run or 'none'}"

    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def persistent_cron_run(job_id: str, async_mode: bool = False) -> str:
    """Manually trigger a job execution immediately.

    Bypasses schedule, active hours, and backoff checks.

    Args:
        job_id: The job's unique identifier
        async_mode: If true, run in background thread and return immediately.
                    Results are available via persistent_cron_logs/persistent_cron_get.
                    NOTE: async_mode runs within the MCP server process. Jobs that
                    spawn claude CLI (like heartbeat) may timeout due to MCP server
                    re-initialization conflicts. Use the cron daemon for reliable
                    periodic execution of such jobs.
    """
    try:
        job = _registry.get(job_id)
        if job is None:
            return f"ERROR: Job not found: {job_id}"

        if not job.enabled:
            return "ERROR: Job is disabled. Enable it first with persistent_cron_update."

        # Check concurrency
        if job.running_at:
            if not _executor.is_stuck(job):
                return f"ERROR: Job is already running (started at {job.running_at})"
            # Stuck: clear marker
            with _registry_lock:
                _registry.update(job_id, {"running_at": None})

        # Set running marker
        with _registry_lock:
            _registry.update(job_id, {"running_at": _now_iso()})

        # Async mode: run in background thread
        if async_mode:
            def _run_in_background(job_to_run, jid):
                try:
                    bg_result = _executor.execute_job(job_to_run)
                    if bg_result.skipped:
                        _log.append(LogEntry(
                            job_id=jid, job_name=job_to_run.name,
                            status="skip", error="",
                            output_summary=bg_result.skip_reason,
                        ))
                        with _registry_lock:
                            _registry.update(jid, {"running_at": None})
                        return
                    updates = _executor.apply_result(job_to_run, bg_result)
                    # Extract and handle delete_after_run marker before registry update
                    should_delete = updates.pop("_delete_after_run", False)
                    with _registry_lock:
                        _registry.update(jid, updates)
                        # Auto-delete if marked
                        if should_delete:
                            try:
                                _registry.remove(jid)
                            except Exception as del_e:
                                print(f"WARNING: delete_after_run failed for {jid}: {del_e}", file=sys.stderr)
                    _log.append(LogEntry(
                        job_id=jid, job_name=job_to_run.name,
                        status="success" if bg_result.success else "error",
                        error=bg_result.error,
                        duration_ms=bg_result.duration_ms,
                        output_summary=bg_result.output[:2000] if bg_result.output else "",
                    ))
                    if not bg_result.success:
                        _notifications.add_notification(
                            jid, job_to_run.name, "error",
                            f"Async run failed: {bg_result.error[:200]}"
                        )
                except Exception as e:
                    try:
                        with _registry_lock:
                            _registry.update(jid, {"running_at": None})
                    except Exception as clear_e:
                        print(f"WARNING: Failed to clear running_at for {jid}: {clear_e}", file=sys.stderr)
                    _log.append(LogEntry(
                        job_id=jid, job_name=job_to_run.name,
                        status="error", error=str(e),
                    ))

            thread = threading.Thread(
                target=_run_in_background,
                args=(job, job_id),
                daemon=True,
            )
            thread.start()
            return f"Job started in background: {job.name} (id: {job_id})\n  Check results with persistent_cron_logs or persistent_cron_get"

        # Execute (heartbeat preprocessing is handled by executor)
        result = _executor.execute_job(job)

        # Handle heartbeat skip
        if result.skipped:
            _log.append(LogEntry(
                job_id=job.id,
                job_name=job.name,
                status="skip",
                error=result.skip_reason if "error" in result.skip_reason.lower() or "blocked" in result.skip_reason.lower() else "",
                output_summary=result.skip_reason,
            ))
            # Clear running marker
            with _registry_lock:
                _registry.update(job_id, {"running_at": None})
            return f"Job skipped: {job.name}\n  Reason: {result.skip_reason}"

        # For sync (MCP) manual runs: do NOT apply_result (which updates
        # consecutive_errors, next_run, backoff). MCP sync execution timeouts
        # are an architectural limitation, not job failures.
        # Only update running_at and log the result.
        with _registry_lock:
            _registry.update(job_id, {"running_at": None})

        # Log
        _log.append(LogEntry(
            job_id=job.id,
            job_name=job.name,
            status="success" if result.success else "error",
            error=result.error,
            duration_ms=result.duration_ms,
            output_summary=result.output[:2000] if result.output else "",
        ))

        # Notification on error (informational only, no backoff effect)
        if not result.success:
            _notifications.add_notification(
                job.id, job.name, "error",
                f"Manual run failed: {result.error[:200]}"
            )

        # On success, clear any previous error state
        if result.success:
            with _registry_lock:
                _registry.update(job_id, {
                    "last_result": "success",
                    "last_error": "",
                    "consecutive_errors": 0,
                })

        # Handle delete_after_run in sync path
        job_deleted = False
        if result.success and job.delete_after_run:
            try:
                with _registry_lock:
                    _registry.remove(job_id)
                job_deleted = True
            except Exception as del_e:
                print(f"WARNING: delete_after_run failed for {job_id}: {del_e}", file=sys.stderr)

        # Format response
        lines = [
            f"Job executed: {job.name}",
            f"  Status: {'success' if result.success else 'error'}",
            f"  Duration: {result.duration_ms}ms",
        ]
        if result.error:
            lines.append(f"  Error: {result.error[:300]}")
        if result.output:
            lines.append(f"  Output: {result.output[:2000]}")
        if job_deleted:
            lines.append("  Auto-deleted: job removed after successful run")

        return "\n".join(lines)

    except Exception as e:
        # Clear running marker on exception
        try:
            with _registry_lock:
                _registry.update(job_id, {"running_at": None})
        except Exception as clear_e:
            print(f"WARNING: Failed to clear running_at for {job_id}: {clear_e}", file=sys.stderr)
        return f"ERROR: {e}"


@mcp.tool()
def persistent_cron_logs(
    job_id: str = "",
    limit: int = 20,
) -> str:
    """View execution logs for jobs.

    Args:
        job_id: Filter by job ID (empty = all jobs)
        limit: Max entries to return (default 20)
    """
    try:
        entries = _log.get_recent(
            job_id=job_id if job_id else None,
            limit=limit,
        )

        if not entries:
            return "No execution logs found."

        lines = [f"Execution Logs ({len(entries)} entries):", ""]
        for entry in entries:
            status_icon = "ok" if entry.status == "success" else "ERR" if entry.status == "error" else "skip"
            lines.append(f"  [{status_icon}] {entry.timestamp} | {entry.job_name or entry.job_id} | {entry.duration_ms}ms")
            if entry.error:
                lines.append(f"    Error: {entry.error[:200]}")
            if entry.output_summary:
                lines.append(f"    Output: {entry.output_summary[:500]}")

        return "\n".join(lines)

    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def persistent_cron_status() -> str:
    """Check the status of the cron daemon and overall system.

    Returns daemon running state, job counts, and recent activity.
    """
    try:
        daemon = _check_daemon_running()
        all_jobs = _registry.list_all()
        enabled_jobs = [j for j in all_jobs if j.enabled]
        error_jobs = [j for j in all_jobs if j.consecutive_errors > 0]

        lines = [
            "Persistent Cron Status:",
            f"  Daemon: {'RUNNING (pid: ' + str(daemon['pid']) + ')' if daemon['running'] else 'NOT RUNNING'}",
            f"  Jobs: {len(all_jobs)} total, {len(enabled_jobs)} enabled, {len(error_jobs)} with errors",
        ]

        # Recent activity
        recent = _log.get_recent(limit=5)
        if recent:
            lines.append("")
            lines.append("Recent executions:")
            for entry in recent:
                status_icon = "ok" if entry.status == "success" else "ERR"
                lines.append(f"  [{status_icon}] {entry.timestamp} | {entry.job_name or entry.job_id}")

        # Pending notifications
        pending = _notifications.get_pending()
        if pending:
            lines.append("")
            lines.append(f"Pending notifications: {len(pending)}")

        # Daemon start instructions if not running
        if not daemon["running"]:
            lines.append("")
            daemon_path = os.path.join(TOOLS_DIR, "cron_daemon.py")
            lines.append(f"To start daemon: pythonw \"{daemon_path}\"")
            lines.append(f"Or: python \"{daemon_path}\" --foreground")

        return "\n".join(lines)

    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def persistent_cron_notifications() -> str:
    """Get pending notifications from cron job executions and mark them as consumed.

    Call this at session start to see what happened while you were away.
    """
    try:
        pending = _notifications.get_pending()

        if not pending:
            return "No pending notifications."

        lines = [f"Cron Notifications ({len(pending)} pending):", ""]
        for n in pending:
            status_icon = "ok" if n.status == "success" else "ERR" if n.status == "error" else "!!"
            lines.append(f"  [{status_icon}] {n.timestamp} | {n.job_name}")
            lines.append(f"    {n.message}")
            lines.append("")

        # Mark as consumed
        count = _notifications.mark_consumed()
        lines.append(f"({count} notifications marked as consumed)")

        return "\n".join(lines)

    except Exception as e:
        return f"ERROR: {e}"


def main():
    print("Persistent cron MCP server starting on stdio...", file=sys.stderr)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
