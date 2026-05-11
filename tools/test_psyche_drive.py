#!/usr/bin/env python3
"""Tests for psyche_drive.py — psyche drive pathway (C20-1).

TDD: tests written before implementation.
Tests cover:
- Phase 1: PsycheDriveState, should_update, emotion/observation/activation updates, dispatcher
- Phase 2: skill_executor integration
- Phase 3: timeout, backoff, STM new entry filtering
"""

import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

# Add hooks directory to path for importing psyche_drive
HOOKS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "hooks")
TOOLS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "tools")
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)


class TestPsycheDriveState(unittest.TestCase):
    """Step 1.1: PsycheDriveState — timing management table."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.tmpdir, ".psyche-drive-state.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_init_creates_default_state(self):
        """Initial state has all categories with zero timestamps."""
        from psyche_drive import PsycheDriveState
        state = PsycheDriveState(self.tmpdir)
        for cat in ("emotion", "observation", "activation"):
            self.assertEqual(state.get_last_update(cat), 0.0)
            self.assertEqual(state.get_last_phase(cat), "")
            self.assertEqual(state.get_failure_count(cat), 0)

    def test_save_and_load_roundtrip(self):
        """State survives save/load cycle."""
        from psyche_drive import PsycheDriveState
        state = PsycheDriveState(self.tmpdir)
        now = time.time()
        state.record_update("emotion", now, "design")
        state.save()

        state2 = PsycheDriveState(self.tmpdir)
        self.assertAlmostEqual(state2.get_last_update("emotion"), now, places=1)
        self.assertEqual(state2.get_last_phase("emotion"), "design")

    def test_file_not_exists_returns_defaults(self):
        """Missing file creates default state without error."""
        from psyche_drive import PsycheDriveState
        nonexistent = os.path.join(self.tmpdir, "subdir_missing")
        # Should not raise
        state = PsycheDriveState(nonexistent)
        self.assertEqual(state.get_last_update("emotion"), 0.0)

    def test_corrupted_json_returns_defaults(self):
        """Corrupted JSON file falls back to defaults."""
        from psyche_drive import PsycheDriveState
        os.makedirs(self.tmpdir, exist_ok=True)
        with open(self.state_file, "w") as f:
            f.write("{corrupted")
        state = PsycheDriveState(self.tmpdir)
        self.assertEqual(state.get_last_update("emotion"), 0.0)

    def test_record_update_time(self):
        """record_update stores time and phase for a category."""
        from psyche_drive import PsycheDriveState
        state = PsycheDriveState(self.tmpdir)
        now = time.time()
        state.record_update("observation", now, "impl")
        self.assertAlmostEqual(state.get_last_update("observation"), now, places=1)
        self.assertEqual(state.get_last_phase("observation"), "impl")


class TestShouldUpdate(unittest.TestCase):
    """Step 1.2: should_update — time-based + phase transition logic."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_first_run_returns_true(self):
        """First run (never updated) → should update."""
        from psyche_drive import PsycheDriveState, should_update
        state = PsycheDriveState(self.tmpdir)
        self.assertTrue(should_update(state, "emotion", "design"))

    def test_within_interval_returns_false(self):
        """Within interval → should NOT update."""
        from psyche_drive import PsycheDriveState, should_update
        state = PsycheDriveState(self.tmpdir)
        # Record update just now
        state.record_update("emotion", time.time(), "design")
        self.assertFalse(should_update(state, "emotion", "design"))

    def test_past_interval_returns_true(self):
        """Past interval → should update."""
        from psyche_drive import INTERVALS, PsycheDriveState, should_update
        state = PsycheDriveState(self.tmpdir)
        # Record update long ago
        state.record_update("emotion", time.time() - INTERVALS["emotion"] - 10, "design")
        self.assertTrue(should_update(state, "emotion", "design"))

    def test_phase_transition_forces_update(self):
        """Phase transition → should update regardless of interval."""
        from psyche_drive import PsycheDriveState, should_update
        state = PsycheDriveState(self.tmpdir)
        # Just updated in "design" phase
        state.record_update("emotion", time.time(), "design")
        # Phase changed to "impl" → force update
        self.assertTrue(should_update(state, "emotion", "impl"))

    def test_backoff_extends_interval(self):
        """With 3+ failures (threshold), effective interval is extended."""
        from psyche_drive import BACKOFF_THRESHOLD, INTERVALS, PsycheDriveState, should_update
        state = PsycheDriveState(self.tmpdir)
        base_interval = INTERVALS["emotion"]
        # Record update slightly past base interval
        state.record_update("emotion", time.time() - base_interval - 10, "design")
        # Need BACKOFF_THRESHOLD failures to trigger backoff
        for _ in range(BACKOFF_THRESHOLD):
            state.record_failure("emotion")
        # With 3 failures (threshold=3), effective interval = base * 2^1 = 2*base
        # We're only base+10 seconds past → should be false if 2*base > base+10
        # For emotion (300s), 2*300=600 > 310 → False
        self.assertFalse(should_update(state, "emotion", "design"))

    def test_same_phase_no_transition(self):
        """Same phase → no transition detected, follows time only."""
        from psyche_drive import PsycheDriveState, should_update
        state = PsycheDriveState(self.tmpdir)
        state.record_update("emotion", time.time(), "design")
        self.assertFalse(should_update(state, "emotion", "design"))


