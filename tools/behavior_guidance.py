"""Behavior Guidance - Pipeline 1: Tone Directive Injection (v2).

Converts Gap Analysis + emotion state + dynamics phase into concrete
behavioral guidance with specific action recommendations.

Does NOT modify any state files (READ-ONLY).
Stateless per call, except for in-memory saturation tracking.
"""

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

# --- Constants ---

EMOTION_STATE_FILENAME = "emotion_state.json"
DYNAMICS_STATE_FILENAME = "dynamics_state.json"
GAP_ANALYSIS_PREFIX = "gap_analysis_"
MAX_GAP_FILE_SIZE = 100_000  # 100KB safety limit

# Priority ordering for gap sorting
PRIORITY_ORDER = {"高": 0, "中": 1, "低": 2}

# Saturation prevention
SATURATION_VARIANCE_THRESHOLD = 0.05
SATURATION_CONSECUTIVE_LIMIT = 8

# --- In-memory saturation state (per-process, not persisted) ---

_saturation_history: list[tuple[float, float, float]] = []


def _reset_saturation() -> None:
    """Clear saturation history (for testing)."""
    global _saturation_history
    _saturation_history = []


# --- Gap Analysis Reading ---

def _read_gap_analysis(docs_dir: str) -> list[dict]:
    """Read remaining gaps from the latest gap_analysis_*.md file.

    Returns list of dicts with keys: id, description, priority, status.
    Returns empty list on any failure or if no gaps found.
    """
    if not os.path.isdir(docs_dir):
        return []

    try:
        gap_files = sorted(
            [f for f in os.listdir(docs_dir) if f.startswith(GAP_ANALYSIS_PREFIX) and f.endswith(".md")],
            reverse=True,
        )
    except OSError as e:
        logger.warning("behavior_guidance: failed to list docs dir: %s", e)
        return []

    if not gap_files:
        return []

    filepath = os.path.join(docs_dir, gap_files[0])

    try:
        file_size = os.path.getsize(filepath)
        if file_size > MAX_GAP_FILE_SIZE:
            logger.warning("behavior_guidance: gap file too large: %d bytes", file_size)
            return []
    except OSError:
        return []

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("behavior_guidance: failed to read gap file: %s", e)
        return []

    # Parse markdown table rows matching: | G<num> <title> | description | priority | status |
    # Real format: "| G4 Discord中継 | GitHub→Discord通知 | 低 | 未着手（目的不明確） |"
    # Also matches test format: "| G4 | description | 低 | status |"
    gap_pattern = re.compile(
        r"^\|\s*(G\d+)\s+(.+?)\s*\|\s*(.+?)\s*\|\s*(高|中|低)\s*\|\s*(.+?)\s*\|",
        re.MULTILINE,
    )

    gaps = []
    for match in gap_pattern.finditer(content):
        gap_id = match.group(1).strip()
        title = match.group(2).strip()
        description = match.group(3).strip()
        priority = match.group(4).strip()
        status = match.group(5).strip()
        # Combine title and description for full context
        full_desc = f"{title}: {description}" if title else description
        gaps.append({
            "id": gap_id,
            "description": full_desc,
            "priority": priority,
            "status": status,
        })

    return gaps


# --- Emotion State Reading ---

def _read_emotion_state(memory_dir: str) -> dict | None:
    """Read emotion_state.json + dynamics_state.json into a combined dict.

    Returns dict with keys: fulfillment, tension, affinity, phase.
    Returns None on any failure.
    """
    emotion_path = os.path.join(memory_dir, EMOTION_STATE_FILENAME)
    dynamics_path = os.path.join(memory_dir, DYNAMICS_STATE_FILENAME)

    try:
        with open(emotion_path, "r", encoding="utf-8") as f:
            emotion_data = json.load(f)
        if not isinstance(emotion_data, dict):
            return None
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None

    try:
        with open(dynamics_path, "r", encoding="utf-8") as f:
            dynamics_data = json.load(f)
        if not isinstance(dynamics_data, dict):
            return None
        phase = dynamics_data.get("phase")
        if phase not in ("normal", "peak", "rebound"):
            return None
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None

    return {
        "fulfillment": float(emotion_data.get("fulfillment", 0.0)),
        "tension": float(emotion_data.get("tension", 0.0)),
        "affinity": float(emotion_data.get("affinity", 0.0)),
        "phase": phase,
    }


# --- Saturation Check ---

def _check_saturation(fulfillment: float, tension: float, affinity: float) -> bool:
    """Check if emotion state has been stagnant (saturated).

    Returns True if guidance should be suppressed due to saturation.
    """
    global _saturation_history

    current = (fulfillment, tension, affinity)
    _saturation_history.append(current)

    max_history = SATURATION_CONSECUTIVE_LIMIT + 1
    if len(_saturation_history) > max_history:
        _saturation_history = _saturation_history[-max_history:]

    if len(_saturation_history) < SATURATION_CONSECUTIVE_LIMIT + 1:
        return False

    window = _saturation_history[-(SATURATION_CONSECUTIVE_LIMIT + 1):]
    for axis_idx in range(3):
        values = [entry[axis_idx] for entry in window]
        spread = max(values) - min(values)
        if spread >= SATURATION_VARIANCE_THRESHOLD:
            return False

    return True


