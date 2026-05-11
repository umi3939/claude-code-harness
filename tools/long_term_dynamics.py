#!/usr/bin/env python3
"""Long-term dynamics logging for Claude Code MCP.

Passively observes emotion state over time, aggregating observations
into window-based statistics. Adapted from psyche/long_term_dynamics.py
for the Claude Code 3-axis emotion system.

Design principles:
- PASSIVE observation only (does NOT change emotion state)
- Window-based aggregation (every N observations)
- Append-only log (past entries never modified)
- Lightweight statistics (mean, variance, phase distribution)

Files:
- long_term_dynamics_log.json: append-only entry list (max 100 entries)
- long_term_dynamics_buffer.json: unaggregated observation buffer
"""

import json
import math
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


# --- Constants ---

LOG_FILENAME = "long_term_dynamics_log.json"
BUFFER_FILENAME = "long_term_dynamics_buffer.json"

WINDOW_SIZE = 10
MAX_ENTRIES = 100

AXIS_NAMES = ("fulfillment", "tension", "affinity")
VALID_PHASES = ("normal", "peak", "rebound")


# --- Helpers ---

def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write_json(filepath: Path, data: dict) -> None:
    """Write JSON data atomically."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(filepath.parent),
        prefix=".ltdyn_",
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


def _compute_mean(values: list[float]) -> float:
    """Compute mean of values."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def _compute_variance(values: list[float]) -> float:
    """Compute population variance of values."""
    if len(values) < 2:
        return 0.0
    mean = _compute_mean(values)
    return sum((x - mean) ** 2 for x in values) / len(values)


# --- Buffer management ---

def _get_buffer_path(memory_dir: str) -> Path:
    return Path(memory_dir) / BUFFER_FILENAME


def _get_log_path(memory_dir: str) -> Path:
    return Path(memory_dir) / LOG_FILENAME


def load_buffer(memory_dir: str) -> list[dict]:
    """Load the observation buffer. Returns empty list if not found."""
    filepath = _get_buffer_path(memory_dir)
    data = _load_json(filepath)
    if data is None or "observations" not in data:
        return []
    obs = data.get("observations", [])
    if not isinstance(obs, list):
        return []
    return obs


def save_buffer(memory_dir: str, observations: list[dict]) -> None:
    """Save the observation buffer."""
    filepath = _get_buffer_path(memory_dir)
    _atomic_write_json(filepath, {"observations": observations})


# --- Log management ---

def load_log(memory_dir: str) -> list[dict]:
    """Load the entry log. Returns empty list if not found."""
    filepath = _get_log_path(memory_dir)
    data = _load_json(filepath)
    if data is None or "entries" not in data:
        return []
    entries = data.get("entries", [])
    if not isinstance(entries, list):
        return []
    return entries


def save_log(memory_dir: str, entries: list[dict]) -> None:
    """Save the entry log."""
    filepath = _get_log_path(memory_dir)
    _atomic_write_json(filepath, {"entries": entries})


# --- Aggregation ---

def _aggregate_window(observations: list[dict]) -> dict:
    """Aggregate a list of observations into window statistics.

    Returns a dict with:
    - axis_stats: {axis_name: {mean, variance}} for each emotion axis
    - phase_distribution: {phase: count} normalized to fractions
    - change_frequency: number of significant changes between consecutive observations
    - observation_count: number of observations in window
    - timestamp_start: earliest observation timestamp
    - timestamp_end: latest observation timestamp
    """
    if not observations:
        return {
            "axis_stats": {a: {"mean": 0.0, "variance": 0.0} for a in AXIS_NAMES},
            "phase_distribution": {"normal": 1.0, "peak": 0.0, "rebound": 0.0},
            "change_frequency": 0,
            "observation_count": 0,
            "timestamp_start": "",
            "timestamp_end": "",
        }

    # Collect samples per axis
    axis_samples: dict[str, list[float]] = {a: [] for a in AXIS_NAMES}
    phases: list[str] = []

    for obs in observations:
        for axis in AXIS_NAMES:
            val = obs.get(axis)
            if isinstance(val, (int, float)):
                axis_samples[axis].append(float(val))
        phase = obs.get("phase", "normal")
        if phase in VALID_PHASES:
            phases.append(phase)

    # Compute axis stats
    axis_stats = {}
    for axis in AXIS_NAMES:
        samples = axis_samples[axis]
        axis_stats[axis] = {
            "mean": round(_compute_mean(samples), 4),
            "variance": round(_compute_variance(samples), 4),
        }

    # Phase distribution (fractions)
    phase_dist: dict[str, float] = {"normal": 0.0, "peak": 0.0, "rebound": 0.0}
    if phases:
        for p in phases:
            phase_dist[p] = phase_dist.get(p, 0.0) + 1.0
        total = len(phases)
        for p in phase_dist:
            phase_dist[p] = round(phase_dist[p] / total, 4)

    # Change frequency: count pairs where any axis changed by > 0.05
    change_count = 0
    for i in range(1, len(observations)):
        prev = observations[i - 1]
        curr = observations[i]
        for axis in AXIS_NAMES:
            pv = prev.get(axis, 0.0)
            cv = curr.get(axis, 0.0)
            if isinstance(pv, (int, float)) and isinstance(cv, (int, float)):
                if abs(float(cv) - float(pv)) > 0.05:
                    change_count += 1
                    break  # count once per pair

    # Timestamps
    timestamps = [obs.get("timestamp", "") for obs in observations if obs.get("timestamp")]
    ts_start = timestamps[0] if timestamps else ""
    ts_end = timestamps[-1] if timestamps else ""

    return {
        "axis_stats": axis_stats,
        "phase_distribution": phase_dist,
        "change_frequency": change_count,
        "observation_count": len(observations),
        "timestamp_start": ts_start,
        "timestamp_end": ts_end,
    }


