"""
Message Event Hook Dispatcher.

Provides structured extension points (hook points) at each stage of the
message processing pipeline. Hooks are external processes that receive
event context via stdin JSON and execute without blocking the pipeline.

This is a generic Claude Code utility, not part of any specific project.

Event lifecycle (matches actual implementation order):
  received -> filtered -> buffered -> sanitized -> sent

Safety valves:
  - Per-hook timeout (subprocess killed on exceed)
  - Global timeout (remaining hooks skipped)
  - Consecutive failure auto-disable
  - Debounce (suppress rapid re-fire of same hook+event)
  - Reentry prevention (_dispatch_depth)
  - fire_and_forget default (non-blocking)
  - Graceful shutdown (kill tracked subprocesses)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════

EVENT_RECEIVED = "message:received"
EVENT_FILTERED = "message:filtered"
EVENT_BUFFERED = "message:buffered"
EVENT_SANITIZED = "message:sanitized"
EVENT_SENT = "message:sent"

ALL_EVENTS = frozenset({
    EVENT_RECEIVED,
    EVENT_FILTERED,
    EVENT_BUFFERED,
    EVENT_SANITIZED,
    EVENT_SENT,
})

# Default safety valve values
DEFAULT_HOOK_TIMEOUT = 10.0          # seconds per hook
DEFAULT_GLOBAL_TIMEOUT = 30.0        # seconds for all hooks in one event
DEFAULT_CONSECUTIVE_FAILURE_LIMIT = 5
DEFAULT_DEBOUNCE_SECONDS = 0.5       # suppress same hook+event within this window
DEFAULT_LOG_MAX_LINES = 500

# Project root (tools/ の親ディレクトリ)
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))

from file_io import resolve_project_root  # noqa: E402

_PROJECT_ROOT = resolve_project_root()

# Paths
DISCORD_DATA_DIR = os.path.join(_PROJECT_ROOT, "discord_data")
DEFAULT_HOOK_CONFIG_PATH = os.path.join(DISCORD_DATA_DIR, "message_hooks.json")
DEFAULT_HOOK_LOG_PATH = os.path.join(DISCORD_DATA_DIR, "message_hook_log.jsonl")


# ═══════════════════════════════════════════════════════════════
# MessageEventContext
# ═══════════════════════════════════════════════════════════════

@dataclass
class MessageEventContext:
    """Normalized message event context passed to hooks via stdin JSON."""
    event: str                    # One of ALL_EVENTS
    source: str                   # Message source (e.g. "discord")
    sender_id: str                # Source-specific user ID
    channel_id: str               # Source-specific channel ID
    message_id: str               # Source-specific message ID
    content: str                  # Message text (raw or sanitized depending on stage)
    timestamp: str                # ISO-8601 receive time
    conversation_type: str        # "dm" or "channel"
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Event-specific optional fields
    filter_passed: Optional[bool] = None
    filter_reason: Optional[str] = None
    sanitize_findings: Optional[List[str]] = None
    buffer_entry_id: Optional[str] = None
    send_success: Optional[bool] = None
    send_error: Optional[str] = None


# ═══════════════════════════════════════════════════════════════
# HookDefinition
# ═══════════════════════════════════════════════════════════════

@dataclass
class HookDefinition:
    """Declaration of a single hook."""
    id: str                              # Unique identifier
    events: List[str]                    # Events to trigger on
    command: str                         # Shell command to execute
    enabled: bool = True
    timeout: float = DEFAULT_HOOK_TIMEOUT
    source_filter: Optional[List[str]] = None  # None = all sources


def load_hook_definitions(path: str) -> List[HookDefinition]:
    """Load hook definitions from a JSON config file.

    Returns empty list if file is missing or invalid.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []

    hooks_data = data.get("hooks", [])
    result = []
    for h in hooks_data:
        try:
            hd = HookDefinition(
                id=h["id"],
                events=h["events"],
                command=h["command"],
                enabled=h.get("enabled", True),
                timeout=h.get("timeout", DEFAULT_HOOK_TIMEOUT),
                source_filter=h.get("source_filter", None),
            )
            result.append(hd)
        except (KeyError, TypeError):
            continue
    return result


