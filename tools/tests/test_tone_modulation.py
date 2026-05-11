#!/usr/bin/env python3
"""Tests for tone_modulation.py."""

import json
import os
import sys
import tempfile
import shutil

import pytest

# Add tools directory to path
TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from tone_modulation import (
    compute_tone,
    _normalize,
    _equal_weights_result,
    _generate_description,
    TONES,
    BASE_WEIGHTS,
    TONE_DESCRIPTIONS,
    MIN_WEIGHT,
)


@pytest.fixture
def temp_memory_dir():
    """Create a temporary memory directory."""
    d = tempfile.mkdtemp(prefix="tone_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _write_emotion_state(memory_dir, fulfillment=0.0, tension=0.0, affinity=0.0):
    """Write an emotion state file."""
    state = {
        "fulfillment": fulfillment,
        "tension": tension,
        "affinity": affinity,
        "last_updated": "2026-03-13T00:00:00Z",
        "created_at": "2026-03-13T00:00:00Z",
    }
    path = os.path.join(memory_dir, "emotion_state.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f)


def _write_dynamics_state(memory_dir, phase="normal", phase_call_count=0):
    """Write a dynamics state file."""
    state = {
        "phase": phase,
        "phase_call_count": phase_call_count,
        "accumulation_history": [],
        "peak_axis": "",
        "last_updated": "2026-03-13T00:00:00Z",
    }
    path = os.path.join(memory_dir, "dynamics_state.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f)


# --- Structure tests ---


class TestOutputStructure:
    def test_returns_dict(self, temp_memory_dir):
        _write_emotion_state(temp_memory_dir)
        result = compute_tone(temp_memory_dir)
        assert isinstance(result, dict)

    def test_has_required_keys(self, temp_memory_dir):
        _write_emotion_state(temp_memory_dir)
        result = compute_tone(temp_memory_dir)
        assert "primary_tone" in result
        assert "tone_weights" in result
        assert "description" in result

    def test_primary_tone_is_valid(self, temp_memory_dir):
        _write_emotion_state(temp_memory_dir)
        result = compute_tone(temp_memory_dir)
        assert result["primary_tone"] in TONES

    def test_tone_weights_has_all_tones(self, temp_memory_dir):
        _write_emotion_state(temp_memory_dir)
        result = compute_tone(temp_memory_dir)
        for tone in TONES:
            assert tone in result["tone_weights"]

    def test_weights_sum_to_one(self, temp_memory_dir):
        _write_emotion_state(temp_memory_dir)
        result = compute_tone(temp_memory_dir)
        total = sum(result["tone_weights"].values())
        assert abs(total - 1.0) < 0.01

    def test_weights_non_negative(self, temp_memory_dir):
        _write_emotion_state(temp_memory_dir)
        result = compute_tone(temp_memory_dir)
        for tone, weight in result["tone_weights"].items():
            assert weight >= 0.0, f"{tone} weight is negative: {weight}"

    def test_description_is_string(self, temp_memory_dir):
        _write_emotion_state(temp_memory_dir)
        result = compute_tone(temp_memory_dir)
        assert isinstance(result["description"], str)
        assert len(result["description"]) > 0


# --- Neutral state tests ---


class TestNeutralState:
    def test_neutral_state_returns_neutral_dominant(self, temp_memory_dir):
        _write_emotion_state(temp_memory_dir, 0.0, 0.0, 0.0)
        result = compute_tone(temp_memory_dir)
        # Neutral has highest base weight so should be primary
        assert result["primary_tone"] == "neutral"

    def test_neutral_state_neutral_has_highest_weight(self, temp_memory_dir):
        _write_emotion_state(temp_memory_dir, 0.0, 0.0, 0.0)
        result = compute_tone(temp_memory_dir)
        weights = result["tone_weights"]
        assert weights["neutral"] >= weights["light"]
        assert weights["neutral"] >= weights["serious"]


# --- Positive emotion tests ---


class TestPositiveEmotion:
    def test_high_fulfillment_low_tension_favors_light(self, temp_memory_dir):
        _write_emotion_state(temp_memory_dir, fulfillment=0.7, tension=-0.3, affinity=0.5)
        result = compute_tone(temp_memory_dir)
        weights = result["tone_weights"]
        assert weights["light"] > weights["serious"]
        assert weights["light"] > weights["reserved"]

    def test_high_fulfillment_high_affinity_favors_warm(self, temp_memory_dir):
        _write_emotion_state(temp_memory_dir, fulfillment=0.6, tension=0.0, affinity=0.7)
        result = compute_tone(temp_memory_dir)
        weights = result["tone_weights"]
        assert weights["warm"] > weights["serious"]
        assert weights["warm"] > weights["reserved"]

    def test_high_affinity_boosts_warm(self, temp_memory_dir):
        _write_emotion_state(temp_memory_dir, fulfillment=0.0, tension=0.0, affinity=0.8)
        result = compute_tone(temp_memory_dir)
        weights = result["tone_weights"]
        assert weights["warm"] > weights["reserved"]

    def test_very_positive_state_light_or_warm_primary(self, temp_memory_dir):
        _write_emotion_state(temp_memory_dir, fulfillment=0.9, tension=-0.5, affinity=0.9)
        result = compute_tone(temp_memory_dir)
        assert result["primary_tone"] in ("light", "warm")


# --- Negative emotion tests ---


class TestNegativeEmotion:
    def test_low_fulfillment_high_tension_favors_serious(self, temp_memory_dir):
        _write_emotion_state(temp_memory_dir, fulfillment=-0.5, tension=0.6, affinity=0.0)
        result = compute_tone(temp_memory_dir)
        weights = result["tone_weights"]
        assert weights["serious"] > weights["light"]

    def test_high_tension_reduces_light(self, temp_memory_dir):
        _write_emotion_state(temp_memory_dir, fulfillment=0.0, tension=0.8, affinity=0.0)
        _write_dynamics_state(temp_memory_dir)
        result = compute_tone(temp_memory_dir)
        weights = result["tone_weights"]
        # Light should be reduced relative to serious
        assert weights["serious"] > weights["light"]

    def test_negative_affinity_boosts_reserved(self, temp_memory_dir):
        _write_emotion_state(temp_memory_dir, fulfillment=0.0, tension=0.0, affinity=-0.6)
        result = compute_tone(temp_memory_dir)
        weights = result["tone_weights"]
        assert weights["reserved"] > BASE_WEIGHTS["reserved"] / sum(BASE_WEIGHTS.values())

    def test_very_negative_state_serious_or_reserved(self, temp_memory_dir):
        _write_emotion_state(temp_memory_dir, fulfillment=-0.8, tension=0.8, affinity=-0.5)
        result = compute_tone(temp_memory_dir)
        assert result["primary_tone"] in ("serious", "reserved")


# --- Dynamics phase tests ---


class TestDynamicsPhase:
    def test_peak_amplifies_dominant_tone(self, temp_memory_dir):
        _write_emotion_state(temp_memory_dir, fulfillment=0.5, tension=-0.2, affinity=0.3)
        _write_dynamics_state(temp_memory_dir, phase="normal")
        normal_result = compute_tone(temp_memory_dir)

        _write_dynamics_state(temp_memory_dir, phase="peak")
        peak_result = compute_tone(temp_memory_dir)

        # The dominant tone in peak should have higher relative weight
        normal_primary = normal_result["primary_tone"]
        # Peak should maintain or increase the dominant tone's weight
        assert peak_result["tone_weights"][normal_primary] >= normal_result["tone_weights"][normal_primary] * 0.95

    def test_rebound_favors_neutral(self, temp_memory_dir):
        _write_emotion_state(temp_memory_dir, fulfillment=0.5, tension=-0.2, affinity=0.3)
        _write_dynamics_state(temp_memory_dir, phase="rebound")
        result = compute_tone(temp_memory_dir)
        weights = result["tone_weights"]
        # Neutral should be relatively strong during rebound
        assert weights["neutral"] > 0.2

    def test_normal_phase_no_special_effect(self, temp_memory_dir):
        _write_emotion_state(temp_memory_dir, fulfillment=0.3, tension=0.0, affinity=0.3)
        _write_dynamics_state(temp_memory_dir, phase="normal")
        result = compute_tone(temp_memory_dir)
        # Just verify it returns valid result
        assert result["primary_tone"] in TONES


# --- Fallback tests ---


class TestFallback:
    def test_missing_emotion_file_returns_equal(self, temp_memory_dir):
        # No emotion state file exists -> should still work
        result = compute_tone(temp_memory_dir)
        assert result["primary_tone"] in TONES
        total = sum(result["tone_weights"].values())
        assert abs(total - 1.0) < 0.01

    def test_missing_dynamics_file_defaults_normal(self, temp_memory_dir):
        _write_emotion_state(temp_memory_dir, fulfillment=0.5, tension=0.0, affinity=0.0)
        # No dynamics file -> should default to normal phase
        result = compute_tone(temp_memory_dir)
        assert result["primary_tone"] in TONES

    def test_corrupted_emotion_file(self, temp_memory_dir):
        path = os.path.join(temp_memory_dir, "emotion_state.json")
        with open(path, "w") as f:
            f.write("not json{{{")
        result = compute_tone(temp_memory_dir)
        # Should fall back to equal weights
        assert result["primary_tone"] in TONES
        total = sum(result["tone_weights"].values())
        assert abs(total - 1.0) < 0.01

    def test_nonexistent_directory(self):
        result = compute_tone("/nonexistent/path/that/does/not/exist")
        assert result["primary_tone"] in TONES


# --- Normalize tests ---


class TestNormalize:
    def test_normalize_sums_to_one(self):
        weights = {"neutral": 2.0, "light": 1.0, "serious": 1.0, "warm": 1.0, "reserved": 0.5}
        result = _normalize(weights)
        total = sum(result.values())
        assert abs(total - 1.0) < 0.01

    def test_normalize_applies_floor(self):
        weights = {"neutral": 1.0, "light": 0.0, "serious": 0.0, "warm": 0.0, "reserved": 0.0}
        result = _normalize(weights)
        for tone in TONES:
            assert result[tone] > 0.0

    def test_normalize_preserves_relative_order(self):
        weights = {"neutral": 3.0, "light": 2.0, "serious": 1.0, "warm": 0.5, "reserved": 0.1}
        result = _normalize(weights)
        assert result["neutral"] > result["light"]
        assert result["light"] > result["serious"]


# --- Equal weights tests ---


class TestEqualWeights:
    def test_equal_weights_result_structure(self):
        result = _equal_weights_result()
        assert result["primary_tone"] == "neutral"
        assert len(result["tone_weights"]) == 5
        total = sum(result["tone_weights"].values())
        assert abs(total - 1.0) < 0.01

    def test_equal_weights_all_same(self):
        result = _equal_weights_result()
        weights = list(result["tone_weights"].values())
        assert max(weights) - min(weights) < 0.01


# --- Description tests ---


class TestDescription:
    def test_description_contains_tone(self, temp_memory_dir):
        _write_emotion_state(temp_memory_dir, fulfillment=0.5, tension=0.0, affinity=0.5)
        result = compute_tone(temp_memory_dir)
        desc = result["description"]
        assert "推奨トーン" in desc

    def test_high_fulfillment_mentions_fulfillment(self):
        weights = {"neutral": 0.2, "light": 0.3, "serious": 0.1, "warm": 0.3, "reserved": 0.1}
        desc = _generate_description("light", weights, 0.5, 0.0, 0.0, "normal")
        assert "充実感" in desc

    def test_peak_phase_mentioned_in_description(self):
        weights = {"neutral": 0.2, "light": 0.3, "serious": 0.1, "warm": 0.3, "reserved": 0.1}
        desc = _generate_description("light", weights, 0.5, 0.0, 0.0, "peak")
        assert "増幅" in desc

    def test_rebound_phase_mentioned_in_description(self):
        weights = {"neutral": 0.4, "light": 0.15, "serious": 0.15, "warm": 0.15, "reserved": 0.15}
        desc = _generate_description("neutral", weights, 0.0, 0.0, 0.0, "rebound")
        assert "抑制" in desc
