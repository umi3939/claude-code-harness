#!/usr/bin/env python3
"""Emotion to tone instruction converter.

Converts emotion 3-axis values (fulfillment/tension/affinity) and
optional tone computation results into tone instruction text for
Discord response prompt injection.

Design: docs/design_c20_2_emotion_tone.md
Plan: docs/plan_c20_2_emotion_tone.md

Properties:
- Completely stateless (pure function)
- Read-only (never modifies emotion state)
- No persistence (results are ephemeral)
- Fail-open (returns neutral default on any failure)
"""

import logging

logger = logging.getLogger(__name__)

# --- Constants ---

# Thresholds aligned with tone_modulation.py
POSITIVE_THRESHOLD = 0.2
NEGATIVE_THRESHOLD = -0.2

# Max length for tone instruction text (safety valve)
TONE_INSTRUCTION_MAX_LENGTH = 500

# Band names
BAND_HIGH = "high"
BAND_MID = "mid"
BAND_LOW = "low"

# Axis priority order for tie-breaking
AXIS_PRIORITY = ("fulfillment", "tension", "affinity")

# Neutral default instruction (fallback for all failures)
NEUTRAL_DEFAULT_INSTRUCTION = (
    "推奨トーン: バランスの取れた通常のトーン。\n"
    "応答態度: 落ち着いて、自然体で応答する。\n"
    "応答長: 通常の長さで応答する。"
)

# --- Attitude instruction table ---
# Keyed by (dominant_axis, dominant_band)
_ATTITUDE_TABLE = {
    ("fulfillment", BAND_HIGH): "前向きな姿勢で、積極的に応答する。",
    ("fulfillment", BAND_MID): "落ち着いて、自然体で応答する。",
    ("fulfillment", BAND_LOW): "慎重に、控えめに応答する。",
    ("tension", BAND_HIGH): "簡潔に、要点を絞って応答する。",
    ("tension", BAND_MID): "落ち着いて、自然体で応答する。",
    ("tension", BAND_LOW): "穏やかに、リラックスした調子で応答する。",
    ("affinity", BAND_HIGH): "温かく、丁寧に応答する。",
    ("affinity", BAND_MID): "落ち着いて、自然体で応答する。",
    ("affinity", BAND_LOW): "控えめに、最小限の応答にとどめる。",
}

# --- Response length tendency table ---
_LENGTH_TABLE = {
    ("fulfillment", BAND_HIGH): "通常〜やや長めの応答が自然です。",
    ("fulfillment", BAND_MID): "通常の長さで応答する。",
    ("fulfillment", BAND_LOW): "やや短めの応答が自然です。",
    ("tension", BAND_HIGH): "簡潔な応答が自然です。",
    ("tension", BAND_MID): "通常の長さで応答する。",
    ("tension", BAND_LOW): "通常〜やや長めの応答が自然です。",
    ("affinity", BAND_HIGH): "通常〜やや長めの応答が自然です。",
    ("affinity", BAND_MID): "通常の長さで応答する。",
    ("affinity", BAND_LOW): "短めの応答が自然です。",
}

# Default length/attitude for unknown combinations
_DEFAULT_ATTITUDE = "落ち着いて、自然体で応答する。"
_DEFAULT_LENGTH = "通常の長さで応答する。"


# --- Core functions ---

def classify_band(
    value: float,
    positive_threshold: float = POSITIVE_THRESHOLD,
    negative_threshold: float = NEGATIVE_THRESHOLD,
) -> str:
    """Classify an axis value into high/mid/low band.

    Args:
        value: Axis value (-1.0 to +1.0).
        positive_threshold: Upper boundary for mid band (exclusive).
        negative_threshold: Lower boundary for mid band (exclusive).

    Returns:
        "high", "mid", or "low".
    """
    if value > positive_threshold:
        return BAND_HIGH
    elif value < negative_threshold:
        return BAND_LOW
    else:
        return BAND_MID


