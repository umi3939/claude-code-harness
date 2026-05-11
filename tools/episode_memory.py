#!/usr/bin/env python3
"""Episode memory tool for recording event-level detail within sessions.

Records structured episode entries as JSON files organized by session.
Preserves verbatim user utterances as first-class data within each episode.
Provides CLI for record, list-sessions, list-episodes, and show operations.
"""

import argparse
import io
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Ensure UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# --- Constants ---

EPISODES_SUBDIR = "episodes"

# Episode type enumeration
EPISODE_TYPES = frozenset({
    "user_request",
    "decision",
    "error",
    "solution",
    "feedback",
    "observation",
})

# Size and retention constraints
DEFAULT_UTTERANCE_SIZE_CAP = 2000  # bytes per utterance text
DEFAULT_UTTERANCES_PER_EPISODE = 10  # max utterance entries per episode
DEFAULT_EPISODES_PER_SESSION = 100  # max episodes per session file
DEFAULT_TOTAL_SESSIONS = 50  # max session files
DEFAULT_SESSION_AGE_HOURS = 4  # hours before auto-creating a new session


# --- ID generation ---

def _generate_episode_id() -> str:
    """Generate a unique episode ID (12 hex chars from random UUID)."""
    return uuid.uuid4().hex[:12]


# --- Path helpers ---

def get_episodes_path(memory_dir: str) -> Path:
    """Return the path to the episodes subdirectory."""
    return Path(memory_dir) / EPISODES_SUBDIR


def _ensure_episodes_dir(memory_dir: str) -> Path:
    """Ensure the episodes subdirectory exists and return its path."""
    episodes_dir = get_episodes_path(memory_dir)
    episodes_dir.mkdir(parents=True, exist_ok=True)
    return episodes_dir


# --- Session file helpers ---

def _session_id_from_timestamp(dt: datetime) -> str:
    """Generate a session ID from a datetime."""
    return "session_" + dt.strftime("%Y%m%d_%H%M%S")


def _session_filename(session_id: str) -> str:
    """Return the filename for a session ID."""
    return session_id + ".json"


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


def _write_session_file(filepath: Path, data: dict) -> None:
    """Write session data to file atomically."""
    filepath.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(filepath.parent),
        prefix=".episode_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(filepath))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


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


def _get_latest_session_file(episodes_dir: Path) -> Path | None:
    """Return the most recently modified session file, or None."""
    files = _list_session_files(episodes_dir)
    return files[-1] if files else None


def _enforce_total_session_cap(episodes_dir: Path, total_sessions: int) -> None:
    """Delete oldest session files if total exceeds the cap."""
    files = _list_session_files(episodes_dir)
    while len(files) > total_sessions:
        oldest = files.pop(0)
        try:
            oldest.unlink()
        except OSError:
            pass


# --- Utterance helpers ---

def _build_utterance(text: str, size_cap: int = DEFAULT_UTTERANCE_SIZE_CAP) -> dict:
    """Build a user utterance object with size cap enforcement."""
    text_bytes = text.encode("utf-8")
    truncated = len(text_bytes) > size_cap
    if truncated:
        # Truncate at byte boundary, decode safely
        truncated_bytes = text_bytes[:size_cap]
        text = truncated_bytes.decode("utf-8", errors="ignore")
    return {
        "text": text,
        "role": "user",
        "truncated": truncated,
    }


# --- Core functions ---

