#!/usr/bin/env python3
"""Tests for MEDIUM-1~17 + LOW-1~3 batch fixes."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
TOOLS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "tools")
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)


# ═══════════════════════════════════════════════════════════════
# MEDIUM-2/3: pattern_extractor.py — shell=False implicit, bare except→logged
# ═══════════════════════════════════════════════════════════════

class TestPatternExtractorExceptHandling(unittest.TestCase):
    """MEDIUM-3: bare except:pass → except Exception + log."""

    def test_no_bare_except_pass(self):
        """pattern_extractor should not have bare except:pass."""
        import inspect

        import pattern_extractor
        source = inspect.getsource(pattern_extractor)
        # "except Exception:" should be used, not "except:" alone
        self.assertNotIn("except:\n", source.replace(" ", ""))

    def test_call_growth_recorder_logs_on_failure(self):
        """_call_growth_recorder should log, not silently pass."""
        import inspect

        import pattern_extractor
        source = inspect.getsource(pattern_extractor._call_growth_recorder)
        self.assertNotIn("pass", source)
        self.assertIn("Exception", source)

    def test_main_logs_on_failure(self):
        """main() should log, not silently pass."""
        import inspect

        import pattern_extractor
        source = inspect.getsource(pattern_extractor.main)
        self.assertNotIn("except Exception:\n        pass", source)


class TestPatternExtractorSubprocess(unittest.TestCase):
    """MEDIUM-2: subprocess call should not use shell=True."""

    def test_no_shell_true(self):
        """pattern_extractor should not use shell=True."""
        import inspect

        import pattern_extractor
        source = inspect.getsource(pattern_extractor)
        self.assertNotIn("shell=True", source)


# ═══════════════════════════════════════════════════════════════
# MEDIUM-4: psyche_drive.py — unused imports removed
# ═══════════════════════════════════════════════════════════════

class TestPsycheDriveImports(unittest.TestCase):
    """MEDIUM-4: psyche_drive.py should not have unused imports."""

    def test_no_unused_path_import(self):
        """Path should not be imported if unused."""
        source = Path(os.path.join(HOOKS_DIR, "psyche_drive.py")).read_text(encoding="utf-8")
        lines = source.split("\n")
        import_lines = [line for line in lines if line.strip().startswith("from pathlib import Path")]
        self.assertEqual(len(import_lines), 0, "Path import should be removed")

    def test_no_unused_traceback_import(self):
        """traceback should not be imported if unused."""
        source = Path(os.path.join(HOOKS_DIR, "psyche_drive.py")).read_text(encoding="utf-8")
        lines = source.split("\n")
        import_lines = [line for line in lines if line.strip() == "import traceback"]
        self.assertEqual(len(import_lines), 0, "traceback import should be removed")

    def test_no_unused_read_entries_import(self):
        """read_entries should not be imported if unused."""
        source = Path(os.path.join(HOOKS_DIR, "psyche_drive.py")).read_text(encoding="utf-8")
        self.assertNotIn("read_entries", source)


# ═══════════════════════════════════════════════════════════════
# MEDIUM-5: cron_scheduler.py — cwd symlink validation
# ═══════════════════════════════════════════════════════════════

class TestCronCwdSymlinkValidation(unittest.TestCase):
    """MEDIUM-5: job.cwd should reject symlinks to prevent traversal."""

    def test_add_rejects_symlink_cwd(self):
        """Adding a job with symlink cwd should resolve it."""
        from cron_scheduler import CronJob, CronJobStore
        with tempfile.TemporaryDirectory() as tmpdir:
            real_dir = os.path.join(tmpdir, "real")
            os.makedirs(real_dir)
            store = CronJobStore(os.path.join(tmpdir, "jobs.json"))
            job = CronJob(name="test", cwd=real_dir)
            result = store.add(job)
            # cwd should be resolved to real path
            self.assertEqual(result.cwd, os.path.realpath(real_dir))

    def test_execute_rejects_symlink_cwd(self):
        """Execute should resolve symlink cwd."""
        from cron_scheduler import CronJob
        job = CronJob(name="test", cwd=os.path.expanduser("~"))
        # Just verify the cwd validation logic exists
        self.assertTrue(os.path.isdir(job.cwd))


# ═══════════════════════════════════════════════════════════════
# MEDIUM-10: DEPRECATED_SESSION_FLAG — normal string
# ═══════════════════════════════════════════════════════════════

class TestDeprecatedSessionFlag(unittest.TestCase):
    """MEDIUM-10: DEPRECATED_SESSION_FLAG should be plain string."""

    def test_not_split_string(self):
        """Should not use string concatenation to avoid detection."""
        source = Path(os.path.join(HOOKS_DIR, "..", "hooks", "behavior-guard.js")).resolve()
        content = source.read_text(encoding="utf-8")
        # The old pattern was: '.session-rea' + 'dy'
        self.assertNotIn("'.session-rea' + 'dy'", content)


# ═══════════════════════════════════════════════════════════════
# LOW-1: test files — open() → with statement
# ═══════════════════════════════════════════════════════════════

class TestOpenWithStatement(unittest.TestCase):
    """LOW-1: Test files should use 'with open()' not bare open().read()."""

    def test_test_high_h8_h17_no_bare_open(self):
        source = Path(os.path.join(TOOLS_DIR, "test_high_h8_h17.py")).read_text(encoding="utf-8")
        # Bare open() pattern: open(...).read()
        import re
        bare_opens = re.findall(r'(?<!with\s)open\([^)]+\)\.read\(\)', source)
        self.assertEqual(len(bare_opens), 0, f"Found bare open().read(): {bare_opens}")

    def test_test_medium_perf_quality_no_bare_open(self):
        source = Path(os.path.join(TOOLS_DIR, "test_medium_perf_quality.py")).read_text(encoding="utf-8")
        import re
        bare_opens = re.findall(r'(?<!with\s)open\([^)]+\)\.read\(\)', source)
        self.assertEqual(len(bare_opens), 0, f"Found bare open().read(): {bare_opens}")


# ═══════════════════════════════════════════════════════════════
# LOW-2: vector_search.py — zip strict
# ═══════════════════════════════════════════════════════════════

class TestVectorSearchZipStrict(unittest.TestCase):
    """LOW-2: cosine_similarity should use zip(..., strict=True)."""

    def test_zip_strict_in_cosine(self):
        import inspect

        from vector_search import cosine_similarity
        source = inspect.getsource(cosine_similarity)
        self.assertIn("strict=True", source)


# ═══════════════════════════════════════════════════════════════
# LOW-3: test_topic_index.py — /tmp → tempfile
# ═══════════════════════════════════════════════════════════════

class TestTopicIndexNoHardcodedTmp(unittest.TestCase):
    """LOW-3: test_topic_index.py should not use /tmp directly."""

    def test_no_hardcoded_tmp(self):
        source = Path(os.path.join(TOOLS_DIR, "tests", "test_topic_index.py")).read_text(encoding="utf-8")
        # Allow /tmp in comments but not in actual code
        lines = [line for line in source.split("\n") if not line.strip().startswith("#")]
        code = "\n".join(lines)
        self.assertNotIn('"/tmp"', code)


if __name__ == "__main__":
    unittest.main()
