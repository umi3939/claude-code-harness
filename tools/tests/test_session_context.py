"""Tests for session_context.py."""

import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# Add parent directory to path so we can import session_context
sys.path.insert(0, str(Path(__file__).parent.parent))
import session_context


# --- Fixtures ---

@pytest.fixture
def tmp_memory_dir(tmp_path):
    """Provide a temporary memory directory."""
    return str(tmp_path)


@pytest.fixture
def context_with_records(tmp_memory_dir):
    """Create a memory dir with 3 pre-saved records."""
    for i in range(3):
        session_context.save_context(
            memory_dir=tmp_memory_dir,
            summary=f"Session {i + 1} work",
            completed=f"task_a{i},task_b{i}",
            pending=f"task_c{i}",
            decisions=f"Decision {i + 1}",
            issues=f"Issue {i + 1}" if i % 2 == 0 else "",
            next_actions=f"Next {i + 1}",
        )
    return tmp_memory_dir


# ===== save_context tests =====

class TestSaveContext:
    """Tests for the save_context function."""

    def test_save_creates_file(self, tmp_memory_dir):
        result = session_context.save_context(
            memory_dir=tmp_memory_dir,
            summary="Test summary",
        )
        assert not result.startswith("ERROR:")
        assert Path(result).exists()

    def test_save_returns_path(self, tmp_memory_dir):
        result = session_context.save_context(
            memory_dir=tmp_memory_dir,
            summary="Test summary",
        )
        expected = str(session_context.get_context_path(tmp_memory_dir))
        assert result == expected

    def test_save_with_all_fields(self, tmp_memory_dir):
        result = session_context.save_context(
            memory_dir=tmp_memory_dir,
            summary="Full save test",
            completed="task1,task2",
            pending="task3,task4",
            decisions="Chose approach A over B",
            issues="Blocked on API access",
            next_actions="Resume task3 first",
        )
        assert not result.startswith("ERROR:")

        text = Path(result).read_text(encoding="utf-8")
        assert "Full save test" in text
        assert "task1" in text
        assert "task2" in text
        assert "task3" in text
        assert "task4" in text
        assert "Chose approach A over B" in text
        assert "Blocked on API access" in text
        assert "Resume task3 first" in text

    def test_save_minimal_fields(self, tmp_memory_dir):
        result = session_context.save_context(
            memory_dir=tmp_memory_dir,
            summary="Minimal",
        )
        assert not result.startswith("ERROR:")
        text = Path(result).read_text(encoding="utf-8")
        assert "Minimal" in text

    def test_save_creates_parent_dirs(self, tmp_memory_dir):
        nested = os.path.join(tmp_memory_dir, "deep", "nested", "dir")
        result = session_context.save_context(
            memory_dir=nested,
            summary="Deep save",
        )
        assert not result.startswith("ERROR:")
        assert Path(result).exists()

    def test_save_appends_to_existing(self, tmp_memory_dir):
        session_context.save_context(
            memory_dir=tmp_memory_dir, summary="First session"
        )
        session_context.save_context(
            memory_dir=tmp_memory_dir, summary="Second session"
        )

        text = session_context.get_context_path(tmp_memory_dir).read_text(
            encoding="utf-8"
        )
        assert "First session" in text
        assert "Second session" in text

    def test_save_markdown_format(self, tmp_memory_dir):
        result = session_context.save_context(
            memory_dir=tmp_memory_dir,
            summary="Format check",
            completed="done1",
            pending="todo1",
        )
        text = Path(result).read_text(encoding="utf-8")
        assert text.startswith("# Session Context History")
        assert "## Session:" in text
        assert "### Summary" in text
        assert "### Completed Tasks" in text
        assert "- [x] done1" in text
        assert "### Pending Tasks" in text
        assert "- [ ] todo1" in text

    def test_save_records_datetime(self, tmp_memory_dir):
        result = session_context.save_context(
            memory_dir=tmp_memory_dir, summary="Datetime test"
        )
        text = Path(result).read_text(encoding="utf-8")
        # Should contain a date in YYYY-MM-DD format
        now = time.strftime("%Y-%m-%d")
        assert now in text


# ===== load_context tests =====

