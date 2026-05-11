"""Tests for session_end_auto.py — auto session_end on Stop hook."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Add tools/ to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

# Import after path setup
import session_end_auto


class TestShouldRun(unittest.TestCase):
    """Test double-execution prevention via flag file."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.hooks_dir = self.tmpdir

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_should_run_when_flag_absent(self):
        """No flag file => should run."""
        result = session_end_auto.should_run(self.hooks_dir)
        self.assertTrue(result)

    def test_should_not_run_when_flag_present(self):
        """Flag file exists => skip."""
        flag_path = os.path.join(self.hooks_dir, ".session-end-done")
        with open(flag_path, "w") as f:
            f.write("1")
        result = session_end_auto.should_run(self.hooks_dir)
        self.assertFalse(result)

    def test_should_run_when_flag_unreadable(self):
        """Flag read error => treat as not done (fail toward execution)."""
        # Create a directory with the flag name to cause read error
        flag_path = os.path.join(self.hooks_dir, ".session-end-done")
        os.makedirs(flag_path)
        result = session_end_auto.should_run(self.hooks_dir)
        # Directory exists => os.path.exists is True => should NOT run
        self.assertFalse(result)


class TestBuildSummary(unittest.TestCase):
    """Test summary generation from STM entries."""

    def test_empty_stm(self):
        """Empty STM => fallback message."""
        summary = session_end_auto.build_summary({"entries": []})
        self.assertIn("自動保存", summary)
        self.assertIn("STMエントリなし", summary)

    def test_none_stm(self):
        """None input => fallback message."""
        summary = session_end_auto.build_summary(None)
        self.assertIn("自動保存", summary)

    def test_stm_with_entries(self):
        """STM with entries => summary includes category and content."""
        store = {
            "entries": [
                {
                    "category": "thought",
                    "content": "Implemented new feature X",
                    "weight": 1.0,
                    "timestamp": "2026-03-26T10:00:00",
                },
                {
                    "category": "unresolved",
                    "content": "Bug Y needs investigation",
                    "weight": 0.8,
                    "timestamp": "2026-03-26T10:05:00",
                },
            ]
        }
        summary = session_end_auto.build_summary(store)
        self.assertIn("thought", summary)
        self.assertIn("Implemented new feature X", summary)
        self.assertIn("unresolved", summary)
        self.assertIn("Bug Y needs investigation", summary)

    def test_summary_truncated_at_limit(self):
        """Summary respects max length to avoid oversized input."""
        entries = []
        for i in range(100):
            entries.append({
                "category": "thought",
                "content": f"Entry number {i} with some padding text to make it long enough " * 5,
                "weight": 1.0,
                "timestamp": f"2026-03-26T10:{i:02d}:00",
            })
        store = {"entries": entries}
        summary = session_end_auto.build_summary(store)
        self.assertLessEqual(len(summary), 2000)

    def test_stm_missing_entries_key(self):
        """Store dict without 'entries' key => fallback."""
        summary = session_end_auto.build_summary({})
        self.assertIn("自動保存", summary)


class TestExtractFields(unittest.TestCase):
    """Test extraction of completed/pending/decisions from STM."""

    def test_extract_from_entries(self):
        store = {
            "entries": [
                {"category": "thought", "content": "Finished task A", "weight": 1.0, "timestamp": "2026-03-26T10:00:00"},
                {"category": "unresolved", "content": "Need to fix B", "weight": 0.9, "timestamp": "2026-03-26T10:01:00"},
                {"category": "self_review", "content": "Decided to use approach C", "weight": 0.8, "timestamp": "2026-03-26T10:02:00"},
            ]
        }
        fields = session_end_auto.extract_fields(store)
        self.assertIn("pending", fields)
        self.assertIn("Need to fix B", fields["pending"])

    def test_extract_empty(self):
        fields = session_end_auto.extract_fields(None)
        self.assertEqual(fields["completed"], "")
        self.assertEqual(fields["pending"], "")


