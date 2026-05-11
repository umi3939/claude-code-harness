#!/usr/bin/env python3
"""Staged compression tool for episode memory sessions.

Provides 4-stage progressive compression (full -> condensed -> summary -> skeleton)
based on session age. Episodes are never deleted -- only compressed to reduce detail.

Compression stages:
  0 (full):      All fields at original detail
  1 (condensed): Utterances truncated to shorter cap
  2 (summary):   Only first utterance (truncated), summary truncated
  3 (skeleton):  No utterances, summary reduced to single-line prefix
"""

import argparse
import io
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# --- Constants ---

EPISODES_SUBDIR = "episodes"
COMPRESSION_STATE_FILE = "compression_state.json"

STAGE_LABELS = {0: "full", 1: "condensed", 2: "summary", 3: "skeleton"}

# Age thresholds (days) for stage transitions
THRESHOLD_STAGE_1_DAYS = 7
THRESHOLD_STAGE_2_DAYS = 30
THRESHOLD_STAGE_3_DAYS = 90

# Number of most recent sessions exempt from compression
PROTECTED_RECENT_SESSIONS = 3

# Truncation caps
CONDENSED_UTTERANCE_CAP = 500   # bytes per utterance at stage 1
SUMMARY_UTTERANCE_CAP = 200     # bytes for single retained utterance at stage 2
SUMMARY_TEXT_CAP = 200           # chars for summary text at stage 2
SKELETON_SUMMARY_CAP = 80       # chars for summary text at stage 3

# Safety cap: max session files (compress to skeleton, never delete)
MAX_TOTAL_SESSIONS = 500


# --- Path helpers ---

def _get_episodes_dir(memory_dir: str) -> Path:
    """Return the path to the episodes subdirectory."""
    return Path(memory_dir) / EPISODES_SUBDIR


def _get_state_file_path(memory_dir: str) -> Path:
    """Return the path to the compression state file."""
    return Path(memory_dir) / COMPRESSION_STATE_FILE


# --- File I/O helpers ---

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


def _write_file_atomic(filepath: Path, data: dict) -> None:
    """Write JSON data to file atomically using tempfile + os.replace."""
    filepath.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(filepath.parent),
        prefix=".compress_",
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


# M-P1: TTL cache for directory listing to avoid full scan every cycle
_session_files_cache: dict = {"path": None, "files": [], "expires": 0.0}
_SESSION_FILES_CACHE_TTL = 30.0  # seconds


def _list_session_files(episodes_dir: Path) -> list[Path]:
    """List all session files sorted by modification time (oldest first).

    Results are cached with a short TTL to avoid repeated full directory scans.
    """
    if not episodes_dir.exists():
        return []

    now = time.monotonic()
    cache = _session_files_cache
    if cache["path"] == str(episodes_dir) and now < cache["expires"]:
        return cache["files"]

    files = sorted(
        [f for f in episodes_dir.iterdir()
         if f.is_file() and f.name.startswith("session_") and f.name.endswith(".json")],
        key=lambda f: f.stat().st_mtime,
    )

    cache["path"] = str(episodes_dir)
    cache["files"] = files
    cache["expires"] = now + _SESSION_FILES_CACHE_TTL
    return files


# --- Compression state ---

def _load_compression_state(memory_dir: str) -> dict:
    """Load the compression state file. Returns empty state if missing/corrupted."""
    state_path = _get_state_file_path(memory_dir)
    try:
        text = state_path.read_text(encoding="utf-8")
        data = json.loads(text)
        if isinstance(data, dict) and "sessions" in data:
            return data
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        pass
    return {"last_run": None, "sessions": {}}


def _save_compression_state(memory_dir: str, state: dict) -> None:
    """Save the compression state file atomically."""
    state_path = _get_state_file_path(memory_dir)
    _write_file_atomic(state_path, state)


# --- Timestamp helpers ---

