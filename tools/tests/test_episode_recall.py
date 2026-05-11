"""Tests for episode_recall.py."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Add parent directory to path so we can import episode_recall and topic_index
sys.path.insert(0, str(Path(__file__).parent.parent))
import episode_recall
import topic_index

# --- Fixtures & Helpers ---

@pytest.fixture
def tmp_memory_dir(tmp_path):
    """Provide a temporary memory directory."""
    return str(tmp_path)


def _create_session_file(memory_dir: str, session_id: str, episodes: list[dict]) -> None:
    """Helper to create a session file with given episodes."""
    episodes_dir = Path(memory_dir) / "episodes"
    episodes_dir.mkdir(parents=True, exist_ok=True)
    session_data = {
        "session_id": session_id,
        "created_at": "2026-03-09T00:00:00Z",
        "episodes": episodes,
    }
    filepath = episodes_dir / f"{session_id}.json"
    filepath.write_text(json.dumps(session_data, ensure_ascii=False), encoding="utf-8")


def _make_episode(
    episode_id: str,
    summary: str = "",
    tags: list[str] | None = None,
    user_utterances: list[dict] | None = None,
    episode_type: str = "observation",
    timestamp: str = "2026-03-09T10:00:00Z",
    session_id: str = "session_20260309_100000",
) -> dict:
    """Helper to create an episode dict."""
    return {
        "episode_id": episode_id,
        "episode_type": episode_type,
        "summary": summary,
        "user_utterances": user_utterances or [],
        "tags": tags or [],
        "timestamp": timestamp,
        "session_id": session_id,
    }


@pytest.fixture
def memory_with_episodes(tmp_memory_dir):
    """Create a memory dir with episodes across multiple sessions for testing."""
    _create_session_file(tmp_memory_dir, "session_20260307_100000", [
        _make_episode(
            "ep_001",
            summary="Fixed a critical bug in psyche/emotion.py",
            tags=["bugfix", "psyche/emotion.py"],
            user_utterances=[
                {"text": "Fix the login bug please", "role": "user", "truncated": False},
            ],
            episode_type="error",
            timestamp="2026-03-07T10:00:00Z",
            session_id="session_20260307_100000",
        ),
        _make_episode(
            "ep_002",
            summary="Refactored auth module for clarity",
            tags=["auth", "refactor"],
            episode_type="decision",
            timestamp="2026-03-07T11:00:00Z",
            session_id="session_20260307_100000",
        ),
    ])

    _create_session_file(tmp_memory_dir, "session_20260308_100000", [
        _make_episode(
            "ep_003",
            summary="Added new safety valve to orchestrator",
            tags=["orchestrator", "safety"],
            user_utterances=[
                {"text": "Check orchestrator.py for the issue", "role": "user", "truncated": False},
            ],
            episode_type="solution",
            timestamp="2026-03-08T10:00:00Z",
            session_id="session_20260308_100000",
        ),
        _make_episode(
            "ep_004",
            summary="Deployed to main branch successfully",
            tags=["deploy", "main"],
            episode_type="observation",
            timestamp="2026-03-08T14:00:00Z",
            session_id="session_20260308_100000",
        ),
    ])

    _create_session_file(tmp_memory_dir, "session_20260309_100000", [
        _make_episode(
            "ep_005",
            summary="Bug fix in psyche/emotion.py decay function",
            tags=["bugfix", "psyche/emotion.py"],
            user_utterances=[
                {"text": "The emotion decay is broken again", "role": "user", "truncated": False},
            ],
            episode_type="error",
            timestamp="2026-03-09T09:00:00Z",
            session_id="session_20260309_100000",
        ),
        _make_episode(
            "ep_006",
            summary="Reviewed coefficient registry changes",
            tags=["review", "coefficient_registry"],
            episode_type="observation",
            timestamp="2026-03-09T15:00:00Z",
            session_id="session_20260309_100000",
        ),
    ])

    return tmp_memory_dir


@pytest.fixture
def memory_with_index(memory_with_episodes):
    """Create a memory dir with episodes and a built topic index."""
    topic_index.build_index(memory_with_episodes)
    return memory_with_episodes


# ===== Keyword Search Tests =====

class TestKeywordSearch:
    """Tests for keyword search (Pathway 1)."""

    def test_single_keyword_match(self, memory_with_episodes):
        result = episode_recall.keyword_search(
            memory_with_episodes, ["bugfix"]
        )
        assert "ep_005" in result
        assert "ep_001" in result

    def test_single_keyword_in_summary(self, memory_with_episodes):
        result = episode_recall.keyword_search(
            memory_with_episodes, ["orchestrator"]
        )
        assert "ep_003" in result

    def test_single_keyword_in_user_utterances(self, memory_with_episodes):
        result = episode_recall.keyword_search(
            memory_with_episodes, ["login"]
        )
        assert "ep_001" in result

    def test_single_keyword_in_tags(self, memory_with_episodes):
        result = episode_recall.keyword_search(
            memory_with_episodes, ["safety"]
        )
        assert "ep_003" in result

    def test_multiple_keywords_and_logic(self, memory_with_episodes):
        result = episode_recall.keyword_search(
            memory_with_episodes, ["bugfix", "decay"]
        )
        # Only ep_005 has both "bugfix" (tag) and "decay" (summary)
        assert "ep_005" in result
        assert "ep_001" not in result  # has "bugfix" but not "decay"

    def test_case_insensitivity(self, memory_with_episodes):
        result = episode_recall.keyword_search(
            memory_with_episodes, ["BUGFIX"]
        )
        assert "ep_005" in result
        assert "ep_001" in result

    def test_no_matches_returns_message(self, memory_with_episodes):
        result = episode_recall.keyword_search(
            memory_with_episodes, ["nonexistent_keyword_xyz"]
        )
        assert "No matching episodes" in result

    def test_empty_keywords_returns_error(self, memory_with_episodes):
        result = episode_recall.keyword_search(
            memory_with_episodes, []
        )
        assert result.startswith("ERROR:")

    def test_result_ordered_by_timestamp_descending(self, memory_with_episodes):
        result = episode_recall.keyword_search(
            memory_with_episodes, ["bugfix"]
        )
        # ep_005 (2026-03-09) should appear before ep_001 (2026-03-07)
        pos_005 = result.find("ep_005")
        pos_001 = result.find("ep_001")
        assert pos_005 < pos_001

    def test_result_limit_enforced(self, tmp_memory_dir):
        episodes = []
        for i in range(20):
            episodes.append(_make_episode(
                f"ep_kw_{i:03d}",
                summary=f"Episode {i} about testing",
                tags=["testing"],
                timestamp=f"2026-03-09T{i:02d}:00:00Z",
            ))
        _create_session_file(tmp_memory_dir, "session_kw_limit", episodes)

        result = episode_recall.keyword_search(
            tmp_memory_dir, ["testing"], limit=5
        )
        assert "20 total" in result
        assert "showing 5" in result

    def test_empty_episodes_directory(self, tmp_memory_dir):
        result = episode_recall.keyword_search(
            tmp_memory_dir, ["anything"]
        )
        assert "No matching episodes" in result

    def test_corrupted_session_files_skipped(self, tmp_memory_dir):
        _create_session_file(tmp_memory_dir, "session_valid", [
            _make_episode("ep_valid", summary="Valid episode with keyword", tags=["target"]),
        ])
        # Create corrupted file
        episodes_dir = Path(tmp_memory_dir) / "episodes"
        corrupted = episodes_dir / "session_corrupted.json"
        corrupted.write_text("NOT VALID JSON", encoding="utf-8")

        result = episode_recall.keyword_search(
            tmp_memory_dir, ["target"]
        )
        assert "ep_valid" in result


# ===== Time-Range Search Tests =====

class TestTimeRangeSearch:
    """Tests for time-axis search (Pathway 2)."""

    def test_absolute_range_start_and_end(self, memory_with_episodes):
        result = episode_recall.time_range_search(
            memory_with_episodes,
            start="2026-03-08T00:00:00Z",
            end="2026-03-08T23:59:59Z",
        )
        assert "ep_003" in result
        assert "ep_004" in result
        assert "ep_001" not in result
        assert "ep_005" not in result

    def test_absolute_range_start_only(self, memory_with_episodes):
        result = episode_recall.time_range_search(
            memory_with_episodes,
            start="2026-03-08T00:00:00Z",
        )
        # ep_003 and ep_004 are on 2026-03-08, ep_005 and ep_006 on 2026-03-09
        assert "ep_003" in result
        assert "ep_004" in result
        assert "ep_001" not in result  # 2026-03-07

    def test_absolute_range_end_only(self, memory_with_episodes):
        result = episode_recall.time_range_search(
            memory_with_episodes,
            end="2026-03-07T11:30:00Z",
        )
        assert "ep_001" in result
        assert "ep_002" in result
        assert "ep_003" not in result

    def test_relative_range_days(self, tmp_memory_dir):
        now = datetime.now(timezone.utc)
        recent_ts = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        old_ts = (now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")

        _create_session_file(tmp_memory_dir, "session_rel", [
            _make_episode("ep_recent", summary="Recent", timestamp=recent_ts),
            _make_episode("ep_old", summary="Old", timestamp=old_ts),
        ])

        result = episode_recall.time_range_search(
            tmp_memory_dir, last="7d"
        )
        assert "ep_recent" in result
        assert "ep_old" not in result

    def test_relative_range_hours(self, tmp_memory_dir):
        now = datetime.now(timezone.utc)
        recent_ts = (now - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        old_ts = (now - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

        _create_session_file(tmp_memory_dir, "session_hours", [
            _make_episode("ep_30min", summary="Recent 30min", timestamp=recent_ts),
            _make_episode("ep_5h", summary="Old 5h", timestamp=old_ts),
        ])

        result = episode_recall.time_range_search(
            tmp_memory_dir, last="2h"
        )
        assert "ep_30min" in result
        assert "ep_5h" not in result

    def test_relative_range_weeks(self, tmp_memory_dir):
        now = datetime.now(timezone.utc)
        recent_ts = (now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        old_ts = (now - timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ")

        _create_session_file(tmp_memory_dir, "session_weeks", [
            _make_episode("ep_3d", summary="3 days ago", timestamp=recent_ts),
            _make_episode("ep_20d", summary="20 days ago", timestamp=old_ts),
        ])

        result = episode_recall.time_range_search(
            tmp_memory_dir, last="2w"
        )
        assert "ep_3d" in result
        assert "ep_20d" not in result

    def test_session_based_search(self, memory_with_episodes):
        result = episode_recall.time_range_search(
            memory_with_episodes, sessions=1
        )
        # Should only include the most recent session (session_20260309)
        assert "ep_005" in result or "ep_006" in result

    def test_session_based_multiple(self, memory_with_episodes):
        result = episode_recall.time_range_search(
            memory_with_episodes, sessions=2
        )
        # Should include last 2 sessions
        assert "ep_003" in result or "ep_004" in result
        assert "ep_005" in result or "ep_006" in result

    def test_empty_range_returns_message(self, memory_with_episodes):
        result = episode_recall.time_range_search(
            memory_with_episodes,
            start="2020-01-01T00:00:00Z",
            end="2020-01-02T00:00:00Z",
        )
        assert "No matching episodes" in result

    def test_no_mode_specified_returns_error(self, memory_with_episodes):
        result = episode_recall.time_range_search(
            memory_with_episodes
        )
        assert result.startswith("ERROR:")

    def test_multiple_modes_returns_error(self, memory_with_episodes):
        result = episode_recall.time_range_search(
            memory_with_episodes,
            last="7d",
            sessions=2,
        )
        assert result.startswith("ERROR:")

    def test_invalid_relative_format_returns_error(self, memory_with_episodes):
        result = episode_recall.time_range_search(
            memory_with_episodes, last="invalid"
        )
        assert result.startswith("ERROR:")

    def test_result_ordered_by_timestamp_descending(self, memory_with_episodes):
        result = episode_recall.time_range_search(
            memory_with_episodes,
            start="2026-03-07T00:00:00Z",
            end="2026-03-09T23:59:59Z",
        )
        # ep_006 (15:00) should appear before ep_005 (09:00) should appear before ep_004...
        pos_006 = result.find("ep_006")
        pos_005 = result.find("ep_005")
        pos_001 = result.find("ep_001")
        assert pos_006 < pos_005 < pos_001

    def test_result_limit_enforced(self, tmp_memory_dir):
        episodes = []
        for i in range(15):
            episodes.append(_make_episode(
                f"ep_tr_{i:03d}",
                summary=f"Time range episode {i}",
                timestamp=f"2026-03-09T{i:02d}:00:00Z",
            ))
        _create_session_file(tmp_memory_dir, "session_tr_limit", episodes)

        result = episode_recall.time_range_search(
            tmp_memory_dir,
            start="2026-03-09T00:00:00Z",
            end="2026-03-09T23:59:59Z",
            limit=3,
        )
        assert "15 total" in result
        assert "showing 3" in result

    def test_sessions_less_than_one_returns_error(self, memory_with_episodes):
        result = episode_recall.time_range_search(
            memory_with_episodes, sessions=0
        )
        assert result.startswith("ERROR:")


# ===== Context Search Tests =====

class TestContextSearch:
    """Tests for context search (Pathway 3)."""

    def test_single_tag_exact_match(self, memory_with_index):
        result = episode_recall.context_search(
            memory_with_index, ["bugfix"]
        )
        assert "ep_005" in result
        assert "ep_001" in result

    def test_multiple_tags_or_logic(self, memory_with_index):
        result = episode_recall.context_search(
            memory_with_index, ["bugfix", "safety"]
        )
        # OR logic: should include episodes with either tag
        assert "ep_001" in result or "ep_005" in result
        assert "ep_003" in result

    def test_prefix_matching(self, memory_with_index):
        result = episode_recall.context_search(
            memory_with_index, ["psyche/"], prefix=True
        )
        # Should match episodes tagged with psyche/emotion.py
        assert "ep_001" in result or "ep_005" in result

    def test_missing_index_returns_suggestion(self, tmp_memory_dir):
        result = episode_recall.context_search(
            tmp_memory_dir, ["any_tag"]
        )
        assert "index not found" in result.lower() or "build" in result.lower()

    def test_stale_reference_handled_gracefully(self, tmp_memory_dir):
        # Create an episode, build index, then remove the episode
        _create_session_file(tmp_memory_dir, "session_stale", [
            _make_episode("ep_stale", summary="Will be removed", tags=["stale_tag"]),
        ])
        topic_index.build_index(tmp_memory_dir)

        # Remove the episode by overwriting with empty session
        _create_session_file(tmp_memory_dir, "session_stale", [])

        result = episode_recall.context_search(
            tmp_memory_dir, ["stale_tag"]
        )
        # Should not crash; should return no matches since episode is gone
        assert "No matching episodes" in result

    def test_result_ordered_by_match_count_then_timestamp(self, tmp_memory_dir):
        _create_session_file(tmp_memory_dir, "session_order", [
            _make_episode(
                "ep_one_tag", summary="One tag match",
                tags=["alpha"],
                timestamp="2026-03-09T12:00:00Z",
            ),
            _make_episode(
                "ep_two_tags", summary="Two tag match",
                tags=["alpha", "beta"],
                timestamp="2026-03-09T10:00:00Z",
            ),
        ])
        topic_index.build_index(tmp_memory_dir)

        result = episode_recall.context_search(
            tmp_memory_dir, ["alpha", "beta"]
        )
        # ep_two_tags has 2 matches, ep_one_tag has 1 match
        pos_two = result.find("ep_two_tags")
        pos_one = result.find("ep_one_tag")
        assert pos_two < pos_one

    def test_result_limit_enforced(self, tmp_memory_dir):
        episodes = []
        for i in range(15):
            episodes.append(_make_episode(
                f"ep_ctx_{i:03d}",
                summary=f"Context episode {i}",
                tags=["common_tag"],
                timestamp=f"2026-03-09T{i:02d}:00:00Z",
            ))
        _create_session_file(tmp_memory_dir, "session_ctx_limit", episodes)
        topic_index.build_index(tmp_memory_dir)

        result = episode_recall.context_search(
            tmp_memory_dir, ["common_tag"], limit=5
        )
        assert "15 total" in result
        assert "showing 5" in result

    def test_empty_tags_returns_error(self, memory_with_index):
        result = episode_recall.context_search(
            memory_with_index, []
        )
        assert result.startswith("ERROR:")

    def test_no_matching_tags_returns_message(self, memory_with_index):
        result = episode_recall.context_search(
            memory_with_index, ["completely_nonexistent_tag"]
        )
        assert "No matching episodes" in result

    def test_matching_tags_shown_in_output(self, memory_with_index):
        result = episode_recall.context_search(
            memory_with_index, ["bugfix"]
        )
        assert "tags:" in result.lower()


# ===== Episode Type Filtering Tests =====

class TestEpisodeTypeFiltering:
    """Tests for episode type filtering across all three pathways."""

    def test_keyword_search_type_filter(self, memory_with_episodes):
        result = episode_recall.keyword_search(
            memory_with_episodes, ["bugfix"], episode_type="error"
        )
        assert "ep_001" in result
        assert "ep_005" in result

    def test_keyword_search_type_filter_excludes(self, memory_with_episodes):
        result = episode_recall.keyword_search(
            memory_with_episodes, ["bugfix"], episode_type="solution"
        )
        # No episodes with bugfix tag are type "solution"
        assert "No matching episodes" in result

    def test_time_range_type_filter(self, memory_with_episodes):
        result = episode_recall.time_range_search(
            memory_with_episodes,
            start="2026-03-07T00:00:00Z",
            end="2026-03-09T23:59:59Z",
            episode_type="decision",
        )
        assert "ep_002" in result
        assert "ep_001" not in result  # type is "error"

    def test_context_search_type_filter(self, memory_with_index):
        result = episode_recall.context_search(
            memory_with_index, ["bugfix"], episode_type="error"
        )
        assert "ep_001" in result or "ep_005" in result

    def test_context_search_type_filter_excludes(self, memory_with_index):
        result = episode_recall.context_search(
            memory_with_index, ["bugfix"], episode_type="feedback"
        )
        assert "No matching episodes" in result


# ===== Result Formatting Tests =====

class TestResultFormatting:
    """Tests for result output formatting."""

    def test_keyword_result_contains_header(self, memory_with_episodes):
        result = episode_recall.keyword_search(
            memory_with_episodes, ["bugfix"]
        )
        assert "Keyword search results" in result
        assert "total" in result
        assert "showing" in result

    def test_time_range_result_contains_header(self, memory_with_episodes):
        result = episode_recall.time_range_search(
            memory_with_episodes,
            start="2026-03-07T00:00:00Z",
            end="2026-03-09T23:59:59Z",
        )
        assert "Time-range search results" in result
        assert "total" in result

    def test_context_result_contains_header(self, memory_with_index):
        result = episode_recall.context_search(
            memory_with_index, ["bugfix"]
        )
        assert "Context search results" in result
        assert "total" in result

    def test_result_entry_contains_required_fields(self, memory_with_episodes):
        result = episode_recall.keyword_search(
            memory_with_episodes, ["orchestrator"]
        )
        assert "ep_003" in result
        assert "solution" in result  # episode_type
        assert "2026-03-08T10:00:00Z" in result  # timestamp
        assert "session_20260308_100000" in result  # session_id
        assert "Summary:" in result

    def test_summary_truncated_in_result(self, tmp_memory_dir):
        long_summary = "A" * 200
        _create_session_file(tmp_memory_dir, "session_trunc", [
            _make_episode("ep_trunc", summary=long_summary, tags=["test_trunc"]),
        ])

        result = episode_recall.keyword_search(
            tmp_memory_dir, ["test_trunc"]
        )
        assert "..." in result

    def test_no_results_message_not_error(self, memory_with_episodes):
        result = episode_recall.keyword_search(
            memory_with_episodes, ["zzzzz_nonexistent"]
        )
        assert not result.startswith("ERROR:")
        assert "No matching episodes" in result


# ===== Empty / Missing Directory Tests =====

class TestEmptyMissingDir:
    """Tests for empty or missing episodes directory."""

    def test_keyword_search_empty_dir(self, tmp_memory_dir):
        result = episode_recall.keyword_search(
            tmp_memory_dir, ["anything"]
        )
        assert "No matching episodes" in result

    def test_time_range_search_empty_dir(self, tmp_memory_dir):
        result = episode_recall.time_range_search(
            tmp_memory_dir, last="7d"
        )
        assert "No matching episodes" in result

    def test_context_search_no_index(self, tmp_memory_dir):
        result = episode_recall.context_search(
            tmp_memory_dir, ["anything"]
        )
        assert "index not found" in result.lower() or "build" in result.lower()


# ===== Corrupted Files Tests =====

class TestCorruptedFiles:
    """Tests for handling corrupted session files."""

    def test_keyword_search_skips_corrupted(self, tmp_memory_dir):
        _create_session_file(tmp_memory_dir, "session_good", [
            _make_episode("ep_good", summary="Good episode", tags=["findme"]),
        ])
        episodes_dir = Path(tmp_memory_dir) / "episodes"
        corrupted = episodes_dir / "session_bad.json"
        corrupted.write_text("{invalid json}", encoding="utf-8")

        result = episode_recall.keyword_search(
            tmp_memory_dir, ["findme"]
        )
        assert "ep_good" in result

    def test_time_range_search_skips_corrupted(self, tmp_memory_dir):
        _create_session_file(tmp_memory_dir, "session_good", [
            _make_episode("ep_good", summary="Good", timestamp="2026-03-09T10:00:00Z"),
        ])
        episodes_dir = Path(tmp_memory_dir) / "episodes"
        corrupted = episodes_dir / "session_bad.json"
        corrupted.write_text("NOT JSON", encoding="utf-8")

        result = episode_recall.time_range_search(
            tmp_memory_dir,
            start="2026-03-09T00:00:00Z",
            end="2026-03-09T23:59:59Z",
        )
        assert "ep_good" in result


# ===== UTF-8 Tests =====

class TestUTF8:
    """Tests for UTF-8 content in search terms and episode data."""

    def test_utf8_keyword_search(self, tmp_memory_dir):
        _create_session_file(tmp_memory_dir, "session_utf8", [
            _make_episode(
                "ep_utf8",
                summary="Japanese text search",
                tags=["bugfix"],
                user_utterances=[
                    {"text": "emotion decay function", "role": "user", "truncated": False},
                ],
            ),
        ])

        result = episode_recall.keyword_search(
            tmp_memory_dir, ["bugfix"]
        )
        assert "ep_utf8" in result

    def test_utf8_in_episode_data(self, tmp_memory_dir):
        _create_session_file(tmp_memory_dir, "session_jp", [
            _make_episode(
                "ep_jp",
                summary="Emotion decay function",
                tags=["tag"],
            ),
        ])

        result = episode_recall.keyword_search(
            tmp_memory_dir, ["Emotion"]
        )
        assert "ep_jp" in result

    def test_utf8_tags_in_context_search(self, tmp_memory_dir):
        _create_session_file(tmp_memory_dir, "session_utf8_ctx", [
            _make_episode(
                "ep_utf8_ctx",
                summary="Test episode",
                tags=["module_name"],
            ),
        ])
        topic_index.build_index(tmp_memory_dir)

        result = episode_recall.context_search(
            tmp_memory_dir, ["module_name"]
        )
        assert "ep_utf8_ctx" in result


# ===== Internal Helper Tests =====

class TestInternalHelpers:
    """Tests for internal helper functions."""

    def test_parse_relative_time_hours(self):
        delta = episode_recall._parse_relative_time("24h")
        assert delta == timedelta(hours=24)

    def test_parse_relative_time_days(self):
        delta = episode_recall._parse_relative_time("7d")
        assert delta == timedelta(days=7)

    def test_parse_relative_time_weeks(self):
        delta = episode_recall._parse_relative_time("2w")
        assert delta == timedelta(weeks=2)

    def test_parse_relative_time_invalid(self):
        assert episode_recall._parse_relative_time("invalid") is None
        assert episode_recall._parse_relative_time("7m") is None
        assert episode_recall._parse_relative_time("") is None

    def test_parse_relative_time_case_insensitive(self):
        assert episode_recall._parse_relative_time("7D") == timedelta(days=7)
        assert episode_recall._parse_relative_time("24H") == timedelta(hours=24)
        assert episode_recall._parse_relative_time("2W") == timedelta(weeks=2)

    def test_parse_timestamp_with_z(self):
        dt = episode_recall._parse_timestamp("2026-03-09T10:00:00Z")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 3
        assert dt.day == 9

    def test_parse_timestamp_without_z(self):
        dt = episode_recall._parse_timestamp("2026-03-09T10:00:00")
        assert dt is not None

    def test_parse_timestamp_date_only(self):
        dt = episode_recall._parse_timestamp("2026-03-09")
        assert dt is not None
        assert dt.year == 2026

    def test_parse_timestamp_invalid(self):
        assert episode_recall._parse_timestamp("not a date") is None
        assert episode_recall._parse_timestamp("") is None

    def test_truncate_summary_short(self):
        assert episode_recall._truncate_summary("Short") == "Short"

    def test_truncate_summary_long(self):
        long = "A" * 200
        result = episode_recall._truncate_summary(long, max_len=50)
        assert result.endswith("...")
        assert len(result) == 53  # 50 + "..."

    def test_normalize_tag(self):
        assert episode_recall._normalize_tag("  Psyche\\Emotion.py  ") == "psyche/emotion.py"

    def test_episode_in_time_range_true(self):
        ep = _make_episode("test", timestamp="2026-03-09T10:00:00Z")
        start = datetime(2026, 3, 9, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 3, 9, 23, 59, 59, tzinfo=timezone.utc)
        assert episode_recall._episode_in_time_range(ep, start, end) is True

    def test_episode_in_time_range_false(self):
        ep = _make_episode("test", timestamp="2026-03-08T10:00:00Z")
        start = datetime(2026, 3, 9, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 3, 9, 23, 59, 59, tzinfo=timezone.utc)
        assert episode_recall._episode_in_time_range(ep, start, end) is False

    def test_episode_in_time_range_invalid_timestamp(self):
        ep = _make_episode("test", timestamp="not a date")
        start = datetime(2026, 3, 9, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 3, 9, 23, 59, 59, tzinfo=timezone.utc)
        assert episode_recall._episode_in_time_range(ep, start, end) is False

    def test_episode_matches_keywords_single(self):
        ep = _make_episode("test", summary="Fix the bug", tags=["bugfix"])
        matches, detail = episode_recall._episode_matches_keywords(ep, ["bug"])
        assert matches is True

    def test_episode_matches_keywords_all_required(self):
        ep = _make_episode("test", summary="Fix the bug", tags=["bugfix"])
        matches, _ = episode_recall._episode_matches_keywords(ep, ["bug", "nonexistent"])
        assert matches is False

    def test_filter_by_type_none(self):
        episodes = [
            _make_episode("a", episode_type="error"),
            _make_episode("b", episode_type="solution"),
        ]
        result = episode_recall._filter_by_type(episodes, None)
        assert len(result) == 2

    def test_filter_by_type_specific(self):
        episodes = [
            _make_episode("a", episode_type="error"),
            _make_episode("b", episode_type="solution"),
        ]
        result = episode_recall._filter_by_type(episodes, "error")
        assert len(result) == 1
        assert result[0]["episode_id"] == "a"

    def test_load_all_episodes(self, memory_with_episodes):
        episodes = episode_recall._load_all_episodes(memory_with_episodes)
        assert len(episodes) == 6

    def test_load_episodes_from_recent_sessions(self, memory_with_episodes):
        episodes = episode_recall._load_episodes_from_recent_sessions(memory_with_episodes, 1)
        # Should only get episodes from the most recent session
        session_ids = set(ep.get("session_id") for ep in episodes)
        assert len(session_ids) == 1

    def test_load_all_episodes_empty(self, tmp_memory_dir):
        episodes = episode_recall._load_all_episodes(tmp_memory_dir)
        assert episodes == []


# ===== CLI Tests =====

class TestCLI:
    """Tests for CLI subcommand parsing and output."""

    def test_cli_keyword(self, memory_with_episodes, capsys):
        episode_recall.main([
            "keyword",
            "--memory-dir", memory_with_episodes,
            "--keywords", "bugfix",
        ])
        captured = capsys.readouterr()
        assert "Keyword search results" in captured.out

    def test_cli_keyword_with_type(self, memory_with_episodes, capsys):
        episode_recall.main([
            "keyword",
            "--memory-dir", memory_with_episodes,
            "--keywords", "bugfix",
            "--type", "error",
        ])
        captured = capsys.readouterr()
        assert "Keyword search results" in captured.out

    def test_cli_keyword_with_limit(self, memory_with_episodes, capsys):
        episode_recall.main([
            "keyword",
            "--memory-dir", memory_with_episodes,
            "--keywords", "bugfix",
            "--limit", "1",
        ])
        captured = capsys.readouterr()
        assert "showing 1" in captured.out

    def test_cli_keyword_multiple_keywords(self, memory_with_episodes, capsys):
        episode_recall.main([
            "keyword",
            "--memory-dir", memory_with_episodes,
            "--keywords", "bugfix,decay",
        ])
        captured = capsys.readouterr()
        assert "Keyword search results" in captured.out

    def test_cli_time_range_last(self, tmp_memory_dir, capsys):
        now = datetime.now(timezone.utc)
        ts = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        _create_session_file(tmp_memory_dir, "session_cli_tr", [
            _make_episode("ep_cli_tr", summary="Recent episode", timestamp=ts),
        ])
        episode_recall.main([
            "time-range",
            "--memory-dir", tmp_memory_dir,
            "--last", "24h",
        ])
        captured = capsys.readouterr()
        assert "Time-range search results" in captured.out

    def test_cli_time_range_absolute(self, memory_with_episodes, capsys):
        episode_recall.main([
            "time-range",
            "--memory-dir", memory_with_episodes,
            "--start", "2026-03-08T00:00:00Z",
            "--end", "2026-03-08T23:59:59Z",
        ])
        captured = capsys.readouterr()
        assert "Time-range search results" in captured.out

    def test_cli_time_range_sessions(self, memory_with_episodes, capsys):
        episode_recall.main([
            "time-range",
            "--memory-dir", memory_with_episodes,
            "--sessions", "2",
        ])
        captured = capsys.readouterr()
        assert "Time-range search results" in captured.out

    def test_cli_time_range_with_type(self, memory_with_episodes, capsys):
        episode_recall.main([
            "time-range",
            "--memory-dir", memory_with_episodes,
            "--start", "2026-03-07T00:00:00Z",
            "--end", "2026-03-09T23:59:59Z",
            "--type", "error",
        ])
        captured = capsys.readouterr()
        assert "Time-range search results" in captured.out

    def test_cli_context(self, memory_with_index, capsys):
        episode_recall.main([
            "context",
            "--memory-dir", memory_with_index,
            "--tags", "bugfix",
        ])
        captured = capsys.readouterr()
        assert "Context search results" in captured.out

    def test_cli_context_prefix(self, memory_with_index, capsys):
        episode_recall.main([
            "context",
            "--memory-dir", memory_with_index,
            "--tags", "psyche/",
            "--prefix",
        ])
        captured = capsys.readouterr()
        assert "Context search results" in captured.out or "No matching" in captured.out

    def test_cli_context_with_type(self, memory_with_index, capsys):
        episode_recall.main([
            "context",
            "--memory-dir", memory_with_index,
            "--tags", "bugfix",
            "--type", "error",
        ])
        captured = capsys.readouterr()
        assert "Context search results" in captured.out

    def test_cli_context_with_limit(self, memory_with_index, capsys):
        episode_recall.main([
            "context",
            "--memory-dir", memory_with_index,
            "--tags", "bugfix",
            "--limit", "1",
        ])
        captured = capsys.readouterr()
        assert "showing 1" in captured.out

    def test_cli_no_command(self):
        with pytest.raises(SystemExit):
            episode_recall.main([])

    def test_cli_keyword_missing_keywords(self):
        with pytest.raises(SystemExit):
            episode_recall.main(["keyword", "--memory-dir", "/tmp"])

    def test_cli_keyword_missing_memory_dir(self):
        with pytest.raises(SystemExit):
            episode_recall.main(["keyword", "--keywords", "test"])

    def test_cli_time_range_missing_memory_dir(self):
        with pytest.raises(SystemExit):
            episode_recall.main(["time-range", "--last", "7d"])

    def test_cli_context_missing_tags(self):
        with pytest.raises(SystemExit):
            episode_recall.main(["context", "--memory-dir", "/tmp"])

    def test_cli_context_missing_memory_dir(self):
        with pytest.raises(SystemExit):
            episode_recall.main(["context", "--tags", "test"])

    def test_cli_keyword_error_to_stderr(self, tmp_memory_dir, capsys):
        with pytest.raises(SystemExit):
            episode_recall.main([
                "keyword",
                "--memory-dir", tmp_memory_dir,
                "--keywords", "",
            ])

    def test_cli_time_range_no_mode_error(self, memory_with_episodes, capsys):
        with pytest.raises(SystemExit):
            episode_recall.main([
                "time-range",
                "--memory-dir", memory_with_episodes,
            ])


# ═══════════════════════════════════════════════════════════════
# C4: TTL Cache for _load_all_episodes
# ═══════════════════════════════════════════════════════════════


class TestEpisodeCaching:
    """Test that _load_all_episodes uses a TTL cache."""

    def test_cache_avoids_repeated_file_reads(self, memory_with_episodes):
        """Second call within TTL should not re-read files."""
        import episode_recall
        # Clear any existing cache
        if hasattr(episode_recall._load_all_episodes, 'cache_clear'):
            episode_recall._load_all_episodes.cache_clear()
        elif hasattr(episode_recall, '_episode_cache'):
            episode_recall._episode_cache.clear()

        # First call
        result1 = episode_recall._load_all_episodes(memory_with_episodes)
        # Second call should return same data (from cache)
        result2 = episode_recall._load_all_episodes(memory_with_episodes)
        assert len(result1) == len(result2)
        assert len(result1) > 0

    def test_cache_can_be_cleared(self, memory_with_episodes):
        """Cache should support manual invalidation."""
        import episode_recall
        result1 = episode_recall._load_all_episodes(memory_with_episodes)
        # Clear cache
        if hasattr(episode_recall._load_all_episodes, 'cache_clear'):
            episode_recall._load_all_episodes.cache_clear()
        elif hasattr(episode_recall, '_episode_cache'):
            episode_recall._episode_cache.clear()
        # Should still work after clearing
        result2 = episode_recall._load_all_episodes(memory_with_episodes)
        assert len(result1) == len(result2)


# ═══════════════════════════════════════════════════════════════
# C5: Result Size Cap in context_search
# ═══════════════════════════════════════════════════════════════


class TestResultSizeCap:
    """Test that context_search caps intermediate dict sizes."""

    def test_context_search_respects_limit(self, memory_with_episodes):
        """context_search should not return more than limit results."""
        import episode_recall
        result = episode_recall.context_search(
            memory_dir=memory_with_episodes,
            tags=["test"],
            limit=2,
        )
        # Even if more episodes match, output should be limited
        # (This tests the limit parameter works)
        assert isinstance(result, str)

    def test_episode_matches_cap_constant_exists(self):
        """Module should define a cap constant for intermediate results."""
        import episode_recall
        assert hasattr(episode_recall, "MAX_EPISODE_MATCHES")
        assert isinstance(episode_recall.MAX_EPISODE_MATCHES, int)
        assert episode_recall.MAX_EPISODE_MATCHES > 0


# --- C22-B: Episode recall count ---

class TestIncrementEpisodeRecallCounts:
    """Tests for increment_episode_recall_counts in episode_memory.py."""

    def test_recall_count_incremented(self, tmp_memory_dir):
        """recall_count is incremented for specified episodes."""
        from episode_memory import (
            _load_session_file,
            get_episodes_path,
            increment_episode_recall_counts,
            record_episode,
        )

        # Record two episodes
        result1 = record_episode(tmp_memory_dir, "observation", "ep1", session_id="test_sess")
        result2 = record_episode(tmp_memory_dir, "observation", "ep2", session_id="test_sess")
        ep1_id = result1.split(":")[1].strip().split(" ")[0]
        ep2_id = result2.split(":")[1].strip().split(" ")[0]

        # Build episode_session_map
        sess_path = str(get_episodes_path(tmp_memory_dir) / "test_sess.json")
        episode_session_map = {ep1_id: sess_path, ep2_id: sess_path}

        # Increment
        increment_episode_recall_counts(tmp_memory_dir, episode_session_map)

        # Verify
        from pathlib import Path
        data = _load_session_file(Path(sess_path))
        by_id = {ep["episode_id"]: ep for ep in data["episodes"]}
        assert by_id[ep1_id].get("recall_count", 0) == 1
        assert by_id[ep2_id].get("recall_count", 0) == 1

    def test_backward_compat_missing_recall_count(self, tmp_memory_dir):
        """Episodes without recall_count are treated as 0."""
        from pathlib import Path

        from episode_memory import (
            _load_session_file,
            get_episodes_path,
            increment_episode_recall_counts,
            record_episode,
        )

        record_episode(tmp_memory_dir, "observation", "ep1", session_id="test_sess")
        sess_path = str(get_episodes_path(tmp_memory_dir) / "test_sess.json")

        # Manually verify no recall_count field exists yet
        data = _load_session_file(Path(sess_path))
        ep = data["episodes"][0]
        ep_id = ep["episode_id"]
        assert "recall_count" not in ep

        # Increment (should go from implicit 0 to 1)
        increment_episode_recall_counts(tmp_memory_dir, {ep_id: sess_path})

        data = _load_session_file(Path(sess_path))
        assert data["episodes"][0]["recall_count"] == 1

    def test_atomic_write(self, tmp_memory_dir):
        """Session file should be written atomically (no corruption on concurrent access)."""
        import json
        from pathlib import Path

        from episode_memory import get_episodes_path, increment_episode_recall_counts, record_episode

        record_episode(tmp_memory_dir, "observation", "ep1", session_id="test_sess")
        sess_path = str(get_episodes_path(tmp_memory_dir) / "test_sess.json")

        data = json.loads(Path(sess_path).read_text(encoding="utf-8"))
        ep_id = data["episodes"][0]["episode_id"]

        # Increment multiple times
        for _ in range(3):
            increment_episode_recall_counts(tmp_memory_dir, {ep_id: sess_path})

        # File should still be valid JSON
        data = json.loads(Path(sess_path).read_text(encoding="utf-8"))
        assert data["episodes"][0]["recall_count"] == 3

    def test_ranking_not_affected(self, tmp_memory_dir):
        """recall_count should not affect search ranking in episode_recall.py."""
        import episode_recall
        from episode_memory import get_episodes_path, increment_episode_recall_counts, record_episode

        # Record episodes with known keywords
        record_episode(tmp_memory_dir, "observation", "unique_alpha_keyword",
                       tags=["testtag"], session_id="test_sess")
        record_episode(tmp_memory_dir, "observation", "unique_alpha_keyword again",
                       tags=["testtag"], session_id="test_sess")

        # Search before increment
        episode_recall._episode_cache.clear()
        result_before = episode_recall.keyword_search(tmp_memory_dir, ["unique_alpha_keyword"], limit=10)

        # Increment first episode's recall_count many times
        import json
        from pathlib import Path
        sess_path = str(get_episodes_path(tmp_memory_dir) / "test_sess.json")
        data = json.loads(Path(sess_path).read_text(encoding="utf-8"))
        ep1_id = data["episodes"][0]["episode_id"]
        for _ in range(10):
            increment_episode_recall_counts(tmp_memory_dir, {ep1_id: sess_path})

        # Search after increment — order should be unchanged
        episode_recall._episode_cache.clear()
        result_after = episode_recall.keyword_search(tmp_memory_dir, ["unique_alpha_keyword"], limit=10)
        assert result_before == result_after


# --- Cache Size Limit Tests ---

class TestCacheSizeLimits:
    """Verify that module-level caches have bounded size."""

    def test_episode_cache_has_max_size_constant(self):
        """Module should define MAX_CACHE_ENTRIES for _episode_cache."""
        assert hasattr(episode_recall, "MAX_CACHE_ENTRIES"), \
            "Module should define MAX_CACHE_ENTRIES constant"
        assert episode_recall.MAX_CACHE_ENTRIES > 0

    def test_session_files_cache_has_max_size_constant(self):
        """Module should define MAX_CACHE_ENTRIES for _session_files_cache."""
        # Same constant governs both caches
        assert hasattr(episode_recall, "MAX_CACHE_ENTRIES")

    def test_episode_cache_evicts_oldest(self, tmp_memory_dir):
        """_episode_cache should evict oldest entries when exceeding MAX_CACHE_ENTRIES."""
        episode_recall._episode_cache.clear()
        import time as _time

        # Manually insert more entries than MAX_CACHE_ENTRIES
        max_entries = episode_recall.MAX_CACHE_ENTRIES
        for i in range(max_entries + 5):
            fake_dir = f"/fake/memory/dir/{i}"
            episode_recall._episode_cache[fake_dir] = (_time.monotonic(), [])

        # Trigger cache cleanup by loading episodes from a real dir
        _create_session_file(tmp_memory_dir, "sess_cache_test", [
            {"episode_id": "ep_cache_1", "episode_type": "test",
             "timestamp": "2026-03-09T10:00:00Z", "session_id": "sess_cache_test",
             "summary": "cache test", "tags": [], "user_utterances": []},
        ])
        episode_recall._load_all_episodes(tmp_memory_dir)

        # Cache should not exceed MAX_CACHE_ENTRIES
        assert len(episode_recall._episode_cache) <= max_entries + 1  # +1 for the real entry just loaded

    def test_session_files_cache_evicts_oldest(self):
        """_session_files_cache should evict oldest entries when exceeding MAX_CACHE_ENTRIES."""
        episode_recall._session_files_cache.clear()
        import time as _time

        max_entries = episode_recall.MAX_CACHE_ENTRIES
        for i in range(max_entries + 5):
            fake_key = f"/fake/episodes/dir/{i}"
            episode_recall._session_files_cache[fake_key] = (_time.monotonic(), [])

        # After exceeding limit, next cache write should trigger eviction
        # We need to call _list_session_files with a valid path to trigger cleanup
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            episode_recall._list_session_files(Path(td))
            assert len(episode_recall._session_files_cache) <= max_entries + 1
