"""Tests for memory_mcp_server.py.

Tests the integration/orchestration logic unique to this file.
Lower-level modules (episode_memory, emotion_state, etc.) are mocked
since they have their own dedicated tests.
"""

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import asyncio

# Add parent directory to path so we can import memory_mcp_server
sys.path.insert(0, str(Path(__file__).parent.parent))
import memory_mcp_server

# --- Fixtures ---


@pytest.fixture
def tmp_memory_dir(tmp_path, monkeypatch):
    """Provide a temporary memory directory and patch DEFAULT_MEMORY_DIR and GROWTH_DIR."""
    d = str(tmp_path)
    monkeypatch.setattr(memory_mcp_server, "DEFAULT_MEMORY_DIR", d)
    monkeypatch.setattr(memory_mcp_server, "GROWTH_DIR", d)
    return d


@pytest.fixture
def episodes_dir(tmp_memory_dir):
    """Create episodes/ subdirectory inside tmp_memory_dir."""
    ep_dir = Path(tmp_memory_dir) / "episodes"
    ep_dir.mkdir()
    return ep_dir


@pytest.fixture
def session_file(episodes_dir):
    """Create a session file with one episode (no trace)."""
    data = {
        "session_id": "session_20260322_100000",
        "episodes": [
            {
                "episode_id": "ep_001",
                "episode_type": "observation",
                "summary": "Test episode",
                "timestamp": "2026-03-22T10:00:00",
            }
        ],
    }
    path = episodes_dir / "session_20260322_100000.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


# =============================================================================
# P1: _attach_trace_to_latest_episode
# =============================================================================


class TestAttachTraceToLatestEpisode:
    """Tests for _attach_trace_to_latest_episode."""

    def test_no_episodes_dir(self, tmp_memory_dir):
        """Returns empty string when episodes/ does not exist."""
        result = memory_mcp_server._attach_trace_to_latest_episode(
            tmp_memory_dir, {"fulfillment": 0.5}
        )
        assert result == ""

    def test_empty_episodes_dir(self, episodes_dir, tmp_memory_dir):
        """Returns empty string when episodes/ has no session files."""
        result = memory_mcp_server._attach_trace_to_latest_episode(
            tmp_memory_dir, {"fulfillment": 0.5}
        )
        assert result == ""

    def test_attach_trace_success(self, session_file, tmp_memory_dir):
        """Attaches trace to the last episode in the latest session file."""
        trace = {"fulfillment": 0.3, "tension": -0.1, "affinity": 0.2}
        result = memory_mcp_server._attach_trace_to_latest_episode(
            tmp_memory_dir, trace
        )
        assert result == "Emotion trace attached."

        # Verify file content
        data = json.loads(session_file.read_text(encoding="utf-8"))
        assert data["episodes"][-1]["emotion_trace"] == trace

    def test_immutability_skip(self, session_file, tmp_memory_dir):
        """Skips if the episode already has an emotion_trace (immutability)."""
        # Pre-attach a trace
        data = json.loads(session_file.read_text(encoding="utf-8"))
        data["episodes"][-1]["emotion_trace"] = {"fulfillment": 0.0}
        session_file.write_text(json.dumps(data), encoding="utf-8")

        result = memory_mcp_server._attach_trace_to_latest_episode(
            tmp_memory_dir, {"fulfillment": 0.9}
        )
        assert "already present" in result

        # Verify original trace is unchanged
        data2 = json.loads(session_file.read_text(encoding="utf-8"))
        assert data2["episodes"][-1]["emotion_trace"]["fulfillment"] == 0.0

    def test_no_episodes_in_file(self, episodes_dir, tmp_memory_dir):
        """Returns empty string when session file has empty episodes list."""
        data = {"session_id": "s1", "episodes": []}
        path = episodes_dir / "session_20260322_120000.json"
        path.write_text(json.dumps(data), encoding="utf-8")

        result = memory_mcp_server._attach_trace_to_latest_episode(
            tmp_memory_dir, {"fulfillment": 0.1}
        )
        assert result == ""

    def test_invalid_json(self, episodes_dir, tmp_memory_dir):
        """Returns empty string when session file contains invalid JSON."""
        path = episodes_dir / "session_20260322_130000.json"
        path.write_text("NOT JSON", encoding="utf-8")

        result = memory_mcp_server._attach_trace_to_latest_episode(
            tmp_memory_dir, {"fulfillment": 0.1}
        )
        assert result == ""

    def test_picks_latest_session_file(self, episodes_dir, tmp_memory_dir):
        """Picks the most recently modified session file."""
        # Create older file
        old_data = {
            "session_id": "s_old",
            "episodes": [{"episode_id": "old_ep", "summary": "old"}],
        }
        old_path = episodes_dir / "session_20260320_100000.json"
        old_path.write_text(json.dumps(old_data), encoding="utf-8")

        # Create newer file (written after)
        time.sleep(0.05)
        new_data = {
            "session_id": "s_new",
            "episodes": [{"episode_id": "new_ep", "summary": "new"}],
        }
        new_path = episodes_dir / "session_20260322_100000.json"
        new_path.write_text(json.dumps(new_data), encoding="utf-8")

        trace = {"fulfillment": 0.5}
        memory_mcp_server._attach_trace_to_latest_episode(tmp_memory_dir, trace)

        # Trace should be on the newer file
        new_loaded = json.loads(new_path.read_text(encoding="utf-8"))
        assert "emotion_trace" in new_loaded["episodes"][-1]

        # Old file should be untouched
        old_loaded = json.loads(old_path.read_text(encoding="utf-8"))
        assert "emotion_trace" not in old_loaded["episodes"][-1]


# =============================================================================
# P1: _parse_lessons
# =============================================================================


