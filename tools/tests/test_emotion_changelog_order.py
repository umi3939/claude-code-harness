#!/usr/bin/env python3
"""Tests for emotion_state.py change log order — log before save.

TDD: Tests written before implementation.
Verifies that _record_change_log_entry is called BEFORE save_state,
so that even if save_state fails, the change log is already recorded.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS_DIR = str(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)


class TestChangeLogBeforeSave(unittest.TestCase):
    """Verify _record_change_log_entry is called before save_state."""

    def test_log_recorded_even_when_save_fails(self):
        """If save_state raises/returns error, change log should still be recorded."""
        import emotion_state

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create initial state
            initial_state = {
                "fulfillment": 0.0,
                "tension": 0.0,
                "affinity": 0.0,
                "last_updated": "2026-01-01T00:00:00+00:00",
            }
            state_path = os.path.join(tmpdir, "emotion_state.json")
            with open(state_path, 'w') as f:
                json.dump(initial_state, f)

            # Make save_state fail by returning an error string
            with patch.object(emotion_state, 'save_state', return_value="ERROR: disk full"):
                result = emotion_state.update_state(
                    tmpdir,
                    fulfillment=0.5,
                    mode="set",
                    reason="test update",
                )

            # save_state should have returned an error
            self.assertIn("ERROR", result)

            # But change log should have been recorded
            log_path = os.path.join(tmpdir, "emotion_change_log.json")
            self.assertTrue(os.path.exists(log_path),
                            "Change log file should exist even when save_state fails")

            with open(log_path) as f:
                log_data = json.load(f)

            entries = log_data.get("entries", [])
            self.assertEqual(len(entries), 1, "Exactly one change log entry expected")
            self.assertEqual(entries[0]["reason"], "test update")
            self.assertAlmostEqual(entries[0]["after"]["fulfillment"], 0.5)

    def test_call_order_log_then_save(self):
        """_record_change_log_entry must be called before save_state."""
        import emotion_state

        call_order = []

        original_record = emotion_state._record_change_log_entry
        original_save = emotion_state.save_state

        def tracking_record(*args, **kwargs):
            call_order.append("log")
            return original_record(*args, **kwargs)

        def tracking_save(*args, **kwargs):
            call_order.append("save")
            return original_save(*args, **kwargs)

        with tempfile.TemporaryDirectory() as tmpdir:
            initial_state = {
                "fulfillment": 0.0,
                "tension": 0.0,
                "affinity": 0.0,
                "last_updated": "2026-01-01T00:00:00+00:00",
            }
            state_path = os.path.join(tmpdir, "emotion_state.json")
            with open(state_path, 'w') as f:
                json.dump(initial_state, f)

            with patch.object(emotion_state, '_record_change_log_entry', side_effect=tracking_record), \
                 patch.object(emotion_state, 'save_state', side_effect=tracking_save):
                emotion_state.update_state(
                    tmpdir,
                    tension=0.3,
                    mode="delta",
                    reason="order test",
                )

            self.assertEqual(call_order, ["log", "save"],
                             f"Expected log before save, got: {call_order}")

    def test_normal_update_still_works(self):
        """Normal update (no failures) should still work correctly with new order."""
        import emotion_state

        with tempfile.TemporaryDirectory() as tmpdir:
            initial_state = {
                "fulfillment": 0.0,
                "tension": 0.0,
                "affinity": 0.0,
                "last_updated": "2026-01-01T00:00:00+00:00",
            }
            state_path = os.path.join(tmpdir, "emotion_state.json")
            with open(state_path, 'w') as f:
                json.dump(initial_state, f)

            result = emotion_state.update_state(
                tmpdir,
                fulfillment=0.7,
                tension=-0.2,
                mode="set",
                reason="normal update",
            )

            self.assertNotIn("ERROR", result)
            self.assertIn("fulfillment=+0.700", result)

            # Verify both state and log exist
            with open(state_path) as f:
                state = json.load(f)
            self.assertAlmostEqual(state["fulfillment"], 0.7)

            log_path = os.path.join(tmpdir, "emotion_change_log.json")
            with open(log_path) as f:
                log_data = json.load(f)
            self.assertEqual(len(log_data.get("entries", [])), 1)


if __name__ == '__main__':
    unittest.main()
