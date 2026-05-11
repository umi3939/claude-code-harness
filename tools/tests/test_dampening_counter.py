"""Tests for dampening_counter.py - Pipeline 2: Dampening連続適用制限."""

import json
import os
import sys
import tempfile

import pytest

# Add tools to path
TOOLS_DIR = os.path.join(os.path.dirname(__file__), "..")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from dampening_counter import (
    DEFAULT_COUNTER_STATE,
    MAX_CONSECUTIVE_DAMPENING,
    check_and_update,
    load_counter,
    reset_counter,
    save_counter,
)


@pytest.fixture
def memory_dir(tmp_path):
    """Create a temporary memory directory."""
    return str(tmp_path)


# --- load_counter tests ---

class TestLoadCounter:
    def test_load_missing_file_returns_default(self, memory_dir):
        """FileNotFoundError -> default state."""
        result = load_counter(memory_dir)
        assert result == DEFAULT_COUNTER_STATE
        assert result["consecutive_count"] == 0
        assert result["last_dampened"] is False

    def test_load_valid_state(self, memory_dir):
        """Valid JSON file loads correctly."""
        state = {"consecutive_count": 3, "last_dampened": True}
        path = os.path.join(memory_dir, "dampening_counter.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f)
        result = load_counter(memory_dir)
        assert result["consecutive_count"] == 3
        assert result["last_dampened"] is True

    def test_load_corrupt_json_returns_default(self, memory_dir):
        """Corrupt JSON -> default state."""
        path = os.path.join(memory_dir, "dampening_counter.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{broken json")
        result = load_counter(memory_dir)
        assert result == DEFAULT_COUNTER_STATE

    def test_load_non_dict_returns_default(self, memory_dir):
        """Non-dict JSON -> default state."""
        path = os.path.join(memory_dir, "dampening_counter.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump([1, 2, 3], f)
        result = load_counter(memory_dir)
        assert result == DEFAULT_COUNTER_STATE

    def test_load_negative_count_clamped_to_zero(self, memory_dir):
        """Negative consecutive_count is clamped to 0."""
        path = os.path.join(memory_dir, "dampening_counter.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"consecutive_count": -5, "last_dampened": False}, f)
        result = load_counter(memory_dir)
        assert result["consecutive_count"] == 0

    def test_load_missing_fields_use_defaults(self, memory_dir):
        """Missing fields filled from defaults."""
        path = os.path.join(memory_dir, "dampening_counter.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"consecutive_count": 2}, f)
        result = load_counter(memory_dir)
        assert result["consecutive_count"] == 2
        assert result["last_dampened"] is False  # default

    def test_load_wrong_type_count_uses_default(self, memory_dir):
        """Non-int consecutive_count falls back to default 0."""
        path = os.path.join(memory_dir, "dampening_counter.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"consecutive_count": "abc", "last_dampened": True}, f)
        result = load_counter(memory_dir)
        assert result["consecutive_count"] == 0
        assert result["last_dampened"] is True


# --- save_counter tests ---

class TestSaveCounter:
    def test_save_and_load_roundtrip(self, memory_dir):
        """Save then load returns same state."""
        state = {"consecutive_count": 4, "last_dampened": True}
        save_counter(memory_dir, state)
        loaded = load_counter(memory_dir)
        assert loaded == state

    def test_save_overwrites_existing(self, memory_dir):
        """Save overwrites previous state."""
        save_counter(memory_dir, {"consecutive_count": 1, "last_dampened": True})
        save_counter(memory_dir, {"consecutive_count": 0, "last_dampened": False})
        loaded = load_counter(memory_dir)
        assert loaded["consecutive_count"] == 0
        assert loaded["last_dampened"] is False


# --- reset_counter tests ---

class TestResetCounter:
    def test_reset_returns_default(self, memory_dir):
        """Reset returns default state."""
        save_counter(memory_dir, {"consecutive_count": 5, "last_dampened": True})
        result = reset_counter(memory_dir)
        assert result == DEFAULT_COUNTER_STATE

    def test_reset_persists_to_file(self, memory_dir):
        """Reset state is persisted."""
        save_counter(memory_dir, {"consecutive_count": 3, "last_dampened": True})
        reset_counter(memory_dir)
        loaded = load_counter(memory_dir)
        assert loaded == DEFAULT_COUNTER_STATE


# --- check_and_update tests ---

class TestCheckAndUpdate:
    def test_not_dampened_returns_original(self, memory_dir):
        """dampening_factor=1.0 -> returns 1.0, counter stays 0."""
        result = check_and_update(memory_dir, 1.0)
        assert result == 1.0
        counter = load_counter(memory_dir)
        assert counter["consecutive_count"] == 0

    def test_dampened_increments_counter(self, memory_dir):
        """dampening_factor<1.0 -> increments counter, returns original."""
        result = check_and_update(memory_dir, 0.7)
        assert result == 0.7
        counter = load_counter(memory_dir)
        assert counter["consecutive_count"] == 1
        assert counter["last_dampened"] is True

    def test_consecutive_dampening_below_limit(self, memory_dir):
        """Multiple dampening below limit -> returns original each time."""
        for i in range(MAX_CONSECUTIVE_DAMPENING - 1):
            result = check_and_update(memory_dir, 0.5)
            assert result == 0.5
        counter = load_counter(memory_dir)
        assert counter["consecutive_count"] == MAX_CONSECUTIVE_DAMPENING - 1

    def test_consecutive_dampening_at_limit_forces_reset(self, memory_dir):
        """At MAX_CONSECUTIVE -> returns 1.0 and resets counter."""
        for i in range(MAX_CONSECUTIVE_DAMPENING - 1):
            check_and_update(memory_dir, 0.5)
        # This call should trigger the reset
        result = check_and_update(memory_dir, 0.5)
        assert result == 1.0
        counter = load_counter(memory_dir)
        assert counter["consecutive_count"] == 0
        assert counter["last_dampened"] is False

    def test_not_dampened_resets_counter(self, memory_dir):
        """Non-dampened call resets consecutive counter."""
        check_and_update(memory_dir, 0.5)
        check_and_update(memory_dir, 0.5)
        counter = load_counter(memory_dir)
        assert counter["consecutive_count"] == 2
        # Now a non-dampened call
        check_and_update(memory_dir, 1.0)
        counter = load_counter(memory_dir)
        assert counter["consecutive_count"] == 0

    def test_interleaved_dampening_does_not_accumulate(self, memory_dir):
        """Dampened -> not dampened -> dampened resets counter each time."""
        check_and_update(memory_dir, 0.5)
        check_and_update(memory_dir, 0.5)
        check_and_update(memory_dir, 1.0)  # resets
        check_and_update(memory_dir, 0.5)
        counter = load_counter(memory_dir)
        assert counter["consecutive_count"] == 1

    def test_boundary_dampening_factor_exactly_one(self, memory_dir):
        """dampening_factor=1.0 is not considered dampened."""
        result = check_and_update(memory_dir, 1.0)
        assert result == 1.0
        counter = load_counter(memory_dir)
        assert counter["consecutive_count"] == 0

    def test_boundary_dampening_factor_just_below_one(self, memory_dir):
        """dampening_factor=0.999 is considered dampened."""
        result = check_and_update(memory_dir, 0.999)
        assert result == 0.999
        counter = load_counter(memory_dir)
        assert counter["consecutive_count"] == 1

    def test_zero_dampening_factor(self, memory_dir):
        """dampening_factor=0.0 is extreme but valid dampening."""
        result = check_and_update(memory_dir, 0.0)
        assert result == 0.0
        counter = load_counter(memory_dir)
        assert counter["consecutive_count"] == 1

    def test_fresh_dir_no_file(self, memory_dir):
        """Works on fresh directory with no existing counter file."""
        result = check_and_update(memory_dir, 0.8)
        assert result == 0.8
        counter = load_counter(memory_dir)
        assert counter["consecutive_count"] == 1
