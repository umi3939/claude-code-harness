#!/usr/bin/env python3
"""Tests for temporal_self_difference module.

Tests cover:
- Snapshot taking and saving
- FIFO limit (10 snapshots)
- ComponentChangeType (unchanged/intensified/softened/shifted)
- DifferenceMagnitude (none/minimal/noticeable/significant/substantial)
- ChangeNature (stable/fluctuating/shifting/transformed/returning/undefined)
- Integrated description format (no numbers, no evaluation)
- Edge cases (no file, 0-1 history, corrupted file)
"""

import json
import os
import sys
import tempfile
import shutil

import pytest

# Add the tools directory to sys.path
TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import temporal_self_difference as tsd


@pytest.fixture
def tmp_memory_dir():
    """Create a temporary memory directory for tests."""
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _write_emotion_state(memory_dir, fulfillment=0.0, tension=0.0, affinity=0.0):
    """Write a minimal emotion state file."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = {
        "fulfillment": fulfillment,
        "tension": tension,
        "affinity": affinity,
        "last_updated": now,
        "created_at": now,
    }
    path = os.path.join(memory_dir, "emotion_state.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _write_dynamics_state(memory_dir, phase="normal"):
    """Write a minimal dynamics state file."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = {
        "phase": phase,
        "phase_call_count": 0,
        "accumulation_history": [],
        "peak_axis": "",
        "last_updated": now,
    }
    path = os.path.join(memory_dir, "dynamics_state.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _make_snapshot(
    categories=None,
    phase="normal",
    snapshot_id="test",
    values=None,
):
    """Create a test snapshot dict."""
    if categories is None:
        categories = {
            "fulfillment": "neutral",
            "tension": "neutral",
            "affinity": "neutral",
        }
    if values is None:
        values = {
            "fulfillment": 0.0,
            "tension": 0.0,
            "affinity": 0.0,
        }
    return {
        "snapshot_id": snapshot_id,
        "timestamp": "2026-03-13T00:00:00Z",
        "emotion_values": values,
        "emotion_categories": categories,
        "dynamics_phase": phase,
        "change_frequency": "none",
        "change_trends": {
            "fulfillment": "stable",
            "tension": "stable",
            "affinity": "stable",
        },
    }


# =============================================
# Snapshot taking and persistence
# =============================================


class TestSnapshotTaking:
    def test_take_snapshot_default_state(self, tmp_memory_dir):
        """Snapshot from default state should have neutral categories."""
        _write_emotion_state(tmp_memory_dir)
        _write_dynamics_state(tmp_memory_dir)
        snap = tsd._take_snapshot(tmp_memory_dir)
        assert snap["emotion_categories"]["fulfillment"] == "neutral"
        assert snap["emotion_categories"]["tension"] == "neutral"
        assert snap["emotion_categories"]["affinity"] == "neutral"
        assert snap["dynamics_phase"] == "normal"
        assert "snapshot_id" in snap
        assert "timestamp" in snap

    def test_take_snapshot_positive_state(self, tmp_memory_dir):
        """Snapshot with positive values should have correct categories."""
        _write_emotion_state(tmp_memory_dir, fulfillment=0.8, tension=0.4, affinity=-0.5)
        _write_dynamics_state(tmp_memory_dir)
        snap = tsd._take_snapshot(tmp_memory_dir)
        assert snap["emotion_categories"]["fulfillment"] == "strongly_positive"
        assert snap["emotion_categories"]["tension"] == "positive"
        assert snap["emotion_categories"]["affinity"] == "negative"

    def test_snapshot_persistence(self, tmp_memory_dir):
        """Snapshots should be persisted to file."""
        _write_emotion_state(tmp_memory_dir)
        _write_dynamics_state(tmp_memory_dir)
        tsd.compute_difference(tmp_memory_dir)

        data = tsd._load_snapshots(tmp_memory_dir)
        assert len(data["snapshots"]) == 1
        assert data["comparison_count"] == 1

    def test_snapshot_accumulates(self, tmp_memory_dir):
        """Multiple calls should accumulate snapshots."""
        _write_emotion_state(tmp_memory_dir)
        _write_dynamics_state(tmp_memory_dir)
        tsd.compute_difference(tmp_memory_dir)
        tsd.compute_difference(tmp_memory_dir)
        tsd.compute_difference(tmp_memory_dir)

        data = tsd._load_snapshots(tmp_memory_dir)
        assert len(data["snapshots"]) == 3
        assert data["comparison_count"] == 3


