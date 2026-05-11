"""Unit tests for file_io.resolve_project_root().

Covers the 8 cases of design doc §3.3 (env state x expected behavior),
plus 1 sanity test for env-state isolation between tests (N8).

Constraints:
- All env mutation via monkeypatch (auto-restored after each test) -> N8.
- pytest -n is NOT used (no parallel execution); see C-add-4.
"""

import os
import sys

import pytest

_TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

import file_io  # noqa: E402

_EXPECTED_FALLBACK = os.path.dirname(
    os.path.dirname(os.path.abspath(file_io.__file__))
)


def test_unset_returns_fallback(monkeypatch):
    """env unset -> fallback value, no exception."""
    monkeypatch.delenv("CLAUDE_PROJECT_ROOT", raising=False)
    result = file_io.resolve_project_root()
    assert result == _EXPECTED_FALLBACK


def test_empty_string_returns_fallback(monkeypatch):
    """env="" -> fallback value, no exception."""
    monkeypatch.setenv("CLAUDE_PROJECT_ROOT", "")
    result = file_io.resolve_project_root()
    assert result == _EXPECTED_FALLBACK


def test_whitespace_returns_fallback(monkeypatch):
    """env="   " -> strip-empty -> fallback. Python str.strip() removes all Unicode whitespace (including \u3000), so any all-whitespace value falls through to fallback."""
    monkeypatch.setenv("CLAUDE_PROJECT_ROOT", "   ")
    result = file_io.resolve_project_root()
    assert result == _EXPECTED_FALLBACK


def test_relative_path_raises(monkeypatch):
    """env=relative path -> RuntimeError with 'absolute' in message."""
    monkeypatch.setenv("CLAUDE_PROJECT_ROOT", "./foo")
    with pytest.raises(RuntimeError) as exc_info:
        file_io.resolve_project_root()
    assert "absolute" in str(exc_info.value).lower()


def test_nonexistent_absolute_raises(monkeypatch, tmp_path):
    """env=absolute but non-existent path -> RuntimeError with 'does not exist'."""
    missing = tmp_path / "does_not_exist_subdir"
    monkeypatch.setenv("CLAUDE_PROJECT_ROOT", str(missing))
    with pytest.raises(RuntimeError) as exc_info:
        file_io.resolve_project_root()
    assert "does not exist" in str(exc_info.value).lower()


def test_file_path_raises(monkeypatch, tmp_path):
    """env=existing file (not directory) -> RuntimeError with 'directory'."""
    a_file = tmp_path / "some_file.txt"
    a_file.write_text("placeholder", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_ROOT", str(a_file))
    with pytest.raises(RuntimeError) as exc_info:
        file_io.resolve_project_root()
    assert "directory" in str(exc_info.value).lower()


def test_valid_dir_returns_normalized(monkeypatch, tmp_path):
    """env=existing directory -> absolute path returned, normalized."""
    monkeypatch.setenv("CLAUDE_PROJECT_ROOT", str(tmp_path))
    result = file_io.resolve_project_root()
    assert result == os.path.normpath(str(tmp_path))
    assert os.path.isabs(result)
    assert os.path.isdir(result)


def test_trailing_separator_normalized(monkeypatch, tmp_path):
    """env=tmpdir with trailing separator -> normpath-ed value returned."""
    raw = str(tmp_path) + os.sep
    monkeypatch.setenv("CLAUDE_PROJECT_ROOT", raw)
    result = file_io.resolve_project_root()
    assert result == os.path.normpath(raw)
    assert not result.endswith(os.sep) or result == os.sep


def test_env_isolation_sanity():
    """Sanity: after the prior tests, CLAUDE_PROJECT_ROOT must not leak.

    Per N8, monkeypatch must restore env state. If this test fails, the
    fixture restoration is broken.
    """
    assert os.environ.get("CLAUDE_PROJECT_ROOT") in (None, ""), (
        "CLAUDE_PROJECT_ROOT leaked from prior tests; N8 violation"
    )
