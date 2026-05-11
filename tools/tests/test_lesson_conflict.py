"""Tests for lesson_conflict.py — Lesson Conflict Resolution (C22-E).

Tests cover:
- _tokenize: text tokenization for similarity
- _word_overlap: word overlap ratio between two texts
- detect_conflicts: conflict detection by Rule grouping + Fix divergence
- resolve_priority: priority resolution (newer > older, confidence > lower)
- get_conflict_report: formatted text report
- Edge cases: empty registry, no rules, single lesson per rule, identical fixes
"""

import json
import os
import sys
from pathlib import Path

import pytest

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))
import lesson_conflict

# --- Fixtures ---


@pytest.fixture
def tmp_memory_dir(tmp_path):
    """Provide a temporary memory directory."""
    return str(tmp_path)


def _write_lessons(memory_dir: str, entries: list[dict]) -> None:
    """Write lesson entries to lessons_registry.md for testing."""
    parts = ["# Lessons Registry\n"]
    for entry in entries:
        parts.append(f"## Lesson: {entry.get('date', '2026-01-01')}")
        parts.append("")
        parts.append("### Action")
        parts.append(entry.get("action", "some action"))
        parts.append("")
        parts.append("### Why")
        parts.append(entry.get("why", "some reason"))
        parts.append("")
        parts.append("### Fix")
        parts.append(entry.get("fix", "some fix"))
        parts.append("")
        parts.append("### Lesson")
        parts.append(entry.get("lesson", "some lesson"))
        parts.append("")
        if entry.get("rule"):
            parts.append("### Related Rule")
            parts.append(entry["rule"])
            parts.append("")
        parts.append("---")
        parts.append("")
    path = os.path.join(memory_dir, "lessons_registry.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


def _write_metadata(memory_dir: str, metadata: dict) -> None:
    """Write lesson_metadata.json for testing."""
    path = os.path.join(memory_dir, "lesson_metadata.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(metadata, f)


# --- Phase 1: Unit Tests for _tokenize ---


class TestTokenize:
    """_tokenize: text to lowercase word set."""

    def test_basic(self):
        result = lesson_conflict._tokenize("Hello World test")
        assert result == {"hello", "world", "test"}

    def test_empty(self):
        result = lesson_conflict._tokenize("")
        assert result == set()

    def test_punctuation(self):
        """Punctuation attached to words is kept (simple split)."""
        result = lesson_conflict._tokenize("hello, world!")
        assert "hello," in result or "hello" in result

    def test_dedup(self):
        result = lesson_conflict._tokenize("the the the")
        assert result == {"the"}


# --- Phase 2: Unit Tests for _word_overlap ---


class TestWordOverlap:
    """_word_overlap: Jaccard-like overlap ratio."""

    def test_identical(self):
        ratio = lesson_conflict._word_overlap("use mock for testing", "use mock for testing")
        assert ratio == 1.0

    def test_no_overlap(self):
        ratio = lesson_conflict._word_overlap("apple banana cherry", "dog elephant fox")
        assert ratio == 0.0

    def test_partial_overlap(self):
        ratio = lesson_conflict._word_overlap("use real database", "use mock database")
        # union: {use, real, database, mock} = 4, intersection: {use, database} = 2
        # 2/4 = 0.5
        assert ratio == pytest.approx(0.5)

    def test_empty_both(self):
        ratio = lesson_conflict._word_overlap("", "")
        assert ratio == 0.0

    def test_one_empty(self):
        ratio = lesson_conflict._word_overlap("hello", "")
        assert ratio == 0.0


# --- Phase 3: detect_conflicts ---


class TestDetectConflicts:
    """detect_conflicts: find conflicting lessons within same Rule category."""

    def test_empty_registry(self, tmp_memory_dir):
        """No lessons file returns empty list."""
        result = lesson_conflict.detect_conflicts(tmp_memory_dir)
        assert result == []

    def test_no_rules(self, tmp_memory_dir):
        """Lessons without rules are not checked for conflicts."""
        _write_lessons(tmp_memory_dir, [
            {"fix": "do X", "lesson": "lesson A"},
            {"fix": "do Y", "lesson": "lesson B"},
        ])
        result = lesson_conflict.detect_conflicts(tmp_memory_dir)
        assert result == []

    def test_single_per_rule(self, tmp_memory_dir):
        """Only one lesson per rule -- no conflict possible."""
        _write_lessons(tmp_memory_dir, [
            {"fix": "do X", "rule": "R1"},
        ])
        result = lesson_conflict.detect_conflicts(tmp_memory_dir)
        assert result == []

    def test_same_rule_similar_fix(self, tmp_memory_dir):
        """Same rule, similar fix text -- no conflict."""
        _write_lessons(tmp_memory_dir, [
            {"fix": "always use real database for tests", "rule": "testing"},
            {"fix": "use real database connection for integration tests", "rule": "testing"},
        ])
        result = lesson_conflict.detect_conflicts(tmp_memory_dir)
        assert result == []

    def test_same_rule_divergent_fix(self, tmp_memory_dir):
        """Same rule, divergent fix text -- conflict detected."""
        _write_lessons(tmp_memory_dir, [
            {"fix": "always use mock objects for all tests", "rule": "testing",
             "date": "2026-01-01"},
            {"fix": "never use mocks use real database connections", "rule": "testing",
             "date": "2026-02-01"},
        ])
        result = lesson_conflict.detect_conflicts(tmp_memory_dir)
        assert len(result) >= 1
        conflict = result[0]
        assert "lesson_a_id" in conflict
        assert "lesson_b_id" in conflict
        assert conflict["rule"] == "testing"
        assert "reason" in conflict

    def test_different_rules_no_conflict(self, tmp_memory_dir):
        """Different rules never conflict even with divergent fixes."""
        _write_lessons(tmp_memory_dir, [
            {"fix": "always mock everything", "rule": "testing"},
            {"fix": "never mock anything", "rule": "production"},
        ])
        result = lesson_conflict.detect_conflicts(tmp_memory_dir)
        assert result == []

    def test_multiple_conflicts(self, tmp_memory_dir):
        """Three lessons in same rule with divergent fixes can produce multiple conflicts."""
        _write_lessons(tmp_memory_dir, [
            {"fix": "always do X and Y together", "rule": "R1", "date": "2026-01-01"},
            {"fix": "never do X only do Z", "rule": "R1", "date": "2026-02-01"},
            {"fix": "skip both X and Z do W instead", "rule": "R1", "date": "2026-03-01"},
        ])
        result = lesson_conflict.detect_conflicts(tmp_memory_dir)
        assert len(result) >= 2

    def test_corrupt_file_failopen(self, tmp_memory_dir):
        """Corrupt lessons file returns empty (fail-open)."""
        path = os.path.join(tmp_memory_dir, "lessons_registry.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("not valid markdown at all random text")
        result = lesson_conflict.detect_conflicts(tmp_memory_dir)
        assert result == []


# --- Phase 4: resolve_priority ---


class TestResolvePriority:
    """resolve_priority: determine which lesson takes precedence."""

    def test_newer_wins(self):
        """Newer lesson wins by default."""
        a = {"date": "2026-01-01", "lesson": "old", "fix": "old fix"}
        b = {"date": "2026-03-01", "lesson": "new", "fix": "new fix"}
        meta = {}
        result = lesson_conflict.resolve_priority(a, b, 1, 2, meta)
        assert result["winner_id"] == 2
        assert result["loser_id"] == 1
        assert "newer" in result["reason"].lower() or "date" in result["reason"].lower()

    def test_confidence_overrides_date(self):
        """Higher confidence wins even if older."""
        a = {"date": "2026-01-01", "lesson": "old high conf", "fix": "X"}
        b = {"date": "2026-03-01", "lesson": "new low conf", "fix": "Y"}
        meta = {
            "1": {"confidence": 0.9, "applied_count": 5},
            "2": {"confidence": 0.2, "applied_count": 1},
        }
        result = lesson_conflict.resolve_priority(a, b, 1, 2, meta)
        assert result["winner_id"] == 1
        assert "confidence" in result["reason"].lower()

    def test_same_date_same_confidence(self):
        """Equal date and confidence: newer ID wins as tiebreaker."""
        a = {"date": "2026-01-01", "lesson": "A", "fix": "X"}
        b = {"date": "2026-01-01", "lesson": "B", "fix": "Y"}
        meta = {}
        result = lesson_conflict.resolve_priority(a, b, 1, 2, meta)
        assert result["winner_id"] == 2
        assert result["loser_id"] == 1


# --- Phase 5: get_conflict_report ---


class TestGetConflictReport:
    """get_conflict_report: formatted text output."""

    def test_no_conflicts(self, tmp_memory_dir):
        """No conflicts produces clean message."""
        report = lesson_conflict.get_conflict_report(tmp_memory_dir)
        assert "no conflict" in report.lower() or "0 conflict" in report.lower()

    def test_with_conflicts(self, tmp_memory_dir):
        """Report includes conflict details."""
        _write_lessons(tmp_memory_dir, [
            {"fix": "always use mock objects for all tests", "rule": "testing",
             "date": "2026-01-01"},
            {"fix": "never use mocks use real database connections", "rule": "testing",
             "date": "2026-02-01"},
        ])
        report = lesson_conflict.get_conflict_report(tmp_memory_dir)
        assert "testing" in report.lower()
        assert "conflict" in report.lower()