def _parse_timestamp(ts_str: str) -> datetime | None:
    """Parse an ISO timestamp string to a UTC datetime. Returns None on failure."""
    try:
        dt = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ")
        return dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _get_latest_episode_timestamp(session_data: dict) -> datetime | None:
    """Get the timestamp of the most recent episode in a session.

    Returns None if no episodes have valid timestamps.
    """
    latest = None
    for ep in session_data.get("episodes", []):
        ts = _parse_timestamp(ep.get("timestamp", ""))
        if ts is not None:
            if latest is None or ts > latest:
                latest = ts
    return latest


# --- Stage determination ---

def determine_target_stage(
    latest_episode_ts: datetime | None,
    now: datetime,
    is_protected: bool = False,
    threshold_stage_1_days: int = THRESHOLD_STAGE_1_DAYS,
    threshold_stage_2_days: int = THRESHOLD_STAGE_2_DAYS,
    threshold_stage_3_days: int = THRESHOLD_STAGE_3_DAYS,
) -> int:
    """Determine the target compression stage for a session.

    Args:
        latest_episode_ts: Timestamp of the session's most recent episode.
            None means no valid timestamps (treated as maximally old).
        now: Current time.
        is_protected: If True, session is exempt from compression (returns 0).
        threshold_stage_1_days: Days for stage 0->1 transition.
        threshold_stage_2_days: Days for stage 1->2 transition.
        threshold_stage_3_days: Days for stage 2->3 transition.

    Returns:
        Target compression stage (0, 1, 2, or 3).
    """
    if is_protected:
        return 0

    if latest_episode_ts is None:
        # No valid timestamps -- treat as maximally old
        return 3

    age_days = (now - latest_episode_ts).total_seconds() / 86400

    if age_days >= threshold_stage_3_days:
        return 3
    elif age_days >= threshold_stage_2_days:
        return 2
    elif age_days >= threshold_stage_1_days:
        return 1
    else:
        return 0


# --- Insight extraction (C22-D) ---

# Patterns with colon (Japanese and English labeled patterns)
_INSIGHT_PATTERN_COLON = re.compile(
    r"(?:理由|なぜ|教訓|lesson|判断|決定|重要|気づき|発見)\s*[:：]",
    re.IGNORECASE,
)
# Patterns that work inline without colon
_INSIGHT_PATTERN_INLINE = re.compile(
    r"\bbecause\b",
    re.IGNORECASE,
)

INSIGHTS_MAX_TOTAL_CHARS = 500


def extract_insights(episode: dict) -> list[str]:
    """Extract insight strings from an episode's utterances and summary.

    Searches for patterns indicating judgments, lessons, surprises, and reasons.
    Returns a list of matched lines, capped at INSIGHTS_MAX_TOTAL_CHARS total.

    Args:
        episode: Episode record dict.

    Returns:
        List of insight strings (may be empty).
    """
    sources: list[str] = []

    # Collect text from utterances first
    for utt in episode.get("user_utterances", []):
        text = utt.get("text", "")
        if text:
            sources.append(text)

    # If no utterances, fall back to summary
    if not sources:
        summary = episode.get("summary", "")
        if summary:
            sources.append(summary)

    insights: list[str] = []
    total_chars = 0

    for text in sources:
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            if _INSIGHT_PATTERN_COLON.search(line) or _INSIGHT_PATTERN_INLINE.search(line):
                remaining = INSIGHTS_MAX_TOTAL_CHARS - total_chars
                if remaining <= 0:
                    break
                truncated_line = line[:remaining]
                insights.append(truncated_line)
                total_chars += len(truncated_line)
        if total_chars >= INSIGHTS_MAX_TOTAL_CHARS:
            break

    return insights


# --- Episode compression ---

def _truncate_utf8(text: str, byte_cap: int) -> tuple[str, bool]:
    """Truncate text to fit within a UTF-8 byte cap without splitting codepoints.

    Returns (truncated_text, was_truncated).
    """
    text_bytes = text.encode("utf-8")
    if len(text_bytes) <= byte_cap:
        return text, False
    truncated_bytes = text_bytes[:byte_cap]
    truncated_text = truncated_bytes.decode("utf-8", errors="ignore")
    return truncated_text, True


