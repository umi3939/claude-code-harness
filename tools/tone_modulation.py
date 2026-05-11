#!/usr/bin/env python3
"""Tone modulation for Claude Code MCP.

Computes recommended response tone based on current emotion state
(fulfillment/tension/affinity) and dynamics phase (NORMAL/PEAK/REBOUND).

Completely stateless and read-only: reads emotion state, computes tone weights,
returns result. Never modifies emotion state or persists anything.

Adapted from psyche/tone.py for Claude Code's 3-axis emotion system.
Design: design_tone_mcp.md
"""

from __future__ import annotations

import sys
import os

# Add tools directory to path for imports
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from emotion_state import load_state, AXIS_NEUTRAL
from emotion_dynamics import load_dynamics_state, check_session_reset

# --- Constants ---

TONES = ("neutral", "light", "serious", "warm", "reserved")

# Base weights before state-based modification
BASE_WEIGHTS = {
    "neutral": 1.0,
    "light": 0.3,
    "serious": 0.3,
    "warm": 0.4,
    "reserved": 0.2,
}

# Thresholds for axis influence
POSITIVE_THRESHOLD = 0.2
NEGATIVE_THRESHOLD = -0.2
HIGH_AFFINITY_THRESHOLD = 0.3

# Dynamics phase amplification/suppression
PEAK_AMPLIFY = 1.3
REBOUND_SUPPRESS = 0.7

# Minimum weight floor (before normalization)
MIN_WEIGHT = 0.05

# --- Descriptions ---

TONE_DESCRIPTIONS = {
    "neutral": "バランスの取れた通常のトーン",
    "light": "軽やかで遊び心のあるトーン",
    "serious": "真面目で慎重なトーン",
    "warm": "温かく優しいトーン",
    "reserved": "控えめで最小限のトーン",
}


def compute_tone(memory_dir: str) -> dict:
    """Compute tone bias from current emotion state and dynamics phase.

    Args:
        memory_dir: Path to memory directory containing emotion state files.

    Returns:
        Dict with:
            primary_tone: str - most recommended tone
            tone_weights: dict - normalized weights for each tone (sum = 1.0)
            description: str - Japanese description of the recommended tone
    """
    # Load emotion state (read-only)
    try:
        state = load_state(memory_dir)
        fulfillment = float(state.get("fulfillment", AXIS_NEUTRAL))
        tension = float(state.get("tension", AXIS_NEUTRAL))
        affinity = float(state.get("affinity", AXIS_NEUTRAL))
    except Exception:
        # Fallback: equal weights
        return _equal_weights_result()

    # Load dynamics phase (read-only)
    try:
        dynamics = load_dynamics_state(memory_dir)
        dynamics = check_session_reset(dynamics)
        phase = dynamics.get("phase", "normal")
    except Exception:
        phase = "normal"

    # Start with base weights
    weights = dict(BASE_WEIGHTS)

    # --- Emotion-based modulation ---

    # Fulfillment + Tension combined effect
    if fulfillment > POSITIVE_THRESHOLD and tension < POSITIVE_THRESHOLD:
        # Positive fulfillment, low tension -> light/warm
        factor = 1.0 + fulfillment
        weights["light"] *= factor
        weights["warm"] *= (1.0 + fulfillment * 0.6)
    elif fulfillment < NEGATIVE_THRESHOLD and tension > POSITIVE_THRESHOLD:
        # Low fulfillment, high tension -> serious/reserved
        factor = 1.0 + abs(fulfillment) + tension
        weights["serious"] *= factor
        weights["reserved"] *= (1.0 + tension * 0.5)

    # Fulfillment alone
    if fulfillment > POSITIVE_THRESHOLD:
        weights["light"] *= (1.0 + fulfillment * 0.5)
    elif fulfillment < NEGATIVE_THRESHOLD:
        weights["serious"] *= (1.0 + abs(fulfillment) * 0.5)

    # Tension alone
    if tension > POSITIVE_THRESHOLD:
        weights["serious"] *= (1.0 + tension * 0.4)
        weights["reserved"] *= (1.0 + tension * 0.3)
        weights["light"] *= max(0.3, 1.0 - tension * 0.5)
    elif tension < NEGATIVE_THRESHOLD:
        # Low tension (relaxed) -> light/warm
        weights["light"] *= (1.0 + abs(tension) * 0.3)
        weights["warm"] *= (1.0 + abs(tension) * 0.2)

    # Affinity effect
    if affinity > HIGH_AFFINITY_THRESHOLD:
        weights["warm"] *= (1.0 + affinity * 0.8)
        weights["light"] *= (1.0 + affinity * 0.3)
    elif affinity < NEGATIVE_THRESHOLD:
        weights["reserved"] *= (1.0 + abs(affinity) * 0.5)

    # Combined: high fulfillment + low tension + high affinity -> extra light/warm
    if (fulfillment > POSITIVE_THRESHOLD
            and tension < POSITIVE_THRESHOLD
            and affinity > POSITIVE_THRESHOLD):
        weights["light"] *= 1.2
        weights["warm"] *= 1.2

    # --- Dynamics phase modulation ---

    if phase == "peak":
        # Amplify the dominant tone
        max_tone = max(weights, key=weights.get)
        weights[max_tone] *= PEAK_AMPLIFY
    elif phase == "rebound":
        # Suppress everything except neutral
        for tone in TONES:
            if tone != "neutral":
                weights[tone] *= REBOUND_SUPPRESS
        weights["neutral"] *= 1.2

    # --- Floor and normalize ---
    weights = _normalize(weights)

    # Determine primary tone
    primary = max(weights, key=weights.get)

    # Generate description
    description = _generate_description(primary, weights, fulfillment, tension, affinity, phase)

    return {
        "primary_tone": primary,
        "tone_weights": weights,
        "description": description,
    }


