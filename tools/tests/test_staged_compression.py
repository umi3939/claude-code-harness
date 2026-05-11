#!/usr/bin/env python3
"""Tests for staged_compression.py.

Covers all test cases from the design document's Testing Expectations section.
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

# Ensure the tools directory is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import staged_compression

# --- Test helpers ---

def _make_episode(
    episode_id="ep_001",
    episode_type="observation",
    summary="Test episode summary for testing purposes",
    user_texts=None,
    tags=None,
    timestamp=None,
    session_id="session_20260101_000000",
    compression_stage=None,
):
    """Create a test episode dict."""
    if timestamp is None:
        timestamp = "2026-01-01T00:00:00Z"
    ep = {
        "episode_id": episode_id,
        "episode_type": episode_type,
        "summary": summary,
        "user_utterances": [],
        "tags": tags or [],
        "timestamp": timestamp,
        "session_id": session_id,
    }
    if user_texts:
        for text in user_texts:
            text_bytes = text.encode("utf-8")
            ep["user_utterances"].append({
                "text": text,
                "role": "user",
                "truncated": len(text_bytes) > 2000,
            })
    if compression_stage is not None:
        ep["compression_stage"] = compression_stage
    return ep


def _make_session(session_id, episodes, created_at=None):
    """Create a test session dict."""
    if created_at is None:
        created_at = "2026-01-01T00:00:00Z"
    return {
        "session_id": session_id,
        "created_at": created_at,
        "episodes": episodes,
    }


def _write_session(episodes_dir, session_id, session_data):
    """Write a session file to the episodes directory."""
    filepath = episodes_dir / f"{session_id}.json"
    filepath.write_text(json.dumps(session_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return filepath


class StagedCompressionTestBase(unittest.TestCase):
    """Base class that provides a temporary directory."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.memory_dir = self.temp_dir
        self.episodes_dir = Path(self.memory_dir) / "episodes"
        self.episodes_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)


# ================================================================
# Test: Compression of a full episode to each target stage
# ================================================================

class TestCompressEpisodeStageTransitions(unittest.TestCase):
    """Test compression from stage 0 to each target (0->1, 0->2, 0->3, 1->2, 1->3, 2->3)."""

    def _make_full_episode(self):
        return _make_episode(
            summary="A" * 300,
            user_texts=["Hello " * 200, "World " * 200, "Third " * 200],
        )

    def test_stage_0_to_1(self):
        ep = self._make_full_episode()
        result = staged_compression.compress_episode(ep, 1)
        self.assertEqual(result["compression_stage"], 1)
        # All 3 utterances retained
        self.assertEqual(len(result["user_utterances"]), 3)
        # Each truncated to CONDENSED_UTTERANCE_CAP bytes
        for utt in result["user_utterances"]:
            self.assertLessEqual(len(utt["text"].encode("utf-8")), staged_compression.CONDENSED_UTTERANCE_CAP)
        # Summary unchanged
        self.assertEqual(result["summary"], "A" * 300)

    def test_stage_0_to_2(self):
        ep = self._make_full_episode()
        result = staged_compression.compress_episode(ep, 2)
        self.assertEqual(result["compression_stage"], 2)
        # Only first utterance
        self.assertEqual(len(result["user_utterances"]), 1)
        self.assertLessEqual(len(result["user_utterances"][0]["text"].encode("utf-8")), staged_compression.SUMMARY_UTTERANCE_CAP)
        # Summary truncated
        self.assertLessEqual(len(result["summary"]), staged_compression.SUMMARY_TEXT_CAP)

    def test_stage_0_to_3(self):
        ep = self._make_full_episode()
        result = staged_compression.compress_episode(ep, 3)
        self.assertEqual(result["compression_stage"], 3)
        # No utterances
        self.assertEqual(len(result["user_utterances"]), 0)
        # Summary truncated to skeleton cap
        self.assertLessEqual(len(result["summary"]), staged_compression.SKELETON_SUMMARY_CAP)

    def test_stage_1_to_2(self):
        ep = self._make_full_episode()
        ep = staged_compression.compress_episode(ep, 1)
        result = staged_compression.compress_episode(ep, 2)
        self.assertEqual(result["compression_stage"], 2)
        self.assertEqual(len(result["user_utterances"]), 1)
        self.assertLessEqual(len(result["summary"]), staged_compression.SUMMARY_TEXT_CAP)

    def test_stage_1_to_3(self):
        ep = self._make_full_episode()
        ep = staged_compression.compress_episode(ep, 1)
        result = staged_compression.compress_episode(ep, 3)
        self.assertEqual(result["compression_stage"], 3)
        self.assertEqual(len(result["user_utterances"]), 0)
        self.assertLessEqual(len(result["summary"]), staged_compression.SKELETON_SUMMARY_CAP)

    def test_stage_2_to_3(self):
        ep = self._make_full_episode()
        ep = staged_compression.compress_episode(ep, 2)
        result = staged_compression.compress_episode(ep, 3)
        self.assertEqual(result["compression_stage"], 3)
        self.assertEqual(len(result["user_utterances"]), 0)
        self.assertLessEqual(len(result["summary"]), staged_compression.SKELETON_SUMMARY_CAP)


# ================================================================
# Test: User utterance truncation at each stage
# ================================================================

class TestUtteranceTruncation(unittest.TestCase):
    """Test size cap enforcement and truncated flag at each stage."""

    def test_stage_1_truncation_flag_set(self):
        """Utterances exceeding CONDENSED_UTTERANCE_CAP get truncated flag."""
        ep = _make_episode(user_texts=["X" * 1000])
        result = staged_compression.compress_episode(ep, 1)
        self.assertTrue(result["user_utterances"][0]["truncated"])
        self.assertLessEqual(
            len(result["user_utterances"][0]["text"].encode("utf-8")),
            staged_compression.CONDENSED_UTTERANCE_CAP,
        )

    def test_stage_1_short_utterance_not_truncated(self):
        """Short utterances preserve truncated=False."""
        ep = _make_episode(user_texts=["short text"])
        result = staged_compression.compress_episode(ep, 1)
        self.assertFalse(result["user_utterances"][0]["truncated"])
        self.assertEqual(result["user_utterances"][0]["text"], "short text")

    def test_stage_2_utterance_cap(self):
        """Single retained utterance truncated to SUMMARY_UTTERANCE_CAP."""
        ep = _make_episode(user_texts=["Y" * 1000])
        result = staged_compression.compress_episode(ep, 2)
        self.assertEqual(len(result["user_utterances"]), 1)
        self.assertLessEqual(
            len(result["user_utterances"][0]["text"].encode("utf-8")),
            staged_compression.SUMMARY_UTTERANCE_CAP,
        )
        self.assertTrue(result["user_utterances"][0]["truncated"])


# ================================================================
# Test: User utterance count reduction at stage 2
# ================================================================