def _truncate_chars(text: str, char_cap: int) -> str:
    """Truncate text to a character cap."""
    if len(text) <= char_cap:
        return text
    return text[:char_cap]


def compress_episode(episode: dict, target_stage: int) -> dict:
    """Compress a single episode record to a target stage.

    This is a pure function (no file I/O). Returns a new dict with compression
    applied. If the episode is already at or beyond the target stage, returns
    it unchanged.

    Args:
        episode: Episode record dict.
        target_stage: Target compression stage (0, 1, 2, or 3).

    Returns:
        Compressed episode dict (new copy).
    """
    current_stage = episode.get("compression_stage", 0)

    if current_stage >= target_stage:
        # Already at or beyond target stage -- no-op
        return dict(episode)

    result = dict(episode)

    # Extract insights before first compression (stage 0 -> 1+)
    # Only extract if insights field does not already exist
    if current_stage == 0 and target_stage >= 1 and "insights" not in result:
        result["insights"] = extract_insights(result)

    # Apply stage transitions sequentially
    if current_stage < 1 and target_stage >= 1:
        result = _apply_stage_1(result)

    if current_stage < 2 and target_stage >= 2:
        result = _apply_stage_2(result)

    if current_stage < 3 and target_stage >= 3:
        result = _apply_stage_3(result)

    return result


def _apply_stage_1(episode: dict) -> dict:
    """Apply stage 1 (condensed) compression.

    Truncate each user utterance to CONDENSED_UTTERANCE_CAP bytes.
    """
    result = dict(episode)
    utterances = result.get("user_utterances", [])
    new_utterances = []
    for utt in utterances:
        new_utt = dict(utt)
        text = new_utt.get("text", "")
        truncated_text, was_truncated = _truncate_utf8(text, CONDENSED_UTTERANCE_CAP)
        new_utt["text"] = truncated_text
        if was_truncated:
            new_utt["truncated"] = True
        new_utterances.append(new_utt)
    result["user_utterances"] = new_utterances
    result["compression_stage"] = 1
    return result


def _apply_stage_2(episode: dict) -> dict:
    """Apply stage 2 (summary) compression.

    Keep only first utterance (truncated to SUMMARY_UTTERANCE_CAP).
    Truncate summary to SUMMARY_TEXT_CAP chars.
    """
    result = dict(episode)

    # Reduce utterances to first only
    utterances = result.get("user_utterances", [])
    if utterances:
        first_utt = dict(utterances[0])
        text = first_utt.get("text", "")
        truncated_text, was_truncated = _truncate_utf8(text, SUMMARY_UTTERANCE_CAP)
        first_utt["text"] = truncated_text
        if was_truncated:
            first_utt["truncated"] = True
        result["user_utterances"] = [first_utt]
    else:
        result["user_utterances"] = []

    # Truncate summary
    summary = result.get("summary", "")
    result["summary"] = _truncate_chars(summary, SUMMARY_TEXT_CAP)

    result["compression_stage"] = 2
    return result


def _apply_stage_3(episode: dict) -> dict:
    """Apply stage 3 (skeleton) compression.

    Remove all utterances. Truncate summary to SKELETON_SUMMARY_CAP chars.
    """
    result = dict(episode)

    # Remove all utterances
    result["user_utterances"] = []

    # Truncate summary
    summary = result.get("summary", "")
    result["summary"] = _truncate_chars(summary, SKELETON_SUMMARY_CAP)

    result["compression_stage"] = 3
    return result


# --- Recall delay (C22-B) ---

def apply_recall_delay(target_stage: int, recall_count: int) -> int:
    """Compute delayed target stage based on recall_count.

    recall_count >= 5: delay 2 stages
    recall_count >= 3: delay 1 stage
    Result is clamped to 0 minimum.

    Pure function, no side effects.
    """
    if recall_count >= 5:
        delay = 2
    elif recall_count >= 3:
        delay = 1
    else:
        delay = 0
    return max(0, target_stage - delay)


# --- Session-level compression ---

