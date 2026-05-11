"""Tests for PROJECT_ROOT-relative path constants.

Verifies that all modules use project-root-relative paths
instead of hardcoded ~/.claude/ paths.

NOTE: These tests must NOT be run with pytest -n (xdist parallel mode).
importlib.reload-based tests assume serial execution.
"""

import os
import sys
import unittest

import pytest

# Ensure tools/ is on path
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

# Expected project root: parent of tools/
EXPECTED_PROJECT_ROOT = os.path.dirname(TOOLS_DIR)


@pytest.fixture(autouse=True)
def _ensure_no_claude_project_root_env(monkeypatch):
    """Remove CLAUDE_PROJECT_ROOT before each test (Phase 3 / N8 structural guard).

    Existing tests assume env unset (fallback path). New env-reflection tests
    set the env explicitly via monkeypatch.setenv after this fixture runs.
    monkeypatch automatically restores at teardown.
    """
    monkeypatch.delenv("CLAUDE_PROJECT_ROOT", raising=False)


class TestCronSchedulerPaths(unittest.TestCase):
    """cron_scheduler.py path constants."""

    def test_cron_dir_is_project_relative(self):
        from cron_scheduler import CRON_DIR
        expected = os.path.join(EXPECTED_PROJECT_ROOT, "cron")
        self.assertEqual(CRON_DIR, expected)

    def test_heartbeat_default_path_is_project_relative(self):
        from cron_scheduler import HEARTBEAT_DEFAULT_PATH
        expected = os.path.join(EXPECTED_PROJECT_ROOT, "HEARTBEAT.md")
        self.assertEqual(HEARTBEAT_DEFAULT_PATH, expected)

    def test_heartbeat_actions_file_is_project_relative(self):
        from cron_scheduler import HEARTBEAT_ACTIONS_FILE
        expected = os.path.join(EXPECTED_PROJECT_ROOT, "cron", "heartbeat_actions.jsonl")
        self.assertEqual(HEARTBEAT_ACTIONS_FILE, expected)

    def test_no_home_claude_in_cron_paths(self):
        from cron_scheduler import CRON_DIR, HEARTBEAT_ACTIONS_FILE, HEARTBEAT_DEFAULT_PATH
        home_claude = os.path.join(os.path.expanduser("~"), ".claude")
        for path in [CRON_DIR, HEARTBEAT_DEFAULT_PATH, HEARTBEAT_ACTIONS_FILE]:
            self.assertFalse(
                path.startswith(home_claude),
                f"Path should not start with ~/.claude: {path}"
            )


class TestDiscordMcpServerPaths(unittest.TestCase):
    """discord_mcp_server.py path constants."""

    def test_discord_data_dir_is_project_relative(self):
        from discord_mcp_server import DISCORD_DATA_DIR
        expected = os.path.join(EXPECTED_PROJECT_ROOT, "discord_data")
        self.assertEqual(DISCORD_DATA_DIR, expected)


class TestDiscordReceiverModelsPaths(unittest.TestCase):
    """discord_receiver_models.py path constants."""

    def test_discord_data_dir_is_project_relative(self):
        from discord_receiver_models import DISCORD_DATA_DIR
        expected = os.path.join(EXPECTED_PROJECT_ROOT, "discord_data")
        self.assertEqual(DISCORD_DATA_DIR, expected)


class TestMessageEventHooksPaths(unittest.TestCase):
    """message_event_hooks.py path constants."""

    def test_discord_data_dir_is_project_relative(self):
        from message_event_hooks import DISCORD_DATA_DIR
        expected = os.path.join(EXPECTED_PROJECT_ROOT, "discord_data")
        self.assertEqual(DISCORD_DATA_DIR, expected)


class TestMessageLogHandlerPaths(unittest.TestCase):
    """message_log_handler.py path constants."""

    def test_discord_data_dir_is_project_relative(self):
        from message_log_handler import DISCORD_DATA_DIR
        expected = os.path.join(EXPECTED_PROJECT_ROOT, "discord_data")
        self.assertEqual(DISCORD_DATA_DIR, expected)