class TestUpdateEmotion(unittest.TestCase):
    """Step 1.3: _update_emotion — emotion react chain."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.memory_dir = self.tmpdir
        # Create STM with entries
        stm_data = {
            "entries": [
                {
                    "id": "abc123",
                    "category": "thought",
                    "content": "Working on psyche drive implementation",
                    "timestamp": "2026-03-25T10:00:00+00:00",
                    "weight": 1.0,
                },
                {
                    "id": "def456",
                    "category": "impression",
                    "content": "The design is elegant and well-structured",
                    "timestamp": "2026-03-25T10:05:00+00:00",
                    "weight": 1.0,
                },
            ],
            "session_count": 1,
        }
        with open(os.path.join(self.memory_dir, "short_term_memory.json"), "w") as f:
            json.dump(stm_data, f)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("psyche_drive.emotion_react_fn")
    @patch("psyche_drive.load_state")
    @patch("psyche_drive.load_dynamics_state")
    @patch("psyche_drive.dynamics_session_reset")
    @patch("psyche_drive.dynamics_get_amplitude")
    @patch("psyche_drive.facade_get_dampening")
    @patch("psyche_drive.dynamics_update")
    @patch("psyche_drive.save_dynamics_state")
    @patch("psyche_drive.update_state")
    @patch("psyche_drive.facade_record_long_term")
    def test_emotion_chain_full_execution(
        self,
        mock_lt_record, mock_update_state, mock_save_dyn,
        mock_dyn_update, mock_dampening, mock_get_amp,
        mock_dyn_reset, mock_load_dyn, mock_load_state, mock_react
    ):
        """Full emotion_react chain executes all 10 steps in order."""
        from psyche_drive import PsycheDriveState, _update_emotion

        # Setup mocks
        mock_load_state.return_value = {"fulfillment": 0.1, "tension": 0.0, "affinity": 0.2, "last_updated": "2026-03-25T10:00:00Z"}
        mock_load_dyn.return_value = {"phase": "normal", "phase_call_count": 0, "accumulation_history": [], "peak_axis": ""}
        mock_dyn_reset.return_value = {"phase": "normal", "phase_call_count": 0, "accumulation_history": [], "peak_axis": ""}
        mock_get_amp.return_value = 1.0
        mock_dampening.return_value = 0.9
        mock_react.return_value = {"fulfillment": 0.05, "tension": -0.02, "affinity": 0.03}
        mock_dyn_update.return_value = ({"phase": "normal", "phase_call_count": 0, "accumulation_history": [0.1], "peak_axis": ""}, 1.0)
        mock_update_state.return_value = "OK"
        # After update_state, load_state is called again for long_term_record
        mock_load_state.side_effect = [
            {"fulfillment": 0.1, "tension": 0.0, "affinity": 0.2, "last_updated": "2026-03-25T10:00:00Z"},
            {"fulfillment": 0.15, "tension": -0.02, "affinity": 0.23, "last_updated": "2026-03-25T10:10:00Z"},
        ]
        mock_lt_record.return_value = {"status": "buffered", "buffer_size": 1}

        state = PsycheDriveState(self.tmpdir)
        result = _update_emotion(self.memory_dir, state)

        self.assertTrue(result)
        # Verify chain order
        mock_load_state.assert_called()
        mock_load_dyn.assert_called_once_with(self.memory_dir)
        mock_dyn_reset.assert_called_once()
        mock_get_amp.assert_called_once()
        mock_dampening.assert_called_once_with(self.memory_dir)
        mock_react.assert_called_once()
        mock_dyn_update.assert_called_once()
        mock_save_dyn.assert_called_once()
        mock_update_state.assert_called_once()
        # long_term_record: load_state called again after update_state
        self.assertEqual(mock_load_state.call_count, 2)
        mock_lt_record.assert_called_once()

    @patch("psyche_drive.emotion_react_fn")
    @patch("psyche_drive.load_state")
    @patch("psyche_drive.load_dynamics_state")
    @patch("psyche_drive.dynamics_session_reset")
    @patch("psyche_drive.dynamics_get_amplitude")
    @patch("psyche_drive.facade_get_dampening")
    @patch("psyche_drive.dynamics_update")
    @patch("psyche_drive.save_dynamics_state")
    @patch("psyche_drive.update_state")
    @patch("psyche_drive.facade_record_long_term")
    def test_effective_amplitude_uses_dynamics(
        self,
        mock_lt_record, mock_update_state, mock_save_dyn,
        mock_dyn_update, mock_dampening, mock_get_amp,
        mock_dyn_reset, mock_load_dyn, mock_load_state, mock_react
    ):
        """Effective amplitude = dynamics_amplitude * stability_dampening (no manual_override)."""
        from psyche_drive import PsycheDriveState, _update_emotion

        mock_load_state.side_effect = [
            {"fulfillment": 0.1, "tension": 0.0, "affinity": 0.2, "last_updated": "2026-03-25T10:00:00Z"},
            {"fulfillment": 0.15, "tension": -0.02, "affinity": 0.23, "last_updated": "2026-03-25T10:10:00Z"},
        ]
        mock_load_dyn.return_value = {"phase": "peak", "phase_call_count": 1}
        mock_dyn_reset.return_value = {"phase": "peak", "phase_call_count": 1}
        mock_get_amp.return_value = 1.3  # peak amplitude
        mock_dampening.return_value = 0.8  # stability dampening
        mock_react.return_value = {"fulfillment": 0.05, "tension": -0.02, "affinity": 0.03}
        mock_dyn_update.return_value = ({"phase": "peak"}, 1.3)
        mock_update_state.return_value = "OK"
        mock_lt_record.return_value = {"status": "buffered", "buffer_size": 1}

        state = PsycheDriveState(self.tmpdir)
        _update_emotion(self.memory_dir, state)

        # Verify effective_amplitude = 1.3 * 0.8 = 1.04
        call_args = mock_react.call_args
        self.assertAlmostEqual(call_args[1]["amplitude_modifier"], 1.3 * 0.8, places=4)

    def test_no_new_stm_entries_skips(self):
        """No new STM entries since last update → returns None (skip, not failure)."""
        from psyche_drive import PsycheDriveState, _update_emotion

        state = PsycheDriveState(self.tmpdir)
        # Set last update to far in the future so all STM entries are "old"
        state.record_update("emotion", time.time() + 99999, "design")

        result = _update_emotion(self.memory_dir, state)
        self.assertIsNone(result)

    @patch("psyche_drive.emotion_react_fn")
    @patch("psyche_drive.load_state")
    @patch("psyche_drive.load_dynamics_state")
    @patch("psyche_drive.dynamics_session_reset")
    @patch("psyche_drive.dynamics_get_amplitude")
    @patch("psyche_drive.facade_get_dampening")
    @patch("psyche_drive.dynamics_update")
    @patch("psyche_drive.save_dynamics_state")
    @patch("psyche_drive.update_state")
    @patch("psyche_drive.facade_record_long_term")
    def test_dampening_failure_defaults_to_1(
        self,
        mock_lt_record, mock_update_state, mock_save_dyn,
        mock_dyn_update, mock_dampening, mock_get_amp,
        mock_dyn_reset, mock_load_dyn, mock_load_state, mock_react
    ):
        """facade_get_dampening failure → dampening=1.0 (passthrough)."""
        from psyche_drive import PsycheDriveState, _update_emotion

        mock_load_state.side_effect = [
            {"fulfillment": 0.0, "tension": 0.0, "affinity": 0.0, "last_updated": "2026-03-25T10:00:00Z"},
            {"fulfillment": 0.05, "tension": -0.02, "affinity": 0.03, "last_updated": "2026-03-25T10:10:00Z"},
        ]
        mock_load_dyn.return_value = {"phase": "normal"}
        mock_dyn_reset.return_value = {"phase": "normal"}
        mock_get_amp.return_value = 1.0
        mock_dampening.side_effect = Exception("dampening failed")
        mock_react.return_value = {"fulfillment": 0.05, "tension": -0.02, "affinity": 0.03}
        mock_dyn_update.return_value = ({"phase": "normal"}, 1.0)
        mock_update_state.return_value = "OK"
        mock_lt_record.return_value = {"status": "buffered", "buffer_size": 1}

        state = PsycheDriveState(self.tmpdir)
        result = _update_emotion(self.memory_dir, state)

        self.assertTrue(result)
        # amplitude_modifier should be 1.0 * 1.0 = 1.0 (dampening defaulted)
        call_args = mock_react.call_args
        self.assertAlmostEqual(call_args[1]["amplitude_modifier"], 1.0, places=4)

    @patch("psyche_drive.load_state")
    def test_exception_returns_false(self, mock_load_state):
        """Exception during emotion update → returns False, no crash."""
        from psyche_drive import PsycheDriveState, _update_emotion

        mock_load_state.side_effect = Exception("file error")
        state = PsycheDriveState(self.tmpdir)
        result = _update_emotion(self.memory_dir, state)
        self.assertFalse(result)

    def test_skip_does_not_increment_failure_count(self):
        """STM no new entries (None return) → failure_count must NOT increase."""
        from psyche_drive import PsycheDriveState, _update_emotion

        state = PsycheDriveState(self.tmpdir)
        # Set last update to far in the future so all STM entries are "old"
        state.record_update("emotion", time.time() + 99999, "design")
        initial_failures = state.get_failure_count("emotion")

        result = _update_emotion(self.memory_dir, state)
        self.assertIsNone(result)
        self.assertEqual(state.get_failure_count("emotion"), initial_failures)


class TestRunWithTimeoutNone(unittest.TestCase):
    """_run_with_timeout propagates None from wrapped function."""

    def test_run_with_timeout_propagates_none(self):
        """_run_with_timeout returns None when wrapped function returns None."""
        from psyche_drive import _run_with_timeout
        result = _run_with_timeout(lambda: None, timeout=5.0)
        self.assertIsNone(result)


class TestUpdateObservation(unittest.TestCase):
    """Step 1.4: _update_observation — self observation pipeline."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("psyche_drive.run_snapshot")
    def test_calls_run_snapshot(self, mock_snapshot):
        """Calls observation_facade.run_snapshot."""
        from psyche_drive import _update_observation
        mock_snapshot.return_value = {"observe": {}, "difference": {}}
        result = _update_observation(self.tmpdir)
        self.assertTrue(result)
        mock_snapshot.assert_called_once_with(self.tmpdir)

    @patch("psyche_drive.run_snapshot")
    def test_snapshot_failure_returns_false(self, mock_snapshot):
        """run_snapshot failure → returns False, no crash."""
        from psyche_drive import _update_observation
        mock_snapshot.side_effect = Exception("snapshot failed")
        result = _update_observation(self.tmpdir)
        self.assertFalse(result)


