"""
Message log handler for hook dispatcher.

Receives MessageEventContext JSON via stdin and appends a log entry to:
- message:received → discord_data/message_received_log.jsonl
- message:sent → discord_data/message_sent_log.jsonl

Each log line: ts, sender_id, channel_id, content (200 char limit), event_type.
Sent logs additionally include send_success and send_error.
Log rotation: 1000 lines per file.

This is a generic Claude Code utility, not part of any specific project.
"""

from __future__ import annotations

import json
import os
import sys

CONTENT_MAX_LENGTH = 200
LOG_MAX_LINES = 1000

# Project root (tools/ の親ディレクトリ)
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))

from file_io import resolve_project_root  # noqa: E402

_PROJECT_ROOT = resolve_project_root()

DISCORD_DATA_DIR = os.path.join(_PROJECT_ROOT, "discord_data")

VALID_EVENTS = {"message:received", "message:sent"}

EVENT_TO_FILE = {
    "message:received": "message_received_log.jsonl",
    "message:sent": "message_sent_log.jsonl",
}


def handle_message_log(stdin_data: str, log_dir: str | None = None) -> int:
    """Process a message event and append to the appropriate log file.

    Args:
        stdin_data: JSON string of MessageEventContext.
        log_dir: Directory for log files. Defaults to DISCORD_DATA_DIR.

    Returns:
        0 on success, 1 on error.
    """
    if log_dir is None:
        log_dir = DISCORD_DATA_DIR

    # Parse input
    try:
        ctx = json.loads(stdin_data)
    except (json.JSONDecodeError, TypeError):
        print("ERROR: Invalid JSON input", file=sys.stderr)
        return 1

    event = ctx.get("event")
    if event not in VALID_EVENTS:
        print(f"ERROR: Unsupported event: {event}", file=sys.stderr)
        return 1

    # Build log entry
    content = ctx.get("content", "")
    if len(content) > CONTENT_MAX_LENGTH:
        content = content[:CONTENT_MAX_LENGTH]

    entry = {
        "ts": ctx.get("timestamp", ""),
        "sender_id": ctx.get("sender_id", ""),
        "channel_id": ctx.get("channel_id", ""),
        "content": content,
        "event_type": event,
    }

    # Add sent-specific fields
    if event == "message:sent":
        entry["send_success"] = ctx.get("send_success")
        entry["send_error"] = ctx.get("send_error")

    # Write to file
    log_filename = EVENT_TO_FILE[event]
    log_path = os.path.join(log_dir, log_filename)

    try:
        os.makedirs(log_dir, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        # Rotation: prune to LOG_MAX_LINES
        _rotate_if_needed(log_path)
    except OSError as e:
        print(f"ERROR: Failed to write log: {e}", file=sys.stderr)
        return 1

    return 0


def _rotate_if_needed(log_path: str) -> None:
    """Prune log file to LOG_MAX_LINES, keeping newest entries."""
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > LOG_MAX_LINES:
            keep = lines[-LOG_MAX_LINES:]
            with open(log_path, "w", encoding="utf-8") as f:
                f.writelines(keep)
    except (FileNotFoundError, OSError):
        pass


def main() -> None:
    """Entry point: read stdin and process."""
    stdin_data = sys.stdin.read()
    exit_code = handle_message_log(stdin_data)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
