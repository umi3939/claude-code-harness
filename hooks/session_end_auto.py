"""Auto session_end for Stop hook — C21-7.

Called by stop-session-end.js. Checks if session_end has already run
(via flag file), generates a summary from STM, and calls session_end.

This module does NOT write to STM, emotion state, or episodes directly.
All persistence is handled by session_end itself.
"""

import json
import logging
import os
import sys
import time

logger = logging.getLogger(__name__)

FLAG_NAME = ".session-end-done"
SUMMARY_MAX_LEN = 2000


def should_run(hooks_dir: str) -> bool:
    """Check if session_end has already been executed this session.

    Returns True if session_end should be run (flag absent).
    On flag-read error, returns False if path exists (conservative).
    """
    flag_path = os.path.join(hooks_dir, FLAG_NAME)
    return not os.path.exists(flag_path)


def load_stm(memory_dir: str) -> dict:
    """Load STM store from short_term_memory.json.

    Returns the parsed store dict, or empty dict on any failure.
    """
    stm_path = os.path.join(memory_dir, "short_term_memory.json")
    try:
        with open(stm_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        logger.warning("STM load failed: %s", e)
        return {}


def build_summary(store: dict | None) -> str:
    """Build a summary string from STM entries.

    Groups entries by category and formats them chronologically.
    Falls back to a minimal message if STM is empty or None.
    """
    if not store or not store.get("entries"):
        return "自動保存: STMエントリなし"

    entries = store["entries"]
    # Group by category
    by_category: dict[str, list[str]] = {}
    for entry in entries:
        cat = entry.get("category", "unknown")
        content = entry.get("content", "")
        if content:
            by_category.setdefault(cat, []).append(content)

    if not by_category:
        return "自動保存: STMエントリなし"

    parts = ["[自動生成summary]"]
    for cat, contents in by_category.items():
        parts.append(f"## {cat}")
        for c in contents:
            # Truncate individual entries if very long
            if len(c) > 200:
                c = c[:197] + "..."
            parts.append(f"- {c}")

    summary = "\n".join(parts)
    if len(summary) > SUMMARY_MAX_LEN:
        summary = summary[: SUMMARY_MAX_LEN - 3] + "..."
    return summary


def extract_fields(store: dict | None) -> dict:
    """Extract completed/pending/decisions from STM entries.

    Returns a dict with keys: completed, pending, decisions.
    """
    result = {"completed": "", "pending": "", "decisions": ""}
    if not store or not store.get("entries"):
        return result

    completed_items = []
    pending_items = []
    decision_items = []

    for entry in store["entries"]:
        cat = entry.get("category", "")
        content = entry.get("content", "")
        if not content:
            continue
        if cat == "unresolved":
            pending_items.append(content[:100])
        elif cat == "self_review":
            decision_items.append(content[:100])
        elif cat == "thought":
            completed_items.append(content[:100])

    if completed_items:
        result["completed"] = ", ".join(completed_items[:5])
    if pending_items:
        result["pending"] = ", ".join(pending_items[:5])
    if decision_items:
        result["decisions"] = ", ".join(decision_items[:5])

    return result


def call_session_end(memory_dir: str, summary: str, **kwargs) -> str:
    """Call session_end function directly (not via MCP).

    Imports from memory_mcp_server and calls session_end with
    MEMORY_DIR set so resolve_memory_dir works correctly.
    """
    # Ensure MEMORY_DIR is set for the import
    os.environ.setdefault("MEMORY_DIR", memory_dir)

    tools_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"
    )
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)

    from memory_mcp_server import session_end as _session_end

    return _session_end(
        summary=summary,
        completed=kwargs.get("completed", ""),
        pending=kwargs.get("pending", ""),
        decisions=kwargs.get("decisions", ""),
    )


def write_flag(hooks_dir: str) -> None:
    """Write the session-end-done flag file."""
    flag_path = os.path.join(hooks_dir, FLAG_NAME)
    try:
        with open(flag_path, "w", encoding="utf-8") as f:
            f.write(str(int(time.time())))
    except OSError as e:
        logger.error("Failed to write flag %s: %s", flag_path, e)


