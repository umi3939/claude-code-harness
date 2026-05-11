#!/usr/bin/env python3
"""Tests for setup_quality_cron.py.

Supports both direct execution and pytest.
"""

import importlib.util
import os

import pytest

SCRIPT_PATH = os.path.join(
    os.path.expanduser("~"), ".claude", "tools", "setup_quality_cron.py"
)


def _load_mod():
    spec = importlib.util.spec_from_file_location("setup_quality_cron", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_mod()


def test_build_config_returns_dict():
    config = mod.build_cron_job_config()
    assert isinstance(config, dict)


def test_config_has_required_fields():
    config = mod.build_cron_job_config()
    for key in ["name", "command", "schedule"]:
        assert key in config


def test_command_includes_ruff():
    config = mod.build_cron_job_config()
    assert "ruff check" in config.get("command", "")


def test_command_targets_dirs():
    config = mod.build_cron_job_config()
    cmd = config.get("command", "")
    assert "tools/" in cmd
    assert "hooks/" in cmd


def test_weekly_schedule():
    config = mod.build_cron_job_config()
    schedule = config.get("schedule", "")
    assert "week" in schedule.lower() or schedule.startswith("0 0 * * 0")


def test_descriptive_name():
    config = mod.build_cron_job_config()
    assert "quality" in config.get("name", "").lower()


def test_ruff_command():
    ruff_cmd = mod.get_ruff_command()
    assert isinstance(ruff_cmd, str)
    assert "ruff" in ruff_cmd


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