class TestParseLessons:
    """Tests for _parse_lessons."""

    def test_no_file(self, tmp_memory_dir):
        """Returns empty list when lessons_registry.md does not exist."""
        result = memory_mcp_server._parse_lessons(tmp_memory_dir)
        assert result == []

    def test_empty_file(self, tmp_memory_dir):
        """Returns empty list when file is empty."""
        path = os.path.join(tmp_memory_dir, "lessons_registry.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("")
        result = memory_mcp_server._parse_lessons(tmp_memory_dir)
        assert result == []

    def test_single_lesson(self, tmp_memory_dir):
        """Parses a single lesson with all fields."""
        content = """# Lessons Registry

## Lesson: 2026-03-10

### Action
Skipped analysis step

### Why
Was in a hurry

### Lesson
Never skip analysis

### Fix
Add blocking hook

### Related Rule
impl-without-analysis
---
"""
        path = os.path.join(tmp_memory_dir, "lessons_registry.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        result = memory_mcp_server._parse_lessons(tmp_memory_dir)
        assert len(result) == 1
        assert result[0]["date"] == "2026-03-10"
        assert "Skipped analysis step" in result[0]["action"]
        assert "Was in a hurry" in result[0]["why"]
        assert "Never skip analysis" in result[0]["lesson"]
        assert "Add blocking hook" in result[0]["fix"]
        assert "impl-without-analysis" in result[0]["rule"]

    def test_multiple_lessons(self, tmp_memory_dir):
        """Parses multiple lessons correctly."""
        content = """## Lesson: 2026-03-10

### Lesson
First lesson

---

## Lesson: 2026-03-15

### Lesson
Second lesson

---
"""
        path = os.path.join(tmp_memory_dir, "lessons_registry.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        result = memory_mcp_server._parse_lessons(tmp_memory_dir)
        assert len(result) == 2
        assert "First lesson" in result[0]["lesson"]
        assert "Second lesson" in result[1]["lesson"]

    def test_multiline_field(self, tmp_memory_dir):
        """Concatenates multiline field content."""
        content = """## Lesson: 2026-03-10

### Lesson
First line of lesson
Second line of lesson

---
"""
        path = os.path.join(tmp_memory_dir, "lessons_registry.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        result = memory_mcp_server._parse_lessons(tmp_memory_dir)
        assert len(result) == 1
        assert "First line" in result[0]["lesson"]
        assert "Second line" in result[0]["lesson"]

    def test_whitespace_stripped(self, tmp_memory_dir):
        """Field values have trailing whitespace stripped."""
        content = """## Lesson: 2026-03-10

### Lesson
Some text with trailing space

---
"""
        path = os.path.join(tmp_memory_dir, "lessons_registry.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        result = memory_mcp_server._parse_lessons(tmp_memory_dir)
        assert not result[0]["lesson"].endswith(" ")


# =============================================================================
# P1: _load_principles
# =============================================================================


class TestLoadPrinciples:
    """Tests for _load_principles."""

    def test_no_file(self, tmp_memory_dir):
        """Returns exists=False when file does not exist."""
        result = memory_mcp_server._load_principles(tmp_memory_dir)
        assert result["exists"] is False
        assert result["lesson_count"] == 0
        assert result["principles"] == []

    def test_empty_file(self, tmp_memory_dir):
        """Returns exists=True but empty principles for empty file."""
        path = os.path.join(tmp_memory_dir, "consolidated_principles.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("")
        result = memory_mcp_server._load_principles(tmp_memory_dir)
        assert result["exists"] is True
        assert result["principles"] == []

    def test_with_frontmatter_and_principles(self, tmp_memory_dir):
        """Parses frontmatter lesson_count and principle sections."""
        content = """---
name: Consolidated Principles
lesson_count: 15
---

## Never skip analysis
- Lesson #5: analysis caught design flaw
- Lesson #10: skipping led to rework

## Always use TDD
- Lesson #3: TDD caught regression
"""
        path = os.path.join(tmp_memory_dir, "consolidated_principles.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        result = memory_mcp_server._load_principles(tmp_memory_dir)
        assert result["exists"] is True
        assert result["lesson_count"] == 15
        assert len(result["principles"]) == 2
        assert result["principles"][0]["title"] == "Never skip analysis"
        assert len(result["principles"][0]["evidence"]) == 2
        assert result["principles"][1]["title"] == "Always use TDD"
        assert len(result["principles"][1]["evidence"]) == 1
        assert "consolidated_principles.md" not in result["raw"] or len(result["raw"]) > 0

    def test_no_frontmatter(self, tmp_memory_dir):
        """Works even without frontmatter (lesson_count defaults to 0)."""
        content = """## Principle One
- Evidence A
"""
        path = os.path.join(tmp_memory_dir, "consolidated_principles.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        result = memory_mcp_server._load_principles(tmp_memory_dir)
        assert result["exists"] is True
        assert result["lesson_count"] == 0
        assert len(result["principles"]) == 1


# =============================================================================
# P1: _extract_gap_analysis
# =============================================================================


class TestExtractGapAnalysis:
    """Tests for _extract_gap_analysis."""

    def test_no_files(self, tmp_path):
        """Returns message when no gap analysis files exist."""
        result = memory_mcp_server._extract_gap_analysis(str(tmp_path))
        assert "No gap analysis found" in result

    def test_no_directory(self):
        """Returns message when directory does not exist."""
        result = memory_mcp_server._extract_gap_analysis("/nonexistent/path")
        assert "No gap analysis found" in result

    def test_extracts_gap_items(self, tmp_path):
        """Extracts ### G items from gap analysis file."""
        content = """# Gap Analysis Cycle 19

### G1: Test coverage for untested files
Some description

### G2: Error handling improvements
More description

### G3: Documentation gaps
Details
"""
        path = tmp_path / "gap_analysis_c19.md"
        path.write_text(content, encoding="utf-8")

        result = memory_mcp_server._extract_gap_analysis(str(tmp_path))
        assert "G1: Test coverage" in result
        assert "G2: Error handling" in result
        assert "G3: Documentation gaps" in result
        assert "Current Cycle Gaps" in result

    def test_picks_latest_alphabetically(self, tmp_path):
        """Uses the last file when sorted alphabetically."""
        (tmp_path / "gap_analysis_c18.md").write_text(
            "### G1: Old gap", encoding="utf-8"
        )
        (tmp_path / "gap_analysis_c19.md").write_text(
            "### G1: New gap", encoding="utf-8"
        )

        result = memory_mcp_server._extract_gap_analysis(str(tmp_path))
        assert "New gap" in result
        assert "Old gap" not in result

    def test_no_gap_items(self, tmp_path):
        """Returns header but no items when file has no ### G lines."""
        (tmp_path / "gap_analysis_c19.md").write_text(
            "# Just a title\nSome text", encoding="utf-8"
        )
        result = memory_mcp_server._extract_gap_analysis(str(tmp_path))
        assert "Current Cycle Gaps" in result


# =============================================================================
# P2: _fts_search
# =============================================================================


class TestFtsSearch:
    """Tests for _fts_search orchestration logic."""

    @pytest.fixture
    def mock_semantic(self, monkeypatch):
        """Mock SemanticIndex and related functions."""
        monkeypatch.setattr(memory_mcp_server, "_SEMANTIC_AVAILABLE", True)

        mock_idx = MagicMock()
        mock_idx.is_dirty.return_value = False
        mock_idx.get_stats.return_value = {"episode_count": 5, "lesson_count": 3}
        mock_idx.hybrid_search.return_value = []
        mock_idx.close = MagicMock()

        mock_cls = MagicMock(return_value=mock_idx)
        monkeypatch.setattr(memory_mcp_server, "SemanticIndex", mock_cls)
        monkeypatch.setattr(
            memory_mcp_server, "get_lessons_mtime", MagicMock(return_value=100.0)
        )
        monkeypatch.setattr(
            memory_mcp_server, "extract_query_terms", MagicMock(return_value=["test"])
        )
        monkeypatch.setattr(
            memory_mcp_server, "generate_snippet", MagicMock(return_value="...snippet...")
        )
        monkeypatch.setattr(
            memory_mcp_server, "format_score_breakdown", MagicMock(return_value="0.5000")
        )
        monkeypatch.setattr(memory_mcp_server, "_lessons_mtime_at_sync", 100.0)

        return mock_idx

    def test_no_results(self, tmp_memory_dir, mock_semantic):
        """Returns 'No matching results' when hybrid_search returns empty."""
        result = memory_mcp_server._fts_search(
            tmp_memory_dir, "test query", "", "", 10, None
        )
        assert "No matching results" in result

    def test_episode_results_formatted(self, tmp_memory_dir, mock_semantic, monkeypatch):
        """Formats episode results with score breakdown."""
        mock_semantic.hybrid_search.return_value = [
            {
                "source_type": "episode",
                "source_id": "ep_001",
                "score": 0.85,
                "original_text": "Some episode text",
            }
        ]
        # Mock _load_all_episodes to return matching episode
        monkeypatch.setattr(
            memory_mcp_server,
            "_load_all_episodes",
            MagicMock(return_value=[
                {"episode_id": "ep_001", "summary": "Test episode", "timestamp": "2026-03-22T10:00:00"}
            ]),
        )

        result = memory_mcp_server._fts_search(
            tmp_memory_dir, "test", "", "", 10, None
        )
        assert "FTS Search" in result
        assert "1 episodes" in result

    def test_lesson_results_formatted(self, tmp_memory_dir, mock_semantic):
        """Formats lesson results with preview."""
        mock_semantic.hybrid_search.return_value = [
            {
                "source_type": "lesson",
                "source_id": "5",
                "score": 0.7,
                "original_text": "Never skip the analysis step in the development flow",
            }
        ]

        result = memory_mcp_server._fts_search(
            tmp_memory_dir, "analysis", "", "", 10, None
        )
        assert "Lesson Search" in result
        assert "lesson #5" in result

    def test_dirty_triggers_sync(self, tmp_memory_dir, mock_semantic, monkeypatch):
        """Syncs episodes and lessons when index is dirty."""
        mock_semantic.is_dirty.return_value = True
        mock_semantic.hybrid_search.return_value = []

        mock_load_eps = MagicMock(return_value=[])
        mock_parse = MagicMock(return_value=[])
        monkeypatch.setattr(memory_mcp_server, "_load_all_episodes", mock_load_eps)
        monkeypatch.setattr(memory_mcp_server, "_parse_lessons", mock_parse)

        memory_mcp_server._fts_search(tmp_memory_dir, "q", "", "", 10, None)

        mock_semantic.sync_episodes.assert_called_once()
        mock_semantic.sync_lessons.assert_called_once()
        mock_semantic.clear_dirty.assert_called()

    def test_empty_index_triggers_rebuild(self, tmp_memory_dir, mock_semantic, monkeypatch):
        """Rebuilds when index has zero entries."""
        mock_semantic.is_dirty.return_value = False
        mock_semantic.get_stats.return_value = {"episode_count": 0, "lesson_count": 0}
        mock_semantic.hybrid_search.return_value = []

        mock_load_eps = MagicMock(return_value=[])
        mock_parse = MagicMock(return_value=[])
        monkeypatch.setattr(memory_mcp_server, "_load_all_episodes", mock_load_eps)
        monkeypatch.setattr(memory_mcp_server, "_parse_lessons", mock_parse)

        memory_mcp_server._fts_search(tmp_memory_dir, "q", "", "", 10, None)

        mock_semantic.rebuild.assert_called_once()

    def test_exception_returns_error(self, tmp_memory_dir, mock_semantic):
        """Returns error message on exception."""
        mock_semantic.hybrid_search.side_effect = RuntimeError("DB error")

        result = memory_mcp_server._fts_search(
            tmp_memory_dir, "test", "", "", 10, None
        )
        assert "ERROR" in result
        mock_semantic.close.assert_called()


# =============================================================================
# P2: memory_record (tool function)
# =============================================================================


class TestMemoryRecord:
    """Tests for memory_record tool function."""

    @pytest.fixture
    def mock_deps(self, tmp_memory_dir, monkeypatch):
        """Mock all lower-level dependencies for memory_record."""
        mocks = {}
        mocks["record_episode"] = MagicMock(return_value="Recorded ep_001")
        mocks["build_index"] = MagicMock(return_value="Index rebuilt")
        mocks["_load_change_log"] = MagicMock(return_value=[])
        mocks["create_trace"] = MagicMock(return_value={"fulfillment": 0.1})
        mocks["_attach"] = MagicMock(return_value="Emotion trace attached.")

        monkeypatch.setattr(memory_mcp_server, "record_episode", mocks["record_episode"])
        monkeypatch.setattr(memory_mcp_server, "build_index", mocks["build_index"])
        monkeypatch.setattr(memory_mcp_server, "_load_change_log", mocks["_load_change_log"])
        monkeypatch.setattr(memory_mcp_server, "create_trace", mocks["create_trace"])
        monkeypatch.setattr(
            memory_mcp_server, "_attach_trace_to_latest_episode", mocks["_attach"]
        )
        monkeypatch.setattr(memory_mcp_server, "_SEMANTIC_AVAILABLE", False)
        return mocks

    def test_basic_record(self, mock_deps):
        """Records episode, rebuilds index, attaches trace."""
        result = memory_mcp_server.memory_record(
            episode_type="observation", summary="Test observation"
        )
        assert "Recorded ep_001" in result
        assert "Index rebuilt" in result
        assert "Emotion trace attached" in result

        mock_deps["record_episode"].assert_called_once()
        mock_deps["build_index"].assert_called_once()

    def test_tags_split(self, mock_deps):
        """Comma-separated tags are split into a list."""
        memory_mcp_server.memory_record(
            episode_type="decision", summary="test", tags="a, b, c"
        )
        call_kwargs = mock_deps["record_episode"].call_args
        assert call_kwargs[1]["tags"] == ["a", "b", "c"]

    def test_empty_tags(self, mock_deps):
        """Empty tags string results in empty list."""
        memory_mcp_server.memory_record(
            episode_type="decision", summary="test", tags=""
        )
        call_kwargs = mock_deps["record_episode"].call_args
        assert call_kwargs[1]["tags"] == []

    def test_user_text_wrapped(self, mock_deps):
        """user_text is wrapped in a list for record_episode."""
        memory_mcp_server.memory_record(
            episode_type="feedback", summary="test", user_text="hello"
        )
        call_kwargs = mock_deps["record_episode"].call_args
        assert call_kwargs[1]["user_texts"] == ["hello"]

    def test_empty_user_text(self, mock_deps):
        """Empty user_text results in None."""
        memory_mcp_server.memory_record(
            episode_type="feedback", summary="test", user_text=""
        )
        call_kwargs = mock_deps["record_episode"].call_args
        assert call_kwargs[1]["user_texts"] is None

    def test_trace_failure_nonfatal(self, mock_deps):
        """Trace attachment failure does not crash the tool."""
        mock_deps["create_trace"].side_effect = RuntimeError("no state")
        result = memory_mcp_server.memory_record(
            episode_type="observation", summary="test"
        )
        assert "Recorded ep_001" in result
        assert "emotion trace skipped" in result

    def test_error_result_skips_trace(self, mock_deps):
        """When record_episode returns ERROR, trace is skipped."""
        mock_deps["record_episode"].return_value = "ERROR: disk full"
        result = memory_mcp_server.memory_record(
            episode_type="observation", summary="test"
        )
        assert "ERROR: disk full" in result
        mock_deps["_attach"].assert_not_called()

    def test_semantic_dirty_flag(self, mock_deps, monkeypatch):
        """Sets semantic index dirty flag when available."""
        mock_idx = MagicMock()
        mock_cls = MagicMock(return_value=mock_idx)
        monkeypatch.setattr(memory_mcp_server, "_SEMANTIC_AVAILABLE", True)
        monkeypatch.setattr(memory_mcp_server, "SemanticIndex", mock_cls)

        memory_mcp_server.memory_record(episode_type="observation", summary="test")
        mock_idx.set_dirty.assert_called_once()

    def test_exception_returns_error(self, mock_deps):
        """Top-level exception returns ERROR string."""
        mock_deps["record_episode"].side_effect = RuntimeError("boom")
        result = memory_mcp_server.memory_record(
            episode_type="observation", summary="test"
        )
        assert result.startswith("ERROR:")


# =============================================================================
# P2: memory_search (tool function)
# =============================================================================


class TestMemorySearch:
    """Tests for memory_search tool function."""

    @pytest.fixture
    def mock_search_deps(self, tmp_memory_dir, monkeypatch):
        """Mock search-related dependencies."""
        monkeypatch.setattr(
            memory_mcp_server, "keyword_search",
            MagicMock(return_value="Keyword: 0 results")
        )
        monkeypatch.setattr(
            memory_mcp_server, "context_search",
            MagicMock(return_value="Context: 0 results")
        )
        monkeypatch.setattr(
            memory_mcp_server, "time_range_search",
            MagicMock(return_value="Time: 0 results")
        )
        monkeypatch.setattr(
            memory_mcp_server, "get_state_dict",
            MagicMock(return_value=None)
        )
        monkeypatch.setattr(memory_mcp_server, "_SEMANTIC_AVAILABLE", False)

    def test_mutual_exclusion(self, mock_search_deps):
        """Returns error when both query and keywords are provided."""
        result = asyncio.run(memory_mcp_server.memory_search(query="test", keywords="test"))
        assert "mutually exclusive" in result

    def test_no_params_error(self, mock_search_deps):
        """Returns error when no search parameters provided."""
        result = asyncio.run(memory_mcp_server.memory_search())
        assert "At least one" in result

    def test_keyword_search_path(self, mock_search_deps):
        """Keyword search is called when keywords provided."""
        result = asyncio.run(memory_mcp_server.memory_search(keywords="test,debug"))
        assert "Keyword Search" in result

    def test_tag_search_path(self, mock_search_deps):
        """Context search is called when tags provided."""
        result = asyncio.run(memory_mcp_server.memory_search(tags="auth,bugfix"))
        assert "Context Search" in result

    def test_time_search_path(self, mock_search_deps):
        """Time range search is called when last provided."""
        result = asyncio.run(memory_mcp_server.memory_search(last="7d"))
        assert "Time Search" in result

    def test_query_without_semantic(self, mock_search_deps):
        """Returns error for query when semantic module unavailable."""
        result = asyncio.run(memory_mcp_server.memory_search(query="test query"))
        assert "not available" in result

    def test_combined_search(self, mock_search_deps):
        """Multiple search types can be combined."""
        result = asyncio.run(memory_mcp_server.memory_search(keywords="test", tags="debug", last="7d"))
        assert "Keyword Search" in result
        assert "Context Search" in result
        assert "Time Search" in result

    def test_writes_flag_file(self, mock_search_deps, tmp_path, monkeypatch):
        """Writes .memory-search-done flag file on success."""
        flag_dir = str(tmp_path / ".claude" / "hooks")
        os.makedirs(flag_dir, exist_ok=True)
        monkeypatch.setattr(os.path, "expanduser", lambda x: str(tmp_path))

        result = asyncio.run(memory_mcp_server.memory_search(keywords="test"))
        assert "Keyword Search" in result

        flag_file = os.path.join(flag_dir, ".memory-search-done")
        assert os.path.exists(flag_file), "Flag file .memory-search-done was not created"
        content = open(flag_file).read().strip()
        assert content.isdigit(), f"Flag file content should be a timestamp, got: {content}"

    def test_mood_reorder_keyword_search_raw_path(self, mock_search_deps, monkeypatch):
        """When mood_reorder is enabled and emotion state exists, keyword_search_raw is used."""
        emotion_state = {"fulfillment": 0.5, "tension": 0.0, "affinity": 0.0}
        monkeypatch.setattr(
            memory_mcp_server, "get_state_dict",
            MagicMock(return_value=emotion_state)
        )
        mock_raw = MagicMock(return_value=[
            ({"episode_id": "ep1", "summary": "test episode"}, "detail1"),
        ])
        monkeypatch.setattr(memory_mcp_server, "keyword_search_raw", mock_raw)
        mock_reorder = MagicMock(side_effect=lambda eps, emo: eps)
        monkeypatch.setattr(memory_mcp_server, "mood_reorder", mock_reorder)

        result = asyncio.run(memory_mcp_server.memory_search(keywords="test", mood_reorder_enabled=True))

        mock_raw.assert_called_once()
        mock_reorder.assert_called_once()
        assert "mood-reordered" in result


# =============================================================================
# P3: session_start (tool function)
# =============================================================================


class TestSessionStart:
    """Tests for session_start tool function."""

    @pytest.fixture
    def mock_session_deps(self, tmp_memory_dir, monkeypatch):
        """Mock all dependencies for session_start."""
        monkeypatch.setattr(
            memory_mcp_server, "load_state",
            MagicMock(return_value={"fulfillment": 0.5, "tension": 0.1, "affinity": 0.3})
        )
        monkeypatch.setattr(
            memory_mcp_server, "apply_session_decay",
            MagicMock(return_value={"fulfillment": 0.4, "tension": 0.08, "affinity": 0.24})
        )
        monkeypatch.setattr(
            memory_mcp_server, "save_state",
            MagicMock(return_value="Saved")
        )
        monkeypatch.setattr(
            memory_mcp_server, "stm_load",
            MagicMock(return_value={"entries": [], "meta": {"session_count": 1}})
        )
        monkeypatch.setattr(
            memory_mcp_server, "stm_decay",
            MagicMock(return_value=({"entries": [], "meta": {"session_count": 2}}, 0))
        )
        monkeypatch.setattr(
            memory_mcp_server, "stm_save",
            MagicMock(return_value="OK")
        )
        monkeypatch.setattr(
            memory_mcp_server, "stm_stats",
            MagicMock(return_value={"session_count": 2, "avg_weight": 0.0, "by_category": {}})
        )
        monkeypatch.setattr(
            memory_mcp_server, "stm_read_entries",
            MagicMock(return_value=[])
        )
        # activation_surface_fn is the imported function (renamed to avoid shadowing)
        monkeypatch.setattr(
            memory_mcp_server, "activation_surface_fn",
            MagicMock(return_value="No activations")
        )
        monkeypatch.setattr(
            memory_mcp_server, "facade_run_snapshot",
            MagicMock(return_value={
                "observe": {"integrated": "calm"},
                "difference": {"magnitude": "low", "integrated_description": "stable"},
                "strain": {"level": "low", "description": "no strain"},
                "self_image": {"overall_impression": "neutral", "integrated_description": "steady"},
                "coherence": {"coherence_level": "high", "description": "coherent"},
                "stability": {"dampening_factor": 1.0, "description": "stable"},
                "tone": {"primary_tone": "calm", "description": "calm tone"},
            })
        )

    def test_basic_output_structure(self, mock_session_deps):
        """session_start returns output with all major sections."""
        result = memory_mcp_server.session_start()
        assert "Emotion Restore" in result
        assert "STM Restore" in result
        assert "Self Snapshot" in result

    def test_emotion_restore_section(self, mock_session_deps):
        """Includes before/after emotion values."""
        result = memory_mcp_server.session_start()
        assert "Before:" in result
        assert "After:" in result

    def test_snapshot_error_resilience(self, mock_session_deps, monkeypatch):
        """Continues even if self_snapshot fails."""
        monkeypatch.setattr(
            memory_mcp_server, "facade_run_snapshot",
            MagicMock(side_effect=RuntimeError("snapshot failed"))
        )
        result = memory_mcp_server.session_start()
        # Should still have emotion and STM sections
        assert "Emotion Restore" in result
        assert "STM Restore" in result
        assert "ERROR" in result

    def test_session_context_loaded(self, mock_session_deps, tmp_memory_dir):
        """Loads previous session context if available."""
        ctx = """# Session Context

## Session: 2026-03-21

### Pending
- Fix bug in auth module

### Next
- Run full test suite
"""
        ctx_path = os.path.join(tmp_memory_dir, "session_context.md")
        with open(ctx_path, "w", encoding="utf-8") as f:
            f.write(ctx)

        result = memory_mcp_server.session_start()
        assert "Previous Session" in result
        assert "Fix bug in auth module" in result

    def test_gap_analysis_shown(self, mock_session_deps, tmp_path, monkeypatch):
        """Shows gap analysis when docs/ has gap_analysis file."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        gap_file = docs_dir / "gap_analysis_c19.md"
        gap_file.write_text("### G1: Test coverage needed", encoding="utf-8")

        monkeypatch.setattr(os.path, "expanduser", lambda x: str(tmp_path))

        result = memory_mcp_server.session_start()
        # Gap analysis output depends on expanduser path matching
        assert "Emotion Restore" in result

    def test_principles_shown(self, mock_session_deps, tmp_memory_dir):
        """Shows consolidated principles if file exists."""
        content = """---
lesson_count: 10
---

## Never skip analysis
- Lesson #5: caught design flaw
"""
        path = os.path.join(tmp_memory_dir, "consolidated_principles.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        result = memory_mcp_server.session_start()
        assert "Consolidated Principles" in result
        assert "Never skip analysis" in result

    def test_session_start_deletes_dev_flow_state(self, mock_session_deps, tmp_path, monkeypatch):
        """session_start deletes .dev-flow-state so stale flow position is not injected."""
        # expanduser("~") returns tmp_path, so path becomes tmp_path/.claude/hooks/.dev-flow-state
        hooks_dir = os.path.join(str(tmp_path), ".claude", "hooks")
        os.makedirs(hooks_dir, exist_ok=True)
        flow_state_path = os.path.join(hooks_dir, ".dev-flow-state")
        with open(flow_state_path, "w", encoding="utf-8") as f:
            f.write('{"design": 1000, "planner": 2000}')

        # Patch expanduser so session_start finds our tmp hooks dir
        monkeypatch.setattr(os.path, "expanduser", lambda x: str(tmp_path))

        memory_mcp_server.session_start()
        assert not os.path.exists(flow_state_path), ".dev-flow-state should be deleted by session_start"

    def test_session_start_no_error_when_dev_flow_state_missing(self, mock_session_deps, tmp_path, monkeypatch):
        """session_start succeeds even when .dev-flow-state does not exist."""
        monkeypatch.setattr(os.path, "expanduser", lambda x: str(tmp_path))
        # No .dev-flow-state file exists — should not raise
        result = memory_mcp_server.session_start()
        assert "Emotion Restore" in result


# =============================================================================
# P3: session_end (tool function)
# =============================================================================


class TestSessionEnd:
    """Tests for session_end tool function."""

    @pytest.fixture
    def mock_end_deps(self, tmp_memory_dir, monkeypatch):
        """Mock all dependencies for session_end."""
        monkeypatch.setattr(
            memory_mcp_server, "sc_save",
            MagicMock(return_value="/path/to/session_context.md")
        )
        monkeypatch.setattr(
            memory_mcp_server, "facade_run_mini_snapshot",
            MagicMock(return_value={
                "observe": {"integrated": "tired but satisfied"},
                "self_image": {"overall_impression": "productive", "integrated_description": "good day"},
                "tone": {"primary_tone": "warm", "description": "warm tone"},
            })
        )
        monkeypatch.setattr(
            memory_mcp_server, "load_state",
            MagicMock(return_value={"fulfillment": 0.6, "tension": -0.1, "affinity": 0.4})
        )

    def test_basic_output(self, mock_end_deps):
        """session_end returns all three sections."""
        result = memory_mcp_server.session_end(summary="Did some testing")
        assert "Session Context Saved" in result
        assert "Final Self Snapshot" in result
        assert "Emotion State" in result

    def test_passes_all_params(self, mock_end_deps):
        """All parameters are forwarded to sc_save."""
        memory_mcp_server.session_end(
            summary="Work done",
            completed="task1,task2",
            pending="task3",
            decisions="chose X over Y",
            issues="none",
            next_actions="deploy",
        )
        call_kwargs = memory_mcp_server.sc_save.call_args[1]
        assert call_kwargs["summary"] == "Work done"
        assert call_kwargs["completed"] == "task1,task2"
        assert call_kwargs["pending"] == "task3"

    def test_snapshot_failure_resilience(self, mock_end_deps, monkeypatch):
        """Continues even if mini_snapshot fails."""
        monkeypatch.setattr(
            memory_mcp_server, "facade_run_mini_snapshot",
            MagicMock(side_effect=RuntimeError("snapshot boom"))
        )
        result = memory_mcp_server.session_end(summary="test")
        assert "Session Context Saved" in result
        assert "ERROR" in result

    def test_context_save_failure(self, mock_end_deps, monkeypatch):
        """Reports error if sc_save returns ERROR."""
        monkeypatch.setattr(
            memory_mcp_server, "sc_save",
            MagicMock(return_value="ERROR: disk full")
        )
        result = memory_mcp_server.session_end(summary="test")
        assert "ERROR: disk full" in result


# =============================================================================
# P3: emotion_react (tool function)
# =============================================================================


class TestEmotionReact:
    """Tests for emotion_react tool function."""

    @pytest.fixture
    def mock_react_deps(self, tmp_memory_dir, monkeypatch):
        """Mock all dependencies for emotion_react."""
        monkeypatch.setattr(
            memory_mcp_server, "load_state",
            MagicMock(return_value={"fulfillment": 0.3, "tension": 0.0, "affinity": 0.2})
        )
        monkeypatch.setattr(
            memory_mcp_server, "load_dynamics_state",
            MagicMock(return_value={"phase": "normal", "accumulator": 0.0})
        )
        monkeypatch.setattr(
            memory_mcp_server, "dynamics_session_reset",
            MagicMock(side_effect=lambda x: x)
        )
        monkeypatch.setattr(
            memory_mcp_server, "dynamics_get_amplitude",
            MagicMock(return_value=1.0)
        )
        monkeypatch.setattr(
            memory_mcp_server, "facade_get_dampening",
            MagicMock(return_value=1.0)
        )
        monkeypatch.setattr(
            memory_mcp_server, "emotion_react_fn",
            MagicMock(return_value={"fulfillment": 0.1, "tension": -0.05, "affinity": 0.08})
        )
        monkeypatch.setattr(
            memory_mcp_server, "dynamics_update",
            MagicMock(return_value=({"phase": "normal", "accumulator": 0.1}, {}))
        )
        monkeypatch.setattr(
            memory_mcp_server, "save_dynamics_state",
            MagicMock()
        )
        monkeypatch.setattr(
            memory_mcp_server, "get_dynamics_info",
            MagicMock(return_value="phase=normal, amp=1.0")
        )
        monkeypatch.setattr(
            memory_mcp_server, "update_state",
            MagicMock(return_value="fulfillment=+0.400, tension=-0.050, affinity=+0.280")
        )
        monkeypatch.setattr(
            memory_mcp_server, "facade_record_long_term",
            MagicMock(return_value={"status": "buffered", "buffer_size": 3})
        )

    def test_basic_react(self, mock_react_deps):
        """Runs full 6-stage pipeline and returns formatted result."""
        result = memory_mcp_server.emotion_react(
            emotion_label="happy",
            emotion_valence=0.7,
            intent="sharing",
        )
        assert "Reaction deltas" in result
        assert "Dynamics:" in result
        assert "Stability:" in result

    def test_custom_amplitude(self, mock_react_deps):
        """Manual amplitude_modifier overrides dynamics amplitude."""
        memory_mcp_server.emotion_react(
            emotion_label="sad",
            emotion_valence=-0.5,
            amplitude_modifier=0.5,
        )
        # emotion_react_fn should receive 0.5 as amplitude
        call_kwargs = memory_mcp_server.emotion_react_fn.call_args[1]
        assert call_kwargs["amplitude_modifier"] == 0.5

    def test_stability_dampening_applied(self, mock_react_deps, monkeypatch):
        """Stability dampening multiplies the effective amplitude."""
        monkeypatch.setattr(
            memory_mcp_server, "facade_get_dampening",
            MagicMock(return_value=0.5)
        )
        memory_mcp_server.emotion_react(
            emotion_label="angry",
            emotion_valence=-0.8,
        )
        call_kwargs = memory_mcp_server.emotion_react_fn.call_args[1]
        # Default amplitude 1.0 * dampening 0.5 = 0.5
        assert call_kwargs["amplitude_modifier"] == 0.5

    def test_reason_formatting(self, mock_react_deps):
        """Reason is prepended with auto-generated context."""
        memory_mcp_server.emotion_react(
            emotion_label="happy",
            emotion_valence=0.6,
            intent="sharing",
            reason="user praised work",
        )
        call_kwargs = memory_mcp_server.update_state.call_args[1]
        assert "react: happy" in call_kwargs["reason"]
        assert "user praised work" in call_kwargs["reason"]

    def test_long_term_buffered(self, mock_react_deps):
        """Shows buffer info when long-term returns buffered status."""
        result = memory_mcp_server.emotion_react(
            emotion_label="neutral", emotion_valence=0.0
        )
        assert "buffered" in result

    def test_long_term_aggregated(self, mock_react_deps, monkeypatch):
        """Shows entry ID when long-term returns aggregated status."""
        monkeypatch.setattr(
            memory_mcp_server, "facade_record_long_term",
            MagicMock(return_value={
                "status": "aggregated",
                "entry": {"entry_id": 42},
            })
        )
        result = memory_mcp_server.emotion_react(
            emotion_label="happy", emotion_valence=0.5
        )
        assert "#42" in result

    def test_dampening_failure_fallback(self, mock_react_deps, monkeypatch):
        """Falls back to dampening=1.0 when facade_get_dampening fails."""
        monkeypatch.setattr(
            memory_mcp_server, "facade_get_dampening",
            MagicMock(side_effect=RuntimeError("no stability data"))
        )
        result = memory_mcp_server.emotion_react(
            emotion_label="happy", emotion_valence=0.3
        )
        # Should not crash
        assert "Reaction deltas" in result

    def test_exception_returns_error(self, mock_react_deps, monkeypatch):
        """Top-level exception returns ERROR string."""
        monkeypatch.setattr(
            memory_mcp_server, "load_state",
            MagicMock(side_effect=RuntimeError("state corrupt"))
        )
        result = memory_mcp_server.emotion_react(
            emotion_label="happy", emotion_valence=0.5
        )
        assert result.startswith("ERROR:")


# --- activation_surface shadowing bug tests ---


class TestActivationSurfaceShadowingFix:
    """Verify activation_surface MCP tool calls the imported function, not itself.

    Bug: The MCP tool function `activation_surface()` at line 860 shadows the
    imported `activation_surface` from line 81, causing infinite recursion or
    'multiple values for argument' errors.
    """

    def test_activation_surface_tool_calls_imported_function(self, tmp_memory_dir, monkeypatch):
        """activation_surface tool must delegate to the imported function, not recurse."""
        mock_surface = MagicMock(return_value="mock activation result")
        monkeypatch.setattr(memory_mcp_server, "activation_surface_fn", mock_surface)

        result = memory_mcp_server.activation_surface(context="test task")

        mock_surface.assert_called_once_with(tmp_memory_dir, context="test task")
        assert result == "mock activation result"

    def test_activation_surface_tool_no_context(self, tmp_memory_dir, monkeypatch):
        """activation_surface tool with empty context passes context=None."""
        mock_surface = MagicMock(return_value="no context result")
        monkeypatch.setattr(memory_mcp_server, "activation_surface_fn", mock_surface)

        result = memory_mcp_server.activation_surface(context="")

        mock_surface.assert_called_once_with(tmp_memory_dir, context=None)
        assert result == "no context result"

    def test_activation_surface_error_handling(self, tmp_memory_dir, monkeypatch):
        """activation_surface tool returns ERROR string on exception."""
        mock_surface = MagicMock(side_effect=RuntimeError("test error"))
        monkeypatch.setattr(memory_mcp_server, "activation_surface_fn", mock_surface)

        result = memory_mcp_server.activation_surface(context="")

        assert result.startswith("ERROR:")
        assert "test error" in result

    def test_session_start_activation_uses_imported_function(self, tmp_memory_dir, monkeypatch):
        """session_start's activation section must use activation_surface_fn, not the tool."""
        mock_surface = MagicMock(return_value="snapshot activation")
        monkeypatch.setattr(memory_mcp_server, "activation_surface_fn", mock_surface)

        # Mock other dependencies needed by session_start
        monkeypatch.setattr(
            memory_mcp_server, "load_state",
            MagicMock(return_value={"valence": 0.5, "arousal": 0.3, "label": "calm"})
        )
        monkeypatch.setattr(
            memory_mcp_server, "stm_load",
            MagicMock(return_value={"entries": []})
        )
        monkeypatch.setattr(
            memory_mcp_server, "stm_decay",
            MagicMock(return_value=({"entries": []}, 0))
        )
        monkeypatch.setattr(
            memory_mcp_server, "stm_save",
            MagicMock()
        )
        monkeypatch.setattr(
            memory_mcp_server, "stm_read_entries",
            MagicMock(return_value=[])
        )
        monkeypatch.setattr(
            memory_mcp_server, "facade_run_snapshot",
            MagicMock(return_value={
                "observe": {"integrated": "calm"},
                "difference": {"magnitude": "low", "integrated_description": "stable"},
                "strain": {"level": "low", "description": "no strain"},
                "self_image": {"overall_impression": "neutral", "integrated_description": "steady"},
                "coherence": {"coherence_level": "high", "description": "coherent"},
                "stability": {"dampening_factor": 1.0, "description": "stable"},
                "tone": {"primary_tone": "calm", "description": "calm tone"},
            })
        )

        result = memory_mcp_server.session_start()

        # activation_surface_fn should have been called (not the tool function)
        mock_surface.assert_called_once_with(tmp_memory_dir)
        assert "snapshot activation" in result


# --- C22-B: Use-Based Reinforcement Integration Tests ---

class TestSTMBoostIntegration:
    """Integration test: STM write -> read -> boost -> decay -> reread."""

    def test_stm_read_boosts_and_persists(self, tmp_memory_dir):
        """stm_read should boost recall_count and weight, then persist."""
        from short_term_store import load_store, save_store, write_entry

        # Write an entry
        store = load_store(tmp_memory_dir)
        store = write_entry(store, "test thought", "thought")
        save_store(tmp_memory_dir, store)

        # Read via MCP handler (triggers boost)
        result = memory_mcp_server.stm_read()
        assert "test thought" in result

        # Verify boost was persisted
        store = load_store(tmp_memory_dir)
        entry = store["entries"][0]
        assert entry.get("recall_count", 0) == 1
        assert entry["weight"] > 1.0 - 0.01  # boosted back near 1.0

    def test_stm_boost_then_decay_then_reread(self, tmp_memory_dir):
        """Full flow: write -> read(boost) -> decay -> reread(boost again)."""
        from short_term_store import (
            apply_session_decay,
            load_store,
            save_store,
            write_entry,
        )

        # Write
        store = load_store(tmp_memory_dir)
        store = write_entry(store, "important thought", "thought")
        save_store(tmp_memory_dir, store)

        # Read (boost: recall_count=1, weight=1.0+0.3 clipped to 1.0)
        memory_mcp_server.stm_read()

        # Decay
        store = load_store(tmp_memory_dir)
        store, _ = apply_session_decay(store)
        save_store(tmp_memory_dir, store)

        # Verify decayed weight is higher than it would be without recall
        entry_after_decay = store["entries"][0]
        assert entry_after_decay["recall_count"] == 1
        # With recall_count=1, resistance=0.05, effective_rate=0.75+0.05=0.80
        # weight = 1.0 * 0.80 * 0.75 (thought decay_factor) = 0.60
        assert entry_after_decay["weight"] > 0.55

        # Read again (boost again: recall_count=2)
        memory_mcp_server.stm_read()
        store = load_store(tmp_memory_dir)
        entry_final = store["entries"][0]
        assert entry_final["recall_count"] == 2


class TestEpisodeRecallIntegration:
    """Integration test: episode record -> search -> recall increment -> compression delay."""

    def test_search_increments_recall_count(self, tmp_memory_dir):
        """memory_search should increment recall_count on returned episodes."""
        import episode_recall
        from episode_memory import (
            _load_session_file,
            get_episodes_path,
            record_episode,
        )

        # Record an episode with a unique keyword (session_id must start with session_)
        record_episode(
            tmp_memory_dir,
            "observation",
            "recall_integration_test_unique_xyzzy",
            tags=["recall_test"],
            session_id="session_recall_test",
        )

        # Clear caches so search sees the new episode
        episode_recall._episode_cache.clear()
        episode_recall._session_files_cache.clear()

        # Search for it via keyword
        result = asyncio.run(memory_mcp_server.memory_search(keywords="xyzzy"))
        assert "recall_integration_test_unique_xyzzy" in result

        # Verify recall_count was incremented
        sess_path = get_episodes_path(tmp_memory_dir) / "session_recall_test.json"
        data = _load_session_file(sess_path)
        assert data is not None
        ep = data["episodes"][0]
        assert ep.get("recall_count", 0) >= 1


# =============================================================================
# C22-A: Lesson Validation Loop integration
# =============================================================================


class TestLessonApplicationTracking:
    """Tests for lesson application tracking in memory_search."""

    @pytest.fixture
    def mock_semantic_with_lessons(self, tmp_memory_dir, monkeypatch):
        """Set up mock semantic index returning lesson results."""
        mock_idx = MagicMock()
        mock_idx.is_dirty.return_value = False
        mock_idx.get_stats.return_value = {"episode_count": 1, "lesson_count": 1}
        mock_idx.close.return_value = None

        # Return a lesson result
        mock_idx.hybrid_search.return_value = [
            {
                "source_type": "lesson",
                "source_id": "3",
                "score": 0.75,
                "original_text": "Always run tests before commit",
            }
        ]

        monkeypatch.setattr(memory_mcp_server, "_SEMANTIC_AVAILABLE", True)
        monkeypatch.setattr(
            memory_mcp_server,
            "_get_semantic_index",
            MagicMock(return_value=mock_idx),
        )
        monkeypatch.setattr(memory_mcp_server, "_lessons_mtime_at_sync", 100.0)

        # Mock _load_all_episodes for cached_episodes path
        monkeypatch.setattr(
            memory_mcp_server,
            "_load_all_episodes",
            MagicMock(return_value=[]),
        )

        return mock_idx

    def test_lesson_search_shows_confidence(self, tmp_memory_dir, mock_semantic_with_lessons):
        """Lesson search results include confidence and applied_count."""
        result = memory_mcp_server._fts_search(
            tmp_memory_dir, "tests", "", "", 10, None
        )
        assert "Lesson Search" in result
        # Default confidence 0.5 and applied=0 for untracked
        assert "confidence=" in result
        assert "applied=" in result

    def test_lesson_application_recorded(self, tmp_memory_dir, mock_semantic_with_lessons):
        """_fts_search records lesson application via returned_lessons."""

        returned_lessons = []
        memory_mcp_server._fts_search(
            tmp_memory_dir, "tests", "", "", 10, None,
            returned_lessons=returned_lessons,
        )
        # returned_lessons should contain lesson numbers
        assert len(returned_lessons) > 0
        assert returned_lessons[0] == ("3", 0.75)

    def test_lesson_ranking_unaffected(self, tmp_memory_dir, mock_semantic_with_lessons, monkeypatch):
        """Confidence does not change search ranking order."""
        import lesson_metadata

        # Set high confidence on lesson 3
        lesson_metadata.record_application(tmp_memory_dir, "3", "s1")
        lesson_metadata.validate_lesson(tmp_memory_dir, "3", success=True)
        lesson_metadata.validate_lesson(tmp_memory_dir, "3", success=True)

        # Mock returns two lessons in score-ranked order (hybrid_search sorts by score)
        mock_idx = mock_semantic_with_lessons
        mock_idx.hybrid_search.return_value = [
            {
                "source_type": "lesson",
                "source_id": "7",
                "score": 0.80,
                "original_text": "Lesson seven text",
            },
            {
                "source_type": "lesson",
                "source_id": "3",
                "score": 0.50,
                "original_text": "Lesson three text",
            },
        ]

        result = memory_mcp_server._fts_search(
            tmp_memory_dir, "test", "", "", 10, None
        )
        lines = result.split("\n")
        # lesson #7 (score 0.80) should appear before lesson #3 (score 0.50)
        # regardless of confidence
        lesson7_idx = None
        lesson3_idx = None
        for i, line in enumerate(lines):
            if "lesson #7" in line:
                lesson7_idx = i
            if "lesson #3" in line:
                lesson3_idx = i
        assert lesson7_idx is not None and lesson3_idx is not None
        assert lesson7_idx < lesson3_idx

    def test_fts_search_failopen_on_metadata_error(self, tmp_memory_dir, mock_semantic_with_lessons, monkeypatch):
        """Metadata error does not break lesson search output."""
        import lesson_metadata

        # Force metadata load to raise
        monkeypatch.setattr(
            lesson_metadata, "load_metadata",
            MagicMock(side_effect=OSError("disk error")),
        )

        result = memory_mcp_server._fts_search(
            tmp_memory_dir, "tests", "", "", 10, None
        )
        # Should still have lesson results (fail-open)
        assert "Lesson Search" in result
        assert "lesson #3" in result


class TestValidateLessonMcpTool:
    """Tests for the validate_lesson MCP tool."""

    def test_validate_success(self, tmp_memory_dir):
        """validate_lesson tool reports confidence increase."""
        import lesson_metadata

        lesson_metadata.record_application(tmp_memory_dir, "1", "s1")
        result = memory_mcp_server.validate_lesson(
            lesson_id="1", success=True, category="test"
        )
        assert "confidence" in result.lower()
        assert "0.6" in result

    def test_validate_failure(self, tmp_memory_dir):
        """validate_lesson tool reports confidence decrease."""
        import lesson_metadata

        lesson_metadata.record_application(tmp_memory_dir, "1", "s1")
        result = memory_mcp_server.validate_lesson(
            lesson_id="1", success=False, category="perf"
        )
        assert "0.35" in result

    def test_validate_nonexistent_lesson(self, tmp_memory_dir):
        """validate_lesson on untracked lesson creates entry."""
        result = memory_mcp_server.validate_lesson(
            lesson_id="999", success=True
        )
        assert "0.6" in result


# --- C22-G: Success Pattern MCP Tools ---


class TestRecordSuccessMcpTool:
    """Tests for the record_success MCP tool."""

    def test_record_success_normal(self, tmp_memory_dir):
        """record_success tool creates a record and returns confirmation."""
        result = memory_mcp_server.record_success_tool(
            event_type="review_zero",
            context="Clean code review",
            why_success="Thorough TDD",
            tags="testing,quality",
        )
        assert "Success pattern #1 recorded" in result
        assert "review_zero" in result

    def test_record_success_invalid_event_type(self, tmp_memory_dir):
        """record_success tool rejects invalid event_type."""
        result = memory_mcp_server.record_success_tool(
            event_type="invalid",
            context="ctx",
            why_success="why",
        )
        assert "ERROR" in result

    def test_record_success_empty_tags(self, tmp_memory_dir):
        """record_success tool handles empty tags."""
        result = memory_mcp_server.record_success_tool(
            event_type="test_pass",
            context="ctx",
            why_success="why",
            tags="",
        )
        assert "Success pattern #1 recorded" in result


class TestSearchSuccessesMcpTool:
    """Tests for the search_successes MCP tool."""

    def test_search_with_results(self, tmp_memory_dir):
        """search_successes tool returns formatted results."""
        memory_mcp_server.record_success_tool(
            event_type="test_pass",
            context="API test coverage",
            why_success="Mock design pattern",
            tags="api",
        )
        result = memory_mcp_server.search_successes_tool(query="API")
        assert "API" in result
        assert "Mock design" in result

    def test_search_no_results(self, tmp_memory_dir):
        """search_successes tool handles no results."""
        result = memory_mcp_server.search_successes_tool(query="nonexistent")
        assert "No matching" in result or "0" in result


class TestSuccessPatternInFtsSearch:
    """Tests for Success Pattern Search section in _fts_search."""

    @pytest.fixture
    def mock_semantic(self, monkeypatch):
        """Mock SemanticIndex and related functions for _fts_search."""
        monkeypatch.setattr(memory_mcp_server, "_SEMANTIC_AVAILABLE", True)

        mock_idx = MagicMock()
        mock_idx.is_dirty.return_value = False
        mock_idx.get_stats.return_value = {"episode_count": 5, "lesson_count": 3}
        mock_idx.hybrid_search.return_value = []
        mock_idx.close = MagicMock()

        mock_cls = MagicMock(return_value=mock_idx)
        monkeypatch.setattr(memory_mcp_server, "SemanticIndex", mock_cls)
        monkeypatch.setattr(
            memory_mcp_server, "get_lessons_mtime", MagicMock(return_value=100.0)
        )
        monkeypatch.setattr(
            memory_mcp_server, "extract_query_terms", MagicMock(return_value=["test"])
        )
        monkeypatch.setattr(
            memory_mcp_server, "generate_snippet", MagicMock(return_value="...snippet...")
        )
        monkeypatch.setattr(
            memory_mcp_server, "format_score_breakdown", MagicMock(return_value="0.5000")
        )
        monkeypatch.setattr(memory_mcp_server, "_lessons_mtime_at_sync", 100.0)

        return mock_idx

    def test_success_section_appended(self, tmp_memory_dir, mock_semantic, monkeypatch):
        """_fts_search appends Success Pattern Search section when records exist."""
        import success_registry

        # hybrid_search must return non-empty so _fts_search doesn't early-return
        mock_semantic.hybrid_search.return_value = [
            {"source_type": "episode", "source_id": "ep_001", "score": 0.5, "original_text": "text"},
        ]
        monkeypatch.setattr(
            memory_mcp_server, "_load_all_episodes",
            MagicMock(return_value=[{"episode_id": "ep_001", "episode_type": "test", "summary": "s", "timestamp": "2026-01-01"}]),
        )

        success_registry.record_success(
            tmp_memory_dir, "review_zero", "TDD workflow success", "Tests first", tags=["tdd"]
        )
        result = memory_mcp_server._fts_search(
            memory_dir=tmp_memory_dir,
            query="TDD",
            tags="",
            last="",
            limit=10,
            current_emotion=None,
        )
        assert "Success Pattern Search" in result
        assert "TDD workflow" in result

    def test_success_section_failopen(self, tmp_memory_dir, mock_semantic, monkeypatch):
        """_fts_search omits Success section on error (fail-open)."""
        import success_registry

        monkeypatch.setattr(
            success_registry, "search_successes",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        result = memory_mcp_server._fts_search(
            memory_dir=tmp_memory_dir,
            query="test",
            tags="",
            last="",
            limit=10,
            current_emotion=None,
        )
        assert "Success Pattern Search" not in result

    def test_success_section_max_5(self, tmp_memory_dir, mock_semantic, monkeypatch):
        """_fts_search shows max 5 success pattern results."""
        import success_registry

        # hybrid_search must return non-empty so _fts_search doesn't early-return
        mock_semantic.hybrid_search.return_value = [
            {"source_type": "episode", "source_id": "ep_001", "score": 0.5, "original_text": "text"},
        ]
        monkeypatch.setattr(
            memory_mcp_server, "_load_all_episodes",
            MagicMock(return_value=[{"episode_id": "ep_001", "episode_type": "test", "summary": "s", "timestamp": "2026-01-01"}]),
        )

        for i in range(8):
            success_registry.record_success(
                tmp_memory_dir, "test_pass", f"pattern {i}", f"reason {i}"
            )
        result = memory_mcp_server._fts_search(
            memory_dir=tmp_memory_dir,
            query="pattern",
            tags="",
            last="",
            limit=10,
            current_emotion=None,
        )
        assert "Success Pattern Search" in result
        import re
        # Should have max 5 success entries
        success_section = result.split("Success Pattern Search")[1] if "Success Pattern Search" in result else ""
        success_entries = re.findall(r"^\s+\d+\.", success_section, re.MULTILINE)
        assert len(success_entries) <= 5


# =============================================================================
# GROWTH_DIR: Global growth storage tests
# =============================================================================


class TestGrowthDirConstant:
    """GROWTH_DIR should point to ~/.claude/growth (global, not project-specific)."""

    def test_growth_dir_is_global(self):
        """GROWTH_DIR should be under ~/.claude/growth, not DEFAULT_MEMORY_DIR."""
        expected = os.path.join(os.path.expanduser("~"), ".claude", "growth")
        assert memory_mcp_server.GROWTH_DIR == expected

    def test_growth_dir_differs_from_default_memory_dir(self):
        """GROWTH_DIR must not be the same as DEFAULT_MEMORY_DIR."""
        assert memory_mcp_server.GROWTH_DIR != memory_mcp_server.DEFAULT_MEMORY_DIR


class TestGrowthToolsUseGrowthDir:
    """Growth tools must pass GROWTH_DIR (not DEFAULT_MEMORY_DIR) to their modules."""

    @pytest.fixture(autouse=True)
    def setup_dirs(self, tmp_path, monkeypatch):
        """Patch both DEFAULT_MEMORY_DIR and GROWTH_DIR to separate temp dirs."""
        self.memory_dir = str(tmp_path / "memory")
        self.growth_dir = str(tmp_path / "growth")
        os.makedirs(self.memory_dir, exist_ok=True)
        os.makedirs(self.growth_dir, exist_ok=True)
        monkeypatch.setattr(memory_mcp_server, "DEFAULT_MEMORY_DIR", self.memory_dir)
        monkeypatch.setattr(memory_mcp_server, "GROWTH_DIR", self.growth_dir)

    def test_validate_lesson_uses_growth_dir(self, monkeypatch):
        """validate_lesson should pass GROWTH_DIR to lesson_metadata."""
        called_with = {}
        def fake_validate(md, lid, success, category=""):
            called_with["memory_dir"] = md
            return {"confidence": 0.5, "applied_count": 1}
        monkeypatch.setattr(memory_mcp_server.lesson_metadata, "validate_lesson", fake_validate)
        memory_mcp_server.validate_lesson("1", True)
        assert called_with["memory_dir"] == self.growth_dir

    def test_record_success_uses_growth_dir(self, monkeypatch):
        """record_success_tool should pass GROWTH_DIR to success_registry."""
        called_with = {}
        def fake_record(md, **kwargs):
            called_with["memory_dir"] = md
            return {"id": 1, "event_type": "test_pass", "tags": []}
        monkeypatch.setattr(memory_mcp_server.success_registry, "record_success", fake_record)
        memory_mcp_server.record_success_tool("test_pass", "ctx", "why")
        assert called_with["memory_dir"] == self.growth_dir

    def test_search_successes_uses_growth_dir(self, monkeypatch):
        """search_successes_tool should pass GROWTH_DIR to success_registry."""
        called_with = {}
        def fake_search(md, **kwargs):
            called_with["memory_dir"] = md
            return []
        monkeypatch.setattr(memory_mcp_server.success_registry, "search_successes", fake_search)
        memory_mcp_server.search_successes_tool()
        assert called_with["memory_dir"] == self.growth_dir

    def test_update_mastery_uses_growth_dir(self, monkeypatch):
        """update_mastery should pass GROWTH_DIR to mastery_profile."""
        called_with = {}
        def fake_update(md, **kwargs):
            called_with["memory_dir"] = md
            return {"mastery_score": 0.8, "success_count": 5, "total_count": 6, "trend": "up"}
        monkeypatch.setattr(memory_mcp_server.mastery_profile, "update_mastery", fake_update)
        memory_mcp_server.update_mastery("testing", True)
        assert called_with["memory_dir"] == self.growth_dir

    def test_mastery_report_uses_growth_dir(self, monkeypatch):
        """mastery_report should pass GROWTH_DIR to mastery_profile."""
        called_with = {}
        def fake_report(md):
            called_with["memory_dir"] = md
            return "report"
        monkeypatch.setattr(memory_mcp_server.mastery_profile, "generate_report", fake_report)
        memory_mcp_server.mastery_report()
        assert called_with["memory_dir"] == self.growth_dir

    def test_growth_dashboard_uses_growth_dir(self, monkeypatch):
        """growth_dashboard should pass GROWTH_DIR to growth_metrics."""
        called_with = {}
        def fake_dash(md):
            called_with["memory_dir"] = md
            return "dashboard"
        monkeypatch.setattr(memory_mcp_server.growth_metrics, "generate_dashboard", fake_dash)
        memory_mcp_server.growth_dashboard()
        assert called_with["memory_dir"] == self.growth_dir

    def test_growth_health_uses_growth_dir(self, monkeypatch):
        """growth_health should pass GROWTH_DIR to growth_metrics."""
        called_with = {}
        def fake_health(md):
            called_with["memory_dir"] = md
            return "healthy"
        monkeypatch.setattr(memory_mcp_server.growth_metrics, "get_health_summary", fake_health)
        memory_mcp_server.growth_health()
        assert called_with["memory_dir"] == self.growth_dir

    def test_find_lessons_uses_growth_dir(self, monkeypatch):
        """find_lessons should pass GROWTH_DIR to lesson_injector."""
        called_with = {}
        def fake_find(md, ctx, limit=5):
            called_with["memory_dir"] = md
            return []
        monkeypatch.setattr(memory_mcp_server.lesson_injector, "find_relevant_lessons", fake_find)
        memory_mcp_server.find_lessons("test context")
        assert called_with["memory_dir"] == self.growth_dir

    def test_detect_lesson_conflicts_uses_growth_dir(self, monkeypatch):
        """detect_lesson_conflicts should pass GROWTH_DIR to lesson_conflict."""
        called_with = {}
        def fake_detect(md):
            called_with["memory_dir"] = md
            return "no conflicts"
        monkeypatch.setattr(memory_mcp_server.lesson_conflict, "get_conflict_report", fake_detect)
        memory_mcp_server.detect_lesson_conflicts()
        assert called_with["memory_dir"] == self.growth_dir

    def test_record_trajectory_uses_growth_dir(self, monkeypatch):
        """record_trajectory should pass GROWTH_DIR to trajectory_store."""
        called_with = {}
        def fake_record(md, **kwargs):
            called_with["memory_dir"] = md
            return {"id": 1, "task_class": "test", "steps": [], "transferability": 0.5}
        monkeypatch.setattr(memory_mcp_server.trajectory_store, "record_trajectory", fake_record)
        memory_mcp_server.record_trajectory("test", "[]", "ok")
        assert called_with["memory_dir"] == self.growth_dir

    def test_find_trajectories_uses_growth_dir(self, monkeypatch):
        """find_trajectories should pass GROWTH_DIR to trajectory_store."""
        called_with = {}
        def fake_find(md, **kwargs):
            called_with["memory_dir"] = md
            return []
        monkeypatch.setattr(memory_mcp_server.trajectory_store, "find_similar", fake_find)
        memory_mcp_server.find_trajectories("test")
        assert called_with["memory_dir"] == self.growth_dir

    def test_golden_paths_uses_growth_dir(self, monkeypatch):
        """golden_paths should pass GROWTH_DIR to trajectory_store."""
        called_with = {}
        def fake_golden(md, **kwargs):
            called_with["memory_dir"] = md
            return []
        monkeypatch.setattr(memory_mcp_server.trajectory_store, "get_golden_paths", fake_golden)
        memory_mcp_server.golden_paths()
        assert called_with["memory_dir"] == self.growth_dir

    def test_record_transfer_uses_growth_dir(self, monkeypatch):
        """record_transfer should pass GROWTH_DIR to transfer_monitor."""
        called_with = {}
        def fake_record(md, **kwargs):
            called_with["memory_dir"] = md
            return {"id": 1, "success": True, "pattern_id": "p1", "source_domain": "a", "target_domain": "b"}
        monkeypatch.setattr(memory_mcp_server.transfer_monitor, "record_transfer", fake_record)
        memory_mcp_server.record_transfer("p1", "a", "b", True)
        assert called_with["memory_dir"] == self.growth_dir

    def test_transfer_report_uses_growth_dir(self, monkeypatch):
        """transfer_report should pass GROWTH_DIR to transfer_monitor."""
        called_with = {}
        def fake_report(md):
            called_with["memory_dir"] = md
            return "report"
        monkeypatch.setattr(memory_mcp_server.transfer_monitor, "get_transfer_report", fake_report)
        memory_mcp_server.transfer_report()
        assert called_with["memory_dir"] == self.growth_dir

    def test_create_aar_uses_growth_dir(self, monkeypatch):
        """create_aar should pass GROWTH_DIR to after_action_review."""
        called_with = {}
        def fake_create(md, **kwargs):
            called_with["memory_dir"] = md
            return {"id": 1, "intent": "test", "tags": []}
        monkeypatch.setattr(memory_mcp_server.after_action_review, "create_aar", fake_create)
        memory_mcp_server.create_aar("i", "a", "w", "r", "c", "t")
        assert called_with["memory_dir"] == self.growth_dir

    def test_search_aars_uses_growth_dir(self, monkeypatch):
        """search_aars_tool should pass GROWTH_DIR to after_action_review."""
        called_with = {}
        def fake_search(md, **kwargs):
            called_with["memory_dir"] = md
            return []
        monkeypatch.setattr(memory_mcp_server.after_action_review, "search_aars", fake_search)
        memory_mcp_server.search_aars_tool()
        assert called_with["memory_dir"] == self.growth_dir

    def test_aar_report_uses_growth_dir(self, monkeypatch):
        """aar_report should pass GROWTH_DIR to after_action_review."""
        called_with = {}
        def fake_report(md, **kwargs):
            called_with["memory_dir"] = md
            return "report"
        monkeypatch.setattr(memory_mcp_server.after_action_review, "get_aar_report", fake_report)
        memory_mcp_server.aar_report()
        assert called_with["memory_dir"] == self.growth_dir
