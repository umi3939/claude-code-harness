"""
Cron/Heartbeat scheduler core module.

Provides job registry, schedule computation, execution engine,
execution logging, and notification buffer for session-external
autonomous job execution.

This is a generic Claude Code utility, not part of any specific project.
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from croniter import croniter
from security_sanitizer import SecuritySanitizer

# ═══════════════════════════════════════════════════════════════
# Safety valve constants
# ═══════════════════════════════════════════════════════════════

MIN_REFIRE_GAP_SECONDS = 2
MAX_CONCURRENT_JOBS = 3
DEFAULT_TIMEOUT_SECONDS = 300
MAX_EXECUTIONS_PER_HOUR = 20
MAX_MISSED_JOBS_RECOVERY = 5
STUCK_THRESHOLD_SECONDS = 7200  # 2 hours
BACKOFF_SCHEDULE = [30, 60, 300, 900, 3600]
MAX_CONSECUTIVE_ERRORS = 5
LOG_MAX_BYTES = 2_000_000
LOG_MAX_LINES = 2000

# Project root (tools/ の親ディレクトリ)
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))

from file_io import resolve_project_root  # noqa: E402

_PROJECT_ROOT = resolve_project_root()

# Default paths
CRON_DIR = os.path.join(_PROJECT_ROOT, "cron")
JOBS_FILE = os.path.join(CRON_DIR, "jobs.json")
LOG_FILE = os.path.join(CRON_DIR, "execution.jsonl")
NOTIFICATIONS_FILE = os.path.join(CRON_DIR, "notifications.json")

# Transient error patterns
TRANSIENT_PATTERNS = {
    "rate_limit": re.compile(r"(rate[_ ]limit|too many requests|429)", re.I),
    "overloaded": re.compile(r"(529|overloaded|high demand)", re.I),
    "network": re.compile(r"(network|econnreset|econnrefused|enotfound)", re.I),
    "timeout": re.compile(r"(timeout|etimedout)", re.I),
    "server_error": re.compile(r"\b5\d{2}\b"),
}

# Heartbeat constants
HEARTBEAT_DEFAULT_PATH = os.path.join(_PROJECT_ROOT, "HEARTBEAT.md")
HEARTBEAT_ACTIONS_FILE = os.path.join(_PROJECT_ROOT, "cron", "heartbeat_actions.jsonl")
HEARTBEAT_MAX_SIZE = 10240  # 10KB

# HTML comment regex for heartbeat preprocessing
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


# ═══════════════════════════════════════════════════════════════
# Heartbeat helpers
# ═══════════════════════════════════════════════════════════════


def strip_html_comments(text: str) -> str:
    """Strip HTML comments from text (prevent comment-based injection)."""
    return _HTML_COMMENT_RE.sub("", text)


FILE_IO_TIMEOUT = 10  # seconds, timeout for file I/O operations


def _read_file_with_timeout(path: str, timeout: float = FILE_IO_TIMEOUT) -> tuple:
    """Read a file with a timeout to prevent hanging on network mounts.

    Returns (content, error) — same contract as read_heartbeat_file.
    """
    result = [None, None]  # [content, error]

    def _do_read():
        try:
            with open(path, "r", encoding="utf-8") as f:
                result[0] = f.read()
        except OSError as e:
            result[1] = f"Error reading file: {e}"

    thread = threading.Thread(target=_do_read, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        return None, f"File read timed out after {timeout}s"
    return result[0], result[1]


def read_heartbeat_file(path: str) -> tuple:
    """Read HEARTBEAT.md with size limit and I/O timeout.

    Returns (content, error):
    - (content, None) on success
    - (None, None) if file doesn't exist (not an error)
    - (None, error_message) on size limit exceeded, read error, or timeout
    """
    if not os.path.exists(path):
        return None, None
    try:
        size = os.path.getsize(path)
        if size > HEARTBEAT_MAX_SIZE:
            return None, f"HEARTBEAT.md size ({size} bytes) exceeds limit ({HEARTBEAT_MAX_SIZE} bytes)"
        return _read_file_with_timeout(path)
    except OSError as e:
        return None, f"Error reading HEARTBEAT.md: {e}"


def is_heartbeat_empty(path: str, content: Optional[str] = None) -> bool:
    """Check if HEARTBEAT.md is effectively empty.

    Empty means: file doesn't exist, is zero-length, or contains only
    markdown headers (# lines), HTML comments, and whitespace.

    Args:
        path: Path to HEARTBEAT.md (used if content is None).
        content: Pre-read file content to avoid redundant file I/O.
                 If None, reads from path.
    """
    if content is None:
        content, error = read_heartbeat_file(path)
        if content is None:
            return True  # Missing or error -> treat as empty
    if not content.strip():
        return True

    # Strip HTML comments first
    stripped = strip_html_comments(content)

    # Remove markdown headers (lines starting with #)
    lines = stripped.split("\n")
    remaining = []
    for line in lines:
        stripped_line = line.strip()
        if stripped_line and not stripped_line.startswith("#"):
            remaining.append(stripped_line)

    return len(remaining) == 0


def _read_action_history(actions_file: str, limit: int = 3) -> str:
    """Read the last N entries from heartbeat_actions.jsonl.

    Returns formatted string for inclusion in prompt.
    Returns "No previous actions." if file missing/empty/unreadable.
    """
    try:
        with open(actions_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except (FileNotFoundError, OSError):
        return "No previous actions."

    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                entries.append(data)
        except (json.JSONDecodeError, TypeError):
            continue

    if not entries:
        return "No previous actions."

    # Take last N entries
    recent = entries[-limit:]
    formatted = []
    for e in recent:
        ts = e.get("timestamp", "?")
        concern = e.get("concern", "?")
        action = e.get("action_taken", "?")
        result = e.get("result", "?")
        formatted.append(f"{ts} | {concern} | {action} | {result}")

    return "\n".join(formatted)


def build_heartbeat_prompt(content: str, actions_file: Optional[str] = None) -> str:
    """Build the heartbeat prompt template with HEARTBEAT.md content.

    The template instructs the AI to:
    - Review the concern list and act if needed
    - NOT modify HEARTBEAT.md (read-only)
    - NOT register new cron jobs

    Args:
        content: The sanitized HEARTBEAT.md content.
        actions_file: Path to heartbeat_actions.jsonl. Defaults to HEARTBEAT_ACTIONS_FILE.
    """
    if actions_file is None:
        actions_file = HEARTBEAT_ACTIONS_FILE

    action_history = _read_action_history(actions_file)

    return (
        "You are performing a periodic heartbeat check. "
        "Below is your concern list from HEARTBEAT.md. "
        "Review each item and take appropriate action if needed. "
        "If nothing requires attention, simply report that all is well.\n\n"
        "IMPORTANT CONSTRAINTS:\n"
        "- HEARTBEAT.md is READ-ONLY. Do NOT modify or write to it.\n"
        "- Do NOT register new cron jobs or scheduled tasks.\n"
        "- Do NOT use emojis.\n"
        "- Take action when the concern list explicitly instructs it.\n\n"
        "--- CONCERN LIST ---\n"
        f"{content}\n"
        "--- END CONCERN LIST ---\n\n"
        "--- ACTION HISTORY ---\n"
        f"{action_history}\n"
        "If the last action matches your planned action, skip it unless circumstances changed.\n"
        "--- END ACTION HISTORY ---"
    )


# ═══════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════


@dataclass
class CronJob:
    """A scheduled job definition."""
    id: str = ""
    name: str = ""
    description: str = ""
    enabled: bool = True
    schedule: Dict[str, Any] = field(default_factory=dict)
    prompt: str = ""
    cwd: str = ""
    one_shot: bool = False
    ttl: Optional[str] = None  # ISO-8601 expiry
    active_hours: Optional[Dict[str, str]] = None  # {"start": "08:00", "end": "23:00"}
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    type: str = "standard"  # "standard" or "heartbeat"
    permission_mode: str = "bypassPermissions"  # permission mode for CLI execution
    created_at: str = ""
    next_run: Optional[str] = None
    last_run: Optional[str] = None
    last_result: Optional[str] = None  # "success" / "error" / "skip"
    last_error: Optional[str] = None
    consecutive_errors: int = 0
    running_at: Optional[str] = None  # concurrency guard marker
    delete_after_run: bool = False  # If True, job is removed after successful execution
    timezone: str = ""  # IANA timezone name (e.g. "Asia/Tokyo"). Empty = UTC
    jitter_seconds: int = 0  # Random offset range [0, jitter_seconds] added to next_run

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Remove None values for cleaner JSON
        return {k: v for k, v in d.items() if v is not None}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CronJob":
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in known_fields}
        return cls(**filtered)


@dataclass
class ExecutionResult:
    """Result of a job execution."""
    success: bool = False
    output: str = ""
    error: str = ""
    duration_ms: int = 0
    timed_out: bool = False
    skipped: bool = False  # True if heartbeat preprocessing decided to skip
    skip_reason: str = ""  # Reason for skip (heartbeat empty, blocked, etc.)


@dataclass
class LogEntry:
    """A single execution log entry."""
    timestamp: str = ""
    job_id: str = ""
    job_name: str = ""
    status: str = ""  # "success" / "error" / "skip"
    error: str = ""
    duration_ms: int = 0
    output_summary: str = ""


@dataclass
class Notification:
    """A notification for the next session."""
    id: str = ""
    job_id: str = ""
    job_name: str = ""
    status: str = ""
    message: str = ""
    timestamp: str = ""
    consumed: bool = False


# ═══════════════════════════════════════════════════════════════
# Atomic file I/O
# ═══════════════════════════════════════════════════════════════


def _ensure_dir(dir_path: str) -> None:
    """Create directory if it doesn't exist."""
    os.makedirs(dir_path, exist_ok=True)


def _atomic_write(path: str, data: str) -> None:
    """Write data to file atomically using tmp+replace pattern."""
    dir_path = os.path.dirname(path)
    _ensure_dir(dir_path)

    fd, tmp = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        # Backup existing file (best-effort)
        if os.path.exists(path):
            try:
                shutil.copy2(path, path + ".bak")
            except OSError:
                pass
        # Atomic replace - works on POSIX, best-effort on Windows
        try:
            os.replace(tmp, path)
        except OSError:
            # Windows fallback: copy + unlink
            shutil.copy2(tmp, path)
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _now_iso() -> str:
    """Return current time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: str) -> Optional[datetime]:
    """Parse ISO-8601 string to datetime, returning None on failure."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# ═══════════════════════════════════════════════════════════════
# Schedule computation
# ═══════════════════════════════════════════════════════════════


def _resolve_timezone(timezone_str: str) -> Optional["ZoneInfo"]:  # noqa: F821
    """Resolve IANA timezone name to ZoneInfo object.

    Returns None for empty string (UTC default).
    Falls back to None (UTC) with stderr warning on invalid timezone.
    """
    if not timezone_str:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(timezone_str)
    except (KeyError, Exception) as e:
        import sys as _sys
        print(
            f"WARNING: Invalid timezone '{timezone_str}', falling back to UTC: {e}",
            file=_sys.stderr,
        )
        return None


def compute_next_run(schedule: Dict[str, Any], now: Optional[datetime] = None,
                     timezone_str: str = "") -> Optional[str]:
    """
    Compute the next run time for a given schedule definition.

    Schedule types:
      - {"type": "at", "datetime": "ISO-8601"}: one-time at specific time
      - {"type": "every", "interval_seconds": int, "anchor": "ISO-8601"}: fixed interval
      - {"type": "cron", "expression": "* * * * *"}: cron expression

    Args:
        schedule: Schedule definition dict.
        now: Current time (default: utcnow).
        timezone_str: IANA timezone name for cron expression evaluation.
                      Empty string = UTC (default). Only affects cron type.

    Returns ISO-8601 string (always UTC) or None if no future run.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    stype = schedule.get("type", "")

    if stype == "at":
        dt_str = schedule.get("datetime", "")
        dt = _parse_iso(dt_str)
        if dt is None:
            return None
        return dt.isoformat() if dt > now else None

    elif stype == "every":
        interval = schedule.get("interval_seconds")
        # Fallback: legacy "value" field
        if interval is None:
            try:
                interval = int(schedule.get("value", 0))
            except (ValueError, TypeError):
                interval = 0
        if not isinstance(interval, (int, float)) or not math.isfinite(interval) or interval <= 0:
            return None
        interval = max(1, int(interval))

        anchor_str = schedule.get("anchor", "")
        anchor = _parse_iso(anchor_str)
        if anchor is None:
            anchor = now

        if now < anchor:
            return anchor.isoformat()

        elapsed = (now - anchor).total_seconds()
        steps = max(1, math.ceil(elapsed / interval))
        next_dt = anchor + timedelta(seconds=steps * interval)
        return next_dt.isoformat()

    elif stype == "cron":
        expr = schedule.get("expression", "")
        if not isinstance(expr, str) or not expr.strip():
            return None
        expr = expr.strip()

        # Timezone support: convert UTC now to local time for cron evaluation
        tz_info = _resolve_timezone(timezone_str)
        if tz_info is not None:
            now_local = now.astimezone(tz_info)
        else:
            now_local = now

        try:
            cron = croniter(expr, now_local)
            next_dt = cron.get_next(datetime)
        except (ValueError, KeyError):
            return None

        # Convert result back to UTC
        if tz_info is not None:
            # croniter returns tz-aware datetime matching input tz
            if next_dt.tzinfo is None:
                next_dt = next_dt.replace(tzinfo=tz_info)
            next_dt = next_dt.astimezone(timezone.utc)
        else:
            if next_dt.tzinfo is None:
                next_dt = next_dt.replace(tzinfo=timezone.utc)

        # croniter bug workaround: if result is not in the future, retry
        if next_dt <= now:
            try:
                next_second = now + timedelta(seconds=1)
                if tz_info is not None:
                    next_second_local = next_second.astimezone(tz_info)
                else:
                    next_second_local = next_second
                cron2 = croniter(expr, next_second_local)
                next_dt = cron2.get_next(datetime)
                if tz_info is not None:
                    if next_dt.tzinfo is None:
                        next_dt = next_dt.replace(tzinfo=tz_info)
                    next_dt = next_dt.astimezone(timezone.utc)
                else:
                    if next_dt.tzinfo is None:
                        next_dt = next_dt.replace(tzinfo=timezone.utc)
                if next_dt > now:
                    return next_dt.isoformat()
            except (ValueError, KeyError):
                pass
            return None

        return next_dt.isoformat()

    return None


def is_within_active_hours(active_hours: Optional[Dict[str, str]],
                           now: Optional[datetime] = None,
                           timezone_str: str = "") -> bool:
    """
    Check if current time is within active hours window.
    Returns True if no active_hours is set (always active).

    When timezone_str is provided, converts UTC now to local time before comparison.
    This applies to all schedule types (cron, at, every).

    active_hours: {"start": "HH:MM", "end": "HH:MM"}
    """
    if active_hours is None:
        return True

    if now is None:
        now = datetime.now(timezone.utc)

    # Convert to local time if timezone is specified
    tz_info = _resolve_timezone(timezone_str)
    if tz_info is not None:
        now = now.astimezone(tz_info)

    start_str = active_hours.get("start", "")
    end_str = active_hours.get("end", "")
    if not start_str or not end_str:
        return True

    try:
        start_parts = start_str.split(":")
        end_parts = end_str.split(":")
        start_minutes = int(start_parts[0]) * 60 + int(start_parts[1])
        end_minutes = int(end_parts[0]) * 60 + int(end_parts[1])
    except (ValueError, IndexError):
        return True

    current_minutes = now.hour * 60 + now.minute

    if start_minutes == end_minutes:
        return False  # Zero-width window = always inactive (canonical interval semantics)

    if start_minutes < end_minutes:
        # Normal range: e.g. 08:00 - 23:00
        return start_minutes <= current_minutes < end_minutes
    else:
        # Overnight range: e.g. 22:00 - 06:00
        return current_minutes >= start_minutes or current_minutes < end_minutes


def compute_next_active_start(active_hours: Dict[str, str],
                              now: Optional[datetime] = None) -> Optional[str]:
    """
    Compute the next active hours start time.
    Returns ISO-8601 string of the next time the active window opens.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    start_str = active_hours.get("start", "")
    if not start_str:
        return None

    try:
        parts = start_str.split(":")
        start_h = int(parts[0])
        start_m = int(parts[1])
    except (ValueError, IndexError):
        return None

    # Try today first
    candidate = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    if candidate <= now:
        # Already past today's start, try tomorrow
        candidate += timedelta(days=1)
    return candidate.isoformat()


# ═══════════════════════════════════════════════════════════════
# Jitter (Load Spreading)
# ═══════════════════════════════════════════════════════════════


def _default_jitter_rng(max_seconds: int) -> int:
    """Generate a random integer in [0, max_seconds] using system random."""
    import random
    return random.randint(0, max_seconds)  # noqa: S311


def apply_jitter(
    next_run_iso: Optional[str],
    jitter_seconds: int,
    active_hours: Optional[Dict[str, str]] = None,
    timezone_str: str = "",
    rng_fn=None,
) -> Optional[str]:
    """Apply random jitter offset to a next_run timestamp.

    Adds a random offset in [0, jitter_seconds] to the given next_run time.
    If the result exceeds active_hours end, clamps to the original next_run
    (i.e., no jitter is applied rather than pushing outside the window).

    Args:
        next_run_iso: ISO-8601 next run timestamp (UTC). None returns None.
        jitter_seconds: Maximum jitter offset in seconds. 0 or negative = no-op.
        active_hours: Optional {"start": "HH:MM", "end": "HH:MM"} window.
        timezone_str: IANA timezone for active_hours evaluation.
        rng_fn: Optional callable(max_seconds) -> int for deterministic testing.

    Returns:
        ISO-8601 string with jitter applied (always UTC), or None.
    """
    if not next_run_iso:
        return None

    if jitter_seconds <= 0:
        return next_run_iso

    dt = _parse_iso(next_run_iso)
    if dt is None:
        return None

    if rng_fn is None:
        rng_fn = _default_jitter_rng

    offset = rng_fn(jitter_seconds)
    jittered_dt = dt + timedelta(seconds=offset)

    # Active hours clamp: if jittered time falls outside active window, revert to original
    if active_hours is not None:
        end_str = active_hours.get("end", "")
        if end_str:
            try:
                end_parts = end_str.split(":")
                end_h = int(end_parts[0])
                end_m = int(end_parts[1])
            except (ValueError, IndexError):
                # Invalid active_hours format: skip clamp
                return jittered_dt.isoformat()

            # Determine the check time in local TZ if timezone specified
            tz_info = _resolve_timezone(timezone_str)
            if tz_info is not None:
                check_dt = jittered_dt.astimezone(tz_info)
            else:
                check_dt = jittered_dt

            check_minutes = check_dt.hour * 60 + check_dt.minute
            end_minutes = end_h * 60 + end_m

            start_str = active_hours.get("start", "")
            try:
                start_parts = start_str.split(":")
                start_minutes = int(start_parts[0]) * 60 + int(start_parts[1])
            except (ValueError, IndexError):
                start_minutes = 0

            # Determine if jittered time is outside active window
            outside = False
            if start_minutes < end_minutes:
                # Normal range: e.g. 08:00-23:00
                if check_minutes >= end_minutes:
                    outside = True
            else:
                # Overnight range: e.g. 22:00-06:00
                if end_minutes <= check_minutes < start_minutes:
                    outside = True

            if outside:
                # Clamp: revert to original (no jitter)
                return next_run_iso

    return jittered_dt.isoformat()


# ═══════════════════════════════════════════════════════════════
# Job Registry
# ═══════════════════════════════════════════════════════════════


class JobRegistry:
    """Manages job persistence with atomic file I/O."""

    def __init__(self, store_path: Optional[str] = None):
        self.store_path = store_path or JOBS_FILE
        self._cache: Optional[str] = None  # serialized JSON cache for diff check

    def _load(self) -> List[CronJob]:
        """Load jobs from store file."""
        try:
            with open(self.store_path, "r", encoding="utf-8") as f:
                raw = f.read()
            data = json.loads(raw)
            if not isinstance(data, dict):
                return []
            jobs_data = data.get("jobs", [])
            if not isinstance(jobs_data, list):
                return []
            jobs = []
            for item in jobs_data:
                if isinstance(item, dict):
                    try:
                        jobs.append(CronJob.from_dict(item))
                    except (TypeError, ValueError):
                        continue
            self._cache = raw
            return jobs
        except FileNotFoundError:
            self._cache = None
            return []
        except (json.JSONDecodeError, OSError):
            self._cache = None
            return []

    def _save(self, jobs: List[CronJob]) -> None:
        """Save jobs to store file with diff check."""
        store = {"version": 1, "jobs": [j.to_dict() for j in jobs]}
        new_json = json.dumps(store, indent=2, ensure_ascii=False)

        # Diff check: skip write if unchanged
        if self._cache is not None:
            try:
                cached_data = json.loads(self._cache)
                cached_json = json.dumps(cached_data, indent=2, ensure_ascii=False)
                if cached_json == new_json:
                    return
            except (json.JSONDecodeError, TypeError):
                pass

        _atomic_write(self.store_path, new_json)
        self._cache = new_json

    def add(self, job: CronJob) -> CronJob:
        """Add a new job. Assigns ID and created_at if not set."""
        if not job.id:
            job.id = str(uuid.uuid4())
        if not job.created_at:
            job.created_at = _now_iso()
        if not job.cwd:
            job.cwd = os.path.expanduser("~")

        # Compute initial next_run
        if job.enabled and job.schedule:
            next_run = compute_next_run(job.schedule, timezone_str=job.timezone)
            if next_run:
                job.next_run = next_run
            elif job.schedule.get("type") == "cron":
                expr = job.schedule.get("expression", "")
                raise ValueError(f"Invalid cron expression: {expr!r}")

        jobs = self._load()
        # Check for duplicate ID
        if any(j.id == job.id for j in jobs):
            raise ValueError(f"Job with id {job.id} already exists")
        jobs.append(job)
        self._save(jobs)
        return job

    def get(self, job_id: str) -> Optional[CronJob]:
        """Get a job by ID."""
        jobs = self._load()
        for j in jobs:
            if j.id == job_id:
                return j
        return None

    def update(self, job_id: str, updates: Dict[str, Any]) -> Optional[CronJob]:
        """Update a job's fields. Returns updated job or None if not found."""
        jobs = self._load()
        for i, j in enumerate(jobs):
            if j.id == job_id:
                for key, value in updates.items():
                    if hasattr(j, key) and key != "id":
                        setattr(j, key, value)
                # Recompute next_run if schedule changed
                if "schedule" in updates and j.enabled:
                    next_run = compute_next_run(j.schedule, timezone_str=j.timezone)
                    if next_run:
                        j.next_run = next_run
                jobs[i] = j
                self._save(jobs)
                return j
        return None

    def remove(self, job_id: str) -> bool:
        """Remove a job by ID. Returns True if removed."""
        jobs = self._load()
        initial_len = len(jobs)
        jobs = [j for j in jobs if j.id != job_id]
        if len(jobs) < initial_len:
            self._save(jobs)
            return True
        return False

    def list_all(self) -> List[CronJob]:
        """List all jobs."""
        return self._load()

    def list_enabled(self) -> List[CronJob]:
        """List only enabled jobs."""
        return [j for j in self._load() if j.enabled]


# ═══════════════════════════════════════════════════════════════
# Backoff & error handling
# ═══════════════════════════════════════════════════════════════


def backoff_seconds(consecutive_errors: int) -> int:
    """
    Compute backoff delay from consecutive error count.
    Uses BACKOFF_SCHEDULE: [30, 60, 300, 900, 3600].
    """
    if consecutive_errors <= 0:
        return 0
    idx = min(consecutive_errors - 1, len(BACKOFF_SCHEDULE) - 1)
    return BACKOFF_SCHEDULE[idx]


def is_transient_error(error_text: str) -> bool:
    """Check if an error message indicates a transient/retriable error."""
    if not error_text:
        return False
    return any(pat.search(error_text) for pat in TRANSIENT_PATTERNS.values())


# ═══════════════════════════════════════════════════════════════
# Job Executor
# ═══════════════════════════════════════════════════════════════


class HeartbeatPreprocessResult:
    """Result of heartbeat preprocessing."""

    def __init__(self, should_execute: bool, prompt: Optional[str] = None,
                 skip_reason: str = "", skip_status: str = "skip",
                 error_message: str = ""):
        self.should_execute = should_execute
        self.prompt = prompt  # The processed prompt to use for execution
        self.skip_reason = skip_reason  # Why it was skipped (for logging)
        self.skip_status = skip_status  # "skip" or "error" (for log entry)
        self.error_message = error_message  # Error detail (for notifications)


class JobExecutor:
    """Executes jobs by spawning Claude CLI subprocess."""

    def __init__(self, registry: Optional[JobRegistry] = None):
        self.registry = registry or JobRegistry()
        # Cached SecuritySanitizer for heartbeat jobs (fail-closed, block mode)
        self._heartbeat_sanitizer = SecuritySanitizer(
            injection_mode="block",
            sanitize_mode="escape",
            fail_open=False,
        )

    def is_ttl_expired(self, job: CronJob) -> bool:
        """Check if a job's TTL has expired."""
        if not job.ttl:
            return False
        expiry = _parse_iso(job.ttl)
        if expiry is None:
            return False
        return datetime.now(timezone.utc) > expiry

    def is_stuck(self, job: CronJob) -> bool:
        """Check if a job is stuck (running_at older than threshold)."""
        if not job.running_at:
            return False
        running_dt = _parse_iso(job.running_at)
        if running_dt is None:
            return True  # Invalid timestamp = treat as stuck
        elapsed = (datetime.now(timezone.utc) - running_dt).total_seconds()
        return elapsed > STUCK_THRESHOLD_SECONDS

    def should_skip(self, job: CronJob) -> Optional[str]:
        """
        Check if a job should be skipped.
        Returns skip reason string or None if job should run.
        """
        if not job.enabled:
            return "disabled"

        if self.is_ttl_expired(job):
            return "ttl_expired"

        if not is_within_active_hours(job.active_hours, timezone_str=job.timezone):
            return "outside_active_hours"

        if job.running_at and not self.is_stuck(job):
            return "already_running"

        # Backoff check
        if job.consecutive_errors > 0 and job.last_run:
            last_dt = _parse_iso(job.last_run)
            if last_dt:
                wait = backoff_seconds(job.consecutive_errors)
                next_allowed = last_dt + timedelta(seconds=wait)
                if datetime.now(timezone.utc) < next_allowed:
                    return "backoff"

        return None

    def preprocess_heartbeat(self, job: CronJob) -> HeartbeatPreprocessResult:
        """Preprocess a heartbeat job: read file, check empty, sanitize, build prompt.

        Stages:
        A. Read + size check + empty check
        B. SecuritySanitizer (fail-closed, block mode)
        C. Build prompt template

        Returns HeartbeatPreprocessResult indicating whether to proceed with execution.
        """
        heartbeat_path = job.prompt  # For heartbeat jobs, prompt holds the file path

        # Stage A: Read and empty check
        content, read_error = read_heartbeat_file(heartbeat_path)
        if read_error:
            return HeartbeatPreprocessResult(
                should_execute=False,
                skip_reason=f"heartbeat_file_error: {read_error}",
                skip_status="skip",
                error_message=f"Heartbeat file error: {read_error}",
            )

        if is_heartbeat_empty(heartbeat_path, content=content):
            return HeartbeatPreprocessResult(
                should_execute=False,
                skip_reason="heartbeat_empty",
                skip_status="skip",
            )

        # Stage B: Sanitize (fail-closed, block mode for heartbeat)
        # Strip HTML comments before sanitization
        content_no_comments = strip_html_comments(content)
        san_result = self._heartbeat_sanitizer.sanitize(content_no_comments)

        if san_result.blocked:
            reason = san_result.block_reason
            return HeartbeatPreprocessResult(
                should_execute=False,
                skip_reason=f"security_blocked: {reason}",
                skip_status="skip",
                error_message=f"Heartbeat blocked: {reason}",
            )

        # Stage C: Build prompt
        prompt = build_heartbeat_prompt(san_result.text)
        return HeartbeatPreprocessResult(
            should_execute=True,
            prompt=prompt,
        )

    def execute_job(self, job: CronJob) -> ExecutionResult:
        """
        Execute a job by spawning Claude CLI.

        For heartbeat jobs, preprocesses (read file -> empty check -> sanitize ->
        build prompt) before execution. Returns a skip result if preprocessing
        determines the job should not run.

        Uses job.permission_mode for --permission-mode flag.
        Default: bypassPermissions (backward compatible).
        Heartbeat jobs use "plan" for restricted permissions.
        """
        # Heartbeat preprocessing
        if job.type == "heartbeat":
            pp = self.preprocess_heartbeat(job)
            if not pp.should_execute:
                # Return a special "skip" result
                return ExecutionResult(
                    success=True,  # Not an error, just a skip
                    output=f"heartbeat_skip: {pp.skip_reason}",
                    duration_ms=0,
                    skipped=True,
                    skip_reason=pp.skip_reason,
                )
            # Replace prompt with the preprocessed one
            job = dataclasses.replace(job, prompt=pp.prompt)

        start_time = time.monotonic()
        result = ExecutionResult()

        try:
            proc = subprocess.run(
                [
                    shutil.which("claude") or "claude",  # noqa: S607
                    "--print",
                    "--permission-mode", job.permission_mode,
                    "--no-session-persistence",
                    job.prompt,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=job.timeout_seconds,
                cwd=job.cwd if job.cwd and os.path.isdir(job.cwd) else os.path.expanduser("~"),
            )
            result.duration_ms = int((time.monotonic() - start_time) * 1000)
            result.output = proc.stdout or ""
            if proc.returncode == 0:
                result.success = True
            else:
                result.success = False
                result.error = proc.stderr or f"Exit code: {proc.returncode}"

        except subprocess.TimeoutExpired:
            result.duration_ms = int((time.monotonic() - start_time) * 1000)
            result.success = False
            result.error = f"Timeout after {job.timeout_seconds}s"
            result.timed_out = True

        except FileNotFoundError:
            result.duration_ms = int((time.monotonic() - start_time) * 1000)
            result.success = False
            result.error = "claude CLI not found"

        except OSError as e:
            result.duration_ms = int((time.monotonic() - start_time) * 1000)
            result.success = False
            result.error = f"OS error: {e}"

        return result

    def apply_result(self, job: CronJob, result: ExecutionResult) -> Dict[str, Any]:
        """
        Apply execution result to job state.
        Returns dict of updates to apply via registry.update().
        """
        now = _now_iso()
        updates: Dict[str, Any] = {
            "last_run": now,
            "running_at": None,
        }

        if result.success:
            updates["last_result"] = "success"
            updates["last_error"] = None
            updates["consecutive_errors"] = 0
        else:
            updates["last_result"] = "error"
            updates["last_error"] = result.error[:500] if result.error else "Unknown error"
            updates["consecutive_errors"] = job.consecutive_errors + 1

        # Recompute next_run
        if result.success and job.one_shot:
            # One-shot: disable after successful run
            updates["enabled"] = False
            updates["next_run"] = None
        elif not result.success and job.one_shot:
            # One-shot failure: check transient for retry
            new_errors = updates["consecutive_errors"]
            if is_transient_error(result.error) and new_errors <= MAX_CONSECUTIVE_ERRORS:
                wait = backoff_seconds(new_errors)
                next_dt = datetime.now(timezone.utc) + timedelta(seconds=wait)
                updates["next_run"] = next_dt.isoformat()
            else:
                # Permanent error or too many retries
                updates["enabled"] = False
                updates["next_run"] = None
        else:
            # Recurring job: compute next natural run
            next_run = compute_next_run(job.schedule, timezone_str=job.timezone)
            if next_run and not result.success:
                # Apply backoff: use max(natural_next, now + backoff)
                new_errors = updates["consecutive_errors"]
                backoff_wait = backoff_seconds(new_errors)
                backoff_dt = datetime.now(timezone.utc) + timedelta(seconds=backoff_wait)
                natural_dt = _parse_iso(next_run)
                if natural_dt and backoff_dt > natural_dt:
                    updates["next_run"] = backoff_dt.isoformat()
                else:
                    updates["next_run"] = next_run
            else:
                # Apply jitter as post-processing on success path
                if next_run and job.jitter_seconds > 0:
                    next_run = apply_jitter(
                        next_run, job.jitter_seconds,
                        active_hours=job.active_hours,
                        timezone_str=job.timezone,
                    )
                updates["next_run"] = next_run

        # Auto-disable on too many errors
        if updates.get("consecutive_errors", 0) >= MAX_CONSECUTIVE_ERRORS:
            updates["enabled"] = False

        # TTL check
        if self.is_ttl_expired(job):
            updates["enabled"] = False
            updates["next_run"] = None

        # Delete-after-run marker: signal to caller to remove job
        if result.success and job.delete_after_run:
            updates["_delete_after_run"] = True

        return updates


# ═══════════════════════════════════════════════════════════════
# Execution Log
# ═══════════════════════════════════════════════════════════════


class ExecutionLog:
    """Append-only JSONL execution log with auto-pruning."""

    def __init__(self, log_path: Optional[str] = None):
        self.log_path = log_path or LOG_FILE

    def append(self, entry: LogEntry) -> None:
        """Append a log entry."""
        _ensure_dir(os.path.dirname(self.log_path))
        data = {
            "timestamp": entry.timestamp or _now_iso(),
            "job_id": entry.job_id,
            "job_name": entry.job_name,
            "status": entry.status,
            "duration_ms": entry.duration_ms,
            "output_summary": entry.output_summary[:2000] if entry.output_summary else "",
        }
        if entry.error:
            data["error"] = entry.error[:500]

        line = json.dumps(data, ensure_ascii=False) + "\n"
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line)

        self._prune_if_needed()

    def _prune_if_needed(self) -> None:
        """Prune log if it exceeds size/line limits."""
        try:
            stat = os.stat(self.log_path)
        except OSError:
            return

        if stat.st_size <= LOG_MAX_BYTES:
            return

        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return

        # Keep only the most recent LOG_MAX_LINES
        if len(lines) > LOG_MAX_LINES:
            kept = lines[-LOG_MAX_LINES:]
        else:
            kept = lines

        # Write pruned content atomically
        _atomic_write(self.log_path, "".join(kept))

    def get_recent(self, job_id: Optional[str] = None, limit: int = 50) -> List[LogEntry]:
        """Get recent log entries, optionally filtered by job_id."""
        limit = max(1, min(5000, limit))
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                raw = f.read()
        except (FileNotFoundError, OSError):
            return []

        entries: List[LogEntry] = []
        for line in raw.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if not isinstance(data, dict):
                    continue
                if job_id and data.get("job_id") != job_id:
                    continue
                entries.append(LogEntry(
                    timestamp=data.get("timestamp", ""),
                    job_id=data.get("job_id", ""),
                    job_name=data.get("job_name", ""),
                    status=data.get("status", ""),
                    error=data.get("error", ""),
                    duration_ms=data.get("duration_ms", 0),
                    output_summary=data.get("output_summary", ""),
                ))
            except (json.JSONDecodeError, TypeError):
                continue

        # Return most recent entries
        return entries[-limit:]

    def prune(self) -> None:
        """Force prune the log file."""
        self._prune_if_needed()


# ═══════════════════════════════════════════════════════════════
# Notification Buffer
# ═══════════════════════════════════════════════════════════════


class NotificationBuffer:
    """
    Buffer for notifications to be delivered at next session start.
    Persisted as JSON file.
    """

    def __init__(self, path: Optional[str] = None):
        self.path = path or NOTIFICATIONS_FILE

    def _load(self) -> List[Notification]:
        """Load notifications from file."""
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
            if not isinstance(data, dict):
                return []
            items = data.get("notifications", [])
            if not isinstance(items, list):
                return []
            result = []
            for item in items:
                if isinstance(item, dict):
                    result.append(Notification(
                        id=item.get("id", ""),
                        job_id=item.get("job_id", ""),
                        job_name=item.get("job_name", ""),
                        status=item.get("status", ""),
                        message=item.get("message", ""),
                        timestamp=item.get("timestamp", ""),
                        consumed=item.get("consumed", False),
                    ))
            return result
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []

    def _save(self, notifications: List[Notification]) -> None:
        """Save notifications atomically."""
        data = {
            "notifications": [
                {
                    "id": n.id,
                    "job_id": n.job_id,
                    "job_name": n.job_name,
                    "status": n.status,
                    "message": n.message,
                    "timestamp": n.timestamp,
                    "consumed": n.consumed,
                }
                for n in notifications
            ]
        }
        _atomic_write(self.path, json.dumps(data, indent=2, ensure_ascii=False))

    def add_notification(self, job_id: str, job_name: str, status: str,
                         message: str) -> Notification:
        """Add a notification."""
        notif = Notification(
            id=str(uuid.uuid4()),
            job_id=job_id,
            job_name=job_name,
            status=status,
            message=message,
            timestamp=_now_iso(),
            consumed=False,
        )
        notifications = self._load()
        notifications.append(notif)
        self._save(notifications)
        return notif

    def get_pending(self) -> List[Notification]:
        """Get all unconsumed notifications."""
        return [n for n in self._load() if not n.consumed]

    def mark_consumed(self) -> int:
        """Mark all notifications as consumed. Returns count consumed."""
        notifications = self._load()
        count = 0
        for n in notifications:
            if not n.consumed:
                n.consumed = True
                count += 1
        if count > 0:
            self._save(notifications)
        return count

    def clear_consumed(self) -> int:
        """Remove all consumed notifications. Returns count removed."""
        notifications = self._load()
        remaining = [n for n in notifications if not n.consumed]
        removed = len(notifications) - len(remaining)
        if removed > 0:
            self._save(remaining)
        return removed
