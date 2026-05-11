#!/usr/bin/env python3
"""Temporal self-difference cognition module for Claude Code MCP.

Maintains a FIFO history of self-state snapshots and computes abstract
descriptions of how internal state has changed between observations.

Design: design_temporal_self_difference_mcp.md

Key principles:
- READ-ONLY observation of emotion/dynamics (never writes to those systems)
- Only writes to its own snapshot file (self_difference_snapshots.json)
- Output contains NO numeric values — only abstract categories
- NO evaluation (good/bad/improvement/deterioration)
- Comparison runs ONLY when explicitly called (no automatic triggers)
"""

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Add the tools directory to sys.path
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import emotion_state
import emotion_dynamics
from self_model import _axis_to_category, _observe_changes

# --- Constants ---

SNAPSHOTS_FILENAME = "self_difference_snapshots.json"
FIFO_MAX = 10

# Category ordering for distance calculation
CATEGORY_INDEX = {
    "strongly_negative": 0,
    "negative": 1,
    "neutral": 2,
    "positive": 3,
    "strongly_positive": 4,
}

# Axis Japanese names for integrated description
_AXIS_JA = {
    "fulfillment": "充実感",
    "tension": "緊張",
    "affinity": "親和性",
    "dynamics_phase": "感情動力学",
}


# --- Snapshot persistence ---

def _get_snapshots_path(memory_dir: str) -> Path:
    """Return path to the snapshots file."""
    return Path(memory_dir) / SNAPSHOTS_FILENAME


def _load_snapshots(memory_dir: str) -> dict:
    """Load snapshots data from file.

    Returns:
        {"snapshots": [...], "comparison_count": int}
    """
    filepath = _get_snapshots_path(memory_dir)
    try:
        text = filepath.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            return {"snapshots": [], "comparison_count": 0}
        snapshots = data.get("snapshots", [])
        if not isinstance(snapshots, list):
            snapshots = []
        count = data.get("comparison_count", 0)
        if not isinstance(count, int):
            count = 0
        return {"snapshots": snapshots, "comparison_count": count}
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return {"snapshots": [], "comparison_count": 0}


def _save_snapshots(memory_dir: str, data: dict) -> None:
    """Save snapshots data atomically."""
    filepath = _get_snapshots_path(memory_dir)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(filepath.parent),
        prefix=".selfdiff_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
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


# --- Snapshot creation ---

def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _take_snapshot(memory_dir: str) -> dict:
    """Take a snapshot of the current self-state.

    Reads from emotion_state, emotion_dynamics, and self_model change observation.
    """
    now = _now_iso()
    snapshot_id = now.replace("-", "").replace(":", "").replace("T", "").replace("Z", "")

    # Load emotion values
    state = emotion_state.load_state(memory_dir)
    values = {
        "fulfillment": float(state.get("fulfillment", 0.0)),
        "tension": float(state.get("tension", 0.0)),
        "affinity": float(state.get("affinity", 0.0)),
    }

    # Compute categories
    categories = {
        axis: _axis_to_category(values[axis])
        for axis in ("fulfillment", "tension", "affinity")
    }

    # Load dynamics phase
    dyn_state = emotion_dynamics.load_dynamics_state(memory_dir)
    dynamics_phase = dyn_state.get("phase", "normal")

    # Observe changes for frequency and trends
    change_obs = _observe_changes(memory_dir)
    change_frequency = change_obs.get("frequency", "none")
    change_trends = change_obs.get("trends", {
        "fulfillment": "stable",
        "tension": "stable",
        "affinity": "stable",
    })

    return {
        "snapshot_id": snapshot_id,
        "timestamp": now,
        "emotion_values": values,
        "emotion_categories": categories,
        "dynamics_phase": dynamics_phase,
        "change_frequency": change_frequency,
        "change_trends": change_trends,
    }


# --- Component change type ---

def _compute_component_change(prev_category: str, curr_category: str) -> str:
    """Compute change type for a single emotion axis.

    Returns: unchanged, intensified, softened, or shifted
    """
    if prev_category == curr_category:
        return "unchanged"

    prev_idx = CATEGORY_INDEX.get(prev_category)
    curr_idx = CATEGORY_INDEX.get(curr_category)

    if prev_idx is None or curr_idx is None:
        return "shifted"

    # Check for sign crossing (shifted)
    prev_sign = -1 if prev_idx < 2 else (1 if prev_idx > 2 else 0)
    curr_sign = -1 if curr_idx < 2 else (1 if curr_idx > 2 else 0)

    if prev_sign != 0 and curr_sign != 0 and prev_sign != curr_sign:
        return "shifted"

    if curr_idx > prev_idx:
        return "intensified"
    else:
        return "softened"


