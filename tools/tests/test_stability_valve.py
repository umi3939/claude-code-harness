#!/usr/bin/env python3
"""Tests for stability_valve.py — Claude Code MCP stability valve."""

import json
import os
import sys
import tempfile
import shutil

import pytest

# Add tools dir to path
TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from stability_valve import (
    check_stability,
    get_dampening_factor,
    _compute_emotion_saturation,
    _compute_change_fixation,
    _compute_dynamics_stagnation,
    SATURATION_THRESHOLD,
    FIXATION_WINDOW,
    DAMPENING_MIN,
    DAMPENING_SCALE,
)
from emotion_state import ALL_AXES


@pytest.fixture
def memory_dir(tmp_path):
    """Create a temporary memory directory with default files."""
    md = str(tmp_path / "memory")
    os.makedirs(md, exist_ok=True)
    return md


def _write_emotion_state(memory_dir, fulfillment=0.0, tension=0.0, affinity=0.0):
    """Write an emotion state file."""
    state = {
        "fulfillment": fulfillment,
        "tension": tension,
        "affinity": affinity,
        "last_updated": "2026-03-13T00:00:00Z",
        "created_at": "2026-03-13T00:00:00Z",
    }
    with open(os.path.join(memory_dir, "emotion_state.json"), "w") as f:
        json.dump(state, f)


def _write_change_log(memory_dir, entries):
    """Write a change log file."""
    with open(os.path.join(memory_dir, "emotion_change_log.json"), "w") as f:
        json.dump({"entries": entries}, f)


def _write_dynamics_state(memory_dir, phase="normal", phase_call_count=0):
    """Write a dynamics state file."""
    state = {
        "phase": phase,
        "phase_call_count": phase_call_count,
        "accumulation_history": [],
        "peak_axis": "",
        "last_updated": "2026-03-13T00:00:00Z",
    }
    with open(os.path.join(memory_dir, "dynamics_state.json"), "w") as f:
        json.dump(state, f)


# ============================================================
# _compute_emotion_saturation tests
# ============================================================


class TestEmotionSaturation:
    def test_neutral_state(self):
        """All axes at neutral -> saturation = 0.0."""
        state = {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0}
        assert _compute_emotion_saturation(state) == 0.0

    def test_below_threshold(self):
        """Axes below threshold -> saturation = 0.0."""
        state = {"fulfillment": 0.5, "tension": -0.7, "affinity": 0.3}
        assert _compute_emotion_saturation(state) == 0.0

    def test_at_threshold(self):
        """Axis exactly at threshold -> saturation = 0.0."""
        state = {"fulfillment": 0.8, "tension": 0.0, "affinity": 0.0}
        # 0.8 is the threshold, so (0.8 - 0.8) / (1.0 - 0.8) = 0.0
        assert _compute_emotion_saturation(state) == 0.0

    def test_above_threshold(self):
        """Axis above threshold -> saturation > 0."""
        state = {"fulfillment": 0.9, "tension": 0.0, "affinity": 0.0}
        result = _compute_emotion_saturation(state)
        assert result == pytest.approx(0.5, abs=0.01)

    def test_max_saturation(self):
        """Axis at 1.0 -> saturation = 1.0."""
        state = {"fulfillment": 1.0, "tension": 0.0, "affinity": 0.0}
        assert _compute_emotion_saturation(state) == 1.0

    def test_negative_saturated(self):
        """Negative axis beyond threshold also detected."""
        state = {"fulfillment": 0.0, "tension": -0.95, "affinity": 0.0}
        result = _compute_emotion_saturation(state)
        assert result == pytest.approx(0.75, abs=0.01)

    def test_multiple_axes_saturated_uses_max(self):
        """When multiple axes saturated, uses the most extreme."""
        state = {"fulfillment": 0.85, "tension": 0.95, "affinity": 0.0}
        result = _compute_emotion_saturation(state)
        expected = (0.95 - 0.8) / (1.0 - 0.8)  # 0.75
        assert result == pytest.approx(expected, abs=0.01)

    def test_empty_state(self):
        """Empty dict -> saturation = 0.0 (missing axes default to neutral)."""
        assert _compute_emotion_saturation({}) == 0.0

    def test_non_numeric_values(self):
        """Non-numeric values are skipped."""
        state = {"fulfillment": "bad", "tension": 0.9, "affinity": None}
        result = _compute_emotion_saturation(state)
        assert result == pytest.approx(0.5, abs=0.01)