def _auto_emotion_react_session(summary: str) -> str:
    """Auto-emit emotion_react based on session summary.

    Called at session end to record an emotional reaction to the session.
    Uses a neutral-positive default (productive session = fulfillment).

    Args:
        summary: Session summary text.

    Returns:
        emotion_react result string, or empty string on failure.
    """
    if not summary:
        return ""
    try:
        tools_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"
        )
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)

        from memory_mcp_server import emotion_react
        result = emotion_react(
            emotion_label="session_complete",
            emotion_valence=0.3,
            intent="neutral",
            reason=f"Session auto-end: {str(summary)[:200]}",
        )
        return str(result)[:200] if result else ""
    except Exception as e:
        logger.warning("auto_emotion_react_session failed: %s", e)
        return ""


def _auto_memory_record_session(summary: str) -> str:
    """Auto-record session episode via memory_record.

    Called at session end to persist a session-level episode without
    requiring manual invocation.

    Args:
        summary: Session summary text.

    Returns:
        memory_record result string, or empty string on failure.
    """
    if not summary:
        return ""
    try:
        tools_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"
        )
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)

        from memory_mcp_server import memory_record
        result = memory_record(
            episode_type="session",
            summary=f"[Auto] {str(summary)[:500]}",
            tags="auto,session_end",
        )
        return str(result)[:200] if result else ""
    except Exception as e:
        logger.warning("auto_memory_record_session failed: %s", e)
        return ""


def run(hooks_dir: str, memory_dir: str) -> bool:
    """Main orchestration: check flag, build summary, call session_end.

    Returns True if session_end was successfully called, False otherwise.
    Always writes flag on attempt (even on failure) to avoid retry loops.
    """
    # Stage A: Double-execution check
    if not should_run(hooks_dir):
        logger.info("session_end already done, skipping")
        return False

    # Stage B: Load STM and build summary
    store = load_stm(memory_dir)
    summary = build_summary(store)
    fields = extract_fields(store)

    # Stage C: Call session_end
    try:
        result = call_session_end(
            memory_dir,
            summary,
            completed=fields["completed"],
            pending=fields["pending"],
            decisions=fields["decisions"],
        )
        logger.info("session_end completed: %s", result[:200] if result else "OK")
    except Exception as e:
        logger.error("session_end failed: %s", e)
        # Write flag even on failure to prevent retry storm
        write_flag(hooks_dir)
        return False

    # Stage D: Auto memory_record (session episode) — fail-open
    try:
        _auto_memory_record_session(summary)
    except Exception as e:
        logger.warning("auto memory_record failed: %s", e)

    # Stage E: Auto emotion_react (session emotion) — fail-open
    try:
        _auto_emotion_react_session(summary)
    except Exception as e:
        logger.warning("auto emotion_react failed: %s", e)

    write_flag(hooks_dir)
    return True


def main():
    """Entry point when called from stop-session-end.js."""
    logging.basicConfig(
        level=logging.INFO,
        format="[session_end_auto] %(message)s",
        stream=sys.stderr,
    )

    # Memory dir from environment (set by stop-session-end.js)
    memory_dir = os.environ.get("MEMORY_DIR", "").strip()
    if not memory_dir:
        # Fallback: find first project memory dir via glob
        import glob
        _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates = glob.glob(os.path.join(_project_root, "memory"))
        if not candidates:
            candidates = glob.glob(os.path.join(
                os.path.expanduser("~"), ".claude", "projects", "*", "memory",
            ))
        if candidates:
            memory_dir = candidates[0]

    hooks_dir = os.path.dirname(os.path.abspath(__file__))

    success = run(hooks_dir, memory_dir)
    if success:
        print("[Stop] session_end auto-saved", file=sys.stderr)
    else:
        if not should_run(hooks_dir):
            print("[Stop] session_end already done, skipped", file=sys.stderr)
        else:
            print("[Stop] session_end auto-save failed", file=sys.stderr)


if __name__ == "__main__":
    main()
