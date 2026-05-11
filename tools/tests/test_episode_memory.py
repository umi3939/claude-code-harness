"""Tests for episode_memory.py."""

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

# Add parent directory to path so we can import episode_memory
sys.path.insert(0, str(Path(__file__).parent.parent))
import episode_memory


# --- Fixtures ---

@pytest.fixture
def tmp_memory_dir(tmp_path):
    """Provide a temporary memory directory."""
    return str(tmp_path)


@pytest.fixture
def memory_with_episodes(tmp_memory_dir):
    """Create a memory dir with a session containing 3 episodes."""
    episode_memory.record_episode(
        memory_dir=tmp_memory_dir,
        episode_type="user_request",
        summary="User asked for bug fix",
        user_texts=["Fix the login bug please"],
        tags=["auth", "bugfix"],
        session_id="session_20260309_100000",
    )
    episode_memory.record_episode(
        memory_dir=tmp_memory_dir,
        episode_type="decision",
        summary="Decided to refactor auth module",
        tags=["auth", "refactor"],
        session_id="session_20260309_100000",
    )
    episode_memory.record_episode(
        memory_dir=tmp_memory_dir,
        episode_type="solution",
        summary="Bug fix applied successfully",
        user_texts=["Looks good, thanks"],
        tags=["auth", "bugfix"],
        session_id="session_20260309_100000",
    )
    return tmp_memory_dir


@pytest.fixture
def memory_with_multiple_sessions(tmp_memory_dir):
    """Create a memory dir with 3 sessions."""
    for i in range(3):
        sid = f"session_2026030{i + 1}_100000"
        episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="observation",
            summary=f"Observation in session {i + 1}",
            session_id=sid,
        )
    return tmp_memory_dir


# ===== record_episode tests =====