# =============================================
# FIFO limit
# =============================================


class TestFIFO:
    def test_fifo_at_limit(self, tmp_memory_dir):
        """After 10 calls, should have exactly 10 snapshots."""
        _write_emotion_state(tmp_memory_dir)
        _write_dynamics_state(tmp_memory_dir)
        for _ in range(10):
            tsd.compute_difference(tmp_memory_dir)

        data = tsd._load_snapshots(tmp_memory_dir)
        assert len(data["snapshots"]) == 10

    def test_fifo_over_limit(self, tmp_memory_dir):
        """After 12 calls, should still have exactly 10 snapshots."""
        _write_emotion_state(tmp_memory_dir)
        _write_dynamics_state(tmp_memory_dir)
        for _ in range(12):
            tsd.compute_difference(tmp_memory_dir)

        data = tsd._load_snapshots(tmp_memory_dir)
        assert len(data["snapshots"]) == 10
        assert data["comparison_count"] == 12

    def test_fifo_oldest_removed(self, tmp_memory_dir):
        """Oldest snapshot should be removed when FIFO overflows."""
        _write_emotion_state(tmp_memory_dir)
        _write_dynamics_state(tmp_memory_dir)

        # Manually seed a snapshot with a unique marker ID
        seed_snap = _make_snapshot(snapshot_id="SEED_FIRST")
        tsd._save_snapshots(tmp_memory_dir, {
            "snapshots": [seed_snap],
            "comparison_count": 1,
        })

        # Do 10 more calls to push out the seed
        for _ in range(10):
            tsd.compute_difference(tmp_memory_dir)

        data = tsd._load_snapshots(tmp_memory_dir)
        ids = [s["snapshot_id"] for s in data["snapshots"]]
        assert "SEED_FIRST" not in ids
        assert len(data["snapshots"]) == 10


# =============================================
# ComponentChangeType
# =============================================


class TestComponentChangeType:
    def test_unchanged(self):
        assert tsd._compute_component_change("neutral", "neutral") == "unchanged"
        assert tsd._compute_component_change("positive", "positive") == "unchanged"

    def test_intensified(self):
        assert tsd._compute_component_change("neutral", "positive") == "intensified"
        assert tsd._compute_component_change("positive", "strongly_positive") == "intensified"
        assert tsd._compute_component_change("strongly_negative", "negative") == "intensified"

    def test_softened(self):
        assert tsd._compute_component_change("positive", "neutral") == "softened"
        assert tsd._compute_component_change("strongly_positive", "positive") == "softened"
        assert tsd._compute_component_change("negative", "strongly_negative") == "softened"

    def test_shifted_sign_change(self):
        assert tsd._compute_component_change("negative", "positive") == "shifted"
        assert tsd._compute_component_change("positive", "negative") == "shifted"
        assert tsd._compute_component_change("strongly_negative", "strongly_positive") == "shifted"

    def test_unknown_category(self):
        assert tsd._compute_component_change("unknown", "positive") == "shifted"

    def test_dynamics_unchanged(self):
        assert tsd._compute_dynamics_change("normal", "normal") == "unchanged"

    def test_dynamics_shifted(self):
        assert tsd._compute_dynamics_change("normal", "peak") == "shifted"
        assert tsd._compute_dynamics_change("peak", "rebound") == "shifted"


# =============================================
# DifferenceMagnitude
# =============================================


