#!/usr/bin/env python3
"""Episode recall tool providing three independent search pathways over episode data.

Pathway 1: Keyword search (full-text, case-insensitive, AND logic)
Pathway 2: Time-axis search (absolute range / relative range / session-based)
Pathway 3: Context search (topic-based via reverse index, OR logic)

All pathways are stateless, independent, and read-only.
Episode data is read directly from session JSON files (no dependency on episode_memory.py API).
Context search additionally reads the topic index produced by topic_index.py.
"""

import argparse
import io
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# --- Constants ---

EPISODES_SUBDIR = "episodes"
INDEX_FILENAME = "topic_index.json"

DEFAULT_RESULT_LIMIT = 50
DEFAULT_SUMMARY_PREVIEW_LENGTH = 120
MAX_EPISODE_MATCHES = 500  # Cap for intermediate result dicts in context_search
MAX_CACHE_ENTRIES = 50  # Maximum number of entries in TTL caches

# TTL cache for _load_all_episodes
_episode_cache: dict = {}  # {memory_dir: (timestamp, episodes)}
_EPISODE_CACHE_TTL = 30  # seconds

# TTL cache for _list_session_files
_session_files_cache: dict = {}  # {str(episodes_dir): (timestamp, files)}
_SESSION_FILES_CACHE_TTL = 30  # seconds

# Relative time pattern: e.g., "7d", "24h", "2w"
_RELATIVE_TIME_PATTERN = re.compile(r'^(\d+)([hdw])$', re.IGNORECASE)


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
    """List all session files sorted by modification time (oldest first).

    Uses a TTL cache (30s) to avoid repeated stat() calls.
    """
    import time as _time
    cache_key = str(episodes_dir)
    now = _time.monotonic()
    cached = _session_files_cache.get(cache_key)
    if cached is not None:
        cache_time, files = cached
        if now - cache_time < _SESSION_FILES_CACHE_TTL:
            return list(files)

    if not episodes_dir.exists():
        return []
    files = sorted(
        [f for f in episodes_dir.iterdir()
         if f.is_file() and f.name.startswith("session_") and f.name.endswith(".json")],
        key=lambda f: f.stat().st_mtime,
    )
    # Evict oldest entries if cache exceeds size limit
    while len(_session_files_cache) >= MAX_CACHE_ENTRIES:
        oldest_key = min(_session_files_cache, key=lambda k: _session_files_cache[k][0])
        del _session_files_cache[oldest_key]
    _session_files_cache[cache_key] = (now, files)
    return list(files)


# --- Tag normalization (consistent with topic_index.py) ---

def _normalize_tag(tag: str) -> str:
    """Normalize a tag: lowercase, strip whitespace, unify path separators."""
    tag = tag.strip().lower()
    tag = tag.replace("\\", "/")
    return tag


# --- Episode loading helper ---

def _load_all_episodes(memory_dir: str) -> list[dict]:
    """Load all episodes from all session files. Corrupted files are skipped.

    Uses a TTL cache (30s) to avoid repeated file I/O on consecutive searches.
    """
    import time as _time
    now = _time.monotonic()
    cached = _episode_cache.get(memory_dir)
    if cached is not None:
        cache_time, episodes = cached
        if now - cache_time < _EPISODE_CACHE_TTL:
            return list(episodes)  # Return copy to prevent mutation

    episodes_dir = _get_episodes_path(memory_dir)
    session_files = _list_session_files(episodes_dir)
    all_episodes = []
    for sf in session_files:
        data = _load_session_file(sf)
        if data is None:
            continue
        for ep in data.get("episodes", []):
            all_episodes.append(ep)

    # Evict oldest entries if cache exceeds size limit
    while len(_episode_cache) >= MAX_CACHE_ENTRIES:
        oldest_key = min(_episode_cache, key=lambda k: _episode_cache[k][0])
        del _episode_cache[oldest_key]
    _episode_cache[memory_dir] = (now, all_episodes)
    return list(all_episodes)  # Return copy


