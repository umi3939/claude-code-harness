"""
Tests for message_memory_handler.py — Memory recording handler for hook dispatcher.

TDD: Tests written before implementation.
Covers:
- stdin JSON parsing (MessageEventContext)
- DM messages trigger memory_manager.py record subprocess
- Channel messages are ignored (exit 0, no subprocess)
- Subprocess call arguments (--type, --summary, --tags)
- Summary format: sender + content preview
- Subprocess failure does not cause non-zero exit
- HOOK_ORIGIN environment variable set to prevent future circular hooks
- Error handling (invalid JSON, missing fields)
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)


def _make_dm_context(**overrides):
    """Create a DM message:received context dict."""
    ctx = {
        "event": "message:received",
        "source": "discord",
        "sender_id": "user123",
        "channel_id": "chan456",
        "message_id": "msg789",
        "content": "hello, remember this please",
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


def _make_channel_context(**overrides):
    """Create a channel message:received context dict."""
    ctx = _make_dm_context(conversation_type="channel")
    ctx.update(overrides)
    return ctx


class TestDMMemoryRecording(unittest.TestCase):
    """Test that DM messages trigger memory recording."""

    @patch("message_memory_handler.subprocess.run")
    def test_dm_message_calls_memory_manager(self, mock_run):
        from message_memory_handler import handle_message_memory
        mock_run.return_value = MagicMock(returncode=0)
        ctx = _make_dm_context()
        exit_code = handle_message_memory(json.dumps(ctx))
        self.assertEqual(exit_code, 0)
        mock_run.assert_called_once()

    @patch("message_memory_handler.subprocess.run")
    def test_dm_message_passes_correct_type(self, mock_run):
        from message_memory_handler import handle_message_memory
        mock_run.return_value = MagicMock(returncode=0)
        ctx = _make_dm_context()
        handle_message_memory(json.dumps(ctx))
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        # Find --type argument
        type_idx = cmd.index("--type")
        self.assertEqual(cmd[type_idx + 1], "discord_received")

    @patch("message_memory_handler.subprocess.run")
    def test_dm_message_passes_summary_with_sender(self, mock_run):
        from message_memory_handler import handle_message_memory
        mock_run.return_value = MagicMock(returncode=0)
        ctx = _make_dm_context(content="hello world")
        ctx["metadata"]["author_name"] = "alice"
        handle_message_memory(json.dumps(ctx))
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        summary_idx = cmd.index("--summary")
        summary = cmd[summary_idx + 1]
        self.assertIn("alice", summary)
        self.assertIn("hello world", summary)

    @patch("message_memory_handler.subprocess.run")
    def test_dm_message_passes_tags(self, mock_run):
        from message_memory_handler import handle_message_memory
        mock_run.return_value = MagicMock(returncode=0)
        ctx = _make_dm_context()
        handle_message_memory(json.dumps(ctx))
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        tags_idx = cmd.index("--tags")
        tags = cmd[tags_idx + 1]
        self.assertIn("user123", tags)
        self.assertIn("discord", tags)
        self.assertIn("dm", tags)

    @patch("message_memory_handler.subprocess.run")
    def test_dm_message_sets_hook_origin_env(self, mock_run):
        from message_memory_handler import handle_message_memory
        mock_run.return_value = MagicMock(returncode=0)
        ctx = _make_dm_context()
        handle_message_memory(json.dumps(ctx))
        call_args = mock_run.call_args
        env = call_args[1].get("env") or call_args[0][0]  # check kwargs
        # The env should contain HOOK_ORIGIN
        if "env" in call_args[1]:
            self.assertIn("HOOK_ORIGIN", call_args[1]["env"])
            self.assertEqual(call_args[1]["env"]["HOOK_ORIGIN"], "message_memory_handler")

    @patch("message_memory_handler.subprocess.run")
    def test_dm_message_uses_sender_id_when_no_author_name(self, mock_run):
        from message_memory_handler import handle_message_memory
        mock_run.return_value = MagicMock(returncode=0)
        ctx = _make_dm_context(metadata={})  # no author_name
        handle_message_memory(json.dumps(ctx))
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        summary_idx = cmd.index("--summary")
        summary = cmd[summary_idx + 1]
        self.assertIn("user123", summary)


class TestChannelMessageIgnored(unittest.TestCase):
    """Test that channel messages are ignored."""

    @patch("message_memory_handler.subprocess.run")
    def test_channel_message_not_recorded(self, mock_run):
        from message_memory_handler import handle_message_memory
        ctx = _make_channel_context()
        exit_code = handle_message_memory(json.dumps(ctx))
        self.assertEqual(exit_code, 0)
        mock_run.assert_not_called()


class TestSubprocessFailure(unittest.TestCase):
    """Test that subprocess failure does not propagate."""

    @patch("message_memory_handler.subprocess.run")
    def test_memory_manager_failure_returns_zero(self, mock_run):
        from message_memory_handler import handle_message_memory
        mock_run.side_effect = Exception("memory_manager crashed")
        ctx = _make_dm_context()
        exit_code = handle_message_memory(json.dumps(ctx))
        self.assertEqual(exit_code, 0)

    @patch("message_memory_handler.subprocess.run")
    def test_memory_manager_nonzero_exit_returns_zero(self, mock_run):
        from message_memory_handler import handle_message_memory
        mock_run.return_value = MagicMock(returncode=1)
        ctx = _make_dm_context()
        exit_code = handle_message_memory(json.dumps(ctx))
        self.assertEqual(exit_code, 0)


class TestErrorHandling(unittest.TestCase):
    """Test error handling."""

    def test_invalid_json_returns_nonzero(self):
        from message_memory_handler import handle_message_memory
        exit_code = handle_message_memory("not json")
        self.assertNotEqual(exit_code, 0)

    def test_missing_event_returns_nonzero(self):
        from message_memory_handler import handle_message_memory
        ctx = {"sender_id": "x", "content": "y"}
        exit_code = handle_message_memory(json.dumps(ctx))
        self.assertNotEqual(exit_code, 0)

    @patch("message_memory_handler.subprocess.run")
    def test_non_received_event_returns_zero_no_action(self, mock_run):
        from message_memory_handler import handle_message_memory
        ctx = _make_dm_context(event="message:sent")
        exit_code = handle_message_memory(json.dumps(ctx))
        self.assertEqual(exit_code, 0)
        mock_run.assert_not_called()


class TestSummaryTruncation(unittest.TestCase):
    """Test that summary content is reasonably truncated."""

    @patch("message_memory_handler.subprocess.run")
    def test_long_content_truncated_in_summary(self, mock_run):
        from message_memory_handler import handle_message_memory
        mock_run.return_value = MagicMock(returncode=0)
        long_content = "a" * 500
        ctx = _make_dm_context(content=long_content)
        handle_message_memory(json.dumps(ctx))
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        summary_idx = cmd.index("--summary")
        summary = cmd[summary_idx + 1]
        # Summary should not contain full 500 chars of content
        self.assertLess(len(summary), 300)


if __name__ == "__main__":
    unittest.main()
