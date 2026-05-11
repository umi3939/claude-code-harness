"""
Tests for message_log_handler.py — Message log handler for hook dispatcher.

TDD: Tests written before implementation.
Covers:
- stdin JSON parsing (MessageEventContext)
- message:received → discord_data/message_received_log.jsonl
- message:sent → discord_data/message_sent_log.jsonl
- Log entry structure: ts, sender_id, channel_id, content (200 char limit), event_type
- Sent log includes send_success and send_error
- Content truncation at 200 chars
- Log rotation at 1000 lines
- Error handling (invalid JSON, missing fields)
- Exit code 0 on success
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from io import StringIO

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)


def _make_received_context(**overrides):
    """Create a minimal message:received context dict."""
    ctx = {
        "event": "message:received",
        "source": "discord",
        "sender_id": "user123",
        "channel_id": "chan456",
        "message_id": "msg789",
        "content": "hello world",
        "timestamp": "2026-03-22T12:00:00+00:00",
        "conversation_type": "dm",
        "metadata": {"author_name": "testuser"},
        "filter_passed": None,
        "filter_reason": None,
        "sanitize_findings": None,
        "buffer_entry_id": None,
        "send_success": None,
        "send_error": None,
    }
    ctx.update(overrides)
    return ctx


def _make_sent_context(**overrides):
    """Create a minimal message:sent context dict."""
    ctx = _make_received_context(
        event="message:sent",
        send_success=True,
        send_error=None,
    )
    ctx.update(overrides)
    return ctx


class TestReceivedLogHandler(unittest.TestCase):
    """Test message:received log recording."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.log_dir = self.tmpdir

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_handler(self, ctx_dict):
        """Run the handler with given context, return exit code."""
        from message_log_handler import handle_message_log
        stdin_data = json.dumps(ctx_dict)
        return handle_message_log(stdin_data, log_dir=self.log_dir)

    def test_received_creates_log_file(self):
        ctx = _make_received_context()
        exit_code = self._run_handler(ctx)
        self.assertEqual(exit_code, 0)
        log_path = os.path.join(self.log_dir, "message_received_log.jsonl")
        self.assertTrue(os.path.exists(log_path))

    def test_received_log_entry_structure(self):
        ctx = _make_received_context()
        self._run_handler(ctx)
        log_path = os.path.join(self.log_dir, "message_received_log.jsonl")
        with open(log_path, "r", encoding="utf-8") as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry["ts"], "2026-03-22T12:00:00+00:00")
        self.assertEqual(entry["sender_id"], "user123")
        self.assertEqual(entry["channel_id"], "chan456")
        self.assertEqual(entry["content"], "hello world")
        self.assertEqual(entry["event_type"], "message:received")

    def test_received_multiple_entries_appended(self):
        for i in range(3):
            ctx = _make_received_context(content=f"msg {i}")
            self._run_handler(ctx)
        log_path = os.path.join(self.log_dir, "message_received_log.jsonl")
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 3)


