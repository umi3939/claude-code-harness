#!/usr/bin/env python3
"""Emotion state management for Claude Code's memory system.

Provides three-axis emotion state (fulfillment, tension, affinity),
session-interval decay, emotional trace attachment to episodes,
and memory-emotion return (recall -> emotion feedback).

Design principles (design_claude_emotion_system.md):
- Emotion state is information only; never directly triggers actions/responses
- No emotion state is evaluated as "desirable" or "undesirable"
- Emotion state is not an optimization target
- Emotional traces are immutable once recorded
- Existing episodes without traces work unchanged (backward compatible)

Safety valves:
1. Per-episode return cap (each axis)
2. Total return cap (all episodes combined, each axis)
3. Rumination decay (same episode repeated return shrinks)
4. Value range clamp (always within valid range)
5. Session-interval decay (toward neutral on restore)
6. Trace immutability (recorded traces never modified)
"""

import io
import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Ensure UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


# --- Constants ---

EMOTION_STATE_FILENAME = "emotion_state.json"
RETURN_HISTORY_FILENAME = "emotion_return_history.json"
CHANGE_LOG_FILENAME = "emotion_change_log.json"

# Axis names
AXIS_FULFILLMENT = "fulfillment"
AXIS_TENSION = "tension"
AXIS_AFFINITY = "affinity"
ALL_AXES = (AXIS_FULFILLMENT, AXIS_TENSION, AXIS_AFFINITY)

# Value range
AXIS_MIN = -1.0
AXIS_MAX = 1.0
AXIS_NEUTRAL = 0.0

# Session-interval decay: per hour, emotion moves this fraction toward neutral
SESSION_DECAY_RATE_PER_HOUR = 0.1

# Return safety valve thresholds
# Derived from psyche/memory_emotion_return.py patterns, adapted for 3-axis system
PER_EPISODE_RETURN_CAP = 0.15       # Max return per episode per axis
TOTAL_RETURN_CAP = 0.3              # Max total return per axis (all episodes combined)
RUMINATION_THRESHOLD = 3            # After this many returns from same episode, decay starts
RUMINATION_DECAY_FACTOR = 0.3       # Each additional occurrence reduces by this factor

# Convergence: when current value and return direction align, reduce return
CONVERGENCE_THRESHOLD = 0.5         # Start converging when abs(current) > this
CONVERGENCE_SCALE = 0.7             # Scale factor for convergence reduction

# Freshness decay: older traces produce smaller returns
# hours after which trace freshness halves
FRESHNESS_HALF_LIFE_HOURS = 168.0   # 1 week

# Return history FIFO window
RETURN_HISTORY_WINDOW = 50

# Change log constants
CHANGE_LOG_FIFO_LIMIT = 50
CHANGE_LOG_REASON_MAX_LENGTH = 200
CHANGE_LOG_FRESHNESS_HALF_LIFE_HOURS = 168.0  # 1 week


# --- Helper functions ---

def _clamp(value: float, lo: float = AXIS_MIN, hi: float = AXIS_MAX) -> float:
    """Clamp a value to the valid range."""
    return max(lo, min(hi, value))


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


