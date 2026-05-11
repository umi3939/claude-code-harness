#!/usr/bin/env python3
"""Emotion dynamics module: Peak & Rebound for Claude Code MCP.

Tracks emotional change accumulation and manages 3-phase state machine:
NORMAL -> PEAK -> REBOUND -> NORMAL

Adapted from psyche/dynamics.py for Claude Code's 3-axis emotion system.
This module modifies emotion_react's amplitude_modifier only;
it never directly changes emotion values.

Design: design_emotion_dynamics_mcp.md
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


# --- Constants (design doc defaults) ---

ACCUMULATION_WINDOW = 5
ACCUMULATION_THRESHOLD = 0.8
PEAK_AMPLITUDE = 1.3
PEAK_AMPLITUDE_MAX = 1.5
REBOUND_AMPLITUDE = 0.6
REBOUND_AMPLITUDE_MIN = 0.4
PEAK_DURATION = 3
REBOUND_DURATION = 5
SESSION_RESET_HOURS = 4.0

DYNAMICS_STATE_FILENAME = "dynamics_state.json"


class DynamicsPhase(Enum):
    """Current phase of emotional dynamics."""
    NORMAL = "normal"
    PEAK = "peak"
    REBOUND = "rebound"


# --- State helpers ---

def create_default_state() -> dict:
    """Create a default dynamics state (NORMAL phase, empty accumulation)."""
    return {
        "phase": DynamicsPhase.NORMAL.value,
        "phase_call_count": 0,
        "accumulation_history": [],
        "peak_axis": "",
        "last_updated": _now_iso(),
    }


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(ts_str: str) -> datetime | None:
    """Parse an ISO 8601 timestamp. Returns None on failure."""
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(ts_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# --- Persistence ---

def _get_state_path(memory_dir: str) -> Path:
    """Return path to dynamics_state.json."""
    return Path(memory_dir) / DYNAMICS_STATE_FILENAME


def load_dynamics_state(memory_dir: str) -> dict:
    """Load dynamics state from file. Returns default if not found."""
    filepath = _get_state_path(memory_dir)
    try:
        text = filepath.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            return create_default_state()
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return create_default_state()

    # Validate and fill missing fields
    state = create_default_state()

    phase_str = data.get("phase", "normal")
    try:
        DynamicsPhase(phase_str)
        state["phase"] = phase_str
    except ValueError:
        state["phase"] = DynamicsPhase.NORMAL.value

    if isinstance(data.get("phase_call_count"), int):
        state["phase_call_count"] = max(0, data["phase_call_count"])

    if isinstance(data.get("accumulation_history"), list):
        hist = []
        for v in data["accumulation_history"]:
            if isinstance(v, (int, float)):
                hist.append(float(v))
        state["accumulation_history"] = hist[-ACCUMULATION_WINDOW:]

    if isinstance(data.get("peak_axis"), str):
        state["peak_axis"] = data["peak_axis"]

    if isinstance(data.get("last_updated"), str):
        state["last_updated"] = data["last_updated"]

    return state


def save_dynamics_state(memory_dir: str, state: dict) -> str:
    """Save dynamics state to file. Returns status message."""
    try:
        state["last_updated"] = _now_iso()
        filepath = _get_state_path(memory_dir)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(filepath.parent),
            prefix=".dynamics_",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, str(filepath))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return "Dynamics state saved."
    except Exception as e:
        return f"ERROR: Failed to save dynamics state: {e}"


# --- Core logic ---

def get_current_amplitude(state: dict) -> float:
    """Return the amplitude_modifier for the current phase without changing state.

    Args:
        state: Current dynamics state dict.

    Returns:
        amplitude_modifier value (1.0 for NORMAL, PEAK_AMPLITUDE for PEAK,
        REBOUND_AMPLITUDE for REBOUND).
    """
    phase_str = state.get("phase", "normal")
    if phase_str == "peak":
        return min(PEAK_AMPLITUDE, PEAK_AMPLITUDE_MAX)
    elif phase_str == "rebound":
        return max(REBOUND_AMPLITUDE, REBOUND_AMPLITUDE_MIN)
    return 1.0


def update_dynamics(
    state: dict,
    reaction_deltas: dict,
) -> tuple[dict, float]:
    """Update dynamics state based on reaction deltas.

    Args:
        state: Current dynamics state dict.
        reaction_deltas: Dict of {axis: delta_value} from emotion_react.

    Returns:
        Tuple of (updated_state, amplitude_modifier).
        amplitude_modifier should be passed to the next emotion_react call.
    """
    phase = DynamicsPhase(state.get("phase", "normal"))
    call_count = state.get("phase_call_count", 0)
    history = list(state.get("accumulation_history", []))
    peak_axis = state.get("peak_axis", "")

    amplitude_modifier = 1.0

    if phase == DynamicsPhase.NORMAL:
        # Track accumulation: sum of absolute deltas for this call
        total_abs_delta = sum(abs(v) for v in reaction_deltas.values() if isinstance(v, (int, float)))
        history.append(total_abs_delta)
        # FIFO window
        if len(history) > ACCUMULATION_WINDOW:
            history = history[-ACCUMULATION_WINDOW:]

        # Check if accumulation exceeds threshold
        accumulation_sum = sum(history)
        if accumulation_sum >= ACCUMULATION_THRESHOLD and len(history) > 0:
            # Transition to PEAK
            phase = DynamicsPhase.PEAK
            call_count = 0
            # Record peak axis (axis with largest absolute delta)
            if reaction_deltas:
                peak_axis = max(
                    reaction_deltas.keys(),
                    key=lambda k: abs(reaction_deltas.get(k, 0.0)),
                )
            else:
                peak_axis = ""
        else:
            amplitude_modifier = 1.0

    if phase == DynamicsPhase.PEAK:
        amplitude_modifier = PEAK_AMPLITUDE
        # Clamp to max
        amplitude_modifier = min(amplitude_modifier, PEAK_AMPLITUDE_MAX)
        call_count += 1

        if call_count >= PEAK_DURATION:
            # Transition to REBOUND
            phase = DynamicsPhase.REBOUND
            call_count = 0

    elif phase == DynamicsPhase.REBOUND:
        amplitude_modifier = REBOUND_AMPLITUDE
        # Clamp to min
        amplitude_modifier = max(amplitude_modifier, REBOUND_AMPLITUDE_MIN)
        call_count += 1

        if call_count >= REBOUND_DURATION:
            # Transition to NORMAL
            phase = DynamicsPhase.NORMAL
            call_count = 0
            history = []  # Reset accumulation
            peak_axis = ""

    new_state = {
        "phase": phase.value,
        "phase_call_count": call_count,
        "accumulation_history": history,
        "peak_axis": peak_axis,
        "last_updated": state.get("last_updated", _now_iso()),
    }

    return new_state, amplitude_modifier


def get_dynamics_info(state: dict) -> str:
    """Return human-readable description of current dynamics state."""
    phase = state.get("phase", "normal")
    call_count = state.get("phase_call_count", 0)
    history = state.get("accumulation_history", [])
    peak_axis = state.get("peak_axis", "")

    if phase == "peak":
        return (
            f"Phase: PEAK (call {call_count}/{PEAK_DURATION}, "
            f"axis={peak_axis}, amplitude={PEAK_AMPLITUDE})"
        )
    elif phase == "rebound":
        return (
            f"Phase: REBOUND (call {call_count}/{REBOUND_DURATION}, "
            f"amplitude={REBOUND_AMPLITUDE})"
        )
    else:
        accumulation = sum(history) if history else 0.0
        return (
            f"Phase: NORMAL (accumulation={accumulation:.3f}/{ACCUMULATION_THRESHOLD}, "
            f"window={len(history)}/{ACCUMULATION_WINDOW})"
        )


def check_session_reset(
    state: dict,
    hours_threshold: float = SESSION_RESET_HOURS,
) -> dict:
    """Reset PEAK/REBOUND to NORMAL if enough time has elapsed.

    Args:
        state: Current dynamics state.
        hours_threshold: Hours of inactivity after which to reset.

    Returns:
        Possibly reset state.
    """
    phase = state.get("phase", "normal")
    if phase == "normal":
        return state

    last_updated = _parse_iso(state.get("last_updated", ""))
    if last_updated is None:
        return state

    now = datetime.now(timezone.utc)
    elapsed_hours = (now - last_updated).total_seconds() / 3600.0

    if elapsed_hours >= hours_threshold:
        return create_default_state()

    return state
