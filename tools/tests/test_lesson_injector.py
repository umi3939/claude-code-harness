"""Tests for lesson_injector.py - Semantic Lesson Injection.

Tests cover:
- find_relevant_lessons: empty lessons, context matching, confidence ordering,
  low confidence warning flag, limit enforcement, fail-open on registry error
- format_injection: empty list, normal formatting, warning display
- Integration: end-to-end with real registry + metadata
"""

import os
import sys
from pathlib import Path

import pytest

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))
import lesson_metadata
import lessons_registry

# --- Fixtures ---


@pytest.fixture
def tmp_memory_dir(tmp_path):
    return str(tmp_path)


def _populate_registry(mem_dir):
    """Populate a test registry with 5 entries."""
    lessons_registry.add_lesson(
        memory_dir=mem_dir,
        action="Skipped reading CLAUDE.md at session start",
        why="Missed critical rules about division of labor",
        fix="Added mandatory reading step to session startup",
        lesson="Always read instruction files fully before starting work",
        rule="session startup",
    )
    lessons_registry.add_lesson(
        memory_dir=mem_dir,
        action="Leader directly edited code files",
        why="Violated separation of concerns between leader and implementer",
        fix="Delegated all code changes to implementation member",
        lesson="Leaders must never write code directly",
        rule="leader role",
    )
    lessons_registry.add_lesson(
        memory_dir=mem_dir,
        action="Deployed without running tests",
        why="Introduced a regression in production",
        fix="Added CI gate requiring all tests to pass",
        lesson="Always run tests before deployment",
        rule="testing",
    )
    lessons_registry.add_lesson(
        memory_dir=mem_dir,
        action="Used shell=True in subprocess call",
        why="Security vulnerability: command injection risk",
        fix="Changed to list-based arguments",
        lesson="Never use shell=True in subprocess; use list args",
        rule="security",
    )
    lessons_registry.add_lesson(
        memory_dir=mem_dir,
        action="Forgot to set timeout on API call",
        why="API hang caused entire system to stall",
        fix="Added 30s timeout to all external API calls",
        lesson="Always set timeout on external API calls",
        rule="reliability",
    )
    meta = {
        "1": {"applied_count": 10, "confidence": 0.9, "last_applied": None, "last_applied_session_id": None},
        "2": {"applied_count": 5, "confidence": 0.7, "last_applied": None, "last_applied_session_id": None},
        "3": {"applied_count": 3, "confidence": 0.5, "last_applied": None, "last_applied_session_id": None},
        "4": {"applied_count": 1, "confidence": 0.2, "last_applied": None, "last_applied_session_id": None},
        "5": {"applied_count": 2, "confidence": 0.6, "last_applied": None, "last_applied_session_id": None},
    }
    lesson_metadata.save_metadata(mem_dir, meta)


@pytest.fixture
def registry_with_lessons(tmp_memory_dir):
    _populate_registry(tmp_memory_dir)
    return tmp_memory_dir


@pytest.fixture
def injector():
    import lesson_injector
    return lesson_injector


class TestFindRelevantLessons:

    def test_empty_registry_returns_empty(self, tmp_memory_dir, injector):
        result = injector.find_relevant_lessons(tmp_memory_dir, "anything")
        assert result == []

    def test_no_match_returns_empty(self, registry_with_lessons, injector):
        result = injector.find_relevant_lessons(
            registry_with_lessons, "quantum physics particle accelerator"
        )
        assert result == []

    def test_context_match_returns_relevant(self, registry_with_lessons, injector):
        result = injector.find_relevant_lessons(
            registry_with_lessons, "subprocess shell security"
        )
        assert len(result) >= 1
        lesson_texts = [r["lesson_text"] for r in result]
        assert any("shell" in t.lower() or "subprocess" in t.lower() for t in lesson_texts)

    def test_results_sorted_by_confidence(self, registry_with_lessons, injector):
        result = injector.find_relevant_lessons(
            registry_with_lessons, "code session startup leader testing"
        )
        if len(result) >= 2:
            confidences = [r["confidence"] for r in result]
            assert confidences == sorted(confidences, reverse=True)

    def test_low_confidence_warning_flag(self, registry_with_lessons, injector):
        result = injector.find_relevant_lessons(
            registry_with_lessons, "subprocess shell security"
        )
        for r in result:
            if r["confidence"] < 0.3:
                assert r["warning"] == "\u8981\u691c\u8a3c"
            else:
                assert r.get("warning") is None

    def test_limit_enforced(self, registry_with_lessons, injector):
        result = injector.find_relevant_lessons(
            registry_with_lessons, "code session startup leader testing security API",
            limit=2,
        )
        assert len(result) <= 2

    def test_result_structure(self, registry_with_lessons, injector):
        result = injector.find_relevant_lessons(
            registry_with_lessons, "testing deployment"
        )
        for r in result:
            assert "lesson_id" in r
            assert "lesson_text" in r
            assert "confidence" in r
            assert "applied_count" in r

    def test_default_confidence_for_untracked(self, tmp_memory_dir, injector):
        lessons_registry.add_lesson(
            memory_dir=tmp_memory_dir,
            action="Some action",
            why="Some reason",
            fix="Some fix",
            lesson="Always check defaults",
        )
        result = injector.find_relevant_lessons(
            tmp_memory_dir, "check defaults"
        )
        assert len(result) >= 1
        assert result[0]["confidence"] == 0.5

    def test_fail_open_on_corrupt_registry(self, tmp_memory_dir, injector):
        lessons_path = os.path.join(tmp_memory_dir, "lessons_registry.md")
        with open(lessons_path, "w", encoding="utf-8") as f:
            f.write("<<<CORRUPT DATA>>>")
        result = injector.find_relevant_lessons(
            tmp_memory_dir, "anything"
        )
        assert result == []


class TestFormatInjection:

    def test_empty_list_returns_empty_string(self, injector):
        assert injector.format_injection([]) == ""

    def test_normal_formatting(self, injector):
        lessons = [
            {
                "lesson_id": "3",
                "lesson_text": "Always run tests before deployment",
                "confidence": 0.8,
                "applied_count": 5,
            },
        ]
        result = injector.format_injection(lessons)
        assert "=== Relevant Lessons ===" in result
        assert "#3" in result
        assert "conf=0.8" in result
        assert "Always run tests before deployment" in result

    def test_warning_display(self, injector):
        lessons = [
            {
                "lesson_id": "4",
                "lesson_text": "Never use shell=True",
                "confidence": 0.2,
                "applied_count": 1,
                "warning": "\u8981\u691c\u8a3c",
            },
        ]
        result = injector.format_injection(lessons)
        assert "\u8981\u691c\u8a3c" in result

    def test_multiple_lessons_numbered(self, injector):
        lessons = [
            {"lesson_id": "1", "lesson_text": "Lesson A", "confidence": 0.9, "applied_count": 10},
            {"lesson_id": "2", "lesson_text": "Lesson B", "confidence": 0.7, "applied_count": 5},
        ]
        result = injector.format_injection(lessons)
        assert "1." in result
        assert "2." in result


class TestIntegration:

    def test_full_pipeline(self, registry_with_lessons, injector):
        lessons = injector.find_relevant_lessons(
            registry_with_lessons, "testing deployment regression"
        )
        text = injector.format_injection(lessons)
        if lessons:
            assert "=== Relevant Lessons ===" in text
            assert len(text) > 0
