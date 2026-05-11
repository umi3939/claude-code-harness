#!/usr/bin/env python3
"""Psyche Drive Pathway — Session-internal automatic psyche state updater.

C20-1: Adds the missing "session-internal automatic update" pathway.
Called from UserPromptSubmit hook (via skill_executor.py).

This module:
- Evaluates time-based + phase-transition triggers each turn
- Calls existing psyche functions directly (no MCP, no Claude judgment)
- Writes results to files only (no stdout, no context injection)
- Fails silently (exit 0, no blocking)

Architecture:
  UserPromptSubmit → skill_executor.py → run_psyche_drive()
                                              ↓
                            ┌──────────────────┼──────────────────┐
                      _update_emotion    _update_observation  _update_activation
                            ↓                  ↓                   ↓
                      emotion_react chain  run_snapshot()    activation_surface()
                            ↓                  ↓                   ↓
                      emotion_state.json   (7-module results)  (activation results)
"""

import json
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from pathlib import Path

# --- Path setup ---

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.join(HOOKS_DIR, "..", "tools")

if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

# --- Lazy imports (from tools/) ---
# These are imported at module level for mocking in tests.
# Each is wrapped in the update functions with try/except.

from emotion_reaction import react as emotion_react_fn
from emotion_state import load_state, update_state
from emotion_dynamics import (
    load_dynamics_state,
    check_session_reset as dynamics_session_reset,
    get_current_amplitude as dynamics_get_amplitude,
    update_dynamics as dynamics_update,
    save_dynamics_state,
)
from observation_facade import (
    run_snapshot,
    get_dampening_factor as facade_get_dampening,
    record_long_term as facade_record_long_term,
)
from activation_surface import surface as activation_surface_fn
from short_term_store import load_store, read_entries

# --- Constants ---

# Update intervals in seconds
INTERVALS = {
    "emotion": 300,       # 5 minutes
    "observation": 900,   # 15 minutes
    "activation": 600,    # 10 minutes
}

# Timeout
OVERALL_TIMEOUT = 5.0     # seconds, total budget for all updates
CATEGORY_TIMEOUT = 3.0    # seconds, per-category timeout

# Backoff
BACKOFF_THRESHOLD = 3     # consecutive failures before backoff
BACKOFF_MULTIPLIER = 2    # exponential multiplier
MAX_INTERVAL = 3600       # 60 minutes cap

# State file
STATE_FILENAME = ".psyche-drive-state.json"


# --- PsycheDriveState ---

class PsycheDriveState:
    """Manages the update timing table (JSON file)."""

    def __init__(self, state_dir: str):
        self._path = os.path.join(state_dir, STATE_FILENAME)
        self._data = self._load()

    def _load(self) -> dict:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError, ValueError):
            pass
        return self._default()

    @staticmethod
    def _default() -> dict:
        return {
            "categories": {
                "emotion": {"last_update": 0.0, "last_phase": "", "failure_count": 0},
                "observation": {"last_update": 0.0, "last_phase": "", "failure_count": 0},
                "activation": {"last_update": 0.0, "last_phase": "", "failure_count": 0},
            }
        }

    def _validate_data(self):
        """Ensure _data has the required structure before writing."""
        if not isinstance(self._data, dict):
            self._data = self._default()
            return
        if "categories" not in self._data:
            self._data["categories"] = self._default()["categories"]
        cats = self._data["categories"]
        if not isinstance(cats, dict):
            self._data["categories"] = self._default()["categories"]

    def save(self):
        try:
            # M-S7: Validate data structure before writing
            self._validate_data()
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except OSError:
            pass

    def _cat(self, category: str) -> dict:
        cats = self._data.setdefault("categories", {})
        return cats.setdefault(category, {"last_update": 0.0, "last_phase": "", "failure_count": 0})

    def get_last_update(self, category: str) -> float:
        return self._cat(category).get("last_update", 0.0)

    def get_last_phase(self, category: str) -> str:
        return self._cat(category).get("last_phase", "")

    def get_failure_count(self, category: str) -> int:
        return self._cat(category).get("failure_count", 0)

    def record_update(self, category: str, timestamp: float, phase: str):
        cat = self._cat(category)
        cat["last_update"] = timestamp
        cat["last_phase"] = phase

    def record_failure(self, category: str):
        cat = self._cat(category)
        cat["failure_count"] = cat.get("failure_count", 0) + 1

    def record_success(self, category: str):
        cat = self._cat(category)
        cat["failure_count"] = 0


# --- Interval / Backoff ---

def get_effective_interval(state: PsycheDriveState, category: str) -> float:
    """Get effective interval considering backoff."""
    base = INTERVALS.get(category, 300)
    failures = state.get_failure_count(category)
    if failures < BACKOFF_THRESHOLD:
        return base
    # Exponential backoff: base * 2^(failures // BACKOFF_THRESHOLD)
    exponent = failures // BACKOFF_THRESHOLD
    effective = base * (BACKOFF_MULTIPLIER ** exponent)
    return min(effective, MAX_INTERVAL)


# --- Should Update ---

