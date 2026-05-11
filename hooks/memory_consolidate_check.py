#!/usr/bin/env python3
"""G47: Auto-trigger memory_consolidate check at session end.

Checks if new lessons exist since last consolidation.
If so, runs memory_consolidate in 'check' mode and logs the result.
Fail-open: errors never block session end.
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


def main() -> int:
    """Check if consolidation is needed and log result."""
    try:
        from memory_mcp_server import memory_consolidate
    except ImportError as e:
        logger.warning("memory_consolidate import failed: %s", e)
        return 0  # fail-open

    try:
        result = memory_consolidate(mode="check")
        if "No new lessons" in result or "STATUS: No new lessons" in result:
            print("[Consolidate] No new lessons since last consolidation", file=sys.stderr)
        else:
            print("[Consolidate] New lessons detected. Consolidation recommended.", file=sys.stderr)
    except Exception as e:
        logger.warning("memory_consolidate check failed: %s", e)

    return 0  # always fail-open


if __name__ == "__main__":
    sys.exit(main())
