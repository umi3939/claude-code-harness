#!/usr/bin/env python3
"""Tests for hook_stats.py — Hook発火統計表示スクリプト.

observations.jsonlを解析して以下を表示:
1. ツール呼び出し統計（上位20件）
2. セッション統計
3. 時間帯統計
4. 直近のアクティビティ
"""

import json
import os
import sys
import tempfile
import unittest

# Ensure tools dir is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def make_entry(ts, tool, sid="s001", params=None):
    """Helper to create a single JSONL entry."""
    entry = {"ts": ts, "sid": sid, "tool": tool}
    if params:
        entry["params"] = params
    return json.dumps(entry)


def write_temp_jsonl(content):
    """Write content to a temp file and return path. Caller must delete."""
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


SAMPLE_DATA = "\n".join([
    make_entry("2026-03-20T09:00:00.000Z", "Bash", "s001", {"cmd": "ls"}),
    make_entry("2026-03-20T09:01:00.000Z", "Read", "s001", {"file": "a.py"}),
    make_entry("2026-03-20T09:02:00.000Z", "Bash", "s001", {"cmd": "echo hi"}),
    make_entry("2026-03-20T10:00:00.000Z", "Edit", "s002", {"file": "b.py"}),
    make_entry("2026-03-20T10:01:00.000Z", "Bash", "s002", {"cmd": "pytest"}),
    make_entry("2026-03-20T14:00:00.000Z", "Write", "s003"),
    make_entry("2026-03-20T14:01:00.000Z", "Bash", "s003", {"cmd": "cat f"}),
    make_entry("2026-03-20T23:00:00.000Z", "Read", "s003", {"file": "c.py"}),
]) + "\n"


def get_sample_entries():
    """Parse SAMPLE_DATA via parse_observations and return entries."""
    from hook_stats import parse_observations
    path = write_temp_jsonl(SAMPLE_DATA)
    try:
        return parse_observations(path)
    finally:
        os.unlink(path)


class TestParseObservations(unittest.TestCase):
    """Test parsing of observations.jsonl."""

    def test_parse_valid_entries(self):
        from hook_stats import parse_observations
        path = write_temp_jsonl(SAMPLE_DATA)
        try:
            entries = parse_observations(path)
            self.assertEqual(len(entries), 8)
            self.assertEqual(entries[0]["tool"], "Bash")
            self.assertEqual(entries[0]["sid"], "s001")
        finally:
            os.unlink(path)

    def test_parse_empty_file(self):
        from hook_stats import parse_observations
        path = write_temp_jsonl("")
        try:
            entries = parse_observations(path)
            self.assertEqual(entries, [])
        finally:
            os.unlink(path)

    def test_parse_skips_invalid_json(self):
        from hook_stats import parse_observations
        data = make_entry("2026-03-20T09:00:00.000Z", "Bash", "s001") + "\n"
        data += "not valid json\n"
        data += make_entry("2026-03-20T09:01:00.000Z", "Read", "s001") + "\n"
        path = write_temp_jsonl(data)
        try:
            entries = parse_observations(path)
            self.assertEqual(len(entries), 2)
        finally:
            os.unlink(path)

    def test_parse_skips_empty_lines(self):
        from hook_stats import parse_observations
        data = make_entry("2026-03-20T09:00:00.000Z", "Bash", "s001") + "\n\n\n"
        path = write_temp_jsonl(data)
        try:
            entries = parse_observations(path)
            self.assertEqual(len(entries), 1)
        finally:
            os.unlink(path)

    def test_parse_nonexistent_file(self):
        from hook_stats import parse_observations
        entries = parse_observations("/nonexistent/path/file.jsonl")
        self.assertEqual(entries, [])


