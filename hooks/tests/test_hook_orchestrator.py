#!/usr/bin/env python3
"""Tests for hook_orchestrator.py — core orchestrator engine."""

import json
import os
import sys

import pytest

HOOKS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)

from hook_orchestrator import (
    ExecutionResult,
    SubprocessDefinition,
    aggregate_and_exit,
    reset_self_files,
    run_orchestrator,
)


@pytest.fixture(autouse=True)
def _reset_self_files_between_tests():
    """Reset _SELF_FILES before and after each test to avoid cross-test contamination."""
    reset_self_files()
    yield
    reset_self_files()


# ── Test 1: Basic successful execution ──

def test_single_subprocess_success():
    """A single sub-process that exits 0 produces a clean result."""
    defn = SubprocessDefinition(
        name="echo-test",
        command=[sys.executable, "-c", "import sys; print('hello'); sys.exit(0)"],
        timeout=10,
    )
    results = run_orchestrator([defn], stdin_data=b'{}')
    assert len(results) == 1
    assert results[0].exit_code == 0
    assert "hello" in results[0].stdout
    assert results[0].timed_out is False
    assert results[0].error == ""


# ── Test 2: Blocking exit code propagation ──

def test_blocking_exit_code():
    """Sub-process returning exit 2 is recorded correctly."""
    defn = SubprocessDefinition(
        name="blocker",
        command=[sys.executable, "-c", "import sys; sys.stderr.write('blocked!\\n'); sys.exit(2)"],
        timeout=10,
    )
    results = run_orchestrator([defn], stdin_data=b'{}')
    assert results[0].exit_code == 2
    assert "blocked!" in results[0].stderr


# ── Test 3: Timeout handling ──

def test_timeout_handling():
    """Sub-process exceeding timeout is marked as timed out."""
    defn = SubprocessDefinition(
        name="slow",
        command=[sys.executable, "-c", "import time; time.sleep(30)"],
        timeout=1,
    )
    results = run_orchestrator([defn], stdin_data=b'{}')
    assert results[0].timed_out is True
    assert results[0].exit_code == -1
    assert "Timed out" in results[0].error


# ── Test 4: Fault isolation ──

def test_fault_isolation_continues_after_failure():
    """After one sub-process fails, the next still runs."""
    defns = [
        SubprocessDefinition(
            name="fail",
            command=[sys.executable, "-c", "import sys; sys.exit(1)"],
            timeout=5,
        ),
        SubprocessDefinition(
            name="ok",
            command=[sys.executable, "-c", "import sys; print('ran'); sys.exit(0)"],
            timeout=5,
        ),
    ]
    results = run_orchestrator(defns, stdin_data=b'{}')
    assert len(results) == 2
    assert results[0].exit_code == 1
    assert results[1].exit_code == 0
    assert "ran" in results[1].stdout


# ── Test 5: stdin distribution ──

def test_stdin_distributed_to_all():
    """Each sub-process receives the same stdin data."""
    payload = json.dumps({"tool_input": {"file_path": "test.py"}}).encode("utf-8")
    defns = [
        SubprocessDefinition(
            name="reader1",
            command=[sys.executable, "-c",
                     "import sys, json; d=json.load(sys.stdin); print(d['tool_input']['file_path'])"],
            timeout=5,
        ),
        SubprocessDefinition(
            name="reader2",
            command=[sys.executable, "-c",
                     "import sys, json; d=json.load(sys.stdin); print(d['tool_input']['file_path'])"],
            timeout=5,
        ),
    ]
    results = run_orchestrator(defns, stdin_data=payload)
    assert results[0].stdout.strip() == "test.py"
    assert results[1].stdout.strip() == "test.py"


# ── Test 6: Self-edit detection ──

def test_self_edit_skips_all():
    """When the edited file is the orchestrator itself, all sub-processes are skipped."""
    orchestrator_path = os.path.join(HOOKS_DIR, "hook_orchestrator.py")
    payload = json.dumps({
        "tool_input": {"file_path": orchestrator_path}
    }).encode("utf-8")
    defn = SubprocessDefinition(
        name="should-skip",
        command=[sys.executable, "-c", "print('should not run')"],
        timeout=5,
    )
    results = run_orchestrator([defn], stdin_data=payload)
    assert results[0].exit_code == 0
    assert "self-edit" in results[0].stderr


