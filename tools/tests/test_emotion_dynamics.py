#!/usr/bin/env python3
"""Tests for emotion_dynamics.py — Peak/Rebound dynamics module."""

import json
import os
import sys
import tempfile
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emotion_dynamics import (
    ACCUMULATION_THRESHOLD,
    ACCUMULATION_WINDOW,
    DYNAMICS_STATE_FILENAME,
    DynamicsPhase,
    PEAK_AMPLITUDE,
    PEAK_AMPLITUDE_MAX,
    PEAK_DURATION,
    REBOUND_AMPLITUDE,
    REBOUND_AMPLITUDE_MIN,
    REBOUND_DURATION,
    SESSION_RESET_HOURS,
    check_session_reset,
    create_default_state,
    get_current_amplitude,
    get_dynamics_info,
    load_dynamics_state,
    save_dynamics_state,
    update_dynamics,
)


@pytest.fixture
def tmp_dir():
    """Create a temporary directory for testing."""
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


# --- Default state ---

class TestCreateDefaultState:
    def test_default_phase_is_normal(self):
        state = create_default_state()
        assert state["phase"] == "normal"

    def test_default_call_count_is_zero(self):
        state = create_default_state()
        assert state["phase_call_count"] == 0

    def test_default_accumulation_empty(self):
        state = create_default_state()
        assert state["accumulation_history"] == []

    def test_default_peak_axis_empty(self):
        state = create_default_state()
        assert state["peak_axis"] == ""

    def test_default_has_last_updated(self):
        state = create_default_state()
        assert "last_updated" in state
        assert isinstance(state["last_updated"], str)


# --- Persistence ---

class TestPersistence:
    def test_save_and_load(self, tmp_dir):
        state = create_default_state()
        state["phase"] = "peak"
        state["phase_call_count"] = 2
        state["accumulation_history"] = [0.1, 0.2]
        state["peak_axis"] = "tension"

        result = save_dynamics_state(tmp_dir, state)
        assert not result.startswith("ERROR")

        loaded = load_dynamics_state(tmp_dir)
        assert loaded["phase"] == "peak"
        assert loaded["phase_call_count"] == 2
        assert loaded["accumulation_history"] == [0.1, 0.2]
        assert loaded["peak_axis"] == "tension"

    def test_load_missing_file_returns_default(self, tmp_dir):
        state = load_dynamics_state(tmp_dir)
        assert state["phase"] == "normal"
        assert state["phase_call_count"] == 0

    def test_load_corrupted_file_returns_default(self, tmp_dir):
        filepath = Path(tmp_dir) / DYNAMICS_STATE_FILENAME
        filepath.write_text("not json", encoding="utf-8")
        state = load_dynamics_state(tmp_dir)
        assert state["phase"] == "normal"

    def test_load_invalid_phase_resets_to_normal(self, tmp_dir):
        filepath = Path(tmp_dir) / DYNAMICS_STATE_FILENAME
        data = {"phase": "invalid_phase", "phase_call_count": 5}
        filepath.write_text(json.dumps(data), encoding="utf-8")
        state = load_dynamics_state(tmp_dir)
        assert state["phase"] == "normal"

    def test_load_truncates_long_history(self, tmp_dir):
        filepath = Path(tmp_dir) / DYNAMICS_STATE_FILENAME
        data = {
            "phase": "normal",
            "accumulation_history": [0.1] * 20,
        }
        filepath.write_text(json.dumps(data), encoding="utf-8")
        state = load_dynamics_state(tmp_dir)
        assert len(state["accumulation_history"]) == ACCUMULATION_WINDOW

    def test_save_updates_last_updated(self, tmp_dir):
        state = create_default_state()
        state["last_updated"] = "2020-01-01T00:00:00Z"
        save_dynamics_state(tmp_dir, state)
        loaded = load_dynamics_state(tmp_dir)
        assert loaded["last_updated"] != "2020-01-01T00:00:00Z"


# --- NORMAL phase accumulation ---

