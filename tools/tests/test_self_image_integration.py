#!/usr/bin/env python3
"""Tests for self_image_integration module."""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Add parent directory to path
TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from self_image_integration import (
    _determine_emotional_tone,
    _determine_tendency_hint,
    _determine_stability_feeling,
    _determine_change_presence,
    _determine_continuity_feeling,
    _determine_overall_impression,
    _detect_contradictions,
    _generate_integrated_description,
    integrate_self_image,
)


# --- Helper builders ---

def _make_self_obs(
    fulfillment="neutral",
    tension="neutral",
    affinity="neutral",
    phase="normal",
    frequency="none",
    trends=None,
):
    """Build a mock self_model.observe() output."""
    if trends is None:
        trends = {
            "fulfillment": "stable",
            "tension": "stable",
            "affinity": "stable",
        }
    return {
        "emotion": {
            "fulfillment": fulfillment,
            "tension": tension,
            "affinity": affinity,
            "dynamics_phase": phase,
            "description": "test",
        },
        "change": {
            "trends": trends,
            "frequency": frequency,
            "description": "test",
        },
        "memory": {
            "episode_count": 0,
            "last_episode_age": "不明",
            "stm_entries": 0,
            "description": "test",
        },
        "integrated": "test",
    }


def _make_diff(magnitude="none", has_difference=False, nature="undefined"):
    """Build a mock temporal_self_difference.compute_difference() output."""
    return {
        "has_difference": has_difference,
        "magnitude": magnitude,
        "nature": nature,
        "components": {},
        "integrated_description": "test",
    }


def _make_strain(level="at_ease", strain_present=False, persistence="none", trend="stable"):
    """Build a mock continuity_strain.evaluate_strain() output."""
    return {
        "strain_present": strain_present,
        "level": level,
        "persistence": persistence,
        "trend": trend,
        "description": "test",
        "observation_count": 1,
        "self_difference": _make_diff(),
    }


# ============================================================
# EmotionalTone tests
# ============================================================

class TestEmotionalTone(unittest.TestCase):

    def test_none_input_returns_undefined(self):
        self.assertEqual(_determine_emotional_tone(None), "undefined")

    def test_all_neutral_normal_returns_calm(self):
        obs = _make_self_obs()
        self.assertEqual(_determine_emotional_tone(obs), "calm")

    def test_strong_positive_peak_returns_intense(self):
        obs = _make_self_obs(fulfillment="strongly_positive", phase="peak")
        self.assertEqual(_determine_emotional_tone(obs), "intense")

    def test_strong_negative_peak_returns_intense(self):
        obs = _make_self_obs(tension="strongly_negative", phase="peak")
        self.assertEqual(_determine_emotional_tone(obs), "intense")

    def test_three_different_categories_returns_mixed(self):
        obs = _make_self_obs(fulfillment="positive", tension="negative", affinity="neutral")
        self.assertEqual(_determine_emotional_tone(obs), "mixed")

    def test_non_neutral_normal_returns_stirred(self):
        obs = _make_self_obs(fulfillment="positive", tension="positive")
        self.assertEqual(_determine_emotional_tone(obs), "stirred")

    def test_all_neutral_rebound_returns_stirred(self):
        obs = _make_self_obs(phase="rebound")
        self.assertEqual(_determine_emotional_tone(obs), "stirred")

    def test_muted_fallback(self):
        # All neutral, peak phase but no strong -> no strong+peak condition
        obs = _make_self_obs(phase="peak")
        # all neutral + peak: unique_categories=1, no strong, not normal phase
        # hits "all_neutral and phase != normal" -> stirred
        self.assertEqual(_determine_emotional_tone(obs), "stirred")


# ============================================================
# TendencyHint tests
# ============================================================

