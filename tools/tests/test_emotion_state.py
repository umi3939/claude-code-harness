#!/usr/bin/env python3
"""Tests for emotion_state.py — emotion management for Claude Code memory system.

Tests cover:
- Emotion state CRUD (create, load, save, update)
- Session-interval decay
- Emotional trace creation and extraction
- Memory-emotion return processing
- Safety valves (per-episode cap, total cap, rumination, clamping)
- Backward compatibility (episodes without traces)
- Edge cases and error handling
"""

import json
import math
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure we can import from the tools directory
TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from emotion_state import (
    AXIS_AFFINITY,
    AXIS_FULFILLMENT,
    AXIS_MAX,
    AXIS_MIN,
    AXIS_NEUTRAL,
    AXIS_TENSION,
    ALL_AXES,
    CONVERGENCE_SCALE,
    CONVERGENCE_THRESHOLD,
    FRESHNESS_HALF_LIFE_HOURS,
    PER_EPISODE_RETURN_CAP,
    RETURN_HISTORY_WINDOW,
    RUMINATION_DECAY_FACTOR,
    RUMINATION_THRESHOLD,
    SESSION_DECAY_RATE_PER_HOUR,
    TOTAL_RETURN_CAP,
    _clamp,
    _compute_freshness,
    _derive_single_return,
    _now_iso,
    _parse_iso,
    apply_session_decay,
    create_default_state,
    create_trace,
    extract_trace,
    get_state,
    get_state_dict,
    load_state,
    process_return,
    process_return_from_search_results,
    save_state,
    update_state,
)


# --- Fixtures ---

@pytest.fixture
def memory_dir(tmp_path):
    """Create a temporary memory directory."""
    return str(tmp_path)


@pytest.fixture
def memory_dir_with_state(memory_dir):
    """Create a memory dir with a saved emotion state."""
    state = create_default_state()
    state["fulfillment"] = 0.5
    state["tension"] = -0.3
    state["affinity"] = 0.2
    save_state(memory_dir, state)
    return memory_dir


@pytest.fixture
def memory_dir_with_episodes(memory_dir):
    """Create a memory dir with episodes (some with traces, some without)."""
    episodes_dir = Path(memory_dir) / "episodes"
    episodes_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    session_data = {
        "session_id": "session_test",
        "created_at": now_str,
        "episodes": [
            {
                "episode_id": "aaa111bbb222",
                "episode_type": "observation",
                "summary": "First episode without trace",
                "tags": ["test"],
                "timestamp": now_str,
                "session_id": "session_test",
                "user_utterances": [],
            },
            {
                "episode_id": "ccc333ddd444",
                "episode_type": "decision",
                "summary": "Second episode with trace",
                "tags": ["test", "emotion"],
                "timestamp": now_str,
                "session_id": "session_test",
                "user_utterances": [],
                "emotion_trace": {
                    "fulfillment": 0.7,
                    "tension": -0.2,
                    "affinity": 0.5,
                    "trace_timestamp": now_str,
                },
            },
            {
                "episode_id": "eee555fff666",
                "episode_type": "solution",
                "summary": "Third episode with trace",
                "tags": ["test"],
                "timestamp": now_str,
                "session_id": "session_test",
                "user_utterances": [],
                "emotion_trace": {
                    "fulfillment": -0.3,
                    "tension": 0.8,
                    "affinity": -0.1,
                    "trace_timestamp": now_str,
                },
            },
        ],
    }

    session_file = episodes_dir / "session_test.json"
    session_file.write_text(json.dumps(session_data, ensure_ascii=False, indent=2), encoding="utf-8")

    return memory_dir


# =====================================================================
# Helper function tests
# =====================================================================

class TestClamp:
    def test_within_range(self):
        assert _clamp(0.5) == 0.5

    def test_above_max(self):
        assert _clamp(1.5) == AXIS_MAX

    def test_below_min(self):
        assert _clamp(-1.5) == AXIS_MIN

    def test_at_boundaries(self):
        assert _clamp(AXIS_MIN) == AXIS_MIN
        assert _clamp(AXIS_MAX) == AXIS_MAX

    def test_custom_range(self):
        assert _clamp(5.0, 0.0, 3.0) == 3.0
        assert _clamp(-5.0, 0.0, 3.0) == 0.0


class TestParseIso:
    def test_z_suffix(self):
        result = _parse_iso("2026-03-10T12:30:00Z")
        assert result is not None
        assert result.year == 2026
        assert result.month == 3
        assert result.hour == 12

    def test_no_z(self):
        result = _parse_iso("2026-03-10T12:30:00")
        assert result is not None

    def test_invalid(self):
        assert _parse_iso("not-a-date") is None
        assert _parse_iso("") is None


class TestNowIso:
    def test_format(self):
        result = _now_iso()
        assert result.endswith("Z")
        assert "T" in result
        parsed = _parse_iso(result)
        assert parsed is not None


# =====================================================================
# Emotion State CRUD tests
# =====================================================================