class TestLoadContext:
    """Tests for the load_context function."""

    def test_load_no_file(self, tmp_memory_dir):
        result = session_context.load_context(tmp_memory_dir)
        assert "No session context found" in result

    def test_load_returns_latest(self, context_with_records):
        result = session_context.load_context(context_with_records)
        assert "Session 3 work" in result

    def test_load_does_not_include_old(self, context_with_records):
        result = session_context.load_context(context_with_records)
        # load returns only the latest record
        assert "Session 1 work" not in result

    def test_load_contains_all_sections(self, tmp_memory_dir):
        session_context.save_context(
            memory_dir=tmp_memory_dir,
            summary="Load test",
            completed="doneA",
            pending="todoB",
            decisions="Dec X",
            issues="Issue Y",
            next_actions="Next Z",
        )
        result = session_context.load_context(tmp_memory_dir)
        assert "Load test" in result
        assert "doneA" in result
        assert "todoB" in result
        assert "Dec X" in result
        assert "Issue Y" in result
        assert "Next Z" in result

    def test_load_corrupted_file(self, tmp_memory_dir):
        path = session_context.get_context_path(tmp_memory_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00\x01\x02\x03")
        result = session_context.load_context(tmp_memory_dir)
        # Should not crash, return graceful message
        assert "No session context found" in result or "Session" in result


# ===== list_contexts tests =====

class TestListContexts:
    """Tests for the list_contexts function."""

    def test_list_no_file(self, tmp_memory_dir):
        result = session_context.list_contexts(tmp_memory_dir)
        assert "No session context history" in result

    def test_list_shows_count(self, context_with_records):
        result = session_context.list_contexts(context_with_records)
        assert "3 records" in result

    def test_list_shows_summaries(self, context_with_records):
        result = session_context.list_contexts(context_with_records)
        assert "Session 1 work" in result
        assert "Session 2 work" in result
        assert "Session 3 work" in result

    def test_list_shows_numbering(self, context_with_records):
        result = session_context.list_contexts(context_with_records)
        assert "1." in result
        assert "2." in result
        assert "3." in result

    def test_list_truncates_long_summary(self, tmp_memory_dir):
        long_summary = "A" * 200
        session_context.save_context(
            memory_dir=tmp_memory_dir, summary=long_summary
        )
        result = session_context.list_contexts(tmp_memory_dir)
        assert "..." in result
        # Should show at most 80 chars of summary + "..."
        for line in result.split("\n"):
            if "AAA" in line:
                # The preview part should be truncated
                assert len(line) < 200


# ===== FIFO history tests =====

class TestFIFO:
    """Tests for FIFO history management."""

    def test_fifo_default_limit(self, tmp_memory_dir):
        for i in range(8):
            session_context.save_context(
                memory_dir=tmp_memory_dir, summary=f"Session {i}"
            )

        text = session_context.get_context_path(tmp_memory_dir).read_text(
            encoding="utf-8"
        )
        records = session_context._markdown_to_records(text)
        assert len(records) == session_context.DEFAULT_HISTORY_LIMIT

    def test_fifo_keeps_newest(self, tmp_memory_dir):
        for i in range(8):
            session_context.save_context(
                memory_dir=tmp_memory_dir, summary=f"Session {i}"
            )

        text = session_context.get_context_path(tmp_memory_dir).read_text(
            encoding="utf-8"
        )
        records = session_context._markdown_to_records(text)
        # The oldest ones (0, 1, 2) should be gone
        summaries = [r["summary"] for r in records]
        assert "Session 0" not in summaries
        assert "Session 1" not in summaries
        assert "Session 2" not in summaries
        assert "Session 7" in summaries

    def test_fifo_custom_limit(self, tmp_memory_dir):
        for i in range(5):
            session_context.save_context(
                memory_dir=tmp_memory_dir,
                summary=f"Session {i}",
                history_limit=2,
            )

        text = session_context.get_context_path(tmp_memory_dir).read_text(
            encoding="utf-8"
        )
        records = session_context._markdown_to_records(text)
        assert len(records) == 2

    def test_fifo_preserves_order(self, tmp_memory_dir):
        for i in range(3):
            session_context.save_context(
                memory_dir=tmp_memory_dir,
                summary=f"Session {i}",
                history_limit=3,
            )

        text = session_context.get_context_path(tmp_memory_dir).read_text(
            encoding="utf-8"
        )
        records = session_context._markdown_to_records(text)
        assert records[0]["summary"] == "Session 0"
        assert records[1]["summary"] == "Session 1"
        assert records[2]["summary"] == "Session 2"


# ===== Error handling tests =====

class TestErrorHandling:
    """Tests for error handling and safety valves."""

    def test_load_nonexistent_dir(self):
        result = session_context.load_context("/nonexistent/path/xyz")
        assert "No session context found" in result

    def test_list_nonexistent_dir(self):
        result = session_context.list_contexts("/nonexistent/path/xyz")
        assert "No session context history" in result

    def test_save_oversized_record(self, tmp_memory_dir):
        huge = "X" * (session_context.MAX_RECORD_BYTES + 1000)
        result = session_context.save_context(
            memory_dir=tmp_memory_dir, summary=huge
        )
        assert result.startswith("ERROR:")
        assert "exceeds limit" in result

    def test_empty_summary_still_saves(self, tmp_memory_dir):
        result = session_context.save_context(
            memory_dir=tmp_memory_dir, summary=""
        )
        # Empty summary is technically allowed (CLI interactive mode rejects it,
        # but the core function accepts it)
        assert not result.startswith("ERROR:")

    def test_save_with_special_characters(self, tmp_memory_dir):
        result = session_context.save_context(
            memory_dir=tmp_memory_dir,
            summary="Summary with 日本語 and <html> & \"quotes\"",
            completed="task with, commas, inside",
        )
        assert not result.startswith("ERROR:")
        loaded = session_context.load_context(tmp_memory_dir)
        assert "日本語" in loaded

    def test_corrupted_file_recovery(self, tmp_memory_dir):
        """If existing file is corrupted, save should start fresh."""
        path = session_context.get_context_path(tmp_memory_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("CORRUPTED GARBAGE WITHOUT PROPER HEADERS", encoding="utf-8")

        result = session_context.save_context(
            memory_dir=tmp_memory_dir, summary="Recovery test"
        )
        assert not result.startswith("ERROR:")

        loaded = session_context.load_context(tmp_memory_dir)
        assert "Recovery test" in loaded


# ===== Record parsing roundtrip tests =====

class TestParsingRoundtrip:
    """Tests for markdown serialization/deserialization roundtrip."""

    def test_roundtrip_full_record(self):
        record = {
            "session_datetime": "2026-03-09 10:00:00",
            "summary": "Test roundtrip",
            "completed": ["taskA", "taskB"],
            "pending": ["taskC"],
            "decisions": "Decision 1",
            "issues": "Issue 1",
            "next_actions": "Next 1",
        }
        md = session_context._record_to_markdown(record)
        parsed = session_context._markdown_to_records(md)
        assert len(parsed) == 1
        p = parsed[0]
        assert p["session_datetime"] == "2026-03-09 10:00:00"
        assert p["summary"] == "Test roundtrip"
        assert p["completed"] == ["taskA", "taskB"]
        assert p["pending"] == ["taskC"]
        assert p["decisions"] == "Decision 1"
        assert p["issues"] == "Issue 1"
        assert p["next_actions"] == "Next 1"

    def test_roundtrip_empty_fields(self):
        record = {
            "session_datetime": "2026-03-09 12:00:00",
            "summary": "Minimal record",
            "completed": [],
            "pending": [],
            "decisions": "",
            "issues": "",
            "next_actions": "",
        }
        md = session_context._record_to_markdown(record)
        parsed = session_context._markdown_to_records(md)
        assert len(parsed) == 1
        p = parsed[0]
        assert p["summary"] == "Minimal record"
        assert p["completed"] == []
        assert p["pending"] == []

    def test_roundtrip_multiple_records(self):
        records = [
            {
                "session_datetime": f"2026-03-0{i} 10:00:00",
                "summary": f"Session {i}",
                "completed": [f"task{i}"],
                "pending": [],
                "decisions": "",
                "issues": "",
                "next_actions": "",
            }
            for i in range(1, 4)
        ]
        md_parts = [session_context._record_to_markdown(r) for r in records]
        full_md = "\n---\n\n".join(md_parts)
        parsed = session_context._markdown_to_records(full_md)
        assert len(parsed) == 3
        assert parsed[0]["summary"] == "Session 1"
        assert parsed[2]["summary"] == "Session 3"

    def test_parse_empty_input(self):
        assert session_context._markdown_to_records("") == []
        assert session_context._markdown_to_records("   ") == []


# ===== CLI tests =====

class TestCLI:
    """Tests for the CLI interface."""

    def test_cli_save_noninteractive(self, tmp_memory_dir):
        session_context.main([
            "save",
            "--memory-dir", tmp_memory_dir,
            "--summary", "CLI save test",
            "--completed", "a,b",
            "--pending", "c",
            "--decisions", "dec",
            "--issues", "iss",
            "--next", "nxt",
        ])
        loaded = session_context.load_context(tmp_memory_dir)
        assert "CLI save test" in loaded

    def test_cli_load(self, tmp_memory_dir, capsys):
        session_context.save_context(
            memory_dir=tmp_memory_dir, summary="For CLI load"
        )
        session_context.main(["load", "--memory-dir", tmp_memory_dir])
        captured = capsys.readouterr()
        assert "For CLI load" in captured.out

    def test_cli_list(self, context_with_records, capsys):
        session_context.main(["list", "--memory-dir", context_with_records])
        captured = capsys.readouterr()
        assert "3 records" in captured.out

    def test_cli_no_command(self):
        with pytest.raises(SystemExit):
            session_context.main([])

    def test_cli_save_missing_memory_dir(self):
        with pytest.raises(SystemExit):
            session_context.main(["save"])

    def test_cli_custom_history_limit(self, tmp_memory_dir):
        for i in range(5):
            session_context.main([
                "save",
                "--memory-dir", tmp_memory_dir,
                "--summary", f"Batch {i}",
                "--history-limit", "2",
            ])
        text = session_context.get_context_path(tmp_memory_dir).read_text(
            encoding="utf-8"
        )
        records = session_context._markdown_to_records(text)
        assert len(records) == 2


# ===== get_context_path tests =====

class TestGetContextPath:
    """Tests for get_context_path."""

    def test_returns_path_object(self):
        result = session_context.get_context_path("/some/dir")
        assert isinstance(result, Path)

    def test_includes_filename(self):
        result = session_context.get_context_path("/some/dir")
        assert result.name == session_context.CONTEXT_FILENAME

    def test_includes_directory(self):
        result = session_context.get_context_path("/some/dir")
        assert str(result.parent).replace("\\", "/") == "/some/dir"


# ===== build_record tests =====

class TestBuildRecord:
    """Tests for _build_record helper."""

    def test_basic_fields(self):
        record = session_context._build_record(summary="Test")
        assert record["summary"] == "Test"
        assert isinstance(record["completed"], list)
        assert isinstance(record["pending"], list)
        assert record["session_datetime"]  # non-empty

    def test_comma_separated_tasks(self):
        record = session_context._build_record(
            summary="X",
            completed="a, b, c",
            pending="d, e",
        )
        assert record["completed"] == ["a", "b", "c"]
        assert record["pending"] == ["d", "e"]

    def test_empty_task_strings(self):
        record = session_context._build_record(
            summary="X", completed="", pending=""
        )
        assert record["completed"] == []
        assert record["pending"] == []

    def test_strips_whitespace(self):
        record = session_context._build_record(summary="  padded  ")
        assert record["summary"] == "padded"

    def test_handles_trailing_comma(self):
        record = session_context._build_record(
            summary="X", completed="a,b,"
        )
        assert record["completed"] == ["a", "b"]


# ===== Markdown injection tests (MED-1) =====

class TestMarkdownInjection:
    """User input containing '## Session:' must not corrupt record parsing."""

    def test_summary_with_session_header(self, tmp_memory_dir):
        """Summary containing '## Session:' on a non-line-start position."""
        result = session_context.save_context(
            memory_dir=tmp_memory_dir,
            summary="Work on ## Session: header parsing",
        )
        assert not result.startswith("ERROR:")
        loaded = session_context.load_context(tmp_memory_dir)
        assert "## Session: header parsing" in loaded

    def test_decisions_with_session_header(self, tmp_memory_dir):
        """Decisions field containing '## Session:' should not split records."""
        session_context.save_context(
            memory_dir=tmp_memory_dir,
            summary="First session",
            decisions="Decided to handle ## Session: in user text",
        )
        session_context.save_context(
            memory_dir=tmp_memory_dir,
            summary="Second session",
        )
        text = session_context.get_context_path(tmp_memory_dir).read_text(
            encoding="utf-8"
        )
        records = session_context._markdown_to_records(text)
        assert len(records) == 2
        assert records[0]["decisions"] == "Decided to handle ## Session: in user text"
        assert records[1]["summary"] == "Second session"

    def test_roundtrip_with_embedded_session_header(self):
        """Roundtrip parse of a record whose content includes '## Session:'."""
        record = {
            "session_datetime": "2026-03-09 15:00:00",
            "summary": "Contains ## Session: fake header",
            "completed": [],
            "pending": [],
            "decisions": "Also ## Session: here",
            "issues": "",
            "next_actions": "",
        }
        md = session_context._record_to_markdown(record)
        parsed = session_context._markdown_to_records(md)
        assert len(parsed) == 1
        assert parsed[0]["summary"] == "Contains ## Session: fake header"
        assert parsed[0]["decisions"] == "Also ## Session: here"

# ================================================================
# H4: Atomic Write Temp File Cleanup
# ================================================================


class TestAtomicWriteCleanup:
    """Verify temp files are cleaned up on failure."""

    def test_temp_file_cleaned_on_replace_failure(self, tmp_path):
        """If os.replace fails, temp file should be removed in finally block."""
        import tempfile
        from unittest.mock import patch
        import session_context

        memory_dir = str(tmp_path / "memory")
        os.makedirs(memory_dir, exist_ok=True)

        # Mock os.replace to fail
        with patch("session_context.os.replace", side_effect=OSError("replace failed")):
            result = session_context.save_context(
                memory_dir=memory_dir,
                summary="test summary",
                completed=["task1"],
            )

        # Should return error
        assert "ERROR" in result

        # No stale temp files should remain
        parent = tmp_path / "memory"
        temp_files = list(parent.glob(".session_context_*.tmp"))
        assert len(temp_files) == 0, f"Stale temp files found: {temp_files}"

