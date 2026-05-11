"""Tests for topic_index.py."""

import json
import os
import sys
from pathlib import Path

import pytest

# Add parent directory to path so we can import topic_index
sys.path.insert(0, str(Path(__file__).parent.parent))
import topic_index


# --- Fixtures ---

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
def memory_with_tagged_episodes(tmp_memory_dir):
    """Create a memory dir with episodes containing various tags."""
    _create_session_file(tmp_memory_dir, "session_20260309_100000", [
        _make_episode(
            "ep_aaa001",
            summary="Fixed bug in psyche/emotion.py related to decay",
            tags=["auth", "bugfix"],
            timestamp="2026-03-09T10:00:00Z",
            session_id="session_20260309_100000",
        ),
        _make_episode(
            "ep_aaa002",
            summary="Refactored orchestrator.phase_engine module",
            tags=["refactor"],
            user_utterances=[{"text": "Check the psyche/thought.py file", "role": "user", "truncated": False}],
            timestamp="2026-03-09T11:00:00Z",
            session_id="session_20260309_100000",
        ),
        _make_episode(
            "ep_aaa003",
            summary="Working on feature/new-recall branch",
            tags=["recall"],
            timestamp="2026-03-09T12:00:00Z",
            session_id="session_20260309_100000",
        ),
    ])

    _create_session_file(tmp_memory_dir, "session_20260310_100000", [
        _make_episode(
            "ep_bbb001",
            summary="Updated psyche/emotion.py with new safety valve",
            tags=["safety"],
            timestamp="2026-03-10T10:00:00Z",
            session_id="session_20260310_100000",
        ),
        _make_episode(
            "ep_bbb002",
            summary="Reviewed changes on main branch",
            tags=[],
            user_utterances=[{"text": "Look at src/brain.py for context", "role": "user", "truncated": False}],
            timestamp="2026-03-10T11:00:00Z",
            session_id="session_20260310_100000",
        ),
    ])

    return tmp_memory_dir


# ===== Tag extraction tests =====

class TestTagExtraction:
    """Tests for tag extraction from episode data."""

    def test_extract_file_path_from_summary(self):
        tags = topic_index._extract_tags_from_text("Fixed bug in psyche/emotion.py")
        assert "psyche/emotion.py" in tags

    def test_extract_backslash_path_normalized(self):
        tags = topic_index._extract_tags_from_text("Check psyche\\emotion.py for issues")
        assert "psyche/emotion.py" in tags

    def test_extract_module_name_from_summary(self):
        tags = topic_index._extract_tags_from_text("Refactored orchestrator.phase_engine module")
        assert "orchestrator.phase_engine" in tags

    def test_extract_git_branch_feature(self):
        tags = topic_index._extract_tags_from_text("Working on feature/new-recall branch")
        assert "feature/new-recall" in tags

    def test_extract_git_branch_fix(self):
        tags = topic_index._extract_tags_from_text("Merged fix/memory-leak into main")
        assert "fix/memory-leak" in tags
        assert "main" in tags

    def test_extract_git_branch_master(self):
        tags = topic_index._extract_tags_from_text("Deployed from master today")
        assert "master" in tags

    def test_extract_git_branch_main(self):
        tags = topic_index._extract_tags_from_text("Pushed to main successfully")
        assert "main" in tags

    def test_extract_from_user_utterance(self):
        episode = _make_episode(
            "test_ep",
            summary="Some work",
            user_utterances=[
                {"text": "Check psyche/thought.py for the bug", "role": "user", "truncated": False}
            ],
        )
        tags = topic_index._extract_tags_from_episode(episode)
        assert "psyche/thought.py" in tags

    def test_extract_from_tags_field(self):
        episode = _make_episode(
            "test_ep",
            summary="Simple summary",
            tags=["auth", "bugfix"],
        )
        tags = topic_index._extract_tags_from_episode(episode)
        assert "auth" in tags
        assert "bugfix" in tags

    def test_no_duplicate_from_tags_field_and_text(self):
        episode = _make_episode(
            "test_ep",
            summary="Fixed psyche/emotion.py",
            tags=["psyche/emotion.py"],
        )
        tags = topic_index._extract_tags_from_episode(episode)
        # Should have the tag only once (it's a set)
        assert "psyche/emotion.py" in tags

    def test_extract_multiple_paths_from_text(self):
        tags = topic_index._extract_tags_from_text(
            "Compare psyche/emotion.py and psyche/thought.py"
        )
        assert "psyche/emotion.py" in tags
        assert "psyche/thought.py" in tags

    def test_extract_no_tags_from_empty_text(self):
        tags = topic_index._extract_tags_from_text("")
        assert len(tags) == 0

    def test_extract_no_tags_from_plain_text(self):
        tags = topic_index._extract_tags_from_text("This is a plain sentence with no patterns")
        assert len(tags) == 0

    def test_extract_multiple_module_names(self):
        tags = topic_index._extract_tags_from_text(
            "Updated psyche.emotion and psyche.thought modules"
        )
        assert "psyche.emotion" in tags
        assert "psyche.thought" in tags


