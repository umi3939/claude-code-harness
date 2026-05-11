"""Integration tests for memory_mcp_server CLAUDE_PROJECT_ROOT env var reflection.

Verifies that the structural change to memory_mcp_server.py line 24-30 (Phase 2 F2)
makes _PROJECT_ROOT and its derived paths (GROWTH_DIR, hooks flag_dir) reflect the
CLAUDE_PROJECT_ROOT env var.

Constraints (design doc §6.2, plan §4 Phase 2 Step 2-1, pre-impl §5 C-add-1/C-add-7):
- All env mutation via monkeypatch (auto-restored) -> N8.
- importlib.reload(memory_mcp_server) is required because env is read at module top.
- pytest -n is NOT used (importlib.reload is not parallel-safe; see C-add-4).
- autouse fixture deletes env + reloads module before/after each test to prevent state leak.
"""

import importlib
import os
import sys

import pytest

_TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

import memory_mcp_server  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_env_and_reload(monkeypatch):
    """Ensure clean env state and module reload around each test (N8 + reload isolation).

    Sanity-checks that pytest didn't leak the env var into this test process,
    then deletes the env, reloads the module so the test starts from a known state,
    and reloads again at teardown so subsequent tests/imports see fallback values.
    """
    monkeypatch.delenv("CLAUDE_PROJECT_ROOT", raising=False)
    importlib.reload(memory_mcp_server)
    yield
    # Teardown: ensure env is gone (monkeypatch handles it, but be explicit)
    # and reload module so other tests see fallback _PROJECT_ROOT.
    os.environ.pop("CLAUDE_PROJECT_ROOT", None)
    importlib.reload(memory_mcp_server)


def test_growth_dir_reflects_env(monkeypatch, tmp_path):
    """CLAUDE_PROJECT_ROOT=tmpdir -> GROWTH_DIR == tmpdir/growth."""
    monkeypatch.setenv("CLAUDE_PROJECT_ROOT", str(tmp_path))
    importlib.reload(memory_mcp_server)
    expected = os.path.join(os.path.normpath(str(tmp_path)), "growth")
    assert memory_mcp_server.GROWTH_DIR == expected


def test_hooks_dir_reflects_env(monkeypatch, tmp_path):
    """CLAUDE_PROJECT_ROOT=tmpdir -> _PROJECT_ROOT prefix matches tmpdir.

    The flag_dir = os.path.join(_PROJECT_ROOT, "hooks") computations occur inside
    function bodies (line 601, 1382, 1794), so we verify the source-of-truth
    _PROJECT_ROOT itself is updated. All flag_dir computations derive from it.
    """
    monkeypatch.setenv("CLAUDE_PROJECT_ROOT", str(tmp_path))
    importlib.reload(memory_mcp_server)
    expected_root = os.path.normpath(str(tmp_path))
    assert memory_mcp_server._PROJECT_ROOT == expected_root
    expected_hooks = os.path.join(expected_root, "hooks")
    assert os.path.join(memory_mcp_server._PROJECT_ROOT, "hooks") == expected_hooks


def test_unset_env_uses_fallback(monkeypatch):
    """env unset -> _PROJECT_ROOT == os.path.dirname(TOOLS_DIR) (backward compat)."""
    monkeypatch.delenv("CLAUDE_PROJECT_ROOT", raising=False)
    importlib.reload(memory_mcp_server)
    expected_fallback = os.path.dirname(memory_mcp_server.TOOLS_DIR)
    assert memory_mcp_server._PROJECT_ROOT == expected_fallback


def test_existing_test_project_root_paths_compat(monkeypatch):
    """Existing test_project_root_paths.py expectations remain valid under env-unset.

    Verifies that with env unset (the assumed precondition of the existing file),
    _PROJECT_ROOT equals the parent of TOOLS_DIR (the legacy fallback shape).
    This guards against regressions of the existing 14+ test cases.
    """
    monkeypatch.delenv("CLAUDE_PROJECT_ROOT", raising=False)
    importlib.reload(memory_mcp_server)
    assert os.environ.get("CLAUDE_PROJECT_ROOT") is None
    assert memory_mcp_server._PROJECT_ROOT == os.path.dirname(memory_mcp_server.TOOLS_DIR)
    assert os.path.isabs(memory_mcp_server._PROJECT_ROOT)
    assert os.path.isdir(memory_mcp_server._PROJECT_ROOT)