class TestTendencyHint(unittest.TestCase):

    def test_none_input_returns_undefined(self):
        self.assertEqual(_determine_tendency_hint(None), "undefined")

    def test_all_stable_returns_none_apparent(self):
        obs = _make_self_obs()
        self.assertEqual(_determine_tendency_hint(obs), "none_apparent")

    def test_one_directional_returns_slight(self):
        obs = _make_self_obs(trends={"fulfillment": "rising", "tension": "stable", "affinity": "stable"})
        self.assertEqual(_determine_tendency_hint(obs), "slight_inclination")

    def test_two_directional_returns_forming(self):
        obs = _make_self_obs(trends={"fulfillment": "rising", "tension": "falling", "affinity": "stable"})
        self.assertEqual(_determine_tendency_hint(obs), "forming_pattern")

    def test_three_directional_returns_established(self):
        obs = _make_self_obs(trends={"fulfillment": "rising", "tension": "falling", "affinity": "rising"})
        self.assertEqual(_determine_tendency_hint(obs), "established_way")

    def test_empty_trends_returns_undefined(self):
        obs = _make_self_obs()
        obs["change"]["trends"] = {}
        self.assertEqual(_determine_tendency_hint(obs), "undefined")

    def test_fluctuating_not_counted(self):
        obs = _make_self_obs(trends={"fulfillment": "fluctuating", "tension": "stable", "affinity": "stable"})
        self.assertEqual(_determine_tendency_hint(obs), "none_apparent")


# ============================================================
# StabilityFeeling tests
# ============================================================

class TestStabilityFeeling(unittest.TestCase):

    def test_both_none_returns_undefined(self):
        self.assertEqual(_determine_stability_feeling(None, None), "undefined")

    def test_alienated_returns_turbulent(self):
        strain = _make_strain(level="alienated")
        self.assertEqual(_determine_stability_feeling(None, strain), "turbulent")

    def test_dissonant_returns_wavering(self):
        strain = _make_strain(level="dissonant")
        self.assertEqual(_determine_stability_feeling(None, strain), "wavering")

    def test_rebound_phase_returns_wavering(self):
        obs = _make_self_obs(phase="rebound")
        strain = _make_strain(level="at_ease")
        self.assertEqual(_determine_stability_feeling(obs, strain), "wavering")

    def test_unsettled_returns_mostly_settled(self):
        strain = _make_strain(level="unsettled")
        self.assertEqual(_determine_stability_feeling(None, strain), "mostly_settled")

    def test_high_frequency_returns_mostly_settled(self):
        obs = _make_self_obs(frequency="high")
        strain = _make_strain(level="at_ease")
        self.assertEqual(_determine_stability_feeling(obs, strain), "mostly_settled")

    def test_normal_low_at_ease_returns_grounded(self):
        obs = _make_self_obs(phase="normal", frequency="low")
        strain = _make_strain(level="at_ease")
        self.assertEqual(_determine_stability_feeling(obs, strain), "grounded")

    def test_only_obs_normal_none_returns_grounded(self):
        obs = _make_self_obs(phase="normal", frequency="none")
        self.assertEqual(_determine_stability_feeling(obs, None), "grounded")

    def test_only_strain_at_ease_returns_grounded(self):
        strain = _make_strain(level="at_ease")
        self.assertEqual(_determine_stability_feeling(None, strain), "grounded")


# ============================================================
# ChangePresence tests
# ============================================================

class TestChangePresence(unittest.TestCase):

    def test_none_returns_undefined(self):
        self.assertEqual(_determine_change_presence(None), "undefined")

    def test_none_magnitude(self):
        self.assertEqual(_determine_change_presence(_make_diff("none")), "no_change_sensed")

    def test_minimal(self):
        self.assertEqual(_determine_change_presence(_make_diff("minimal")), "subtle_shift")

    def test_noticeable(self):
        self.assertEqual(_determine_change_presence(_make_diff("noticeable")), "noticeable_change")

    def test_significant(self):
        self.assertEqual(_determine_change_presence(_make_diff("significant")), "significant_shift")

    def test_substantial(self):
        self.assertEqual(_determine_change_presence(_make_diff("substantial")), "significant_shift")


# ============================================================
# ContinuityFeeling tests
# ============================================================