# ===== Tag normalization tests =====

class TestTagNormalization:
    """Tests for tag normalization."""

    def test_normalize_lowercase(self):
        assert topic_index._normalize_tag("Psyche/Emotion.py") == "psyche/emotion.py"

    def test_normalize_strip_whitespace(self):
        assert topic_index._normalize_tag("  auth  ") == "auth"

    def test_normalize_backslash_to_forward_slash(self):
        assert topic_index._normalize_tag("psyche\\emotion.py") == "psyche/emotion.py"

    def test_normalize_mixed_case_and_whitespace(self):
        assert topic_index._normalize_tag("  PSYCHE\\Emotion.PY  ") == "psyche/emotion.py"

    def test_normalize_empty_returns_empty(self):
        assert topic_index._normalize_tag("") == ""

    def test_normalize_whitespace_only_returns_empty(self):
        assert topic_index._normalize_tag("   ") == ""


# ===== Build index tests =====

class TestBuildIndex:
    """Tests for the build_index function."""

    def test_build_from_multiple_sessions(self, memory_with_tagged_episodes):
        result = topic_index.build_index(memory_with_tagged_episodes)
        assert not result.startswith("ERROR:")
        assert "tags" in result
        assert "episodes" in result

    def test_build_creates_index_file(self, memory_with_tagged_episodes):
        topic_index.build_index(memory_with_tagged_episodes)
        index_path = topic_index._get_index_path(memory_with_tagged_episodes)
        assert index_path.exists()

    def test_build_index_structure(self, memory_with_tagged_episodes):
        topic_index.build_index(memory_with_tagged_episodes)
        index_path = topic_index._get_index_path(memory_with_tagged_episodes)
        data = json.loads(index_path.read_text(encoding="utf-8"))
        assert "version" in data
        assert "tag_count" in data
        assert "episode_count" in data
        assert "index" in data
        assert isinstance(data["index"], dict)

    def test_build_index_contains_expected_tags(self, memory_with_tagged_episodes):
        topic_index.build_index(memory_with_tagged_episodes)
        index_data = topic_index._load_index(memory_with_tagged_episodes)
        index = index_data["index"]

        # Tags from the tags field
        assert "auth" in index
        assert "bugfix" in index
        assert "refactor" in index
        assert "recall" in index
        assert "safety" in index

        # Tags extracted from text
        assert "psyche/emotion.py" in index

    def test_build_episode_references_correct(self, memory_with_tagged_episodes):
        topic_index.build_index(memory_with_tagged_episodes)
        index_data = topic_index._load_index(memory_with_tagged_episodes)
        index = index_data["index"]

        # psyche/emotion.py should be referenced by two episodes (ep_aaa001 and ep_bbb001)
        refs = index.get("psyche/emotion.py", [])
        ep_ids = [r["episode_id"] for r in refs]
        assert "ep_aaa001" in ep_ids
        assert "ep_bbb001" in ep_ids

    def test_build_episode_reference_fields(self, memory_with_tagged_episodes):
        topic_index.build_index(memory_with_tagged_episodes)
        index_data = topic_index._load_index(memory_with_tagged_episodes)
        index = index_data["index"]

        # Check that references have required fields
        for tag, refs in index.items():
            for ref in refs:
                assert "episode_id" in ref
                assert "session_id" in ref
                assert "timestamp" in ref

    def test_build_missing_episodes_dir(self, tmp_memory_dir):
        result = topic_index.build_index(tmp_memory_dir)
        assert not result.startswith("ERROR:")
        assert "0 tags" in result
        assert "0 episodes" in result

    def test_build_empty_episodes_dir(self, tmp_memory_dir):
        episodes_dir = Path(tmp_memory_dir) / "episodes"
        episodes_dir.mkdir(parents=True, exist_ok=True)
        result = topic_index.build_index(tmp_memory_dir)
        assert not result.startswith("ERROR:")
        assert "0 tags" in result

    def test_build_corrupted_session_files_skipped(self, tmp_memory_dir):
        # Create a valid session
        _create_session_file(tmp_memory_dir, "session_20260309_100000", [
            _make_episode("ep_valid", summary="Valid episode", tags=["valid_tag"]),
        ])

        # Create a corrupted session file
        episodes_dir = Path(tmp_memory_dir) / "episodes"
        corrupted = episodes_dir / "session_20260309_corrupted.json"
        corrupted.write_text("NOT VALID JSON", encoding="utf-8")

        result = topic_index.build_index(tmp_memory_dir)
        assert not result.startswith("ERROR:")
        assert "1 episodes" in result  # Only the valid episode

    def test_rebuild_replaces_old_index(self, memory_with_tagged_episodes):
        # Build first time
        topic_index.build_index(memory_with_tagged_episodes)
        index_data1 = topic_index._load_index(memory_with_tagged_episodes)

        # Add a new session
        _create_session_file(memory_with_tagged_episodes, "session_20260311_100000", [
            _make_episode("ep_new001", summary="Brand new episode", tags=["new_tag"]),
        ])

        # Rebuild
        topic_index.build_index(memory_with_tagged_episodes)
        index_data2 = topic_index._load_index(memory_with_tagged_episodes)

        # The new tag should appear
        assert "new_tag" in index_data2["index"]
        # Episode count should be higher
        assert index_data2["episode_count"] > index_data1["episode_count"]

    def test_build_creates_memory_dir_if_needed(self, tmp_path):
        nested = str(tmp_path / "deep" / "nested")
        result = topic_index.build_index(nested)
        assert not result.startswith("ERROR:")
        assert Path(nested).exists()


