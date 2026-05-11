#!/usr/bin/env python3
"""Tests for continuity_strain module.

Uses mocked temporal_self_difference.compute_difference to supply
controlled difference data.
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Add tools directory to path
TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import continuity_strain
from continuity_strain import (
    _default_state,
    _determine_base_level,
    _determine_persistence,
    _determine_trend,
    _escalate_level,
    _generate_description,
    _is_significant,
    _load_state,
    _save_state,
    evaluate_strain,
    STATE_FILENAME,
    OBSERVATIONS_MAX,
    STRAIN_HISTORY_MAX,
)


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def _make_diff(magnitude="none", nature="stable", has_difference=False):
    """Create a mock compute_difference return value."""
    return {
        "has_difference": has_difference,
        "magnitude": magnitude,
        "nature": nature,
        "components": {
            "fulfillment": {"change_type": "unchanged", "from": "neutral", "to": "neutral"},
            "tension": {"change_type": "unchanged", "from": "neutral", "to": "neutral"},
            "affinity": {"change_type": "unchanged", "from": "neutral", "to": "neutral"},
            "dynamics_phase": {"change_type": "unchanged", "from": "normal", "to": "normal"},
        },
        "integrated_description": "自己状態に変化は見られない。",
    }


# --- Test: Initial state (no file) ---

class TestInitialState:
    def test_no_file_returns_at_ease(self, tmp_dir):
        """No state file → at_ease, strain_present=False."""
        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=_make_diff()):
            result = evaluate_strain(tmp_dir)
        assert result["strain_present"] is False
        assert result["level"] == "at_ease"
        assert result["persistence"] == "none"
        assert result["trend"] == "stable"
        assert result["observation_count"] == 1

    def test_initial_description(self, tmp_dir):
        """Initial description matches at_ease text."""
        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=_make_diff()):
            result = evaluate_strain(tmp_dir)
        assert result["description"] == "自己の連続性に違和感はない。"


# --- Test: Significance judgment ---

class TestSignificance:
    @pytest.mark.parametrize("mag,expected", [
        ("none", False),
        ("minimal", False),
        ("noticeable", True),
        ("significant", True),
        ("substantial", True),
    ])
    def test_is_significant(self, mag, expected):
        assert _is_significant(mag) == expected


# --- Test: StrainLevel determination ---

class TestStrainLevel:
    @pytest.mark.parametrize("count,expected", [
        (0, "at_ease"),
        (1, "at_ease"),
        (2, "at_ease"),
        (3, "unsettled"),
        (4, "unsettled"),
        (5, "dissonant"),
        (9, "dissonant"),
        (10, "alienated"),
        (15, "alienated"),
    ])
    def test_base_level(self, count, expected):
        assert _determine_base_level(count) == expected

    def test_escalation_to_unsettled(self, tmp_dir):
        """3 consecutive significant with substantial escalates unsettled→dissonant."""
        diff = _make_diff("substantial", "shifting", True)
        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=diff):
            for _ in range(3):
                result = evaluate_strain(tmp_dir)
        # Base level = unsettled (3 consecutive), escalation from substantial → dissonant
        assert result["level"] == "dissonant"

    def test_at_ease_no_escalation(self, tmp_dir):
        """at_ease is never escalated even with substantial."""
        diff = _make_diff("substantial", "shifting", True)
        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=diff):
            result = evaluate_strain(tmp_dir)
        # Only 1 consecutive significant → at_ease, no escalation
        assert result["level"] == "at_ease"

    def test_progression_at_ease_to_unsettled(self, tmp_dir):
        """After 3 significant diffs, level becomes unsettled."""
        diff = _make_diff("noticeable", "shifting", True)
        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=diff):
            for _ in range(3):
                result = evaluate_strain(tmp_dir)
        assert result["level"] == "unsettled"
        assert result["strain_present"] is True

    def test_progression_to_dissonant(self, tmp_dir):
        """After 5 significant diffs, level becomes dissonant."""
        diff = _make_diff("significant", "shifting", True)
        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=diff):
            for _ in range(5):
                result = evaluate_strain(tmp_dir)
        assert result["level"] == "dissonant"

    def test_progression_to_alienated(self, tmp_dir):
        """After 10 significant diffs, level becomes alienated."""
        diff = _make_diff("significant", "shifting", True)
        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=diff):
            for _ in range(10):
                result = evaluate_strain(tmp_dir)
        assert result["level"] == "alienated"


# --- Test: Escalation correction ---

class TestEscalationCorrection:
    def test_escalation_at_5_substantial(self, tmp_dir):
        """5 consecutive substantial → dissonant escalated to alienated."""
        diff = _make_diff("substantial", "shifting", True)
        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=diff):
            for _ in range(5):
                result = evaluate_strain(tmp_dir)
        # Base = dissonant (5 consec), most common = substantial → escalate to alienated
        assert result["level"] == "alienated"

    def test_no_escalation_with_noticeable(self, tmp_dir):
        """5 consecutive noticeable → dissonant without escalation."""
        diff = _make_diff("noticeable", "shifting", True)
        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=diff):
            for _ in range(5):
                result = evaluate_strain(tmp_dir)
        assert result["level"] == "dissonant"

    def test_escalate_level_function(self):
        assert _escalate_level("at_ease") == "unsettled"
        assert _escalate_level("unsettled") == "dissonant"
        assert _escalate_level("dissonant") == "alienated"
        assert _escalate_level("alienated") == "alienated"


# --- Test: StrainPersistence ---

class TestStrainPersistence:
    @pytest.mark.parametrize("count,expected", [
        (0, "none"),
        (2, "none"),
        (3, "momentary"),
        (4, "momentary"),
        (5, "ongoing"),
        (9, "ongoing"),
        (10, "chronic"),
        (20, "chronic"),
    ])
    def test_persistence(self, count, expected):
        assert _determine_persistence(count) == expected


# --- Test: StrainTrend ---

class TestStrainTrend:
    def test_stable_all_same(self):
        assert _determine_trend(["at_ease", "at_ease", "at_ease", "at_ease"]) == "stable"

    def test_building(self):
        assert _determine_trend(["at_ease", "unsettled", "dissonant"]) == "building"

    def test_easing(self):
        assert _determine_trend(["alienated", "dissonant", "unsettled"]) == "easing"

    def test_fluctuating(self):
        assert _determine_trend(["at_ease", "dissonant", "unsettled", "alienated"]) == "fluctuating"

    def test_less_than_2_entries(self):
        assert _determine_trend([]) == "stable"
        assert _determine_trend(["unsettled"]) == "stable"

    def test_uses_last_4(self):
        # Only last 4 matter: [unsettled, dissonant, dissonant, alienated] → building
        history = ["at_ease", "at_ease", "unsettled", "dissonant", "dissonant", "alienated"]
        assert _determine_trend(history) == "building"

    def test_non_decreasing_but_equal_start_end(self):
        # [unsettled, unsettled, unsettled, unsettled] → stable (all same)
        assert _determine_trend(["unsettled", "unsettled", "unsettled", "unsettled"]) == "stable"


# --- Test: Decay mechanism ---

class TestDecayMechanism:
    def test_two_insignificant_reduces_by_one(self, tmp_dir):
        """2 consecutive insignificant → consecutive_significant decreases by 1."""
        sig_diff = _make_diff("noticeable", "shifting", True)
        insig_diff = _make_diff("minimal", "stable", True)

        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=sig_diff):
            for _ in range(4):
                evaluate_strain(tmp_dir)

        # Now 4 consecutive significant → unsettled
        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=insig_diff):
            evaluate_strain(tmp_dir)  # 1 insignificant
            result = evaluate_strain(tmp_dir)  # 2 insignificant → reduce by 1 (4→3)

        assert result["strain_present"] is True
        assert result["level"] == "unsettled"  # forced to unsettled during easing

    def test_four_insignificant_resets(self, tmp_dir):
        """4 consecutive insignificant → complete reset to at_ease."""
        sig_diff = _make_diff("significant", "shifting", True)
        insig_diff = _make_diff("none", "stable", False)

        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=sig_diff):
            for _ in range(5):
                evaluate_strain(tmp_dir)

        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=insig_diff):
            for _ in range(4):
                result = evaluate_strain(tmp_dir)

        assert result["strain_present"] is False
        assert result["level"] == "at_ease"

    def test_easing_trend_during_decay(self, tmp_dir):
        """During decay (2+ insignificant, cons_sig > 0), trend is easing."""
        sig_diff = _make_diff("noticeable", "shifting", True)
        insig_diff = _make_diff("minimal", "stable", True)

        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=sig_diff):
            for _ in range(4):
                evaluate_strain(tmp_dir)

        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=insig_diff):
            evaluate_strain(tmp_dir)
            result = evaluate_strain(tmp_dir)

        assert result["trend"] == "easing"

    def test_decay_clamped_to_unsettled(self, tmp_dir):
        """During easing, level is clamped to unsettled max."""
        sig_diff = _make_diff("significant", "shifting", True)
        insig_diff = _make_diff("minimal", "stable", True)

        # Build up to dissonant (5 consecutive)
        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=sig_diff):
            for _ in range(5):
                evaluate_strain(tmp_dir)

        # Start decay
        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=insig_diff):
            evaluate_strain(tmp_dir)
            result = evaluate_strain(tmp_dir)  # 2 insignificant → reduce, clamp to unsettled

        assert result["level"] == "unsettled"


# --- Test: Description generation ---

class TestDescription:
    def test_at_ease(self):
        desc = _generate_description("at_ease", "none", "stable")
        assert desc == "自己の連続性に違和感はない。"
        assert "数" not in desc  # no numbers

    def test_unsettled_momentary_building(self):
        desc = _generate_description("unsettled", "momentary", "building")
        assert "わずかな違和感" in desc
        assert "最近現れた" in desc
        assert "強まっている" in desc

    def test_dissonant_ongoing_stable(self):
        desc = _generate_description("dissonant", "ongoing", "stable")
        assert "不協和" in desc
        assert "しばらく続いている" in desc

    def test_alienated_chronic_fluctuating(self):
        desc = _generate_description("alienated", "chronic", "fluctuating")
        assert "断絶感" in desc
        assert "長く持続している" in desc
        assert "揺れ動いている" in desc

    def test_no_numbers_in_description(self, tmp_dir):
        """Description must not contain any digits."""
        diff = _make_diff("significant", "shifting", True)
        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=diff):
            for _ in range(5):
                result = evaluate_strain(tmp_dir)
        assert not any(c.isdigit() for c in result["description"])

    def test_no_evaluation_in_description(self, tmp_dir):
        """Description must not contain evaluation words."""
        diff = _make_diff("significant", "shifting", True)
        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=diff):
            for _ in range(5):
                result = evaluate_strain(tmp_dir)
        for word in ["良い", "悪い", "改善", "悪化", "問題"]:
            assert word not in result["description"]

    def test_easing_description(self):
        desc = _generate_description("unsettled", "momentary", "easing")
        assert "和らいでいる" in desc


# --- Test: FIFO limits ---

class TestFIFO:
    def test_observations_fifo(self, tmp_dir):
        """Observations are capped at OBSERVATIONS_MAX."""
        diff = _make_diff("noticeable", "shifting", True)
        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=diff):
            for _ in range(25):
                evaluate_strain(tmp_dir)

        state = _load_state(tmp_dir)
        assert len(state["observations"]) <= OBSERVATIONS_MAX

    def test_strain_history_fifo(self, tmp_dir):
        """Strain level history is capped at STRAIN_HISTORY_MAX."""
        diff = _make_diff("noticeable", "shifting", True)
        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=diff):
            for _ in range(15):
                evaluate_strain(tmp_dir)

        state = _load_state(tmp_dir)
        assert len(state["strain_level_history"]) <= STRAIN_HISTORY_MAX


# --- Test: Edge cases ---

class TestEdgeCases:
    def test_corrupted_file(self, tmp_dir):
        """Corrupted state file → fallback to at_ease."""
        path = Path(tmp_dir) / STATE_FILENAME
        path.write_text("not json at all {{{{", encoding="utf-8")

        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=_make_diff()):
            result = evaluate_strain(tmp_dir)
        assert result["level"] == "at_ease"
        assert result["strain_present"] is False

    def test_single_observation(self, tmp_dir):
        """Single observation → at_ease."""
        with patch.object(continuity_strain.temporal_self_difference, "compute_difference",
                          return_value=_make_diff("significant", "shifting", True)):
            result = evaluate_strain(tmp_dir)
        assert result["level"] == "at_ease"
        assert result["observation_count"] == 1

    def test_self_difference_included(self, tmp_dir):
        """Result includes the self_difference from compute_difference."""
        diff = _make_diff("noticeable", "shifting", True)
        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=diff):
            result = evaluate_strain(tmp_dir)
        assert result["self_difference"] == diff

    def test_empty_state_file(self, tmp_dir):
        """Empty JSON object → fallback to default."""
        path = Path(tmp_dir) / STATE_FILENAME
        path.write_text("{}", encoding="utf-8")

        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=_make_diff()):
            result = evaluate_strain(tmp_dir)
        assert result["level"] == "at_ease"


# --- Test: Persistence (file I/O) ---

class TestPersistence:
    def test_state_persists_across_calls(self, tmp_dir):
        """State is saved and loaded correctly between calls."""
        diff = _make_diff("significant", "shifting", True)
        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=diff):
            evaluate_strain(tmp_dir)
            evaluate_strain(tmp_dir)

        state = _load_state(tmp_dir)
        assert state["consecutive_significant_count"] == 2
        assert len(state["observations"]) == 2

    def test_state_file_created(self, tmp_dir):
        """State file is created after first call."""
        assert not (Path(tmp_dir) / STATE_FILENAME).exists()

        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=_make_diff()):
            evaluate_strain(tmp_dir)

        assert (Path(tmp_dir) / STATE_FILENAME).exists()

    def test_save_load_roundtrip(self, tmp_dir):
        """Save and load produce identical state."""
        state = _default_state()
        state["consecutive_significant_count"] = 7
        state["observations"].append({"magnitude": "significant", "timestamp": "test"})
        _save_state(tmp_dir, state)
        loaded = _load_state(tmp_dir)
        assert loaded["consecutive_significant_count"] == 7
        assert len(loaded["observations"]) == 1


# --- Test: Mixed sequences ---

class TestMixedSequences:
    def test_significant_then_insignificant_then_significant(self, tmp_dir):
        """Interrupted sequence resets insignificant count."""
        sig = _make_diff("significant", "shifting", True)
        insig = _make_diff("minimal", "stable", True)

        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=sig):
            for _ in range(3):
                evaluate_strain(tmp_dir)

        # 1 insignificant (not enough for decay)
        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=insig):
            evaluate_strain(tmp_dir)

        # Back to significant → cons_insig resets
        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=sig):
            result = evaluate_strain(tmp_dir)

        # cons_sig was 3, then 1 insig (no reduction < 2), then +1 sig = 4
        assert result["strain_present"] is True
        assert result["level"] == "unsettled"

    def test_observation_count_increments(self, tmp_dir):
        """observation_count increments with each call."""
        with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=_make_diff()):
            for i in range(5):
                result = evaluate_strain(tmp_dir)
                assert result["observation_count"] == i + 1


# --- Test: Trend in integration ---

class TestTrendIntegration:
    def test_building_trend(self, tmp_dir):
        """Building trend when levels increase monotonically."""
        # Feed increasing magnitudes to build up strain
        diffs = [
            _make_diff("noticeable", "shifting", True),  # 1
            _make_diff("noticeable", "shifting", True),  # 2 → at_ease
            _make_diff("noticeable", "shifting", True),  # 3 → unsettled
            _make_diff("significant", "shifting", True),  # 4 → unsettled
            _make_diff("significant", "shifting", True),  # 5 → dissonant
        ]
        results = []
        for d in diffs:
            with patch.object(continuity_strain.temporal_self_difference, "compute_difference", return_value=d):
                results.append(evaluate_strain(tmp_dir))

        # Level history: at_ease, at_ease, unsettled, unsettled, dissonant
        # Last 4: [at_ease, unsettled, unsettled, dissonant] → building
        assert results[-1]["trend"] == "building"
