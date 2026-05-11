#!/usr/bin/env python3
"""Lesson Conflict Resolution (C22-E).

Detects conflicting lessons within the same Rule category by comparing
Fix text similarity. Read-only: detection only, no automatic resolution.

Design:
- fail-open: errors return empty results, never raise
- Read-only: does not modify lessons_registry.md or metadata
- Priority rules: newer > older (default), confidence high > low
"""

import logging
import os

logger = logging.getLogger(__name__)

# Overlap threshold: below this, two Fix texts are considered divergent
OVERLAP_THRESHOLD = 0.4

LESSONS_FILENAME = "lessons_registry.md"
METADATA_FILENAME = "lesson_metadata.json"

# Confidence gap required to override date-based priority
CONFIDENCE_GAP_THRESHOLD = 0.3


def _tokenize(text: str) -> set[str]:
    """Split text into lowercase word set.

    Args:
        text: Input text string.

    Returns:
        Set of lowercase words.
    """
    if not text or not text.strip():
        return set()
    return set(text.lower().split())


def _word_overlap(text_a: str, text_b: str) -> float:
    """Compute Jaccard similarity (intersection/union) of word sets.

    Args:
        text_a: First text.
        text_b: Second text.

    Returns:
        Float in [0.0, 1.0]. Returns 0.0 if both empty.
    """
    tokens_a = _tokenize(text_a)
    tokens_b = _tokenize(text_b)
    if not tokens_a and not tokens_b:
        return 0.0
    union = tokens_a | tokens_b
    if not union:
        return 0.0
    intersection = tokens_a & tokens_b
    return len(intersection) / len(union)


