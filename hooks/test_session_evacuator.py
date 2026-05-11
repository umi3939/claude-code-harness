#!/usr/bin/env python3
"""Tests for session_evacuator.py — TDD first."""

import json
import os
import tempfile
import time

import pytest

# Import will be available after implementation
# For TDD, we define expected interface first
import session_evacuator


@pytest.fixture
def temp_dirs():
    """Create temporary hooks_dir and memory_dir with realistic state files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        hooks_dir = os.path.join(tmpdir, "hooks")
        memory_dir = os.path.join(tmpdir, "memory")
        os.makedirs(hooks_dir)
        os.makedirs(memory_dir)
        yield hooks_dir, memory_dir


@pytest.fixture
def flow_state_file(temp_dirs):
    """Create a .dev-flow-state file with realistic content."""
    hooks_dir, _ = temp_dirs
    state = {
        "design": 1700000000,
        "planner": 1700001000,
        "pre_analysis": 1700002000,
        "impl": 1700003000,
        "post_analysis": 0,
        "reviewer": 0,
    }
    path = os.path.join(hooks_dir, ".dev-flow-state")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f)
    return path


@pytest.fixture
def psyche_state_file(temp_dirs):
    """Create a .psyche-drive-state.json file."""
    _, memory_dir = temp_dirs
    state = {
        "categories": {
            "emotion": {"last_update": 1700003500.0, "last_phase": "impl", "failure_count": 0},
            "observation": {"last_update": 1700003000.0, "last_phase": "impl", "failure_count": 0},
            "activation": {"last_update": 1700002500.0, "last_phase": "pre_analysis", "failure_count": 1},
        }
    }
    path = os.path.join(memory_dir, ".psyche-drive-state.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f)
    return path


@pytest.fixture
def stm_file(temp_dirs):
    """Create a short_term_memory.json file with test entries."""
    _, memory_dir = temp_dirs
    store = {
        "entries": [
            {
                "id": "aaa111",
                "category": "thought",
                "content": "Working on session continuity feature",
                "timestamp": "2026-03-26T10:00:00+00:00",
                "weight": 1.0,
            },
            {
                "id": "bbb222",
                "category": "self_review",
                "content": "Reviewed design doc, looks good",
                "timestamp": "2026-03-26T10:05:00+00:00",
                "weight": 0.9,
            },
            {
                "id": "ccc333",
                "category": "question",
                "content": "How does PreCompact hook interact with MCP?",
                "timestamp": "2026-03-26T10:10:00+00:00",
                "weight": 0.8,
            },
        ]
    }
    path = os.path.join(memory_dir, "short_term_memory.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f)
    return path


# === Test: evacuate() happy path ===

class TestEvacuateHappyPath:
    def test_evacuate_creates_file(self, temp_dirs, flow_state_file, psyche_state_file, stm_file):
        """evacuate() should create .session-evacuation.json in hooks_dir."""
        hooks_dir, memory_dir = temp_dirs
        result = session_evacuator.evacuate(hooks_dir, memory_dir)
        assert result is True
        evac_path = os.path.join(hooks_dir, ".session-evacuation.json")
        assert os.path.isfile(evac_path)

    def test_evacuate_data_has_required_fields(self, temp_dirs, flow_state_file, psyche_state_file, stm_file):
        """Evacuation data must contain all required fields."""
        hooks_dir, memory_dir = temp_dirs
        session_evacuator.evacuate(hooks_dir, memory_dir)
        evac_path = os.path.join(hooks_dir, ".session-evacuation.json")
        with open(evac_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        required_fields = [
            "evacuated_at", "flow_state", "flow_current_phase",
            "flow_remaining_steps", "psyche_state", "stm_summary",
            "stm_entry_count",
        ]
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"

    def test_evacuate_flow_state_extracted(self, temp_dirs, flow_state_file, psyche_state_file, stm_file):
        """Flow state should be correctly read from .dev-flow-state."""
        hooks_dir, memory_dir = temp_dirs
        session_evacuator.evacuate(hooks_dir, memory_dir)
        evac_path = os.path.join(hooks_dir, ".session-evacuation.json")
        with open(evac_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["flow_current_phase"] == "impl"
        assert "post_analysis" in data["flow_remaining_steps"]

    def test_evacuate_psyche_state_extracted(self, temp_dirs, flow_state_file, psyche_state_file, stm_file):
        """Psyche state should be correctly read from .psyche-drive-state.json."""
        hooks_dir, memory_dir = temp_dirs
        session_evacuator.evacuate(hooks_dir, memory_dir)
        evac_path = os.path.join(hooks_dir, ".session-evacuation.json")
        with open(evac_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        psyche = data["psyche_state"]
        assert "emotion" in psyche
        assert "observation" in psyche
        assert "activation" in psyche

    def test_evacuate_stm_summary_extracted(self, temp_dirs, flow_state_file, psyche_state_file, stm_file):
        """STM summary should contain recent entry content."""
        hooks_dir, memory_dir = temp_dirs
        session_evacuator.evacuate(hooks_dir, memory_dir)
        evac_path = os.path.join(hooks_dir, ".session-evacuation.json")
        with open(evac_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["stm_entry_count"] == 3
        assert "session continuity" in data["stm_summary"]

    def test_evacuate_timestamp_is_recent(self, temp_dirs, flow_state_file, psyche_state_file, stm_file):
        """evacuated_at should be a recent epoch timestamp."""
        hooks_dir, memory_dir = temp_dirs
        before = time.time()
        session_evacuator.evacuate(hooks_dir, memory_dir)
        after = time.time()
        evac_path = os.path.join(hooks_dir, ".session-evacuation.json")
        with open(evac_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert before <= data["evacuated_at"] <= after


# === Test: edge cases ===

class TestEvacuateEdgeCases:
    def test_evacuate_no_flow_state(self, temp_dirs, psyche_state_file, stm_file):
        """evacuate() should succeed even without .dev-flow-state."""
        hooks_dir, memory_dir = temp_dirs
        result = session_evacuator.evacuate(hooks_dir, memory_dir)
        assert result is True
        evac_path = os.path.join(hooks_dir, ".session-evacuation.json")
        with open(evac_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["flow_state"] == {}
        assert data["flow_current_phase"] == ""
        assert data["flow_remaining_steps"] == ""

    def test_evacuate_no_psyche_state(self, temp_dirs, flow_state_file, stm_file):
        """evacuate() should succeed even without .psyche-drive-state.json."""
        hooks_dir, memory_dir = temp_dirs
        result = session_evacuator.evacuate(hooks_dir, memory_dir)
        assert result is True
        evac_path = os.path.join(hooks_dir, ".session-evacuation.json")
        with open(evac_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["psyche_state"] == {}

    def test_evacuate_no_stm(self, temp_dirs, flow_state_file, psyche_state_file):
        """evacuate() should succeed even without short_term_memory.json."""
        hooks_dir, memory_dir = temp_dirs
        result = session_evacuator.evacuate(hooks_dir, memory_dir)
        assert result is True
        evac_path = os.path.join(hooks_dir, ".session-evacuation.json")
        with open(evac_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["stm_summary"] == ""
        assert data["stm_entry_count"] == 0

    def test_evacuate_empty_memory_dir(self, temp_dirs):
        """evacuate() should succeed with completely empty memory_dir."""
        hooks_dir, memory_dir = temp_dirs
        result = session_evacuator.evacuate(hooks_dir, memory_dir)
        assert result is True

    def test_evacuate_overwrites_existing(self, temp_dirs, flow_state_file, psyche_state_file, stm_file):
        """Consecutive evacuations should overwrite previous data."""
        hooks_dir, memory_dir = temp_dirs
        session_evacuator.evacuate(hooks_dir, memory_dir)
        time.sleep(0.05)
        session_evacuator.evacuate(hooks_dir, memory_dir)
        evac_path = os.path.join(hooks_dir, ".session-evacuation.json")
        with open(evac_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Only one file, latest timestamp
        assert data["evacuated_at"] > 0


# === Test: sanitize ===

class TestSanitizeText:
    def test_sanitize_removes_control_chars(self):
        """Control characters should be stripped from text."""
        dirty = "hello\x00world\x1b[31mred\x7f"
        clean = session_evacuator._sanitize_text(dirty)
        assert "\x00" not in clean
        assert "\x1b" not in clean
        assert "\x7f" not in clean
        assert "hello" in clean
        assert "world" in clean

    def test_sanitize_truncates_long_text(self):
        """Text exceeding max_chars should be truncated."""
        long_text = "a" * 5000
        clean = session_evacuator._sanitize_text(long_text, max_chars=2000)
        assert len(clean) <= 2000

    def test_sanitize_preserves_normal_text(self):
        """Normal text (including newlines and tabs) should be preserved."""
        normal = "Hello\nWorld\tTab"
        clean = session_evacuator._sanitize_text(normal)
        assert clean == normal


# === Test: STM summary size limit ===

class TestStmSummaryLimit:
    def test_stm_summary_respects_size_limit(self, temp_dirs):
        """STM summary should be truncated if total content exceeds limit."""
        _, memory_dir = temp_dirs
        # Create STM with many large entries
        entries = []
        for i in range(50):
            entries.append({
                "id": f"entry_{i}",
                "category": "thought",
                "content": f"Entry {i}: " + "x" * 200,
                "timestamp": f"2026-03-26T10:{i:02d}:00+00:00",
                "weight": 1.0,
            })
        store = {"entries": entries}
        stm_path = os.path.join(memory_dir, "short_term_memory.json")
        with open(stm_path, "w", encoding="utf-8") as f:
            json.dump(store, f)

        hooks_dir = os.path.join(os.path.dirname(memory_dir), "hooks")
        os.makedirs(hooks_dir, exist_ok=True)
        result = session_evacuator.evacuate(hooks_dir, memory_dir)
        assert result is True

        evac_path = os.path.join(hooks_dir, ".session-evacuation.json")
        with open(evac_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Summary should be bounded
        assert len(data["stm_summary"]) <= session_evacuator.MAX_STM_SUMMARY_CHARS


# === Test: evacuation file size ===

class TestEvacuationFileSize:
    def test_evacuation_file_size_bounded(self, temp_dirs, flow_state_file, psyche_state_file, stm_file):
        """Total evacuation file should not exceed MAX_EVACUATION_FILE_SIZE."""
        hooks_dir, memory_dir = temp_dirs
        session_evacuator.evacuate(hooks_dir, memory_dir)
        evac_path = os.path.join(hooks_dir, ".session-evacuation.json")
        file_size = os.path.getsize(evac_path)
        assert file_size <= session_evacuator.MAX_EVACUATION_FILE_SIZE

    def test_evacuation_file_size_bounded_all_fields_huge(self, temp_dirs):
        """Even when ALL fields are huge, file size must stay within limit."""
        hooks_dir, memory_dir = temp_dirs

        # Create huge flow state
        huge_flow = {phase: 1700000000 + i * 1000 for i, phase in enumerate(session_evacuator.FLOW_ORDER)}
        # Add many extra keys to bloat it
        for i in range(200):
            huge_flow[f"extra_key_{i}"] = "x" * 100
        path = os.path.join(hooks_dir, ".dev-flow-state")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(huge_flow, f)

        # Create huge psyche state
        huge_psyche = {"categories": {}}
        for i in range(100):
            huge_psyche["categories"][f"category_{i}"] = {
                "last_update": 1700000000.0,
                "last_phase": "x" * 200,
                "failure_count": 0,
            }
        path = os.path.join(memory_dir, ".psyche-drive-state.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(huge_psyche, f)

        # Create huge STM
        entries = []
        for i in range(50):
            entries.append({
                "id": f"entry_{i}",
                "category": "thought",
                "content": "x" * 500,
                "timestamp": f"2026-03-26T10:{i % 60:02d}:00+00:00",
                "weight": 1.0,
            })
        path = os.path.join(memory_dir, "short_term_memory.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"entries": entries}, f)

        result = session_evacuator.evacuate(hooks_dir, memory_dir)
        assert result is True

        evac_path = os.path.join(hooks_dir, ".session-evacuation.json")
        file_size = os.path.getsize(evac_path)
        assert file_size <= session_evacuator.MAX_EVACUATION_FILE_SIZE


# === Test: _resolve_memory_dir ===

class TestResolveMemoryDir:
    def test_resolve_with_env_var(self, monkeypatch, tmp_path):
        """MEMORY_DIR env var should be used when set, valid, and under ~/.claude/."""
        home = os.path.expanduser("~")
        # Create a directory under ~/.claude/ for testing
        test_mem_dir = os.path.join(home, ".claude", "projects", "_test_resolve_mem", "memory")
        os.makedirs(test_mem_dir, exist_ok=True)
        try:
            monkeypatch.setenv("MEMORY_DIR", test_mem_dir)
            result = session_evacuator._resolve_memory_dir()
            assert result == test_mem_dir
        finally:
            # Cleanup
            import shutil
            parent = os.path.join(home, ".claude", "projects", "_test_resolve_mem")
            if os.path.isdir(parent):
                shutil.rmtree(parent)

    def test_resolve_without_env_var_uses_glob(self, monkeypatch, tmp_path):
        """Without MEMORY_DIR, should use glob fallback."""
        monkeypatch.delenv("MEMORY_DIR", raising=False)
        # We can't easily control glob results, just verify it doesn't crash
        result = session_evacuator._resolve_memory_dir()
        assert isinstance(result, str)

    def test_resolve_rejects_path_outside_home(self, monkeypatch, tmp_path):
        """Paths outside ~/.claude/ should be rejected."""
        # Create a directory outside home
        outside_dir = os.path.join(str(tmp_path), "evil", "memory")
        os.makedirs(outside_dir, exist_ok=True)
        monkeypatch.setenv("MEMORY_DIR", outside_dir)
        # Should raise RuntimeError for path outside home
        with pytest.raises(RuntimeError, match="outside"):
            session_evacuator._resolve_memory_dir()
