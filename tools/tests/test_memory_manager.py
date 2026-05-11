#!/usr/bin/env python3
"""Tests for memory_manager.py unified wrapper."""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure the tools directory is on sys.path for imports
TOOLS_DIR = str(Path(__file__).resolve().parent.parent)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import memory_manager


# --- Test helpers ---

def _create_temp_memory_dir():
    """Create a temporary memory directory with episodes subdirectory."""
    tmp = tempfile.mkdtemp(prefix="test_mm_")
    episodes_dir = os.path.join(tmp, "episodes")
    os.makedirs(episodes_dir, exist_ok=True)
    return tmp


def _create_session_file(memory_dir, session_id, episodes):
    """Create a session file with the given episodes."""
    episodes_dir = os.path.join(memory_dir, "episodes")
    os.makedirs(episodes_dir, exist_ok=True)
    session_data = {
        "session_id": session_id,
        "created_at": "2026-03-01T00:00:00Z",
        "episodes": episodes,
    }
    filepath = os.path.join(episodes_dir, f"{session_id}.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(session_data, f, ensure_ascii=False, indent=2)
    return filepath


def _make_episode(episode_id="ep001", episode_type="observation",
                  summary="test summary", tags=None, timestamp=None):
    """Create a minimal episode dict."""
    return {
        "episode_id": episode_id,
        "episode_type": episode_type,
        "summary": summary,
        "user_utterances": [],
        "tags": tags or [],
        "timestamp": timestamp or "2026-03-01T12:00:00Z",
        "session_id": "session_test",
    }


# --- CLI parsing tests ---

class TestCLIParsing(unittest.TestCase):
    """Test argument parsing for all subcommands."""

    def test_startup_args(self):
        parser = memory_manager.build_parser()
        args = parser.parse_args([
            "startup",
            "--memory-dir", "/tmp/mem",
            "--cwd", "/tmp/project",
        ])
        self.assertEqual(args.command, "startup")
        self.assertEqual(args.memory_dir, "/tmp/mem")
        self.assertEqual(args.cwd, "/tmp/project")
        self.assertIsNone(args.max_chars)

    def test_startup_with_max_chars(self):
        parser = memory_manager.build_parser()
        args = parser.parse_args([
            "startup",
            "--memory-dir", "/tmp/mem",
            "--cwd", "/tmp/project",
            "--max-chars", "2000",
        ])
        self.assertEqual(args.max_chars, 2000)

    def test_record_args(self):
        parser = memory_manager.build_parser()
        args = parser.parse_args([
            "record",
            "--memory-dir", "/tmp/mem",
            "--type", "observation",
            "--summary", "test summary",
            "--tags", "tag1,tag2",
        ])
        self.assertEqual(args.command, "record")
        self.assertEqual(args.episode_type, "observation")
        self.assertEqual(args.summary, "test summary")
        self.assertEqual(args.tags, "tag1,tag2")

    def test_record_with_user_text(self):
        parser = memory_manager.build_parser()
        args = parser.parse_args([
            "record",
            "--memory-dir", "/tmp/mem",
            "--type", "user_request",
            "--summary", "test",
            "--user-text", "hello",
            "--user-text", "world",
        ])
        self.assertEqual(args.user_text, ["hello", "world"])

    def test_record_with_session_id(self):
        parser = memory_manager.build_parser()
        args = parser.parse_args([
            "record",
            "--memory-dir", "/tmp/mem",
            "--type", "decision",
            "--summary", "decided X",
            "--session-id", "session_20260301_000000",
        ])
        self.assertEqual(args.session_id, "session_20260301_000000")

    def test_maintain_args(self):
        parser = memory_manager.build_parser()
        args = parser.parse_args([
            "maintain",
            "--memory-dir", "/tmp/mem",
        ])
        self.assertEqual(args.command, "maintain")
        self.assertEqual(args.memory_dir, "/tmp/mem")

    def test_search_keywords(self):
        parser = memory_manager.build_parser()
        args = parser.parse_args([
            "search",
            "--memory-dir", "/tmp/mem",
            "--keywords", "bug,fix",
        ])
        self.assertEqual(args.command, "search")
        self.assertEqual(args.keywords, "bug,fix")

    def test_search_tags(self):
        parser = memory_manager.build_parser()
        args = parser.parse_args([
            "search",
            "--memory-dir", "/tmp/mem",
            "--tags", "psyche,emotion",
        ])
        self.assertEqual(args.tags, "psyche,emotion")

    def test_search_last(self):
        parser = memory_manager.build_parser()
        args = parser.parse_args([
            "search",
            "--memory-dir", "/tmp/mem",
            "--last", "7d",
        ])
        self.assertEqual(args.last, "7d")

    def test_search_with_limit(self):
        parser = memory_manager.build_parser()
        args = parser.parse_args([
            "search",
            "--memory-dir", "/tmp/mem",
            "--keywords", "test",
            "--limit", "10",
        ])
        self.assertEqual(args.limit, 10)

    def test_no_command_returns_none(self):
        parser = memory_manager.build_parser()
        args = parser.parse_args([])
        self.assertIsNone(args.command)


# --- _run_step tests ---

class TestRunStep(unittest.TestCase):
    """Test the _run_step error-handling wrapper."""

    def test_successful_step(self):
        ok, result = memory_manager._run_step("test", lambda: "success")
        self.assertTrue(ok)
        self.assertEqual(result, "success")

    def test_error_result(self):
        ok, result = memory_manager._run_step("test", lambda: "ERROR: something failed")
        self.assertFalse(ok)
        self.assertIn("ERROR:", result)
        self.assertIn("[test]", result)

    def test_exception_step(self):
        def raise_error():
            raise ValueError("boom")
        ok, result = memory_manager._run_step("test", raise_error)
        self.assertFalse(ok)
        self.assertIn("[test]", result)
        self.assertIn("Exception", result)
        self.assertIn("boom", result)

    def test_step_with_args(self):
        def add(a, b):
            return str(a + b)
        ok, result = memory_manager._run_step("add", add, 1, 2)
        self.assertTrue(ok)
        self.assertEqual(result, "3")

    def test_step_with_kwargs(self):
        def greet(name="world"):
            return f"hello {name}"
        ok, result = memory_manager._run_step("greet", greet, name="test")
        self.assertTrue(ok)
        self.assertEqual(result, "hello test")


# --- Integration tests with real file I/O ---

class TestStartupIntegration(unittest.TestCase):
    """Test the startup subcommand with real tools."""

    def setUp(self):
        self.memory_dir = _create_temp_memory_dir()
        # Create a session with an episode
        _create_session_file(
            self.memory_dir,
            "session_20260301_000000",
            [_make_episode()],
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.memory_dir, ignore_errors=True)

    def test_startup_basic(self):
        """Startup should complete even with minimal data."""
        parser = memory_manager.build_parser()
        args = parser.parse_args([
            "startup",
            "--memory-dir", self.memory_dir,
            "--cwd", self.memory_dir,  # use temp dir as cwd
        ])
        # Capture stdout
        from io import StringIO
        captured = StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            exit_code = memory_manager.cmd_startup(args)
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue()
        # Should have compression output
        self.assertIn("compression", output.lower())

    def test_startup_empty_memory(self):
        """Startup with empty memory dir should not crash."""
        empty_dir = _create_temp_memory_dir()
        # Remove the episodes dir content (keep the dir)
        import shutil
        episodes_dir = os.path.join(empty_dir, "episodes")
        shutil.rmtree(episodes_dir, ignore_errors=True)
        os.makedirs(episodes_dir, exist_ok=True)

        parser = memory_manager.build_parser()
        args = parser.parse_args([
            "startup",
            "--memory-dir", empty_dir,
            "--cwd", empty_dir,
        ])

        from io import StringIO
        captured = StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            exit_code = memory_manager.cmd_startup(args)
        finally:
            sys.stdout = old_stdout

        # Should not crash
        shutil.rmtree(empty_dir, ignore_errors=True)


class TestRecordIntegration(unittest.TestCase):
    """Test the record subcommand with real tools."""

    def setUp(self):
        self.memory_dir = _create_temp_memory_dir()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.memory_dir, ignore_errors=True)

    def test_record_basic(self):
        """Record should create an episode and rebuild the index."""
        parser = memory_manager.build_parser()
        args = parser.parse_args([
            "record",
            "--memory-dir", self.memory_dir,
            "--type", "observation",
            "--summary", "test recording",
            "--tags", "test,memory",
        ])

        from io import StringIO
        captured = StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            exit_code = memory_manager.cmd_record(args)
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Episode recorded", output)
        self.assertIn("Index built", output)

    def test_record_with_user_text(self):
        """Record with user utterances."""
        parser = memory_manager.build_parser()
        args = parser.parse_args([
            "record",
            "--memory-dir", self.memory_dir,
            "--type", "user_request",
            "--summary", "user asked something",
            "--user-text", "hello there",
        ])

        from io import StringIO
        captured = StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            exit_code = memory_manager.cmd_record(args)
        finally:
            sys.stdout = old_stdout

        self.assertEqual(exit_code, 0)
        self.assertIn("Episode recorded", captured.getvalue())

    def test_record_invalid_type(self):
        """Record with invalid episode type should report error but not crash."""
        parser = memory_manager.build_parser()
        args = parser.parse_args([
            "record",
            "--memory-dir", self.memory_dir,
            "--type", "invalid_type",
            "--summary", "test",
        ])

        from io import StringIO
        captured_err = StringIO()
        captured_out = StringIO()
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = captured_out
        sys.stderr = captured_err
        try:
            exit_code = memory_manager.cmd_record(args)
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        # Should still try to build index even if record fails
        self.assertEqual(exit_code, 1)
        self.assertIn("ERROR", captured_err.getvalue())