# ============================================================
# _compute_change_fixation tests
# ============================================================


class TestChangeFixation:
    def _make_entry(self, axis, delta):
        """Create a change log entry where the given axis changes by delta."""
        before = {a: 0.0 for a in ALL_AXES}
        after = {a: 0.0 for a in ALL_AXES}
        after[axis] = delta
        return {"before": before, "after": after, "timestamp": "2026-03-13T00:00:00Z"}

    def test_empty_log(self):
        """Empty log -> fixation = 0.0."""
        assert _compute_change_fixation([]) == 0.0

    def test_too_few_entries(self):
        """Fewer than FIXATION_WINDOW entries -> fixation = 0.0."""
        entries = [self._make_entry("fulfillment", 0.1)] * (FIXATION_WINDOW - 1)
        assert _compute_change_fixation(entries) == 0.0

    def test_all_same_direction(self):
        """All entries same axis same direction -> fixation = 1.0."""
        entries = [self._make_entry("fulfillment", 0.1)] * FIXATION_WINDOW
        assert _compute_change_fixation(entries) == 1.0

    def test_all_same_negative(self):
        """All entries same axis negative direction -> fixation = 1.0."""
        entries = [self._make_entry("tension", -0.2)] * FIXATION_WINDOW
        assert _compute_change_fixation(entries) == 1.0

    def test_mixed_directions(self):
        """Mixed directions -> fixation = 0.0 (below threshold)."""
        entries = [
            self._make_entry("fulfillment", 0.1),
            self._make_entry("fulfillment", -0.1),
            self._make_entry("fulfillment", 0.1),
            self._make_entry("tension", 0.1),
            self._make_entry("affinity", -0.1),
        ]
        assert _compute_change_fixation(entries) == 0.0

    def test_almost_all_same(self):
        """FIXATION_WINDOW-1 same out of FIXATION_WINDOW -> partial fixation."""
        entries = [self._make_entry("fulfillment", 0.1)] * (FIXATION_WINDOW - 1)
        entries.append(self._make_entry("tension", 0.1))
        result = _compute_change_fixation(entries)
        # 4 out of 5 match -> 4/5 = 0.8
        assert result == pytest.approx(0.8, abs=0.01)

    def test_only_recent_window_matters(self):
        """Only the last FIXATION_WINDOW entries are checked."""
        old = [self._make_entry("tension", -0.1)] * 10
        recent = [self._make_entry("fulfillment", 0.1)] * FIXATION_WINDOW
        entries = old + recent
        result = _compute_change_fixation(entries)
        assert result == 1.0

    def test_zero_delta_entries(self):
        """Entries with no meaningful delta are excluded."""
        entries = [{"before": {"fulfillment": 0.5, "tension": 0.0, "affinity": 0.0},
                    "after": {"fulfillment": 0.5, "tension": 0.0, "affinity": 0.0}}] * FIXATION_WINDOW
        assert _compute_change_fixation(entries) == 0.0


# ============================================================
# _compute_dynamics_stagnation tests
# ============================================================


class TestDynamicsStagnation:
    def test_normal_phase(self):
        """Normal phase -> stagnation = 0.0."""
        state = {"phase": "normal", "phase_call_count": 10}
        assert _compute_dynamics_stagnation(state) == 0.0

    def test_peak_within_duration(self):
        """Peak phase within expected duration -> stagnation = 0.0."""
        state = {"phase": "peak", "phase_call_count": 2}
        assert _compute_dynamics_stagnation(state) == 0.0

    def test_peak_at_duration(self):
        """Peak phase exactly at expected duration -> stagnation = 0.0."""
        state = {"phase": "peak", "phase_call_count": 3}  # PEAK_DURATION = 3
        assert _compute_dynamics_stagnation(state) == 0.0

    def test_peak_exceeded_by_1(self):
        """Peak phase exceeded by 1 call -> stagnation ~0.33."""
        state = {"phase": "peak", "phase_call_count": 4}
        result = _compute_dynamics_stagnation(state)
        assert result == pytest.approx(1.0 / 3.0, abs=0.01)

    def test_peak_exceeded_by_3(self):
        """Peak phase exceeded by 3+ calls -> stagnation = 1.0."""
        state = {"phase": "peak", "phase_call_count": 6}
        assert _compute_dynamics_stagnation(state) == 1.0

    def test_rebound_within_duration(self):
        """Rebound phase within expected duration -> stagnation = 0.0."""
        state = {"phase": "rebound", "phase_call_count": 3}
        assert _compute_dynamics_stagnation(state) == 0.0

    def test_rebound_exceeded(self):
        """Rebound phase exceeded by 2 calls -> stagnation ~0.67."""
        state = {"phase": "rebound", "phase_call_count": 7}  # REBOUND_DURATION = 5
        result = _compute_dynamics_stagnation(state)
        assert result == pytest.approx(2.0 / 3.0, abs=0.01)

    def test_missing_phase(self):
        """Missing phase defaults to normal -> stagnation = 0.0."""
        assert _compute_dynamics_stagnation({}) == 0.0

    def test_non_int_call_count(self):
        """Non-int call count -> stagnation = 0.0."""
        state = {"phase": "peak", "phase_call_count": "bad"}
        assert _compute_dynamics_stagnation(state) == 0.0


