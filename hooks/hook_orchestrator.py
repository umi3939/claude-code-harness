#!/usr/bin/env python3
"""Hook orchestrator engine — runs multiple sub-processes sequentially with fault isolation.

Design: docs/design_hook_consolidation.md
- Reads stdin once, distributes to all sub-processes
- Individual timeouts per sub-process
- Fault isolation: one failure does not affect others
- Blocking (exit 2) propagation
- Self-edit detection skip
"""

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import List

# ── Constants ──

MAX_SUBPROCESS_COUNT = 20

# Set of file paths (normpath + casefold) that are considered "self" for self-edit detection.
# Each orchestrator entry-point registers itself here at import time.
_SELF_FILES: set = set()


def register_self_file(file_path: str) -> None:
    """Register a file path for self-edit detection."""
    normalized = os.path.normpath(os.path.abspath(file_path)).casefold()
    _SELF_FILES.add(normalized)


# Register the orchestrator engine itself
register_self_file(__file__)


def reset_self_files() -> None:
    """Reset _SELF_FILES to only the orchestrator engine itself (for testing)."""
    _SELF_FILES.clear()
    register_self_file(__file__)


# ── Data structures ──

@dataclass
class SubprocessDefinition:
    """Definition of a sub-process to run."""
    name: str
    command: List[str]
    timeout: int


@dataclass
class ExecutionResult:
    """Result of running a single sub-process."""
    name: str
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str = ""


# ── Core engine ──

def _is_self_edit(stdin_data: bytes) -> bool:
    """Check if the edited file is the orchestrator or any registered entry-point."""
    try:
        data = json.loads(stdin_data)
        file_path = data.get("tool_input", {}).get("file_path", "")
        if not file_path:
            return False
        target = os.path.normpath(os.path.abspath(file_path)).casefold()
        return target in _SELF_FILES
    except (json.JSONDecodeError, TypeError, AttributeError):
        return False


def run_orchestrator(
    definitions: List[SubprocessDefinition],
    stdin_data: bytes,
) -> List[ExecutionResult]:
    """Run all sub-process definitions sequentially with fault isolation.

    Args:
        definitions: Ordered list of sub-processes to run.
        stdin_data: Raw bytes to pipe to each sub-process's stdin.

    Returns:
        List of ExecutionResult, one per definition, in the same order.
    """
    # Max subprocess limit
    if len(definitions) > MAX_SUBPROCESS_COUNT:
        return [
            ExecutionResult(
                name=d.name,
                exit_code=-1,
                error=f"Too many sub-processes: {len(definitions)} > {MAX_SUBPROCESS_COUNT}",
            )
            for d in definitions
        ]

    # Self-edit detection
    if _is_self_edit(stdin_data):
        return [
            ExecutionResult(
                name=d.name,
                exit_code=0,
                stderr="Skipped: self-edit detected",
            )
            for d in definitions
        ]

    results: List[ExecutionResult] = []
    for defn in definitions:
        result = _run_single(defn, stdin_data)
        results.append(result)
    return results


def _run_single(defn: SubprocessDefinition, stdin_data: bytes) -> ExecutionResult:
    """Run a single sub-process with timeout and fault isolation."""
    try:
        proc = subprocess.run(
            defn.command,
            input=stdin_data,
            capture_output=True,
            timeout=defn.timeout,
        )
        return ExecutionResult(
            name=defn.name,
            exit_code=proc.returncode,
            stdout=proc.stdout.decode("utf-8", errors="replace"),
            stderr=proc.stderr.decode("utf-8", errors="replace"),
        )
    except subprocess.TimeoutExpired:
        return ExecutionResult(
            name=defn.name,
            exit_code=-1,
            timed_out=True,
            error=f"Timed out after {defn.timeout}s",
        )
    except FileNotFoundError:
        return ExecutionResult(
            name=defn.name,
            exit_code=-1,
            error=f"Command not found: {defn.command[0]}",
        )
    except OSError as exc:
        return ExecutionResult(
            name=defn.name,
            exit_code=-1,
            error=f"OS error: {exc}",
        )


def aggregate_and_exit(results: List[ExecutionResult]) -> None:
    """Aggregate results and exit with appropriate code.

    - stdout from all sub-processes is forwarded to stdout
    - stderr from all sub-processes is forwarded to stderr
    - If any sub-process returned exit 2 (blocking), exit 2
    - Otherwise exit 0
    - If >50% of sub-processes failed, emit a WARNING to stderr
    """
    has_blocking = False
    fail_count = 0

    for r in results:
        if r.stdout:
            sys.stdout.write(r.stdout)
        if r.stderr:
            sys.stderr.write(r.stderr)
        if r.exit_code == 2:
            has_blocking = True
        if r.exit_code != 0:
            fail_count += 1

    total = len(results)
    if total > 0 and fail_count > total / 2:
        sys.stderr.write(
            f"WARNING: {fail_count}/{total} sub-processes failed. "
            "Orchestrator health may be degraded.\n"
        )

    sys.exit(2 if has_blocking else 0)