# ── Test 7: aggregate_and_exit with blocking ──

def test_aggregate_exit_blocking(capsys):
    """aggregate_and_exit exits 2 if any result has exit_code 2."""
    results = [
        ExecutionResult(name="ok", exit_code=0, stdout="ok\n", stderr=""),
        ExecutionResult(name="block", exit_code=2, stdout="", stderr="blocked\n"),
    ]
    with pytest.raises(SystemExit) as exc_info:
        aggregate_and_exit(results)
    assert exc_info.value.code == 2


# ── Test 8: aggregate_and_exit all green ──

def test_aggregate_exit_clean(capsys):
    """aggregate_and_exit exits 0 when all sub-processes succeed."""
    results = [
        ExecutionResult(name="a", exit_code=0),
        ExecutionResult(name="b", exit_code=0),
    ]
    with pytest.raises(SystemExit) as exc_info:
        aggregate_and_exit(results)
    assert exc_info.value.code == 0


# ── Test 9: Command not found ──

def test_command_not_found():
    """Non-existent command produces an error, not a crash."""
    defn = SubprocessDefinition(
        name="missing",
        command=["nonexistent_binary_xyz_123"],
        timeout=5,
    )
    results = run_orchestrator([defn], stdin_data=b'{}')
    assert results[0].exit_code == -1
    assert "not found" in results[0].error.lower() or "Command not found" in results[0].error


# ── Test 10: Majority failure warning ──

def test_majority_failure_warning(capsys):
    """When >50% sub-processes fail, a warning is emitted."""
    results = [
        ExecutionResult(name="fail1", exit_code=1, error="err"),
        ExecutionResult(name="fail2", exit_code=1, error="err"),
        ExecutionResult(name="ok", exit_code=0),
    ]
    with pytest.raises(SystemExit) as exc_info:
        aggregate_and_exit(results)
    assert exc_info.value.code == 0  # no blocking, just warning
    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert "2/3" in captured.err


# ── Test 11: Max subprocess limit ──

def test_max_subprocess_limit():
    """Exceeding MAX_SUBPROCESS_COUNT rejects all definitions."""
    defns = [
        SubprocessDefinition(name=f"d{i}", command=["echo", "x"], timeout=1)
        for i in range(25)
    ]
    results = run_orchestrator(defns, stdin_data=b'{}')
    assert len(results) == 25
    assert all("too many" in r.error.lower() for r in results)


# ── Test 12: reset_self_files restores to engine-only ──

def test_reset_self_files():
    """reset_self_files() clears extra entries and keeps only the engine."""
    from hook_orchestrator import _SELF_FILES, register_self_file, reset_self_files

    # Add a fake entry
    register_self_file("/tmp/fake_orchestrator.py")
    fake_norm = os.path.normpath(os.path.abspath("/tmp/fake_orchestrator.py")).casefold()
    assert fake_norm in _SELF_FILES

    # Reset
    reset_self_files()

    # Fake entry removed, engine entry remains
    assert fake_norm not in _SELF_FILES
    engine_path = os.path.normpath(
        os.path.abspath(os.path.join(HOOKS_DIR, "hook_orchestrator.py"))
    ).casefold()
    assert engine_path in _SELF_FILES


# ── Test 13: aggregate_and_exit forwards stdout ──

def test_aggregate_exit_forwards_stdout(capsys):
    """aggregate_and_exit writes sub-process stdout to sys.stdout."""
    results = [
        ExecutionResult(name="a", exit_code=0, stdout="output_from_a\n"),
        ExecutionResult(name="b", exit_code=0, stdout="output_from_b\n"),
    ]
    with pytest.raises(SystemExit):
        aggregate_and_exit(results)
    captured = capsys.readouterr()
    assert "output_from_a\n" in captured.out
    assert "output_from_b\n" in captured.out