def _atomic_write_json(filepath: Path, data: dict) -> None:
    """Write JSON data atomically."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(filepath.parent),
        prefix=".emotion_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(filepath))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _load_json(filepath: Path) -> dict | None:
    """Load a JSON file. Returns None if missing or corrupted."""
    try:
        text = filepath.read_text(encoding="utf-8")
        data = json.loads(text)
        if isinstance(data, dict):
            return data
        return None
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None


# --- Emotion State ---

def create_default_state() -> dict:
    """Create a default emotion state (all axes at neutral)."""
    now = _now_iso()
    return {
        "fulfillment": AXIS_NEUTRAL,
        "tension": AXIS_NEUTRAL,
        "affinity": AXIS_NEUTRAL,
        "last_updated": now,
        "created_at": now,
    }


def _get_state_path(memory_dir: str) -> Path:
    """Return the path to the emotion state file."""
    return Path(memory_dir) / EMOTION_STATE_FILENAME


def _get_return_history_path(memory_dir: str) -> Path:
    """Return the path to the return history file."""
    return Path(memory_dir) / RETURN_HISTORY_FILENAME


def _get_change_log_path(memory_dir: str) -> Path:
    """Return the path to the emotion change log file."""
    return Path(memory_dir) / CHANGE_LOG_FILENAME


def load_state(memory_dir: str) -> dict:
    """Load emotion state from file. Returns default state if not found.

    Does NOT apply session-interval decay (call apply_session_decay separately).
    """
    filepath = _get_state_path(memory_dir)
    data = _load_json(filepath)
    if data is None:
        return create_default_state()

    # Validate and fill missing fields
    state = create_default_state()
    for axis in ALL_AXES:
        if axis in data and isinstance(data[axis], (int, float)):
            state[axis] = _clamp(float(data[axis]))
    if "last_updated" in data and isinstance(data["last_updated"], str):
        state["last_updated"] = data["last_updated"]
    if "created_at" in data and isinstance(data["created_at"], str):
        state["created_at"] = data["created_at"]

    return state


def save_state(memory_dir: str, state: dict) -> str:
    """Save emotion state to file.

    Returns a success message or error.
    """
    try:
        state["last_updated"] = _now_iso()
        filepath = _get_state_path(memory_dir)
        _atomic_write_json(filepath, state)
        return "Emotion state saved."
    except Exception as e:
        return f"ERROR: Failed to save emotion state: {e}"


def apply_session_decay(state: dict) -> dict:
    """Apply session-interval decay to emotion state.

    Moves each axis toward neutral proportional to elapsed time since last update.
    Returns the modified state (same dict, mutated).
    """
    last_updated_str = state.get("last_updated", "")
    last_updated = _parse_iso(last_updated_str)

    if last_updated is None:
        # Can't compute decay without a timestamp; just return as-is
        return state

    now = datetime.now(timezone.utc)
    elapsed_hours = max(0.0, (now - last_updated).total_seconds() / 3600.0)

    if elapsed_hours < 0.01:
        # Less than ~36 seconds, no meaningful decay
        return state

    # Decay factor: exponential decay toward neutral
    # After 1 hour: retain (1 - rate) of distance from neutral
    # After t hours: retain (1 - rate)^t
    decay_factor = (1.0 - SESSION_DECAY_RATE_PER_HOUR) ** elapsed_hours
    decay_factor = max(0.0, min(1.0, decay_factor))

    for axis in ALL_AXES:
        current = state.get(axis, AXIS_NEUTRAL)
        # Move toward neutral: new = neutral + (current - neutral) * decay_factor
        state[axis] = _clamp(AXIS_NEUTRAL + (current - AXIS_NEUTRAL) * decay_factor)

    state["last_updated"] = _now_iso()
    return state


def update_state(
    memory_dir: str,
    fulfillment: float | None = None,
    tension: float | None = None,
    affinity: float | None = None,
    mode: str = "delta",
    reason: str | None = None,
) -> str:
    """Update emotion state axes.

    Args:
        memory_dir: Path to memory directory.
        fulfillment: Value for fulfillment axis (optional).
        tension: Value for tension axis (optional).
        affinity: Value for affinity axis (optional).
        mode: "delta" to add to current values, "set" to replace values.
        reason: Reason for the change (optional, max 200 chars).

    Returns a status message.
    """
    if mode not in ("delta", "set"):
        return "ERROR: mode must be 'delta' or 'set'."

    updates = {}
    for axis_name, axis_val in [
        (AXIS_FULFILLMENT, fulfillment),
        (AXIS_TENSION, tension),
        (AXIS_AFFINITY, affinity),
    ]:
        if axis_val is not None:
            if not isinstance(axis_val, (int, float)):
                return f"ERROR: {axis_name} must be a number."
            fval = float(axis_val)
            if math.isnan(fval) or math.isinf(fval):
                return f"ERROR: {axis_name} must be a finite number (got {axis_val})."
            updates[axis_name] = fval

    if not updates:
        return "ERROR: At least one axis value must be provided."

    state = load_state(memory_dir)

    # Capture before values for change log
    before = {axis: state.get(axis, AXIS_NEUTRAL) for axis in ALL_AXES}

    for axis, value in updates.items():
        if mode == "delta":
            state[axis] = _clamp(state.get(axis, AXIS_NEUTRAL) + value)
        else:  # set
            state[axis] = _clamp(value)

    # Record change log entry before save — ensures log exists even if save fails
    after = {axis: state.get(axis, AXIS_NEUTRAL) for axis in ALL_AXES}
    _record_change_log_entry(memory_dir, before, after, reason)

    result = save_state(memory_dir, state)
    if result.startswith("ERROR"):
        return result

    # Format output showing new state
    axis_strs = [f"{a}={state[a]:+.3f}" for a in ALL_AXES]
    return f"Emotion state updated ({mode}): {', '.join(axis_strs)}"


def get_state(memory_dir: str) -> str:
    """Get current emotion state as formatted string.

    Loads state and applies session-interval decay before returning.
    """
    state = load_state(memory_dir)
    state = apply_session_decay(state)
    save_state(memory_dir, state)

    axis_strs = [f"{a}={state[a]:+.3f}" for a in ALL_AXES]
    return (
        f"Emotion state: {', '.join(axis_strs)}\n"
        f"Last updated: {state['last_updated']}"
    )


def get_state_dict(memory_dir: str) -> dict:
    """Get current emotion state as dict (for internal use)."""
    state = load_state(memory_dir)
    return state


# --- Emotion Change Log ---


def _record_change_log_entry(
    memory_dir: str,
    before: dict,
    after: dict,
    reason: str | None,
) -> None:
    """Record a change log entry. Called internally after emotion state update.

    Does not raise exceptions; failures are silently ignored to avoid
    interfering with the emotion update operation itself.
    """
    try:
        # Truncate reason to max length
        reason_text = ""
        if reason is not None and isinstance(reason, str):
            reason_text = reason[:CHANGE_LOG_REASON_MAX_LENGTH]

        entry = {
            "timestamp": _now_iso(),
            "before": {axis: before.get(axis, AXIS_NEUTRAL) for axis in ALL_AXES},
            "after": {axis: after.get(axis, AXIS_NEUTRAL) for axis in ALL_AXES},
            "reason": reason_text,
        }

        # Load existing log
        log = _load_change_log(memory_dir)

        # Append and enforce FIFO limit
        log.append(entry)
        if len(log) > CHANGE_LOG_FIFO_LIMIT:
            log = log[-CHANGE_LOG_FIFO_LIMIT:]

        # Save
        _save_change_log(memory_dir, log)
    except Exception as e:
        print(f"Warning: change log recording failed: {e}", file=sys.stderr)


def _load_change_log(memory_dir: str) -> list:
    """Load change log entries from file. Returns empty list if not found."""
    filepath = _get_change_log_path(memory_dir)
    data = _load_json(filepath)
    if data is None or "entries" not in data:
        return []
    entries = data.get("entries", [])
    if not isinstance(entries, list):
        return []
    return entries


def _save_change_log(memory_dir: str, entries: list) -> None:
    """Save change log entries to file."""
    filepath = _get_change_log_path(memory_dir)
    _atomic_write_json(filepath, {"entries": entries})


def _compute_change_freshness(timestamp_str: str) -> float:
    """Compute freshness for a change log entry.

    Returns a value in [0, 1] where 1.0 = just recorded, decreasing over time.
    Freshness is computed dynamically at read time, not stored.
    """
    ts = _parse_iso(timestamp_str)
    if ts is None:
        return 0.5  # Unknown timestamp: moderate freshness

    now = datetime.now(timezone.utc)
    elapsed_hours = max(0.0, (now - ts).total_seconds() / 3600.0)

    freshness = math.pow(0.5, elapsed_hours / CHANGE_LOG_FRESHNESS_HALF_LIFE_HOURS)
    return max(0.0, min(1.0, freshness))


def get_change_history(memory_dir: str, limit: int = 0) -> list[dict]:
    """Get change history entries with dynamically computed freshness.

    Args:
        memory_dir: Path to memory directory.
        limit: Maximum number of entries to return (0 = all, most recent first).

    Returns a list of change log entries, each augmented with a 'freshness' field.
    Entries are returned in reverse chronological order (newest first).
    """
    log = _load_change_log(memory_dir)

    # Reverse for most-recent-first order (avoid mutating internal list)
    log = list(reversed(log))

    # Apply limit
    if limit > 0:
        log = log[:limit]

    # Compute freshness for each entry
    for entry in log:
        entry["freshness"] = _compute_change_freshness(entry.get("timestamp", ""))

    return log


def format_change_history(entries: list[dict]) -> str:
    """Format change history entries as a human-readable string.

    Args:
        entries: List of change log entries (as returned by get_change_history).

    Returns formatted string.
    """
    if not entries:
        return "No emotion change history recorded."

    lines = [f"Emotion change history ({len(entries)} entries):"]
    for i, entry in enumerate(entries, 1):
        ts = entry.get("timestamp", "?")
        freshness = entry.get("freshness", 0.0)
        reason = entry.get("reason", "")
        before = entry.get("before", {})
        after = entry.get("after", {})

        # Format axis changes
        changes = []
        for axis in ALL_AXES:
            b = before.get(axis, 0.0)
            a = after.get(axis, 0.0)
            if abs(a - b) > 0.0005:
                changes.append(f"{axis}: {b:+.3f} -> {a:+.3f}")

        change_str = ", ".join(changes) if changes else "(no change)"
        freshness_str = f"freshness={freshness:.2f}"

        line = f"  {i}. [{ts}] ({freshness_str}) {change_str}"
        if reason:
            line += f" | reason: {reason}"
        lines.append(line)

    return "\n".join(lines)


# --- Emotional Trace ---

def create_trace(memory_dir: str, change_log: list | None = None) -> dict:
    """Create an emotional trace from the current emotion state.

    Returns a trace dict suitable for embedding in an episode record.
    The trace is a snapshot of the current emotion axes + timestamp,
    plus emotion deltas (difference from the last change_log entry's
    "after" values to the current state).

    Args:
        memory_dir: Path to memory directory.
        change_log: Optional list of change log entries. If None, loaded
                    from file. Passed as parameter to minimize coupling
                    (dependency injection per design_emotion_memory_binding.md).

    Delta fields added:
        delta_fulfillment, delta_tension, delta_affinity: float
            Difference between current state and the last change_log entry's
            "after" values for each axis. Zero if change_log is empty.
        delta_reference_timestamp: str or None
            Timestamp of the change_log entry used for delta calculation.
            None if change_log was empty.
    """
    state = load_state(memory_dir)

    # Load change log if not injected
    if change_log is None:
        change_log = _load_change_log(memory_dir)

    # Compute deltas from the most recent change_log entry
    deltas = {axis: 0.0 for axis in ALL_AXES}
    delta_ref_ts = None

    if change_log:
        latest_entry = change_log[-1]  # change_log is chronological, last = newest
        after_values = latest_entry.get("after", {})
        delta_ref_ts = latest_entry.get("timestamp")
        for axis in ALL_AXES:
            current_val = state.get(axis, AXIS_NEUTRAL)
            after_val = after_values.get(axis, AXIS_NEUTRAL)
            if isinstance(after_val, (int, float)) and isinstance(current_val, (int, float)):
                deltas[axis] = float(current_val) - float(after_val)

    return {
        "fulfillment": state.get(AXIS_FULFILLMENT, AXIS_NEUTRAL),
        "tension": state.get(AXIS_TENSION, AXIS_NEUTRAL),
        "affinity": state.get(AXIS_AFFINITY, AXIS_NEUTRAL),
        "trace_timestamp": _now_iso(),
        "delta_fulfillment": deltas[AXIS_FULFILLMENT],
        "delta_tension": deltas[AXIS_TENSION],
        "delta_affinity": deltas[AXIS_AFFINITY],
        "delta_reference_timestamp": delta_ref_ts,
    }


def extract_trace(episode: dict) -> dict | None:
    """Extract an emotional trace from an episode record.

    Returns the trace dict if present, None otherwise.
    """
    trace = episode.get("emotion_trace")
    if trace is None:
        return None
    if not isinstance(trace, dict):
        return None
    # Validate required fields
    if not all(axis in trace for axis in ALL_AXES):
        return None
    return trace


# --- Memory-Emotion Return ---

def _load_return_history(memory_dir: str) -> list:
    """Load return history from file."""
    filepath = _get_return_history_path(memory_dir)
    data = _load_json(filepath)
    if data is None or "history" not in data:
        return []
    history = data.get("history", [])
    if not isinstance(history, list):
        return []
    return history


def _save_return_history(memory_dir: str, history: list) -> None:
    """Save return history to file."""
    filepath = _get_return_history_path(memory_dir)
    _atomic_write_json(filepath, {"history": history})


def _compute_freshness(trace_timestamp_str: str) -> float:
    """Compute freshness factor from trace timestamp.

    Returns a value in [0, 1] where 1.0 = just recorded, decreasing over time.
    Uses exponential decay with half-life.
    """
    ts = _parse_iso(trace_timestamp_str)
    if ts is None:
        return 0.5  # Unknown timestamp: use moderate freshness

    now = datetime.now(timezone.utc)
    elapsed_hours = max(0.0, (now - ts).total_seconds() / 3600.0)

    # Exponential decay: freshness = 0.5^(elapsed / half_life)
    freshness = math.pow(0.5, elapsed_hours / FRESHNESS_HALF_LIFE_HOURS)
    return max(0.0, min(1.0, freshness))


def _derive_single_return(
    trace: dict,
    current_state: dict,
) -> dict:
    """Derive return amounts from a single episode's trace.

    Returns dict mapping axis names to delta values.
    """
    deltas = {}

    trace_ts = trace.get("trace_timestamp", "")
    freshness = _compute_freshness(trace_ts)

    for axis in ALL_AXES:
        trace_val = trace.get(axis, AXIS_NEUTRAL)
        current_val = current_state.get(axis, AXIS_NEUTRAL)

        if not isinstance(trace_val, (int, float)):
            continue
        if not isinstance(current_val, (int, float)):
            current_val = AXIS_NEUTRAL

        # Base return: proportional to difference between trace and current
        diff = float(trace_val) - float(current_val)
        base_return = diff * 0.15  # Scale factor

        # Freshness scaling
        base_return *= freshness

        # Convergence: reduce return when current already high in same direction
        if abs(current_val) > CONVERGENCE_THRESHOLD:
            # Same direction as diff: reduce
            if (current_val > 0 and diff > 0) or (current_val < 0 and diff < 0):
                convergence = 1.0 - (abs(current_val) - CONVERGENCE_THRESHOLD) * CONVERGENCE_SCALE
                base_return *= max(0.1, convergence)

        deltas[axis] = base_return

    return deltas


def process_return(
    memory_dir: str,
    episodes: list[dict],
) -> str:
    """Process memory-emotion return for a list of recalled episodes.

    Reads emotion traces from episodes, derives return amounts,
    applies safety valves, and updates current emotion state.

    Args:
        memory_dir: Path to memory directory.
        episodes: List of episode dicts (as returned by recall/search).

    Returns a status message describing the return processing result.
    """
    if not episodes:
        return "No episodes provided for return processing."

    current_state = load_state(memory_dir)
    return_history = _load_return_history(memory_dir)

    # Count episode occurrences in history (for rumination)
    history_counts: dict[str, int] = {}
    for rec in return_history:
        eid = rec.get("episode_id", "")
        if eid:
            history_counts[eid] = history_counts.get(eid, 0) + 1

    # Derive per-episode returns
    per_episode_returns: list[dict] = []
    episodes_with_traces = 0

    for ep in episodes:
        trace = extract_trace(ep)
        if trace is None:
            continue

        episodes_with_traces += 1
        episode_id = ep.get("episode_id", "unknown")

        # Derive raw return amounts
        raw_deltas = _derive_single_return(trace, current_state)

        # Safety valve 3: Rumination decay
        rumination_applied = False
        occurrence_count = history_counts.get(episode_id, 0)
        if occurrence_count >= RUMINATION_THRESHOLD:
            excess = occurrence_count - RUMINATION_THRESHOLD + 1
            decay_multiplier = max(0.0, 1.0 - excess * RUMINATION_DECAY_FACTOR)
            for axis in raw_deltas:
                raw_deltas[axis] *= decay_multiplier
            rumination_applied = True

        # Safety valve 1: Per-episode cap
        for axis in raw_deltas:
            if raw_deltas[axis] > PER_EPISODE_RETURN_CAP:
                raw_deltas[axis] = PER_EPISODE_RETURN_CAP
            elif raw_deltas[axis] < -PER_EPISODE_RETURN_CAP:
                raw_deltas[axis] = -PER_EPISODE_RETURN_CAP

        per_episode_returns.append({
            "episode_id": episode_id,
            "deltas": raw_deltas,
            "rumination_applied": rumination_applied,
        })

    if not per_episode_returns:
        return "No episodes with emotion traces found. No return applied."

    # Aggregate total deltas
    total_deltas: dict[str, float] = {axis: 0.0 for axis in ALL_AXES}
    for entry in per_episode_returns:
        for axis, delta in entry["deltas"].items():
            total_deltas[axis] += delta

    # Safety valve 2: Total return cap
    for axis in total_deltas:
        if total_deltas[axis] > TOTAL_RETURN_CAP:
            total_deltas[axis] = TOTAL_RETURN_CAP
        elif total_deltas[axis] < -TOTAL_RETURN_CAP:
            total_deltas[axis] = -TOTAL_RETURN_CAP

    # Safety valve 4: Clamp after applying deltas
    applied_deltas: dict[str, float] = {}
    for axis in ALL_AXES:
        old_val = current_state.get(axis, AXIS_NEUTRAL)
        new_val = _clamp(old_val + total_deltas[axis])
        actual_delta = new_val - old_val
        if abs(actual_delta) > 1e-6:
            applied_deltas[axis] = actual_delta
            current_state[axis] = new_val

    # Save updated state
    if applied_deltas:
        save_result = save_state(memory_dir, current_state)
        if save_result.startswith("ERROR"):
            return save_result

    # Update return history (FIFO)
    now_str = _now_iso()
    for entry in per_episode_returns:
        # Only record if any non-zero delta
        has_nonzero = any(abs(v) > 1e-6 for v in entry["deltas"].values())
        if has_nonzero:
            return_history.append({
                "episode_id": entry["episode_id"],
                "deltas": entry["deltas"],
                "rumination_applied": entry["rumination_applied"],
                "timestamp": now_str,
            })

    # FIFO trimming
    if len(return_history) > RETURN_HISTORY_WINDOW:
        return_history = return_history[-RETURN_HISTORY_WINDOW:]

    _save_return_history(memory_dir, return_history)

    # Format result
    if not applied_deltas:
        return (
            f"Return processing complete: {episodes_with_traces} episodes with traces, "
            f"but all deltas were zero (convergence/rumination). No state change."
        )

    delta_strs = [f"{a}={applied_deltas.get(a, 0.0):+.4f}" for a in ALL_AXES if a in applied_deltas]
    state_strs = [f"{a}={current_state[a]:+.3f}" for a in ALL_AXES]
    return (
        f"Return processing complete: {episodes_with_traces} episodes with traces, "
        f"{len(per_episode_returns)} processed.\n"
        f"Applied deltas: {', '.join(delta_strs)}\n"
        f"New state: {', '.join(state_strs)}"
    )


def process_return_from_search_results(
    memory_dir: str,
    search_result_text: str,
) -> str:
    """Process return from search result text by loading actual episode data.

    This is a convenience wrapper that extracts episode IDs from search results,
    loads the full episode data including traces, and processes the return.

    Args:
        memory_dir: Path to memory directory.
        search_result_text: Text output from memory_search.

    Returns a status message.
    """
    # Extract episode IDs from search result text
    # Format: "[type] episode_id (timestamp) ..."
    episode_ids = re.findall(r'\[(?:user_request|decision|error|solution|feedback|observation)\]\s+([a-fA-F0-9]{12})', search_result_text)

    if not episode_ids:
        return "No episode IDs found in search results."

    # Load episodes from session files
    episodes_dir = Path(memory_dir) / "episodes"
    if not episodes_dir.exists():
        return "No episodes directory found."

    # Load all episodes and match by ID
    matched_episodes = []
    session_files = sorted(
        [f for f in episodes_dir.iterdir()
         if f.is_file() and f.name.startswith("session_") and f.name.endswith(".json")],
        key=lambda f: f.stat().st_mtime,
    )

    id_set = set(episode_ids)
    for sf in session_files:
        data = _load_json(sf)
        if data is None:
            continue
        for ep in data.get("episodes", []):
            if ep.get("episode_id") in id_set:
                matched_episodes.append(ep)

    if not matched_episodes:
        return "No matching episodes found in episode data."

    return process_return(memory_dir, matched_episodes)
