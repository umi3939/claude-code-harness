#!/usr/bin/env python3
"""Tests for emotion_reaction.py."""

import os
import sys

# Add tools directory to path
TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from emotion_reaction import (
    _EMOTION_BASE_DELTAS,
    ALL_AXES,
    DELTA_CAP,
    _apply_convergence_suppression,
    react,
)

# Neutral current state for baseline tests
NEUTRAL_STATE = {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0}


class TestEmotionLabelBaseDeltas:
    """Stage 1: Each emotion label produces expected base deltas."""

    def test_happy_base_deltas(self):
        result = react("happy", 0.0, "farewell", NEUTRAL_STATE)
        assert result["fulfillment"] > 0
        assert result["tension"] < 0
        assert result["affinity"] > 0

    def test_sad_base_deltas(self):
        result = react("sad", 0.0, "farewell", NEUTRAL_STATE)
        assert result["fulfillment"] < 0
        assert result["tension"] > 0
        assert result["affinity"] == 0.0

    def test_angry_base_deltas(self):
        result = react("angry", 0.0, "farewell", NEUTRAL_STATE)
        assert result["fulfillment"] < 0
        assert result["tension"] > 0
        assert result["affinity"] < 0

    def test_surprised_base_deltas(self):
        result = react("surprised", 0.0, "farewell", NEUTRAL_STATE)
        assert result["fulfillment"] > 0
        assert result["tension"] > 0
        assert result["affinity"] == 0.0

    def test_scared_base_deltas(self):
        result = react("scared", 0.0, "farewell", NEUTRAL_STATE)
        assert result["fulfillment"] < 0
        assert result["tension"] > 0
        assert result["affinity"] < 0

    def test_loving_base_deltas(self):
        result = react("loving", 0.0, "farewell", NEUTRAL_STATE)
        assert result["fulfillment"] > 0
        assert result["tension"] < 0
        assert result["affinity"] > 0

    def test_teasing_base_deltas(self):
        result = react("teasing", 0.0, "farewell", NEUTRAL_STATE)
        assert result["fulfillment"] > 0
        assert result["tension"] < 0
        assert result["affinity"] > 0

    def test_neutral_no_change(self):
        result = react("neutral", 0.0, "farewell", NEUTRAL_STATE)
        assert result["fulfillment"] == 0.0
        assert result["tension"] == 0.0
        assert result["affinity"] == 0.0


class TestValenceSecondaryEffects:
    """Stage 2: Valence modifies fulfillment and tension."""

    def test_positive_valence_increases_fulfillment(self):
        with_valence = react("neutral", 0.8, "farewell", NEUTRAL_STATE)
        without_valence = react("neutral", 0.0, "farewell", NEUTRAL_STATE)
        assert with_valence["fulfillment"] > without_valence["fulfillment"]

    def test_positive_valence_decreases_tension(self):
        with_valence = react("neutral", 0.8, "farewell", NEUTRAL_STATE)
        without_valence = react("neutral", 0.0, "farewell", NEUTRAL_STATE)
        assert with_valence["tension"] < without_valence["tension"]

    def test_negative_valence_decreases_fulfillment(self):
        with_valence = react("neutral", -0.8, "farewell", NEUTRAL_STATE)
        without_valence = react("neutral", 0.0, "farewell", NEUTRAL_STATE)
        assert with_valence["fulfillment"] < without_valence["fulfillment"]

    def test_negative_valence_increases_tension(self):
        with_valence = react("neutral", -0.8, "farewell", NEUTRAL_STATE)
        without_valence = react("neutral", 0.0, "farewell", NEUTRAL_STATE)
        assert with_valence["tension"] > without_valence["tension"]

    def test_zero_valence_no_secondary_effect(self):
        result = react("neutral", 0.0, "farewell", NEUTRAL_STATE)
        assert result["fulfillment"] == 0.0
        assert result["tension"] == 0.0

    def test_valence_clamped_to_range(self):
        """Valence beyond [-1, 1] should be clamped."""
        result_high = react("neutral", 2.0, "farewell", NEUTRAL_STATE)
        result_one = react("neutral", 1.0, "farewell", NEUTRAL_STATE)
        assert abs(result_high["fulfillment"] - result_one["fulfillment"]) < 1e-9


