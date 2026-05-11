#!/usr/bin/env python3
"""Spontaneous surfacing tool: session-start memory briefing generation.

Gathers environmental signals (cwd, git state, recent files) and uses them
to retrieve relevant episodes from episode data. Produces a concise Markdown
briefing without requiring the agent to formulate a query.

Stateless: no persistent data, no configuration files, no saved state.
Read-only: does not modify episode data, topic index, or any other files.
Independent: reads data files directly, no API dependency on other memory tools.
"""

import argparse
import io
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# --- Constants ---

EPISODES_SUBDIR = "episodes"
INDEX_FILENAME = "topic_index.json"

MAX_RECENT_SESSIONS = 3
MAX_RECENT_FILES = 50
RECENT_FILE_HOURS = 24
MAX_BRIEFING_CHARS = 4000
MAX_TOPIC_RESULTS = 15
MAX_TIME_RESULTS = 10
SUMMARY_PREVIEW_LENGTH = 100

FILE_EXTENSIONS = frozenset({
    ".py", ".md", ".json", ".yaml", ".yml", ".toml", ".cfg", ".txt", ".js", ".ts",
})

SCAN_SUBDIRS = ("src", "tests", "psyche", "tools", "docs")


# --- Tag normalization (consistent with topic_index.py) ---

def _normalize_tag(tag: str) -> str:
    """Normalize a tag: lowercase, strip whitespace, unify path separators."""
    tag = tag.strip().lower()
    tag = tag.replace("\\", "/")
    return tag


# --- Environmental signal collection ---

def _collect_signals(cwd: str) -> dict:
    """Collect environmental signals from the working directory.

    Returns a dict with keys: cwd, branch, git_files, recent_files.
    """
    signals = {
        "cwd": cwd,
        "branch": None,
        "git_files": [],
        "recent_files": [],
    }

    # Git branch
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=cwd,
            encoding="utf-8", errors="replace",
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            if branch:
                signals["branch"] = branch
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass

    # Git status
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
            cwd=cwd,
            encoding="utf-8", errors="replace",
        )
        if result.returncode == 0:
            git_files = []
            for line in result.stdout.splitlines():
                # Porcelain format: XY filename
                if len(line) > 3:
                    filepath = line[3:].strip()
                    # Handle renamed files: "old -> new"
                    if " -> " in filepath:
                        filepath = filepath.split(" -> ")[-1]
                    if filepath:
                        git_files.append(filepath)
            signals["git_files"] = git_files
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass

    # Recent files
    try:
        cwd_path = Path(cwd)
        now = datetime.now(timezone.utc).timestamp()
        cutoff = now - (RECENT_FILE_HOURS * 3600)
        recent = []

        # Scan top level
        _scan_dir_for_recent(cwd_path, cwd_path, cutoff, recent)

        # Scan one level into common subdirectories
        for subdir_name in SCAN_SUBDIRS:
            subdir = cwd_path / subdir_name
            if subdir.is_dir():
                _scan_dir_for_recent(subdir, cwd_path, cutoff, recent)

        # Sort by modification time descending, cap at MAX_RECENT_FILES
        recent.sort(key=lambda x: x[1], reverse=True)
        signals["recent_files"] = [r[0] for r in recent[:MAX_RECENT_FILES]]
    except OSError:
        pass

    return signals


def _scan_dir_for_recent(
    scan_dir: Path, base_dir: Path, cutoff: float, results: list
) -> None:
    """Scan a directory (non-recursively) for recently modified files."""
    try:
        for entry in scan_dir.iterdir():
            if not entry.is_file():
                continue
            if entry.suffix.lower() not in FILE_EXTENSIONS:
                continue
            try:
                mtime = entry.stat().st_mtime
                if mtime >= cutoff:
                    rel_path = str(entry.relative_to(base_dir))
                    results.append((rel_path, mtime))
            except OSError:
                continue
    except OSError:
        pass


# --- Tag extraction from signals ---

