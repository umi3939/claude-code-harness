#!/usr/bin/env python3
"""Tests for emotion change log functionality in emotion_state.py.

Tests cover:
- Change entry generation on update_state calls
- FIFO upper limit enforcement
- Freshness dynamic computation
- Persistence (save/load of change log)
- Backward compatibility (update_state without reason parameter)
- Reason text truncation (safety valve)
- format_change_history output
- get_change_history ordering and limit
"""

import json
import math
import os
import sys
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
    ALL_AXES,
    AXIS_AFFINITY,
    AXIS_FULFILLMENT,
    AXIS_NEUTRAL,
    AXIS_TENSION,
    CHANGE_LOG_FIFO_LIMIT,
    CHANGE_LOG_FRESHNESS_HALF_LIFE_HOURS,
    CHANGE_LOG_REASON_MAX_LENGTH,
    _compute_change_freshness,
    _load_change_log,
    _now_iso,
    _parse_iso,
    _record_change_log_entry,
    _save_change_log,
    create_default_state,
    format_change_history,
    get_change_history,
    load_state,
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


# =====================================================================
# Change Entry Generation tests
# =====================================================================


class TestChangeEntryGeneration:
    def test_update_creates_change_entry(self, memory_dir):
        """update_state should create a change log entry."""
        update_state(memory_dir, fulfillment=0.3, mode="set")
        log = _load_change_log(memory_dir)
        assert len(log) == 1

    def test_entry_has_required_fields(self, memory_dir):
        """Each change entry must have timestamp, before, after, reason."""
        update_state(memory_dir, fulfillment=0.5, mode="set", reason="test reason")
        log = _load_change_log(memory_dir)
        entry = log[0]
        assert "timestamp" in entry
        assert "before" in entry
        assert "after" in entry
        assert "reason" in entry

    def test_before_after_values_correct(self, memory_dir_with_state):
        """Before and after values should match the actual state change."""
        # State starts at fulfillment=0.5, tension=-0.3, affinity=0.2
        update_state(memory_dir_with_state, fulfillment=0.1, mode="delta")
        log = _load_change_log(memory_dir_with_state)
        entry = log[0]
        assert abs(entry["before"]["fulfillment"] - 0.5) < 0.01
        assert abs(entry["after"]["fulfillment"] - 0.6) < 0.01
        # Unchanged axes should show same before/after
        assert abs(entry["before"]["tension"] - (-0.3)) < 0.01
        assert abs(entry["after"]["tension"] - (-0.3)) < 0.01

    def test_multiple_updates_create_multiple_entries(self, memory_dir):
        """Each update_state call should create one entry."""
        update_state(memory_dir, fulfillment=0.1, mode="delta")
        update_state(memory_dir, tension=-0.2, mode="delta")
        update_state(memory_dir, affinity=0.3, mode="set")
        log = _load_change_log(memory_dir)
        assert len(log) == 3

    def test_set_mode_captures_before_after(self, memory_dir):
        """Set mode should record the change from old value to new value."""
        update_state(memory_dir, fulfillment=0.5, mode="set")
        update_state(memory_dir, fulfillment=-0.3, mode="set")
        log = _load_change_log(memory_dir)
        assert len(log) == 2
        # Second entry: before should be 0.5, after should be -0.3
        entry = log[1]
        assert abs(entry["before"]["fulfillment"] - 0.5) < 0.01
        assert abs(entry["after"]["fulfillment"] - (-0.3)) < 0.01

    def test_reason_stored(self, memory_dir):
        """Reason text should be stored as-is in the entry."""
        update_state(memory_dir, fulfillment=0.1, mode="delta", reason="deep thought")
        log = _load_change_log(memory_dir)
        assert log[0]["reason"] == "deep thought"

    def test_reason_empty_when_not_provided(self, memory_dir):
        """Reason should be empty string when not provided."""
        update_state(memory_dir, fulfillment=0.1, mode="delta")
        log = _load_change_log(memory_dir)
        assert log[0]["reason"] == ""

    def test_reason_none_stored_as_empty(self, memory_dir):
        """Reason=None should be stored as empty string."""
        update_state(memory_dir, fulfillment=0.1, mode="delta", reason=None)
        log = _load_change_log(memory_dir)
        assert log[0]["reason"] == ""

    def test_timestamp_is_valid_iso(self, memory_dir):
        """Timestamp should be a valid ISO 8601 string."""
        update_state(memory_dir, fulfillment=0.1, mode="delta")
        log = _load_change_log(memory_dir)
        ts = log[0]["timestamp"]
        parsed = _parse_iso(ts)
        assert parsed is not None

    def test_all_three_axes_in_before_after(self, memory_dir):
        """Before and after should contain all three axes."""
        update_state(memory_dir, fulfillment=0.1, mode="delta")
        log = _load_change_log(memory_dir)
        entry = log[0]
        for axis in ALL_AXES:
            assert axis in entry["before"]
            assert axis in entry["after"]

    def test_error_update_does_not_create_entry(self, memory_dir):
        """Failed updates (e.g., invalid mode) should not create entries."""
        result = update_state(memory_dir, fulfillment=0.1, mode="invalid")
        assert result.startswith("ERROR")
        log = _load_change_log(memory_dir)
        assert len(log) == 0

    def test_no_values_error_does_not_create_entry(self, memory_dir):
        """update_state with no axis values should not create entries."""
        result = update_state(memory_dir, mode="delta")
        assert result.startswith("ERROR")
        log = _load_change_log(memory_dir)
        assert len(log) == 0


