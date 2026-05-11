#!/usr/bin/env python3
"""Tests for orchestrate_stop.py — Stop hook orchestrator."""

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
    yield
    reset_self_files()


# ── Test 1: Definitions count (5 base + 1 G68 Phase 4 prod = 6) ──

def test_definitions_count():
    """orchestrate_stop defines 5 base + 1 stop-output-quality = 6."""
    import orchestrate_stop
    assert len(orchestrate_stop.DEFINITIONS) == 6


# ── Test 2: Definition names match expected sub-processes ──

def test_definitions_names():
    """Sub-process names match the design doc + G68 Phase 4 prod.

    Order: stop-output-quality is placed AFTER hontou-ni-check (per plan §Phase 4-1)
    so output review runs after completion-signal detection. Phase 0 probe entry
    was removed in Phase 6-3.
    """
    import orchestrate_stop
    names = [d.name for d in orchestrate_stop.DEFINITIONS]
    assert names == [
        "stop-consolidation-check",
        "lesson-after-feedback",
        "hontou-ni-check",
        "stop-output-quality",
        "stop-session-end",
        "stop-session-aar",
    ]


# ── Test 3: All use node interpreter ──

def test_all_use_node():
    """All sub-processes use node."""
    import orchestrate_stop
    for d in orchestrate_stop.DEFINITIONS:
        assert d.command[0] == "node", f"{d.name} should use node"


# ── Test 4: Timeouts match design ──

def test_timeouts():
    """Base 5 sub-processes timeout=10s, stop-output-quality=5s."""
    import orchestrate_stop
    for d in orchestrate_stop.DEFINITIONS:
        if d.name == "stop-output-quality":
            assert d.timeout == 5, f"{d.name} timeout should be 5, got {d.timeout}"
        else:
            assert d.timeout == 10, f"{d.name} timeout should be 10, got {d.timeout}"


# ── Test 5: Scripts exist on disk ──

def test_scripts_exist():
    """Each command references a script that exists."""
    import orchestrate_stop
    for d in orchestrate_stop.DEFINITIONS:
        script_path = d.command[-1]
        assert os.path.isfile(script_path), f"Script not found: {script_path} (sub: {d.name})"


# ── Test 6: Main function calls run_orchestrator + aggregate_and_exit ──

def test_main_calls_orchestrator():
    """main() pipes stdin through run_orchestrator and aggregate_and_exit."""
    import orchestrate_stop
    mock_results = [MagicMock()]
    with patch("orchestrate_stop.run_orchestrator", return_value=mock_results) as m_run, \
         patch("orchestrate_stop.aggregate_and_exit") as m_agg, \
         patch("sys.stdin") as mock_stdin:
        mock_stdin.buffer.read.return_value = b'{"stop_response":"test"}'
        orchestrate_stop.main()
    m_run.assert_called_once()
    m_agg.assert_called_once_with(mock_results)
