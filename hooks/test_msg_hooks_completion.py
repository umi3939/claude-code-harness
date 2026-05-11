"""
Tests for message event hooks completion scripts:
- msg_hook_log_reader.py (Route B: PostToolUse log reader)
- msg_hook_health_check.py (Route B: SessionStart health check)
- tools/message_skill_trigger_handler.py (Route C: DM trigger)

TDD: Tests written first.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.join(os.path.dirname(HOOKS_DIR), "tools")

if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)


# ═══════════════════════════════════════════════════════════════
# Test: msg_hook_log_reader.py
# ═══════════════════════════════════════════════════════════════


class TestLogReaderReadRecentLog(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.log_path = os.path.join(self.tmpdir, "message_hook_log.jsonl")

    def tearDown(self):
        if os.path.exists(self.log_path):
            os.remove(self.log_path)
        os.rmdir(self.tmpdir)

    def _write_entries(self, entries: list[dict]) -> None:
        with open(self.log_path, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def test_nonexistent_file_returns_empty(self):
        from msg_hook_log_reader import read_recent_log
        result = read_recent_log(os.path.join(self.tmpdir, "nonexistent.jsonl"))
        self.assertEqual(result, [])

    def test_single_entry(self):
        from msg_hook_log_reader import read_recent_log
        self._write_entries([{"hook_id": "h1", "success": True}])
        result = read_recent_log(self.log_path, max_entries=5)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["hook_id"], "h1")

    def test_returns_newest_first(self):
        from msg_hook_log_reader import read_recent_log
        self._write_entries([{"hook_id": "h1"}, {"hook_id": "h2"}, {"hook_id": "h3"}])
        result = read_recent_log(self.log_path, max_entries=5)
        self.assertEqual(result[0]["hook_id"], "h3")
        self.assertEqual(result[2]["hook_id"], "h1")

    def test_max_entries_limit(self):
        from msg_hook_log_reader import read_recent_log
        self._write_entries([{"hook_id": f"h{i}"} for i in range(10)])
        result = read_recent_log(self.log_path, max_entries=3)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["hook_id"], "h9")

    def test_invalid_json_lines_skipped(self):
        from msg_hook_log_reader import read_recent_log
        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"hook_id": "valid"}) + "\n")
            f.write("not valid json\n")
            f.write(json.dumps({"hook_id": "also_valid"}) + "\n")
        result = read_recent_log(self.log_path, max_entries=5)
        self.assertEqual(len(result), 2)

    def test_empty_lines_skipped(self):
        from msg_hook_log_reader import read_recent_log
        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"hook_id": "h1"}) + "\n\n")
            f.write(json.dumps({"hook_id": "h2"}) + "\n")
        result = read_recent_log(self.log_path, max_entries=5)
        self.assertEqual(len(result), 2)


class TestLogReaderFormatSummary(unittest.TestCase):

    def test_empty_entries_message(self):
        from msg_hook_log_reader import format_log_summary
        result = format_log_summary([])
        self.assertIn("No recent hook executions", result)

    def test_success_entry_shows_OK(self):
        from msg_hook_log_reader import format_log_summary
        entries = [{"hook_id": "test_hook", "event": "message:received",
                    "success": True, "duration_ms": 15.5,
                    "timestamp": "2026-03-30T00:00:00Z", "error": "", "stdout": ""}]
        result = format_log_summary(entries)
        self.assertIn("test_hook", result)
        self.assertIn("OK", result)

    def test_failure_entry_shows_FAIL(self):
        from msg_hook_log_reader import format_log_summary
        entries = [{"hook_id": "fail_hook", "event": "message:sent",
                    "success": False, "duration_ms": 100.0,
                    "timestamp": "2026-03-30T00:00:00Z", "error": "Timeout", "stdout": ""}]
        result = format_log_summary(entries)
        self.assertIn("FAIL", result)
        self.assertIn("Timeout", result)

    def test_stdout_truncation(self):
        from msg_hook_log_reader import format_log_summary
        entries = [{"hook_id": "h1", "event": "message:received",
                    "success": True, "duration_ms": 5.0,
                    "timestamp": "", "error": "", "stdout": "x" * 300}]
        result = format_log_summary(entries)
        self.assertIn("...", result)


class TestLogReaderMain(unittest.TestCase):

    def test_main_exits_0(self):
        from msg_hook_log_reader import main
        old = os.environ.get("MSG_HOOK_LOG_PATH")
        os.environ["MSG_HOOK_LOG_PATH"] = os.path.join(tempfile.gettempdir(), "nonexistent_lr.jsonl")
        try:
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 0)
        finally:
            if old is not None:
                os.environ["MSG_HOOK_LOG_PATH"] = old
            else:
                del os.environ["MSG_HOOK_LOG_PATH"]


# ═══════════════════════════════════════════════════════════════
# Test: msg_hook_health_check.py
# ═══════════════════════════════════════════════════════════════


class TestHealthCheckLoadConfig(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmpdir, "message_hooks.json")

    def tearDown(self):
        if os.path.exists(self.config_path):
            os.remove(self.config_path)
        os.rmdir(self.tmpdir)

    def test_nonexistent_returns_empty(self):
        from msg_hook_health_check import load_hook_config
        result = load_hook_config(os.path.join(self.tmpdir, "no.json"))
        self.assertEqual(result, [])

    def test_valid_config(self):
        from msg_hook_health_check import load_hook_config
        data = {"hooks": [
            {"id": "h1", "events": ["message:received"], "command": "echo", "enabled": True},
            {"id": "h2", "events": ["message:sent"], "command": "echo", "enabled": False},
        ]}
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        result = load_hook_config(self.config_path)
        self.assertEqual(len(result), 2)

    def test_invalid_json_returns_empty(self):
        from msg_hook_health_check import load_hook_config
        with open(self.config_path, "w") as f:
            f.write("not json")
        result = load_hook_config(self.config_path)
        self.assertEqual(result, [])


class TestHealthCheckScanFailures(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.log_path = os.path.join(self.tmpdir, "log.jsonl")

    def tearDown(self):
        if os.path.exists(self.log_path):
            os.remove(self.log_path)
        os.rmdir(self.tmpdir)

    def test_no_log_returns_empty(self):
        from msg_hook_health_check import scan_recent_failures
        result = scan_recent_failures(os.path.join(self.tmpdir, "none.jsonl"))
        self.assertEqual(result, {})

    def test_counts_failures(self):
        from msg_hook_health_check import scan_recent_failures
        entries = [
            {"hook_id": "h1", "success": False},
            {"hook_id": "h1", "success": True},
            {"hook_id": "h1", "success": False},
            {"hook_id": "h2", "success": False},
        ]
        with open(self.log_path, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        result = scan_recent_failures(self.log_path, scan_count=50)
        self.assertEqual(result["h1"], 2)
        self.assertEqual(result["h2"], 1)


class TestHealthCheckAutoDisabled(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.log_path = os.path.join(self.tmpdir, "log.jsonl")

    def tearDown(self):
        if os.path.exists(self.log_path):
            os.remove(self.log_path)
        os.rmdir(self.tmpdir)

    def test_no_auto_disabled(self):
        from msg_hook_health_check import detect_auto_disabled
        entries = [{"hook_id": "h1", "success": True}] * 10
        with open(self.log_path, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        result = detect_auto_disabled(self.log_path, threshold=5)
        self.assertEqual(result, [])

    def test_consecutive_failures_detected(self):
        from msg_hook_health_check import detect_auto_disabled
        entries = [{"hook_id": "h1", "success": False}] * 5
        with open(self.log_path, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        result = detect_auto_disabled(self.log_path, threshold=5)
        self.assertIn("h1", result)

    def test_success_resets_consecutive(self):
        from msg_hook_health_check import detect_auto_disabled
        entries = [
            {"hook_id": "h1", "success": False},
            {"hook_id": "h1", "success": False},
            {"hook_id": "h1", "success": False},
            {"hook_id": "h1", "success": True},
            {"hook_id": "h1", "success": False},
            {"hook_id": "h1", "success": False},
            {"hook_id": "h1", "success": False},
        ]
        with open(self.log_path, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        result = detect_auto_disabled(self.log_path, threshold=5)
        self.assertEqual(result, [])


class TestHealthCheckFormatSummary(unittest.TestCase):

    def test_no_hooks(self):
        from msg_hook_health_check import format_health_summary
        result = format_health_summary([], {}, [])
        self.assertIn("No hooks registered", result)

    def test_hooks_summary(self):
        from msg_hook_health_check import format_health_summary
        hooks = [
            {"id": "h1", "enabled": True, "events": ["message:received"]},
            {"id": "h2", "enabled": False, "events": ["message:sent"]},
        ]
        result = format_health_summary(hooks, {"h1": 2}, [])
        self.assertIn("enabled: 1", result)
        self.assertIn("disabled: 1", result)
        self.assertIn("recent failures: 2", result)

    def test_auto_disabled_warning(self):
        from msg_hook_health_check import format_health_summary
        hooks = [{"id": "h1", "enabled": True, "events": ["message:received"]}]
        result = format_health_summary(hooks, {}, ["h1"])
        self.assertIn("WARNING", result)
        self.assertIn("auto-disabled", result)


class TestHealthCheckMain(unittest.TestCase):

    def test_main_exits_0(self):
        from msg_hook_health_check import main
        old_c = os.environ.get("MSG_HOOK_CONFIG_PATH")
        old_l = os.environ.get("MSG_HOOK_LOG_PATH")
        os.environ["MSG_HOOK_CONFIG_PATH"] = os.path.join(tempfile.gettempdir(), "nonexistent_hc.json")
        os.environ["MSG_HOOK_LOG_PATH"] = os.path.join(tempfile.gettempdir(), "nonexistent_hc.jsonl")
        try:
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 0)
        finally:
            for key, old in [("MSG_HOOK_CONFIG_PATH", old_c), ("MSG_HOOK_LOG_PATH", old_l)]:
                if old is not None:
                    os.environ[key] = old
                elif key in os.environ:
                    del os.environ[key]


# ═══════════════════════════════════════════════════════════════
# Test: message_skill_trigger_handler.py (Route C)
# ═══════════════════════════════════════════════════════════════


class TestSkillTriggerHandler(unittest.TestCase):

    def test_non_received_event_ignored(self):
        from message_skill_trigger_handler import handle_dm_trigger
        ctx = {"event": "message:sent", "conversation_type": "dm", "content": "hi"}
        self.assertEqual(handle_dm_trigger(json.dumps(ctx)), 0)

    def test_channel_message_ignored(self):
        from message_skill_trigger_handler import handle_dm_trigger
        ctx = {"event": "message:received", "conversation_type": "channel", "content": "hi"}
        self.assertEqual(handle_dm_trigger(json.dumps(ctx)), 0)

    def test_invalid_json_returns_1(self):
        from message_skill_trigger_handler import handle_dm_trigger
        self.assertEqual(handle_dm_trigger("not json"), 1)

    def test_empty_string_returns_1(self):
        from message_skill_trigger_handler import handle_dm_trigger
        self.assertEqual(handle_dm_trigger(""), 1)

    def test_missing_event_returns_1(self):
        from message_skill_trigger_handler import handle_dm_trigger
        ctx = {"conversation_type": "dm", "content": "hi"}
        self.assertEqual(handle_dm_trigger(json.dumps(ctx)), 1)

    def test_dm_received_returns_0(self):
        from message_skill_trigger_handler import handle_dm_trigger
        ctx = {
            "event": "message:received", "conversation_type": "dm",
            "content": "hello", "sender_id": "12345",
            "metadata": {"author_name": "testuser"},
            "timestamp": "2026-03-30T00:00:00Z",
        }
        self.assertEqual(handle_dm_trigger(json.dumps(ctx)), 0)

    def test_no_discord_send_import(self):
        """Verify no import/call of discord_send in executable code."""
        import message_skill_trigger_handler as mod
        with open(mod.__file__, "r", encoding="utf-8") as f:
            lines = f.readlines()
        in_docstring = False
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith('"""') or stripped.endswith('"""'):
                if stripped.count('"""') == 1:
                    in_docstring = not in_docstring
                continue
            if in_docstring or stripped.startswith("#"):
                continue
            if "discord_send" in stripped and ("import" in stripped or "(" in stripped):
                self.fail(f"Line {i} imports or calls discord_send: {stripped}")

    def test_no_skill_executor_import(self):
        """Verify no import/call of skill_executor in executable code."""
        import message_skill_trigger_handler as mod
        with open(mod.__file__, "r", encoding="utf-8") as f:
            lines = f.readlines()
        in_docstring = False
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith('"""') or stripped.endswith('"""'):
                if stripped.count('"""') == 1:
                    in_docstring = not in_docstring
                continue
            if in_docstring or stripped.startswith("#"):
                continue
            if "skill_executor" in stripped and ("import" in stripped or "(" in stripped):
                self.fail(f"Line {i} imports or calls skill_executor: {stripped}")

    def test_content_max_length(self):
        from message_skill_trigger_handler import handle_dm_trigger, CONTENT_MAX_LENGTH
        ctx = {
            "event": "message:received", "conversation_type": "dm",
            "content": "a" * 5000, "sender_id": "12345",
            "metadata": {"author_name": "testuser"},
            "timestamp": "2026-03-30T00:00:00Z",
        }
        self.assertEqual(handle_dm_trigger(json.dumps(ctx)), 0)


if __name__ == "__main__":
    unittest.main()
