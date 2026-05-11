#!/usr/bin/env python3
"""Tests for growth_recorder.py — hook-driven growth recording."""

import json
import os
import sys
from unittest.mock import patch

import pytest

# Add hooks dir to path
HOOKS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)

TOOLS_DIR = os.path.join(os.path.dirname(HOOKS_DIR), "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import growth_recorder

# ── Fixtures ──


@pytest.fixture
def growth_dir(tmp_path):
    """Create a temporary growth directory."""
    d = tmp_path / "growth"
    d.mkdir()
    return str(d)


# ── test_pass event ──


class TestTestPass:
    def test_happy_path(self, growth_dir):
        """test_pass records success and updates mastery."""
        stdin_data = json.dumps({
            "test_file": "tools/tests/test_example.py",
            "test_count": 5,
        })
        result = growth_recorder.handle_test_pass(stdin_data, growth_dir)
        assert result["success"] is True
        assert result["event_type"] == "test_pass"

        # Verify success_patterns.json was created
        sp_path = os.path.join(growth_dir, "success_patterns.json")
        assert os.path.exists(sp_path)
        with open(sp_path, "r", encoding="utf-8") as f:
            records = json.load(f)
        assert len(records) == 1
        assert records[0]["event_type"] == "test_pass"

        # Verify mastery_profile.json was created
        mp_path = os.path.join(growth_dir, "mastery_profile.json")
        assert os.path.exists(mp_path)
        with open(mp_path, "r", encoding="utf-8") as f:
            profile = json.load(f)
        assert "testing" in profile
        assert profile["testing"]["success_count"] == 1

    def test_empty_stdin(self, growth_dir):
        """Empty stdin should not crash (fail-open)."""
        result = growth_recorder.handle_test_pass("", growth_dir)
        assert result["success"] is True
        assert "default" in result.get("context", "").lower() or result["success"]

    def test_invalid_json(self, growth_dir):
        """Invalid JSON should not crash (fail-open)."""
        result = growth_recorder.handle_test_pass("not json", growth_dir)
        assert result["success"] is True

    def test_domain_extraction_from_path(self, growth_dir):
        """Domain should be extracted from test file path."""
        stdin_data = json.dumps({
            "test_file": "tools/tests/test_mastery_profile.py",
        })
        result = growth_recorder.handle_test_pass(stdin_data, growth_dir)
        assert result["success"] is True
        mp_path = os.path.join(growth_dir, "mastery_profile.json")
        with open(mp_path, "r", encoding="utf-8") as f:
            profile = json.load(f)
        assert "testing" in profile


# ── review_pass event ──


class TestReviewPass:
    def test_happy_path(self, growth_dir):
        """review_pass records a review_zero success."""
        stdin_data = json.dumps({
            "review_summary": "No issues found",
        })
        result = growth_recorder.handle_review_pass(stdin_data, growth_dir)
        assert result["success"] is True
        assert result["event_type"] == "review_zero"

        sp_path = os.path.join(growth_dir, "success_patterns.json")
        assert os.path.exists(sp_path)
        with open(sp_path, "r", encoding="utf-8") as f:
            records = json.load(f)
        assert len(records) == 1
        assert records[0]["event_type"] == "review_zero"

    def test_empty_stdin(self, growth_dir):
        """Empty stdin should still record with defaults."""
        result = growth_recorder.handle_review_pass("", growth_dir)
        assert result["success"] is True

    def test_invalid_json(self, growth_dir):
        """Invalid JSON should still record with defaults."""
        result = growth_recorder.handle_review_pass("{bad", growth_dir)
        assert result["success"] is True


# ── session_summary event ──


class TestSessionSummary:
    def test_happy_path(self, growth_dir):
        """session_summary returns health summary string."""
        result = growth_recorder.handle_session_summary("", growth_dir)
        assert result["success"] is True
        assert "summary" in result
        assert isinstance(result["summary"], str)

    def test_with_existing_data(self, growth_dir):
        """session_summary works when data files exist."""
        # Create some data first
        growth_recorder.handle_test_pass(
            json.dumps({"test_file": "test_x.py", "test_count": 3}),
            growth_dir,
        )
        result = growth_recorder.handle_session_summary("", growth_dir)
        assert result["success"] is True
        assert len(result["summary"]) > 0


# ── main() CLI entry point ──


class TestMain:
    def test_unknown_event_type(self, growth_dir):
        """Unknown event type should log error, not crash."""
        with patch.dict(os.environ, {"GROWTH_DIR": growth_dir}):
            exit_code = growth_recorder.main(["growth_recorder.py", "unknown_type"])
        assert exit_code == 1

    def test_missing_event_type(self, growth_dir):
        """Missing event type arg should return error."""
        with patch.dict(os.environ, {"GROWTH_DIR": growth_dir}):
            exit_code = growth_recorder.main(["growth_recorder.py"])
        assert exit_code == 1

    def test_test_pass_via_main(self, growth_dir):
        """main() with test_pass should call handle_test_pass."""
        stdin_data = json.dumps({"test_file": "test_foo.py", "test_count": 2})
        with patch.dict(os.environ, {"GROWTH_DIR": growth_dir}):
            with patch("sys.stdin") as mock_stdin:
                mock_stdin.read.return_value = stdin_data
                exit_code = growth_recorder.main(["growth_recorder.py", "test_pass"])
        assert exit_code == 0

    def test_growth_dir_default(self):
        """GROWTH_DIR should fall back to project_root/growth/."""
        env = {}  # No GROWTH_DIR set
        default = growth_recorder.get_growth_dir(env)
        # project_root is two levels up from growth_recorder.py (hooks/ -> project_root/)
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(growth_recorder.__file__)))
        expected = os.path.join(project_root, "growth")
        assert default == expected

    def test_growth_dir_from_env(self, growth_dir):
        """GROWTH_DIR env var should be used when set."""
        result = growth_recorder.get_growth_dir({"GROWTH_DIR": growth_dir})
        assert result == growth_dir