# ============================================================
# check_stability integration tests
# ============================================================


class TestCheckStability:
    def test_all_normal(self, memory_dir):
        """All normal state -> no dampening."""
        _write_emotion_state(memory_dir, 0.0, 0.0, 0.0)
        _write_change_log(memory_dir, [])
        _write_dynamics_state(memory_dir, "normal", 0)

        result = check_stability(memory_dir)
        assert result["dampening_factor"] == 1.0
        assert result["is_active"] is False
        assert result["overall_extremity"] == 0.0

    def test_saturated_emotion(self, memory_dir):
        """Saturated emotion -> dampening active."""
        _write_emotion_state(memory_dir, 0.95, 0.0, 0.0)
        _write_change_log(memory_dir, [])
        _write_dynamics_state(memory_dir, "normal", 0)

        result = check_stability(memory_dir)
        assert result["is_active"] is True
        assert result["dampening_factor"] < 1.0
        assert result["indicators"]["emotion_saturation"] > 0.0

    def test_fixated_changes(self, memory_dir):
        """All recent changes same direction -> dampening active."""
        _write_emotion_state(memory_dir, 0.5, 0.0, 0.0)
        entries = []
        for i in range(FIXATION_WINDOW):
            entries.append({
                "timestamp": f"2026-03-13T00:0{i}:00Z",
                "before": {"fulfillment": 0.0 + i * 0.1, "tension": 0.0, "affinity": 0.0},
                "after": {"fulfillment": 0.1 + i * 0.1, "tension": 0.0, "affinity": 0.0},
                "reason": "",
            })
        _write_change_log(memory_dir, entries)
        _write_dynamics_state(memory_dir, "normal", 0)

        result = check_stability(memory_dir)
        assert result["is_active"] is True
        assert result["indicators"]["change_fixation"] > 0.0

    def test_stagnated_dynamics(self, memory_dir):
        """Dynamics phase stagnated -> dampening active."""
        _write_emotion_state(memory_dir, 0.0, 0.0, 0.0)
        _write_change_log(memory_dir, [])
        _write_dynamics_state(memory_dir, "peak", 6)

        result = check_stability(memory_dir)
        assert result["is_active"] is True
        assert result["indicators"]["dynamics_stagnation"] > 0.0

    def test_dampening_minimum(self, memory_dir):
        """Dampening never goes below DAMPENING_MIN."""
        _write_emotion_state(memory_dir, 1.0, 1.0, 1.0)
        _write_change_log(memory_dir, [])
        _write_dynamics_state(memory_dir, "peak", 10)

        result = check_stability(memory_dir)
        assert result["dampening_factor"] >= DAMPENING_MIN

    def test_description_japanese(self, memory_dir):
        """Description is in Japanese."""
        _write_emotion_state(memory_dir, 0.0, 0.0, 0.0)
        _write_change_log(memory_dir, [])
        _write_dynamics_state(memory_dir, "normal", 0)

        result = check_stability(memory_dir)
        assert "安定化バルブ" in result["description"]

    def test_description_active(self, memory_dir):
        """Active description includes detected indicators."""
        _write_emotion_state(memory_dir, 0.95, 0.0, 0.0)
        _write_change_log(memory_dir, [])
        _write_dynamics_state(memory_dir, "normal", 0)

        result = check_stability(memory_dir)
        assert "活性" in result["description"]
        assert "感情飽和" in result["description"]

    def test_no_files_exist(self, memory_dir):
        """No files exist -> defaults -> no dampening."""
        result = check_stability(memory_dir)
        assert result["dampening_factor"] == 1.0
        assert result["is_active"] is False

    def test_indicators_are_clamped(self, memory_dir):
        """All indicator values stay in [0.0, 1.0]."""
        _write_emotion_state(memory_dir, 1.0, -1.0, 1.0)
        _write_change_log(memory_dir, [])
        _write_dynamics_state(memory_dir, "rebound", 100)

        result = check_stability(memory_dir)
        for name, val in result["indicators"].items():
            assert 0.0 <= val <= 1.0, f"{name} = {val} out of range"

    def test_overall_is_max_of_indicators(self, memory_dir):
        """Overall extremity is the max of all indicators."""
        _write_emotion_state(memory_dir, 0.9, 0.0, 0.0)
        _write_change_log(memory_dir, [])
        _write_dynamics_state(memory_dir, "normal", 0)

        result = check_stability(memory_dir)
        indicators = result["indicators"]
        expected_max = max(indicators.values())
        assert result["overall_extremity"] == pytest.approx(expected_max, abs=0.001)


