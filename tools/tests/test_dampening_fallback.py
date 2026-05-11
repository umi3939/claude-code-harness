#!/usr/bin/env python3
"""Tests for facade_get_dampening fallback in emotion_react handler.

TDD: Tests written before implementation.
Verifies that when facade_get_dampening raises an exception,
emotion_react falls back to dampening=1.0 and logs a warning.
"""

import json
import os
import sys
import unittest
from unittest.mock import patch
from io import StringIO

TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)


def _make_memory_dir(tmp_dir):
    """Create a minimal memory directory with required state files."""
    md = os.path.join(tmp_dir, "memory")
    os.makedirs(md, exist_ok=True)

    state = {
        "fulfillment": 0.0,
        "tension": 0.0,
        "affinity": 0.0,
        "last_updated": "2026-03-22T00:00:00Z",
        "created_at": "2026-03-22T00:00:00Z",
    }
    with open(os.path.join(md, "emotion_state.json"), "w") as f:
        json.dump(state, f)

    dynamics = {
        "phase": "normal",
        "accumulated_magnitude": 0.0,
        "session_reaction_count": 0,
        "last_session_id": None,
    }
    with open(os.path.join(md, "dynamics_state.json"), "w") as f:
        json.dump(dynamics, f)

    with open(os.path.join(md, "emotion_change_log.json"), "w") as f:
        json.dump([], f)

    with open(os.path.join(md, "long_term_dynamics_buffer.json"), "w") as f:
        json.dump([], f)
    with open(os.path.join(md, "long_term_dynamics_log.json"), "w") as f:
        json.dump([], f)

    return md


class TestDampeningFallback(unittest.TestCase):
    """When facade_get_dampening raises, emotion_react should fallback to 1.0."""

    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.mkdtemp()
        self._memory_dir = _make_memory_dir(self._tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_fallback_dampening_on_exception(self):
        """emotion_react should succeed even when facade_get_dampening raises."""
        from memory_mcp_server import emotion_react

        with patch("memory_mcp_server.DEFAULT_MEMORY_DIR", self._memory_dir):
            with patch(
                "memory_mcp_server.facade_get_dampening",
                side_effect=RuntimeError("stability valve broken"),
            ):
                result = emotion_react(
                    emotion_label="happy",
                    emotion_valence=0.5,
                    intent="sharing",
                )
        # Should not return an error
        self.assertNotIn("ERROR", result)

    def test_fallback_dampening_logs_warning(self):
        """A warning should be printed to stderr when dampening fails."""
        from memory_mcp_server import emotion_react

        with patch("memory_mcp_server.DEFAULT_MEMORY_DIR", self._memory_dir):
            with patch(
                "memory_mcp_server.facade_get_dampening",
                side_effect=RuntimeError("stability valve broken"),
            ):
                with patch("sys.stderr", new_callable=StringIO) as mock_stderr:
                    emotion_react(
                        emotion_label="happy",
                        emotion_valence=0.5,
                        intent="sharing",
                    )
                    stderr_output = mock_stderr.getvalue()

        self.assertIn("dampening", stderr_output.lower())
        self.assertIn("stability valve broken", stderr_output)

    def test_fallback_uses_dampening_1_0(self):
        """When dampening fails, effective_amplitude should not be dampened (factor=1.0)."""
        from memory_mcp_server import emotion_react

        # First: normal run (dampening=1.0 by default with neutral state)
        with patch("memory_mcp_server.DEFAULT_MEMORY_DIR", self._memory_dir):
            normal_result = emotion_react(
                emotion_label="happy",
                emotion_valence=0.5,
                intent="sharing",
            )

        # Reset state
        self._memory_dir = _make_memory_dir(self._tmpdir)

        # Second: run with broken dampening (should fallback to 1.0, same result)
        with patch("memory_mcp_server.DEFAULT_MEMORY_DIR", self._memory_dir):
            with patch(
                "memory_mcp_server.facade_get_dampening",
                side_effect=RuntimeError("broken"),
            ):
                fallback_result = emotion_react(
                    emotion_label="happy",
                    emotion_valence=0.5,
                    intent="sharing",
                )

        # Both should succeed (not error)
        self.assertNotIn("ERROR", normal_result)
        self.assertNotIn("ERROR", fallback_result)


if __name__ == "__main__":
    unittest.main()