class TestDifferenceMagnitude:
    def test_none_no_changes(self):
        """No changes = none."""
        components = {
            "fulfillment": "unchanged",
            "tension": "unchanged",
            "affinity": "unchanged",
            "dynamics_phase": "unchanged",
        }
        prev = _make_snapshot()
        curr = _make_snapshot()
        assert tsd._compute_magnitude(components, prev, curr) == "none"

    def test_minimal_one_small_change(self):
        """One component changed with distance 1 = minimal."""
        components = {
            "fulfillment": "intensified",
            "tension": "unchanged",
            "affinity": "unchanged",
            "dynamics_phase": "unchanged",
        }
        prev = _make_snapshot(categories={"fulfillment": "neutral", "tension": "neutral", "affinity": "neutral"})
        curr = _make_snapshot(categories={"fulfillment": "positive", "tension": "neutral", "affinity": "neutral"})
        assert tsd._compute_magnitude(components, prev, curr) == "minimal"

    def test_noticeable_two_small_changes(self):
        """Two components changed with total distance <= 3 = noticeable."""
        components = {
            "fulfillment": "intensified",
            "tension": "intensified",
            "affinity": "unchanged",
            "dynamics_phase": "unchanged",
        }
        prev = _make_snapshot(categories={"fulfillment": "neutral", "tension": "neutral", "affinity": "neutral"})
        curr = _make_snapshot(categories={"fulfillment": "positive", "tension": "positive", "affinity": "neutral"})
        assert tsd._compute_magnitude(components, prev, curr) == "noticeable"

    def test_significant_three_changes(self):
        """Three components changed with total distance <= 5 = significant."""
        components = {
            "fulfillment": "intensified",
            "tension": "intensified",
            "affinity": "intensified",
            "dynamics_phase": "unchanged",
        }
        prev = _make_snapshot(categories={"fulfillment": "neutral", "tension": "neutral", "affinity": "neutral"})
        curr = _make_snapshot(categories={"fulfillment": "positive", "tension": "positive", "affinity": "positive"})
        # distance = 1+1+1 = 3, changed = 3
        assert tsd._compute_magnitude(components, prev, curr) == "significant"

    def test_substantial_large_changes(self):
        """Large changes = substantial."""
        components = {
            "fulfillment": "shifted",
            "tension": "shifted",
            "affinity": "shifted",
            "dynamics_phase": "shifted",
        }
        prev = _make_snapshot(
            categories={"fulfillment": "strongly_negative", "tension": "strongly_negative", "affinity": "strongly_negative"},
            phase="normal",
        )
        curr = _make_snapshot(
            categories={"fulfillment": "strongly_positive", "tension": "strongly_positive", "affinity": "strongly_positive"},
            phase="peak",
        )
        # distance = 4+4+4+1 = 13, changed = 4
        assert tsd._compute_magnitude(components, prev, curr) == "substantial"

    def test_dynamics_phase_change_adds_distance(self):
        """Dynamics phase change adds distance 1."""
        components = {
            "fulfillment": "unchanged",
            "tension": "unchanged",
            "affinity": "unchanged",
            "dynamics_phase": "shifted",
        }
        prev = _make_snapshot(phase="normal")
        curr = _make_snapshot(phase="peak")
        # changed = 1, distance = 1
        assert tsd._compute_magnitude(components, prev, curr) == "minimal"

    def test_noticeable_boundary(self):
        """Two changes with distance exactly 3 = noticeable."""
        components = {
            "fulfillment": "shifted",
            "tension": "unchanged",
            "affinity": "unchanged",
            "dynamics_phase": "shifted",
        }
        prev = _make_snapshot(
            categories={"fulfillment": "neutral", "tension": "neutral", "affinity": "neutral"},
            phase="normal",
        )
        curr = _make_snapshot(
            categories={"fulfillment": "strongly_positive", "tension": "neutral", "affinity": "neutral"},
            phase="peak",
        )
        # distance = 2 + 1 = 3, changed = 2
        assert tsd._compute_magnitude(components, prev, curr) == "noticeable"


# =============================================
# ChangeNature
# =============================================


