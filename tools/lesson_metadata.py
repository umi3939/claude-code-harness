#!/usr/bin/env python3
"""Lesson metadata management for the Lesson Validation Loop.

Tracks application counts, confidence scores, and validation history
for lessons in lessons_registry.md. The registry file itself is
read-only; all metadata is stored in a separate JSON file.

Key design decisions:
- fail-open: metadata errors never block lesson search
- confidence bounded: 0.1 <= confidence <= 1.0 (default 0.5)
- session dedup: same session_id does not re-increment applied_count
- event log: JSONL append-only for audit trail
- atomic writes: temp file + rename to prevent corruption
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

METADATA_FILENAME = "lesson_metadata.json"
EVENT_LOG_FILENAME = "lesson_events.jsonl"

DEFAULT_CONFIDENCE = 0.5
CONFIDENCE_MIN = 0.1
CONFIDENCE_MAX = 1.0
CONFIDENCE_SUCCESS_DELTA = 0.1
CONFIDENCE_FAILURE_DELTA = -0.15


def generate_lesson_id(lesson_number: int) -> str:
    """Convert a 1-based lesson index to a string ID.

    Args:
        lesson_number: 1-based index from _parse_lessons / sync_lessons.

    Returns:
        String representation of the lesson number.
    """
    return str(lesson_number)


def load_metadata(memory_dir: str) -> dict:
    """Load lesson metadata from JSON file.

    Args:
        memory_dir: Path to the memory directory.

    Returns:
        Dict mapping lesson_id (str) to metadata dict.
        Returns empty dict on missing/corrupt file (fail-open).
    """
    path = os.path.join(memory_dir, METADATA_FILENAME)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if not content.strip():
            return {}
        data = json.loads(content)
        if not isinstance(data, dict):
            logger.warning("lesson_metadata.json is not a dict, returning empty")
            return {}
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load lesson metadata: %s", e)
        return {}


def save_metadata(memory_dir: str, metadata: dict) -> None:
    """Atomically save lesson metadata to JSON file.

    Args:
        memory_dir: Path to the memory directory.
        metadata: Dict mapping lesson_id to metadata dict.
    """
    os.makedirs(memory_dir, exist_ok=True)
    path = os.path.join(memory_dir, METADATA_FILENAME)
    fd, tmp_path = tempfile.mkstemp(
        dir=memory_dir,
        prefix=".lesson_metadata_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _append_event(memory_dir: str, event: dict) -> None:
    """Append an event to the JSONL event log.

    Args:
        memory_dir: Path to the memory directory.
        event: Event dict to append.
    """
    try:
        os.makedirs(memory_dir, exist_ok=True)
        path = os.path.join(memory_dir, EVENT_LOG_FILENAME)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning("Failed to append lesson event: %s", e)


def _ensure_entry(metadata: dict, lesson_id: str) -> dict:
    """Ensure a metadata entry exists for the lesson.

    Args:
        metadata: Full metadata dict (mutated in place).
        lesson_id: String lesson ID.

    Returns:
        The entry dict for this lesson_id.
    """
    if lesson_id not in metadata:
        metadata[lesson_id] = {
            "applied_count": 0,
            "confidence": DEFAULT_CONFIDENCE,
            "last_applied": None,
            "last_applied_session_id": None,
        }
    return metadata[lesson_id]


def record_application(
    memory_dir: str,
    lesson_id: str,
    session_id: str,
) -> dict:
    """Record that a lesson was returned in a memory_search result.

    Increments applied_count and updates last_applied timestamp.
    Skips if the same session_id already applied this lesson (dedup).

    Args:
        memory_dir: Path to the memory directory.
        lesson_id: String lesson ID.
        session_id: Current session identifier for dedup.

    Returns:
        Updated metadata entry for this lesson.
    """
    metadata = load_metadata(memory_dir)
    entry = _ensure_entry(metadata, lesson_id)

    # Session dedup: skip if same session already recorded
    if entry.get("last_applied_session_id") == session_id:
        return entry

    entry["applied_count"] = entry.get("applied_count", 0) + 1
    entry["last_applied"] = datetime.now(timezone.utc).isoformat()
    entry["last_applied_session_id"] = session_id

    save_metadata(memory_dir, metadata)

    _append_event(memory_dir, {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "lesson_id": lesson_id,
        "event": "applied",
        "session_id": session_id,
    })

    return entry


def validate_lesson(
    memory_dir: str,
    lesson_id: str,
    success: bool,
    category: str = "",
) -> dict:
    """Record a validation result for a lesson.

    Updates confidence: +0.1 on success, -0.15 on failure.
    Bounded to [0.1, 1.0].

    Args:
        memory_dir: Path to the memory directory.
        lesson_id: String lesson ID.
        success: True if the lesson proved effective, False otherwise.
        category: Optional pattern category for the validation event.

    Returns:
        Updated metadata entry for this lesson.
    """
    metadata = load_metadata(memory_dir)
    entry = _ensure_entry(metadata, lesson_id)

    delta = CONFIDENCE_SUCCESS_DELTA if success else CONFIDENCE_FAILURE_DELTA
    new_confidence = entry["confidence"] + delta
    new_confidence = max(CONFIDENCE_MIN, min(CONFIDENCE_MAX, new_confidence))
    # Round to avoid floating point drift
    entry["confidence"] = round(new_confidence, 2)

    save_metadata(memory_dir, metadata)

    event_type = "validated" if success else "invalidated"
    _append_event(memory_dir, {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "lesson_id": lesson_id,
        "event": event_type,
        "category": category,
        "success": success,
    })

    return entry


def get_lesson_confidence(metadata: dict, lesson_id: str) -> float:
    """Get the confidence score for a lesson from loaded metadata.

    Args:
        metadata: Loaded metadata dict.
        lesson_id: String lesson ID.

    Returns:
        Confidence score (default 0.5 for untracked lessons).
    """
    entry = metadata.get(lesson_id, {})
    return entry.get("confidence", DEFAULT_CONFIDENCE)
