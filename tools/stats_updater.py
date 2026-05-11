#!/usr/bin/env python3
"""Auto-compute and update infrastructure stats across documentation.

Counts MCP servers, tools, blocking hooks, agents, commands, tests.
Can display stats or auto-update MEMORY.md and other docs.

Usage:
    python stats_updater.py              # Display current stats
    python stats_updater.py --update     # Update docs with current stats
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

_TOOLS_DIR_PATH = Path(os.path.abspath(__file__)).parent
CLAUDE_DIR = _TOOLS_DIR_PATH.parent
TOOLS_DIR = CLAUDE_DIR / "tools"
HOOKS_DIR = CLAUDE_DIR / "hooks"
AGENTS_DIR = CLAUDE_DIR / "agents"
COMMANDS_DIR = CLAUDE_DIR / "commands"
MCP_JSON = CLAUDE_DIR / ".mcp.json"
RULES_JSON = HOOKS_DIR / "behavior-rules.json"


def count_mcp_servers() -> dict:
    """Count MCP servers and their tools from .mcp.json."""
    if not MCP_JSON.exists():
        return {"servers": 0, "details": {}}
    with open(MCP_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    servers = data.get("mcpServers", {})
    details = {}
    for name, config in servers.items():
        desc = config.get("description", "")
        # Try to extract tool count from description like "(17 tools)"
        m = re.search(r"\((\d+)\s*tool", desc)
        tool_count = int(m.group(1)) if m else "?"
        details[name] = {"description": desc, "tools": tool_count}
    return {"servers": len(servers), "details": details}


def count_blocking_hooks() -> int:
    """Count blocking rules in behavior-rules.json."""
    if not RULES_JSON.exists():
        return 0
    with open(RULES_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    return sum(1 for r in data.get("rules", []) if r.get("blocking"))


def count_total_rules() -> int:
    """Count total rules in behavior-rules.json."""
    if not RULES_JSON.exists():
        return 0
    with open(RULES_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    return len(data.get("rules", []))


def count_agents() -> int:
    """Count agent definition files."""
    if not AGENTS_DIR.exists():
        return 0
    return len(list(AGENTS_DIR.glob("*.md")))


def count_commands() -> int:
    """Count slash command files."""
    if not COMMANDS_DIR.exists():
        return 0
    return len(list(COMMANDS_DIR.glob("*.md")))


def list_commands() -> list[str]:
    """List slash command names (without .md extension)."""
    if not COMMANDS_DIR.exists():
        return []
    return sorted(f.stem for f in COMMANDS_DIR.glob("*.md"))


def list_agents() -> list[str]:
    """List agent names (without .md extension)."""
    if not AGENTS_DIR.exists():
        return []
    return sorted(f.stem for f in AGENTS_DIR.glob("*.md"))


def count_tests() -> int:
    """Count test files and try to get test count."""
    test_files = list(TOOLS_DIR.glob("test_*.py")) + list(TOOLS_DIR.glob("tests/test_*.py"))
    return len(test_files)


def compute_stats() -> dict:
    """Compute all infrastructure stats."""
    mcp = count_mcp_servers()
    total_tools = sum(
        v["tools"] for v in mcp["details"].values()
        if isinstance(v["tools"], int)
    )
    unknown_tools = sum(1 for v in mcp["details"].values() if v["tools"] == "?")

    return {
        "mcp_servers": mcp["servers"],
        "mcp_server_details": mcp["details"],
        "mcp_tools_known": total_tools,
        "mcp_tools_unknown": unknown_tools,
        "mcp_tools_display": f"{total_tools}+" if unknown_tools > 0 else str(total_tools),
        "blocking_hooks": count_blocking_hooks(),
        "total_rules": count_total_rules(),
        "agents": count_agents(),
        "agent_names": list_agents(),
        "commands": count_commands(),
        "command_names": list_commands(),
        "test_files": count_tests(),
    }


def format_stats(stats: dict) -> str:
    """Format stats for display."""
    lines = [
        "=== Infrastructure Stats ===",
        f"MCP Servers: {stats['mcp_servers']}",
    ]
    for name, detail in stats["mcp_server_details"].items():
        lines.append(f"  - {name}: {detail['tools']} tools")
    lines.extend([
        f"MCP Tools: {stats['mcp_tools_display']}",
        f"Behavior Rules: {stats['total_rules']} ({stats['blocking_hooks']} blocking)",
        f"Agents: {stats['agents']}",
        f"Commands: {stats['commands']}",
        f"Test Files: {stats['test_files']}",
    ])
    return "\n".join(lines)


def build_mcp_summary(stats: dict) -> str:
    """Build MCP summary line for MEMORY.md."""
    parts = []
    for name, detail in stats["mcp_server_details"].items():
        tools = detail["tools"]
        if isinstance(tools, int):
            parts.append(f"{name}({tools})")
        else:
            parts.append(f"{name}")
    return f"**MCP {stats['mcp_servers']}サーバー**: {' + '.join(parts)} = {stats['mcp_tools_display']}ツール"


def update_memory_md(stats: dict) -> bool:
    """Update MEMORY.md with current stats."""
    # Find MEMORY.md in both project memory dirs
    # Use CLAUDE_MEMORY_DIR env var if set, otherwise scan project dirs
    env_dir = os.environ.get("CLAUDE_MEMORY_DIR")
    if env_dir:
        memory_dirs = [Path(env_dir)]
    else:
        projects_dir = CLAUDE_DIR / "projects"
        memory_dirs = []
        if projects_dir.exists():
            for p in projects_dir.iterdir():
                mem = p / "memory"
                if mem.exists() and (mem / "MEMORY.md").exists():
                    memory_dirs.append(mem)

    updated = False
    for memory_dir in memory_dirs:
        memory_md = memory_dir / "MEMORY.md"
        if not memory_md.exists():
            continue

        content = memory_md.read_text(encoding="utf-8")
        new_content = content

        # Update MCP server line
        mcp_line = build_mcp_summary(stats)
        new_content = re.sub(
            r"- \*\*MCP \d+サーバー\*\*:.*ツール",
            f"- {mcp_line}",
            new_content,
        )

        # Update blocking hooks count
        new_content = re.sub(
            r"BehaviorGuard\(\d+ ?ルール,\s*\d+ blocking\)",
            f"BehaviorGuard({stats['total_rules']}ルール, {stats['blocking_hooks']} blocking)",
            new_content,
        )

        # Update commands count and names list
        cmd_names = " ".join(f"`/{n}`" for n in stats["command_names"])
        new_content = re.sub(
            r"\*\*スラッシュコマンド\d+つ\*\*:.*$",
            f"**スラッシュコマンド{stats['commands']}つ**: {cmd_names}",
            new_content,
            flags=re.MULTILINE,
        )

        if new_content != content:
            memory_md.write_text(new_content, encoding="utf-8")
            print(f"Updated: {memory_md}")
            updated = True

    return updated


def update_mcp_tools_md(stats: dict) -> bool:
    """Update mcp-tools.md description with current counts."""
    mcp_tools_md = COMMANDS_DIR / "mcp-tools.md"
    if not mcp_tools_md.exists():
        return False

    content = mcp_tools_md.read_text(encoding="utf-8")
    new_content = re.sub(
        r'description: "MCPツール全\d+\+?種の使い方を表示"',
        f'description: "MCPツール全{stats["mcp_tools_display"]}種の使い方を表示"',
        content,
    )
    new_content = re.sub(
        r"# MCPツール全\d+\+?種の使い方（\d+サーバー構成）",
        f'# MCPツール全{stats["mcp_tools_display"]}種の使い方（{stats["mcp_servers"]}サーバー構成）',
        new_content,
    )

    if new_content != content:
        mcp_tools_md.write_text(new_content, encoding="utf-8")
        print(f"Updated: {mcp_tools_md}")
        return True
    return False


def main():
    update_mode = "--update" in sys.argv

    stats = compute_stats()
    print(format_stats(stats))

    if update_mode:
        print("\n=== Updating docs ===")
        update_memory_md(stats)
        update_mcp_tools_md(stats)
        print("Done.")


if __name__ == "__main__":
    main()