class TestUtteranceCountReduction(unittest.TestCase):
    """At stage 2, only the first utterance is retained."""

    def test_multiple_utterances_reduced_to_first(self):
        ep = _make_episode(user_texts=["first", "second", "third"])
        result = staged_compression.compress_episode(ep, 2)
        self.assertEqual(len(result["user_utterances"]), 1)
        self.assertIn("first", result["user_utterances"][0]["text"])

    def test_no_utterances_stays_empty(self):
        ep = _make_episode(user_texts=None)
        result = staged_compression.compress_episode(ep, 2)
        self.assertEqual(len(result["user_utterances"]), 0)


# ================================================================
# Test: User utterance removal at stage 3
# ================================================================

class TestUtteranceRemovalStage3(unittest.TestCase):
    """At stage 3, all utterances are removed."""

    def test_utterances_removed(self):
        ep = _make_episode(user_texts=["hello", "world"])
        result = staged_compression.compress_episode(ep, 3)
        self.assertEqual(len(result["user_utterances"]), 0)

    def test_already_empty_stays_empty(self):
        ep = _make_episode(user_texts=None)
        result = staged_compression.compress_episode(ep, 3)
        self.assertEqual(len(result["user_utterances"]), 0)


# ================================================================
# Test: Summary truncation at stages 2 and 3
# ================================================================

class TestSummaryTruncation(unittest.TestCase):
    """Test summary text truncation at stages 2 and 3."""

    def test_stage_2_summary_truncated(self):
        long_summary = "S" * 500
        ep = _make_episode(summary=long_summary)
        result = staged_compression.compress_episode(ep, 2)
        self.assertLessEqual(len(result["summary"]), staged_compression.SUMMARY_TEXT_CAP)

    def test_stage_2_short_summary_preserved(self):
        ep = _make_episode(summary="Short summary")
        result = staged_compression.compress_episode(ep, 2)
        self.assertEqual(result["summary"], "Short summary")

    def test_stage_3_summary_truncated(self):
        long_summary = "S" * 500
        ep = _make_episode(summary=long_summary)
        result = staged_compression.compress_episode(ep, 3)
        self.assertLessEqual(len(result["summary"]), staged_compression.SKELETON_SUMMARY_CAP)

    def test_stage_3_short_summary_preserved(self):
        ep = _make_episode(summary="Short")
        result = staged_compression.compress_episode(ep, 3)
        self.assertEqual(result["summary"], "Short")


# ================================================================
# Test: compression_stage field addition and update
# ================================================================

class TestCompressionStageField(unittest.TestCase):
    """Test that compression_stage field is correctly set."""

    def test_stage_field_absent_for_full(self):
        ep = _make_episode()
        # Full episode has no compression_stage
        self.assertNotIn("compression_stage", ep)

    def test_stage_field_set_on_compression(self):
        ep = _make_episode()
        result = staged_compression.compress_episode(ep, 1)
        self.assertEqual(result["compression_stage"], 1)

    def test_stage_field_updated_on_further_compression(self):
        ep = _make_episode()
        result1 = staged_compression.compress_episode(ep, 1)
        self.assertEqual(result1["compression_stage"], 1)
        result2 = staged_compression.compress_episode(result1, 2)
        self.assertEqual(result2["compression_stage"], 2)
        result3 = staged_compression.compress_episode(result2, 3)
        self.assertEqual(result3["compression_stage"], 3)


# ================================================================
# Test: Age-based stage determination
# ================================================================

class TestDetermineTargetStage(unittest.TestCase):
    """Test age-based threshold logic."""

    def setUp(self):
        self.now = datetime(2026, 3, 9, 12, 0, 0, tzinfo=timezone.utc)

    def test_recent_session_returns_0(self):
        ts = self.now - timedelta(days=2)
        self.assertEqual(staged_compression.determine_target_stage(ts, self.now), 0)

    def test_7_day_old_returns_1(self):
        ts = self.now - timedelta(days=8)
        self.assertEqual(staged_compression.determine_target_stage(ts, self.now), 1)

    def test_30_day_old_returns_2(self):
        ts = self.now - timedelta(days=35)
        self.assertEqual(staged_compression.determine_target_stage(ts, self.now), 2)

    def test_90_day_old_returns_3(self):
        ts = self.now - timedelta(days=100)
        self.assertEqual(staged_compression.determine_target_stage(ts, self.now), 3)

    def test_exactly_7_days_returns_1(self):
        ts = self.now - timedelta(days=7)
        self.assertEqual(staged_compression.determine_target_stage(ts, self.now), 1)

    def test_exactly_30_days_returns_2(self):
        ts = self.now - timedelta(days=30)
        self.assertEqual(staged_compression.determine_target_stage(ts, self.now), 2)

    def test_exactly_90_days_returns_3(self):
        ts = self.now - timedelta(days=90)
        self.assertEqual(staged_compression.determine_target_stage(ts, self.now), 3)

    def test_custom_thresholds(self):
        ts = self.now - timedelta(days=5)
        result = staged_compression.determine_target_stage(
            ts, self.now,
            threshold_stage_1_days=3,
            threshold_stage_2_days=10,
            threshold_stage_3_days=20,
        )
        self.assertEqual(result, 1)


# ================================================================
# Test: Protected recent sessions
# ================================================================

class TestProtectedSessions(StagedCompressionTestBase):
    """Protected sessions are not compressed regardless of age."""

    def test_protected_flag_returns_stage_0(self):
        ts = datetime(2025, 1, 1, tzinfo=timezone.utc)
        now = datetime(2026, 3, 9, tzinfo=timezone.utc)
        result = staged_compression.determine_target_stage(ts, now, is_protected=True)
        self.assertEqual(result, 0)

    def test_protected_sessions_not_compressed_in_compress_sessions(self):
        """The N most recent sessions should not be compressed."""
        now = datetime(2026, 3, 9, 12, 0, 0, tzinfo=timezone.utc)
        old_ts = "2025-01-01T00:00:00Z"

        # Create 4 sessions (all old by timestamp), 3 should be protected
        for i in range(4):
            session_id = f"session_2025010{i+1}_000000"
            ep = _make_episode(episode_id=f"ep_{i}", timestamp=old_ts, session_id=session_id)
            session_data = _make_session(session_id, [ep], created_at=old_ts)
            _write_session(self.episodes_dir, session_id, session_data)

        result = staged_compression.compress_sessions(
            self.memory_dir,
            now=now,
            protected_recent_sessions=3,
        )

        # Only the oldest (1 session) should be compressed
        self.assertIn("1 sessions compressed", result)


# ================================================================
# Test: Idempotency
# ================================================================

class TestIdempotency(unittest.TestCase):
    """Compressing already-compressed episodes is a no-op."""

    def test_same_stage_is_noop(self):
        ep = _make_episode(user_texts=["hello"])
        compressed = staged_compression.compress_episode(ep, 1)
        compressed_again = staged_compression.compress_episode(compressed, 1)
        self.assertEqual(compressed, compressed_again)

    def test_lower_target_is_noop(self):
        ep = _make_episode(user_texts=["hello"], compression_stage=2)
        result = staged_compression.compress_episode(ep, 1)
        self.assertEqual(result["compression_stage"], 2)

    def test_stage_3_compressed_again_is_noop(self):
        ep = _make_episode(user_texts=["hello"])
        compressed = staged_compression.compress_episode(ep, 3)
        compressed_again = staged_compression.compress_episode(compressed, 3)
        self.assertEqual(compressed, compressed_again)


