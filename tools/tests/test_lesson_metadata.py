"""Tests for lesson_metadata.py — Lesson Validation Loop metadata management.

Tests cover:
- generate_lesson_id: int → str conversion
- load_metadata / save_metadata: file I/O, missing/corrupt fallback
- record_application: counter increment, timestamp, session dedup, event log
- validate_lesson: confidence update (success/failure), bounds, event log
- get_lesson_confidence: default 0.5 for untracked
"""

import json
import os
import sys
from pathlib import Path

import pytest

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))
import lesson_metadata

# --- Fixtures ---


@pytest.fixture
def tmp_memory_dir(tmp_path):
    """Provide a temporary memory directory."""
    return str(tmp_path)


# --- Phase 1: Unit Tests ---


class TestGenerateLessonId:
    """generate_lesson_id: int lesson number → str ID."""

    def test_basic(self):
        assert lesson_metadata.generate_lesson_id(1) == "1"

    def test_large_number(self):
        assert lesson_metadata.generate_lesson_id(999) == "999"


class TestLoadMetadata:
    """load_metadata: JSON file read with fallback."""

    def test_no_file(self, tmp_memory_dir):
        """Missing file returns empty dict."""
        result = lesson_metadata.load_metadata(tmp_memory_dir)
        assert result == {}

    def test_empty_file(self, tmp_memory_dir):
        """Empty file returns empty dict."""
        path = os.path.join(tmp_memory_dir, "lesson_metadata.json")
        with open(path, "w") as f:
            f.write("")
        result = lesson_metadata.load_metadata(tmp_memory_dir)
        assert result == {}

    def test_corrupt_json(self, tmp_memory_dir):
        """Corrupt JSON returns empty dict (fail-open)."""
        path = os.path.join(tmp_memory_dir, "lesson_metadata.json")
        with open(path, "w") as f:
            f.write("{broken json!!")
        result = lesson_metadata.load_metadata(tmp_memory_dir)
        assert result == {}

    def test_valid_data(self, tmp_memory_dir):
        """Valid JSON returns parsed dict."""
        path = os.path.join(tmp_memory_dir, "lesson_metadata.json")
        data = {"1": {"applied_count": 3, "confidence": 0.8}}
        with open(path, "w") as f:
            json.dump(data, f)
        result = lesson_metadata.load_metadata(tmp_memory_dir)
        assert result == data


class TestSaveMetadata:
    """save_metadata: atomic JSON write."""

    def test_save_and_reload(self, tmp_memory_dir):
        """Saved data can be reloaded."""
        data = {"1": {"applied_count": 1, "confidence": 0.5}}
        lesson_metadata.save_metadata(tmp_memory_dir, data)
        result = lesson_metadata.load_metadata(tmp_memory_dir)
        assert result == data

    def test_creates_directory(self, tmp_path):
        """Creates parent directory if missing."""
        nested = str(tmp_path / "nested" / "dir")
        data = {"1": {"confidence": 0.5}}
        lesson_metadata.save_metadata(nested, data)
        result = lesson_metadata.load_metadata(nested)
        assert result == data


