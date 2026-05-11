"""Coherence Alert - Pipeline 3: Coherence状態によるblocking制御.

Generates blocking alerts when identity coherence is disconnected.
Called from behavior-guard.js for Agent/TeamCreate invocations.

disconnected -> exit(2) blocking + thinker起動を促すメッセージ.
stable/slightly_shifting -> exit(0) pass-through.
Does NOT modify any state files (READ-ONLY w.r.t. coherence state).
"""

import json
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

# --- Constants ---

NOTIFICATION_COOLDOWN = 5  # Number of hook firings between same-level notifications
COOLDOWN_FILENAME = "coherence_cooldown.json"
ALERT_LEVELS = ("disconnected",)  # unsettled is normal session-decay fluctuation
DEFAULT_COOLDOWN_STATE = {
    "last_alert_level": "",
    "call_count_since_last": NOTIFICATION_COOLDOWN,
}


# --- Cooldown Persistence ---

def _get_cooldown_path(data_dir: str) -> str:
    """Return path to cooldown state file."""
    return os.path.join(data_dir, COOLDOWN_FILENAME)


def _load_cooldown_state(state_file: str) -> dict:
    """Load cooldown state. Returns default on missing/corrupt file."""
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return dict(DEFAULT_COOLDOWN_STATE)
        result = dict(DEFAULT_COOLDOWN_STATE)
        if isinstance(data.get("last_alert_level"), str):
            result["last_alert_level"] = data["last_alert_level"]
        if isinstance(data.get("call_count_since_last"), int):
            result["call_count_since_last"] = max(0, data["call_count_since_last"])
        return result
    except FileNotFoundError:
        return dict(DEFAULT_COOLDOWN_STATE)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        logger.warning("coherence_alert: failed to load cooldown: %s", e)
        return dict(DEFAULT_COOLDOWN_STATE)


def _save_cooldown_state(state_file: str, state: dict) -> None:
    """Save cooldown state atomically."""
    try:
        dir_path = os.path.dirname(state_file)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=dir_path or ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f)
            os.replace(tmp_path, state_file)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as e:
        logger.error("coherence_alert: failed to save cooldown: %s", e)


# --- Alert Generation ---

def generate_coherence_alert(coherence_level: str | None) -> dict:
    """Generate alert for coherence level.

    Returns dict with keys:
        text: Alert message string (empty if no alert).
        should_block: True if this level should block execution.
    """
    if not coherence_level or coherence_level not in ALERT_LEVELS:
        return {"text": "", "should_block": False}

    if coherence_level == "disconnected":
        return {
            "text": (
                "[Coherence Alert] 自己一貫性が断絶しています。"
                "大きな判断を保留し、thinkerで原因を調査してください。"
            ),
            "should_block": True,
        }

    return {"text": "", "should_block": False}


def check_and_notify(coherence_level: str | None, data_dir: str) -> dict:
    """Check coherence level with cooldown and return notification if appropriate.

    Returns dict with keys:
        text: Alert text (empty string if suppressed by cooldown).
        should_block: True if coherence level requires blocking (even if text suppressed).

    Cooldown only suppresses the text message, not the blocking behavior.
    """
    state_file = _get_cooldown_path(data_dir)
    cooldown = _load_cooldown_state(state_file)

    # Not an alert level -> increment counter, no notification, no block
    if not coherence_level or coherence_level not in ALERT_LEVELS:
        cooldown["call_count_since_last"] += 1
        _save_cooldown_state(state_file, cooldown)
        return {"text": "", "should_block": False}

    # Alert level -> always block, but cooldown may suppress text
    alert_result = generate_coherence_alert(coherence_level)

    # Check cooldown for text suppression
    same_level = cooldown["last_alert_level"] == coherence_level
    in_cooldown = cooldown["call_count_since_last"] < NOTIFICATION_COOLDOWN

    if same_level and in_cooldown:
        cooldown["call_count_since_last"] += 1
        _save_cooldown_state(state_file, cooldown)
        return {"text": "", "should_block": True}  # Block but suppress text

    # Generate alert (either different level or cooldown expired)
    cooldown["last_alert_level"] = coherence_level
    cooldown["call_count_since_last"] = 0
    _save_cooldown_state(state_file, cooldown)

    return alert_result
