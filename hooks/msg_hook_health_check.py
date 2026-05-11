"""
Message hook health check for Claude Code SessionStart hook.

Reads discord_data/message_hooks.json and discord_data/message_hook_log.jsonl
to produce a status summary of all registered message event hooks.

Reports:
- Total hooks registered and their enabled/disabled state
- Recent failure counts from the log (last 50 entries)
- Hooks that may have been auto-disabled (consecutive failures)

Exit 0 always (informational only, never blocks).

This is a generic Claude Code utility, not part of any specific project.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)

DEFAULT_HOOK_CONFIG_PATH = os.path.join(
    _PROJECT_ROOT, "discord_data", "message_hooks.json"
)
DEFAULT_LOG_PATH = os.path.join(
    _PROJECT_ROOT, "discord_data", "message_hook_log.jsonl"
)

RECENT_LOG_SCAN_COUNT = 50
CONSECUTIVE_FAILURE_THRESHOLD = 5


def load_hook_config(config_path: str) -> list[dict]:
    """Load hook definitions from message_hooks.json."""
    if not os.path.isfile(config_path):
        return []
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"WARNING: Could not read hook config: {e}", file=sys.stderr)
        return []
    return data.get("hooks", [])


def scan_recent_failures(
    log_path: str, scan_count: int = RECENT_LOG_SCAN_COUNT
) -> dict[str, int]:
    """Scan recent log entries for failure counts per hook_id."""
    if not os.path.isfile(log_path):
        return {}
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return {}

    recent_lines = lines[-scan_count:] if len(lines) > scan_count else lines
    failure_counts: Counter[str] = Counter()

    for line in recent_lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if not entry.get("success", True):
                failure_counts[entry.get("hook_id", "unknown")] += 1
        except json.JSONDecodeError:
            continue

    return dict(failure_counts)


def detect_auto_disabled(
    log_path: str, threshold: int = CONSECUTIVE_FAILURE_THRESHOLD
) -> list[str]:
    """Detect hooks with consecutive failures >= threshold (scanning newest first)."""
    if not os.path.isfile(log_path):
        return []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []

    consecutive: dict[str, int] = {}
    seen_success: set[str] = set()
    auto_disabled: list[str] = []

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        hook_id = entry.get("hook_id", "")
        if not hook_id or hook_id in seen_success:
            continue

        if entry.get("success", True):
            seen_success.add(hook_id)
        else:
            consecutive[hook_id] = consecutive.get(hook_id, 0) + 1
            if consecutive[hook_id] >= threshold and hook_id not in auto_disabled:
                auto_disabled.append(hook_id)

    return auto_disabled


def format_health_summary(
    hooks: list[dict],
    failure_counts: dict[str, int],
    auto_disabled: list[str],
) -> str:
    """Format a health check summary for stdout."""
    lines = ["[msg-hook-health] Message Event Hook Status:"]

    if not hooks:
        lines.append("  No hooks registered in message_hooks.json")
        return "\n".join(lines)

    enabled_count = sum(1 for h in hooks if h.get("enabled", True))
    disabled_count = len(hooks) - enabled_count
    lines.append(f"  Registered: {len(hooks)} (enabled: {enabled_count}, disabled: {disabled_count})")

    for h in hooks:
        hook_id = h.get("id", "?")
        enabled = h.get("enabled", True)
        events = h.get("events", [])
        status = "enabled" if enabled else "DISABLED"
        failures = failure_counts.get(hook_id, 0)

        line = f"  - {hook_id}: {status} | events: {', '.join(events)}"
        if failures > 0:
            line += f" | recent failures: {failures}"
        lines.append(line)

    if auto_disabled:
        lines.append(f"  WARNING: Potentially auto-disabled hooks: {', '.join(auto_disabled)}")

    return "\n".join(lines)


def main() -> None:
    """Entry point: check health and output summary to stdout."""
    config_path = os.environ.get("MSG_HOOK_CONFIG_PATH", "").strip() or DEFAULT_HOOK_CONFIG_PATH
    log_path = os.environ.get("MSG_HOOK_LOG_PATH", "").strip() or DEFAULT_LOG_PATH

    hooks = load_hook_config(config_path)
    failure_counts = scan_recent_failures(log_path)
    auto_disabled = detect_auto_disabled(log_path)

    summary = format_health_summary(hooks, failure_counts, auto_disabled)
    print(summary)
    sys.exit(0)


if __name__ == "__main__":
    main()