def find_dominant_axis(
    fulfillment: float,
    tension: float,
    affinity: float,
) -> tuple[str, str]:
    """Find the dominant axis (most distant from mid band).

    Args:
        fulfillment: Fulfillment axis value.
        tension: Tension axis value.
        affinity: Affinity axis value.

    Returns:
        Tuple of (axis_name, band). Tie-breaking order:
        fulfillment > tension > affinity.
    """
    axes = [
        ("fulfillment", fulfillment),
        ("tension", tension),
        ("affinity", affinity),
    ]

    best_axis = "fulfillment"
    best_distance = 0.0

    for name, value in axes:
        distance = abs(value)
        if distance > best_distance:
            best_distance = distance
            best_axis = name
            # No need to store value, we recalculate band below

    # Find the value for the best axis
    value_map = {"fulfillment": fulfillment, "tension": tension, "affinity": affinity}
    best_value = value_map[best_axis]
    best_band = classify_band(best_value)

    return best_axis, best_band


def generate_tone_instruction(
    emotion_axes: dict | None,
    tone_result: dict | None = None,
) -> str:
    """Generate tone instruction text from emotion axes and optional tone result.

    Args:
        emotion_axes: Dict with fulfillment/tension/affinity values.
                      None or invalid -> neutral default.
        tone_result: Optional dict from tone_modulation.compute_tone().
                     Contains primary_tone, tone_weights, description.
                     None -> fallback to band classification only.

    Returns:
        Tone instruction text string, within TONE_INSTRUCTION_MAX_LENGTH.
    """
    # Validate emotion_axes
    if not isinstance(emotion_axes, dict):
        return NEUTRAL_DEFAULT_INSTRUCTION

    try:
        fulfillment = float(emotion_axes.get("fulfillment", 0.0))
        tension = float(emotion_axes.get("tension", 0.0))
        affinity = float(emotion_axes.get("affinity", 0.0))
    except (TypeError, ValueError):
        return NEUTRAL_DEFAULT_INSTRUCTION

    # If all axes are missing keys, return neutral
    if not any(k in emotion_axes for k in ("fulfillment", "tension", "affinity")):
        return NEUTRAL_DEFAULT_INSTRUCTION

    # Stage 1: Band classification + dominant axis
    dominant_axis, dominant_band = find_dominant_axis(fulfillment, tension, affinity)

    # Stage 2: Build tone instruction text
    parts = []

    # 2a: Recommended tone name
    if tone_result and isinstance(tone_result, dict):
        primary_tone = tone_result.get("primary_tone", "neutral")
        description = tone_result.get("description", "")
        parts.append(f"推奨トーン: {primary_tone}")
        if description:
            # Truncate description to prevent exceeding max length
            desc_max = 200
            if len(description) > desc_max:
                description = description[:desc_max]
            parts.append(f"トーン詳細: {description}")
    else:
        # Fallback: derive tone name from band classification
        tone_name = _derive_tone_from_bands(dominant_axis, dominant_band)
        parts.append(f"推奨トーン: {tone_name}")

    # 2b: Response attitude instruction
    attitude = _ATTITUDE_TABLE.get(
        (dominant_axis, dominant_band), _DEFAULT_ATTITUDE
    )
    parts.append(f"応答態度: {attitude}")

    # 2c: Response length tendency
    length_tendency = _LENGTH_TABLE.get(
        (dominant_axis, dominant_band), _DEFAULT_LENGTH
    )
    parts.append(f"応答長: {length_tendency}")

    result = "\n".join(parts)

    # Safety valve: truncate if exceeding max length
    if len(result) > TONE_INSTRUCTION_MAX_LENGTH:
        result = result[:TONE_INSTRUCTION_MAX_LENGTH]

    return result


def _derive_tone_from_bands(dominant_axis: str, dominant_band: str) -> str:
    """Derive a tone name from band classification when compute_tone is unavailable.

    Args:
        dominant_axis: The most influential axis name.
        dominant_band: The band of the dominant axis.

    Returns:
        A Japanese tone description string.
    """
    tone_map = {
        ("fulfillment", BAND_HIGH): "軽やかで前向きなトーン",
        ("fulfillment", BAND_MID): "バランスの取れた通常のトーン",
        ("fulfillment", BAND_LOW): "慎重で控えめなトーン",
        ("tension", BAND_HIGH): "真面目で簡潔なトーン",
        ("tension", BAND_MID): "バランスの取れた通常のトーン",
        ("tension", BAND_LOW): "穏やかでリラックスしたトーン",
        ("affinity", BAND_HIGH): "温かく親しみやすいトーン",
        ("affinity", BAND_MID): "バランスの取れた通常のトーン",
        ("affinity", BAND_LOW): "控えめで距離感のあるトーン",
    }
    return tone_map.get(
        (dominant_axis, dominant_band),
        "バランスの取れた通常のトーン",
    )
