#!/usr/bin/env python3
"""Stability valve for Claude Code's emotion system.

Monitors three extremity indicators and produces a dampening_factor
that scales emotion_react's amplitude_modifier to prevent runaway states.

Indicators:
1. emotion_saturation: any axis abs >= 0.8
2. change_fixation: recent changes all push same axis same direction
3. dynamics_stagnation: PEAK or REBOUND phase exceeds expected duration

The valve is stateless: each call reads current state from files,
computes extremity, and returns a dampening factor.

Design: design_stability_valve_mcp.md
Adapted from: psyche/stability_valve.py
"""

import os
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from emotion_state import (
    load_state,
    ALL_AXES,
    AXIS_NEUTRAL,
    _load_change_log,
)
from emotion_dynamics import (
    load_dynamics_state,
    PEAK_DURATION,
    REBOUND_DURATION,
)


# --- Constants ---

# Saturation threshold: abs(axis_value) >= this is considered saturated
SATURATION_THRESHOLD = 0.8

# Number of recent change_log entries to check for fixation
FIXATION_WINDOW = 5

# Minimum dampening factor (never fully suppress)
DAMPENING_MIN = 0.3

# Dampening scale: dampening = 1.0 - max_extremity * DAMPENING_SCALE
DAMPENING_SCALE = 0.7


# --- Indicator: Emotion Saturation ---

def _compute_emotion_saturation(state: dict) -> float:
    """Compute emotion saturation extremity (0.0 - 1.0).

    Returns how far the most extreme axis exceeds the saturation threshold.
    0.0 = no axis is saturated.
    1.0 = at least one axis is at maximum (±1.0).
    """
    max_abs = 0.0
    for axis in ALL_AXES:
        val = state.get(axis, AXIS_NEUTRAL)
        if isinstance(val, (int, float)):
            max_abs = max(max_abs, abs(float(val)))

    if max_abs < SATURATION_THRESHOLD:
        return 0.0

    # Normalize: 0.8 -> 0.0, 1.0 -> 1.0
    return min(1.0, (max_abs - SATURATION_THRESHOLD) / (1.0 - SATURATION_THRESHOLD))


# --- Indicator: Change Fixation ---

def _compute_change_fixation(change_log: list) -> float:
    """Compute change fixation extremity (0.0 - 1.0).

    Checks if the most recent FIXATION_WINDOW entries all move the same
    axis in the same direction. Returns 1.0 if all entries agree,
    scaled down proportionally if fewer agree.
    """
    if len(change_log) < FIXATION_WINDOW:
        return 0.0

    recent = change_log[-FIXATION_WINDOW:]

    # For each entry, determine which axis changed the most and its direction
    moves = []
    for entry in recent:
        before = entry.get("before", {})
        after = entry.get("after", {})

        best_axis = None
        best_delta = 0.0
        for axis in ALL_AXES:
            b = before.get(axis, 0.0)
            a = after.get(axis, 0.0)
            if isinstance(b, (int, float)) and isinstance(a, (int, float)):
                delta = float(a) - float(b)
                if abs(delta) > abs(best_delta):
                    best_delta = delta
                    best_axis = axis

        if best_axis is not None and abs(best_delta) > 1e-6:
            direction = "+" if best_delta > 0 else "-"
            moves.append((best_axis, direction))

    if len(moves) < FIXATION_WINDOW:
        return 0.0

    # Count how many agree with the most common (axis, direction) pair
    from collections import Counter
    counts = Counter(moves)
    most_common_count = counts.most_common(1)[0][1]

    # All same = 1.0, threshold at FIXATION_WINDOW-1 for partial
    if most_common_count < FIXATION_WINDOW - 1:
        return 0.0

    return most_common_count / FIXATION_WINDOW


# --- Indicator: Dynamics Stagnation ---

