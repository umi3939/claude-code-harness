"""Tests for behavior_guidance.py - Pipeline 1: Behavior Guidance Injection (v2).

New spec: Gap Analysis + emotion state + dynamics phase -> concrete action recommendations.
"""

import json
import os
import sys

import pytest

TOOLS_DIR = os.path.join(os.path.dirname(__file__), "..")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from behavior_guidance import (
    SATURATION_CONSECUTIVE_LIMIT,
    SATURATION_VARIANCE_THRESHOLD,
    generate_guidance,
    _read_gap_analysis,
    _read_emotion_state,
    _recommend_action,
    _reset_saturation,
)


@pytest.fixture(autouse=True)
def reset_saturation():
    """Reset saturation history before each test."""
    _reset_saturation()
    yield
    _reset_saturation()


@pytest.fixture
def memory_dir(tmp_path):
    return str(tmp_path)


@pytest.fixture
def docs_dir(tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    return str(d)


def _write_emotion_state(memory_dir, fulfillment=0.0, tension=0.0, affinity=0.0):
    state = {
        "fulfillment": fulfillment,
        "tension": tension,
        "affinity": affinity,
        "last_updated": "2026-03-30T00:00:00",
    }
    with open(os.path.join(memory_dir, "emotion_state.json"), "w", encoding="utf-8") as f:
        json.dump(state, f)


def _write_dynamics_state(memory_dir, phase="normal"):
    state = {
        "phase": phase,
        "phase_call_count": 0,
        "accumulation_history": [],
    }
    with open(os.path.join(memory_dir, "dynamics_state.json"), "w", encoding="utf-8") as f:
        json.dump(state, f)


def _write_gap_analysis(docs_dir, filename="gap_analysis_c26_20260330.md", gaps=None):
    if gaps is None:
        gaps = [
            ("G30", "behavior-guard二重管理", "プロジェクト+グローバルの2箇所に存在", "中", "新規発見"),
            ("G31", "1:1:1 Hook-Skill-MCP対応", "MCPツール数に合わせてhook/skill作り直し", "高", "新規。Large tier"),
        ]
    lines = [
        "# Gap Analysis - Test",
        "",
        "## 残存ギャップ",
        "",
        "| Gap | 説明 | 重要度 | 状態 |",
        "|-----|------|--------|------|",
    ]
    for item in gaps:
        if len(item) == 5:
            gap_id, title, desc, priority, status = item
            lines.append(f"| {gap_id} {title} | {desc} | {priority} | {status} |")
        else:
            gap_id, title, priority, status = item
            lines.append(f"| {gap_id} {title} | {title} | {priority} | {status} |")
    content = "\n".join(lines) + "\n"
    filepath = os.path.join(docs_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return filepath


class TestReadGapAnalysis:
    def test_reads_gaps_from_latest_file(self, docs_dir):
        _write_gap_analysis(docs_dir, "gap_analysis_c25_20260329.md", [
            ("G1", "old gap", "低", "未解決"),
        ])
        _write_gap_analysis(docs_dir, "gap_analysis_c26_20260330.md", [
            ("G30", "behavior-guard二重管理", "中", "新規発見"),
        ])
        gaps = _read_gap_analysis(docs_dir)
        assert len(gaps) >= 1
        assert any(g["id"] == "G30" for g in gaps)

    def test_extracts_priority(self, docs_dir):
        _write_gap_analysis(docs_dir, gaps=[
            ("G31", "Hook-Skill-MCP対応", "高", "新規"),
        ])
        gaps = _read_gap_analysis(docs_dir)
        assert gaps[0]["priority"] == "高"

    def test_extracts_description(self, docs_dir):
        _write_gap_analysis(docs_dir, gaps=[
            ("G30", "behavior-guard二重管理", "中", "新規発見"),
        ])
        gaps = _read_gap_analysis(docs_dir)
        assert "behavior-guard" in gaps[0]["description"]

    def test_empty_docs_dir_returns_empty(self, tmp_path):
        empty_dir = str(tmp_path / "empty_docs")
        os.makedirs(empty_dir, exist_ok=True)
        gaps = _read_gap_analysis(empty_dir)
        assert gaps == []

    def test_nonexistent_dir_returns_empty(self):
        gaps = _read_gap_analysis("/nonexistent/path")
        assert gaps == []

    def test_malformed_file_returns_empty(self, docs_dir):
        filepath = os.path.join(docs_dir, "gap_analysis_c99_20260401.md")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("no table here\njust text\n")
        gaps = _read_gap_analysis(docs_dir)
        assert gaps == []

    def test_multiple_gaps_extracted(self, docs_dir):
        _write_gap_analysis(docs_dir, gaps=[
            ("G13", "sample feature gap", "高", "未着手"),
            ("G26", "memory_search並列", "中", "hook強制で回避中"),
            ("G30", "behavior-guard二重管理", "中", "新規発見"),
        ])
        gaps = _read_gap_analysis(docs_dir)
        assert len(gaps) == 3


class TestReadEmotionState:
    def test_reads_emotion_and_phase(self, memory_dir):
        _write_emotion_state(memory_dir, fulfillment=0.5, tension=0.1, affinity=0.3)
        _write_dynamics_state(memory_dir, "peak")
        result = _read_emotion_state(memory_dir)
        assert result is not None
        assert result["fulfillment"] == 0.5
        assert result["tension"] == 0.1
        assert result["affinity"] == 0.3
        assert result["phase"] == "peak"

    def test_missing_emotion_file_returns_none(self, memory_dir):
        _write_dynamics_state(memory_dir, "normal")
        result = _read_emotion_state(memory_dir)
        assert result is None

    def test_missing_dynamics_file_returns_none(self, memory_dir):
        _write_emotion_state(memory_dir, tension=0.5)
        result = _read_emotion_state(memory_dir)
        assert result is None

    def test_corrupt_emotion_file_returns_none(self, memory_dir):
        with open(os.path.join(memory_dir, "emotion_state.json"), "w") as f:
            f.write("{broken")
        _write_dynamics_state(memory_dir, "normal")
        result = _read_emotion_state(memory_dir)
        assert result is None


class TestRecommendAction:
    def test_high_fulfillment_low_tension_big_task(self):
        gaps = [{"id": "G30", "description": "behavior-guard二重管理", "priority": "中", "status": "新規"}]
        emotion = {"fulfillment": 0.5, "tension": 0.1, "affinity": 0.3, "phase": "normal"}
        result = _recommend_action(gaps, emotion)
        assert "集中力が高い" in result or "落ち着いた" in result
        assert "G30" in result

    def test_high_tension_cautious(self):
        gaps = [{"id": "G30", "description": "二重管理", "priority": "中", "status": "新規"}]
        emotion = {"fulfillment": 0.0, "tension": 0.5, "affinity": 0.0, "phase": "normal"}
        result = _recommend_action(gaps, emotion)
        assert "慎重" in result or "仕上げ" in result or "既存" in result

    def test_low_fulfillment_small_task(self):
        gaps = [{"id": "G28", "description": "docs/INDEX.md自動更新", "priority": "低", "status": "未解決"}]
        emotion = {"fulfillment": -0.5, "tension": 0.1, "affinity": 0.0, "phase": "normal"}
        result = _recommend_action(gaps, emotion)
        assert "エネルギーが低い" in result or "小さな" in result

    def test_rebound_phase_avoids_big_changes(self):
        gaps = [{"id": "G31", "description": "1:1:1対応", "priority": "高", "status": "新規"}]
        emotion = {"fulfillment": 0.3, "tension": 0.1, "affinity": 0.2, "phase": "rebound"}
        result = _recommend_action(gaps, emotion)
        assert "回復中" in result or "方向転換を避け" in result

    def test_peak_phase_challenging(self):
        gaps = [{"id": "G13", "description": "sample feature gap", "priority": "高", "status": "未着手"}]
        emotion = {"fulfillment": 0.5, "tension": 0.2, "affinity": 0.3, "phase": "peak"}
        result = _recommend_action(gaps, emotion)
        assert "勢い" in result or "チャレンジ" in result

    def test_no_gaps_returns_no_task_recommendation(self):
        emotion = {"fulfillment": 0.5, "tension": 0.1, "affinity": 0.3, "phase": "normal"}
        result = _recommend_action([], emotion)
        assert "残存ギャップなし" in result or result == ""

    def test_high_priority_gap_preferred(self):
        gaps = [
            {"id": "G4", "description": "Discord中継", "priority": "低", "status": "未着手"},
            {"id": "G13", "description": "sample feature gap", "priority": "高", "status": "未着手"},
            {"id": "G30", "description": "二重管理", "priority": "中", "status": "新規"},
        ]
        emotion = {"fulfillment": 0.5, "tension": 0.1, "affinity": 0.3, "phase": "normal"}
        result = _recommend_action(gaps, emotion)
        assert "G13" in result


class TestGenerateGuidance:
    def test_full_integration_with_gaps(self, memory_dir, docs_dir):
        _write_emotion_state(memory_dir, fulfillment=0.5, tension=0.1)
        _write_dynamics_state(memory_dir, "normal")
        _write_gap_analysis(docs_dir, gaps=[("G30", "behavior-guard二重管理", "中", "新規発見")])
        result = generate_guidance(memory_dir, docs_dir)
        assert "[Behavior Guidance]" not in result
        assert "G30" in result
        assert len(result) > 0

    def test_high_tension_with_gaps(self, memory_dir, docs_dir):
        _write_emotion_state(memory_dir, tension=0.5)
        _write_dynamics_state(memory_dir, "normal")
        _write_gap_analysis(docs_dir, gaps=[("G30", "二重管理", "中", "新規")])
        result = generate_guidance(memory_dir, docs_dir)
        assert "慎重" in result or "仕上げ" in result or "既存" in result

    def test_rebound_phase_with_gaps(self, memory_dir, docs_dir):
        _write_emotion_state(memory_dir, fulfillment=0.3, tension=0.1)
        _write_dynamics_state(memory_dir, "rebound")
        _write_gap_analysis(docs_dir, gaps=[("G31", "1:1:1対応", "高", "新規")])
        result = generate_guidance(memory_dir, docs_dir)
        assert "回復中" in result or "方向転換" in result

    def test_missing_emotion_returns_empty(self, memory_dir, docs_dir):
        _write_dynamics_state(memory_dir, "normal")
        _write_gap_analysis(docs_dir)
        result = generate_guidance(memory_dir, docs_dir)
        assert result == ""

    def test_missing_dynamics_returns_empty(self, memory_dir, docs_dir):
        _write_emotion_state(memory_dir, tension=0.5)
        _write_gap_analysis(docs_dir)
        result = generate_guidance(memory_dir, docs_dir)
        assert result == ""

    def test_no_gap_files_still_returns_state_guidance(self, memory_dir, tmp_path):
        _write_emotion_state(memory_dir, fulfillment=0.5, tension=0.1)
        _write_dynamics_state(memory_dir, "normal")
        empty_docs = str(tmp_path / "empty_docs")
        os.makedirs(empty_docs, exist_ok=True)
        result = generate_guidance(memory_dir, empty_docs)
        assert "状態" in result or result == ""

    def test_corrupt_emotion_returns_empty(self, memory_dir, docs_dir):
        with open(os.path.join(memory_dir, "emotion_state.json"), "w") as f:
            f.write("{broken")
        _write_dynamics_state(memory_dir, "normal")
        _write_gap_analysis(docs_dir)
        result = generate_guidance(memory_dir, docs_dir)
        assert result == ""

    def test_empty_memory_dir(self, tmp_path, docs_dir):
        result = generate_guidance(str(tmp_path), docs_dir)
        assert result == ""


class TestSaturationPrevention:
    def test_repeated_same_state_triggers_saturation(self, memory_dir, docs_dir):
        _write_emotion_state(memory_dir, tension=0.5)
        _write_dynamics_state(memory_dir, "normal")
        _write_gap_analysis(docs_dir, gaps=[("G30", "二重管理", "中", "新規")])
        result = ""
        for _ in range(SATURATION_CONSECUTIVE_LIMIT + 1):
            result = generate_guidance(memory_dir, docs_dir)
        assert result == ""

    def test_changing_state_prevents_saturation(self, memory_dir, docs_dir):
        _write_dynamics_state(memory_dir, "normal")
        _write_gap_analysis(docs_dir, gaps=[("G30", "二重管理", "中", "新規")])
        for i in range(SATURATION_CONSECUTIVE_LIMIT + 1):
            _write_emotion_state(memory_dir, tension=0.3 + (i * 0.05))
            result = generate_guidance(memory_dir, docs_dir)
        assert result != ""

    def test_saturation_resets_after_change(self, memory_dir, docs_dir):
        _write_dynamics_state(memory_dir, "normal")
        _write_emotion_state(memory_dir, tension=0.5)
        _write_gap_analysis(docs_dir, gaps=[("G30", "二重管理", "中", "新規")])
        for _ in range(SATURATION_CONSECUTIVE_LIMIT + 1):
            generate_guidance(memory_dir, docs_dir)
        _write_emotion_state(memory_dir, tension=0.1, fulfillment=0.5)
        result = generate_guidance(memory_dir, docs_dir)
        assert result != ""


class TestEdgeCases:
    def test_boundary_tension_exactly_03(self, memory_dir, docs_dir):
        _write_emotion_state(memory_dir, tension=0.3)
        _write_dynamics_state(memory_dir, "normal")
        _write_gap_analysis(docs_dir)
        result = generate_guidance(memory_dir, docs_dir)
        assert "慎重" not in result

    def test_boundary_tension_just_above_03(self, memory_dir, docs_dir):
        _write_emotion_state(memory_dir, tension=0.31)
        _write_dynamics_state(memory_dir, "normal")
        _write_gap_analysis(docs_dir)
        result = generate_guidance(memory_dir, docs_dir)
        assert "慎重" in result or "仕上げ" in result or "既存" in result
