"""Tests for long_term_dynamics.py — long-term emotion dynamics logging."""

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

from long_term_dynamics import (
    AXIS_NAMES,
    BUFFER_FILENAME,
    LOG_FILENAME,
    MAX_ENTRIES,
    WINDOW_SIZE,
    _aggregate_window,
    _compute_mean,
    _compute_variance,
    format_stats,
    get_long_term_stats,
    load_buffer,
    load_log,
    record_observation,
    save_buffer,
    save_log,
)


@pytest.fixture
def tmp_memory_dir():
    """Create a temp directory for memory files."""
    d = tempfile.mkdtemp(prefix="test_ltdyn_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


# --- Helper tests ---


class TestHelpers:
    def test_compute_mean_empty(self):
        assert _compute_mean([]) == 0.0

    def test_compute_mean_single(self):
        assert _compute_mean([5.0]) == 5.0

    def test_compute_mean_multiple(self):
        assert abs(_compute_mean([1.0, 2.0, 3.0]) - 2.0) < 1e-9

    def test_compute_variance_empty(self):
        assert _compute_variance([]) == 0.0

    def test_compute_variance_single(self):
        assert _compute_variance([5.0]) == 0.0

    def test_compute_variance_multiple(self):
        # variance of [1, 2, 3] = ((1-2)^2 + (2-2)^2 + (3-2)^2) / 3 = 2/3
        result = _compute_variance([1.0, 2.0, 3.0])
        assert abs(result - 2.0 / 3.0) < 1e-9


# --- Buffer tests ---


class TestBuffer:
    def test_load_empty_buffer(self, tmp_memory_dir):
        buf = load_buffer(tmp_memory_dir)
        assert buf == []

    def test_save_and_load_buffer(self, tmp_memory_dir):
        obs = [{"fulfillment": 0.5, "tension": -0.1, "affinity": 0.2, "phase": "normal", "timestamp": "2026-01-01T00:00:00Z"}]
        save_buffer(tmp_memory_dir, obs)
        loaded = load_buffer(tmp_memory_dir)
        assert len(loaded) == 1
        assert loaded[0]["fulfillment"] == 0.5

    def test_load_corrupted_buffer(self, tmp_memory_dir):
        path = os.path.join(tmp_memory_dir, BUFFER_FILENAME)
        with open(path, "w") as f:
            f.write("not json")
        assert load_buffer(tmp_memory_dir) == []


# --- Log tests ---


class TestLog:
    def test_load_empty_log(self, tmp_memory_dir):
        log = load_log(tmp_memory_dir)
        assert log == []

    def test_save_and_load_log(self, tmp_memory_dir):
        entries = [{"entry_id": 1, "axis_stats": {}}]
        save_log(tmp_memory_dir, entries)
        loaded = load_log(tmp_memory_dir)
        assert len(loaded) == 1
        assert loaded[0]["entry_id"] == 1

    def test_load_corrupted_log(self, tmp_memory_dir):
        path = os.path.join(tmp_memory_dir, LOG_FILENAME)
        with open(path, "w") as f:
            f.write("{}")  # missing "entries" key
        assert load_log(tmp_memory_dir) == []


# --- Aggregation tests ---


class TestAggregation:
    def test_aggregate_empty(self):
        result = _aggregate_window([])
        assert result["observation_count"] == 0
        assert result["axis_stats"]["fulfillment"]["mean"] == 0.0

    def test_aggregate_single_observation(self):
        obs = [{"fulfillment": 0.5, "tension": -0.3, "affinity": 0.1, "phase": "normal", "timestamp": "2026-01-01T00:00:00Z"}]
        result = _aggregate_window(obs)
        assert result["observation_count"] == 1
        assert result["axis_stats"]["fulfillment"]["mean"] == 0.5
        assert result["axis_stats"]["tension"]["mean"] == -0.3
        assert result["axis_stats"]["fulfillment"]["variance"] == 0.0

    def test_aggregate_multiple_observations(self):
        obs = [
            {"fulfillment": 0.2, "tension": 0.0, "affinity": 0.0, "phase": "normal", "timestamp": "2026-01-01T00:00:00Z"},
            {"fulfillment": 0.4, "tension": 0.0, "affinity": 0.0, "phase": "normal", "timestamp": "2026-01-01T00:01:00Z"},
            {"fulfillment": 0.6, "tension": 0.0, "affinity": 0.0, "phase": "peak", "timestamp": "2026-01-01T00:02:00Z"},
        ]
        result = _aggregate_window(obs)
        assert result["observation_count"] == 3
        assert abs(result["axis_stats"]["fulfillment"]["mean"] - 0.4) < 0.001

    def test_aggregate_phase_distribution(self):
        obs = [
            {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0, "phase": "normal", "timestamp": "t1"},
            {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0, "phase": "peak", "timestamp": "t2"},
            {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0, "phase": "normal", "timestamp": "t3"},
            {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0, "phase": "rebound", "timestamp": "t4"},
        ]
        result = _aggregate_window(obs)
        pd = result["phase_distribution"]
        assert abs(pd["normal"] - 0.5) < 0.001
        assert abs(pd["peak"] - 0.25) < 0.001
        assert abs(pd["rebound"] - 0.25) < 0.001

    def test_aggregate_change_frequency(self):
        obs = [
            {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0, "phase": "normal", "timestamp": "t1"},
            {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0, "phase": "normal", "timestamp": "t2"},
            {"fulfillment": 0.5, "tension": 0.0, "affinity": 0.0, "phase": "normal", "timestamp": "t3"},
        ]
        result = _aggregate_window(obs)
        # Only one change > 0.05 threshold (0.0 -> 0.5)
        assert result["change_frequency"] == 1

    def test_aggregate_timestamps(self):
        obs = [
            {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0, "phase": "normal", "timestamp": "2026-01-01T00:00:00Z"},
            {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0, "phase": "normal", "timestamp": "2026-01-01T01:00:00Z"},
        ]
        result = _aggregate_window(obs)
        assert result["timestamp_start"] == "2026-01-01T00:00:00Z"
        assert result["timestamp_end"] == "2026-01-01T01:00:00Z"

    def test_aggregate_invalid_phase_ignored(self):
        obs = [
            {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0, "phase": "invalid_phase", "timestamp": "t1"},
        ]
        result = _aggregate_window(obs)
        pd = result["phase_distribution"]
        # Invalid phase not counted, distribution stays at defaults
        assert pd["normal"] == 0.0


# --- record_observation tests ---


class TestRecordObservation:
    def test_single_observation_buffered(self, tmp_memory_dir):
        state = {"fulfillment": 0.3, "tension": -0.1, "affinity": 0.2}
        result = record_observation(tmp_memory_dir, emotion_state=state, dynamics_phase="normal")
        assert result["status"] == "buffered"
        assert result["buffer_size"] == 1
        assert result["entry"] is None

    def test_buffer_grows_until_window(self, tmp_memory_dir):
        state = {"fulfillment": 0.1, "tension": 0.0, "affinity": 0.0}
        for i in range(WINDOW_SIZE - 1):
            result = record_observation(tmp_memory_dir, emotion_state=state, dynamics_phase="normal")
            assert result["status"] == "buffered"
            assert result["buffer_size"] == i + 1

    def test_window_aggregation(self, tmp_memory_dir):
        state = {"fulfillment": 0.5, "tension": -0.2, "affinity": 0.1}
        for i in range(WINDOW_SIZE - 1):
            record_observation(tmp_memory_dir, emotion_state=state, dynamics_phase="normal")

        result = record_observation(tmp_memory_dir, emotion_state=state, dynamics_phase="normal")
        assert result["status"] == "aggregated"
        assert result["buffer_size"] == 0
        assert result["entry"] is not None
        assert result["entry"]["entry_id"] == 1
        assert result["entry"]["observation_count"] == WINDOW_SIZE

    def test_buffer_cleared_after_aggregation(self, tmp_memory_dir):
        state = {"fulfillment": 0.1, "tension": 0.0, "affinity": 0.0}
        for _ in range(WINDOW_SIZE):
            record_observation(tmp_memory_dir, emotion_state=state, dynamics_phase="normal")

        buf = load_buffer(tmp_memory_dir)
        assert len(buf) == 0

    def test_log_grows_with_entries(self, tmp_memory_dir):
        state = {"fulfillment": 0.1, "tension": 0.0, "affinity": 0.0}
        # Fill 2 windows
        for _ in range(WINDOW_SIZE * 2):
            record_observation(tmp_memory_dir, emotion_state=state, dynamics_phase="normal")

        log = load_log(tmp_memory_dir)
        assert len(log) == 2
        assert log[0]["entry_id"] == 1
        assert log[1]["entry_id"] == 2

    def test_invalid_phase_defaults_to_normal(self, tmp_memory_dir):
        state = {"fulfillment": 0.1, "tension": 0.0, "affinity": 0.0}
        result = record_observation(tmp_memory_dir, emotion_state=state, dynamics_phase="invalid")
        buf = load_buffer(tmp_memory_dir)
        assert buf[0]["phase"] == "normal"

    def test_missing_axis_defaults_to_zero(self, tmp_memory_dir):
        state = {"fulfillment": 0.5}  # missing tension and affinity
        result = record_observation(tmp_memory_dir, emotion_state=state, dynamics_phase="normal")
        buf = load_buffer(tmp_memory_dir)
        assert buf[0]["tension"] == 0.0
        assert buf[0]["affinity"] == 0.0

    def test_fifo_trim_on_max_entries(self, tmp_memory_dir):
        state = {"fulfillment": 0.1, "tension": 0.0, "affinity": 0.0}
        # Create MAX_ENTRIES + 5 entries
        total_obs = WINDOW_SIZE * (MAX_ENTRIES + 5)
        for _ in range(total_obs):
            record_observation(tmp_memory_dir, emotion_state=state, dynamics_phase="normal")

        log = load_log(tmp_memory_dir)
        assert len(log) == MAX_ENTRIES
        # First entry should be entry_id 6 (entries 1-5 trimmed)
        assert log[0]["entry_id"] == 6

    def test_none_emotion_state_does_not_crash(self, tmp_memory_dir):
        # When emotion_state is None and no emotion_state.json exists,
        # should still work with defaults
        result = record_observation(tmp_memory_dir, emotion_state=None, dynamics_phase="normal")
        assert result["status"] == "buffered"


# --- get_long_term_stats tests ---


class TestGetLongTermStats:
    def test_empty_stats(self, tmp_memory_dir):
        stats = get_long_term_stats(tmp_memory_dir)
        assert stats["total_entries"] == 0
        assert stats["entries_used"] == 0
        assert stats["trend"]["fulfillment"] == "stable"

    def test_stats_with_buffer_only(self, tmp_memory_dir):
        state = {"fulfillment": 0.5, "tension": 0.0, "affinity": 0.0}
        for _ in range(3):
            record_observation(tmp_memory_dir, emotion_state=state, dynamics_phase="normal")

        stats = get_long_term_stats(tmp_memory_dir)
        assert stats["total_entries"] == 0
        assert stats["buffer_pending"] == 3

    def test_stats_with_entries(self, tmp_memory_dir):
        state = {"fulfillment": 0.5, "tension": -0.2, "affinity": 0.3}
        for _ in range(WINDOW_SIZE):
            record_observation(tmp_memory_dir, emotion_state=state, dynamics_phase="normal")

        stats = get_long_term_stats(tmp_memory_dir)
        assert stats["total_entries"] == 1
        assert stats["entries_used"] == 1
        assert abs(stats["overall_axis_means"]["fulfillment"] - 0.5) < 0.01

    def test_stats_last_n(self, tmp_memory_dir):
        state = {"fulfillment": 0.1, "tension": 0.0, "affinity": 0.0}
        for _ in range(WINDOW_SIZE * 5):
            record_observation(tmp_memory_dir, emotion_state=state, dynamics_phase="normal")

        stats = get_long_term_stats(tmp_memory_dir, last_n=3)
        assert stats["total_entries"] == 5
        assert stats["entries_used"] == 3

    def test_stats_trend_rising(self, tmp_memory_dir):
        # First entries with low fulfillment, later with high
        for i in range(WINDOW_SIZE * 4):
            val = 0.1 if i < WINDOW_SIZE * 2 else 0.8
            state = {"fulfillment": val, "tension": 0.0, "affinity": 0.0}
            record_observation(tmp_memory_dir, emotion_state=state, dynamics_phase="normal")

        stats = get_long_term_stats(tmp_memory_dir, last_n=4)
        assert stats["trend"]["fulfillment"] == "rising"

    def test_stats_trend_falling(self, tmp_memory_dir):
        for i in range(WINDOW_SIZE * 4):
            val = 0.8 if i < WINDOW_SIZE * 2 else 0.1
            state = {"fulfillment": val, "tension": 0.0, "affinity": 0.0}
            record_observation(tmp_memory_dir, emotion_state=state, dynamics_phase="normal")

        stats = get_long_term_stats(tmp_memory_dir, last_n=4)
        assert stats["trend"]["fulfillment"] == "falling"

    def test_stats_phase_distribution(self, tmp_memory_dir):
        state = {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0}
        # 5 normal, 5 peak
        for i in range(WINDOW_SIZE):
            phase = "normal" if i < 5 else "peak"
            record_observation(tmp_memory_dir, emotion_state=state, dynamics_phase=phase)

        stats = get_long_term_stats(tmp_memory_dir)
        pd = stats["phase_distribution_average"]
        assert abs(pd["normal"] - 0.5) < 0.01
        assert abs(pd["peak"] - 0.5) < 0.01


# --- format_stats tests ---


class TestFormatStats:
    def test_format_empty(self, tmp_memory_dir):
        stats = get_long_term_stats(tmp_memory_dir)
        result = format_stats(stats)
        assert "No long-term dynamics data" in result

    def test_format_with_pending_buffer(self, tmp_memory_dir):
        state = {"fulfillment": 0.5, "tension": 0.0, "affinity": 0.0}
        for _ in range(3):
            record_observation(tmp_memory_dir, emotion_state=state, dynamics_phase="normal")

        stats = get_long_term_stats(tmp_memory_dir)
        result = format_stats(stats)
        assert "observations buffered" in result
        assert str(WINDOW_SIZE) in result

    def test_format_with_entries(self, tmp_memory_dir):
        state = {"fulfillment": 0.5, "tension": -0.2, "affinity": 0.3}
        for _ in range(WINDOW_SIZE):
            record_observation(tmp_memory_dir, emotion_state=state, dynamics_phase="normal")

        stats = get_long_term_stats(tmp_memory_dir)
        result = format_stats(stats)
        assert "Long-term dynamics" in result
        assert "fulfillment" in result
        assert "Phase distribution" in result
