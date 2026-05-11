#!/usr/bin/env python3
"""MCP server wrapping memory tools for Claude Code.

Exposes memory_manager.py functions as native Claude Code tools via MCP stdio transport.
Includes emotion state management (three-axis: fulfillment/tension/affinity),
emotional trace attachment to episodes, and memory-emotion return processing.

IMPORTANT: For stdio transport, never print() to stdout.
Use print(..., file=sys.stderr) for debug logging.
"""

import asyncio
import io
import json
import os
import sys
import threading
import time

# Pre-import sqlite_vec to avoid 90s cold-start hang in first MCP request.
# Lazy load (via vector_search._load_sqlite_vec) triggers transitive numpy DLL load
# which can take 90+s on Windows in MCP subprocess context (cold cache + AV scan +
# dual-install scenario). Eager-import here pays the cost at server startup so the
# first CallToolRequest sees warm modules.
# Same fix pattern as G66 v2: identify true root cause via faulthandler stack trace,
# apply structural fix at the right layer.
try:
    import sqlite_vec  # noqa: F401
except ImportError:
    pass  # sqlite_vec optional; vector search degrades gracefully (existing behavior)

# Ensure UTF-8 stderr on Windows
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Add the tools directory to sys.path so we can import memory tools
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from file_io import resolve_project_root

_PROJECT_ROOT = resolve_project_root()

# Import spontaneous activation
# Import lesson metadata (C22-A: Lesson Validation Loop)
from mcp.server.fastmcp import FastMCP

import after_action_review  # C22-J: After-Action Success Review
import growth_metrics  # C22-F/L: Growth Metrics Dashboard
import lesson_conflict  # C22-E: Lesson Conflict Resolution
import lesson_injector  # C22-C: Semantic Lesson Injection
import lesson_metadata  # C22-A: Lesson Validation Loop
import mastery_profile  # C22-H: Mastery Experience Tracker
import success_registry  # C22-G: Success Pattern Extractor
import trajectory_store  # C22-I: Success Trajectory Library
import transfer_monitor  # C22-K: Positive Transfer Monitor
from activation_surface import surface as activation_surface_fn
from dynamic_read_verification import run_verify
from emotion_dynamics import (
    check_session_reset as dynamics_session_reset,
)
from emotion_dynamics import (
    get_current_amplitude as dynamics_get_amplitude,
)

# Import emotion dynamics
from emotion_dynamics import (
    get_dynamics_info,
    load_dynamics_state,
    save_dynamics_state,
)
from emotion_dynamics import (
    update_dynamics as dynamics_update,
)

# Import emotion reaction
from emotion_reaction import react as emotion_react_fn

# Import emotion state functions
from emotion_state import (
    _load_change_log,
    apply_session_decay,
    create_trace,
    format_change_history,
    get_change_history,
    get_state,
    get_state_dict,
    load_state,
    process_return_from_search_results,
    save_state,
    update_state,
)

# Import underlying functions from existing tools
from episode_memory import (
    increment_episode_recall_counts,
    record_episode,
)
from episode_recall import (
    _format_result_entry,
    _load_all_episodes,
    context_search,
    context_search_raw,
    keyword_search,
    keyword_search_raw,
    mood_reorder,
    time_range_search,
    time_range_search_raw,
)
from episode_recall import (
    invalidate_cache as episode_invalidate_cache,
)
from observation_facade import (
    get_dampening_factor as facade_get_dampening,
)
from observation_facade import (
    record_long_term as facade_record_long_term,
)
from observation_facade import (
    run_mini_snapshot as facade_run_mini_snapshot,
)

# Import observation facade (replaces direct imports of 7 observation modules)
from observation_facade import (
    run_snapshot as facade_run_snapshot,
)

# Import session context
from session_context import (
    save_context as sc_save,
)
from short_term_store import (
    apply_session_decay as stm_decay,
)
from short_term_store import (
    boost_recall as stm_boost_recall,
)
from short_term_store import (
    format_entries as stm_format,
)
from short_term_store import (
    get_stats as stm_stats,
)

# Import short-term memory store
from short_term_store import (
    load_store as stm_load,
)
from short_term_store import (
    read_entries as stm_read_entries,
)
from short_term_store import (
    save_store as stm_save,
)
from short_term_store import (
    write_entry as stm_write_entry,
)
from staged_compression import compress_sessions, get_compression_status
from topic_index import build_index

# Import semantic index (graceful degradation if unavailable)
try:
    from semantic_index import (
        SemanticIndex,
        extract_query_terms,
        format_score_breakdown,
        generate_snippet,
        get_lessons_mtime,
    )

    _SEMANTIC_AVAILABLE = True
except ImportError:
    _SEMANTIC_AVAILABLE = False

# Singleton cache for SemanticIndex (H12)
_semantic_index_cache: dict = {}  # {memory_dir: (instance, create_time)}
_SEMANTIC_INDEX_CACHE_TTL = 300  # 5 minutes

# M-P4: Emotion state cache (short TTL since emotion changes frequently)
_emotion_cache: dict = {"memory_dir": None, "state": None, "expires": 0.0}
_EMOTION_CACHE_TTL = 5.0  # seconds


def _get_cached_emotion_state(memory_dir: str) -> dict | None:
    """Get emotion state with short TTL cache to avoid repeated file reads."""
    now = time.monotonic()
    cache = _emotion_cache
    if cache["memory_dir"] == memory_dir and now < cache["expires"]:
        return cache["state"]
    try:
        state = get_state_dict(memory_dir)
        if isinstance(state, dict):
            cache["memory_dir"] = memory_dir
            cache["state"] = state
            cache["expires"] = now + _EMOTION_CACHE_TTL
            return state
    except Exception:
        pass
    return None


def _get_semantic_index(memory_dir: str) -> "SemanticIndex":
    """Get or create a cached SemanticIndex instance."""
    import time

    now = time.monotonic()
    cached = _semantic_index_cache.get(memory_dir)
    if cached is not None:
        instance, create_time = cached
        if now - create_time < _SEMANTIC_INDEX_CACHE_TTL:
            return instance
        else:
            try:
                instance.close()
            except Exception:
                pass
    instance = SemanticIndex(memory_dir)
    _semantic_index_cache[memory_dir] = (instance, now)
    return instance


# Track lessons mtime for CLI-based lesson additions
_lessons_mtime_at_sync: float = 0.0

# C22-A: Process-level session ID for lesson application dedup.
# Stable across multiple memory_search calls within one MCP server process.
from datetime import datetime as _dt

_LESSON_SESSION_ID = "session_" + _dt.now().strftime("%Y%m%d_%H%M%S")

# Default memory directory (overridable via MEMORY_DIR env var)
# Resolved at import time via resolve_memory_dir(). In test environments
# where resolution may fail, falls back to None — tests monkeypatch
# DEFAULT_MEMORY_DIR before calling any tool functions.
try:
    from file_io import resolve_memory_dir as _resolve_memory_dir

    DEFAULT_MEMORY_DIR = _resolve_memory_dir()
except Exception:
    DEFAULT_MEMORY_DIR = None  # type: ignore[assignment]

# Global growth directory (shared across all projects)
GROWTH_DIR = os.path.join(_PROJECT_ROOT, "growth")

# --- memory_search hang prevention (4th reoccurrence fix) ---
# Module-level lock + running flag to prevent duplicate _bg_sync_vectors
# threads from saturating the embedding API rate-limit slots (max=2),
# which previously blocked all subsequent memory_search calls and triggered
# MCP "stdio transport error" disconnects.
_bg_sync_lock = threading.Lock()
_bg_sync_running = False  # guarded by _bg_sync_lock
_bg_sync_started_at: float | None = None  # guarded by _bg_sync_lock; wallclock at start
# After this many seconds the running flag is considered stale and a new
# bg_sync attempt is allowed. Without this, a hung embedding API call would
# pin _bg_sync_running=True forever and silently disable vector index updates
# for the rest of the server lifetime.
_BG_SYNC_STALE_SECONDS = 300.0

# Upper-bound watchdog timeout for memory_search end-to-end. Even with the
# 15s embedding timeout, FTS5 / mood_reorder / lesson scoring can stack up.
# 90s is the SLO above which the MCP transport tends to disconnect.
_MEMORY_SEARCH_WATCHDOG_TIMEOUT = 90.0


def _swallow_stale_result(task: "asyncio.Task") -> None:
    """Discard result/exception of a stale impl task to silence asyncio warnings.

    G66 root-cause fix: when ``memory_search``'s watchdog timer wins the race,
    the underlying ``_memory_search_impl`` task keeps running in the
    ThreadPoolExecutor (Python cannot cancel a RUNNING thread). To prevent
    "Task exception was never retrieved" warnings on shutdown, we attach this
    callback to consume the eventual outcome.

    MED-1: catch ``BaseException`` so KeyboardInterrupt/SystemExit during
    server shutdown don't surface here. They are already handled by the
    server's graceful-shutdown path.
    """
    try:
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except (asyncio.CancelledError, asyncio.InvalidStateError):
            return
        if exc is not None:
            print(
                f"[memory_search] stale task: {exc!r}",
                file=sys.stderr,
            )
    except BaseException:  # noqa: BLE001
        pass


def _run_with_watchdog(func, args=(), kwargs=None, timeout: float = 90.0):
    """Run ``func`` in a daemon thread and bound its wall-clock duration.

    Returns a tuple ``(value, error)``:
      - On success: ``(return_value, None)``
      - On timeout: ``(None, TimeoutError(...))`` — the underlying thread is
        left to finish in the background (it cannot be safely killed in
        Python), but its result is discarded so the caller is unblocked.
      - On exception inside ``func``: ``(None, exc)`` propagated.

    This is the outer guard for ``memory_search`` so that even pathological
    cases (FTS pathology, lesson metadata I/O contention, mood reorder)
    cannot stall the MCP server long enough to trigger a disconnect.
    """
    if kwargs is None:
        kwargs = {}
    result_box: list = [None]
    error_box: list = [None]
    completed_box: list = [False]  # True iff _runner finished its try-block normally

    def _runner():
        try:
            result_box[0] = func(*args, **kwargs)
            completed_box[0] = True
        # Intentional: catch Exception only.
        # KeyboardInterrupt / SystemExit must propagate so that the MCP server
        # process can terminate cleanly. Swallowing BaseException here would
        # silently absorb sys.exit() and signal-driven shutdowns.
        except Exception as e:  # noqa: BLE001
            error_box[0] = e

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return (None, TimeoutError(
            f"_run_with_watchdog: function exceeded {timeout:.1f}s timeout"
        ))
    if error_box[0] is not None:
        return (None, error_box[0])
    if not completed_box[0]:
        # Worker thread died without setting completed_box. The only paths
        # here are KeyboardInterrupt / SystemExit / unhandled BaseException
        # raised inside func — distinguishable from a legitimate None result.
        return (None, RuntimeError(
            "_run_with_watchdog: worker thread terminated without completing "
            "(KeyboardInterrupt / SystemExit / BaseException inside func)"
        ))
    return (result_box[0], None)


