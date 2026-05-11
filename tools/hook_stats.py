#!/usr/bin/env python3
"""Hook発火統計表示スクリプト.

observations.jsonlを解析して以下を表示:
1. ツール呼び出し統計（上位20件）
2. セッション統計
3. 時間帯統計
4. 直近のアクティビティ

Usage:
    python hook_stats.py [--recent N] [--session SESSION_ID]
"""

import argparse
import json
import os
import sys
from datetime import datetime
from collections import Counter, OrderedDict
from typing import Dict, List, Optional

DEFAULT_OBS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "data", "observations.jsonl"
)

DEFAULT_FIRINGS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "data", "hook_firing_log.jsonl"
)

DEFAULT_RULES_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "hooks", "behavior-rules.json"
)


def parse_observations(filepath: str) -> List[dict]:
    """Parse observations.jsonl, skipping invalid lines.

    Returns list of dicts with at least ts, tool, sid fields.
    """
    entries = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue
    except (FileNotFoundError, OSError):
        return []
    return entries


def compute_tool_stats(entries: List[dict]) -> Dict[str, int]:
    """Count calls per tool. Returns dict sorted by count descending."""
    counter = Counter(e.get("tool", "unknown") for e in entries)
    return dict(counter.most_common())


def compute_session_stats(entries: List[dict]) -> Dict[str, int]:
    """Count calls per session. Returns dict sorted by count descending."""
    counter = Counter(e.get("sid", "unknown") for e in entries)
    return dict(counter.most_common())


def compute_hourly_stats(entries: List[dict]) -> Dict[int, int]:
    """Count calls per hour (0-23). Returns OrderedDict with all 24 hours."""
    counter = Counter()
    for e in entries:
        ts = e.get("ts", "")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            counter[dt.hour] += 1
        except (ValueError, TypeError):
            continue
    result = OrderedDict()
    for h in range(24):
        result[h] = counter.get(h, 0)
    return result


def get_recent_activity(entries: List[dict], n: int = 10) -> List[dict]:
    """Return the most recent N entries (newest first)."""
    if n <= 0:
        return []
    return list(reversed(entries[-n:]))


def filter_by_session(entries: List[dict], session_id: Optional[str]) -> List[dict]:
    """Filter entries by session ID. If None, return all."""
    if session_id is None:
        return entries
    return [e for e in entries if e.get("sid") == session_id]


def format_stats(entries: List[dict], recent_n: int = 10) -> str:
    """Format all statistics as text output."""
    lines = []
    total = len(entries)
    lines.append(f"=== Hook Statistics ({total} total calls) ===")
    lines.append("")

    # 1. Tool call statistics (top 20)
    lines.append("--- Tool Call Counts (top 20) ---")
    tool_stats = compute_tool_stats(entries)
    for i, (tool, count) in enumerate(tool_stats.items()):
        if i >= 20:
            break
        bar = "#" * min(count, 50)
        lines.append(f"  {tool:30s} {count:5d}  {bar}")
    if not tool_stats:
        lines.append("  (no data)")
    lines.append("")

    # 2. Session statistics
    lines.append("--- Session Counts ---")
    session_stats = compute_session_stats(entries)
    for sid, count in session_stats.items():
        lines.append(f"  {sid:30s} {count:5d}")
    if not session_stats:
        lines.append("  (no data)")
    lines.append("")

    # 3. Hourly statistics
    lines.append("--- Hourly Distribution ---")
    hourly = compute_hourly_stats(entries)
    max_val = max(hourly.values()) if hourly and max(hourly.values()) > 0 else 1
    for hour, count in hourly.items():
        bar_len = int((count / max_val) * 30) if max_val > 0 else 0
        bar = "#" * bar_len
        lines.append(f"  {hour:02d}:00  {count:5d}  {bar}")
    lines.append("")

    # 4. Recent activity
    lines.append(f"--- Recent Activity (last {recent_n}) ---")
    recent = get_recent_activity(entries, recent_n)
    for e in recent:
        ts = e.get("ts", "?")[:19]
        tool = e.get("tool", "?")
        sid = e.get("sid", "?")
        params_str = ""
        params = e.get("params", {})
        if isinstance(params, dict):
            if "cmd" in params:
                params_str = params["cmd"][:60]
            elif "file" in params:
                params_str = params["file"]
        lines.append(f"  {ts}  {tool:15s}  [{sid}]  {params_str}")
    if not recent:
        lines.append("  (no data)")
    lines.append("")

    return "\n".join(lines)


##############################################################################
# Hook firing log functions (C18-6)
##############################################################################

def parse_hook_firings(filepath: str) -> List[dict]:
    """Parse hook_firing_log.jsonl, skipping invalid lines.

    Returns list of dicts with at least ts, rule_id, blocking, outcome fields.
    """
    entries = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue
    except (FileNotFoundError, OSError):
        return []
    return entries


def rule_firing_stats(entries: List[dict]) -> Dict[str, Dict[str, int]]:
    """Count firings per rule, distinguishing blocked vs warned.

    Returns dict: {rule_id: {"blocked": N, "warned": N, "total": N}}
    """
    stats: Dict[str, Dict[str, int]] = {}
    for e in entries:
        rid = e.get("rule_id", "unknown")
        if rid not in stats:
            stats[rid] = {"blocked": 0, "warned": 0, "total": 0}
        outcome = e.get("outcome", "warned")
        if outcome == "blocked":
            stats[rid]["blocked"] += 1
        else:
            stats[rid]["warned"] += 1
        stats[rid]["total"] += 1
    return stats