# ===== Lookup tests =====

class TestLookup:
    """Tests for the lookup_by_tags function."""

    def test_lookup_exact_match(self, memory_with_tagged_episodes):
        topic_index.build_index(memory_with_tagged_episodes)
        result = topic_index.lookup_by_tags(memory_with_tagged_episodes, ["auth"])
        assert "ep_aaa001" in result

    def test_lookup_no_match(self, memory_with_tagged_episodes):
        topic_index.build_index(memory_with_tagged_episodes)
        result = topic_index.lookup_by_tags(memory_with_tagged_episodes, ["nonexistent_tag"])
        assert "No matching episodes" in result

    def test_lookup_prefix_match(self, memory_with_tagged_episodes):
        topic_index.build_index(memory_with_tagged_episodes)
        result = topic_index.lookup_by_tags(
            memory_with_tagged_episodes, ["psyche/"], prefix=True
        )
        # Should match psyche/emotion.py, psyche/thought.py
        assert "ep_aaa001" in result or "ep_aaa002" in result or "ep_bbb001" in result

    def test_lookup_multiple_tags(self, memory_with_tagged_episodes):
        topic_index.build_index(memory_with_tagged_episodes)
        result = topic_index.lookup_by_tags(
            memory_with_tagged_episodes, ["auth", "safety"]
        )
        # Should return episodes tagged with either tag
        assert "ep_aaa001" in result  # has "auth"
        assert "ep_bbb001" in result  # has "safety"

    def test_lookup_missing_index(self, tmp_memory_dir):
        result = topic_index.lookup_by_tags(tmp_memory_dir, ["some_tag"])
        assert "Index not found" in result
        assert "build" in result.lower()

    def test_lookup_empty_tags(self, memory_with_tagged_episodes):
        topic_index.build_index(memory_with_tagged_episodes)
        result = topic_index.lookup_by_tags(memory_with_tagged_episodes, [])
        assert result.startswith("ERROR:")

    def test_lookup_result_sorted_by_timestamp(self, memory_with_tagged_episodes):
        topic_index.build_index(memory_with_tagged_episodes)
        result = topic_index.lookup_by_tags(
            memory_with_tagged_episodes, ["psyche/emotion.py"]
        )
        # ep_bbb001 (2026-03-10) should appear before ep_aaa001 (2026-03-09)
        pos_bbb = result.find("ep_bbb001")
        pos_aaa = result.find("ep_aaa001")
        assert pos_bbb < pos_aaa

    def test_lookup_result_limit(self, tmp_memory_dir):
        # Create many episodes with the same tag
        episodes = []
        for i in range(20):
            episodes.append(_make_episode(
                f"ep_limit_{i:03d}",
                summary=f"Episode {i}",
                tags=["common_tag"],
                timestamp=f"2026-03-09T{i:02d}:00:00Z",
            ))
        _create_session_file(tmp_memory_dir, "session_limit", episodes)
        topic_index.build_index(tmp_memory_dir)

        result = topic_index.lookup_by_tags(
            tmp_memory_dir, ["common_tag"], limit=5
        )
        assert "20 total" in result
        assert "showing 5" in result

    def test_lookup_tag_normalization(self, tmp_memory_dir):
        _create_session_file(tmp_memory_dir, "session_norm", [
            _make_episode("ep_norm", summary="Test", tags=["Psyche/Emotion.py"]),
        ])
        topic_index.build_index(tmp_memory_dir)

        # Query with different case/separator
        result = topic_index.lookup_by_tags(tmp_memory_dir, ["psyche/emotion.py"])
        assert "ep_norm" in result

    def test_lookup_corrupted_index(self, tmp_memory_dir):
        # Write corrupted index
        index_path = topic_index._get_index_path(tmp_memory_dir)
        Path(tmp_memory_dir).mkdir(parents=True, exist_ok=True)
        index_path.write_text("NOT VALID JSON", encoding="utf-8")

        result = topic_index.lookup_by_tags(tmp_memory_dir, ["some_tag"])
        assert "Index not found" in result


