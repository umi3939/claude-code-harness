"""Tests for file_io utility module."""

import json
import os
import tempfile
import shutil
import glob as glob_module

import pytest


# ---------------------------------------------------------------------------
# safe_load_json
# ---------------------------------------------------------------------------

class TestSafeLoadJson:
    """Tests for safe_load_json."""

    def test_load_valid_json(self, tmp_path):
        from file_io import safe_load_json

        p = tmp_path / "data.json"
        p.write_text('{"key": "value"}', encoding="utf-8")
        result = safe_load_json(str(p))
        assert result == {"key": "value"}

    def test_load_json_list(self, tmp_path):
        """safe_load_json should return any JSON type, not just dict."""
        from file_io import safe_load_json

        p = tmp_path / "list.json"
        p.write_text('[1, 2, 3]', encoding="utf-8")
        result = safe_load_json(str(p))
        assert result == [1, 2, 3]

    def test_file_not_found_returns_default(self, tmp_path):
        from file_io import safe_load_json

        result = safe_load_json(str(tmp_path / "missing.json"))
        assert result is None

    def test_file_not_found_returns_custom_default(self, tmp_path):
        from file_io import safe_load_json

        result = safe_load_json(str(tmp_path / "missing.json"), default={})
        assert result == {}

    def test_parse_error_returns_default(self, tmp_path):
        from file_io import safe_load_json

        p = tmp_path / "bad.json"
        p.write_text("not json at all", encoding="utf-8")
        result = safe_load_json(str(p), default={"fallback": True})
        assert result == {"fallback": True}

    def test_empty_file_returns_default(self, tmp_path):
        from file_io import safe_load_json

        p = tmp_path / "empty.json"
        p.write_text("", encoding="utf-8")
        result = safe_load_json(str(p))
        assert result is None

    def test_unicode_content(self, tmp_path):
        from file_io import safe_load_json

        p = tmp_path / "unicode.json"
        p.write_text('{"name": "日本語テスト"}', encoding="utf-8")
        result = safe_load_json(str(p))
        assert result == {"name": "日本語テスト"}


# ---------------------------------------------------------------------------
# atomic_write_json
# ---------------------------------------------------------------------------

class TestAtomicWriteJson:
    """Tests for atomic_write_json."""

    def test_write_and_read_back(self, tmp_path):
        from file_io import safe_load_json, atomic_write_json

        p = tmp_path / "out.json"
        data = {"hello": "world", "num": 42}
        atomic_write_json(str(p), data)
        result = safe_load_json(str(p))
        assert result == data

    def test_creates_parent_directories(self, tmp_path):
        from file_io import atomic_write_json

        p = tmp_path / "a" / "b" / "c" / "out.json"
        atomic_write_json(str(p), {"nested": True})
        assert p.exists()
        assert json.loads(p.read_text(encoding="utf-8")) == {"nested": True}

    def test_overwrites_existing_file(self, tmp_path):
        from file_io import safe_load_json, atomic_write_json

        p = tmp_path / "data.json"
        atomic_write_json(str(p), {"v": 1})
        atomic_write_json(str(p), {"v": 2})
        assert safe_load_json(str(p)) == {"v": 2}

    def test_unicode_write(self, tmp_path):
        from file_io import safe_load_json, atomic_write_json

        p = tmp_path / "unicode.json"
        data = {"name": "日本語", "emoji": "🎉"}
        atomic_write_json(str(p), data)
        result = safe_load_json(str(p))
        assert result == data

    def test_no_orphan_tempfiles_on_success(self, tmp_path):
        from file_io import atomic_write_json

        atomic_write_json(str(tmp_path / "ok.json"), {"x": 1})
        # Only the target file should exist
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name == "ok.json"

    def test_custom_indent(self, tmp_path):
        from file_io import atomic_write_json

        p = tmp_path / "indented.json"
        atomic_write_json(str(p), {"a": 1}, indent=4)
        text = p.read_text(encoding="utf-8")
        assert "    " in text  # 4-space indent


# ---------------------------------------------------------------------------
# safe_load_jsonl
# ---------------------------------------------------------------------------

