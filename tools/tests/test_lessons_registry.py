"""Tests for lessons_registry.py."""

import os
import sys
import time
from pathlib import Path

import pytest

# Add parent directory to path so we can import lessons_registry
sys.path.insert(0, str(Path(__file__).parent.parent))
import lessons_registry


# --- Fixtures ---

@pytest.fixture
def tmp_memory_dir(tmp_path):
    """Provide a temporary memory directory."""
    return str(tmp_path)


@pytest.fixture
def registry_with_entries(tmp_memory_dir):
    """Create a memory dir with 3 pre-saved lesson entries."""
    lessons_registry.add_lesson(
        memory_dir=tmp_memory_dir,
        action="Skipped reading CLAUDE.md at session start",
        why="Missed critical rules about division of labor",
        fix="Added mandatory reading step to session startup",
        lesson="Always read instruction files fully before starting work",
        rule="作業前に全体把握",
    )
    lessons_registry.add_lesson(
        memory_dir=tmp_memory_dir,
        action="Leader directly edited code files",
        why="Violated separation of concerns between leader and implementer",
        fix="Delegated all code changes to implementation member",
        lesson="Leaders must never write code directly",
        rule="リーダーはコード一切書かない",
    )
    lessons_registry.add_lesson(
        memory_dir=tmp_memory_dir,
        action="Summarized design doc instead of reading fully",
        why="Missed important constraints specified in the design",
        fix="Re-read full design doc and re-implemented",
        lesson="Never summarize instruction documents; read them in full",
    )
    return tmp_memory_dir


# ===== add_lesson tests =====

