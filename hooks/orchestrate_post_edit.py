#!/usr/bin/env python3
"""PostToolUse Edit|Write orchestrator — consolidates 4 sub-hooks into one process.

Design: docs/design_hook_consolidation.md
Sub-processes:
  1. auto-stats-update (node, 15s) — doc stats recalculation on infra file changes
  2. auto-test-runner (node, 35s) — detect and run tests for edited file
  3. auto-consolidation-check (node, 5s) — lesson registry consolidation detection
  4. ruff-quality-gate (python, 15s) — format/lint/security check
"""

import os
import sys

from hook_orchestrator import (
    SubprocessDefinition,
    aggregate_and_exit,
    register_self_file,
    run_orchestrator,
)

# Register this entry-point for self-edit detection
register_self_file(__file__)

# ── Sub-process script base directory ──

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Static sub-process definitions ──

DEFINITIONS = [
    SubprocessDefinition(
        name="auto-stats-update",
        command=["node", os.path.join(HOOKS_DIR, "auto-stats-update.js")],
        timeout=15,
    ),
    SubprocessDefinition(
        name="auto-test-runner",
        command=["node", os.path.join(HOOKS_DIR, "auto-test-runner.js")],
        timeout=35,
    ),
    SubprocessDefinition(
        name="auto-consolidation-check",
        command=["node", os.path.join(HOOKS_DIR, "auto-consolidation-check.js")],
        timeout=5,
    ),
    SubprocessDefinition(
        name="ruff-quality-gate",
        command=[sys.executable, os.path.join(HOOKS_DIR, "ruff_quality_gate.py")],
        timeout=15,
    ),
]


def main() -> None:
    """Read stdin once and run all sub-processes."""
    stdin_data = sys.stdin.buffer.read()
    results = run_orchestrator(DEFINITIONS, stdin_data=stdin_data)
    aggregate_and_exit(results)


if __name__ == "__main__":
    main()
