#!/usr/bin/env python3
"""Tests for build_heartbeat_prompt action history integration."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_DIR = str(Path(__file__).resolve().parent.parent)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from cron_scheduler import build_heartbeat_prompt


class TestBuildHeartbeatPromptActionHistory(unittest.TestCase):
    """Tests for action history section in heartbeat prompt."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_hb_prompt_")
        self.actions_file = os.path.join(self.tmpdir, "heartbeat_actions.jsonl")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_actions_file(self):
        """When actions file doesn't exist, prompt includes 'No previous actions.'"""
        prompt = build_heartbeat_prompt("Check Discord", actions_file=self.actions_file)
        self.assertIn("--- ACTION HISTORY ---", prompt)
        self.assertIn("No previous actions.", prompt)

    def test_empty_actions_file(self):
        """When actions file is empty, prompt includes 'No previous actions.'"""
        with open(self.actions_file, "w") as f:
            f.write("")
        prompt = build_heartbeat_prompt("Check Discord", actions_file=self.actions_file)
        self.assertIn("No previous actions.", prompt)

    def test_single_action_entry(self):
        """Single action entry appears in prompt."""
        entry = {
            "timestamp": "2026-03-21T10:00:00+00:00",
            "concern": "heartbeat",
            "action_taken": "full_run",
            "result": "success",
        }
        with open(self.actions_file, "w") as f:
            f.write(json.dumps(entry) + "\n")
        prompt = build_heartbeat_prompt("Check Discord", actions_file=self.actions_file)
        self.assertIn("--- ACTION HISTORY ---", prompt)
        self.assertIn("2026-03-21T10:00:00+00:00", prompt)
        self.assertIn("heartbeat", prompt)
        self.assertIn("full_run", prompt)
        self.assertIn("success", prompt)

    def test_last_3_entries_only(self):
        """Only the last 3 entries are included when more exist."""
        entries = []
        for i in range(5):
            entries.append({
                "timestamp": f"2026-03-21T{10+i:02d}:00:00+00:00",
                "concern": "heartbeat",
                "action_taken": f"action_{i}",
                "result": "success",
            })
        with open(self.actions_file, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        prompt = build_heartbeat_prompt("Check Discord", actions_file=self.actions_file)
        # Should NOT contain action_0 and action_1
        self.assertNotIn("action_0", prompt)
        self.assertNotIn("action_1", prompt)
        # Should contain action_2, action_3, action_4
        self.assertIn("action_2", prompt)
        self.assertIn("action_3", prompt)
        self.assertIn("action_4", prompt)

    def test_malformed_json_lines_skipped(self):
        """Malformed JSON lines are skipped gracefully."""
        good_entry = {
            "timestamp": "2026-03-21T10:00:00+00:00",
            "concern": "heartbeat",
            "action_taken": "full_run",
            "result": "success",
        }
        with open(self.actions_file, "w") as f:
            f.write("not valid json\n")
            f.write(json.dumps(good_entry) + "\n")
            f.write("{bad json too\n")
        prompt = build_heartbeat_prompt("Check Discord", actions_file=self.actions_file)
        self.assertIn("full_run", prompt)
        self.assertIn("--- ACTION HISTORY ---", prompt)

    def test_redundancy_suppression_instruction(self):
        """Prompt includes instruction to skip redundant actions."""
        prompt = build_heartbeat_prompt("Check Discord", actions_file=self.actions_file)
        self.assertIn("skip it unless circumstances changed", prompt)

    def test_entry_format_pipe_separated(self):
        """Each entry is formatted as pipe-separated fields."""
        entry = {
            "timestamp": "2026-03-21T10:00:00+00:00",
            "concern": "heartbeat",
            "action_taken": "full_run",
            "result": "success",
        }
        with open(self.actions_file, "w") as f:
            f.write(json.dumps(entry) + "\n")
        prompt = build_heartbeat_prompt("Check Discord", actions_file=self.actions_file)
        self.assertIn("2026-03-21T10:00:00+00:00 | heartbeat | full_run | success", prompt)

    def test_default_actions_file_when_not_specified(self):
        """When actions_file is not specified, default path is used."""
        # Just verify the function works without actions_file parameter
        # (the default file likely doesn't exist, so should show 'No previous actions.')
        prompt = build_heartbeat_prompt("Check Discord")
        self.assertIn("--- ACTION HISTORY ---", prompt)

    def test_concern_list_still_present(self):
        """Original concern list content is preserved."""
        prompt = build_heartbeat_prompt("Check Discord messages", actions_file=self.actions_file)
        self.assertIn("--- CONCERN LIST ---", prompt)
        self.assertIn("Check Discord messages", prompt)
        self.assertIn("--- END CONCERN LIST ---", prompt)


if __name__ == "__main__":
    unittest.main()