class TestMaintainIntegration(unittest.TestCase):
    """Test the maintain subcommand."""

    def setUp(self):
        self.memory_dir = _create_temp_memory_dir()
        _create_session_file(
            self.memory_dir,
            "session_20260301_000000",
            [_make_episode()],
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.memory_dir, ignore_errors=True)

    def test_maintain_basic(self):
        """Maintain should compress, index, and show status."""
        parser = memory_manager.build_parser()
        args = parser.parse_args([
            "maintain",
            "--memory-dir", self.memory_dir,
        ])

        from io import StringIO
        captured = StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            exit_code = memory_manager.cmd_maintain(args)
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue()
        self.assertEqual(exit_code, 0)
        # Should contain index build result
        self.assertIn("Index built", output)
        # Should contain status info
        self.assertIn("sessions", output.lower())


class TestSearchIntegration(unittest.TestCase):
    """Test the search subcommand."""

    def setUp(self):
        self.memory_dir = _create_temp_memory_dir()
        _create_session_file(
            self.memory_dir,
            "session_20260301_000000",
            [
                _make_episode(
                    episode_id="ep001",
                    summary="Fixed a bug in the memory module",
                    tags=["bug", "memory"],
                    timestamp="2026-03-01T12:00:00Z",
                ),
                _make_episode(
                    episode_id="ep002",
                    summary="Added new feature for search",
                    tags=["feature", "search"],
                    timestamp="2026-03-01T13:00:00Z",
                ),
            ],
        )
        # Build the topic index so context search works
        from topic_index import build_index
        build_index(self.memory_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.memory_dir, ignore_errors=True)

    def test_search_by_keywords(self):
        """Search by keywords."""
        parser = memory_manager.build_parser()
        args = parser.parse_args([
            "search",
            "--memory-dir", self.memory_dir,
            "--keywords", "bug",
        ])

        from io import StringIO
        captured = StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            exit_code = memory_manager.cmd_search(args)
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue()
        self.assertIn("bug", output.lower())

    def test_search_by_tags(self):
        """Search by tags."""
        parser = memory_manager.build_parser()
        args = parser.parse_args([
            "search",
            "--memory-dir", self.memory_dir,
            "--tags", "bug",
        ])

        from io import StringIO
        captured = StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            exit_code = memory_manager.cmd_search(args)
        finally:
            sys.stdout = old_stdout

        # Should produce context search output
        output = captured.getvalue()
        self.assertTrue(len(output) > 0)

    def test_search_by_time(self):
        """Search by time range."""
        parser = memory_manager.build_parser()
        args = parser.parse_args([
            "search",
            "--memory-dir", self.memory_dir,
            "--last", "7d",
        ])

        from io import StringIO
        captured = StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            exit_code = memory_manager.cmd_search(args)
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue()
        self.assertTrue(len(output) > 0)

    def test_search_no_params_error(self):
        """Search with no parameters should error."""
        parser = memory_manager.build_parser()
        args = parser.parse_args([
            "search",
            "--memory-dir", self.memory_dir,
        ])

        from io import StringIO
        captured_err = StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured_err
        try:
            exit_code = memory_manager.cmd_search(args)
        finally:
            sys.stderr = old_stderr

        self.assertEqual(exit_code, 1)
        self.assertIn("ERROR", captured_err.getvalue())

    def test_search_combined(self):
        """Search with multiple pathways at once."""
        parser = memory_manager.build_parser()
        args = parser.parse_args([
            "search",
            "--memory-dir", self.memory_dir,
            "--keywords", "bug",
            "--tags", "memory",
        ])

        from io import StringIO
        captured = StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            exit_code = memory_manager.cmd_search(args)
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue()
        # Should have output from both pathways
        self.assertTrue(len(output) > 0)

    def test_search_with_limit(self):
        """Search with a limit parameter."""
        parser = memory_manager.build_parser()
        args = parser.parse_args([
            "search",
            "--memory-dir", self.memory_dir,
            "--keywords", "bug",
            "--limit", "1",
        ])

        from io import StringIO
        captured = StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            exit_code = memory_manager.cmd_search(args)
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue()
        self.assertTrue(len(output) > 0)


