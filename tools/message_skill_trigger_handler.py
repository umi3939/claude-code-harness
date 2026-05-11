"""
DM received trigger handler for message event hooks (Route C).

Receives MessageEventContext JSON via stdin. For DM message:received events only,
records the message to STM and episodic memory by directly calling the underlying
Python functions (NOT via skill_executor, NOT via discord_send).

Structural guarantee against double-send:
- This module does NOT import discord_send or skill_executor
- This module does NOT import any module that sends Discord messages
- Only record-side MCP functions (stm, memory) are used

Non-DM and non-received events are silently ignored (exit 0).
All errors are absorbed with logging (exit 0) to prevent pipeline disruption.

This is a generic Claude Code utility, not part of any specific project.
"""

from __future__ import annotations

import json
import logging
import os
import sys

logger = logging.getLogger(__name__)

CONTENT_MAX_LENGTH = 200

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))

# Ensure tools/ is on sys.path for imports
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

from file_io import resolve_project_root  # noqa: E402

_PROJECT_ROOT = resolve_project_root()


def _record_to_stm(content: str, sender_name: str) -> None:
    """Record DM reception to short-term memory via direct function call."""
    try:
        from file_io import resolve_memory_dir
        from short_term_store import load_store, save_store, write_entry

        memory_dir = resolve_memory_dir()
        store = load_store(memory_dir)
        stm_content = f"[Discord DM received] {sender_name}: {content}"
        store = write_entry(store, stm_content, "impression")
        result = save_store(memory_dir, store)
        if result.startswith("ERROR"):
            logger.warning("STM save failed: %s", result)
    except Exception as e:
        logger.warning("STM recording failed (non-fatal): %s", e)


def _record_to_episodic_memory(
    content: str, sender_name: str, sender_id: str
) -> None:
    """Record DM reception to episodic memory via direct function call."""
    try:
        from episode_memory import record_episode
        from file_io import resolve_memory_dir
        from topic_index import build_index

        memory_dir = resolve_memory_dir()

        summary = f"[Discord DM] {sender_name}: {content}"
        tags = [sender_id, "discord", "dm"] if sender_id else ["discord", "dm"]

        result = record_episode(
            memory_dir=memory_dir,
            episode_type="observation",
            summary=summary,
            tags=tags,
        )
        if not result.startswith("ERROR"):
            build_index(memory_dir=memory_dir)
        else:
            logger.warning("Episode record failed: %s", result)
    except Exception as e:
        logger.warning("Episodic memory recording failed (non-fatal): %s", e)


def handle_dm_trigger(stdin_data: str) -> int:
    """Process a message event. Record DM messages to STM and memory.

    Args:
        stdin_data: JSON string of MessageEventContext.

    Returns:
        0 on success or ignored, 1 on parse error.
    """
    # Parse input
    try:
        ctx = json.loads(stdin_data)
    except (json.JSONDecodeError, TypeError):
        print("ERROR: Invalid JSON input", file=sys.stderr)
        return 1

    event = ctx.get("event")
    if not event:
        print("ERROR: Missing 'event' field", file=sys.stderr)
        return 1

    # Only handle message:received
    if event != "message:received":
        return 0

    # Only handle DM
    if ctx.get("conversation_type", "") != "dm":
        return 0

    # Extract fields with truncation
    metadata = ctx.get("metadata", {})
    sender_name = metadata.get("author_name", "") or ctx.get("sender_id", "unknown")
    sender_id = ctx.get("sender_id", "")
    content = ctx.get("content", "")
    if len(content) > CONTENT_MAX_LENGTH:
        content = content[:CONTENT_MAX_LENGTH]

    # Set HOOK_ORIGIN env to prevent future circular hooks
    os.environ["HOOK_ORIGIN"] = "message_skill_trigger_handler"

    # Record to STM (non-blocking, error absorbed)
    _record_to_stm(content, sender_name)

    # Record to episodic memory (non-blocking, error absorbed)
    _record_to_episodic_memory(content, sender_name, sender_id)

    return 0


def main() -> None:
    """Entry point: read stdin and process."""
    stdin_data = sys.stdin.read()
    exit_code = handle_dm_trigger(stdin_data)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
