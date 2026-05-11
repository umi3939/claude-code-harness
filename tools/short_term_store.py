#!/usr/bin/env python3
"""Short-term memory store for Claude Code.

Holds raw thoughts, questions, impressions, and unresolved items
that persist across sessions but decay over time.

This is the "middle layer" between lived experience and episode summaries.
Entries here still have texture and warmth -- they haven't been compressed
into conclusions yet.

Design:
  - Entries are raw text with a category and weight
  - Weight starts at 1.0 and decays each session (multiplied by DECAY_RATE)
  - Entries below MIN_WEIGHT are pruned on load
  - FIFO cap ensures bounded growth
  - Entries can be "promoted" to episodes when they've been digested

Persistence: JSON file in memory directory.
"""

import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

# --- Constants ---

STM_FILENAME = "short_term_memory.json"
MAX_ENTRIES = 200
DECAY_RATE = 0.75       # weight *= 0.75 per session (after ~4 sessions, weight < 0.32)
MIN_WEIGHT = 0.10       # entries below this are pruned
MIN_ENTRIES = 10        # safety floor: never prune below this count
MAX_CONTENT_CHARS = 2000 # safety cap on entry content length
MAX_WEIGHT = 1.0        # weight upper bound (clip on boost)
BOOST_AMOUNT = 0.1      # weight boost per recall (P4-4: gentle recovery)
RECALL_RESISTANCE_RATE = 0.05   # resistance per recall_count
MAX_RECALL_RESISTANCE = 0.3     # resistance cap

VALID_CATEGORIES = ("thought", "question", "impression", "unresolved", "feeling", "self_review")

# Category-specific rules: ttl_days (None = no expiry), decay_factor (multiplied with DECAY_RATE)
CATEGORY_RULES = {
    "self_review": {"ttl_days": 3,  "decay_factor": 0.5},
    "thought":     {"ttl_days": 14, "decay_factor": 0.75},
    "question":    {"ttl_days": 21, "decay_factor": 0.85},
    "impression":  {"ttl_days": 21, "decay_factor": 0.80},
    "unresolved":  {"ttl_days": 30, "decay_factor": 0.90},
    "feeling":     {"ttl_days": 14, "decay_factor": 0.70},
}

# Default for unknown categories: no TTL expiry, standard decay
_DEFAULT_RULE = {"ttl_days": None, "decay_factor": 1.0}


# --- Data helpers ---

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _get_store_path(memory_dir: str) -> Path:
    return Path(memory_dir) / STM_FILENAME


def _empty_store() -> dict:
    return {
        "entries": [],
        "last_session_decay_at": None,
        "last_ttl_sweep_at": None,
        "session_count": 0,
    }


# --- Core I/O ---

def load_store(memory_dir: str) -> dict:
    """Load the short-term memory store from disk."""
    path = _get_store_path(memory_dir)
    if not path.exists():
        return _empty_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "entries" not in data:
            return _empty_store()
        # Migration: add last_ttl_sweep_at if missing (backward compat)
        if "last_ttl_sweep_at" not in data:
            data["last_ttl_sweep_at"] = None
        return data
    except (json.JSONDecodeError, OSError):
        return _empty_store()


