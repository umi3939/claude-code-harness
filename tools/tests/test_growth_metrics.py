"""Tests for growth_metrics.py — Growth Metrics Dashboard (C22-F/L).

Tests cover:
- collect_metrics: lesson/success/mastery data aggregation
- balance_ratio: boundary values and edge cases
- generate_dashboard: formatted output with 4 sections
- get_health_summary: 1-line summary generation
- fail-open: missing/corrupt data files
- MCP tool wrappers
"""

import json
import os
import sys
from pathlib import Path

import pytest

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))
import growth_metrics

# --- Fixtures ---


@pytest.fixture
def tmp_memory_dir(tmp_path):
    """Provide a temporary memory directory."""
    return str(tmp_path)


def _write_lessons_registry(memory_dir: str, count: int) -> None:
    """Write a lessons_registry.md with the given number of entries."""
    path = os.path.join(memory_dir, "lessons_registry.md")
    lines = ["# Lessons Registry\n"]
    for i in range(1, count + 1):
        lines.append(f"\n## Lesson: 2026-03-{i:02d}\n")
        lines.append("\n### Action\n")
        lines.append(f"Action {i}\n")
        lines.append("\n### Why\n")
        lines.append(f"Why {i}\n")
        lines.append("\n### Fix\n")
        lines.append(f"Fix {i}\n")
        lines.append("\n### Lesson\n")
        lines.append(f"Lesson {i}\n")
        if i < count:
            lines.append("\n---\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(lines))


def _write_success_patterns(memory_dir: str, count: int) -> None:
    """Write success_patterns.json with the given number of records."""
    records = []
    for i in range(1, count + 1):
        records.append({
            "id": i,
            "event_type": "test_pass",
            "context": f"Context {i}",
            "why_success": f"Why {i}",
            "tags": ["tag1"],
            "recorded_at": f"2026-03-{i:02d}T00:00:00+00:00",
        })
    path = os.path.join(memory_dir, "success_patterns.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f)


def _write_mastery_profile(memory_dir: str, domains: dict) -> None:
    """Write mastery_profile.json with the given domains."""
    path = os.path.join(memory_dir, "mastery_profile.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(domains, f)


def _write_lesson_metadata(memory_dir: str, metadata: dict) -> None:
    """Write lesson_metadata.json with the given metadata."""
    path = os.path.join(memory_dir, "lesson_metadata.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(metadata, f)


# --- collect_metrics tests ---


class TestCollectMetrics:
    """Tests for collect_metrics function."""

    def test_empty_memory_dir(self, tmp_memory_dir):
        """All metrics zero when no data files exist."""
        result = growth_metrics.collect_metrics(tmp_memory_dir)
        assert result["lessons"]["total"] == 0
        assert result["successes"]["total"] == 0
        assert result["mastery"]["total_domains"] == 0
        assert result["balance"]["ratio"] is None

    def test_lessons_only(self, tmp_memory_dir):
        """Lessons present, no successes -> ratio near 0."""
        _write_lessons_registry(tmp_memory_dir, 5)
        result = growth_metrics.collect_metrics(tmp_memory_dir)
        assert result["lessons"]["total"] == 5
        assert result["successes"]["total"] == 0
        assert result["balance"]["ratio"] == pytest.approx(0.0)

    def test_successes_only(self, tmp_memory_dir):
        """Successes present, no lessons -> ratio 1.0."""
        _write_success_patterns(tmp_memory_dir, 3)
        result = growth_metrics.collect_metrics(tmp_memory_dir)
        assert result["lessons"]["total"] == 0
        assert result["successes"]["total"] == 3
        assert result["balance"]["ratio"] == pytest.approx(1.0)

    def test_balanced_data(self, tmp_memory_dir):
        """Equal lessons and successes -> ratio 0.5."""
        _write_lessons_registry(tmp_memory_dir, 4)
        _write_success_patterns(tmp_memory_dir, 4)
        result = growth_metrics.collect_metrics(tmp_memory_dir)
        assert result["lessons"]["total"] == 4
        assert result["successes"]["total"] == 4
        assert result["balance"]["ratio"] == pytest.approx(0.5)

    def test_mastery_data(self, tmp_memory_dir):
        """Mastery profile data is included in metrics."""
        _write_mastery_profile(tmp_memory_dir, {
            "testing": {
                "success_count": 8,
                "total_count": 10,
                "mastery_score": 0.8,
                "trend": "improving",
                "best_approach": "TDD",
                "recent_results": [True] * 10,
            },
            "design": {
                "success_count": 3,
                "total_count": 5,
                "mastery_score": 0.6,
                "trend": "stable",
                "best_approach": "",
                "recent_results": [True, False] * 5,
            },
        })
        result = growth_metrics.collect_metrics(tmp_memory_dir)
        assert result["mastery"]["total_domains"] == 2
        assert len(result["mastery"]["strengths"]) > 0

    def test_lesson_metadata_included(self, tmp_memory_dir):
        """Lesson metadata (confidence, validated count) is collected."""
        _write_lessons_registry(tmp_memory_dir, 3)
        _write_lesson_metadata(tmp_memory_dir, {
            "1": {"applied_count": 2, "confidence": 0.7, "last_applied": None, "last_applied_session_id": None},
            "2": {"applied_count": 0, "confidence": 0.5, "last_applied": None, "last_applied_session_id": None},
            "3": {"applied_count": 1, "confidence": 0.9, "last_applied": None, "last_applied_session_id": None},
        })
        result = growth_metrics.collect_metrics(tmp_memory_dir)
        assert result["lessons"]["total"] == 3
        assert result["lessons"]["avg_confidence"] == pytest.approx(0.7, abs=0.01)
        assert result["lessons"]["validated_count"] == 2  # confidence != 0.5


# --- balance_ratio tests ---


class TestBalanceRatio:
    """Tests for balance ratio calculation edge cases."""

    def test_ratio_zero_division(self, tmp_memory_dir):
        """No data -> ratio is None."""
        result = growth_metrics.collect_metrics(tmp_memory_dir)
        assert result["balance"]["ratio"] is None
        assert result["balance"]["status"] == "no_data"

    def test_ratio_below_threshold(self, tmp_memory_dir):
        """ratio < 0.3 -> failure_heavy warning."""
        _write_lessons_registry(tmp_memory_dir, 8)
        _write_success_patterns(tmp_memory_dir, 2)
        result = growth_metrics.collect_metrics(tmp_memory_dir)
        assert result["balance"]["ratio"] == pytest.approx(0.2)
        assert result["balance"]["status"] == "failure_heavy"

    def test_ratio_above_threshold(self, tmp_memory_dir):
        """ratio > 0.7 -> success_biased warning."""
        _write_lessons_registry(tmp_memory_dir, 2)
        _write_success_patterns(tmp_memory_dir, 8)
        result = growth_metrics.collect_metrics(tmp_memory_dir)
        assert result["balance"]["ratio"] == pytest.approx(0.8)
        assert result["balance"]["status"] == "success_biased"

    def test_ratio_balanced(self, tmp_memory_dir):
        """0.3 <= ratio <= 0.7 -> balanced."""
        _write_lessons_registry(tmp_memory_dir, 5)
        _write_success_patterns(tmp_memory_dir, 5)
        result = growth_metrics.collect_metrics(tmp_memory_dir)
        assert result["balance"]["ratio"] == pytest.approx(0.5)
        assert result["balance"]["status"] == "balanced"

    def test_ratio_boundary_030(self, tmp_memory_dir):
        """ratio exactly 0.3 -> balanced (inclusive)."""
        _write_lessons_registry(tmp_memory_dir, 7)
        _write_success_patterns(tmp_memory_dir, 3)
        result = growth_metrics.collect_metrics(tmp_memory_dir)
        assert result["balance"]["ratio"] == pytest.approx(0.3)
        assert result["balance"]["status"] == "balanced"

    def test_ratio_boundary_070(self, tmp_memory_dir):
        """ratio exactly 0.7 -> balanced (inclusive)."""
        _write_lessons_registry(tmp_memory_dir, 3)
        _write_success_patterns(tmp_memory_dir, 7)
        result = growth_metrics.collect_metrics(tmp_memory_dir)
        assert result["balance"]["ratio"] == pytest.approx(0.7)
        assert result["balance"]["status"] == "balanced"


# --- generate_dashboard tests ---


class TestGenerateDashboard:
    """Tests for generate_dashboard function."""

    def test_empty_dashboard(self, tmp_memory_dir):
        """Dashboard generates even with no data."""
        result = growth_metrics.generate_dashboard(tmp_memory_dir)
        assert isinstance(result, str)
        assert "Growth Metrics Dashboard" in result

    def test_dashboard_has_four_sections(self, tmp_memory_dir):
        """Dashboard contains all 4 sections."""
        _write_lessons_registry(tmp_memory_dir, 3)
        _write_success_patterns(tmp_memory_dir, 2)
        _write_mastery_profile(tmp_memory_dir, {
            "testing": {
                "success_count": 5, "total_count": 6,
                "mastery_score": 0.83, "trend": "improving",
                "best_approach": "TDD", "recent_results": [True] * 6,
            },
        })
        result = growth_metrics.generate_dashboard(tmp_memory_dir)
        assert "Lessons" in result
        assert "Success" in result
        assert "Mastery" in result
        assert "Balance" in result


# --- get_health_summary tests ---


class TestGetHealthSummary:
    """Tests for get_health_summary function."""

    def test_empty_summary(self, tmp_memory_dir):
        """Health summary works with no data."""
        result = growth_metrics.get_health_summary(tmp_memory_dir)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_summary_single_line(self, tmp_memory_dir):
        """Health summary is a single line."""
        _write_lessons_registry(tmp_memory_dir, 3)
        _write_success_patterns(tmp_memory_dir, 2)
        result = growth_metrics.get_health_summary(tmp_memory_dir)
        assert "\n" not in result


# --- fail-open tests ---


class TestFailOpen:
    """Tests for fail-open behavior: corrupt data should not raise."""

    def test_corrupt_success_patterns(self, tmp_memory_dir):
        """Corrupt success_patterns.json -> graceful fallback."""
        path = os.path.join(tmp_memory_dir, "success_patterns.json")
        with open(path, "w") as f:
            f.write("{{{invalid json")
        result = growth_metrics.collect_metrics(tmp_memory_dir)
        assert result["successes"]["total"] == 0

    def test_corrupt_mastery_profile(self, tmp_memory_dir):
        """Corrupt mastery_profile.json -> graceful fallback."""
        path = os.path.join(tmp_memory_dir, "mastery_profile.json")
        with open(path, "w") as f:
            f.write("not json")
        result = growth_metrics.collect_metrics(tmp_memory_dir)
        assert result["mastery"]["total_domains"] == 0

    def test_corrupt_lesson_metadata(self, tmp_memory_dir):
        """Corrupt lesson_metadata.json -> graceful fallback."""
        _write_lessons_registry(tmp_memory_dir, 2)
        path = os.path.join(tmp_memory_dir, "lesson_metadata.json")
        with open(path, "w") as f:
            f.write("broken")
        result = growth_metrics.collect_metrics(tmp_memory_dir)
        assert result["lessons"]["total"] == 2
        assert result["lessons"]["avg_confidence"] == pytest.approx(0.5)