class TestRecordEpisode:
    """Tests for the record_episode function."""

    def test_record_creates_episodes_dir(self, tmp_memory_dir):
        result = episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="user_request",
            summary="Test episode",
        )
        assert not result.startswith("ERROR:")
        assert episode_memory.get_episodes_path(tmp_memory_dir).exists()

    def test_record_returns_success_message(self, tmp_memory_dir):
        result = episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="decision",
            summary="Test decision",
        )
        assert "Episode recorded:" in result
        assert "session_" in result

    def test_record_with_all_fields(self, tmp_memory_dir):
        result = episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="feedback",
            summary="User gave feedback",
            user_texts=["This is great", "But fix this part"],
            tags=["module_a", "quality"],
            session_id="session_20260309_120000",
        )
        assert not result.startswith("ERROR:")

        # Verify data was written
        session_file = episode_memory.get_episodes_path(tmp_memory_dir) / "session_20260309_120000.json"
        data = json.loads(session_file.read_text(encoding="utf-8"))
        ep = data["episodes"][0]
        assert ep["episode_type"] == "feedback"
        assert ep["summary"] == "User gave feedback"
        assert len(ep["user_utterances"]) == 2
        assert ep["user_utterances"][0]["text"] == "This is great"
        assert ep["user_utterances"][0]["role"] == "user"
        assert ep["user_utterances"][0]["truncated"] is False
        assert ep["user_utterances"][1]["text"] == "But fix this part"
        assert ep["tags"] == ["module_a", "quality"]
        assert ep["session_id"] == "session_20260309_120000"

    def test_record_minimal_fields(self, tmp_memory_dir):
        result = episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="observation",
            summary="Minimal episode",
        )
        assert not result.startswith("ERROR:")

    def test_record_creates_parent_dirs(self, tmp_memory_dir):
        nested = os.path.join(tmp_memory_dir, "deep", "nested", "dir")
        result = episode_memory.record_episode(
            memory_dir=nested,
            episode_type="error",
            summary="Deep episode",
        )
        assert not result.startswith("ERROR:")
        assert episode_memory.get_episodes_path(nested).exists()

    def test_record_invalid_type(self, tmp_memory_dir):
        result = episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="invalid_type",
            summary="Should fail",
        )
        assert result.startswith("ERROR:")
        assert "Invalid episode type" in result

    def test_record_empty_summary(self, tmp_memory_dir):
        result = episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="observation",
            summary="",
        )
        assert result.startswith("ERROR:")
        assert "Summary is required" in result

    def test_record_whitespace_summary(self, tmp_memory_dir):
        result = episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="observation",
            summary="   ",
        )
        assert result.startswith("ERROR:")

    def test_record_appends_to_existing_session(self, tmp_memory_dir):
        sid = "session_20260309_140000"
        episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="user_request",
            summary="First episode",
            session_id=sid,
        )
        episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="solution",
            summary="Second episode",
            session_id=sid,
        )

        session_file = episode_memory.get_episodes_path(tmp_memory_dir) / f"{sid}.json"
        data = json.loads(session_file.read_text(encoding="utf-8"))
        assert len(data["episodes"]) == 2
        assert data["episodes"][0]["summary"] == "First episode"
        assert data["episodes"][1]["summary"] == "Second episode"

    def test_record_generates_unique_ids(self, tmp_memory_dir):
        ids = set()
        for i in range(20):
            result = episode_memory.record_episode(
                memory_dir=tmp_memory_dir,
                episode_type="observation",
                summary=f"Episode {i}",
                session_id="session_20260309_150000",
            )
            # Extract episode ID from result
            ep_id = result.split(":")[1].strip().split(" ")[0]
            ids.add(ep_id)
        assert len(ids) == 20

    def test_record_episode_id_format(self, tmp_memory_dir):
        result = episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="observation",
            summary="ID format test",
            session_id="session_20260309_160000",
        )
        ep_id = result.split(":")[1].strip().split(" ")[0]
        assert len(ep_id) == 12
        assert all(c in "0123456789abcdef" for c in ep_id)

    def test_record_timestamp_format(self, tmp_memory_dir):
        sid = "session_20260309_170000"
        episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="observation",
            summary="Timestamp test",
            session_id=sid,
        )
        session_file = episode_memory.get_episodes_path(tmp_memory_dir) / f"{sid}.json"
        data = json.loads(session_file.read_text(encoding="utf-8"))
        ts = data["episodes"][0]["timestamp"]
        # Should be ISO 8601 format ending with Z
        assert ts.endswith("Z")
        # Should be parseable
        datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")

    def test_record_strips_summary_whitespace(self, tmp_memory_dir):
        sid = "session_20260309_180000"
        episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="observation",
            summary="  padded summary  ",
            session_id=sid,
        )
        session_file = episode_memory.get_episodes_path(tmp_memory_dir) / f"{sid}.json"
        data = json.loads(session_file.read_text(encoding="utf-8"))
        assert data["episodes"][0]["summary"] == "padded summary"

    def test_record_strips_empty_tags(self, tmp_memory_dir):
        sid = "session_20260309_190000"
        episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="observation",
            summary="Tags test",
            tags=["valid", "", "  ", "also_valid"],
            session_id=sid,
        )
        session_file = episode_memory.get_episodes_path(tmp_memory_dir) / f"{sid}.json"
        data = json.loads(session_file.read_text(encoding="utf-8"))
        assert data["episodes"][0]["tags"] == ["valid", "also_valid"]

    def test_record_no_user_texts(self, tmp_memory_dir):
        sid = "session_20260309_200000"
        episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="decision",
            summary="No utterances",
            session_id=sid,
        )
        session_file = episode_memory.get_episodes_path(tmp_memory_dir) / f"{sid}.json"
        data = json.loads(session_file.read_text(encoding="utf-8"))
        assert data["episodes"][0]["user_utterances"] == []

    def test_record_all_episode_types(self, tmp_memory_dir):
        for ep_type in episode_memory.EPISODE_TYPES:
            result = episode_memory.record_episode(
                memory_dir=tmp_memory_dir,
                episode_type=ep_type,
                summary=f"Testing {ep_type}",
                session_id=f"session_type_{ep_type}",
            )
            assert not result.startswith("ERROR:"), f"Failed for type: {ep_type}"


# ===== User utterance tests =====