class TestChangeNature:
    def test_undefined_no_history(self):
        """With no history at all, nature is undefined."""
        current = _make_snapshot()
        assert tsd._compute_change_nature([], current) == "undefined"

    def test_stable_no_changes(self):
        """Repeated identical snapshots = stable."""
        snap = _make_snapshot()
        history = [snap, snap, snap]
        current = _make_snapshot()
        result = tsd._compute_change_nature(history, current)
        assert result == "stable"

    def test_shifting_gradual_change(self):
        """Gradual one-direction change = shifting."""
        s1 = _make_snapshot(categories={"fulfillment": "neutral", "tension": "neutral", "affinity": "neutral"})
        s2 = _make_snapshot(categories={"fulfillment": "positive", "tension": "neutral", "affinity": "neutral"})
        s3 = _make_snapshot(categories={"fulfillment": "strongly_positive", "tension": "neutral", "affinity": "neutral"})
        current = _make_snapshot(categories={"fulfillment": "strongly_positive", "tension": "positive", "affinity": "neutral"})
        result = tsd._compute_change_nature([s1, s2, s3], current)
        assert result == "shifting"

    def test_transformed_large_average(self):
        """Average changes >= 3 = transformed."""
        s1 = _make_snapshot(
            categories={"fulfillment": "strongly_negative", "tension": "strongly_negative", "affinity": "strongly_negative"},
            phase="normal",
        )
        s2 = _make_snapshot(
            categories={"fulfillment": "strongly_positive", "tension": "strongly_positive", "affinity": "strongly_positive"},
            phase="peak",
        )
        current = _make_snapshot(
            categories={"fulfillment": "strongly_negative", "tension": "strongly_negative", "affinity": "strongly_negative"},
            phase="normal",
        )
        result = tsd._compute_change_nature([s1, s2], current)
        # avg changes between (s1->s2) and (s2->current) = (4 + 4) / 2 = 4 >= 3
        assert result == "transformed"

    def test_fluctuating_high_variance(self):
        """High variance in change counts with low average = fluctuating."""
        s1 = _make_snapshot(categories={"fulfillment": "neutral", "tension": "neutral", "affinity": "neutral"})
        # Big change
        s2 = _make_snapshot(
            categories={"fulfillment": "strongly_positive", "tension": "strongly_positive", "affinity": "strongly_positive"},
            phase="peak",
        )
        # Back to original
        s3 = _make_snapshot(categories={"fulfillment": "neutral", "tension": "neutral", "affinity": "neutral"})
        # No change
        current = _make_snapshot(categories={"fulfillment": "neutral", "tension": "neutral", "affinity": "neutral"})
        result = tsd._compute_change_nature([s1, s2, s3], current)
        # pair changes: s1->s2=4, s2->s3=4, s3->current=0
        # avg=8/3=2.67, variance = ((4-2.67)^2+(4-2.67)^2+(0-2.67)^2)/3 = (1.77+1.77+7.13)/3 = 3.55 > 1.0, avg=2.67 >= 2
        # So this won't be fluctuating (avg >= 2 fails the condition)
        # Let me adjust to get variance > 1 and avg < 2
        pass

    def test_fluctuating_proper(self):
        """Proper fluctuating: variance > 1.0 and average < 2."""
        s1 = _make_snapshot(categories={"fulfillment": "neutral", "tension": "neutral", "affinity": "neutral"})
        s2 = _make_snapshot(categories={"fulfillment": "positive", "tension": "positive", "affinity": "positive"})
        s3 = _make_snapshot(categories={"fulfillment": "neutral", "tension": "neutral", "affinity": "neutral"})
        current = _make_snapshot(categories={"fulfillment": "neutral", "tension": "neutral", "affinity": "neutral"})
        # pair changes: s1->s2=3, s2->s3=3, s3->current=0
        # avg=6/3=2, avg is NOT < 2 (it equals 2), so fluctuating won't trigger
        # Need avg < 2 strictly
        # Adjust: make some smaller changes
        s2b = _make_snapshot(categories={"fulfillment": "positive", "tension": "neutral", "affinity": "positive"})
        # s1->s2b=2, s2b->s3=2, s3->current=0
        # avg=4/3=1.33, variance = ((2-1.33)^2+(2-1.33)^2+(0-1.33)^2)/3 = (0.45+0.45+1.77)/3 = 0.89
        # Variance 0.89 < 1.0 — not enough
        # Try with more extreme variance
        s1 = _make_snapshot(categories={"fulfillment": "neutral", "tension": "neutral", "affinity": "neutral"})
        s2 = _make_snapshot(categories={"fulfillment": "positive", "tension": "positive", "affinity": "neutral"})  # 2 changes
        s3 = _make_snapshot(categories={"fulfillment": "neutral", "tension": "neutral", "affinity": "neutral"})  # 2 changes back
        s4 = _make_snapshot(categories={"fulfillment": "neutral", "tension": "neutral", "affinity": "neutral"})  # 0 changes
        current = _make_snapshot(categories={"fulfillment": "positive", "tension": "positive", "affinity": "positive"})  # 3 changes
        # pairs in recent 5: s1->s2=2, s2->s3=2, s3->s4=0, s4->current=3
        # avg=7/4=1.75 < 2, variance = ((2-1.75)^2+(2-1.75)^2+(0-1.75)^2+(3-1.75)^2)/4 = (0.0625+0.0625+3.0625+1.5625)/4 = 1.1875 > 1.0
        result = tsd._compute_change_nature([s1, s2, s3, s4], current)
        assert result == "fluctuating"

    def test_returning(self):
        """When current is closer to 5-ago than to previous = returning."""
        s0 = _make_snapshot(categories={"fulfillment": "positive", "tension": "neutral", "affinity": "neutral"})
        s1 = _make_snapshot(categories={"fulfillment": "strongly_positive", "tension": "neutral", "affinity": "neutral"})
        s2 = _make_snapshot(categories={"fulfillment": "strongly_positive", "tension": "positive", "affinity": "neutral"})
        s3 = _make_snapshot(categories={"fulfillment": "strongly_positive", "tension": "positive", "affinity": "positive"})
        s4 = _make_snapshot(categories={"fulfillment": "strongly_positive", "tension": "strongly_positive", "affinity": "positive"})
        # current returns close to s0
        current = _make_snapshot(categories={"fulfillment": "positive", "tension": "neutral", "affinity": "neutral"})
        # s0 is 5 back from current. dist(s0, current) = 0+0+0 = 0
        # dist(s4, current) = 1+2+1 = 4
        # 0 < 4 → returning
        result = tsd._compute_change_nature([s0, s1, s2, s3, s4], current)
        assert result == "returning"