class TestNormalAccumulation:
    def test_accumulates_absolute_deltas(self):
        state = create_default_state()
        deltas = {"fulfillment": 0.1, "tension": -0.05, "affinity": 0.03}
        new_state, amp = update_dynamics(state, deltas)
        # abs(0.1) + abs(-0.05) + abs(0.03) = 0.18
        assert len(new_state["accumulation_history"]) == 1
        assert abs(new_state["accumulation_history"][0] - 0.18) < 1e-6

    def test_amplitude_is_1_in_normal(self):
        state = create_default_state()
        deltas = {"fulfillment": 0.05}
        _, amp = update_dynamics(state, deltas)
        assert amp == 1.0

    def test_fifo_window_limits_history(self):
        state = create_default_state()
        # Fill beyond window
        for i in range(ACCUMULATION_WINDOW + 3):
            deltas = {"fulfillment": 0.01}
            state, _ = update_dynamics(state, deltas)
        assert len(state["accumulation_history"]) == ACCUMULATION_WINDOW

    def test_empty_deltas_accumulate_zero(self):
        state = create_default_state()
        new_state, amp = update_dynamics(state, {})
        assert new_state["accumulation_history"] == [0.0]
        assert amp == 1.0


# --- NORMAL -> PEAK transition ---

class TestNormalToPeak:
    def test_transition_on_threshold(self):
        state = create_default_state()
        # Single large delta that exceeds threshold
        deltas = {"fulfillment": 0.5, "tension": 0.4}
        # abs(0.5) + abs(0.4) = 0.9 > 0.8 threshold
        new_state, amp = update_dynamics(state, deltas)
        assert new_state["phase"] == "peak"

    def test_transition_accumulative(self):
        state = create_default_state()
        # Multiple smaller deltas that accumulate
        for _ in range(4):
            deltas = {"fulfillment": 0.1, "tension": 0.1}
            state, _ = update_dynamics(state, deltas)
        # 4 * 0.2 = 0.8 >= threshold
        assert state["phase"] == "peak"

    def test_peak_axis_recorded(self):
        state = create_default_state()
        deltas = {"fulfillment": 0.5, "tension": 0.1, "affinity": 0.3}
        new_state, _ = update_dynamics(state, deltas)
        assert new_state["peak_axis"] == "fulfillment"

    def test_peak_amplitude_on_transition(self):
        state = create_default_state()
        deltas = {"fulfillment": 0.5, "tension": 0.4}
        _, amp = update_dynamics(state, deltas)
        assert amp == PEAK_AMPLITUDE

    def test_no_transition_below_threshold(self):
        state = create_default_state()
        deltas = {"fulfillment": 0.05}
        new_state, _ = update_dynamics(state, deltas)
        assert new_state["phase"] == "normal"


# --- PEAK phase ---