# ================================================================
# Test: Corrupted session file handling
# ================================================================

class TestCorruptedSessionFile(StagedCompressionTestBase):
    """Corrupted session files are skipped, not deleted."""

    def test_corrupted_file_skipped(self):
        # Write a valid session
        ep = _make_episode(timestamp="2025-01-01T00:00:00Z")
        session_data = _make_session("session_20250101_000000", [ep])
        _write_session(self.episodes_dir, "session_20250101_000000", session_data)

        # Write corrupted file
        corrupted_path = self.episodes_dir / "session_20250102_000000.json"
        corrupted_path.write_text("NOT VALID JSON {{{", encoding="utf-8")

        now = datetime(2026, 3, 9, tzinfo=timezone.utc)
        result = staged_compression.compress_sessions(self.memory_dir, now=now, protected_recent_sessions=0)

        # Corrupted file still exists
        self.assertTrue(corrupted_path.exists())
        self.assertIn("Warning", result)

    def test_corrupted_file_not_deleted(self):
        corrupted_path = self.episodes_dir / "session_20250103_000000.json"
        corrupted_path.write_text("{}", encoding="utf-8")  # Missing required fields

        now = datetime(2026, 3, 9, tzinfo=timezone.utc)
        staged_compression.compress_sessions(self.memory_dir, now=now, protected_recent_sessions=0)
        self.assertTrue(corrupted_path.exists())


# ================================================================
# Test: Missing episodes directory
# ================================================================

class TestMissingEpisodesDir(unittest.TestCase):
    """Missing episodes directory returns 'No episodes found' without error."""

    def test_compress_no_episodes_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = staged_compression.compress_sessions(tmpdir)
            self.assertEqual(result, "No episodes found.")

    def test_status_no_episodes_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = staged_compression.get_compression_status(tmpdir)
            self.assertEqual(result, "No episodes found.")

    def test_dry_run_no_episodes_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = staged_compression.dry_run(tmpdir)
            self.assertEqual(result, "No episodes found.")


# ================================================================
# Test: Missing compression state file
# ================================================================

class TestMissingStateFile(StagedCompressionTestBase):
    """Missing state file is recreated from scratch."""

    def test_state_file_created_on_first_run(self):
        ep = _make_episode(timestamp="2025-01-01T00:00:00Z")
        session_data = _make_session("session_20250101_000000", [ep])
        _write_session(self.episodes_dir, "session_20250101_000000", session_data)

        state_path = staged_compression._get_state_file_path(self.memory_dir)
        self.assertFalse(state_path.exists())

        now = datetime(2026, 3, 9, tzinfo=timezone.utc)
        staged_compression.compress_sessions(self.memory_dir, now=now, protected_recent_sessions=0)

        self.assertTrue(state_path.exists())
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertIn("last_run", state)
        self.assertIn("sessions", state)

    def test_corrupted_state_file_recovered(self):
        ep = _make_episode(timestamp="2025-01-01T00:00:00Z")
        session_data = _make_session("session_20250101_000000", [ep])
        _write_session(self.episodes_dir, "session_20250101_000000", session_data)

        # Write corrupted state file
        state_path = staged_compression._get_state_file_path(self.memory_dir)
        state_path.write_text("BROKEN", encoding="utf-8")

        now = datetime(2026, 3, 9, tzinfo=timezone.utc)
        # Should not raise
        result = staged_compression.compress_sessions(self.memory_dir, now=now, protected_recent_sessions=0)
        self.assertIn("Compression complete", result)


# ================================================================
# Test: Atomic write behavior
# ================================================================

class TestAtomicWrite(StagedCompressionTestBase):
    """Test atomic write (temp file cleanup on failure)."""

    def test_atomic_write_success(self):
        filepath = Path(self.temp_dir) / "test_atomic.json"
        staged_compression._write_file_atomic(filepath, {"key": "value"})
        data = json.loads(filepath.read_text(encoding="utf-8"))
        self.assertEqual(data["key"], "value")

    def test_atomic_write_no_partial_on_error(self):
        """On write failure, original file should remain unchanged."""
        filepath = Path(self.temp_dir) / "test_atomic2.json"
        filepath.write_text('{"original": true}', encoding="utf-8")

        # Simulate write failure by patching os.replace to raise
        with patch("staged_compression.os.replace", side_effect=OSError("simulated")):
            with self.assertRaises(OSError):
                staged_compression._write_file_atomic(filepath, {"new": True})

        # Original file should be intact
        data = json.loads(filepath.read_text(encoding="utf-8"))
        self.assertTrue(data["original"])

    def test_temp_file_cleaned_on_failure(self):
        """Temp file should be removed on write failure."""
        filepath = Path(self.temp_dir) / "test_atomic3.json"
        parent_dir = filepath.parent

        with patch("staged_compression.os.replace", side_effect=OSError("simulated")):
            with self.assertRaises(OSError):
                staged_compression._write_file_atomic(filepath, {"data": True})

        # No leftover temp files
        tmp_files = [f for f in parent_dir.iterdir() if f.suffix == ".tmp"]
        self.assertEqual(len(tmp_files), 0)


# ================================================================
# Test: Maximum total sessions safety cap
# ================================================================

class TestMaxTotalSessions(StagedCompressionTestBase):
    """Exceeding MAX_TOTAL_SESSIONS compresses oldest to stage 3 but does not delete."""

    def test_overflow_compresses_to_skeleton(self):
        now = datetime(2026, 3, 9, tzinfo=timezone.utc)
        old_ts = "2025-06-01T00:00:00Z"

        # Create 5 sessions, set cap to 3
        for i in range(5):
            session_id = f"session_2025060{i+1}_000000"
            ep = _make_episode(episode_id=f"ep_{i}", timestamp=old_ts, session_id=session_id)
            session_data = _make_session(session_id, [ep], created_at=old_ts)
            _write_session(self.episodes_dir, session_id, session_data)

        staged_compression.compress_sessions(
            self.memory_dir,
            now=now,
            max_total_sessions=3,
            protected_recent_sessions=0,
        )

        # All 5 files should still exist (no deletion)
        remaining = list(self.episodes_dir.glob("session_*.json"))
        self.assertEqual(len(remaining), 5)

        # The 2 oldest should be at stage 3
        files = sorted(remaining, key=lambda f: f.stat().st_mtime)
        for f in files[:2]:
            data = json.loads(f.read_text(encoding="utf-8"))
            for ep in data["episodes"]:
                self.assertEqual(ep.get("compression_stage", 0), 3)


# ================================================================
# Test: Dry-run mode
# ================================================================

