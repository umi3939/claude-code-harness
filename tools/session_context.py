#!/usr/bin/env python3
"""Session work context save/restore tool.

Saves and restores session work context as structured Markdown files.
Provides CLI for both interactive and non-interactive use.
"""

import argparse
import io
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# Ensure UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# --- Constants ---

DEFAULT_HISTORY_LIMIT = 5
MAX_RECORD_BYTES = 50_000  # 50 KB per record
CONTEXT_FILENAME = "session_context.md"


# --- Data helpers ---

def _build_record(
    summary: str,
    completed: str = "",
    pending: str = "",
    decisions: str = "",
    issues: str = "",
    next_actions: str = "",
) -> dict:
    """Build a context record dict from raw string inputs."""
    now = datetime.now()
    return {
        "session_datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary.strip(),
        "completed": [t.strip() for t in completed.split(",") if t.strip()] if completed else [],
        "pending": [t.strip() for t in pending.split(",") if t.strip()] if pending else [],
        "decisions": decisions.strip(),
        "issues": issues.strip(),
        "next_actions": next_actions.strip(),
    }


def _record_to_markdown(record: dict) -> str:
    """Convert a single record dict to a Markdown section."""
    lines = []
    lines.append(f"## Session: {record['session_datetime']}")
    lines.append("")
    lines.append("### Summary")
    lines.append(record.get("summary", "(none)") or "(none)")
    lines.append("")

    completed = record.get("completed", [])
    if completed:
        lines.append("### Completed Tasks")
        for task in completed:
            lines.append(f"- [x] {task}")
        lines.append("")

    pending = record.get("pending", [])
    if pending:
        lines.append("### Pending Tasks")
        for task in pending:
            lines.append(f"- [ ] {task}")
        lines.append("")

    decisions = record.get("decisions", "")
    if decisions:
        lines.append("### Decisions")
        lines.append(decisions)
        lines.append("")

    issues = record.get("issues", "")
    if issues:
        lines.append("### Issues / Blockers")
        lines.append(issues)
        lines.append("")

    next_actions = record.get("next_actions", "")
    if next_actions:
        lines.append("### Next Session")
        lines.append(next_actions)
        lines.append("")

    return "\n".join(lines)


def _markdown_to_records(text: str) -> list:
    """Parse a context Markdown file back into a list of record dicts."""
    records = []
    if not text or not text.strip():
        return records

    # Split on lines that start with "## Session:" to avoid matching the
    # same substring embedded in user-provided content (markdown injection).
    import re
    sections = re.split(r"(?m)^## Session:", text)
    for section in sections[1:]:  # skip element before first "## Session:"
        section = section.strip()
        if not section:
            continue

        record = {
            "session_datetime": "",
            "summary": "",
            "completed": [],
            "pending": [],
            "decisions": "",
            "issues": "",
            "next_actions": "",
        }

        lines = section.split("\n")
        # First line contains the datetime
        record["session_datetime"] = lines[0].strip()

        current_heading = None
        content_lines = []

        for line in lines[1:]:
            stripped = line.strip()
            if stripped.startswith("### "):
                # Flush previous heading content
                if current_heading is not None:
                    _flush_heading(record, current_heading, content_lines)
                current_heading = stripped[4:].strip()
                content_lines = []
            elif stripped == "---":
                # Skip separators between records
                continue
            else:
                content_lines.append(line)

        # Flush last heading
        if current_heading is not None:
            _flush_heading(record, current_heading, content_lines)

        if record["session_datetime"]:
            records.append(record)

    return records


def _flush_heading(record: dict, heading: str, lines: list) -> None:
    """Flush accumulated lines into the appropriate record field."""
    text = "\n".join(lines).strip()
    if not text or text == "(none)":
        return

    heading_lower = heading.lower()

    if heading_lower == "summary":
        record["summary"] = text
    elif heading_lower == "completed tasks":
        record["completed"] = _parse_task_list(text)
    elif heading_lower == "pending tasks":
        record["pending"] = _parse_task_list(text)
    elif heading_lower == "decisions":
        record["decisions"] = text
    elif heading_lower.startswith("issues"):
        record["issues"] = text
    elif heading_lower.startswith("next"):
        record["next_actions"] = text


def _parse_task_list(text: str) -> list:
    """Parse Markdown checkbox list into plain task strings."""
    tasks = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("- [x] "):
            tasks.append(line[6:])
        elif line.startswith("- [ ] "):
            tasks.append(line[6:])
        elif line.startswith("- "):
            tasks.append(line[2:])
        elif line:
            tasks.append(line)
    return tasks


# --- Core functions ---

def get_context_path(memory_dir: str) -> Path:
    """Return the full path to the context file."""
    return Path(memory_dir) / CONTEXT_FILENAME


