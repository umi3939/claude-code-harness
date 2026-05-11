#!/usr/bin/env python3
"""Tests for ECC-inspired improvements: ruff hook, pattern extractor, profiles, health check.

TDD: tests written before implementation.
"""

import json
import os
import sys
import tempfile
import unittest

HOME = os.path.expanduser("~")
HOOKS_DIR = os.path.join(HOME, ".claude", "hooks")
TOOLS_DIR = os.path.join(HOME, ".claude", "tools")
DATA_DIR = os.path.join(HOME, ".claude", "data")


# ═══════════════════════════════════════════════════════════════
# 1A: ruff-quality-gate.sh
# ═══════════════════════════════════════════════════════════════


class TestRuffQualityGate(unittest.TestCase):
    """1A: PostToolUse ruff hook should exist and be executable."""

    def test_hook_script_exists(self):
        """ruff-quality-gate.sh should exist in hooks/."""
        path = os.path.join(HOOKS_DIR, "ruff-quality-gate.sh")
        self.assertTrue(os.path.exists(path), f"Missing: {path}")

    def test_hook_contains_ruff_commands(self):
        """Hook should call ruff format and ruff check."""
        path = os.path.join(HOOKS_DIR, "ruff-quality-gate.sh")
        if not os.path.exists(path):
            self.skipTest("Hook not created yet")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("ruff format", content)
        self.assertIn("ruff check", content)

    def test_hook_handles_non_python_files(self):
        """Hook should exit 0 for non-.py files."""
        path = os.path.join(HOOKS_DIR, "ruff-quality-gate.sh")
        if not os.path.exists(path):
            self.skipTest("Hook not created yet")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn(".py", content, "Hook should check for .py extension")


# ═══════════════════════════════════════════════════════════════
# 1B: Linter config edit block rule
# ═══════════════════════════════════════════════════════════════


class TestLinterConfigBlock(unittest.TestCase):
    """1B: behavior-rules.json should block ruff config edits."""

    def test_rule_exists_in_behavior_rules(self):
        """A rule blocking ruff config edits should exist."""
        rules_path = os.path.join(HOOKS_DIR, "behavior-rules.json")
        with open(rules_path, encoding="utf-8") as f:
            data = json.load(f)
        rule_ids = [r["id"] for r in data["rules"]]
        self.assertTrue(
            any("ruff" in rid or "linter" in rid for rid in rule_ids),
            f"No ruff/linter config block rule found. Rules: {rule_ids}",
        )


# ═══════════════════════════════════════════════════════════════
# 1C: ruff configuration
# ═══════════════════════════════════════════════════════════════


class TestRuffConfig(unittest.TestCase):
    """1C: ruff.toml should exist with correct settings."""

    def test_ruff_config_exists(self):
        """ruff.toml should exist in .claude/."""
        path = os.path.join(HOME, ".claude", "ruff.toml")
        self.assertTrue(os.path.exists(path), f"Missing: {path}")

    def test_ruff_config_has_key_settings(self):
        """Config should have line-length, target-version, and select rules."""
        path = os.path.join(HOME, ".claude", "ruff.toml")
        if not os.path.exists(path):
            self.skipTest("Config not created yet")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("line-length", content)
        self.assertIn("py310", content)
        self.assertIn("select", content)


# ═══════════════════════════════════════════════════════════════
# 2: Pattern extractor
# ═══════════════════════════════════════════════════════════════


class TestPatternExtractor(unittest.TestCase):
    """2: pattern_extractor.py should extract review patterns."""

    def test_module_exists(self):
        """pattern_extractor.py should exist in hooks/."""
        path = os.path.join(HOOKS_DIR, "pattern_extractor.py")
        self.assertTrue(os.path.exists(path), f"Missing: {path}")

    def test_extract_patterns_from_review_text(self):
        """Should extract HIGH/MED/CRITICAL patterns from review output."""
        sys.path.insert(0, HOOKS_DIR)
        try:
            from pattern_extractor import extract_patterns

            text = (
                "## Review Results\n"
                "### MED#1: Missing error handling in parser.py\n"
                "Null check missing on line 42.\n"
                "### HIGH#1: SQL injection in query builder\n"
                "User input passed directly to query.\n"
                "### MED#2: Unused import\n"
                "import os not used.\n"
            )
            patterns = extract_patterns(text)
            self.assertIsInstance(patterns, list)
            self.assertGreaterEqual(len(patterns), 2)
            severities = [p.get("severity", "") for p in patterns]
            self.assertIn("MED", severities)
            self.assertIn("HIGH", severities)
        finally:
            if HOOKS_DIR in sys.path:
                sys.path.remove(HOOKS_DIR)

    def test_accumulate_to_jsonl(self):
        """Should write patterns to a JSONL file."""
        sys.path.insert(0, HOOKS_DIR)
        try:
            from pattern_extractor import accumulate_patterns

            tmpdir = tempfile.mkdtemp()
            jsonl_path = os.path.join(tmpdir, "review_patterns.jsonl")
            patterns = [
                {"severity": "MED", "category": "error_handling", "description": "Missing null check"},
            ]
            accumulate_patterns(patterns, jsonl_path)
            self.assertTrue(os.path.exists(jsonl_path))
            with open(jsonl_path) as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 1)
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)
        finally:
            if HOOKS_DIR in sys.path:
                sys.path.remove(HOOKS_DIR)


# ═══════════════════════════════════════════════════════════════
# 3: Hook profiles
# ═══════════════════════════════════════════════════════════════


class TestHookProfiles(unittest.TestCase):
    """3: behavior-guard.js should support HOOK_PROFILE env variable."""

    def test_behavior_guard_reads_hook_profile(self):
        """behavior-guard.js should check HOOK_PROFILE environment variable."""
        path = os.path.join(HOOKS_DIR, "behavior-guard.js")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("HOOK_PROFILE", content)

    def test_profile_levels_defined(self):
        """All three profile levels should be referenced."""
        path = os.path.join(HOOKS_DIR, "behavior-guard.js")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("minimal", content)
        self.assertIn("standard", content)


# ═══════════════════════════════════════════════════════════════
# 4: MCP Health Check
# ═══════════════════════════════════════════════════════════════


class TestMCPHealthCheck(unittest.TestCase):
    """4: mcp-health-check.js should exist and track MCP response times."""

    def test_hook_script_exists(self):
        """mcp-health-check.js should exist in hooks/."""
        path = os.path.join(HOOKS_DIR, "mcp-health-check.js")
        self.assertTrue(os.path.exists(path), f"Missing: {path}")

    def test_hook_checks_last_response(self):
        """Hook should reference mcp_last_response.json."""
        path = os.path.join(HOOKS_DIR, "mcp-health-check.js")
        if not os.path.exists(path):
            self.skipTest("Hook not created yet")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("mcp_last_response", content)

    def test_hook_warns_not_blocks(self):
        """Hook should warn (exit 0) not block (exit 2)."""
        path = os.path.join(HOOKS_DIR, "mcp-health-check.js")
        if not os.path.exists(path):
            self.skipTest("Hook not created yet")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("exit(0)", content)


if __name__ == "__main__":
    unittest.main()