def _compute_dynamics_change(prev_phase: str, curr_phase: str) -> str:
    """Compute change type for dynamics phase."""
    if prev_phase == curr_phase:
        return "unchanged"
    return "shifted"


# --- Category distance ---

def _category_distance(cat_a: str, cat_b: str) -> int:
    """Compute absolute distance between two categories."""
    idx_a = CATEGORY_INDEX.get(cat_a, 2)
    idx_b = CATEGORY_INDEX.get(cat_b, 2)
    return abs(idx_a - idx_b)


# --- Difference magnitude ---

def _compute_magnitude(components: dict, prev_snapshot: dict, curr_snapshot: dict) -> str:
    """Compute DifferenceMagnitude from component changes.

    Args:
        components: dict of component_name -> change_type
        prev_snapshot: previous snapshot
        curr_snapshot: current snapshot

    Returns: none, minimal, noticeable, significant, or substantial
    """
    changed_count = sum(1 for ct in components.values() if ct != "unchanged")
    if changed_count == 0:
        return "none"

    # Calculate total distance
    total_distance = 0
    for axis in ("fulfillment", "tension", "affinity"):
        prev_cat = prev_snapshot.get("emotion_categories", {}).get(axis, "neutral")
        curr_cat = curr_snapshot.get("emotion_categories", {}).get(axis, "neutral")
        total_distance += _category_distance(prev_cat, curr_cat)

    # Dynamics phase change counts as distance 1
    if components.get("dynamics_phase") == "shifted":
        total_distance += 1

    # Apply magnitude rules (order matters — first match wins)
    if changed_count == 1 and total_distance <= 1:
        return "minimal"
    if changed_count <= 2 and total_distance <= 3:
        return "noticeable"
    if changed_count <= 3 and total_distance <= 5:
        return "significant"
    return "substantial"


# --- Change nature ---

def _compute_change_nature(snapshots: list, current_snapshot: dict) -> str:
    """Compute ChangeNature from snapshot history.

    Args:
        snapshots: full snapshot history (chronological, oldest first)
        current_snapshot: the current (latest) snapshot

    Returns: stable, fluctuating, shifting, transformed, returning, or undefined
    """
    # All snapshots including current
    all_snaps = snapshots + [current_snapshot]
    n = len(all_snaps)

    # Rule 1: fewer than 2 snapshots
    if n < 2:
        return "undefined"

    # Helper: compute changed component count between two snapshots
    def _changed_count(snap_a, snap_b):
        count = 0
        for axis in ("fulfillment", "tension", "affinity"):
            cat_a = snap_a.get("emotion_categories", {}).get(axis, "neutral")
            cat_b = snap_b.get("emotion_categories", {}).get(axis, "neutral")
            if cat_a != cat_b:
                count += 1
        if snap_a.get("dynamics_phase", "normal") != snap_b.get("dynamics_phase", "normal"):
            count += 1
        return count

    # Helper: compute total category index distance between two snapshots (3 axes only)
    def _total_distance(snap_a, snap_b):
        dist = 0
        for axis in ("fulfillment", "tension", "affinity"):
            cat_a = snap_a.get("emotion_categories", {}).get(axis, "neutral")
            cat_b = snap_b.get("emotion_categories", {}).get(axis, "neutral")
            dist += _category_distance(cat_a, cat_b)
        return dist

    # Rule 2: average changed components across recent pairs
    recent_count = min(5, n)
    recent = all_snaps[-recent_count:]
    pair_changes = []
    for i in range(len(recent) - 1):
        pair_changes.append(_changed_count(recent[i], recent[i + 1]))

    if pair_changes:
        avg_changes = sum(pair_changes) / len(pair_changes)
    else:
        avg_changes = 0.0

    if avg_changes < 0.5:
        return "stable"

    # Rule 3: variance of change counts > 1.0 and average < 2
    if len(pair_changes) >= 2:  # need at least 2 for meaningful variance
        mean_pc = sum(pair_changes) / len(pair_changes)
        variance = sum((x - mean_pc) ** 2 for x in pair_changes) / len(pair_changes)
        if variance > 1.0 and mean_pc < 2:
            return "fluctuating"

    # Rule 4: returning — need 5+ snapshots in all_snaps
    if n >= 6:  # 5 previous + current
        five_ago = all_snaps[-6]  # snapshot 5 positions before current
        prev = all_snaps[-2]  # direct previous
        dist_five_ago = _total_distance(five_ago, current_snapshot)
        dist_prev = _total_distance(prev, current_snapshot)
        if dist_five_ago < dist_prev:
            return "returning"

    # Rule 5: transformed
    if avg_changes >= 3:
        return "transformed"

    # Rule 6: default
    return "shifting"