# ── Edge cases ──


class TestEdgeCases:
    def test_growth_dir_does_not_exist(self, tmp_path):
        """Should create growth_dir if it doesn't exist."""
        new_dir = str(tmp_path / "nonexistent" / "growth")
        result = growth_recorder.handle_test_pass(
            json.dumps({"test_file": "test.py"}), new_dir
        )
        assert result["success"] is True
        assert os.path.isdir(new_dir)

    def test_concurrent_writes_dont_corrupt(self, growth_dir):
        """Multiple sequential writes should not corrupt data."""
        for i in range(5):
            growth_recorder.handle_test_pass(
                json.dumps({"test_file": f"test_{i}.py", "test_count": i + 1}),
                growth_dir,
            )
        sp_path = os.path.join(growth_dir, "success_patterns.json")
        with open(sp_path, "r", encoding="utf-8") as f:
            records = json.load(f)
        assert len(records) == 5

    def test_extremely_long_input(self, growth_dir):
        """Very long input should be truncated, not crash."""
        stdin_data = json.dumps({
            "test_file": "x" * 10000,
            "test_count": 999,
        })
        result = growth_recorder.handle_test_pass(stdin_data, growth_dir)
        assert result["success"] is True

    def test_module_import_failure_is_caught(self, growth_dir):
        """If a growth module raises, handle gracefully."""
        with patch("growth_recorder.success_registry.record_success", side_effect=Exception("boom")):
            result = growth_recorder.handle_test_pass(
                json.dumps({"test_file": "test.py"}), growth_dir
            )
            # Should still succeed (fail-open for individual modules)
            assert result["success"] is False or "error" in result


# ── read_observations utility ──