class TestRecordApplication:
    """record_application: tracks when a lesson is applied."""

    def test_first_application(self, tmp_memory_dir):
        """First application creates entry with applied_count=1."""
        result = lesson_metadata.record_application(
            tmp_memory_dir, "1", "session_test_001"
        )
        assert result["applied_count"] == 1
        assert result["confidence"] == 0.5
        assert "last_applied" in result
        assert result["last_applied_session_id"] == "session_test_001"

    def test_increment_count(self, tmp_memory_dir):
        """Second application from different session increments count."""
        lesson_metadata.record_application(
            tmp_memory_dir, "1", "session_test_001"
        )
        result = lesson_metadata.record_application(
            tmp_memory_dir, "1", "session_test_002"
        )
        assert result["applied_count"] == 2

    def test_session_dedup(self, tmp_memory_dir):
        """Same session_id does not increment applied_count."""
        lesson_metadata.record_application(
            tmp_memory_dir, "1", "session_test_001"
        )
        result = lesson_metadata.record_application(
            tmp_memory_dir, "1", "session_test_001"
        )
        assert result["applied_count"] == 1

    def test_event_log_created(self, tmp_memory_dir):
        """Application creates an event log entry."""
        lesson_metadata.record_application(
            tmp_memory_dir, "1", "session_test_001"
        )
        log_path = os.path.join(tmp_memory_dir, "lesson_events.jsonl")
        assert os.path.exists(log_path)
        with open(log_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["event"] == "applied"
        assert event["lesson_id"] == "1"

    def test_session_dedup_no_log(self, tmp_memory_dir):
        """Duplicate session does not write event log."""
        lesson_metadata.record_application(
            tmp_memory_dir, "1", "session_test_001"
        )
        lesson_metadata.record_application(
            tmp_memory_dir, "1", "session_test_001"
        )
        log_path = os.path.join(tmp_memory_dir, "lesson_events.jsonl")
        with open(log_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        # Only one event (first application), not two
        assert len(lines) == 1


class TestValidateLesson:
    """validate_lesson: updates confidence based on success/failure."""

    def test_success_increases_confidence(self, tmp_memory_dir):
        """Success adds +0.1 to confidence."""
        # Start with default 0.5
        lesson_metadata.record_application(
            tmp_memory_dir, "1", "session_test_001"
        )
        result = lesson_metadata.validate_lesson(
            tmp_memory_dir, "1", success=True, category="test_cat"
        )
        assert abs(result["confidence"] - 0.6) < 0.001

    def test_failure_decreases_confidence(self, tmp_memory_dir):
        """Failure subtracts 0.15 from confidence."""
        lesson_metadata.record_application(
            tmp_memory_dir, "1", "session_test_001"
        )
        result = lesson_metadata.validate_lesson(
            tmp_memory_dir, "1", success=False, category="test_cat"
        )
        assert abs(result["confidence"] - 0.35) < 0.001

    def test_confidence_upper_bound(self, tmp_memory_dir):
        """Confidence cannot exceed 1.0."""
        lesson_metadata.record_application(
            tmp_memory_dir, "1", "session_test_001"
        )
        # Force confidence close to max
        meta = lesson_metadata.load_metadata(tmp_memory_dir)
        meta["1"]["confidence"] = 0.95
        lesson_metadata.save_metadata(tmp_memory_dir, meta)
        result = lesson_metadata.validate_lesson(
            tmp_memory_dir, "1", success=True
        )
        assert result["confidence"] == 1.0

    def test_confidence_lower_bound(self, tmp_memory_dir):
        """Confidence cannot go below 0.1."""
        lesson_metadata.record_application(
            tmp_memory_dir, "1", "session_test_001"
        )
        # Force confidence close to min
        meta = lesson_metadata.load_metadata(tmp_memory_dir)
        meta["1"]["confidence"] = 0.15
        lesson_metadata.save_metadata(tmp_memory_dir, meta)
        result = lesson_metadata.validate_lesson(
            tmp_memory_dir, "1", success=False
        )
        assert result["confidence"] == 0.1

    def test_validate_untracked_lesson(self, tmp_memory_dir):
        """Validating an untracked lesson creates entry with default confidence first."""
        result = lesson_metadata.validate_lesson(
            tmp_memory_dir, "99", success=True
        )
        assert result["confidence"] == 0.6  # 0.5 + 0.1
        assert result["applied_count"] == 0

    def test_event_log_validated(self, tmp_memory_dir):
        """Validation writes event log."""
        lesson_metadata.validate_lesson(
            tmp_memory_dir, "1", success=True, category="security"
        )
        log_path = os.path.join(tmp_memory_dir, "lesson_events.jsonl")
        with open(log_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["event"] == "validated"
        assert event["category"] == "security"

    def test_event_log_invalidated(self, tmp_memory_dir):
        """Failed validation writes 'invalidated' event."""
        lesson_metadata.validate_lesson(
            tmp_memory_dir, "1", success=False, category="perf"
        )
        log_path = os.path.join(tmp_memory_dir, "lesson_events.jsonl")
        with open(log_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        event = json.loads(lines[0])
        assert event["event"] == "invalidated"


class TestGetLessonConfidence:
    """get_lesson_confidence: read confidence from metadata."""

    def test_untracked_returns_default(self):
        """Untracked lesson returns 0.5."""
        assert lesson_metadata.get_lesson_confidence({}, "1") == 0.5

    def test_tracked_returns_stored(self):
        """Tracked lesson returns stored confidence."""
        metadata = {"1": {"confidence": 0.8}}
        assert lesson_metadata.get_lesson_confidence(metadata, "1") == 0.8