def _normalize(weights: dict) -> dict:
    """Apply minimum floor and normalize weights to sum to 1.0."""
    # Apply floor
    for tone in TONES:
        weights[tone] = max(MIN_WEIGHT, weights.get(tone, MIN_WEIGHT))

    # Normalize
    total = sum(weights[t] for t in TONES)
    if total <= 0:
        # Shouldn't happen with floor, but safety
        return {t: 1.0 / len(TONES) for t in TONES}

    return {t: round(weights[t] / total, 4) for t in TONES}


def _equal_weights_result() -> dict:
    """Return equal weights when emotion state is unavailable."""
    equal = {t: round(1.0 / len(TONES), 4) for t in TONES}
    return {
        "primary_tone": "neutral",
        "tone_weights": equal,
        "description": "感情状態を読み取れないため、均等な重みを返しています。",
    }


def _generate_description(
    primary: str,
    weights: dict,
    fulfillment: float,
    tension: float,
    affinity: float,
    phase: str,
) -> str:
    """Generate a Japanese description of the recommended tone."""
    tone_desc = TONE_DESCRIPTIONS.get(primary, primary)

    # Build context fragments
    parts = [f"推奨トーン: {tone_desc}。"]

    # Emotion context
    if fulfillment > 0.3:
        parts.append("充実感が高い状態。")
    elif fulfillment < -0.3:
        parts.append("停滞感がある状態。")

    if tension > 0.3:
        parts.append("緊張度が高い。")
    elif tension < -0.3:
        parts.append("リラックスしている。")

    if affinity > 0.3:
        parts.append("親和性が高い。")
    elif affinity < -0.3:
        parts.append("距離感がある。")

    # Dynamics context
    if phase == "peak":
        parts.append("感情の高まりにより、トーン傾向が増幅されています。")
    elif phase == "rebound":
        parts.append("反動期のため、ニュートラル寄りに抑制されています。")

    # Secondary tone if close
    sorted_tones = sorted(weights.items(), key=lambda x: -x[1])
    if len(sorted_tones) >= 2:
        second_tone, second_weight = sorted_tones[1]
        primary_weight = sorted_tones[0][1]
        if second_weight > primary_weight * 0.8:
            second_desc = TONE_DESCRIPTIONS.get(second_tone, second_tone)
            parts.append(f"副次的に「{second_desc}」の傾向もあります。")

    return "".join(parts)
