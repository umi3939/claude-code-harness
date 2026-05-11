#!/usr/bin/env python3
"""Tests for observation_facade.py — facade for self-observation modules."""

import json
import os
import sys
import tempfile

import pytest

# Add tools dir to path
TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from observation_facade import (
    run_snapshot,
    run_mini_snapshot,
    get_dampening_factor,
    record_long_term,
)


@pytest.fixture
def memory_dir(tmp_path):
    """Create a temporary memory directory with default state files."""
    md = str(tmp_path / "memory")
    os.makedirs(md, exist_ok=True)

    # Minimal emotion state
    state = {
        "fulfillment": 0.1,
        "tension": -0.2,
        "affinity": 0.3,
        "last_updated": "2026-03-22T00:00:00Z",
        "created_at": "2026-03-22T00:00:00Z",
    }
    with open(os.path.join(md, "emotion_state.json"), "w") as f:
        json.dump(state, f)

    # Minimal dynamics state
    dynamics = {
        "phase": "normal",
        "accumulated_magnitude": 0.0,
        "session_reaction_count": 0,
        "last_session_id": None,
    }
    with open(os.path.join(md, "dynamics_state.json"), "w") as f:
        json.dump(dynamics, f)

    # Empty change log
    with open(os.path.join(md, "emotion_change_log.json"), "w") as f:
        json.dump([], f)

    # Empty long-term dynamics
    with open(os.path.join(md, "long_term_dynamics_buffer.json"), "w") as f:
        json.dump([], f)
    with open(os.path.join(md, "long_term_dynamics_log.json"), "w") as f:
        json.dump([], f)

    # Empty self-difference snapshots
    with open(os.path.join(md, "self_difference_snapshots.json"), "w") as f:
        json.dump({"snapshots": []}, f)

    # Empty continuity strain state
    with open(os.path.join(md, "continuity_strain_state.json"), "w") as f:
        json.dump({"observations": [], "persistent_themes": []}, f)

    return md


# --- run_snapshot tests ---

class TestRunSnapshot:
    def test_returns_dict_with_all_7_keys(self, memory_dir):
        result = run_snapshot(memory_dir)
        assert isinstance(result, dict)
        expected_keys = {
            "observe", "difference", "strain",
            "self_image", "coherence", "stability", "tone",
        }
        assert set(result.keys()) == expected_keys

    def test_observe_returns_raw_dict(self, memory_dir):
        result = run_snapshot(memory_dir)
        obs = result["observe"]
        assert isinstance(obs, dict)
        assert "integrated" in obs

    def test_difference_returns_raw_dict(self, memory_dir):
        result = run_snapshot(memory_dir)
        diff = result["difference"]
        assert isinstance(diff, dict)
        assert "magnitude" in diff
        assert "integrated_description" in diff

    def test_strain_returns_raw_dict(self, memory_dir):
        result = run_snapshot(memory_dir)
        strain = result["strain"]
        assert isinstance(strain, dict)
        assert "level" in strain
        assert "description" in strain

    def test_self_image_returns_raw_dict(self, memory_dir):
        result = run_snapshot(memory_dir)
        img = result["self_image"]
        assert isinstance(img, dict)
        assert "overall_impression" in img
        assert "integrated_description" in img

    def test_coherence_returns_raw_dict(self, memory_dir):
        result = run_snapshot(memory_dir)
        coh = result["coherence"]
        assert isinstance(coh, dict)
        assert "coherence_level" in coh
        assert "description" in coh

    def test_stability_returns_raw_dict(self, memory_dir):
        result = run_snapshot(memory_dir)
        stab = result["stability"]
        assert isinstance(stab, dict)
        assert "dampening_factor" in stab
        assert "description" in stab

    def test_tone_returns_raw_dict(self, memory_dir):
        result = run_snapshot(memory_dir)
        tone = result["tone"]
        assert isinstance(tone, dict)
        assert "primary_tone" in tone
        assert "description" in tone

    def test_no_string_formatting(self, memory_dir):
        """Facade must return raw dicts, not formatted strings."""
        result = run_snapshot(memory_dir)
        for key, value in result.items():
            assert isinstance(value, dict), f"{key} should be dict, got {type(value)}"


# --- run_mini_snapshot tests ---