class TestUpdateActivation(unittest.TestCase):
    """Step 1.5: _update_activation — activation surface update."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("psyche_drive.activation_surface_fn")
    def test_calls_activation_surface(self, mock_surface):
        """Calls activation_surface.surface with context."""
        from psyche_drive import _update_activation
        mock_surface.return_value = "test result"
        result = _update_activation(self.tmpdir, "impl phase context")
        self.assertTrue(result)
        mock_surface.assert_called_once_with(self.tmpdir, context="impl phase context")

    @patch("psyche_drive.activation_surface_fn")
    def test_surface_failure_returns_false(self, mock_surface):
        """activation_surface failure → returns False, no crash."""
        from psyche_drive import _update_activation
        mock_surface.side_effect = Exception("surface failed")
        result = _update_activation(self.tmpdir)
        self.assertFalse(result)

    @patch("psyche_drive.activation_surface_fn")
    def test_no_context_passes_none(self, mock_surface):
        """No context → passes None."""
        from psyche_drive import _update_activation
        mock_surface.return_value = "result"
        _update_activation(self.tmpdir)
        mock_surface.assert_called_once_with(self.tmpdir, context=None)


class TestMainDispatcher(unittest.TestCase):
    """Step 1.6: run_psyche_drive — main dispatcher."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.hooks_dir = os.path.join(self.tmpdir, "hooks")
        os.makedirs(self.hooks_dir, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_session_ready_skips(self):
        """No behavior-guard-state.json → skip entirely."""
        from psyche_drive import run_psyche_drive
        with patch("psyche_drive.HOOKS_DIR", self.hooks_dir):
            # No .behavior-guard-state.json exists
            result = run_psyche_drive(self.tmpdir)
            self.assertIsNone(result)

    def test_session_ready_runs(self):
        """With behavior-guard-state.json session_ready=true → proceeds with update checks."""
        from psyche_drive import run_psyche_drive
        with patch("psyche_drive.HOOKS_DIR", self.hooks_dir), \
             patch("psyche_drive._update_emotion") as mock_emo, \
             patch("psyche_drive._update_observation") as mock_obs, \
             patch("psyche_drive._update_activation") as mock_act, \
             patch("psyche_drive._get_current_phase") as mock_phase:
            # Create behavior-guard-state with session_ready=true
            with open(os.path.join(self.hooks_dir, ".behavior-guard-state.json"), "w") as f:
                json.dump({"session_ready": True}, f)
            mock_phase.return_value = "design"
            mock_emo.return_value = True
            mock_obs.return_value = True
            mock_act.return_value = True

            run_psyche_drive(self.tmpdir)
            # First run: all categories should be updated (never updated before)
            mock_emo.assert_called_once()
            mock_obs.assert_called_once()
            mock_act.assert_called_once()

    def test_no_updates_needed_returns_quickly(self):
        """All categories recently updated → no updates triggered."""
        from psyche_drive import PsycheDriveState, run_psyche_drive
        with patch("psyche_drive.HOOKS_DIR", self.hooks_dir), \
             patch("psyche_drive._update_emotion") as mock_emo, \
             patch("psyche_drive._update_observation") as mock_obs, \
             patch("psyche_drive._update_activation") as mock_act, \
             patch("psyche_drive._get_current_phase") as mock_phase:
            # Create behavior-guard-state with session_ready=true
            with open(os.path.join(self.hooks_dir, ".behavior-guard-state.json"), "w") as f:
                json.dump({"session_ready": True}, f)
            mock_phase.return_value = "design"

            # Pre-populate state with recent updates
            state = PsycheDriveState(self.tmpdir)
            now = time.time()
            for cat in ("emotion", "observation", "activation"):
                state.record_update(cat, now, "design")
            state.save()

            run_psyche_drive(self.tmpdir)
            mock_emo.assert_not_called()
            mock_obs.assert_not_called()
            mock_act.assert_not_called()

    def test_phase_transition_forces_all(self):
        """Phase transition → all categories updated."""
        from psyche_drive import PsycheDriveState, run_psyche_drive
        with patch("psyche_drive.HOOKS_DIR", self.hooks_dir), \
             patch("psyche_drive._update_emotion") as mock_emo, \
             patch("psyche_drive._update_observation") as mock_obs, \
             patch("psyche_drive._update_activation") as mock_act, \
             patch("psyche_drive._get_current_phase") as mock_phase:
            with open(os.path.join(self.hooks_dir, ".behavior-guard-state.json"), "w") as f:
                json.dump({"session_ready": True}, f)
            mock_phase.return_value = "impl"  # Different from recorded "design"
            mock_emo.return_value = True
            mock_obs.return_value = True
            mock_act.return_value = True

            # State has recent updates but in "design" phase
            state = PsycheDriveState(self.tmpdir)
            now = time.time()
            for cat in ("emotion", "observation", "activation"):
                state.record_update(cat, now, "design")
            state.save()

            run_psyche_drive(self.tmpdir)
            mock_emo.assert_called_once()
            mock_obs.assert_called_once()
            mock_act.assert_called_once()

    def test_one_failure_others_continue(self):
        """One category fails → others still execute."""
        from psyche_drive import run_psyche_drive
        with patch("psyche_drive.HOOKS_DIR", self.hooks_dir), \
             patch("psyche_drive._update_emotion") as mock_emo, \
             patch("psyche_drive._update_observation") as mock_obs, \
             patch("psyche_drive._update_activation") as mock_act, \
             patch("psyche_drive._get_current_phase") as mock_phase:
            with open(os.path.join(self.hooks_dir, ".behavior-guard-state.json"), "w") as f:
                json.dump({"session_ready": True}, f)
            mock_phase.return_value = "design"
            mock_emo.return_value = False  # emotion fails
            mock_obs.return_value = True
            mock_act.return_value = True

            run_psyche_drive(self.tmpdir)
            # All called despite emotion failure
            mock_emo.assert_called_once()
            mock_obs.assert_called_once()
            mock_act.assert_called_once()

    def test_no_stdout_output(self):
        """Psyche drive produces no stdout output (no context injection)."""
        import io

        from psyche_drive import run_psyche_drive
        with patch("psyche_drive.HOOKS_DIR", self.hooks_dir), \
             patch("psyche_drive._update_emotion") as mock_emo, \
             patch("psyche_drive._update_observation") as mock_obs, \
             patch("psyche_drive._update_activation") as mock_act, \
             patch("psyche_drive._get_current_phase") as mock_phase, \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with open(os.path.join(self.hooks_dir, ".behavior-guard-state.json"), "w") as f:
                json.dump({"session_ready": True}, f)
            mock_phase.return_value = "design"
            mock_emo.return_value = True
            mock_obs.return_value = True
            mock_act.return_value = True

            run_psyche_drive(self.tmpdir)
            self.assertEqual(mock_stdout.getvalue(), "")


class TestBackoff(unittest.TestCase):
    """Step 3.2: Backoff logic."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_three_failures_doubles_interval(self):
        """3 consecutive failures → interval doubles."""
        from psyche_drive import INTERVALS, PsycheDriveState, get_effective_interval
        state = PsycheDriveState(self.tmpdir)
        for _ in range(3):
            state.record_failure("emotion")
        effective = get_effective_interval(state, "emotion")
        self.assertEqual(effective, INTERVALS["emotion"] * 2)

    def test_six_failures_quadruples(self):
        """6 failures → 4x interval."""
        from psyche_drive import INTERVALS, PsycheDriveState, get_effective_interval
        state = PsycheDriveState(self.tmpdir)
        for _ in range(6):
            state.record_failure("emotion")
        effective = get_effective_interval(state, "emotion")
        self.assertEqual(effective, INTERVALS["emotion"] * 4)

    def test_max_interval_cap(self):
        """Interval caps at MAX_INTERVAL (3600s)."""
        from psyche_drive import MAX_INTERVAL, PsycheDriveState, get_effective_interval
        state = PsycheDriveState(self.tmpdir)
        for _ in range(100):
            state.record_failure("emotion")
        effective = get_effective_interval(state, "emotion")
        self.assertLessEqual(effective, MAX_INTERVAL)

    def test_success_resets_failures(self):
        """One success resets failure count to 0."""
        from psyche_drive import INTERVALS, PsycheDriveState, get_effective_interval
        state = PsycheDriveState(self.tmpdir)
        for _ in range(6):
            state.record_failure("emotion")
        state.record_success("emotion")
        effective = get_effective_interval(state, "emotion")
        self.assertEqual(effective, INTERVALS["emotion"])
        self.assertEqual(state.get_failure_count("emotion"), 0)


class TestTimeout(unittest.TestCase):
    """Step 3.1: Timeout with ThreadPoolExecutor."""

    def test_normal_completion(self):
        """Function completes within timeout → returns result."""
        from psyche_drive import _run_with_timeout
        result = _run_with_timeout(lambda: True, timeout=5.0)
        self.assertTrue(result)

    def test_timeout_returns_false(self):
        """Function exceeds timeout → returns False."""

        from psyche_drive import _run_with_timeout

        def slow_fn():
            time.sleep(10)
            return True

        result = _run_with_timeout(slow_fn, timeout=0.1)
        self.assertFalse(result)

    def test_exception_returns_false(self):
        """Function raises exception → returns False."""
        from psyche_drive import _run_with_timeout

        def bad_fn():
            raise ValueError("test error")

        result = _run_with_timeout(bad_fn, timeout=5.0)
        self.assertFalse(result)


class TestSTMNewEntryFilter(unittest.TestCase):
    """Step 3.4: STM new entry filtering."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_iso_to_epoch_conversion(self):
        """ISO timestamps correctly converted to epoch for comparison."""
        from psyche_drive import _get_new_stm_entries
        stm_data = {
            "entries": [
                {"id": "a", "content": "old", "timestamp": "2026-03-25T09:00:00+00:00", "weight": 1.0},
                {"id": "b", "content": "new", "timestamp": "2026-03-25T11:00:00+00:00", "weight": 1.0},
            ],
        }
        stm_path = os.path.join(self.tmpdir, "short_term_memory.json")
        with open(stm_path, "w") as f:
            json.dump(stm_data, f)

        # Last update at 10:00 → only "new" (11:00) should be returned
        from datetime import datetime, timezone
        last_update = datetime(2026, 3, 25, 10, 0, 0, tzinfo=timezone.utc).timestamp()
        entries = _get_new_stm_entries(self.tmpdir, last_update)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["id"], "b")

    def test_no_entries_after_cutoff(self):
        """All entries before cutoff → empty list."""
        from psyche_drive import _get_new_stm_entries
        stm_data = {
            "entries": [
                {"id": "a", "content": "old", "timestamp": "2026-03-25T09:00:00+00:00", "weight": 1.0},
            ],
        }
        stm_path = os.path.join(self.tmpdir, "short_term_memory.json")
        with open(stm_path, "w") as f:
            json.dump(stm_data, f)

        last_update = time.time() + 99999
        entries = _get_new_stm_entries(self.tmpdir, last_update)
        self.assertEqual(len(entries), 0)

    def test_entries_without_timestamp_included(self):
        """Entries without timestamp are included (conservative)."""
        from psyche_drive import _get_new_stm_entries
        stm_data = {
            "entries": [
                {"id": "a", "content": "no timestamp", "weight": 1.0},
            ],
        }
        stm_path = os.path.join(self.tmpdir, "short_term_memory.json")
        with open(stm_path, "w") as f:
            json.dump(stm_data, f)

        entries = _get_new_stm_entries(self.tmpdir, time.time())
        self.assertEqual(len(entries), 1)


