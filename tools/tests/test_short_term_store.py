#!/usr/bin/env python3
"""Tests for short_term_store.py"""

import json
import os
import sys
import tempfile

import pytest

# Add tools dir to path
TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from datetime import datetime, timedelta, timezone

from short_term_store import (
    CATEGORY_RULES,
    DECAY_RATE,
    MAX_ENTRIES,
    MIN_ENTRIES,
    MIN_WEIGHT,
    VALID_CATEGORIES,
    apply_session_decay,
    format_entries,
    get_stats,
    load_store,
    promote_entry,
    read_entries,
    save_store,
    write_entry,
)


@pytest.fixture
def tmp_memory_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


# --- load/save ---

class TestLoadSave:
    def test_load_empty(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        assert store["entries"] == []
        assert store["session_count"] == 0

    def test_save_and_load_roundtrip(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        store = write_entry(store, "test thought", "thought")
        save_store(tmp_memory_dir, store)

        loaded = load_store(tmp_memory_dir)
        assert len(loaded["entries"]) == 1
        assert loaded["entries"][0]["content"] == "test thought"

    def test_load_corrupted_file(self, tmp_memory_dir):
        path = os.path.join(tmp_memory_dir, "short_term_memory.json")
        with open(path, "w") as f:
            f.write("not json{{{")
        store = load_store(tmp_memory_dir)
        assert store["entries"] == []

    def test_save_atomic(self, tmp_memory_dir):
        store = write_entry(load_store(tmp_memory_dir), "entry1")
        result = save_store(tmp_memory_dir, store)
        assert not result.startswith("ERROR")
        assert os.path.exists(result)


# --- write ---

class TestWrite:
    def test_write_basic(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        store = write_entry(store, "a thought", "thought")
        assert len(store["entries"]) == 1
        assert store["entries"][0]["category"] == "thought"
        assert store["entries"][0]["weight"] == 1.0

    def test_write_all_categories(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        for cat in VALID_CATEGORIES:
            store = write_entry(store, f"test {cat}", cat)
        assert len(store["entries"]) == len(VALID_CATEGORIES)

    def test_self_review_is_valid_category(self, tmp_memory_dir):
        assert "self_review" in VALID_CATEGORIES

    def test_write_self_review(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        store = write_entry(store, "Why: X. Scale: Y. Past: Z.", "self_review")
        assert len(store["entries"]) == 1
        assert store["entries"][0]["category"] == "self_review"

    def test_write_invalid_category_defaults_to_thought(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        store = write_entry(store, "test", "invalid_cat")
        assert store["entries"][0]["category"] == "thought"

    def test_write_with_emotion(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        emotion = {"fulfillment": 0.5, "tension": 0.1, "affinity": 0.8}
        store = write_entry(store, "warm feeling", "feeling", emotion)
        assert store["entries"][0]["emotion_snapshot"] == emotion

    def test_fifo_trim(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        for i in range(MAX_ENTRIES + 10):
            store = write_entry(store, f"entry {i}")
        assert len(store["entries"]) == MAX_ENTRIES
        # Oldest should be trimmed, newest kept
        assert store["entries"][-1]["content"] == f"entry {MAX_ENTRIES + 9}"

    def test_content_truncation(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        long_content = "x" * 5000
        store = write_entry(store, long_content)
        assert len(store["entries"][0]["content"]) < 5000
        assert store["entries"][0]["content"].endswith("...")

    def test_entry_has_id(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        store = write_entry(store, "test")
        assert "id" in store["entries"][0]
        assert len(store["entries"][0]["id"]) == 12

    def test_entry_has_timestamp(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        store = write_entry(store, "test")
        assert "timestamp" in store["entries"][0]


# --- session decay ---

class TestSessionDecay:
    def test_decay_reduces_weight(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        store = write_entry(store, "test")  # category="thought", decay_factor=0.75
        assert store["entries"][0]["weight"] == 1.0

        store, pruned = apply_session_decay(store)
        # thought: DECAY_RATE * category_factor = 0.75 * 0.75 = 0.5625
        expected = round(DECAY_RATE * CATEGORY_RULES["thought"]["decay_factor"], 4)
        assert store["entries"][0]["weight"] == expected
        assert pruned == 0

    def test_decay_prunes_low_weight(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        store = write_entry(store, "will survive")
        store = write_entry(store, "will die")
        # Manually set low weight
        store["entries"][1]["weight"] = MIN_WEIGHT - 0.01

        store, pruned = apply_session_decay(store)
        assert pruned == 1
        assert len(store["entries"]) == 1
        assert store["entries"][0]["content"] == "will survive"

    def test_multiple_decay_rounds(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        store = write_entry(store, "test")

        # Apply decay multiple times
        for _ in range(10):
            store, _ = apply_session_decay(store)

        # After 10 rounds: 0.75^10 ≈ 0.056 < MIN_WEIGHT=0.10
        assert len(store["entries"]) == 0

    def test_decay_increments_session_count(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        assert store["session_count"] == 0
        store, _ = apply_session_decay(store)
        assert store["session_count"] == 1
        store, _ = apply_session_decay(store)
        assert store["session_count"] == 2

    def test_new_entry_survives_while_old_pruned(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        store = write_entry(store, "old entry")

        # Decay 5 times: 0.75^5 ≈ 0.237 > 0.10, still alive
        for _ in range(5):
            store, _ = apply_session_decay(store)

        # Add new entry
        store = write_entry(store, "new entry")

        # Decay 2 more times
        for _ in range(2):
            store, _ = apply_session_decay(store)

        # Old: 0.75^7 ≈ 0.133 > 0.10 (barely alive)
        # New: 0.75^2 ≈ 0.5625
        entries = store["entries"]
        alive_contents = [e["content"] for e in entries]
        assert "new entry" in alive_contents


# --- read ---

class TestRead:
    def test_read_all(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        store = write_entry(store, "a")
        store = write_entry(store, "b")
        entries = read_entries(store)
        assert len(entries) == 2

    def test_read_by_category(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        store = write_entry(store, "thought1", "thought")
        store = write_entry(store, "question1", "question")
        store = write_entry(store, "thought2", "thought")

        thoughts = read_entries(store, category="thought")
        assert len(thoughts) == 2
        questions = read_entries(store, category="question")
        assert len(questions) == 1

    def test_read_with_limit(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        for i in range(10):
            store = write_entry(store, f"entry {i}")
        entries = read_entries(store, limit=3)
        assert len(entries) == 3

    def test_read_most_recent_first(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        store = write_entry(store, "first")
        store = write_entry(store, "second")
        entries = read_entries(store)
        assert entries[0]["content"] == "second"

    def test_read_by_min_weight(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        store = write_entry(store, "heavy")
        store = write_entry(store, "light")
        store["entries"][1]["weight"] = 0.3
        entries = read_entries(store, min_weight=0.5)
        assert len(entries) == 1
        assert entries[0]["content"] == "heavy"

    def test_read_empty(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        entries = read_entries(store)
        assert entries == []


# --- format ---

class TestFormat:
    def test_format_empty(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        result = format_entries(read_entries(store))
        assert "empty" in result.lower()

    def test_format_with_entries(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        store = write_entry(store, "a thought")
        result = format_entries(read_entries(store))
        assert "a thought" in result
        assert "[thought]" in result


# --- promote ---

class TestPromote:
    def test_promote_removes_entry(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        store = write_entry(store, "to promote")
        entry_id = store["entries"][0]["id"]

        store, removed = promote_entry(store, entry_id)
        assert len(store["entries"]) == 0
        assert removed["content"] == "to promote"

    def test_promote_nonexistent_id(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        store = write_entry(store, "keep")

        store, removed = promote_entry(store, "nonexistent")
        assert len(store["entries"]) == 1
        assert removed is None

    def test_promote_only_target(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        store = write_entry(store, "keep1")
        store = write_entry(store, "promote_me")
        store = write_entry(store, "keep2")
        target_id = store["entries"][1]["id"]

        store, removed = promote_entry(store, target_id)
        assert len(store["entries"]) == 2
        assert removed["content"] == "promote_me"
        contents = [e["content"] for e in store["entries"]]
        assert "keep1" in contents
        assert "keep2" in contents


# --- stats ---

class TestStats:
    def test_stats_empty(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        stats = get_stats(store)
        assert stats["total"] == 0
        assert stats["avg_weight"] == 0.0

    def test_stats_with_entries(self, tmp_memory_dir):
        store = load_store(tmp_memory_dir)
        store = write_entry(store, "t1", "thought")
        store = write_entry(store, "q1", "question")
        store = write_entry(store, "t2", "thought")
        stats = get_stats(store)
        assert stats["total"] == 3
        assert stats["by_category"]["thought"] == 2
        assert stats["by_category"]["question"] == 1
        assert stats["avg_weight"] == 1.0

    def test_stats_includes_ttl_pruned_count(self, tmp_memory_dir):
        """get_stats should include ttl_pruned_count field."""
        store = load_store(tmp_memory_dir)
        stats = get_stats(store)
        assert "ttl_pruned_count" in stats
        assert stats["ttl_pruned_count"] == 0


# --- MAX_ENTRIES constant ---

class TestMaxEntries:
    def test_max_entries_is_200(self):
        """MAX_ENTRIES should be 200 (raised from 50)."""
        assert MAX_ENTRIES == 200

    def test_fifo_trim_at_200(self, tmp_memory_dir):
        """FIFO trim should work at the new 200 limit."""
        store = load_store(tmp_memory_dir)
        for i in range(210):
            store = write_entry(store, f"entry {i}")
        assert len(store["entries"]) == 200
        assert store["entries"][-1]["content"] == "entry 209"
        assert store["entries"][0]["content"] == "entry 10"


# --- Category rules ---

class TestCategoryRules:
    def test_all_valid_categories_have_rules(self):
        """Every valid category should have an entry in CATEGORY_RULES."""
        for cat in VALID_CATEGORIES:
            assert cat in CATEGORY_RULES, f"{cat} missing from CATEGORY_RULES"

    def test_category_rules_have_ttl_and_decay_factor(self):
        """Each rule should have ttl_days and decay_factor keys."""
        for cat, rule in CATEGORY_RULES.items():
            assert "ttl_days" in rule, f"{cat} missing ttl_days"
            assert "decay_factor" in rule, f"{cat} missing decay_factor"

    def test_self_review_ttl_3_days(self):
        assert CATEGORY_RULES["self_review"]["ttl_days"] == 3

    def test_thought_ttl_14_days(self):
        assert CATEGORY_RULES["thought"]["ttl_days"] == 14

    def test_question_ttl_21_days(self):
        assert CATEGORY_RULES["question"]["ttl_days"] == 21

    def test_impression_ttl_21_days(self):
        assert CATEGORY_RULES["impression"]["ttl_days"] == 21

    def test_unresolved_ttl_30_days(self):
        assert CATEGORY_RULES["unresolved"]["ttl_days"] == 30

    def test_feeling_ttl_14_days(self):
        assert CATEGORY_RULES["feeling"]["ttl_days"] == 14

    def test_self_review_decay_factor_05(self):
        assert CATEGORY_RULES["self_review"]["decay_factor"] == 0.5

    def test_thought_decay_factor_075(self):
        assert CATEGORY_RULES["thought"]["decay_factor"] == 0.75

    def test_question_decay_factor_085(self):
        assert CATEGORY_RULES["question"]["decay_factor"] == 0.85

    def test_impression_decay_factor_080(self):
        assert CATEGORY_RULES["impression"]["decay_factor"] == 0.80

    def test_unresolved_decay_factor_090(self):
        assert CATEGORY_RULES["unresolved"]["decay_factor"] == 0.90

    def test_feeling_decay_factor_070(self):
        assert CATEGORY_RULES["feeling"]["decay_factor"] == 0.70


# --- MIN_ENTRIES constant ---

class TestMinEntries:
    def test_min_entries_is_10(self):
        """MIN_ENTRIES safety floor should be 10."""
        assert MIN_ENTRIES == 10


# --- TTL pruning ---

def _make_entry(category="thought", age_days=0, weight=1.0, content="test"):
    """Helper: create an entry with a specific age."""
    ts = datetime.now(timezone.utc) - timedelta(days=age_days)
    return {
        "id": "test" + str(age_days),
        "category": category,
        "content": content,
        "timestamp": ts.isoformat(),
        "weight": weight,
        "session_created": 0,
    }


class TestTTLPruning:
    def test_ttl_prunes_expired_self_review(self, tmp_memory_dir):
        """self_review with TTL=3 days should be pruned after 4 days."""
        store = load_store(tmp_memory_dir)
        store["entries"] = [
            _make_entry("self_review", age_days=4, content="old review"),
            _make_entry("thought", age_days=1, content="recent thought"),
        ]
        store, pruned = apply_session_decay(store)
        contents = [e["content"] for e in store["entries"]]
        assert "old review" not in contents
        assert "recent thought" in contents

    def test_ttl_keeps_non_expired_self_review(self, tmp_memory_dir):
        """self_review within TTL=3 days should survive."""
        store = load_store(tmp_memory_dir)
        store["entries"] = [
            _make_entry("self_review", age_days=2, content="recent review"),
        ]
        store, pruned = apply_session_decay(store)
        contents = [e["content"] for e in store["entries"]]
        assert "recent review" in contents

    def test_ttl_prunes_expired_thought(self, tmp_memory_dir):
        """thought with TTL=14 days should be pruned after 15 days."""
        store = load_store(tmp_memory_dir)
        store["entries"] = [
            _make_entry("thought", age_days=15, content="old thought"),
        ]
        store, pruned = apply_session_decay(store)
        assert len(store["entries"]) == 0

    def test_ttl_prunes_expired_unresolved(self, tmp_memory_dir):
        """unresolved with TTL=30 days should be pruned after 31 days."""
        store = load_store(tmp_memory_dir)
        store["entries"] = [
            _make_entry("unresolved", age_days=31, content="old unresolved"),
        ]
        store, pruned = apply_session_decay(store)
        assert len(store["entries"]) == 0

    def test_ttl_keeps_unresolved_within_ttl(self, tmp_memory_dir):
        """unresolved within 30 days should survive."""
        store = load_store(tmp_memory_dir)
        store["entries"] = [
            _make_entry("unresolved", age_days=29, content="recent unresolved"),
        ]
        store, pruned = apply_session_decay(store)
        contents = [e["content"] for e in store["entries"]]
        assert "recent unresolved" in contents

    def test_ttl_pruned_count_in_return(self, tmp_memory_dir):
        """apply_session_decay should return total pruned count including TTL."""
        store = load_store(tmp_memory_dir)
        store["entries"] = [
            _make_entry("self_review", age_days=4, content="expired1"),
            _make_entry("self_review", age_days=5, content="expired2"),
            _make_entry("thought", age_days=1, content="alive"),
        ]
        store, pruned = apply_session_decay(store)
        # 2 TTL-pruned, 0 weight-pruned
        assert pruned >= 2

    def test_ttl_none_means_no_expiry(self, tmp_memory_dir):
        """If a category's TTL is None, entries never expire by TTL."""
        # We test this by checking the CATEGORY_RULES structure allows None
        # and that unknown categories get default (None TTL)
        store = load_store(tmp_memory_dir)
        # Use an entry with unknown category (defaults to thought by write,
        # but we can manually set it to test fallback)
        entry = _make_entry("thought", age_days=100, content="ancient")
        entry["category"] = "unknown_future_cat"
        store["entries"] = [entry]
        store, pruned = apply_session_decay(store)
        # Unknown category should use default: no TTL expiry (None)
        # It may be pruned by weight decay though, so check TTL didn't kill it
        # Weight: 1.0 * DECAY_RATE * default_factor(1.0) = 0.75 >= MIN_WEIGHT
        contents = [e["content"] for e in store["entries"]]
        assert "ancient" in contents

    def test_ttl_processing_order_decay_then_ttl(self, tmp_memory_dir):
        """Design spec: order is (1)weight decay -> (2)MIN_WEIGHT prune -> (3)TTL prune."""
        store = load_store(tmp_memory_dir)
        # Entry that would be pruned by weight decay (step 2) AND by TTL (step 3)
        store["entries"] = [
            _make_entry("self_review", age_days=4, weight=0.05, content="both"),
            _make_entry("thought", age_days=1, weight=1.0, content="healthy"),
        ]
        store, pruned = apply_session_decay(store)
        # "both" should be gone (weight too low AND TTL expired)
        contents = [e["content"] for e in store["entries"]]
        assert "both" not in contents
        assert "healthy" in contents
        # Total pruned should be at least 1
        assert pruned >= 1

    def test_last_ttl_sweep_at_updated(self, tmp_memory_dir):
        """apply_session_decay should update last_ttl_sweep_at."""
        store = load_store(tmp_memory_dir)
        assert store.get("last_ttl_sweep_at") is None
        store, _ = apply_session_decay(store)
        assert store["last_ttl_sweep_at"] is not None


# --- Category-specific decay factors ---

class TestCategoryDecayFactors:
    def test_self_review_decays_faster(self, tmp_memory_dir):
        """self_review (factor=0.5) should decay faster than thought (factor=0.75)."""
        store = load_store(tmp_memory_dir)
        store["entries"] = [
            _make_entry("self_review", age_days=0, weight=1.0, content="review"),
            _make_entry("thought", age_days=0, weight=1.0, content="thought"),
        ]
        store, _ = apply_session_decay(store)
        review = next(e for e in store["entries"] if e["content"] == "review")
        thought = next(e for e in store["entries"] if e["content"] == "thought")
        # self_review: 1.0 * DECAY_RATE * 0.5 = 0.75 * 0.5 = 0.375
        # thought: 1.0 * DECAY_RATE * 0.75 = 0.75 * 0.75 = 0.5625
        assert review["weight"] < thought["weight"]
        assert abs(review["weight"] - round(DECAY_RATE * 0.5, 4)) < 0.001
        assert abs(thought["weight"] - round(DECAY_RATE * 0.75, 4)) < 0.001

    def test_unresolved_decays_slowest(self, tmp_memory_dir):
        """unresolved (factor=0.90) should decay slower than all others."""
        store = load_store(tmp_memory_dir)
        store["entries"] = [
            _make_entry("unresolved", age_days=0, weight=1.0, content="unresolved"),
            _make_entry("feeling", age_days=0, weight=1.0, content="feeling"),
        ]
        store, _ = apply_session_decay(store)
        unresolved = next(e for e in store["entries"] if e["content"] == "unresolved")
        feeling = next(e for e in store["entries"] if e["content"] == "feeling")
        # unresolved: 1.0 * 0.75 * 0.90 = 0.675
        # feeling: 1.0 * 0.75 * 0.70 = 0.525
        assert unresolved["weight"] > feeling["weight"]

    def test_unknown_category_uses_default_factor(self, tmp_memory_dir):
        """Unknown category should use decay_factor=1.0 (standard DECAY_RATE)."""
        store = load_store(tmp_memory_dir)
        entry = _make_entry("thought", age_days=0, weight=1.0, content="unknown")
        entry["category"] = "future_category"
        store["entries"] = [entry]
        store, _ = apply_session_decay(store)
        # Should use factor 1.0: 1.0 * 0.75 * 1.0 = 0.75
        assert abs(store["entries"][0]["weight"] - round(DECAY_RATE, 4)) < 0.001

    def test_question_decay_factor(self, tmp_memory_dir):
        """question (factor=0.85) should produce correct weight."""
        store = load_store(tmp_memory_dir)
        store["entries"] = [
            _make_entry("question", age_days=0, weight=1.0, content="q"),
        ]
        store, _ = apply_session_decay(store)
        expected = round(DECAY_RATE * 0.85, 4)
        assert abs(store["entries"][0]["weight"] - expected) < 0.001


# --- Safety floor (MIN_ENTRIES) ---

class TestSafetyFloor:
    def test_min_entries_floor_prevents_total_wipeout(self, tmp_memory_dir):
        """Even if all entries are TTL-expired, at least MIN_ENTRIES should remain."""
        store = load_store(tmp_memory_dir)
        # Create 15 entries, all TTL-expired
        store["entries"] = [
            _make_entry("self_review", age_days=10, weight=1.0, content=f"expired_{i}")
            for i in range(15)
        ]
        store, pruned = apply_session_decay(store)
        # Should keep at least MIN_ENTRIES=10
        assert len(store["entries"]) >= MIN_ENTRIES

    def test_min_entries_floor_with_weight_decay(self, tmp_memory_dir):
        """Combined TTL + weight pruning should not go below MIN_ENTRIES."""
        store = load_store(tmp_memory_dir)
        # 12 entries: all TTL-expired AND low weight
        store["entries"] = [
            _make_entry("self_review", age_days=10, weight=0.05, content=f"doomed_{i}")
            for i in range(12)
        ]
        store, pruned = apply_session_decay(store)
        # Even though both mechanisms want to prune, floor of 10 applies
        assert len(store["entries"]) >= MIN_ENTRIES

    def test_below_min_entries_ttl_still_applies(self, tmp_memory_dir):
        """If total entries < MIN_ENTRIES, floor does NOT apply.
        TTL and weight pruning operate normally."""
        store = load_store(tmp_memory_dir)
        # Only 5 entries (below floor), all TTL-expired
        store["entries"] = [
            _make_entry("self_review", age_days=10, weight=1.0, content=f"few_{i}")
            for i in range(5)
        ]
        store, pruned = apply_session_decay(store)
        # Weight: 1.0 * 0.75 * 0.5 = 0.375 >= MIN_WEIGHT, so weight doesn't prune
        # TTL: all expired (age 10 > ttl 3). Floor only applies when
        # original_count >= MIN_ENTRIES, so with 5 entries floor is NOT active.
        # All 5 should be TTL-pruned.
        assert len(store["entries"]) == 0
        assert pruned == 5

    def test_floor_keeps_newest_entries(self, tmp_memory_dir):
        """When floor prevents total pruning, the most recent entries should be kept."""
        store = load_store(tmp_memory_dir)
        entries = []
        for i in range(15):
            # All expired, but with different ages
            entries.append(
                _make_entry("self_review", age_days=10 + i, weight=1.0, content=f"entry_{i}")
            )
        store["entries"] = entries
        store, pruned = apply_session_decay(store)
        # Should keep MIN_ENTRIES=10, and they should be the newest ones
        assert len(store["entries"]) >= MIN_ENTRIES
        # The kept entries should be the ones with lowest age (most recent)
        kept_contents = [e["content"] for e in store["entries"]]
        # entry_0 has age 10, entry_14 has age 24 -- entry_0..9 are newest
        for i in range(10):
            assert f"entry_{i}" in kept_contents


# --- load_store migration ---

class TestLoadStoreMigration:
    def test_load_legacy_store_without_last_ttl_sweep_at(self, tmp_memory_dir):
        """Loading a store without last_ttl_sweep_at should add the field."""
        path = os.path.join(tmp_memory_dir, "short_term_memory.json")
        legacy = {
            "entries": [],
            "last_session_decay_at": None,
            "session_count": 3,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(legacy, f)
        store = load_store(tmp_memory_dir)
        assert "last_ttl_sweep_at" in store
        assert store["last_ttl_sweep_at"] is None
        assert store["session_count"] == 3

    def test_load_store_with_last_ttl_sweep_at_preserves(self, tmp_memory_dir):
        """Loading a store with last_ttl_sweep_at should preserve the value."""
        path = os.path.join(tmp_memory_dir, "short_term_memory.json")
        data = {
            "entries": [],
            "last_session_decay_at": None,
            "last_ttl_sweep_at": "2026-03-20T00:00:00+00:00",
            "session_count": 5,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        store = load_store(tmp_memory_dir)
        assert store["last_ttl_sweep_at"] == "2026-03-20T00:00:00+00:00"

    def test_empty_store_has_last_ttl_sweep_at(self):
        """A fresh empty store should have last_ttl_sweep_at=None."""
        from short_term_store import _empty_store
        store = _empty_store()
        assert "last_ttl_sweep_at" in store
        assert store["last_ttl_sweep_at"] is None


# --- C22-B: Use-Based Reinforcement ---

class TestBoostRecall:
    """Tests for boost_recall (STM reference boost)."""

    def test_recall_count_incremented(self):
        """boost_recall increments recall_count by 1 for specified entries."""
        from short_term_store import boost_recall
        store = {"entries": [
            {"id": "a", "weight": 0.5, "recall_count": 0},
            {"id": "b", "weight": 0.5, "recall_count": 2},
            {"id": "c", "weight": 0.5},
        ], "session_count": 1}
        updated = boost_recall(store, ["a", "b", "c"])
        by_id = {e["id"]: e for e in updated["entries"]}
        assert by_id["a"]["recall_count"] == 1
        assert by_id["b"]["recall_count"] == 3
        assert by_id["c"]["recall_count"] == 1  # backward compat: missing -> 0 + 1

    def test_weight_boosted_by_amount(self):
        """boost_recall adds BOOST_AMOUNT (0.3) to weight."""
        from short_term_store import BOOST_AMOUNT, boost_recall
        store = {"entries": [
            {"id": "a", "weight": 0.5, "recall_count": 0},
        ], "session_count": 1}
        updated = boost_recall(store, ["a"])
        entry = updated["entries"][0]
        assert abs(entry["weight"] - (0.5 + BOOST_AMOUNT)) < 1e-9

    def test_weight_clipped_at_max(self):
        """boost_recall clips weight at MAX_WEIGHT (1.0)."""
        from short_term_store import MAX_WEIGHT, boost_recall
        store = {"entries": [
            {"id": "a", "weight": 0.95, "recall_count": 0},
        ], "session_count": 1}
        updated = boost_recall(store, ["a"])
        entry = updated["entries"][0]
        assert entry["weight"] == MAX_WEIGHT

    def test_boost_amount_is_0_1(self):
        """BOOST_AMOUNT should be 0.1 (P4-4: weight recovery on read)."""
        from short_term_store import BOOST_AMOUNT
        assert BOOST_AMOUNT == 0.1

    def test_backward_compat_missing_recall_count(self):
        """Entries without recall_count field are treated as recall_count=0."""
        from short_term_store import boost_recall
        store = {"entries": [
            {"id": "a", "weight": 0.5},  # no recall_count
        ], "session_count": 1}
        updated = boost_recall(store, ["a"])
        entry = updated["entries"][0]
        assert entry["recall_count"] == 1

    def test_non_targeted_entries_unchanged(self):
        """Entries not in entry_ids list are not modified."""
        from short_term_store import boost_recall
        store = {"entries": [
            {"id": "a", "weight": 0.5, "recall_count": 0},
            {"id": "b", "weight": 0.6, "recall_count": 1},
        ], "session_count": 1}
        updated = boost_recall(store, ["a"])
        by_id = {e["id"]: e for e in updated["entries"]}
        assert by_id["b"]["weight"] == 0.6
        assert by_id["b"]["recall_count"] == 1


class TestDecayRecallResistance:
    """Tests for recall-based decay resistance in apply_session_decay."""

    def test_decay_resistance_reduces_effective_decay(self):
        """Higher recall_count should result in less weight loss during decay."""
        store = {"entries": [
            {"id": "no_recall", "weight": 1.0, "recall_count": 0,
             "category": "thought", "timestamp": "2026-03-28T00:00:00Z"},
            {"id": "recalled", "weight": 1.0, "recall_count": 4,
             "category": "thought", "timestamp": "2026-03-28T00:00:00Z"},
        ], "session_count": 1}
        updated, _ = apply_session_decay(store)
        by_id = {e["id"]: e for e in updated["entries"]}
        assert by_id["recalled"]["weight"] > by_id["no_recall"]["weight"]

    def test_recall_resistance_capped_at_max(self):
        """Resistance is capped at MAX_RECALL_RESISTANCE (0.3)."""
        store = {"entries": [
            {"id": "a", "weight": 1.0, "recall_count": 100,
             "category": "thought", "timestamp": "2026-03-28T00:00:00Z"},
            {"id": "b", "weight": 1.0, "recall_count": 6,
             "category": "thought", "timestamp": "2026-03-28T00:00:00Z"},
        ], "session_count": 1}
        updated, _ = apply_session_decay(store)
        by_id = {e["id"]: e for e in updated["entries"]}
        assert abs(by_id["a"]["weight"] - by_id["b"]["weight"]) < 1e-4

    def test_effective_rate_never_reaches_one(self):
        """Decay never completely stops even with max resistance."""
        store = {"entries": [
            {"id": "a", "weight": 1.0, "recall_count": 100,
             "category": "thought", "timestamp": "2026-03-28T00:00:00Z"},
        ], "session_count": 1}
        updated, _ = apply_session_decay(store)
        entry = updated["entries"][0]
        assert entry["weight"] < 1.0
