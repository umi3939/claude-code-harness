"""Write-Time violation collector.

Parses structured JSON output from ruff and bandit,
converts to JSONL records, and appends to data/write_time_violations.jsonl.

This module is called from hooks/ruff-quality-gate.sh.
It is a pure data collection layer — no judgment, evaluation, or action.
"""

import json
import logging
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# --- Constants (static configuration, not dynamically changeable) ---
FIFO_MAX_LINES = 1000
PER_RUN_MAX_RECORDS = 50


def parse_ruff_json(raw_output):
    """Parse ruff --output-format json output into violation records.

    Args:
        raw_output: Raw string from ruff JSON output.

    Returns:
        List of violation record dicts. Empty list on any parse failure.
    """
    if not raw_output:
        return []
    try:
        data = json.loads(raw_output)
    except (json.JSONDecodeError, TypeError):
        return []

    if not isinstance(data, list):
        return []

    now = datetime.now(timezone.utc).isoformat()
    records = []
    for item in data:
        if not isinstance(item, dict):
            continue
        location = item.get("location", {}) or {}
        records.append({
            "timestamp": now,
            "filepath": item.get("filename", ""),
            "row": location.get("row", 0),
            "col": location.get("column", 0),
            "rule_code": item.get("code", ""),
            "severity": "",
            "message": item.get("message", ""),
            "tool": "ruff",
        })
    return records


def parse_bandit_json(raw_output):
    """Parse bandit -f json output into violation records.

    Args:
        raw_output: Raw string from bandit JSON output.

    Returns:
        List of violation record dicts. Empty list on any parse failure.
    """
    if not raw_output:
        return []
    try:
        data = json.loads(raw_output)
    except (json.JSONDecodeError, TypeError):
        return []

    if not isinstance(data, dict):
        return []

    results = data.get("results", [])
    if not isinstance(results, list):
        return []

    now = datetime.now(timezone.utc).isoformat()
    records = []
    for item in results:
        if not isinstance(item, dict):
            continue
        records.append({
            "timestamp": now,
            "filepath": item.get("filename", ""),
            "row": item.get("line_number", 0),
            "col": item.get("col_offset", 0),
            "rule_code": item.get("test_id", ""),
            "severity": item.get("issue_severity", ""),
            "message": item.get("issue_text", ""),
            "tool": "bandit",
        })
    return records


def append_violations(records, outfile):
    """Append violation records to JSONL file.

    Enforces per-run write limit (PER_RUN_MAX_RECORDS).
    Creates parent directory if needed.

    Args:
        records: List of violation record dicts.
        outfile: Path to JSONL output file.

    Returns:
        Number of records actually written.
    """
    if not records:
        return 0

    # Per-run limit: take first N only
    to_write = records[:PER_RUN_MAX_RECORDS]

    # Ensure parent directory exists
    parent_dir = os.path.dirname(outfile)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    with open(outfile, "a", encoding="utf-8") as f:
        for record in to_write:
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            f.write(line + "\n")

    return len(to_write)


def enforce_fifo_limit(outfile, max_lines=FIFO_MAX_LINES):
    """Trim JSONL file to max_lines, removing oldest entries.

    Uses temp file + rename for safe atomic replacement.

    Args:
        outfile: Path to JSONL file.
        max_lines: Maximum number of lines to keep.
    """
    if not os.path.exists(outfile):
        return

    with open(outfile, "r", encoding="utf-8") as f:
        lines = f.readlines()

    if len(lines) <= max_lines:
        return

    # Keep the newest (last) max_lines entries
    trimmed = lines[-max_lines:]

    # Write to temp file in same directory, then rename (atomic on same filesystem)
    parent_dir = os.path.dirname(outfile)
    fd, tmp_path = tempfile.mkstemp(dir=parent_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_f:
            tmp_f.writelines(trimmed)
        # On Windows, must remove target first if it exists
        if os.path.exists(outfile):
            os.replace(tmp_path, outfile)
        else:
            os.rename(tmp_path, outfile)
    except Exception:
        logger.exception("Failed to enforce FIFO limit on %s", outfile)
        # Clean up temp file if rename failed
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def main():
    """CLI entry point called from ruff-quality-gate.sh.

    Usage:
        python violation_collector.py <tool> <jsonl_outfile> <<< '<json_output>'

    Reads JSON from stdin, parses it, appends to outfile, enforces FIFO.
    Always exits 0 (collection failure must not block the quality gate).
    """
    try:
        if len(sys.argv) < 3:
            sys.exit(0)

        tool = sys.argv[1]  # "ruff" or "bandit"
        outfile = sys.argv[2]

        raw_input = sys.stdin.read()

        if tool == "ruff":
            records = parse_ruff_json(raw_input)
        elif tool == "bandit":
            records = parse_bandit_json(raw_input)
        else:
            sys.exit(0)

        if records:
            append_violations(records, outfile)
            enforce_fifo_limit(outfile)

    except Exception:
        # Collection failure must never block the quality gate
        logger.debug("Collection failed", exc_info=True)

    sys.exit(0)


if __name__ == "__main__":
    main()