class TestToolStats(unittest.TestCase):
    """Test tool call statistics."""

    def test_tool_counts(self):
        from hook_stats import compute_tool_stats
        entries = get_sample_entries()
        stats = compute_tool_stats(entries)
        # Bash appears 4 times, Read 2, Edit 1, Write 1
        self.assertEqual(stats["Bash"], 4)
        self.assertEqual(stats["Read"], 2)
        self.assertEqual(stats["Edit"], 1)
        self.assertEqual(stats["Write"], 1)

    def test_tool_counts_empty(self):
        from hook_stats import compute_tool_stats
        stats = compute_tool_stats([])
        self.assertEqual(len(stats), 0)

    def test_tool_stats_top_n(self):
        from hook_stats import compute_tool_stats
        # Create many different tools
        entries = []
        for i in range(25):
            entries.append({"ts": f"2026-03-20T09:{i:02d}:00.000Z", "tool": f"Tool{i}", "sid": "s1"})
        # Tool0 gets extra calls
        for _ in range(5):
            entries.append({"ts": "2026-03-20T10:00:00.000Z", "tool": "Tool0", "sid": "s1"})
        stats = compute_tool_stats(entries)
        self.assertEqual(stats["Tool0"], 6)  # 1 + 5
        self.assertEqual(len(stats), 25)


class TestSessionStats(unittest.TestCase):
    """Test session statistics."""

    def test_session_counts(self):
        from hook_stats import compute_session_stats
        entries = get_sample_entries()
        stats = compute_session_stats(entries)
        self.assertEqual(stats["s001"], 3)
        self.assertEqual(stats["s002"], 2)
        self.assertEqual(stats["s003"], 3)

    def test_session_counts_empty(self):
        from hook_stats import compute_session_stats
        stats = compute_session_stats([])
        self.assertEqual(len(stats), 0)


class TestHourlyStats(unittest.TestCase):
    """Test hourly statistics."""

    def test_hourly_counts(self):
        from hook_stats import compute_hourly_stats
        entries = get_sample_entries()
        stats = compute_hourly_stats(entries)
        self.assertEqual(stats[9], 3)   # 09:00, 09:01, 09:02
        self.assertEqual(stats[10], 2)  # 10:00, 10:01
        self.assertEqual(stats[14], 2)  # 14:00, 14:01
        self.assertEqual(stats[23], 1)  # 23:00
        # Hours with no activity should be 0
        self.assertEqual(stats[0], 0)
        self.assertEqual(stats[12], 0)

    def test_hourly_all_24_hours(self):
        from hook_stats import compute_hourly_stats
        entries = get_sample_entries()
        stats = compute_hourly_stats(entries)
        self.assertEqual(len(stats), 24)

    def test_hourly_empty(self):
        from hook_stats import compute_hourly_stats
        stats = compute_hourly_stats([])
        self.assertEqual(len(stats), 24)
        self.assertTrue(all(v == 0 for v in stats.values()))


class TestRecentActivity(unittest.TestCase):
    """Test recent activity listing."""

    def test_recent_default(self):
        from hook_stats import get_recent_activity
        entries = get_sample_entries()
        recent = get_recent_activity(entries, n=3)
        self.assertEqual(len(recent), 3)
        # Should be last 3 entries (most recent first)
        self.assertEqual(recent[0]["tool"], "Read")
        self.assertEqual(recent[1]["tool"], "Bash")
        self.assertEqual(recent[2]["tool"], "Write")

    def test_recent_more_than_available(self):
        from hook_stats import get_recent_activity
        entries = get_sample_entries()
        recent = get_recent_activity(entries, n=100)
        self.assertEqual(len(recent), 8)

    def test_recent_zero(self):
        from hook_stats import get_recent_activity
        entries = get_sample_entries()
        recent = get_recent_activity(entries, n=0)
        self.assertEqual(len(recent), 0)

    def test_recent_empty(self):
        from hook_stats import get_recent_activity
        recent = get_recent_activity([], n=5)
        self.assertEqual(len(recent), 0)


class TestSessionFilter(unittest.TestCase):
    """Test filtering by session ID."""

    def test_filter_by_session(self):
        from hook_stats import filter_by_session
        entries = get_sample_entries()
        filtered = filter_by_session(entries, "s002")
        self.assertEqual(len(filtered), 2)
        self.assertTrue(all(e["sid"] == "s002" for e in filtered))

    def test_filter_nonexistent_session(self):
        from hook_stats import filter_by_session
        entries = get_sample_entries()
        filtered = filter_by_session(entries, "nonexistent")
        self.assertEqual(len(filtered), 0)

    def test_filter_none_returns_all(self):
        from hook_stats import filter_by_session
        entries = get_sample_entries()
        filtered = filter_by_session(entries, None)
        self.assertEqual(len(filtered), 8)