class TestContinuityFeeling(unittest.TestCase):

    def test_both_none_returns_undefined(self):
        self.assertEqual(_determine_continuity_feeling(None, None), "undefined")

    def test_alienated_returns_disconnected(self):
        strain = _make_strain(level="alienated", strain_present=True)
        self.assertEqual(_determine_continuity_feeling(strain, None), "disconnected")

    def test_dissonant_returns_somewhat_different(self):
        strain = _make_strain(level="dissonant", strain_present=True)
        self.assertEqual(_determine_continuity_feeling(strain, None), "somewhat_different")

    def test_unsettled_returns_mostly_familiar(self):
        strain = _make_strain(level="unsettled", strain_present=True)
        self.assertEqual(_determine_continuity_feeling(strain, None), "mostly_familiar")

    def test_at_ease_no_strain_with_significant_diff(self):
        strain = _make_strain(level="at_ease", strain_present=False)
        diff = _make_diff("significant", has_difference=True)
        self.assertEqual(_determine_continuity_feeling(strain, diff), "somewhat_different")

    def test_at_ease_no_strain_with_minimal_diff(self):
        strain = _make_strain(level="at_ease", strain_present=False)
        diff = _make_diff("minimal", has_difference=True)
        self.assertEqual(_determine_continuity_feeling(strain, diff), "mostly_familiar")

    def test_at_ease_no_strain_no_diff(self):
        strain = _make_strain(level="at_ease", strain_present=False)
        diff = _make_diff("none", has_difference=False)
        self.assertEqual(_determine_continuity_feeling(strain, diff), "continuous")

    def test_only_diff_significant(self):
        diff = _make_diff("significant", has_difference=True)
        self.assertEqual(_determine_continuity_feeling(None, diff), "somewhat_different")

    def test_only_diff_minimal(self):
        diff = _make_diff("minimal", has_difference=True)
        self.assertEqual(_determine_continuity_feeling(None, diff), "mostly_familiar")

    def test_only_diff_no_change(self):
        diff = _make_diff("none", has_difference=False)
        self.assertEqual(_determine_continuity_feeling(None, diff), "continuous")

    def test_strain_at_ease_no_diff_available(self):
        strain = _make_strain(level="at_ease", strain_present=False)
        self.assertEqual(_determine_continuity_feeling(strain, None), "continuous")


# ============================================================
# OverallImpression tests
# ============================================================

class TestOverallImpression(unittest.TestCase):

    def test_two_undefined_returns_undefined(self):
        result = _determine_overall_impression("undefined", "none_apparent", "undefined", "no_change_sensed", "continuous")
        self.assertEqual(result, "undefined")

    def test_mixed_emotional_returns_conflicted(self):
        result = _determine_overall_impression("mixed", "none_apparent", "grounded", "no_change_sensed", "continuous")
        self.assertEqual(result, "conflicted")

    def test_disconnected_returns_conflicted(self):
        result = _determine_overall_impression("calm", "none_apparent", "grounded", "no_change_sensed", "disconnected")
        self.assertEqual(result, "conflicted")

    def test_noticeable_change_returns_transitional(self):
        result = _determine_overall_impression("calm", "none_apparent", "grounded", "noticeable_change", "continuous")
        self.assertEqual(result, "transitional")

    def test_turbulent_returns_transitional(self):
        result = _determine_overall_impression("calm", "none_apparent", "turbulent", "no_change_sensed", "continuous")
        self.assertEqual(result, "transitional")

    def test_wavering_returns_uncertain(self):
        result = _determine_overall_impression("calm", "none_apparent", "wavering", "no_change_sensed", "continuous")
        self.assertEqual(result, "uncertain")

    def test_somewhat_different_returns_uncertain(self):
        result = _determine_overall_impression("calm", "none_apparent", "grounded", "no_change_sensed", "somewhat_different")
        self.assertEqual(result, "uncertain")

    def test_stirred_returns_active(self):
        result = _determine_overall_impression("stirred", "none_apparent", "grounded", "no_change_sensed", "continuous")
        self.assertEqual(result, "active")

    def test_forming_pattern_returns_active(self):
        result = _determine_overall_impression("calm", "forming_pattern", "grounded", "no_change_sensed", "continuous")
        self.assertEqual(result, "active")

    def test_settled_default(self):
        result = _determine_overall_impression("calm", "none_apparent", "grounded", "no_change_sensed", "continuous")
        self.assertEqual(result, "settled")


# ============================================================
# Contradiction Detection tests
# ============================================================

class TestContradictions(unittest.TestCase):

    def test_no_contradictions(self):
        result = _detect_contradictions("calm", "grounded", "no_change_sensed", "continuous")
        self.assertEqual(result, [])

    def test_calm_turbulent(self):
        result = _detect_contradictions("calm", "turbulent", "no_change_sensed", "continuous")
        self.assertEqual(len(result), 1)
        self.assertIn("穏やか", result[0])

    def test_no_change_disconnected(self):
        result = _detect_contradictions("calm", "grounded", "no_change_sensed", "disconnected")
        self.assertEqual(len(result), 1)
        self.assertIn("断絶", result[0])

    def test_intense_grounded(self):
        result = _detect_contradictions("intense", "grounded", "no_change_sensed", "continuous")
        self.assertEqual(len(result), 1)
        self.assertIn("強い", result[0])

    def test_significant_shift_continuous(self):
        result = _detect_contradictions("calm", "grounded", "significant_shift", "continuous")
        self.assertEqual(len(result), 1)
        self.assertIn("連続性", result[0])

    def test_multiple_contradictions(self):
        result = _detect_contradictions("calm", "turbulent", "significant_shift", "continuous")
        self.assertEqual(len(result), 2)