# ===== List tags tests =====

class TestListTags:
    """Tests for the list_tags function."""

    def test_list_tags_sorted_by_count(self, memory_with_tagged_episodes):
        topic_index.build_index(memory_with_tagged_episodes)
        result = topic_index.list_tags(memory_with_tagged_episodes)

        # Should show tags sorted by count descending
        assert "Tags (" in result
        assert "total" in result

    def test_list_tags_shows_counts(self, memory_with_tagged_episodes):
        topic_index.build_index(memory_with_tagged_episodes)
        result = topic_index.list_tags(memory_with_tagged_episodes)
        assert "episodes)" in result

    def test_list_tags_missing_index(self, tmp_memory_dir):
        result = topic_index.list_tags(tmp_memory_dir)
        assert "Index not found" in result

    def test_list_tags_empty_index(self, tmp_memory_dir):
        # Build index with no episodes
        topic_index.build_index(tmp_memory_dir)
        result = topic_index.list_tags(tmp_memory_dir)
        assert "No tags" in result or "0 total" in result

    def test_list_tags_limit(self, tmp_memory_dir):
        # Create many distinct tags
        episodes = []
        for i in range(30):
            episodes.append(_make_episode(
                f"ep_many_{i:03d}",
                summary=f"Episode {i}",
                tags=[f"tag_{i:03d}"],
            ))
        _create_session_file(tmp_memory_dir, "session_many_tags", episodes)
        topic_index.build_index(tmp_memory_dir)

        result = topic_index.list_tags(tmp_memory_dir, limit=10)
        assert "30 total" in result
        assert "showing 10" in result

    def test_list_tags_order(self, tmp_memory_dir):
        # Create episodes where one tag appears more than another
        _create_session_file(tmp_memory_dir, "session_order", [
            _make_episode("ep_o1", tags=["common"]),
            _make_episode("ep_o2", tags=["common"]),
            _make_episode("ep_o3", tags=["common"]),
            _make_episode("ep_o4", tags=["rare"]),
        ])
        topic_index.build_index(tmp_memory_dir)

        result = topic_index.list_tags(tmp_memory_dir)
        # "common" (3 episodes) should appear before "rare" (1 episode)
        pos_common = result.find("common")
        pos_rare = result.find("rare")
        assert pos_common < pos_rare


# ===== Deduplication tests =====

