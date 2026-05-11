"""
Discord receive buffer (FIFO, file-backed) and receive log (JSONL, pruned).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Timeout for file write operations (seconds)
WRITE_TIMEOUT_SECONDS = 10

import contextlib

from discord_receiver_models import (
    DEFAULT_BUFFER_MAX_SIZE,
    DEFAULT_RECEIVE_LOG_MAX_BYTES,
    DEFAULT_RECEIVE_LOG_MAX_LINES,
    RECEIVE_BUFFER_FILE,
    RECEIVE_LOG_FILE,
    STATUS_COMPLETED,
    STATUS_DISCARDED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_PROCESSING,
    BufferEntry,
    ReceiveLogEntry,
    _ensure_dir,
    _now_iso,
)


def _file_lock(filepath: str):
    """Cross-platform file lock context manager.

    Uses msvcrt.locking() on Windows, fcntl.flock() on Unix.
    Lock file is created alongside the target file.
    """
    lock_path = filepath + ".lock"
    _ensure_dir(os.path.dirname(lock_path))

    @contextlib.contextmanager
    def _lock():
        fd = None
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
            if sys.platform == "win32":
                import msvcrt
                # Try to lock with retries
                for _ in range(100):
                    try:
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        import time
                        time.sleep(0.01)
                else:
                    # Last attempt (will raise if still locked)
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


class ReceiveBuffer:
    """FIFO buffer for received messages with file persistence.

    - Entries are stored as JSONL.
    - Active entries (pending/processing) count toward the size limit.
    - Completed/failed/discarded entries are periodically pruned.
    - When buffer is full, new messages are rejected.

    NOTE: Phase 1 loads/parses the full file on each operation. With max_size=100
    this is acceptable. Phase 2 should migrate to an in-memory cache with periodic
    persistence to reduce file I/O under high message throughput.
    """

    def __init__(self, buffer_path: Optional[str] = None,
                 max_size: int = DEFAULT_BUFFER_MAX_SIZE):
        self.buffer_path = buffer_path or RECEIVE_BUFFER_FILE
        self.max_size = max_size

    def _load_all(self) -> List[BufferEntry]:
        """Load all buffer entries from file."""
        entries = []
        try:
            with open(self.buffer_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if isinstance(data, dict):
                            entries.append(BufferEntry.from_dict(data))
                    except (json.JSONDecodeError, TypeError):
                        continue
        except FileNotFoundError:
            pass
        except PermissionError:
            raise
        except OSError as e:
            logger.warning("Failed to load buffer file %s: %s", self.buffer_path, e)
        return entries

    def _save_all(self, entries: List[BufferEntry]) -> None:
        """Save all entries to buffer file (overwrite)."""
        _ensure_dir(os.path.dirname(self.buffer_path))
        with open(self.buffer_path, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")

    def _active_count(self, entries: List[BufferEntry]) -> int:
        """Count active entries (pending or processing)."""
        return sum(1 for e in entries
                   if e.status in (STATUS_PENDING, STATUS_PROCESSING))

    def add(self, entry: BufferEntry) -> bool:
        """Add an entry to the buffer.

        Returns True if added, False if buffer is full.
        Uses file locking to prevent race conditions in concurrent access.
        """
        _ensure_dir(os.path.dirname(self.buffer_path))
        with _file_lock(self.buffer_path):
            entries = self._load_all()

            # Prune old completed/discarded entries first
            self._prune_entries(entries)

            if self._active_count(entries) >= self.max_size:
                return False

            if not entry.id:
                entry.id = str(uuid.uuid4())
            if not entry.received_at:
                entry.received_at = _now_iso()

            entries.append(entry)
            self._save_all(entries)
            return True

    def get_pending(self) -> List[BufferEntry]:
        """Get all pending entries in FIFO order.

        Returns entries with status 'pending' or 'failed' (retriable).
        """
        entries = self._load_all()
        return [e for e in entries
                if e.status in (STATUS_PENDING, STATUS_FAILED)]

    def update_status(self, entry_id: str, status: str,
                      result: str = "") -> bool:
        """Update an entry's status. Returns True if found.
        Uses file locking to prevent race conditions.
        """
        _ensure_dir(os.path.dirname(self.buffer_path))
        with _file_lock(self.buffer_path):
            entries = self._load_all()
            for e in entries:
                if e.id == entry_id:
                    e.status = status
                    if result:
                        e.result = result
                    self._save_all(entries)
                    return True
            return False

    def get_stats(self) -> Dict[str, int]:
        """Get buffer statistics."""
        entries = self._load_all()
        stats: Dict[str, int] = {
            "total": len(entries),
            "pending": 0,
            "processing": 0,
            "completed": 0,
            "failed": 0,
            "discarded": 0,
        }
        for e in entries:
            if e.status in stats:
                stats[e.status] += 1
        return stats

    def _prune_entries(self, entries: List[BufferEntry]) -> None:
        """Remove old completed/failed/discarded entries in-place.

        Keeps at most max_size * 2 total entries to prevent unbounded growth.
        Removes oldest inactive entries first, preserving FIFO order.
        """
        total_limit = self.max_size * 2
        if len(entries) <= total_limit:
            return

        # Identify which inactive entries to remove (oldest first)
        inactive_indices = [
            i for i, e in enumerate(entries)
            if e.status in (STATUS_COMPLETED, STATUS_FAILED, STATUS_DISCARDED)
        ]
        active_count = len(entries) - len(inactive_indices)

        # How many inactive to keep
        keep_inactive = max(0, total_limit - active_count)
        # Remove oldest inactive (keep the last keep_inactive from inactive_indices)
        remove_indices = set(inactive_indices[:len(inactive_indices) - keep_inactive])

        # Rebuild in original order, skipping removed entries
        kept = [e for i, e in enumerate(entries) if i not in remove_indices]
        entries.clear()
        entries.extend(kept)


class ReceiveLog:
    """Append-only JSONL receive log with auto-pruning."""

    def __init__(self, log_path: Optional[str] = None,
                 max_bytes: int = DEFAULT_RECEIVE_LOG_MAX_BYTES,
                 max_lines: int = DEFAULT_RECEIVE_LOG_MAX_LINES):
        self.log_path = log_path or RECEIVE_LOG_FILE
        self.max_bytes = max_bytes
        self.max_lines = max_lines

    def append(self, entry: ReceiveLogEntry) -> None:
        """Append a log entry."""
        _ensure_dir(os.path.dirname(self.log_path))
        if not entry.timestamp:
            entry.timestamp = _now_iso()
        line = json.dumps(entry.to_dict(), ensure_ascii=False) + "\n"
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line)
        self._prune_if_needed()

    def _prune_if_needed(self) -> None:
        """Prune log if it exceeds size limit."""
        try:
            stat = os.stat(self.log_path)
        except FileNotFoundError:
            return
        except PermissionError:
            raise
        except OSError:
            return
        if stat.st_size <= self.max_bytes:
            return
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return
        except PermissionError:
            raise
        except OSError:
            return
        if len(lines) > self.max_lines:
            kept = lines[-self.max_lines:]
            with open(self.log_path, "w", encoding="utf-8") as f:
                f.writelines(kept)

    def get_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent log entries."""
        limit = max(1, min(5000, limit))
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                raw = f.read()
        except FileNotFoundError:
            return []
        except PermissionError:
            raise
        except OSError as e:
            logger.warning("Failed to read log file %s: %s", self.log_path, e)
            return []
        entries = []
        for line in raw.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if isinstance(data, dict):
                    entries.append(data)
            except (json.JSONDecodeError, TypeError):
                continue
        return entries[-limit:]