class TestFormatOutput(unittest.TestCase):
    """Test formatted output."""

    def test_format_produces_output(self):
        from hook_stats import format_stats
        entries = [
            {"ts": "2026-03-20T09:00:00.000Z", "tool": "Bash", "sid": "s001"},
            {"ts": "2026-03-20T10:00:00.000Z", "tool": "Read", "sid": "s001"},
        ]
        output = format_stats(entries, recent_n=5)
        self.assertIn("Bash", output)
        self.assertIn("Read", output)

    def test_format_empty(self):
        from hook_stats import format_stats
        output = format_stats([], recent_n=5)
        self.assertIn("0", output)  # Should show 0 total

    def test_format_contains_sections(self):
        from hook_stats import format_stats
        entries = [
            {"ts": "2026-03-20T09:00:00.000Z", "tool": "Bash", "sid": "s001"},
        ]
        output = format_stats(entries, recent_n=5)
        # Should have section headers
        self.assertIn("Tool", output)
        self.assertIn("Session", output)
        self.assertIn("Hour", output)


class TestMainCLI(unittest.TestCase):
    """Test CLI argument parsing."""

    def test_parse_args_defaults(self):
        from hook_stats import parse_args
        args = parse_args([])
        self.assertEqual(args.recent, 10)
        self.assertIsNone(args.session)

    def test_parse_args_recent(self):
        from hook_stats import parse_args
        args = parse_args(["--recent", "20"])
        self.assertEqual(args.recent, 20)

    def test_parse_args_session(self):
        from hook_stats import parse_args
        args = parse_args(["--session", "s12345"])
        self.assertEqual(args.session, "s12345")

    def test_parse_args_combined(self):
        from hook_stats import parse_args
        args = parse_args(["--recent", "5", "--session", "s999"])
        self.assertEqual(args.recent, 5)
        self.assertEqual(args.session, "s999")


##############################################################################
# Tests for hook firing log functions (C18-6)
##############################################################################

def make_firing_entry(ts, rule_id, blocking=False, tool_name="Bash",
                      tool_input_summary="", outcome="warned"):
    """Helper to create a single hook firing log entry."""
    return json.dumps({
        "ts": ts,
        "rule_id": rule_id,
        "blocking": blocking,
        "tool_name": tool_name,
        "tool_input_summary": tool_input_summary,
        "outcome": outcome,
    })


SAMPLE_FIRINGS = "\n".join([
    make_firing_entry("2026-03-20T09:00:00.000Z", "py-edit-as-leader",
                      tool_name="Edit", tool_input_summary="/tools/foo.py"),
    make_firing_entry("2026-03-20T09:01:00.000Z", "impl-without-test",
                      blocking=True, tool_name="Edit",
                      tool_input_summary="/tools/bar.py", outcome="blocked"),
    make_firing_entry("2026-03-20T09:02:00.000Z", "py-edit-as-leader",
                      tool_name="Write", tool_input_summary="/tools/baz.py"),
    make_firing_entry("2026-03-20T10:00:00.000Z", "bash-same-cmd-loop",
                      tool_name="Bash", tool_input_summary="pytest"),
    make_firing_entry("2026-03-21T14:00:00.000Z", "agent-without-memory-search",
                      blocking=True, tool_name="Agent",
                      tool_input_summary="subagent_type=implementer",
                      outcome="blocked"),
    make_firing_entry("2026-03-21T14:01:00.000Z", "py-edit-as-leader",
                      tool_name="Edit", tool_input_summary="/tools/qux.py"),
]) + "\n"


def get_sample_firings():
    """Parse SAMPLE_FIRINGS via parse_hook_firings and return entries."""
    from hook_stats import parse_hook_firings
    p = write_temp_jsonl(SAMPLE_FIRINGS)
    try:
        return parse_hook_firings(p)
    finally:
        os.unlink(p)


