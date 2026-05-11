#!/usr/bin/env python3
"""Stop hook orchestrator — consolidates 5 sub-hooks into one process.

Design: docs/design_hook_consolidation.md
Sub-processes:
  1. stop-consolidation-check (node, 10s) — lesson registry change detection
  2. lesson-after-feedback (node, 10s) — feedback/error without lesson detection
  3. hontou-ni-check (node, 10s) — completion signal self-questioning (blocking)
  4. stop-session-end (node, 10s) — session end auto-execution (DEPRECATED)
  5. stop-session-aar (node, 10s) — after-action review auto-execution (DEPRECATED)
"""

import os
import sys

from hook_orchestrator import (
    SubprocessDefinition,
    aggregate_and_exit,
    run_orchestrator,
)

# ── Sub-process script base directory ──

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Static sub-process definitions ──

DEFINITIONS = [
    SubprocessDefinition(
        name="stop-consolidation-check",
        command=["node", os.path.join(HOOKS_DIR, "stop-consolidation-check.js")],
        timeout=10,
    ),
    SubprocessDefinition(
        name="lesson-after-feedback",
        command=["node", os.path.join(HOOKS_DIR, "lesson-after-feedback.js")],
        timeout=10,
    ),
    SubprocessDefinition(
        name="hontou-ni-check",
        command=["node", os.path.join(HOOKS_DIR, "hontou-ni-check.js")],
        timeout=10,
    ),
    # G68 Phase 4-1: leader output quality guard (hedging + explanation-required).
    # Placed after hontou-ni-check so output review runs after completion-signal
    # detection. Existing sub-processes are unchanged.
    SubprocessDefinition(
        name="stop-output-quality",
        command=["node", os.path.join(HOOKS_DIR, "stop-output-quality.js")],
        timeout=5,
    ),
    SubprocessDefinition(
        name="stop-session-end",
        command=["node", os.path.join(HOOKS_DIR, "stop-session-end.js")],
        timeout=10,
    ),
    SubprocessDefinition(
        name="stop-session-aar",
        command=["node", os.path.join(HOOKS_DIR, "stop-session-aar.js")],
        timeout=10,
    ),
]


def main() -> None:
    """Read stdin once and run all sub-processes."""
    stdin_data = sys.stdin.buffer.read()
    results = run_orchestrator(DEFINITIONS, stdin_data=stdin_data)
    aggregate_and_exit(results)


if __name__ == "__main__":
    main()