class TestIntentAdjustments:
    """Stage 3: Intent modifies deltas."""

    def test_sharing_increases_affinity(self):
        sharing = react("neutral", 0.0, "sharing", NEUTRAL_STATE)
        farewell = react("neutral", 0.0, "farewell", NEUTRAL_STATE)
        assert sharing["affinity"] > farewell["affinity"]

    def test_question_increases_affinity(self):
        question = react("neutral", 0.0, "question", NEUTRAL_STATE)
        farewell = react("neutral", 0.0, "farewell", NEUTRAL_STATE)
        assert question["affinity"] > farewell["affinity"]

    def test_expression_increases_fulfillment(self):
        expression = react("neutral", 0.0, "expression", NEUTRAL_STATE)
        farewell = react("neutral", 0.0, "farewell", NEUTRAL_STATE)
        assert expression["fulfillment"] > farewell["fulfillment"]

    def test_greeting_increases_affinity_decreases_tension(self):
        greeting = react("neutral", 0.0, "greeting", NEUTRAL_STATE)
        farewell = react("neutral", 0.0, "farewell", NEUTRAL_STATE)
        assert greeting["affinity"] > farewell["affinity"]
        assert greeting["tension"] < farewell["tension"]

    def test_farewell_no_adjustment(self):
        result = react("neutral", 0.0, "farewell", NEUTRAL_STATE)
        assert result["fulfillment"] == 0.0
        assert result["tension"] == 0.0
        assert result["affinity"] == 0.0

    def test_unknown_intent_no_adjustment(self):
        result = react("neutral", 0.0, "unknown_intent", NEUTRAL_STATE)
        assert result["fulfillment"] == 0.0
        assert result["tension"] == 0.0
        assert result["affinity"] == 0.0


class TestAmplitudeModifier:
    """Amplitude modifier scales magnitude without changing direction."""

    def test_amplitude_half(self):
        normal = react("happy", 0.5, "farewell", NEUTRAL_STATE)
        half = react("happy", 0.5, "farewell", NEUTRAL_STATE, amplitude_modifier=0.5)
        for axis in ALL_AXES:
            if normal[axis] != 0.0:
                assert abs(half[axis]) < abs(normal[axis])
                # Same sign
                assert (half[axis] > 0) == (normal[axis] > 0) or half[axis] == 0.0

    def test_amplitude_double(self):
        normal = react("happy", 0.5, "farewell", NEUTRAL_STATE)
        double = react("happy", 0.5, "farewell", NEUTRAL_STATE, amplitude_modifier=2.0)
        for axis in ALL_AXES:
            if normal[axis] != 0.0:
                # Double should be larger (before capping)
                assert abs(double[axis]) >= abs(normal[axis]) - 1e-9

    def test_amplitude_one_no_change(self):
        normal = react("happy", 0.5, "farewell", NEUTRAL_STATE)
        with_one = react("happy", 0.5, "farewell", NEUTRAL_STATE, amplitude_modifier=1.0)
        for axis in ALL_AXES:
            assert abs(normal[axis] - with_one[axis]) < 1e-9


class TestDeltaCap:
    """Delta is clamped to [-0.3, +0.3] per axis."""

    def test_positive_cap(self):
        # Use high amplitude to try to exceed cap
        result = react("loving", 1.0, "sharing", NEUTRAL_STATE, amplitude_modifier=5.0)
        for axis in ALL_AXES:
            assert result[axis] <= DELTA_CAP + 1e-9

    def test_negative_cap(self):
        result = react("angry", -1.0, "farewell", NEUTRAL_STATE, amplitude_modifier=5.0)
        for axis in ALL_AXES:
            assert result[axis] >= -DELTA_CAP - 1e-9

    def test_all_axes_within_cap(self):
        for label in _EMOTION_BASE_DELTAS:
            for valence in [-1.0, 0.0, 1.0]:
                for intent in ["sharing", "question", "expression", "greeting", "farewell"]:
                    result = react(label, valence, intent, NEUTRAL_STATE)
                    for axis in ALL_AXES:
                        assert -DELTA_CAP - 1e-9 <= result[axis] <= DELTA_CAP + 1e-9, \
                            f"Cap violated: {label}/{valence}/{intent} -> {axis}={result[axis]}"


class TestConvergenceSuppression:
    """When current value is high, same-direction deltas are suppressed."""

    def test_suppression_reduces_positive_delta_at_high_value(self):
        high_state = {"fulfillment": 0.8, "tension": 0.0, "affinity": 0.0}
        suppressed = react("happy", 0.5, "farewell", high_state)
        normal = react("happy", 0.5, "farewell", NEUTRAL_STATE)
        # fulfillment delta should be smaller when already high
        assert suppressed["fulfillment"] < normal["fulfillment"]

    def test_suppression_reduces_negative_delta_at_low_value(self):
        low_state = {"fulfillment": -0.8, "tension": 0.0, "affinity": 0.0}
        suppressed = react("sad", -0.5, "farewell", low_state)
        normal = react("sad", -0.5, "farewell", NEUTRAL_STATE)
        # fulfillment delta should be less negative when already low
        assert suppressed["fulfillment"] > normal["fulfillment"]

    def test_no_suppression_below_threshold(self):
        moderate_state = {"fulfillment": 0.3, "tension": 0.0, "affinity": 0.0}
        result = react("happy", 0.0, "farewell", moderate_state)
        neutral_result = react("happy", 0.0, "farewell", NEUTRAL_STATE)
        assert abs(result["fulfillment"] - neutral_result["fulfillment"]) < 1e-9

    def test_opposite_direction_not_suppressed(self):
        high_state = {"fulfillment": 0.8, "tension": 0.0, "affinity": 0.0}
        # sad decreases fulfillment (opposite to current positive)
        result = react("sad", 0.0, "farewell", high_state)
        neutral_result = react("sad", 0.0, "farewell", NEUTRAL_STATE)
        # Opposite direction: no suppression
        assert abs(result["fulfillment"] - neutral_result["fulfillment"]) < 1e-9

    def test_convergence_suppression_function_directly(self):
        # Below threshold: no change
        assert _apply_convergence_suppression(0.1, 0.3) == 0.1
        # Above threshold, same direction: reduced
        result = _apply_convergence_suppression(0.1, 0.7)
        assert result < 0.1
        assert result > 0.0
        # Above threshold, opposite direction: no change
        assert _apply_convergence_suppression(-0.1, 0.7) == -0.1


