#!/usr/bin/env python3
"""Success Trajectory Library — stores and retrieves reusable execution trajectories.

Records successful task execution sequences (decision steps, tools, approaches)
for later retrieval and reuse. Frequently-used trajectories become "Golden Paths"
that serve as templates for similar future tasks.

Key design decisions:
- fail-open: store errors never block other operations
- append-only: no deletion, oldest evicted at cap
- atomic writes: temp file + rename to prevent corruption
- independent storage: trajectories.json, no existing data modified
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

STORE_FILENAME = "trajectories.json"
MAX_TRAJECTORIES = 200
MAX_STEPS = 20
MAX_FIELD_LEN = 500


def load_store(memory_dir: str) -> list:
    """Load trajectory store from JSON file.

    Args:
        memory_dir: Path to the memory directory.

    Returns:
        List of trajectory dicts.
        Returns empty list on missing/corrupt file (fail-open).
    """
    path = os.path.join(memory_dir, STORE_FILENAME)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if not content.strip():
            return []
        data = json.loads(content)
        if not isinstance(data, list):
            logger.warning("trajectories.json is not a list, returning empty")
            return []
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load trajectory store: %s", e)
        return []


def save_store(memory_dir: str, records: list) -> None:
    """Atomically save trajectory store to JSON file.

    Args:
        memory_dir: Path to the memory directory.
        records: List of trajectory dicts.
    """
    os.makedirs(memory_dir, exist_ok=True)
    path = os.path.join(memory_dir, STORE_FILENAME)
    fd, tmp_path = tempfile.mkstemp(
        dir=memory_dir,
        prefix=".trajectories_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _truncate(text: str, max_len: int = MAX_FIELD_LEN) -> str:
    """Truncate text to max_len characters."""
    return text[:max_len]


def _sanitize_step(step: dict) -> dict:
    """Truncate all string fields in a step dict."""
    return {
        "action": _truncate(str(step.get("action", ""))),
        "tool": _truncate(str(step.get("tool", ""))),
        "approach": _truncate(str(step.get("approach", ""))),
        "result": _truncate(str(step.get("result", ""))),
    }


def record_trajectory(
    memory_dir: str,
    task_class: str,
    steps: list,
    outcome: str,
    transferability: float = 0.5,
) -> dict:
    """Record a successful execution trajectory.

    Args:
        memory_dir: Path to the memory directory.
        task_class: Task classification (e.g. "hook_implementation", "mcp_tool_creation").
        steps: List of step dicts with {action, tool, approach, result}.
        outcome: Final result text.
        transferability: How transferable to other tasks (0.0-1.0).

    Returns:
        The created trajectory record dict.

    Raises:
        ValueError: If task_class or outcome is empty.
    """
    if not task_class or not task_class.strip():
        raise ValueError("task_class must not be empty")
    if not outcome or not outcome.strip():
        raise ValueError("outcome must not be empty")

    # Clamp transferability to [0.0, 1.0]
    transferability = max(0.0, min(1.0, transferability))

    # Truncate fields
    task_class = _truncate(task_class)
    outcome = _truncate(outcome)

    # Limit and sanitize steps
    sanitized_steps = [_sanitize_step(s) for s in steps[:MAX_STEPS]]

    records = load_store(memory_dir)

    # Sequential ID: max existing + 1
    max_id = max((r.get("id", 0) for r in records), default=0)
    new_id = max_id + 1

    record = {
        "id": new_id,
        "task_class": task_class,
        "steps": sanitized_steps,
        "outcome": outcome,
        "transferability": transferability,
        "usage_count": 0,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }

    records.append(record)

    # Enforce max cap (evict oldest)
    if len(records) > MAX_TRAJECTORIES:
        records = records[len(records) - MAX_TRAJECTORIES:]

    save_store(memory_dir, records)
    return record


def find_similar(
    memory_dir: str,
    task_class: str,
    limit: int = 3,
) -> list:
    """Find trajectories matching a task class, ordered by usage_count descending.

    Args:
        memory_dir: Path to the memory directory.
        task_class: Task class to filter by (exact match).
        limit: Maximum number of results.

    Returns:
        List of matching trajectory dicts, sorted by usage_count descending.
    """
    records = load_store(memory_dir)
    matched = [r for r in records if r.get("task_class") == task_class]
    matched.sort(key=lambda r: r.get("usage_count", 0), reverse=True)
    return matched[:limit]


def increment_usage(memory_dir: str, trajectory_id: int) -> dict | None:
    """Increment the usage_count of a trajectory.

    Args:
        memory_dir: Path to the memory directory.
        trajectory_id: ID of the trajectory to increment.

    Returns:
        Updated trajectory dict, or None if not found.
    """
    records = load_store(memory_dir)
    for rec in records:
        if rec.get("id") == trajectory_id:
            rec["usage_count"] = rec.get("usage_count", 0) + 1
            save_store(memory_dir, records)
            return rec
    return None


def get_golden_paths(memory_dir: str, min_usage: int = 3) -> list:
    """Get trajectories that qualify as Golden Paths (high usage).

    Args:
        memory_dir: Path to the memory directory.
        min_usage: Minimum usage_count to qualify (default 3).

    Returns:
        List of golden path trajectory dicts, sorted by usage_count descending.
    """
    records = load_store(memory_dir)
    golden = [r for r in records if r.get("usage_count", 0) >= min_usage]
    golden.sort(key=lambda r: r.get("usage_count", 0), reverse=True)
    return golden
