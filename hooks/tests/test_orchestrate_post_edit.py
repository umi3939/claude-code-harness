#!/usr/bin/env python3
"""Tests for orchestrate_post_edit.py — PostToolUse Edit|Write orchestrator.

Phase 2 test suite: validates definitions, interpreters, timeouts, self-edit, and main().
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

HOOKS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)


@pytest.fixture(autouse=True)
def _reset_self_files_between_tests():
    """Reset _SELF_FILES before and after each test to avoid cross-test contamination."""
    from hook_orchestrator import reset_self_files
    reset_self_files()
    # Re-import to re-register orchestrate_post_edit's self file
    import importlib

    import orchestrate_post_edit
    importlib.reload(orchestrate_post_edit)
    yield
    reset_self_files()


# ── Test 1: Definitions are correct ──

def test_definitions_count():
    """orchestrate_post_edit defines exactly 4 sub-processes."""
    import orchestrate_post_edit
    assert len(orchestrate_post_edit.DEFINITIONS) == 4


def test_definitions_names():
    """Sub-process names match expected hooks."""
    import orchestrate_post_edit
    names = [d.name for d in orchestrate_post_edit.DEFINITIONS]
    assert names == [
        "auto-stats-update",
        "auto-test-runner",
        "auto-consolidation-check",
        "ruff-quality-gate",
    ]


# ── Test 2: Correct interpreters ──

def test_interpreters():
    """First 3 use node, last uses python."""
    import orchestrate_post_edit
    for d in orchestrate_post_edit.DEFINITIONS[:3]:
        assert d.command[0] == "node", f"{d.name} should use node"
    assert orchestrate_post_edit.DEFINITIONS[3].command[0] == sys.executable


# ── Test 3: Timeouts match design ──

def test_timeouts():
    """Individual timeouts match the design specification."""
    import orchestrate_post_edit
    expected = {"auto-stats-update": 15, "auto-test-runner": 35,
                "auto-consolidation-check": 5, "ruff-quality-gate": 15}
    for d in orchestrate_post_edit.DEFINITIONS:
        assert d.timeout == expected[d.name], f"{d.name} timeout mismatch"


# ── Test 4: Self-edit registration ──

def test_self_file_registered():
    """orchestrate_post_edit.py registers itself for self-edit detection."""
    from hook_orchestrator import _SELF_FILES
    own_path = os.path.normpath(os.path.abspath(
        os.path.join(HOOKS_DIR, "orchestrate_post_edit.py")
    )).casefold()
    assert own_path in _SELF_FILES


# ── Test 5: Scripts exist on disk ──

def test_scripts_exist():
    """Each command references a script that exists."""
    import orchestrate_post_edit
    for d in orchestrate_post_edit.DEFINITIONS:
        script_path = d.command[-1]
        assert os.path.isfile(script_path), f"Script not found: {script_path} (sub: {d.name})"


# ── Test 6: Main function calls run_orchestrator + aggregate_and_exit ──

def test_main_calls_orchestrator():
    """main() pipes stdin through run_orchestrator and aggregate_and_exit."""
    import orchestrate_post_edit
    mock_results = [MagicMock()]
    with patch("orchestrate_post_edit.run_orchestrator", return_value=mock_results) as m_run, \
         patch("orchestrate_post_edit.aggregate_and_exit") as m_agg, \
         patch("sys.stdin") as mock_stdin:
        mock_stdin.buffer.read.return_value = b'{"tool_input":{}}'
        orchestrate_post_edit.main()
    m_run.assert_called_once()
    m_agg.assert_called_once_with(mock_results)
