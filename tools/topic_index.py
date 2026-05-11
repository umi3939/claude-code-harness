#!/usr/bin/env python3
"""Topic index tool for building and querying reverse indexes over episode tags.

Extracts topic tags from episode data (file paths, module names, git branches)
and maintains a reverse index mapping tags to episode references.
Provides CLI for build, lookup, and list-tags operations.
"""

import argparse
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

# Ensure UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# --- Constants ---

EPISODES_SUBDIR = "episodes"
INDEX_FILENAME = "topic_index.json"

DEFAULT_LOOKUP_LIMIT = 100
DEFAULT_LIST_TAGS_LIMIT = 50

# --- Tag extraction patterns ---

# File paths: strings containing path separators with file extensions
# Matches things like psyche/emotion.py, src\utils\helper.js, ./config.yaml
_FILE_PATH_PATTERN = re.compile(
    r'(?:^|(?<=\s))([a-zA-Z0-9_.][a-zA-Z0-9_./\\-]*[/\\][a-zA-Z0-9_./\\-]*\.[a-zA-Z0-9]{1,10})(?=\s|$|[,;:\)\]\}])'
)

# Module names: Python import-style dotted names (at least one dot)
# Matches things like psyche.emotion, orchestrator.phase_engine
_MODULE_NAME_PATTERN = re.compile(
    r'(?:^|(?<=\s))([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+)(?=\s|$|[,;:\)\]\}])'
)

# Git branch names: common patterns like feature/xxx, fix/xxx, master, main
_GIT_BRANCH_PATTERN = re.compile(
    r'(?:^|(?<=\s))((?:feature|fix|hotfix|bugfix|release|chore|refactor|docs)/[a-zA-Z0-9_.-]+|master|main)(?=\s|$|[,;:\)\]\}])'
)


# --- Path helpers ---

def _get_episodes_path(memory_dir: str) -> Path:
    """Return the path to the episodes subdirectory."""
    return Path(memory_dir) / EPISODES_SUBDIR


def _get_index_path(memory_dir: str) -> Path:
    """Return the path to the topic index file."""
    return Path(memory_dir) / INDEX_FILENAME


# --- Session file reading (read-only, no dependency on episode_memory.py API) ---

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


# --- Tag normalization ---

def _normalize_tag(tag: str) -> str:
    """Normalize a tag: lowercase, strip whitespace, unify path separators."""
    tag = tag.strip().lower()
    tag = tag.replace("\\", "/")
    return tag


# --- Tag extraction ---

def _extract_tags_from_text(text: str) -> set[str]:
    """Extract tags from a text string using mechanical pattern matching."""
    tags = set()

    # File paths
    for match in _FILE_PATH_PATTERN.finditer(text):
        tags.add(_normalize_tag(match.group(1)))

    # Module names
    for match in _MODULE_NAME_PATTERN.finditer(text):
        tags.add(_normalize_tag(match.group(1)))

    # Git branch names
    for match in _GIT_BRANCH_PATTERN.finditer(text):
        tags.add(_normalize_tag(match.group(1)))

    return tags


def _extract_tags_from_episode(episode: dict) -> set[str]:
    """Extract all tags from an episode record.

    Combines existing tags field with mechanically extracted tags from
    summary and user_utterances[].text fields. Deduplicates after normalization.
    """
    tags = set()

    # Tags from the existing tags field
    for tag in episode.get("tags", []):
        normalized = _normalize_tag(tag)
        if normalized:
            tags.add(normalized)

    # Tags from summary
    summary = episode.get("summary", "")
    if summary:
        tags.update(_extract_tags_from_text(summary))

    # Tags from user utterances
    for utterance in episode.get("user_utterances", []):
        text = utterance.get("text", "")
        if text:
            tags.update(_extract_tags_from_text(text))

    return tags


# --- Index operations ---