# ═══════════════════════════════════════════════════════════════
# HookExecutionRecord
# ═══════════════════════════════════════════════════════════════

@dataclass
class HookExecutionRecord:
    """Record of a single hook execution."""
    timestamp: str
    hook_id: str
    event: str
    success: bool
    duration_ms: float
    exit_code: int
    stdout: str
    stderr: str
    error: str


# ═══════════════════════════════════════════════════════════════
# HookExecutionLog (JSONL + auto-prune + asyncio.Lock)
# ═══════════════════════════════════════════════════════════════

class HookExecutionLog:
    """Append-only JSONL log with auto-pruning."""

    def __init__(self, path: Optional[str], max_lines: int = DEFAULT_LOG_MAX_LINES):
        self._path = path
        self._max_lines = max_lines
        self._lock = asyncio.Lock()

    async def append(self, record: HookExecutionRecord) -> None:
        """Append a record to the log. Auto-prune if over max_lines."""
        if self._path is None:
            return

        async with self._lock:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self._path), exist_ok=True)

            # Append
            line = json.dumps(asdict(record), ensure_ascii=False) + "\n"
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line)

            # Prune if needed
            self._prune_sync()

    def _prune_sync(self) -> None:
        """Prune log to max_lines (keep newest)."""
        if self._path is None:
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > self._max_lines:
                keep = lines[-self._max_lines:]
                with open(self._path, "w", encoding="utf-8") as f:
                    f.writelines(keep)
        except (FileNotFoundError, OSError):
            pass

    def read_all(self) -> List[Dict[str, Any]]:
        """Read all log entries."""
        if self._path is None:
            return []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            result = []
            for line in lines:
                line = line.strip()
                if line:
                    result.append(json.loads(line))
            return result
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []


# ═══════════════════════════════════════════════════════════════
# HookDispatcher
# ═══════════════════════════════════════════════════════════════