# --- Action Recommendation ---

def _classify_state(emotion: dict) -> tuple[str, str]:
    """Classify emotion+phase into state description and task recommendation type.

    Returns (state_description, task_type).
    task_type is one of: "big", "cautious", "small", "recovery", "challenge"
    """
    phase = emotion["phase"]
    fulfillment = emotion["fulfillment"]
    tension = emotion["tension"]

    # Phase rules take priority
    if phase == "rebound":
        return "回復中", "recovery"

    if phase == "peak":
        return "勢いがある状態", "challenge"

    # Axis rules (normal phase)
    if tension > 0.3:
        return "慎重さが求められる状態", "cautious"

    if fulfillment > 0.3 and tension <= 0.3:
        return "集中力が高い状態（充実感高め・緊張低め）", "big"

    if fulfillment < -0.3:
        return "エネルギーが低い状態", "small"

    return "通常状態", "big"


def _sort_gaps_by_priority(gaps: list[dict]) -> list[dict]:
    """Sort gaps by priority (高 > 中 > 低)."""
    return sorted(gaps, key=lambda g: PRIORITY_ORDER.get(g["priority"], 99))


def _select_gap_for_task_type(gaps: list[dict], task_type: str) -> dict | None:
    """Select the most appropriate gap for the current task type."""
    sorted_gaps = _sort_gaps_by_priority(gaps)

    if task_type == "recovery":
        # Recovery: prefer low/medium priority, avoid high
        for g in reversed(sorted_gaps):
            if g["priority"] in ("低", "中"):
                return g
        return sorted_gaps[-1] if sorted_gaps else None

    if task_type == "small":
        # Low energy: prefer lowest priority
        for g in reversed(sorted_gaps):
            if g["priority"] == "低":
                return g
        return sorted_gaps[-1] if sorted_gaps else None

    if task_type == "cautious":
        # Cautious: prefer medium priority, avoid large
        for g in sorted_gaps:
            if g["priority"] == "中":
                return g
        return sorted_gaps[-1] if sorted_gaps else None

    # "big" or "challenge": pick highest priority
    return sorted_gaps[0] if sorted_gaps else None


def _recommend_action(gaps: list[dict], emotion: dict) -> str:
    """Generate concrete action recommendation from gaps and emotion state.

    Returns recommendation text string.
    """
    state_desc, task_type = _classify_state(emotion)

    if not gaps:
        return f"状態: {state_desc}\n推奨: 残存ギャップなし。ドキュメント整理や品質改善を検討。"

    selected = _select_gap_for_task_type(gaps, task_type)
    if selected is None:
        return f"状態: {state_desc}\n推奨: 残存ギャップなし。"

    lines = [f"状態: {state_desc}"]

    if task_type == "big":
        lines.append(
            f"推奨: {selected['id']}({selected['description']})が{selected['priority']}優先度で未解決。"
            f"今の落ち着いた状態なら取り組むのに適している。"
        )
    elif task_type == "challenge":
        lines.append(
            f"推奨: 勢いを活かして{selected['id']}({selected['description']})に取り組む。"
            f"チャレンジングなタスクに適した状態。"
        )
    elif task_type == "cautious":
        lines.append(
            f"推奨: 大きな設計判断は避け、既存タスクの仕上げに集中。"
            f"{selected['id']}({selected['description']})の修正・詰めを推奨。"
        )
    elif task_type == "small":
        lines.append(
            f"推奨: 小さなタスクから始める。"
            f"{selected['id']}({selected['description']})が低負荷で取り組みやすい。"
        )
    elif task_type == "recovery":
        lines.append(
            f"推奨: 大きな方向転換を避ける。"
            f"{selected['id']}({selected['description']})のような小〜中規模タスクが適切。"
        )

    # Add phase-specific note
    phase = emotion["phase"]
    if phase == "rebound":
        lines.append("注意: REBOUNDフェーズ。大きな判断を保留し安定を優先。")
    elif phase == "peak":
        lines.append("注意: PEAKフェーズ。勢いはあるが基本に忠実に。")
    elif emotion["tension"] > 0.3:
        lines.append("注意: 緊張高め。新規サイクル開始よりも品質の詰めを優先。")

    return "\n".join(lines)


# --- Public API ---

def generate_guidance(memory_dir: str, docs_dir: str = "") -> str:
    """Generate behavioral guidance text from Gap Analysis + emotion state.

    Args:
        memory_dir: Directory containing emotion_state.json and dynamics_state.json.
        docs_dir: Directory containing gap_analysis_*.md files.

    Returns:
        Guidance string or empty string if state files are missing/corrupt
        or saturation cutoff is active.
    """
    emotion = _read_emotion_state(memory_dir)
    if emotion is None:
        return ""

    fulfillment = emotion["fulfillment"]
    tension = emotion["tension"]
    affinity = emotion["affinity"]

    if _check_saturation(fulfillment, tension, affinity):
        return ""

    gaps = _read_gap_analysis(docs_dir) if docs_dir else []

    return _recommend_action(gaps, emotion)