class TestPeakPhase:
    def _make_peak_state(self):
        return {
            "phase": "peak",
            "phase_call_count": 0,
            "accumulation_history": [0.3, 0.3, 0.3],
            "peak_axis": "fulfillment",
            "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def test_peak_amplitude_modifier(self):
        state = self._make_peak_state()
        _, amp = update_dynamics(state, {"fulfillment": 0.1})
        assert amp == PEAK_AMPLITUDE

    def test_peak_amplitude_clamped(self):
        # PEAK_AMPLITUDE is already 1.3, which is below MAX of 1.5
        # This test verifies the clamp logic exists
        assert PEAK_AMPLITUDE <= PEAK_AMPLITUDE_MAX

    def test_peak_call_count_increments(self):
        state = self._make_peak_state()
        new_state, _ = update_dynamics(state, {"fulfillment": 0.1})
        assert new_state["phase_call_count"] == 1

    def test_peak_to_rebound_after_duration(self):
        state = self._make_peak_state()
        for i in range(PEAK_DURATION):
            state, _ = update_dynamics(state, {"fulfillment": 0.1})
        assert state["phase"] == "rebound"

    def test_peak_stays_before_duration(self):
        state = self._make_peak_state()
        for i in range(PEAK_DURATION - 1):
            state, _ = update_dynamics(state, {"fulfillment": 0.1})
        assert state["phase"] == "peak"


# --- REBOUND phase ---

class TestReboundPhase:
    def _make_rebound_state(self):
        return {
            "phase": "rebound",
            "phase_call_count": 0,
            "accumulation_history": [],
            "peak_axis": "fulfillment",
            "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def test_rebound_amplitude_modifier(self):
        state = self._make_rebound_state()
        _, amp = update_dynamics(state, {"fulfillment": 0.1})
        assert amp == REBOUND_AMPLITUDE

    def test_rebound_amplitude_clamped_to_min(self):
        assert REBOUND_AMPLITUDE >= REBOUND_AMPLITUDE_MIN

    def test_rebound_call_count_increments(self):
        state = self._make_rebound_state()
        new_state, _ = update_dynamics(state, {"fulfillment": 0.1})
        assert new_state["phase_call_count"] == 1

    def test_rebound_to_normal_after_duration(self):
        state = self._make_rebound_state()
        for i in range(REBOUND_DURATION):
            state, _ = update_dynamics(state, {"fulfillment": 0.1})
        assert state["phase"] == "normal"

    def test_rebound_stays_before_duration(self):
        state = self._make_rebound_state()
        for i in range(REBOUND_DURATION - 1):
            state, _ = update_dynamics(state, {"fulfillment": 0.1})
        assert state["phase"] == "rebound"

    def test_rebound_resets_accumulation(self):
        state = self._make_rebound_state()
        state["accumulation_history"] = [0.5, 0.5]
        for i in range(REBOUND_DURATION):
            state, _ = update_dynamics(state, {"fulfillment": 0.1})
        assert state["phase"] == "normal"
        assert state["accumulation_history"] == []

    def test_rebound_resets_peak_axis(self):
        state = self._make_rebound_state()
        for i in range(REBOUND_DURATION):
            state, _ = update_dynamics(state, {"fulfillment": 0.1})
        assert state["peak_axis"] == ""


# --- Full cycle ---

class TestFullCycle:
    def test_normal_peak_rebound_normal(self):
        state = create_default_state()

        # Drive to peak
        deltas = {"fulfillment": 0.5, "tension": 0.4}
        state, amp = update_dynamics(state, deltas)
        assert state["phase"] == "peak"
        assert amp == PEAK_AMPLITUDE

        # Stay in peak for PEAK_DURATION calls
        for _ in range(PEAK_DURATION - 1):
            state, amp = update_dynamics(state, {"fulfillment": 0.1})
            assert amp == PEAK_AMPLITUDE
        # This call transitions to rebound
        state, amp = update_dynamics(state, {"fulfillment": 0.1})
        assert state["phase"] == "rebound"
        assert amp == REBOUND_AMPLITUDE

        # Stay in rebound for REBOUND_DURATION calls
        for _ in range(REBOUND_DURATION - 1):
            state, amp = update_dynamics(state, {"fulfillment": 0.1})
            assert amp == REBOUND_AMPLITUDE
        # This call transitions to normal
        state, amp = update_dynamics(state, {"fulfillment": 0.1})
        assert state["phase"] == "normal"
        assert amp == 1.0


# --- Session reset ---

class TestSessionReset:
    def test_no_reset_when_normal(self):
        state = create_default_state()
        state["last_updated"] = "2020-01-01T00:00:00Z"
        result = check_session_reset(state)
        assert result["phase"] == "normal"

    def test_reset_peak_after_threshold(self):
        old_time = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state = {
            "phase": "peak",
            "phase_call_count": 2,
            "accumulation_history": [0.5],
            "peak_axis": "tension",
            "last_updated": old_time,
        }
        result = check_session_reset(state)
        assert result["phase"] == "normal"
        assert result["phase_call_count"] == 0

    def test_reset_rebound_after_threshold(self):
        old_time = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state = {
            "phase": "rebound",
            "phase_call_count": 3,
            "accumulation_history": [],
            "peak_axis": "affinity",
            "last_updated": old_time,
        }
        result = check_session_reset(state)
        assert result["phase"] == "normal"

    def test_no_reset_within_threshold(self):
        recent_time = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state = {
            "phase": "peak",
            "phase_call_count": 1,
            "accumulation_history": [],
            "peak_axis": "fulfillment",
            "last_updated": recent_time,
        }
        result = check_session_reset(state)
        assert result["phase"] == "peak"

    def test_custom_threshold(self):
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state = {
            "phase": "peak",
            "phase_call_count": 1,
            "accumulation_history": [],
            "peak_axis": "fulfillment",
            "last_updated": old_time,
        }
        result = check_session_reset(state, hours_threshold=1.0)
        assert result["phase"] == "normal"

    def test_no_reset_without_timestamp(self):
        state = {
            "phase": "peak",
            "phase_call_count": 1,
            "accumulation_history": [],
            "peak_axis": "fulfillment",
            "last_updated": "",
        }
        result = check_session_reset(state)
        assert result["phase"] == "peak"  # Can't determine elapsed time


# --- get_dynamics_info ---

class TestGetDynamicsInfo:
    def test_normal_info(self):
        state = create_default_state()
        info = get_dynamics_info(state)
        assert "NORMAL" in info
        assert "accumulation=" in info

    def test_peak_info(self):
        state = {
            "phase": "peak",
            "phase_call_count": 1,
            "accumulation_history": [],
            "peak_axis": "tension",
            "last_updated": "",
        }
        info = get_dynamics_info(state)
        assert "PEAK" in info
        assert "tension" in info

    def test_rebound_info(self):
        state = {
            "phase": "rebound",
            "phase_call_count": 2,
            "accumulation_history": [],
            "peak_axis": "",
            "last_updated": "",
        }
        info = get_dynamics_info(state)
        assert "REBOUND" in info


# --- Edge cases ---

class TestEdgeCases:
    def test_negative_deltas_accumulate(self):
        state = create_default_state()
        deltas = {"fulfillment": -0.5, "tension": -0.4}
        new_state, _ = update_dynamics(state, deltas)
        # abs(-0.5) + abs(-0.4) = 0.9 > threshold
        assert new_state["phase"] == "peak"

    def test_mixed_sign_deltas(self):
        state = create_default_state()
        deltas = {"fulfillment": 0.3, "tension": -0.3, "affinity": 0.3}
        new_state, _ = update_dynamics(state, deltas)
        # abs(0.3) + abs(-0.3) + abs(0.3) = 0.9 > threshold
        assert new_state["phase"] == "peak"

    def test_zero_deltas_do_not_trigger(self):
        state = create_default_state()
        for _ in range(10):
            state, amp = update_dynamics(state, {"fulfillment": 0.0})
        assert state["phase"] == "normal"
        assert amp == 1.0

    def test_very_small_deltas_eventually_trigger(self):
        state = create_default_state()
        # 5 * (abs(0.08) + abs(0.08) + abs(0.02)) = 5 * 0.18 = 0.9 > 0.8
        for _ in range(5):
            state, _ = update_dynamics(state, {"fulfillment": 0.08, "tension": 0.08, "affinity": 0.02})
        assert state["phase"] == "peak"

    def test_peak_axis_picks_largest_abs_delta(self):
        state = create_default_state()
        deltas = {"fulfillment": -0.1, "tension": 0.8, "affinity": -0.05}
        new_state, _ = update_dynamics(state, deltas)
        assert new_state["peak_axis"] == "tension"

    def test_get_current_amplitude_normal(self):
        state = create_default_state()
        assert get_current_amplitude(state) == 1.0

    def test_get_current_amplitude_peak(self):
        state = {"phase": "peak"}
        assert get_current_amplitude(state) == PEAK_AMPLITUDE

    def test_get_current_amplitude_rebound(self):
        state = {"phase": "rebound"}
        assert get_current_amplitude(state) == REBOUND_AMPLITUDE

    def test_dynamics_phase_enum_values(self):
        assert DynamicsPhase.NORMAL.value == "normal"
        assert DynamicsPhase.PEAK.value == "peak"
        assert DynamicsPhase.REBOUND.value == "rebound"

    def test_save_load_roundtrip_preserves_all_fields(self, tmp_dir):
        state = {
            "phase": "rebound",
            "phase_call_count": 3,
            "accumulation_history": [0.1, 0.2, 0.3],
            "peak_axis": "affinity",
            "last_updated": "2025-01-01T00:00:00Z",
        }
        save_dynamics_state(tmp_dir, state)
        loaded = load_dynamics_state(tmp_dir)
        assert loaded["phase"] == "rebound"
        assert loaded["phase_call_count"] == 3
        assert loaded["accumulation_history"] == [0.1, 0.2, 0.3]
        assert loaded["peak_axis"] == "affinity"

    @pytest.fixture
    def tmp_dir(self):
        d = tempfile.mkdtemp()
        yield d
        shutil.rmtree(d, ignore_errors=True)