class TestSafeLoadJsonl:
    """Tests for safe_load_jsonl."""

    def test_load_valid_jsonl(self, tmp_path):
        from file_io import safe_load_jsonl

        p = tmp_path / "data.jsonl"
        lines = ['{"a": 1}\n', '{"b": 2}\n', '{"c": 3}\n']
        p.write_text("".join(lines), encoding="utf-8")
        result = safe_load_jsonl(str(p))
        assert result == [{"a": 1}, {"b": 2}, {"c": 3}]

    def test_file_not_found_returns_empty(self, tmp_path):
        from file_io import safe_load_jsonl

        result = safe_load_jsonl(str(tmp_path / "missing.jsonl"))
        assert result == []

    def test_skips_invalid_lines(self, tmp_path):
        from file_io import safe_load_jsonl

        p = tmp_path / "mixed.jsonl"
        content = '{"good": 1}\nBAD LINE\n{"good": 2}\n'
        p.write_text(content, encoding="utf-8")
        result = safe_load_jsonl(str(p))
        assert result == [{"good": 1}, {"good": 2}]

    def test_skips_empty_lines(self, tmp_path):
        from file_io import safe_load_jsonl

        p = tmp_path / "blanks.jsonl"
        content = '{"a": 1}\n\n\n{"b": 2}\n'
        p.write_text(content, encoding="utf-8")
        result = safe_load_jsonl(str(p))
        assert result == [{"a": 1}, {"b": 2}]

    def test_empty_file(self, tmp_path):
        from file_io import safe_load_jsonl

        p = tmp_path / "empty.jsonl"
        p.write_text("", encoding="utf-8")
        result = safe_load_jsonl(str(p))
        assert result == []


# ---------------------------------------------------------------------------
# append_jsonl
# ---------------------------------------------------------------------------

class TestAppendJsonl:
    """Tests for append_jsonl."""

    def test_append_to_new_file(self, tmp_path):
        from file_io import append_jsonl, safe_load_jsonl

        p = tmp_path / "log.jsonl"
        append_jsonl(str(p), {"event": "start"})
        result = safe_load_jsonl(str(p))
        assert result == [{"event": "start"}]

    def test_append_multiple_entries(self, tmp_path):
        from file_io import append_jsonl, safe_load_jsonl

        p = tmp_path / "log.jsonl"
        append_jsonl(str(p), {"n": 1})
        append_jsonl(str(p), {"n": 2})
        append_jsonl(str(p), {"n": 3})
        result = safe_load_jsonl(str(p))
        assert len(result) == 3
        assert result[2] == {"n": 3}

    def test_creates_parent_directories(self, tmp_path):
        from file_io import append_jsonl

        p = tmp_path / "sub" / "dir" / "log.jsonl"
        append_jsonl(str(p), {"x": 1})
        assert p.exists()

    def test_each_entry_is_single_line(self, tmp_path):
        from file_io import append_jsonl

        p = tmp_path / "log.jsonl"
        append_jsonl(str(p), {"a": 1})
        append_jsonl(str(p), {"b": 2})
        lines = p.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2


# ---------------------------------------------------------------------------
# rotate_jsonl
# ---------------------------------------------------------------------------

class TestRotateJsonl:
    """Tests for rotate_jsonl."""

    def test_no_rotation_needed(self, tmp_path):
        from file_io import append_jsonl, rotate_jsonl, safe_load_jsonl

        p = tmp_path / "log.jsonl"
        for i in range(5):
            append_jsonl(str(p), {"n": i})
        rotated = rotate_jsonl(str(p), max_lines=10)
        assert rotated is False
        assert len(safe_load_jsonl(str(p))) == 5

    def test_rotation_keeps_latest(self, tmp_path):
        from file_io import append_jsonl, rotate_jsonl, safe_load_jsonl

        p = tmp_path / "log.jsonl"
        for i in range(20):
            append_jsonl(str(p), {"n": i})
        rotated = rotate_jsonl(str(p), max_lines=10)
        assert rotated is True
        result = safe_load_jsonl(str(p))
        assert len(result) == 10
        # Should keep the last 10 entries (n=10..19)
        assert result[0] == {"n": 10}
        assert result[-1] == {"n": 19}

    def test_rotation_exact_boundary(self, tmp_path):
        from file_io import append_jsonl, rotate_jsonl, safe_load_jsonl

        p = tmp_path / "log.jsonl"
        for i in range(10):
            append_jsonl(str(p), {"n": i})
        rotated = rotate_jsonl(str(p), max_lines=10)
        assert rotated is False
        assert len(safe_load_jsonl(str(p))) == 10

    def test_rotation_file_not_found(self, tmp_path):
        from file_io import rotate_jsonl

        rotated = rotate_jsonl(str(tmp_path / "missing.jsonl"), max_lines=10)
        assert rotated is False

    def test_rotation_default_max_lines(self, tmp_path):
        from file_io import append_jsonl, rotate_jsonl, safe_load_jsonl

        p = tmp_path / "log.jsonl"
        for i in range(5):
            append_jsonl(str(p), {"n": i})
        # Default is 1000, so no rotation
        rotated = rotate_jsonl(str(p))
        assert rotated is False


