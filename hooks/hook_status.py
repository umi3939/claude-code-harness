"""Hook Status CLI — read-only inspection of hook system data.

Subcommands:
  list   Show all rules with status
  info   Show details for a specific rule
  log    Show recent firing log entries
  check  Validate rules file structure

This module is read-only: it never writes to rules, logs, or state files.
"""

import argparse
import json
import os
import re
import sys

# --- Constants ---
DEFAULT_LOG_LIMIT = 50
RULE_ID_PATTERN = r"^[a-z0-9_-]+$"
REQUIRED_RULE_FIELDS = [
    "id", "description", "type", "trigger", "message", "severity", "blocking",
]

# --- Default file paths (relative to project root) ---
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
DEFAULT_RULES_PATH = os.path.join(_SCRIPT_DIR, "behavior-rules.json")
DEFAULT_LOG_PATH = os.path.join(_PROJECT_ROOT, "data", "hook_firing_log.jsonl")
DEFAULT_STATE_PATH = os.path.join(_SCRIPT_DIR, ".behavior-guard-state.json")

# --- Eligibility support (G37) ---
_ELIGIBILITY_FILE = os.path.join(_SCRIPT_DIR, "hook-eligibility.json")

def _load_eligibility():
    """Load eligibility config. Fail-open: returns empty dict on error."""
    try:
        from hook_eligibility import load_eligibility_config, check_all_eligibility
        config = load_eligibility_config(_ELIGIBILITY_FILE)
        return check_all_eligibility(config)
    except Exception:
        return {}


# --- Data loading functions ---


def load_rules(path):
    """Load and parse behavior-rules.json.

    Returns:
        (version, rules_list) tuple. On failure: (None, []).
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Warning: Rules file not found: {path}", file=sys.stderr)
        return None, []
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error: Failed to parse rules file: {e}", file=sys.stderr)
        return None, []

    if not isinstance(data, dict):
        print("Error: Rules file is not a JSON object", file=sys.stderr)
        return None, []

    version = data.get("version")
    rules = data.get("rules", [])
    if not isinstance(rules, list):
        rules = []
    return version, rules


def load_firing_log(path):
    """Load and parse hook_firing_log.jsonl.

    Returns:
        List of entry dicts. Malformed lines are skipped.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw_lines = f.readlines()
    except FileNotFoundError:
        print(f"Warning: Firing log not found: {path}", file=sys.stderr)
        return []
    except OSError as e:
        print(f"Error: Failed to read firing log: {e}", file=sys.stderr)
        return []

    entries = []
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            entries.append(entry)
        except json.JSONDecodeError:
            # Skip malformed lines
            continue
    return entries


