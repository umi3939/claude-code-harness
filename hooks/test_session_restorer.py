#!/usr/bin/env python3
"""Tests for session_restorer.py — TDD first."""

import inspect
import json
import os
import tempfile
import time

import pytest
import session_restorer


@pytest.fixture
def temp_dirs():
    """Create temporary hooks_dir and memory_dir."""
    with tempfile.TemporaryDirectory() as tmpdir:
        hooks_dir = os.path.join(tmpdir, "hooks")
        memory_dir = os.path.join(tmpdir, "memory")
        os.makedirs(hooks_dir)
        os.makedirs(memory_dir)
        yield hooks_dir, memory_dir


def _write_evacuation(hooks_dir, data):
    """Helper: write evacuation data file."""
    path = os.path.join(hooks_dir, ".session-evacuation.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return path


def _make_valid_evacuation(evacuated_at=None):
    """Create a valid evacuation data dict."""
    return {
        "evacuated_at": evacuated_at or time.time(),
        "flow_state": {
            "design": 1700000000,
            "planner": 1700001000,
            "pre_analysis": 1700002000,
            "impl": 1700003000,
            "post_analysis": 0,
            "reviewer": 0,
        },
        "flow_current_phase": "impl",
        "flow_remaining_steps": "post_analysis -> reviewer -> commit",
        "psyche_state": {
            "emotion": {"last_update": 1700003500.0, "last_phase": "impl", "failure_count": 0},
            "observation": {"last_update": 1700003000.0, "last_phase": "impl", "failure_count": 0},
        },
        "stm_summary": "[thought] Working on session continuity\n[self_review] Design looks good",
        "stm_entry_count": 5,
    }


# === Test: restore() happy path ===

class TestRestoreHappyPath:
    def test_restore_returns_text(self, temp_dirs):
        """restore() should return recovery text when evacuation data exists."""
        hooks_dir, memory_dir = temp_dirs
        _write_evacuation(hooks_dir, _make_valid_evacuation())
        text = session_restorer.restore(hooks_dir)
        assert text is not None
        assert len(text) > 0

    def test_restore_includes_session_recovery_header(self, temp_dirs):
        """Recovery text should start with [Session Recovery] header."""
        hooks_dir, memory_dir = temp_dirs
        _write_evacuation(hooks_dir, _make_valid_evacuation())
        text = session_restorer.restore(hooks_dir)
        assert "[Session Recovery]" in text

    def test_restore_includes_flow_position(self, temp_dirs):
        """Recovery text should include flow phase information."""
        hooks_dir, memory_dir = temp_dirs
        _write_evacuation(hooks_dir, _make_valid_evacuation())
        text = session_restorer.restore(hooks_dir)
        assert "impl" in text
        assert "post_analysis" in text

    def test_restore_includes_stm_summary(self, temp_dirs):
        """Recovery text should include STM summary content."""
        hooks_dir, memory_dir = temp_dirs
        _write_evacuation(hooks_dir, _make_valid_evacuation())
        text = session_restorer.restore(hooks_dir)
        assert "session continuity" in text

    def test_restore_includes_psyche_state(self, temp_dirs):
        """Recovery text should include psyche state information."""
        hooks_dir, memory_dir = temp_dirs
        _write_evacuation(hooks_dir, _make_valid_evacuation())
        text = session_restorer.restore(hooks_dir)
        assert "emotion" in text

    def test_restore_includes_timestamp(self, temp_dirs):
        """Recovery text should include evacuation timestamp."""
        hooks_dir, memory_dir = temp_dirs
        _write_evacuation(hooks_dir, _make_valid_evacuation())
        text = session_restorer.restore(hooks_dir)
        # Should contain some time indication
        assert "evacuated" in text.lower() or "20" in text  # Year prefix

    def test_restore_deletes_evacuation_file(self, temp_dirs):
        """After successful restore, evacuation file should be deleted."""
        hooks_dir, memory_dir = temp_dirs
        evac_path = _write_evacuation(hooks_dir, _make_valid_evacuation())
        session_restorer.restore(hooks_dir)
        assert not os.path.exists(evac_path)

    def test_restore_writes_flow_state(self, temp_dirs):
        """After restore, .dev-flow-state should be written with restored phase + restored flag."""
        hooks_dir, memory_dir = temp_dirs
        data = _make_valid_evacuation()
        _write_evacuation(hooks_dir, data)
        session_restorer.restore(hooks_dir)
        flow_path = os.path.join(hooks_dir, ".dev-flow-state")
        assert os.path.isfile(flow_path)
        with open(flow_path, "r", encoding="utf-8") as f:
            flow = json.load(f)
        # impl should have a current timestamp (not the old one)
        assert flow["impl"] > data["flow_state"]["impl"]
        # restored flag
        assert flow.get("restored") is True


# === Test: restore() returns None when no evacuation ===

class TestRestoreNoEvacuation:
    def test_restore_returns_none_without_file(self, temp_dirs):
        """restore() should return None when no evacuation file exists."""
        hooks_dir, memory_dir = temp_dirs
        result = session_restorer.restore(hooks_dir)
        assert result is None


# === Test: validation ===

class TestValidation:
    def test_rejects_missing_required_fields(self, temp_dirs):
        """Evacuation data missing required fields should be rejected."""
        hooks_dir, _ = temp_dirs
        _write_evacuation(hooks_dir, {"evacuated_at": time.time()})  # Missing most fields
        result = session_restorer.restore(hooks_dir)
        assert result is None
        # File should still be deleted (cleanup)
        assert not os.path.exists(os.path.join(hooks_dir, ".session-evacuation.json"))

    def test_rejects_future_timestamp(self, temp_dirs):
        """Evacuation with future timestamp (clock skew) should be rejected."""
        hooks_dir, _ = temp_dirs
        data = _make_valid_evacuation(evacuated_at=time.time() + 7200)  # 2 hours in future
        _write_evacuation(hooks_dir, data)
        result = session_restorer.restore(hooks_dir)
        assert result is None

    def test_rejects_expired_data(self, temp_dirs):
        """Evacuation older than expiry threshold should be rejected."""
        hooks_dir, _ = temp_dirs
        data = _make_valid_evacuation(evacuated_at=time.time() - 7200)  # 2 hours ago
        _write_evacuation(hooks_dir, data)
        result = session_restorer.restore(hooks_dir)
        assert result is None

    def test_rejects_malformed_json(self, temp_dirs):
        """Malformed JSON in evacuation file should be rejected."""
        hooks_dir, _ = temp_dirs
        path = os.path.join(hooks_dir, ".session-evacuation.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("not json at all {{{")
        result = session_restorer.restore(hooks_dir)
        assert result is None
        # File should be cleaned up
        assert not os.path.exists(path)

    def test_rejects_non_dict_data(self, temp_dirs):
        """Evacuation file containing non-dict should be rejected."""
        hooks_dir, _ = temp_dirs
        path = os.path.join(hooks_dir, ".session-evacuation.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump([1, 2, 3], f)
        result = session_restorer.restore(hooks_dir)
        assert result is None


# === Test: one-time restoration ===

class TestOneTimeRestore:
    def test_second_restore_returns_none(self, temp_dirs):
        """Second call to restore() should return None (file already deleted)."""
        hooks_dir, _ = temp_dirs
        _write_evacuation(hooks_dir, _make_valid_evacuation())
        first = session_restorer.restore(hooks_dir)
        assert first is not None
        second = session_restorer.restore(hooks_dir)
        assert second is None


# === Test: flow state restore with current timestamps ===

class TestFlowStateRestore:
    def test_flow_timestamps_updated_to_current(self, temp_dirs):
        """Restored flow state should use current timestamps, not old ones."""
        hooks_dir, _ = temp_dirs
        data = _make_valid_evacuation()
        _write_evacuation(hooks_dir, data)
        before = time.time()
        session_restorer.restore(hooks_dir)
        after = time.time()
        flow_path = os.path.join(hooks_dir, ".dev-flow-state")
        with open(flow_path, "r", encoding="utf-8") as f:
            flow = json.load(f)
        # All non-zero phases should have timestamps >= before
        for phase in ["design", "planner", "pre_analysis", "impl"]:
            if data["flow_state"].get(phase, 0) > 0:
                assert flow[phase] >= before
                assert flow[phase] <= after

    def test_flow_zero_phases_stay_zero(self, temp_dirs):
        """Phases that were 0 in evacuation should remain 0 after restore."""
        hooks_dir, _ = temp_dirs
        data = _make_valid_evacuation()
        _write_evacuation(hooks_dir, data)
        session_restorer.restore(hooks_dir)
        flow_path = os.path.join(hooks_dir, ".dev-flow-state")
        with open(flow_path, "r", encoding="utf-8") as f:
            flow = json.load(f)
        assert flow.get("post_analysis", 0) == 0
        assert flow.get("reviewer", 0) == 0


def test_restore_docstring_describes_pretooluse_hook_chain():
    """restore() docstring must describe PreToolUse hook chain, not UserPromptSubmit."""
    doc = inspect.getdoc(session_restorer.restore)
    assert "PreToolUse" in doc
    assert "behavior-guard.js" in doc
    assert "UserPromptSubmit" not in doc