class TestDryRun(StagedCompressionTestBase):
    """Dry-run mode does not modify files."""

    def test_dry_run_no_file_changes(self):
        now = datetime(2026, 3, 9, tzinfo=timezone.utc)
        old_ts = "2025-01-01T00:00:00Z"

        session_id = "session_20250101_000000"
        ep = _make_episode(timestamp=old_ts, user_texts=["hello world"], session_id=session_id)
        session_data = _make_session(session_id, [ep])
        filepath = _write_session(self.episodes_dir, session_id, session_data)

        original_content = filepath.read_text(encoding="utf-8")

        result = staged_compression.dry_run(self.memory_dir, now=now, protected_recent_sessions=0)

        # File should not have changed
        current_content = filepath.read_text(encoding="utf-8")
        self.assertEqual(original_content, current_content)

        # Report should mention what would happen
        self.assertIn("would change", result.lower())

        # No state file should be created
        state_path = staged_compression._get_state_file_path(self.memory_dir)
        self.assertFalse(state_path.exists())


# ================================================================
# Test: Status output formatting
# ================================================================

class TestStatusOutput(StagedCompressionTestBase):
    """Test get_compression_status output."""

    def test_status_with_mixed_stages(self):
        # Create sessions with different stages
        ep0 = _make_episode(episode_id="ep_0", timestamp="2026-03-09T00:00:00Z")
        ep1 = _make_episode(episode_id="ep_1", compression_stage=1, timestamp="2026-02-01T00:00:00Z")
        ep3 = _make_episode(episode_id="ep_3", compression_stage=3, timestamp="2025-01-01T00:00:00Z")

        s1 = _make_session("session_20260309_000000", [ep0])
        s2 = _make_session("session_20260201_000000", [ep1])
        s3 = _make_session("session_20250101_000000", [ep3])

        _write_session(self.episodes_dir, "session_20260309_000000", s1)
        _write_session(self.episodes_dir, "session_20260201_000000", s2)
        _write_session(self.episodes_dir, "session_20250101_000000", s3)

        result = staged_compression.get_compression_status(self.memory_dir)

        self.assertIn("3 sessions", result)
        self.assertIn("3 episodes", result)
        self.assertIn("Stage 0 (full)", result)
        self.assertIn("Stage 1 (condensed)", result)
        self.assertIn("Stage 3 (skeleton)", result)
        self.assertIn("Total storage", result)

    def test_status_empty_dir(self):
        import shutil
        shutil.rmtree(self.episodes_dir)
        result = staged_compression.get_compression_status(self.memory_dir)
        self.assertEqual(result, "No episodes found.")


# ================================================================
# Test: CLI subcommand parsing
# ================================================================

class TestCLI(StagedCompressionTestBase):
    """Test CLI argument parsing and subcommand dispatch."""

    def test_compress_subcommand(self):
        ep = _make_episode(timestamp="2025-01-01T00:00:00Z")
        session_data = _make_session("session_20250101_000000", [ep])
        _write_session(self.episodes_dir, "session_20250101_000000", session_data)

        with patch("staged_compression.compress_sessions", return_value="done") as mock:
            staged_compression.main(["compress", "--memory-dir", self.memory_dir])
            mock.assert_called_once()

    def test_status_subcommand(self):
        with patch("staged_compression.get_compression_status", return_value="status") as mock:
            staged_compression.main(["status", "--memory-dir", self.memory_dir])
            mock.assert_called_once()

    def test_dry_run_subcommand(self):
        with patch("staged_compression.dry_run", return_value="dry-run result") as mock:
            staged_compression.main(["dry-run", "--memory-dir", self.memory_dir])
            mock.assert_called_once()

    def test_no_subcommand_exits(self):
        with self.assertRaises(SystemExit):
            staged_compression.main([])

    def test_force_stage_option(self):
        ep = _make_episode(timestamp="2025-01-01T00:00:00Z")
        session_data = _make_session("session_20250101_000000", [ep])
        _write_session(self.episodes_dir, "session_20250101_000000", session_data)

        with patch("staged_compression.compress_sessions", return_value="done") as mock:
            staged_compression.main(["compress", "--memory-dir", self.memory_dir, "--force-stage", "2"])
            mock.assert_called_once()
            call_kwargs = mock.call_args
            self.assertEqual(call_kwargs[1].get("force_stage"), 2)


# ================================================================
# Test: Episodes without timestamps
# ================================================================

class TestEpisodesWithoutTimestamps(unittest.TestCase):
    """Episodes without timestamps are treated as maximally old."""

    def test_no_timestamp_returns_stage_3(self):
        result = staged_compression.determine_target_stage(
            None,
            datetime(2026, 3, 9, tzinfo=timezone.utc),
        )
        self.assertEqual(result, 3)

    def test_session_with_no_timestamp_episodes(self):
        """Session where episodes lack timestamps gets compressed to stage 3."""
        ep = _make_episode(timestamp="")
        ts = staged_compression._get_latest_episode_timestamp(
            _make_session("s1", [ep])
        )
        self.assertIsNone(ts)


# ================================================================
# Test: Mixed compression stages within a single session
# ================================================================

class TestMixedStagesInSession(StagedCompressionTestBase):
    """A session can contain episodes at different compression stages."""

    def test_mixed_stages_compressed_independently(self):
        now = datetime(2026, 3, 9, tzinfo=timezone.utc)

        # Episode at stage 0 and episode already at stage 1
        ep0 = _make_episode(
            episode_id="ep_0",
            timestamp="2025-01-01T00:00:00Z",
            user_texts=["X" * 1000],
        )
        ep1 = _make_episode(
            episode_id="ep_1",
            timestamp="2025-01-01T00:00:00Z",
            user_texts=["Y" * 300],
            compression_stage=1,
        )
        # Manually truncate ep1's utterances (simulating prior compression)
        ep1["user_utterances"][0]["text"] = ep1["user_utterances"][0]["text"][:staged_compression.CONDENSED_UTTERANCE_CAP]

        session_data = _make_session("session_20250101_000000", [ep0, ep1])
        _write_session(self.episodes_dir, "session_20250101_000000", session_data)

        staged_compression.compress_sessions(
            self.memory_dir, now=now, protected_recent_sessions=0,
        )

        # Read back
        filepath = self.episodes_dir / "session_20250101_000000.json"
        data = json.loads(filepath.read_text(encoding="utf-8"))

        # Both should now be at stage 3 (old enough)
        for ep in data["episodes"]:
            self.assertEqual(ep["compression_stage"], 3)


# ================================================================
# Test: Force-stage option overriding age thresholds
# ================================================================