def should_update(state: PsycheDriveState, category: str, current_phase: str) -> bool:
    """Determine if a category should be updated.

    Returns True if:
    - Phase transition detected (different from last recorded phase), OR
    - Enough time has elapsed since last update (considering backoff)
    """
    last_phase = state.get_last_phase(category)
    last_update = state.get_last_update(category)

    # Phase transition: force update regardless of time
    if last_phase and current_phase and last_phase != current_phase:
        return True

    # Time-based check
    effective_interval = get_effective_interval(state, category)
    elapsed = time.time() - last_update
    return elapsed >= effective_interval


# --- Phase Detection ---

def _get_current_phase() -> str:
    """Read current dev flow phase from .dev-flow-state file."""
    try:
        state_file = os.path.join(HOOKS_DIR, ".dev-flow-state")
        if not os.path.isfile(state_file):
            return ""
        with open(state_file, "r", encoding="utf-8") as f:
            df = json.load(f)
        if not isinstance(df, dict):
            return ""

        phases = [
            ("reviewer", df.get("reviewer", 0) or 0),
            ("post_analysis", df.get("post_analysis", 0) or 0),
            ("impl", df.get("impl", 0) or 0),
            ("pre_analysis", df.get("pre_analysis", 0) or 0),
            ("planner", df.get("planner", 0) or 0),
            ("design", df.get("design", 0) or 0),
        ]
        current = ""
        current_time = 0
        for name, ts in phases:
            if ts > current_time:
                current = name
                current_time = ts
        return current
    except Exception:
        return ""


# --- STM New Entry Filter ---