class HookDispatcher:
    """Dispatches hooks for message events.

    Safety valves:
    - Per-hook timeout
    - Global timeout (all hooks for one event)
    - Debounce (suppress rapid re-fire)
    - Reentry prevention (_dispatch_depth counter)
    - Consecutive failure auto-disable
    - fire_and_forget mode (default)
    - Graceful shutdown (kill tracked subprocesses)
    """

    def __init__(
        self,
        hooks: List[HookDefinition],
        log_path: Optional[str],
        global_timeout: float = DEFAULT_GLOBAL_TIMEOUT,
        consecutive_failure_limit: int = DEFAULT_CONSECUTIVE_FAILURE_LIMIT,
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
        log_max_lines: int = DEFAULT_LOG_MAX_LINES,
        logger=None,
    ):
        self._hooks = hooks
        self._log = HookExecutionLog(log_path, max_lines=log_max_lines)
        self._global_timeout = global_timeout
        self._consecutive_failure_limit = consecutive_failure_limit
        self._debounce_seconds = debounce_seconds
        self._logger = logger

        # Runtime state (volatile)
        self._dispatch_depth = 0
        self._last_dispatch_time: Dict[Tuple[str, str], float] = {}  # (hook_id, event) -> monotonic
        self._consecutive_failures: Dict[str, int] = {}               # hook_id -> count
        self._auto_disabled: Set[str] = set()                         # hook_ids
        self._running_processes: List[asyncio.subprocess.Process] = []
        self._background_tasks: List[asyncio.Task] = []

    def _log_msg(self, level: str, msg: str) -> None:
        if self._logger:
            getattr(self._logger, level, self._logger.info)(msg)

    # ── Hook selection ────────────────────────────────────────

    def _select_hooks(self, event: str, source: str) -> List[HookDefinition]:
        """Select enabled hooks matching event and source."""
        result = []
        for h in self._hooks:
            if not h.enabled:
                continue
            if h.id in self._auto_disabled:
                continue
            if event not in h.events:
                continue
            if h.source_filter is not None and source not in h.source_filter:
                continue
            result.append(h)
        return result

    def _apply_debounce(self, hooks: List[HookDefinition], event: str) -> List[HookDefinition]:
        """Filter out hooks that fired too recently."""
        if self._debounce_seconds <= 0:
            return hooks
        now = time.monotonic()
        result = []
        for h in hooks:
            key = (h.id, event)
            last = self._last_dispatch_time.get(key, 0.0)
            if (now - last) >= self._debounce_seconds:
                result.append(h)
        return result

    # ── Single hook execution ─────────────────────────────────

    async def _execute_single_hook(
        self, hook: HookDefinition, ctx: MessageEventContext
    ) -> HookExecutionRecord:
        """Execute a single hook as a subprocess, passing context via stdin JSON."""
        start_time = time.monotonic()
        timestamp = datetime.now(timezone.utc).isoformat()
        stdin_data = json.dumps(asdict(ctx), ensure_ascii=False).encode("utf-8")

        try:
            # Parse command safely with shlex.split (no shell injection)
            cmd_parts = shlex.split(hook.command)
            proc = await asyncio.create_subprocess_exec(
                cmd_parts[0], *cmd_parts[1:],
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._running_processes.append(proc)

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(input=stdin_data),
                    timeout=hook.timeout,
                )
            except asyncio.TimeoutError:
                # Kill the process on timeout
                try:
                    proc.kill()
                    await proc.wait()
                except ProcessLookupError:
                    pass
                finally:
                    if proc in self._running_processes:
                        self._running_processes.remove(proc)

                duration_ms = (time.monotonic() - start_time) * 1000
                return HookExecutionRecord(
                    timestamp=timestamp,
                    hook_id=hook.id,
                    event=ctx.event,
                    success=False,
                    duration_ms=duration_ms,
                    exit_code=-1,
                    stdout="",
                    stderr="",
                    error=f"Timeout after {hook.timeout}s",
                )
            finally:
                if proc in self._running_processes:
                    self._running_processes.remove(proc)

            duration_ms = (time.monotonic() - start_time) * 1000
            stdout_str = stdout_bytes.decode("utf-8", errors="replace").strip()
            stderr_str = stderr_bytes.decode("utf-8", errors="replace").strip()
            exit_code = proc.returncode or 0

            # Truncate output for log
            max_output = 2000
            stdout_log = stdout_str[:max_output]
            stderr_log = stderr_str[:max_output]

            return HookExecutionRecord(
                timestamp=timestamp,
                hook_id=hook.id,
                event=ctx.event,
                success=(exit_code == 0),
                duration_ms=duration_ms,
                exit_code=exit_code,
                stdout=stdout_log,
                stderr=stderr_log,
                error="" if exit_code == 0 else f"exit code {exit_code}",
            )

        except (FileNotFoundError, PermissionError, OSError, ValueError) as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            return HookExecutionRecord(
                timestamp=timestamp,
                hook_id=hook.id,
                event=ctx.event,
                success=False,
                duration_ms=duration_ms,
                exit_code=-1,
                stdout="",
                stderr="",
                error=str(e),
            )
        except Exception as e:
            # Unexpected exception — log it so security-related errors aren't hidden
            logger.warning(
                "Unexpected error executing hook %s: %s", hook.id, e, exc_info=True
            )
            duration_ms = (time.monotonic() - start_time) * 1000
            return HookExecutionRecord(
                timestamp=timestamp,
                hook_id=hook.id,
                event=ctx.event,
                success=False,
                duration_ms=duration_ms,
                exit_code=-1,
                stdout="",
                stderr="",
                error=str(e),
            )

    # ── Dispatch ──────────────────────────────────────────────

    async def dispatch(
        self,
        ctx: MessageEventContext,
        fire_and_forget: bool = True,
    ) -> bool:
        """Dispatch hooks for the given event context.

        Args:
            ctx: The event context.
            fire_and_forget: If True (default), run hooks in background
                             and return immediately. If False, wait for
                             all hooks to complete.

        Returns:
            True if dispatch was accepted, False if rejected (reentry).
        """
        # Reentry prevention
        if self._dispatch_depth > 0:
            self._log_msg("warning", f"Reentry rejected for {ctx.event} (depth={self._dispatch_depth})")
            return False

        # Select and debounce hooks
        selected = self._select_hooks(ctx.event, ctx.source)
        selected = self._apply_debounce(selected, ctx.event)

        if not selected:
            return True

        if fire_and_forget:
            task = asyncio.create_task(self._run_hooks(selected, ctx))
            self._background_tasks.append(task)
            task.add_done_callback(lambda t: self._background_tasks.remove(t) if t in self._background_tasks else None)
            return True
        else:
            await self._run_hooks(selected, ctx)
            return True

    async def _run_hooks(
        self, hooks: List[HookDefinition], ctx: MessageEventContext
    ) -> None:
        """Run selected hooks sequentially with global timeout."""
        self._dispatch_depth += 1
        global_start = time.monotonic()

        try:
            for hook in hooks:
                # Check global timeout
                elapsed = time.monotonic() - global_start
                if elapsed >= self._global_timeout:
                    self._log_msg("warning",
                        f"Global timeout ({self._global_timeout}s) reached, "
                        f"skipping remaining hooks for {ctx.event}")
                    break

                # Execute
                record = await self._execute_single_hook(hook, ctx)

                # Update debounce timestamp
                self._last_dispatch_time[(hook.id, ctx.event)] = time.monotonic()

                # Log
                await self._log.append(record)

                # Update consecutive failure tracking
                if record.success:
                    self._consecutive_failures[hook.id] = 0
                else:
                    count = self._consecutive_failures.get(hook.id, 0) + 1
                    self._consecutive_failures[hook.id] = count
                    if count >= self._consecutive_failure_limit:
                        self._auto_disabled.add(hook.id)
                        self._log_msg("warning",
                            f"Hook '{hook.id}' auto-disabled after "
                            f"{count} consecutive failures")
        finally:
            self._dispatch_depth -= 1

    # ── Shutdown ──────────────────────────────────────────────

    async def shutdown(self) -> None:
        """Kill all running hook subprocesses and cancel background tasks."""
        # Kill running subprocesses
        for proc in list(self._running_processes):
            try:
                proc.kill()
                await proc.wait()
            except (ProcessLookupError, OSError):
                pass
        self._running_processes.clear()

        # Cancel background tasks
        for task in list(self._background_tasks):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._background_tasks.clear()


