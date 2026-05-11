#!/usr/bin/env python3
"""Observation Writer — log internal sub-tool calls to observations.jsonl.

Used by orchestrators (growth_recorder, skill_executor) to make their
internal Python-import-based sub-tool calls visible in observations.jsonl,
matching the format produced by observation-logger.js (PostToolUse hook).

Format: {"ts": ISO, "sid": "s"+epoch[:12], "tool": "mcp__server__tool", "params": {...}}

Fail-open: errors never propagate to callers.
"""

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Default paths (overridable via function args for testing)
_CLAUDE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_DATA_DIR = os.path.join(_CLAUDE_DIR, "data")
_DEFAULT_HOOKS_DIR = os.path.join(_CLAUDE_DIR, "hooks")

# Max param value length (matches observation-logger.js truncation for MCP tools)
MAX_PARAM_LEN = 80

# Tool name -> MCP server name mapping
_SELF_OBSERVATION_TOOLS = frozenset({
    "behavior_analyze", "behavior_evolve", "behavior_guidance",
    "continuity_strain", "identity_coherence",
    "long_term_record", "long_term_stats",
    "self_difference", "self_image", "self_observe", "self_snapshot",
    "stability_check", "tone_check",
    "skill_metadata", "workflow_crystallize",
})

_PERSISTENT_CRON_TOOLS = frozenset({
    "persistent_cron_add", "persistent_cron_list", "persistent_cron_get",
    "persistent_cron_update", "persistent_cron_run", "persistent_cron_status",
    "persistent_cron_logs", "persistent_cron_notifications",
    "persistent_cron_remove", "persistent_cron_emergency_stop",
})

_DISCORD_TOOLS = frozenset({
    "discord_connect", "discord_send", "discord_status",
    "discord_receive_status", "discord_receive_allow",
    "discord_receive_pending", "discord_receive_remove",
})

_HTTP_FETCH_TOOLS = frozenset({
    "http_fetch",
})


def _resolve_mcp_tool_name(tool_name: str) -> str:
    """Convert a short tool name to full MCP-prefixed name.

    Args:
        tool_name: Short name (e.g. "record_success_tool") or already-prefixed name.

    Returns:
        Full MCP name (e.g. "mcp__memory-tools__record_success_tool").
    """
    if tool_name.startswith("mcp__"):
        return tool_name

    if tool_name in _SELF_OBSERVATION_TOOLS:
        return f"mcp__self-observation__{tool_name}"
    if tool_name in _PERSISTENT_CRON_TOOLS:
        return f"mcp__persistent-cron__{tool_name}"
    if tool_name in _DISCORD_TOOLS:
        return f"mcp__discord__{tool_name}"
    if tool_name in _HTTP_FETCH_TOOLS:
        return f"mcp__http-fetch__{tool_name}"

    # Default: memory-tools (largest server, most tools)
    return f"mcp__memory-tools__{tool_name}"


def _read_session_id(hooks_dir: str) -> str:
    """Read session ID from .session-start-time, formatted as observation-logger.js.

    Args:
        hooks_dir: Path to hooks directory containing .session-start-time.

    Returns:
        Session ID string: 's' + epoch, truncated to 12 chars total.
        Returns 'unknown' if file cannot be read.
    """
    try:
        sst_file = os.path.join(hooks_dir, ".session-start-time")
        with open(sst_file, "r", encoding="utf-8") as f:
            epoch = f.read().strip()
        sid = "s" + epoch
        return sid[:12]
    except (OSError, ValueError):
        return "unknown"


def _truncate_params(params: dict) -> dict:
    """Truncate param values for safe logging (matches observation-logger.js behavior).

    Args:
        params: Raw parameters dict.

    Returns:
        New dict with string values truncated to MAX_PARAM_LEN.
    """
    if not params or not isinstance(params, dict):
        return {}
    truncated = {}
    for k, v in params.items():
        if isinstance(v, str):
            truncated[k] = v[:MAX_PARAM_LEN]
        elif isinstance(v, (int, float, bool)):
            truncated[k] = v
        else:
            # Convert to string and truncate
            truncated[k] = str(v)[:MAX_PARAM_LEN]
    return truncated


def log_internal_tool_call(
    tool_name: str,
    params: dict = None,
    data_dir: str = None,
    hooks_dir: str = None,
) -> None:
    """Write an observation entry for an internally-called tool.

    Used by orchestrators (growth_recorder, skill_executor) to make
    their sub-tool calls visible in observations.jsonl.

    Format matches observation-logger.js output:
    {"ts": ISO, "sid": "s"+epoch[:12], "tool": "mcp__server__tool", "params": {...}}

    Fail-open: never raises exceptions.

    Args:
        tool_name: Short tool name (e.g. "record_success_tool") or full MCP name.
        params: Optional parameters dict to record (values truncated to 80 chars).
        data_dir: Override data directory (for testing). Defaults to ~/.claude/data.
        hooks_dir: Override hooks directory (for testing). Defaults to ~/.claude/hooks.
    """
    try:
        _data_dir = data_dir or _DEFAULT_DATA_DIR
        _hooks_dir = hooks_dir or _DEFAULT_HOOKS_DIR

        sid = _read_session_id(_hooks_dir)
        mcp_name = _resolve_mcp_tool_name(tool_name)
        truncated = _truncate_params(params)

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "sid": sid,
            "tool": mcp_name,
            "params": truncated,
        }

        os.makedirs(_data_dir, exist_ok=True)
        obs_file = os.path.join(_data_dir, "observations.jsonl")
        line = json.dumps(entry, ensure_ascii=False) + "\n"

        # Atomic-ish append: open in append mode (OS-level atomic on most filesystems)
        with open(obs_file, "a", encoding="utf-8") as f:
            f.write(line)

    except Exception as e:
        # Fail-open: log but never propagate
        logger.debug("observation_writer: failed to log %s: %s", tool_name, e)