class TestForceStage(StagedCompressionTestBase):
    """Force-stage overrides age-based thresholds."""

    def test_force_stage_overrides_age(self):
        now = datetime(2026, 3, 9, tzinfo=timezone.utc)
        # Recent session (normally stage 0)
        recent_ts = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

        session_id = "session_20260308_000000"
        ep = _make_episode(timestamp=recent_ts, user_texts=["hello"], session_id=session_id)
        session_data = _make_session(session_id, [ep])
        _write_session(self.episodes_dir, session_id, session_data)

        # Force to stage 2 (only 1 session, protected_recent_sessions=0)
        staged_compression.compress_sessions(
            self.memory_dir,
            now=now,
            force_stage=2,
            protected_recent_sessions=0,
        )

        filepath = self.episodes_dir / f"{session_id}.json"
        data = json.loads(filepath.read_text(encoding="utf-8"))
        self.assertEqual(data["episodes"][0]["compression_stage"], 2)

    def test_force_stage_does_not_affect_protected(self):
        now = datetime(2026, 3, 9, tzinfo=timezone.utc)
        recent_ts = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

        session_id = "session_20260308_000000"
        ep = _make_episode(timestamp=recent_ts, user_texts=["hello"], session_id=session_id)
        session_data = _make_session(session_id, [ep])
        _write_session(self.episodes_dir, session_id, session_data)

        # Force stage 2, but session is protected (only 1 session, protected=1)
        staged_compression.compress_sessions(
            self.memory_dir,
            now=now,
            force_stage=2,
            protected_recent_sessions=1,
        )

        filepath = self.episodes_dir / f"{session_id}.json"
        data = json.loads(filepath.read_text(encoding="utf-8"))
        # Should remain uncompressed
        self.assertNotIn("compression_stage", data["episodes"][0])


# ================================================================
# Test: UTF-8 content preservation during truncation
# ================================================================

class TestUTF8Preservation(unittest.TestCase):
    """No mid-codepoint truncation during UTF-8 byte truncation."""

    def test_multibyte_character_not_split(self):
        """Truncating UTF-8 text should not produce invalid codepoints."""
        # Japanese characters (3 bytes each in UTF-8)
        text = "日本語テスト" * 100  # each char is 3 bytes
        truncated, was_truncated = staged_compression._truncate_utf8(text, 10)
        self.assertTrue(was_truncated)
        # Should be valid UTF-8
        truncated.encode("utf-8")
        # Should be within byte cap
        self.assertLessEqual(len(truncated.encode("utf-8")), 10)

    def test_emoji_not_split(self):
        """Emoji (4 bytes in UTF-8) should not be split."""
        text = "Hello 🌟 World 🎉 Test 🚀"
        truncated, was_truncated = staged_compression._truncate_utf8(text, 12)
        self.assertTrue(was_truncated)
        truncated.encode("utf-8")  # Should not raise

    def test_ascii_truncation_exact(self):
        text = "abcdefghij"
        truncated, was_truncated = staged_compression._truncate_utf8(text, 5)
        self.assertTrue(was_truncated)
        self.assertEqual(truncated, "abcde")

    def test_no_truncation_needed(self):
        text = "short"
        truncated, was_truncated = staged_compression._truncate_utf8(text, 100)
        self.assertFalse(was_truncated)
        self.assertEqual(truncated, "short")

    def test_episode_compression_preserves_utf8(self):
        """Full pipeline compression with Japanese text."""
        ep = _make_episode(
            summary="要約テキスト" * 50,  # long Japanese summary
            user_texts=["日本語の発言" * 200],  # long Japanese utterance
        )
        result = staged_compression.compress_episode(ep, 3)
        # Should produce valid strings
        result["summary"].encode("utf-8")
        self.assertLessEqual(len(result["summary"]), staged_compression.SKELETON_SUMMARY_CAP)


# ================================================================
# Test: Empty session files
# ================================================================

class TestEmptySessionFile(StagedCompressionTestBase):
    """Session file with no episodes."""

    def test_empty_episodes_list(self):
        session_data = _make_session("session_20250101_000000", [])
        _write_session(self.episodes_dir, "session_20250101_000000", session_data)

        now = datetime(2026, 3, 9, tzinfo=timezone.utc)
        result = staged_compression.compress_sessions(self.memory_dir, now=now, protected_recent_sessions=0)
        # Should complete without error
        self.assertIn("Compression complete", result)


# ================================================================
# Test: Session with only skeleton episodes
# ================================================================

class TestAlreadySkeletonSession(StagedCompressionTestBase):
    """Session where all episodes are already at stage 3."""

    def test_no_further_compression(self):
        ep = _make_episode(
            compression_stage=3,
            timestamp="2025-01-01T00:00:00Z",
            user_texts=None,
            summary="Short",
        )
        ep["user_utterances"] = []
        session_data = _make_session("session_20250101_000000", [ep])
        filepath = _write_session(self.episodes_dir, "session_20250101_000000", session_data)

        original_content = filepath.read_text(encoding="utf-8")

        now = datetime(2026, 3, 9, tzinfo=timezone.utc)
        result = staged_compression.compress_sessions(self.memory_dir, now=now, protected_recent_sessions=0)

        # 0 episodes changed (already at skeleton)
        self.assertIn("0 episodes changed", result)


# ================================================================
# Test: Full end-to-end compression pipeline
# ================================================================

