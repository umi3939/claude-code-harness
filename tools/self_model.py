#!/usr/bin/env python3
"""Self-state observation module for Claude Code MCP.

Provides a stateless, READ-ONLY integrated snapshot of current internal state
by observing emotion, dynamics, change history, and memory systems.

Design: design_self_model_mcp.md

Key principles:
- Completely stateless: no persistence, no accumulation
- READ-ONLY: never writes to any observed system
- Abstract descriptions only: no raw numbers exposed
- No evaluation: no "good"/"bad"/"healthy"/"abnormal" judgments
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add the tools directory to sys.path
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import emotion_state
import emotion_dynamics


# --- Axis category mapping ---

def _axis_to_category(value: float) -> str:
    """Convert a numeric axis value to an abstract category.

    Thresholds (design doc):
      <= -0.7: strongly_negative
      <= -0.3: negative
      -0.3 ~ +0.3: neutral
      >= +0.3: positive
      >= +0.7: strongly_positive
    """
    if value >= 0.7:
        return "strongly_positive"
    if value >= 0.3:
        return "positive"
    if value <= -0.7:
        return "strongly_negative"
    if value <= -0.3:
        return "negative"
    return "neutral"


_CATEGORY_JA = {
    "strongly_positive": "強く正",
    "positive": "やや正",
    "neutral": "中立",
    "negative": "やや負",
    "strongly_negative": "強く負",
}

_AXIS_JA = {
    "fulfillment": "充実感",
    "tension": "緊張",
    "affinity": "親和性",
}

_PHASE_JA = {
    "normal": "通常フェーズ",
    "peak": "ピークフェーズ",
    "rebound": "リバウンドフェーズ",
}


# --- Section 1: Emotion observation ---

def _observe_emotion(memory_dir: str) -> dict:
    """Observe current emotion state and dynamics phase.

    Returns:
        {
            "fulfillment": category_str,
            "tension": category_str,
            "affinity": category_str,
            "dynamics_phase": phase_str,
            "description": abstract_text,
        }
    """
    state = emotion_state.load_state(memory_dir)

    categories = {}
    for axis in emotion_state.ALL_AXES:
        val = state.get(axis, 0.0)
        categories[axis] = _axis_to_category(float(val))

    # Get dynamics phase
    dyn_state = emotion_dynamics.load_dynamics_state(memory_dir)
    phase = dyn_state.get("phase", "normal")

    # Build description
    parts = []
    for axis in emotion_state.ALL_AXES:
        ja_axis = _AXIS_JA.get(axis, axis)
        ja_cat = _CATEGORY_JA.get(categories[axis], categories[axis])
        parts.append(f"{ja_axis}は{ja_cat}")

    ja_phase = _PHASE_JA.get(phase, phase)
    description = f"{parts[0]}、{parts[1]}、{parts[2]}。感情動力学は{ja_phase}。"

    return {
        "fulfillment": categories["fulfillment"],
        "tension": categories["tension"],
        "affinity": categories["affinity"],
        "dynamics_phase": phase,
        "description": description,
    }


# --- Section 2: Change observation ---

def _compute_trend(changes: list[dict], axis: str) -> str:
    """Compute trend for a single axis from change history entries.

    Args:
        changes: List of change history entries (newest first from get_change_history).
        axis: Axis name to analyze.

    Returns:
        "rising", "falling", "stable", or "fluctuating"
    """
    if not changes:
        return "stable"

    diffs = []
    for entry in changes:
        before = entry.get("before", {})
        after = entry.get("after", {})
        b = before.get(axis, 0.0)
        a = after.get(axis, 0.0)
        diffs.append(a - b)

    # Check if all diffs are negligible
    if all(abs(d) < 0.05 for d in diffs):
        return "stable"

    positive_count = sum(1 for d in diffs if d > 0)
    negative_count = sum(1 for d in diffs if d < 0)

    if positive_count >= 3:
        return "rising"
    if negative_count >= 3:
        return "falling"
    return "fluctuating"


def _observe_changes(memory_dir: str) -> dict:
    """Observe recent change patterns.

    Returns:
        {
            "trends": {axis: trend_str},
            "frequency": str,
            "description": abstract_text,
        }
    """
    changes = emotion_state.get_change_history(memory_dir, limit=5)

    count = len(changes)

    # Compute trends for each axis
    trends = {}
    for axis in emotion_state.ALL_AXES:
        trends[axis] = _compute_trend(changes, axis)

    # Frequency
    if count >= 5:
        frequency = "high"
    elif count >= 3:
        frequency = "moderate"
    elif count >= 1:
        frequency = "low"
    else:
        frequency = "none"

    _FREQ_JA = {
        "high": "高頻度",
        "moderate": "中頻度",
        "low": "低頻度",
        "none": "なし",
    }
    _TREND_JA = {
        "rising": "上昇傾向",
        "falling": "下降傾向",
        "stable": "安定",
        "fluctuating": "変動",
    }

    trend_parts = []
    for axis in emotion_state.ALL_AXES:
        ja_axis = _AXIS_JA.get(axis, axis)
        ja_trend = _TREND_JA.get(trends[axis], trends[axis])
        trend_parts.append(f"{ja_axis}は{ja_trend}")

    ja_freq = _FREQ_JA.get(frequency, frequency)
    description = f"変化頻度は{ja_freq}。{trend_parts[0]}、{trend_parts[1]}、{trend_parts[2]}。"

    return {
        "trends": trends,
        "frequency": frequency,
        "description": description,
    }


# --- Section 3: Memory observation ---

def _count_episodes(memory_dir: str) -> tuple[int, str]:
    """Count total episodes and find last episode age.

    Returns:
        (episode_count, last_episode_age_text)
    """
    episodes_dir = Path(memory_dir) / "episodes"
    if not episodes_dir.exists():
        return 0, "不明"

    total_episodes = 0
    latest_timestamp = None

    session_files = [
        f for f in episodes_dir.iterdir()
        if f.is_file() and f.name.startswith("session_") and f.name.endswith(".json")
    ]

    for sf in session_files:
        try:
            data = json.loads(sf.read_text(encoding="utf-8"))
            episodes = data.get("episodes", [])
            total_episodes += len(episodes)
            for ep in episodes:
                ts_str = ep.get("timestamp", "")
                ts = emotion_state._parse_iso(ts_str)
                if ts is not None:
                    if latest_timestamp is None or ts > latest_timestamp:
                        latest_timestamp = ts
        except (json.JSONDecodeError, OSError):
            continue

    if latest_timestamp is None:
        return total_episodes, "不明"

    now = datetime.now(timezone.utc)
    elapsed_seconds = (now - latest_timestamp).total_seconds()

    if elapsed_seconds < 3600:  # less than 1 hour
        age_text = "数分前"
    elif elapsed_seconds < 86400:  # less than 1 day
        age_text = "数時間前"
    else:
        age_text = "数日前"

    return total_episodes, age_text


def _count_stm_entries(memory_dir: str) -> int:
    """Count STM entries by reading stm.json (short_term_memory.json) directly."""
    stm_path = Path(memory_dir) / "short_term_memory.json"
    if not stm_path.exists():
        return 0
    try:
        data = json.loads(stm_path.read_text(encoding="utf-8"))
        entries = data.get("entries", [])
        if isinstance(entries, list):
            return len(entries)
        return 0
    except (json.JSONDecodeError, OSError):
        return 0


def _observe_memory(memory_dir: str) -> dict:
    """Observe memory statistics.

    Returns:
        {
            "episode_count": int,
            "last_episode_age": str,
            "stm_entries": int,
            "description": abstract_text,
        }
    """
    episode_count, last_age = _count_episodes(memory_dir)
    stm_entries = _count_stm_entries(memory_dir)

    parts = []
    if episode_count > 0:
        parts.append(f"記憶には{episode_count}のエピソードがあり、最終記録は{last_age}")
    else:
        parts.append("エピソード記録はまだない")

    if stm_entries > 0:
        parts.append(f"短期記憶に{stm_entries}つの項目がある")
    else:
        parts.append("短期記憶は空")

    description = f"{parts[0]}。{parts[1]}。"

    return {
        "episode_count": episode_count,
        "last_episode_age": last_age,
        "stm_entries": stm_entries,
        "description": description,
    }


# --- Section 4: Integration ---

def _integrate(emotion: dict, change: dict, memory: dict) -> str:
    """Integrate three sections into a 2-3 sentence abstract description.

    No numbers, no evaluation, facts only.
    """
    # Emotion summary
    emo_desc = emotion.get("description", "")

    # Change summary - pick the most notable aspect
    frequency = change.get("frequency", "none")
    if frequency == "none":
        change_summary = "最近の変化は記録されていない"
    elif frequency == "high":
        change_summary = "最近の変化は活発"
    elif frequency == "moderate":
        change_summary = "最近の変化はある程度"
    else:
        change_summary = "最近の変化は少なめ"

    # Identify dominant trend
    trends = change.get("trends", {})
    trend_word = {"rising": "上昇", "falling": "下降"}
    notable_trends = [
        f"{_AXIS_JA.get(axis, axis)}が{trend_word[t]}"
        for axis, t in trends.items()
        if t in ("rising", "falling")
    ]
    if notable_trends:
        change_summary += f"で、{'/'.join(notable_trends)}傾向"

    # Memory summary
    ep_count = memory.get("episode_count", 0)
    stm = memory.get("stm_entries", 0)
    if ep_count > 0:
        memory_summary = f"記憶には{ep_count}のエピソードがあり、短期記憶に{stm}つの項目がある"
    else:
        memory_summary = "記憶のエピソードはまだなく、短期記憶は空"

    # Compose 2-3 sentences
    sentences = [emo_desc.rstrip("。"), change_summary, memory_summary]
    return "。".join(sentences) + "。"


# --- Public API ---

def observe(memory_dir: str) -> dict:
    """Generate an integrated self-state snapshot.

    Completely stateless, READ-ONLY observation.
    Each call produces a fresh snapshot from current data.
    No accumulation, no history tracking.

    Returns:
        {
            "emotion": {...},      # Section 1: emotion state
            "change": {...},       # Section 2: change patterns
            "memory": {...},       # Section 3: memory statistics
            "integrated": str,     # Section 4: integrated description
        }
    """
    emo = _observe_emotion(memory_dir)
    chg = _observe_changes(memory_dir)
    mem = _observe_memory(memory_dir)
    integrated = _integrate(emo, chg, mem)

    return {
        "emotion": emo,
        "change": chg,
        "memory": mem,
        "integrated": integrated,
    }