class TestUserUtterances:
    """Tests for user utterance handling."""

    def test_utterance_size_cap_truncation(self, tmp_memory_dir):
        long_text = "A" * 3000  # exceeds default 2000 byte cap
        sid = "session_20260309_210000"
        episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="feedback",
            summary="Long utterance test",
            user_texts=[long_text],
            session_id=sid,
        )
        session_file = episode_memory.get_episodes_path(tmp_memory_dir) / f"{sid}.json"
        data = json.loads(session_file.read_text(encoding="utf-8"))
        utt = data["episodes"][0]["user_utterances"][0]
        assert utt["truncated"] is True
        assert len(utt["text"].encode("utf-8")) <= episode_memory.DEFAULT_UTTERANCE_SIZE_CAP

    def test_utterance_within_cap_not_truncated(self, tmp_memory_dir):
        short_text = "Short message"
        sid = "session_20260309_220000"
        episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="feedback",
            summary="Short utterance test",
            user_texts=[short_text],
            session_id=sid,
        )
        session_file = episode_memory.get_episodes_path(tmp_memory_dir) / f"{sid}.json"
        data = json.loads(session_file.read_text(encoding="utf-8"))
        utt = data["episodes"][0]["user_utterances"][0]
        assert utt["truncated"] is False
        assert utt["text"] == "Short message"

    def test_utterance_role_always_user(self, tmp_memory_dir):
        sid = "session_20260309_230000"
        episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="user_request",
            summary="Role test",
            user_texts=["text1", "text2"],
            session_id=sid,
        )
        session_file = episode_memory.get_episodes_path(tmp_memory_dir) / f"{sid}.json"
        data = json.loads(session_file.read_text(encoding="utf-8"))
        for utt in data["episodes"][0]["user_utterances"]:
            assert utt["role"] == "user"

    def test_utterance_per_episode_cap(self, tmp_memory_dir):
        texts = [f"Utterance {i}" for i in range(20)]
        sid = "session_20260309_cap_test"
        episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="feedback",
            summary="Many utterances",
            user_texts=texts,
            session_id=sid,
            utterances_per_episode=5,
        )
        session_file = episode_memory.get_episodes_path(tmp_memory_dir) / f"{sid}.json"
        data = json.loads(session_file.read_text(encoding="utf-8"))
        assert len(data["episodes"][0]["user_utterances"]) == 5

    def test_utterance_custom_size_cap(self, tmp_memory_dir):
        text = "A" * 100
        sid = "session_20260309_custom_cap"
        episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="feedback",
            summary="Custom cap test",
            user_texts=[text],
            session_id=sid,
            utterance_size_cap=50,
        )
        session_file = episode_memory.get_episodes_path(tmp_memory_dir) / f"{sid}.json"
        data = json.loads(session_file.read_text(encoding="utf-8"))
        utt = data["episodes"][0]["user_utterances"][0]
        assert utt["truncated"] is True
        assert len(utt["text"].encode("utf-8")) <= 50

    def test_utterance_multibyte_truncation(self, tmp_memory_dir):
        # Japanese text (3 bytes per char in UTF-8)
        text = "あ" * 1000  # 3000 bytes
        sid = "session_20260309_multibyte"
        episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="feedback",
            summary="Multibyte truncation test",
            user_texts=[text],
            session_id=sid,
        )
        session_file = episode_memory.get_episodes_path(tmp_memory_dir) / f"{sid}.json"
        data = json.loads(session_file.read_text(encoding="utf-8"))
        utt = data["episodes"][0]["user_utterances"][0]
        assert utt["truncated"] is True
        # Should be valid UTF-8 after truncation
        utt["text"].encode("utf-8")


# ===== Per-session episode cap tests =====

class TestEpisodeCapFIFO:
    """Tests for per-session episode cap (FIFO removal)."""

    def test_episode_cap_enforced(self, tmp_memory_dir):
        sid = "session_20260309_fifo"
        for i in range(10):
            episode_memory.record_episode(
                memory_dir=tmp_memory_dir,
                episode_type="observation",
                summary=f"Episode {i}",
                session_id=sid,
                episodes_per_session=5,
            )

        session_file = episode_memory.get_episodes_path(tmp_memory_dir) / f"{sid}.json"
        data = json.loads(session_file.read_text(encoding="utf-8"))
        assert len(data["episodes"]) == 5

    def test_episode_cap_keeps_newest(self, tmp_memory_dir):
        sid = "session_20260309_fifo_order"
        for i in range(10):
            episode_memory.record_episode(
                memory_dir=tmp_memory_dir,
                episode_type="observation",
                summary=f"Episode {i}",
                session_id=sid,
                episodes_per_session=3,
            )

        session_file = episode_memory.get_episodes_path(tmp_memory_dir) / f"{sid}.json"
        data = json.loads(session_file.read_text(encoding="utf-8"))
        summaries = [ep["summary"] for ep in data["episodes"]]
        assert "Episode 7" in summaries
        assert "Episode 8" in summaries
        assert "Episode 9" in summaries
        assert "Episode 0" not in summaries


# ===== Total session cap tests =====

class TestTotalSessionCap:
    """Tests for total session file cap."""

    def test_session_cap_enforced(self, tmp_memory_dir):
        for i in range(8):
            sid = f"session_2026030{i}_100000"
            episode_memory.record_episode(
                memory_dir=tmp_memory_dir,
                episode_type="observation",
                summary=f"Session {i} episode",
                session_id=sid,
                total_sessions=3,
            )
            # Small sleep to ensure different mtime
            time.sleep(0.05)

        files = episode_memory._list_session_files(
            episode_memory.get_episodes_path(tmp_memory_dir)
        )
        assert len(files) <= 3

    def test_session_cap_keeps_newest(self, tmp_memory_dir):
        for i in range(5):
            sid = f"session_2026030{i + 1}_100000"
            episode_memory.record_episode(
                memory_dir=tmp_memory_dir,
                episode_type="observation",
                summary=f"Session {i + 1}",
                session_id=sid,
                total_sessions=2,
            )
            time.sleep(0.05)

        files = episode_memory._list_session_files(
            episode_memory.get_episodes_path(tmp_memory_dir)
        )
        names = [f.stem for f in files]
        # The newest sessions should survive
        assert "session_20260305_100000" in names


# ===== Session auto-detection tests =====