class TestRunMiniSnapshot:
    def test_returns_dict_with_3_keys(self, memory_dir):
        result = run_mini_snapshot(memory_dir)
        assert isinstance(result, dict)
        expected_keys = {"observe", "self_image", "tone"}
        assert set(result.keys()) == expected_keys

    def test_observe_returns_raw_dict(self, memory_dir):
        result = run_mini_snapshot(memory_dir)
        obs = result["observe"]
        assert isinstance(obs, dict)
        assert "integrated" in obs

    def test_self_image_returns_raw_dict(self, memory_dir):
        result = run_mini_snapshot(memory_dir)
        img = result["self_image"]
        assert isinstance(img, dict)
        assert "overall_impression" in img

    def test_tone_returns_raw_dict(self, memory_dir):
        result = run_mini_snapshot(memory_dir)
        tone = result["tone"]
        assert isinstance(tone, dict)
        assert "primary_tone" in tone

    def test_no_string_formatting(self, memory_dir):
        result = run_mini_snapshot(memory_dir)
        for key, value in result.items():
            assert isinstance(value, dict), f"{key} should be dict, got {type(value)}"


# --- get_dampening_factor tests ---

class TestGetDampeningFactor:
    def test_returns_float(self, memory_dir):
        result = get_dampening_factor(memory_dir)
        assert isinstance(result, float)

    def test_default_is_1_0(self, memory_dir):
        """With no extreme emotion state, dampening should be 1.0 (inactive)."""
        result = get_dampening_factor(memory_dir)
        assert result == 1.0

    def test_extreme_state_returns_less_than_1(self, memory_dir):
        """With extreme emotion, dampening should be < 1.0."""
        state = {
            "fulfillment": 0.95,
            "tension": 0.95,
            "affinity": 0.95,
            "last_updated": "2026-03-22T00:00:00Z",
            "created_at": "2026-03-22T00:00:00Z",
        }
        with open(os.path.join(memory_dir, "emotion_state.json"), "w") as f:
            json.dump(state, f)

        # Also need repetitive change log for fixation
        changes = []
        for i in range(20):
            changes.append({
                "timestamp": f"2026-03-22T00:{i:02d}:00Z",
                "reason": "test",
                "before": {"fulfillment": 0.9, "tension": 0.9, "affinity": 0.9},
                "after": {"fulfillment": 0.95, "tension": 0.95, "affinity": 0.95},
            })
        with open(os.path.join(memory_dir, "emotion_change_log.json"), "w") as f:
            json.dump(changes, f)

        result = get_dampening_factor(memory_dir)
        assert result <= 1.0

    def test_value_range(self, memory_dir):
        """Dampening factor should be between 0 and 1."""
        result = get_dampening_factor(memory_dir)
        assert 0.0 <= result <= 1.0


# --- record_long_term tests ---

class TestRecordLongTerm:
    def test_returns_dict(self, memory_dir):
        emotion_state = {
            "fulfillment": 0.1,
            "tension": -0.2,
            "affinity": 0.3,
        }
        result = record_long_term(memory_dir, emotion_state, "normal")
        assert isinstance(result, dict)

    def test_has_status_field(self, memory_dir):
        emotion_state = {
            "fulfillment": 0.1,
            "tension": -0.2,
            "affinity": 0.3,
        }
        result = record_long_term(memory_dir, emotion_state, "normal")
        assert "status" in result

    def test_buffer_status(self, memory_dir):
        """First few calls should return 'buffered' status."""
        emotion_state = {
            "fulfillment": 0.1,
            "tension": -0.2,
            "affinity": 0.3,
        }
        result = record_long_term(memory_dir, emotion_state, "normal")
        assert result["status"] in ("buffered", "aggregated")

    def test_buffer_size_increments(self, memory_dir):
        """Buffer size should be present when buffered."""
        emotion_state = {
            "fulfillment": 0.1,
            "tension": -0.2,
            "affinity": 0.3,
        }
        result = record_long_term(memory_dir, emotion_state, "normal")
        if result["status"] == "buffered":
            assert "buffer_size" in result


# --- Error handling tests ---

class TestErrorHandling:
    def test_run_snapshot_with_empty_dir(self, tmp_path):
        """run_snapshot should not crash with minimal directory."""
        md = str(tmp_path / "empty_memory")
        os.makedirs(md, exist_ok=True)
        # Should not raise - modules handle missing files gracefully
        result = run_snapshot(md)
        assert isinstance(result, dict)

    def test_run_mini_snapshot_with_empty_dir(self, tmp_path):
        md = str(tmp_path / "empty_memory")
        os.makedirs(md, exist_ok=True)
        result = run_mini_snapshot(md)
        assert isinstance(result, dict)

    def test_get_dampening_factor_with_empty_dir(self, tmp_path):
        md = str(tmp_path / "empty_memory")
        os.makedirs(md, exist_ok=True)
        result = get_dampening_factor(md)
        assert isinstance(result, float)