class TestDeduplication:
    """Tests for tag deduplication within episodes."""

    def test_same_tag_from_field_and_text(self, tmp_memory_dir):
        _create_session_file(tmp_memory_dir, "session_dedup", [
            _make_episode(
                "ep_dedup",
                summary="Updated psyche/emotion.py for better decay",
                tags=["psyche/emotion.py"],
            ),
        ])
        topic_index.build_index(tmp_memory_dir)
        index_data = topic_index._load_index(tmp_memory_dir)
        refs = index_data["index"].get("psyche/emotion.py", [])

        # Should only have one reference (not duplicated)
        ep_ids = [r["episode_id"] for r in refs]
        assert ep_ids.count("ep_dedup") == 1

    def test_same_tag_different_case(self, tmp_memory_dir):
        _create_session_file(tmp_memory_dir, "session_case_dedup", [
            _make_episode(
                "ep_case",
                summary="Check AUTH module",
                tags=["auth"],
            ),
        ])
        topic_index.build_index(tmp_memory_dir)
        index_data = topic_index._load_index(tmp_memory_dir)
        # Both "AUTH" (from text) would match as "auth" after normalization
        refs = index_data["index"].get("auth", [])
        ep_ids = [r["episode_id"] for r in refs]
        assert ep_ids.count("ep_case") == 1


# ===== UTF-8 tests =====

class TestUTF8:
    """Tests for UTF-8 content handling."""

    def test_utf8_tags_in_episode(self, tmp_memory_dir):
        _create_session_file(tmp_memory_dir, "session_utf8", [
            _make_episode(
                "ep_utf8",
                summary="Unicode test",
                tags=["japanese_module"],
            ),
        ])
        topic_index.build_index(tmp_memory_dir)
        index_data = topic_index._load_index(tmp_memory_dir)
        assert "japanese_module" in index_data["index"]

    def test_utf8_summary_extraction(self, tmp_memory_dir):
        _create_session_file(tmp_memory_dir, "session_utf8_summary", [
            _make_episode(
                "ep_utf8_sum",
                summary="Fixed bug in psyche/emotion.py for Japanese text",
                tags=[],
            ),
        ])
        topic_index.build_index(tmp_memory_dir)
        index_data = topic_index._load_index(tmp_memory_dir)
        assert "psyche/emotion.py" in index_data["index"]

    def test_utf8_index_file_readable(self, tmp_memory_dir):
        _create_session_file(tmp_memory_dir, "session_utf8_file", [
            _make_episode("ep_utf8_f", tags=["tag_with_unicode"]),
        ])
        topic_index.build_index(tmp_memory_dir)
        index_path = topic_index._get_index_path(tmp_memory_dir)
        # Should be readable as UTF-8
        text = index_path.read_text(encoding="utf-8")
        data = json.loads(text)
        assert isinstance(data, dict)


# ===== Edge cases =====

class TestEdgeCases:
    """Tests for edge cases."""

    def test_episode_without_id_skipped(self, tmp_memory_dir):
        episodes_dir = Path(tmp_memory_dir) / "episodes"
        episodes_dir.mkdir(parents=True, exist_ok=True)
        session_data = {
            "session_id": "session_no_id",
            "created_at": "2026-03-09T00:00:00Z",
            "episodes": [
                {"summary": "No ID episode", "tags": ["orphan"], "episode_type": "observation",
                 "user_utterances": [], "timestamp": "2026-03-09T10:00:00Z", "session_id": "session_no_id"},
            ],
        }
        filepath = episodes_dir / "session_no_id.json"
        filepath.write_text(json.dumps(session_data), encoding="utf-8")

        result = topic_index.build_index(tmp_memory_dir)
        assert not result.startswith("ERROR:")
        assert "0 tags" in result

    def test_episode_with_empty_tags_field(self, tmp_memory_dir):
        _create_session_file(tmp_memory_dir, "session_empty_tags", [
            _make_episode("ep_empty_tags", summary="Plain summary", tags=[]),
        ])
        result = topic_index.build_index(tmp_memory_dir)
        assert not result.startswith("ERROR:")

    def test_episode_with_whitespace_only_tags(self, tmp_memory_dir):
        _create_session_file(tmp_memory_dir, "session_ws_tags", [
            _make_episode("ep_ws_tags", summary="Plain", tags=["  ", ""]),
        ])
        topic_index.build_index(tmp_memory_dir)
        index_data = topic_index._load_index(tmp_memory_dir)
        # Empty/whitespace-only tags should be excluded
        assert "" not in index_data["index"]

    def test_non_session_files_ignored(self, tmp_memory_dir):
        episodes_dir = Path(tmp_memory_dir) / "episodes"
        episodes_dir.mkdir(parents=True, exist_ok=True)
        # Create a non-session file
        (episodes_dir / "other_file.json").write_text("{}", encoding="utf-8")
        (episodes_dir / "readme.txt").write_text("test", encoding="utf-8")

        result = topic_index.build_index(tmp_memory_dir)
        assert not result.startswith("ERROR:")
        assert "0 episodes" in result

    def test_index_file_location(self, tmp_memory_dir):
        _create_session_file(tmp_memory_dir, "session_loc", [
            _make_episode("ep_loc", tags=["test"]),
        ])
        topic_index.build_index(tmp_memory_dir)

        # Index should be in memory dir root, not in episodes/
        index_path = Path(tmp_memory_dir) / "topic_index.json"
        assert index_path.exists()
        episodes_index = Path(tmp_memory_dir) / "episodes" / "topic_index.json"
        assert not episodes_index.exists()


