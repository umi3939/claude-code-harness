#!/usr/bin/env python3
"""Session Restorer — Restores work context after context compaction.

C20-3: Called from PreToolUse hook chain (behavior-guard.js → skill_executor.py)
on the first Agent/TeamCreate/mcp__ tool call after compaction.
Reads evacuation data, generates context injection text, restores flow
state, and deletes the evacuation file.

Constraints:
- Output goes to stdout (context injection) — NOT to STM (no self-reinforcement loop)
- Flow state timestamps are updated to current time (not past timestamps)
- One-time restoration: evacuation file is deleted after successful restore
- Failure must not block existing context injection or psyche drive
"""

import json
import logging
import os
import time
from datetime import datetime, timezone

# --- Constants ---

EVACUATION_FILENAME = ".session-evacuation.json"
FLOW_STATE_FILENAME = ".dev-flow-state"

# Validity limits
MAX_AGE_SECONDS = 3600  # 1 hour — older evacuation data is stale
MAX_FUTURE_SECONDS = 300  # 5 minutes tolerance for clock skew

# Required fields in evacuation data
REQUIRED_FIELDS = [
    "evacuated_at", "flow_state", "flow_current_phase",
    "flow_remaining_steps", "psyche_state", "stm_summary",
    "stm_entry_count",
]

logger = logging.getLogger(__name__)


# --- Validation ---

def _validate_evacuation(data):
    """Validate evacuation data structure and freshness.

    Returns:
        True if valid, False if should be rejected.
    """
    # Must be dict
    if not isinstance(data, dict):
        return False

    # Required fields
    for field in REQUIRED_FIELDS:
        if field not in data:
            logger.warning("Missing required field: %s", field)
            return False

    # Timestamp checks
    evacuated_at = data.get("evacuated_at", 0)
    if not isinstance(evacuated_at, (int, float)):
        return False

    now = time.time()

    # Reject future timestamps (clock skew)
    if evacuated_at > now + MAX_FUTURE_SECONDS:
        logger.warning("Evacuation timestamp is in the future: %s", evacuated_at)
        return False

    # Reject expired data
    age = now - evacuated_at
    if age > MAX_AGE_SECONDS:
        logger.warning("Evacuation data expired: %.0f seconds old", age)
        return False

    return True


# --- Format Recovery Text ---

def _format_recovery_text(data):
    """Generate context injection text from evacuation data.

    Returns:
        Formatted recovery text string.
    """
    sections = []
    sections.append("[Session Recovery] Context restored after compaction")

    # Evacuation timestamp
    evacuated_at = data.get("evacuated_at", 0)
    try:
        dt = datetime.fromtimestamp(evacuated_at, tz=timezone.utc)
        sections.append(f"Evacuated at: {dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    except (OSError, ValueError, OverflowError):
        sections.append(f"Evacuated at: {evacuated_at}")

    # Flow position
    phase = data.get("flow_current_phase", "")
    remaining = data.get("flow_remaining_steps", "")
    if phase:
        flow_line = f"Flow position: {phase} completed"
        if remaining:
            flow_line += f". Next: {remaining}"
        sections.append(flow_line)

    # STM summary
    stm_summary = data.get("stm_summary", "")
    stm_count = data.get("stm_entry_count", 0)
    if stm_summary:
        sections.append(f"STM snapshot ({stm_count} entries):")
        sections.append(stm_summary)

    # Psyche state
    psyche = data.get("psyche_state", {})
    if psyche:
        psyche_lines = []
        now = time.time()
        for cat_name, cat_data in psyche.items():
            if isinstance(cat_data, dict):
                last_update = cat_data.get("last_update", 0)
                elapsed = now - last_update if last_update > 0 else -1
                phase_str = cat_data.get("last_phase", "")
                if elapsed >= 0:
                    mins = int(elapsed / 60)
                    psyche_lines.append(f"  {cat_name}: {mins}min ago (phase: {phase_str})")
                else:
                    psyche_lines.append(f"  {cat_name}: never updated")
        if psyche_lines:
            sections.append("Psyche state (time since last update):")
            sections.extend(psyche_lines)

    return "\n".join(sections)


# --- Restore Flow State ---

def _restore_flow_state(hooks_dir, data):
    """Write .dev-flow-state with restored phase information.

    Updates timestamps to current time for non-zero phases.
    Adds restored:true flag for behavior-guard awareness.
    """
    flow_state = data.get("flow_state", {})
    if not isinstance(flow_state, dict):
        return

    now = time.time()
    restored = {}
    for phase, ts in flow_state.items():
        if isinstance(ts, (int, float)) and ts > 0:
            restored[phase] = now  # Current time, not past
        else:
            restored[phase] = 0

    # Mark as restored (design section 4: behavior-guard awareness)
    restored["restored"] = True

    flow_path = os.path.join(hooks_dir, FLOW_STATE_FILENAME)
    try:
        with open(flow_path, "w", encoding="utf-8") as f:
            json.dump(restored, f, indent=2)
    except OSError as e:
        logger.warning("Failed to restore flow state: %s", e)


# --- Delete Evacuation File ---

def _delete_evacuation(hooks_dir):
    """Delete the evacuation file.

    Returns:
        True if deleted successfully, False otherwise.
    """
    evac_path = os.path.join(hooks_dir, EVACUATION_FILENAME)
    try:
        os.remove(evac_path)
        return True
    except OSError as e:
        logger.warning("Failed to delete evacuation file: %s", e)
        return False


# --- Main Entry Point ---

def restore(hooks_dir):
    """Attempt to restore session context from evacuation data.

    Called from skill_executor.py on each PreToolUse (Agent/TeamCreate/mcp__).
    Hook chain: behavior-guard.js → skill_executor.py → session_restorer.restore()
    If evacuation data exists and is valid, generates recovery text,
    restores flow state, and deletes the evacuation file.

    Args:
        hooks_dir: Path to hooks directory

    Returns:
        Recovery text string for context injection, or None if no restoration needed.
    """
    evac_path = os.path.join(hooks_dir, EVACUATION_FILENAME)

    # Check if evacuation data exists
    if not os.path.isfile(evac_path):
        return None

    # Read evacuation data
    try:
        with open(evac_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read evacuation data: %s", e)
        # Clean up invalid file
        _delete_evacuation(hooks_dir)
        return None

    # Validate
    if not _validate_evacuation(data):
        # Clean up invalid/expired data
        _delete_evacuation(hooks_dir)
        return None

    # Design section 3: "削除に失敗した場合は復元テキストを出力しない"
    # Order: read -> delete -> format -> output
    # Delete first to ensure one-time restoration
    if not _delete_evacuation(hooks_dir):
        # Deletion failed — do not output recovery text (design constraint)
        return None

    # Restore flow state (with current timestamps)
    _restore_flow_state(hooks_dir, data)

    # Format recovery text
    recovery_text = _format_recovery_text(data)

    return recovery_text
