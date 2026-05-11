"""Common file I/O utilities for JSON and JSONL operations.

Stateless utility functions. No business logic, no logging, no project imports.
All functions accept file paths from callers — this module never decides paths.
"""

import contextlib
import json
import os
import shutil
import sys
import tempfile
from typing import Any

# ---------------------------------------------------------------------------
# File locking (cross-platform)
# ---------------------------------------------------------------------------


def _file_lock(filepath: str):
    """Cross-platform file lock context manager.

    Uses msvcrt.locking() on Windows, fcntl.flock() on Unix.
    Lock file is created alongside the target file.
    """
    lock_path = filepath + ".lock"
    parent = os.path.dirname(lock_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    @contextlib.contextmanager
    def _lock():
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
                        import time
                        time.sleep(0.01)
                else:
                    import msvcrt as _msvcrt
                    _msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
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


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def safe_load_json(path: str, default: Any = None) -> Any:
    """Load a JSON file. Return *default* on missing file or parse error.

    No type checking is performed — the caller is responsible for validating
    the returned value.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, OSError):
        return default


def atomic_write_json(path: str, data: Any, indent: int = 2) -> None:
    """Write JSON atomically via tempfile + os.replace.

    Creates parent directories if needed.  On Windows, if ``os.replace`` fails
    (e.g. target locked), falls back to shutil.copy2 + os.unlink.  The
    tempfile is always cleaned up — no orphans are left behind.
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=parent or ".",
        prefix=".fileio_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        # Primary: atomic rename
        try:
            os.replace(tmp_path, path)
        except OSError:
            # Windows fallback: copy + unlink
            shutil.copy2(tmp_path, path)
            os.unlink(tmp_path)
    except Exception:
        # Clean up tempfile on any failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# JSONL
# ---------------------------------------------------------------------------


def safe_load_jsonl(path: str) -> list[Any]:
    """Load a JSONL file, skipping blank and unparseable lines.

    Returns an empty list if the file does not exist.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except (FileNotFoundError, OSError):
        return []

    results: list[Any] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            results.append(json.loads(stripped))
        except (json.JSONDecodeError, ValueError):
            continue
    return results


def append_jsonl(path: str, entry: Any) -> None:
    """Append a single JSON-serialised line to a JSONL file.

    Creates parent directories if needed.  The file is created if it does not
    exist.

    Uses cross-platform file locking to prevent interleaving when multiple
    processes append concurrently.
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    with _file_lock(path):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def rotate_jsonl(path: str, max_lines: int = 1000) -> bool:
    """Remove oldest lines from a JSONL file, keeping *max_lines* newest.

    Returns ``True`` if rotation actually occurred, ``False`` otherwise
    (including when the file does not exist or is within the limit).

    Uses atomic write-back to avoid partial truncation.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except (FileNotFoundError, OSError):
        return False

    if len(lines) <= max_lines:
        return False

    # Keep the tail (newest entries)
    kept = lines[-max_lines:]

    # Atomic write-back
    parent = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(
        dir=parent or ".",
        prefix=".rotate_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.writelines(kept)
        try:
            os.replace(tmp_path, path)
        except OSError:
            shutil.copy2(tmp_path, path)
            os.unlink(tmp_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return True


# ---------------------------------------------------------------------------
# Project root resolution
# ---------------------------------------------------------------------------


def resolve_project_root() -> str:
    """Resolve the project root directory path.

    Resolution order:
    1. ``CLAUDE_PROJECT_ROOT`` env var (if set, non-empty after strip, and valid)
    2. Fallback: parent of this file's directory (``<.claude>``)

    Validation rules for the env value (design doc §3.3):
    - None / empty / whitespace-only -> fallback (no exception)
    - Relative path -> RuntimeError (message contains 'absolute')
    - Absolute, but path does not exist -> RuntimeError ('does not exist')
    - Absolute, exists, but not a directory -> RuntimeError ('directory')
    - Absolute, existing directory -> normalized env value

    Pure function: no file/dir creation, no logging, no side effects.
    Distinct from ``resolve_memory_dir`` which DOES create the directory.
    """
    raw = os.environ.get("CLAUDE_PROJECT_ROOT")
    if raw is None or raw.strip() == "":
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if not os.path.isabs(raw):
        raise RuntimeError(
            f"CLAUDE_PROJECT_ROOT='{raw}' must be an absolute path."
        )

    if not os.path.exists(raw):
        raise RuntimeError(
            f"CLAUDE_PROJECT_ROOT='{raw}' does not exist."
        )

    if not os.path.isdir(raw):
        raise RuntimeError(
            f"CLAUDE_PROJECT_ROOT='{raw}' is not a directory."
        )

    return os.path.normpath(raw)


# ---------------------------------------------------------------------------
# Memory directory resolution
# ---------------------------------------------------------------------------


def resolve_memory_dir() -> str:
    """Resolve the memory directory path.

    Resolution order:
    1. ``MEMORY_DIR`` environment variable (if set and non-empty)
    2. Global default: ``<project_root>/memory/`` — created if it does not exist.

    When ``MEMORY_DIR`` is set, the directory must already exist; otherwise
    ``RuntimeError`` is raised.
    """
    env_val = os.environ.get("MEMORY_DIR", "").strip()
    if env_val:
        if not os.path.isdir(env_val):
            raise RuntimeError(
                f"MEMORY_DIR is set to '{env_val}' but the directory does not exist."
            )
        return env_val

    # Global default: project root / memory
    _tools_dir = os.path.dirname(os.path.abspath(__file__))
    _project_root = os.path.dirname(_tools_dir)
    global_dir = os.path.join(_project_root, "memory")
    os.makedirs(global_dir, exist_ok=True)
    return global_dir