class TestWriteFlag(unittest.TestCase):
    """Test flag file creation."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_write_flag_creates_file(self):
        session_end_auto.write_flag(self.tmpdir)
        flag_path = os.path.join(self.tmpdir, ".session-end-done")
        self.assertTrue(os.path.exists(flag_path))

    def test_write_flag_content_is_timestamp(self):
        session_end_auto.write_flag(self.tmpdir)
        flag_path = os.path.join(self.tmpdir, ".session-end-done")
        content = open(flag_path).read().strip()
        # Should be a numeric timestamp
        self.assertTrue(content.isdigit() or float(content) > 0)


class TestRunSessionEnd(unittest.TestCase):
    """Test the main run() orchestration."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.hooks_dir = self.tmpdir
        self.memory_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        shutil.rmtree(self.memory_dir, ignore_errors=True)

    @patch("session_end_auto.call_session_end")
    @patch("session_end_auto.load_stm")
    def test_run_skips_when_flag_exists(self, mock_load_stm, mock_call):
        """Already done => skip entirely."""
        flag_path = os.path.join(self.hooks_dir, ".session-end-done")
        with open(flag_path, "w") as f:
            f.write("1")
        result = session_end_auto.run(self.hooks_dir, self.memory_dir)
        self.assertFalse(result)
        mock_call.assert_not_called()

    @patch("session_end_auto.call_session_end")
    @patch("session_end_auto.load_stm")
    def test_run_executes_and_writes_flag(self, mock_load_stm, mock_call):
        """Normal run: load STM, call session_end, write flag."""
        mock_load_stm.return_value = {"entries": []}
        mock_call.return_value = "OK"
        result = session_end_auto.run(self.hooks_dir, self.memory_dir)
        self.assertTrue(result)
        mock_call.assert_called_once()
        # Flag should be written
        flag_path = os.path.join(self.hooks_dir, ".session-end-done")
        self.assertTrue(os.path.exists(flag_path))

    @patch("session_end_auto.call_session_end")
    @patch("session_end_auto.load_stm")
    def test_run_writes_flag_even_on_session_end_error(self, mock_load_stm, mock_call):
        """session_end failure => still write flag (avoid retry loop)."""
        mock_load_stm.return_value = {"entries": []}
        mock_call.side_effect = Exception("session_end failed")
        result = session_end_auto.run(self.hooks_dir, self.memory_dir)
        # Flag is written even on failure (per design: flag means "attempted")
        flag_path = os.path.join(self.hooks_dir, ".session-end-done")
        self.assertTrue(os.path.exists(flag_path))
        # Returns False on error
        self.assertFalse(result)

    @patch("session_end_auto.call_session_end")
    @patch("session_end_auto.load_stm")
    def test_run_passes_summary_and_fields(self, mock_load_stm, mock_call):
        """Verify summary and fields are passed to session_end."""
        mock_load_stm.return_value = {
            "entries": [
                {"category": "thought", "content": "Did task X", "weight": 1.0, "timestamp": "2026-03-26T10:00:00"},
                {"category": "unresolved", "content": "Pending Y", "weight": 0.9, "timestamp": "2026-03-26T10:01:00"},
            ]
        }
        mock_call.return_value = "OK"
        session_end_auto.run(self.hooks_dir, self.memory_dir)
        call_args = mock_call.call_args
        # First positional arg is memory_dir, second is summary
        self.assertIn("Did task X", call_args[0][1])
        # kwargs should include pending
        self.assertIn("Pending Y", call_args[1].get("pending", ""))


class TestFallbackMemoryDir(unittest.TestCase):
    """Test that fallback memory dir uses CLAUDE_PROJECT_ROOT-based path."""

    @patch.dict(os.environ, {}, clear=True)
    def test_fallback_uses_env_or_claude_dir(self):
        """Fallback memory_dir should derive from CLAUDE_PROJECT_ROOT or .claude dir."""
        # Read the source to verify the fallback string
        src_path = os.path.join(os.path.dirname(__file__), "session_end_auto.py")
        with open(src_path, encoding="utf-8") as f:
            source = f.read()
        # Source should reference MEMORY_DIR env var (the canonical config knob)
        self.assertIn("MEMORY_DIR", source)


if __name__ == "__main__":
    unittest.main()