class TestParseHookFirings(unittest.TestCase):
    """Test parsing of hook_firing_log.jsonl."""

    def test_parse_valid_entries(self):
        from hook_stats import parse_hook_firings
        p = write_temp_jsonl(SAMPLE_FIRINGS)
        try:
            entries = parse_hook_firings(p)
            self.assertEqual(len(entries), 6)
            self.assertEqual(entries[0]["rule_id"], "py-edit-as-leader")
            self.assertEqual(entries[1]["outcome"], "blocked")
        finally:
            os.unlink(p)

    def test_parse_empty_file(self):
        from hook_stats import parse_hook_firings
        p = write_temp_jsonl("")
        try:
            entries = parse_hook_firings(p)
            self.assertEqual(entries, [])
        finally:
            os.unlink(p)

    def test_parse_skips_invalid_json(self):
        from hook_stats import parse_hook_firings
        data = make_firing_entry("2026-03-20T09:00:00.000Z", "rule-a") + "\n"
        data += "not valid json\n"
        data += make_firing_entry("2026-03-20T09:01:00.000Z", "rule-b") + "\n"
        p = write_temp_jsonl(data)
        try:
            entries = parse_hook_firings(p)
            self.assertEqual(len(entries), 2)
        finally:
            os.unlink(p)

    def test_parse_nonexistent_file(self):
        from hook_stats import parse_hook_firings
        entries = parse_hook_firings("/nonexistent/path/file.jsonl")
        self.assertEqual(entries, [])


class TestRuleFiringStats(unittest.TestCase):
    """Test rule_firing_stats: per-rule firing counts with blocking/warning."""

    def test_basic_counts(self):
        from hook_stats import rule_firing_stats
        entries = get_sample_firings()
        stats = rule_firing_stats(entries)
        # py-edit-as-leader: 3 warned, 0 blocked
        self.assertEqual(stats["py-edit-as-leader"]["warned"], 3)
        self.assertEqual(stats["py-edit-as-leader"]["blocked"], 0)
        # impl-without-test: 0 warned, 1 blocked
        self.assertEqual(stats["impl-without-test"]["warned"], 0)
        self.assertEqual(stats["impl-without-test"]["blocked"], 1)
        # agent-without-memory-search: 0 warned, 1 blocked
        self.assertEqual(stats["agent-without-memory-search"]["blocked"], 1)

    def test_total_count(self):
        from hook_stats import rule_firing_stats
        entries = get_sample_firings()
        stats = rule_firing_stats(entries)
        # Each rule entry has total = warned + blocked
        self.assertEqual(stats["py-edit-as-leader"]["total"], 3)
        self.assertEqual(stats["bash-same-cmd-loop"]["total"], 1)

    def test_empty_entries(self):
        from hook_stats import rule_firing_stats
        stats = rule_firing_stats([])
        self.assertEqual(len(stats), 0)


class TestDailyFiringTrend(unittest.TestCase):
    """Test daily_firing_trend: firings per day."""

    def test_basic_trend(self):
        from hook_stats import daily_firing_trend
        entries = get_sample_firings()
        trend = daily_firing_trend(entries)
        # 2026-03-20: 4 firings, 2026-03-21: 2 firings
        self.assertEqual(trend["2026-03-20"], 4)
        self.assertEqual(trend["2026-03-21"], 2)

    def test_empty_entries(self):
        from hook_stats import daily_firing_trend
        trend = daily_firing_trend([])
        self.assertEqual(len(trend), 0)

    def test_single_day(self):
        from hook_stats import daily_firing_trend
        entries = [
            {"ts": "2026-03-22T01:00:00.000Z", "rule_id": "r1"},
            {"ts": "2026-03-22T23:59:00.000Z", "rule_id": "r2"},
        ]
        trend = daily_firing_trend(entries)
        self.assertEqual(len(trend), 1)
        self.assertEqual(trend["2026-03-22"], 2)