def build_index(memory_dir: str) -> str:
    """Build or rebuild the complete topic index from all episodes.

    Scans all session files, extracts tags from all episodes,
    constructs the reverse mapping, writes the index file atomically.

    Returns a summary message, or an error message prefixed with "ERROR:".
    """
    try:
        episodes_dir = _get_episodes_path(memory_dir)
        index_path = _get_index_path(memory_dir)

        # Ensure memory dir exists
        Path(memory_dir).mkdir(parents=True, exist_ok=True)

        # Scan all session files
        session_files = _list_session_files(episodes_dir)

        # Build reverse index: tag -> list of episode references
        index: dict[str, list[dict]] = {}
        total_episodes = 0
        total_tags_extracted = 0

        for session_file in session_files:
            session_data = _load_session_file(session_file)
            if session_data is None:
                continue

            session_id = session_data.get("session_id", "")

            for episode in session_data.get("episodes", []):
                episode_id = episode.get("episode_id", "")
                timestamp = episode.get("timestamp", "")

                if not episode_id:
                    continue

                total_episodes += 1

                # Extract tags
                tags = _extract_tags_from_episode(episode)

                for tag in tags:
                    if tag not in index:
                        index[tag] = []

                    index[tag].append({
                        "episode_id": episode_id,
                        "session_id": session_id,
                        "timestamp": timestamp,
                    })
                    total_tags_extracted += 1

        # Write index atomically
        index_data = {
            "version": 1,
            "tag_count": len(index),
            "episode_count": total_episodes,
            "index": index,
        }

        _write_index_file(index_path, index_data)

        return f"Index built: {len(index)} tags from {total_episodes} episodes ({total_tags_extracted} tag-episode associations)"

    except Exception as e:
        return f"ERROR: Failed to build index: {e}"


def _write_index_file(index_path: Path, data: dict) -> None:
    """Write index data to file atomically using tempfile + os.replace."""
    index_path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(index_path.parent),
        prefix=".topic_index_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(index_path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _load_index(memory_dir: str) -> dict | None:
    """Load the topic index from file. Returns None if missing or corrupted."""
    index_path = _get_index_path(memory_dir)
    try:
        text = index_path.read_text(encoding="utf-8")
        data = json.loads(text)
        if isinstance(data, dict) and "index" in data:
            return data
        return None
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None


def lookup_by_tags(
    memory_dir: str,
    tags: list[str],
    prefix: bool = False,
    limit: int = DEFAULT_LOOKUP_LIMIT,
) -> str:
    """Look up episodes by one or more topic tags.

    Args:
        memory_dir: Path to memory directory.
        tags: List of tag strings to look up.
        prefix: If True, match tag prefixes instead of exact matches.
        limit: Maximum number of results to return.

    Returns a formatted result string, or an error message.
    """
    if not tags:
        return "ERROR: No tags provided for lookup."

    index_data = _load_index(memory_dir)
    if index_data is None:
        return "Index not found. Run 'build' to create the topic index first."

    index = index_data.get("index", {})

    # Normalize query tags
    query_tags = [_normalize_tag(t) for t in tags if _normalize_tag(t)]
    if not query_tags:
        return "ERROR: No valid tags provided after normalization."

    # Collect matching episode references
    seen_episode_ids: set[str] = set()
    results: list[dict] = []

    for query_tag in query_tags:
        if prefix:
            # Prefix match: find all index keys starting with query_tag
            matching_keys = [k for k in index if k.startswith(query_tag)]
        else:
            # Exact match
            matching_keys = [query_tag] if query_tag in index else []

        for key in matching_keys:
            for ref in index[key]:
                ep_id = ref.get("episode_id", "")
                if ep_id and ep_id not in seen_episode_ids:
                    seen_episode_ids.add(ep_id)
                    results.append(ref)

    if not results:
        tag_list = ", ".join(query_tags)
        mode = "prefix" if prefix else "exact"
        return f"No matching episodes found for tags: {tag_list} ({mode} match)"

    # Sort by timestamp descending (most recent first)
    results.sort(key=lambda r: r.get("timestamp", ""), reverse=True)

    # Apply limit
    total_matches = len(results)
    results = results[:limit]

    # Format output
    tag_list = ", ".join(query_tags)
    mode = "prefix" if prefix else "exact"
    lines = [f"Lookup results for tags: {tag_list} ({mode} match, {total_matches} total, showing {len(results)}):", ""]

    for i, ref in enumerate(results, 1):
        lines.append(
            f"  {i}. [{ref.get('episode_id', '?')}] "
            f"session={ref.get('session_id', '?')} "
            f"time={ref.get('timestamp', '?')}"
        )

    return "\n".join(lines)