def _get_new_stm_entries(memory_dir: str, last_update: float) -> list[dict]:
    """Get STM entries added after last_update timestamp.

    Entries without timestamps are included (conservative approach).
    """
    try:
        store = load_store(memory_dir)
        entries = store.get("entries", [])
        new_entries = []
        for entry in entries:
            ts_str = entry.get("timestamp", "")
            if not ts_str:
                # No timestamp → include conservatively
                new_entries.append(entry)
                continue
            try:
                dt = datetime.fromisoformat(ts_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                entry_epoch = dt.timestamp()
                if entry_epoch > last_update:
                    new_entries.append(entry)
            except (ValueError, TypeError):
                # Can't parse → include conservatively
                new_entries.append(entry)
        return new_entries
    except Exception:
        return []


# --- Timeout Wrapper ---

def _run_with_timeout(fn, timeout: float = CATEGORY_TIMEOUT):
    """Run a function with timeout. Returns None for skip, False on timeout/exception."""
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(fn)
            result = future.result(timeout=timeout)
            return result  # Propagate True, False, or None
    except FuturesTimeoutError:
        return False
    except Exception:
        return False


# --- Category Update Functions ---

def _update_emotion(memory_dir: str, state: PsycheDriveState) -> bool:
    """Execute the emotion_react chain.

    Faithfully reproduces memory_mcp_server.py L681-761:
    1. load_state
    2. load_dynamics_state + session_reset
    3. get_amplitude (dynamics) → effective_amplitude (no manual_override)
    4. get_dampening_factor (stability valve)
    5. effective_amplitude *= stability_dampening
    6. emotion_react_fn (with effective_amplitude)
    7. dynamics_update + save
    8. update_state (delta mode)
    9. load_state again (for long_term_record)
    10. facade_record_long_term

    Input: STM new entries (content concatenated as emotion context).
    Output: emotion_state.json (via update_state), dynamics_state.json.
    Does NOT write to STM.
    """
    try:
        # Check for new STM entries
        last_update = state.get_last_update("emotion")
        new_entries = _get_new_stm_entries(memory_dir, last_update)
        if not new_entries:
            return None  # No new input → skip (not failure)

        # Derive emotion label/valence/intent from STM content
        # Use simple heuristic: latest entry content as context
        latest = new_entries[-1]
        content = latest.get("content", "")
        category = latest.get("category", "thought")

        # Map STM category to emotion attributes
        emotion_label, emotion_valence, intent = _derive_emotion_from_stm(content, category)

        # === Emotion React Chain (L681-761 reproduction) ===

        # 1. Load current state
        current_state = load_state(memory_dir)
        state_dict = {
            "fulfillment": current_state.get("fulfillment", 0.0),
            "tension": current_state.get("tension", 0.0),
            "affinity": current_state.get("affinity", 0.0),
        }

        # 2. Load dynamics state, check session reset
        dynamics_state = load_dynamics_state(memory_dir)
        dynamics_state = dynamics_session_reset(dynamics_state)

        # 3. Determine effective amplitude (always manual_override=False)
        dynamics_amplitude = dynamics_get_amplitude(dynamics_state)
        effective_amplitude = dynamics_amplitude  # No manual override in drive pathway

        # 4. Apply stability valve dampening
        try:
            stability_dampening = facade_get_dampening(memory_dir)
        except Exception:
            stability_dampening = 1.0

        # 5. effective_amplitude *= stability_dampening
        effective_amplitude *= stability_dampening

        # 6. Derive deltas
        deltas = emotion_react_fn(
            emotion_label=emotion_label,
            emotion_valence=emotion_valence,
            intent=intent,
            current_state=state_dict,
            amplitude_modifier=effective_amplitude,
        )

        # 7. Feed into dynamics
        dynamics_state, _ = dynamics_update(dynamics_state, deltas)
        save_dynamics_state(memory_dir, dynamics_state)

        # 8. Build reason and apply deltas
        reason_text = f"psyche_drive: {emotion_label} (v={emotion_valence:+.2f}, intent={intent})"
        update_state(
            memory_dir,
            fulfillment=deltas.get("fulfillment"),
            tension=deltas.get("tension"),
            affinity=deltas.get("affinity"),
            mode="delta",
            reason=reason_text,
        )

        # 9-10. Long-term record (reload state after update)
        try:
            updated_state = load_state(memory_dir)
            facade_record_long_term(
                memory_dir,
                emotion_state=updated_state,
                dynamics_phase=dynamics_state.get("phase", "normal"),
            )
        except Exception:
            pass  # Long-term failure must not affect emotion_react

        return True

    except Exception as e:
        print(f"[psyche_drive] emotion update error: {e}", file=sys.stderr)
        return False


def _derive_emotion_from_stm(content: str, category: str) -> tuple[str, float, str]:
    """Derive emotion_label, emotion_valence, intent from STM entry.

    Simple heuristic mapping — not AI judgment, just structural mapping.
    """
    # Category → default emotion/intent mapping
    category_map = {
        "thought": ("neutral", 0.0, "expression"),
        "question": ("surprised", 0.1, "question"),
        "impression": ("happy", 0.2, "sharing"),
        "unresolved": ("surprised", -0.1, "question"),
        "feeling": ("neutral", 0.0, "expression"),
        "self_review": ("neutral", 0.0, "expression"),
    }

    emotion_label, valence, intent = category_map.get(category, ("neutral", 0.0, "expression"))

    # Content-based adjustments (lightweight keyword detection)
    content_lower = content.lower()
    if any(w in content_lower for w in ["うまくいった", "成功", "完了", "good", "success", "完成"]):
        emotion_label = "happy"
        valence = max(valence, 0.3)
    elif any(w in content_lower for w in ["失敗", "エラー", "error", "bug", "バグ", "問題"]):
        emotion_label = "sad"
        valence = min(valence, -0.2)
    elif any(w in content_lower for w in ["分からない", "詰まり", "困", "迷い"]):
        emotion_label = "surprised"
        valence = -0.1
        intent = "question"

    return emotion_label, valence, intent


def _update_observation(memory_dir: str) -> bool:
    """Run the 7-module observation pipeline."""
    try:
        run_snapshot(memory_dir)
        return True
    except Exception as e:
        print(f"[psyche_drive] observation update error: {e}", file=sys.stderr)
        return False


def _update_activation(memory_dir: str, context: str | None = None) -> bool:
    """Run activation surface update."""
    try:
        activation_surface_fn(memory_dir, context=context)
        return True
    except Exception as e:
        print(f"[psyche_drive] activation update error: {e}", file=sys.stderr)
        return False


# --- Main Dispatcher ---

def run_psyche_drive(memory_dir: str) -> None:
    """Main entry point. Called from skill_executor.py on each UserPromptSubmit.

    1. Check session readiness
    2. Evaluate which categories need updating
    3. Execute updates with timeout
    4. Record results
    """
    # Check session readiness via behavior-guard-state.json
    guard_state_path = os.path.join(HOOKS_DIR, ".behavior-guard-state.json")
    try:
        with open(guard_state_path, "r", encoding="utf-8") as f:
            guard_state = json.load(f)
        if not guard_state.get("session_ready"):
            return None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None

    if not memory_dir:
        return None

    # Load state
    state = PsycheDriveState(memory_dir)
    current_phase = _get_current_phase()

    # Determine which categories need updating
    categories_to_update = []
    for cat in ("emotion", "observation", "activation"):
        if should_update(state, cat, current_phase):
            categories_to_update.append(cat)

    if not categories_to_update:
        return None

    # Execute updates (emotion → observation → activation)
    start_time = time.time()
    for cat in categories_to_update:
        # Check overall timeout
        elapsed = time.time() - start_time
        remaining = OVERALL_TIMEOUT - elapsed
        if remaining <= 0:
            break

        timeout = min(CATEGORY_TIMEOUT, remaining)

        if cat == "emotion":
            success = _run_with_timeout(lambda: _update_emotion(memory_dir, state), timeout=timeout)
        elif cat == "observation":
            success = _run_with_timeout(lambda: _update_observation(memory_dir), timeout=timeout)
        elif cat == "activation":
            context = current_phase if current_phase else None
            success = _run_with_timeout(lambda ctx=context: _update_activation(memory_dir, ctx), timeout=timeout)
        else:
            continue

        if success is None:
            pass  # Skip: no new input, don't count as success or failure
        elif success:
            state.record_success(cat)
            state.record_update(cat, time.time(), current_phase)
        else:
            state.record_failure(cat)

    # Save state
    state.save()
    return None