class TestZeroFireRules(unittest.TestCase):
    """Test zero_fire_rules: rules defined but never fired."""

    def test_detects_unfired_rules(self):
        from hook_stats import zero_fire_rules
        all_rule_ids = ["py-edit-as-leader", "impl-without-test", "never-fired-rule"]
        entries = get_sample_firings()
        zero = zero_fire_rules(entries, all_rule_ids)
        self.assertIn("never-fired-rule", zero)
        self.assertNotIn("py-edit-as-leader", zero)

    def test_no_firings_all_zero(self):
        from hook_stats import zero_fire_rules
        all_rule_ids = ["rule-a", "rule-b"]
        zero = zero_fire_rules([], all_rule_ids)
        self.assertEqual(set(zero), {"rule-a", "rule-b"})

    def test_all_rules_fired(self):
        from hook_stats import zero_fire_rules
        entries = get_sample_firings()
        fired_ids = list(set(e["rule_id"] for e in entries))
        zero = zero_fire_rules(entries, fired_ids)
        self.assertEqual(zero, [])

    def test_empty_rule_list(self):
        from hook_stats import zero_fire_rules
        entries = get_sample_firings()
        zero = zero_fire_rules(entries, [])
        self.assertEqual(zero, [])


class TestRotateHookFiringLog(unittest.TestCase):
    """Test log rotation: keep last N lines when exceeding limit."""

    def test_no_rotation_under_limit(self):
        from hook_stats import rotate_hook_firing_log
        # 5 lines, limit 1000 → no change
        data = "\n".join([
            make_firing_entry(f"2026-03-20T09:{i:02d}:00.000Z", "rule-a")
            for i in range(5)
        ]) + "\n"
        p = write_temp_jsonl(data)
        try:
            rotated = rotate_hook_firing_log(p, max_lines=1000)
            self.assertFalse(rotated)
            with open(p, "r", encoding="utf-8") as f:
                lines = [l for l in f.read().strip().split("\n") if l.strip()]
            self.assertEqual(len(lines), 5)
        finally:
            os.unlink(p)

    def test_rotation_over_limit(self):
        from hook_stats import rotate_hook_firing_log
        # 10 lines, limit 5 → keep last 5
        data = "\n".join([
            make_firing_entry(f"2026-03-20T09:{i:02d}:00.000Z", f"rule-{i}")
            for i in range(10)
        ]) + "\n"
        p = write_temp_jsonl(data)
        try:
            rotated = rotate_hook_firing_log(p, max_lines=5)
            self.assertTrue(rotated)
            with open(p, "r", encoding="utf-8") as f:
                lines = [l for l in f.read().strip().split("\n") if l.strip()]
            self.assertEqual(len(lines), 5)
            # Should keep the last 5 (rule-5 through rule-9)
            first = json.loads(lines[0])
            self.assertEqual(first["rule_id"], "rule-5")
        finally:
            os.unlink(p)

    def test_rotation_nonexistent_file(self):
        from hook_stats import rotate_hook_firing_log
        rotated = rotate_hook_firing_log("/nonexistent/file.jsonl", max_lines=100)
        self.assertFalse(rotated)

    def test_rotation_exactly_at_limit(self):
        from hook_stats import rotate_hook_firing_log
        # 5 lines, limit 5 → no rotation
        data = "\n".join([
            make_firing_entry(f"2026-03-20T09:{i:02d}:00.000Z", "rule-a")
            for i in range(5)
        ]) + "\n"
        p = write_temp_jsonl(data)
        try:
            rotated = rotate_hook_firing_log(p, max_lines=5)
            self.assertFalse(rotated)
        finally:
            os.unlink(p)


class TestFiringCLIArgs(unittest.TestCase):
    """Test CLI argument parsing for firing stats."""

    def test_parse_args_firings_flag(self):
        from hook_stats import parse_args
        args = parse_args(["--firings"])
        self.assertTrue(args.firings)

    def test_parse_args_rotate_flag(self):
        from hook_stats import parse_args
        args = parse_args(["--rotate"])
        self.assertTrue(args.rotate)

    def test_parse_args_firings_file(self):
        from hook_stats import parse_args
        args = parse_args(["--firings", "--firings-file", "/tmp/test.jsonl"])
        self.assertEqual(args.firings_file, "/tmp/test.jsonl")


if __name__ == "__main__":
    unittest.main()
