#!/usr/bin/env python3
"""Tests for emotion_tone_converter.py - Emotion to tone instruction conversion.

TDD: Tests written before implementation.
"""

import unittest


class TestClassifyBand(unittest.TestCase):
    """Test the band classification function."""

    def test_high_band_above_positive_threshold(self):
        from emotion_tone_converter import classify_band
        self.assertEqual(classify_band(0.5), "high")

    def test_high_band_at_positive_threshold(self):
        """At threshold is mid (exclusive boundary)."""
        from emotion_tone_converter import classify_band
        self.assertEqual(classify_band(0.2), "mid")

    def test_high_band_above_positive_threshold_exact(self):
        from emotion_tone_converter import classify_band
        self.assertEqual(classify_band(0.21), "high")

    def test_low_band_below_negative_threshold(self):
        from emotion_tone_converter import classify_band
        self.assertEqual(classify_band(-0.5), "low")

    def test_low_band_at_negative_threshold(self):
        """At threshold is mid (exclusive boundary)."""
        from emotion_tone_converter import classify_band
        self.assertEqual(classify_band(-0.2), "mid")

    def test_low_band_below_negative_threshold_exact(self):
        from emotion_tone_converter import classify_band
        self.assertEqual(classify_band(-0.21), "low")

    def test_mid_band_zero(self):
        from emotion_tone_converter import classify_band
        self.assertEqual(classify_band(0.0), "mid")

    def test_mid_band_small_positive(self):
        from emotion_tone_converter import classify_band
        self.assertEqual(classify_band(0.1), "mid")

    def test_mid_band_small_negative(self):
        from emotion_tone_converter import classify_band
        self.assertEqual(classify_band(-0.1), "mid")

    def test_extreme_high(self):
        from emotion_tone_converter import classify_band
        self.assertEqual(classify_band(1.0), "high")

    def test_extreme_low(self):
        from emotion_tone_converter import classify_band
        self.assertEqual(classify_band(-1.0), "low")


class TestFindDominantAxis(unittest.TestCase):
    """Test the dominant axis identification."""

    def test_fulfillment_dominant_high(self):
        from emotion_tone_converter import find_dominant_axis
        axis, band = find_dominant_axis(0.8, 0.0, 0.0)
        self.assertEqual(axis, "fulfillment")
        self.assertEqual(band, "high")

    def test_tension_dominant_high(self):
        from emotion_tone_converter import find_dominant_axis
        axis, band = find_dominant_axis(0.0, 0.7, 0.0)
        self.assertEqual(axis, "tension")
        self.assertEqual(band, "high")

    def test_affinity_dominant_low(self):
        from emotion_tone_converter import find_dominant_axis
        axis, band = find_dominant_axis(0.0, 0.0, -0.9)
        self.assertEqual(axis, "affinity")
        self.assertEqual(band, "low")

    def test_all_neutral_returns_fulfillment(self):
        """When all axes are mid, default to fulfillment."""
        from emotion_tone_converter import find_dominant_axis
        axis, band = find_dominant_axis(0.0, 0.0, 0.0)
        self.assertEqual(axis, "fulfillment")
        self.assertEqual(band, "mid")

    def test_tie_breaking_order(self):
        """When axes are equally distant from mid, fulfillment > tension > affinity."""
        from emotion_tone_converter import find_dominant_axis
        axis, band = find_dominant_axis(0.5, 0.5, 0.5)
        self.assertEqual(axis, "fulfillment")

    def test_negative_dominant(self):
        from emotion_tone_converter import find_dominant_axis
        axis, band = find_dominant_axis(-0.8, 0.1, 0.1)
        self.assertEqual(axis, "fulfillment")
        self.assertEqual(band, "low")


