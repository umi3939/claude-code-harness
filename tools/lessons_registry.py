#!/usr/bin/env python3
"""Lessons registry tool for recording and searching operational lessons.

Records structured lesson entries as human-readable Markdown.
Provides CLI for add, list, and search operations.
"""

import argparse
import io
import os
import re
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

LESSONS_FILENAME = "lessons_registry.md"
ENTRY_SEPARATOR = "---"


# --- Data helpers ---

def _build_entry(
    action: str,
    why: str,
    fix: str,
    lesson: str,
    rule: str = "",
) -> dict:
    """Build a lesson entry dict from raw string inputs."""
    now = datetime.now()
    return {
        "date": now.strftime("%Y-%m-%d"),
        "action": action.strip(),
        "why": why.strip(),
        "fix": fix.strip(),
        "lesson": lesson.strip(),
        "rule": rule.strip(),
    }


def _entry_to_markdown(entry: dict) -> str:
    """Convert a single entry dict to a Markdown section."""
    lines = []
    lines.append(f"## Lesson: {entry['date']}")
    lines.append("")
    lines.append("### Action")
    lines.append(entry.get("action", "(none)") or "(none)")
    lines.append("")
    lines.append("### Why")
    lines.append(entry.get("why", "(none)") or "(none)")
    lines.append("")
    lines.append("### Fix")
    lines.append(entry.get("fix", "(none)") or "(none)")
    lines.append("")
    lines.append("### Lesson")
    lines.append(entry.get("lesson", "(none)") or "(none)")
    lines.append("")

    rule = entry.get("rule", "")
    if rule:
        lines.append("### Related Rule")
        lines.append(rule)
        lines.append("")

    return "\n".join(lines)


def _markdown_to_entries(text: str) -> list:
    """Parse a lessons Markdown file back into a list of entry dicts."""
    entries = []
    if not text or not text.strip():
        return entries

    sections = re.split(r"(?m)^## Lesson:", text)
    for section in sections[1:]:  # skip element before first "## Lesson:"
        section = section.strip()
        if not section:
            continue

        entry = {
            "date": "",
            "action": "",
            "why": "",
            "fix": "",
            "lesson": "",
            "rule": "",
        }

        lines = section.split("\n")
        # First line contains the date
        entry["date"] = lines[0].strip()

        current_heading = None
        content_lines = []

        for line in lines[1:]:
            stripped = line.strip()
            if stripped.startswith("### "):
                # Flush previous heading content
                if current_heading is not None:
                    _flush_heading(entry, current_heading, content_lines)
                current_heading = stripped[4:].strip()
                content_lines = []
            elif stripped == ENTRY_SEPARATOR:
                continue
            else:
                content_lines.append(line)

        # Flush last heading
        if current_heading is not None:
            _flush_heading(entry, current_heading, content_lines)

        if entry["date"]:
            entries.append(entry)

    return entries


def _flush_heading(entry: dict, heading: str, lines: list) -> None:
    """Flush accumulated lines into the appropriate entry field."""
    text = "\n".join(lines).strip()
    if not text or text == "(none)":
        return

    heading_lower = heading.lower()

    if heading_lower == "action":
        entry["action"] = text
    elif heading_lower == "why":
        entry["why"] = text
    elif heading_lower == "fix":
        entry["fix"] = text
    elif heading_lower == "lesson":
        entry["lesson"] = text
    elif heading_lower == "related rule":
        entry["rule"] = text


# --- Core functions ---

def get_lessons_path(memory_dir: str) -> Path:
    """Return the full path to the lessons file."""
    return Path(memory_dir) / LESSONS_FILENAME


