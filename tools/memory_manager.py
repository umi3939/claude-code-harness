#!/usr/bin/env python3
"""Memory manager: unified wrapper for the 5 episode memory tools.

Provides five subcommands that orchestrate the underlying tools:
  startup  -- compress old sessions, rebuild index, generate briefing, verify
  record   -- record an episode and rebuild the topic index
  maintain -- compress sessions, rebuild index, show status
  search   -- unified search across keyword, context, and time pathways
  verify   -- verify answers to dynamic read verification questions

Each step catches errors independently so partial failures do not block
subsequent steps.
"""

import argparse
import io
import sys
import traceback

# Ensure UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# --- Imports from existing tools ---
# These are direct Python function imports (no subprocess).

from episode_memory import record_episode
from topic_index import build_index
from episode_recall import keyword_search, time_range_search, context_search
from staged_compression import compress_sessions, get_compression_status
from spontaneous_surfacing import generate_briefing
from dynamic_read_verification import run_verification, run_verify


# --- Step runner ---

def _run_step(step_name: str, func, *args, **kwargs) -> tuple[bool, str]:
    """Run a single step, catching all exceptions.

    Returns (success, result_string).
    """
    try:
        result = func(*args, **kwargs)
        if isinstance(result, str) and result.startswith("ERROR:"):
            return False, f"[{step_name}] {result}"
        return True, result if isinstance(result, str) else str(result)
    except Exception:
        tb = traceback.format_exc()
        return False, f"[{step_name}] Exception:\n{tb}"


# --- Subcommand implementations ---

def cmd_startup(args) -> int:
    """Startup: compress -> rebuild index -> generate briefing."""
    errors = []

    # Step 1: Compress old sessions
    ok, result = _run_step(
        "compress", compress_sessions, memory_dir=args.memory_dir
    )
    if not ok:
        errors.append(result)
        print(result, file=sys.stderr)
    else:
        print(result)

    # Step 2: Rebuild topic index
    ok, result = _run_step(
        "build_index", build_index, memory_dir=args.memory_dir
    )
    if not ok:
        errors.append(result)
        print(result, file=sys.stderr)
    else:
        print(result)

    # Step 3: Generate briefing
    briefing_kwargs = {
        "memory_dir": args.memory_dir,
        "cwd": args.cwd,
    }
    if args.max_chars is not None:
        briefing_kwargs["max_chars"] = args.max_chars

    ok, result = _run_step(
        "briefing", generate_briefing, **briefing_kwargs
    )
    if not ok:
        errors.append(result)
        print(result, file=sys.stderr)
    else:
        print(result)

    # Step 4: Dynamic read verification
    if not getattr(args, "skip_verify", False):
        verify_kwargs = {
            "cwd": args.cwd,
            "memory_dir": args.memory_dir,
        }
        verify_files = getattr(args, "verify_files", None)
        if verify_files:
            verify_kwargs["verify_files"] = verify_files

        ok, result = _run_step(
            "verification", run_verification, **verify_kwargs
        )
        if not ok:
            errors.append(result)
            print(result, file=sys.stderr)
        else:
            print(result)

    # Step 5: Lesson reminder (random lesson from registry)
    try:
        _show_lesson_reminder(args.memory_dir)
    except Exception:
        pass  # Non-critical, never block startup

    return 1 if errors else 0


def _show_lesson_reminder(memory_dir: str):
    """Display a random lesson from lessons_registry.md as a session reminder."""
    import os
    import random

    lessons_file = os.path.join(memory_dir, "lessons_registry.md")
    if not os.path.exists(lessons_file):
        return

    with open(lessons_file, "r", encoding="utf-8") as f:
        content = f.read()

    sections = content.split("## Lesson:")
    if len(sections) < 2:
        return

    lessons = []
    for sec in sections[1:]:
        lines = sec.strip().split("\n")
        lesson_text = ""
        related_rule = ""
        for j, line in enumerate(lines):
            if line.startswith("### Lesson"):
                lesson_text = lines[j + 1].strip() if j + 1 < len(lines) else ""
            elif line.startswith("### Related Rule"):
                related_rule = lines[j + 1].strip() if j + 1 < len(lines) else ""
        if lesson_text:
            lessons.append({"lesson": lesson_text, "rule": related_rule})

    if not lessons:
        return

    # Pick 2 random lessons (or fewer if not enough)
    sample = random.sample(lessons, min(2, len(lessons)))
    print("\n=== Lesson Reminder ===")
    for i, l in enumerate(sample, 1):
        print(f"  {i}. {l['lesson']}")
        if l['rule']:
            print(f"     → {l['rule']}")
    print()


def cmd_record(args) -> int:
    """Record an episode and rebuild the topic index."""
    errors = []

    # Parse tags
    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []

    # Step 1: Record episode
    ok, result = _run_step(
        "record",
        record_episode,
        memory_dir=args.memory_dir,
        episode_type=args.episode_type,
        summary=args.summary,
        user_texts=args.user_text,
        tags=tags,
        session_id=args.session_id,
    )
    if not ok:
        errors.append(result)
        print(result, file=sys.stderr)
    else:
        print(result)

    # Step 2: Rebuild topic index
    ok, result = _run_step(
        "build_index", build_index, memory_dir=args.memory_dir
    )
    if not ok:
        errors.append(result)
        print(result, file=sys.stderr)
    else:
        print(result)

    return 1 if errors else 0