def _compress_session_data(
    session_data: dict,
    target_stage: int,
    force: bool = False,
) -> tuple[dict, int]:
    """Compress all episodes in a session to the target stage.

    Args:
        session_data: Session dict with episodes list.
        target_stage: Target compression stage.
        force: If True, skip recall delay (used for force_skeleton overflow).

    Returns (modified_session_data, count_of_episodes_changed).
    """
    episodes = session_data.get("episodes", [])
    new_episodes = []
    changed_count = 0

    for ep in episodes:
        current_stage = ep.get("compression_stage", 0)
        effective_target = target_stage
        if not force:
            recall_count = ep.get("recall_count", 0)
            if not isinstance(recall_count, int) or recall_count < 0:
                recall_count = 0
            effective_target = apply_recall_delay(target_stage, recall_count)
        compressed = compress_episode(ep, effective_target)
        if compressed.get("compression_stage", 0) != current_stage:
            changed_count += 1
        new_episodes.append(compressed)

    result = dict(session_data)
    result["episodes"] = new_episodes
    return result, changed_count


# --- Main compression functions ---

def compress_sessions(
    memory_dir: str,
    now: datetime | None = None,
    force_stage: int | None = None,
    threshold_stage_1_days: int = THRESHOLD_STAGE_1_DAYS,
    threshold_stage_2_days: int = THRESHOLD_STAGE_2_DAYS,
    threshold_stage_3_days: int = THRESHOLD_STAGE_3_DAYS,
    protected_recent_sessions: int = PROTECTED_RECENT_SESSIONS,
    max_total_sessions: int = MAX_TOTAL_SESSIONS,
) -> str:
    """Run compression on all eligible sessions.

    Args:
        memory_dir: Path to memory directory.
        now: Current time (defaults to UTC now).
        force_stage: If set, force all eligible sessions to this stage.
        threshold_stage_1_days: Days for stage 0->1 transition.
        threshold_stage_2_days: Days for stage 1->2 transition.
        threshold_stage_3_days: Days for stage 2->3 transition.
        protected_recent_sessions: Number of most recent sessions exempt.
        max_total_sessions: Safety cap for total sessions.

    Returns:
        Summary string describing what was compressed.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    episodes_dir = _get_episodes_dir(memory_dir)

    if not episodes_dir.exists():
        return "No episodes found."

    session_files = _list_session_files(episodes_dir)
    if not session_files:
        return "No episodes found."

    # Determine protected sessions (most recent N by modification time)
    protected_set = set()
    if protected_recent_sessions > 0:
        recent_files = session_files[-protected_recent_sessions:]
        protected_set = {f.stem for f in recent_files}

    # If exceeding max_total_sessions, force oldest beyond cap to stage 3
    force_skeleton_set = set()
    if len(session_files) > max_total_sessions:
        overflow_count = len(session_files) - max_total_sessions
        for f in session_files[:overflow_count]:
            force_skeleton_set.add(f.stem)

    sessions_compressed = 0
    total_episodes_changed = 0
    warnings = []
    state_sessions = {}

    for filepath in session_files:
        session_id = filepath.stem
        session_data = _load_session_file(filepath)

        if session_data is None:
            warnings.append(f"Warning: Skipped corrupted session file: {filepath.name}")
            continue

        is_protected = session_id in protected_set

        if force_stage is not None and not is_protected:
            target_stage = force_stage
        elif session_id in force_skeleton_set:
            target_stage = 3
        else:
            latest_ts = _get_latest_episode_timestamp(session_data)
            target_stage = determine_target_stage(
                latest_ts, now, is_protected,
                threshold_stage_1_days,
                threshold_stage_2_days,
                threshold_stage_3_days,
            )

        # force=True for force_skeleton (overflow) and explicit force_stage
        is_forced = (session_id in force_skeleton_set) or (force_stage is not None and not is_protected)
        if target_stage > 0:
            compressed_data, changed = _compress_session_data(session_data, target_stage, force=is_forced)
            if changed > 0:
                _write_file_atomic(filepath, compressed_data)
                sessions_compressed += 1
                total_episodes_changed += changed

        # Track max stage for state file
        # Use compressed_data (already in memory) when compression was attempted,
        # otherwise use the original session_data to avoid unnecessary file re-read.
        max_stage = 0
        final_data = compressed_data if target_stage > 0 else session_data
        for ep in (final_data or {}).get("episodes", []):
            ep_stage = ep.get("compression_stage", 0)
            if ep_stage > max_stage:
                max_stage = ep_stage
        state_sessions[session_id] = {"max_stage": max_stage}

    # Save compression state
    state = {
        "last_run": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sessions": state_sessions,
    }
    _save_compression_state(memory_dir, state)

    # Build summary
    lines = []
    lines.append(f"Compression complete: {sessions_compressed} sessions compressed, {total_episodes_changed} episodes changed.")
    for w in warnings:
        lines.append(w)
    return "\n".join(lines)


def dry_run(
    memory_dir: str,
    now: datetime | None = None,
    force_stage: int | None = None,
    threshold_stage_1_days: int = THRESHOLD_STAGE_1_DAYS,
    threshold_stage_2_days: int = THRESHOLD_STAGE_2_DAYS,
    threshold_stage_3_days: int = THRESHOLD_STAGE_3_DAYS,
    protected_recent_sessions: int = PROTECTED_RECENT_SESSIONS,
    max_total_sessions: int = MAX_TOTAL_SESSIONS,
) -> str:
    """Report what would be compressed without modifying files.

    Same logic as compress_sessions but does not write any files.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    episodes_dir = _get_episodes_dir(memory_dir)

    if not episodes_dir.exists():
        return "No episodes found."

    session_files = _list_session_files(episodes_dir)
    if not session_files:
        return "No episodes found."

    # Determine protected sessions
    protected_set = set()
    if protected_recent_sessions > 0:
        recent_files = session_files[-protected_recent_sessions:]
        protected_set = {f.stem for f in recent_files}

    # Max total sessions overflow
    force_skeleton_set = set()
    if len(session_files) > max_total_sessions:
        overflow_count = len(session_files) - max_total_sessions
        for f in session_files[:overflow_count]:
            force_skeleton_set.add(f.stem)

    lines = ["Dry-run compression report:", ""]
    would_compress_sessions = 0
    would_change_episodes = 0
    warnings = []

    for filepath in session_files:
        session_id = filepath.stem
        session_data = _load_session_file(filepath)

        if session_data is None:
            warnings.append(f"Warning: Would skip corrupted session file: {filepath.name}")
            continue

        is_protected = session_id in protected_set

        if force_stage is not None and not is_protected:
            target_stage = force_stage
        elif session_id in force_skeleton_set:
            target_stage = 3
        else:
            latest_ts = _get_latest_episode_timestamp(session_data)
            target_stage = determine_target_stage(
                latest_ts, now, is_protected,
                threshold_stage_1_days,
                threshold_stage_2_days,
                threshold_stage_3_days,
            )

        if target_stage > 0:
            compressed_preview, changed = _compress_session_data(session_data, target_stage)
            if changed > 0:
                episode_count = len(session_data.get("episodes", []))
                # Count insights that would be extracted
                insight_count = sum(
                    len(ep.get("insights", []))
                    for ep in compressed_preview.get("episodes", [])
                    if ep.get("insights")
                )
                insight_info = f", {insight_count} insights extracted" if insight_count > 0 else ""
                lines.append(
                    f"  {session_id}: {episode_count} episodes -> stage {target_stage} "
                    f"({STAGE_LABELS.get(target_stage, '?')}), {changed} episodes would change{insight_info}"
                )
                would_compress_sessions += 1
                would_change_episodes += changed
            else:
                lines.append(f"  {session_id}: already at stage {target_stage} or higher")
        else:
            if is_protected:
                lines.append(f"  {session_id}: protected (recent)")
            else:
                lines.append(f"  {session_id}: no compression needed (recent)")

    lines.append("")
    lines.append(f"Would compress {would_compress_sessions} sessions, {would_change_episodes} episodes.")
    for w in warnings:
        lines.append(w)
    return "\n".join(lines)


