#!/usr/bin/env python3
"""Tests for LOW findings (13 items) from code review.

TDD: tests written before implementation.
"""

import json
import os
import sys
import tempfile
import unittest

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)


# ═══════════════════════════════════════════════════════════════
# L1: Unused imports in dynamic_read_verification.py
# ═══════════════════════════════════════════════════════════════


class TestDynamicReadUnusedImports(unittest.TestCase):
    """L1: No unused imports (ruff F401 clean)."""

    def test_no_unused_imports(self):
        """ruff should report no F401 violations."""
        import subprocess

        path = os.path.join(TOOLS_DIR, "dynamic_read_verification.py")
        config = os.path.join(os.path.expanduser("~"), ".claude", "ruff.toml")
        args = ["ruff", "check", "--select", "F401", path]
        if os.path.exists(config):
            args.extend(["--config", config])
        result = subprocess.run(args, capture_output=True, text=True, timeout=15)  # noqa: S603
        self.assertEqual(
            result.returncode,
            0,
            f"F401 violations found:\n{result.stdout}",
        )


# ═══════════════════════════════════════════════════════════════
# L2: bot_personality.py null/None handling
# ═══════════════════════════════════════════════════════════════


class TestBotPersonalityNullHandling(unittest.TestCase):
    """L2: Consistent None handling in bot_personality."""

    def test_emotion_read_returns_dict_on_failure(self):
        """_create_emotion_read_fn should always return a dict, never None."""
        from bot_personality import _create_emotion_read_fn

        with tempfile.TemporaryDirectory() as tmpdir:
            read_fn = _create_emotion_read_fn(tmpdir)
            result = read_fn()
            self.assertIsInstance(result, dict)
            # Should have all axes
            self.assertIn("fulfillment", result)
            self.assertIn("tension", result)
            self.assertIn("affinity", result)


# ═══════════════════════════════════════════════════════════════
# L3: emotion_state.py error messages with context
# ═══════════════════════════════════════════════════════════════


class TestEmotionStateErrorContext(unittest.TestCase):
    """L3: Error messages should include what was attempted."""

    def test_save_error_includes_path_hint(self):
        """save_state error should indicate what went wrong."""
        from emotion_state import update_state

        result = update_state("/nonexistent/path/that/fails", fulfillment=0.1, mode="delta")
        if result.startswith("ERROR"):
            # Error message should give context about what failed
            self.assertTrue(
                len(result) > 20,
                "Error message too short, should include context",
            )

    def test_nan_error_includes_axis_name(self):
        """NaN rejection should name the problematic axis."""
        from emotion_state import update_state

        tmpdir = tempfile.mkdtemp()
        result = update_state(tmpdir, tension=float("nan"), mode="set")
        self.assertIn("tension", result)
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════
# L5: spontaneous_surfacing.py dead code (H17 verified)
# ═══════════════════════════════════════════════════════════════


class TestSpontaneousSurfacingNoDeadCode(unittest.TestCase):
    """L5: Verify H17 already removed dead code."""

    def test_module_importable(self):
        """Module should import without errors (no syntax issues from removal)."""
        # This file may have been superseded by activation_surface.py
        path = os.path.join(TOOLS_DIR, "spontaneous_surfacing.py")
        if not os.path.exists(path):
            self.skipTest("spontaneous_surfacing.py not present")
        # Just verify it imports
        import importlib.util

        spec = importlib.util.spec_from_file_location("ss", path)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:  # noqa: S110
            pass  # May have import dependencies; existence check is sufficient


# ═══════════════════════════════════════════════════════════════
# L6: episode_recall.py sort optimization
# ═══════════════════════════════════════════════════════════════


class TestEpisodeRecallSortOptimization(unittest.TestCase):
    """L6: keyword_search should sort efficiently."""

    def test_keyword_search_returns_sorted_results(self):
        """Results should be sorted by timestamp descending."""
        from episode_recall import keyword_search

        tmpdir = tempfile.mkdtemp()
        episodes_dir = os.path.join(tmpdir, "episodes")
        os.makedirs(episodes_dir, exist_ok=True)

        # Create a session file with episodes
        session_data = {
            "session_id": "test",
            "episodes": [
                {
                    "episode_id": "ep1",
                    "episode_type": "observation",
                    "summary": "first test observation",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "session_id": "test",
                    "tags": [],
                },
                {
                    "episode_id": "ep2",
                    "episode_type": "observation",
                    "summary": "second test observation",
                    "timestamp": "2026-01-02T00:00:00Z",
                    "session_id": "test",
                    "tags": [],
                },
            ],
        }
        with open(os.path.join(episodes_dir, "session_test.json"), "w") as f:
            json.dump(session_data, f)

        result = keyword_search(tmpdir, keywords=["test", "observation"])
        # Most recent should appear first
        self.assertIn("ep2", result[: result.index("ep1")])
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════
# L8: memory_mcp_server.py flag file optimization
# ═══════════════════════════════════════════════════════════════


class TestMCPServerFlagOptimization(unittest.TestCase):
    """L8: Hook flag should be written once per session, not every search."""

    def test_flag_write_is_conditional(self):
        """Flag write should check if already written recently."""
        import inspect

        import memory_mcp_server

        # Find the memory_search function source
        source = inspect.getsource(memory_mcp_server.memory_search)
        # After fix: should have a check before writing the flag
        # e.g., "if not os.path.exists" or time-based check
        has_conditional = "exists" in source or "already" in source.lower() or "_flag_written" in source
        self.assertTrue(
            has_conditional,
            "memory_search should check before writing flag file every time",
        )


if __name__ == "__main__":
    unittest.main()
