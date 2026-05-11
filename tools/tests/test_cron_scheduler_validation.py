"""Tests for cron_scheduler add() validation of invalid cron expressions."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.expanduser("~"), ".claude", "tools"))

from cron_scheduler import CronJob, JobRegistry


@pytest.fixture
def tmp_registry(tmp_path):
    """Create a temporary JobRegistry."""
    store_path = str(tmp_path / "jobs.json")
    return JobRegistry(store_path=store_path)


class TestAddCronValidation:
    """Test that add() rejects invalid cron expressions."""

    def test_invalid_cron_out_of_range_raises(self, tmp_registry):
        """Out-of-range cron field values should raise ValueError."""
        job = CronJob(
            name="bad-cron",
            schedule={"type": "cron", "expression": "99 99 99 99 99"},
            prompt="echo hi",
        )
        with pytest.raises(ValueError, match="Invalid cron expression"):
            tmp_registry.add(job)

    def test_invalid_cron_garbage_raises(self, tmp_registry):
        """Garbage cron expression should raise ValueError."""
        job = CronJob(
            name="garbage-cron",
            schedule={"type": "cron", "expression": "not a cron"},
            prompt="echo hi",
        )
        with pytest.raises(ValueError, match="Invalid cron expression"):
            tmp_registry.add(job)

    def test_valid_cron_succeeds(self, tmp_registry):
        """Valid cron expression should be accepted and saved."""
        job = CronJob(
            name="good-cron",
            schedule={"type": "cron", "expression": "*/5 * * * *"},
            prompt="echo hi",
        )
        result = tmp_registry.add(job)
        assert result.id  # ID was assigned
        assert result.next_run is not None  # next_run was computed

    def test_valid_every_schedule_succeeds(self, tmp_registry):
        """Non-cron schedule types should still work fine."""
        job = CronJob(
            name="every-job",
            schedule={"type": "every", "interval_seconds": 300},
            prompt="echo hi",
        )
        result = tmp_registry.add(job)
        assert result.id
        assert result.next_run is not None

    def test_disabled_job_with_invalid_cron_ok(self, tmp_registry):
        """Disabled jobs skip validation (next_run not computed)."""
        job = CronJob(
            name="disabled-bad-cron",
            enabled=False,
            schedule={"type": "cron", "expression": "* * * * * *"},
            prompt="echo hi",
        )
        # Should not raise because enabled=False skips compute_next_run
        result = tmp_registry.add(job)
        assert result.id