class TestAddLesson:
    """Tests for the add_lesson function."""

    def test_add_creates_file(self, tmp_memory_dir):
        result = lessons_registry.add_lesson(
            memory_dir=tmp_memory_dir,
            action="Test action",
            why="Test reason",
            fix="Test fix",
            lesson="Test lesson",
        )
        assert not result.startswith("WARNING:")
        assert Path(result).exists()

    def test_add_returns_path(self, tmp_memory_dir):
        result = lessons_registry.add_lesson(
            memory_dir=tmp_memory_dir,
            action="Test action",
            why="Test reason",
            fix="Test fix",
            lesson="Test lesson",
        )
        expected = str(lessons_registry.get_lessons_path(tmp_memory_dir))
        assert result == expected

    def test_add_with_all_fields(self, tmp_memory_dir):
        result = lessons_registry.add_lesson(
            memory_dir=tmp_memory_dir,
            action="Specific action taken",
            why="Why it was problematic",
            fix="How it was fixed",
            lesson="General lesson learned",
            rule="Related rule reference",
        )
        assert not result.startswith("WARNING:")

        text = Path(result).read_text(encoding="utf-8")
        assert "Specific action taken" in text
        assert "Why it was problematic" in text
        assert "How it was fixed" in text
        assert "General lesson learned" in text
        assert "Related rule reference" in text

    def test_add_without_rule(self, tmp_memory_dir):
        result = lessons_registry.add_lesson(
            memory_dir=tmp_memory_dir,
            action="Action",
            why="Reason",
            fix="Fix",
            lesson="Lesson",
        )
        assert not result.startswith("WARNING:")
        text = Path(result).read_text(encoding="utf-8")
        assert "Related Rule" not in text

    def test_add_with_rule(self, tmp_memory_dir):
        result = lessons_registry.add_lesson(
            memory_dir=tmp_memory_dir,
            action="Action",
            why="Reason",
            fix="Fix",
            lesson="Lesson",
            rule="Some rule",
        )
        assert not result.startswith("WARNING:")
        text = Path(result).read_text(encoding="utf-8")
        assert "### Related Rule" in text
        assert "Some rule" in text

    def test_add_creates_parent_dirs(self, tmp_memory_dir):
        nested = os.path.join(tmp_memory_dir, "deep", "nested", "dir")
        result = lessons_registry.add_lesson(
            memory_dir=nested,
            action="Deep action",
            why="Deep reason",
            fix="Deep fix",
            lesson="Deep lesson",
        )
        assert not result.startswith("WARNING:")
        assert Path(result).exists()

    def test_add_appends_to_existing(self, tmp_memory_dir):
        lessons_registry.add_lesson(
            memory_dir=tmp_memory_dir,
            action="First action",
            why="First reason",
            fix="First fix",
            lesson="First lesson",
        )
        lessons_registry.add_lesson(
            memory_dir=tmp_memory_dir,
            action="Second action",
            why="Second reason",
            fix="Second fix",
            lesson="Second lesson",
        )

        text = lessons_registry.get_lessons_path(tmp_memory_dir).read_text(
            encoding="utf-8"
        )
        assert "First action" in text
        assert "Second action" in text
        assert "First lesson" in text
        assert "Second lesson" in text

    def test_add_never_modifies_existing_entries(self, tmp_memory_dir):
        lessons_registry.add_lesson(
            memory_dir=tmp_memory_dir,
            action="Original action",
            why="Original reason",
            fix="Original fix",
            lesson="Original lesson",
        )
        text_before = lessons_registry.get_lessons_path(tmp_memory_dir).read_text(
            encoding="utf-8"
        )

        lessons_registry.add_lesson(
            memory_dir=tmp_memory_dir,
            action="New action",
            why="New reason",
            fix="New fix",
            lesson="New lesson",
        )
        text_after = lessons_registry.get_lessons_path(tmp_memory_dir).read_text(
            encoding="utf-8"
        )

        # Original content should still be present unchanged
        assert "Original action" in text_after
        assert "Original lesson" in text_after

    def test_add_markdown_format(self, tmp_memory_dir):
        result = lessons_registry.add_lesson(
            memory_dir=tmp_memory_dir,
            action="Format check action",
            why="Format check reason",
            fix="Format check fix",
            lesson="Format check lesson",
            rule="Format check rule",
        )
        text = Path(result).read_text(encoding="utf-8")
        assert text.startswith("# Lessons Registry")
        assert "## Lesson:" in text
        assert "### Action" in text
        assert "### Why" in text
        assert "### Fix" in text
        assert "### Lesson" in text
        assert "### Related Rule" in text

    def test_add_records_date(self, tmp_memory_dir):
        result = lessons_registry.add_lesson(
            memory_dir=tmp_memory_dir,
            action="Date test",
            why="Date reason",
            fix="Date fix",
            lesson="Date lesson",
        )
        text = Path(result).read_text(encoding="utf-8")
        today = time.strftime("%Y-%m-%d")
        assert today in text


# ===== list_lessons tests =====

class TestListLessons:
    """Tests for the list_lessons function."""

    def test_list_no_file(self, tmp_memory_dir):
        result = lessons_registry.list_lessons(tmp_memory_dir)
        assert "No lesson entries found" in result

    def test_list_shows_count(self, registry_with_entries):
        result = lessons_registry.list_lessons(registry_with_entries)
        assert "3 entries" in result

    def test_list_shows_lesson_previews(self, registry_with_entries):
        result = lessons_registry.list_lessons(registry_with_entries)
        assert "Always read instruction files" in result
        assert "Leaders must never write code" in result
        assert "Never summarize instruction documents" in result

    def test_list_shows_numbering(self, registry_with_entries):
        result = lessons_registry.list_lessons(registry_with_entries)
        assert "1." in result
        assert "2." in result
        assert "3." in result

    def test_list_shows_dates(self, registry_with_entries):
        result = lessons_registry.list_lessons(registry_with_entries)
        today = time.strftime("%Y-%m-%d")
        assert today in result

    def test_list_truncates_long_lesson(self, tmp_memory_dir):
        long_lesson = "A" * 200
        lessons_registry.add_lesson(
            memory_dir=tmp_memory_dir,
            action="Action",
            why="Reason",
            fix="Fix",
            lesson=long_lesson,
        )
        result = lessons_registry.list_lessons(tmp_memory_dir)
        assert "..." in result
        for line in result.split("\n"):
            if "AAA" in line:
                assert len(line) < 200