# ============================================================
# get_dampening_factor tests
# ============================================================


class TestGetDampeningFactor:
    def test_normal_returns_1(self, memory_dir):
        """Normal state -> dampening = 1.0."""
        _write_emotion_state(memory_dir, 0.0, 0.0, 0.0)
        _write_change_log(memory_dir, [])
        _write_dynamics_state(memory_dir, "normal", 0)

        assert get_dampening_factor(memory_dir) == 1.0

    def test_saturated_returns_less_than_1(self, memory_dir):
        """Saturated state -> dampening < 1.0."""
        _write_emotion_state(memory_dir, 0.95, 0.0, 0.0)
        _write_change_log(memory_dir, [])
        _write_dynamics_state(memory_dir, "normal", 0)

        result = get_dampening_factor(memory_dir)
        assert result < 1.0
        assert result >= DAMPENING_MIN

    def test_consistent_with_check_stability(self, memory_dir):
        """get_dampening_factor matches check_stability's dampening_factor."""
        _write_emotion_state(memory_dir, 0.85, -0.5, 0.3)
        _write_change_log(memory_dir, [])
        _write_dynamics_state(memory_dir, "normal", 0)

        full = check_stability(memory_dir)
        lightweight = get_dampening_factor(memory_dir)
        assert full["dampening_factor"] == lightweight

    def test_no_files_returns_1(self, memory_dir):
        """No files -> dampening = 1.0."""
        assert get_dampening_factor(memory_dir) == 1.0


# ============================================================
# Dampening formula tests
# ============================================================


class TestDampeningFormula:
    def test_dampening_at_0_extremity(self, memory_dir):
        """0 extremity -> dampening = 1.0."""
        _write_emotion_state(memory_dir, 0.0, 0.0, 0.0)
        _write_change_log(memory_dir, [])
        _write_dynamics_state(memory_dir, "normal", 0)
        assert get_dampening_factor(memory_dir) == 1.0

    def test_dampening_at_half_extremity(self, memory_dir):
        """0.5 extremity -> dampening = 1.0 - 0.5*0.7 = 0.65."""
        # Saturation: (0.9 - 0.8) / (1.0 - 0.8) = 0.5
        _write_emotion_state(memory_dir, 0.9, 0.0, 0.0)
        _write_change_log(memory_dir, [])
        _write_dynamics_state(memory_dir, "normal", 0)

        result = get_dampening_factor(memory_dir)
        assert result == pytest.approx(0.65, abs=0.01)

    def test_dampening_at_full_extremity(self, memory_dir):
        """1.0 extremity -> dampening = max(0.3, 1.0 - 1.0*0.7) = 0.3."""
        _write_emotion_state(memory_dir, 1.0, 0.0, 0.0)
        _write_change_log(memory_dir, [])
        _write_dynamics_state(memory_dir, "normal", 0)

        result = get_dampening_factor(memory_dir)
        assert result == pytest.approx(DAMPENING_MIN, abs=0.01)
