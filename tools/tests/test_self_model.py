#!/usr/bin/env python3
"""Tests for self_model.py — self-state observation module."""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

# Add tools directory to path
TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import self_model
import emotion_state


# --- Fixtures ---

@pytest.fixture
def tmp_memory_dir(tmp_path):
    """Create a temporary memory directory."""
    return str(tmp_path)


@pytest.fixture
def memory_with_emotion(tmp_memory_dir):
    """Memory dir with a saved emotion state."""
    state = {
        "fulfillment": 0.5,
        "tension": -0.1,
        "affinity": 0.8,
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    path = Path(tmp_memory_dir) / "emotion_state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    return tmp_memory_dir


@pytest.fixture
def memory_with_dynamics(tmp_memory_dir):
    """Memory dir with dynamics state."""
    dyn = {
        "phase": "peak",
        "phase_call_count": 1,
        "accumulation_history": [0.3, 0.4],
        "peak_axis": "fulfillment",
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    path = Path(tmp_memory_dir) / "dynamics_state.json"
    path.write_text(json.dumps(dyn), encoding="utf-8")
    return tmp_memory_dir


@pytest.fixture
def memory_with_change_log(tmp_memory_dir):
    """Memory dir with emotion change log."""
    now = datetime.now(timezone.utc)
    entries = []
    for i in range(5):
        ts = (now - timedelta(minutes=5 * (4 - i))).strftime("%Y-%m-%dT%H:%M:%SZ")
        entries.append({
            "timestamp": ts,
            "before": {"fulfillment": 0.1 * i, "tension": 0.0, "affinity": 0.0},
            "after": {"fulfillment": 0.1 * (i + 1), "tension": 0.0, "affinity": 0.0},
            "reason": f"test change {i}",
        })
    path = Path(tmp_memory_dir) / "emotion_change_log.json"
    path.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    return tmp_memory_dir


@pytest.fixture
def memory_with_episodes(tmp_memory_dir):
    """Memory dir with episode files."""
    episodes_dir = Path(tmp_memory_dir) / "episodes"
    episodes_dir.mkdir()

    now = datetime.now(timezone.utc)
    data = {
        "episodes": [
            {
                "episode_id": "aaa111bbb222",
                "timestamp": (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "summary": "test episode 1",
            },
            {
                "episode_id": "ccc333ddd444",
                "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "summary": "test episode 2",
            },
        ]
    }
    sf = episodes_dir / "session_20260313_120000.json"
    sf.write_text(json.dumps(data), encoding="utf-8")
    return tmp_memory_dir


@pytest.fixture
def memory_with_stm(tmp_memory_dir):
    """Memory dir with short-term memory entries."""
    store = {
        "entries": [
            {"id": "abc", "category": "thought", "content": "hello", "weight": 1.0},
            {"id": "def", "category": "question", "content": "world", "weight": 0.75},
            {"id": "ghi", "category": "feeling", "content": "test", "weight": 0.5},
        ],
        "session_count": 2,
    }
    path = Path(tmp_memory_dir) / "short_term_memory.json"
    path.write_text(json.dumps(store), encoding="utf-8")
    return tmp_memory_dir


# === Tests for _axis_to_category ===

class TestAxisToCategory:
    def test_strongly_positive(self):
        assert self_model._axis_to_category(0.7) == "strongly_positive"
        assert self_model._axis_to_category(1.0) == "strongly_positive"
        assert self_model._axis_to_category(0.9) == "strongly_positive"

    def test_positive(self):
        assert self_model._axis_to_category(0.3) == "positive"
        assert self_model._axis_to_category(0.5) == "positive"
        assert self_model._axis_to_category(0.69) == "positive"

    def test_neutral(self):
        assert self_model._axis_to_category(0.0) == "neutral"
        assert self_model._axis_to_category(0.29) == "neutral"
        assert self_model._axis_to_category(-0.29) == "neutral"

    def test_negative(self):
        assert self_model._axis_to_category(-0.3) == "negative"
        assert self_model._axis_to_category(-0.5) == "negative"
        assert self_model._axis_to_category(-0.69) == "negative"

    def test_strongly_negative(self):
        assert self_model._axis_to_category(-0.7) == "strongly_negative"
        assert self_model._axis_to_category(-1.0) == "strongly_negative"
        assert self_model._axis_to_category(-0.9) == "strongly_negative"

    def test_boundary_positive(self):
        """Test the boundary at +0.3."""
        assert self_model._axis_to_category(0.3) == "positive"
        assert self_model._axis_to_category(0.299) == "neutral"

    def test_boundary_negative(self):
        """Test the boundary at -0.3."""
        assert self_model._axis_to_category(-0.3) == "negative"
        assert self_model._axis_to_category(-0.299) == "neutral"

    def test_boundary_strongly_positive(self):
        """Test the boundary at +0.7."""
        assert self_model._axis_to_category(0.7) == "strongly_positive"
        assert self_model._axis_to_category(0.699) == "positive"

    def test_boundary_strongly_negative(self):
        """Test the boundary at -0.7."""
        assert self_model._axis_to_category(-0.7) == "strongly_negative"
        assert self_model._axis_to_category(-0.699) == "negative"


# === Tests for _observe_emotion ===

class TestObserveEmotion:
    def test_with_emotion_state(self, memory_with_emotion):
        result = self_model._observe_emotion(memory_with_emotion)
        assert result["fulfillment"] == "positive"
        assert result["tension"] == "neutral"
        assert result["affinity"] == "strongly_positive"
        assert result["dynamics_phase"] == "normal"
        assert "description" in result
        assert isinstance(result["description"], str)
        assert len(result["description"]) > 0

    def test_no_emotion_file(self, tmp_memory_dir):
        """When no emotion state exists, defaults to neutral."""
        result = self_model._observe_emotion(tmp_memory_dir)
        assert result["fulfillment"] == "neutral"
        assert result["tension"] == "neutral"
        assert result["affinity"] == "neutral"
        assert result["dynamics_phase"] == "normal"

    def test_with_dynamics_peak(self, memory_with_emotion, memory_with_dynamics):
        """Dynamics phase is correctly read when peak."""
        # Combine: write emotion state into the dynamics dir
        emo_state = {
            "fulfillment": 0.5, "tension": 0.0, "affinity": 0.0,
            "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        emo_path = Path(memory_with_dynamics) / "emotion_state.json"
        emo_path.write_text(json.dumps(emo_state), encoding="utf-8")

        result = self_model._observe_emotion(memory_with_dynamics)
        assert result["dynamics_phase"] == "peak"

    def test_description_no_numbers(self, memory_with_emotion):
        """Description should not contain raw numbers."""
        result = self_model._observe_emotion(memory_with_emotion)
        desc = result["description"]
        # Should not contain decimal numbers like 0.5 or -0.1
        import re
        assert not re.search(r'-?\d+\.\d+', desc)


# === Tests for _compute_trend ===

class TestComputeTrend:
    def test_rising(self):
        changes = [
            {"before": {"fulfillment": 0.0}, "after": {"fulfillment": 0.2}},
            {"before": {"fulfillment": 0.2}, "after": {"fulfillment": 0.3}},
            {"before": {"fulfillment": 0.3}, "after": {"fulfillment": 0.5}},
        ]
        assert self_model._compute_trend(changes, "fulfillment") == "rising"

    def test_falling(self):
        changes = [
            {"before": {"tension": 0.5}, "after": {"tension": 0.3}},
            {"before": {"tension": 0.3}, "after": {"tension": 0.1}},
            {"before": {"tension": 0.1}, "after": {"tension": -0.1}},
        ]
        assert self_model._compute_trend(changes, "tension") == "falling"

    def test_stable(self):
        changes = [
            {"before": {"affinity": 0.5}, "after": {"affinity": 0.51}},
            {"before": {"affinity": 0.51}, "after": {"affinity": 0.50}},
            {"before": {"affinity": 0.50}, "after": {"affinity": 0.52}},
        ]
        assert self_model._compute_trend(changes, "affinity") == "stable"

    def test_fluctuating(self):
        changes = [
            {"before": {"fulfillment": 0.0}, "after": {"fulfillment": 0.3}},
            {"before": {"fulfillment": 0.3}, "after": {"fulfillment": 0.0}},
            {"before": {"fulfillment": 0.0}, "after": {"fulfillment": 0.3}},
            {"before": {"fulfillment": 0.3}, "after": {"fulfillment": 0.0}},
            {"before": {"fulfillment": 0.0}, "after": {"fulfillment": 0.3}},
        ]
        # 3 positive, 2 negative -> rising (3 >= 3)
        # Actually this is 3 positive and 2 negative, so rising
        # Let me make a proper fluctuating case:
        changes_fluct = [
            {"before": {"fulfillment": 0.0}, "after": {"fulfillment": 0.3}},
            {"before": {"fulfillment": 0.3}, "after": {"fulfillment": 0.0}},
            {"before": {"fulfillment": 0.0}, "after": {"fulfillment": 0.3}},
            {"before": {"fulfillment": 0.3}, "after": {"fulfillment": 0.0}},
        ]
        # 2 positive, 2 negative -> fluctuating
        assert self_model._compute_trend(changes_fluct, "fulfillment") == "fluctuating"

    def test_empty_changes(self):
        assert self_model._compute_trend([], "fulfillment") == "stable"

    def test_missing_axis_defaults(self):
        changes = [
            {"before": {}, "after": {}},
            {"before": {}, "after": {}},
            {"before": {}, "after": {}},
        ]
        # All diffs are 0, so stable
        assert self_model._compute_trend(changes, "fulfillment") == "stable"


# === Tests for _observe_changes ===

class TestObserveChanges:
    def test_with_change_log(self, memory_with_change_log):
        result = self_model._observe_changes(memory_with_change_log)
        assert "trends" in result
        assert "frequency" in result
        assert "description" in result
        assert result["frequency"] == "high"  # 5 entries
        assert result["trends"]["fulfillment"] == "rising"  # all positive diffs

    def test_no_change_log(self, tmp_memory_dir):
        result = self_model._observe_changes(tmp_memory_dir)
        assert result["frequency"] == "none"
        assert result["trends"]["fulfillment"] == "stable"

    def test_frequency_moderate(self, tmp_memory_dir):
        """3-4 entries -> moderate."""
        now = datetime.now(timezone.utc)
        entries = []
        for i in range(3):
            ts = (now - timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
            entries.append({
                "timestamp": ts,
                "before": {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0},
                "after": {"fulfillment": 0.1, "tension": 0.0, "affinity": 0.0},
                "reason": "",
            })
        path = Path(tmp_memory_dir) / "emotion_change_log.json"
        path.write_text(json.dumps({"entries": entries}), encoding="utf-8")

        result = self_model._observe_changes(tmp_memory_dir)
        assert result["frequency"] == "moderate"

    def test_frequency_low(self, tmp_memory_dir):
        """1-2 entries -> low."""
        now = datetime.now(timezone.utc)
        entries = [{
            "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "before": {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0},
            "after": {"fulfillment": 0.1, "tension": 0.0, "affinity": 0.0},
            "reason": "",
        }]
        path = Path(tmp_memory_dir) / "emotion_change_log.json"
        path.write_text(json.dumps({"entries": entries}), encoding="utf-8")

        result = self_model._observe_changes(tmp_memory_dir)
        assert result["frequency"] == "low"


# === Tests for memory observation ===

class TestObserveMemory:
    def test_with_episodes(self, memory_with_episodes):
        result = self_model._observe_memory(memory_with_episodes)
        assert result["episode_count"] == 2
        assert result["last_episode_age"] == "数分前"
        assert "description" in result

    def test_no_episodes_dir(self, tmp_memory_dir):
        result = self_model._observe_memory(tmp_memory_dir)
        assert result["episode_count"] == 0
        assert result["last_episode_age"] == "不明"

    def test_with_stm(self, memory_with_stm):
        result = self_model._observe_memory(memory_with_stm)
        assert result["stm_entries"] == 3

    def test_no_stm(self, tmp_memory_dir):
        result = self_model._observe_memory(tmp_memory_dir)
        assert result["stm_entries"] == 0

    def test_episode_age_hours(self, tmp_memory_dir):
        """Episode recorded hours ago."""
        episodes_dir = Path(tmp_memory_dir) / "episodes"
        episodes_dir.mkdir()
        old_time = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        data = {"episodes": [{"episode_id": "x", "timestamp": old_time, "summary": "old"}]}
        sf = episodes_dir / "session_old.json"
        sf.write_text(json.dumps(data), encoding="utf-8")

        result = self_model._observe_memory(tmp_memory_dir)
        assert result["last_episode_age"] == "数時間前"

    def test_episode_age_days(self, tmp_memory_dir):
        """Episode recorded days ago."""
        episodes_dir = Path(tmp_memory_dir) / "episodes"
        episodes_dir.mkdir()
        old_time = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        data = {"episodes": [{"episode_id": "x", "timestamp": old_time, "summary": "old"}]}
        sf = episodes_dir / "session_old.json"
        sf.write_text(json.dumps(data), encoding="utf-8")

        result = self_model._observe_memory(tmp_memory_dir)
        assert result["last_episode_age"] == "数日前"

    def test_corrupt_stm_file(self, tmp_memory_dir):
        """Corrupt STM file returns 0."""
        path = Path(tmp_memory_dir) / "short_term_memory.json"
        path.write_text("not json", encoding="utf-8")
        assert self_model._count_stm_entries(tmp_memory_dir) == 0

    def test_corrupt_episode_file(self, tmp_memory_dir):
        """Corrupt episode file is skipped gracefully."""
        episodes_dir = Path(tmp_memory_dir) / "episodes"
        episodes_dir.mkdir()
        sf = episodes_dir / "session_bad.json"
        sf.write_text("not json", encoding="utf-8")

        result = self_model._observe_memory(tmp_memory_dir)
        assert result["episode_count"] == 0


# === Tests for integration ===

class TestIntegrate:
    def test_basic_integration(self):
        emotion = {
            "fulfillment": "positive",
            "tension": "neutral",
            "affinity": "strongly_positive",
            "dynamics_phase": "normal",
            "description": "充実感はやや正、緊張は中立、親和性は強く正。感情動力学は通常フェーズ。",
        }
        change = {
            "trends": {"fulfillment": "rising", "tension": "stable", "affinity": "stable"},
            "frequency": "moderate",
            "description": "変化頻度は中頻度。",
        }
        memory = {
            "episode_count": 120,
            "last_episode_age": "数分前",
            "stm_entries": 5,
            "description": "記憶には120のエピソードがあり、短期記憶に5つの項目がある。",
        }

        result = self_model._integrate(emotion, change, memory)
        assert isinstance(result, str)
        assert len(result) > 0
        # Should not contain raw float numbers
        import re
        assert not re.search(r'\d+\.\d+', result)

    def test_integration_no_episodes(self):
        emotion = {
            "fulfillment": "neutral", "tension": "neutral", "affinity": "neutral",
            "dynamics_phase": "normal",
            "description": "充実感は中立、緊張は中立、親和性は中立。感情動力学は通常フェーズ。",
        }
        change = {
            "trends": {"fulfillment": "stable", "tension": "stable", "affinity": "stable"},
            "frequency": "none",
            "description": "変化なし。",
        }
        memory = {
            "episode_count": 0, "last_episode_age": "不明", "stm_entries": 0,
            "description": "エピソード記録はまだない。短期記憶は空。",
        }

        result = self_model._integrate(emotion, change, memory)
        assert "エピソード" in result


# === Tests for observe() (full pipeline) ===

class TestObserve:
    def test_full_observe_empty(self, tmp_memory_dir):
        """Observe on empty memory dir returns valid structure."""
        result = self_model.observe(tmp_memory_dir)
        assert "emotion" in result
        assert "change" in result
        assert "memory" in result
        assert "integrated" in result
        assert isinstance(result["integrated"], str)

    def test_full_observe_with_data(self, memory_with_emotion):
        """Observe with emotion state returns correct categories."""
        # Add change log
        now = datetime.now(timezone.utc)
        entries = []
        for i in range(3):
            ts = (now - timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
            entries.append({
                "timestamp": ts,
                "before": {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0},
                "after": {"fulfillment": 0.1, "tension": 0.0, "affinity": 0.0},
                "reason": "",
            })
        log_path = Path(memory_with_emotion) / "emotion_change_log.json"
        log_path.write_text(json.dumps({"entries": entries}), encoding="utf-8")

        result = self_model.observe(memory_with_emotion)
        assert result["emotion"]["fulfillment"] == "positive"
        assert result["change"]["frequency"] == "moderate"
        assert result["memory"]["episode_count"] == 0

    def test_observe_returns_no_raw_numbers(self, memory_with_emotion):
        """Integrated text should not expose raw numbers."""
        result = self_model.observe(memory_with_emotion)
        integrated = result["integrated"]
        import re
        # No floats like 0.5 or -0.1
        assert not re.search(r'-?\d+\.\d+', integrated)

    def test_observe_no_evaluation_words(self, memory_with_emotion):
        """Integrated text should not contain evaluation words."""
        result = self_model.observe(memory_with_emotion)
        integrated = result["integrated"]
        for word in ["良い", "悪い", "健全", "異常", "望ましい", "望ましくない"]:
            assert word not in integrated
