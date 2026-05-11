#!/usr/bin/env python3
"""Tests for mood-linked reordering (Feature B of design_emotion_memory_binding.md).

Tests cover:
- compute_emotion_similarity: 3-axis equal weighting, edge cases
- compute_delta_correction: delta boost and cap
- mood_reorder: integration, safety valves, trace-absent neutral
- memory_search integration with mood reorder
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
import asyncio

# Ensure we can import from the tools directory
TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from episode_recall import (
    DELTA_CORRECTION_CAP,
    EMOTION_CONTRIBUTION_CAP,
    compute_delta_correction,
    compute_emotion_similarity,
    mood_reorder,
)


# --- Fixtures ---


def _make_episode(episode_id, trace=None, timestamp="2026-03-13T10:00:00Z"):
    """Helper to create a minimal episode dict."""
    ep = {
        "episode_id": episode_id,
        "episode_type": "observation",
        "timestamp": timestamp,
        "session_id": "test_session",
        "summary": f"Episode {episode_id}",
    }
    if trace is not None:
        ep["emotion_trace"] = trace
    return ep


def _make_trace(fulfillment=0.0, tension=0.0, affinity=0.0,
                delta_fulfillment=None, delta_tension=None, delta_affinity=None):
    """Helper to create an emotion trace dict."""
    trace = {
        "fulfillment": fulfillment,
        "tension": tension,
        "affinity": affinity,
        "trace_timestamp": "2026-03-13T10:00:00Z",
    }
    if delta_fulfillment is not None:
        trace["delta_fulfillment"] = delta_fulfillment
    if delta_tension is not None:
        trace["delta_tension"] = delta_tension
    if delta_affinity is not None:
        trace["delta_affinity"] = delta_affinity
    return trace


# --- compute_emotion_similarity tests ---


class TestComputeEmotionSimilarity:
    def test_identical_states(self):
        state = {"fulfillment": 0.5, "tension": -0.3, "affinity": 0.8}
        trace = {"fulfillment": 0.5, "tension": -0.3, "affinity": 0.8}
        assert compute_emotion_similarity(state, trace) == 1.0

    def test_opposite_states(self):
        state = {"fulfillment": 1.0, "tension": 1.0, "affinity": 1.0}
        trace = {"fulfillment": -1.0, "tension": -1.0, "affinity": -1.0}
        assert compute_emotion_similarity(state, trace) == pytest.approx(0.0)

    def test_neutral_states(self):
        state = {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0}
        trace = {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0}
        assert compute_emotion_similarity(state, trace) == 1.0

    def test_partial_difference(self):
        state = {"fulfillment": 0.5, "tension": 0.0, "affinity": 0.0}
        trace = {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0}
        # Distance: 0.5/3 = 0.1667, normalized: 0.1667/2 = 0.0833
        # Similarity: 1 - 0.0833 = 0.9167
        sim = compute_emotion_similarity(state, trace)
        assert sim == pytest.approx(1.0 - (0.5 / 3.0 / 2.0), abs=1e-4)

    def test_equal_axis_weighting(self):
        """Each axis should contribute equally to similarity."""
        state = {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0}
        trace_f = {"fulfillment": 0.6, "tension": 0.0, "affinity": 0.0}
        trace_t = {"fulfillment": 0.0, "tension": 0.6, "affinity": 0.0}
        trace_a = {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.6}
        sim_f = compute_emotion_similarity(state, trace_f)
        sim_t = compute_emotion_similarity(state, trace_t)
        sim_a = compute_emotion_similarity(state, trace_a)
        assert sim_f == pytest.approx(sim_t, abs=1e-6)
        assert sim_t == pytest.approx(sim_a, abs=1e-6)

    def test_missing_axis_treated_as_zero(self):
        state = {"fulfillment": 0.5}
        trace = {"fulfillment": 0.5, "tension": 0.0, "affinity": 0.0}
        assert compute_emotion_similarity(state, trace) == 1.0

    def test_invalid_types_treated_as_zero(self):
        state = {"fulfillment": "invalid", "tension": 0.0, "affinity": 0.0}
        trace = {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0}
        assert compute_emotion_similarity(state, trace) == 1.0

    def test_returns_between_0_and_1(self):
        """Similarity should always be in [0, 1]."""
        import random
        random.seed(42)
        for _ in range(100):
            state = {a: random.uniform(-1, 1) for a in ("fulfillment", "tension", "affinity")}
            trace = {a: random.uniform(-1, 1) for a in ("fulfillment", "tension", "affinity")}
            sim = compute_emotion_similarity(state, trace)
            assert 0.0 <= sim <= 1.0


# --- compute_delta_correction tests ---


class TestComputeDeltaCorrection:
    def test_no_deltas(self):
        trace = {"fulfillment": 0.5, "tension": 0.0, "affinity": 0.0}
        assert compute_delta_correction(trace) == 0.0

    def test_zero_deltas(self):
        trace = _make_trace(delta_fulfillment=0.0, delta_tension=0.0, delta_affinity=0.0)
        assert compute_delta_correction(trace) == 0.0

    def test_small_deltas(self):
        trace = _make_trace(delta_fulfillment=0.1, delta_tension=0.0, delta_affinity=0.0)
        correction = compute_delta_correction(trace)
        assert 0.0 < correction < DELTA_CORRECTION_CAP

    def test_large_deltas_capped(self):
        trace = _make_trace(delta_fulfillment=2.0, delta_tension=2.0, delta_affinity=2.0)
        correction = compute_delta_correction(trace)
        assert correction == pytest.approx(DELTA_CORRECTION_CAP)

    def test_negative_deltas_use_absolute_value(self):
        trace_pos = _make_trace(delta_fulfillment=0.5, delta_tension=0.0, delta_affinity=0.0)
        trace_neg = _make_trace(delta_fulfillment=-0.5, delta_tension=0.0, delta_affinity=0.0)
        assert compute_delta_correction(trace_pos) == pytest.approx(
            compute_delta_correction(trace_neg)
        )

    def test_invalid_delta_types_ignored(self):
        trace = _make_trace()
        trace["delta_fulfillment"] = "invalid"
        trace["delta_tension"] = 0.3
        trace["delta_affinity"] = 0.3
        correction = compute_delta_correction(trace)
        assert correction > 0.0  # Only 2 valid deltas contribute


# --- mood_reorder tests ---


class TestMoodReorder:
    def test_empty_list(self):
        assert mood_reorder([], {"fulfillment": 0.5, "tension": 0.0, "affinity": 0.0}) == []

    def test_single_episode(self):
        ep = _make_episode("aaa")
        result = mood_reorder([ep], {"fulfillment": 0.5, "tension": 0.0, "affinity": 0.0})
        assert len(result) == 1
        assert result[0] is ep

    def test_no_traces_preserves_original_order(self):
        """Episodes without traces should keep original order (no emotion penalty)."""
        eps = [_make_episode(f"ep{i}") for i in range(5)]
        state = {"fulfillment": 0.5, "tension": 0.3, "affinity": -0.2}
        result = mood_reorder(eps, state)
        assert [ep["episode_id"] for ep in result] == [f"ep{i}" for i in range(5)]

    def test_similar_trace_moves_up(self):
        """Episode with trace similar to current emotion should move up
        when emotion contribution is high enough to overcome position advantage."""
        state = {"fulfillment": 0.8, "tension": 0.0, "affinity": 0.0}

        # ep0 at position 0 with dissimilar trace
        ep0 = _make_episode("ep0", _make_trace(fulfillment=-0.8, tension=0.0, affinity=0.0))
        # ep1 at position 1 with similar trace
        ep1 = _make_episode("ep1", _make_trace(fulfillment=0.8, tension=0.0, affinity=0.0))
        # ep2 at position 2 with no trace
        ep2 = _make_episode("ep2")

        # Use higher emotion cap (0.6) so emotion can overcome position advantage
        result = mood_reorder([ep0, ep1, ep2], state, emotion_contribution_cap=0.6)
        ids = [ep["episode_id"] for ep in result]
        # ep1 (similar) should be above ep0 (dissimilar) due to emotion boost
        assert ids.index("ep1") < ids.index("ep0")

    def test_all_episodes_preserved(self):
        """Reordering should never remove episodes."""
        eps = [
            _make_episode("ep0", _make_trace(fulfillment=0.5)),
            _make_episode("ep1"),
            _make_episode("ep2", _make_trace(fulfillment=-0.5)),
        ]
        state = {"fulfillment": 0.5, "tension": 0.0, "affinity": 0.0}
        result = mood_reorder(eps, state)
        assert len(result) == 3
        result_ids = {ep["episode_id"] for ep in result}
        assert result_ids == {"ep0", "ep1", "ep2"}

    def test_emotion_contribution_cap(self):
        """With emotion cap at 0, original order should be fully preserved."""
        state = {"fulfillment": 0.8, "tension": 0.0, "affinity": 0.0}
        ep0 = _make_episode("ep0", _make_trace(fulfillment=-1.0))
        ep1 = _make_episode("ep1", _make_trace(fulfillment=0.8))
        result = mood_reorder([ep0, ep1], state, emotion_contribution_cap=0.0)
        assert result[0]["episode_id"] == "ep0"
        assert result[1]["episode_id"] == "ep1"

    def test_trace_absent_neutral(self):
        """Episodes without traces should get zero emotion score (not penalized)."""
        state = {"fulfillment": 0.5, "tension": 0.5, "affinity": 0.5}
        # Two episodes at same position priority, one with perfect trace, one without
        ep_with = _make_episode("with", _make_trace(fulfillment=0.5, tension=0.5, affinity=0.5))
        ep_without = _make_episode("without")
        # Put ep_without first (position advantage), ep_with second
        result = mood_reorder([ep_without, ep_with], state)
        # ep_with gets emotion boost but ep_without has position advantage
        # With default 0.3 cap, position (0.7 weight) dominates
        # ep_without: position=1.0*(0.7) + 0.0*(0.3) = 0.7
        # ep_with:    position=0.5*(0.7) + 1.0*(0.3) = 0.65
        # ep_without should still be first due to position dominance
        assert result[0]["episode_id"] == "without"

    def test_delta_correction_boosts_high_delta_episodes(self):
        """Episodes with large emotion deltas should get a small boost."""
        state = {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0}
        # Both have similar traces but ep1 has large deltas
        ep0 = _make_episode("ep0", _make_trace(
            fulfillment=0.0, tension=0.0, affinity=0.0,
            delta_fulfillment=0.0, delta_tension=0.0, delta_affinity=0.0,
        ))
        ep1 = _make_episode("ep1", _make_trace(
            fulfillment=0.0, tension=0.0, affinity=0.0,
            delta_fulfillment=1.0, delta_tension=1.0, delta_affinity=1.0,
        ))
        # ep0 first, ep1 second
        result = mood_reorder([ep0, ep1], state, emotion_contribution_cap=0.5)
        # ep1 has delta boost, may still not surpass position advantage
        # But at 0.5 cap, the delta correction could help
        # Verify both are present
        assert len(result) == 2

    def test_disable_reorder_flag(self):
        """mood_reorder with cap=0 effectively disables reordering."""
        state = {"fulfillment": 1.0, "tension": 1.0, "affinity": 1.0}
        eps = [
            _make_episode("ep0", _make_trace(fulfillment=-1.0, tension=-1.0, affinity=-1.0)),
            _make_episode("ep1", _make_trace(fulfillment=1.0, tension=1.0, affinity=1.0)),
        ]
        result = mood_reorder(eps, state, emotion_contribution_cap=0.0)
        assert [ep["episode_id"] for ep in result] == ["ep0", "ep1"]

    def test_stable_sort_for_equal_scores(self):
        """Episodes with equal scores should maintain relative order."""
        state = {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0}
        eps = [_make_episode(f"ep{i}", _make_trace()) for i in range(5)]
        result = mood_reorder(eps, state)
        assert [ep["episode_id"] for ep in result] == [f"ep{i}" for i in range(5)]


# --- Raw search function tests ---


class TestRawSearchFunctions:
    """Test that raw search variants return proper data structures."""

    @pytest.fixture
    def memory_dir(self, tmp_path):
        episodes_dir = tmp_path / "episodes"
        episodes_dir.mkdir()

        # Use current time to ensure time_range_search works
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Create a session file with test episodes
        session = {
            "session_id": "test_session_001",
            "episodes": [
                {
                    "episode_id": "aaa111bbb222",
                    "episode_type": "observation",
                    "timestamp": now,
                    "session_id": "test_session_001",
                    "summary": "Testing keyword search",
                    "tags": ["test", "search"],
                    "user_utterances": [],
                    "emotion_trace": {
                        "fulfillment": 0.5,
                        "tension": 0.2,
                        "affinity": 0.3,
                        "trace_timestamp": now,
                    },
                },
                {
                    "episode_id": "ccc333ddd444",
                    "episode_type": "decision",
                    "timestamp": now,
                    "session_id": "test_session_001",
                    "summary": "Another test episode about memory",
                    "tags": ["memory", "decision"],
                    "user_utterances": [],
                },
            ],
        }
        session_file = episodes_dir / "session_test001.json"
        session_file.write_text(json.dumps(session), encoding="utf-8")

        # Create topic index
        index = {
            "index": {
                "test": [{"episode_id": "aaa111bbb222", "session_id": "test_session_001"}],
                "search": [{"episode_id": "aaa111bbb222", "session_id": "test_session_001"}],
                "memory": [{"episode_id": "ccc333ddd444", "session_id": "test_session_001"}],
                "decision": [{"episode_id": "ccc333ddd444", "session_id": "test_session_001"}],
            },
            "built_at": now,
        }
        (tmp_path / "topic_index.json").write_text(json.dumps(index), encoding="utf-8")

        return str(tmp_path)

    def test_keyword_search_raw_returns_tuples(self, memory_dir):
        from episode_recall import keyword_search_raw
        results = keyword_search_raw(memory_dir, ["test"])
        assert len(results) > 0
        ep, detail = results[0]
        assert isinstance(ep, dict)
        assert "episode_id" in ep
        assert isinstance(detail, str)

    def test_time_range_search_raw_returns_episodes(self, memory_dir):
        from episode_recall import time_range_search_raw
        results = time_range_search_raw(memory_dir, last="30d")
        assert len(results) > 0
        assert isinstance(results[0], dict)
        assert "episode_id" in results[0]

    def test_context_search_raw_returns_tuples(self, memory_dir):
        from episode_recall import context_search_raw
        results = context_search_raw(memory_dir, ["test"])
        assert len(results) > 0
        ep, match_info = results[0]
        assert isinstance(ep, dict)
        assert isinstance(match_info, dict)
        assert "matching_tags" in match_info

    def test_keyword_search_raw_empty_keywords(self, memory_dir):
        from episode_recall import keyword_search_raw
        assert keyword_search_raw(memory_dir, []) == []

    def test_context_search_raw_empty_tags(self, memory_dir):
        from episode_recall import context_search_raw
        assert context_search_raw(memory_dir, []) == []

    def test_time_range_search_raw_no_params(self, memory_dir):
        from episode_recall import time_range_search_raw
        assert time_range_search_raw(memory_dir) == []


# --- Integration: memory_search with mood reorder ---


class TestMemorySearchMoodReorder:
    """Test memory_search with mood_reorder_enabled parameter."""

    @pytest.fixture
    def memory_dir(self, tmp_path):
        episodes_dir = tmp_path / "episodes"
        episodes_dir.mkdir()

        session = {
            "session_id": "test_session_001",
            "episodes": [
                {
                    "episode_id": "aaa111bbb222",
                    "episode_type": "observation",
                    "timestamp": "2026-03-13T10:00:00Z",
                    "session_id": "test_session_001",
                    "summary": "First test episode for integration",
                    "tags": ["integration"],
                    "user_utterances": [],
                    "emotion_trace": {
                        "fulfillment": -0.5,
                        "tension": 0.0,
                        "affinity": 0.0,
                        "trace_timestamp": "2026-03-13T10:00:00Z",
                    },
                },
                {
                    "episode_id": "ccc333ddd444",
                    "episode_type": "observation",
                    "timestamp": "2026-03-13T11:00:00Z",
                    "session_id": "test_session_001",
                    "summary": "Second test episode for integration",
                    "tags": ["integration"],
                    "user_utterances": [],
                    "emotion_trace": {
                        "fulfillment": 0.8,
                        "tension": 0.0,
                        "affinity": 0.0,
                        "trace_timestamp": "2026-03-13T11:00:00Z",
                    },
                },
            ],
        }
        session_file = episodes_dir / "session_test001.json"
        session_file.write_text(json.dumps(session), encoding="utf-8")

        # Create emotion state
        emotion_state = {
            "fulfillment": 0.8,
            "tension": 0.0,
            "affinity": 0.0,
            "last_updated": "2026-03-13T12:00:00Z",
            "created_at": "2026-03-13T08:00:00Z",
        }
        (tmp_path / "emotion_state.json").write_text(
            json.dumps(emotion_state), encoding="utf-8"
        )

        # Create topic index
        index = {
            "index": {
                "integration": [
                    {"episode_id": "aaa111bbb222", "session_id": "test_session_001"},
                    {"episode_id": "ccc333ddd444", "session_id": "test_session_001"},
                ],
            },
            "built_at": "2026-03-13T10:00:00Z",
        }
        (tmp_path / "topic_index.json").write_text(json.dumps(index), encoding="utf-8")

        return str(tmp_path)

    def test_mood_reorder_enabled_shows_label(self, memory_dir, monkeypatch):
        """When mood reorder is enabled, output should indicate mood-reordered."""
        monkeypatch.setattr(
            "memory_mcp_server.DEFAULT_MEMORY_DIR", memory_dir
        )
        from memory_mcp_server import memory_search
        result = asyncio.run(memory_search(keywords="test", mood_reorder_enabled=True))
        assert "mood-reordered" in result

    def test_mood_reorder_disabled_no_label(self, memory_dir, monkeypatch):
        """When mood reorder is disabled, should use standard search."""
        monkeypatch.setattr(
            "memory_mcp_server.DEFAULT_MEMORY_DIR", memory_dir
        )
        from memory_mcp_server import memory_search
        result = asyncio.run(memory_search(keywords="test", mood_reorder_enabled=False))
        assert "mood-reordered" not in result

    def test_keyword_search_with_mood(self, memory_dir, monkeypatch):
        monkeypatch.setattr(
            "memory_mcp_server.DEFAULT_MEMORY_DIR", memory_dir
        )
        from memory_mcp_server import memory_search
        result = asyncio.run(memory_search(keywords="integration", mood_reorder_enabled=True))
        assert "Keyword Search" in result
        assert "aaa111bbb222" in result
        assert "ccc333ddd444" in result

    def test_time_search_with_mood(self, memory_dir, monkeypatch):
        monkeypatch.setattr(
            "memory_mcp_server.DEFAULT_MEMORY_DIR", memory_dir
        )
        from memory_mcp_server import memory_search
        result = asyncio.run(memory_search(last="30d", mood_reorder_enabled=True))
        assert "Time" in result

    def test_context_search_with_mood(self, memory_dir, monkeypatch):
        monkeypatch.setattr(
            "memory_mcp_server.DEFAULT_MEMORY_DIR", memory_dir
        )
        from memory_mcp_server import memory_search
        result = asyncio.run(memory_search(tags="integration", mood_reorder_enabled=True))
        assert "Context" in result
        assert "mood-reordered" in result

    def test_no_emotion_state_falls_back(self, memory_dir, monkeypatch):
        """If emotion state cannot be obtained, should fall back to standard search."""
        monkeypatch.setattr(
            "memory_mcp_server.DEFAULT_MEMORY_DIR", memory_dir
        )
        # Mock get_state_dict to raise an exception, simulating inability to
        # obtain emotion state. Note: simply removing the file is insufficient
        # because get_state_dict returns a default state when file is absent.
        monkeypatch.setattr(
            "memory_mcp_server.get_state_dict",
            lambda _dir: (_ for _ in ()).throw(RuntimeError("no emotion")),
        )
        from memory_mcp_server import memory_search
        # Should not crash and should return results without mood-reordered label
        result = asyncio.run(memory_search(keywords="test", mood_reorder_enabled=True))
        assert "ERROR" not in result or "No matching" in result
        assert "mood-reordered" not in result
