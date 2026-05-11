#!/usr/bin/env python3
"""Emotion reaction module for Claude Code's memory system.

Derives 3-axis emotion deltas (fulfillment, tension, affinity) from
conversation perceptual attributes (emotion label, valence, intent).

Adapted from psyche/reaction.py for Claude Code MCP use.
This module is stateless: each call computes deltas from current state
and perceptual inputs without accumulation.

Design: design_emotion_reaction_mcp.md
"""

from emotion_state import CONVERGENCE_SCALE, CONVERGENCE_THRESHOLD

# --- Constants ---

# Maximum delta per axis per reaction call
DELTA_CAP = 0.3

# Emotion label -> 3-axis base deltas
# Adapted from psyche/reaction.py _EMOTION_MAP, mapped to 3-axis system
_EMOTION_BASE_DELTAS: dict[str, dict[str, float]] = {
    "happy":     {"fulfillment": +0.15, "tension": -0.10, "affinity": +0.10},
    "sad":       {"fulfillment": -0.15, "tension": +0.10, "affinity":  0.00},
    "angry":     {"fulfillment": -0.10, "tension": +0.15, "affinity": -0.10},
    "surprised": {"fulfillment": +0.05, "tension": +0.05, "affinity":  0.00},
    "scared":    {"fulfillment": -0.10, "tension": +0.15, "affinity": -0.05},
    "loving":    {"fulfillment": +0.15, "tension": -0.10, "affinity": +0.15},
    "teasing":   {"fulfillment": +0.05, "tension": -0.05, "affinity": +0.10},
    "neutral":   {"fulfillment":  0.00, "tension":  0.00, "affinity":  0.00},
}

# Valence secondary effects (scaled by abs(valence))
_VALENCE_POSITIVE = {"fulfillment": +0.05, "tension": -0.03}
_VALENCE_NEGATIVE = {"fulfillment": -0.05, "tension": +0.03}

# Intent adjustments
_INTENT_ADJUSTMENTS: dict[str, dict[str, float]] = {
    "sharing":    {"affinity": +0.05},
    "question":   {"affinity": +0.05},
    "expression": {"fulfillment": +0.05},
    "greeting":   {"affinity": +0.05, "tension": -0.03},
    "farewell":   {},
}

ALL_AXES = ("fulfillment", "tension", "affinity")


def _apply_convergence_suppression(
    delta: float,
    current_value: float,
) -> float:
    """Reduce delta when current value is already high in the same direction.

    Uses the same structure as emotion_state.py's convergence logic:
    when abs(current_value) > CONVERGENCE_THRESHOLD and delta pushes
    further in the same direction, scale delta down.
    """
    if abs(current_value) <= CONVERGENCE_THRESHOLD:
        return delta

    # Same direction check
    same_direction = (current_value > 0 and delta > 0) or (current_value < 0 and delta < 0)
    if not same_direction:
        return delta

    convergence = 1.0 - (abs(current_value) - CONVERGENCE_THRESHOLD) * CONVERGENCE_SCALE
    convergence = max(0.1, convergence)
    return delta * convergence


def react(
    emotion_label: str,
    emotion_valence: float,
    intent: str,
    current_state: dict,
    amplitude_modifier: float = 1.0,
) -> dict:
    """Derive 3-axis deltas from perceptual attributes.

    Args:
        emotion_label: Perceived emotion (happy, sad, angry, surprised,
                       scared, loving, teasing, neutral).
        emotion_valence: Emotion valence (-1.0 to +1.0).
        intent: Conversation intent (sharing, question, expression,
                greeting, farewell, or other).
        current_state: Dict with current {fulfillment, tension, affinity} values.
        amplitude_modifier: Scales delta magnitude without changing direction.
                           Default 1.0.

    Returns:
        Dict with {fulfillment: float, tension: float, affinity: float} delta values.
    """
    deltas = {axis: 0.0 for axis in ALL_AXES}

    # --- Stage 1: Emotion label -> base deltas ---
    base = _EMOTION_BASE_DELTAS.get(emotion_label, _EMOTION_BASE_DELTAS["neutral"])
    for axis in ALL_AXES:
        deltas[axis] += base.get(axis, 0.0)

    # --- Stage 2: Valence secondary effects ---
    valence = max(-1.0, min(1.0, emotion_valence))
    if valence > 0:
        for axis, weight in _VALENCE_POSITIVE.items():
            deltas[axis] += valence * weight
    elif valence < 0:
        for axis, weight in _VALENCE_NEGATIVE.items():
            deltas[axis] += abs(valence) * weight

    # --- Stage 3: Intent adjustments ---
    intent_adj = _INTENT_ADJUSTMENTS.get(intent, {})
    for axis, adj in intent_adj.items():
        deltas[axis] += adj

    # --- Safety valves ---

    # Clamp amplitude_modifier to [0.0, 5.0] to prevent direction reversal or extreme scaling
    amplitude_modifier = max(0.0, min(5.0, amplitude_modifier))

    # Apply amplitude_modifier (scales magnitude, not direction)
    for axis in ALL_AXES:
        if amplitude_modifier != 1.0:
            deltas[axis] *= amplitude_modifier

    # Apply convergence suppression
    for axis in ALL_AXES:
        current_val = current_state.get(axis, 0.0)
        if isinstance(current_val, (int, float)):
            deltas[axis] = _apply_convergence_suppression(deltas[axis], float(current_val))

    # Clamp each axis delta to [-DELTA_CAP, +DELTA_CAP]
    for axis in ALL_AXES:
        deltas[axis] = max(-DELTA_CAP, min(DELTA_CAP, deltas[axis]))

    return deltas