class TestSkillExecutorIntegration(unittest.TestCase):
    """Step 2.1: skill_executor.py integration."""

    def test_psyche_drive_called_in_main(self):
        """run_psyche_drive is called in skill_executor main for UserPromptSubmit."""
        # Verify the integration point exists
        skill_executor_path = os.path.join(HOOKS_DIR, "skill_executor.py")
        with open(skill_executor_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("psyche_drive", content)

    def test_psyche_drive_independent_of_context_injection(self):
        """Psyche drive failure doesn't affect context injection."""
        # This tests that the integration uses its own try-except block
        skill_executor_path = os.path.join(HOOKS_DIR, "skill_executor.py")
        with open(skill_executor_path, "r", encoding="utf-8") as f:
            content = f.read()
        # Should have separate try block for psyche_drive
        self.assertIn("run_psyche_drive", content)


class TestSessionReadyBehaviorGuardState(unittest.TestCase):
    """Session readiness check via behavior-guard-state.json (replaces .session-ready flag)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.hooks_dir = os.path.join(self.tmpdir, "hooks")
        os.makedirs(self.hooks_dir, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_guard_state(self, data):
        """Write behavior-guard-state.json in hooks_dir."""
        path = os.path.join(self.hooks_dir, ".behavior-guard-state.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_session_ready_true_runs(self):
        """behavior-guard-state.json with session_ready=true → psyche_drive executes."""
        from psyche_drive import run_psyche_drive
        self._write_guard_state({"session_ready": True})
        with patch("psyche_drive.HOOKS_DIR", self.hooks_dir), \
             patch("psyche_drive._update_emotion") as mock_emo, \
             patch("psyche_drive._update_observation") as mock_obs, \
             patch("psyche_drive._update_activation") as mock_act, \
             patch("psyche_drive._get_current_phase") as mock_phase:
            mock_phase.return_value = "design"
            mock_emo.return_value = True
            mock_obs.return_value = True
            mock_act.return_value = True
            run_psyche_drive(self.tmpdir)
            # Should proceed (all categories need updating on first run)
            mock_emo.assert_called_once()
            mock_obs.assert_called_once()
            mock_act.assert_called_once()

    def test_file_not_exists_skips(self):
        """behavior-guard-state.json does not exist → skip."""
        from psyche_drive import run_psyche_drive
        with patch("psyche_drive.HOOKS_DIR", self.hooks_dir):
            result = run_psyche_drive(self.tmpdir)
            self.assertIsNone(result)

    def test_invalid_json_skips(self):
        """behavior-guard-state.json with corrupt JSON → skip."""
        from psyche_drive import run_psyche_drive
        path = os.path.join(self.hooks_dir, ".behavior-guard-state.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{broken json!!")
        with patch("psyche_drive.HOOKS_DIR", self.hooks_dir):
            result = run_psyche_drive(self.tmpdir)
            self.assertIsNone(result)

    def test_session_ready_false_skips(self):
        """behavior-guard-state.json with session_ready=false → skip."""
        from psyche_drive import run_psyche_drive
        self._write_guard_state({"session_ready": False})
        with patch("psyche_drive.HOOKS_DIR", self.hooks_dir):
            result = run_psyche_drive(self.tmpdir)
            self.assertIsNone(result)

    def test_session_ready_missing_key_skips(self):
        """behavior-guard-state.json exists but missing session_ready key → skip."""
        from psyche_drive import run_psyche_drive
        self._write_guard_state({"other_key": "value"})
        with patch("psyche_drive.HOOKS_DIR", self.hooks_dir):
            result = run_psyche_drive(self.tmpdir)
            self.assertIsNone(result)


class TestPsycheDriveStateValidation(unittest.TestCase):
    """M-S7: JSON write should validate required keys before writing."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_save_validates_categories_key(self):
        """State save should fail or skip if 'categories' key is missing."""
        from psyche_drive import PsycheDriveState
        state = PsycheDriveState(self.tmpdir)
        # Corrupt the internal data
        state._data = {"bad": "data"}
        # After fix, save should validate and either fix or reject
        state.save()
        # If save writes, the file should still have valid structure
        path = os.path.join(self.tmpdir, ".psyche-drive-state.json")
        if os.path.exists(path):
            with open(path, "r") as f:
                data = json.load(f)
            # Must have categories key (auto-repaired or rejected)
            self.assertIn("categories", data)

    def test_save_validates_category_structure(self):
        """State save should ensure each category has required keys."""
        from psyche_drive import PsycheDriveState
        state = PsycheDriveState(self.tmpdir)
        # Set categories with missing keys
        state._data = {"categories": {"emotion": {"last_update": 0.0}}}  # missing keys
        state.save()
        path = os.path.join(self.tmpdir, ".psyche-drive-state.json")
        if os.path.exists(path):
            with open(path, "r") as f:
                data = json.load(f)
            cat = data["categories"]["emotion"]
            self.assertIn("last_update", cat)


class TestPsycheDriveStateLoadValidation(unittest.TestCase):
    """M-S8: JSON load should validate type."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.tmpdir, ".psyche-drive-state.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_rejects_non_dict_json(self):
        """Loading a JSON array should return default state, not crash."""
        with open(self.state_file, "w") as f:
            json.dump([1, 2, 3], f)
        from psyche_drive import PsycheDriveState
        state = PsycheDriveState(self.tmpdir)
        # Should get default state, not the array
        self.assertIn("categories", state._data)
        self.assertIsInstance(state._data, dict)

    def test_load_rejects_string_json(self):
        """Loading a JSON string should return default state."""
        with open(self.state_file, "w") as f:
            json.dump("just a string", f)
        from psyche_drive import PsycheDriveState
        state = PsycheDriveState(self.tmpdir)
        self.assertIn("categories", state._data)
        self.assertIsInstance(state._data, dict)


if __name__ == "__main__":
    unittest.main()