# --- Core functions ---

def record_observation(
    memory_dir: str,
    emotion_state: dict | None = None,
    dynamics_phase: str = "normal",
) -> dict:
    """Record a single observation of the current emotion state.

    If the observation buffer reaches WINDOW_SIZE, aggregates into an entry
    and appends to the log.

    Args:
        memory_dir: Path to memory directory.
        emotion_state: Dict with fulfillment/tension/affinity values.
            If None, reads from emotion_state.json.
        dynamics_phase: Current dynamics phase (normal/peak/rebound).

    Returns:
        Dict with:
        - status: "buffered" or "aggregated"
        - buffer_size: current buffer size after operation
        - entry: the new entry dict if aggregated, None otherwise
    """
    # Load or use provided emotion state
    if emotion_state is None:
        try:
            from emotion_state import load_state
            emotion_state = load_state(memory_dir)
        except Exception:
            emotion_state = {}

    # Validate phase
    if dynamics_phase not in VALID_PHASES:
        dynamics_phase = "normal"

    # Build observation
    observation = {
        "timestamp": _now_iso(),
        "phase": dynamics_phase,
    }
    for axis in AXIS_NAMES:
        val = emotion_state.get(axis, 0.0)
        if isinstance(val, (int, float)):
            observation[axis] = round(float(val), 4)
        else:
            observation[axis] = 0.0

    # Add to buffer
    buffer = load_buffer(memory_dir)
    buffer.append(observation)

    # Check if window is complete
    if len(buffer) >= WINDOW_SIZE:
        # Aggregate
        entry = _aggregate_window(buffer)

        # Assign entry ID
        log = load_log(memory_dir)
        next_id = len(log) + 1
        entry["entry_id"] = next_id
        entry["created_at"] = _now_iso()

        # Append to log (FIFO trim)
        log.append(entry)
        if len(log) > MAX_ENTRIES:
            log = log[-MAX_ENTRIES:]

        # Save log and clear buffer
        save_log(memory_dir, log)
        save_buffer(memory_dir, [])

        return {
            "status": "aggregated",
            "buffer_size": 0,
            "entry": entry,
        }
    else:
        # Save buffer
        save_buffer(memory_dir, buffer)
        return {
            "status": "buffered",
            "buffer_size": len(buffer),
            "entry": None,
        }