def _parse_lessons(memory_dir: str) -> list[dict]:
    """Parse lessons_registry.md into structured lesson objects.

    Reuses the same parsing logic as memory_mcp_server._parse_lessons.

    Args:
        memory_dir: Path to the memory directory.

    Returns:
        List of lesson dicts with keys: date, action, why, fix, lesson, rule.
    """
    lessons_path = os.path.join(memory_dir, LESSONS_FILENAME)
    if not os.path.exists(lessons_path):
        return []
    try:
        with open(lessons_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        logger.warning("Failed to read lessons file: %s", e)
        return []

    lessons = []
    current: dict = {}
    current_field: str | None = None
    for line in content.split("\n"):
        line_stripped = line.strip()
        if line_stripped.startswith("## Lesson:"):
            if current.get("lesson") or current.get("fix"):
                lessons.append(current)
            current = {"date": line_stripped.replace("## Lesson:", "").strip()}
            current_field = None
        elif line_stripped.startswith("### Action"):
            current_field = "action"
        elif line_stripped.startswith("### Why"):
            current_field = "why"
        elif line_stripped.startswith("### Fix"):
            current_field = "fix"
        elif line_stripped.startswith("### Lesson"):
            current_field = "lesson"
        elif line_stripped.startswith("### Related Rule"):
            current_field = "rule"
        elif line_stripped == "---":
            current_field = None
        elif current_field and line_stripped:
            current[current_field] = current.get(current_field, "") + line_stripped + " "

    if current.get("lesson") or current.get("fix"):
        lessons.append(current)

    # Clean whitespace
    for les in lessons:
        for k in les:
            if isinstance(les[k], str):
                les[k] = les[k].strip()
    return lessons


def _load_metadata(memory_dir: str) -> dict:
    """Load lesson_metadata.json (fail-open).

    Args:
        memory_dir: Path to the memory directory.

    Returns:
        Dict mapping lesson_id to metadata dict. Empty dict on error.
    """
    import json
    path = os.path.join(memory_dir, METADATA_FILENAME)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return {}
    except (ValueError, OSError) as e:
        logger.warning("Failed to load lesson metadata: %s", e)
        return {}


def detect_conflicts(memory_dir: str) -> list[dict]:
    """Detect conflicting lessons within the same Rule category.

    Groups lessons by their Rule field, then compares Fix text similarity
    within each group. Pairs with low word overlap are flagged as conflicts.

    Args:
        memory_dir: Path to the memory directory.

    Returns:
        List of conflict dicts, each with:
        - lesson_a_id: 1-based index of first lesson
        - lesson_b_id: 1-based index of second lesson
        - rule: The shared Rule category
        - overlap: Word overlap ratio (0.0-1.0)
        - reason: Human-readable conflict description
        - recommended_priority: Result of resolve_priority
    """
    try:
        lessons = _parse_lessons(memory_dir)
    except Exception as e:
        logger.warning("Failed to parse lessons for conflict detection: %s", e)
        return []

    if not lessons:
        return []

    # Group by rule (skip lessons without a rule)
    rule_groups: dict[str, list[tuple[int, dict]]] = {}
    for idx, lesson in enumerate(lessons):
        rule = lesson.get("rule", "").strip()
        if not rule:
            continue
        rule_lower = rule.lower()
        if rule_lower not in rule_groups:
            rule_groups[rule_lower] = []
        rule_groups[rule_lower].append((idx + 1, lesson))  # 1-based ID

    # Load metadata for priority resolution
    metadata = _load_metadata(memory_dir)

    conflicts = []
    for rule, group in rule_groups.items():
        if len(group) < 2:
            continue
        # Compare all pairs
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                id_a, lesson_a = group[i]
                id_b, lesson_b = group[j]
                fix_a = lesson_a.get("fix", "")
                fix_b = lesson_b.get("fix", "")

                if not fix_a or not fix_b:
                    continue

                overlap = _word_overlap(fix_a, fix_b)
                if overlap < OVERLAP_THRESHOLD:
                    priority = resolve_priority(
                        lesson_a, lesson_b, id_a, id_b, metadata
                    )
                    conflicts.append({
                        "lesson_a_id": id_a,
                        "lesson_b_id": id_b,
                        "rule": lesson_a.get("rule", rule),
                        "overlap": round(overlap, 3),
                        "reason": (
                            f"Fix texts diverge (overlap={overlap:.1%}): "
                            f"#{id_a} \"{fix_a[:50]}...\" vs "
                            f"#{id_b} \"{fix_b[:50]}...\""
                        ),
                        "recommended_priority": priority,
                    })

    return conflicts


def resolve_priority(
    lesson_a: dict,
    lesson_b: dict,
    id_a: int,
    id_b: int,
    metadata: dict,
) -> dict:
    """Determine which lesson should take precedence.

    Priority rules:
    1. If confidence gap >= CONFIDENCE_GAP_THRESHOLD: higher confidence wins
    2. Otherwise: newer date wins (default)
    3. Tie: higher ID wins (assumed to be newer entry)

    Args:
        lesson_a: First lesson dict.
        lesson_b: Second lesson dict.
        id_a: 1-based ID of first lesson.
        id_b: 1-based ID of second lesson.
        metadata: Loaded lesson_metadata.json dict.

    Returns:
        Dict with winner_id, loser_id, reason.
    """
    # Get confidence from metadata
    conf_a = metadata.get(str(id_a), {}).get("confidence", 0.5)
    conf_b = metadata.get(str(id_b), {}).get("confidence", 0.5)

    # Rule 1: Confidence gap
    conf_gap = abs(conf_a - conf_b)
    if conf_gap >= CONFIDENCE_GAP_THRESHOLD:
        if conf_a > conf_b:
            return {
                "winner_id": id_a,
                "loser_id": id_b,
                "reason": f"Higher confidence ({conf_a:.2f} vs {conf_b:.2f})",
            }
        else:
            return {
                "winner_id": id_b,
                "loser_id": id_a,
                "reason": f"Higher confidence ({conf_b:.2f} vs {conf_a:.2f})",
            }

    # Rule 2: Date comparison
    date_a = lesson_a.get("date", "")
    date_b = lesson_b.get("date", "")

    if date_a and date_b and date_a != date_b:
        if date_b > date_a:
            return {
                "winner_id": id_b,
                "loser_id": id_a,
                "reason": f"Newer date ({date_b} vs {date_a})",
            }
        else:
            return {
                "winner_id": id_a,
                "loser_id": id_b,
                "reason": f"Newer date ({date_a} vs {date_b})",
            }

    # Rule 3: Tiebreaker -- higher ID
    return {
        "winner_id": id_b,
        "loser_id": id_a,
        "reason": "Tiebreaker: higher lesson ID (assumed newer entry)",
    }


def get_conflict_report(memory_dir: str) -> str:
    """Generate a formatted conflict report.

    Args:
        memory_dir: Path to the memory directory.

    Returns:
        Human-readable conflict report string.
    """
    try:
        conflicts = detect_conflicts(memory_dir)
    except Exception as e:
        logger.warning("Failed to generate conflict report: %s", e)
        return "ERROR: Failed to generate conflict report."

    if not conflicts:
        return "Lesson Conflict Report: 0 conflicts detected. All lessons are consistent."

    lines = [
        f"Lesson Conflict Report: {len(conflicts)} conflict(s) detected.",
        "",
    ]

    for i, conflict in enumerate(conflicts, 1):
        priority = conflict.get("recommended_priority", {})
        lines.append(f"--- Conflict {i} ---")
        lines.append(f"  Rule: {conflict['rule']}")
        lines.append(f"  Lessons: #{conflict['lesson_a_id']} vs #{conflict['lesson_b_id']}")
        lines.append(f"  Overlap: {conflict['overlap']:.1%}")
        lines.append(f"  {conflict['reason']}")
        if priority:
            lines.append(
                f"  Recommended: Keep #{priority['winner_id']} "
                f"(reason: {priority['reason']})"
            )
        lines.append("")

    lines.append(
        "NOTE: This is a read-only report. "
        "No lessons were modified. Review and resolve manually."
    )
    return "\n".join(lines)