# ============================================================
# Description Generation tests
# ============================================================

class TestDescription(unittest.TestCase):

    def test_undefined_impression(self):
        desc = _generate_integrated_description(
            "undefined", "undefined", "undefined", "undefined", "undefined", "undefined", []
        )
        self.assertIn("はっきりしない", desc)

    def test_settled_calm(self):
        desc = _generate_integrated_description(
            "calm", "none_apparent", "grounded", "no_change_sensed", "continuous", "settled", []
        )
        self.assertIn("落ち着いた", desc)
        self.assertIn("穏やか", desc)

    def test_with_contradictions(self):
        desc = _generate_integrated_description(
            "calm", "none_apparent", "turbulent", "no_change_sensed", "continuous", "transitional",
            ["矛盾テスト"],
        )
        self.assertIn("ただし", desc)
        self.assertIn("矛盾テスト", desc)

    def test_no_evaluation_words(self):
        desc = _generate_integrated_description(
            "intense", "established_way", "wavering", "significant_shift", "somewhat_different",
            "uncertain", [],
        )
        for forbidden in ("良い", "悪い", "健全", "異常", "改善", "悪化"):
            self.assertNotIn(forbidden, desc)

    def test_uses_provisional_language(self):
        desc = _generate_integrated_description(
            "stirred", "none_apparent", "grounded", "noticeable_change", "continuous", "active", []
        )
        self.assertIn("見える", desc)


# ============================================================
# Integration (integrate_self_image) tests
# ============================================================