class TestMemoryMcpServerPaths(unittest.TestCase):
    """memory_mcp_server.py path constants."""

    def test_growth_dir_is_project_relative(self):
        from memory_mcp_server import GROWTH_DIR
        expected = os.path.join(EXPECTED_PROJECT_ROOT, "growth")
        self.assertEqual(GROWTH_DIR, expected)

    def test_project_root_constant_exists(self):
        from memory_mcp_server import _PROJECT_ROOT
        self.assertEqual(_PROJECT_ROOT, EXPECTED_PROJECT_ROOT)


class TestSessionSummaryGeneratorPaths(unittest.TestCase):
    """session_summary_generator.py path constants."""

    def test_claude_dir_is_project_root(self):
        from pathlib import Path

        from session_summary_generator import CLAUDE_DIR
        self.assertEqual(str(CLAUDE_DIR), str(Path(EXPECTED_PROJECT_ROOT)))


class TestSessionPostmortemPaths(unittest.TestCase):
    """session_postmortem.py path constants."""

    def test_claude_dir_is_project_root(self):
        from pathlib import Path

        from session_postmortem import CLAUDE_DIR
        self.assertEqual(str(CLAUDE_DIR), str(Path(EXPECTED_PROJECT_ROOT)))


class TestStatsUpdaterPaths(unittest.TestCase):
    """stats_updater.py path constants."""

    def test_claude_dir_is_project_root(self):
        from pathlib import Path

        from stats_updater import CLAUDE_DIR
        self.assertEqual(str(CLAUDE_DIR), str(Path(EXPECTED_PROJECT_ROOT)))

    def test_tools_dir_is_project_relative(self):
        from pathlib import Path

        from stats_updater import TOOLS_DIR
        expected = Path(EXPECTED_PROJECT_ROOT) / "tools"
        self.assertEqual(str(TOOLS_DIR), str(expected))


class TestSetupQualityCronPaths(unittest.TestCase):
    """setup_quality_cron.py path constants."""

    def test_claude_dir_is_project_root(self):
        from setup_quality_cron import CLAUDE_DIR
        self.assertEqual(CLAUDE_DIR, EXPECTED_PROJECT_ROOT)

    def test_tools_dir_is_project_relative(self):
        from setup_quality_cron import TOOLS_DIR
        expected = os.path.join(EXPECTED_PROJECT_ROOT, "tools")
        self.assertEqual(TOOLS_DIR, expected)


class TestFileIoPaths(unittest.TestCase):
    """file_io.py _resolve_memory_dir fallback."""

    def test_resolve_memory_dir_fallback_is_project_relative(self):
        """When MEMORY_DIR env is not set, fallback should use project root."""
        import file_io
        # Save and clear env
        old_val = os.environ.pop("MEMORY_DIR", None)
        try:
            # Re-call the function
            result = file_io.resolve_memory_dir()
            expected = os.path.join(EXPECTED_PROJECT_ROOT, "memory")
            self.assertEqual(result, expected)
        finally:
            if old_val is not None:
                os.environ["MEMORY_DIR"] = old_val


class TestSelfObservationMcpServerPaths(unittest.TestCase):
    """self_observation_mcp_server.py path constants."""

    def test_project_root_constant_exists(self):
        from self_observation_mcp_server import _PROJECT_ROOT
        self.assertEqual(_PROJECT_ROOT, EXPECTED_PROJECT_ROOT)


# ---------------------------------------------------------------------------
# Phase 3: env reflection tests for 9 horizontally-expanded modules.
#
# Each test sets CLAUDE_PROJECT_ROOT to a tmp dir, reloads the target module,
# and asserts that the module's _PROJECT_ROOT (and a derived path constant)
# reflects the env value. importlib.reload is required because each module
# resolves _PROJECT_ROOT at module-top-level import time.
#
# IMPORTANT: do NOT run these with `pytest -n` (xdist parallel mode) — the
# reload + monkeypatch combination is not safe under parallel workers.
# ---------------------------------------------------------------------------


def _reload(module_name):
    """Import (or reload) and return a tools/ module."""
    import importlib
    if module_name in sys.modules:
        return importlib.reload(sys.modules[module_name])
    return importlib.import_module(module_name)


