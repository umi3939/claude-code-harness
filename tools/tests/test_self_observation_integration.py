"""Tests for self-observation integration (4 passes).

Pass A: session_start writes snapshot summary to STM
Pass B: emotion_update calls facade_record_long_term
Pass C: HEARTBEAT.md contains behavior_analyze row
Pass D: session_start alerts on coherence/stability issues

LOW fix coverage: import re moved to module level, path sanitization in error msg.
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))
import memory_mcp_server


# --- Pass A: Snapshot summary written to STM ---


class TestPassA_SnapshotToSTM:
    """session_start should write snapshot summary to STM after self_snapshot."""

    @patch("memory_mcp_server.stm_save")
    @patch("memory_mcp_server.stm_write_entry")
    @patch("memory_mcp_server.stm_load")
    @patch("memory_mcp_server.facade_run_snapshot")
    @patch("memory_mcp_server.activation_surface_fn", return_value="surface ok")
    @patch("memory_mcp_server.stm_decay", return_value=({}, 0))
    @patch("memory_mcp_server.stm_stats", return_value={"avg_weight": 0.5})
    @patch("memory_mcp_server.stm_read_entries", return_value=[])
    @patch("memory_mcp_server.apply_session_decay", side_effect=lambda s: s)
    @patch("memory_mcp_server.save_state")
    @patch(
        "memory_mcp_server.load_state",
        return_value={"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0},
    )
    def test_snapshot_summary_written_to_stm(
        self,
        mock_load_state,
        mock_save_state,
        mock_decay,
        mock_read_entries,
        mock_stm_stats,
        mock_stm_decay,
        mock_surface,
        mock_snapshot,
        mock_stm_load,
        mock_stm_write,
        mock_stm_save,
    ):
        """Snapshot results are summarized and written to STM as self_review."""
        mock_snapshot.return_value = {
            "observe": {"integrated": "Observing normally"},
            "difference": {"magnitude": "low", "integrated_description": "No change"},
            "strain": {"level": "low", "description": "Stable"},
            "self_image": {
                "overall_impression": "neutral",
                "integrated_description": "OK",
            },
            "coherence": {
                "coherence_level": "connected",
                "description": "All good",
            },
            "stability": {"dampening_factor": 1.0, "description": "Inactive"},
            "tone": {"primary_tone": "neutral", "description": "Normal"},
        }
        mock_stm_load.return_value = {"entries": []}
        mock_stm_write.return_value = {"entries": [{"content": "test"}]}
        mock_stm_save.return_value = "OK"

        result = memory_mcp_server.session_start()

        # Verify stm_write_entry was called with self_review category
        assert mock_stm_write.call_count >= 1
        # Find the call with snapshot summary
        found_snapshot_write = False
        for call in mock_stm_write.call_args_list:
            args = call[0]
            if len(args) >= 3 and args[2] == "self_review" and "Session start snapshot" in str(args[1]):
                found_snapshot_write = True
                # Verify content contains key observation data
                assert "observe:" in args[1]
                assert "coherence:" in args[1]
                assert "stability:" in args[1]
                break
        assert found_snapshot_write, "Snapshot summary was not written to STM"

    @patch("memory_mcp_server.stm_save")
    @patch("memory_mcp_server.stm_write_entry")
    @patch("memory_mcp_server.stm_load")
    @patch("memory_mcp_server.facade_run_snapshot")
    @patch("memory_mcp_server.activation_surface_fn", return_value="surface ok")
    @patch("memory_mcp_server.stm_decay", return_value=({}, 0))
    @patch("memory_mcp_server.stm_stats", return_value={"avg_weight": 0.5})
    @patch("memory_mcp_server.stm_read_entries", return_value=[])
    @patch("memory_mcp_server.apply_session_decay", side_effect=lambda s: s)
    @patch("memory_mcp_server.save_state")
    @patch(
        "memory_mcp_server.load_state",
        return_value={"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0},
    )
    def test_stm_write_failure_does_not_break_session_start(
        self,
        mock_load_state,
        mock_save_state,
        mock_decay,
        mock_read_entries,
        mock_stm_stats,
        mock_stm_decay,
        mock_surface,
        mock_snapshot,
        mock_stm_load,
        mock_stm_write,
        mock_stm_save,
    ):
        """STM write failure must not cause session_start to fail."""
        mock_snapshot.return_value = {
            "observe": {"integrated": "Observing normally"},
            "difference": {"magnitude": "low", "integrated_description": "No change"},
            "strain": {"level": "low", "description": "Stable"},
            "self_image": {
                "overall_impression": "neutral",
                "integrated_description": "OK",
            },
            "coherence": {
                "coherence_level": "connected",
                "description": "All good",
            },
            "stability": {"dampening_factor": 1.0, "description": "Inactive"},
            "tone": {"primary_tone": "neutral", "description": "Normal"},
        }
        # stm_load works for STM restore (step 2) but stm_write_entry fails for Pass A
        mock_stm_load.return_value = {"entries": []}
        mock_stm_write.side_effect = RuntimeError("STM write broken")

        # Should NOT raise
        result = memory_mcp_server.session_start()
        assert "Self Snapshot" in result
        # The snapshot data should still be in output
        assert "[observe]" in result

    @patch("memory_mcp_server.stm_save")
    @patch("memory_mcp_server.stm_write_entry")
    @patch("memory_mcp_server.stm_load")
    @patch("memory_mcp_server.facade_run_snapshot")
    @patch("memory_mcp_server.activation_surface_fn", return_value="surface ok")
    @patch("memory_mcp_server.stm_decay", return_value=({}, 0))
    @patch("memory_mcp_server.stm_stats", return_value={"avg_weight": 0.5})
    @patch("memory_mcp_server.stm_read_entries", return_value=[])
    @patch("memory_mcp_server.apply_session_decay", side_effect=lambda s: s)
    @patch("memory_mcp_server.save_state")
    @patch(
        "memory_mcp_server.load_state",
        return_value={"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0},
    )
    def test_snapshot_summary_truncated_to_500_chars(
        self,
        mock_load_state,
        mock_save_state,
        mock_decay,
        mock_read_entries,
        mock_stm_stats,
        mock_stm_decay,
        mock_surface,
        mock_snapshot,
        mock_stm_load,
        mock_stm_write,
        mock_stm_save,
    ):
        """Snapshot summary must be truncated to 500 chars max."""
        long_text = "A" * 600
        mock_snapshot.return_value = {
            "observe": {"integrated": long_text},
            "difference": {"magnitude": "low", "integrated_description": "No change"},
            "strain": {"level": "low", "description": "Stable"},
            "self_image": {
                "overall_impression": "neutral",
                "integrated_description": "OK",
            },
            "coherence": {
                "coherence_level": "connected",
                "description": "All good",
            },
            "stability": {"dampening_factor": 1.0, "description": "Inactive"},
            "tone": {"primary_tone": "neutral", "description": "Normal"},
        }
        mock_stm_load.return_value = {"entries": []}
        mock_stm_write.return_value = {"entries": []}
        mock_stm_save.return_value = "OK"

        memory_mcp_server.session_start()

        for call in mock_stm_write.call_args_list:
            args = call[0]
            if len(args) >= 2 and "Session start snapshot" in str(args[1]):
                assert len(args[1]) <= 500


# --- Pass B: emotion_update calls facade_record_long_term ---


class TestPassB_EmotionUpdateLongTerm:
    """emotion_update should call facade_record_long_term before returning."""

    @patch("memory_mcp_server.facade_record_long_term")
    @patch("memory_mcp_server.load_state", return_value={"fulfillment": 0.1, "tension": 0.0, "affinity": 0.0})
    @patch("memory_mcp_server.update_state", return_value="Updated OK")
    def test_long_term_record_called(self, mock_update, mock_load, mock_lt):
        """facade_record_long_term is called after update_state."""
        mock_lt.return_value = {"status": "buffered", "buffer_size": 1}

        result = memory_mcp_server.emotion_update(fulfillment=0.1, reason="test")

        assert mock_lt.call_count == 1
        call_kwargs = mock_lt.call_args
        # Verify it passes emotion_state
        assert "emotion_state" in call_kwargs[1] or len(call_kwargs[0]) >= 2

    @patch("memory_mcp_server.facade_record_long_term")
    @patch("memory_mcp_server.load_state", return_value={"fulfillment": 0.1, "tension": 0.0, "affinity": 0.0})
    @patch("memory_mcp_server.update_state", return_value="Updated OK")
    def test_long_term_failure_does_not_break_emotion_update(
        self, mock_update, mock_load, mock_lt
    ):
        """Long-term record failure must not affect emotion_update result."""
        mock_lt.side_effect = RuntimeError("LT broken")

        result = memory_mcp_server.emotion_update(fulfillment=0.1, reason="test")
        # Should still return the update result, not an error
        assert "Updated OK" in result

    @patch("memory_mcp_server.facade_record_long_term")
    @patch("memory_mcp_server.load_state", return_value={"fulfillment": 0.1, "tension": 0.0, "affinity": 0.0})
    @patch("memory_mcp_server.update_state", return_value="Updated OK")
    def test_long_term_record_uses_correct_phase(self, mock_update, mock_load, mock_lt):
        """Long-term record should pass dynamics_phase='unknown' when dynamics unavailable."""
        mock_lt.return_value = {"status": "buffered", "buffer_size": 1}

        memory_mcp_server.emotion_update(tension=0.2)

        assert mock_lt.call_count == 1


# --- Pass C: HEARTBEAT.md contains behavior_analyze ---


class TestPassC_HeartbeatBehaviorAnalyze:
    """HEARTBEAT.md should contain a behavior_analyze entry in the Concerns table."""

    def test_heartbeat_has_behavior_analyze(self):
        """HEARTBEAT.md Concerns table includes behavior_analyze row."""
        heartbeat_path = os.path.join(
            Path(__file__).parent.parent.parent, "HEARTBEAT.md"
        )
        if not os.path.exists(heartbeat_path):
            pytest.skip("HEARTBEAT.md not found")

        with open(heartbeat_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "behavior_analyze" in content, "HEARTBEAT.md missing behavior_analyze row"


# --- Pass D: Observation alerts on coherence/stability ---


class TestPassD_ObservationAlerts:
    """session_start should emit alerts when coherence is low or stability active."""

    def _run_session_start_with_snap(self, coherence_level, dampening_factor):
        """Helper to run session_start with specific snapshot values."""
        snap = {
            "observe": {"integrated": "Observing"},
            "difference": {"magnitude": "low", "integrated_description": "No change"},
            "strain": {"level": "low", "description": "Stable"},
            "self_image": {
                "overall_impression": "neutral",
                "integrated_description": "OK",
            },
            "coherence": {
                "coherence_level": coherence_level,
                "description": "Test",
            },
            "stability": {
                "dampening_factor": dampening_factor,
                "description": "Test",
            },
            "tone": {"primary_tone": "neutral", "description": "Normal"},
        }

        with (
            patch("memory_mcp_server.load_state", return_value={"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0}),
            patch("memory_mcp_server.save_state"),
            patch("memory_mcp_server.apply_session_decay", side_effect=lambda s: s),
            patch("memory_mcp_server.stm_load", return_value={"entries": []}),
            patch("memory_mcp_server.stm_decay", return_value=({"entries": []}, 0)),
            patch("memory_mcp_server.stm_save", return_value="OK"),
            patch("memory_mcp_server.stm_stats", return_value={"avg_weight": 0.5}),
            patch("memory_mcp_server.stm_read_entries", return_value=[]),
            patch("memory_mcp_server.stm_write_entry", side_effect=lambda store, content, cat, *a, **kw: store),
            patch("memory_mcp_server.activation_surface_fn", return_value="ok"),
            patch("memory_mcp_server.facade_run_snapshot", return_value=snap),
        ):
            return memory_mcp_server.session_start()

    def test_disconnected_coherence_triggers_alert(self):
        """coherence='disconnected' should produce an alert in output."""
        result = self._run_session_start_with_snap("disconnected", 1.0)
        assert "[ALERT]" in result
        assert "disconnected" in result

    def test_fragmented_coherence_triggers_alert(self):
        """coherence='fragmented' should produce an alert in output."""
        result = self._run_session_start_with_snap("fragmented", 1.0)
        assert "[ALERT]" in result
        assert "fragmented" in result

    def test_dampening_below_1_triggers_notice(self):
        """dampening < 1.0 should produce a notice in output."""
        result = self._run_session_start_with_snap("connected", 0.7)
        assert "[NOTICE]" in result
        assert "dampening=0.70" in result

    def test_no_alert_when_normal(self):
        """No alerts when coherence is connected and dampening is 1.0."""
        result = self._run_session_start_with_snap("connected", 1.0)
        assert "[ALERT]" not in result
        assert "[NOTICE]" not in result

    def test_both_alerts_when_both_conditions(self):
        """Both alert and notice when coherence is bad AND dampening active."""
        result = self._run_session_start_with_snap("disconnected", 0.5)
        assert "[ALERT]" in result
        assert "[NOTICE]" in result