# --- Error resilience tests ---

class TestErrorResilience(unittest.TestCase):
    """Test that errors in one step don't block subsequent steps."""

    def test_startup_continues_after_compress_error(self):
        """Startup should continue even if compress fails."""
        memory_dir = _create_temp_memory_dir()

        # Make episodes dir unreadable to cause compress to fail
        # (use mock instead for portability)
        with patch("memory_manager.compress_sessions", side_effect=RuntimeError("mock compress error")):
            parser = memory_manager.build_parser()
            args = parser.parse_args([
                "startup",
                "--memory-dir", memory_dir,
                "--cwd", memory_dir,
            ])

            from io import StringIO
            captured_out = StringIO()
            captured_err = StringIO()
            old_stdout = sys.stdout
            old_stderr = sys.stderr
            sys.stdout = captured_out
            sys.stderr = captured_err
            try:
                exit_code = memory_manager.cmd_startup(args)
            finally:
                sys.stdout = old_stdout
                sys.stderr = old_stderr

            # Should have error about compress
            self.assertIn("compress", captured_err.getvalue().lower())
            # But should still try to build index and generate briefing
            output = captured_out.getvalue()
            # Index build should have run (might show "0 tags" for empty dir)
            self.assertTrue("Index" in output or "index" in output or len(output) > 0)

        import shutil
        shutil.rmtree(memory_dir, ignore_errors=True)

    def test_record_continues_after_episode_error(self):
        """Record should still rebuild index even if recording fails."""
        memory_dir = _create_temp_memory_dir()

        with patch("memory_manager.record_episode", return_value="ERROR: mock record error"):
            parser = memory_manager.build_parser()
            args = parser.parse_args([
                "record",
                "--memory-dir", memory_dir,
                "--type", "observation",
                "--summary", "test",
            ])

            from io import StringIO
            captured_out = StringIO()
            captured_err = StringIO()
            old_stdout = sys.stdout
            old_stderr = sys.stderr
            sys.stdout = captured_out
            sys.stderr = captured_err
            try:
                exit_code = memory_manager.cmd_record(args)
            finally:
                sys.stdout = old_stdout
                sys.stderr = old_stderr

            # Error should be reported
            self.assertIn("ERROR", captured_err.getvalue())
            # Index build should still have been attempted
            self.assertIn("Index", captured_out.getvalue())

        import shutil
        shutil.rmtree(memory_dir, ignore_errors=True)

    def test_maintain_continues_after_index_error(self):
        """Maintain should show status even if index build fails."""
        memory_dir = _create_temp_memory_dir()
        _create_session_file(
            memory_dir,
            "session_20260301_000000",
            [_make_episode()],
        )

        with patch("memory_manager.build_index", side_effect=RuntimeError("mock index error")):
            parser = memory_manager.build_parser()
            args = parser.parse_args([
                "maintain",
                "--memory-dir", memory_dir,
            ])

            from io import StringIO
            captured_out = StringIO()
            captured_err = StringIO()
            old_stdout = sys.stdout
            old_stderr = sys.stderr
            sys.stdout = captured_out
            sys.stderr = captured_err
            try:
                exit_code = memory_manager.cmd_maintain(args)
            finally:
                sys.stdout = old_stdout
                sys.stderr = old_stderr

            # Index error should be reported
            self.assertIn("index", captured_err.getvalue().lower())
            # Status should still show
            output = captured_out.getvalue()
            self.assertTrue(len(output) > 0)

        import shutil
        shutil.rmtree(memory_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