class TestSessionAutoDetection:
    """Tests for automatic session detection."""

    def test_auto_creates_new_session_when_none_exists(self, tmp_memory_dir):
        result = episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="observation",
            summary="First ever episode",
        )
        assert not result.startswith("ERROR:")
        assert "session_" in result

    def test_auto_reuses_recent_session(self, tmp_memory_dir):
        # Record first episode (creates session)
        result1 = episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="observation",
            summary="Episode 1",
        )
        session1 = result1.split(" in ")[1]

        # Record second episode (should reuse same session)
        result2 = episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="observation",
            summary="Episode 2",
        )
        session2 = result2.split(" in ")[1]

        assert session1 == session2

    def test_auto_creates_new_session_when_old(self, tmp_memory_dir):
        # Create a session with old timestamp
        episodes_dir = episode_memory._ensure_episodes_dir(tmp_memory_dir)
        old_time = datetime.now(timezone.utc) - timedelta(hours=10)
        old_sid = episode_memory._session_id_from_timestamp(old_time)
        old_data = {
            "session_id": old_sid,
            "created_at": old_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "episodes": [],
        }
        episode_memory._write_session_file(
            episodes_dir / episode_memory._session_filename(old_sid),
            old_data,
        )

        # Record new episode without specifying session_id
        result = episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="observation",
            summary="New session episode",
            session_age_hours=4,
        )
        assert not result.startswith("ERROR:")
        # Should have created a new session, not reused old one
        new_sid = result.split(" in ")[1]
        assert new_sid != old_sid

    def test_explicit_session_id_creates_new_if_missing(self, tmp_memory_dir):
        sid = "session_20260309_explicit"
        result = episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="observation",
            summary="Explicit new session",
            session_id=sid,
        )
        assert not result.startswith("ERROR:")
        assert sid in result


# ===== Corrupted file handling tests =====

class TestCorruptedFileHandling:
    """Tests for handling corrupted session files."""

    def test_corrupted_session_skipped_in_listing(self, tmp_memory_dir):
        episodes_dir = episode_memory._ensure_episodes_dir(tmp_memory_dir)

        # Create a valid session
        episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="observation",
            summary="Valid session",
            session_id="session_20260309_valid",
        )

        # Create a corrupted session file
        corrupted_file = episodes_dir / "session_20260309_corrupted.json"
        corrupted_file.write_text("NOT VALID JSON", encoding="utf-8")

        result = episode_memory.list_sessions(tmp_memory_dir)
        assert "corrupted" in result.lower()
        assert "Valid session" not in result  # list_sessions shows session IDs, not summaries
        assert "session_20260309_valid" in result

    def test_corrupted_session_skipped_in_show(self, tmp_memory_dir):
        episodes_dir = episode_memory._ensure_episodes_dir(tmp_memory_dir)

        # Create a corrupted session file
        corrupted_file = episodes_dir / "session_20260309_corrupted.json"
        corrupted_file.write_text("{bad json", encoding="utf-8")

        result = episode_memory.show_episode(tmp_memory_dir, "nonexistent_id")
        assert "not found" in result.lower()

    def test_binary_corrupted_file(self, tmp_memory_dir):
        episodes_dir = episode_memory._ensure_episodes_dir(tmp_memory_dir)
        corrupted_file = episodes_dir / "session_20260309_binary.json"
        corrupted_file.write_bytes(b"\x00\x01\x02\x03")

        result = episode_memory.list_sessions(tmp_memory_dir)
        assert "corrupted" in result.lower()


# ===== list_sessions tests =====

class TestListSessions:
    """Tests for the list_sessions function."""

    def test_list_no_episodes_dir(self, tmp_memory_dir):
        result = episode_memory.list_sessions(tmp_memory_dir)
        assert "No episodes found" in result

    def test_list_empty_episodes_dir(self, tmp_memory_dir):
        episode_memory._ensure_episodes_dir(tmp_memory_dir)
        result = episode_memory.list_sessions(tmp_memory_dir)
        assert "No episodes found" in result

    def test_list_shows_count(self, memory_with_multiple_sessions):
        result = episode_memory.list_sessions(memory_with_multiple_sessions)
        assert "3 sessions" in result

    def test_list_shows_session_ids(self, memory_with_multiple_sessions):
        result = episode_memory.list_sessions(memory_with_multiple_sessions)
        assert "session_20260301_100000" in result
        assert "session_20260302_100000" in result
        assert "session_20260303_100000" in result

    def test_list_shows_episode_counts(self, memory_with_multiple_sessions):
        result = episode_memory.list_sessions(memory_with_multiple_sessions)
        assert "episodes: 1" in result

    def test_list_shows_numbering(self, memory_with_multiple_sessions):
        result = episode_memory.list_sessions(memory_with_multiple_sessions)
        assert "1." in result
        assert "2." in result
        assert "3." in result


