"""Tests for after_action_review.py — After-Action Success Review (C22-J).

Tests cover:
- load_store: file I/O, missing/corrupt fallback, atomic write
- create_aar: normal, required fields validation, truncation, max records, tags
- search_aars: query matching, tag filtering, limit
- get_aar_report: formatted text output
- Edge cases: empty data, boundary values, invalid input
"""

import json
import os
import sys
from pathlib import Path

import pytest

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))
import after_action_review

# --- Fixtures ---


@pytest.fixture
def tmp_memory_dir(tmp_path):
    """Provide a temporary memory directory."""
    return str(tmp_path)


# --- load_store ---


class TestLoadStore:
    """load_store: JSON file read with fallback."""

    def test_no_file(self, tmp_memory_dir):
        """Missing file returns empty list."""
        result = after_action_review.load_store(tmp_memory_dir)
        assert result == []

    def test_corrupt_file(self, tmp_memory_dir):
        """Corrupt JSON returns empty list."""
        path = os.path.join(tmp_memory_dir, after_action_review.STORE_FILENAME)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{corrupt json!!")
        result = after_action_review.load_store(tmp_memory_dir)
        assert result == []

    def test_not_a_list(self, tmp_memory_dir):
        """Non-list JSON returns empty list."""
        path = os.path.join(tmp_memory_dir, after_action_review.STORE_FILENAME)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"not": "a list"}, f)
        result = after_action_review.load_store(tmp_memory_dir)
        assert result == []

    def test_empty_file(self, tmp_memory_dir):
        """Empty file returns empty list."""
        path = os.path.join(tmp_memory_dir, after_action_review.STORE_FILENAME)
        with open(path, "w", encoding="utf-8") as f:
            f.write("")
        result = after_action_review.load_store(tmp_memory_dir)
        assert result == []

    def test_valid_data(self, tmp_memory_dir):
        """Valid JSON list is loaded correctly."""
        records = [{"id": 1, "intent": "test"}]
        path = os.path.join(tmp_memory_dir, after_action_review.STORE_FILENAME)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(records, f)
        result = after_action_review.load_store(tmp_memory_dir)
        assert result == records


# --- create_aar ---