class TestEndToEnd(StagedCompressionTestBase):
    """End-to-end test of the full compression pipeline."""

    def test_full_pipeline(self):
        now = datetime(2026, 3, 9, 12, 0, 0, tzinfo=timezone.utc)

        # Session 1: 100 days old -> stage 3
        s1_ts = (now - timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%SZ")
        s1_eps = [
            _make_episode(episode_id="ep_1a", timestamp=s1_ts, user_texts=["old data"], summary="Old " * 50),
            _make_episode(episode_id="ep_1b", timestamp=s1_ts, user_texts=["more old"], summary="Also old " * 30),
        ]
        s1 = _make_session("session_20251130_000000", s1_eps, created_at=s1_ts)
        _write_session(self.episodes_dir, "session_20251130_000000", s1)

        # Session 2: 40 days old -> stage 2
        s2_ts = (now - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
        s2_eps = [
            _make_episode(episode_id="ep_2a", timestamp=s2_ts, user_texts=["middle data", "more text"], summary="Medium " * 40),
        ]
        s2 = _make_session("session_20260128_000000", s2_eps, created_at=s2_ts)
        _write_session(self.episodes_dir, "session_20260128_000000", s2)

        # Session 3: 10 days old -> stage 1
        s3_ts = (now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        s3_eps = [
            _make_episode(episode_id="ep_3a", timestamp=s3_ts, user_texts=["recent-ish"], summary="Recent " * 10),
        ]
        s3 = _make_session("session_20260227_000000", s3_eps, created_at=s3_ts)
        _write_session(self.episodes_dir, "session_20260227_000000", s3)

        # Session 4: 1 day old -> stage 0 (protected)
        s4_ts = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        s4_eps = [
            _make_episode(episode_id="ep_4a", timestamp=s4_ts, user_texts=["very recent"], summary="New stuff"),
        ]
        s4 = _make_session("session_20260308_000000", s4_eps, created_at=s4_ts)
        _write_session(self.episodes_dir, "session_20260308_000000", s4)

        # Run compression (protect last 1 session)
        result = staged_compression.compress_sessions(
            self.memory_dir,
            now=now,
            protected_recent_sessions=1,
        )

        self.assertIn("Compression complete", result)

        # Verify session 1 -> stage 3
        d1 = json.loads((self.episodes_dir / "session_20251130_000000.json").read_text(encoding="utf-8"))
        for ep in d1["episodes"]:
            self.assertEqual(ep["compression_stage"], 3)
            self.assertEqual(len(ep["user_utterances"]), 0)
            self.assertLessEqual(len(ep["summary"]), staged_compression.SKELETON_SUMMARY_CAP)

        # Verify session 2 -> stage 2
        d2 = json.loads((self.episodes_dir / "session_20260128_000000.json").read_text(encoding="utf-8"))
        for ep in d2["episodes"]:
            self.assertEqual(ep["compression_stage"], 2)
            self.assertLessEqual(len(ep["user_utterances"]), 1)
            self.assertLessEqual(len(ep["summary"]), staged_compression.SUMMARY_TEXT_CAP)

        # Verify session 3 -> stage 1
        d3 = json.loads((self.episodes_dir / "session_20260227_000000.json").read_text(encoding="utf-8"))
        for ep in d3["episodes"]:
            self.assertEqual(ep["compression_stage"], 1)

        # Verify session 4 -> unchanged (protected)
        d4 = json.loads((self.episodes_dir / "session_20260308_000000.json").read_text(encoding="utf-8"))
        for ep in d4["episodes"]:
            self.assertNotIn("compression_stage", ep)

        # Verify state file
        state_path = staged_compression._get_state_file_path(self.memory_dir)
        self.assertTrue(state_path.exists())
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertIn("last_run", state)
        self.assertIn("sessions", state)

        # Verify status command works
        status = staged_compression.get_compression_status(self.memory_dir)
        self.assertIn("4 sessions", status)

    def test_incremental_compression(self):
        """Running compression twice should be idempotent."""
        now = datetime(2026, 3, 9, 12, 0, 0, tzinfo=timezone.utc)
        old_ts = (now - timedelta(days=50)).strftime("%Y-%m-%dT%H:%M:%SZ")

        ep = _make_episode(timestamp=old_ts, user_texts=["data"], summary="Summary " * 30)
        session_data = _make_session("session_20260118_000000", [ep])
        _write_session(self.episodes_dir, "session_20260118_000000", session_data)

        # First run
        result1 = staged_compression.compress_sessions(self.memory_dir, now=now, protected_recent_sessions=0)
        self.assertIn("1 sessions compressed", result1)

        # Second run (idempotent)
        result2 = staged_compression.compress_sessions(self.memory_dir, now=now, protected_recent_sessions=0)
        self.assertIn("0 episodes changed", result2)


# ================================================================
# Test: Preserved fields at all stages
# ================================================================

class TestPreservedFields(unittest.TestCase):
    """episode_id, episode_type, tags, timestamp, session_id are preserved at all stages."""

    def test_fields_preserved_at_stage_1(self):
        ep = _make_episode(
            episode_id="ep_test",
            episode_type="decision",
            tags=["tag1", "tag2"],
            timestamp="2026-01-01T12:00:00Z",
            session_id="session_test",
            user_texts=["hello"],
            summary="A summary",
        )
        result = staged_compression.compress_episode(ep, 1)
        self.assertEqual(result["episode_id"], "ep_test")
        self.assertEqual(result["episode_type"], "decision")
        self.assertEqual(result["tags"], ["tag1", "tag2"])
        self.assertEqual(result["timestamp"], "2026-01-01T12:00:00Z")
        self.assertEqual(result["session_id"], "session_test")

    def test_fields_preserved_at_stage_2(self):
        ep = _make_episode(
            episode_id="ep_test",
            episode_type="error",
            tags=["debug"],
            timestamp="2026-01-01T12:00:00Z",
            session_id="session_test",
        )
        result = staged_compression.compress_episode(ep, 2)
        self.assertEqual(result["episode_id"], "ep_test")
        self.assertEqual(result["episode_type"], "error")
        self.assertEqual(result["tags"], ["debug"])
        self.assertEqual(result["timestamp"], "2026-01-01T12:00:00Z")
        self.assertEqual(result["session_id"], "session_test")

    def test_fields_preserved_at_stage_3(self):
        ep = _make_episode(
            episode_id="ep_test",
            episode_type="feedback",
            tags=["important", "review"],
            timestamp="2026-01-01T12:00:00Z",
            session_id="session_test",
        )
        result = staged_compression.compress_episode(ep, 3)
        self.assertEqual(result["episode_id"], "ep_test")
        self.assertEqual(result["episode_type"], "feedback")
        self.assertEqual(result["tags"], ["important", "review"])
        self.assertEqual(result["timestamp"], "2026-01-01T12:00:00Z")
        self.assertEqual(result["session_id"], "session_test")


# ================================================================
# Test: Timestamp parsing edge cases
# ================================================================

class TestTimestampParsing(unittest.TestCase):
    """Test timestamp parsing for various edge cases."""

    def test_valid_timestamp(self):
        result = staged_compression._parse_timestamp("2026-03-09T12:00:00Z")
        self.assertIsNotNone(result)
        self.assertEqual(result.year, 2026)

    def test_invalid_timestamp(self):
        result = staged_compression._parse_timestamp("not-a-date")
        self.assertIsNone(result)

    def test_empty_timestamp(self):
        result = staged_compression._parse_timestamp("")
        self.assertIsNone(result)

    def test_none_timestamp(self):
        result = staged_compression._parse_timestamp(None)
        self.assertIsNone(result)

    def test_latest_episode_timestamp_picks_most_recent(self):
        eps = [
            _make_episode(episode_id="ep_1", timestamp="2026-01-01T00:00:00Z"),
            _make_episode(episode_id="ep_2", timestamp="2026-03-01T00:00:00Z"),
            _make_episode(episode_id="ep_3", timestamp="2026-02-01T00:00:00Z"),
        ]
        session_data = _make_session("s1", eps)
        result = staged_compression._get_latest_episode_timestamp(session_data)
        self.assertEqual(result.month, 3)

    def test_latest_episode_timestamp_all_invalid(self):
        eps = [
            _make_episode(episode_id="ep_1", timestamp=""),
            _make_episode(episode_id="ep_2", timestamp="bad"),
        ]
        session_data = _make_session("s1", eps)
        result = staged_compression._get_latest_episode_timestamp(session_data)
        self.assertIsNone(result)


# ================================================================
# Test: compress_episode is a pure function
# ================================================================

class TestPureFunction(unittest.TestCase):
    """compress_episode should not mutate the input dict."""

    def test_input_not_mutated(self):
        ep = _make_episode(user_texts=["hello world"], summary="original summary")
        original_summary = ep["summary"]
        original_utterance_count = len(ep["user_utterances"])

        _ = staged_compression.compress_episode(ep, 3)

        # Original should be unchanged
        self.assertEqual(ep["summary"], original_summary)
        self.assertEqual(len(ep["user_utterances"]), original_utterance_count)
        self.assertNotIn("compression_stage", ep)


# ================================================================
# Test: No session files in empty episodes directory
# ================================================================

class TestEmptyEpisodesDir(StagedCompressionTestBase):
    """Episodes directory exists but has no session files."""

    def test_compress_empty_dir(self):
        result = staged_compression.compress_sessions(self.memory_dir)
        self.assertEqual(result, "No episodes found.")

    def test_dry_run_empty_dir(self):
        result = staged_compression.dry_run(self.memory_dir)
        self.assertEqual(result, "No episodes found.")

    def test_status_empty_dir(self):
        result = staged_compression.get_compression_status(self.memory_dir)
        self.assertEqual(result, "No episodes found.")


# ================================================================
# Test: Dry-run report format
# ================================================================

class TestDryRunReport(StagedCompressionTestBase):
    """Test that dry-run produces informative output."""

    def test_dry_run_shows_session_details(self):
        now = datetime(2026, 3, 9, tzinfo=timezone.utc)
        old_ts = "2025-01-01T00:00:00Z"

        ep = _make_episode(timestamp=old_ts, user_texts=["text"])
        session_data = _make_session("session_20250101_000000", [ep])
        _write_session(self.episodes_dir, "session_20250101_000000", session_data)

        result = staged_compression.dry_run(self.memory_dir, now=now, protected_recent_sessions=0)

        self.assertIn("Dry-run compression report", result)
        self.assertIn("session_20250101_000000", result)
        self.assertIn("skeleton", result)

    def test_dry_run_shows_protected(self):
        now = datetime(2026, 3, 9, tzinfo=timezone.utc)
        recent_ts = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

        ep = _make_episode(timestamp=recent_ts)
        session_data = _make_session("session_20260308_000000", [ep])
        _write_session(self.episodes_dir, "session_20260308_000000", session_data)

        result = staged_compression.dry_run(self.memory_dir, now=now, protected_recent_sessions=1)
        self.assertIn("protected", result)


# ================================================================
# Test: Compression state file format
# ================================================================

class TestCompressionStateFormat(StagedCompressionTestBase):
    """Test the compression state file structure."""

    def test_state_file_has_correct_structure(self):
        now = datetime(2026, 3, 9, 12, 0, 0, tzinfo=timezone.utc)
        old_ts = "2025-06-01T00:00:00Z"

        ep = _make_episode(timestamp=old_ts)
        session_data = _make_session("session_20250601_000000", [ep])
        _write_session(self.episodes_dir, "session_20250601_000000", session_data)

        staged_compression.compress_sessions(self.memory_dir, now=now, protected_recent_sessions=0)

        state_path = staged_compression._get_state_file_path(self.memory_dir)
        state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertIn("last_run", state)
        self.assertIn("sessions", state)
        self.assertIn("session_20250601_000000", state["sessions"])
        self.assertIn("max_stage", state["sessions"]["session_20250601_000000"])

    def test_state_file_last_run_timestamp(self):
        now = datetime(2026, 3, 9, 12, 0, 0, tzinfo=timezone.utc)
        old_ts = "2025-06-01T00:00:00Z"

        ep = _make_episode(timestamp=old_ts)
        session_data = _make_session("session_20250601_000000", [ep])
        _write_session(self.episodes_dir, "session_20250601_000000", session_data)

        staged_compression.compress_sessions(self.memory_dir, now=now, protected_recent_sessions=0)

        state_path = staged_compression._get_state_file_path(self.memory_dir)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["last_run"], "2026-03-09T12:00:00Z")


# ================================================================
# Test: Character truncation for summary
# ================================================================

class TestCharTruncation(unittest.TestCase):
    """Test _truncate_chars helper."""

    def test_within_cap(self):
        result = staged_compression._truncate_chars("short", 100)
        self.assertEqual(result, "short")

    def test_exactly_at_cap(self):
        result = staged_compression._truncate_chars("12345", 5)
        self.assertEqual(result, "12345")

    def test_over_cap(self):
        result = staged_compression._truncate_chars("1234567890", 5)
        self.assertEqual(result, "12345")

    def test_empty_string(self):
        result = staged_compression._truncate_chars("", 5)
        self.assertEqual(result, "")


# ================================================================
# Test: extract_insights (C22-D)
# ================================================================

class TestExtractInsights(unittest.TestCase):
    """Test insight extraction from episode utterances and summary."""

    def test_japanese_pattern_detection(self):
        """Japanese patterns like 理由:, 教訓:, 判断: are detected."""
        ep = _make_episode(
            user_texts=["理由: デッドロックを回避するため非同期にした"],
            summary="非同期処理への変更",
        )
        insights = staged_compression.extract_insights(ep)
        self.assertGreater(len(insights), 0)
        self.assertTrue(any("デッドロック" in i for i in insights))

    def test_english_pattern_detection(self):
        """English patterns like 'because', 'lesson:' are detected."""
        ep = _make_episode(
            user_texts=["We changed it because the old approach caused timeouts"],
            summary="Timeout fix",
        )
        insights = staged_compression.extract_insights(ep)
        self.assertGreater(len(insights), 0)
        self.assertTrue(any("timeout" in i.lower() for i in insights))

    def test_multiple_patterns_in_utterances(self):
        """Multiple patterns across utterances are all captured."""
        ep = _make_episode(
            user_texts=[
                "教訓: テストなしでデプロイしない",
                "重要: この設定は本番環境のみ",
                "普通のテキスト without patterns",
            ],
            summary="デプロイルール",
        )
        insights = staged_compression.extract_insights(ep)
        self.assertGreaterEqual(len(insights), 2)

    def test_no_patterns_returns_empty(self):
        """Episodes without insight patterns return empty list."""
        ep = _make_episode(
            user_texts=["hello world", "just chatting"],
            summary="casual conversation",
        )
        insights = staged_compression.extract_insights(ep)
        self.assertEqual(insights, [])

    def test_max_length_500_chars(self):
        """Total insights text is capped at 500 characters."""
        long_reason = "理由: " + "あ" * 600
        ep = _make_episode(
            user_texts=[long_reason],
            summary="long reason",
        )
        insights = staged_compression.extract_insights(ep)
        total_len = sum(len(i) for i in insights)
        self.assertLessEqual(total_len, 500)

    def test_extraction_from_summary_when_no_utterances(self):
        """When utterances are empty, extract from summary."""
        ep = _make_episode(
            user_texts=None,
            summary="教訓: embedding timeoutは5秒では不足、30秒必要",
        )
        insights = staged_compression.extract_insights(ep)
        self.assertGreater(len(insights), 0)

    def test_empty_episode(self):
        """Episode with empty utterances and empty summary returns empty."""
        ep = _make_episode(user_texts=None, summary="")
        insights = staged_compression.extract_insights(ep)
        self.assertEqual(insights, [])

    def test_pattern_kizuki(self):
        """気づき pattern is detected."""
        ep = _make_episode(
            user_texts=["気づき: hookのstdoutはPreToolUseでは見えない"],
            summary="hook仕様",
        )
        insights = staged_compression.extract_insights(ep)
        self.assertGreater(len(insights), 0)

    def test_pattern_hakken(self):
        """発見 pattern is detected."""
        ep = _make_episode(
            user_texts=["発見: sync_vectorsをdaemon threadにすると安定する"],
            summary="sync修正",
        )
        insights = staged_compression.extract_insights(ep)
        self.assertGreater(len(insights), 0)


# ================================================================
# Test: compress_episode with insights (C22-D)
# ================================================================

class TestCompressEpisodeInsights(unittest.TestCase):
    """Test insights extraction and preservation during compression."""

    def test_stage_0_to_1_extracts_insights(self):
        """Compressing from stage 0 to 1+ triggers insight extraction."""
        ep = _make_episode(
            user_texts=["理由: パフォーマンス改善のため"],
            summary="Performance improvement",
        )
        result = staged_compression.compress_episode(ep, 1)
        self.assertIn("insights", result)
        self.assertGreater(len(result["insights"]), 0)

    def test_stage_1_to_3_preserves_insights(self):
        """Insights survive compression from stage 1 through to stage 3."""
        ep = _make_episode(
            user_texts=["教訓: デッドロック回避には非同期が必須"],
            summary="Deadlock fix",
        )
        result1 = staged_compression.compress_episode(ep, 1)
        self.assertIn("insights", result1)
        insights_original = result1["insights"]

        result3 = staged_compression.compress_episode(result1, 3)
        self.assertEqual(result3["compression_stage"], 3)
        self.assertEqual(result3["insights"], insights_original)

    def test_existing_insights_not_overwritten(self):
        """If insights field already exists, it is not overwritten."""
        ep = _make_episode(
            user_texts=["理由: 新しい理由"],
            summary="New reason",
        )
        ep["insights"] = ["既存のインサイト: これは保持されるべき"]
        result = staged_compression.compress_episode(ep, 1)
        self.assertEqual(result["insights"], ["既存のインサイト: これは保持されるべき"])

    def test_insights_preserved_at_skeleton(self):
        """Skeleton stage removes utterances but keeps insights."""
        ep = _make_episode(
            user_texts=["判断: この設計にした理由はスケーラビリティ"],
            summary="Design decision " * 20,
        )
        result = staged_compression.compress_episode(ep, 3)
        self.assertEqual(result["compression_stage"], 3)
        self.assertEqual(len(result["user_utterances"]), 0)
        self.assertIn("insights", result)
        self.assertGreater(len(result["insights"]), 0)

    def test_backward_compat_no_insights_field(self):
        """Episodes without insights field (old data) compress normally."""
        ep = _make_episode(
            user_texts=["hello world"],
            summary="casual",
            compression_stage=1,
        )
        # No insights field, already at stage 1
        result = staged_compression.compress_episode(ep, 3)
        self.assertEqual(result["compression_stage"], 3)
        # insights may be empty list or absent, but should not error

    def test_no_pattern_episode_gets_empty_insights(self):
        """Episodes without patterns get insights=[] (not absent)."""
        ep = _make_episode(
            user_texts=["just normal text"],
            summary="normal summary",
        )
        result = staged_compression.compress_episode(ep, 1)
        self.assertIn("insights", result)
        self.assertEqual(result["insights"], [])

    def test_dry_run_shows_insights_info(self):
        """Dry-run output includes insights information."""
        import tempfile
        import shutil
        tmp_dir = tempfile.mkdtemp()
        try:
            episodes_dir = Path(tmp_dir) / "episodes"
            episodes_dir.mkdir()
            now = datetime(2026, 3, 9, tzinfo=timezone.utc)
            old_ts = "2025-01-01T00:00:00Z"
            ep = _make_episode(
                timestamp=old_ts,
                user_texts=["教訓: TDDは必須"],
                summary="TDD rule",
            )
            session_data = _make_session("session_20250101_000000", [ep])
            _write_session(episodes_dir, "session_20250101_000000", session_data)

            result = staged_compression.dry_run(tmp_dir, now=now, protected_recent_sessions=0)
            # dry_run should mention insights
            self.assertIn("insight", result.lower())
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


class TestApplyRecallDelay(unittest.TestCase):
    """C22-B: Tests for apply_recall_delay compression delay."""

    def test_recall_count_3_delays_one_stage(self):
        """recall_count >= 3 should delay compression by 1 stage."""
        from staged_compression import apply_recall_delay
        assert apply_recall_delay(2, 3) == 1  # target 2 - 1 = 1

    def test_recall_count_5_delays_two_stages(self):
        """recall_count >= 5 should delay compression by 2 stages."""
        from staged_compression import apply_recall_delay
        assert apply_recall_delay(3, 5) == 1  # target 3 - 2 = 1

    def test_delay_cannot_go_below_zero(self):
        """Delayed target stage should not go below 0."""
        from staged_compression import apply_recall_delay
        assert apply_recall_delay(1, 5) == 0  # target 1 - 2 = -1 -> clamped to 0
        assert apply_recall_delay(0, 10) == 0  # target 0 - 2 -> clamped to 0

    def test_recall_count_below_threshold_no_delay(self):
        """recall_count < 3 should not cause any delay."""
        from staged_compression import apply_recall_delay
        assert apply_recall_delay(2, 0) == 2
        assert apply_recall_delay(3, 2) == 3

    def test_force_skeleton_ignores_delay(self):
        """force_skeleton (stage 3 from overflow) should not be delayed."""
        # This is tested via _compress_session_data with force=True
        from staged_compression import _compress_session_data
        session_data = {
            "session_id": "test",
            "episodes": [
                {"episode_id": "e1", "summary": "s", "recall_count": 10,
                 "user_utterances": [], "episode_type": "observation",
                 "timestamp": "2025-01-01T00:00:00Z"},
            ],
        }
        result, changed = _compress_session_data(session_data, 3, force=True)
        assert result["episodes"][0].get("compression_stage", 0) == 3

    def test_backward_compat_no_recall_count(self):
        """Episodes without recall_count (treated as 0) get no delay."""
        from staged_compression import apply_recall_delay
        assert apply_recall_delay(2, 0) == 2  # default recall_count=0


class TestDryRunRecallDelay(unittest.TestCase):
    """C22-B: Dry-run should show recall delay info."""

    def test_dry_run_shows_delayed_episodes(self):
        """dry_run output should reflect recall-delayed compression stages."""
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")

        episodes_dir = os.path.join(self.tmp_dir, "episodes")
        os.makedirs(episodes_dir, exist_ok=True)
        session_data = {
            "session_id": "session_old",
            "created_at": old_ts,
            "episodes": [
                {"episode_id": "e1", "summary": "test", "recall_count": 5,
                 "user_utterances": [], "episode_type": "observation",
                 "timestamp": old_ts, "compression_stage": 0},
            ],
        }
        with open(os.path.join(episodes_dir, "session_old.json"), "w") as f:
            json.dump(session_data, f)

        result = staged_compression.dry_run(
            self.tmp_dir, now=now, protected_recent_sessions=0)
        assert isinstance(result, str)

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