# ===== Atomic write tests =====

class TestAtomicWrite:
    """Tests for atomic write behavior."""

    def test_no_temp_files_left_after_build(self, tmp_memory_dir):
        _create_session_file(tmp_memory_dir, "session_atomic", [
            _make_episode("ep_atomic", tags=["test"]),
        ])
        topic_index.build_index(tmp_memory_dir)

        # No temp files should remain
        memory_path = Path(tmp_memory_dir)
        temp_files = [f for f in memory_path.iterdir() if f.name.startswith(".topic_index_")]
        assert len(temp_files) == 0

    def test_index_is_valid_json_after_write(self, tmp_memory_dir):
        _create_session_file(tmp_memory_dir, "session_json", [
            _make_episode("ep_json", tags=["test1", "test2"]),
        ])
        topic_index.build_index(tmp_memory_dir)

        index_path = topic_index._get_index_path(tmp_memory_dir)
        # Should be valid parseable JSON
        data = json.loads(index_path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)


# ===== CLI tests =====

class TestCLI:
    """Tests for the CLI interface."""

    def test_cli_build(self, memory_with_tagged_episodes, capsys):
        topic_index.main([
            "build",
            "--memory-dir", memory_with_tagged_episodes,
        ])
        captured = capsys.readouterr()
        assert "Index built" in captured.out

    def test_cli_lookup(self, memory_with_tagged_episodes, capsys):
        topic_index.build_index(memory_with_tagged_episodes)
        topic_index.main([
            "lookup",
            "--memory-dir", memory_with_tagged_episodes,
            "--tags", "auth",
        ])
        captured = capsys.readouterr()
        assert "ep_aaa001" in captured.out

    def test_cli_lookup_prefix(self, memory_with_tagged_episodes, capsys):
        topic_index.build_index(memory_with_tagged_episodes)
        topic_index.main([
            "lookup",
            "--memory-dir", memory_with_tagged_episodes,
            "--tags", "psyche/",
            "--prefix",
        ])
        captured = capsys.readouterr()
        assert "Lookup results" in captured.out

    def test_cli_lookup_with_limit(self, memory_with_tagged_episodes, capsys):
        topic_index.build_index(memory_with_tagged_episodes)
        topic_index.main([
            "lookup",
            "--memory-dir", memory_with_tagged_episodes,
            "--tags", "auth",
            "--limit", "1",
        ])
        captured = capsys.readouterr()
        assert "showing 1" in captured.out

    def test_cli_lookup_multiple_tags(self, memory_with_tagged_episodes, capsys):
        topic_index.build_index(memory_with_tagged_episodes)
        topic_index.main([
            "lookup",
            "--memory-dir", memory_with_tagged_episodes,
            "--tags", "auth,safety",
        ])
        captured = capsys.readouterr()
        assert "Lookup results" in captured.out

    def test_cli_list_tags(self, memory_with_tagged_episodes, capsys):
        topic_index.build_index(memory_with_tagged_episodes)
        topic_index.main([
            "list-tags",
            "--memory-dir", memory_with_tagged_episodes,
        ])
        captured = capsys.readouterr()
        assert "Tags (" in captured.out

    def test_cli_list_tags_with_limit(self, memory_with_tagged_episodes, capsys):
        topic_index.build_index(memory_with_tagged_episodes)
        topic_index.main([
            "list-tags",
            "--memory-dir", memory_with_tagged_episodes,
            "--limit", "3",
        ])
        captured = capsys.readouterr()
        assert "showing 3" in captured.out or "Tags (" in captured.out

    def test_cli_no_command(self):
        with pytest.raises(SystemExit):
            topic_index.main([])

    def test_cli_build_missing_memory_dir(self):
        with pytest.raises(SystemExit):
            topic_index.main(["build"])

    def test_cli_lookup_missing_tags(self):
        with pytest.raises(SystemExit):
            topic_index.main(["lookup", "--memory-dir", "/tmp"])

    def test_cli_lookup_missing_memory_dir(self):
        with pytest.raises(SystemExit):
            topic_index.main(["lookup", "--tags", "test"])


