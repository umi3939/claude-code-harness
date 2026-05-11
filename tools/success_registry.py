#!/usr/bin/env python3
"""Success Pattern Registry — structured recording and search of success patterns.

Counterpart to lessons_registry (failure patterns). Records what worked and why,
enabling pattern reuse across sessions.

Key design decisions:
- fail-open: registry errors never block other operations
- append-only: no deletion, oldest evicted at cap
- atomic writes: temp file + rename to prevent corruption
- independent storage: success_patterns.json, no existing data modified
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

REGISTRY_FILENAME = "success_patterns.json"
VALID_EVENT_TYPES = ("review_zero", "test_pass", "user_positive")
MAX_RECORDS = 500
MAX_CONTEXT_LEN = 500
MAX_WHY_SUCCESS_LEN = 1000
MAX_TAG_LEN = 50
MAX_TAGS = 10


def load_registry(memory_dir: str) -> list:
    """Load success pattern registry from JSON file.

    Args:
        memory_dir: Path to the memory directory.

    Returns:
        List of success record dicts.
        Returns empty list on missing/corrupt file (fail-open).
    """
    path = os.path.join(memory_dir, REGISTRY_FILENAME)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if not content.strip():
            return []
        data = json.loads(content)
        if not isinstance(data, list):
            logger.warning("success_patterns.json is not a list, returning empty")
            return []
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load success registry: %s", e)
        return []


def save_registry(memory_dir: str, records: list) -> None:
    """Atomically save success pattern registry to JSON file.

    Args:
        memory_dir: Path to the memory directory.
        records: List of success record dicts.
    """
    os.makedirs(memory_dir, exist_ok=True)
    path = os.path.join(memory_dir, REGISTRY_FILENAME)
    fd, tmp_path = tempfile.mkstemp(
        dir=memory_dir,
        prefix=".success_patterns_",
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


def record_success(
    memory_dir: str,
    event_type: str,
    context: str,
    why_success: str,
    tags: list | None = None,
) -> dict:
    """Record a success pattern.

    Args:
        memory_dir: Path to the memory directory.
        event_type: One of review_zero, test_pass, user_positive.
        context: Description of what happened (max 500 chars).
        why_success: Analysis of why it succeeded (max 1000 chars).
        tags: Optional list of tags (max 10 tags, each max 50 chars).

    Returns:
        The created record dict.

    Raises:
        ValueError: If event_type is not in the allowed list.
    """
    if event_type not in VALID_EVENT_TYPES:
        raise ValueError(
            f"event_type must be one of {VALID_EVENT_TYPES}, got: {event_type!r}"
        )

    if tags is None:
        tags = []

    # Apply field length limits (truncate, not reject)
    context = context[:MAX_CONTEXT_LEN]
    why_success = why_success[:MAX_WHY_SUCCESS_LEN]
    tags = [t[:MAX_TAG_LEN] for t in tags[:MAX_TAGS]]

    records = load_registry(memory_dir)

    # Sequential ID: max existing + 1
    max_id = max((r.get("id", 0) for r in records), default=0)
    new_id = max_id + 1

    record = {
        "id": new_id,
        "event_type": event_type,
        "context": context,
        "why_success": why_success,
        "tags": tags,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }

    records.append(record)

    # Enforce max records cap (remove oldest)
    if len(records) > MAX_RECORDS:
        records = records[len(records) - MAX_RECORDS:]

    save_registry(memory_dir, records)
    return record


def search_successes(
    memory_dir: str,
    query: str = "",
    tags: list | None = None,
    limit: int = 10,
) -> list:
    """Search success patterns by keyword and/or tags.

    Args:
        memory_dir: Path to the memory directory.
        query: Text to match against context and why_success (case-insensitive).
        tags: Optional list of tags to filter by (all must match).
        limit: Maximum number of results.

    Returns:
        List of matching record dicts, newest first.
    """
    records = load_registry(memory_dir)

    results = []
    query_lower = query.lower() if query else ""

    for rec in records:
        # Tag filter: all specified tags must be present
        if tags:
            rec_tags = rec.get("tags", [])
            if not all(t in rec_tags for t in tags):
                continue

        # Query filter: AND-search — all words must appear in context or why_success
        if query_lower:
            ctx = rec.get("context", "").lower()
            why = rec.get("why_success", "").lower()
            combined = ctx + " " + why
            words = query_lower.split()
            if not all(w in combined for w in words):
                continue

        results.append(rec)

    # Newest first
    results.reverse()
    return results[:limit]


def get_stats(memory_dir: str) -> dict:
    """Get success pattern statistics by event type.

    Args:
        memory_dir: Path to the memory directory.

    Returns:
        Dict with total count and per-event-type counts.
    """
    records = load_registry(memory_dir)
    stats = {
        "total": len(records),
        "review_zero": 0,
        "test_pass": 0,
        "user_positive": 0,
    }
    for rec in records:
        et = rec.get("event_type", "")
        if et in stats:
            stats[et] += 1
    return stats
