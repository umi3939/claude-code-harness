#!/usr/bin/env python3
"""Tool usage tracker: extracts all MCP tools from server files and compares
against observations.jsonl to show which tools have been used in the current session.

Used by:
- tool_usage_status MCP tool (self_observation_mcp_server.py)
- Context injection in skill_executor.py (_get_session_start_extras)
"""

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

# Regex to match @mcp.tool() followed by def function_name(
_TOOL_DEF_PATTERN = re.compile(
    r"@mcp\.tool\(\)\s*\n\s*(?:async\s+)?def\s+(\w+)\s*\("
)

# Default MCP server files (relative to project root)
_DEFAULT_SERVER_FILENAMES = [
    "memory_mcp_server.py",
    "self_observation_mcp_server.py",
    "cron_mcp_server.py",
    "discord_mcp_server.py",
    "http_fetch_mcp_server.py",
]

# Tools that are auto-triggered by hooks/cron (always counted as "used")
_AUTO_TRIGGERED_TOOLS = {
    "activation_surface", "emotion_restore", "stm_restore",
    "self_observe", "self_difference", "continuity_strain",
    "self_image", "identity_coherence", "stability_check", "tone_check",
    "psyche_drive", "behavior_guidance", "long_term_record",
    "hook_health_check", "sync_hooks_to_global",
    "observation_log", "self_snapshot",
}

# Regex to extract tool name from mcp__server__tool_name format
_MCP_TOOL_NAME_PATTERN = re.compile(r"^mcp__[^_]+(?:__|-\w+__)?(\w+)$")


def _extract_tool_name_from_mcp_prefix(tool_field: str) -> str:
    """Extract tool name from mcp__server-name__tool_name format.

    Examples:
        mcp__memory-tools__session_start -> session_start
        mcp__self-observation__self_observe -> self_observe
        mcp__persistent-cron__persistent_cron_add -> persistent_cron_add
    """
    # Split on __ and take the last part
    parts = tool_field.split("__")
    if len(parts) >= 3:
        return parts[-1]
    return ""


def get_all_mcp_tools(server_files: list) -> dict:
    """Extract all @mcp.tool() decorated function names from MCP server files.

    Args:
        server_files: List of absolute paths to MCP server Python files.

    Returns:
        Dict mapping server name (without .py) to list of tool function names.
        Example: {"memory_mcp_server": ["session_start", "memory_search", ...]}
    """
    result = {}
    for filepath in server_files:
        server_name = os.path.basename(filepath).replace(".py", "")
        if not os.path.exists(filepath):
            logger.warning("MCP server file not found: %s", filepath)
            continue
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as e:
            logger.error("Failed to read %s: %s", filepath, e)
            continue

        tools = _TOOL_DEF_PATTERN.findall(content)
        result[server_name] = tools

    return result


def get_session_tool_usage(
    observations_file: str,
    session_start_time: str = None,
) -> set:
    """Get set of MCP tool names used in the current session from observations.jsonl.

    Args:
        observations_file: Absolute path to observations.jsonl.
        session_start_time: ISO timestamp string. If provided, only entries
            with ts >= this value are included. If None, all entries are included.

    Returns:
        Set of tool names (e.g. {"session_start", "memory_search"}).
        Returns empty set if file doesn't exist or is empty.
    """
    if not os.path.exists(observations_file):
        return set()

    used_tools = set()
    try:
        with open(observations_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                tool_field = entry.get("tool", "")
                if not tool_field.startswith("mcp__"):
                    continue

                # Apply time filter if specified
                if session_start_time is not None:
                    ts = entry.get("ts", "")
                    if ts < session_start_time:
                        continue

                tool_name = _extract_tool_name_from_mcp_prefix(tool_field)
                if tool_name:
                    used_tools.add(tool_name)
    except OSError as e:
        logger.error("Failed to read observations file: %s", e)
        return set()

    return used_tools | _AUTO_TRIGGERED_TOOLS


def format_usage_report(all_tools: dict, used_tools: set) -> str:
    """Format a usage report showing which tools have been used.

    Args:
        all_tools: Dict from get_all_mcp_tools (server_name -> [tool_names]).
        used_tools: Set from get_session_tool_usage.

    Returns:
        Formatted report string with per-server grouping and summary.
    """
    total_count = 0
    used_count = 0
    unused_list = []
    lines = []

    for server_name, tools in sorted(all_tools.items()):
        lines.append(f"--- {server_name} ---")
        for tool in sorted(tools):
            total_count += 1
            if tool in used_tools:
                used_count += 1
                marker = "[a]" if tool in _AUTO_TRIGGERED_TOOLS else ""
                suffix = f" {marker}" if marker else ""
                lines.append(f"  [x] {tool}{suffix}")
            else:
                lines.append(f"  [ ] {tool}")
                unused_list.append(tool)
        lines.append("")

    # Summary
    pct = (used_count * 100 // total_count) if total_count > 0 else 0
    summary = f"Used: {used_count}/{total_count} ({pct}%)"

    header = [
        "=== MCP Tool Usage Status ===",
        summary,
        "",
    ]

    # Unused tools section
    footer = []
    if unused_list:
        footer.append(f"--- Unused Tools ({len(unused_list)}) ---")
        for t in sorted(unused_list):
            footer.append(f"  - {t}")

    return "\n".join(header + lines + footer)


def get_default_server_files(project_root: str = None) -> list:
    """Get default MCP server file paths.

    Args:
        project_root: Project root directory. If None, auto-detected.

    Returns:
        List of absolute paths to MCP server files.
    """
    if project_root is None:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tools_dir = os.path.join(project_root, "tools")
    return [
        os.path.join(tools_dir, fname) for fname in _DEFAULT_SERVER_FILENAMES
    ]


def get_usage_summary(
    observations_file: str = None,
    session_start_time: str = None,
    project_root: str = None,
) -> str:
    """Compact usage summary for context injection.

    Returns string like:
        ToolUsage: 64/73 (87%) | Unused: tool_a, tool_b | Auto[a]: tool_c, tool_d
    Fail-open: returns empty string on any error.
    """
    try:
        if project_root is None:
            project_root = os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            )
        if observations_file is None:
            observations_file = os.path.join(
                project_root, "data", "observations.jsonl"
            )
        server_files = get_default_server_files(project_root)
        all_tools = get_all_mcp_tools(server_files)
        used = get_session_tool_usage(observations_file, session_start_time)

        all_tool_names = {t for tools in all_tools.values() for t in tools}
        total = len(all_tool_names)
        used_in_scope = used & all_tool_names
        used_count = len(used_in_scope)
        pct = (used_count * 100 // total) if total > 0 else 0

        unused = sorted(all_tool_names - used)
        auto_in_scope = sorted(_AUTO_TRIGGERED_TOOLS & all_tool_names)

        parts = [f"ToolUsage: {used_count}/{total} ({pct}%)"]
        if unused:
            parts.append(f"Unused: {', '.join(unused)}")
        if auto_in_scope:
            parts.append(f"Auto[a]: {', '.join(auto_in_scope)}")

        return " | ".join(parts)
    except Exception as e:
        logger.error("get_usage_summary failed: %s", e)
        return ""