_load_all_episodes.cache_clear = lambda: _episode_cache.clear()


def invalidate_cache() -> None:
    """Clear episode and session file caches.

    Call after modifying episode data (e.g. recall count increment)
    to ensure subsequent searches see fresh data.
    """
    _episode_cache.clear()
    _session_files_cache.clear()


def _load_episodes_from_recent_sessions(memory_dir: str, n_sessions: int) -> list[dict]:
    """Load episodes from the N most recent session files."""
    episodes_dir = _get_episodes_path(memory_dir)
    session_files = _list_session_files(episodes_dir)
    # Take the last N files (most recent by mtime)
    recent_files = session_files[-n_sessions:] if n_sessions < len(session_files) else session_files
    all_episodes = []
    for sf in recent_files:
        data = _load_session_file(sf)
        if data is None:
            continue
        for ep in data.get("episodes", []):
            all_episodes.append(ep)
    return all_episodes


# --- Episode type filtering ---

def _filter_by_type(episodes: list[dict], episode_type: str | None) -> list[dict]:
    """Filter episodes by type if specified."""
    if not episode_type:
        return episodes
    return [ep for ep in episodes if ep.get("episode_type") == episode_type]


# --- Result formatting ---

def _truncate_summary(summary: str, max_len: int = DEFAULT_SUMMARY_PREVIEW_LENGTH) -> str:
    """Truncate a summary to a maximum preview length."""
    if len(summary) <= max_len:
        return summary
    return summary[:max_len] + "..."


def _format_result_entry(
    index: int,
    episode: dict,
    matching_detail: str = "",
) -> str:
    """Format a single episode result entry."""
    ep_id = episode.get("episode_id", "?")
    ep_type = episode.get("episode_type", "?")
    timestamp = episode.get("timestamp", "?")
    session_id = episode.get("session_id", "?")
    summary = _truncate_summary(episode.get("summary", ""))

    line = f"  {index}. [{ep_type}] {ep_id} ({timestamp}) session={session_id}"
    line += f"\n     Summary: {summary}"
    if matching_detail:
        line += f"\n     Match: {matching_detail}"
    return line


# --- Pathway 1: Keyword Search ---

def _episode_matches_keywords(episode: dict, keywords: list[str]) -> tuple[bool, str]:
    """Check if an episode matches all keywords (AND logic, case-insensitive).

    Returns (matches, matching_detail_string).
    """
    # Build searchable text from summary, user_utterances[].text, tags
    fields = {}
    summary = episode.get("summary", "")
    if summary:
        fields["summary"] = summary

    for utt in episode.get("user_utterances", []):
        text = utt.get("text", "")
        if text:
            if "utterance" not in fields:
                fields["utterance"] = text
            else:
                fields["utterance"] += " " + text

    tags_str = " ".join(episode.get("tags", []))
    if tags_str:
        fields["tags"] = tags_str

    # Check all keywords are present in at least one field (combined)
    combined = " ".join(fields.values()).lower()

    for kw in keywords:
        if kw.lower() not in combined:
            return False, ""

    # Find which fields matched for detail
    matched_fields = []
    for kw in keywords:
        kw_lower = kw.lower()
        for field_name, field_val in fields.items():
            if kw_lower in field_val.lower():
                matched_fields.append(f"{field_name}:'{kw}'")
                break

    return True, ", ".join(matched_fields)