class TestCreateAAR:
    """create_aar: record creation and validation."""

    def _make_kwargs(self, **overrides):
        defaults = dict(
            intent="Deploy pipeline",
            actual="Deployed smoothly",
            why_success="Good testing",
            replicable="Staging first",
            context_dependent="Team availability",
            transferable="Any deployment",
        )
        defaults.update(overrides)
        return defaults

    def test_basic_creation(self, tmp_memory_dir):
        """Creates a record with all fields and auto-incremented ID."""
        rec = after_action_review.create_aar(tmp_memory_dir, **self._make_kwargs())
        assert rec["id"] == 1
        assert rec["intent"] == "Deploy pipeline"
        assert rec["why_success"] == "Good testing"
        assert "recorded_at" in rec
        assert rec["tags"] == []

    def test_sequential_ids(self, tmp_memory_dir):
        """IDs auto-increment."""
        r1 = after_action_review.create_aar(tmp_memory_dir, **self._make_kwargs())
        r2 = after_action_review.create_aar(tmp_memory_dir, **self._make_kwargs(intent="Second"))
        assert r1["id"] == 1
        assert r2["id"] == 2

    def test_with_tags(self, tmp_memory_dir):
        """Tags are stored correctly."""
        rec = after_action_review.create_aar(
            tmp_memory_dir, **self._make_kwargs(), tags=["deploy", "ci"]
        )
        assert rec["tags"] == ["deploy", "ci"]

    def test_tags_max_count(self, tmp_memory_dir):
        """Tags beyond MAX_TAGS are dropped."""
        tags = [f"tag{i}" for i in range(15)]
        rec = after_action_review.create_aar(
            tmp_memory_dir, **self._make_kwargs(), tags=tags
        )
        assert len(rec["tags"]) == after_action_review.MAX_TAGS

    def test_tags_truncated(self, tmp_memory_dir):
        """Long tag strings are truncated."""
        long_tag = "x" * 100
        rec = after_action_review.create_aar(
            tmp_memory_dir, **self._make_kwargs(), tags=[long_tag]
        )
        assert len(rec["tags"][0]) == after_action_review.MAX_TAG_LEN

    def test_empty_intent_raises(self, tmp_memory_dir):
        """Empty intent raises ValueError."""
        with pytest.raises(ValueError, match="intent"):
            after_action_review.create_aar(
                tmp_memory_dir, **self._make_kwargs(intent="")
            )

    def test_whitespace_intent_raises(self, tmp_memory_dir):
        """Whitespace-only intent raises ValueError."""
        with pytest.raises(ValueError, match="intent"):
            after_action_review.create_aar(
                tmp_memory_dir, **self._make_kwargs(intent="   ")
            )

    def test_empty_why_success_raises(self, tmp_memory_dir):
        """Empty why_success raises ValueError."""
        with pytest.raises(ValueError, match="why_success"):
            after_action_review.create_aar(
                tmp_memory_dir, **self._make_kwargs(why_success="")
            )

    def test_field_truncation(self, tmp_memory_dir):
        """Fields exceeding MAX_FIELD_LEN are truncated."""
        long_text = "a" * 2000
        rec = after_action_review.create_aar(
            tmp_memory_dir, **self._make_kwargs(intent=long_text)
        )
        assert len(rec["intent"]) == after_action_review.MAX_FIELD_LEN

    def test_max_records_enforced(self, tmp_memory_dir):
        """Records beyond MAX_RECORDS are trimmed (oldest removed)."""
        # Pre-populate store with MAX_RECORDS entries
        records = [
            {"id": i, "intent": f"old-{i}", "recorded_at": "2025-01-01T00:00:00+00:00"}
            for i in range(1, after_action_review.MAX_RECORDS + 1)
        ]
        path = os.path.join(tmp_memory_dir, after_action_review.STORE_FILENAME)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(records, f)

        after_action_review.create_aar(tmp_memory_dir, **self._make_kwargs())
        stored = after_action_review.load_store(tmp_memory_dir)
        assert len(stored) == after_action_review.MAX_RECORDS
        # Oldest (id=1) should be trimmed
        ids = [r["id"] for r in stored]
        assert 1 not in ids

    def test_persisted_to_file(self, tmp_memory_dir):
        """Record is persisted to disk."""
        after_action_review.create_aar(tmp_memory_dir, **self._make_kwargs())
        path = os.path.join(tmp_memory_dir, after_action_review.STORE_FILENAME)
        assert os.path.exists(path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert len(data) == 1

    def test_none_tags_handled(self, tmp_memory_dir):
        """tags=None produces empty list."""
        rec = after_action_review.create_aar(
            tmp_memory_dir, **self._make_kwargs(), tags=None
        )
        assert rec["tags"] == []


# --- search_aars ---


class TestSearchAARs:
    """search_aars: query and tag filtering."""

    def _seed(self, tmp_memory_dir, count=3):
        """Seed some records."""
        for i in range(count):
            after_action_review.create_aar(
                tmp_memory_dir,
                intent=f"Intent-{i}",
                actual=f"Actual-{i}",
                why_success=f"Why-{i}",
                replicable=f"Replicable-{i}",
                context_dependent=f"Context-{i}",
                transferable=f"Transfer-{i}",
                tags=[f"tag-{i}", "common"],
            )

    def test_empty_store(self, tmp_memory_dir):
        """Empty store returns empty list."""
        result = after_action_review.search_aars(tmp_memory_dir)
        assert result == []

    def test_no_filter(self, tmp_memory_dir):
        """No query/tags returns all (up to limit)."""
        self._seed(tmp_memory_dir)
        result = after_action_review.search_aars(tmp_memory_dir)
        assert len(result) == 3

    def test_query_match(self, tmp_memory_dir):
        """Query filters by content field text."""
        self._seed(tmp_memory_dir)
        result = after_action_review.search_aars(tmp_memory_dir, query="Intent-1")
        assert len(result) == 1
        assert result[0]["intent"] == "Intent-1"

    def test_query_case_insensitive(self, tmp_memory_dir):
        """Query matching is case-insensitive."""
        self._seed(tmp_memory_dir)
        result = after_action_review.search_aars(tmp_memory_dir, query="intent-2")
        assert len(result) == 1

    def test_tag_filter(self, tmp_memory_dir):
        """Tag filtering returns matching records."""
        self._seed(tmp_memory_dir)
        result = after_action_review.search_aars(tmp_memory_dir, tags=["tag-0"])
        assert len(result) == 1
        assert "tag-0" in result[0]["tags"]

    def test_tag_common(self, tmp_memory_dir):
        """Common tag matches all records."""
        self._seed(tmp_memory_dir)
        result = after_action_review.search_aars(tmp_memory_dir, tags=["common"])
        assert len(result) == 3

    def test_limit(self, tmp_memory_dir):
        """Limit caps results."""
        self._seed(tmp_memory_dir, count=10)
        result = after_action_review.search_aars(tmp_memory_dir, limit=3)
        assert len(result) == 3

    def test_results_ordered_newest_first(self, tmp_memory_dir):
        """Results are ordered by ID descending (newest first)."""
        self._seed(tmp_memory_dir)
        result = after_action_review.search_aars(tmp_memory_dir)
        ids = [r["id"] for r in result]
        assert ids == sorted(ids, reverse=True)

    def test_query_and_tags_combined(self, tmp_memory_dir):
        """Query and tags filter together (AND)."""
        self._seed(tmp_memory_dir)
        result = after_action_review.search_aars(
            tmp_memory_dir, query="Intent-0", tags=["tag-0"]
        )
        assert len(result) == 1

    def test_no_match(self, tmp_memory_dir):
        """Query that matches nothing returns empty list."""
        self._seed(tmp_memory_dir)
        result = after_action_review.search_aars(tmp_memory_dir, query="nonexistent")
        assert result == []


# --- get_aar_report ---


class TestGetAARReport:
    """get_aar_report: formatted text output."""

    def test_empty_report(self, tmp_memory_dir):
        """Empty store produces default message."""
        result = after_action_review.get_aar_report(tmp_memory_dir)
        assert "No After-Action Reviews" in result

    def test_report_contains_fields(self, tmp_memory_dir):
        """Report contains all content fields."""
        after_action_review.create_aar(
            tmp_memory_dir,
            intent="Test intent",
            actual="Test actual",
            why_success="Test why",
            replicable="Test replicable",
            context_dependent="Test context",
            transferable="Test transfer",
            tags=["demo"],
        )
        report = after_action_review.get_aar_report(tmp_memory_dir)
        assert "Test intent" in report
        assert "Test actual" in report
        assert "Test why" in report
        assert "Test replicable" in report
        assert "Test context" in report
        assert "Test transfer" in report
        assert "demo" in report
        assert "AAR #1" in report

    def test_report_limit(self, tmp_memory_dir):
        """Report respects limit parameter."""
        for i in range(5):
            after_action_review.create_aar(
                tmp_memory_dir,
                intent=f"Intent-{i}",
                actual="a",
                why_success="b",
                replicable="c",
                context_dependent="d",
                transferable="e",
            )
        report = after_action_review.get_aar_report(tmp_memory_dir, limit=2)
        # Should contain the 2 most recent
        assert "AAR #5" in report
        assert "AAR #4" in report
        assert "AAR #1" not in report

    def test_report_header(self, tmp_memory_dir):
        """Report has correct header."""
        after_action_review.create_aar(
            tmp_memory_dir,
            intent="X",
            actual="Y",
            why_success="Z",
            replicable="R",
            context_dependent="C",
            transferable="T",
        )
        report = after_action_review.get_aar_report(tmp_memory_dir)
        assert "=== After-Action Reviews" in report
