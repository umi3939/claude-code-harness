#!/usr/bin/env python3
"""Tests for identity_coherence module."""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock

# Add the tools directory to sys.path
TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import identity_coherence


# =============================================================================
# Shift Detection Tests
# =============================================================================


class TestDetectTemporalDifference:
    def test_none_input(self):
        assert identity_coherence._detect_temporal_difference(None) is False

    def test_magnitude_none(self):
        assert identity_coherence._detect_temporal_difference({"magnitude": "none"}) is False

    def test_magnitude_minimal(self):
        assert identity_coherence._detect_temporal_difference({"magnitude": "minimal"}) is False

    def test_magnitude_noticeable(self):
        assert identity_coherence._detect_temporal_difference({"magnitude": "noticeable"}) is True

    def test_magnitude_significant(self):
        assert identity_coherence._detect_temporal_difference({"magnitude": "significant"}) is True

    def test_magnitude_substantial(self):
        assert identity_coherence._detect_temporal_difference({"magnitude": "substantial"}) is True

    def test_missing_magnitude_key(self):
        assert identity_coherence._detect_temporal_difference({}) is False


class TestDetectContinuityStrain:
    def test_none_input(self):
        assert identity_coherence._detect_continuity_strain(None) is False

    def test_level_at_ease(self):
        assert identity_coherence._detect_continuity_strain({"level": "at_ease"}) is False

    def test_level_unsettled(self):
        assert identity_coherence._detect_continuity_strain({"level": "unsettled"}) is True

    def test_level_dissonant(self):
        assert identity_coherence._detect_continuity_strain({"level": "dissonant"}) is True

    def test_level_alienated(self):
        assert identity_coherence._detect_continuity_strain({"level": "alienated"}) is True

    def test_missing_level_key(self):
        assert identity_coherence._detect_continuity_strain({}) is False


class TestDetectSelfImageFlux:
    def test_none_input(self):
        assert identity_coherence._detect_self_image_flux(None) is False

    def test_grounded(self):
        assert identity_coherence._detect_self_image_flux({"stability_feeling": "grounded"}) is False

    def test_mostly_settled(self):
        assert identity_coherence._detect_self_image_flux({"stability_feeling": "mostly_settled"}) is False

    def test_wavering(self):
        assert identity_coherence._detect_self_image_flux({"stability_feeling": "wavering"}) is True

    def test_turbulent(self):
        assert identity_coherence._detect_self_image_flux({"stability_feeling": "turbulent"}) is True

    def test_missing_key(self):
        assert identity_coherence._detect_self_image_flux({}) is False


class TestDetectEmotionalTurbulence:
    def test_none_input(self):
        assert identity_coherence._detect_emotional_turbulence(None) is False

    def test_calm(self):
        assert identity_coherence._detect_emotional_turbulence({"emotional_tone": "calm"}) is False

    def test_muted(self):
        assert identity_coherence._detect_emotional_turbulence({"emotional_tone": "muted"}) is False

    def test_stirred(self):
        assert identity_coherence._detect_emotional_turbulence({"emotional_tone": "stirred"}) is True

    def test_mixed(self):
        assert identity_coherence._detect_emotional_turbulence({"emotional_tone": "mixed"}) is True

    def test_intense(self):
        assert identity_coherence._detect_emotional_turbulence({"emotional_tone": "intense"}) is True

    def test_missing_key(self):
        assert identity_coherence._detect_emotional_turbulence({}) is False


# =============================================================================
# Level and Intensity Determination Tests
# =============================================================================


class TestDetermineCoherenceLevel:
    def test_zero_sources(self):
        assert identity_coherence._determine_coherence_level(0) == "stable"

    def test_one_source(self):
        assert identity_coherence._determine_coherence_level(1) == "slightly_shifting"

    def test_two_sources(self):
        assert identity_coherence._determine_coherence_level(2) == "unsettled"

    def test_three_sources(self):
        assert identity_coherence._determine_coherence_level(3) == "disconnected"

    def test_four_sources(self):
        assert identity_coherence._determine_coherence_level(4) == "disconnected"


