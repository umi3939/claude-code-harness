#!/usr/bin/env python3
"""Session Evacuator — Saves work context before context compaction.

C20-3: Called from PreCompact hook (pre-compact-save.js) via child_process.execFileSync.
Reads flow state, psyche state, and STM summary, then writes a structured
evacuation data file for later restoration.

Constraints:
- NO MCP calls (PreCompact hook cannot invoke MCP)
- NO network/external process calls
- File I/O only
- Must complete within 3 seconds (Python startup + processing)
- Failure must not block flag reset in pre-compact-save.js
"""

import json
import logging
import os
import sys
import time

# --- Constants ---

EVACUATION_FILENAME = ".session-evacuation.json"
FLOW_STATE_FILENAME = ".dev-flow-state"
PSYCHE_STATE_FILENAME = ".psyche-drive-state.json"
STM_FILENAME = "short_term_memory.json"

# Size limits (design: section 3 safety valves)
MAX_STM_SUMMARY_CHARS = 2000
MAX_EVACUATION_FILE_SIZE = 32 * 1024  # 32 KB
MAX_STM_ENTRIES_TO_READ = 10  # Only read last N entries for summary

# Flow phase ordering (for remaining steps calculation)
FLOW_ORDER = [
    "design", "planner", "pre_analysis", "impl",
    "post_analysis", "reviewer", "commit",
]

FLOW_AFTER = {
    "design": "planner -> pre_analysis -> impl -> post_analysis -> reviewer -> commit",
    "planner": "pre_analysis -> impl -> post_analysis -> reviewer -> commit",
    "pre_analysis": "impl -> post_analysis -> reviewer -> commit",
    "impl": "post_analysis -> reviewer -> commit",
    "post_analysis": "reviewer -> commit",
    "reviewer": "commit",
    "commit": "",
}

logger = logging.getLogger(__name__)


# --- Sanitization ---

def _sanitize_text(text, max_chars=MAX_STM_SUMMARY_CHARS):
    """Remove control characters and truncate text.

    Preserves newlines (\\n), tabs (\\t), and carriage returns (\\r).
    Strips all other control characters (0x00-0x1f except \\t\\n\\r, and 0x7f).
    """
    if not isinstance(text, str):
        return ""
    # Remove control characters except \t \n \r
    cleaned = []
    for ch in text:
        code = ord(ch)
        if code == 0x09 or code == 0x0A or code == 0x0D:
            # Tab, newline, carriage return — keep
            cleaned.append(ch)
        elif 0x00 <= code <= 0x1F or code == 0x7F:
            # Control character — skip
            continue
        else:
            cleaned.append(ch)
    result = "".join(cleaned)

    # Truncate
    if len(result) > max_chars:
        result = result[:max_chars]
    return result


# --- State Readers ---

def _read_flow_state(hooks_dir):
    """Read .dev-flow-state and extract current phase + remaining steps.

    Returns:
        dict with keys: flow_state, flow_current_phase, flow_remaining_steps
    """
    result = {
        "flow_state": {},
        "flow_current_phase": "",
        "flow_remaining_steps": "",
    }
    try:
        state_file = os.path.join(hooks_dir, FLOW_STATE_FILENAME)
        if not os.path.isfile(state_file):
            return result

        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return result

        result["flow_state"] = data

        # Find latest phase with nonzero timestamp
        current_phase = ""
        current_time = 0
        for phase in FLOW_ORDER:
            ts = data.get(phase, 0) or 0
            if ts > current_time:
                current_phase = phase
                current_time = ts

        result["flow_current_phase"] = current_phase
        result["flow_remaining_steps"] = FLOW_AFTER.get(current_phase, "")

    except (json.JSONDecodeError, OSError, ValueError) as e:
        logger.warning("Failed to read flow state: %s", e)
    return result