def keyword_search(
    memory_dir: str,
    keywords: list[str],
    limit: int = DEFAULT_RESULT_LIMIT,
    episode_type: str | None = None,
) -> str:
    """Search episodes by keyword (full-text, case-insensitive, AND logic).

    Args:
        memory_dir: Path to memory directory.
        keywords: List of keyword strings (all must match).
        limit: Maximum number of results.
        episode_type: Optional episode type filter.

    Returns a formatted result string.
    """
    if not keywords:
        return "ERROR: No keywords provided for search."

    all_episodes = _load_all_episodes(memory_dir)
    all_episodes = _filter_by_type(all_episodes, episode_type)

    # Search
    results = []
    for ep in all_episodes:
        matches, detail = _episode_matches_keywords(ep, keywords)
        if matches:
            results.append((ep, detail))

    if not results:
        kw_str = ", ".join(keywords)
        type_filter = f" (type={episode_type})" if episode_type else ""
        return f"No matching episodes found for keywords: {kw_str}{type_filter}"

    # Sort by timestamp descending (most recent first)
    results.sort(key=lambda r: r[0].get("timestamp", ""), reverse=True)

    total_matches = len(results)
    results = results[:limit]

    # Format output
    kw_str = ", ".join(keywords)
    type_filter = f", type={episode_type}" if episode_type else ""
    lines = [
        f"Keyword search results for: {kw_str}{type_filter} "
        f"({total_matches} total, showing {len(results)}):",
        "",
    ]

    for i, (ep, detail) in enumerate(results, 1):
        lines.append(_format_result_entry(i, ep, matching_detail=detail))

    return "\n".join(lines)


# --- Pathway 2: Time-Axis Search ---

def _parse_relative_time(relative_str: str) -> timedelta | None:
    """Parse a relative time string like '7d', '24h', '2w'. Returns None on failure."""
    match = _RELATIVE_TIME_PATTERN.match(relative_str)
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2).lower()
    if unit == "h":
        return timedelta(hours=value)
    elif unit == "d":
        return timedelta(days=value)
    elif unit == "w":
        return timedelta(weeks=value)
    return None


