"""Dampening Counter - Pipeline 2: Dampening連続適用制限.

Tracks consecutive dampening applications in emotion_react.
When consecutive dampening count reaches MAX_CONSECUTIVE, forces
dampening_factor to 1.0 (no suppression) for one cycle.

State is persisted to a JSON file in the memory directory.
Session start resets the counter.

READ-ONLY with respect to emotion state. Only modifies its own counter file.
"""

import json
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

# --- Constants ---

COUNTER_FILENAME = "dampening_counter.json"
MAX_CONSECUTIVE_DAMPENING = 5
DEFAULT_COUNTER_STATE = {"consecutive_count": 0, "last_dampened": False}


# --- Persistence ---

def _get_counter_path(memory_dir: str) -> str:
    """Return absolute path to dampening counter state file."""
    return os.path.join(os.path.abspath(memory_dir), COUNTER_FILENAME)


def load_counter(memory_dir: str) -> dict:
    """Load counter state from file. Returns default on missing/corrupt file."""
    filepath = _get_counter_path(memory_dir)
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning("dampening_counter: invalid data type, returning default")
            return dict(DEFAULT_COUNTER_STATE)
        result = dict(DEFAULT_COUNTER_STATE)
        if isinstance(data.get("consecutive_count"), int):
            result["consecutive_count"] = max(0, data["consecutive_count"])
        if isinstance(data.get("last_dampened"), bool):
            result["last_dampened"] = data["last_dampened"]
        return result
    except FileNotFoundError:
        return dict(DEFAULT_COUNTER_STATE)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        logger.warning("dampening_counter: failed to load state: %s", e)
        return dict(DEFAULT_COUNTER_STATE)


def save_counter(memory_dir: str, state: dict) -> None:
    """Save counter state atomically (tmp + rename)."""
    filepath = _get_counter_path(memory_dir)
    try:
        dir_path = os.path.dirname(filepath)
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f)
            os.replace(tmp_path, filepath)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as e:
        logger.error("dampening_counter: failed to save state: %s", e)


def reset_counter(memory_dir: str) -> dict:
    """Reset counter to default state. Called at session start."""
    state = dict(DEFAULT_COUNTER_STATE)
    save_counter(memory_dir, state)
    logger.info("dampening_counter: reset to default")
    return state


# --- Core Logic ---

def check_and_update(memory_dir: str, dampening_factor: float) -> float:
    """Check dampening counter and return effective dampening factor.

    If dampening has been consecutively applied MAX_CONSECUTIVE times,
    returns 1.0 (no suppression) and resets the counter.
    Otherwise, returns the original dampening_factor.

    Args:
        memory_dir: Path to memory directory.
        dampening_factor: The dampening factor from stability valve (0.0-1.0).

    Returns:
        Effective dampening factor (original or 1.0 if reset triggered).
    """
    counter = load_counter(memory_dir)
    is_dampened = dampening_factor < 1.0

    if is_dampened:
        counter["consecutive_count"] += 1
        counter["last_dampened"] = True

        if counter["consecutive_count"] >= MAX_CONSECUTIVE_DAMPENING:
            logger.info(
                "dampening_counter: consecutive limit reached (%d), "
                "forcing dampening=1.0",
                counter["consecutive_count"],
            )
            counter["consecutive_count"] = 0
            counter["last_dampened"] = False
            save_counter(memory_dir, counter)
            return 1.0
    else:
        counter["consecutive_count"] = 0
        counter["last_dampened"] = False

    save_counter(memory_dir, counter)
    return dampening_factor
