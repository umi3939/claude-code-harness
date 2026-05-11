#!/usr/bin/env python3
"""Pattern extractor — instinct-like automatic pattern detection from reviews.

PostToolUse hook that monitors reviewer Agent output and extracts
HIGH/MED/CRITICAL patterns, accumulating them in a JSONL file.
When the same pattern appears 3+ times, writes a lesson candidate to STM.

Usage (as PostToolUse hook for Agent tool):
    Reads tool result from stdin JSON, extracts patterns from Agent output.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HOOKS_DIR, "..", "data")
DEFAULT_PATTERNS_FILE = os.path.join(DATA_DIR, "review_patterns.jsonl")
GROWTH_RECORDER = os.path.join(HOOKS_DIR, "growth_recorder.py")

# Pattern for extracting review findings
# Matches: ### HIGH#1: description, ### MED#2: description, ### CRITICAL#1: description
_FINDING_PATTERN = re.compile(
    r"###\s+(HIGH|MED|MEDIUM|CRITICAL|LOW)\s*#?\d*\s*:\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)

# Threshold for promoting to lesson candidate
PROMOTION_THRESHOLD = 3


def extract_patterns(text: str) -> list[dict]:
    """Extract review finding patterns from text.

    Returns list of dicts with keys: severity, description, category.
    """
    patterns = []
    for m in _FINDING_PATTERN.finditer(text):
        severity = m.group(1).upper()
        if severity == "MEDIUM":
            severity = "MED"
        description = m.group(2).strip()

        # Simple category inference from description
        category = _infer_category(description)

        patterns.append({
            "severity": severity,
            "description": description,
            "category": category,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    return patterns


def _infer_category(description: str) -> str:
    """Infer a category from the finding description."""
    desc_lower = description.lower()
    if any(w in desc_lower for w in ("error handling", "exception", "try", "catch")):
        return "error_handling"
    if any(w in desc_lower for w in ("injection", "xss", "sql", "security", "sanitiz")):
        return "security"
    if any(w in desc_lower for w in ("unused", "dead code", "import")):
        return "dead_code"
    if any(w in desc_lower for w in ("timeout", "performance", "cache", "slow")):
        return "performance"
    if any(w in desc_lower for w in ("type", "validation", "check", "null", "none")):
        return "validation"
    if any(w in desc_lower for w in ("test", "coverage", "assert")):
        return "testing"
    return "other"


def accumulate_patterns(
    patterns: list[dict],
    jsonl_path: str = DEFAULT_PATTERNS_FILE,
) -> list[dict]:
    """Append patterns to JSONL file and return any that crossed the promotion threshold.

    Returns list of patterns that have appeared >= PROMOTION_THRESHOLD times.
    """
    os.makedirs(os.path.dirname(jsonl_path), exist_ok=True)

    # Write new patterns
    with open(jsonl_path, "a", encoding="utf-8") as f:
        for p in patterns:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    # Count category occurrences
    category_counts: dict[str, int] = {}
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    cat = entry.get("category", "other")
                    category_counts[cat] = category_counts.get(cat, 0) + 1
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass

    # Find patterns that crossed the threshold
    promoted = []
    for p in patterns:
        cat = p.get("category", "other")
        if category_counts.get(cat, 0) >= PROMOTION_THRESHOLD:
            promoted.append(p)

    return promoted


def _get_session_id() -> str:
    """Read session ID from .session-start-time file.

    Returns normalized format matching observation-logger.js:
    's' + epoch, truncated to 12 chars.
    """
    try:
        fpath = os.path.join(HOOKS_DIR, ".session-start-time")
        with open(fpath, "r", encoding="utf-8") as f:
            epoch = f.read().strip()
        sid = "s" + epoch if not epoch.startswith("s") else epoch
        return sid[:12]
    except OSError:
        return ""


def _get_data_dir() -> str:
    """Return default data directory path."""
    return DATA_DIR


def _call_growth_recorder(event_type: str, stdin_data: str) -> None:
    """Call growth_recorder.py as subprocess. Fail-open."""

    try:
        subprocess.run(
            [sys.executable, GROWTH_RECORDER, event_type],
            input=stdin_data,
            capture_output=True,
            timeout=3,
            text=True,
        )
    except Exception:
        pass  # Fail-open: growth recording failure never blocks hook


def main():
    """Entry point for PostToolUse hook."""
    try:
        raw = sys.stdin.read()
        if not raw:
            sys.exit(0)

        data = json.loads(raw)
        tool_name = data.get("tool_name", "")
        tool_result = data.get("tool_result", "")

        # Only process Agent tool results (reviewer output)
        if tool_name != "Agent":
            sys.exit(0)

        # Extract patterns from the result
        if not isinstance(tool_result, str):
            tool_result = str(tool_result)

        patterns = extract_patterns(tool_result)

        # Only HIGH/MED/CRITICAL count — LOW is excluded
        significant = [p for p in patterns if p["severity"] in ("HIGH", "MED", "CRITICAL")]

        if significant:
            # Accumulate and check for promotions
            promoted = accumulate_patterns(significant)

            # If any patterns promoted, write to STM via stdout (context injection)
            if promoted:
                categories = set(p["category"] for p in promoted)
                print(
                    f"[PatternExtractor] Recurring review pattern detected: "
                    f"{', '.join(categories)} ({len(promoted)} instances). "
                    f"Consider creating a lesson."
                )
        else:
            # Review passed (no HIGH/MED/CRITICAL) — record growth
            session_id = _get_session_id()
            data_dir = _get_data_dir()
            review_summary = tool_result[:500]

            growth_data = json.dumps({
                "review_summary": review_summary,
                "session_id": session_id,
                "data_dir": data_dir,
            })

            _call_growth_recorder("review_pass", growth_data)
            _call_growth_recorder("trajectory", growth_data)

    except Exception:
        pass  # Hook must not crash

    sys.exit(0)


if __name__ == "__main__":
    main()