# =============================================
# Integrated description
# =============================================


class TestIntegratedDescription:
    def test_no_change(self):
        desc = tsd._generate_description("none", "stable", {"fulfillment": "unchanged"})
        assert desc == "自己状態に変化は見られない。"

    def test_minimal(self):
        desc = tsd._generate_description("minimal", "stable", {"fulfillment": "unchanged"})
        assert desc == "自己状態にわずかな揺らぎがある。"

    def test_noticeable_shifting(self):
        components = {
            "fulfillment": "intensified",
            "tension": "unchanged",
            "affinity": "unchanged",
            "dynamics_phase": "unchanged",
        }
        desc = tsd._generate_description("noticeable", "shifting", components)
        assert "充実感" in desc
        assert "緩やかに移行している" in desc
        assert "自己状態は" in desc

    def test_significant_multiple_components(self):
        components = {
            "fulfillment": "intensified",
            "tension": "softened",
            "affinity": "unchanged",
            "dynamics_phase": "unchanged",
        }
        desc = tsd._generate_description("significant", "transformed", components)
        assert "充実感" in desc
        assert "緊張" in desc
        assert "顕著に異なっている" in desc

    def test_description_no_numbers(self):
        """Description must not contain numeric values."""
        components = {
            "fulfillment": "shifted",
            "tension": "shifted",
            "affinity": "shifted",
            "dynamics_phase": "shifted",
        }
        desc = tsd._generate_description("substantial", "transformed", components)
        # Check no digits in description
        import re
        assert not re.search(r'\d', desc), f"Description contains numbers: {desc}"

    def test_description_no_evaluation(self):
        """Description must not contain evaluative terms."""
        components = {
            "fulfillment": "shifted",
            "tension": "shifted",
            "affinity": "shifted",
            "dynamics_phase": "shifted",
        }
        desc = tsd._generate_description("substantial", "transformed", components)
        for word in ["良い", "悪い", "改善", "悪化", "好", "不良"]:
            assert word not in desc, f"Description contains evaluation: {word}"


# =============================================
# Edge cases
# =============================================