# ===== list_episodes tests =====

class TestListEpisodes:
    """Tests for the list_episodes function."""

    def test_list_no_episodes_dir(self, tmp_memory_dir):
        result = episode_memory.list_episodes(tmp_memory_dir)
        assert "No episodes found" in result

    def test_list_specific_session(self, memory_with_episodes):
        result = episode_memory.list_episodes(
            memory_with_episodes, "session_20260309_100000"
        )
        assert "3 episodes" in result
        assert "user_request" in result
        assert "decision" in result
        assert "solution" in result

    def test_list_latest_session(self, memory_with_episodes):
        result = episode_memory.list_episodes(memory_with_episodes)
        assert "3 episodes" in result

    def test_list_nonexistent_session(self, memory_with_episodes):
        result = episode_memory.list_episodes(
            memory_with_episodes, "session_nonexistent"
        )
        assert result.startswith("ERROR:")
        assert "not found" in result.lower()

    def test_list_truncates_long_summary(self, tmp_memory_dir):
        long_summary = "A" * 200
        episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="observation",
            summary=long_summary,
            session_id="session_20260309_long",
        )
        result = episode_memory.list_episodes(tmp_memory_dir, "session_20260309_long")
        assert "..." in result

    def test_list_shows_episode_ids(self, memory_with_episodes):
        result = episode_memory.list_episodes(
            memory_with_episodes, "session_20260309_100000"
        )
        # Each episode should have a 12-char hex ID shown
        lines = [l for l in result.split("\n") if l.strip().startswith(("1.", "2.", "3."))]
        assert len(lines) == 3


# ===== show_episode tests =====

class TestShowEpisode:
    """Tests for the show_episode function."""

    def test_show_episode_by_id(self, tmp_memory_dir):
        sid = "session_20260309_show_test"
        result = episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="user_request",
            summary="Show test episode",
            user_texts=["Find the bug"],
            tags=["debug"],
            session_id=sid,
        )
        ep_id = result.split(":")[1].strip().split(" ")[0]

        show_result = episode_memory.show_episode(tmp_memory_dir, ep_id)
        assert ep_id in show_result
        assert "user_request" in show_result
        assert "Show test episode" in show_result
        assert "Find the bug" in show_result
        assert "debug" in show_result

    def test_show_nonexistent_episode(self, tmp_memory_dir):
        result = episode_memory.show_episode(tmp_memory_dir, "nonexistent_id")
        assert result.startswith("ERROR:")
        assert "not found" in result.lower()

    def test_show_episode_across_sessions(self, tmp_memory_dir):
        # Create episodes in different sessions
        results = []
        for i in range(3):
            result = episode_memory.record_episode(
                memory_dir=tmp_memory_dir,
                episode_type="observation",
                summary=f"Episode in session {i}",
                session_id=f"session_cross_{i}",
            )
            results.append(result)

        # Show episode from the second session
        ep_id = results[1].split(":")[1].strip().split(" ")[0]
        show_result = episode_memory.show_episode(tmp_memory_dir, ep_id)
        assert "Episode in session 1" in show_result

    def test_show_episode_with_truncated_utterance(self, tmp_memory_dir):
        long_text = "B" * 3000
        sid = "session_20260309_truncated_show"
        result = episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="feedback",
            summary="Truncated show test",
            user_texts=[long_text],
            session_id=sid,
        )
        ep_id = result.split(":")[1].strip().split(" ")[0]

        show_result = episode_memory.show_episode(tmp_memory_dir, ep_id)
        assert "[truncated]" in show_result

    def test_show_episode_without_utterances(self, tmp_memory_dir):
        sid = "session_20260309_no_utt"
        result = episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="decision",
            summary="No utterances show test",
            session_id=sid,
        )
        ep_id = result.split(":")[1].strip().split(" ")[0]

        show_result = episode_memory.show_episode(tmp_memory_dir, ep_id)
        assert "No utterances show test" in show_result
        assert "User utterances" not in show_result


# ===== Atomic write tests =====

class TestAtomicWrite:
    """Tests for atomic write behavior."""

    def test_temp_file_cleanup_on_failure(self, tmp_memory_dir):
        episodes_dir = episode_memory._ensure_episodes_dir(tmp_memory_dir)

        # Record a valid episode first
        episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="observation",
            summary="Before failure",
            session_id="session_20260309_atomic",
        )

        # Count files before
        files_before = list(episodes_dir.iterdir())
        temp_files_before = [f for f in files_before if f.name.startswith(".episode_")]
        assert len(temp_files_before) == 0

    def test_data_integrity_after_write(self, tmp_memory_dir):
        sid = "session_20260309_integrity"
        episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="observation",
            summary="Integrity test",
            user_texts=["User said something"],
            tags=["test_tag"],
            session_id=sid,
        )

        session_file = episode_memory.get_episodes_path(tmp_memory_dir) / f"{sid}.json"
        data = json.loads(session_file.read_text(encoding="utf-8"))

        # Verify JSON structure is valid and complete
        assert data["session_id"] == sid
        assert "created_at" in data
        assert len(data["episodes"]) == 1
        ep = data["episodes"][0]
        assert "episode_id" in ep
        assert "episode_type" in ep
        assert "summary" in ep
        assert "user_utterances" in ep
        assert "tags" in ep
        assert "timestamp" in ep
        assert "session_id" in ep