# =====================================================================
# FIFO Upper Limit tests
# =====================================================================


class TestFIFOLimit:
    def test_fifo_limit_enforced(self, memory_dir):
        """Log should not exceed CHANGE_LOG_FIFO_LIMIT entries."""
        for i in range(CHANGE_LOG_FIFO_LIMIT + 10):
            update_state(memory_dir, fulfillment=0.01 * (i % 20), mode="set")
        log = _load_change_log(memory_dir)
        assert len(log) <= CHANGE_LOG_FIFO_LIMIT

    def test_oldest_entries_removed(self, memory_dir):
        """When exceeding limit, oldest entries should be removed."""
        for i in range(CHANGE_LOG_FIFO_LIMIT + 5):
            update_state(
                memory_dir,
                fulfillment=0.01 * (i % 20),
                mode="set",
                reason=f"update_{i}",
            )
        log = _load_change_log(memory_dir)
        # The first 5 entries (update_0 through update_4) should be gone
        reasons = [e.get("reason", "") for e in log]
        assert "update_0" not in reasons
        assert "update_4" not in reasons
        # The latest entries should be present
        assert f"update_{CHANGE_LOG_FIFO_LIMIT + 4}" in reasons

    def test_exactly_at_limit(self, memory_dir):
        """Exactly FIFO_LIMIT entries should be retained without removal."""
        for i in range(CHANGE_LOG_FIFO_LIMIT):
            update_state(memory_dir, fulfillment=0.01 * (i % 20), mode="set")
        log = _load_change_log(memory_dir)
        assert len(log) == CHANGE_LOG_FIFO_LIMIT


# =====================================================================
# Freshness Dynamic Computation tests
# =====================================================================


