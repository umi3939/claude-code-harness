#!/usr/bin/env python3
"""Semantic Lesson Injection - surface relevant lessons based on work context.

Given a context string (task description, file paths, etc.), searches the
lessons registry for matching entries and ranks them by confidence score
from lesson_metadata.

Design decisions:
- fail-open: any error returns empty list, never raises
- read-only: never modifies lesson data
- existing search ranking unchanged: confidence only affects display order
- low confidence (< 0.3) flagged with warning
"""

import logging
import os
import re
import sys

logger = logging.getLogger(__name__)

# Ensure we can import sibling modules
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

import lesson_metadata
import lessons_registry

# Confidence threshold below which lessons get a warning flag
LOW_CONFIDENCE_THRESHOLD = 0.3

# Minimum keyword length to avoid noise from short words
MIN_KEYWORD_LENGTH = 3

# Stop words to filter out from context tokenization
_STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "and", "or", "but", "if",
    "then", "else", "when", "at", "by", "for", "with", "about", "from",
    "to", "in", "on", "of", "not", "no", "this", "that", "it", "its",
})


def _tokenize_context(context: str) -> list:
    """Extract meaningful keywords from context text.

    Args:
        context: Free-form text describing the current work context.

    Returns:
        List of lowercase keyword strings, deduplicated, stop words removed.
    """
    # Split on non-alphanumeric characters (keep unicode letters)
    tokens = re.split(r'[^a-zA-Z0-9\u3000-\u9fff\uff00-\uffef]+', context.lower())
    seen = set()
    result = []
    for token in tokens:
        if (
            len(token) >= MIN_KEYWORD_LENGTH
            and token not in _STOP_WORDS
            and token not in seen
        ):
            seen.add(token)
            result.append(token)
    return result


def _load_entries(memory_dir: str) -> list:
    """Load lesson entries from the registry file.

    Args:
        memory_dir: Path to the memory directory.

    Returns:
        List of entry dicts from the lessons registry.
        Empty list on any error (fail-open).
    """
    try:
        lessons_path = lessons_registry.get_lessons_path(memory_dir)
        if not lessons_path.exists():
            return []
        text = lessons_path.read_text(encoding="utf-8")
        return lessons_registry._markdown_to_entries(text)
    except Exception as e:
        logger.warning("Failed to load lesson entries: %s", e)
        return []


def find_relevant_lessons(
    memory_dir: str,
    context: str,
    limit: int = 5,
) -> list:
    """Find lessons relevant to the given work context.

    Tokenizes the context into keywords, searches each lesson's fields
    for matches, then ranks results by confidence score from metadata.

    Args:
        memory_dir: Path to the memory directory.
        context: Free-form text describing current work context.
        limit: Maximum number of results to return.

    Returns:
        List of dicts: [{lesson_id, lesson_text, confidence, applied_count, warning?}]
        Sorted by confidence descending. Empty list on any error.
    """
    try:
        keywords = _tokenize_context(context)
        if not keywords:
            return []

        entries = _load_entries(memory_dir)
        if not entries:
            return []

        metadata = lesson_metadata.load_metadata(memory_dir)

        matches = []
        for i, entry in enumerate(entries, 1):
            lesson_id = str(i)
            # Build searchable text from all fields
            searchable = " ".join([
                entry.get("action", ""),
                entry.get("why", ""),
                entry.get("fix", ""),
                entry.get("lesson", ""),
                entry.get("rule", ""),
            ]).lower()

            # Check if any keyword matches
            matched = any(kw in searchable for kw in keywords)
            if not matched:
                continue

            confidence = lesson_metadata.get_lesson_confidence(metadata, lesson_id)
            meta_entry = metadata.get(lesson_id, {})
            applied_count = meta_entry.get("applied_count", 0)

            result_item = {
                "lesson_id": lesson_id,
                "lesson_text": entry.get("lesson", ""),
                "confidence": confidence,
                "applied_count": applied_count,
            }

            if confidence < LOW_CONFIDENCE_THRESHOLD:
                result_item["warning"] = "\u8981\u691c\u8a3c"

            matches.append(result_item)

        # Sort by confidence descending
        matches.sort(key=lambda x: x["confidence"], reverse=True)

        return matches[:limit]

    except Exception as e:
        logger.warning("find_relevant_lessons failed (fail-open): %s", e)
        return []


def format_injection(lessons: list) -> str:
    """Format a list of lesson results into human-readable injection text.

    Args:
        lessons: List of dicts from find_relevant_lessons.

    Returns:
        Formatted string for context injection, or empty string if no lessons.
    """
    if not lessons:
        return ""

    lines = ["=== Relevant Lessons ==="]
    for i, lesson in enumerate(lessons, 1):
        lesson_id = lesson.get("lesson_id", "?")
        confidence = lesson.get("confidence", 0.5)
        text = lesson.get("lesson_text", "")
        warning = lesson.get("warning")

        tag = f"#{lesson_id} conf={confidence}"
        if warning:
            tag += f" \u26a0{warning}"

        lines.append(f"  {i}. [{tag}] {text}")

    return "\n".join(lines)