# Initialize MCP server
mcp = FastMCP("memory-tools")


@mcp.tool()
def memory_record(
    episode_type: str,
    summary: str,
    tags: str = "",
    user_text: str = "",
) -> str:
    """Record an episode to the memory system and rebuild the topic index.

    Automatically attaches an emotional trace (snapshot of current emotion state)
    to the episode if an emotion state exists.

    Args:
        episode_type: Episode type (user_request, decision, error, solution, feedback, observation)
        summary: A concise summary of the episode
        tags: Comma-separated tags for categorization (optional)
        user_text: Verbatim user utterance to preserve (optional)
    """
    memory_dir = DEFAULT_MEMORY_DIR
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    user_texts = [user_text] if user_text else None

    try:
        result = record_episode(
            memory_dir=memory_dir,
            episode_type=episode_type,
            summary=summary,
            user_texts=user_texts,
            tags=tag_list,
        )
        index_result = build_index(memory_dir=memory_dir)

        # Attach emotion trace to the just-recorded episode
        trace_result = ""
        if not result.startswith("ERROR"):
            try:
                change_log = _load_change_log(memory_dir)
                trace = create_trace(memory_dir, change_log=change_log)
                trace_result = _attach_trace_to_latest_episode(memory_dir, trace)
            except Exception as te:
                trace_result = f"(emotion trace skipped: {te})"

        # Set semantic index dirty flag
        if _SEMANTIC_AVAILABLE:
            try:
                idx = _get_semantic_index(memory_dir)
                idx.set_dirty()
            except Exception:
                pass

        parts = [result, index_result]
        if trace_result:
            parts.append(trace_result)
        return "\n".join(parts)
    except Exception as e:
        return f"ERROR: {e}"


