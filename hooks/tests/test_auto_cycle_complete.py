#!/usr/bin/env python3
"""Tests for auto_cycle_complete.py — GROWTH_DIR path resolution."""

import os
import sys

import pytest

# Add hooks dir to path
HOOKS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)

TOOLS_DIR = os.path.join(os.path.dirname(HOOKS_DIR), "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import auto_cycle_complete


class TestGetGrowthDir:
    """Tests for get_growth_dir() path resolution."""

    def test_default_returns_project_root_growth(self):
        """Default GROWTH_DIR should be project_root/growth/, not ~/.claude/growth/."""
        # Temporarily clear GROWTH_DIR if set
        old = os.environ.pop("GROWTH_DIR", None)
        try:
            result = auto_cycle_complete.get_growth_dir()
            # project_root is two levels up from auto_cycle_complete.py (hooks/ -> project_root/)
            project_root = os.path.dirname(
                os.path.dirname(os.path.abspath(auto_cycle_complete.__file__))
            )
            expected = os.path.join(project_root, "growth")
            assert result == expected
            # Must NOT contain .claude in the path
            assert ".claude" not in result.replace("\\", "/").split("growth")[0]
        finally:
            if old is not None:
                os.environ["GROWTH_DIR"] = old

    def test_env_override(self, tmp_path):
        """GROWTH_DIR env var should override default."""
        custom = str(tmp_path / "custom_growth")
        old = os.environ.get("GROWTH_DIR")
        os.environ["GROWTH_DIR"] = custom
        try:
            result = auto_cycle_complete.get_growth_dir()
            assert result == custom
        finally:
            if old is not None:
                os.environ["GROWTH_DIR"] = old
            else:
                os.environ.pop("GROWTH_DIR", None)