def test_cron_scheduler_env_reflected(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_ROOT", str(tmp_path))
    mod = _reload("cron_scheduler")
    assert mod._PROJECT_ROOT == os.path.normpath(str(tmp_path))
    assert mod.CRON_DIR == os.path.join(os.path.normpath(str(tmp_path)), "cron")


def test_discord_mcp_server_env_reflected(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_ROOT", str(tmp_path))
    mod = _reload("discord_mcp_server")
    assert mod._PROJECT_ROOT == os.path.normpath(str(tmp_path))
    assert mod.DISCORD_DATA_DIR == os.path.join(
        os.path.normpath(str(tmp_path)), "discord_data"
    )


def test_discord_receiver_models_env_reflected(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_ROOT", str(tmp_path))
    mod = _reload("discord_receiver_models")
    assert mod._PROJECT_ROOT == os.path.normpath(str(tmp_path))
    assert mod.DISCORD_DATA_DIR == os.path.join(
        os.path.normpath(str(tmp_path)), "discord_data"
    )


def test_github_notifier_env_reflected(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_ROOT", str(tmp_path))
    mod = _reload("github_notifier")
    assert mod._PROJECT_ROOT == os.path.normpath(str(tmp_path))
    assert mod.DISCORD_DATA_DIR == os.path.join(
        os.path.normpath(str(tmp_path)), "discord_data"
    )


def test_message_event_hooks_env_reflected(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_ROOT", str(tmp_path))
    mod = _reload("message_event_hooks")
    assert mod._PROJECT_ROOT == os.path.normpath(str(tmp_path))
    assert mod.DISCORD_DATA_DIR == os.path.join(
        os.path.normpath(str(tmp_path)), "discord_data"
    )


def test_message_log_handler_env_reflected(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_ROOT", str(tmp_path))
    mod = _reload("message_log_handler")
    assert mod._PROJECT_ROOT == os.path.normpath(str(tmp_path))
    assert mod.DISCORD_DATA_DIR == os.path.join(
        os.path.normpath(str(tmp_path)), "discord_data"
    )


def test_message_memory_handler_env_reflected(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("MEMORY_DIR", raising=False)
    mod = _reload("message_memory_handler")
    assert mod._PROJECT_ROOT == os.path.normpath(str(tmp_path))
    assert mod.MEMORY_DIR == os.path.join(os.path.normpath(str(tmp_path)), "memory")


def test_self_observation_mcp_server_env_reflected(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_ROOT", str(tmp_path))
    mod = _reload("self_observation_mcp_server")
    assert mod._PROJECT_ROOT == os.path.normpath(str(tmp_path))


def test_message_skill_trigger_handler_env_reflected(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_ROOT", str(tmp_path))
    mod = _reload("message_skill_trigger_handler")
    assert mod._PROJECT_ROOT == os.path.normpath(str(tmp_path))


def test_only_one_env_var_lookup_in_codebase():
    """N3 structural guard: CLAUDE_PROJECT_ROOT lookup exists in exactly 1 file.

    The single source of truth is file_io.resolve_project_root(); any other
    reference to os.environ.get("CLAUDE_PROJECT_ROOT" indicates duplicate
    implementation drift (violates design doc N3 / setting C-3).
    """
    import re
    pattern = re.compile(r'os\.environ\.get\(\s*["\']CLAUDE_PROJECT_ROOT["\']')
    # The test file itself references the literal in this assertion; exclude it.
    self_name = os.path.basename(__file__)
    hits = []
    for entry in os.listdir(TOOLS_DIR):
        if not entry.endswith(".py"):
            continue
        if entry == self_name:
            continue
        full = os.path.join(TOOLS_DIR, entry)
        if not os.path.isfile(full):
            continue
        try:
            with open(full, "r", encoding="utf-8") as fh:
                content = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        if pattern.search(content):
            hits.append(entry)
    assert hits == ["file_io.py"], (
        f"Expected exactly 1 hit in file_io.py, got {hits}. "
        "Duplicate CLAUDE_PROJECT_ROOT lookup violates N3 (no duplicate impl)."
    )


if __name__ == "__main__":
    unittest.main()
