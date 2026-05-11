#!/usr/bin/env python3
"""Auto memory consolidation — replaces manual-only consolidation check.

Called from stop-consolidation-check.js when new lessons exceed consolidated
count. Instead of just printing a suggestion, automatically runs
memory_consolidate(mode='check') and logs the result.

Fail-open: never blocks, never raises.
"""

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


def auto_consolidate_if_needed() -> str:
    """Run memory_consolidate(mode='check') and return the result.

    This is the auto-trigger replacement for the manual
    'Consider running memory_consolidate' suggestion.

    Returns:
        Result string from memory_consolidate, or empty string on failure.
    """
    try:
        from memory_mcp_server import memory_consolidate
        result = memory_consolidate(mode="check")
        if result:
            return str(result)[:500]
        return ""
    except Exception as e:
        logger.warning("auto_consolidate_if_needed failed: %s", e)
        return ""


def main():
    """Entry point when called from stop-consolidation-check.js."""
    logging.basicConfig(
        level=logging.INFO,
        format="[consolidation_auto] %(message)s",
        stream=sys.stderr,
    )
    result = auto_consolidate_if_needed()
    if result:
        print(f"[Consolidation] {result[:200]}", file=sys.stderr)


if __name__ == "__main__":
    main()