class TestDetermineOverlapIntensity:
    def test_zero(self):
        assert identity_coherence._determine_overlap_intensity(0) == "none"

    def test_one(self):
        assert identity_coherence._determine_overlap_intensity(1) == "mild"

    def test_two(self):
        assert identity_coherence._determine_overlap_intensity(2) == "moderate"

    def test_three(self):
        assert identity_coherence._determine_overlap_intensity(3) == "intense"

    def test_four(self):
        assert identity_coherence._determine_overlap_intensity(4) == "intense"


# =============================================================================
# Description Generation Tests
# =============================================================================


class TestGenerateDescription:
    def test_stable_no_sources(self):
        desc = identity_coherence._generate_description("stable", [])
        assert "同じ場所にいるように感じられる" in desc

    def test_stable_ignores_sources(self):
        desc = identity_coherence._generate_description("stable", ["temporal_difference"])
        assert "同じ場所にいるように感じられる" in desc
        assert "ずれ" not in desc

    def test_slightly_shifting_with_source(self):
        desc = identity_coherence._generate_description(
            "slightly_shifting", ["temporal_difference"]
        )
        assert "ずれ" in desc
        assert "自己状態の変化" in desc

    def test_unsettled_with_multiple_sources(self):
        desc = identity_coherence._generate_description(
            "unsettled", ["self_image_flux", "emotional_turbulence"]
        )
        assert "つかみにくい" in desc
        assert "自己像の揺らぎ" in desc
        assert "感情の動き" in desc

    def test_disconnected_with_all_sources(self):
        desc = identity_coherence._generate_description(
            "disconnected",
            ["temporal_difference", "continuity_strain", "self_image_flux", "emotional_turbulence"],
        )
        assert "離れてしまった" in desc

    def test_no_evaluation_words(self):
        """Descriptions must not contain evaluation words."""
        for level in ("stable", "slightly_shifting", "unsettled", "disconnected"):
            desc = identity_coherence._generate_description(level, ["temporal_difference"])
            for word in ("良い", "悪い", "健全", "異常", "正常", "問題"):
                assert word not in desc, f"Found evaluation word '{word}' in {level} description"

    def test_provisional_language(self):
        """Descriptions must use provisional language."""
        for level in ("stable", "slightly_shifting", "unsettled", "disconnected"):
            desc = identity_coherence._generate_description(level, [])
            assert "感じられる" in desc, f"Missing provisional language in {level} description"


# =============================================================================
# Integration Tests (assess_coherence with mocked inputs)
# =============================================================================