def _extract_tags(signals: dict) -> list[str]:
    """Convert environmental signals to normalized topic tag strings."""
    tags_set = set()

    # Working directory path components: last 2-3 segments
    cwd = signals.get("cwd", "")
    if cwd:
        cwd_path = Path(cwd)
        parts = cwd_path.parts
        # Add the last component (project name)
        if parts:
            tags_set.add(_normalize_tag(parts[-1]))
        # Add last 2 components joined
        if len(parts) >= 2:
            joined = "/".join(parts[-2:])
            tags_set.add(_normalize_tag(joined))
        # Add last 3 components joined
        if len(parts) >= 3:
            joined = "/".join(parts[-3:])
            tags_set.add(_normalize_tag(joined))

    # Git branch name
    branch = signals.get("branch")
    if branch:
        tags_set.add(_normalize_tag(branch))

    # Modified file paths from git status
    for filepath in signals.get("git_files", []):
        tags_set.add(_normalize_tag(filepath))

    # Recently modified file paths
    for filepath in signals.get("recent_files", []):
        tags_set.add(_normalize_tag(filepath))

    # Remove empty tags
    tags_set.discard("")

    return list(tags_set)


# --- Session file reading (independent, same pattern as episode_recall.py) ---

def _load_session_file(filepath: Path) -> dict | None:
    """Load and parse a session JSON file. Returns None if corrupted."""
    try:
        text = filepath.read_text(encoding="utf-8")
        data = json.loads(text)
        if isinstance(data, dict) and "session_id" in data and "episodes" in data:
            return data
        return None
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None


def _list_session_files(episodes_dir: Path) -> list[Path]:
    """List all session files sorted by modification time (oldest first)."""
    if not episodes_dir.exists():
        return []
    files = sorted(
        [f for f in episodes_dir.iterdir()
         if f.is_file() and f.name.startswith("session_") and f.name.endswith(".json")],
        key=lambda f: f.stat().st_mtime,
    )
    return files


def _load_topic_index(memory_dir: str) -> dict | None:
    """Load the topic index from file. Returns None if missing or corrupted."""
    index_path = Path(memory_dir) / INDEX_FILENAME
    try:
        text = index_path.read_text(encoding="utf-8")
        data = json.loads(text)
        if isinstance(data, dict) and "index" in data:
            return data
        return None
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None


# --- Retrieval ---

def _retrieve_by_topics(
    memory_dir: str, tags: list[str]
) -> list[dict]:
    """Look up episodes by topic tags from the topic index.

    Returns a list of dicts with keys: episode_id, session_id, timestamp, match_count, matching_tags.
    """
    index_data = _load_topic_index(memory_dir)
    if index_data is None:
        return []

    index = index_data.get("index", {})
    if not index:
        return []

    # episode_id -> match info
    matches: dict[str, dict] = {}

    for tag in tags:
        normalized = _normalize_tag(tag)
        if not normalized:
            continue
        if normalized not in index:
            continue
        for ref in index[normalized]:
            ep_id = ref.get("episode_id", "")
            if not ep_id:
                continue
            if ep_id not in matches:
                matches[ep_id] = {
                    "episode_id": ep_id,
                    "session_id": ref.get("session_id", ""),
                    "timestamp": ref.get("timestamp", ""),
                    "match_count": 0,
                    "matching_tags": [],
                }
            matches[ep_id]["match_count"] += 1
            if normalized not in matches[ep_id]["matching_tags"]:
                matches[ep_id]["matching_tags"].append(normalized)

    # Sort by match count descending, then timestamp descending
    results = list(matches.values())
    results.sort(key=lambda r: (r["match_count"], r["timestamp"]), reverse=True)

    return results[:MAX_TOPIC_RESULTS]


def _retrieve_by_recency(
    memory_dir: str, n_sessions: int
) -> list[dict]:
    """Load episodes from the N most recent session files.

    Returns a list of full episode dicts.
    """
    episodes_dir = Path(memory_dir) / EPISODES_SUBDIR
    session_files = _list_session_files(episodes_dir)
    if not session_files:
        return []

    # Take the last N files (most recent by mtime)
    recent_files = session_files[-n_sessions:] if n_sessions < len(session_files) else session_files

    episodes = []
    for sf in recent_files:
        data = _load_session_file(sf)
        if data is None:
            continue
        for ep in data.get("episodes", []):
            episodes.append(ep)

    # Sort by timestamp descending (most recent first)
    episodes.sort(key=lambda ep: ep.get("timestamp", ""), reverse=True)

    return episodes[:MAX_TIME_RESULTS]