def _read_psyche_state(memory_dir):
    """Read .psyche-drive-state.json and extract category update times.

    Returns:
        dict with category names as keys, last_update times as values
    """
    try:
        state_file = os.path.join(memory_dir, PSYCHE_STATE_FILENAME)
        if not os.path.isfile(state_file):
            return {}

        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return {}

        categories = data.get("categories", {})
        if not isinstance(categories, dict):
            return {}

        # Extract last_update for each category
        result = {}
        for cat_name, cat_data in categories.items():
            if isinstance(cat_data, dict):
                result[cat_name] = {
                    "last_update": cat_data.get("last_update", 0.0),
                    "last_phase": cat_data.get("last_phase", ""),
                    "failure_count": cat_data.get("failure_count", 0),
                }
        return result

    except (json.JSONDecodeError, OSError, ValueError) as e:
        logger.warning("Failed to read psyche state: %s", e)
        return {}


def _read_stm_summary(memory_dir):
    """Read short_term_memory.json and create a summary of recent entries.

    Returns:
        tuple of (summary_text, entry_count)
    """
    try:
        stm_file = os.path.join(memory_dir, STM_FILENAME)
        if not os.path.isfile(stm_file):
            return "", 0

        with open(stm_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return "", 0

        entries = data.get("entries", [])
        if not isinstance(entries, list):
            return "", 0

        total_count = len(entries)
        if total_count == 0:
            return "", 0

        # Take last N entries for summary
        recent = entries[-MAX_STM_ENTRIES_TO_READ:]

        # Build summary: concatenate category + content
        parts = []
        for entry in recent:
            if not isinstance(entry, dict):
                continue
            cat = entry.get("category", "unknown")
            content = entry.get("content", "")
            if content:
                parts.append(f"[{cat}] {content}")

        summary = "\n".join(parts)
        summary = _sanitize_text(summary, max_chars=MAX_STM_SUMMARY_CHARS)
        return summary, total_count

    except (json.JSONDecodeError, OSError, ValueError) as e:
        logger.warning("Failed to read STM: %s", e)
        return "", 0


# --- Write Evacuation Data ---

def _write_evacuation(hooks_dir, evacuation_data):
    """Write evacuation data to .session-evacuation.json.

    Uses temp file + rename for atomic write on supported platforms.
    On Windows, falls back to direct write if rename fails.
    """
    evac_path = os.path.join(hooks_dir, EVACUATION_FILENAME)
    content = json.dumps(evacuation_data, indent=2, ensure_ascii=False)

    # Check size limit
    if len(content.encode("utf-8")) > MAX_EVACUATION_FILE_SIZE:
        # Stage 0: Truncate STM summary to fit
        if "stm_summary" in evacuation_data:
            while len(content.encode("utf-8")) > MAX_EVACUATION_FILE_SIZE:
                current_summary = evacuation_data["stm_summary"]
                if len(current_summary) <= 100:
                    break
                evacuation_data["stm_summary"] = current_summary[:len(current_summary) // 2]
                content = json.dumps(evacuation_data, indent=2, ensure_ascii=False)

        # Stage 1: truncate flow_remaining_steps
        if len(content.encode("utf-8")) > MAX_EVACUATION_FILE_SIZE:
            if "flow_remaining_steps" in evacuation_data:
                evacuation_data["flow_remaining_steps"] = ""
                content = json.dumps(evacuation_data, indent=2, ensure_ascii=False)

        # Stage 2: clear psyche_state
        if len(content.encode("utf-8")) > MAX_EVACUATION_FILE_SIZE:
            if "psyche_state" in evacuation_data:
                evacuation_data["psyche_state"] = {}
                content = json.dumps(evacuation_data, indent=2, ensure_ascii=False)

        # Stage 3: clear stm_summary entirely
        if len(content.encode("utf-8")) > MAX_EVACUATION_FILE_SIZE:
            if "stm_summary" in evacuation_data:
                evacuation_data["stm_summary"] = ""
                content = json.dumps(evacuation_data, indent=2, ensure_ascii=False)

        # Stage 4: clear flow_state
        if len(content.encode("utf-8")) > MAX_EVACUATION_FILE_SIZE:
            if "flow_state" in evacuation_data:
                evacuation_data["flow_state"] = {}
                content = json.dumps(evacuation_data, indent=2, ensure_ascii=False)

    # Write: try temp+rename, fall back to direct write
    try:
        tmp_path = evac_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
        # On Windows, need to remove target first
        if os.path.exists(evac_path):
            os.remove(evac_path)
        os.rename(tmp_path, evac_path)
    except OSError:
        # Fallback: direct write
        with open(evac_path, "w", encoding="utf-8") as f:
            f.write(content)


# --- Main Entry Point ---

def evacuate(hooks_dir, memory_dir):
    """Execute the evacuation process.

    Reads flow state, psyche state, and STM summary, then writes
    structured evacuation data to hooks_dir/.session-evacuation.json.

    Args:
        hooks_dir: Path to hooks directory (contains .dev-flow-state)
        memory_dir: Path to memory directory (contains .psyche-drive-state.json, short_term_memory.json)

    Returns:
        True on success, False on failure
    """
    try:
        # 1. Read flow state
        flow_data = _read_flow_state(hooks_dir)

        # 2. Read psyche state
        psyche_data = _read_psyche_state(memory_dir)

        # 3. Read STM summary
        stm_summary, stm_count = _read_stm_summary(memory_dir)

        # 4. Build evacuation data
        evacuation_data = {
            "evacuated_at": time.time(),
            "flow_state": flow_data["flow_state"],
            "flow_current_phase": flow_data["flow_current_phase"],
            "flow_remaining_steps": flow_data["flow_remaining_steps"],
            "psyche_state": psyche_data,
            "stm_summary": stm_summary,
            "stm_entry_count": stm_count,
        }

        # 5. Write to file
        _write_evacuation(hooks_dir, evacuation_data)

        return True

    except Exception as e:
        logger.error("Evacuation failed: %s", e)
        return False


# --- memory_dir resolution (simplified, no skill_executor import) ---

def _validate_path_under_home(resolved_path):
    """Validate that resolved_path is under ~/.claude/.

    Raises RuntimeError if the path is outside the allowed directory.
    """
    home = os.path.expanduser("~")
    allowed_prefix = os.path.realpath(os.path.join(home, ".claude"))
    real_path = os.path.realpath(resolved_path)
    if not real_path.startswith(allowed_prefix + os.sep) and real_path != allowed_prefix:
        raise RuntimeError(
            f"Memory directory is outside ~/.claude/: {resolved_path}"
        )


def _resolve_memory_dir():
    """Resolve memory directory without importing skill_executor.

    Uses MEMORY_DIR environment variable or glob fallback.
    Pre-impl analysis #2: avoid skill_executor import side effects.

    Raises:
        RuntimeError: If resolved path is outside ~/.claude/
    """
    import glob as _glob

    # 1. Environment variable
    env_dir = os.environ.get("MEMORY_DIR", "")
    if env_dir and os.path.isdir(env_dir):
        _validate_path_under_home(env_dir)
        return env_dir

    # 2. Glob fallback
    home = os.path.expanduser("~")
    candidates = _glob.glob(os.path.join(home, ".claude", "projects", "*", "memory"))
    if not candidates:
        return ""

    resolved = ""
    if len(candidates) == 1:
        resolved = candidates[0]
    else:
        # Match cwd to project directory name
        try:
            cwd = os.getcwd().replace("\\", "/")
            if len(cwd) >= 2 and cwd[1] == ":":
                cwd = cwd[0] + cwd[2:]
            cwd_key = cwd.replace("/", "-")
            for c in candidates:
                parent = os.path.basename(os.path.dirname(c))
                if parent == cwd_key:
                    resolved = c
                    break
        except Exception:
            pass

        # Most recent
        if not resolved:
            try:
                resolved = max(candidates, key=lambda d: os.path.getmtime(d))
            except Exception:
                resolved = candidates[0]

    if resolved:
        _validate_path_under_home(resolved)
    return resolved


def main():
    """CLI entry point — called from pre-compact-save.js."""
    hooks_dir = os.path.dirname(os.path.abspath(__file__))
    memory_dir = _resolve_memory_dir()

    success = evacuate(hooks_dir, memory_dir)
    if success:
        print("[Evacuator] Session state evacuated successfully.", file=sys.stderr)
    else:
        print("[Evacuator] Session state evacuation failed.", file=sys.stderr)

    # Always exit 0 — evacuation failure must not block PreCompact
    sys.exit(0)


if __name__ == "__main__":
    main()