class TestSentLogHandler(unittest.TestCase):
    """Test message:sent log recording."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.log_dir = self.tmpdir

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_handler(self, ctx_dict):
        from message_log_handler import handle_message_log
        stdin_data = json.dumps(ctx_dict)
        return handle_message_log(stdin_data, log_dir=self.log_dir)

    def test_sent_creates_separate_log_file(self):
        ctx = _make_sent_context()
        exit_code = self._run_handler(ctx)
        self.assertEqual(exit_code, 0)
        log_path = os.path.join(self.log_dir, "message_sent_log.jsonl")
        self.assertTrue(os.path.exists(log_path))

    def test_sent_log_entry_includes_send_success(self):
        ctx = _make_sent_context(send_success=True)
        self._run_handler(ctx)
        log_path = os.path.join(self.log_dir, "message_sent_log.jsonl")
        with open(log_path, "r", encoding="utf-8") as f:
            entry = json.loads(f.readline())
        self.assertTrue(entry["send_success"])
        self.assertIsNone(entry.get("send_error"))

    def test_sent_log_entry_includes_send_error(self):
        ctx = _make_sent_context(send_success=False, send_error="timeout")
        self._run_handler(ctx)
        log_path = os.path.join(self.log_dir, "message_sent_log.jsonl")
        with open(log_path, "r", encoding="utf-8") as f:
            entry = json.loads(f.readline())
        self.assertFalse(entry["send_success"])
        self.assertEqual(entry["send_error"], "timeout")


class TestContentTruncation(unittest.TestCase):
    """Test content is truncated at 200 characters."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.log_dir = self.tmpdir

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_handler(self, ctx_dict):
        from message_log_handler import handle_message_log
        stdin_data = json.dumps(ctx_dict)
        return handle_message_log(stdin_data, log_dir=self.log_dir)

    def test_content_under_200_chars_not_truncated(self):
        content = "a" * 199
        ctx = _make_received_context(content=content)
        self._run_handler(ctx)
        log_path = os.path.join(self.log_dir, "message_received_log.jsonl")
        with open(log_path, "r", encoding="utf-8") as f:
            entry = json.loads(f.readline())
        self.assertEqual(len(entry["content"]), 199)

    def test_content_at_200_chars_not_truncated(self):
        content = "a" * 200
        ctx = _make_received_context(content=content)
        self._run_handler(ctx)
        log_path = os.path.join(self.log_dir, "message_received_log.jsonl")
        with open(log_path, "r", encoding="utf-8") as f:
            entry = json.loads(f.readline())
        self.assertEqual(len(entry["content"]), 200)

    def test_content_over_200_chars_truncated(self):
        content = "a" * 300
        ctx = _make_received_context(content=content)
        self._run_handler(ctx)
        log_path = os.path.join(self.log_dir, "message_received_log.jsonl")
        with open(log_path, "r", encoding="utf-8") as f:
            entry = json.loads(f.readline())
        self.assertEqual(len(entry["content"]), 200)


class TestLogRotation(unittest.TestCase):
    """Test log rotation at 1000 lines."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.log_dir = self.tmpdir

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_handler(self, ctx_dict):
        from message_log_handler import handle_message_log
        stdin_data = json.dumps(ctx_dict)
        return handle_message_log(stdin_data, log_dir=self.log_dir)

    def test_rotation_at_1000_lines(self):
        # Pre-fill with 999 lines
        log_path = os.path.join(self.log_dir, "message_received_log.jsonl")
        os.makedirs(self.log_dir, exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as f:
            for i in range(999):
                f.write(json.dumps({"ts": f"t{i}", "sender_id": "x",
                                    "channel_id": "x", "content": f"line{i}",
                                    "event_type": "message:received"}) + "\n")
        # Add one more → 1000 lines, no rotation yet
        ctx = _make_received_context(content="line999")
        self._run_handler(ctx)
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 1000)

    def test_rotation_prunes_to_1000(self):
        # Pre-fill with 1000 lines
        log_path = os.path.join(self.log_dir, "message_received_log.jsonl")
        os.makedirs(self.log_dir, exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as f:
            for i in range(1000):
                f.write(json.dumps({"ts": f"t{i}", "sender_id": "x",
                                    "channel_id": "x", "content": f"line{i}",
                                    "event_type": "message:received"}) + "\n")
        # Add one more → 1001 lines → rotation to 1000
        ctx = _make_received_context(content="newest")
        self._run_handler(ctx)
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 1000)
        # Newest line is last
        last_entry = json.loads(lines[-1])
        self.assertEqual(last_entry["content"], "newest")
        # Oldest line (line0) should be gone
        first_entry = json.loads(lines[0])
        self.assertEqual(first_entry["content"], "line1")


class TestErrorHandling(unittest.TestCase):
    """Test error handling for invalid input."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.log_dir = self.tmpdir

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_invalid_json_returns_nonzero(self):
        from message_log_handler import handle_message_log
        exit_code = handle_message_log("not json", log_dir=self.log_dir)
        self.assertNotEqual(exit_code, 0)

    def test_missing_event_field_returns_nonzero(self):
        from message_log_handler import handle_message_log
        ctx = {"sender_id": "x", "content": "y"}  # no 'event'
        exit_code = handle_message_log(json.dumps(ctx), log_dir=self.log_dir)
        self.assertNotEqual(exit_code, 0)

    def test_unknown_event_returns_nonzero(self):
        from message_log_handler import handle_message_log
        ctx = _make_received_context(event="message:unknown")
        exit_code = handle_message_log(json.dumps(ctx), log_dir=self.log_dir)
        self.assertNotEqual(exit_code, 0)

    def test_empty_content_handled(self):
        from message_log_handler import handle_message_log
        ctx = _make_received_context(content="")
        exit_code = handle_message_log(json.dumps(ctx), log_dir=self.log_dir)
        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