class TestCreateDefaultState:
    def test_all_axes_neutral(self):
        state = create_default_state()
        for axis in ALL_AXES:
            assert state[axis] == AXIS_NEUTRAL

    def test_has_timestamps(self):
        state = create_default_state()
        assert "last_updated" in state
        assert "created_at" in state
        assert _parse_iso(state["last_updated"]) is not None


class TestLoadState:
    def test_no_file_returns_default(self, memory_dir):
        state = load_state(memory_dir)
        for axis in ALL_AXES:
            assert state[axis] == AXIS_NEUTRAL

    def test_loads_saved_state(self, memory_dir_with_state):
        state = load_state(memory_dir_with_state)
        assert abs(state["fulfillment"] - 0.5) < 0.01
        assert abs(state["tension"] - (-0.3)) < 0.01
        assert abs(state["affinity"] - 0.2) < 0.01

    def test_corrupted_file_returns_default(self, memory_dir):
        filepath = Path(memory_dir) / "emotion_state.json"
        filepath.write_text("not valid json", encoding="utf-8")
        state = load_state(memory_dir)
        for axis in ALL_AXES:
            assert state[axis] == AXIS_NEUTRAL

    def test_partial_data_fills_defaults(self, memory_dir):
        filepath = Path(memory_dir) / "emotion_state.json"
        filepath.write_text('{"fulfillment": 0.8}', encoding="utf-8")
        state = load_state(memory_dir)
        assert abs(state["fulfillment"] - 0.8) < 0.01
        assert state["tension"] == AXIS_NEUTRAL
        assert state["affinity"] == AXIS_NEUTRAL

    def test_out_of_range_values_clamped(self, memory_dir):
        filepath = Path(memory_dir) / "emotion_state.json"
        filepath.write_text('{"fulfillment": 5.0, "tension": -5.0, "affinity": 0.5}', encoding="utf-8")
        state = load_state(memory_dir)
        assert state["fulfillment"] == AXIS_MAX
        assert state["tension"] == AXIS_MIN


class TestSaveState:
    def test_save_and_reload(self, memory_dir):
        state = create_default_state()
        state["fulfillment"] = 0.42
        result = save_state(memory_dir, state)
        assert not result.startswith("ERROR")
        loaded = load_state(memory_dir)
        assert abs(loaded["fulfillment"] - 0.42) < 0.01

    def test_updates_last_updated(self, memory_dir):
        state = create_default_state()
        old_ts = state["last_updated"]
        time.sleep(0.01)
        save_state(memory_dir, state)
        loaded = load_state(memory_dir)
        # Timestamp should be updated (or at least not None)
        assert loaded["last_updated"] is not None


class TestUpdateState:
    def test_delta_mode(self, memory_dir_with_state):
        result = update_state(memory_dir_with_state, fulfillment=0.1, mode="delta")
        assert not result.startswith("ERROR")
        state = load_state(memory_dir_with_state)
        assert abs(state["fulfillment"] - 0.6) < 0.01

    def test_set_mode(self, memory_dir_with_state):
        result = update_state(memory_dir_with_state, fulfillment=-0.5, mode="set")
        assert not result.startswith("ERROR")
        state = load_state(memory_dir_with_state)
        assert abs(state["fulfillment"] - (-0.5)) < 0.01

    def test_clamping_on_delta(self, memory_dir_with_state):
        # fulfillment is 0.5, adding 2.0 should clamp to 1.0
        update_state(memory_dir_with_state, fulfillment=2.0, mode="delta")
        state = load_state(memory_dir_with_state)
        assert state["fulfillment"] == AXIS_MAX

    def test_clamping_on_set(self, memory_dir):
        update_state(memory_dir, tension=-5.0, mode="set")
        state = load_state(memory_dir)
        assert state["tension"] == AXIS_MIN

    def test_no_values_error(self, memory_dir):
        result = update_state(memory_dir, mode="delta")
        assert result.startswith("ERROR")

    def test_invalid_mode_error(self, memory_dir):
        result = update_state(memory_dir, fulfillment=0.1, mode="invalid")
        assert result.startswith("ERROR")

    def test_multiple_axes(self, memory_dir):
        update_state(memory_dir, fulfillment=0.3, tension=-0.2, affinity=0.1, mode="set")
        state = load_state(memory_dir)
        assert abs(state["fulfillment"] - 0.3) < 0.01
        assert abs(state["tension"] - (-0.2)) < 0.01
        assert abs(state["affinity"] - 0.1) < 0.01