class TestReadObservations:
    """Tests for read_observations() utility."""

    @pytest.fixture
    def data_dir(self, tmp_path):
        d = tmp_path / "data"
        d.mkdir()
        return str(d)

    def _write_obs(self, data_dir, lines):
        """Helper to write observation lines."""
        path = os.path.join(data_dir, "observations.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")

    def test_normal_read(self, data_dir):
        """Reads observations filtered by session_id."""
        self._write_obs(data_dir, [
            {"ts": "2026-03-29T10:00:00Z", "sid": "s1", "tool": "Agent", "params": {"subagent_type": "designer"}},
            {"ts": "2026-03-29T10:01:00Z", "sid": "s2", "tool": "Agent", "params": {"subagent_type": "reviewer"}},
            {"ts": "2026-03-29T10:02:00Z", "sid": "s1", "tool": "Agent", "params": {"subagent_type": "implementer"}},
        ])
        result = growth_recorder.read_observations(data_dir, "s1")
        assert len(result) == 2
        assert result[0]["params"]["subagent_type"] == "designer"
        assert result[1]["params"]["subagent_type"] == "implementer"

    def test_empty_file(self, data_dir):
        """Empty file returns empty list."""
        self._write_obs(data_dir, [])
        result = growth_recorder.read_observations(data_dir, "s1")
        assert result == []

    def test_malformed_json_lines_skipped(self, data_dir):
        """Malformed JSON lines are skipped."""
        path = os.path.join(data_dir, "observations.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"ts":"T","sid":"s1","tool":"Agent","params":{}}\n')
            f.write("not json\n")
            f.write('{"ts":"T2","sid":"s1","tool":"Bash","params":{}}\n')
        result = growth_recorder.read_observations(data_dir, "s1")
        assert len(result) == 2

    def test_session_id_filter(self, data_dir):
        """Only entries matching session_id are returned."""
        self._write_obs(data_dir, [
            {"ts": "T1", "sid": "s1", "tool": "Agent", "params": {}},
            {"ts": "T2", "sid": "s2", "tool": "Agent", "params": {}},
            {"ts": "T3", "sid": "s1", "tool": "Bash", "params": {}},
        ])
        result = growth_recorder.read_observations(data_dir, "s2")
        assert len(result) == 1
        assert result[0]["sid"] == "s2"

    def test_limit_respected(self, data_dir):
        """Limit caps the number of lines read."""
        lines = [{"ts": f"T{i}", "sid": "s1", "tool": "Agent", "params": {}} for i in range(100)]
        self._write_obs(data_dir, lines)
        result = growth_recorder.read_observations(data_dir, "s1", limit=10)
        assert len(result) <= 10

    def test_reads_tail_not_head(self, data_dir):
        """Limit should read from the tail (newest lines), not from the head."""
        # Write 20 lines: first 15 are old session, last 5 are new session
        old_lines = [
            {"ts": f"T{i}", "sid": "old", "tool": "Agent", "params": {"subagent_type": f"old_{i}"}}
            for i in range(15)
        ]
        new_lines = [
            {"ts": f"T{15 + i}", "sid": "new", "tool": "Agent", "params": {"subagent_type": f"new_{i}"}}
            for i in range(5)
        ]
        self._write_obs(data_dir, old_lines + new_lines)

        # With limit=10, should read the last 10 lines (5 old + 5 new)
        result = growth_recorder.read_observations(data_dir, "new", limit=10)
        assert len(result) == 5
        assert result[0]["params"]["subagent_type"] == "new_0"

    def test_file_not_found(self, tmp_path):
        """Missing file returns empty list."""
        result = growth_recorder.read_observations(str(tmp_path / "nonexistent"), "s1")
        assert result == []


# ── handle_trajectory event ──


class TestHandleTrajectory:
    """Tests for handle_trajectory() handler."""

    @pytest.fixture
    def data_dir(self, tmp_path):
        d = tmp_path / "data"
        d.mkdir()
        return str(d)

    def _write_obs(self, data_dir, lines):
        path = os.path.join(data_dir, "observations.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")

    def test_normal_trajectory_record(self, growth_dir, data_dir):
        """Records trajectory when 2+ Agent calls exist."""
        self._write_obs(data_dir, [
            {"ts": "T1", "sid": "s1", "tool": "Agent", "params": {"subagent_type": "designer", "description": "Design doc"}},
            {"ts": "T2", "sid": "s1", "tool": "Agent", "params": {"subagent_type": "implementer", "description": "Implement"}},
            {"ts": "T3", "sid": "s1", "tool": "Agent", "params": {"subagent_type": "reviewer", "description": "Review"}},
        ])
        stdin_data = json.dumps({
            "review_summary": "All clear",
            "session_id": "s1",
            "data_dir": data_dir,
        })
        result = growth_recorder.handle_trajectory(stdin_data, growth_dir)
        assert result["success"] is True
        # Verify trajectory was stored
        traj_path = os.path.join(growth_dir, "trajectories.json")
        assert os.path.exists(traj_path)
        with open(traj_path, "r", encoding="utf-8") as f:
            records = json.load(f)
        assert len(records) == 1
        assert "designer" in records[0]["task_class"]

    def test_skip_when_too_few_agents(self, growth_dir, data_dir):
        """Skip when fewer than 2 Agent calls."""
        self._write_obs(data_dir, [
            {"ts": "T1", "sid": "s1", "tool": "Agent", "params": {"subagent_type": "reviewer"}},
        ])
        stdin_data = json.dumps({
            "review_summary": "OK",
            "session_id": "s1",
            "data_dir": data_dir,
        })
        result = growth_recorder.handle_trajectory(stdin_data, growth_dir)
        assert result["success"] is True
        assert "skipped" in result.get("reason", "")

    def test_skip_on_obs_failure(self, growth_dir, tmp_path):
        """Skip when observations can't be read."""
        stdin_data = json.dumps({
            "review_summary": "OK",
            "session_id": "s1",
            "data_dir": str(tmp_path / "nonexistent"),
        })
        result = growth_recorder.handle_trajectory(stdin_data, growth_dir)
        assert result["success"] is True
        assert "skipped" in result.get("reason", "")

    def test_fail_open_on_stdin_error(self, growth_dir):
        """Bad stdin should not crash."""
        result = growth_recorder.handle_trajectory("not json", growth_dir)
        assert result["success"] is True
        assert "skipped" in result.get("reason", "")


# ── handle_session_aar event ──


class TestHandleSessionAAR:
    """Tests for handle_session_aar() handler."""

    @pytest.fixture
    def data_dir(self, tmp_path):
        d = tmp_path / "data"
        d.mkdir()
        return str(d)

    def _write_obs(self, data_dir, lines):
        path = os.path.join(data_dir, "observations.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")

    def test_normal_aar_record(self, growth_dir, data_dir):
        """Records AAR with observations data."""
        self._write_obs(data_dir, [
            {"ts": "T1", "sid": "s1", "tool": "Agent", "params": {"subagent_type": "designer"}},
            {"ts": "T2", "sid": "s1", "tool": "Agent", "params": {"subagent_type": "implementer"}},
            {"ts": "T3", "sid": "s1", "tool": "Bash", "params": {"cmd": "pytest"}},
            {"ts": "T4", "sid": "s1", "tool": "Agent", "params": {"subagent_type": "reviewer"}},
        ])
        stdin_data = json.dumps({
            "summary": "Implemented feature X",
            "completed": ["feature X done"],
            "pending": ["test coverage"],
            "decisions": ["used TDD"],
            "session_id": "s1",
            "data_dir": data_dir,
            "memory_dir": growth_dir,
        })
        result = growth_recorder.handle_session_aar(stdin_data, growth_dir)
        assert result["success"] is True
        aar_path = os.path.join(growth_dir, "after_action_reviews.json")
        assert os.path.exists(aar_path)
        with open(aar_path, "r", encoding="utf-8") as f:
            records = json.load(f)
        assert len(records) == 1
        assert "auto" in records[0]["tags"]
        assert "session" in records[0]["tags"]

    def test_skip_when_intent_empty(self, growth_dir, data_dir):
        """Skip AAR when summary is empty (no intent)."""
        self._write_obs(data_dir, [
            {"ts": "T1", "sid": "s1", "tool": "Agent", "params": {"subagent_type": "reviewer"}},
        ])
        stdin_data = json.dumps({
            "summary": "",
            "completed": [],
            "pending": [],
            "decisions": [],
            "session_id": "s1",
            "data_dir": data_dir,
            "memory_dir": growth_dir,
        })
        result = growth_recorder.handle_session_aar(stdin_data, growth_dir)
        assert result["success"] is True
        assert "skipped" in result.get("reason", "")

    def test_skip_when_no_agents(self, growth_dir, data_dir):
        """Skip AAR when no Agent calls in observations (why_success empty)."""
        self._write_obs(data_dir, [
            {"ts": "T1", "sid": "s1", "tool": "Bash", "params": {}},
        ])
        stdin_data = json.dumps({
            "summary": "Session summary",
            "completed": ["task done"],
            "pending": [],
            "decisions": [],
            "session_id": "s1",
            "data_dir": data_dir,
            "memory_dir": growth_dir,
        })
        result = growth_recorder.handle_session_aar(stdin_data, growth_dir)
        assert result["success"] is True
        assert "skipped" in result.get("reason", "")
        # AAR should NOT have been created
        aar_path = os.path.join(growth_dir, "after_action_reviews.json")
        assert not os.path.exists(aar_path)

    def test_obs_failure_skips_aar(self, growth_dir, tmp_path):
        """When obs file is missing, no Agent data → why_success empty → skip AAR."""
        stdin_data = json.dumps({
            "summary": "Did some work",
            "completed": ["task A"],
            "pending": [],
            "decisions": ["decision 1"],
            "session_id": "s1",
            "data_dir": str(tmp_path / "nonexistent"),
            "memory_dir": growth_dir,
        })
        result = growth_recorder.handle_session_aar(stdin_data, growth_dir)
        assert result["success"] is True
        assert "skipped" in result.get("reason", "")

    def test_fail_open_on_stdin_error(self, growth_dir):
        """Bad stdin should not crash."""
        result = growth_recorder.handle_session_aar("not json", growth_dir)
        assert result["success"] is True
        assert "skipped" in result.get("reason", "")


# ── handle_subagent_stop event ──


class TestHandleSubagentStop:
    """Tests for handle_subagent_stop() handler."""

    def test_happy_path(self, growth_dir):
        """SubagentStop with end_turn reason records mastery update."""
        stdin_data = json.dumps({
            "reason": "end_turn",
            "agent_id": "agent-123",
            "agent_transcript_path": "/tmp/transcript.json",
            "last_assistant_message": "Implementation complete.",
        })
        result = growth_recorder.handle_subagent_stop(stdin_data, growth_dir)
        assert result["success"] is True
        assert result["event_type"] == "subagent_stop"
        assert result["reason"] == "end_turn"
        assert result["agent_id"] == "agent-123"

    def test_empty_stdin(self, growth_dir):
        """Empty stdin should not crash (fail-open), returns skipped."""
        result = growth_recorder.handle_subagent_stop("", growth_dir)
        assert result["success"] is True
        assert "skipped" in result.get("reason_detail", "")

    def test_invalid_json(self, growth_dir):
        """Invalid JSON should not crash (fail-open), returns skipped."""
        result = growth_recorder.handle_subagent_stop("not valid json{", growth_dir)
        assert result["success"] is True
        assert "skipped" in result.get("reason_detail", "")

    def test_long_message_truncated(self, growth_dir):
        """last_assistant_message longer than 500 chars should be truncated."""
        long_msg = "x" * 2000
        stdin_data = json.dumps({
            "reason": "end_turn",
            "agent_id": "agent-456",
            "last_assistant_message": long_msg,
        })
        result = growth_recorder.handle_subagent_stop(stdin_data, growth_dir)
        assert result["success"] is True
        # Verify truncation happened (message stored should be <= 500)
        assert len(result.get("last_message", "")) <= 500

    def test_unknown_reason(self, growth_dir):
        """Unknown reason string should still record successfully."""
        stdin_data = json.dumps({
            "reason": "some_new_reason",
            "agent_id": "agent-789",
        })
        result = growth_recorder.handle_subagent_stop(stdin_data, growth_dir)
        assert result["success"] is True
        assert result["reason"] == "some_new_reason"

    def test_mastery_update_on_end_turn(self, growth_dir):
        """end_turn reason should trigger mastery profile update."""
        stdin_data = json.dumps({
            "reason": "end_turn",
            "agent_id": "agent-100",
            "last_assistant_message": "Done.",
        })
        result = growth_recorder.handle_subagent_stop(stdin_data, growth_dir)
        assert result["success"] is True

        # Verify mastery_profile.json was updated
        mp_path = os.path.join(growth_dir, "mastery_profile.json")
        assert os.path.exists(mp_path)
        with open(mp_path, "r", encoding="utf-8") as f:
            profile = json.load(f)
        assert "agent_lifecycle" in profile
        assert profile["agent_lifecycle"]["success_count"] >= 1

    def test_module_import_failure(self, growth_dir):
        """If mastery_profile module raises, handle gracefully."""
        with patch(
            "growth_recorder.mastery_profile.update_mastery",
            side_effect=Exception("module boom"),
        ):
            stdin_data = json.dumps({
                "reason": "end_turn",
                "agent_id": "agent-err",
            })
            result = growth_recorder.handle_subagent_stop(stdin_data, growth_dir)
            # Should still succeed (mastery update is non-fatal)
            assert result["success"] is True

    def test_missing_agent_id(self, growth_dir):
        """Missing agent_id should default to 'unknown'."""
        stdin_data = json.dumps({
            "reason": "end_turn",
        })
        result = growth_recorder.handle_subagent_stop(stdin_data, growth_dir)
        assert result["success"] is True
        assert result["agent_id"] == "unknown"

    def test_no_transcript_path(self, growth_dir):
        """Missing transcript path should be handled gracefully."""
        stdin_data = json.dumps({
            "reason": "end_turn",
            "agent_id": "agent-no-path",
        })
        result = growth_recorder.handle_subagent_stop(stdin_data, growth_dir)
        assert result["success"] is True

    def test_event_handler_registered(self):
        """subagent_stop should be registered in EVENT_HANDLERS."""
        assert "subagent_stop" in growth_recorder.EVENT_HANDLERS
        assert growth_recorder.EVENT_HANDLERS["subagent_stop"] == growth_recorder.handle_subagent_stop
