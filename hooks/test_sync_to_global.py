"""Tests for sync_to_global.py - SessionStart hook that syncs project hooks to global."""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import sync_to_global


class TestSyncTargetFiles(unittest.TestCase):
    """Test that SYNC_TARGET_FILES constant contains the correct files."""

    def test_contains_behavior_guard(self):
        self.assertIn("behavior-guard.js", sync_to_global.SYNC_TARGET_FILES)

    def test_contains_behavior_rules(self):
        self.assertIn("behavior-rules.json", sync_to_global.SYNC_TARGET_FILES)

    def test_contains_skill_executor(self):
        self.assertIn("skill_executor.py", sync_to_global.SYNC_TARGET_FILES)

    def test_contains_coherence_alert(self):
        self.assertIn("coherence_alert.py", sync_to_global.SYNC_TARGET_FILES)

    def test_contains_coherence_alert_runner(self):
        self.assertIn("coherence_alert_runner.py", sync_to_global.SYNC_TARGET_FILES)

    def test_no_extra_files(self):
        self.assertEqual(len(sync_to_global.SYNC_TARGET_FILES), 5)


class TestSyncToGlobal(unittest.TestCase):
    """Test sync_hooks_to_global function."""

    def setUp(self):
        self.project_hooks_dir = tempfile.mkdtemp(prefix="proj_hooks_")
        self.global_hooks_dir = tempfile.mkdtemp(prefix="global_hooks_")

    def tearDown(self):
        shutil.rmtree(self.project_hooks_dir, ignore_errors=True)
        shutil.rmtree(self.global_hooks_dir, ignore_errors=True)

    def test_copies_existing_file(self):
        src = os.path.join(self.project_hooks_dir, "behavior-guard.js")
        with open(src, "w", encoding="utf-8") as f:
            f.write("// project version")
        result = sync_to_global.sync_hooks_to_global(
            self.project_hooks_dir, self.global_hooks_dir
        )
        dst = os.path.join(self.global_hooks_dir, "behavior-guard.js")
        self.assertTrue(os.path.exists(dst))
        with open(dst, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "// project version")
        self.assertIn("behavior-guard.js", result["copied"])

    def test_skips_missing_file(self):
        result = sync_to_global.sync_hooks_to_global(
            self.project_hooks_dir, self.global_hooks_dir
        )
        self.assertEqual(len(result["copied"]), 0)
        self.assertEqual(len(result["skipped"]), 5)
        self.assertEqual(len(result["errors"]), 0)

    def test_overwrites_older_global_file(self):
        src = os.path.join(self.project_hooks_dir, "behavior-rules.json")
        with open(src, "w", encoding="utf-8") as f:
            f.write('{"version": "new"}')
        dst = os.path.join(self.global_hooks_dir, "behavior-rules.json")
        with open(dst, "w", encoding="utf-8") as f:
            f.write('{"version": "old"}')
        sync_to_global.sync_hooks_to_global(
            self.project_hooks_dir, self.global_hooks_dir
        )
        with open(dst, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), '{"version": "new"}')

    def test_creates_global_dir_if_missing(self):
        new_global = os.path.join(self.global_hooks_dir, "subdir", "hooks")
        src = os.path.join(self.project_hooks_dir, "behavior-guard.js")
        with open(src, "w", encoding="utf-8") as f:
            f.write("// test")
        result = sync_to_global.sync_hooks_to_global(
            self.project_hooks_dir, new_global
        )
        self.assertTrue(os.path.isdir(new_global))
        self.assertIn("behavior-guard.js", result["copied"])

    def test_copies_multiple_files(self):
        for fname in ["behavior-guard.js", "behavior-rules.json", "skill_executor.py"]:
            with open(os.path.join(self.project_hooks_dir, fname), "w", encoding="utf-8") as f:
                f.write(f"content of {fname}")
        result = sync_to_global.sync_hooks_to_global(
            self.project_hooks_dir, self.global_hooks_dir
        )
        self.assertEqual(len(result["copied"]), 3)
        self.assertEqual(len(result["skipped"]), 2)

    def test_handles_permission_error(self):
        src = os.path.join(self.project_hooks_dir, "behavior-guard.js")
        with open(src, "w", encoding="utf-8") as f:
            f.write("// test")
        with patch("sync_to_global.shutil.copy2", side_effect=PermissionError("access denied")):
            result = sync_to_global.sync_hooks_to_global(
                self.project_hooks_dir, self.global_hooks_dir
            )
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("behavior-guard.js", result["errors"][0])

    def test_handles_os_error(self):
        src = os.path.join(self.project_hooks_dir, "skill_executor.py")
        with open(src, "w", encoding="utf-8") as f:
            f.write("# test")
        with patch("sync_to_global.shutil.copy2", side_effect=OSError("disk full")):
            result = sync_to_global.sync_hooks_to_global(
                self.project_hooks_dir, self.global_hooks_dir
            )
        self.assertEqual(len(result["errors"]), 1)

    def test_project_dir_not_exists(self):
        result = sync_to_global.sync_hooks_to_global(
            "/nonexistent/path", self.global_hooks_dir
        )
        self.assertEqual(len(result["copied"]), 0)
        self.assertEqual(len(result["skipped"]), 5)

    def test_copy_preserves_content(self):
        src = os.path.join(self.project_hooks_dir, "behavior-guard.js")
        with open(src, "w", encoding="utf-8") as f:
            f.write("// content")
        sync_to_global.sync_hooks_to_global(
            self.project_hooks_dir, self.global_hooks_dir
        )
        dst = os.path.join(self.global_hooks_dir, "behavior-guard.js")
        with open(dst, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "// content")