def record_episode(
    memory_dir: str,
    episode_type: str,
    summary: str,
    user_texts: list[str] | None = None,
    tags: list[str] | None = None,
    session_id: str | None = None,
    utterance_size_cap: int = DEFAULT_UTTERANCE_SIZE_CAP,
    utterances_per_episode: int = DEFAULT_UTTERANCES_PER_EPISODE,
    episodes_per_session: int = DEFAULT_EPISODES_PER_SESSION,
    total_sessions: int = DEFAULT_TOTAL_SESSIONS,
    session_age_hours: int = DEFAULT_SESSION_AGE_HOURS,
) -> str:
    """Record a new episode in the current session.

    Returns a success message with the episode ID, or an error message
    prefixed with "ERROR:" on failure.
    """
    try:
        # Validate episode type
        if episode_type not in EPISODE_TYPES:
            return f"ERROR: Invalid episode type '{episode_type}'. Must be one of: {', '.join(sorted(EPISODE_TYPES))}"

        # Validate summary
        if not summary or not summary.strip():
            return "ERROR: Summary is required and cannot be empty."

        episodes_dir = _ensure_episodes_dir(memory_dir)

        now = datetime.now(timezone.utc)

        # Build utterances
        utterances = []
        if user_texts:
            for text in user_texts[:utterances_per_episode]:
                utterances.append(_build_utterance(text, utterance_size_cap))

        # Build episode record
        episode_id = _generate_episode_id()
        episode = {
            "episode_id": episode_id,
            "episode_type": episode_type,
            "summary": summary.strip(),
            "user_utterances": utterances,
            "tags": [t.strip() for t in (tags or []) if t.strip()],
            "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session_id": "",  # filled below
        }

        # Determine session file
        if session_id:
            # Explicit session ID
            session_file = episodes_dir / _session_filename(session_id)
            session_data = _load_session_file(session_file)
            if session_data is None:
                # Create new session with the given ID
                session_data = {
                    "session_id": session_id,
                    "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "episodes": [],
                }
        else:
            # Auto-detect: use latest session or create new
            latest_file = _get_latest_session_file(episodes_dir)
            session_data = None

            if latest_file is not None:
                session_data = _load_session_file(latest_file)
                if session_data is not None:
                    # Check age threshold
                    try:
                        created_at = datetime.strptime(
                            session_data["created_at"], "%Y-%m-%dT%H:%M:%SZ"
                        ).replace(tzinfo=timezone.utc)
                        age_hours = (now - created_at).total_seconds() / 3600
                        if age_hours > session_age_hours:
                            session_data = None  # Too old, create new
                    except (ValueError, KeyError):
                        session_data = None  # Unparseable date treated as too old

            if session_data is None:
                # Create new session
                new_session_id = _session_id_from_timestamp(now)
                session_data = {
                    "session_id": new_session_id,
                    "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "episodes": [],
                }

        # Set the episode's session_id
        episode["session_id"] = session_data["session_id"]

        # Append episode
        session_data["episodes"].append(episode)

        # Enforce per-session episode cap (FIFO)
        if len(session_data["episodes"]) > episodes_per_session:
            session_data["episodes"] = session_data["episodes"][-episodes_per_session:]

        # Write session file
        session_file = episodes_dir / _session_filename(session_data["session_id"])
        _write_session_file(session_file, session_data)

        # Enforce total session cap
        _enforce_total_session_cap(episodes_dir, total_sessions)

        return f"Episode recorded: {episode_id} in {session_data['session_id']}"

    except Exception as e:
        return f"ERROR: Failed to record episode: {e}"


def list_sessions(memory_dir: str) -> str:
    """List all sessions with episode counts.

    Returns a formatted summary of all session files.
    """
    episodes_dir = get_episodes_path(memory_dir)

    if not episodes_dir.exists():
        return "No episodes found."

    files = _list_session_files(episodes_dir)
    if not files:
        return "No episodes found."

    lines = [f"Episode sessions ({len(files)} sessions):", ""]
    for i, filepath in enumerate(files, 1):
        data = _load_session_file(filepath)
        if data is None:
            lines.append(f"  {i}. {filepath.stem} [corrupted]")
            continue

        episode_count = len(data.get("episodes", []))
        created_at = data.get("created_at", "unknown")
        lines.append(f"  {i}. {data['session_id']} (created: {created_at}, episodes: {episode_count})")

    return "\n".join(lines)


def list_episodes(memory_dir: str, session_id: str | None = None) -> str:
    """List episodes in a specific session (or the latest).

    Returns a formatted summary of episodes in the session.
    """
    episodes_dir = get_episodes_path(memory_dir)

    if not episodes_dir.exists():
        return "No episodes found."

    if session_id:
        session_file = episodes_dir / _session_filename(session_id)
        data = _load_session_file(session_file)
        if data is None:
            return f"ERROR: Session '{session_id}' not found or corrupted."
    else:
        latest_file = _get_latest_session_file(episodes_dir)
        if latest_file is None:
            return "No episodes found."
        data = _load_session_file(latest_file)
        if data is None:
            return "No episodes found (latest session file corrupted)."

    episodes = data.get("episodes", [])
    if not episodes:
        return f"No episodes in session {data['session_id']}."

    lines = [f"Episodes in {data['session_id']} ({len(episodes)} episodes):", ""]
    for i, ep in enumerate(episodes, 1):
        summary_preview = ep.get("summary", "")[:80]
        if len(ep.get("summary", "")) > 80:
            summary_preview += "..."
        ep_type = ep.get("episode_type", "unknown")
        timestamp = ep.get("timestamp", "")
        ep_id = ep.get("episode_id", "unknown")
        lines.append(f"  {i}. [{ep_type}] {ep_id} ({timestamp}) {summary_preview}")

    return "\n".join(lines)


def show_episode(memory_dir: str, episode_id: str) -> str:
    """Show full details of a specific episode by ID.

    Searches across all session files. Returns formatted episode detail
    or an error message.
    """
    episodes_dir = get_episodes_path(memory_dir)

    if not episodes_dir.exists():
        return f"ERROR: Episode '{episode_id}' not found."

    files = _list_session_files(episodes_dir)

    for filepath in files:
        data = _load_session_file(filepath)
        if data is None:
            continue
        for ep in data.get("episodes", []):
            if ep.get("episode_id") == episode_id:
                return _format_episode_detail(ep)

    return f"ERROR: Episode '{episode_id}' not found."