def list_tags(
    memory_dir: str,
    limit: int = DEFAULT_LIST_TAGS_LIMIT,
) -> str:
    """List all known tags with their episode counts, sorted by count descending.

    Args:
        memory_dir: Path to memory directory.
        limit: Maximum number of tags to show.

    Returns a formatted tag list, or an error/info message.
    """
    index_data = _load_index(memory_dir)
    if index_data is None:
        return "Index not found. Run 'build' to create the topic index first."

    index = index_data.get("index", {})

    if not index:
        return "No tags in index. The index may be empty or needs to be rebuilt."

    # Sort by count descending, then alphabetically for ties
    tag_counts = [(tag, len(refs)) for tag, refs in index.items()]
    tag_counts.sort(key=lambda x: (-x[1], x[0]))

    total_tags = len(tag_counts)
    tag_counts = tag_counts[:limit]

    lines = [f"Tags ({total_tags} total, showing {len(tag_counts)}):", ""]
    for tag, count in tag_counts:
        lines.append(f"  {tag} ({count} episodes)")

    return "\n".join(lines)


# --- CLI ---

def main(argv=None):
    """Entry point for CLI usage."""
    parser = argparse.ArgumentParser(
        description="Topic index: build and query reverse indexes over episode tags"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # build
    build_parser = subparsers.add_parser("build", help="Build or rebuild the topic index")
    build_parser.add_argument(
        "--memory-dir", required=True, help="Path to memory directory"
    )

    # lookup
    lookup_parser = subparsers.add_parser("lookup", help="Look up episodes by topic tags")
    lookup_parser.add_argument(
        "--memory-dir", required=True, help="Path to memory directory"
    )
    lookup_parser.add_argument(
        "--tags", required=True, help="Comma-separated topic tags to look up"
    )
    lookup_parser.add_argument(
        "--prefix", action="store_true", default=False,
        help="Match tag prefixes instead of exact matches"
    )
    lookup_parser.add_argument(
        "--limit", type=int, default=DEFAULT_LOOKUP_LIMIT,
        help=f"Maximum number of results (default: {DEFAULT_LOOKUP_LIMIT})"
    )

    # list-tags
    list_parser = subparsers.add_parser("list-tags", help="List all tags with episode counts")
    list_parser.add_argument(
        "--memory-dir", required=True, help="Path to memory directory"
    )
    list_parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIST_TAGS_LIMIT,
        help=f"Maximum number of tags to show (default: {DEFAULT_LIST_TAGS_LIMIT})"
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "build":
        result = build_index(args.memory_dir)
        if result.startswith("ERROR:"):
            print(result, file=sys.stderr)
            sys.exit(1)
        else:
            print(result)

    elif args.command == "lookup":
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
        result = lookup_by_tags(
            memory_dir=args.memory_dir,
            tags=tags,
            prefix=args.prefix,
            limit=args.limit,
        )
        if result.startswith("ERROR:"):
            print(result, file=sys.stderr)
            sys.exit(1)
        else:
            print(result)

    elif args.command == "list-tags":
        result = list_tags(
            memory_dir=args.memory_dir,
            limit=args.limit,
        )
        if result.startswith("ERROR:"):
            print(result, file=sys.stderr)
            sys.exit(1)
        else:
            print(result)


if __name__ == "__main__":
    main()