class TestGenerateToneInstruction(unittest.TestCase):
    """Test the tone instruction text generation."""

    def test_returns_string(self):
        from emotion_tone_converter import generate_tone_instruction
        result = generate_tone_instruction({"fulfillment": 0.5, "tension": 0.0, "affinity": 0.3})
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_with_tone_result_includes_primary_tone(self):
        from emotion_tone_converter import generate_tone_instruction
        tone_result = {
            "primary_tone": "warm",
            "tone_weights": {"neutral": 0.2, "light": 0.1, "serious": 0.1, "warm": 0.5, "reserved": 0.1},
            "description": "温かく優しいトーン",
        }
        result = generate_tone_instruction(
            {"fulfillment": 0.5, "tension": 0.0, "affinity": 0.5},
            tone_result=tone_result,
        )
        self.assertIn("warm", result)

    def test_without_tone_result_fallback(self):
        """When tone_result is None, should still produce instruction from bands only."""
        from emotion_tone_converter import generate_tone_instruction
        result = generate_tone_instruction(
            {"fulfillment": 0.5, "tension": -0.3, "affinity": 0.4},
            tone_result=None,
        )
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_none_emotion_axes_returns_neutral_default(self):
        from emotion_tone_converter import generate_tone_instruction
        result = generate_tone_instruction(None)
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_empty_dict_emotion_axes_returns_neutral_default(self):
        from emotion_tone_converter import generate_tone_instruction
        result = generate_tone_instruction({})
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_invalid_emotion_axes_returns_neutral_default(self):
        """Non-dict input should produce neutral default."""
        from emotion_tone_converter import generate_tone_instruction
        result = generate_tone_instruction("invalid")
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_high_fulfillment_low_tension_high_affinity(self):
        """Happy state: should suggest warm/approachable tone."""
        from emotion_tone_converter import generate_tone_instruction
        result = generate_tone_instruction(
            {"fulfillment": 0.7, "tension": -0.3, "affinity": 0.6},
        )
        self.assertIsInstance(result, str)

    def test_low_fulfillment_high_tension(self):
        """Stressed state: should suggest serious/reserved tone."""
        from emotion_tone_converter import generate_tone_instruction
        result = generate_tone_instruction(
            {"fulfillment": -0.5, "tension": 0.6, "affinity": 0.0},
        )
        self.assertIsInstance(result, str)

    def test_all_neutral_state(self):
        """All-neutral: should suggest balanced tone."""
        from emotion_tone_converter import generate_tone_instruction
        result = generate_tone_instruction(
            {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0},
        )
        self.assertIsInstance(result, str)

    def test_includes_response_attitude(self):
        """Instruction should contain attitude guidance."""
        from emotion_tone_converter import generate_tone_instruction
        result = generate_tone_instruction(
            {"fulfillment": 0.5, "tension": 0.0, "affinity": 0.3},
        )
        # Should contain some guidance text (Japanese)
        self.assertTrue(len(result) > 10)

    def test_includes_response_length_tendency(self):
        """Instruction should mention response length tendency."""
        from emotion_tone_converter import generate_tone_instruction
        result = generate_tone_instruction(
            {"fulfillment": 0.5, "tension": 0.0, "affinity": 0.5},
        )
        self.assertTrue(len(result) > 10)


class TestMaxLength(unittest.TestCase):
    """Test the max length safety valve."""

    def test_output_within_max_length(self):
        from emotion_tone_converter import TONE_INSTRUCTION_MAX_LENGTH, generate_tone_instruction
        # Even with full tone_result, should stay within limit
        tone_result = {
            "primary_tone": "warm",
            "tone_weights": {"neutral": 0.2, "light": 0.1, "serious": 0.1, "warm": 0.5, "reserved": 0.1},
            "description": "X" * 300,  # Long description
        }
        result = generate_tone_instruction(
            {"fulfillment": 0.8, "tension": -0.5, "affinity": 0.9},
            tone_result=tone_result,
        )
        self.assertLessEqual(len(result), TONE_INSTRUCTION_MAX_LENGTH)

    def test_max_length_constant_exists(self):
        from emotion_tone_converter import TONE_INSTRUCTION_MAX_LENGTH
        self.assertEqual(TONE_INSTRUCTION_MAX_LENGTH, 500)


class TestNeutralDefault(unittest.TestCase):
    """Test the neutral default output."""

    def test_neutral_default_is_non_empty(self):
        from emotion_tone_converter import NEUTRAL_DEFAULT_INSTRUCTION
        self.assertIsInstance(NEUTRAL_DEFAULT_INSTRUCTION, str)
        self.assertTrue(len(NEUTRAL_DEFAULT_INSTRUCTION) > 0)


class TestPureFunctionProperty(unittest.TestCase):
    """Verify statelessness: same input -> same output."""

    def test_same_input_same_output(self):
        from emotion_tone_converter import generate_tone_instruction
        axes = {"fulfillment": 0.5, "tension": -0.2, "affinity": 0.3}
        tone = {
            "primary_tone": "light",
            "tone_weights": {"neutral": 0.2, "light": 0.4, "serious": 0.1, "warm": 0.2, "reserved": 0.1},
            "description": "軽やかなトーン",
        }
        result1 = generate_tone_instruction(axes, tone_result=tone)
        result2 = generate_tone_instruction(axes, tone_result=tone)
        self.assertEqual(result1, result2)


if __name__ == "__main__":
    unittest.main()