def get_compression_status(memory_dir: str) -> str:
    """Get a formatted summary of compression status for all sessions.

    Returns session count per stage, total episodes per stage, and
    estimated total storage size.
    """
    episodes_dir = _get_episodes_dir(memory_dir)

    if not episodes_dir.exists():
        return "No episodes found."

    session_files = _list_session_files(episodes_dir)
    if not session_files:
        return "No episodes found."

    # Count sessions and episodes per stage
    sessions_by_stage = {0: 0, 1: 0, 2: 0, 3: 0}
    episodes_by_stage = {0: 0, 1: 0, 2: 0, 3: 0}
    total_size_bytes = 0
    corrupted_count = 0

    for filepath in session_files:
        session_data = _load_session_file(filepath)

        if session_data is None:
            corrupted_count += 1
            continue

        try:
            total_size_bytes += filepath.stat().st_size
        except OSError:
            pass

        # Determine session max stage from episodes
        session_max_stage = 0
        for ep in session_data.get("episodes", []):
            ep_stage = ep.get("compression_stage", 0)
            if ep_stage in episodes_by_stage:
                episodes_by_stage[ep_stage] += 1
            if ep_stage > session_max_stage:
                session_max_stage = ep_stage

        if session_max_stage in sessions_by_stage:
            sessions_by_stage[session_max_stage] += 1

    # Build report
    total_sessions = sum(sessions_by_stage.values())
    total_episodes = sum(episodes_by_stage.values())

    lines = [f"Compression status ({total_sessions} sessions, {total_episodes} episodes):", ""]

    lines.append("Sessions by stage:")
    for stage in range(4):
        count = sessions_by_stage[stage]
        label = STAGE_LABELS[stage]
        lines.append(f"  Stage {stage} ({label}): {count} sessions")

    lines.append("")
    lines.append("Episodes by stage:")
    for stage in range(4):
        count = episodes_by_stage[stage]
        label = STAGE_LABELS[stage]
        lines.append(f"  Stage {stage} ({label}): {count} episodes")

    lines.append("")
    if total_size_bytes < 1024:
        lines.append(f"Total storage: {total_size_bytes} bytes")
    elif total_size_bytes < 1024 * 1024:
        lines.append(f"Total storage: {total_size_bytes / 1024:.1f} KB")
    else:
        lines.append(f"Total storage: {total_size_bytes / (1024 * 1024):.1f} MB")

    if corrupted_count > 0:
        lines.append(f"Corrupted session files: {corrupted_count}")

    # Show state file info
    state = _load_compression_state(memory_dir)
    if state.get("last_run"):
        lines.append(f"Last compression run: {state['last_run']}")
    else:
        lines.append("No compression runs recorded.")

    return "\n".join(lines)


