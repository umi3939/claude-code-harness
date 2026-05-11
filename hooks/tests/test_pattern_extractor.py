#!/usr/bin/env python3
"""Tests for pattern_extractor.py — review_pass + trajectory subprocess calls."""

import json
import os
import subprocess as sp
import sys
from unittest.mock import MagicMock, patch

import pytest

# Add hooks dir to path
HOOKS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)

import pattern_extractor

# ── Fixtures ──


@pytest.fixture
def agent_stdin_no_issues():
    """PostToolUse stdin JSON with Agent result containing no HIGH/MED/CRITICAL."""
    return json.dumps({
        "tool_name": "Agent",
        "tool_input": {"subagent_type": "reviewer"},
        "tool_result": (
            "## Review Complete\n\nAll checks passed. No issues found.\n\n"
            "### LOW#1: Minor style suggestion\nConsider using f-strings."
        ),
    })


@pytest.fixture
def agent_stdin_with_issues():
    """PostToolUse stdin JSON with Agent result containing HIGH issues."""
    return json.dumps({
        "tool_name": "Agent",
        "tool_input": {"subagent_type": "reviewer"},
        "tool_result": (
            "## Review\n\n### HIGH#1: Missing error handling\n"
            "No try-except around file I/O.\n\n### MED#1: Unused import\nRemove os import."
        ),
    })


@pytest.fixture
def non_agent_stdin():
    """PostToolUse stdin JSON for a non-Agent tool."""
    return json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "tool_result": "file1.py\nfile2.py",
    })


# ── Tests for growth recording integration ──


class TestReviewPassSubprocess:
    """Tests for _call_growth_recorder on review pass."""

    @patch("pattern_extractor._call_growth_recorder")
    def test_review_pass_called_when_no_patterns(self, mock_call, agent_stdin_no_issues):
        """When no HIGH/MED/CRITICAL patterns, calls review_pass + trajectory."""
        with patch("sys.stdin") as mock_stdin, \
             patch("pattern_extractor._get_session_id", return_value="test-session"), \
             patch("pattern_extractor._get_data_dir", return_value="C:/Users/test/data"):
            mock_stdin.read.return_value = agent_stdin_no_issues
            with pytest.raises(SystemExit) as exc_info:
                pattern_extractor.main()
            assert exc_info.value.code == 0

        assert mock_call.call_count == 2
        # First call: review_pass
        first_args = mock_call.call_args_list[0]
        assert first_args[0][0] == "review_pass"
        # Second call: trajectory
        second_args = mock_call.call_args_list[1]
        assert second_args[0][0] == "trajectory"

    @patch("pattern_extractor._call_growth_recorder")
    def test_no_growth_call_when_patterns_found(self, mock_call, agent_stdin_with_issues):
        """When HIGH/MED/CRITICAL patterns found, no growth recording."""
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = agent_stdin_with_issues
            with pytest.raises(SystemExit) as exc_info:
                pattern_extractor.main()
            assert exc_info.value.code == 0

        # review_pass should NOT be called
        for c in mock_call.call_args_list:
            assert c[0][0] not in ("review_pass", "trajectory")

    @patch("pattern_extractor._call_growth_recorder")
    def test_non_agent_tool_no_growth_call(self, mock_call, non_agent_stdin):
        """Non-Agent tools should not trigger growth recording."""
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = non_agent_stdin
            with pytest.raises(SystemExit) as exc_info:
                pattern_extractor.main()
            assert exc_info.value.code == 0

        mock_call.assert_not_called()

    @patch("pattern_extractor._call_growth_recorder", side_effect=Exception("boom"))
    def test_growth_failure_does_not_crash(self, mock_call, agent_stdin_no_issues):
        """Growth recording failure should not crash the hook (fail-open)."""
        with patch("sys.stdin") as mock_stdin, \
             patch("pattern_extractor._get_session_id", return_value="test-session"), \
             patch("pattern_extractor._get_data_dir", return_value="C:/Users/test/data"):
            mock_stdin.read.return_value = agent_stdin_no_issues
            with pytest.raises(SystemExit) as exc_info:
                pattern_extractor.main()
            assert exc_info.value.code == 0

    def test_call_growth_recorder_subprocess_timeout(self):
        """_call_growth_recorder handles subprocess timeout gracefully."""
        with patch("subprocess.run", side_effect=sp.TimeoutExpired(cmd="python", timeout=3)):
            # Should not raise
            pattern_extractor._call_growth_recorder("review_pass", "{}")

    def test_call_growth_recorder_uses_timeout(self):
        """_call_growth_recorder passes timeout=3 to subprocess.run."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            pattern_extractor._call_growth_recorder("review_pass", "{}")
            mock_run.assert_called_once()
            assert mock_run.call_args[1]["timeout"] == 3