# ===== Empty/missing episodes directory tests =====

class TestEmptyMissingDir:
    """Tests for empty or missing episodes directory."""

    def test_list_sessions_missing_dir(self, tmp_memory_dir):
        result = episode_memory.list_sessions(tmp_memory_dir)
        assert "No episodes found" in result

    def test_list_episodes_missing_dir(self, tmp_memory_dir):
        result = episode_memory.list_episodes(tmp_memory_dir)
        assert "No episodes found" in result

    def test_show_episode_missing_dir(self, tmp_memory_dir):
        result = episode_memory.show_episode(tmp_memory_dir, "any_id")
        assert "not found" in result.lower()

    def test_record_creates_dir(self, tmp_memory_dir):
        episodes_dir = episode_memory.get_episodes_path(tmp_memory_dir)
        assert not episodes_dir.exists()

        episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="observation",
            summary="Creates dir",
        )
        assert episodes_dir.exists()


# ===== UTF-8 handling tests =====

class TestUTF8Handling:
    """Tests for UTF-8 handling with non-ASCII content."""

    def test_utf8_summary(self, tmp_memory_dir):
        sid = "session_20260309_utf8"
        episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="observation",
            summary="日本語のサマリー with <html> & \"quotes\"",
            session_id=sid,
        )
        session_file = episode_memory.get_episodes_path(tmp_memory_dir) / f"{sid}.json"
        data = json.loads(session_file.read_text(encoding="utf-8"))
        assert data["episodes"][0]["summary"] == "日本語のサマリー with <html> & \"quotes\""

    def test_utf8_user_utterance(self, tmp_memory_dir):
        sid = "session_20260309_utf8_utt"
        episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="feedback",
            summary="UTF-8 utterance test",
            user_texts=["ユーザーの発言です", "Emojis too"],
            session_id=sid,
        )
        session_file = episode_memory.get_episodes_path(tmp_memory_dir) / f"{sid}.json"
        data = json.loads(session_file.read_text(encoding="utf-8"))
        assert data["episodes"][0]["user_utterances"][0]["text"] == "ユーザーの発言です"

    def test_utf8_tags(self, tmp_memory_dir):
        sid = "session_20260309_utf8_tags"
        episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="observation",
            summary="UTF-8 tags",
            tags=["モジュール名", "feature_name"],
            session_id=sid,
        )
        session_file = episode_memory.get_episodes_path(tmp_memory_dir) / f"{sid}.json"
        data = json.loads(session_file.read_text(encoding="utf-8"))
        assert "モジュール名" in data["episodes"][0]["tags"]

    def test_utf8_in_show_output(self, tmp_memory_dir):
        sid = "session_20260309_utf8_show"
        result = episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="feedback",
            summary="表示テスト",
            user_texts=["日本語テキスト"],
            tags=["タグ"],
            session_id=sid,
        )
        ep_id = result.split(":")[1].strip().split(" ")[0]
        show_result = episode_memory.show_episode(tmp_memory_dir, ep_id)
        assert "表示テスト" in show_result
        assert "日本語テキスト" in show_result
        assert "タグ" in show_result


# ===== get_episodes_path tests =====

class TestGetEpisodesPath:
    """Tests for get_episodes_path."""

    def test_returns_path_object(self):
        result = episode_memory.get_episodes_path("/some/dir")
        assert isinstance(result, Path)

    def test_includes_subdir_name(self):
        result = episode_memory.get_episodes_path("/some/dir")
        assert result.name == episode_memory.EPISODES_SUBDIR

    def test_includes_parent_directory(self):
        result = episode_memory.get_episodes_path("/some/dir")
        assert str(result.parent).replace("\\", "/") == "/some/dir"


# ===== CLI tests =====