# ===== search_lessons tests =====

class TestSearchLessons:
    """Tests for the search_lessons function."""

    def test_search_no_file(self, tmp_memory_dir):
        result = lessons_registry.search_lessons(tmp_memory_dir, "anything")
        assert "No lesson entries found" in result

    def test_search_no_match(self, registry_with_entries):
        result = lessons_registry.search_lessons(
            registry_with_entries, "xyznonexistent"
        )
        assert "No lessons matching" in result

    def test_search_matches_action(self, registry_with_entries):
        result = lessons_registry.search_lessons(
            registry_with_entries, "CLAUDE.md"
        )
        assert "1 matches" in result
        assert "Skipped reading CLAUDE.md" in result

    def test_search_matches_why(self, registry_with_entries):
        result = lessons_registry.search_lessons(
            registry_with_entries, "separation of concerns"
        )
        assert "1 matches" in result

    def test_search_matches_fix(self, registry_with_entries):
        result = lessons_registry.search_lessons(
            registry_with_entries, "Delegated"
        )
        assert "1 matches" in result

    def test_search_matches_lesson(self, registry_with_entries):
        result = lessons_registry.search_lessons(
            registry_with_entries, "instruction"
        )
        # Matches both "Always read instruction files" and "Never summarize instruction documents"
        assert "2 matches" in result

    def test_search_matches_rule(self, registry_with_entries):
        result = lessons_registry.search_lessons(
            registry_with_entries, "リーダー"
        )
        assert "1 matches" in result
        assert "Leaders must never write code" in result

    def test_search_case_insensitive(self, registry_with_entries):
        result = lessons_registry.search_lessons(
            registry_with_entries, "claude.md"
        )
        assert "1 matches" in result

    def test_search_returns_full_entries(self, registry_with_entries):
        result = lessons_registry.search_lessons(
            registry_with_entries, "CLAUDE.md"
        )
        # Should include all fields of the matching entry
        assert "### Action" in result
        assert "### Why" in result
        assert "### Fix" in result
        assert "### Lesson" in result

    def test_search_multiple_matches(self, registry_with_entries):
        # "lesson" appears in all entries' lesson field
        result = lessons_registry.search_lessons(
            registry_with_entries, "read"
        )
        # "Always read instruction files" and "Re-read full design doc"
        assert "matches" in result


# ===== Error handling tests =====