def save_store(memory_dir: str, store: dict) -> str:
    """Save the store atomically. Returns path on success, ERROR on failure."""
    path = _get_store_path(memory_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=".stm_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(path))
        return str(path)
    except Exception as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return f"ERROR: {e}"


# --- Session decay ---

def _parse_timestamp(ts_str: str) -> datetime | None:
    """Parse an ISO timestamp string, returning None on failure."""
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def apply_session_decay(store: dict) -> tuple[dict, int]:
    """Apply one round of session decay to all entries.

    Processing order (fixed, per design spec):
      1. Weight decay with category-specific factors + MIN_WEIGHT pruning
      2. TTL pruning (time-based expiry per category)
      3. Safety floor: ensure at least MIN_ENTRIES remain (only when starting above MIN_ENTRIES)

    Should be called once at session start.
    Returns (updated_store, total_pruned_count).
    """
    original_entries = store.get("entries", [])
    original_count = len(original_entries)
    now = datetime.now(timezone.utc)

    # --- Step 1: Weight decay + MIN_WEIGHT pruning ---
    decayed_entries = []
    weight_pruned_entries = []
    for entry in original_entries:
        cat = entry.get("category", "thought")
        rule = CATEGORY_RULES.get(cat, _DEFAULT_RULE)
        decay_factor = rule["decay_factor"]

        # C22-B: recall resistance — referenced entries decay more slowly
        recall_count = entry.get("recall_count", 0)
        if not isinstance(recall_count, int) or recall_count < 0:
            recall_count = 0
        resistance = min(recall_count * RECALL_RESISTANCE_RATE, MAX_RECALL_RESISTANCE)
        effective_rate = min(DECAY_RATE + resistance, 0.99)

        new_weight = entry.get("weight", 1.0) * effective_rate * decay_factor
        if new_weight >= MIN_WEIGHT:
            entry = dict(entry)
            entry["weight"] = round(new_weight, 4)
            decayed_entries.append(entry)
        else:
            # Keep a copy with decayed weight for potential floor restoration
            entry = dict(entry)
            entry["weight"] = round(new_weight, 4)
            weight_pruned_entries.append(entry)

    # --- Step 2: TTL pruning ---
    ttl_survivors = []
    ttl_expired = []
    for entry in decayed_entries:
        cat = entry.get("category", "thought")
        rule = CATEGORY_RULES.get(cat, _DEFAULT_RULE)
        ttl_days = rule["ttl_days"]

        if ttl_days is None:
            ttl_survivors.append(entry)
            continue

        ts = _parse_timestamp(entry.get("timestamp", ""))
        if ts is None:
            ttl_survivors.append(entry)
            continue

        age = now - ts
        if age.total_seconds() > ttl_days * 86400:
            ttl_expired.append(entry)
        else:
            ttl_survivors.append(entry)

    ttl_pruned = len(ttl_expired)

    # --- Step 3: Safety floor ---
    # Only apply floor when we started at or above MIN_ENTRIES.
    # The floor prevents the combined result from going below MIN_ENTRIES.
    final_entries = ttl_survivors
    if original_count >= MIN_ENTRIES and len(final_entries) < MIN_ENTRIES:
        # Build a pool of pruned entries sorted by timestamp (most recent first)
        all_pruned = ttl_expired + weight_pruned_entries
        all_pruned.sort(
            key=lambda e: e.get("timestamp", ""),
            reverse=True,
        )
        needed = MIN_ENTRIES - len(final_entries)
        restored = all_pruned[:needed]
        final_entries = final_entries + restored
        # Adjust ttl_pruned count for restored TTL entries
        restored_ttl = sum(1 for e in restored if e in ttl_expired)
        ttl_pruned -= restored_ttl

    total_pruned = original_count - len(final_entries)

    store = dict(store)
    store["entries"] = final_entries
    store["last_session_decay_at"] = _now_iso()
    store["last_ttl_sweep_at"] = _now_iso()
    store["session_count"] = store.get("session_count", 0) + 1
    store["_ttl_pruned_count"] = store.get("_ttl_pruned_count", 0) + ttl_pruned
    return store, total_pruned


# --- Write ---

def write_entry(
    store: dict,
    content: str,
    category: str = "thought",
    emotion_snapshot: dict | None = None,
) -> dict:
    """Add a new entry to the store. Returns updated store."""
    if category not in VALID_CATEGORIES:
        category = "thought"

    # Truncate content for safety
    if len(content) > MAX_CONTENT_CHARS:
        content = content[:MAX_CONTENT_CHARS] + "..."

    entry = {
        "id": _new_id(),
        "category": category,
        "content": content,
        "timestamp": _now_iso(),
        "weight": 1.0,
        "session_created": store.get("session_count", 0),
    }
    if emotion_snapshot:
        entry["emotion_snapshot"] = emotion_snapshot

    entries = list(store.get("entries", []))
    entries.append(entry)

    # FIFO trim
    if len(entries) > MAX_ENTRIES:
        entries = entries[-MAX_ENTRIES:]

    store = dict(store)
    store["entries"] = entries
    return store


# --- Read ---

def read_entries(
    store: dict,
    category: str | None = None,
    limit: int = 20,
    min_weight: float = 0.0,
) -> list[dict]:
    """Read entries, optionally filtered by category and weight."""
    entries = store.get("entries", [])

    if category:
        entries = [e for e in entries if e.get("category") == category]

    if min_weight > 0:
        entries = [e for e in entries if e.get("weight", 0) >= min_weight]

    # Most recent first (entries are appended chronologically, so reverse)
    entries = list(reversed(entries))
    return entries[:limit]


# --- Recall boost (C22-B) ---

def boost_recall(store: dict, entry_ids: list[str]) -> dict:
    """Boost weight and increment recall_count for specified entries.

    Called from MCP stm_read handler only (not from internal read_entries).
    Returns updated store (new copy).
    """
    target_set = set(entry_ids)
    new_entries = []
    for entry in store.get("entries", []):
        if entry.get("id") in target_set:
            entry = dict(entry)
            recall_count = entry.get("recall_count", 0)
            if not isinstance(recall_count, int) or recall_count < 0:
                recall_count = 0
            entry["recall_count"] = recall_count + 1
            entry["weight"] = min(entry.get("weight", 1.0) + BOOST_AMOUNT, MAX_WEIGHT)
        new_entries.append(entry)
    result = dict(store)
    result["entries"] = new_entries
    return result


def format_entries(entries: list[dict]) -> str:
    """Format entries for human-readable output."""
    if not entries:
        return "Short-term memory is empty."

    lines = [f"=== Short-Term Memory ({len(entries)} entries) ===\n"]
    for i, entry in enumerate(entries, 1):
        cat = entry.get("category", "?")
        weight = entry.get("weight", 0.0)
        content = entry.get("content", "")
        ts = entry.get("timestamp", "")[:19]  # trim to seconds
        weight_bar = "●" * max(1, round(weight * 5))  # visual weight indicator
        lines.append(f"  {i}. [{cat}] {weight_bar} (w={weight:.2f}, {ts})")
        # Indent content lines
        for line in content.split("\n"):
            lines.append(f"     {line}")
        lines.append("")

    return "\n".join(lines)


# --- Promote to episode (digest) ---

def promote_entry(store: dict, entry_id: str) -> tuple[dict, dict | None]:
    """Remove an entry from STM and return it for episode promotion.

    Returns (updated_store, removed_entry_or_None).
    """
    entries = list(store.get("entries", []))
    removed = None
    new_entries = []
    for entry in entries:
        if entry.get("id") == entry_id:
            removed = entry
        else:
            new_entries.append(entry)

    store = dict(store)
    store["entries"] = new_entries
    return store, removed


# --- Summary stats ---

def get_stats(store: dict) -> dict:
    """Get summary statistics of the STM store."""
    entries = store.get("entries", [])
    ttl_pruned = store.get("_ttl_pruned_count", 0)

    if not entries:
        return {
            "total": 0,
            "by_category": {},
            "avg_weight": 0.0,
            "session_count": store.get("session_count", 0),
            "ttl_pruned_count": ttl_pruned,
        }

    by_cat: dict[str, int] = {}
    total_weight = 0.0
    for e in entries:
        cat = e.get("category", "unknown")
        by_cat[cat] = by_cat.get(cat, 0) + 1
        total_weight += e.get("weight", 0.0)

    return {
        "total": len(entries),
        "by_category": by_cat,
        "avg_weight": round(total_weight / len(entries), 3),
        "session_count": store.get("session_count", 0),
        "ttl_pruned_count": ttl_pruned,
    }