class TestCLI:
    """Tests for the CLI interface."""

    def test_cli_record(self, tmp_memory_dir, capsys):
        episode_memory.main([
            "record",
            "--memory-dir", tmp_memory_dir,
            "--type", "user_request",
            "--summary", "CLI record test",
            "--tags", "tag1,tag2",
            "--user-text", "User said this",
            "--session-id", "session_cli_test",
        ])
        captured = capsys.readouterr()
        assert "Episode recorded:" in captured.out

    def test_cli_record_with_multiple_user_texts(self, tmp_memory_dir, capsys):
        episode_memory.main([
            "record",
            "--memory-dir", tmp_memory_dir,
            "--type", "feedback",
            "--summary", "CLI multi text",
            "--user-text", "First thing",
            "--user-text", "Second thing",
            "--session-id", "session_cli_multi",
        ])
        captured = capsys.readouterr()
        assert "Episode recorded:" in captured.out

        # Verify both texts were saved
        session_file = episode_memory.get_episodes_path(tmp_memory_dir) / "session_cli_multi.json"
        data = json.loads(session_file.read_text(encoding="utf-8"))
        assert len(data["episodes"][0]["user_utterances"]) == 2

    def test_cli_record_minimal(self, tmp_memory_dir, capsys):
        episode_memory.main([
            "record",
            "--memory-dir", tmp_memory_dir,
            "--type", "observation",
            "--summary", "Minimal CLI record",
        ])
        captured = capsys.readouterr()
        assert "Episode recorded:" in captured.out

    def test_cli_list_sessions(self, memory_with_multiple_sessions, capsys):
        episode_memory.main([
            "list-sessions",
            "--memory-dir", memory_with_multiple_sessions,
        ])
        captured = capsys.readouterr()
        assert "3 sessions" in captured.out

    def test_cli_list_episodes(self, memory_with_episodes, capsys):
        episode_memory.main([
            "list-episodes",
            "--memory-dir", memory_with_episodes,
            "--session-id", "session_20260309_100000",
        ])
        captured = capsys.readouterr()
        assert "3 episodes" in captured.out

    def test_cli_list_episodes_latest(self, memory_with_episodes, capsys):
        episode_memory.main([
            "list-episodes",
            "--memory-dir", memory_with_episodes,
        ])
        captured = capsys.readouterr()
        assert "episodes" in captured.out

    def test_cli_show(self, tmp_memory_dir, capsys):
        # First record an episode
        result = episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="decision",
            summary="CLI show test",
            session_id="session_cli_show",
        )
        ep_id = result.split(":")[1].strip().split(" ")[0]

        episode_memory.main([
            "show",
            "--memory-dir", tmp_memory_dir,
            "--episode-id", ep_id,
        ])
        captured = capsys.readouterr()
        assert "CLI show test" in captured.out

    def test_cli_no_command(self):
        with pytest.raises(SystemExit):
            episode_memory.main([])

    def test_cli_record_missing_required(self):
        with pytest.raises(SystemExit):
            episode_memory.main(["record", "--memory-dir", "/tmp"])

    def test_cli_record_invalid_type(self):
        with pytest.raises(SystemExit):
            episode_memory.main([
                "record",
                "--memory-dir", "/tmp",
                "--type", "invalid_type",
                "--summary", "Should fail",
            ])

    def test_cli_show_missing_episode_id(self):
        with pytest.raises(SystemExit):
            episode_memory.main([
                "show",
                "--memory-dir", "/tmp",
            ])

    def test_cli_list_sessions_missing_memory_dir(self):
        with pytest.raises(SystemExit):
            episode_memory.main(["list-sessions"])


# ===== Session file structure tests =====

class TestSessionFileStructure:
    """Tests for session file JSON structure."""

    def test_session_file_has_required_fields(self, tmp_memory_dir):
        sid = "session_20260309_structure"
        episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="observation",
            summary="Structure test",
            session_id=sid,
        )
        session_file = episode_memory.get_episodes_path(tmp_memory_dir) / f"{sid}.json"
        data = json.loads(session_file.read_text(encoding="utf-8"))

        assert "session_id" in data
        assert "created_at" in data
        assert "episodes" in data
        assert isinstance(data["episodes"], list)

    def test_episode_has_required_fields(self, tmp_memory_dir):
        sid = "session_20260309_ep_structure"
        episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="user_request",
            summary="Episode structure test",
            user_texts=["Hello"],
            tags=["test"],
            session_id=sid,
        )
        session_file = episode_memory.get_episodes_path(tmp_memory_dir) / f"{sid}.json"
        data = json.loads(session_file.read_text(encoding="utf-8"))
        ep = data["episodes"][0]

        required_fields = [
            "episode_id", "episode_type", "summary",
            "user_utterances", "tags", "timestamp", "session_id"
        ]
        for field in required_fields:
            assert field in ep, f"Missing field: {field}"

    def test_utterance_has_required_fields(self, tmp_memory_dir):
        sid = "session_20260309_utt_structure"
        episode_memory.record_episode(
            memory_dir=tmp_memory_dir,
            episode_type="feedback",
            summary="Utterance structure test",
            user_texts=["Test text"],
            session_id=sid,
        )
        session_file = episode_memory.get_episodes_path(tmp_memory_dir) / f"{sid}.json"
        data = json.loads(session_file.read_text(encoding="utf-8"))
        utt = data["episodes"][0]["user_utterances"][0]

        assert "text" in utt
        assert "role" in utt
        assert "truncated" in utt
        assert utt["role"] == "user"
        assert isinstance(utt["truncated"], bool)