def save_context(
    memory_dir: str,
    summary: str,
    completed: str = "",
    pending: str = "",
    decisions: str = "",
    issues: str = "",
    next_actions: str = "",
    history_limit: int = DEFAULT_HISTORY_LIMIT,
) -> str:
    """Save a session context record.

    Returns the path to the saved file on success, or an error message
    prefixed with "ERROR:" on failure.
    """
    try:
        record = _build_record(
            summary=summary,
            completed=completed,
            pending=pending,
            decisions=decisions,
            issues=issues,
            next_actions=next_actions,
        )

        md_text = _record_to_markdown(record)

        # Check single-record size limit
        if len(md_text.encode("utf-8")) > MAX_RECORD_BYTES:
            return f"ERROR: Record size ({len(md_text.encode('utf-8'))} bytes) exceeds limit ({MAX_RECORD_BYTES} bytes). Please shorten the content."

        context_path = get_context_path(memory_dir)

        # Load existing records
        existing_records = []
        if context_path.exists():
            try:
                existing_text = context_path.read_text(encoding="utf-8")
                existing_records = _markdown_to_records(existing_text)
            except Exception:
                # Corrupted file -- start fresh
                existing_records = []

        # Append new record
        existing_records.append(record)

        # FIFO trim
        if len(existing_records) > history_limit:
            existing_records = existing_records[-history_limit:]

        # Write full file
        header = "# Session Context History\n\n"
        body = "\n---\n\n".join(
            _record_to_markdown(r) for r in existing_records
        )
        full_text = header + body

        # Ensure directory exists
        context_path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write: write to a temp file in the same directory, then
        # rename.  os.replace is atomic on both POSIX and Windows (NTFS).
        fd, tmp_path = tempfile.mkstemp(
            dir=str(context_path.parent),
            prefix=".session_context_",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(full_text)
            os.replace(tmp_path, str(context_path))
        except BaseException:
            # Clean up the temp file on any failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        return str(context_path)

    except Exception as e:
        return f"ERROR: Failed to save context: {e}"


def load_context(memory_dir: str) -> str:
    """Load and return the latest session context as readable text.

    Returns the formatted context string, or a message indicating no
    context is available.
    """
    context_path = get_context_path(memory_dir)

    if not context_path.exists():
        return "No session context found."

    try:
        text = context_path.read_text(encoding="utf-8")
        records = _markdown_to_records(text)
    except Exception:
        return "No session context found (file unreadable)."

    if not records:
        return "No session context found."

    latest = records[-1]
    return _record_to_markdown(latest)


def list_contexts(memory_dir: str) -> str:
    """List all saved session contexts (summary view).

    Returns a formatted list of sessions with their datetime and summary
    first line.
    """
    context_path = get_context_path(memory_dir)

    if not context_path.exists():
        return "No session context history."

    try:
        text = context_path.read_text(encoding="utf-8")
        records = _markdown_to_records(text)
    except Exception:
        return "No session context history (file unreadable)."

    if not records:
        return "No session context history."

    lines = [f"Session context history ({len(records)} records):", ""]
    for i, record in enumerate(records, 1):
        summary_preview = record.get("summary", "")[:80]
        if len(record.get("summary", "")) > 80:
            summary_preview += "..."
        lines.append(f"  {i}. [{record['session_datetime']}] {summary_preview}")

    return "\n".join(lines)


# --- Interactive input helpers ---

def _input_multiline(prompt: str) -> str:
    """Read multiline input until an empty line is entered."""
    print(prompt)
    print("  (Enter an empty line to finish)")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "":
            break
        lines.append(line)
    return "\n".join(lines)


def _interactive_save(memory_dir: str) -> None:
    """Run interactive save mode, prompting the user for each field."""
    print("=== Save Session Context ===\n")

    summary = _input_multiline("Work summary:")
    if not summary:
        print("Summary is required. Aborting.")
        sys.exit(1)

    completed = input("Completed tasks (comma-separated, or empty): ").strip()
    pending = input("Pending tasks (comma-separated, or empty): ").strip()
    decisions = _input_multiline("Important decisions made:")
    issues = _input_multiline("Issues / blockers:")
    next_actions = _input_multiline("Next session actions:")

    result = save_context(
        memory_dir=memory_dir,
        summary=summary,
        completed=completed,
        pending=pending,
        decisions=decisions,
        issues=issues,
        next_actions=next_actions,
    )

    if result.startswith("ERROR:"):
        print(result, file=sys.stderr)
        sys.exit(1)
    else:
        print(f"\nContext saved to: {result}")


# --- CLI ---

def main(argv=None):
    """Entry point for CLI usage."""
    parser = argparse.ArgumentParser(
        description="Session work context save/restore tool"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # save
    save_parser = subparsers.add_parser("save", help="Save session context")
    save_parser.add_argument(
        "--memory-dir", required=True, help="Path to memory directory"
    )
    save_parser.add_argument("--summary", default=None, help="Work summary")
    save_parser.add_argument(
        "--completed", default="", help="Completed tasks (comma-separated)"
    )
    save_parser.add_argument(
        "--pending", default="", help="Pending tasks (comma-separated)"
    )
    save_parser.add_argument("--decisions", default="", help="Decisions made")
    save_parser.add_argument("--issues", default="", help="Issues / blockers")
    save_parser.add_argument(
        "--next", default="", help="Next session actions", dest="next_actions"
    )
    save_parser.add_argument(
        "--history-limit",
        type=int,
        default=DEFAULT_HISTORY_LIMIT,
        help=f"Max history records to keep (default: {DEFAULT_HISTORY_LIMIT})",
    )

    # load
    load_parser = subparsers.add_parser("load", help="Load latest session context")
    load_parser.add_argument(
        "--memory-dir", required=True, help="Path to memory directory"
    )

    # list
    list_parser = subparsers.add_parser("list", help="List session context history")
    list_parser.add_argument(
        "--memory-dir", required=True, help="Path to memory directory"
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "save":
        if args.summary is None:
            # Interactive mode
            _interactive_save(args.memory_dir)
        else:
            # Non-interactive mode
            result = save_context(
                memory_dir=args.memory_dir,
                summary=args.summary,
                completed=args.completed,
                pending=args.pending,
                decisions=args.decisions,
                issues=args.issues,
                next_actions=args.next_actions,
                history_limit=args.history_limit,
            )
            if result.startswith("ERROR:"):
                print(result, file=sys.stderr)
                sys.exit(1)
            else:
                print(f"Context saved to: {result}")

    elif args.command == "load":
        print(load_context(args.memory_dir))

    elif args.command == "list":
        print(list_contexts(args.memory_dir))


if __name__ == "__main__":
    main()
