"""Tests for coherence_alert.py - Pipeline 3: Coherence Alert (v2 blocking)."""

import os
import sys

import pytest

HOOKS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "hooks")
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)

TOOLS_DIR = os.path.join(os.path.dirname(__file__), "..")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from coherence_alert import (
    NOTIFICATION_COOLDOWN,
    generate_coherence_alert,
    check_and_notify,
    _load_cooldown_state,
    _save_cooldown_state,
)


class TestGenerateCoherenceAlert:
    def test_unsettled_produces_blocking_alert(self):
        result = generate_coherence_alert("unsettled")
        assert result["text"] != ""
        assert result["should_block"] is True
        assert "thinker" in result["text"]
        assert "不安定" in result["text"]

    def test_disconnected_produces_blocking_alert(self):
        result = generate_coherence_alert("disconnected")
        assert result["text"] != ""
        assert result["should_block"] is True
        assert "thinker" in result["text"]
        assert "断絶" in result["text"]

    def test_stable_produces_no_alert(self):
        result = generate_coherence_alert("stable")
        assert result["text"] == ""
        assert result["should_block"] is False

    def test_slightly_shifting_produces_no_alert(self):
        result = generate_coherence_alert("slightly_shifting")
        assert result["text"] == ""
        assert result["should_block"] is False

    def test_empty_string_produces_no_alert(self):
        result = generate_coherence_alert("")
        assert result["text"] == ""
        assert result["should_block"] is False

    def test_unknown_level_produces_no_alert(self):
        result = generate_coherence_alert("unknown_level")
        assert result["text"] == ""
        assert result["should_block"] is False

    def test_none_level_produces_no_alert(self):
        result = generate_coherence_alert(None)
        assert result["text"] == ""
        assert result["should_block"] is False

    def test_unsettled_mentions_state_check(self):
        result = generate_coherence_alert("unsettled")
        assert "確認" in result["text"] or "thinker" in result["text"]

    def test_disconnected_mentions_halt(self):
        result = generate_coherence_alert("disconnected")
        assert "保留" in result["text"] or "判断" in result["text"]


class TestCheckAndNotify:
    def test_first_unsettled_returns_alert_and_blocks(self, tmp_path):
        data_dir = str(tmp_path)
        result = check_and_notify("unsettled", data_dir)
        assert result["text"] != ""
        assert result["should_block"] is True

    def test_first_disconnected_returns_alert_and_blocks(self, tmp_path):
        data_dir = str(tmp_path)
        result = check_and_notify("disconnected", data_dir)
        assert result["text"] != ""
        assert result["should_block"] is True

    def test_stable_returns_no_alert_no_block(self, tmp_path):
        data_dir = str(tmp_path)
        result = check_and_notify("stable", data_dir)
        assert result["text"] == ""
        assert result["should_block"] is False

    def test_cooldown_suppresses_same_level(self, tmp_path):
        data_dir = str(tmp_path)
        result1 = check_and_notify("unsettled", data_dir)
        assert result1["should_block"] is True
        result2 = check_and_notify("unsettled", data_dir)
        assert result2["text"] == ""
        assert result2["should_block"] is True

    def test_different_level_resets_cooldown(self, tmp_path):
        data_dir = str(tmp_path)
        check_and_notify("unsettled", data_dir)
        result = check_and_notify("disconnected", data_dir)
        assert result["text"] != ""
        assert result["should_block"] is True

    def test_cooldown_expires_after_n_stable_calls(self, tmp_path):
        data_dir = str(tmp_path)
        check_and_notify("unsettled", data_dir)
        for _ in range(NOTIFICATION_COOLDOWN):
            check_and_notify("stable", data_dir)
        result = check_and_notify("unsettled", data_dir)
        assert result["text"] != ""
        assert result["should_block"] is True

    def test_none_level_no_block(self, tmp_path):
        data_dir = str(tmp_path)
        result = check_and_notify(None, data_dir)
        assert result["text"] == ""
        assert result["should_block"] is False


class TestCooldownMechanism:
    def test_first_alert_not_cooled(self, tmp_path):
        state_file = str(tmp_path / "coherence_cooldown.json")
        cooldown = _load_cooldown_state(state_file)
        assert cooldown.get("call_count_since_last", NOTIFICATION_COOLDOWN) >= NOTIFICATION_COOLDOWN

    def test_cooldown_blocks_repeated_alerts(self, tmp_path):
        state_file = str(tmp_path / "coherence_cooldown.json")
        _save_cooldown_state(state_file, {
            "last_alert_level": "unsettled",
            "call_count_since_last": 0,
        })
        cooldown = _load_cooldown_state(state_file)
        assert cooldown["call_count_since_last"] < NOTIFICATION_COOLDOWN

    def test_missing_state_file_returns_default(self, tmp_path):
        state_file = str(tmp_path / "nonexistent.json")
        cooldown = _load_cooldown_state(state_file)
        assert cooldown["call_count_since_last"] >= NOTIFICATION_COOLDOWN

    def test_corrupt_state_file_returns_default(self, tmp_path):
        state_file = str(tmp_path / "corrupt.json")
        with open(state_file, "w") as f:
            f.write("{broken")
        cooldown = _load_cooldown_state(state_file)
        assert cooldown["call_count_since_last"] >= NOTIFICATION_COOLDOWN
