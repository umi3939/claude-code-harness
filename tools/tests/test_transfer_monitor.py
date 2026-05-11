"""Tests for transfer_monitor.py — Positive Transfer Monitor.

Tests cover:
- load_log / save_log: file I/O, missing/corrupt fallback, atomic write
- record_transfer: normal, field length limits, max records (500), success/failure
- get_transfer_stats: per-pattern and global stats
- recommend_transfers: scoring, negative transfer warnings, limit
- get_transfer_report: formatted text output
- Edge cases: empty data, boundary values, invalid input
"""

import json
import os
import sys
from pathlib import Path

import pytest

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))
import transfer_monitor

# --- Fixtures ---


@pytest.fixture
def tmp_memory_dir(tmp_path):
    """Provide a temporary memory directory."""
    return str(tmp_path)


# --- load_log / save_log ---


class TestLoadLog:
    """load_log: JSON file read with fallback."""

    def test_no_file(self, tmp_memory_dir):
        """Missing file returns empty list."""
        result = transfer_monitor.load_log(tmp_memory_dir)
        assert result == []

    def test_corrupt_file(self, tmp_memory_dir):
        """Corrupt JSON returns empty list (fail-open)."""
        path = os.path.join(tmp_memory_dir, "transfer_log.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{corrupt json!!")
        result = transfer_monitor.load_log(tmp_memory_dir)
        assert result == []

    def test_not_a_list(self, tmp_memory_dir):
        """Non-list JSON returns empty list."""
        path = os.path.join(tmp_memory_dir, "transfer_log.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"not": "a list"}, f)
        result = transfer_monitor.load_log(tmp_memory_dir)
        assert result == []

    def test_empty_file(self, tmp_memory_dir):
        """Empty file returns empty list."""
        path = os.path.join(tmp_memory_dir, "transfer_log.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("")
        result = transfer_monitor.load_log(tmp_memory_dir)
        assert result == []

    def test_valid_file(self, tmp_memory_dir):
        """Valid JSON list loads correctly."""
        path = os.path.join(tmp_memory_dir, "transfer_log.json")
        data = [{"id": 1, "pattern_id": "P1"}]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        result = transfer_monitor.load_log(tmp_memory_dir)
        assert result == data


class TestSaveLog:
    """save_log: atomic write."""

    def test_creates_directory(self, tmp_path):
        """Creates memory_dir if it does not exist."""
        new_dir = str(tmp_path / "subdir")
        transfer_monitor.save_log(new_dir, [{"id": 1}])
        path = os.path.join(new_dir, "transfer_log.json")
        assert os.path.exists(path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data == [{"id": 1}]

    def test_overwrites_existing(self, tmp_memory_dir):
        """Overwrites existing file atomically."""
        transfer_monitor.save_log(tmp_memory_dir, [{"id": 1}])
        transfer_monitor.save_log(tmp_memory_dir, [{"id": 1}, {"id": 2}])
        result = transfer_monitor.load_log(tmp_memory_dir)
        assert len(result) == 2


# --- record_transfer ---


class TestRecordTransfer:
    """record_transfer: create transfer event records."""

    def test_basic_success(self, tmp_memory_dir):
        """Records a successful transfer."""
        rec = transfer_monitor.record_transfer(
            tmp_memory_dir,
            pattern_id="P1",
            source_domain="hook_impl",
            target_domain="mcp_tool",
            success=True,
            notes="Pattern worked well",
        )
        assert rec["id"] == 1
        assert rec["pattern_id"] == "P1"
        assert rec["source_domain"] == "hook_impl"
        assert rec["target_domain"] == "mcp_tool"
        assert rec["success"] is True
        assert rec["notes"] == "Pattern worked well"
        assert "recorded_at" in rec

    def test_basic_failure(self, tmp_memory_dir):
        """Records a failed transfer."""
        rec = transfer_monitor.record_transfer(
            tmp_memory_dir,
            pattern_id="P2",
            source_domain="testing",
            target_domain="deployment",
            success=False,
            notes="Did not transfer well",
        )
        assert rec["success"] is False

    def test_sequential_ids(self, tmp_memory_dir):
        """IDs are sequential."""
        r1 = transfer_monitor.record_transfer(
            tmp_memory_dir, "P1", "A", "B", True
        )
        r2 = transfer_monitor.record_transfer(
            tmp_memory_dir, "P2", "C", "D", False
        )
        assert r1["id"] == 1
        assert r2["id"] == 2

    def test_notes_truncated(self, tmp_memory_dir):
        """Notes longer than 500 chars are truncated."""
        long_notes = "x" * 600
        rec = transfer_monitor.record_transfer(
            tmp_memory_dir, "P1", "A", "B", True, notes=long_notes
        )
        assert len(rec["notes"]) == 500

    def test_domain_truncated(self, tmp_memory_dir):
        """Domain names longer than 50 chars are truncated."""
        long_domain = "d" * 80
        rec = transfer_monitor.record_transfer(
            tmp_memory_dir, "P1", long_domain, long_domain, True
        )
        assert len(rec["source_domain"]) == 50
        assert len(rec["target_domain"]) == 50

    def test_empty_notes_default(self, tmp_memory_dir):
        """Empty notes defaults to empty string."""
        rec = transfer_monitor.record_transfer(
            tmp_memory_dir, "P1", "A", "B", True
        )
        assert rec["notes"] == ""

    def test_max_records_cap(self, tmp_memory_dir):
        """Records beyond MAX_RECORDS evict oldest."""
        for i in range(502):
            transfer_monitor.record_transfer(
                tmp_memory_dir, f"P{i}", "A", "B", True
            )
        records = transfer_monitor.load_log(tmp_memory_dir)
        assert len(records) == 500
        # Oldest (id=1,2) should be evicted
        ids = [r["id"] for r in records]
        assert 1 not in ids
        assert 2 not in ids
        assert 502 in ids

    def test_empty_pattern_id_raises(self, tmp_memory_dir):
        """Empty pattern_id raises ValueError."""
        with pytest.raises(ValueError, match="pattern_id"):
            transfer_monitor.record_transfer(
                tmp_memory_dir, "", "A", "B", True
            )

    def test_empty_domain_raises(self, tmp_memory_dir):
        """Empty domain raises ValueError."""
        with pytest.raises(ValueError, match="domain"):
            transfer_monitor.record_transfer(
                tmp_memory_dir, "P1", "", "B", True
            )
        with pytest.raises(ValueError, match="domain"):
            transfer_monitor.record_transfer(
                tmp_memory_dir, "P1", "A", "", True
            )


# --- get_transfer_stats ---


class TestGetTransferStats:
    """get_transfer_stats: aggregate transfer statistics."""

    def test_empty(self, tmp_memory_dir):
        """No records returns zero stats."""
        stats = transfer_monitor.get_transfer_stats(tmp_memory_dir)
        assert stats["total"] == 0
        assert stats["positive_transfers"] == 0
        assert stats["negative_transfers"] == 0
        assert stats["success_rate"] == 0.0

    def test_global_stats(self, tmp_memory_dir):
        """Global stats across all patterns."""
        transfer_monitor.record_transfer(tmp_memory_dir, "P1", "A", "B", True)
        transfer_monitor.record_transfer(tmp_memory_dir, "P1", "A", "C", False)
        transfer_monitor.record_transfer(tmp_memory_dir, "P2", "X", "Y", True)

        stats = transfer_monitor.get_transfer_stats(tmp_memory_dir)
        assert stats["total"] == 3
        assert stats["positive_transfers"] == 2
        assert stats["negative_transfers"] == 1
        assert abs(stats["success_rate"] - 2 / 3) < 0.01

    def test_per_pattern_stats(self, tmp_memory_dir):
        """Stats filtered by pattern_id."""
        transfer_monitor.record_transfer(tmp_memory_dir, "P1", "A", "B", True)
        transfer_monitor.record_transfer(tmp_memory_dir, "P1", "A", "C", False)
        transfer_monitor.record_transfer(tmp_memory_dir, "P2", "X", "Y", True)

        stats = transfer_monitor.get_transfer_stats(tmp_memory_dir, pattern_id="P1")
        assert stats["total"] == 2
        assert stats["positive_transfers"] == 1
        assert stats["negative_transfers"] == 1
        assert stats["success_rate"] == 0.5

    def test_pattern_not_found(self, tmp_memory_dir):
        """Non-existent pattern returns zero stats."""
        transfer_monitor.record_transfer(tmp_memory_dir, "P1", "A", "B", True)
        stats = transfer_monitor.get_transfer_stats(tmp_memory_dir, pattern_id="NONEXIST")
        assert stats["total"] == 0


# --- recommend_transfers ---


class TestRecommendTransfers:
    """recommend_transfers: suggest patterns for a target domain."""

    def test_empty(self, tmp_memory_dir):
        """No records returns empty list."""
        result = transfer_monitor.recommend_transfers(tmp_memory_dir, "target_domain")
        assert result == []

    def test_recommends_successful_patterns(self, tmp_memory_dir):
        """Recommends patterns that succeeded in target domain."""
        transfer_monitor.record_transfer(tmp_memory_dir, "P1", "A", "mcp", True)
        transfer_monitor.record_transfer(tmp_memory_dir, "P2", "B", "mcp", True)
        transfer_monitor.record_transfer(tmp_memory_dir, "P3", "C", "hook", True)

        recs = transfer_monitor.recommend_transfers(tmp_memory_dir, "mcp")
        pattern_ids = [r["pattern_id"] for r in recs]
        assert "P1" in pattern_ids
        assert "P2" in pattern_ids
        # P3 succeeded in different domain, may still appear but lower priority

    def test_negative_transfer_warning(self, tmp_memory_dir):
        """Patterns with negative transfers to target domain get warnings."""
        transfer_monitor.record_transfer(tmp_memory_dir, "P1", "A", "mcp", False)
        transfer_monitor.record_transfer(tmp_memory_dir, "P1", "B", "hook", True)

        recs = transfer_monitor.recommend_transfers(tmp_memory_dir, "mcp")
        # P1 should have a warning because it failed in mcp before
        p1_recs = [r for r in recs if r["pattern_id"] == "P1"]
        if p1_recs:
            assert p1_recs[0].get("warning") is not None

    def test_limit(self, tmp_memory_dir):
        """Respects limit parameter."""
        for i in range(10):
            transfer_monitor.record_transfer(
                tmp_memory_dir, f"P{i}", "A", "target", True
            )
        recs = transfer_monitor.recommend_transfers(tmp_memory_dir, "target", limit=3)
        assert len(recs) <= 3

    def test_prioritizes_target_domain_success(self, tmp_memory_dir):
        """Patterns that succeeded in target domain rank higher."""
        # P1: succeeded in target domain
        transfer_monitor.record_transfer(tmp_memory_dir, "P1", "A", "mcp", True)
        # P2: succeeded in different domain only
        transfer_monitor.record_transfer(tmp_memory_dir, "P2", "A", "hook", True)

        recs = transfer_monitor.recommend_transfers(tmp_memory_dir, "mcp", limit=2)
        if len(recs) >= 2:
            # P1 should be first (direct match)
            assert recs[0]["pattern_id"] == "P1"


# --- get_transfer_report ---


class TestGetTransferReport:
    """get_transfer_report: formatted text output."""

    def test_empty_report(self, tmp_memory_dir):
        """Empty data produces a report with zero stats."""
        report = transfer_monitor.get_transfer_report(tmp_memory_dir)
        assert isinstance(report, str)
        assert "0" in report

    def test_report_with_data(self, tmp_memory_dir):
        """Report includes transfer information."""
        transfer_monitor.record_transfer(tmp_memory_dir, "P1", "A", "B", True, "Good")
        transfer_monitor.record_transfer(tmp_memory_dir, "P1", "A", "C", False, "Bad")

        report = transfer_monitor.get_transfer_report(tmp_memory_dir)
        assert "P1" in report
        assert "A" in report
        assert isinstance(report, str)
        assert len(report) > 0