class TestMainFunction(unittest.TestCase):
    def test_main_returns_zero_on_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            proj = os.path.join(tmpdir, "proj")
            glob = os.path.join(tmpdir, "glob")
            os.makedirs(proj)
            os.makedirs(glob)
            result = sync_to_global.main(project_hooks_dir=proj, global_hooks_dir=glob)
            self.assertEqual(result, 0)

    def test_main_returns_zero_even_with_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            proj = os.path.join(tmpdir, "proj")
            glob = os.path.join(tmpdir, "glob")
            os.makedirs(proj)
            os.makedirs(glob)
            with open(os.path.join(proj, "behavior-guard.js"), "w") as f:
                f.write("test")
            with patch("sync_to_global.shutil.copy2", side_effect=PermissionError("nope")):
                result = sync_to_global.main(project_hooks_dir=proj, global_hooks_dir=glob)
            self.assertEqual(result, 0)


class TestEdgeCases(unittest.TestCase):
    def setUp(self):
        self.project_hooks_dir = tempfile.mkdtemp(prefix="proj_hooks_")
        self.global_hooks_dir = tempfile.mkdtemp(prefix="global_hooks_")

    def tearDown(self):
        shutil.rmtree(self.project_hooks_dir, ignore_errors=True)
        shutil.rmtree(self.global_hooks_dir, ignore_errors=True)

    def test_empty_file_is_copied(self):
        src = os.path.join(self.project_hooks_dir, "behavior-guard.js")
        with open(src, "w", encoding="utf-8") as f:
            pass
        result = sync_to_global.sync_hooks_to_global(
            self.project_hooks_dir, self.global_hooks_dir
        )
        dst = os.path.join(self.global_hooks_dir, "behavior-guard.js")
        self.assertTrue(os.path.exists(dst))
        self.assertIn("behavior-guard.js", result["copied"])

    def test_large_file_is_copied(self):
        src = os.path.join(self.project_hooks_dir, "behavior-rules.json")
        content = "x" * 1_000_000
        with open(src, "w", encoding="utf-8") as f:
            f.write(content)
        sync_to_global.sync_hooks_to_global(
            self.project_hooks_dir, self.global_hooks_dir
        )
        dst = os.path.join(self.global_hooks_dir, "behavior-rules.json")
        with open(dst, "r", encoding="utf-8") as f:
            self.assertEqual(len(f.read()), 1_000_000)

    def test_identical_files_still_copied(self):
        content = "// same content"
        for d in [self.project_hooks_dir, self.global_hooks_dir]:
            with open(os.path.join(d, "behavior-guard.js"), "w", encoding="utf-8") as f:
                f.write(content)
        result = sync_to_global.sync_hooks_to_global(
            self.project_hooks_dir, self.global_hooks_dir
        )
        self.assertIn("behavior-guard.js", result["copied"])


if __name__ == "__main__":
    unittest.main()