def _compute_dynamics_stagnation(dynamics_state: dict) -> float:
    """Compute dynamics stagnation extremity (0.0 - 1.0).

    Returns how far the current PEAK or REBOUND phase has exceeded
    its expected duration.
    """
    phase = dynamics_state.get("phase", "normal")
    call_count = dynamics_state.get("phase_call_count", 0)

    if phase == "peak":
        expected = PEAK_DURATION
    elif phase == "rebound":
        expected = REBOUND_DURATION
    else:
        return 0.0

    if not isinstance(call_count, int) or call_count <= expected:
        return 0.0

    # Overshoot: how many calls past expected
    overshoot = call_count - expected
    # Normalize: 1 call over = 0.33, 2 over = 0.67, 3+ over = 1.0
    return min(1.0, overshoot / 3.0)


# --- Main Functions ---

def check_stability(memory_dir: str) -> dict:
    """Observe extremity indicators and return stability bias.

    Reads current emotion state, change log, and dynamics state.
    Computes three extremity indicators and derives a dampening factor.

    Returns:
        {
            "indicators": {
                "emotion_saturation": float,  # 0.0-1.0
                "change_fixation": float,     # 0.0-1.0
                "dynamics_stagnation": float, # 0.0-1.0
            },
            "overall_extremity": float,  # 0.0-1.0
            "dampening_factor": float,   # 0.3-1.0
            "is_active": bool,           # dampening < 1.0
            "description": str,          # Japanese description
        }
    """
    # Load external state (READ-ONLY)
    emotion_state = load_state(memory_dir)
    change_log = _load_change_log(memory_dir)
    dynamics_state = load_dynamics_state(memory_dir)

    # Compute indicators
    saturation = _compute_emotion_saturation(emotion_state)
    fixation = _compute_change_fixation(change_log)
    stagnation = _compute_dynamics_stagnation(dynamics_state)

    # Overall: max of all indicators
    overall = max(saturation, fixation, stagnation)

    # Dampening factor
    dampening = max(DAMPENING_MIN, 1.0 - overall * DAMPENING_SCALE)

    is_active = dampening < 1.0

    # Build description
    desc = _build_description(saturation, fixation, stagnation, dampening, is_active)

    return {
        "indicators": {
            "emotion_saturation": round(saturation, 4),
            "change_fixation": round(fixation, 4),
            "dynamics_stagnation": round(stagnation, 4),
        },
        "overall_extremity": round(overall, 4),
        "dampening_factor": round(dampening, 4),
        "is_active": is_active,
        "description": desc,
    }


def get_dampening_factor(memory_dir: str) -> float:
    """Lightweight version: return only the dampening factor.

    Called by emotion_react to get the dampening without full report.
    """
    emotion_state = load_state(memory_dir)
    change_log = _load_change_log(memory_dir)
    dynamics_state = load_dynamics_state(memory_dir)

    saturation = _compute_emotion_saturation(emotion_state)
    fixation = _compute_change_fixation(change_log)
    stagnation = _compute_dynamics_stagnation(dynamics_state)

    overall = max(saturation, fixation, stagnation)
    return round(max(DAMPENING_MIN, 1.0 - overall * DAMPENING_SCALE), 4)


def _build_description(
    saturation: float,
    fixation: float,
    stagnation: float,
    dampening: float,
    is_active: bool,
) -> str:
    """Build a Japanese description of the stability check result."""
    if not is_active:
        return "安定化バルブ: 非活性（極端性なし、抑制なし）"

    active_indicators = []
    if saturation > 0.0:
        active_indicators.append(f"感情飽和={saturation:.2f}")
    if fixation > 0.0:
        active_indicators.append(f"変化固定={fixation:.2f}")
    if stagnation > 0.0:
        active_indicators.append(f"動力学停滞={stagnation:.2f}")

    indicators_str = ", ".join(active_indicators)
    return (
        f"安定化バルブ: 活性（dampening={dampening:.2f}）"
        f" — 検出: {indicators_str}"
    )