def get_long_term_stats(memory_dir: str, last_n: int = 10) -> dict:
    """Get long-term statistics from the entry log.

    Args:
        memory_dir: Path to memory directory.
        last_n: Number of recent entries to consider (default 10).

    Returns:
        Dict with:
        - total_entries: total entries in log
        - entries_used: how many entries were used for this summary
        - overall_axis_means: {axis: mean across entries}
        - overall_axis_variance_means: {axis: mean of per-window variances}
        - phase_distribution_average: {phase: average fraction across entries}
        - change_frequency_mean: average change frequency across entries
        - trend: {axis: "rising"/"falling"/"stable"} based on first vs last half
        - buffer_pending: number of observations waiting in buffer
        - entries: the raw entry dicts used
    """
    log = load_log(memory_dir)
    buffer = load_buffer(memory_dir)

    if not log:
        return {
            "total_entries": 0,
            "entries_used": 0,
            "overall_axis_means": {a: 0.0 for a in AXIS_NAMES},
            "overall_axis_variance_means": {a: 0.0 for a in AXIS_NAMES},
            "phase_distribution_average": {"normal": 1.0, "peak": 0.0, "rebound": 0.0},
            "change_frequency_mean": 0.0,
            "trend": {a: "stable" for a in AXIS_NAMES},
            "buffer_pending": len(buffer),
            "entries": [],
        }

    # Take last_n entries
    entries = log[-last_n:] if last_n > 0 else log

    # Overall axis means (mean of per-window means)
    overall_means: dict[str, float] = {}
    overall_var_means: dict[str, float] = {}
    for axis in AXIS_NAMES:
        means = [
            e.get("axis_stats", {}).get(axis, {}).get("mean", 0.0)
            for e in entries
        ]
        variances = [
            e.get("axis_stats", {}).get(axis, {}).get("variance", 0.0)
            for e in entries
        ]
        overall_means[axis] = round(_compute_mean(means), 4)
        overall_var_means[axis] = round(_compute_mean(variances), 4)

    # Phase distribution average
    phase_avg: dict[str, float] = {"normal": 0.0, "peak": 0.0, "rebound": 0.0}
    for entry in entries:
        pd = entry.get("phase_distribution", {})
        for p in phase_avg:
            phase_avg[p] += pd.get(p, 0.0)
    n = len(entries)
    for p in phase_avg:
        phase_avg[p] = round(phase_avg[p] / n, 4)

    # Change frequency mean
    freqs = [e.get("change_frequency", 0) for e in entries]
    freq_mean = round(_compute_mean(freqs), 2)

    # Trend: compare first half vs second half means
    trend: dict[str, str] = {}
    if len(entries) >= 2:
        mid = len(entries) // 2
        first_half = entries[:mid]
        second_half = entries[mid:]
        for axis in AXIS_NAMES:
            first_means = [
                e.get("axis_stats", {}).get(axis, {}).get("mean", 0.0)
                for e in first_half
            ]
            second_means = [
                e.get("axis_stats", {}).get(axis, {}).get("mean", 0.0)
                for e in second_half
            ]
            fm = _compute_mean(first_means)
            sm = _compute_mean(second_means)
            diff = sm - fm
            if diff > 0.03:
                trend[axis] = "rising"
            elif diff < -0.03:
                trend[axis] = "falling"
            else:
                trend[axis] = "stable"
    else:
        trend = {a: "stable" for a in AXIS_NAMES}

    return {
        "total_entries": len(log),
        "entries_used": len(entries),
        "overall_axis_means": overall_means,
        "overall_axis_variance_means": overall_var_means,
        "phase_distribution_average": phase_avg,
        "change_frequency_mean": freq_mean,
        "trend": trend,
        "buffer_pending": len(buffer),
        "entries": entries,
    }


def format_stats(stats: dict) -> str:
    """Format long-term stats as a human-readable string."""
    if stats.get("total_entries", 0) == 0:
        pending = stats.get("buffer_pending", 0)
        if pending > 0:
            return f"No long-term entries yet ({pending} observations buffered, {WINDOW_SIZE} needed for first entry)."
        return "No long-term dynamics data recorded yet."

    lines = []
    lines.append(f"Long-term dynamics: {stats['total_entries']} entries total, showing last {stats['entries_used']}")
    lines.append("")

    # Axis means
    lines.append("Axis averages (across windows):")
    for axis in AXIS_NAMES:
        mean = stats["overall_axis_means"].get(axis, 0.0)
        var = stats["overall_axis_variance_means"].get(axis, 0.0)
        trend = stats["trend"].get(axis, "stable")
        lines.append(f"  {axis}: mean={mean:+.4f}, avg_variance={var:.4f}, trend={trend}")

    lines.append("")

    # Phase distribution
    pd = stats["phase_distribution_average"]
    lines.append(f"Phase distribution: normal={pd.get('normal', 0):.1%}, peak={pd.get('peak', 0):.1%}, rebound={pd.get('rebound', 0):.1%}")

    # Change frequency
    lines.append(f"Avg change frequency per window: {stats['change_frequency_mean']:.1f}")

    # Buffer
    pending = stats.get("buffer_pending", 0)
    if pending > 0:
        lines.append(f"Buffer: {pending}/{WINDOW_SIZE} observations pending")

    return "\n".join(lines)
