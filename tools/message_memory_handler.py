"""
Memory recording handler for hook dispatcher.

Receives MessageEventContext JSON via stdin. For DM message:received events,
calls memory_manager.py record subprocess to store the message as an episode.

Channel messages and non-received events are ignored (exit 0).
Subprocess failures are absorbed (exit 0) to prevent pipeline disruption.

This is a generic Claude Code utility, not part of any specific project.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))

from file_io import resolve_project_root  # noqa: E402

_PROJECT_ROOT = resolve_project_root()
MEMORY_MANAGER_PATH = os.path.join(TOOLS_DIR, "memory_manager.py")
MEMORY_DIR = os.environ.get("MEMORY_DIR", "").strip() or os.path.join(_PROJECT_ROOT, "memory")

SUMMARY_CONTENT_MAX = 200


def handle_message_memory(stdin_data: str) -> int:
    """Process a message event and record DM messages to memory.

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
    conversation_type = ctx.get("conversation_type", "")
    if conversation_type != "dm":
        return 0

    # Build summary: sender + content preview
    metadata = ctx.get("metadata", {})
    sender_name = metadata.get("author_name", "") or ctx.get("sender_id", "unknown")
    content = ctx.get("content", "")
    if len(content) > SUMMARY_CONTENT_MAX:
        content = content[:SUMMARY_CONTENT_MAX]
    summary = f"[Discord DM] {sender_name}: {content}"

    # Build tags
    sender_id = ctx.get("sender_id", "")
    tags = f"{sender_id},discord,dm"

    # Call memory_manager.py record
    cmd = [
        sys.executable,
        MEMORY_MANAGER_PATH,
        "record",
        "--memory-dir", MEMORY_DIR,
        "--type", "discord_received",
        "--summary", summary,
        "--tags", tags,
    ]

    # Set HOOK_ORIGIN env to prevent future circular hooks
    env = os.environ.copy()
    env["HOOK_ORIGIN"] = "message_memory_handler"

    try:
        subprocess.run(cmd, env=env, timeout=30, capture_output=True)
    except Exception as e:
        print(f"WARNING: memory_manager call failed: {e}", file=sys.stderr)

    # Always return 0 — memory failure must not affect pipeline
    return 0


def main() -> None:
    """Entry point: read stdin and process."""
    stdin_data = sys.stdin.read()
    exit_code = handle_message_memory(stdin_data)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
