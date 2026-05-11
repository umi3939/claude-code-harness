#!/usr/bin/env python3
"""Self-continuity strain module for Claude Code MCP.

Tracks the persistence and accumulation of self-state temporal differences,
producing an abstract description of "strain" on self-continuity.

Design: design_continuity_strain_mcp.md

Key principles:
- READ-ONLY consumption of temporal_self_difference output
- Only writes to its own state file (continuity_strain_state.json)
- Output contains NO numeric values exposed to the user -- only abstract categories
- NO evaluation (good/bad/improvement/deterioration)
- Does NOT modify any other system's state
"""

import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# Add the tools directory to sys.path
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import temporal_self_difference

# --- Constants ---

STATE_FILENAME = "continuity_strain_state.json"
OBSERVATIONS_MAX = 20
STRAIN_HISTORY_MAX = 10

# Magnitudes considered "significant"
SIGNIFICANT_MAGNITUDES = {"noticeable", "significant", "substantial"}

# StrainLevel thresholds (consecutive_significant count)
STRAIN_LEVELS = [
    (10, "alienated"),
    (5, "dissonant"),
    (3, "unsettled"),
    (0, "at_ease"),
]

# StrainLevel numeric mapping for trend calculation
LEVEL_VALUE = {
    "at_ease": 0,
    "unsettled": 1,
    "dissonant": 2,
    "alienated": 3,
}

# Level escalation order
LEVEL_ORDER = ["at_ease", "unsettled", "dissonant", "alienated"]

# Base descriptions per level
LEVEL_DESCRIPTIONS = {
    "at_ease": "自己の連続性に違和感はない。",
    "unsettled": "自己の連続性にわずかな違和感がある",
    "dissonant": "自己の連続性に顕著な不協和がある",
    "alienated": "自己の連続性に強い断絶感がある",
}

# Persistence suffixes
PERSISTENCE_SUFFIXES = {
    "none": "",
    "momentary": "が、最近現れた",
    "ongoing": "が、しばらく続いている",
    "chronic": "が、長く持続している",
}

# Trend suffixes
TREND_SUFFIXES = {
    "stable": "。",
    "building": "。この感覚は強まっている。",
    "easing": "。ただしこの感覚は和らいでいる。",
    "fluctuating": "。この感覚は揺れ動いている。",
}


# --- State persistence ---

def _get_state_path(memory_dir: str) -> Path:
    """Return path to the state file."""
    return Path(memory_dir) / STATE_FILENAME


def _default_state() -> dict:
    """Return a fresh default state."""
    return {
        "observations": [],
        "current_strain": {
            "strain_present": False,
            "level": "at_ease",
            "persistence": "none",
            "trend": "stable",
            "description": LEVEL_DESCRIPTIONS["at_ease"],
            "timestamp": None,
            "last_update_timestamp": None,
        },
        "consecutive_significant_count": 0,
        "consecutive_insignificant_count": 0,
        "strain_started_at": None,
        "total_strain_observations": 0,
        "strain_level_history": [],
    }


def _load_state(memory_dir: str) -> dict:
    """Load strain state from file. Returns default on any error."""
    filepath = _get_state_path(memory_dir)
    try:
        text = filepath.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            return _default_state()
        # Validate required fields exist
        if "observations" not in data or "consecutive_significant_count" not in data:
            return _default_state()
        return data
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return _default_state()