def _parse_timestamp(ts_str: str) -> datetime | None:
    """Parse an ISO 8601 timestamp string. Returns None on failure."""
    # Try with Z suffix
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(ts_str, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def time_range_search(
    memory_dir: str,
    last: str | None = None,
    start: str | None = None,
    end: str | None = None,
    sessions: int | None = None,
    limit: int = DEFAULT_RESULT_LIMIT,
    episode_type: str | None = None,
) -> str:
    """Search episodes by time range.

    Exactly one of the following must be specified:
    - last: Relative range (e.g., "7d", "24h", "2w")
    - start/end: Absolute range (ISO 8601)
    - sessions: Number of recent sessions

    Args:
        memory_dir: Path to memory directory.
        last: Relative time range string.
        start: Absolute start datetime (ISO 8601).
        end: Absolute end datetime (ISO 8601).
        sessions: Number of recent sessions to include.
        limit: Maximum number of results.
        episode_type: Optional episode type filter.

    Returns a formatted result string.
    """
    # Validate: exactly one mode specified
    modes_specified = sum([
        last is not None,
        (start is not None or end is not None),
        sessions is not None,
    ])
    if modes_specified == 0:
        return "ERROR: One of --last, --start/--end, or --sessions is required."
    if modes_specified > 1:
        return "ERROR: Only one of --last, --start/--end, or --sessions may be specified."

    now = datetime.now(timezone.utc)

    if sessions is not None:
        # Session-based mode
        if sessions < 1:
            return "ERROR: --sessions must be at least 1."
        episodes = _load_episodes_from_recent_sessions(memory_dir, sessions)
        episodes = _filter_by_type(episodes, episode_type)
        query_desc = f"last {sessions} sessions"

    elif last is not None:
        # Relative mode
        delta = _parse_relative_time(last)
        if delta is None:
            return f"ERROR: Invalid relative time format '{last}'. Use e.g., '7d', '24h', '2w'."
        range_start = now - delta
        episodes = _load_all_episodes(memory_dir)
        episodes = _filter_by_type(episodes, episode_type)
        episodes = [
            ep for ep in episodes
            if _episode_in_time_range(ep, range_start, now)
        ]
        query_desc = f"last {last}"

    else:
        # Absolute mode
        range_start = _parse_timestamp(start) if start else None
        range_end = _parse_timestamp(end) if end else None
        if range_start is None and range_end is None:
            return "ERROR: At least one of --start or --end must be a valid ISO 8601 datetime."
        if range_start is None:
            range_start = datetime(2000, 1, 1, tzinfo=timezone.utc)
        if range_end is None:
            range_end = now
        episodes = _load_all_episodes(memory_dir)
        episodes = _filter_by_type(episodes, episode_type)
        episodes = [
            ep for ep in episodes
            if _episode_in_time_range(ep, range_start, range_end)
        ]
        start_str = start or "(open)"
        end_str = end or "(now)"
        query_desc = f"{start_str} to {end_str}"

    if not episodes:
        type_filter = f" (type={episode_type})" if episode_type else ""
        return f"No matching episodes found for time range: {query_desc}{type_filter}"

    # Sort by timestamp descending (most recent first)
    episodes.sort(key=lambda ep: ep.get("timestamp", ""), reverse=True)

    total_matches = len(episodes)
    episodes = episodes[:limit]

    type_filter = f", type={episode_type}" if episode_type else ""
    lines = [
        f"Time-range search results for: {query_desc}{type_filter} "
        f"({total_matches} total, showing {len(episodes)}):",
        "",
    ]

    for i, ep in enumerate(episodes, 1):
        lines.append(_format_result_entry(i, ep))

    return "\n".join(lines)


def _episode_in_time_range(episode: dict, start: datetime, end: datetime) -> bool:
    """Check if an episode's timestamp falls within [start, end]."""
    ts_str = episode.get("timestamp", "")
    ts = _parse_timestamp(ts_str)
    if ts is None:
        return False
    return start <= ts <= end


# --- Pathway 3: Context Search ---

def _load_topic_index(memory_dir: str) -> dict | None:
    """Load the topic index. Returns None if missing or corrupted."""
    index_path = _get_index_path(memory_dir)
    try:
        text = index_path.read_text(encoding="utf-8")
        data = json.loads(text)
        if isinstance(data, dict) and "index" in data:
            return data
        return None
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None


def context_search(
    memory_dir: str,
    tags: list[str],
    prefix: bool = False,
    limit: int = DEFAULT_RESULT_LIMIT,
    episode_type: str | None = None,
) -> str:
    """Search episodes by topic tags using the reverse index (OR logic).

    Args:
        memory_dir: Path to memory directory.
        tags: List of topic tag strings.
        prefix: If True, match tag prefixes.
        limit: Maximum number of results.
        episode_type: Optional episode type filter.

    Returns a formatted result string.
    """
    if not tags:
        return "ERROR: No tags provided for context search."

    # Load topic index
    index_data = _load_topic_index(memory_dir)
    if index_data is None:
        return "Topic index not found. Run 'topic_index.py build' to create the index first."

    index = index_data.get("index", {})

    # Normalize query tags
    query_tags = [_normalize_tag(t) for t in tags if _normalize_tag(t)]
    if not query_tags:
        return "ERROR: No valid tags provided after normalization."

    # Collect matching episode references with match counts
    # episode_id -> {"ref": {...}, "match_count": N, "matching_tags": [...]}
    episode_matches: dict[str, dict] = {}

    for query_tag in query_tags:
        if prefix:
            matching_keys = [k for k in index if k.startswith(query_tag)]
        else:
            matching_keys = [query_tag] if query_tag in index else []

        for key in matching_keys:
            for ref in index[key]:
                ep_id = ref.get("episode_id", "")
                if not ep_id:
                    continue
                if ep_id not in episode_matches:
                    if len(episode_matches) >= MAX_EPISODE_MATCHES:
                        continue  # Cap reached, skip new entries
                    episode_matches[ep_id] = {
                        "ref": ref,
                        "match_count": 0,
                        "matching_tags": [],
                    }
                episode_matches[ep_id]["match_count"] += 1
                if key not in episode_matches[ep_id]["matching_tags"]:
                    episode_matches[ep_id]["matching_tags"].append(key)

    if not episode_matches:
        tag_str = ", ".join(query_tags)
        mode = "prefix" if prefix else "exact"
        type_filter = f" (type={episode_type})" if episode_type else ""
        return f"No matching episodes found for tags: {tag_str} ({mode} match){type_filter}"

    # Load only the session files that contain matching episodes (targeted load)
    needed_session_ids = set()
    for match_info in episode_matches.values():
        sid = match_info["ref"].get("session_id", "")
        if sid:
            needed_session_ids.add(sid)

    episodes_dir = _get_episodes_path(memory_dir)
    ep_by_id: dict[str, dict] = {}
    for sf in _list_session_files(episodes_dir):
        data = _load_session_file(sf)
        if data is None:
            continue
        if data.get("session_id") not in needed_session_ids:
            continue
        for ep in data.get("episodes", []):
            eid = ep.get("episode_id")
            if eid and eid in episode_matches:
                ep_by_id[eid] = ep

    # Assemble results with full episode data
    results = []
    for ep_id, match_info in episode_matches.items():
        ep = ep_by_id.get(ep_id)
        if ep is None:
            continue
        results.append((ep, match_info))

    # Apply type filter
    if episode_type:
        results = [(ep, mi) for ep, mi in results if ep.get("episode_type") == episode_type]

    if not results:
        tag_str = ", ".join(query_tags)
        mode = "prefix" if prefix else "exact"
        type_filter = f" (type={episode_type})" if episode_type else ""
        return f"No matching episodes found for tags: {tag_str} ({mode} match){type_filter}"

    # Sort by match count descending, then by timestamp descending
    results.sort(
        key=lambda r: (r[1]["match_count"], r[0].get("timestamp", "")),
        reverse=True,
    )

    total_matches = len(results)
    results = results[:limit]

    tag_str = ", ".join(query_tags)
    mode = "prefix" if prefix else "exact"
    type_filter = f", type={episode_type}" if episode_type else ""
    lines = [
        f"Context search results for tags: {tag_str} ({mode} match{type_filter}, "
        f"{total_matches} total, showing {len(results)}):",
        "",
    ]

    for i, (ep, match_info) in enumerate(results, 1):
        matching_tags_str = ", ".join(match_info["matching_tags"])
        lines.append(_format_result_entry(i, ep, matching_detail=f"tags: {matching_tags_str}"))

    return "\n".join(lines)


# --- Raw search variants (return episode dicts instead of formatted text) ---


def keyword_search_raw(
    memory_dir: str,
    keywords: list[str],
    limit: int = DEFAULT_RESULT_LIMIT,
    episode_type: str | None = None,
) -> list[tuple[dict, str]]:
    """Like keyword_search but returns raw (episode, match_detail) tuples."""
    if not keywords:
        return []
    all_episodes = _load_all_episodes(memory_dir)
    all_episodes = _filter_by_type(all_episodes, episode_type)
    results = []
    for ep in all_episodes:
        matches, detail = _episode_matches_keywords(ep, keywords)
        if matches:
            results.append((ep, detail))
    results.sort(key=lambda r: r[0].get("timestamp", ""), reverse=True)
    return results[:limit]


def time_range_search_raw(
    memory_dir: str,
    last: str | None = None,
    start: str | None = None,
    end: str | None = None,
    sessions: int | None = None,
    limit: int = DEFAULT_RESULT_LIMIT,
    episode_type: str | None = None,
) -> list[dict]:
    """Like time_range_search but returns raw episode dicts."""
    now = datetime.now(timezone.utc)

    if sessions is not None:
        if sessions < 1:
            return []
        episodes = _load_episodes_from_recent_sessions(memory_dir, sessions)
    elif last is not None:
        delta = _parse_relative_time(last)
        if delta is None:
            return []
        range_start = now - delta
        episodes = _load_all_episodes(memory_dir)
        episodes = [ep for ep in episodes if _episode_in_time_range(ep, range_start, now)]
    elif start is not None or end is not None:
        range_start = _parse_timestamp(start) if start else datetime(2000, 1, 1, tzinfo=timezone.utc)
        range_end = _parse_timestamp(end) if end else now
        if range_start is None:
            range_start = datetime(2000, 1, 1, tzinfo=timezone.utc)
        if range_end is None:
            range_end = now
        episodes = _load_all_episodes(memory_dir)
        episodes = [ep for ep in episodes if _episode_in_time_range(ep, range_start, range_end)]
    else:
        return []

    episodes = _filter_by_type(episodes, episode_type)
    episodes.sort(key=lambda ep: ep.get("timestamp", ""), reverse=True)
    return episodes[:limit]


def context_search_raw(
    memory_dir: str,
    tags: list[str],
    prefix: bool = False,
    limit: int = DEFAULT_RESULT_LIMIT,
    episode_type: str | None = None,
) -> list[tuple[dict, dict]]:
    """Like context_search but returns raw (episode, match_info) tuples."""
    if not tags:
        return []

    index_data = _load_topic_index(memory_dir)
    if index_data is None:
        return []

    index = index_data.get("index", {})
    query_tags = [_normalize_tag(t) for t in tags if _normalize_tag(t)]
    if not query_tags:
        return []

    episode_matches: dict[str, dict] = {}
    for query_tag in query_tags:
        if prefix:
            matching_keys = [k for k in index if k.startswith(query_tag)]
        else:
            matching_keys = [query_tag] if query_tag in index else []
        for key in matching_keys:
            for ref in index[key]:
                ep_id = ref.get("episode_id", "")
                if not ep_id:
                    continue
                if ep_id not in episode_matches:
                    if len(episode_matches) >= MAX_EPISODE_MATCHES:
                        continue
                    episode_matches[ep_id] = {"ref": ref, "match_count": 0, "matching_tags": []}
                episode_matches[ep_id]["match_count"] += 1
                if key not in episode_matches[ep_id]["matching_tags"]:
                    episode_matches[ep_id]["matching_tags"].append(key)

    if not episode_matches:
        return []

    needed_session_ids = set()
    for match_info in episode_matches.values():
        sid = match_info["ref"].get("session_id", "")
        if sid:
            needed_session_ids.add(sid)

    episodes_dir = _get_episodes_path(memory_dir)
    ep_by_id: dict[str, dict] = {}
    for sf in _list_session_files(episodes_dir):
        data = _load_session_file(sf)
        if data is None:
            continue
        if data.get("session_id") not in needed_session_ids:
            continue
        for ep in data.get("episodes", []):
            eid = ep.get("episode_id")
            if eid and eid in episode_matches:
                ep_by_id[eid] = ep

    results = []
    for ep_id, match_info in episode_matches.items():
        ep = ep_by_id.get(ep_id)
        if ep is None:
            continue
        results.append((ep, match_info))

    if episode_type:
        results = [(ep, mi) for ep, mi in results if ep.get("episode_type") == episode_type]

    results.sort(key=lambda r: (r[1]["match_count"], r[0].get("timestamp", "")), reverse=True)
    return results[:limit]


# --- Mood-linked Reordering (Feature B of design_emotion_memory_binding.md) ---

# Safety valve constants
EMOTION_CONTRIBUTION_CAP = 0.3  # Max fraction of final score from emotion similarity
DELTA_CORRECTION_CAP = 0.2      # Max additional correction from emotion deltas


def compute_emotion_similarity(current_state: dict, trace: dict) -> float:
    """Compute emotion similarity between current state and an episode's trace.

    Returns a value in [0, 1] where 1.0 = identical emotion state.
    Three axes are weighted equally (simple average of per-axis similarities).

    Args:
        current_state: Dict with fulfillment, tension, affinity keys (each -1.0 to 1.0).
        trace: Episode emotion trace dict with the same axis keys.

    Returns:
        Similarity score in [0.0, 1.0].
    """
    axes = ("fulfillment", "tension", "affinity")
    total_distance = 0.0
    for axis in axes:
        current_val = current_state.get(axis, 0.0)
        trace_val = trace.get(axis, 0.0)
        if not isinstance(current_val, (int, float)):
            current_val = 0.0
        if not isinstance(trace_val, (int, float)):
            trace_val = 0.0
        # Max possible distance per axis is 2.0 (from -1 to +1)
        total_distance += abs(float(current_val) - float(trace_val))

    # Average distance across 3 axes, normalized to [0, 1] (max avg distance = 2.0)
    avg_distance = total_distance / 3.0
    similarity = 1.0 - (avg_distance / 2.0)
    return max(0.0, min(1.0, similarity))


def compute_delta_correction(trace: dict) -> float:
    """Compute a correction factor based on emotion deltas in the trace.

    Episodes recorded during larger emotion changes get a small boost.
    The correction is capped by DELTA_CORRECTION_CAP.

    Args:
        trace: Episode emotion trace dict, may contain delta_fulfillment/tension/affinity.

    Returns:
        Correction value in [0.0, DELTA_CORRECTION_CAP].
    """
    axes = ("fulfillment", "tension", "affinity")
    total_abs_delta = 0.0
    has_delta = False
    for axis in axes:
        delta_key = f"delta_{axis}"
        delta_val = trace.get(delta_key)
        if delta_val is not None and isinstance(delta_val, (int, float)):
            total_abs_delta += abs(float(delta_val))
            has_delta = True

    if not has_delta:
        return 0.0

    # Average absolute delta across 3 axes, normalized (max per axis = 2.0)
    avg_abs_delta = total_abs_delta / 3.0
    normalized = avg_abs_delta / 2.0  # [0, 1]
    correction = normalized * DELTA_CORRECTION_CAP
    return max(0.0, min(DELTA_CORRECTION_CAP, correction))


def mood_reorder(
    episodes: list[dict],
    current_emotion: dict,
    emotion_contribution_cap: float = EMOTION_CONTRIBUTION_CAP,
) -> list[dict]:
    """Reorder episodes based on mood similarity with current emotion state.

    This is a post-search reordering step. It combines the original position-based
    score with an emotion similarity score. The emotion contribution is capped
    to prevent emotion from dominating search results.

    Safety valves implemented:
    1. Emotion contribution cap: emotion score limited to emotion_contribution_cap fraction
    2. 3-axis equal weighting: each axis contributes equally to similarity
    3. Delta correction cap: emotion delta boost is capped
    4. Trace-absent neutral: episodes without traces get zero emotion score (no penalty)

    Args:
        episodes: List of episode dicts (already ordered by original search).
        current_emotion: Current emotion state dict with fulfillment/tension/affinity.
        emotion_contribution_cap: Max fraction of final score from emotion (default 0.3).

    Returns:
        Reordered list of episode dicts (same episodes, different order).
    """
    if not episodes or len(episodes) <= 1:
        return list(episodes)

    n = len(episodes)
    scored = []

    for i, ep in enumerate(episodes):
        # Position score: higher for earlier items (original rank preserved)
        # Normalized to [0, 1] where 1.0 = first position
        position_score = 1.0 - (i / n)

        # Extract emotion trace
        trace = ep.get("emotion_trace")
        emotion_score = 0.0
        if trace is not None and isinstance(trace, dict):
            similarity = compute_emotion_similarity(current_emotion, trace)
            delta_correction = compute_delta_correction(trace)
            emotion_score = similarity + delta_correction
            # Normalize: similarity is [0,1], delta_correction is [0, DELTA_CORRECTION_CAP]
            # Cap the total emotion score to [0, 1]
            emotion_score = min(1.0, emotion_score)

        # Combine: position dominates, emotion is supplementary
        # final = position * (1 - cap) + emotion * cap
        cap = max(0.0, min(1.0, emotion_contribution_cap))
        final_score = position_score * (1.0 - cap) + emotion_score * cap

        scored.append((final_score, i, ep))  # i for stable sort tiebreaker

    # Sort by final score descending (higher = better)
    scored.sort(key=lambda x: (-x[0], x[1]))

    return [ep for _, _, ep in scored]


# --- CLI ---

def main(argv=None):
    """Entry point for CLI usage."""
    parser = argparse.ArgumentParser(
        description="Episode recall: three-pathway search over episode data"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # keyword
    kw_parser = subparsers.add_parser("keyword", help="Search episodes by keyword")
    kw_parser.add_argument(
        "--memory-dir", required=True, help="Path to memory directory"
    )
    kw_parser.add_argument(
        "--keywords", required=True,
        help="Comma-separated search terms (AND logic)"
    )
    kw_parser.add_argument(
        "--limit", type=int, default=DEFAULT_RESULT_LIMIT,
        help=f"Maximum results (default: {DEFAULT_RESULT_LIMIT})"
    )
    kw_parser.add_argument(
        "--type", dest="episode_type", default=None,
        help="Filter by episode type"
    )

    # time-range
    tr_parser = subparsers.add_parser("time-range", help="Search episodes by time range")
    tr_parser.add_argument(
        "--memory-dir", required=True, help="Path to memory directory"
    )
    tr_parser.add_argument(
        "--last", default=None,
        help="Relative range, e.g., '7d' (7 days), '24h' (24 hours), '2w' (2 weeks)"
    )
    tr_parser.add_argument(
        "--start", default=None,
        help="Absolute start datetime (ISO 8601)"
    )
    tr_parser.add_argument(
        "--end", default=None,
        help="Absolute end datetime (ISO 8601)"
    )
    tr_parser.add_argument(
        "--sessions", type=int, default=None,
        help="Number of recent sessions to include"
    )
    tr_parser.add_argument(
        "--limit", type=int, default=DEFAULT_RESULT_LIMIT,
        help=f"Maximum results (default: {DEFAULT_RESULT_LIMIT})"
    )
    tr_parser.add_argument(
        "--type", dest="episode_type", default=None,
        help="Filter by episode type"
    )

    # context
    ctx_parser = subparsers.add_parser("context", help="Search episodes by topic tags")
    ctx_parser.add_argument(
        "--memory-dir", required=True, help="Path to memory directory"
    )
    ctx_parser.add_argument(
        "--tags", required=True,
        help="Comma-separated topic tags to search for"
    )
    ctx_parser.add_argument(
        "--prefix", action="store_true", default=False,
        help="Match tag prefixes instead of exact matches"
    )
    ctx_parser.add_argument(
        "--limit", type=int, default=DEFAULT_RESULT_LIMIT,
        help=f"Maximum results (default: {DEFAULT_RESULT_LIMIT})"
    )
    ctx_parser.add_argument(
        "--type", dest="episode_type", default=None,
        help="Filter by episode type"
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "keyword":
        keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
        result = keyword_search(
            memory_dir=args.memory_dir,
            keywords=keywords,
            limit=args.limit,
            episode_type=args.episode_type,
        )
        if result.startswith("ERROR:"):
            print(result, file=sys.stderr)
            sys.exit(1)
        else:
            print(result)

    elif args.command == "time-range":
        result = time_range_search(
            memory_dir=args.memory_dir,
            last=args.last,
            start=args.start,
            end=args.end,
            sessions=args.sessions,
            limit=args.limit,
            episode_type=args.episode_type,
        )
        if result.startswith("ERROR:"):
            print(result, file=sys.stderr)
            sys.exit(1)
        else:
            print(result)

    elif args.command == "context":
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
        result = context_search(
            memory_dir=args.memory_dir,
            tags=tags,
            prefix=args.prefix,
            limit=args.limit,
            episode_type=args.episode_type,
        )
        if result.startswith("ERROR:"):
            print(result, file=sys.stderr)
            sys.exit(1)
        else:
            print(result)


if __name__ == "__main__":
    main()