def add_lesson(
    memory_dir: str,
    action: str,
    why: str,
    fix: str,
    lesson: str,
    rule: str = "",
) -> str:
    """Add a lesson entry to the registry.

    Returns the path to the saved file on success, or an error message
    prefixed with "WARNING:" on failure.
    """
    try:
        entry = _build_entry(
            action=action,
            why=why,
            fix=fix,
            lesson=lesson,
            rule=rule,
        )

        md_text = _entry_to_markdown(entry)

        lessons_path = get_lessons_path(memory_dir)

        # Load existing content
        existing_text = ""
        if lessons_path.exists():
            try:
                existing_text = lessons_path.read_text(encoding="utf-8")
            except Exception:
                existing_text = ""

        # Build full file content
        if existing_text.strip():
            full_text = existing_text.rstrip("\n") + "\n\n---\n\n" + md_text
        else:
            full_text = "# Lessons Registry\n\n" + md_text

        # Ensure directory exists
        lessons_path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write
        fd, tmp_path = tempfile.mkstemp(
            dir=str(lessons_path.parent),
            prefix=".lessons_registry_",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(full_text)
            os.replace(tmp_path, str(lessons_path))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        return str(lessons_path)

    except Exception as e:
        return f"WARNING: Failed to record lesson: {e}"


def list_lessons(memory_dir: str) -> str:
    """List all lesson entries (summary view).

    Returns a formatted list of lessons with their date and lesson text preview.
    """
    lessons_path = get_lessons_path(memory_dir)

    if not lessons_path.exists():
        return "No lesson entries found."

    try:
        text = lessons_path.read_text(encoding="utf-8")
        entries = _markdown_to_entries(text)
    except Exception:
        return "No lesson entries found."

    if not entries:
        return "No lesson entries found."

    lines = [f"Lessons registry ({len(entries)} entries):", ""]
    for i, entry in enumerate(entries, 1):
        lesson_preview = entry.get("lesson", "")[:80]
        if len(entry.get("lesson", "")) > 80:
            lesson_preview += "..."
        lines.append(f"  {i}. [{entry['date']}] {lesson_preview}")

    return "\n".join(lines)


def search_lessons(memory_dir: str, keyword: str) -> str:
    """Search lesson entries by keyword.

    Searches across all fields (action, why, fix, lesson, rule).
    Returns matching entries formatted as readable text.
    """
    lessons_path = get_lessons_path(memory_dir)

    if not lessons_path.exists():
        return "No lesson entries found."

    try:
        text = lessons_path.read_text(encoding="utf-8")
        entries = _markdown_to_entries(text)
    except Exception:
        return "No lesson entries found."

    if not entries:
        return "No lesson entries found."

    keyword_lower = keyword.lower()
    matches = []
    for entry in entries:
        searchable = " ".join([
            entry.get("action", ""),
            entry.get("why", ""),
            entry.get("fix", ""),
            entry.get("lesson", ""),
            entry.get("rule", ""),
        ]).lower()
        if keyword_lower in searchable:
            matches.append(entry)

    if not matches:
        return f"No lessons matching '{keyword}'."

    lines = [f"Search results for '{keyword}' ({len(matches)} matches):", ""]
    for entry in matches:
        lines.append(_entry_to_markdown(entry))
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


# --- CLI ---

def main(argv=None):
    """Entry point for CLI usage."""
    parser = argparse.ArgumentParser(
        description="Lessons registry: record and search operational lessons"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # add
    add_parser = subparsers.add_parser("add", help="Add a lesson entry")
    add_parser.add_argument(
        "--memory-dir", required=True, help="Path to memory directory"
    )
    add_parser.add_argument(
        "--action", required=True, help="What was done (specific action)"
    )
    add_parser.add_argument(
        "--why", required=True, help="Why it was a problem"
    )
    add_parser.add_argument(
        "--fix", required=True, help="How it was fixed"
    )
    add_parser.add_argument(
        "--lesson", required=True, help="Generalized lesson"
    )
    add_parser.add_argument(
        "--rule", default="", help="Related Key Rule reference (optional)"
    )

    # list
    list_parser = subparsers.add_parser("list", help="List all lesson entries")
    list_parser.add_argument(
        "--memory-dir", required=True, help="Path to memory directory"
    )

    # search
    search_parser = subparsers.add_parser("search", help="Search lessons by keyword")
    search_parser.add_argument(
        "--memory-dir", required=True, help="Path to memory directory"
    )
    search_parser.add_argument(
        "--keyword", required=True, help="Keyword to search for"
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "add":
        result = add_lesson(
            memory_dir=args.memory_dir,
            action=args.action,
            why=args.why,
            fix=args.fix,
            lesson=args.lesson,
            rule=args.rule,
        )
        if result.startswith("WARNING:"):
            print(result, file=sys.stderr)
        else:
            print(f"Lesson recorded: {result}")

    elif args.command == "list":
        print(list_lessons(args.memory_dir))

    elif args.command == "search":
        print(search_lessons(args.memory_dir, args.keyword))


if __name__ == "__main__":
    main()