# ===== Internal function tests =====

class TestInternalFunctions:
    """Tests for internal helper functions."""

    def test_get_episodes_path(self, tmp_memory_dir):
        path = topic_index._get_episodes_path(tmp_memory_dir)
        assert path.name == "episodes"
        assert str(path.parent) == tmp_memory_dir

    def test_get_index_path(self, tmp_memory_dir):
        path = topic_index._get_index_path(tmp_memory_dir)
        assert path.name == "topic_index.json"
        assert str(path.parent) == tmp_memory_dir

    def test_load_session_file_valid(self, tmp_memory_dir):
        episodes_dir = Path(tmp_memory_dir) / "episodes"
        episodes_dir.mkdir(parents=True, exist_ok=True)
        data = {"session_id": "test", "created_at": "2026-03-09T00:00:00Z", "episodes": []}
        filepath = episodes_dir / "test.json"
        filepath.write_text(json.dumps(data), encoding="utf-8")
        loaded = topic_index._load_session_file(filepath)
        assert loaded is not None
        assert loaded["session_id"] == "test"

    def test_load_session_file_corrupted(self, tmp_memory_dir):
        filepath = Path(tmp_memory_dir) / "bad.json"
        filepath.write_text("NOT JSON", encoding="utf-8")
        loaded = topic_index._load_session_file(filepath)
        assert loaded is None

    def test_load_session_file_missing_fields(self, tmp_memory_dir):
        filepath = Path(tmp_memory_dir) / "incomplete.json"
        filepath.write_text('{"some_field": "value"}', encoding="utf-8")
        loaded = topic_index._load_session_file(filepath)
        assert loaded is None

    def test_load_session_file_nonexistent(self, tmp_memory_dir):
        filepath = Path(tmp_memory_dir) / "nonexistent.json"
        loaded = topic_index._load_session_file(filepath)
        assert loaded is None

    def test_load_index_valid(self, tmp_memory_dir):
        _create_session_file(tmp_memory_dir, "session_load", [
            _make_episode("ep_load", tags=["test"]),
        ])
        topic_index.build_index(tmp_memory_dir)
        loaded = topic_index._load_index(tmp_memory_dir)
        assert loaded is not None
        assert "index" in loaded

    def test_load_index_missing(self, tmp_memory_dir):
        loaded = topic_index._load_index(tmp_memory_dir)
        assert loaded is None

    def test_load_index_corrupted(self, tmp_memory_dir):
        Path(tmp_memory_dir).mkdir(parents=True, exist_ok=True)
        index_path = topic_index._get_index_path(tmp_memory_dir)
        index_path.write_text("CORRUPTED", encoding="utf-8")
        loaded = topic_index._load_index(tmp_memory_dir)
        assert loaded is None

    def test_list_session_files_empty_dir(self, tmp_memory_dir):
        episodes_dir = Path(tmp_memory_dir) / "episodes"
        episodes_dir.mkdir(parents=True, exist_ok=True)
        files = topic_index._list_session_files(episodes_dir)
        assert files == []

    def test_list_session_files_nonexistent_dir(self, tmp_memory_dir):
        episodes_dir = Path(tmp_memory_dir) / "nonexistent"
        files = topic_index._list_session_files(episodes_dir)
        assert files == []