# --- Integrated description ---

_NATURE_JA = {
    "stable": "比較的安定",
    "fluctuating": "揺れ動いている",
    "shifting": "緩やかに移行している",
    "transformed": "顕著に異なっている",
    "returning": "以前の状態に近づいている",
}


def _generate_description(magnitude: str, nature: str, components: dict) -> str:
    """Generate integrated description text (Japanese, no numbers, no evaluation)."""
    if magnitude == "none":
        return "自己状態に変化は見られない。"
    if magnitude == "minimal":
        return "自己状態にわずかな揺らぎがある。"

    # magnitude >= noticeable
    nature_desc = _NATURE_JA.get(nature, nature)

    # List changed component names in Japanese
    changed = []
    for comp, change_type in components.items():
        if change_type != "unchanged":
            ja_name = _AXIS_JA.get(comp, comp)
            changed.append(ja_name)

    if changed:
        comp_desc = "と".join(changed)
        return f"自己状態は{comp_desc}において{nature_desc}。"
    else:
        return f"自己状態は{nature_desc}。"


# --- Public API ---

def compute_difference(memory_dir: str) -> dict:
    """Take a snapshot, compare with previous, return abstract difference.

    This is the main entry point. Each call:
    1. Takes a new snapshot of current state
    2. Saves it to FIFO history
    3. Compares with the previous snapshot
    4. Returns abstract difference description

    Returns:
        {
            "has_difference": bool,
            "magnitude": str,  # none/minimal/noticeable/significant/substantial
            "nature": str,     # stable/fluctuating/shifting/transformed/returning/undefined
            "components": {
                "fulfillment": {"change_type": str, "from": str, "to": str},
                "tension": {"change_type": str, "from": str, "to": str},
                "affinity": {"change_type": str, "from": str, "to": str},
                "dynamics_phase": {"change_type": str, "from": str, "to": str},
            },
            "integrated_description": str,
        }
    """
    # 1. Take current snapshot
    current = _take_snapshot(memory_dir)

    # 2. Load existing snapshots and save the new one
    data = _load_snapshots(memory_dir)
    snapshots = data["snapshots"]
    comparison_count = data["comparison_count"]

    # Get previous snapshot before appending current
    prev = snapshots[-1] if snapshots else None

    # Append current and enforce FIFO
    snapshots.append(current)
    if len(snapshots) > FIFO_MAX:
        snapshots = snapshots[-FIFO_MAX:]

    # Increment comparison count
    comparison_count += 1

    # Save updated history
    _save_snapshots(memory_dir, {
        "snapshots": snapshots,
        "comparison_count": comparison_count,
    })

    # 3. If no previous snapshot, return no-difference with undefined nature
    if prev is None:
        return {
            "has_difference": False,
            "magnitude": "none",
            "nature": "undefined",
            "components": {
                "fulfillment": {"change_type": "unchanged", "from": "", "to": ""},
                "tension": {"change_type": "unchanged", "from": "", "to": ""},
                "affinity": {"change_type": "unchanged", "from": "", "to": ""},
                "dynamics_phase": {"change_type": "unchanged", "from": "", "to": ""},
            },
            "integrated_description": "自己状態に変化は見られない。",
        }

    # 4. Compute component changes
    components = {}
    for axis in ("fulfillment", "tension", "affinity"):
        prev_cat = prev.get("emotion_categories", {}).get(axis, "neutral")
        curr_cat = current.get("emotion_categories", {}).get(axis, "neutral")
        change_type = _compute_component_change(prev_cat, curr_cat)
        components[axis] = {
            "change_type": change_type,
            "from": prev_cat,
            "to": curr_cat,
        }

    prev_phase = prev.get("dynamics_phase", "normal")
    curr_phase = current.get("dynamics_phase", "normal")
    components["dynamics_phase"] = {
        "change_type": _compute_dynamics_change(prev_phase, curr_phase),
        "from": prev_phase,
        "to": curr_phase,
    }

    # 5. Compute magnitude
    component_types = {k: v["change_type"] for k, v in components.items()}
    magnitude = _compute_magnitude(component_types, prev, current)

    # 6. Compute nature from history (excluding the current snapshot which is already appended)
    # Pass snapshots without the last one (current), plus current separately
    history_without_current = snapshots[:-1]
    nature = _compute_change_nature(history_without_current, current)

    # 7. Generate integrated description
    description = _generate_description(magnitude, nature, component_types)

    has_diff = magnitude != "none"

    return {
        "has_difference": has_diff,
        "magnitude": magnitude,
        "nature": nature,
        "components": components,
        "integrated_description": description,
    }
