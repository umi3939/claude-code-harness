"""
Message hook log reader for Claude Code PostToolUse hook.

Reads the most recent entries from discord_data/message_hook_log.jsonl
and outputs a summary to stdout for Claude Code context injection.

Triggered by PostToolUse on discord-related MCP calls.
Exit 0 always (informational only, never blocks).

This is a generic Claude Code utility, not part of any specific project.
"""

from __future__ import annotations

import json
import os
import sys

MAX_RECENT_ENTRIES = 5
LOG_ENTRY_CONTENT_MAX = 150

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)

DEFAULT_LOG_PATH = os.path.join(_PROJECT_ROOT, "discord_data", "message_hook_log.jsonl")


def read_recent_log(log_path: str, max_entries: int = MAX_RECENT_ENTRIES) -> list[dict]:
    """Read the most recent entries from the hook log JSONL file.

    Returns list of parsed log entry dicts, newest first.
    """
    if not os.path.isfile(log_path):
        return []

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as e:
        print(f"WARNING: Could not read log file: {e}", file=sys.stderr)
        return []

    entries: list[dict] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(entries) >= max_entries:
            break

    return entries


def format_log_summary(entries: list[dict]) -> str:
    """Format log entries into a human-readable summary for stdout."""
    if not entries:
        return "[msg-hook-log] No recent hook executions found."

    lines = [f"[msg-hook-log] Recent {len(entries)} hook executions:"]
    for entry in entries:
        hook_id = entry.get("hook_id", "?")
        event = entry.get("event", "?")
        success = entry.get("success", False)
        status = "OK" if success else "FAIL"
        duration_ms = entry.get("duration_ms", 0)
        error = entry.get("error", "")
        ts = entry.get("timestamp", "")

        stdout_preview = entry.get("stdout", "")
        if len(stdout_preview) > LOG_ENTRY_CONTENT_MAX:
            stdout_preview = stdout_preview[:LOG_ENTRY_CONTENT_MAX] + "..."

        line = f"  {ts} | {hook_id} | {event} | {status} | {duration_ms:.0f}ms"
        if error:
            line += f" | err: {error}"
        if stdout_preview:
            line += f" | out: {stdout_preview}"
        lines.append(line)

    return "\n".join(lines)


def main() -> None:
    """Entry point: read log and output summary to stdout."""
    log_path = os.environ.get("MSG_HOOK_LOG_PATH", "").strip() or DEFAULT_LOG_PATH
    entries = read_recent_log(log_path)
    summary = format_log_summary(entries)
    print(summary)
    sys.exit(0)


if __name__ == "__main__":
    main()