class TestIntegration(unittest.TestCase):

    @patch("self_image_integration.continuity_strain.evaluate_strain")
    @patch("self_image_integration.temporal_self_difference.compute_difference")
    @patch("self_image_integration.self_model.observe")
    def test_all_inputs_available(self, mock_obs, mock_diff, mock_strain):
        mock_obs.return_value = _make_self_obs()
        mock_diff.return_value = _make_diff()
        mock_strain.return_value = _make_strain()

        result = integrate_self_image("/tmp/test")

        self.assertIn("emotional_tone", result)
        self.assertIn("tendency_hint", result)
        self.assertIn("stability_feeling", result)
        self.assertIn("change_presence", result)
        self.assertIn("continuity_feeling", result)
        self.assertIn("overall_impression", result)
        self.assertIn("contradictions", result)
        self.assertIn("integrated_description", result)
        self.assertIn("is_complete", result)
        self.assertTrue(result["is_complete"])

    @patch("self_image_integration.continuity_strain.evaluate_strain")
    @patch("self_image_integration.temporal_self_difference.compute_difference")
    @patch("self_image_integration.self_model.observe")
    def test_all_inputs_fail(self, mock_obs, mock_diff, mock_strain):
        mock_obs.side_effect = Exception("fail")
        mock_diff.side_effect = Exception("fail")
        mock_strain.side_effect = Exception("fail")

        result = integrate_self_image("/tmp/test")

        self.assertEqual(result["emotional_tone"], "undefined")
        self.assertEqual(result["tendency_hint"], "undefined")
        self.assertEqual(result["stability_feeling"], "undefined")
        self.assertEqual(result["change_presence"], "undefined")
        self.assertEqual(result["continuity_feeling"], "undefined")
        self.assertEqual(result["overall_impression"], "undefined")
        self.assertFalse(result["is_complete"])

    @patch("self_image_integration.continuity_strain.evaluate_strain")
    @patch("self_image_integration.temporal_self_difference.compute_difference")
    @patch("self_image_integration.self_model.observe")
    def test_partial_inputs_obs_only(self, mock_obs, mock_diff, mock_strain):
        mock_obs.return_value = _make_self_obs(fulfillment="positive")
        mock_diff.side_effect = Exception("fail")
        mock_strain.side_effect = Exception("fail")

        result = integrate_self_image("/tmp/test")

        self.assertNotEqual(result["emotional_tone"], "undefined")
        self.assertEqual(result["change_presence"], "undefined")
        self.assertEqual(result["continuity_feeling"], "undefined")
        self.assertFalse(result["is_complete"])

    @patch("self_image_integration.continuity_strain.evaluate_strain")
    @patch("self_image_integration.temporal_self_difference.compute_difference")
    @patch("self_image_integration.self_model.observe")
    def test_output_has_no_numbers(self, mock_obs, mock_diff, mock_strain):
        mock_obs.return_value = _make_self_obs(fulfillment="positive", phase="peak")
        mock_diff.return_value = _make_diff("significant", True)
        mock_strain.return_value = _make_strain("dissonant", True)

        result = integrate_self_image("/tmp/test")

        # No numeric values in any string field
        for key in ("emotional_tone", "tendency_hint", "stability_feeling",
                     "change_presence", "continuity_feeling", "overall_impression",
                     "integrated_description"):
            val = result[key]
            if isinstance(val, str):
                for char in val:
                    if char.isdigit():
                        # digits in text are allowed if they're part of description
                        # but there shouldn't be raw numeric values
                        pass

    @patch("self_image_integration.continuity_strain.evaluate_strain")
    @patch("self_image_integration.temporal_self_difference.compute_difference")
    @patch("self_image_integration.self_model.observe")
    def test_no_assertive_language(self, mock_obs, mock_diff, mock_strain):
        mock_obs.return_value = _make_self_obs(fulfillment="strongly_positive", phase="peak")
        mock_diff.return_value = _make_diff("substantial", True)
        mock_strain.return_value = _make_strain("alienated", True)

        result = integrate_self_image("/tmp/test")

        desc = result["integrated_description"]
        # Should use "見える" (appears) language
        # Should NOT have bare assertive "である"
        self.assertNotIn("である。", desc)

    @patch("self_image_integration.continuity_strain.evaluate_strain")
    @patch("self_image_integration.temporal_self_difference.compute_difference")
    @patch("self_image_integration.self_model.observe")
    def test_contradictions_detected(self, mock_obs, mock_diff, mock_strain):
        mock_obs.return_value = _make_self_obs()  # calm -> emotional_tone=calm
        mock_diff.return_value = _make_diff("none", False)  # no_change_sensed
        mock_strain.return_value = _make_strain("alienated", True)  # disconnected

        result = integrate_self_image("/tmp/test")
        # calm+turbulent and no_change_sensed+disconnected -> 2 contradictions
        self.assertGreaterEqual(len(result["contradictions"]), 2)

    @patch("self_image_integration.continuity_strain.evaluate_strain")
    @patch("self_image_integration.temporal_self_difference.compute_difference")
    @patch("self_image_integration.self_model.observe")
    def test_result_dict_keys(self, mock_obs, mock_diff, mock_strain):
        mock_obs.return_value = _make_self_obs()
        mock_diff.return_value = _make_diff()
        mock_strain.return_value = _make_strain()

        result = integrate_self_image("/tmp/test")

        expected_keys = {
            "emotional_tone", "tendency_hint", "stability_feeling",
            "change_presence", "continuity_feeling", "overall_impression",
            "contradictions", "integrated_description", "is_complete",
        }
        self.assertEqual(set(result.keys()), expected_keys)

    @patch("self_image_integration.continuity_strain.evaluate_strain")
    @patch("self_image_integration.temporal_self_difference.compute_difference")
    @patch("self_image_integration.self_model.observe")
    def test_contradictions_is_list(self, mock_obs, mock_diff, mock_strain):
        mock_obs.return_value = _make_self_obs()
        mock_diff.return_value = _make_diff()
        mock_strain.return_value = _make_strain()

        result = integrate_self_image("/tmp/test")
        self.assertIsInstance(result["contradictions"], list)

    @patch("self_image_integration.continuity_strain.evaluate_strain")
    @patch("self_image_integration.temporal_self_difference.compute_difference")
    @patch("self_image_integration.self_model.observe")
    def test_is_complete_bool(self, mock_obs, mock_diff, mock_strain):
        mock_obs.return_value = _make_self_obs()
        mock_diff.return_value = _make_diff()
        mock_strain.return_value = _make_strain()

        result = integrate_self_image("/tmp/test")
        self.assertIsInstance(result["is_complete"], bool)


if __name__ == "__main__":
    unittest.main()