# ===== Helper function tests =====

class TestHelpers:
    """Tests for helper functions."""

    def test_generate_episode_id_length(self):
        ep_id = episode_memory._generate_episode_id()
        assert len(ep_id) == 12

    def test_generate_episode_id_hex(self):
        ep_id = episode_memory._generate_episode_id()
        assert all(c in "0123456789abcdef" for c in ep_id)

    def test_generate_episode_id_unique(self):
        ids = {episode_memory._generate_episode_id() for _ in range(100)}
        assert len(ids) == 100

    def test_session_id_from_timestamp(self):
        dt = datetime(2026, 3, 9, 14, 30, 22, tzinfo=timezone.utc)
        sid = episode_memory._session_id_from_timestamp(dt)
        assert sid == "session_20260309_143022"

    def test_session_filename(self):
        fn = episode_memory._session_filename("session_20260309_143022")
        assert fn == "session_20260309_143022.json"

    def test_build_utterance_normal(self):
        utt = episode_memory._build_utterance("Hello world")
        assert utt["text"] == "Hello world"
        assert utt["role"] == "user"
        assert utt["truncated"] is False

    def test_build_utterance_truncated(self):
        long_text = "X" * 3000
        utt = episode_memory._build_utterance(long_text, size_cap=100)
        assert utt["truncated"] is True
        assert len(utt["text"].encode("utf-8")) <= 100

    def test_build_utterance_exact_cap(self):
        # Text exactly at the cap
        text = "A" * 2000
        utt = episode_memory._build_utterance(text, size_cap=2000)
        assert utt["truncated"] is False
        assert utt["text"] == text

    def test_build_utterance_one_over_cap(self):
        text = "A" * 2001
        utt = episode_memory._build_utterance(text, size_cap=2000)
        assert utt["truncated"] is True

    def test_load_session_file_valid(self, tmp_memory_dir):
        episodes_dir = episode_memory._ensure_episodes_dir(tmp_memory_dir)
        data = {"session_id": "test", "created_at": "2026-03-09T00:00:00Z", "episodes": []}
        filepath = episodes_dir / "test.json"
        filepath.write_text(json.dumps(data), encoding="utf-8")
        loaded = episode_memory._load_session_file(filepath)
        assert loaded is not None
        assert loaded["session_id"] == "test"

    def test_load_session_file_invalid_json(self, tmp_memory_dir):
        episodes_dir = episode_memory._ensure_episodes_dir(tmp_memory_dir)
        filepath = episodes_dir / "bad.json"
        filepath.write_text("NOT JSON", encoding="utf-8")
        loaded = episode_memory._load_session_file(filepath)
        assert loaded is None

    def test_load_session_file_missing_fields(self, tmp_memory_dir):
        episodes_dir = episode_memory._ensure_episodes_dir(tmp_memory_dir)
        filepath = episodes_dir / "incomplete.json"
        filepath.write_text('{"some_field": "value"}', encoding="utf-8")
        loaded = episode_memory._load_session_file(filepath)
        assert loaded is None

    def test_load_session_file_nonexistent(self, tmp_memory_dir):
        filepath = Path(tmp_memory_dir) / "nonexistent.json"
        loaded = episode_memory._load_session_file(filepath)
        assert loaded is None

    def test_list_session_files_sorted(self, tmp_memory_dir):
        episodes_dir = episode_memory._ensure_episodes_dir(tmp_memory_dir)
        for i in range(3):
            filepath = episodes_dir / f"session_2026030{i + 1}_000000.json"
            data = {
                "session_id": f"session_2026030{i + 1}_000000",
                "created_at": f"2026-03-0{i + 1}T00:00:00Z",
                "episodes": [],
            }
            filepath.write_text(json.dumps(data), encoding="utf-8")
            time.sleep(0.05)

        files = episode_memory._list_session_files(episodes_dir)
        assert len(files) == 3
        # Should be sorted by mtime (oldest first)
        assert files[0].stem == "session_20260301_000000"

    def test_list_session_files_ignores_non_session(self, tmp_memory_dir):
        episodes_dir = episode_memory._ensure_episodes_dir(tmp_memory_dir)

        # Create a session file
        session_data = {"session_id": "session_test", "created_at": "2026-03-09T00:00:00Z", "episodes": []}
        (episodes_dir / "session_test.json").write_text(json.dumps(session_data), encoding="utf-8")

        # Create a non-session file
        (episodes_dir / "other_file.json").write_text("{}", encoding="utf-8")
        (episodes_dir / "readme.txt").write_text("test", encoding="utf-8")

        files = episode_memory._list_session_files(episodes_dir)
        assert len(files) == 1
        assert files[0].name == "session_test.json"