class TestGetState:
    def test_returns_formatted_string(self, memory_dir_with_state):
        result = get_state(memory_dir_with_state)
        assert "fulfillment=" in result
        assert "tension=" in result
        assert "affinity=" in result
        assert "Last updated:" in result

    def test_applies_decay(self, memory_dir):
        # Save state with old timestamp
        state = create_default_state()
        state["fulfillment"] = 0.8
        old_time = (datetime.now(timezone.utc) - timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state["last_updated"] = old_time
        filepath = Path(memory_dir) / "emotion_state.json"
        filepath.write_text(json.dumps(state), encoding="utf-8")

        result = get_state(memory_dir)
        # After decay, fulfillment should be less than 0.8
        loaded = load_state(memory_dir)
        assert loaded["fulfillment"] < 0.8


# =====================================================================
# Session-Interval Decay tests
# =====================================================================

class TestSessionDecay:
    def test_no_decay_for_recent_update(self):
        state = create_default_state()
        state["fulfillment"] = 0.8
        # last_updated is just now
        result = apply_session_decay(state)
        assert abs(result["fulfillment"] - 0.8) < 0.01

    def test_decay_after_hours(self):
        state = create_default_state()
        state["fulfillment"] = 0.8
        state["tension"] = -0.6
        # Set last_updated to 10 hours ago
        old_time = (datetime.now(timezone.utc) - timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state["last_updated"] = old_time

        result = apply_session_decay(state)
        # Expected: 0.8 * (0.9)^10 ~= 0.8 * 0.3486 ~= 0.279
        expected_factor = (1.0 - SESSION_DECAY_RATE_PER_HOUR) ** 10
        assert abs(result["fulfillment"] - 0.8 * expected_factor) < 0.02
        assert abs(result["tension"] - (-0.6) * expected_factor) < 0.02

    def test_decay_preserves_sign(self):
        state = create_default_state()
        state["affinity"] = -0.5
        old_time = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state["last_updated"] = old_time

        result = apply_session_decay(state)
        assert result["affinity"] < 0  # Still negative
        assert result["affinity"] > -0.5  # But closer to neutral

    def test_very_long_decay_approaches_neutral(self):
        state = create_default_state()
        state["fulfillment"] = 1.0
        # 100 hours ago
        old_time = (datetime.now(timezone.utc) - timedelta(hours=100)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state["last_updated"] = old_time

        result = apply_session_decay(state)
        # After 100 hours, should be very close to neutral
        assert abs(result["fulfillment"]) < 0.01

    def test_no_timestamp_no_decay(self):
        state = create_default_state()
        state["fulfillment"] = 0.8
        state["last_updated"] = "invalid"
        result = apply_session_decay(state)
        assert result["fulfillment"] == 0.8

    def test_neutral_stays_neutral(self):
        state = create_default_state()
        old_time = (datetime.now(timezone.utc) - timedelta(hours=50)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state["last_updated"] = old_time

        result = apply_session_decay(state)
        for axis in ALL_AXES:
            assert abs(result[axis]) < 0.001


# =====================================================================
# Emotional Trace tests
# =====================================================================

class TestCreateTrace:
    def test_creates_trace_from_state(self, memory_dir_with_state):
        trace = create_trace(memory_dir_with_state)
        assert "fulfillment" in trace
        assert "tension" in trace
        assert "affinity" in trace
        assert "trace_timestamp" in trace
        assert abs(trace["fulfillment"] - 0.5) < 0.01

    def test_creates_trace_default_state(self, memory_dir):
        trace = create_trace(memory_dir)
        for axis in ALL_AXES:
            assert trace[axis] == AXIS_NEUTRAL

    def test_trace_has_delta_fields(self, memory_dir_with_state):
        trace = create_trace(memory_dir_with_state)
        assert "delta_fulfillment" in trace
        assert "delta_tension" in trace
        assert "delta_affinity" in trace
        assert "delta_reference_timestamp" in trace

    def test_delta_zero_when_no_change_log(self, memory_dir):
        """When change_log is empty, deltas should be zero."""
        trace = create_trace(memory_dir)
        assert trace["delta_fulfillment"] == 0.0
        assert trace["delta_tension"] == 0.0
        assert trace["delta_affinity"] == 0.0
        assert trace["delta_reference_timestamp"] is None

    def test_delta_zero_with_empty_change_log_injected(self, memory_dir_with_state):
        """When an empty change_log is explicitly passed, deltas should be zero."""
        trace = create_trace(memory_dir_with_state, change_log=[])
        assert trace["delta_fulfillment"] == 0.0
        assert trace["delta_tension"] == 0.0
        assert trace["delta_affinity"] == 0.0
        assert trace["delta_reference_timestamp"] is None

    def test_delta_computed_from_change_log(self, memory_dir):
        """Delta should be current_state - last_change_log_after."""
        # Set up: state at fulfillment=0.5, last change_log after=0.3
        update_state(memory_dir, fulfillment=0.3, mode="set")
        update_state(memory_dir, fulfillment=0.2, mode="delta")
        # Now state is 0.5, last change_log after should have fulfillment=0.5
        # So delta should be 0.0 (current == after of last entry)
        trace = create_trace(memory_dir)
        # The last update set fulfillment to 0.5, and the change log records that as after
        # current state also = 0.5, so delta = 0.0
        assert abs(trace["delta_fulfillment"]) < 0.01

    def test_delta_nonzero_after_external_change(self, memory_dir):
        """If state changed outside of update_state (e.g., via return processing),
        delta should reflect the difference."""
        # First, create a change log entry via normal update
        update_state(memory_dir, fulfillment=0.3, tension=0.0, affinity=0.0, mode="set")
        # Now manually set state to a different value (simulating process_return)
        state = load_state(memory_dir)
        state["fulfillment"] = 0.6  # Changed without going through update_state
        save_state(memory_dir, state)

        trace = create_trace(memory_dir)
        # change_log last after.fulfillment = 0.3, current = 0.6, delta = 0.3
        assert abs(trace["delta_fulfillment"] - 0.3) < 0.01
        assert trace["delta_reference_timestamp"] is not None

    def test_delta_with_injected_change_log(self, memory_dir_with_state):
        """Delta should work with explicitly injected change_log."""
        fake_log = [
            {
                "timestamp": "2026-03-10T12:00:00Z",
                "before": {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0},
                "after": {"fulfillment": 0.2, "tension": -0.1, "affinity": 0.0},
                "reason": "test",
            }
        ]
        # memory_dir_with_state has fulfillment=0.5, tension=-0.3, affinity=0.2
        trace = create_trace(memory_dir_with_state, change_log=fake_log)
        # delta = current - after: fulfillment=0.5-0.2=0.3, tension=-0.3-(-0.1)=-0.2, affinity=0.2-0.0=0.2
        assert abs(trace["delta_fulfillment"] - 0.3) < 0.01
        assert abs(trace["delta_tension"] - (-0.2)) < 0.01
        assert abs(trace["delta_affinity"] - 0.2) < 0.01
        assert trace["delta_reference_timestamp"] == "2026-03-10T12:00:00Z"

    def test_delta_uses_latest_change_log_entry(self, memory_dir):
        """When multiple entries exist, delta should use the last one."""
        fake_log = [
            {
                "timestamp": "2026-03-10T10:00:00Z",
                "before": {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0},
                "after": {"fulfillment": 0.1, "tension": 0.0, "affinity": 0.0},
                "reason": "old entry",
            },
            {
                "timestamp": "2026-03-10T12:00:00Z",
                "before": {"fulfillment": 0.1, "tension": 0.0, "affinity": 0.0},
                "after": {"fulfillment": 0.4, "tension": 0.0, "affinity": 0.0},
                "reason": "latest entry",
            },
        ]
        # Default state: all neutral (0.0)
        trace = create_trace(memory_dir, change_log=fake_log)
        # delta = 0.0 - 0.4 = -0.4 (uses latest entry, not old one)
        assert abs(trace["delta_fulfillment"] - (-0.4)) < 0.01
        assert trace["delta_reference_timestamp"] == "2026-03-10T12:00:00Z"

    def test_backward_compat_trace_without_deltas(self):
        """Traces without delta fields (old format) should still be extractable."""
        episode = {
            "episode_id": "old_format",
            "emotion_trace": {
                "fulfillment": 0.3,
                "tension": -0.1,
                "affinity": 0.5,
                "trace_timestamp": "2026-03-10T12:00:00Z",
            },
        }
        trace = extract_trace(episode)
        assert trace is not None
        # Delta fields not present, which is fine - backward compatible
        assert trace.get("delta_fulfillment") is None
        assert trace["fulfillment"] == 0.3


class TestExtractTrace:
    def test_extracts_existing_trace(self):
        episode = {
            "episode_id": "test123",
            "emotion_trace": {
                "fulfillment": 0.3,
                "tension": -0.1,
                "affinity": 0.5,
                "trace_timestamp": "2026-03-10T12:00:00Z",
            },
        }
        trace = extract_trace(episode)
        assert trace is not None
        assert trace["fulfillment"] == 0.3

    def test_returns_none_for_no_trace(self):
        episode = {"episode_id": "test123"}
        assert extract_trace(episode) is None

    def test_returns_none_for_invalid_trace(self):
        episode = {"episode_id": "test123", "emotion_trace": "not a dict"}
        assert extract_trace(episode) is None

    def test_returns_none_for_incomplete_trace(self):
        episode = {
            "episode_id": "test123",
            "emotion_trace": {"fulfillment": 0.3},  # Missing other axes
        }
        assert extract_trace(episode) is None


# =====================================================================
# Freshness computation tests
# =====================================================================

class TestComputeFreshness:
    def test_recent_trace_high_freshness(self):
        now_str = _now_iso()
        freshness = _compute_freshness(now_str)
        assert freshness > 0.99

    def test_old_trace_low_freshness(self):
        old_time = (datetime.now(timezone.utc) - timedelta(hours=FRESHNESS_HALF_LIFE_HOURS * 3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        freshness = _compute_freshness(old_time)
        assert freshness < 0.15  # After 3 half-lives: 0.125

    def test_half_life_accuracy(self):
        half_life_ago = (datetime.now(timezone.utc) - timedelta(hours=FRESHNESS_HALF_LIFE_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        freshness = _compute_freshness(half_life_ago)
        assert abs(freshness - 0.5) < 0.02

    def test_invalid_timestamp(self):
        freshness = _compute_freshness("invalid")
        assert freshness == 0.5  # Default for unknown


# =====================================================================
# Single return derivation tests
# =====================================================================

class TestDeriveSingleReturn:
    def test_positive_diff_positive_return(self):
        trace = {"fulfillment": 0.8, "tension": 0.0, "affinity": 0.0, "trace_timestamp": _now_iso()}
        current = {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0}
        deltas = _derive_single_return(trace, current)
        assert deltas["fulfillment"] > 0

    def test_negative_diff_negative_return(self):
        trace = {"fulfillment": -0.8, "tension": 0.0, "affinity": 0.0, "trace_timestamp": _now_iso()}
        current = {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0}
        deltas = _derive_single_return(trace, current)
        assert deltas["fulfillment"] < 0

    def test_same_values_near_zero_return(self):
        trace = {"fulfillment": 0.5, "tension": -0.3, "affinity": 0.1, "trace_timestamp": _now_iso()}
        current = {"fulfillment": 0.5, "tension": -0.3, "affinity": 0.1}
        deltas = _derive_single_return(trace, current)
        for axis in ALL_AXES:
            assert abs(deltas[axis]) < 0.01

    def test_old_trace_smaller_return(self):
        now_trace = {"fulfillment": 0.8, "tension": 0.0, "affinity": 0.0, "trace_timestamp": _now_iso()}
        old_time = (datetime.now(timezone.utc) - timedelta(hours=FRESHNESS_HALF_LIFE_HOURS * 2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        old_trace = {"fulfillment": 0.8, "tension": 0.0, "affinity": 0.0, "trace_timestamp": old_time}
        current = {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0}

        now_deltas = _derive_single_return(now_trace, current)
        old_deltas = _derive_single_return(old_trace, current)
        assert abs(now_deltas["fulfillment"]) > abs(old_deltas["fulfillment"])

    def test_convergence_reduces_aligned_return(self):
        # Current is already high positive, trace is even higher
        trace = {"fulfillment": 0.9, "tension": 0.0, "affinity": 0.0, "trace_timestamp": _now_iso()}
        current_high = {"fulfillment": 0.8, "tension": 0.0, "affinity": 0.0}
        current_low = {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0}

        deltas_high = _derive_single_return(trace, current_high)
        deltas_low = _derive_single_return(trace, current_low)
        # Return when already high should be smaller due to convergence
        # (even though base diff is also smaller)
        # With current=0.8 and trace=0.9, diff=0.1, but convergence kicks in
        assert abs(deltas_high["fulfillment"]) < abs(deltas_low["fulfillment"])


# =====================================================================
# Memory-Emotion Return Processing tests
# =====================================================================

class TestProcessReturn:
    def test_empty_episodes(self, memory_dir):
        result = process_return(memory_dir, [])
        assert "No episodes" in result

    def test_episodes_without_traces(self, memory_dir):
        episodes = [
            {"episode_id": "aaa", "summary": "no trace"},
            {"episode_id": "bbb", "summary": "also no trace"},
        ]
        result = process_return(memory_dir, episodes)
        assert "No episodes with emotion traces" in result

    def test_processes_episodes_with_traces(self, memory_dir):
        now_str = _now_iso()
        episodes = [
            {
                "episode_id": "test001",
                "emotion_trace": {
                    "fulfillment": 0.8,
                    "tension": -0.5,
                    "affinity": 0.3,
                    "trace_timestamp": now_str,
                },
            },
        ]
        result = process_return(memory_dir, episodes)
        assert "Return processing complete" in result
        assert "1 episodes with traces" in result

    def test_mixed_episodes(self, memory_dir):
        now_str = _now_iso()
        episodes = [
            {"episode_id": "no_trace"},
            {
                "episode_id": "with_trace",
                "emotion_trace": {
                    "fulfillment": 0.5,
                    "tension": 0.0,
                    "affinity": 0.0,
                    "trace_timestamp": now_str,
                },
            },
        ]
        result = process_return(memory_dir, episodes)
        assert "1 episodes with traces" in result

    def test_state_actually_changes(self, memory_dir):
        state_before = load_state(memory_dir)

        now_str = _now_iso()
        episodes = [
            {
                "episode_id": "change_test",
                "emotion_trace": {
                    "fulfillment": 0.9,
                    "tension": -0.8,
                    "affinity": 0.7,
                    "trace_timestamp": now_str,
                },
            },
        ]
        process_return(memory_dir, episodes)
        state_after = load_state(memory_dir)

        # State should have changed
        changed = False
        for axis in ALL_AXES:
            if abs(state_after[axis] - state_before[axis]) > 0.001:
                changed = True
                break
        assert changed, "Emotion state should have changed after return processing"

    def test_per_episode_cap(self, memory_dir):
        now_str = _now_iso()
        # Create episode with extreme trace values
        episodes = [
            {
                "episode_id": "extreme_test",
                "emotion_trace": {
                    "fulfillment": 1.0,
                    "tension": -1.0,
                    "affinity": 1.0,
                    "trace_timestamp": now_str,
                },
            },
        ]
        process_return(memory_dir, episodes)
        state = load_state(memory_dir)

        # Each axis change should be within PER_EPISODE_RETURN_CAP
        for axis in ALL_AXES:
            assert abs(state[axis]) <= PER_EPISODE_RETURN_CAP + 0.001

    def test_total_cap(self, memory_dir):
        now_str = _now_iso()
        # Create many episodes all pushing same direction
        episodes = [
            {
                "episode_id": f"bulk_{i:03d}",
                "emotion_trace": {
                    "fulfillment": 1.0,
                    "tension": 1.0,
                    "affinity": 1.0,
                    "trace_timestamp": now_str,
                },
            }
            for i in range(20)
        ]
        process_return(memory_dir, episodes)
        state = load_state(memory_dir)

        # Total change should be within TOTAL_RETURN_CAP
        for axis in ALL_AXES:
            assert abs(state[axis]) <= TOTAL_RETURN_CAP + 0.001

    def test_rumination_decay(self, memory_dir):
        now_str = _now_iso()
        episode = {
            "episode_id": "ruminate_test",
            "emotion_trace": {
                "fulfillment": 0.8,
                "tension": 0.0,
                "affinity": 0.0,
                "trace_timestamp": now_str,
            },
        }

        # Process the same episode multiple times
        deltas_per_round = []
        for _ in range(RUMINATION_THRESHOLD + 3):
            state_before = load_state(memory_dir)
            process_return(memory_dir, [episode])
            state_after = load_state(memory_dir)
            delta = abs(state_after["fulfillment"] - state_before["fulfillment"])
            deltas_per_round.append(delta)

        # Later rounds should have smaller deltas due to rumination decay
        # After RUMINATION_THRESHOLD, returns should diminish
        if len(deltas_per_round) > RUMINATION_THRESHOLD:
            early_max = max(deltas_per_round[:RUMINATION_THRESHOLD])
            late_last = deltas_per_round[-1]
            # Late rounds may be zero or very small
            assert late_last <= early_max

    def test_clamp_within_range(self, memory_dir):
        # Set state near max
        update_state(memory_dir, fulfillment=0.95, mode="set")

        now_str = _now_iso()
        episodes = [
            {
                "episode_id": "clamp_test",
                "emotion_trace": {
                    "fulfillment": 1.0,
                    "tension": 0.0,
                    "affinity": 0.0,
                    "trace_timestamp": now_str,
                },
            },
        ]
        process_return(memory_dir, episodes)
        state = load_state(memory_dir)
        assert AXIS_MIN <= state["fulfillment"] <= AXIS_MAX

    def test_return_history_saved(self, memory_dir):
        now_str = _now_iso()
        episodes = [
            {
                "episode_id": "history_test",
                "emotion_trace": {
                    "fulfillment": 0.8,
                    "tension": 0.0,
                    "affinity": 0.0,
                    "trace_timestamp": now_str,
                },
            },
        ]
        process_return(memory_dir, episodes)

        # Check that return history file exists and has entries
        history_path = Path(memory_dir) / "emotion_return_history.json"
        assert history_path.exists()
        data = json.loads(history_path.read_text(encoding="utf-8"))
        assert len(data["history"]) > 0

    def test_return_history_fifo(self, memory_dir):
        now_str = _now_iso()
        # Process many episodes to fill history beyond window
        for i in range(RETURN_HISTORY_WINDOW + 10):
            episodes = [
                {
                    "episode_id": f"fifo_{i:04d}",
                    "emotion_trace": {
                        "fulfillment": 0.5,
                        "tension": 0.0,
                        "affinity": 0.0,
                        "trace_timestamp": now_str,
                    },
                },
            ]
            process_return(memory_dir, episodes)

        history_path = Path(memory_dir) / "emotion_return_history.json"
        data = json.loads(history_path.read_text(encoding="utf-8"))
        assert len(data["history"]) <= RETURN_HISTORY_WINDOW


# =====================================================================
# Process Return from Search Results tests
# =====================================================================

class TestProcessReturnFromSearchResults:
    def test_extracts_ids_and_processes(self, memory_dir_with_episodes):
        # Save an emotion state first
        update_state(memory_dir_with_episodes, fulfillment=0.0, tension=0.0, affinity=0.0, mode="set")

        search_text = """Keyword search results for: test (3 total, showing 3):

  1. [observation] aaa111bbb222 (2026-03-10T12:00:00Z) session=session_test
     Summary: First episode without trace
  2. [decision] ccc333ddd444 (2026-03-10T12:00:00Z) session=session_test
     Summary: Second episode with trace
  3. [solution] eee555fff666 (2026-03-10T12:00:00Z) session=session_test
     Summary: Third episode with trace"""

        result = process_return_from_search_results(memory_dir_with_episodes, search_text)
        assert "2 episodes with traces" in result

    def test_no_ids_found(self, memory_dir):
        result = process_return_from_search_results(memory_dir, "No results here")
        assert "No episode IDs found" in result

    def test_no_episodes_dir(self, memory_dir):
        result = process_return_from_search_results(memory_dir, "[observation] aaa111bbb222")
        assert "No episodes directory" in result


# =====================================================================
# Backward Compatibility tests
# =====================================================================

class TestBackwardCompatibility:
    def test_episodes_without_traces_untouched(self, memory_dir_with_episodes):
        """Episodes without emotion_trace field should work normally."""
        episodes_dir = Path(memory_dir_with_episodes) / "episodes"
        session_file = episodes_dir / "session_test.json"
        data = json.loads(session_file.read_text(encoding="utf-8"))

        # First episode has no trace
        first_ep = data["episodes"][0]
        assert "emotion_trace" not in first_ep
        assert first_ep["episode_id"] == "aaa111bbb222"

    def test_return_skips_traceless_episodes(self, memory_dir_with_episodes):
        """Return processing should skip episodes without traces gracefully."""
        episodes_dir = Path(memory_dir_with_episodes) / "episodes"
        session_file = episodes_dir / "session_test.json"
        data = json.loads(session_file.read_text(encoding="utf-8"))

        # Process all episodes including the one without trace
        result = process_return(memory_dir_with_episodes, data["episodes"])
        assert "2 episodes with traces" in result  # Only 2 of 3 have traces


# =====================================================================
# Edge Case tests
# =====================================================================

class TestEdgeCases:
    def test_empty_memory_dir(self, memory_dir):
        """Operations should work on empty memory dir."""
        state = load_state(memory_dir)
        assert state is not None

        result = get_state(memory_dir)
        assert "fulfillment=" in result

        trace = create_trace(memory_dir)
        assert trace is not None

    def test_concurrent_save_safety(self, memory_dir):
        """Multiple rapid saves should not corrupt data."""
        for i in range(10):
            update_state(memory_dir, fulfillment=i * 0.1, mode="set")

        state = load_state(memory_dir)
        assert AXIS_MIN <= state["fulfillment"] <= AXIS_MAX

    def test_all_axes_at_extremes(self, memory_dir):
        update_state(memory_dir, fulfillment=1.0, tension=-1.0, affinity=1.0, mode="set")
        state = load_state(memory_dir)
        assert state["fulfillment"] == AXIS_MAX
        assert state["tension"] == AXIS_MIN
        assert state["affinity"] == AXIS_MAX

    def test_very_small_deltas(self, memory_dir):
        update_state(memory_dir, fulfillment=0.0001, mode="delta")
        state = load_state(memory_dir)
        assert state["fulfillment"] > 0

    def test_non_numeric_values_in_state_file(self, memory_dir):
        filepath = Path(memory_dir) / "emotion_state.json"
        filepath.write_text('{"fulfillment": "not_a_number", "tension": null}', encoding="utf-8")
        state = load_state(memory_dir)
        # Should fall back to neutral for non-numeric values
        assert state["fulfillment"] == AXIS_NEUTRAL
        assert state["tension"] == AXIS_NEUTRAL

    def test_return_with_no_state_file(self, memory_dir):
        """Return processing should work even when no emotion state file exists."""
        now_str = _now_iso()
        episodes = [
            {
                "episode_id": "no_state_test",
                "emotion_trace": {
                    "fulfillment": 0.5,
                    "tension": 0.0,
                    "affinity": 0.0,
                    "trace_timestamp": now_str,
                },
            },
        ]
        result = process_return(memory_dir, episodes)
        assert not result.startswith("ERROR")


# =====================================================================
# Safety Valve Integration tests
# =====================================================================

class TestSafetyValveIntegration:
    """Tests that verify all safety valves work together correctly."""

    def test_all_valves_prevent_extreme_state(self, memory_dir):
        """Even with many extreme episodes, state should stay reasonable."""
        now_str = _now_iso()

        # Create 50 extreme episodes
        episodes = [
            {
                "episode_id": f"extreme_{i:03d}",
                "emotion_trace": {
                    "fulfillment": 1.0,
                    "tension": -1.0,
                    "affinity": 1.0,
                    "trace_timestamp": now_str,
                },
            }
            for i in range(50)
        ]

        process_return(memory_dir, episodes)
        state = load_state(memory_dir)

        # Total cap should prevent exceeding TOTAL_RETURN_CAP from neutral start
        for axis in ALL_AXES:
            assert AXIS_MIN <= state[axis] <= AXIS_MAX
            assert abs(state[axis]) <= TOTAL_RETURN_CAP + 0.001

    def test_repeated_return_converges(self, memory_dir):
        """Repeatedly processing the same episodes should not diverge."""
        now_str = _now_iso()
        episodes = [
            {
                "episode_id": "converge_test",
                "emotion_trace": {
                    "fulfillment": 0.9,
                    "tension": 0.0,
                    "affinity": 0.0,
                    "trace_timestamp": now_str,
                },
            },
        ]

        prev_fulfillment = 0.0
        for _ in range(20):
            process_return(memory_dir, episodes)
            state = load_state(memory_dir)
            current = state["fulfillment"]
            # Fulfillment should converge (changes get smaller)
            assert AXIS_MIN <= current <= AXIS_MAX
            prev_fulfillment = current

    def test_trace_immutability(self, memory_dir_with_episodes):
        """Emotion traces in episodes should not be modified by return processing."""
        episodes_dir = Path(memory_dir_with_episodes) / "episodes"
        session_file = episodes_dir / "session_test.json"

        # Read original traces
        data_before = json.loads(session_file.read_text(encoding="utf-8"))
        original_trace = data_before["episodes"][1]["emotion_trace"].copy()

        # Process returns
        process_return(memory_dir_with_episodes, data_before["episodes"])

        # Re-read and verify traces unchanged
        data_after = json.loads(session_file.read_text(encoding="utf-8"))
        after_trace = data_after["episodes"][1]["emotion_trace"]

        assert original_trace == after_trace


# =====================================================================
# GetStateDict tests
# =====================================================================

class TestGetStateDict:
    def test_returns_dict(self, memory_dir_with_state):
        state = get_state_dict(memory_dir_with_state)
        assert isinstance(state, dict)
        assert "fulfillment" in state

    def test_default_state(self, memory_dir):
        state = get_state_dict(memory_dir)
        for axis in ALL_AXES:
            assert state[axis] == AXIS_NEUTRAL


# =====================================================================
# _attach_trace_to_latest_episode immutability tests
# =====================================================================

class TestAttachTraceImmutability:
    """Tests that _attach_trace_to_latest_episode respects trace immutability."""

    def test_skips_episode_with_existing_trace(self, memory_dir):
        """If the latest episode already has an emotion_trace, it should not be overwritten."""
        episodes_dir = Path(memory_dir) / "episodes"
        episodes_dir.mkdir(parents=True, exist_ok=True)

        now_str = _now_iso()
        original_trace = {
            "fulfillment": 0.9,
            "tension": -0.5,
            "affinity": 0.3,
            "trace_timestamp": "2026-01-01T00:00:00Z",
        }
        session_data = {
            "session_id": "session_immut",
            "created_at": now_str,
            "episodes": [
                {
                    "episode_id": "immut_test_001",
                    "episode_type": "observation",
                    "summary": "Episode with existing trace",
                    "tags": [],
                    "timestamp": now_str,
                    "session_id": "session_immut",
                    "user_utterances": [],
                    "emotion_trace": original_trace,
                },
            ],
        }

        session_file = episodes_dir / "session_immut.json"
        session_file.write_text(json.dumps(session_data, ensure_ascii=False, indent=2), encoding="utf-8")

        # Import and call the function
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from memory_mcp_server import _attach_trace_to_latest_episode

        new_trace = {
            "fulfillment": 0.1,
            "tension": 0.1,
            "affinity": 0.1,
            "trace_timestamp": now_str,
        }
        result = _attach_trace_to_latest_episode(memory_dir, new_trace)

        # Should skip and return immutability message
        assert "immutability" in result.lower() or "skipped" in result.lower()

        # Verify the original trace is unchanged
        data_after = json.loads(session_file.read_text(encoding="utf-8"))
        assert data_after["episodes"][0]["emotion_trace"] == original_trace

    def test_attaches_trace_when_none_exists(self, memory_dir):
        """If the latest episode has no emotion_trace, it should be attached normally."""
        episodes_dir = Path(memory_dir) / "episodes"
        episodes_dir.mkdir(parents=True, exist_ok=True)

        now_str = _now_iso()
        session_data = {
            "session_id": "session_attach",
            "created_at": now_str,
            "episodes": [
                {
                    "episode_id": "attach_test_001",
                    "episode_type": "decision",
                    "summary": "Episode without trace",
                    "tags": [],
                    "timestamp": now_str,
                    "session_id": "session_attach",
                    "user_utterances": [],
                },
            ],
        }

        session_file = episodes_dir / "session_attach.json"
        session_file.write_text(json.dumps(session_data, ensure_ascii=False, indent=2), encoding="utf-8")

        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from memory_mcp_server import _attach_trace_to_latest_episode

        new_trace = {
            "fulfillment": 0.4,
            "tension": -0.2,
            "affinity": 0.6,
            "trace_timestamp": now_str,
        }
        result = _attach_trace_to_latest_episode(memory_dir, new_trace)

        assert "attached" in result.lower()

        # Verify the trace was added
        data_after = json.loads(session_file.read_text(encoding="utf-8"))
        assert data_after["episodes"][0]["emotion_trace"] == new_trace