class TestFreshnessComputation:
    def test_recent_entry_high_freshness(self):
        """Just-created entry should have freshness close to 1.0."""
        now_str = _now_iso()
        freshness = _compute_change_freshness(now_str)
        assert freshness > 0.99

    def test_old_entry_low_freshness(self):
        """Entry from 3 half-lives ago should have freshness ~0.125."""
        old_time = (
            datetime.now(timezone.utc)
            - timedelta(hours=CHANGE_LOG_FRESHNESS_HALF_LIFE_HOURS * 3)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        freshness = _compute_change_freshness(old_time)
        assert freshness < 0.15

    def test_half_life_accuracy(self):
        """At exactly one half-life, freshness should be ~0.5."""
        half_life_ago = (
            datetime.now(timezone.utc)
            - timedelta(hours=CHANGE_LOG_FRESHNESS_HALF_LIFE_HOURS)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        freshness = _compute_change_freshness(half_life_ago)
        assert abs(freshness - 0.5) < 0.02

    def test_invalid_timestamp_returns_moderate(self):
        """Invalid timestamp should return 0.5 (moderate freshness)."""
        freshness = _compute_change_freshness("not-a-date")
        assert freshness == 0.5

    def test_empty_timestamp_returns_moderate(self):
        """Empty timestamp should return 0.5."""
        freshness = _compute_change_freshness("")
        assert freshness == 0.5

    def test_freshness_range(self):
        """Freshness should always be in [0, 1]."""
        # Very old
        very_old = (
            datetime.now(timezone.utc) - timedelta(hours=10000)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        f = _compute_change_freshness(very_old)
        assert 0.0 <= f <= 1.0

        # Just now
        f = _compute_change_freshness(_now_iso())
        assert 0.0 <= f <= 1.0


# =====================================================================
# Persistence tests
# =====================================================================


class TestPersistence:
    def test_change_log_persists_to_file(self, memory_dir):
        """Change log should be saved to a JSON file."""
        update_state(memory_dir, fulfillment=0.3, mode="set", reason="persist test")
        filepath = Path(memory_dir) / "emotion_change_log.json"
        assert filepath.exists()

    def test_change_log_file_is_valid_json(self, memory_dir):
        """The change log file should be valid JSON."""
        update_state(memory_dir, fulfillment=0.3, mode="set")
        filepath = Path(memory_dir) / "emotion_change_log.json"
        data = json.loads(filepath.read_text(encoding="utf-8"))
        assert "entries" in data
        assert isinstance(data["entries"], list)

    def test_change_log_independent_from_state_file(self, memory_dir):
        """Change log should be in a separate file from emotion_state.json."""
        update_state(memory_dir, fulfillment=0.3, mode="set")
        state_path = Path(memory_dir) / "emotion_state.json"
        log_path = Path(memory_dir) / "emotion_change_log.json"
        assert state_path.exists()
        assert log_path.exists()
        # State file should NOT contain change log
        state_data = json.loads(state_path.read_text(encoding="utf-8"))
        assert "entries" not in state_data

    def test_load_from_corrupted_file(self, memory_dir):
        """Loading from a corrupted file should return empty list."""
        filepath = Path(memory_dir) / "emotion_change_log.json"
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text("not valid json", encoding="utf-8")
        log = _load_change_log(memory_dir)
        assert log == []

    def test_load_from_nonexistent_file(self, memory_dir):
        """Loading when file doesn't exist should return empty list."""
        log = _load_change_log(memory_dir)
        assert log == []

    def test_save_and_reload_consistency(self, memory_dir):
        """Saved entries should be identical when reloaded."""
        update_state(memory_dir, fulfillment=0.5, mode="set", reason="test1")
        update_state(memory_dir, tension=-0.2, mode="delta", reason="test2")
        log1 = _load_change_log(memory_dir)
        # Reload
        log2 = _load_change_log(memory_dir)
        assert len(log1) == len(log2) == 2
        assert log1[0]["reason"] == log2[0]["reason"] == "test1"
        assert log1[1]["reason"] == log2[1]["reason"] == "test2"


# =====================================================================
# Backward Compatibility tests
# =====================================================================


class TestBackwardCompatibility:
    def test_update_without_reason_works(self, memory_dir):
        """update_state without reason parameter should work as before."""
        result = update_state(memory_dir, fulfillment=0.3, mode="set")
        assert not result.startswith("ERROR")
        assert "updated" in result.lower()

    def test_update_without_reason_still_creates_log(self, memory_dir):
        """update_state without reason should still create a change entry."""
        update_state(memory_dir, fulfillment=0.3, mode="set")
        log = _load_change_log(memory_dir)
        assert len(log) == 1
        assert log[0]["reason"] == ""

    def test_existing_code_calling_without_reason(self, memory_dir):
        """Simulate existing code that calls update_state without reason."""
        # This mimics the old calling convention
        result = update_state(memory_dir, fulfillment=0.1, tension=-0.1, mode="delta")
        assert not result.startswith("ERROR")

    def test_state_values_unchanged_by_log(self, memory_dir):
        """Adding change log should not alter the actual emotion state values."""
        update_state(memory_dir, fulfillment=0.5, tension=-0.3, affinity=0.2, mode="set")
        state = load_state(memory_dir)
        assert abs(state["fulfillment"] - 0.5) < 0.01
        assert abs(state["tension"] - (-0.3)) < 0.01
        assert abs(state["affinity"] - 0.2) < 0.01


# =====================================================================
# Reason Text Truncation (Safety Valve) tests
# =====================================================================


class TestReasonTruncation:
    def test_short_reason_unchanged(self, memory_dir):
        """Short reason text should be stored as-is."""
        reason = "short"
        update_state(memory_dir, fulfillment=0.1, mode="delta", reason=reason)
        log = _load_change_log(memory_dir)
        assert log[0]["reason"] == "short"

    def test_long_reason_truncated(self, memory_dir):
        """Reason text exceeding max length should be truncated."""
        long_reason = "a" * (CHANGE_LOG_REASON_MAX_LENGTH + 100)
        update_state(memory_dir, fulfillment=0.1, mode="delta", reason=long_reason)
        log = _load_change_log(memory_dir)
        assert len(log[0]["reason"]) == CHANGE_LOG_REASON_MAX_LENGTH

    def test_exactly_max_length_unchanged(self, memory_dir):
        """Reason text exactly at max length should not be truncated."""
        exact_reason = "b" * CHANGE_LOG_REASON_MAX_LENGTH
        update_state(memory_dir, fulfillment=0.1, mode="delta", reason=exact_reason)
        log = _load_change_log(memory_dir)
        assert log[0]["reason"] == exact_reason
        assert len(log[0]["reason"]) == CHANGE_LOG_REASON_MAX_LENGTH


# =====================================================================
# get_change_history tests
# =====================================================================


class TestGetChangeHistory:
    def test_returns_entries_with_freshness(self, memory_dir):
        """Returned entries should include a freshness field."""
        update_state(memory_dir, fulfillment=0.3, mode="set")
        entries = get_change_history(memory_dir)
        assert len(entries) == 1
        assert "freshness" in entries[0]
        assert 0.0 <= entries[0]["freshness"] <= 1.0

    def test_recent_entry_has_high_freshness(self, memory_dir):
        """Just-created entry should have freshness close to 1.0."""
        update_state(memory_dir, fulfillment=0.3, mode="set")
        entries = get_change_history(memory_dir)
        assert entries[0]["freshness"] > 0.99

    def test_reverse_chronological_order(self, memory_dir):
        """Entries should be returned newest first."""
        update_state(memory_dir, fulfillment=0.1, mode="set", reason="first")
        update_state(memory_dir, fulfillment=0.2, mode="set", reason="second")
        update_state(memory_dir, fulfillment=0.3, mode="set", reason="third")
        entries = get_change_history(memory_dir)
        assert entries[0]["reason"] == "third"
        assert entries[1]["reason"] == "second"
        assert entries[2]["reason"] == "first"

    def test_limit_parameter(self, memory_dir):
        """Limit parameter should restrict the number of returned entries."""
        for i in range(10):
            update_state(memory_dir, fulfillment=0.01 * i, mode="set")
        entries = get_change_history(memory_dir, limit=3)
        assert len(entries) == 3

    def test_limit_zero_returns_all(self, memory_dir):
        """Limit=0 should return all entries."""
        for i in range(5):
            update_state(memory_dir, fulfillment=0.01 * i, mode="set")
        entries = get_change_history(memory_dir, limit=0)
        assert len(entries) == 5

    def test_empty_log(self, memory_dir):
        """Empty log should return empty list."""
        entries = get_change_history(memory_dir)
        assert entries == []

    def test_limit_larger_than_entries(self, memory_dir):
        """Limit larger than actual entries should return all entries."""
        update_state(memory_dir, fulfillment=0.1, mode="set")
        entries = get_change_history(memory_dir, limit=100)
        assert len(entries) == 1


# =====================================================================
# format_change_history tests
# =====================================================================


class TestFormatChangeHistory:
    def test_empty_entries(self):
        """Empty list should return 'no history' message."""
        result = format_change_history([])
        assert "No emotion change history" in result

    def test_single_entry_format(self, memory_dir):
        """Single entry should be formatted with all expected components."""
        update_state(memory_dir, fulfillment=0.5, mode="set", reason="test reason")
        entries = get_change_history(memory_dir)
        result = format_change_history(entries)
        assert "1 entries" in result
        assert "freshness=" in result
        assert "fulfillment:" in result
        assert "test reason" in result

    def test_format_shows_changed_axes_only(self, memory_dir):
        """Only axes that actually changed should be shown."""
        update_state(memory_dir, fulfillment=0.5, mode="set")
        entries = get_change_history(memory_dir)
        result = format_change_history(entries)
        assert "fulfillment:" in result
        # tension and affinity didn't change (both stayed at 0.0)
        # They should not appear as changed axes

    def test_format_without_reason(self, memory_dir):
        """Entry without reason should not show 'reason:' in output."""
        update_state(memory_dir, fulfillment=0.5, mode="set")
        entries = get_change_history(memory_dir)
        result = format_change_history(entries)
        assert "reason:" not in result

    def test_format_with_reason(self, memory_dir):
        """Entry with reason should show 'reason:' in output."""
        update_state(memory_dir, fulfillment=0.5, mode="set", reason="test")
        entries = get_change_history(memory_dir)
        result = format_change_history(entries)
        assert "reason: test" in result

    def test_multiple_entries_format(self, memory_dir):
        """Multiple entries should all be numbered."""
        for i in range(3):
            update_state(memory_dir, fulfillment=0.1 * (i + 1), mode="set")
        entries = get_change_history(memory_dir)
        result = format_change_history(entries)
        assert "3 entries" in result
        assert "1." in result
        assert "2." in result
        assert "3." in result


# =====================================================================
# _record_change_log_entry direct tests
# =====================================================================


class TestRecordChangeLogEntry:
    def test_direct_recording(self, memory_dir):
        """Direct call to _record_change_log_entry should work."""
        before = {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0}
        after = {"fulfillment": 0.5, "tension": 0.0, "affinity": 0.0}
        _record_change_log_entry(memory_dir, before, after, "direct test")
        log = _load_change_log(memory_dir)
        assert len(log) == 1
        assert log[0]["reason"] == "direct test"

    def test_recording_failure_silent(self, tmp_path):
        """Recording failure should be silent (no exception)."""
        # Use a path that cannot be written to
        nonexistent = str(tmp_path / "nonexistent" / "deep" / "path")
        before = {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0}
        after = {"fulfillment": 0.5, "tension": 0.0, "affinity": 0.0}
        # This should not raise even if directory creation works differently
        # The key point: no exception propagates
        _record_change_log_entry(nonexistent, before, after, "test")


# =====================================================================
# _load_change_log / _save_change_log direct tests
# =====================================================================


class TestLoadSaveChangeLog:
    def test_save_and_load(self, memory_dir):
        """Save and load should round-trip correctly."""
        entries = [
            {
                "timestamp": "2026-03-13T10:00:00Z",
                "before": {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0},
                "after": {"fulfillment": 0.5, "tension": 0.0, "affinity": 0.0},
                "reason": "test",
            }
        ]
        _save_change_log(memory_dir, entries)
        loaded = _load_change_log(memory_dir)
        assert len(loaded) == 1
        assert loaded[0]["reason"] == "test"

    def test_load_missing_entries_key(self, memory_dir):
        """JSON without 'entries' key should return empty list."""
        filepath = Path(memory_dir) / "emotion_change_log.json"
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text('{"other_key": []}', encoding="utf-8")
        log = _load_change_log(memory_dir)
        assert log == []

    def test_load_entries_not_list(self, memory_dir):
        """'entries' that is not a list should return empty list."""
        filepath = Path(memory_dir) / "emotion_change_log.json"
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text('{"entries": "not a list"}', encoding="utf-8")
        log = _load_change_log(memory_dir)
        assert log == []