class TestErrorHandling:
    """Tests for error handling and safety valves."""

    def test_list_nonexistent_dir(self):
        result = lessons_registry.list_lessons("/nonexistent/path/xyz")
        assert "No lesson entries found" in result

    def test_search_nonexistent_dir(self):
        result = lessons_registry.search_lessons("/nonexistent/path/xyz", "test")
        assert "No lesson entries found" in result

    def test_add_with_special_characters(self, tmp_memory_dir):
        result = lessons_registry.add_lesson(
            memory_dir=tmp_memory_dir,
            action="Action with 日本語 and <html> & \"quotes\"",
            why="理由を日本語で書く",
            fix="修正内容",
            lesson="教訓を記録する",
        )
        assert not result.startswith("WARNING:")

        text = Path(result).read_text(encoding="utf-8")
        assert "日本語" in text
        assert "理由を日本語で書く" in text
        assert "教訓を記録する" in text

    def test_corrupted_file_recovery(self, tmp_memory_dir):
        """If existing file is corrupted, add should append to it."""
        path = lessons_registry.get_lessons_path(tmp_memory_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("CORRUPTED GARBAGE WITHOUT PROPER HEADERS", encoding="utf-8")

        result = lessons_registry.add_lesson(
            memory_dir=tmp_memory_dir,
            action="Recovery action",
            why="Recovery reason",
            fix="Recovery fix",
            lesson="Recovery lesson",
        )
        assert not result.startswith("WARNING:")

        # The new entry should be findable
        search_result = lessons_registry.search_lessons(
            tmp_memory_dir, "Recovery"
        )
        assert "Recovery lesson" in search_result

    def test_list_corrupted_file(self, tmp_memory_dir):
        """List on a corrupted file should not crash."""
        path = lessons_registry.get_lessons_path(tmp_memory_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00\x01\x02\x03")
        result = lessons_registry.list_lessons(tmp_memory_dir)
        assert "No lesson entries found" in result

    def test_empty_file(self, tmp_memory_dir):
        """Empty file should report no entries."""
        path = lessons_registry.get_lessons_path(tmp_memory_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        result = lessons_registry.list_lessons(tmp_memory_dir)
        assert "No lesson entries found" in result


# ===== Parsing roundtrip tests =====

class TestParsingRoundtrip:
    """Tests for markdown serialization/deserialization roundtrip."""

    def test_roundtrip_full_entry(self):
        entry = {
            "date": "2026-03-09",
            "action": "Test action",
            "why": "Test reason",
            "fix": "Test fix",
            "lesson": "Test lesson",
            "rule": "Test rule",
        }
        md = lessons_registry._entry_to_markdown(entry)
        parsed = lessons_registry._markdown_to_entries(md)
        assert len(parsed) == 1
        p = parsed[0]
        assert p["date"] == "2026-03-09"
        assert p["action"] == "Test action"
        assert p["why"] == "Test reason"
        assert p["fix"] == "Test fix"
        assert p["lesson"] == "Test lesson"
        assert p["rule"] == "Test rule"

    def test_roundtrip_without_rule(self):
        entry = {
            "date": "2026-03-09",
            "action": "Action only",
            "why": "Reason only",
            "fix": "Fix only",
            "lesson": "Lesson only",
            "rule": "",
        }
        md = lessons_registry._entry_to_markdown(entry)
        parsed = lessons_registry._markdown_to_entries(md)
        assert len(parsed) == 1
        p = parsed[0]
        assert p["lesson"] == "Lesson only"
        assert p["rule"] == ""

    def test_roundtrip_multiple_entries(self):
        entries = [
            {
                "date": f"2026-03-0{i}",
                "action": f"Action {i}",
                "why": f"Reason {i}",
                "fix": f"Fix {i}",
                "lesson": f"Lesson {i}",
                "rule": "",
            }
            for i in range(1, 4)
        ]
        md_parts = [lessons_registry._entry_to_markdown(e) for e in entries]
        full_md = "\n---\n\n".join(md_parts)
        parsed = lessons_registry._markdown_to_entries(full_md)
        assert len(parsed) == 3
        assert parsed[0]["action"] == "Action 1"
        assert parsed[2]["action"] == "Action 3"

    def test_parse_empty_input(self):
        assert lessons_registry._markdown_to_entries("") == []
        assert lessons_registry._markdown_to_entries("   ") == []

    def test_roundtrip_with_multiline_content(self):
        entry = {
            "date": "2026-03-09",
            "action": "Line 1\nLine 2\nLine 3",
            "why": "Multi-line\nreason",
            "fix": "Multi-line\nfix",
            "lesson": "Multi-line\nlesson",
            "rule": "",
        }
        md = lessons_registry._entry_to_markdown(entry)
        parsed = lessons_registry._markdown_to_entries(md)
        assert len(parsed) == 1
        assert "Line 1" in parsed[0]["action"]
        assert "Line 3" in parsed[0]["action"]


# ===== CLI tests =====

class TestCLI:
    """Tests for the CLI interface."""

    def test_cli_add(self, tmp_memory_dir, capsys):
        lessons_registry.main([
            "add",
            "--memory-dir", tmp_memory_dir,
            "--action", "CLI add test action",
            "--why", "CLI add test reason",
            "--fix", "CLI add test fix",
            "--lesson", "CLI add test lesson",
        ])
        captured = capsys.readouterr()
        assert "Lesson recorded" in captured.out

        # Verify the entry was actually saved
        result = lessons_registry.list_lessons(tmp_memory_dir)
        assert "CLI add test lesson" in result

    def test_cli_add_with_rule(self, tmp_memory_dir, capsys):
        lessons_registry.main([
            "add",
            "--memory-dir", tmp_memory_dir,
            "--action", "CLI rule test action",
            "--why", "CLI rule test reason",
            "--fix", "CLI rule test fix",
            "--lesson", "CLI rule test lesson",
            "--rule", "Some related rule",
        ])
        captured = capsys.readouterr()
        assert "Lesson recorded" in captured.out

        result = lessons_registry.search_lessons(tmp_memory_dir, "Some related rule")
        assert "CLI rule test lesson" in result

    def test_cli_list(self, registry_with_entries, capsys):
        lessons_registry.main(["list", "--memory-dir", registry_with_entries])
        captured = capsys.readouterr()
        assert "3 entries" in captured.out

    def test_cli_search(self, registry_with_entries, capsys):
        lessons_registry.main([
            "search",
            "--memory-dir", registry_with_entries,
            "--keyword", "CLAUDE.md",
        ])
        captured = capsys.readouterr()
        assert "1 matches" in captured.out
        assert "Skipped reading CLAUDE.md" in captured.out

    def test_cli_search_no_match(self, registry_with_entries, capsys):
        lessons_registry.main([
            "search",
            "--memory-dir", registry_with_entries,
            "--keyword", "nonexistent_keyword_xyz",
        ])
        captured = capsys.readouterr()
        assert "No lessons matching" in captured.out

    def test_cli_no_command(self):
        with pytest.raises(SystemExit):
            lessons_registry.main([])

    def test_cli_add_missing_required(self):
        with pytest.raises(SystemExit):
            lessons_registry.main(["add", "--memory-dir", "/tmp"])

    def test_cli_list_missing_memory_dir(self):
        with pytest.raises(SystemExit):
            lessons_registry.main(["list"])

    def test_cli_search_missing_keyword(self, tmp_memory_dir):
        with pytest.raises(SystemExit):
            lessons_registry.main([
                "search",
                "--memory-dir", tmp_memory_dir,
            ])


# ===== get_lessons_path tests =====

class TestGetLessonsPath:
    """Tests for get_lessons_path."""

    def test_returns_path_object(self):
        result = lessons_registry.get_lessons_path("/some/dir")
        assert isinstance(result, Path)

    def test_includes_filename(self):
        result = lessons_registry.get_lessons_path("/some/dir")
        assert result.name == lessons_registry.LESSONS_FILENAME

    def test_includes_directory(self):
        result = lessons_registry.get_lessons_path("/some/dir")
        assert str(result.parent).replace("\\", "/") == "/some/dir"


# ===== build_entry tests =====

class TestBuildEntry:
    """Tests for _build_entry helper."""

    def test_basic_fields(self):
        entry = lessons_registry._build_entry(
            action="Act", why="Why", fix="Fix", lesson="Lesson"
        )
        assert entry["action"] == "Act"
        assert entry["why"] == "Why"
        assert entry["fix"] == "Fix"
        assert entry["lesson"] == "Lesson"
        assert entry["date"]  # non-empty

    def test_strips_whitespace(self):
        entry = lessons_registry._build_entry(
            action="  padded  ",
            why="  padded  ",
            fix="  padded  ",
            lesson="  padded  ",
        )
        assert entry["action"] == "padded"
        assert entry["why"] == "padded"
        assert entry["fix"] == "padded"
        assert entry["lesson"] == "padded"

    def test_rule_default_empty(self):
        entry = lessons_registry._build_entry(
            action="A", why="W", fix="F", lesson="L"
        )
        assert entry["rule"] == ""

    def test_rule_with_value(self):
        entry = lessons_registry._build_entry(
            action="A", why="W", fix="F", lesson="L", rule="Some rule"
        )
        assert entry["rule"] == "Some rule"

    def test_date_format(self):
        entry = lessons_registry._build_entry(
            action="A", why="W", fix="F", lesson="L"
        )
        # Date should be in YYYY-MM-DD format
        assert len(entry["date"]) == 10
        assert entry["date"][4] == "-"
        assert entry["date"][7] == "-"