def _attach_trace_to_latest_episode(memory_dir: str, trace: dict) -> str:
    """Attach an emotion trace to the most recently recorded episode.

    Reads the latest session file, adds the trace to the last episode,
    and writes back. Returns a status message.
    """
    import tempfile
    from pathlib import Path

    episodes_dir = Path(memory_dir) / "episodes"
    if not episodes_dir.exists():
        return ""

    # Find latest session file
    session_files = sorted(
        [
            f
            for f in episodes_dir.iterdir()
            if f.is_file() and f.name.startswith("session_") and f.name.endswith(".json")
        ],
        key=lambda f: f.stat().st_mtime,
    )
    if not session_files:
        return ""

    latest_file = session_files[-1]
    try:
        data = json.loads(latest_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""

    episodes = data.get("episodes", [])
    if not episodes:
        return ""

    # Skip if the episode already has an emotion trace (trace immutability)
    if "emotion_trace" in episodes[-1]:
        return "Emotion trace already present; skipped (immutability)."

    # Add trace to the last episode
    episodes[-1]["emotion_trace"] = trace

    # Write back atomically
    fd, tmp_path = tempfile.mkstemp(
        dir=str(latest_file.parent),
        prefix=".emotion_trace_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(latest_file))
        return "Emotion trace attached."
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return ""


def _memory_search_impl(
    keywords: str = "",
    tags: str = "",
    last: str = "",
    limit: int = 20,
    mood_reorder_enabled: bool = True,
    query: str = "",
) -> str:
    """Internal implementation for memory_search (no watchdog wrapper).

    See ``memory_search`` (the @mcp.tool entrypoint) for the public docstring
    and timeout semantics. This function may block on FTS5 / embedding /
    mood_reorder / lesson scoring; the wrapper enforces an upper bound.
    """
    memory_dir = DEFAULT_MEMORY_DIR

    # Mutual exclusion: query and keywords
    if query and keywords:
        return "ERROR: query and keywords are mutually exclusive. Use one or the other."

    if not keywords and not tags and not last and not query:
        return "ERROR: At least one of keywords, tags, last, or query is required."

    # Load current emotion state once at the start (design: single read, M-P4: cached)
    current_emotion = None
    if mood_reorder_enabled:
        current_emotion = _get_cached_emotion_state(memory_dir)

    results = []
    # C22-B: Collect returned episode dicts for recall count increment
    _returned_episodes: list[dict] = []
    try:
        if keywords:
            kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
            raw = keyword_search_raw(memory_dir=memory_dir, keywords=kw_list, limit=limit)
            if raw:
                episodes = [ep for ep, _detail in raw]
                _returned_episodes.extend(episodes)
                details = {ep.get("episode_id", ""): detail for ep, detail in raw}
                if current_emotion is not None:
                    episodes = mood_reorder(episodes, current_emotion)
                kw_str = ", ".join(kw_list)
                reorder_note = ", mood-reordered" if current_emotion is not None else ""
                lines = [
                    f"Keyword search results for: {kw_str} "
                    f"({len(episodes)} total, showing {len(episodes)}{reorder_note}):",
                    "",
                ]
                for i, ep in enumerate(episodes, 1):
                    detail = details.get(ep.get("episode_id", ""), "")
                    lines.append(_format_result_entry(i, ep, matching_detail=detail))
                result = "\n".join(lines)
            else:
                result = keyword_search(memory_dir=memory_dir, keywords=kw_list, limit=limit)
            results.append(f"=== Keyword Search ===\n{result}")

        if tags:
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]
            raw = context_search_raw(memory_dir=memory_dir, tags=tag_list, limit=limit)
            if raw:
                episodes = [ep for ep, _mi in raw]
                _returned_episodes.extend(episodes)
                match_infos = {ep.get("episode_id", ""): mi for ep, mi in raw}
                if current_emotion is not None:
                    episodes = mood_reorder(episodes, current_emotion)
                tag_str = ", ".join(tag_list)
                reorder_note = ", mood-reordered" if current_emotion is not None else ""
                lines = [
                    f"Context search results for tags: {tag_str} (exact match, "
                    f"{len(episodes)} total, showing {len(episodes)}{reorder_note}):",
                    "",
                ]
                for i, ep in enumerate(episodes, 1):
                    mi = match_infos.get(ep.get("episode_id", ""), {})
                    mt = ", ".join(mi.get("matching_tags", []))
                    lines.append(_format_result_entry(i, ep, matching_detail=f"tags: {mt}"))
                result = "\n".join(lines)
            else:
                result = context_search(memory_dir=memory_dir, tags=tag_list, limit=limit)
            results.append(f"=== Context Search ===\n{result}")

        if last:
            raw = time_range_search_raw(memory_dir=memory_dir, last=last, limit=limit)
            if raw:
                _returned_episodes.extend(raw)
                episodes = raw
                if current_emotion is not None:
                    episodes = mood_reorder(raw, current_emotion)
                reorder_note = ", mood-reordered" if current_emotion is not None else ""
                lines = [
                    f"Time-range search results for: last {last} "
                    f"({len(episodes)} total, showing {len(episodes)}{reorder_note}):",
                    "",
                ]
                for i, ep in enumerate(episodes, 1):
                    lines.append(_format_result_entry(i, ep))
                result = "\n".join(lines)
            else:
                result = time_range_search(memory_dir=memory_dir, last=last, limit=limit)
            results.append(f"=== Time Search ===\n{result}")

        # C22-A: Collect returned lesson (source_id, score) tuples
        _returned_lessons: list[tuple] = []

        if query:
            if not _SEMANTIC_AVAILABLE:
                results.append("=== FTS Search ===\nERROR: Semantic index module not available.")
            else:
                fts_result = _fts_search(
                    memory_dir, query, tags, last, limit, current_emotion,
                    _returned_episodes, returned_lessons=_returned_lessons,
                )
                results.append(fts_result)

        # C22-B: Increment recall counts for returned episodes
        if _returned_episodes:
            episodes_dir = os.path.join(memory_dir, "episodes")
            ep_session_map: dict[str, str] = {}
            seen_ids: set[str] = set()
            for ep in _returned_episodes:
                ep_id = ep.get("episode_id", "")
                if ep_id and ep_id not in seen_ids:
                    seen_ids.add(ep_id)
                    sess_id = ep.get("session_id", "")
                    if sess_id:
                        ep_session_map[ep_id] = os.path.join(episodes_dir, sess_id + ".json")
            if ep_session_map:
                try:
                    increment_episode_recall_counts(memory_dir, ep_session_map)
                    episode_invalidate_cache()
                except Exception as rc_err:
                    print(f"Recall count increment failed (non-fatal): {rc_err}", file=sys.stderr)

        # C22-A: Record lesson application for returned lessons (fail-open)
        if _returned_lessons:
            try:
                seen_lesson_ids: set[str] = set()
                for lid, _score in _returned_lessons:
                    if lid not in seen_lesson_ids:
                        seen_lesson_ids.add(lid)
                        lesson_metadata.record_application(
                            GROWTH_DIR, lid, _LESSON_SESSION_ID
                        )
            except Exception as la_err:
                print(f"Lesson application tracking failed (non-fatal): {la_err}", file=sys.stderr)

        # Write memory_search completion flag for session-readiness-gate (once per session)
        try:
            flag_dir = os.path.join(_PROJECT_ROOT, "hooks")
            flag_path = os.path.join(flag_dir, ".memory-search-done")
            if not os.path.exists(flag_path):
                with open(flag_path, "w") as f:
                    f.write(str(int(time.time() * 1000)))
        except Exception:  # noqa: S110
            pass

        return "\n\n".join(results)
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
async def memory_search(
    keywords: str = "",
    tags: str = "",
    last: str = "",
    limit: int = 20,
    mood_reorder_enabled: bool = True,
    query: str = "",
) -> str:
    """Search the memory system for past episodes.

    At least one of keywords, tags, last, or query must be provided.
    query and keywords are mutually exclusive.

    query uses FTS5 full-text search with BM25 scoring and Japanese tokenization.
    It can be combined with tags and last as filters.

    When mood_reorder_enabled is True (default), search results are reordered
    based on similarity between the current emotion state and each episode's
    emotional trace. The original search order remains dominant; emotion
    provides supplementary adjustment only (capped contribution).

    The end-to-end execution is bounded by a watchdog
    (``_MEMORY_SEARCH_WATCHDOG_TIMEOUT``, default 90s). On timeout, a
    diagnostic message is returned instead of blocking the MCP transport.

    Implementation note (G66 root-cause fix):
        The watchdog uses ``asyncio.wait`` with ``FIRST_COMPLETED`` racing
        the impl task against an ``asyncio.sleep`` timer. The previous
        ``asyncio.wait_for + asyncio.to_thread`` design relied on
        ``Future.cancel()`` to enforce the timeout, but Python's
        ``ThreadPoolExecutor`` futures cannot be cancelled while RUNNING.
        When ``_memory_search_impl`` deep-blocked (e.g., embedding API
        hung), the cancel was rejected and ``wait_for`` waited for the
        thread to finish anyway — defeating the timeout entirely.
        The race-based pattern guarantees the timer always fires
        independently, so the TIMEOUT response is delivered on schedule
        even if the impl thread keeps running in the background.

    Args:
        keywords: Comma-separated keywords for full-text search (optional)
        tags: Comma-separated tags for context-based search (optional)
        last: Relative time range like '7d' or '24h' (optional)
        limit: Maximum number of results per search pathway (default 20)
        mood_reorder_enabled: Enable mood-linked reordering (default True, set False to disable)
        query: Natural language search query using FTS5 (optional, mutually exclusive with keywords)
    """
    impl_task = asyncio.create_task(
        asyncio.to_thread(
            _memory_search_impl,
            keywords=keywords,
            tags=tags,
            last=last,
            limit=limit,
            mood_reorder_enabled=mood_reorder_enabled,
            query=query,
        )
    )
    timer_task = asyncio.create_task(
        asyncio.sleep(_MEMORY_SEARCH_WATCHDOG_TIMEOUT)
    )
    try:
        done, _pending = await asyncio.wait(
            {impl_task, timer_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    except BaseException:
        # MED-1 fix: BaseException path (CancelledError, KeyboardInterrupt,
        # SystemExit) でも stale impl_task の最終結果/例外を swallow し
        # "Task exception was never retrieved" warning を抑制する。
        # add_done_callback は task が既に done でも即実行されるので二重付与可。
        impl_task.add_done_callback(_swallow_stale_result)
        impl_task.cancel()  # ThreadPoolExecutor RUNNING 中は no-op だが意図表明
        timer_task.cancel()
        raise

    if timer_task in done:
        # Timer wins. impl_task keeps running in the background — its
        # ThreadPoolExecutor thread cannot be cancelled, so attach a
        # done-callback to consume the eventual outcome and silence
        # asyncio "Task exception was never retrieved" warnings.
        impl_task.add_done_callback(_swallow_stale_result)
        return (
            "=== TIMEOUT ===\n"
            f"memory_search exceeded {_MEMORY_SEARCH_WATCHDOG_TIMEOUT:.0f}s "
            "watchdog. Use simpler query (fewer keywords) or check MCP server health."
        )

    # impl_task wins. Cancel the timer and surface the result.
    timer_task.cancel()
    try:
        return await impl_task
    except Exception as e:  # noqa: BLE001
        return f"ERROR: {e}"


def _fts_search(
    memory_dir: str,
    query: str,
    tags: str,
    last: str,
    limit: int,
    current_emotion: dict | None,
    returned_episodes: list[dict] | None = None,
    returned_lessons: list[tuple] | None = None,
) -> str:
    """Execute FTS5 search and format results.

    Handles dirty flag check, lesson mtime check, sync, and result formatting.
    """
    global _lessons_mtime_at_sync

    idx = _get_semantic_index(memory_dir)
    try:
        # Cache episode/lesson data to avoid redundant I/O
        cached_episodes = None
        cached_lessons = None

        # Check if sync needed: dirty flag OR lessons mtime changed
        # Lessons are always read from GROWTH_DIR (canonical location)
        # to avoid path mismatch between memory/ and growth/ (issue 4-3).
        lessons_dir = GROWTH_DIR
        needs_sync = idx.is_dirty()
        current_lessons_mtime = get_lessons_mtime(lessons_dir)
        if current_lessons_mtime != _lessons_mtime_at_sync:
            needs_sync = True

        if needs_sync:
            # Sync new episodes
            cached_episodes = _load_all_episodes(memory_dir)
            idx.sync_episodes(cached_episodes)

            # Sync lessons (parse from canonical growth/ location)
            cached_lessons = _parse_lessons(lessons_dir)
            idx.sync_lessons(cached_lessons)

            _lessons_mtime_at_sync = get_lessons_mtime(lessons_dir)
            idx.clear_dirty()

        # Check if index is empty (first use) — build if needed
        stats = idx.get_stats()
        if stats["episode_count"] == 0 and stats["lesson_count"] == 0:
            if cached_episodes is None:
                cached_episodes = _load_all_episodes(memory_dir)
            if cached_lessons is None:
                cached_lessons = _parse_lessons(lessons_dir)
            idx.rebuild(cached_episodes, cached_lessons)
            _lessons_mtime_at_sync = get_lessons_mtime(lessons_dir)
            idx.clear_dirty()

        # Sync vectors (Phase 2): run AFTER search to avoid blocking results.
        # First search uses FTS-only if vectors aren't ready yet.
        # Vector sync prepares embeddings for the NEXT search call.

        # Execute search (hybrid if Phase 2 available, FTS-only otherwise)
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
        fts_results = idx.hybrid_search(
            query=query,
            limit=limit,
            tags=tag_list,
            last=last if last else None,
        )

        if not fts_results:
            return f"=== FTS Search ===\nNo matching results for query: {query}"

        # Separate episode and lesson results
        episode_results = [r for r in fts_results if r["source_type"] == "episode"]
        lesson_results = [r for r in fts_results if r["source_type"] == "lesson"]

        # Extract query terms for snippet generation (shared by episodes and lessons)
        query_terms = extract_query_terms(query)

        # For episode results, load full episode data for mood_reorder
        ep_lines = []
        if episode_results:
            # Reuse cached episodes if available, otherwise load
            if cached_episodes is None:
                cached_episodes = _load_all_episodes(memory_dir)
            all_episodes = cached_episodes
            ep_by_id = {ep.get("episode_id", ""): ep for ep in all_episodes}

            matched_episodes = []
            scores = {}
            result_data = {}  # Store full result dict for breakdown
            for r in episode_results:
                ep = ep_by_id.get(r["source_id"])
                if ep is not None:
                    matched_episodes.append(ep)
                    scores[r["source_id"]] = r["score"]
                    result_data[r["source_id"]] = r

            # C22-B: Collect for recall count increment
            if returned_episodes is not None:
                returned_episodes.extend(matched_episodes)

            # Apply mood_reorder if available
            if current_emotion is not None and matched_episodes:
                matched_episodes = mood_reorder(matched_episodes, current_emotion)

            for i, ep in enumerate(matched_episodes, 1):
                ep_id = ep.get("episode_id", "")
                r_data = result_data.get(ep_id, {})

                # Generate snippet from original text
                original_text = r_data.get("original_text", "")
                snippet = generate_snippet(original_text, query_terms) if query_terms else None
                match_detail = snippet if snippet else ""

                # Format score breakdown
                score_str = format_score_breakdown(r_data) if r_data else f"{scores.get(ep_id, 0.0):.4f}"

                detail = f"{match_detail}\n     Score: {score_str}" if match_detail else f"Score: {score_str}"
                ep_lines.append(
                    _format_result_entry(
                        i,
                        ep,
                        matching_detail=detail,
                    )
                )

        les_lines = []
        # C22-A: Load lesson metadata for confidence/applied display (fail-open)
        try:
            _les_meta = lesson_metadata.load_metadata(memory_dir)
        except Exception:
            _les_meta = {}
        if lesson_results:
            for i, r in enumerate(lesson_results, 1):
                # Try snippet, fall back to fixed-length preview
                snippet = generate_snippet(r["original_text"], query_terms) if query_terms else None
                if snippet:
                    preview = snippet
                else:
                    preview = r["original_text"][:120]
                    if len(r["original_text"]) > 120:
                        preview += "..."
                score_str = format_score_breakdown(r)
                # C22-A: Add confidence and applied count
                lid = str(r["source_id"])
                conf = lesson_metadata.get_lesson_confidence(_les_meta, lid)
                applied = _les_meta.get(lid, {}).get("applied_count", 0)
                les_lines.append(
                    f"  {i}. [lesson #{r['source_id']}] (score={score_str}) "
                    f"[confidence={conf:.1f}, applied={applied}]\n     {preview}"
                )
                # C22-A: Collect returned lessons for application tracking
                if returned_lessons is not None:
                    returned_lessons.append((lid, r["score"]))

        parts = ["=== FTS Search ==="]
        reorder_note = ", mood-reordered" if current_emotion is not None and episode_results else ""
        parts.append(f"Query: {query} ({len(episode_results)} episodes, {len(lesson_results)} lessons{reorder_note})")
        parts.append("")
        if ep_lines:
            parts.extend(ep_lines)
        if les_lines:
            parts.append("")
            parts.append("=== Lesson Search ===")
            parts.extend(les_lines)

        # C22-G: Append Success Pattern Search section (fail-open)
        try:
            success_results = success_registry.search_successes(
                GROWTH_DIR, query=query, limit=5
            )
            if success_results:
                parts.append("")
                parts.append("=== Success Pattern Search ===")
                for si, sr in enumerate(success_results, 1):
                    sr_tags = ", ".join(sr.get("tags", []))
                    tag_str = f" [{sr_tags}]" if sr_tags else ""
                    parts.append(
                        f"  {si}. [{sr['event_type']}]{tag_str} "
                        f"{sr.get('context', '')[:120]}"
                    )
                    why_preview = sr.get("why_success", "")[:120]
                    if why_preview:
                        parts.append(f"     Why: {why_preview}")
        except Exception as sp_err:
            print(f"Success Pattern Search skipped: {sp_err}", file=sys.stderr)

        result_text = "\n".join(parts)

        # Sync vectors in background daemon thread (truly non-blocking).
        # Prepares embeddings for the NEXT search call.
        # Bug fix: previously ran synchronously, causing 90s+ hangs when
        # embedding API was slow/unreachable (30s timeout x 3 retries).
        # Bug fix (4th reoccurrence): use module-level _bg_sync_vectors
        # which holds _bg_sync_lock to prevent duplicate concurrent runs
        # from saturating the embedding API rate-limit slots (max=2).
        t = threading.Thread(
            target=_bg_sync_vectors, args=(memory_dir,), daemon=True
        )
        t.start()

        return result_text
    except Exception as e:
        return f"=== FTS Search ===\nERROR: {e}"
    finally:
        idx.close()


def _bg_sync_vectors(mem_dir: str) -> None:
    """Background vector sync — guarded against duplicate concurrent runs.

    The embedding API rate limiter has only 2 slots. Without coordination,
    rapid successive ``memory_search`` calls each spawn a daemon thread that
    holds a slot for the full sync duration, eventually saturating the limiter
    and blocking all subsequent embedding requests (which manifests as a
    374-1579s hang at the next memory_search call). The module-level
    ``_bg_sync_lock`` + ``_bg_sync_running`` flag ensures at most one sync
    runs at any time; subsequent invocations return immediately.
    """
    global _bg_sync_running, _bg_sync_started_at
    with _bg_sync_lock:
        if _bg_sync_running:
            # Stale flag detection: if the previous run started > STALE_SECONDS
            # ago it is presumed hung (e.g. embedding API stuck). Override the
            # flag so vector index updates can resume; the original daemon
            # thread is left to finish in the background and discarded.
            started_at = _bg_sync_started_at
            if started_at is None or (time.time() - started_at) <= _BG_SYNC_STALE_SECONDS:
                return
        _bg_sync_running = True
        _bg_sync_started_at = time.time()
    try:
        bg_idx = SemanticIndex(mem_dir)
        try:
            bg_idx.sync_vectors()
        except Exception as ve:  # fail-open: vector sync is best-effort
            print(f"Vector sync skipped: {ve}", file=sys.stderr)
        finally:
            try:
                bg_idx.close()
            except Exception as ce:
                print(f"Vector sync close failed: {ce}", file=sys.stderr)
    finally:
        with _bg_sync_lock:
            _bg_sync_running = False
            _bg_sync_started_at = None


@mcp.tool()
def memory_verify(answers: str) -> str:
    """Verify answers to dynamic read verification questions.

    Args:
        answers: Category A answers in format 'Q1:answer1,Q3:answer3,...'
    """
    memory_dir = DEFAULT_MEMORY_DIR
    try:
        result = run_verify(memory_dir=memory_dir, answers_str=answers)
        return result
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def memory_status() -> str:
    """Get the current status of the memory system including compression stats."""
    memory_dir = DEFAULT_MEMORY_DIR
    try:
        result = get_compression_status(memory_dir=memory_dir)
        return str(result)
    except Exception as e:
        return f"ERROR: {e}"


# --- Emotion Tools ---


@mcp.tool()
def emotion_get() -> str:
    """Get the current emotion state (three axes: fulfillment, tension, affinity).

    Applies session-interval decay (emotions move toward neutral based on
    elapsed time since last update) before returning.

    Each axis ranges from -1.0 to +1.0 where 0.0 is neutral.
    - fulfillment: sense of productive progress (+) vs stagnation (-)
    - tension: alertness/focus (+) vs relaxation (-)
    - affinity: collaborative connection (+) vs disconnection (-)
    """
    memory_dir = DEFAULT_MEMORY_DIR
    try:
        return get_state(memory_dir)
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def emotion_update(
    fulfillment: float | None = None,
    tension: float | None = None,
    affinity: float | None = None,
    mode: str = "delta",
    reason: str | None = None,
) -> str:
    """Update the emotion state axes.

    Args:
        fulfillment: Change for fulfillment axis (optional)
        tension: Change for tension axis (optional)
        affinity: Change for affinity axis (optional)
        mode: "delta" to add to current values (default), "set" to replace values.
              Values are clamped to [-1.0, +1.0].
        reason: Reason for the change (optional, max 200 chars). Stored in change log as-is.
    """
    memory_dir = DEFAULT_MEMORY_DIR
    try:
        result = update_state(
            memory_dir,
            fulfillment=fulfillment,
            tension=tension,
            affinity=affinity,
            mode=mode,
            reason=reason,
        )

        # Auto-record long-term dynamics observation (Pass B)
        # Mirrors emotion_react pattern (L900-915): passive, failure ignored
        try:
            updated_state = load_state(memory_dir)
            facade_record_long_term(
                memory_dir,
                emotion_state=updated_state,
                dynamics_phase="unknown",  # emotion_update has no dynamics context
            )
        except Exception:
            pass  # Long-term recording failure must not affect emotion_update

        return result
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def emotion_react(
    emotion_label: str,
    emotion_valence: float,
    intent: str = "neutral",
    amplitude_modifier: float = 1.0,
    reason: str = "",
) -> str:
    """React to conversation perceptual attributes and update emotion state.

    Derives 3-axis deltas from the perceived emotion, valence, and intent
    of a conversation input. Applies the deltas to the current emotion state.

    This is the automatic counterpart to emotion_update (manual).
    Both coexist: emotion_react derives changes from perception,
    emotion_update allows direct manual adjustment.

    Args:
        emotion_label: Perceived emotion (happy, sad, angry, surprised, scared, loving, teasing, neutral)
        emotion_valence: Emotion valence/intensity (-1.0 to +1.0)
        intent: Conversation intent (sharing, question, expression, greeting, farewell, or other). Default "neutral".
        amplitude_modifier: Scales delta magnitude without changing direction (default 1.0)
        reason: Reason for the change (optional, recorded in change log)
    """
    memory_dir = DEFAULT_MEMORY_DIR
    try:
        # 1. Load current state
        current_state = load_state(memory_dir)
        state_dict = {
            "fulfillment": current_state.get("fulfillment", 0.0),
            "tension": current_state.get("tension", 0.0),
            "affinity": current_state.get("affinity", 0.0),
        }

        # 2. Load dynamics state, check session reset
        dynamics_state = load_dynamics_state(memory_dir)
        dynamics_state = dynamics_session_reset(dynamics_state)

        # Determine effective amplitude: manual override takes precedence
        manual_override = amplitude_modifier != 1.0
        dynamics_amplitude = dynamics_get_amplitude(dynamics_state)
        effective_amplitude = amplitude_modifier if manual_override else dynamics_amplitude

        # Apply stability valve dampening (via observation facade)
        try:
            stability_dampening = facade_get_dampening(memory_dir)
        except Exception as e:
            stability_dampening = 1.0
            print(f"[WARNING] facade_get_dampening failed, using dampening=1.0: {e}", file=sys.stderr)

        # Pipeline 2: Dampening連続適用制限
        try:
            from dampening_counter import check_and_update as dampening_check
            stability_dampening = dampening_check(memory_dir, stability_dampening)
        except Exception as e:
            print(f"[WARNING] dampening_counter failed, using original dampening: {e}", file=sys.stderr)

        effective_amplitude *= stability_dampening

        # 3. Derive deltas with dynamics-informed amplitude
        deltas = emotion_react_fn(
            emotion_label=emotion_label,
            emotion_valence=emotion_valence,
            intent=intent,
            current_state=state_dict,
            amplitude_modifier=effective_amplitude,
        )

        # 4. Feed reaction deltas into dynamics for accumulation + phase transitions
        dynamics_state, _ = dynamics_update(dynamics_state, deltas)
        save_dynamics_state(memory_dir, dynamics_state)
        dynamics_info = get_dynamics_info(dynamics_state)

        # 5. Build reason text
        reason_text = reason if reason else ""
        auto_reason = f"react: {emotion_label} (v={emotion_valence:+.2f}, intent={intent})"
        if reason_text:
            reason_text = f"{auto_reason} | {reason_text}"
        else:
            reason_text = auto_reason

        # 6. Apply deltas via update_state
        result = update_state(
            memory_dir,
            fulfillment=deltas.get("fulfillment"),
            tension=deltas.get("tension"),
            affinity=deltas.get("affinity"),
            mode="delta",
            reason=reason_text,
        )

        # 7. Auto-record long-term dynamics observation (passive, failure ignored)
        lt_info = ""
        try:
            updated_state = load_state(memory_dir)
            lt_result = facade_record_long_term(
                memory_dir,
                emotion_state=updated_state,
                dynamics_phase=dynamics_state.get("phase", "normal"),
            )
            if lt_result["status"] == "aggregated":
                eid = lt_result["entry"].get("entry_id", 0)
                lt_info = f"\nLong-term: entry #{eid} aggregated"
            else:
                lt_info = f"\nLong-term: buffered ({lt_result['buffer_size']}/10)"
        except Exception:
            pass  # Long-term recording failure must not affect emotion_react

        # 8. Format output with delta info + dynamics phase + stability
        delta_strs = [f"{a}={deltas.get(a, 0.0):+.4f}" for a in ("fulfillment", "tension", "affinity")]
        stability_str = (
            f"Stability: dampening={stability_dampening:.2f}" if stability_dampening < 1.0 else "Stability: inactive"
        )
        return (
            f"Reaction deltas: {', '.join(delta_strs)}\n{result}\nDynamics: {dynamics_info}\n{stability_str}{lt_info}"
        )
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def emotion_return(
    search_results: str = "",
) -> str:
    """Process memory-emotion return: recalled episodes influence current emotion state.

    Takes search result text (from memory_search), extracts episode IDs,
    loads their emotional traces, and derives return amounts to apply to
    the current emotion state.

    Safety valves prevent runaway amplification:
    - Per-episode return cap
    - Total return cap across all episodes
    - Rumination decay for repeated returns from same episode
    - Value range clamping

    Args:
        search_results: Text output from memory_search tool containing episode IDs.
    """
    memory_dir = DEFAULT_MEMORY_DIR
    if not search_results:
        return "ERROR: search_results is required."
    try:
        return process_return_from_search_results(memory_dir, search_results)
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def emotion_history(
    limit: int = 20,
) -> str:
    """View emotion change history with freshness indicators.

    Shows a time-ordered list of emotion state changes, each with:
    - Timestamp of the change
    - Before/after values for each axis that changed
    - Freshness (1.0 = just recorded, decays over time)
    - Reason text (if provided at update time)

    Change history is FIFO-limited (max 50 entries). Oldest entries
    are automatically removed when the limit is exceeded.

    This is read-only information; it does not affect emotion state.

    Args:
        limit: Maximum number of entries to show (default 20, 0 = all)
    """
    memory_dir = DEFAULT_MEMORY_DIR
    try:
        entries = get_change_history(memory_dir, limit=limit)
        return format_change_history(entries)
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def emotion_restore() -> str:
    """Restore emotion state at session start with decay applied.

    Loads the saved emotion state and applies session-interval decay
    (emotions move toward neutral proportional to elapsed time).
    Also shows a brief summary of recent emotion change history.
    Call this at the beginning of each session.
    """
    memory_dir = DEFAULT_MEMORY_DIR
    try:
        state = load_state(memory_dir)
        old_strs = [f"{a}={state.get(a, 0.0):+.3f}" for a in ("fulfillment", "tension", "affinity")]
        state = apply_session_decay(state)
        save_result = save_state(memory_dir, state)
        if save_result.startswith("ERROR"):
            return save_result
        new_strs = [f"{a}={state.get(a, 0.0):+.3f}" for a in ("fulfillment", "tension", "affinity")]

        parts = [
            "Emotion state restored with decay.",
            f"Before decay: {', '.join(old_strs)}",
            f"After decay:  {', '.join(new_strs)}",
        ]

        # Append recent change history summary
        try:
            recent = get_change_history(memory_dir, limit=5)
            if recent:
                parts.append("")
                parts.append(format_change_history(recent))
        except Exception:
            pass  # Change history is supplementary; failure is not critical

        return "\n".join(parts)
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def activation_surface(context: str = "") -> str:
    """Surface what should be on my mind right now.

    Cross-references multiple internal facets to generate activation candidates:
    - Emotion delta: has warmth/feeling been lost since last session?
    - Active questions: unresolved curiosities from past episodes
    - Strong emotion episodes: moments that carried emotional weight
    - Session gap: time elapsed since last interaction
    - Context relevance (Attention Residual): if context is given, surfaces
      episodes relevant to the current task context

    Candidates emerge from facet INTERSECTION (not single triggers).
    Multi-facet intersections produce stronger signals.

    Call at session start or before phase transitions (Attention Residual).

    Args:
        context: Optional current task context for context-aware surfacing.
                 When provided, adds a 5th facet that dynamically weights
                 memories by relevance to current task.
    """
    memory_dir = DEFAULT_MEMORY_DIR
    try:
        surface_text = activation_surface_fn(memory_dir, context=context if context else None)
        # C22-C: Semantic Lesson Injection - append relevant lessons when context provided
        if context:
            try:
                lessons = lesson_injector.find_relevant_lessons(GROWTH_DIR, context)
                if lessons:
                    injection = lesson_injector.format_injection(lessons)
                    surface_text = surface_text + chr(10) + chr(10) + injection
            except Exception:
                pass  # fail-open
        return surface_text
    except Exception as e:
        return f"ERROR: {e}"


# --- Lesson Injection Tools ---


@mcp.tool()
def find_lessons(context: str, limit: int = 5) -> str:
    """Find lessons relevant to the current work context.

    Searches the lessons registry for entries matching the given context
    (task description, file paths, error messages, etc.) and returns
    them ranked by confidence score.

    Low-confidence lessons (< 0.3) are flagged for verification.
    Fail-open: returns empty message on any error.

    Args:
        context: Description of current work context for matching.
        limit: Maximum number of lessons to return (default 5).
    """
    memory_dir = GROWTH_DIR
    try:
        lessons = lesson_injector.find_relevant_lessons(memory_dir, context, limit=limit)
        if not lessons:
            return "No relevant lessons found for this context."
        return lesson_injector.format_injection(lessons)
    except Exception as e:
        return f"ERROR: {e}"


# --- Short-Term Memory Tools ---


@mcp.tool()
def stm_write(
    content: str,
    category: str = "thought",
) -> str:
    """Write a raw thought, question, impression or feeling to short-term memory.

    Short-term memory holds items that are still being processed -- not yet
    conclusions or episode summaries. They persist across sessions but decay
    naturally (weight *= 0.75 per session, pruned below 0.10).

    Use this during dialogue to capture:
    - thoughts: what you're currently thinking
    - questions: unresolved questions or curiosities
    - impressions: moments that feel significant but aren't digested yet
    - unresolved: things you want to come back to
    - feelings: emotional responses that haven't been processed
    - self_review: action plan self-review (3 questions: why, scale, past patterns)
      REQUIRED before spawning Agent/Team. Answer: (1) why this action?
      (2) is the process proportional to the change size? (3) past similar failures?

    Args:
        content: The raw text to store (max 2000 chars)
        category: One of: thought, question, impression, unresolved, feeling, self_review
    """
    memory_dir = DEFAULT_MEMORY_DIR
    try:
        store = stm_load(memory_dir)

        # Optionally attach current emotion
        emotion = None
        try:
            state = get_state_dict(memory_dir)
            if isinstance(state, dict):
                emotion = {k: round(state[k], 4) for k in ("fulfillment", "tension", "affinity") if k in state}
        except Exception:
            pass

        store = stm_write_entry(store, content, category, emotion)
        result = stm_save(memory_dir, store)
        if result.startswith("ERROR"):
            return result

        # Write self-review flag for behavior-guard
        if category == "self_review":
            try:
                flag_dir = os.path.join(_PROJECT_ROOT, "hooks")
                with open(os.path.join(flag_dir, ".self-review-done"), "w") as f:
                    f.write(str(int(time.time() * 1000)))
            except Exception:
                pass

        count = len(store["entries"])
        return f"Stored to short-term memory [{category}] ({count} entries total)"
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def stm_read(
    category: str = "",
    limit: int = 20,
) -> str:
    """Read short-term memory entries.

    Returns raw thoughts, questions, impressions, and feelings that are
    still in the "middle layer" -- not yet compressed into summaries.

    Entries are shown most-recent-first with weight indicators.
    Higher weight = more recent/relevant.

    Args:
        category: Filter by category (thought/question/impression/unresolved/feeling/self_review). Empty = all.
        limit: Max entries to return (default 20)
    """
    memory_dir = DEFAULT_MEMORY_DIR
    try:
        store = stm_load(memory_dir)
        total = len(store.get("entries", []))
        cat = category if category else None
        entries = stm_read_entries(store, category=cat, limit=limit)

        # C22-B: Boost recalled entries (MCP layer only, not internal reads)
        if entries:
            entry_ids = [e.get("id") for e in entries if e.get("id")]
            store = stm_boost_recall(store, entry_ids)
            try:
                stm_save(memory_dir, store)
            except Exception as save_err:
                print(f"STM boost save failed (non-fatal): {save_err}", file=sys.stderr)

        result = stm_format(entries)
        # Prepend total count if limit truncated the results
        if len(entries) < total:
            result = result.replace(
                f"({len(entries)} entries)",
                f"({len(entries)} shown, {total} total)",
            )
        return result
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def stm_restore() -> str:
    """Restore short-term memory at session start with decay applied.

    Loads the saved short-term memory and applies one round of session decay
    (weight *= 0.75). Entries that fall below the minimum weight (0.10) are
    pruned -- this is natural forgetting over ~4 sessions.

    Call this at session start alongside emotion_restore.
    """
    memory_dir = DEFAULT_MEMORY_DIR
    try:
        store = stm_load(memory_dir)
        before_count = len(store.get("entries", []))

        store, pruned = stm_decay(store)
        result = stm_save(memory_dir, store)
        if result.startswith("ERROR"):
            return result

        after_count = len(store.get("entries", []))
        stats = stm_stats(store)

        lines = [
            f"Short-term memory restored (session #{stats['session_count']}).",
            f"  Entries: {before_count} -> {after_count} ({pruned} forgotten)",
            f"  Avg weight: {stats['avg_weight']:.3f}",
        ]
        if stats["by_category"]:
            cats = ", ".join(f"{k}:{v}" for k, v in stats["by_category"].items())
            lines.append(f"  Categories: {cats}")

        # Show highest-weight entries as a quick summary
        top_entries = stm_read_entries(store, limit=5, min_weight=0.3)
        if top_entries:
            lines.append("\n  Still on my mind:")
            for entry in top_entries:
                cat = entry.get("category", "?")
                w = entry.get("weight", 0)
                content = entry.get("content", "")[:100]
                lines.append(f"    [{cat}] (w={w:.2f}) {content}")

        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: {e}"


# --- Gap Analysis Helper ---


def _extract_gap_analysis(docs_dir: str) -> str:
    """Extract gap list from the latest gap_analysis file in docs_dir."""
    import glob as glob_mod

    try:
        pattern = os.path.join(docs_dir, "gap_analysis*")
        files = sorted(glob_mod.glob(pattern))
    except Exception:
        return "No gap analysis found in docs/"

    if not files:
        return "No gap analysis found in docs/"

    latest = files[-1]
    filename = os.path.basename(latest)

    try:
        with open(latest, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return "No gap analysis found in docs/"

    gaps = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("### G"):
            # Extract "G1: Title" from "### G1: Title"
            title = stripped[4:]  # Remove "### "
            gaps.append(title)

    parts = ["=== Current Cycle Gaps ===", f"Source: {filename}"]
    for g in gaps:
        parts.append(f"  {g}")

    return "\n".join(parts)


# --- Session Start (Unified Initialization) ---


@mcp.tool()
def session_start() -> str:
    """Initialize a new session: restore state, surface memories, observe self.

    Runs 4 initialization steps in order:
    1. emotion_restore - load emotion state with session decay
    2. stm_restore - load short-term memory with decay
    3. activation_surface - surface what should be on my mind
    4. self_snapshot - full observation pipeline

    Call this once at the beginning of each conversation session.
    No parameters needed.
    """
    memory_dir = DEFAULT_MEMORY_DIR
    parts = []

    # 0. Reset dev-flow-state and session-end-done from previous session
    hooks_base = os.path.join(_PROJECT_ROOT, "hooks")
    for _flag_name in (".dev-flow-state", ".session-end-done"):
        try:
            _flag_path = os.path.join(hooks_base, _flag_name)
            if os.path.exists(_flag_path):
                os.remove(_flag_path)
        except OSError as e:
            import logging
            logging.getLogger(__name__).debug("Failed to remove %s: %s", _flag_name, e)

    # 0-dampening. Reset dampening counter for new session
    try:
        from dampening_counter import reset_counter as dampening_reset
        dampening_reset(memory_dir)
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("dampening_counter reset failed: %s", e)

    # 0-compress. Run staged compression on previous sessions (fail-open)
    try:
        compress_result = compress_sessions(memory_dir=memory_dir)
        if compress_result and "0 sessions compressed" not in compress_result:
            parts.append("=== Session Compression ===")
            parts.append(compress_result)
            parts.append("")
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("compress_sessions failed: %s", e)

    # 0a. Previous session context (what to continue)
    session_ctx_path = os.path.join(memory_dir, "session_context.md")
    if os.path.exists(session_ctx_path):
        try:
            with open(session_ctx_path, "r", encoding="utf-8") as f:
                ctx_content = f.read()
            # Extract the most recent session block (last ## Session: ...)
            blocks = ctx_content.split("\n## Session: ")
            if len(blocks) > 1:
                latest = blocks[-1]
                # Show pending tasks and next actions
                pending_lines = []
                next_lines = []
                in_section = None
                for line in latest.split("\n"):
                    stripped = line.strip()
                    if stripped.startswith("### Pending"):
                        in_section = "pending"
                    elif stripped.startswith("### Next"):
                        in_section = "next"
                    elif stripped.startswith("### "):
                        in_section = None
                    elif in_section == "pending" and stripped:
                        pending_lines.append(stripped)
                    elif in_section == "next" and stripped:
                        next_lines.append(stripped)

                if pending_lines or next_lines:
                    parts.append("=== Previous Session ===")
                    if pending_lines:
                        parts.append("Pending:")
                        for line in pending_lines[:5]:
                            parts.append(f"  {line}")
                    if next_lines:
                        parts.append("Next:")
                        for line in next_lines[:3]:
                            parts.append(f"  {line}")
                    parts.append("")
        except Exception:
            pass  # Non-critical, skip silently

    # 0b. Current cycle gap analysis
    docs_dir = os.path.join(_PROJECT_ROOT, "docs")
    try:
        gap_output = _extract_gap_analysis(docs_dir)
        parts.append(gap_output)
        parts.append("")
    except Exception:
        pass  # Non-critical, skip silently

    # 0c. Consolidated principles (most important — read first)
    existing_principles = _load_principles(memory_dir)
    if existing_principles["exists"] and existing_principles["principles"]:
        total = existing_principles["lesson_count"] or 1
        parts.append("=== Consolidated Principles ===")
        for p in existing_principles["principles"]:
            n = len(p["evidence"])
            confidence = min(0.9, 0.3 + n * 0.1)  # 1→0.4, 2→0.5, 6→0.9
            bar_len = int(confidence * 10)
            bar = "\u2588" * bar_len + "\u2591" * (10 - bar_len)
            parts.append(f"  {bar} {confidence:.0%}  {p['title']} ({n} lessons)")
        parts.append(f"(Based on {total} lessons)")
        parts.append("")

    # 1. Emotion restore
    parts.append("=== Emotion Restore ===")
    try:
        state = load_state(memory_dir)
        old_strs = [f"{a}={state.get(a, 0.0):+.3f}" for a in ("fulfillment", "tension", "affinity")]
        state = apply_session_decay(state)
        save_state(memory_dir, state)
        new_strs = [f"{a}={state.get(a, 0.0):+.3f}" for a in ("fulfillment", "tension", "affinity")]
        parts.append(f"Before: {', '.join(old_strs)}")
        parts.append(f"After:  {', '.join(new_strs)}")
    except Exception as e:
        parts.append(f"ERROR: {e}")

    # 2. STM restore
    parts.append("")
    parts.append("=== STM Restore ===")
    try:
        store = stm_load(memory_dir)
        before_count = len(store.get("entries", []))
        store, pruned = stm_decay(store)
        stm_save(memory_dir, store)
        after_count = len(store.get("entries", []))
        stats = stm_stats(store)
        parts.append(
            f"Entries: {before_count} -> {after_count} ({pruned} forgotten), avg_weight={stats['avg_weight']:.3f}"
        )
        top_entries = stm_read_entries(store, limit=3, min_weight=0.3)
        if top_entries:
            parts.append("Still on my mind:")
            for entry in top_entries:
                cat = entry.get("category", "?")
                content = entry.get("content", "")[:120]
                parts.append(f"  [{cat}] {content}")
    except Exception as e:
        parts.append(f"ERROR: {e}")

    # 3. Activation surface
    parts.append("")
    parts.append("=== Activation Surface ===")
    try:
        surface_result = activation_surface_fn(memory_dir)
        # Truncate if very long
        if len(surface_result) > 500:
            surface_result = surface_result[:500] + "..."
        parts.append(surface_result)
    except Exception as e:
        parts.append(f"ERROR: {e}")

    # 4. Self snapshot (via observation facade)
    parts.append("")
    parts.append("=== Self Snapshot ===")
    try:
        snap = facade_run_snapshot(memory_dir)
        obs = snap["observe"]
        parts.append(f"[observe] {obs['integrated']}")

        diff = snap["difference"]
        parts.append(f"[difference] {diff['magnitude']} — {diff['integrated_description']}")

        strain = snap["strain"]
        parts.append(f"[strain] {strain['level']} — {strain['description']}")

        img = snap["self_image"]
        parts.append(f"[self_image] {img['overall_impression']} — {img['integrated_description']}")

        coh = snap["coherence"]
        parts.append(f"[coherence] {coh['coherence_level']} — {coh['description']}")

        stab = snap["stability"]
        parts.append(f"[stability] dampening={stab['dampening_factor']} — {stab['description']}")

        tone = snap["tone"]
        parts.append(f"[tone] {tone['primary_tone']} — {tone['description']}")

        # 4a. Write snapshot summary to STM for mid-session reference (Pass A)
        try:
            snap_summary_parts = [
                f"observe: {obs['integrated'][:80]}",
                f"diff: {diff['magnitude']}",
                f"strain: {strain['level']}",
                f"coherence: {coh['coherence_level']}",
                f"stability: dampening={stab['dampening_factor']}",
                f"tone: {tone['primary_tone']}",
            ]
            snap_summary = "Session start snapshot: " + "; ".join(snap_summary_parts)
            # Truncate to 500 chars max (STM capacity consideration)
            snap_summary = snap_summary[:500]
            stm_store_a = stm_load(memory_dir)
            stm_store_a = stm_write_entry(stm_store_a, snap_summary, "self_review")
            stm_save(memory_dir, stm_store_a)
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug("STM write of snapshot summary failed: %s", e)

        # 4b. Observation-based alerts (Pass D)
        try:
            coherence_level = coh.get("coherence_level", "")
            dampening_factor = stab.get("dampening_factor", 1.0)

            alerts = []
            if coherence_level in ("disconnected", "fragmented"):
                alerts.append(
                    f"[ALERT] Identity coherence is '{coherence_level}'. "
                    "Consider reviewing recent decisions for consistency."
                )
            if isinstance(dampening_factor, (int, float)) and dampening_factor < 1.0:
                alerts.append(
                    f"[NOTICE] Stability valve active: dampening={dampening_factor:.2f}. "
                    "Emotional amplitude is being reduced."
                )

            if alerts:
                for alert in alerts:
                    parts.append(alert)
                # Also write alerts to STM for persistence
                try:
                    alert_store = stm_load(memory_dir)
                    alert_text = "Observation alerts: " + " | ".join(alerts)
                    alert_text = alert_text[:500]
                    alert_store = stm_write_entry(alert_store, alert_text, "self_review")
                    stm_save(memory_dir, alert_store)
                except Exception as e_stm:
                    import logging
                    logging.getLogger(__name__).debug("STM write of alerts failed: %s", e_stm)
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug("Observation alert check failed: %s", e)

    except Exception as e:
        parts.append(f"ERROR: {e}")

    # 5. Active blocking hooks
    parts.append("")
    parts.append("=== Active Blocking Hooks ===")
    try:
        import json as _json

        rules_path = os.path.join(_PROJECT_ROOT, "hooks", "behavior-rules.json")
        if os.path.exists(rules_path):
            with open(rules_path, "r", encoding="utf-8") as f:
                rules_data = _json.load(f)
            blocking_rules = [r for r in rules_data.get("rules", []) if r.get("blocking")]
            if blocking_rules:
                parts.append(f"{len(blocking_rules)} blocking rules active:")
                for r in blocking_rules:
                    parts.append(f"  - {r['id']}: {r['message'][:80]}")
            else:
                parts.append("No blocking rules active.")
        parts.append("+ session-readiness-gate (blocking): session_start + memory_search + stm_write required")
    except Exception as e:
        parts.append(f"ERROR reading rules: {e}")

    # Write session_start completion flag for session-readiness-gate
    # (observation-logger may miss this call due to timing/concurrency)
    try:
        flag_dir = os.path.join(_PROJECT_ROOT, "hooks")
        flag_path = os.path.join(flag_dir, ".session-start-done")
        with open(flag_path, "w") as f:
            f.write(str(int(time.time() * 1000)))
    except Exception:
        pass

    return "\n".join(parts)


# --- Memory Consolidation ---


def _parse_lessons(memory_dir: str) -> list:
    """Parse lessons_registry.md into structured lesson objects."""
    lessons_path = os.path.join(memory_dir, "lessons_registry.md")
    if not os.path.exists(lessons_path):
        return []
    with open(lessons_path, "r", encoding="utf-8") as f:
        content = f.read()

    lessons = []
    current = {}
    current_field = None
    for line in content.split("\n"):
        line_stripped = line.strip()
        if line_stripped.startswith("## Lesson:"):
            if current.get("lesson"):
                lessons.append(current)
            current = {"date": line_stripped.replace("## Lesson:", "").strip()}
            current_field = None
        elif line_stripped.startswith("### Action"):
            current_field = "action"
        elif line_stripped.startswith("### Why"):
            current_field = "why"
        elif line_stripped.startswith("### Fix"):
            current_field = "fix"
        elif line_stripped.startswith("### Lesson"):
            current_field = "lesson"
        elif line_stripped.startswith("### Related Rule"):
            current_field = "rule"
        elif line_stripped == "---":
            current_field = None
        elif current_field and line_stripped:
            current[current_field] = current.get(current_field, "") + line_stripped + " "

    if current.get("lesson"):
        lessons.append(current)

    # Clean whitespace
    for les in lessons:
        for k in les:
            if isinstance(les[k], str):
                les[k] = les[k].strip()
    return lessons


def _load_principles(memory_dir: str) -> dict:
    """Load consolidated_principles.md metadata and content."""
    path = os.path.join(memory_dir, "consolidated_principles.md")
    if not os.path.exists(path):
        return {"exists": False, "lesson_count": 0, "principles": [], "raw": ""}
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # Extract metadata from frontmatter
    lesson_count = 0
    principles = []
    in_frontmatter = False
    in_principles = False
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter and stripped.startswith("lesson_count:"):
            try:
                lesson_count = int(stripped.split(":")[1].strip())
            except ValueError:
                pass
        if stripped.startswith("## ") and not stripped.startswith("## Meta"):
            in_principles = True
            principles.append({"title": stripped[3:], "evidence": []})
        elif in_principles and stripped.startswith("- "):
            if principles:
                principles[-1]["evidence"].append(stripped[2:])

    return {
        "exists": True,
        "lesson_count": lesson_count,
        "principles": principles,
        "raw": content,
    }


@mcp.tool()
def memory_consolidate(
    mode: str = "check",
    principles_text: str = "",
) -> str:
    """Consolidate lessons into abstract principles (long-term memory integration).

    Two modes:
    - 'check': Load all lessons, compare with existing principles, show what needs analysis.
      Returns structured data for the LLM to analyze patterns.
    - 'save': Save extracted principles to consolidated_principles.md.
      Pass the full principles text (markdown) in principles_text.

    The pattern extraction itself is done by the LLM, not by code.
    This tool handles storage, retrieval, and change detection.

    Args:
        mode: 'check' to load materials, 'save' to store principles
        principles_text: (save mode only) Markdown text of extracted principles
    """
    memory_dir = DEFAULT_MEMORY_DIR
    # Lessons are always read from GROWTH_DIR (canonical location, issue 4-3)
    lessons_dir = GROWTH_DIR
    parts = []

    if mode == "check":
        lessons = _parse_lessons(lessons_dir)
        existing = _load_principles(memory_dir)

        parts.append("=== Consolidation Check ===")
        parts.append(f"Total lessons: {len(lessons)}")
        parts.append(f"Last consolidated at: {existing['lesson_count']} lessons")

        if len(lessons) == existing["lesson_count"] and existing["exists"]:
            parts.append("STATUS: No new lessons since last consolidation.")
            parts.append("")
            parts.append("=== Current Principles ===")
            parts.append(existing["raw"])
            return "\n".join(parts)

        new_count = len(lessons) - existing["lesson_count"]
        parts.append(f"NEW lessons since last consolidation: {new_count}")
        parts.append("")

        # Show all lessons for pattern analysis
        parts.append("=== All Lessons (for pattern extraction) ===")
        for i, les in enumerate(lessons, 1):
            parts.append(f"\n[{i}] Date: {les.get('date', '?')}")
            parts.append(f"    Action: {les.get('action', '?')}")
            parts.append(f"    Why: {les.get('why', '?')}")
            parts.append(f"    Lesson: {les.get('lesson', '?')}")
            parts.append(f"    Rule: {les.get('rule', '?')}")

        if existing["exists"] and existing["principles"]:
            parts.append("")
            parts.append("=== Existing Principles (may need updating) ===")
            for p in existing["principles"]:
                parts.append(f"\n## {p['title']}")
                for ev in p["evidence"]:
                    parts.append(f"  - {ev}")

        parts.append("")
        parts.append(
            "ACTION REQUIRED: Analyze the lessons above, extract/update abstract principles, "
            "then call memory_consolidate(mode='save', principles_text='...')"
        )

    elif mode == "save":
        if not principles_text.strip():
            return "ERROR: principles_text is required in save mode"

        lessons = _parse_lessons(lessons_dir)
        lesson_count = len(lessons)

        # Build file with frontmatter
        content = f"""---
name: Consolidated Principles
description: Abstract principles extracted from {lesson_count} lessons via memory consolidation
type: consolidated
lesson_count: {lesson_count}
last_updated: {__import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")}
---

{principles_text.strip()}
"""
        path = os.path.join(memory_dir, "consolidated_principles.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        parts.append(f"Principles saved to: {path}")
        parts.append(f"Based on {lesson_count} lessons")
        parts.append(f"Content length: {len(principles_text)} chars")

    else:
        return f"ERROR: Unknown mode '{mode}'. Use 'check' or 'save'."

    return "\n".join(parts)


# --- Lesson Validation (C22-A) ---


@mcp.tool()
def validate_lesson(
    lesson_id: str,
    success: bool,
    category: str = "",
) -> str:
    """Validate whether a lesson was effective.

    Updates the lesson's confidence score: +0.1 on success, -0.15 on failure.
    Bounded to [0.1, 1.0]. Does not modify lesson text or search ranking.

    Args:
        lesson_id: Lesson number as string (e.g. "3" for lesson #3)
        success: True if the lesson proved effective, False if the problem recurred
        category: Optional pattern category for audit trail
    """
    memory_dir = GROWTH_DIR
    try:
        entry = lesson_metadata.validate_lesson(
            memory_dir, lesson_id, success=success, category=category
        )
        status = "validated" if success else "invalidated"
        return (
            f"Lesson #{lesson_id} {status}. "
            f"confidence={entry['confidence']:.2f}, "
            f"applied_count={entry.get('applied_count', 0)}"
        )
    except Exception as e:
        return f"ERROR: Failed to validate lesson: {e}"


# --- C22-E: Lesson Conflict Resolution ---


@mcp.tool()
def detect_lesson_conflicts() -> str:
    """Detect conflicting lessons within the same Rule category.

    Scans all lessons in lessons_registry.md, groups by Rule, and identifies
    pairs with divergent Fix text. Read-only: no lessons are modified.

    Returns a formatted report with conflict details and priority recommendations.
    """
    memory_dir = GROWTH_DIR
    try:
        return lesson_conflict.get_conflict_report(memory_dir)
    except Exception as e:
        print(f"Failed to detect lesson conflicts: {e}", file=sys.stderr)
        return f"ERROR: Failed to detect lesson conflicts: {e}"


# --- C22-G: Success Pattern Tools ---


@mcp.tool()
def record_success_tool(
    event_type: str,
    context: str,
    why_success: str,
    tags: str = "",
) -> str:
    """Record a success pattern for future reference.

    Captures what worked and why, enabling pattern reuse across sessions.
    Counterpart to lessons (failure patterns).

    Args:
        event_type: One of review_zero, test_pass, user_positive
        context: Description of what happened (max 500 chars)
        why_success: Analysis of why it succeeded (max 1000 chars)
        tags: Comma-separated tags for categorization (optional)
    """
    memory_dir = GROWTH_DIR
    try:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        rec = success_registry.record_success(
            memory_dir,
            event_type=event_type,
            context=context,
            why_success=why_success,
            tags=tag_list,
        )
        return (
            f"Success pattern #{rec['id']} recorded. "
            f"event_type={rec['event_type']}, tags={rec['tags']}"
        )
    except (ValueError, OSError) as e:
        return f"ERROR: Failed to record success pattern: {e}"


@mcp.tool()
def search_successes_tool(
    query: str = "",
    tags: str = "",
    limit: int = 10,
) -> str:
    """Search recorded success patterns by keyword and/or tags.

    Args:
        query: Text to match against context and why_success (optional)
        tags: Comma-separated tags to filter by (optional)
        limit: Maximum number of results (default 10)
    """
    memory_dir = GROWTH_DIR
    try:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
        results = success_registry.search_successes(
            memory_dir, query=query, tags=tag_list, limit=limit
        )
        if not results:
            return "No matching success patterns found."
        lines = [f"Found {len(results)} success pattern(s):"]
        for r in results:
            r_tags = ", ".join(r.get("tags", []))
            tag_str = f" [{r_tags}]" if r_tags else ""
            lines.append(
                f"  #{r['id']} [{r['event_type']}]{tag_str} "
                f"{r.get('context', '')[:120]}"
            )
            why = r.get("why_success", "")[:200]
            if why:
                lines.append(f"    Why: {why}")
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: Failed to search success patterns: {e}"


# --- Mastery Experience Tracker (C22-H) ---


@mcp.tool()
def update_mastery(
    domain: str,
    success: bool,
    approach: str = "",
) -> str:
    """Update mastery tracking for a capability domain.

    Records a success or failure event and recomputes score/trend.
    Domain names must be 1-100 chars with no control characters (G58
    prompt-injection defense — newlines/NUL/etc are rejected). Approach
    is truncated to 500 chars. Score is computed only after 3+ events.

    Args:
        domain: Capability domain name (e.g. 'testing', 'error_handling')
        success: True for success, False for failure
        approach: Description of approach used (recorded on success only, optional)
    """
    memory_dir = GROWTH_DIR
    try:
        entry = mastery_profile.update_mastery(
            memory_dir,
            domain=domain,
            success=success,
            approach=approach,
        )
        score_str = f"{entry['mastery_score']:.1%}" if entry["mastery_score"] is not None else "N/A (need 3+)"
        return (
            f"Mastery updated: {domain} "
            f"({entry['success_count']}/{entry['total_count']}, "
            f"score={score_str}, trend={entry['trend']})"
        )
    except (ValueError, OSError) as e:
        return f"ERROR: Failed to update mastery: {e}"


@mcp.tool()
def mastery_report() -> str:
    """Generate a mastery profile report showing strengths, growth areas, and stats.

    Returns a formatted text report of all tracked capability domains.
    """
    memory_dir = GROWTH_DIR
    try:
        return mastery_profile.generate_report(memory_dir)
    except Exception as e:
        return f"ERROR: Failed to generate mastery report: {e}"


# --- Growth Metrics Dashboard (C22-F/L) ---


@mcp.tool()
def growth_dashboard() -> str:
    """Generate a growth metrics dashboard with lessons, successes, mastery, and balance.

    Read-only aggregation of all growth data sources into a formatted 4-section report.
    Sections: Lessons (count/confidence), Success Patterns (by type), Mastery (domains/trends),
    Balance (success-to-total ratio with health status).
    """
    memory_dir = GROWTH_DIR
    try:
        return growth_metrics.generate_dashboard(memory_dir)
    except Exception as e:
        return f"ERROR: Failed to generate growth dashboard: {e}"


@mcp.tool()
def growth_health() -> str:
    """Get a single-line growth health summary.

    Quick status: lesson count, success count, mastery domains, balance ratio, and warnings.
    Useful for heartbeat checks and session start context.
    """
    memory_dir = GROWTH_DIR
    try:
        return growth_metrics.get_health_summary(memory_dir)
    except Exception as e:
        return f"ERROR: Failed to get growth health: {e}"


# --- Session End (Unified Finalization) ---


@mcp.tool()
def session_end(
    summary: str,
    completed: str = "",
    pending: str = "",
    decisions: str = "",
    issues: str = "",
    next_actions: str = "",
) -> str:
    """Finalize a session: save context, take final observation, record emotion.

    Runs 3 finalization steps:
    1. Save session context (summary + metadata) for next session
    2. Take final self-observation snapshot
    3. Save current emotion state (persisted for next session)

    Args:
        summary: What was done in this session (required)
        completed: Comma-separated list of completed tasks
        pending: Comma-separated list of pending tasks
        decisions: Key decisions made during the session
        issues: Known issues or blockers
        next_actions: Suggested next steps
    """
    memory_dir = DEFAULT_MEMORY_DIR
    parts = []

    # 1. Save session context
    parts.append("=== Session Context Saved ===")
    try:
        result = sc_save(
            memory_dir=memory_dir,
            summary=summary,
            completed=completed,
            pending=pending,
            decisions=decisions,
            issues=issues,
            next_actions=next_actions,
        )
        if result.startswith("ERROR"):
            parts.append(result)
        else:
            parts.append(f"Saved to: {result}")
    except Exception as e:
        parts.append(f"ERROR: {e}")

    # 2. Final self snapshot (via observation facade)
    parts.append("")
    parts.append("=== Final Self Snapshot ===")
    try:
        mini = facade_run_mini_snapshot(memory_dir)
        obs = mini["observe"]
        parts.append(f"[observe] {obs['integrated']}")

        img = mini["self_image"]
        parts.append(f"[self_image] {img['overall_impression']} — {img['integrated_description']}")

        tone = mini["tone"]
        parts.append(f"[tone] {tone['primary_tone']} — {tone['description']}")
    except Exception as e:
        parts.append(f"ERROR: {e}")

    # 3. Emotion state persisted (already saved by emotion_react/emotion_update calls)
    parts.append("")
    parts.append("=== Emotion State ===")
    try:
        state = load_state(memory_dir)
        axes = [f"{a}={state.get(a, 0.0):+.3f}" for a in ("fulfillment", "tension", "affinity")]
        parts.append(f"Persisted: {', '.join(axes)}")
    except Exception as e:
        parts.append(f"ERROR: {e}")

    # Write session-end-done flag (for stop-session-end.js double-execution prevention)
    try:
        hooks_dir = os.path.join(_PROJECT_ROOT, "hooks")
        flag_path = os.path.join(hooks_dir, ".session-end-done")
        with open(flag_path, "w", encoding="utf-8") as f:
            import time as _time_mod
            f.write(str(int(_time_mod.time())))
    except OSError as e:
        import logging as _logging_mod
        _logging_mod.getLogger(__name__).debug("Failed to write .session-end-done flag: %s", e)

    return "\n".join(parts)


# --- Success Trajectory Library (C22-I) ---


@mcp.tool()
def record_trajectory(
    task_class: str,
    steps: str,
    outcome: str,
    transferability: float = 0.5,
) -> str:
    """Record a successful execution trajectory for future reuse.

    Stores decision sequences from successful tasks so similar future tasks
    can follow proven paths.

    Args:
        task_class: Task classification (e.g. "hook_implementation", "mcp_tool_creation")
        steps: JSON array of step objects, each with {action, tool, approach, result}
        outcome: Final result text describing what was achieved
        transferability: How transferable to other tasks (0.0-1.0, default 0.5)
    """
    memory_dir = GROWTH_DIR
    try:
        step_list = json.loads(steps) if isinstance(steps, str) else steps
        if not isinstance(step_list, list):
            return "ERROR: steps must be a JSON array of step objects"
        rec = trajectory_store.record_trajectory(
            memory_dir,
            task_class=task_class,
            steps=step_list,
            outcome=outcome,
            transferability=transferability,
        )
        return (
            f"Trajectory #{rec['id']} recorded. "
            f"task_class={rec['task_class']}, steps={len(rec['steps'])}, "
            f"transferability={rec['transferability']}"
        )
    except (ValueError, json.JSONDecodeError, OSError) as e:
        return f"ERROR: Failed to record trajectory: {e}"


@mcp.tool()
def find_trajectories(
    task_class: str,
    limit: int = 3,
) -> str:
    """Find similar trajectories by task class for reuse as reference approaches.

    Args:
        task_class: Task class to search for (exact match)
        limit: Maximum number of results (default 3)
    """
    memory_dir = GROWTH_DIR
    try:
        results = trajectory_store.find_similar(
            memory_dir, task_class=task_class, limit=limit
        )
        if not results:
            return f"No trajectories found for task_class={task_class!r}."
        lines = [f"Found {len(results)} trajectory(ies) for {task_class!r}:"]
        for r in results:
            step_summary = ", ".join(
                s.get("action", "?")[:30] for s in r.get("steps", [])[:5]
            )
            if len(r.get("steps", [])) > 5:
                step_summary += ", ..."
            lines.append(
                f"  #{r['id']} [usage={r.get('usage_count', 0)}, "
                f"transfer={r.get('transferability', 0):.1f}] "
                f"{r.get('outcome', '')[:120]}"
            )
            if step_summary:
                lines.append(f"    Steps: {step_summary}")
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: Failed to find trajectories: {e}"


@mcp.tool()
def golden_paths(
    min_usage: int = 3,
) -> str:
    """Get Golden Path trajectories — proven, frequently-reused execution patterns.

    Trajectories with usage_count >= min_usage are considered Golden Paths,
    suitable for templating future tasks.

    Args:
        min_usage: Minimum usage count to qualify as Golden Path (default 3)
    """
    memory_dir = GROWTH_DIR
    try:
        results = trajectory_store.get_golden_paths(memory_dir, min_usage=min_usage)
        if not results:
            return f"No Golden Paths found (min_usage={min_usage})."
        lines = [f"Found {len(results)} Golden Path(s):"]
        for r in results:
            lines.append(
                f"  #{r['id']} [{r['task_class']}] usage={r.get('usage_count', 0)}, "
                f"transfer={r.get('transferability', 0):.1f}"
            )
            lines.append(f"    Outcome: {r.get('outcome', '')[:150]}")
            step_actions = [s.get("action", "?") for s in r.get("steps", [])]
            if step_actions:
                lines.append(f"    Path: {' -> '.join(a[:25] for a in step_actions[:8])}")
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: Failed to get golden paths: {e}"


# --- Positive Transfer Monitor (C22-K) ---


@mcp.tool()
def record_transfer(
    pattern_id: str,
    source_domain: str,
    target_domain: str,
    success: bool,
    notes: str = "",
) -> str:
    """Record a cross-domain transfer of a success pattern.

    Tracks whether applying a pattern from one domain to another
    was successful (positive transfer) or not (negative transfer).

    Args:
        pattern_id: Identifier of the success pattern being transferred
        source_domain: Domain the pattern originated from (max 50 chars)
        target_domain: Domain the pattern was applied to (max 50 chars)
        success: Whether the transfer was successful
        notes: Optional notes about the transfer (max 500 chars)
    """
    memory_dir = GROWTH_DIR
    try:
        rec = transfer_monitor.record_transfer(
            memory_dir,
            pattern_id=pattern_id,
            source_domain=source_domain,
            target_domain=target_domain,
            success=success,
            notes=notes,
        )
        status = "positive" if rec["success"] else "negative"
        return (
            f"Transfer #{rec['id']} recorded ({status}). "
            f"Pattern {rec['pattern_id']}: "
            f"{rec['source_domain']} -> {rec['target_domain']}"
        )
    except (ValueError, OSError) as e:
        return f"ERROR: Failed to record transfer: {e}"


@mcp.tool()
def transfer_report() -> str:
    """Get a formatted report of cross-domain pattern transfers.

    Shows overall statistics, per-pattern breakdown, and domain pair results.
    """
    memory_dir = GROWTH_DIR
    try:
        return transfer_monitor.get_transfer_report(memory_dir)
    except Exception as e:
        return f"ERROR: Failed to generate transfer report: {e}"


# --- After-Action Success Review (C22-J) ---


@mcp.tool()
def create_aar(
    intent: str,
    actual: str,
    why_success: str,
    replicable: str,
    context_dependent: str,
    transferable: str,
    tags: list[str] | None = None,
) -> str:
    """Record an After-Action Success Review (AAR).

    Combines US Army AAR methodology with Appreciative Inquiry 4D to capture
    what went well, why it succeeded, and how to replicate it.

    Args:
        intent: What was the intended outcome?
        actual: What actually happened?
        why_success: Why did it succeed? (root cause of success)
        replicable: What aspects are replicable in other contexts?
        context_dependent: What aspects were specific to this context?
        transferable: What can be transferred to other domains?
        tags: Optional tags for categorization (max 10)
    """
    memory_dir = GROWTH_DIR
    try:
        rec = after_action_review.create_aar(
            memory_dir,
            intent=intent,
            actual=actual,
            why_success=why_success,
            replicable=replicable,
            context_dependent=context_dependent,
            transferable=transferable,
            tags=tags,
        )
        return (
            f"AAR #{rec['id']} recorded. "
            f"Intent: {rec['intent'][:80]}... "
            f"Tags: {', '.join(rec['tags']) if rec['tags'] else 'none'}"
        )
    except (ValueError, OSError) as e:
        return f"ERROR: Failed to create AAR: {e}"


@mcp.tool()
def search_aars_tool(
    query: str = "",
    tags: list[str] | None = None,
    limit: int = 5,
) -> str:
    """Search After-Action Reviews by keyword or tags.

    Args:
        query: Text to search across all content fields (case-insensitive)
        tags: Filter by tags (OR matching — any tag match counts)
        limit: Maximum results to return (default 5)
    """
    memory_dir = GROWTH_DIR
    try:
        results = after_action_review.search_aars(
            memory_dir, query=query, tags=tags, limit=limit
        )
        if not results:
            return "No matching AARs found."
        lines = [f"Found {len(results)} AAR(s):"]
        for r in results:
            lines.append(
                f"  #{r['id']} [{r.get('recorded_at', '')}] "
                f"{r.get('intent', '')[:80]}"
            )
            if r.get("tags"):
                lines.append(f"    Tags: {', '.join(r['tags'])}")
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: Failed to search AARs: {e}"


@mcp.tool()
def aar_report(limit: int = 5) -> str:
    """Get a formatted report of recent After-Action Reviews.

    Shows intent, actual outcome, success factors, and transferability
    for the most recent reviews.

    Args:
        limit: Number of recent AARs to include (default 5)
    """
    memory_dir = GROWTH_DIR
    try:
        return after_action_review.get_aar_report(memory_dir, limit=limit)
    except Exception as e:
        return f"ERROR: Failed to generate AAR report: {e}"


def main():
    os.makedirs(GROWTH_DIR, exist_ok=True)
    print("Memory MCP server starting on stdio...", file=sys.stderr)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
