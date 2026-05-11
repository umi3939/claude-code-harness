"""Tests for success_registry.py — Success Pattern Extractor.

Tests cover:
- load_registry / save_registry: file I/O, missing/corrupt fallback, atomic write
- record_success: normal, invalid event_type, field length limits, max records (500)
- search_successes: query match, tag filter, limit
- get_stats: event type counts
- backward compatibility: unknown fields preserved
"""

import json
import os
import sys
from pathlib import Path

import pytest

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))
import success_registry

# --- Fixtures ---


@pytest.fixture
def tmp_memory_dir(tmp_path):
    """Provide a temporary memory directory."""
    return str(tmp_path)


# --- load_registry / save_registry ---


class TestLoadRegistry:
    """load_registry: JSON file read with fallback."""

    def test_no_file(self, tmp_memory_dir):
        """Missing file returns empty list."""
        result = success_registry.load_registry(tmp_memory_dir)
        assert result == []

    def test_corrupt_file(self, tmp_memory_dir):
        """Corrupt JSON returns empty list."""
        path = os.path.join(tmp_memory_dir, "success_patterns.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{corrupt json!!")
        result = success_registry.load_registry(tmp_memory_dir)
        assert result == []

    def test_not_a_list(self, tmp_memory_dir):
        """Non-list JSON returns empty list."""
        path = os.path.join(tmp_memory_dir, "success_patterns.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"key": "value"}, f)
        result = success_registry.load_registry(tmp_memory_dir)
        assert result == []

    def test_empty_file(self, tmp_memory_dir):
        """Empty file returns empty list."""
        path = os.path.join(tmp_memory_dir, "success_patterns.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("")
        result = success_registry.load_registry(tmp_memory_dir)
        assert result == []


class TestSaveRegistry:
    """save_registry: atomic write."""

    def test_round_trip(self, tmp_memory_dir):
        """Save then load returns same data."""
        records = [{"id": 1, "event_type": "test_pass", "context": "ctx"}]
        success_registry.save_registry(tmp_memory_dir, records)
        loaded = success_registry.load_registry(tmp_memory_dir)
        assert loaded == records

    def test_creates_directory(self, tmp_memory_dir):
        """save_registry creates memory_dir if missing."""
        subdir = os.path.join(tmp_memory_dir, "sub", "dir")
        records = [{"id": 1}]
        success_registry.save_registry(subdir, records)
        loaded = success_registry.load_registry(subdir)
        assert loaded == records


# --- record_success ---


class TestRecordSuccess:
    """record_success: add a success pattern record."""

    def test_normal(self, tmp_memory_dir):
        """Normal record creation."""
        rec = success_registry.record_success(
            tmp_memory_dir,
            event_type="review_zero",
            context="Clean review pass",
            why_success="Good test coverage",
            tags=["testing", "quality"],
        )
        assert rec["id"] == 1
        assert rec["event_type"] == "review_zero"
        assert rec["context"] == "Clean review pass"
        assert rec["why_success"] == "Good test coverage"
        assert rec["tags"] == ["testing", "quality"]
        assert "recorded_at" in rec

    def test_sequential_ids(self, tmp_memory_dir):
        """IDs increment sequentially."""
        r1 = success_registry.record_success(
            tmp_memory_dir, "test_pass", "ctx1", "why1"
        )
        r2 = success_registry.record_success(
            tmp_memory_dir, "test_pass", "ctx2", "why2"
        )
        assert r1["id"] == 1
        assert r2["id"] == 2

    def test_invalid_event_type(self, tmp_memory_dir):
        """Invalid event_type raises ValueError."""
        with pytest.raises(ValueError, match="event_type"):
            success_registry.record_success(
                tmp_memory_dir, "invalid_type", "ctx", "why"
            )

    def test_context_length_limit(self, tmp_memory_dir):
        """Context exceeding 500 chars is truncated."""
        long_ctx = "x" * 600
        rec = success_registry.record_success(
            tmp_memory_dir, "test_pass", long_ctx, "why"
        )
        assert len(rec["context"]) == 500

    def test_why_success_length_limit(self, tmp_memory_dir):
        """why_success exceeding 1000 chars is truncated."""
        long_why = "y" * 1200
        rec = success_registry.record_success(
            tmp_memory_dir, "test_pass", "ctx", long_why
        )
        assert len(rec["why_success"]) == 1000

    def test_tag_length_limit(self, tmp_memory_dir):
        """Individual tags exceeding 50 chars are truncated."""
        long_tag = "z" * 80
        rec = success_registry.record_success(
            tmp_memory_dir, "test_pass", "ctx", "why", tags=[long_tag]
        )
        assert len(rec["tags"][0]) == 50

    def test_max_tags(self, tmp_memory_dir):
        """More than 10 tags are truncated to 10."""
        tags = [f"tag{i}" for i in range(15)]
        rec = success_registry.record_success(
            tmp_memory_dir, "test_pass", "ctx", "why", tags=tags
        )
        assert len(rec["tags"]) == 10

    def test_max_records_500(self, tmp_memory_dir):
        """Records capped at 500 (oldest removed)."""
        # Pre-fill with 500 records
        records = [
            {
                "id": i,
                "event_type": "test_pass",
                "context": f"ctx{i}",
                "why_success": f"why{i}",
                "tags": [],
                "recorded_at": "2026-01-01T00:00:00+00:00",
            }
            for i in range(1, 501)
        ]
        success_registry.save_registry(tmp_memory_dir, records)

        # Add one more
        rec = success_registry.record_success(
            tmp_memory_dir, "test_pass", "new_ctx", "new_why"
        )
        all_records = success_registry.load_registry(tmp_memory_dir)
        assert len(all_records) == 500
        # Oldest (id=1) should be removed
        ids = [r["id"] for r in all_records]
        assert 1 not in ids
        assert rec["id"] == 501

    def test_default_tags_empty(self, tmp_memory_dir):
        """Tags default to empty list."""
        rec = success_registry.record_success(
            tmp_memory_dir, "user_positive", "ctx", "why"
        )
        assert rec["tags"] == []

    def test_persisted(self, tmp_memory_dir):
        """Record is persisted to disk."""
        success_registry.record_success(
            tmp_memory_dir, "review_zero", "ctx", "why"
        )
        loaded = success_registry.load_registry(tmp_memory_dir)
        assert len(loaded) == 1
        assert loaded[0]["event_type"] == "review_zero"


# --- search_successes ---


class TestSearchSuccesses:
    """search_successes: query and tag filtering."""

    def _seed(self, tmp_memory_dir):
        """Seed some test data."""
        success_registry.record_success(
            tmp_memory_dir, "review_zero", "TDD workflow", "Tests first", tags=["tdd"]
        )
        success_registry.record_success(
            tmp_memory_dir, "test_pass", "API integration", "Mock design", tags=["api"]
        )
        success_registry.record_success(
            tmp_memory_dir,
            "user_positive",
            "Documentation update",
            "Clear examples",
            tags=["docs", "tdd"],
        )

    def test_query_match_context(self, tmp_memory_dir):
        """Query matches against context field."""
        self._seed(tmp_memory_dir)
        results = success_registry.search_successes(tmp_memory_dir, query="TDD")
        assert len(results) >= 1
        assert any("TDD" in r["context"] for r in results)

    def test_query_match_why_success(self, tmp_memory_dir):
        """Query matches against why_success field."""
        self._seed(tmp_memory_dir)
        results = success_registry.search_successes(tmp_memory_dir, query="Mock")
        assert len(results) >= 1
        assert any("Mock" in r["why_success"] for r in results)

    def test_tag_filter(self, tmp_memory_dir):
        """Tags filter results."""
        self._seed(tmp_memory_dir)
        results = success_registry.search_successes(
            tmp_memory_dir, tags=["tdd"]
        )
        assert len(results) == 2
        for r in results:
            assert "tdd" in r["tags"]

    def test_limit(self, tmp_memory_dir):
        """Limit caps results."""
        self._seed(tmp_memory_dir)
        results = success_registry.search_successes(
            tmp_memory_dir, query="", limit=1
        )
        assert len(results) <= 1

    def test_empty_query_returns_all(self, tmp_memory_dir):
        """Empty query returns all (up to limit)."""
        self._seed(tmp_memory_dir)
        results = success_registry.search_successes(tmp_memory_dir)
        assert len(results) == 3

    def test_no_match(self, tmp_memory_dir):
        """No matches returns empty list."""
        self._seed(tmp_memory_dir)
        results = success_registry.search_successes(
            tmp_memory_dir, query="nonexistent_xyz"
        )
        assert results == []


# --- get_stats ---


class TestGetStats:
    """get_stats: event type counts."""

    def test_empty(self, tmp_memory_dir):
        """Empty registry returns zero counts."""
        stats = success_registry.get_stats(tmp_memory_dir)
        assert stats["total"] == 0
        assert stats["review_zero"] == 0
        assert stats["test_pass"] == 0
        assert stats["user_positive"] == 0

    def test_counts(self, tmp_memory_dir):
        """Counts by event type."""
        success_registry.record_success(
            tmp_memory_dir, "review_zero", "ctx1", "why1"
        )
        success_registry.record_success(
            tmp_memory_dir, "review_zero", "ctx2", "why2"
        )
        success_registry.record_success(
            tmp_memory_dir, "test_pass", "ctx3", "why3"
        )
        stats = success_registry.get_stats(tmp_memory_dir)
        assert stats["total"] == 3
        assert stats["review_zero"] == 2
        assert stats["test_pass"] == 1
        assert stats["user_positive"] == 0


# --- Backward compatibility ---


class TestBackwardCompat:
    """Unknown fields in existing records are preserved."""

    def test_unknown_fields_preserved(self, tmp_memory_dir):
        """Records with extra fields survive load/save cycle."""
        records = [
            {
                "id": 1,
                "event_type": "test_pass",
                "context": "ctx",
                "why_success": "why",
                "tags": [],
                "recorded_at": "2026-01-01T00:00:00+00:00",
                "future_field": "keep_me",
            }
        ]
        success_registry.save_registry(tmp_memory_dir, records)
        loaded = success_registry.load_registry(tmp_memory_dir)
        assert loaded[0]["future_field"] == "keep_me"