def load_state(path):
    """Load and parse .behavior-guard-state.json.

    Returns:
        Dict. On failure: empty dict (fallback for concurrent access).
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Warning: State file not found: {path}", file=sys.stderr)
        return {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"Warning: Failed to parse state file (concurrent access?): {e}", file=sys.stderr)
        return {}

    if not isinstance(data, dict):
        return {}
    return data


# --- Input validation ---


def _validate_rule_id(rule_id):
    """Validate rule_id against allowed pattern.

    Returns:
        True if valid, False otherwise (prints error to stderr).
    """
    if not rule_id or not re.match(RULE_ID_PATTERN, rule_id):
        print(f"Error: Invalid rule ID '{rule_id}'. Must match {RULE_ID_PATTERN}", file=sys.stderr)
        return False
    return True


# --- Subcommands ---


def cmd_list(rules_path):
    """List all hook rules with status."""
    version, rules = load_rules(rules_path)

    if not rules:
        print("No rules found.")
        return

    if version and version != 2:
        print(f"Warning: Expected rules version 2, got {version}", file=sys.stderr)

    # Header
    # G37: Load eligibility
    eligibility = _load_eligibility()

    print(f"{'ID':<40} {'Type':<12} {'Blocking':<10} {'Disabled':<10} {'Eligible':<10} {'Description'}")
    print("-" * 130)

    for rule in rules:
        rule_id = rule.get("id", "???")
        rule_type = rule.get("type", "???")
        blocking = "Yes" if rule.get("blocking") else "No"
        disabled = "Yes" if rule.get("disabled") else "No"
        # G37: Eligibility status
        elig_result = eligibility.get(rule_id)
        eligible = "N/A" if elig_result is None else ("Yes" if elig_result.eligible else "No")
        desc = rule.get("description", "")
        # Truncate long descriptions
        if len(desc) > 45:
            desc = desc[:42] + "..."
        print(f"{rule_id:<40} {rule_type:<12} {blocking:<10} {disabled:<10} {eligible:<10} {desc}")

    print(f"\nTotal: {len(rules)} rules")


def cmd_info(rule_id, rules_path, log_path, state_path):
    """Show detailed info for a specific rule."""
    if not _validate_rule_id(rule_id):
        return

    version, rules = load_rules(rules_path)
    rule = None
    for r in rules:
        if r.get("id") == rule_id:
            rule = r
            break

    if rule is None:
        print(f"Error: Rule '{rule_id}' not found.", file=sys.stderr)
        return

    # Rule details
    print(f"=== Rule: {rule_id} ===")
    print(f"  Description : {rule.get('description', '')}")
    print(f"  Type        : {rule.get('type', '')}")
    print(f"  Severity    : {rule.get('severity', '')}")
    print(f"  Blocking    : {rule.get('blocking', False)}")
    print(f"  Disabled    : {rule.get('disabled', False)}")
    if rule.get("_disabled_reason"):
        print(f"  Disabled Why: {rule['_disabled_reason']}")
    print(f"  Lesson      : {rule.get('lesson', '')}")
    print(f"  Confidence  : {rule.get('confidence', '')}")
    print(f"  Domain      : {rule.get('domain', '')}")
    print(f"  Message     : {rule.get('message', '')}")

    # G37: Eligibility status
    eligibility = _load_eligibility()
    elig_result = eligibility.get(rule_id)
    if elig_result is not None:
        print(f"  Eligible    : {'Yes' if elig_result.eligible else 'No'}")
        if not elig_result.eligible:
            print(f"  Elig Detail : {elig_result.summary()}")
    else:
        print(f"  Eligible    : N/A (no eligibility config)")

    # Trigger
    trigger = rule.get("trigger", {})
    print(f"  Trigger:")
    for k, v in trigger.items():
        print(f"    {k}: {v}")

    # Evidence
    evidence = rule.get("evidence", [])
    if evidence:
        print(f"  Evidence:")
        for e in evidence:
            print(f"    - {e}")

    # Firing history from log
    log_entries = load_firing_log(log_path)
    rule_entries = [e for e in log_entries if e.get("rule_id") == rule_id]
    print(f"\n  Recent Firings: {len(rule_entries)}")
    for entry in rule_entries[-5:]:  # Show last 5
        ts = entry.get("ts", "???")
        outcome = entry.get("outcome", "???")
        tool = entry.get("tool_name", "???")
        print(f"    [{ts}] {outcome} (tool: {tool})")

    # Block count from state
    state = load_state(state_path)
    block_counts = state.get("_block_counts", {})
    bc = block_counts.get(rule_id, {})
    if bc:
        print(f"\n  Block Count: {bc.get('count', 0)} (first at: {bc.get('first_at', '???')})")


def cmd_log(log_path, rule_id=None, outcome=None, limit=None):
    """Show recent firing log entries."""
    # Validate rule_id filter if provided
    if rule_id is not None and not _validate_rule_id(rule_id):
        return

    if limit is None:
        limit = DEFAULT_LOG_LIMIT

    entries = load_firing_log(log_path)

    if not entries:
        print("No firing log entries found.")
        return

    # Apply filters
    if rule_id:
        entries = [e for e in entries if e.get("rule_id") == rule_id]
    if outcome:
        entries = [e for e in entries if e.get("outcome") == outcome]

    # Apply limit (0 means all)
    if limit > 0:
        entries = entries[-limit:]

    if not entries:
        print("No matching entries found.")
        return

    # Print header
    print(f"{'Timestamp':<28} {'Rule ID':<35} {'Outcome':<10} {'Tool':<15} {'Count'}")
    print("-" * 100)

    for entry in entries:
        ts = entry.get("ts", "???")
        rid = entry.get("rule_id", "???")
        out = entry.get("outcome", "???")
        tool = entry.get("tool_name", "???")
        count = entry.get("count", "?")
        esc = " [ESCALATED]" if entry.get("escalated") else ""
        print(f"{ts:<28} {rid:<35} {out:<10} {tool:<15} {count}{esc}")

    print(f"\nShowing {len(entries)} entries")


def cmd_check(rules_path):
    """Validate rules file structural integrity."""
    # Check file existence
    if not os.path.exists(rules_path):
        print(f"Error: Rules file not found: {rules_path}", file=sys.stderr)
        print("FAIL: File not found")
        return

    version, rules = load_rules(rules_path)

    if version is None:
        print("FAIL: Could not parse rules file")
        return

    issues = []

    # Version check
    if version != 2:
        issues.append(f"WARNING: Expected version 2, got {version}")

    # Check each rule for required fields
    for i, rule in enumerate(rules):
        rule_id = rule.get("id", f"<rule index {i}>")
        missing = [f for f in REQUIRED_RULE_FIELDS if f not in rule]
        if missing:
            issues.append(f"Rule '{rule_id}': missing required fields: {', '.join(missing)}")

    # Disabled rules listing
    disabled_rules = [r for r in rules if r.get("disabled")]

    # Output
    if issues:
        print("=== Issues Found ===")
        for issue in issues:
            print(f"  - {issue}")
    else:
        print("=== Validation OK ===")
        print(f"  All {len(rules)} rules pass structural check")

    if disabled_rules:
        print(f"\n=== Disabled Rules ({len(disabled_rules)}) ===")
        for r in disabled_rules:
            reason = r.get("_disabled_reason", "no reason given")
            print(f"  - {r.get('id', '???')}: {reason}")

    print(f"\nTotal: {len(rules)} rules, {len(issues)} issues, {len(disabled_rules)} disabled")


# --- CLI entry point ---


def main(argv=None):
    """CLI entry point. Returns exit code."""
    parser = argparse.ArgumentParser(
        prog="hook_status",
        description="Hook system status viewer (read-only)",
    )
    subparsers = parser.add_subparsers(dest="command")

    # list
    p_list = subparsers.add_parser("list", help="List all hook rules")
    p_list.add_argument("--rules", default=DEFAULT_RULES_PATH, help="Path to rules file")

    # info
    p_info = subparsers.add_parser("info", help="Show details for a specific rule")
    p_info.add_argument("rule_id", help="Rule ID to inspect")
    p_info.add_argument("--rules", default=DEFAULT_RULES_PATH, help="Path to rules file")
    p_info.add_argument("--log", default=DEFAULT_LOG_PATH, help="Path to firing log")
    p_info.add_argument("--state", default=DEFAULT_STATE_PATH, help="Path to state file")

    # log
    p_log = subparsers.add_parser("log", help="Show recent firing log entries")
    p_log.add_argument("--log", default=DEFAULT_LOG_PATH, help="Path to firing log")
    p_log.add_argument("--rule-id", default=None, help="Filter by rule ID")
    p_log.add_argument("--outcome", default=None, help="Filter by outcome (blocked/passed)")
    p_log.add_argument("--limit", type=int, default=DEFAULT_LOG_LIMIT, help="Max entries to show")
    p_log.add_argument("--all", action="store_true", help="Show all entries (ignore limit)")

    # check
    p_check = subparsers.add_parser("check", help="Validate rules file")
    p_check.add_argument("--rules", default=DEFAULT_RULES_PATH, help="Path to rules file")

    if argv is not None:
        args = parser.parse_args(argv)
    else:
        args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    if args.command == "list":
        cmd_list(args.rules)
    elif args.command == "info":
        cmd_info(args.rule_id, args.rules, args.log, args.state)
    elif args.command == "log":
        effective_limit = 0 if args.all else args.limit
        cmd_log(args.log, rule_id=args.rule_id, outcome=args.outcome, limit=effective_limit)
    elif args.command == "check":
        cmd_check(args.rules)

    return 0


if __name__ == "__main__":
    sys.exit(main())