# --- CLI ---

def main(argv=None):
    """Entry point for CLI usage."""
    parser = argparse.ArgumentParser(
        description="Staged compression for episode memory sessions"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # compress
    compress_parser = subparsers.add_parser("compress", help="Run compression on eligible sessions")
    compress_parser.add_argument(
        "--memory-dir", required=True, help="Path to memory directory"
    )
    compress_parser.add_argument(
        "--force-stage", type=int, choices=[1, 2, 3], default=None,
        help="Force all eligible sessions to a specific stage (overrides age thresholds)"
    )

    # status
    status_parser = subparsers.add_parser("status", help="Show compression status")
    status_parser.add_argument(
        "--memory-dir", required=True, help="Path to memory directory"
    )

    # dry-run
    dryrun_parser = subparsers.add_parser("dry-run", help="Show what would be compressed")
    dryrun_parser.add_argument(
        "--memory-dir", required=True, help="Path to memory directory"
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "compress":
        result = compress_sessions(
            memory_dir=args.memory_dir,
            force_stage=args.force_stage,
        )
        print(result)

    elif args.command == "status":
        result = get_compression_status(args.memory_dir)
        print(result)

    elif args.command == "dry-run":
        result = dry_run(memory_dir=args.memory_dir)
        print(result)


if __name__ == "__main__":
    main()
