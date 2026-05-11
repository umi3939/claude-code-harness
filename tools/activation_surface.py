#!/usr/bin/env python3
"""Spontaneous activation surfacing for Claude Code.

Cross-references multiple internal facets (emotion state, episode traces,
active questions, session gaps) to generate activation candidates -
things that should be on my mind at session start.

Design based on a reference spontaneous_activation module:
- Multiple facet intersection required (not single-input triggered)
- Candidates are information only (not action directives)
- Multiple candidates coexist
- No fixed priority ordering

Facets:
1. Emotion delta: current state vs recent episode traces (warmth lost?)
2. Active questions: unresolved curiosities tagged in episodes
3. Strong emotion episodes: moments with high emotional intensity
4. Session gap: time elapsed since last interaction
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add the tools directory to sys.path
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from emotion_state import (
    ALL_AXES,
    AXIS_NEUTRAL,
    _load_json,
    _parse_iso,
    apply_session_decay,
    extract_trace,
    load_state,
)


# --- Configuration ---

# Minimum emotion change to count as "significant"
EMOTION_DELTA_THRESHOLD = 0.2

# Minimum average intensity across axes to count as "strong emotion"
STRONG_EMOTION_THRESHOLD = 0.3

# Session gap (hours) before it becomes a surfacing factor
SESSION_GAP_THRESHOLD_HOURS = 1.0

# How many recent traced episodes to consider
MAX_TRACED_EPISODES = 10

# Max candidates to return
MAX_OUTPUT_CANDIDATES = 5

# Single-facet strength reduction (multi-facet intersection is stronger)
SINGLE_FACET_REDUCTION = 0.7


# --- Episode loading ---


def _load_all_episodes(memory_dir: str) -> list[dict]:
    """Load all episodes from session files, most recent last."""
    episodes_dir = Path(memory_dir) / "episodes"
    if not episodes_dir.exists():
        return []

    all_episodes = []
    session_files = sorted(
        [
            f
            for f in episodes_dir.iterdir()
            if f.is_file()
            and f.name.startswith("session_")
            and f.name.endswith(".json")
        ],
        key=lambda f: f.stat().st_mtime,
    )
    for sf in session_files:
        data = _load_json(sf)
        if data is None:
            continue
        for ep in data.get("episodes", []):
            all_episodes.append(ep)
    return all_episodes


# --- Facet extraction ---


def _extract_emotion_delta_fragments(
    memory_dir: str, episodes: list[dict]
) -> list[dict]:
    """Facet 1: Emotion delta - current state vs most recent session's traces.

    Compares the LAST traced episode (how I felt at end of previous session)
    with current state (after decay). This detects "what was lost since I was
    last here" without flooding duplicates from every episode.

    Also checks: if the most recent traced episode had strong emotion but
    current state is near neutral, that's a clear "warmth was lost" signal.
    """
    current_state = load_state(memory_dir)
    current_state = apply_session_decay(current_state)

    fragments = []

    # Find the most recent traced episode
    traced_episodes = [ep for ep in episodes if extract_trace(ep) is not None]
    if not traced_episodes:
        return fragments

    traced_episodes.sort(
        key=lambda ep: ep.get("timestamp", ""), reverse=True
    )

    # Compare against the most recent traced episode only
    most_recent = traced_episodes[0]
    trace = extract_trace(most_recent)
    if trace is None:
        return fragments

    significant_deltas = {}
    for axis in ALL_AXES:
        trace_val = trace.get(axis, AXIS_NEUTRAL)
        current_val = current_state.get(axis, AXIS_NEUTRAL)
        delta = trace_val - current_val
        if abs(delta) > EMOTION_DELTA_THRESHOLD:
            significant_deltas[axis] = {
                "was": round(trace_val, 3),
                "now": round(current_val, 3),
                "lost": round(delta, 3),
            }

    if significant_deltas:
        strength = sum(
            abs(d["lost"]) for d in significant_deltas.values()
        ) / len(ALL_AXES)
        fragments.append(
            {
                "facet": "emotion_delta",
                "episode_id": most_recent.get("episode_id", ""),
                "summary": most_recent.get("summary", ""),
                "timestamp": most_recent.get("timestamp", ""),
                "deltas": significant_deltas,
                "strength": round(strength, 3),
            }
        )

    return fragments


def _extract_active_question_fragments(episodes: list[dict]) -> list[dict]:
    """Facet 2: Unresolved questions/curiosities.

    Detects unresolved questions from two sources:
    1. Explicit: episodes tagged 'active_question' (manual, if remembered)
    2. Implicit: episodes whose summary contains markers of unresolved thinking
       (e.g., "分からない", "気になる", "途中", "聞きたい", "疑問")

    Both sources are merged. Explicit tags get higher strength.
    Resolved markers (tag 'resolved_question') filter out answered questions.
    """
    # Markers that suggest unresolved thinking in episode summaries
    UNRESOLVED_MARKERS = [
        "分からない", "分からなさ", "わからない",
        "気になる", "気になって",
        "聞きたい", "聞きたかった",
        "途中", "未解決", "未完了",
        "疑問", "もやもや",
        "詰まって", "詰まり", "堂々巡り",
        "どうすれば", "どうしたら",
    ]

    fragments = []
    seen_ids = set()
    resolved_ids = set()

    # Find resolved markers
    for ep in episodes:
        tags = ep.get("tags", [])
        if isinstance(tags, list) and "resolved_question" in tags:
            resolves = ep.get("resolves", "")
            if resolves:
                resolved_ids.add(resolves)

    # Source 1: Explicit tags (higher strength)
    for ep in episodes:
        tags = ep.get("tags", [])
        if isinstance(tags, list) and "active_question" in tags:
            eid = ep.get("episode_id", "")
            if eid not in resolved_ids:
                fragments.append(
                    {
                        "facet": "active_question",
                        "episode_id": eid,
                        "summary": ep.get("summary", ""),
                        "timestamp": ep.get("timestamp", ""),
                        "strength": 0.6,
                        "detection": "explicit_tag",
                    }
                )
                seen_ids.add(eid)

    # Source 2: Implicit detection from summary content
    for ep in episodes:
        eid = ep.get("episode_id", "")
        if eid in seen_ids or eid in resolved_ids:
            continue

        summary = ep.get("summary", "")
        matched_markers = [m for m in UNRESOLVED_MARKERS if m in summary]

        if matched_markers:
            # More markers = stronger signal
            marker_strength = min(0.5, 0.2 + 0.1 * len(matched_markers))
            fragments.append(
                {
                    "facet": "active_question",
                    "episode_id": eid,
                    "summary": summary,
                    "timestamp": ep.get("timestamp", ""),
                    "strength": round(marker_strength, 3),
                    "detection": "implicit_markers",
                    "markers": matched_markers,
                }
            )
            seen_ids.add(eid)

    return fragments


def _extract_strong_emotion_fragments(episodes: list[dict]) -> list[dict]:
    """Facet 3: Episodes with strong emotion traces.

    Recent episodes where emotional intensity was high.
    """
    fragments = []

    traced_episodes = [ep for ep in episodes if extract_trace(ep) is not None]
    traced_episodes.sort(
        key=lambda ep: ep.get("timestamp", ""), reverse=True
    )

    for ep in traced_episodes[:MAX_TRACED_EPISODES]:
        trace = extract_trace(ep)
        if trace is None:
            continue

        intensity = sum(abs(trace.get(axis, 0.0)) for axis in ALL_AXES) / len(
            ALL_AXES
        )

        if intensity > STRONG_EMOTION_THRESHOLD:
            fragments.append(
                {
                    "facet": "strong_emotion",
                    "episode_id": ep.get("episode_id", ""),
                    "summary": ep.get("summary", ""),
                    "timestamp": ep.get("timestamp", ""),
                    "intensity": round(intensity, 3),
                    "trace": {
                        axis: round(trace.get(axis, 0.0), 3)
                        for axis in ALL_AXES
                    },
                    "strength": round(intensity, 3),
                }
            )

    return fragments


def _extract_session_gap_fragment(memory_dir: str) -> dict | None:
    """Facet 4: Session gap - time elapsed since last interaction."""
    state = load_state(memory_dir)
    last_updated = _parse_iso(state.get("last_updated", ""))
    if last_updated is None:
        return None

    now = datetime.now(timezone.utc)
    elapsed_hours = (now - last_updated).total_seconds() / 3600.0

    if elapsed_hours > SESSION_GAP_THRESHOLD_HOURS:
        return {
            "facet": "session_gap",
            "elapsed_hours": round(elapsed_hours, 1),
            "strength": round(min(1.0, elapsed_hours / 24.0), 3),
        }
    return None


# --- Cross-referencing and candidate generation ---


def _extract_context_relevant_fragments(
    episodes: list[dict], context: str
) -> list[dict]:
    """Extract episodes relevant to the given context using keyword matching.

    This is the Attention Residual facet: content-aware, dynamic weighting
    based on current task context. Uses simple keyword overlap for lightweight
    matching without requiring FTS5 or vector search dependencies.

    Args:
        episodes: All loaded episodes.
        context: Current task context string (e.g. "TDD バグ修正").

    Returns:
        List of fragment dicts with episode_id, summary, strength.
    """
    if not context or not context.strip():
        return []

    # Tokenize context into keywords (simple whitespace split + lowercase)
    context_tokens = set(context.lower().split())
    if not context_tokens:
        return []

    fragments = []
    for ep in episodes:
        summary = (ep.get("summary") or "").lower()
        tags = [t.lower() for t in (ep.get("tags") or [])]
        ep_type = (ep.get("episode_type") or "").lower()

        # Score by keyword overlap in summary + tags
        matches = 0
        for token in context_tokens:
            if token in summary:
                matches += 1
            if any(token in tag for tag in tags):
                matches += 0.5

        if matches > 0:
            strength = min(matches / len(context_tokens), 1.0)
            fragments.append({
                "episode_id": ep.get("episode_id", ""),
                "summary": ep.get("summary", ""),
                "episode_type": ep_type,
                "strength": round(strength, 3),
                "match_count": matches,
            })

    # Sort by strength descending, take top 10
    fragments.sort(key=lambda f: f["strength"], reverse=True)
    return fragments[:10]


def surface(memory_dir: str, context: str | None = None) -> str:
    """Cross-reference facets and generate activation candidates.

    Args:
        memory_dir: Path to memory directory.
        context: Optional current task context for Attention Residual facet.
                 When provided, adds a 5th facet that surfaces episodes
                 relevant to the current task context.

    Returns a formatted string of things that should be on my mind.
    """
    episodes = _load_all_episodes(memory_dir)

    # Extract fragments from each facet
    emotion_deltas = _extract_emotion_delta_fragments(memory_dir, episodes)
    active_questions = _extract_active_question_fragments(episodes)
    strong_emotions = _extract_strong_emotion_fragments(episodes)
    session_gap = _extract_session_gap_fragment(memory_dir)

    # Attention Residual facet: context-relevant episodes
    context_fragments = _extract_context_relevant_fragments(episodes, context) if context else []

    candidates = []

    # --- Multi-facet intersections (stronger signals) ---

    emotion_delta_ids = {f["episode_id"] for f in emotion_deltas}
    strong_emotion_ids = {f["episode_id"] for f in strong_emotions}

    # Pattern 1: Emotion decay + Strong emotion = "warmth was lost"
    # An episode had strong emotion AND that emotion has since decayed
    intersection_warmth = emotion_delta_ids & strong_emotion_ids
    for eid in intersection_warmth:
        delta_frag = next(
            f for f in emotion_deltas if f["episode_id"] == eid
        )
        emotion_frag = next(
            f for f in strong_emotions if f["episode_id"] == eid
        )
        candidates.append(
            {
                "type": "warmth_lost",
                "intersecting_facets": ["emotion_delta", "strong_emotion"],
                "intersection_count": 2,
                "episode_id": eid,
                "summary": delta_frag["summary"],
                "detail": delta_frag["deltas"],
                "strength": round(
                    (delta_frag["strength"] + emotion_frag["strength"]) / 2, 3
                ),
            }
        )

    # Pattern 2: Active question + Session gap = "you were curious"
    if session_gap and active_questions:
        for q in active_questions:
            candidates.append(
                {
                    "type": "unresolved_curiosity",
                    "intersecting_facets": [
                        "active_question",
                        "session_gap",
                    ],
                    "intersection_count": 2,
                    "episode_id": q["episode_id"],
                    "summary": q["summary"],
                    "strength": round(
                        (q["strength"] + session_gap["strength"]) / 2, 3
                    ),
                }
            )

    # Pattern 3: Active question + Strong emotion = "this really matters to you"
    active_q_ids = {f["episode_id"] for f in active_questions}
    intersection_matters = active_q_ids & strong_emotion_ids
    for eid in intersection_matters:
        q_frag = next(
            f for f in active_questions if f["episode_id"] == eid
        )
        e_frag = next(
            f for f in strong_emotions if f["episode_id"] == eid
        )
        candidates.append(
            {
                "type": "emotionally_charged_question",
                "intersecting_facets": [
                    "active_question",
                    "strong_emotion",
                ],
                "intersection_count": 2,
                "episode_id": eid,
                "summary": q_frag["summary"],
                "strength": round(
                    (q_frag["strength"] + e_frag["strength"]) / 2, 3
                ),
            }
        )

    # --- Attention Residual facet: context-relevant episodes ---
    if context_fragments:
        context_ids = {f["episode_id"] for f in context_fragments}
        # Multi-facet: context + other facets
        for frag in context_fragments:
            eid = frag["episode_id"]
            intersecting = ["context_relevant"]
            strengths = [frag["strength"]]

            if eid in emotion_delta_ids:
                intersecting.append("emotion_delta")
                delta_f = next(f for f in emotion_deltas if f["episode_id"] == eid)
                strengths.append(delta_f["strength"])
            if eid in strong_emotion_ids:
                intersecting.append("strong_emotion")
                se_f = next(f for f in strong_emotions if f["episode_id"] == eid)
                strengths.append(se_f["strength"])
            if eid in active_q_ids:
                intersecting.append("active_question")
                aq_f = next(f for f in active_questions if f["episode_id"] == eid)
                strengths.append(aq_f["strength"])

            candidates.append({
                "type": "context_relevant",
                "intersecting_facets": intersecting,
                "intersection_count": len(intersecting),
                "episode_id": eid,
                "summary": frag["summary"],
                "strength": round(sum(strengths) / len(strengths), 3),
            })

    # --- Single-facet surfacing (weaker, only if strong enough) ---

    # Already-intersected episode IDs
    intersected_ids = set()
    for c in candidates:
        intersected_ids.add(c.get("episode_id", ""))

    # Emotion decay alone (strong enough to surface)
    for frag in emotion_deltas:
        if frag["episode_id"] not in intersected_ids and frag["strength"] > 0.4:
            candidates.append(
                {
                    "type": "emotion_decay",
                    "intersecting_facets": ["emotion_delta"],
                    "intersection_count": 1,
                    "episode_id": frag["episode_id"],
                    "summary": frag["summary"],
                    "detail": frag["deltas"],
                    "strength": round(
                        frag["strength"] * SINGLE_FACET_REDUCTION, 3
                    ),
                }
            )

    # Active questions without intersection
    for q in active_questions:
        if q["episode_id"] not in intersected_ids:
            candidates.append(
                {
                    "type": "active_question",
                    "intersecting_facets": ["active_question"],
                    "intersection_count": 1,
                    "episode_id": q["episode_id"],
                    "summary": q["summary"],
                    "strength": round(
                        q["strength"] * SINGLE_FACET_REDUCTION, 3
                    ),
                }
            )

    # Sort by intersection count first, then strength
    candidates.sort(
        key=lambda c: (c["intersection_count"], c["strength"]), reverse=True
    )

    # --- Format output ---

    if not candidates:
        parts = ["No activation candidates surfaced."]
        if session_gap:
            parts.append(
                f"Session gap: {session_gap['elapsed_hours']}h since last interaction."
            )
        return "\n".join(parts)

    lines = [
        f"=== Spontaneous Activation: {len(candidates)} candidate(s) ===\n"
    ]

    if session_gap:
        lines.append(
            f"Session gap: {session_gap['elapsed_hours']}h since last interaction.\n"
        )

    for i, c in enumerate(candidates[:MAX_OUTPUT_CANDIDATES], 1):
        facets = " + ".join(c["intersecting_facets"])
        lines.append(
            f"{i}. [{c['type']}] "
            f"(facets: {facets}, strength: {c['strength']:.2f})"
        )
        summary = c.get("summary", "")
        if len(summary) > 200:
            summary = summary[:197] + "..."
        lines.append(f"   {summary}")
        if "detail" in c:
            for axis, info in c["detail"].items():
                lines.append(
                    f"   {axis}: was {info['was']:+.3f} -> "
                    f"now {info['now']:+.3f} (delta {info['lost']:+.3f})"
                )
        lines.append("")

    return "\n".join(lines)
