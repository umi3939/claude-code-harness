#!/usr/bin/env python3
"""Session Postmortem Generator — structured reflection for session end.

Analyzes STM entries, git commits, and hook firing logs to generate
a structured postmortem with three axes:
- What went well
- What was difficult
- Lessons for next time

Usage:
    python session_postmortem.py
"""

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

_TOOLS_DIR = Path(os.path.abspath(__file__)).parent
CLAUDE_DIR = _TOOLS_DIR.parent
HOOKS_DIR = CLAUDE_DIR / "hooks"
DATA_DIR = CLAUDE_DIR / "data"
SESSION_TIME_FILE = HOOKS_DIR / ".session-start-time"
FIRING_LOG_FILE = DATA_DIR / "hook_firing_log.jsonl"

# Memory directory (directly under project root)
MEMORY_DIR = str(CLAUDE_DIR / "memory")


def _get_session_start_ms():
    """Get session start time in milliseconds."""
    try:
        if SESSION_TIME_FILE.exists():
            return int(SESSION_TIME_FILE.read_text().strip())
    except (ValueError, OSError):
        pass
    return 0


def _get_stm_entries(memory_dir=None):
    """Read STM entries from short_term_memory.json."""
    mem_dir = memory_dir or MEMORY_DIR
    if not mem_dir:
        return []
    stm_file = Path(mem_dir) / "short_term_memory.json"
    try:
        if stm_file.exists():
            data = json.loads(stm_file.read_text(encoding="utf-8"))
            return data.get("entries", [])
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _get_hook_firings_since(start_ms):
    """Get hook firing events since session start."""
    if not FIRING_LOG_FILE.exists() or start_ms <= 0:
        return []
    start_dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    firings = []
    try:
        with open(FIRING_LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    ts = datetime.fromisoformat(entry["ts"].replace("Z", "+00:00"))
                    if ts >= start_dt:
                        firings.append(entry)
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
    except OSError:
        pass
    return firings


def _get_commits_since(start_ms):
    """Get git commit messages since session start."""
    if start_ms <= 0:
        return []
    start_dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    since_str = start_dt.strftime("%Y-%m-%dT%H:%M:%S")
    commits = []
    for repo_dir in [CLAUDE_DIR]:
        if not (repo_dir / ".git").exists():
            continue
        try:
            result = subprocess.run(
                ["git", "log", f"--since={since_str}", "--oneline", "--no-merges"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(repo_dir),
                timeout=10,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    if line.strip():
                        commits.append(line.strip())
        except Exception:
            pass
    return commits


def generate_postmortem(memory_dir=None):
    """Generate a structured postmortem from session data.

    Args:
        memory_dir: Override memory directory (for testing)

    Returns:
        dict with keys: went_well, difficulties, lessons
    """
    start_ms = _get_session_start_ms()
    stm_entries = _get_stm_entries(memory_dir)
    hook_firings = _get_hook_firings_since(start_ms)
    commits = _get_commits_since(start_ms)

    went_well = []
    difficulties = []
    lessons = []

    # Analyze commits (productivity indicator)
    if commits:
        went_well.append(f"{len(commits)} commits completed this session")

    # Analyze STM entries by category
    category_counts = {}
    for entry in stm_entries:
        cat = entry.get("category", "unknown")
        category_counts[cat] = category_counts.get(cat, 0) + 1
        content = entry.get("content", "")

        # Positive signals
        if cat == "thought" and any(
            w in content.lower() for w in ["worked", "success", "うまく", "成功"]
        ):
            went_well.append(content[:120])

        # Difficulty signals
        if cat == "unresolved":
            difficulties.append(content[:120])
        if cat == "question":
            difficulties.append(f"Open question: {content[:100]}")

    # Analyze hook firings (blocked = difficulty, many warns = process learning)
    blocked_count = sum(1 for f in hook_firings if f.get("blocking"))
    warned_count = sum(1 for f in hook_firings if not f.get("blocking"))

    if blocked_count > 0:
        blocked_rules = {}
        for f in hook_firings:
            if f.get("blocking"):
                rule = f.get("rule_id", "unknown")
                blocked_rules[rule] = blocked_rules.get(rule, 0) + 1
        top_blocks = sorted(blocked_rules.items(), key=lambda x: x[1], reverse=True)[:3]
        for rule, count in top_blocks:
            difficulties.append(f"Blocked by hook '{rule}' x{count}")

    if warned_count > 3:
        lessons.append(
            f"{warned_count} hook warnings this session. Review behavior-rules.json patterns."
        )

    # STM-based lessons
    if category_counts.get("self_review", 0) == 0:
        lessons.append("No self_review STM entries. Consider adding self-review before agent launches.")
    if category_counts.get("thought", 0) > 0:
        lessons.append(
            f"{category_counts['thought']} thought entries recorded. Continue documenting reasoning."
        )

    # Ensure at least one item per category
    if not went_well:
        went_well.append("Session completed.")
    if not difficulties:
        difficulties.append("No significant difficulties recorded.")
    if not lessons:
        lessons.append("Continue following the established workflow.")

    return {
        "went_well": went_well,
        "difficulties": difficulties,
        "lessons": lessons,
    }


def format_postmortem(data):
    """Format postmortem data for display.

    Args:
        data: dict with went_well, difficulties, lessons

    Returns:
        Formatted string
    """
    lines = ["=== セッション・ポストモーテム ===", ""]

    lines.append("## うまくいったこと (Went Well)")
    for item in data.get("went_well", []):
        lines.append(f"  - {item}")
    lines.append("")

    lines.append("## 困難だったこと (Difficulties)")
    for item in data.get("difficulties", []):
        lines.append(f"  - {item}")
    lines.append("")

    lines.append("## 次回への教訓 (Lessons)")
    for item in data.get("lessons", []):
        lines.append(f"  - {item}")

    return "\n".join(lines)


def main():
    data = generate_postmortem()
    print(format_postmortem(data))


if __name__ == "__main__":
    main()
