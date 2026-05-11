#!/usr/bin/env python3
"""Growth Metrics Dashboard — unified view of lesson/success/mastery growth (C22-F/L).

Reads lesson_metadata, success_registry, mastery_profile, and lessons_registry
in read-only mode to compute growth indicators and balance ratio.

Key design decisions:
- read-only: never writes to any data file
- fail-open: data errors return safe defaults, never raise
- stateless: no caching, always reads fresh data
"""

import logging
import os
import sys

logger = logging.getLogger(__name__)

# Add tools directory to path for sibling imports
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import lesson_metadata
import mastery_profile
import success_registry
from lessons_registry import _markdown_to_entries, get_lessons_path

# --- Constants ---

BALANCE_LOW_THRESHOLD = 0.3
BALANCE_HIGH_THRESHOLD = 0.7
DEFAULT_CONFIDENCE = 0.5


def _load_lessons(memory_dir: str) -> list:
    """Load lesson entries from lessons_registry.md (read-only).

    Returns:
        List of lesson entry dicts. Empty list on error.
    """
    try:
        path = get_lessons_path(memory_dir)
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8")
        return _markdown_to_entries(text)
    except Exception as e:
        logger.warning("Failed to load lessons: %s", e)
        return []


def _compute_balance(success_count: int, lesson_count: int) -> dict:
    """Compute balance ratio and status.

    balance_ratio = success_count / (success_count + lesson_count)

    Returns:
        Dict with 'ratio' (float or None) and 'status' (str).
    """
    total = success_count + lesson_count
    if total == 0:
        return {"ratio": None, "status": "no_data"}

    ratio = round(success_count / total, 4)

    if ratio < BALANCE_LOW_THRESHOLD:
        status = "failure_heavy"
    elif ratio > BALANCE_HIGH_THRESHOLD:
        status = "success_biased"
    else:
        status = "balanced"

    return {"ratio": ratio, "status": status}


def collect_metrics(memory_dir: str) -> dict:
    """Collect all growth metrics from lesson/success/mastery data.

    Args:
        memory_dir: Path to the memory directory.

    Returns:
        Dict with keys: lessons, successes, mastery, balance.
    """
    # --- Lessons ---
    lessons = _load_lessons(memory_dir)
    lesson_count = len(lessons)

    metadata = lesson_metadata.load_metadata(memory_dir)
    confidences = []
    validated_count = 0
    for _lid, entry in metadata.items():
        conf = entry.get("confidence", DEFAULT_CONFIDENCE)
        confidences.append(conf)
        if conf != DEFAULT_CONFIDENCE:
            validated_count += 1

    # For lessons without metadata, assume default confidence
    for _i in range(lesson_count - len(confidences)):
        confidences.append(DEFAULT_CONFIDENCE)

    avg_confidence = (
        round(sum(confidences) / len(confidences), 4)
        if confidences
        else DEFAULT_CONFIDENCE
    )

    lessons_metrics = {
        "total": lesson_count,
        "validated_count": validated_count,
        "avg_confidence": avg_confidence,
    }

    # --- Successes ---
    success_stats = success_registry.get_stats(memory_dir)
    successes_metrics = {
        "total": success_stats.get("total", 0),
        "review_zero": success_stats.get("review_zero", 0),
        "test_pass": success_stats.get("test_pass", 0),
        "user_positive": success_stats.get("user_positive", 0),
    }

    # --- Mastery ---
    profile = mastery_profile.load_profile(memory_dir)
    strengths = mastery_profile.get_strengths(memory_dir, n=3)
    growth_areas = mastery_profile.get_growth_areas(memory_dir, n=3)

    mastery_metrics = {
        "total_domains": len(profile),
        "strengths": strengths,
        "growth_areas": growth_areas,
    }

    # --- Balance ---
    balance = _compute_balance(successes_metrics["total"], lesson_count)

    return {
        "lessons": lessons_metrics,
        "successes": successes_metrics,
        "mastery": mastery_metrics,
        "balance": balance,
    }


def generate_dashboard(memory_dir: str) -> str:
    """Generate a formatted text dashboard with 4 sections.

    Args:
        memory_dir: Path to the memory directory.

    Returns:
        Formatted multi-line text dashboard.
    """
    try:
        metrics = collect_metrics(memory_dir)
    except Exception as e:
        logger.warning("Failed to collect metrics for dashboard: %s", e)
        return "=== Growth Metrics Dashboard ===\n\n(failed to collect metrics)"

    lines = ["=== Growth Metrics Dashboard ===", ""]

    # Section 1: Lessons
    les = metrics["lessons"]
    lines.append("--- Lessons ---")
    lines.append(f"  Total: {les['total']}")
    lines.append(f"  Validated: {les['validated_count']}")
    lines.append(f"  Avg confidence: {les['avg_confidence']:.2f}")
    lines.append("")

    # Section 2: Successes
    suc = metrics["successes"]
    lines.append("--- Success Patterns ---")
    lines.append(f"  Total: {suc['total']}")
    if suc["total"] > 0:
        lines.append(f"  review_zero: {suc['review_zero']}")
        lines.append(f"  test_pass: {suc['test_pass']}")
        lines.append(f"  user_positive: {suc['user_positive']}")
    lines.append("")

    # Section 3: Mastery
    mas = metrics["mastery"]
    lines.append("--- Mastery ---")
    lines.append(f"  Domains tracked: {mas['total_domains']}")
    if mas["strengths"]:
        lines.append("  Strengths:")
        for s in mas["strengths"]:
            score = s.get("mastery_score", 0)
            trend = s.get("trend", "stable")
            lines.append(f"    {s['domain']}: {score:.1%} ({trend})")
    if mas["growth_areas"]:
        lines.append("  Growth areas:")
        for g in mas["growth_areas"]:
            score = g.get("mastery_score", 0)
            trend = g.get("trend", "stable")
            lines.append(f"    {g['domain']}: {score:.1%} ({trend})")
    lines.append("")

    # Section 4: Balance
    bal = metrics["balance"]
    lines.append("--- Balance ---")
    if bal["ratio"] is not None:
        lines.append(f"  Ratio: {bal['ratio']:.2f} (success / total)")
        status_text = {
            "balanced": "Balanced",
            "failure_heavy": "Warning: failure-heavy (< 0.3). Capture more success patterns.",
            "success_biased": "Warning: success-biased (> 0.7). Deepen failure analysis.",
            "no_data": "No data yet",
        }
        lines.append(f"  Status: {status_text.get(bal['status'], bal['status'])}")
    else:
        lines.append("  No data yet")

    return "\n".join(lines)


def get_health_summary(memory_dir: str) -> str:
    """Generate a single-line health summary.

    Args:
        memory_dir: Path to the memory directory.

    Returns:
        Single-line string summarizing growth health.
    """
    try:
        metrics = collect_metrics(memory_dir)
    except Exception as e:
        logger.warning("Failed to collect metrics for health summary: %s", e)
        return "Growth: no data"

    les = metrics["lessons"]
    suc = metrics["successes"]
    mas = metrics["mastery"]
    bal = metrics["balance"]

    parts = []
    parts.append(f"L:{les['total']}")
    parts.append(f"S:{suc['total']}")
    parts.append(f"M:{mas['total_domains']}dom")

    if bal["ratio"] is not None:
        parts.append(f"bal:{bal['ratio']:.2f}")
        if bal["status"] != "balanced":
            parts.append(f"[{bal['status']}]")
    else:
        parts.append("bal:n/a")

    return "Growth: " + " | ".join(parts)