def daily_firing_trend(entries: List[dict]) -> Dict[str, int]:
    """Count firings per day (YYYY-MM-DD).

    Returns OrderedDict sorted by date.
    """
    counter: Dict[str, int] = {}
    for e in entries:
        ts = e.get("ts", "")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            day = dt.strftime("%Y-%m-%d")
            counter[day] = counter.get(day, 0) + 1
        except (ValueError, TypeError):
            continue
    return OrderedDict(sorted(counter.items()))


def zero_fire_rules(entries: List[dict], all_rule_ids: List[str]) -> List[str]:
    """Find rules that are defined but have zero firings.

    Args:
        entries: parsed firing log entries
        all_rule_ids: list of all defined rule IDs from behavior-rules.json

    Returns: list of rule IDs with zero firings
    """
    fired = set(e.get("rule_id") for e in entries)
    return [rid for rid in all_rule_ids if rid not in fired]


def rotate_hook_firing_log(filepath: str, max_lines: int = 1000) -> bool:
    """Rotate hook firing log if it exceeds max_lines.

    Keeps the last max_lines lines. Returns True if rotation happened.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = [l for l in f.read().strip().split("\n") if l.strip()]
    except (FileNotFoundError, OSError):
        return False

    if len(lines) <= max_lines:
        return False

    kept = lines[-max_lines:]
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(kept) + "\n")
    return True


def load_all_rule_ids(rules_path: str) -> List[str]:
    """Load all rule IDs from behavior-rules.json."""
    try:
        with open(rules_path, "r", encoding="utf-8") as f:
            data = json.loads(f.read())
        return [r["id"] for r in data.get("rules", []) if "id" in r]
    except (FileNotFoundError, OSError, json.JSONDecodeError, KeyError):
        return []


def format_firing_stats(entries: List[dict], all_rule_ids: List[str]) -> str:
    """Format firing statistics as text output."""
    lines = []
    total = len(entries)
    lines.append(f"=== Hook Firing Statistics ({total} total firings) ===")
    lines.append("")

    # 1. Rule firing counts
    lines.append("--- Rule Firing Counts ---")
    stats = rule_firing_stats(entries)
    if stats:
        # Sort by total descending
        sorted_stats = sorted(stats.items(), key=lambda x: x[1]["total"], reverse=True)
        for rid, counts in sorted_stats:
            b = counts["blocked"]
            w = counts["warned"]
            t = counts["total"]
            lines.append(f"  {rid:40s}  total:{t:4d}  blocked:{b:3d}  warned:{w:3d}")
    else:
        lines.append("  (no firings)")
    lines.append("")

    # 2. Daily trend
    lines.append("--- Daily Firing Trend ---")
    trend = daily_firing_trend(entries)
    if trend:
        for day, count in trend.items():
            bar = "#" * min(count, 50)
            lines.append(f"  {day}  {count:5d}  {bar}")
    else:
        lines.append("  (no data)")
    lines.append("")

    # 3. Zero-fire rules
    lines.append("--- Zero-Fire Rules ---")
    zero = zero_fire_rules(entries, all_rule_ids)
    if zero:
        for rid in zero:
            lines.append(f"  {rid}")
    else:
        lines.append("  (all rules have fired)")
    lines.append("")

    return "\n".join(lines)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Hook発火統計表示")
    parser.add_argument("--recent", type=int, default=10,
                        help="直近N件のアクティビティを表示 (default: 10)")
    parser.add_argument("--session", type=str, default=None,
                        help="特定セッションIDでフィルタ")
    parser.add_argument("--file", type=str, default=None,
                        help="observations.jsonlのパス (default: auto)")
    parser.add_argument("--firings", action="store_true",
                        help="発火ログ統計を表示")
    parser.add_argument("--firings-file", type=str, default=None,
                        help="hook_firing_log.jsonlのパス (default: auto)")
    parser.add_argument("--rules-file", type=str, default=None,
                        help="behavior-rules.jsonのパス (default: auto)")
    parser.add_argument("--rotate", action="store_true",
                        help="発火ログのローテーション実行 (1000行上限)")
    return parser.parse_args(argv)


def main():
    args = parse_args()

    if args.rotate:
        firings_path = args.firings_file or os.path.normpath(DEFAULT_FIRINGS_PATH)
        rotated = rotate_hook_firing_log(firings_path, max_lines=1000)
        if rotated:
            print(f"Rotated {firings_path} to 1000 lines")
        else:
            print(f"No rotation needed for {firings_path}")
        return

    if args.firings:
        firings_path = args.firings_file or os.path.normpath(DEFAULT_FIRINGS_PATH)
        rules_path = args.rules_file or os.path.normpath(DEFAULT_RULES_PATH)
        firing_entries = parse_hook_firings(firings_path)
        all_ids = load_all_rule_ids(rules_path)
        output = format_firing_stats(firing_entries, all_ids)
        print(output)
        return

    obs_path = args.file or os.path.normpath(DEFAULT_OBS_PATH)
    entries = parse_observations(obs_path)
    entries = filter_by_session(entries, args.session)
    output = format_stats(entries, recent_n=args.recent)
    print(output)


if __name__ == "__main__":
    main()