def _load_episode_by_id(
    memory_dir: str, episode_id: str, session_id: str,
    session_cache: dict | None = None,
) -> dict | None:
    """Load a specific episode by ID, looking in the specified session first.

    Args:
        memory_dir: Path to the memory directory.
        episode_id: The episode ID to find.
        session_id: The session ID to check first.
        session_cache: Optional dict mapping session_id -> session_data.
            If provided, loaded sessions are cached here to avoid redundant reads.
    """
    episodes_dir = Path(memory_dir) / EPISODES_SUBDIR

    # Try the specified session first
    if session_id:
        data = None
        if session_cache is not None and session_id in session_cache:
            data = session_cache[session_id]
        else:
            session_file = episodes_dir / (session_id + ".json")
            if session_file.exists():
                data = _load_session_file(session_file)
                if session_cache is not None:
                    session_cache[session_id] = data
        if data is not None:
            for ep in data.get("episodes", []):
                if ep.get("episode_id") == episode_id:
                    return ep

    # Fallback: search all session files
    for sf in _list_session_files(episodes_dir):
        sf_session_id = sf.stem
        if session_cache is not None and sf_session_id in session_cache:
            data = session_cache[sf_session_id]
        else:
            data = _load_session_file(sf)
            if session_cache is not None:
                session_cache[sf_session_id] = data
        if data is None:
            continue
        for ep in data.get("episodes", []):
            if ep.get("episode_id") == episode_id:
                return ep

    return None


# --- Merging ---