# ---------------------------------------------------------------------------
# resolve_memory_dir
# ---------------------------------------------------------------------------

class TestResolveMemoryDir:
    """Tests for resolve_memory_dir."""

    def test_env_var_takes_priority(self, tmp_path, monkeypatch):
        from file_io import resolve_memory_dir

        mem_dir = str(tmp_path / "memory")
        os.makedirs(mem_dir, exist_ok=True)
        monkeypatch.setenv("MEMORY_DIR", mem_dir)
        result = resolve_memory_dir()
        assert result == mem_dir

    def test_env_var_nonexistent_dir_raises(self, tmp_path, monkeypatch):
        from file_io import resolve_memory_dir

        monkeypatch.setenv("MEMORY_DIR", str(tmp_path / "nonexistent"))
        with pytest.raises(RuntimeError, match="does not exist"):
            resolve_memory_dir()

    def test_global_default_creates_dir(self, tmp_path, monkeypatch):
        """When MEMORY_DIR is unset, uses ~/.claude/memory/ and creates it."""
        from file_io import resolve_memory_dir

        monkeypatch.delenv("MEMORY_DIR", raising=False)
        monkeypatch.setattr("os.path.expanduser", lambda x: str(tmp_path))
        result = resolve_memory_dir()
        expected = os.path.join(str(tmp_path), ".claude", "memory")
        assert result == expected
        assert os.path.isdir(expected)

    def test_global_default_existing_dir(self, tmp_path, monkeypatch):
        """When ~/.claude/memory/ already exists, returns it without error."""
        from file_io import resolve_memory_dir

        monkeypatch.delenv("MEMORY_DIR", raising=False)
        expected = tmp_path / ".claude" / "memory"
        expected.mkdir(parents=True)
        monkeypatch.setattr("os.path.expanduser", lambda x: str(tmp_path))
        result = resolve_memory_dir()
        assert result == str(expected)

    def test_env_var_empty_string_uses_global_default(self, tmp_path, monkeypatch):
        """Empty MEMORY_DIR env var should be treated as unset."""
        from file_io import resolve_memory_dir

        monkeypatch.setenv("MEMORY_DIR", "")
        monkeypatch.setattr("os.path.expanduser", lambda x: str(tmp_path))
        result = resolve_memory_dir()
        expected = os.path.join(str(tmp_path), ".claude", "memory")
        assert result == expected


# ---------------------------------------------------------------------------
# M-R5: append_jsonl with file locking
# ---------------------------------------------------------------------------

class TestAppendJsonlLocking:
    """M-R5: append_jsonl should use file locking to prevent interleaving."""

    def test_append_jsonl_uses_locking(self):
        """append_jsonl should use file locking for concurrent safety."""
        import inspect
        from file_io import append_jsonl
        source = inspect.getsource(append_jsonl)
        # After fix, should contain locking mechanism
        assert "lock" in source.lower() or "msvcrt" in source or "fcntl" in source, \
            "append_jsonl should use file locking"

    def test_append_jsonl_concurrent_writes_no_interleave(self, tmp_path):
        """Concurrent appends should produce valid JSONL (no interleaved lines)."""
        from file_io import append_jsonl
        import threading

        path = str(tmp_path / "concurrent.jsonl")
        errors = []

        def writer(thread_id, count):
            try:
                for i in range(count):
                    append_jsonl(path, {"thread": thread_id, "i": i})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(t, 20)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors during concurrent write: {errors}"

        # Verify all lines are valid JSON
        with open(path, "r") as f:
            lines = f.readlines()

        assert len(lines) == 100, f"Expected 100 lines, got {len(lines)}"
        for i, line in enumerate(lines):
            try:
                json.loads(line.strip())
            except json.JSONDecodeError:
                pytest.fail(f"Line {i} is not valid JSON: {line!r}")
