#!/usr/bin/env python3
"""Lesson conflict checker — standalone script for lesson-after-feedback.js.

Called via execFileSync('python', [lesson_conflict_checker.py]) from
lesson-after-feedback.js Group 6. Replaces inline Python -c pattern.

Imports detect_lesson_conflicts from memory_mcp_server and outputs
the result as JSON to stdout.

Fail-open: never raises, never blocks. Returns {"success": true} even on failure.
"""

import json
import logging
import os
import sys

logger = logging.getLogger(__name__)

TOOLS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tools",
)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

MAX_REPORT_LEN = 500


def run_conflict_check() -> dict:
    """Run detect_lesson_conflicts and return structured result.

    Returns:
        Dict with keys: success (bool), report (str), error (str or None).
    """
    try:
        from memory_mcp_server import detect_lesson_conflicts
        report = detect_lesson_conflicts()
        return {
            "success": True,
            "report": str(report)[:MAX_REPORT_LEN] if report else "",
            "error": None,
        }
    except Exception as e:
        logger.warning("detect_lesson_conflicts failed: %s", e)
        return {
            "success": True,
            "report": "",
            "error": str(e)[:200],
        }


def main():
    """Entry point when called from lesson-after-feedback.js."""
    logging.basicConfig(
        level=logging.INFO,
        format="[lesson_conflict_checker] %(message)s",
        stream=sys.stderr,
    )
    result = run_conflict_check()
    # Output JSON to stdout for the calling JS hook
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