def _format_episode_detail(episode: dict) -> str:
    """Format a single episode as detailed readable text."""
    lines = []
    lines.append(f"Episode: {episode.get('episode_id', 'unknown')}")
    lines.append(f"  Type: {episode.get('episode_type', 'unknown')}")
    lines.append(f"  Session: {episode.get('session_id', 'unknown')}")
    lines.append(f"  Timestamp: {episode.get('timestamp', 'unknown')}")
    lines.append(f"  Summary: {episode.get('summary', '(none)')}")

    tags = episode.get("tags", [])
    if tags:
        lines.append(f"  Tags: {', '.join(tags)}")

    utterances = episode.get("user_utterances", [])
    if utterances:
        lines.append(f"  User utterances ({len(utterances)}):")
        for j, utt in enumerate(utterances, 1):
            truncated_mark = " [truncated]" if utt.get("truncated") else ""
            lines.append(f"    {j}. {utt.get('text', '')}{truncated_mark}")

    return "\n".join(lines)


# --- Recall count (C22-B) ---

def increment_episode_recall_counts(
    memory_dir: str,
    episode_session_map: dict[str, str],
) -> int:
    """Increment recall_count for episodes found in search results.

    Args:
        memory_dir: Path to memory directory (unused but kept for API consistency).
        episode_session_map: {episode_id: session_file_path} mapping.

    Returns:
        Number of episodes updated.
    """
    import logging
    logger = logging.getLogger(__name__)

    # Group by session file for batch writes
    session_episodes: dict[str, list[str]] = {}
    for ep_id, sess_path in episode_session_map.items():
        session_episodes.setdefault(sess_path, []).append(ep_id)

    updated_count = 0
    for sess_path, ep_ids in session_episodes.items():
        filepath = Path(sess_path)
        session_data = _load_session_file(filepath)
        if session_data is None:
            logger.warning("Cannot load session file for recall increment: %s", sess_path)
            continue

        target_set = set(ep_ids)
        changed = False
        for ep in session_data.get("episodes", []):
            if ep.get("episode_id") in target_set:
                recall_count = ep.get("recall_count", 0)
                if not isinstance(recall_count, int) or recall_count < 0:
                    recall_count = 0
                ep["recall_count"] = recall_count + 1
                updated_count += 1
                changed = True

        if changed:
            try:
                _write_session_file(filepath, session_data)
            except Exception as e:
                logger.error("Failed to write recall counts to %s: %s", sess_path, e)

    return updated_count


# --- CLI ---

def main(argv=None):
    """Entry point for CLI usage."""
    parser = argparse.ArgumentParser(
        description="Episode memory: record event-level detail within sessions"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # record
    record_parser = subparsers.add_parser("record", help="Record a new episode")
    record_parser.add_argument(
        "--memory-dir", required=True, help="Path to memory directory"
    )
    record_parser.add_argument(
        "--type", required=True, dest="episode_type",
        choices=sorted(EPISODE_TYPES),
        help="Episode type"
    )
    record_parser.add_argument(
        "--summary", required=True, help="Brief description of the episode"
    )
    record_parser.add_argument(
        "--tags", default="", help="Comma-separated topic tags"
    )
    record_parser.add_argument(
        "--user-text", action="append", default=None,
        help="Verbatim user utterance text (repeatable)"
    )
    record_parser.add_argument(
        "--session-id", default=None,
        help="Explicit session ID (auto-detects if omitted)"
    )

    # list-sessions
    ls_parser = subparsers.add_parser("list-sessions", help="List all sessions")
    ls_parser.add_argument(
        "--memory-dir", required=True, help="Path to memory directory"
    )

    # list-episodes
    le_parser = subparsers.add_parser("list-episodes", help="List episodes in a session")
    le_parser.add_argument(
        "--memory-dir", required=True, help="Path to memory directory"
    )
    le_parser.add_argument(
        "--session-id", default=None, help="Session ID (latest if omitted)"
    )

    # show
    show_parser = subparsers.add_parser("show", help="Show episode details")
    show_parser.add_argument(
        "--memory-dir", required=True, help="Path to memory directory"
    )
    show_parser.add_argument(
        "--episode-id", required=True, help="Episode ID to show"
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "record":
        tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
        result = record_episode(
            memory_dir=args.memory_dir,
            episode_type=args.episode_type,
            summary=args.summary,
            user_texts=args.user_text,
            tags=tags,
            session_id=args.session_id,
        )
        if result.startswith("ERROR:"):
            print(result, file=sys.stderr)
            sys.exit(1)
        else:
            print(result)

    elif args.command == "list-sessions":
        print(list_sessions(args.memory_dir))

    elif args.command == "list-episodes":
        result = list_episodes(args.memory_dir, args.session_id)
        if result.startswith("ERROR:"):
            print(result, file=sys.stderr)
            sys.exit(1)
        else:
            print(result)

    elif args.command == "show":
        result = show_episode(args.memory_dir, args.episode_id)
        if result.startswith("ERROR:"):
            print(result, file=sys.stderr)
            sys.exit(1)
        else:
            print(result)


if __name__ == "__main__":
    main()