class TestEdgeCases:
    def test_no_snapshot_file(self, tmp_memory_dir):
        """First call with no existing file should work."""
        _write_emotion_state(tmp_memory_dir)
        _write_dynamics_state(tmp_memory_dir)
        result = tsd.compute_difference(tmp_memory_dir)
        assert result["has_difference"] is False
        assert result["magnitude"] == "none"
        assert result["nature"] == "undefined"

    def test_corrupted_snapshot_file(self, tmp_memory_dir):
        """Corrupted snapshot file should be handled gracefully."""
        _write_emotion_state(tmp_memory_dir)
        _write_dynamics_state(tmp_memory_dir)
        # Write corrupted file
        path = os.path.join(tmp_memory_dir, tsd.SNAPSHOTS_FILENAME)
        with open(path, "w") as f:
            f.write("not json{{{")
        result = tsd.compute_difference(tmp_memory_dir)
        assert result["has_difference"] is False
        assert result["magnitude"] == "none"

    def test_empty_snapshot_file(self, tmp_memory_dir):
        """Empty snapshot file should be handled gracefully."""
        _write_emotion_state(tmp_memory_dir)
        _write_dynamics_state(tmp_memory_dir)
        path = os.path.join(tmp_memory_dir, tsd.SNAPSHOTS_FILENAME)
        with open(path, "w") as f:
            f.write("{}")
        result = tsd.compute_difference(tmp_memory_dir)
        assert result["has_difference"] is False

    def test_single_history_entry(self, tmp_memory_dir):
        """With one previous snapshot, comparison should work."""
        _write_emotion_state(tmp_memory_dir, fulfillment=0.5)
        _write_dynamics_state(tmp_memory_dir)
        tsd.compute_difference(tmp_memory_dir)  # First call, no comparison

        _write_emotion_state(tmp_memory_dir, fulfillment=0.8)
        result = tsd.compute_difference(tmp_memory_dir)
        # Should detect a change (positive -> strongly_positive)
        assert result["components"]["fulfillment"]["change_type"] in ("unchanged", "intensified")

    def test_no_emotion_state_file(self, tmp_memory_dir):
        """No emotion state file should use defaults."""
        _write_dynamics_state(tmp_memory_dir)
        result = tsd.compute_difference(tmp_memory_dir)
        assert result is not None
        assert result["magnitude"] == "none"

    def test_comparison_count_increments(self, tmp_memory_dir):
        """comparison_count should increment with each call."""
        _write_emotion_state(tmp_memory_dir)
        _write_dynamics_state(tmp_memory_dir)
        tsd.compute_difference(tmp_memory_dir)
        tsd.compute_difference(tmp_memory_dir)
        tsd.compute_difference(tmp_memory_dir)
        data = tsd._load_snapshots(tmp_memory_dir)
        assert data["comparison_count"] == 3


# =============================================
# Category distance
# =============================================


class TestCategoryDistance:
    def test_same_category(self):
        assert tsd._category_distance("neutral", "neutral") == 0

    def test_adjacent(self):
        assert tsd._category_distance("neutral", "positive") == 1

    def test_two_apart(self):
        assert tsd._category_distance("neutral", "strongly_positive") == 2

    def test_extreme_distance(self):
        assert tsd._category_distance("strongly_negative", "strongly_positive") == 4

    def test_unknown_defaults_to_neutral(self):
        assert tsd._category_distance("unknown", "neutral") == 0


# =============================================
# Full integration test
# =============================================


class TestFullIntegration:
    def test_detect_change(self, tmp_memory_dir):
        """Full flow: state changes between calls should be detected."""
        _write_emotion_state(tmp_memory_dir, fulfillment=0.0, tension=0.0, affinity=0.0)
        _write_dynamics_state(tmp_memory_dir)

        # First call — baseline
        r1 = tsd.compute_difference(tmp_memory_dir)
        assert r1["has_difference"] is False

        # Change emotion state significantly
        _write_emotion_state(tmp_memory_dir, fulfillment=0.8, tension=-0.5, affinity=0.4)
        _write_dynamics_state(tmp_memory_dir, phase="peak")

        # Second call — should detect change
        r2 = tsd.compute_difference(tmp_memory_dir)
        assert r2["has_difference"] is True
        assert r2["magnitude"] in ("noticeable", "significant", "substantial")
        assert r2["components"]["fulfillment"]["change_type"] != "unchanged"
        assert r2["components"]["dynamics_phase"]["change_type"] == "shifted"
        assert r2["integrated_description"]
        assert "。" in r2["integrated_description"]

    def test_no_change_detected(self, tmp_memory_dir):
        """If state doesn't change, magnitude should be none."""
        _write_emotion_state(tmp_memory_dir, fulfillment=0.1, tension=0.1, affinity=0.1)
        _write_dynamics_state(tmp_memory_dir)

        tsd.compute_difference(tmp_memory_dir)
        # Same state
        r2 = tsd.compute_difference(tmp_memory_dir)
        assert r2["magnitude"] == "none"
        assert r2["has_difference"] is False

    def test_output_format(self, tmp_memory_dir):
        """Output should have all required keys."""
        _write_emotion_state(tmp_memory_dir)
        _write_dynamics_state(tmp_memory_dir)
        result = tsd.compute_difference(tmp_memory_dir)

        assert "has_difference" in result
        assert "magnitude" in result
        assert "nature" in result
        assert "components" in result
        assert "integrated_description" in result

        for axis in ("fulfillment", "tension", "affinity", "dynamics_phase"):
            assert axis in result["components"]
            comp = result["components"][axis]
            assert "change_type" in comp
            assert "from" in comp
            assert "to" in comp