def _merge_results(
    memory_dir: str,
    topic_results: list[dict],
    time_results: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Merge topic-based and time-based results, removing duplicates.

    Returns (topic_episodes, time_episodes) where:
    - topic_episodes: list of (episode_dict, matching_tags) tuples
    - time_episodes: list of episode_dicts (not in topic results)
    """
    topic_episodes = []
    seen_ids = set()

    # Cache loaded session data to avoid redundant file reads when
    # multiple topic results reference the same session.
    session_cache: dict[str, dict | None] = {}

    # Process topic results first (higher priority)
    for tr in topic_results:
        ep_id = tr["episode_id"]
        ep = _load_episode_by_id(
            memory_dir, ep_id, tr.get("session_id", ""),
            session_cache=session_cache,
        )
        if ep is not None:
            topic_episodes.append((ep, tr.get("matching_tags", [])))
            seen_ids.add(ep_id)

    # Process time results, excluding duplicates
    time_episodes = []
    for ep in time_results:
        ep_id = ep.get("episode_id", "")
        if ep_id and ep_id not in seen_ids:
            time_episodes.append(ep)
            seen_ids.add(ep_id)

    return topic_episodes, time_episodes


# --- Briefing formatting ---

def _truncate_summary(summary: str, max_len: int = SUMMARY_PREVIEW_LENGTH) -> str:
    """Truncate a summary to a maximum preview length."""
    if len(summary) <= max_len:
        return summary
    return summary[:max_len] + "..."


def _format_briefing(
    cwd: str,
    branch: str | None,
    topic_episodes: list[tuple[dict, list[str]]],
    time_episodes: list[dict],
    max_chars: int = MAX_BRIEFING_CHARS,
) -> str:
    """Format the memory briefing as Markdown with size cap enforcement."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Build header
    header_parts = [f"Context: {cwd}"]
    if branch:
        header_parts.append(f"Branch: {branch}")
    header_parts.append(f"Date: {today}")

    lines = ["# Memory Briefing", "", " | ".join(header_parts), ""]

    current_size = sum(len(line) + 1 for line in lines)  # +1 for newline
    total_surfaced = 0
    omitted = 0
    cap_reached = False

    # Topic-relevant section
    if topic_episodes and not cap_reached:
        section_header = f"## Relevant to Current Context ({len(topic_episodes)} episodes)"
        section_header_size = len(section_header) + 2  # newlines
        if current_size + section_header_size < max_chars:
            lines.append(section_header)
            lines.append("")
            current_size += section_header_size

            for i, (ep, matching_tags) in enumerate(topic_episodes, 1):
                ep_type = ep.get("episode_type", "unknown")
                timestamp = ep.get("timestamp", "")
                summary = _truncate_summary(ep.get("summary", ""))
                tags_str = ", ".join(matching_tags) if matching_tags else ""

                entry_lines = [f"  {i}. [{ep_type}] {timestamp} {summary}"]
                if tags_str:
                    entry_lines.append(f"     Tags: {tags_str}")

                entry_text = "\n".join(entry_lines)
                entry_size = len(entry_text) + 2  # newlines

                if current_size + entry_size >= max_chars:
                    cap_reached = True
                    omitted += len(topic_episodes) - i + 1
                    if time_episodes:
                        omitted += len(time_episodes)
                    break

                lines.append(entry_text)
                lines.append("")
                current_size += entry_size
                total_surfaced += 1
        else:
            cap_reached = True
            omitted += len(topic_episodes)
            if time_episodes:
                omitted += len(time_episodes)

    # Recent activity section
    if time_episodes and not cap_reached:
        section_header = f"## Recent Activity ({len(time_episodes)} episodes)"
        section_header_size = len(section_header) + 2
        if current_size + section_header_size < max_chars:
            lines.append(section_header)
            lines.append("")
            current_size += section_header_size

            for i, ep in enumerate(time_episodes, 1):
                ep_type = ep.get("episode_type", "unknown")
                timestamp = ep.get("timestamp", "")
                summary = _truncate_summary(ep.get("summary", ""))

                entry_text = f"  {i}. [{ep_type}] {timestamp} {summary}"
                entry_size = len(entry_text) + 2

                if current_size + entry_size >= max_chars:
                    cap_reached = True
                    omitted += len(time_episodes) - i + 1
                    break

                lines.append(entry_text)
                lines.append("")
                current_size += entry_size
                total_surfaced += 1
        else:
            cap_reached = True
            omitted += len(time_episodes)

    # Footer
    footer = f"---\nTotal: {total_surfaced} episodes surfaced"
    if omitted > 0:
        footer += f" | {omitted} omitted"
    footer += f" | Briefing capped at {max_chars} characters"
    lines.append(footer)

    return "\n".join(lines)


# --- Main briefing function ---

def generate_briefing(
    memory_dir: str,
    cwd: str,
    max_chars: int = MAX_BRIEFING_CHARS,
    recent_sessions: int = MAX_RECENT_SESSIONS,
) -> str:
    """Generate a memory briefing for the current working context.

    Args:
        memory_dir: Path to the memory directory.
        cwd: Current working directory path.
        max_chars: Hard cap on briefing output size (characters).
        recent_sessions: Number of recent session files for time-based retrieval.

    Returns:
        A Markdown-formatted briefing string.
    """
    # Validate working directory
    if not os.path.isdir(cwd):
        return f"ERROR: Working directory does not exist: {cwd}"

    # Validate memory directory (not an error if missing, just no episodes)
    episodes_dir = Path(memory_dir) / EPISODES_SUBDIR
    if not episodes_dir.exists():
        return "No relevant memories found."

    # Collect environmental signals
    signals = _collect_signals(cwd)

    # Extract tags
    tags = _extract_tags(signals)

    # Retrieve by topics
    topic_results = _retrieve_by_topics(memory_dir, tags) if tags else []

    # Retrieve by recency
    time_results = _retrieve_by_recency(memory_dir, recent_sessions)

    # Check if we have any results
    if not topic_results and not time_results:
        return "No relevant memories found."

    # Merge and deduplicate
    topic_episodes, time_episodes = _merge_results(
        memory_dir, topic_results, time_results
    )

    # Check after merging (episodes may not load)
    if not topic_episodes and not time_episodes:
        return "No relevant memories found."

    # Format briefing
    return _format_briefing(
        cwd=cwd,
        branch=signals.get("branch"),
        topic_episodes=topic_episodes,
        time_episodes=time_episodes,
        max_chars=max_chars,
    )


# --- CLI ---

def main(argv=None):
    """Entry point for CLI usage."""
    parser = argparse.ArgumentParser(
        description="Spontaneous surfacing: session-start memory briefing generation"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # briefing
    briefing_parser = subparsers.add_parser(
        "briefing", help="Generate a memory briefing for the current working context"
    )
    briefing_parser.add_argument(
        "--memory-dir", required=True, help="Path to memory directory"
    )
    briefing_parser.add_argument(
        "--cwd", required=True, help="Current working directory path"
    )
    briefing_parser.add_argument(
        "--max-chars", type=int, default=MAX_BRIEFING_CHARS,
        help=f"Override the briefing character cap (default: {MAX_BRIEFING_CHARS})"
    )
    briefing_parser.add_argument(
        "--recent-sessions", type=int, default=MAX_RECENT_SESSIONS,
        help=f"Override the number of recent sessions to include (default: {MAX_RECENT_SESSIONS})"
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "briefing":
        result = generate_briefing(
            memory_dir=args.memory_dir,
            cwd=args.cwd,
            max_chars=args.max_chars,
            recent_sessions=args.recent_sessions,
        )
        if result.startswith("ERROR:"):
            print(result, file=sys.stderr)
            sys.exit(1)
        else:
            print(result)


if __name__ == "__main__":
    main()