def _save_state(memory_dir: str, state: dict) -> None:
    """Save strain state atomically."""
    filepath = _get_state_path(memory_dir)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(filepath.parent),
        prefix=".strain_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        try:
            os.replace(tmp_path, str(filepath))
        except OSError:
            # Windows fallback: copy + unlink
            shutil.copy2(tmp_path, str(filepath))
            os.unlink(tmp_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
        raise


# --- Helpers ---

def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_significant(magnitude: str) -> bool:
    """Check if a magnitude is considered significant."""
    return magnitude in SIGNIFICANT_MAGNITUDES


def _determine_base_level(consecutive_significant: int) -> str:
    """Determine base strain level from consecutive significant count."""
    for threshold, level in STRAIN_LEVELS:
        if consecutive_significant >= threshold:
            return level
    return "at_ease"


def _escalate_level(level: str) -> str:
    """Escalate a level by one step. alienated stays alienated."""
    idx = LEVEL_ORDER.index(level)
    if idx < len(LEVEL_ORDER) - 1:
        return LEVEL_ORDER[idx + 1]
    return level


def _apply_escalation_correction(base_level: str, observations: list) -> str:
    """Apply escalation correction based on recent observations.

    If the most frequent magnitude in the last 5 observations is 'substantial',
    escalate the level by one step.
    """
    if base_level == "at_ease":
        return base_level

    recent = observations[-5:] if len(observations) > 5 else observations
    if not recent:
        return base_level

    magnitudes = [obs.get("magnitude", "none") for obs in recent]
    counter = Counter(magnitudes)
    most_common_mag = counter.most_common(1)[0][0]

    if most_common_mag == "substantial":
        return _escalate_level(base_level)
    return base_level


def _determine_persistence(consecutive_significant: int) -> str:
    """Determine strain persistence from consecutive significant count."""
    if consecutive_significant >= 10:
        return "chronic"
    if consecutive_significant >= 5:
        return "ongoing"
    if consecutive_significant >= 3:
        return "momentary"
    return "none"


def _determine_trend(strain_level_history: list) -> str:
    """Determine strain trend from recent level history.

    Uses the last 4 entries, numerized via LEVEL_VALUE.
    """
    if len(strain_level_history) < 2:
        return "stable"

    recent = strain_level_history[-4:]
    values = [LEVEL_VALUE.get(lv, 0) for lv in recent]

    # All same
    if all(v == values[0] for v in values):
        return "stable"

    # Monotonically non-decreasing and end > start
    if values[-1] > values[0]:
        is_non_decreasing = all(values[i] <= values[i + 1] for i in range(len(values) - 1))
        if is_non_decreasing:
            return "building"

    # Monotonically non-increasing and end < start
    if values[-1] < values[0]:
        is_non_increasing = all(values[i] >= values[i + 1] for i in range(len(values) - 1))
        if is_non_increasing:
            return "easing"

    return "fluctuating"


def _generate_description(level: str, persistence: str, trend: str) -> str:
    """Generate integrated description text (Japanese, no numbers, no evaluation)."""
    base = LEVEL_DESCRIPTIONS.get(level, LEVEL_DESCRIPTIONS["at_ease"])

    if level == "at_ease":
        # at_ease already ends with "。" and needs no modifiers
        return base

    # Add persistence suffix
    p_suffix = PERSISTENCE_SUFFIXES.get(persistence, "")
    text = base + p_suffix

    # Add trend suffix
    t_suffix = TREND_SUFFIXES.get(trend, "。")
    text += t_suffix

    return text


# --- Public API ---

def evaluate_strain(memory_dir: str) -> dict:
    """Compute difference, update strain state, and return current strain info.

    Calls temporal_self_difference.compute_difference() internally,
    then evaluates whether differences have been persistent enough
    to generate a sense of discontinuity.

    Returns:
        {
            "strain_present": bool,
            "level": str,       # at_ease/unsettled/dissonant/alienated
            "persistence": str,  # none/momentary/ongoing/chronic
            "trend": str,        # stable/building/easing/fluctuating
            "description": str,  # Japanese abstract text
            "observation_count": int,
            "self_difference": dict,  # compute_difference() output
        }
    """
    # 1. Get difference from temporal_self_difference
    diff = temporal_self_difference.compute_difference(memory_dir)

    # 2. Load current state
    state = _load_state(memory_dir)

    now = _now_iso()
    magnitude = diff.get("magnitude", "none")
    nature = diff.get("nature", "undefined")
    has_diff = diff.get("has_difference", False)
    significant = _is_significant(magnitude)

    # 3. Add observation to FIFO
    observation = {
        "magnitude": magnitude,
        "nature": nature,
        "has_difference": has_diff,
        "timestamp": now,
        "is_significant": significant,
    }
    observations = state.get("observations", [])
    observations.append(observation)
    if len(observations) > OBSERVATIONS_MAX:
        observations = observations[-OBSERVATIONS_MAX:]
    state["observations"] = observations

    # 4. Update consecutive counts
    cons_sig = state.get("consecutive_significant_count", 0)
    cons_insig = state.get("consecutive_insignificant_count", 0)

    if significant:
        cons_sig += 1
        cons_insig = 0
    else:
        cons_insig += 1
        # Decay logic
        if cons_insig >= 4:
            # Complete resolution
            cons_sig = 0
            state["strain_started_at"] = None
        elif cons_insig >= 2:
            # Easing: reduce by 1
            cons_sig = max(0, cons_sig - 1)

    state["consecutive_significant_count"] = cons_sig
    state["consecutive_insignificant_count"] = cons_insig

    # 5. Track strain start
    if cons_sig >= 3 and state.get("strain_started_at") is None:
        state["strain_started_at"] = now

    # 6. Determine strain level
    base_level = _determine_base_level(cons_sig)

    # Apply escalation correction (only if strain is present)
    if base_level != "at_ease":
        level = _apply_escalation_correction(base_level, observations)
    else:
        level = base_level

    # Handle decay: during easing (cons_insig >= 2 but not fully resolved),
    # force level to unsettled at most
    if cons_insig >= 2 and cons_sig > 0:
        if LEVEL_VALUE.get(level, 0) > LEVEL_VALUE["unsettled"]:
            level = "unsettled"

    # 7. Determine persistence
    persistence = _determine_persistence(cons_sig)

    # 8. Update strain level history (FIFO)
    history = state.get("strain_level_history", [])
    history.append(level)
    if len(history) > STRAIN_HISTORY_MAX:
        history = history[-STRAIN_HISTORY_MAX:]
    state["strain_level_history"] = history

    # 9. Determine trend
    trend = _determine_trend(history)

    # Override trend during easing
    if cons_insig >= 2 and cons_sig > 0:
        trend = "easing"

    # 10. Determine strain_present
    strain_present = cons_sig >= 3

    # 11. Generate description
    description = _generate_description(level, persistence, trend)

    # 12. Update total observation count
    total = state.get("total_strain_observations", 0) + 1
    state["total_strain_observations"] = total

    # 13. Update current_strain in state
    state["current_strain"] = {
        "strain_present": strain_present,
        "level": level,
        "persistence": persistence,
        "trend": trend,
        "description": description,
        "timestamp": state.get("strain_started_at"),
        "last_update_timestamp": now,
    }

    # 14. Save state
    _save_state(memory_dir, state)

    # 15. Return result
    return {
        "strain_present": strain_present,
        "level": level,
        "persistence": persistence,
        "trend": trend,
        "description": description,
        "observation_count": total,
        "self_difference": diff,
    }