class TestAssessCoherence:
    """Test assess_coherence with all input modules mocked."""

    @patch("identity_coherence.self_image_integration.integrate_self_image")
    @patch("identity_coherence.continuity_strain.evaluate_strain")
    @patch("identity_coherence.temporal_self_difference.compute_difference")
    def test_all_calm_returns_stable(self, mock_diff, mock_strain, mock_image):
        mock_diff.return_value = {"magnitude": "none"}
        mock_strain.return_value = {"level": "at_ease"}
        mock_image.return_value = {
            "stability_feeling": "grounded",
            "emotional_tone": "calm",
        }

        result = identity_coherence.assess_coherence("/tmp/test")
        assert result["coherence_level"] == "stable"
        assert result["overlap_intensity"] == "none"
        assert result["shift_sources"] == []

    @patch("identity_coherence.self_image_integration.integrate_self_image")
    @patch("identity_coherence.continuity_strain.evaluate_strain")
    @patch("identity_coherence.temporal_self_difference.compute_difference")
    def test_one_source_active(self, mock_diff, mock_strain, mock_image):
        mock_diff.return_value = {"magnitude": "noticeable"}
        mock_strain.return_value = {"level": "at_ease"}
        mock_image.return_value = {
            "stability_feeling": "grounded",
            "emotional_tone": "calm",
        }

        result = identity_coherence.assess_coherence("/tmp/test")
        assert result["coherence_level"] == "slightly_shifting"
        assert result["overlap_intensity"] == "mild"
        assert result["shift_sources"] == ["temporal_difference"]

    @patch("identity_coherence.self_image_integration.integrate_self_image")
    @patch("identity_coherence.continuity_strain.evaluate_strain")
    @patch("identity_coherence.temporal_self_difference.compute_difference")
    def test_two_sources_active(self, mock_diff, mock_strain, mock_image):
        mock_diff.return_value = {"magnitude": "significant"}
        mock_strain.return_value = {"level": "dissonant"}
        mock_image.return_value = {
            "stability_feeling": "grounded",
            "emotional_tone": "calm",
        }

        result = identity_coherence.assess_coherence("/tmp/test")
        assert result["coherence_level"] == "unsettled"
        assert result["overlap_intensity"] == "moderate"
        assert set(result["shift_sources"]) == {"temporal_difference", "continuity_strain"}

    @patch("identity_coherence.self_image_integration.integrate_self_image")
    @patch("identity_coherence.continuity_strain.evaluate_strain")
    @patch("identity_coherence.temporal_self_difference.compute_difference")
    def test_three_sources_active(self, mock_diff, mock_strain, mock_image):
        mock_diff.return_value = {"magnitude": "substantial"}
        mock_strain.return_value = {"level": "alienated"}
        mock_image.return_value = {
            "stability_feeling": "wavering",
            "emotional_tone": "calm",
        }

        result = identity_coherence.assess_coherence("/tmp/test")
        assert result["coherence_level"] == "disconnected"
        assert result["overlap_intensity"] == "intense"
        assert len(result["shift_sources"]) == 3

    @patch("identity_coherence.self_image_integration.integrate_self_image")
    @patch("identity_coherence.continuity_strain.evaluate_strain")
    @patch("identity_coherence.temporal_self_difference.compute_difference")
    def test_all_four_sources_active(self, mock_diff, mock_strain, mock_image):
        mock_diff.return_value = {"magnitude": "substantial"}
        mock_strain.return_value = {"level": "alienated"}
        mock_image.return_value = {
            "stability_feeling": "turbulent",
            "emotional_tone": "intense",
        }

        result = identity_coherence.assess_coherence("/tmp/test")
        assert result["coherence_level"] == "disconnected"
        assert result["overlap_intensity"] == "intense"
        assert len(result["shift_sources"]) == 4

    @patch("identity_coherence.self_image_integration.integrate_self_image")
    @patch("identity_coherence.continuity_strain.evaluate_strain")
    @patch("identity_coherence.temporal_self_difference.compute_difference")
    def test_all_modules_fail_returns_stable(self, mock_diff, mock_strain, mock_image):
        mock_diff.side_effect = Exception("fail")
        mock_strain.side_effect = Exception("fail")
        mock_image.side_effect = Exception("fail")

        result = identity_coherence.assess_coherence("/tmp/test")
        assert result["coherence_level"] == "stable"
        assert result["overlap_intensity"] == "none"
        assert result["shift_sources"] == []

    @patch("identity_coherence.self_image_integration.integrate_self_image")
    @patch("identity_coherence.continuity_strain.evaluate_strain")
    @patch("identity_coherence.temporal_self_difference.compute_difference")
    def test_partial_failure_still_works(self, mock_diff, mock_strain, mock_image):
        mock_diff.side_effect = Exception("fail")
        mock_strain.return_value = {"level": "dissonant"}
        mock_image.return_value = {
            "stability_feeling": "wavering",
            "emotional_tone": "calm",
        }

        result = identity_coherence.assess_coherence("/tmp/test")
        assert result["coherence_level"] == "unsettled"
        assert len(result["shift_sources"]) == 2

    @patch("identity_coherence.self_image_integration.integrate_self_image")
    @patch("identity_coherence.continuity_strain.evaluate_strain")
    @patch("identity_coherence.temporal_self_difference.compute_difference")
    def test_output_has_all_required_keys(self, mock_diff, mock_strain, mock_image):
        mock_diff.return_value = {"magnitude": "none"}
        mock_strain.return_value = {"level": "at_ease"}
        mock_image.return_value = {
            "stability_feeling": "grounded",
            "emotional_tone": "calm",
        }

        result = identity_coherence.assess_coherence("/tmp/test")
        assert "coherence_level" in result
        assert "overlap_intensity" in result
        assert "shift_sources" in result
        assert "description" in result

    @patch("identity_coherence.self_image_integration.integrate_self_image")
    @patch("identity_coherence.continuity_strain.evaluate_strain")
    @patch("identity_coherence.temporal_self_difference.compute_difference")
    def test_output_has_no_numeric_values(self, mock_diff, mock_strain, mock_image):
        mock_diff.return_value = {"magnitude": "significant"}
        mock_strain.return_value = {"level": "dissonant"}
        mock_image.return_value = {
            "stability_feeling": "wavering",
            "emotional_tone": "intense",
        }

        result = identity_coherence.assess_coherence("/tmp/test")
        # No numeric values in any output field
        assert isinstance(result["coherence_level"], str)
        assert isinstance(result["overlap_intensity"], str)
        assert isinstance(result["shift_sources"], list)
        assert isinstance(result["description"], str)
        for source in result["shift_sources"]:
            assert isinstance(source, str)

    @patch("identity_coherence.self_image_integration.integrate_self_image")
    @patch("identity_coherence.continuity_strain.evaluate_strain")
    @patch("identity_coherence.temporal_self_difference.compute_difference")
    def test_emotional_turbulence_only(self, mock_diff, mock_strain, mock_image):
        mock_diff.return_value = {"magnitude": "none"}
        mock_strain.return_value = {"level": "at_ease"}
        mock_image.return_value = {
            "stability_feeling": "grounded",
            "emotional_tone": "mixed",
        }

        result = identity_coherence.assess_coherence("/tmp/test")
        assert result["coherence_level"] == "slightly_shifting"
        assert "emotional_turbulence" in result["shift_sources"]

    @patch("identity_coherence.self_image_integration.integrate_self_image")
    @patch("identity_coherence.continuity_strain.evaluate_strain")
    @patch("identity_coherence.temporal_self_difference.compute_difference")
    def test_self_image_flux_only(self, mock_diff, mock_strain, mock_image):
        mock_diff.return_value = {"magnitude": "minimal"}
        mock_strain.return_value = {"level": "at_ease"}
        mock_image.return_value = {
            "stability_feeling": "turbulent",
            "emotional_tone": "calm",
        }

        result = identity_coherence.assess_coherence("/tmp/test")
        assert result["coherence_level"] == "slightly_shifting"
        assert "self_image_flux" in result["shift_sources"]

    @patch("identity_coherence.self_image_integration.integrate_self_image")
    @patch("identity_coherence.continuity_strain.evaluate_strain")
    @patch("identity_coherence.temporal_self_difference.compute_difference")
    def test_description_present_and_nonempty(self, mock_diff, mock_strain, mock_image):
        mock_diff.return_value = {"magnitude": "noticeable"}
        mock_strain.return_value = {"level": "unsettled"}
        mock_image.return_value = {
            "stability_feeling": "wavering",
            "emotional_tone": "stirred",
        }

        result = identity_coherence.assess_coherence("/tmp/test")
        assert len(result["description"]) > 0

    @patch("identity_coherence.self_image_integration.integrate_self_image")
    @patch("identity_coherence.continuity_strain.evaluate_strain")
    @patch("identity_coherence.temporal_self_difference.compute_difference")
    def test_coherence_level_values_are_valid(self, mock_diff, mock_strain, mock_image):
        valid_levels = {"stable", "slightly_shifting", "unsettled", "disconnected"}
        valid_intensities = {"none", "mild", "moderate", "intense"}

        for mag in ("none", "minimal", "noticeable", "significant", "substantial"):
            for level in ("at_ease", "unsettled", "dissonant", "alienated"):
                for stab in ("grounded", "mostly_settled", "wavering", "turbulent"):
                    for tone in ("calm", "muted", "stirred", "mixed", "intense"):
                        mock_diff.return_value = {"magnitude": mag}
                        mock_strain.return_value = {"level": level}
                        mock_image.return_value = {
                            "stability_feeling": stab,
                            "emotional_tone": tone,
                        }
                        result = identity_coherence.assess_coherence("/tmp/test")
                        assert result["coherence_level"] in valid_levels
                        assert result["overlap_intensity"] in valid_intensities