def cmd_maintain(args) -> int:
    """Maintain: compress -> rebuild index -> show status."""
    errors = []

    # Step 1: Compress
    ok, result = _run_step(
        "compress", compress_sessions, memory_dir=args.memory_dir
    )
    if not ok:
        errors.append(result)
        print(result, file=sys.stderr)
    else:
        print(result)

    # Step 2: Rebuild index
    ok, result = _run_step(
        "build_index", build_index, memory_dir=args.memory_dir
    )
    if not ok:
        errors.append(result)
        print(result, file=sys.stderr)
    else:
        print(result)

    # Step 3: Status
    ok, result = _run_step(
        "status", get_compression_status, memory_dir=args.memory_dir
    )
    if not ok:
        errors.append(result)
        print(result, file=sys.stderr)
    else:
        print(result)

    return 1 if errors else 0


def cmd_search(args) -> int:
    """Search: dispatch to the appropriate recall pathway(s)."""
    has_keywords = bool(args.keywords)
    has_tags = bool(args.tags)
    has_last = bool(args.last)

    if not has_keywords and not has_tags and not has_last:
        print("ERROR: At least one of --keywords, --tags, or --last is required.", file=sys.stderr)
        return 1

    limit = args.limit if args.limit else 50

    errors = []
    any_output = False

    # Keyword search
    if has_keywords:
        kw_list = [k.strip() for k in args.keywords.split(",") if k.strip()]
        ok, result = _run_step(
            "keyword_search",
            keyword_search,
            memory_dir=args.memory_dir,
            keywords=kw_list,
            limit=limit,
        )
        if not ok:
            errors.append(result)
            print(result, file=sys.stderr)
        else:
            print(result)
            any_output = True

    # Context search (by tags)
    if has_tags:
        tag_list = [t.strip() for t in args.tags.split(",") if t.strip()]
        ok, result = _run_step(
            "context_search",
            context_search,
            memory_dir=args.memory_dir,
            tags=tag_list,
            limit=limit,
        )
        if not ok:
            errors.append(result)
            print(result, file=sys.stderr)
        else:
            if any_output:
                print()  # separator
            print(result)
            any_output = True

    # Time search
    if has_last:
        ok, result = _run_step(
            "time_search",
            time_range_search,
            memory_dir=args.memory_dir,
            last=args.last,
            limit=limit,
        )
        if not ok:
            errors.append(result)
            print(result, file=sys.stderr)
        else:
            if any_output:
                print()  # separator
            print(result)
            any_output = True

    return 1 if errors and not any_output else 0


def cmd_verify(args) -> int:
    """Verify: check answers against expected values from dynamic verification."""
    if not args.answers:
        print("ERROR: --answers is required.", file=sys.stderr)
        return 1

    ok, result = _run_step(
        "verify", run_verify,
        memory_dir=args.memory_dir,
        answers_str=args.answers,
    )
    if not ok:
        print(result, file=sys.stderr)
        return 1
    else:
        print(result)
        return 0


# --- CLI ---

def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser. Separated for testability."""
    parser = argparse.ArgumentParser(
        description="Memory manager: unified wrapper for episode memory tools"
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to run")

    # startup
    startup_parser = subparsers.add_parser(
        "startup", help="Session startup: compress, index, briefing"
    )
    startup_parser.add_argument(
        "--memory-dir", required=True, help="Path to memory directory"
    )
    startup_parser.add_argument(
        "--cwd", required=True, help="Current working directory"
    )
    startup_parser.add_argument(
        "--max-chars", type=int, default=None,
        help="Override briefing character cap"
    )
    startup_parser.add_argument(
        "--verify-files", default=None,
        help="Comma-separated paths to verification target files (overrides defaults)"
    )
    startup_parser.add_argument(
        "--skip-verify", action="store_true", default=False,
        help="Skip dynamic read verification (debug use)"
    )

    # record
    record_parser = subparsers.add_parser(
        "record", help="Record an episode and rebuild index"
    )
    record_parser.add_argument(
        "--memory-dir", required=True, help="Path to memory directory"
    )
    record_parser.add_argument(
        "--type", required=True, dest="episode_type",
        help="Episode type"
    )
    record_parser.add_argument(
        "--summary", required=True, help="Episode summary"
    )
    record_parser.add_argument(
        "--tags", default="", help="Comma-separated tags"
    )
    record_parser.add_argument(
        "--user-text", action="append", default=None,
        help="Verbatim user utterance (repeatable)"
    )
    record_parser.add_argument(
        "--session-id", default=None, help="Explicit session ID"
    )

    # maintain
    maintain_parser = subparsers.add_parser(
        "maintain", help="Maintenance: compress, index, status"
    )
    maintain_parser.add_argument(
        "--memory-dir", required=True, help="Path to memory directory"
    )

    # search
    search_parser = subparsers.add_parser(
        "search", help="Search episodes (keyword, context, time)"
    )
    search_parser.add_argument(
        "--memory-dir", required=True, help="Path to memory directory"
    )
    search_parser.add_argument(
        "--keywords", default=None,
        help="Comma-separated keywords for full-text search"
    )
    search_parser.add_argument(
        "--tags", default=None,
        help="Comma-separated tags for context search"
    )
    search_parser.add_argument(
        "--last", default=None,
        help="Relative time range (e.g. '7d', '24h')"
    )
    search_parser.add_argument(
        "--limit", type=int, default=None,
        help="Maximum results per search pathway"
    )

    # verify
    verify_parser = subparsers.add_parser(
        "verify", help="Verify answers to dynamic read verification questions"
    )
    verify_parser.add_argument(
        "--memory-dir", required=True, help="Path to memory directory"
    )
    verify_parser.add_argument(
        "--answers", required=True,
        help='Category A answers in format "Q1:answer1,Q3:answer3,..."'
    )

    return parser


def main(argv=None):
    """Entry point for CLI usage."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    dispatch = {
        "startup": cmd_startup,
        "record": cmd_record,
        "maintain": cmd_maintain,
        "search": cmd_search,
        "verify": cmd_verify,
    }

    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    exit_code = handler(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