# ═══════════════════════════════════════════════════════════════
# Discord normalization helper
# ═══════════════════════════════════════════════════════════════

def normalize_discord_message(
    event: str,
    message_data: Dict[str, Any],
    is_dm: bool,
    filter_passed: Optional[bool] = None,
    filter_reason: Optional[str] = None,
    sanitize_findings: Optional[List[str]] = None,
    buffer_entry_id: Optional[str] = None,
    send_success: Optional[bool] = None,
    send_error: Optional[str] = None,
) -> MessageEventContext:
    """Convert Discord-specific message data to MessageEventContext."""
    author = message_data.get("author", {})
    sender_id = author.get("id", "")
    author_name = author.get("username", "")

    metadata: Dict[str, Any] = {}
    if author_name:
        metadata["author_name"] = author_name
    discriminator = author.get("discriminator")
    if discriminator:
        metadata["discriminator"] = discriminator
    guild_id = message_data.get("guild_id")
    if guild_id:
        metadata["guild_id"] = guild_id

    return MessageEventContext(
        event=event,
        source="discord",
        sender_id=sender_id,
        channel_id=message_data.get("channel_id", ""),
        message_id=message_data.get("id", ""),
        content=message_data.get("content", ""),
        timestamp=message_data.get("timestamp", datetime.now(timezone.utc).isoformat()),
        conversation_type="dm" if is_dm else "channel",
        metadata=metadata,
        filter_passed=filter_passed,
        filter_reason=filter_reason,
        sanitize_findings=sanitize_findings,
        buffer_entry_id=buffer_entry_id,
        send_success=send_success,
        send_error=send_error,
    )