class TestUnknownInputs:
    """Unknown emotion labels and intents produce safe defaults."""

    def test_unknown_emotion_label_treated_as_neutral(self):
        result = react("unknown_emotion", 0.0, "farewell", NEUTRAL_STATE)
        assert result["fulfillment"] == 0.0
        assert result["tension"] == 0.0
        assert result["affinity"] == 0.0

    def test_unknown_intent_no_adjustment(self):
        result = react("happy", 0.0, "some_random_intent", NEUTRAL_STATE)
        neutral_intent = react("happy", 0.0, "farewell", NEUTRAL_STATE)
        for axis in ALL_AXES:
            assert abs(result[axis] - neutral_intent[axis]) < 1e-9


class TestCombinedEffects:
    """Test combined stage 1 + 2 + 3 effects."""

    def test_happy_positive_sharing(self):
        """Happy emotion + positive valence + sharing intent."""
        result = react("happy", 0.7, "sharing", NEUTRAL_STATE)
        assert result["fulfillment"] > 0
        assert result["tension"] < 0
        assert result["affinity"] > 0

    def test_angry_negative_farewell(self):
        """Angry + negative valence + farewell."""
        result = react("angry", -0.8, "farewell", NEUTRAL_STATE)
        assert result["fulfillment"] < 0
        assert result["tension"] > 0
        assert result["affinity"] < 0

    def test_neutral_greeting(self):
        """Neutral emotion + greeting intent."""
        result = react("neutral", 0.0, "greeting", NEUTRAL_STATE)
        assert result["affinity"] > 0
        assert result["tension"] < 0

    def test_return_type_is_dict_with_all_axes(self):
        result = react("happy", 0.5, "sharing", NEUTRAL_STATE)
        assert isinstance(result, dict)
        for axis in ALL_AXES:
            assert axis in result
            assert isinstance(result[axis], float)


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_extreme_current_state(self):
        extreme = {"fulfillment": 1.0, "tension": -1.0, "affinity": 1.0}
        result = react("loving", 1.0, "sharing", extreme)
        for axis in ALL_AXES:
            assert -DELTA_CAP - 1e-9 <= result[axis] <= DELTA_CAP + 1e-9

    def test_missing_axis_in_current_state(self):
        """Current state missing an axis defaults to 0.0."""
        partial_state = {"fulfillment": 0.5}
        result = react("happy", 0.5, "farewell", partial_state)
        assert isinstance(result, dict)
        for axis in ALL_AXES:
            assert axis in result

    def test_non_numeric_in_current_state(self):
        """Non-numeric values in current state treated as 0.0."""
        bad_state = {"fulfillment": "not_a_number", "tension": 0.0, "affinity": 0.0}
        result = react("happy", 0.5, "farewell", bad_state)
        # Should not crash; fulfillment convergence check skipped for non-numeric
        assert isinstance(result, dict)

    def test_amplitude_zero(self):
        """Amplitude 0 should produce zero deltas (for labels that have base deltas)."""
        result = react("happy", 0.5, "farewell", NEUTRAL_STATE, amplitude_modifier=0.0)
        for axis in ALL_AXES:
            assert result[axis] == 0.0

    def test_amplitude_negative_clamped_to_zero(self):
        """Negative amplitude_modifier should be clamped to 0.0, producing zero deltas."""
        result = react("happy", 0.5, "farewell", NEUTRAL_STATE, amplitude_modifier=-1.0)
        for axis in ALL_AXES:
            assert result[axis] == 0.0

    def test_amplitude_extreme_clamped_to_five(self):
        """amplitude_modifier > 5.0 should be clamped to 5.0."""
        result_10 = react("happy", 0.5, "farewell", NEUTRAL_STATE, amplitude_modifier=10.0)
        result_5 = react("happy", 0.5, "farewell", NEUTRAL_STATE, amplitude_modifier=5.0)
        for axis in ALL_AXES:
            assert abs(result_10[axis] - result_5[axis]) < 1e-9
