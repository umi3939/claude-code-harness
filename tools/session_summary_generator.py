#!/usr/bin/env python3
"""Auto-generate session summary from git log and observations.

Reads git commits since session start and tool usage observations
to produce a structured summary for session_end.

Usage:
    python session_summary_generator.py [--session-start-time EPOCH_MS]

If no start time given, reads from ~/.claude/hooks/.session-start-time
"""

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

_TOOLS_DIR = Path(os.path.abspath(__file__)).parent
CLAUDE_DIR = _TOOLS_DIR.parent
OBS_FILE = CLAUDE_DIR / "data" / "observations.jsonl"
SESSION_TIME_FILE = CLAUDE_DIR / "hooks" / ".session-start-time"


def get_session_start_time(override_ms: int = 0) -> int:
    """Get session start time in milliseconds."""
    if override_ms > 0:
        return override_ms
    if SESSION_TIME_FILE.exists():
        try:
            return int(SESSION_TIME_FILE.read_text().strip())
        except ValueError:
            pass
    return 0


def get_git_commits_since(start_time_ms: int) -> list[dict]:
    """Get git commits since the given timestamp."""
    if start_time_ms <= 0:
        return []

    start_dt = datetime.fromtimestamp(start_time_ms / 1000, tz=timezone.utc)
    since_str = start_dt.strftime("%Y-%m-%dT%H:%M:%S")

    commits = []
    for repo_dir in [CLAUDE_DIR]:
        if not (repo_dir / ".git").exists():
            continue
        try:
            result = subprocess.run(
                ["git", "log", f"--since={since_str}", "--oneline", "--no-merges"],  # noqa: S607
                capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(repo_dir), timeout=10
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    if line.strip():
                        commits.append({
                            "repo": repo_dir.name,
                            "message": line.strip(),
                        })
        except Exception:
            pass

    return commits


def get_tool_usage_since(start_time_ms: int) -> dict:
    """Get tool usage stats since session start."""
    if not OBS_FILE.exists():
        return {}

    start_dt = datetime.fromtimestamp(start_time_ms / 1000, tz=timezone.utc)
    tool_counts = {}

    try:
        with open(OBS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    obs = json.loads(line.strip())
                    obs_time = datetime.fromisoformat(obs["ts"].replace("Z", "+00:00"))
                    if obs_time >= start_dt:
                        tool = obs.get("tool", "unknown")
                        tool_counts[tool] = tool_counts.get(tool, 0) + 1
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
    except Exception:
        pass

    return tool_counts


def get_files_changed_since(start_time_ms: int) -> dict:
    """Get files changed in commits since session start."""
    if start_time_ms <= 0:
        return {"added": 0, "modified": 0, "total_insertions": 0, "total_deletions": 0}

    start_dt = datetime.fromtimestamp(start_time_ms / 1000, tz=timezone.utc)
    since_str = start_dt.strftime("%Y-%m-%dT%H:%M:%S")

    stats = {"added": 0, "modified": 0, "total_insertions": 0, "total_deletions": 0}

    for repo_dir in [CLAUDE_DIR]:
        if not (repo_dir / ".git").exists():
            continue
        try:
            result = subprocess.run(
                ["git", "log", f"--since={since_str}", "--shortstat", "--no-merges"],  # noqa: S607
                capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(repo_dir), timeout=10
            )
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    line = line.strip()
                    if "insertion" in line or "deletion" in line:
                        import re
                        ins = re.search(r"(\d+) insertion", line)
                        dels = re.search(r"(\d+) deletion", line)
                        files = re.search(r"(\d+) file", line)
                        if ins:
                            stats["total_insertions"] += int(ins.group(1))
                        if dels:
                            stats["total_deletions"] += int(dels.group(1))
                        if files:
                            stats["modified"] += int(files.group(1))
        except Exception:
            pass

    return stats


def generate_summary() -> dict:
    """Generate a complete session summary."""
    start_ms = get_session_start_time()
    commits = get_git_commits_since(start_ms)
    tools = get_tool_usage_since(start_ms)
    file_stats = get_files_changed_since(start_ms)

    # Build summary
    commit_messages = [c["message"] for c in commits]

    # Top tools
    sorted_tools = sorted(tools.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "commit_count": len(commits),
        "commits": commit_messages,
        "tool_usage": dict(sorted_tools),
        "total_tool_calls": sum(tools.values()),
        "file_stats": file_stats,
    }


def format_summary(data: dict) -> str:
    """Format summary for display."""
    lines = ["=== Session Summary (Auto-Generated) ==="]
    lines.append(f"Commits: {data['commit_count']}")
    for msg in data["commits"][:15]:
        lines.append(f"  {msg}")

    lines.append(f"\nTool calls: {data['total_tool_calls']}")
    for tool, count in data["tool_usage"].items():
        lines.append(f"  {tool}: {count}")

    fs = data["file_stats"]
    lines.append(f"\nFiles changed: {fs['modified']}, +{fs['total_insertions']}/-{fs['total_deletions']}")

    return "\n".join(lines)


def main():
    data = generate_summary()
    print(format_summary(data))


if __name__ == "__main__":
    main()
