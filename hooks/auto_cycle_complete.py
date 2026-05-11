#!/usr/bin/env python3
"""Auto cycle_complete trigger — fires when reviewer APPROVE is detected.

Called from subagent-stop-logger.js when a reviewer agent completes.
Reads .dev-flow-state to verify:
1. impl > 0 (implementation was done)
2. reviewer > 0 (reviewer has run)
3. review_issues_pending is null or has count == 0 (no pending issues)
4. .cycle-complete-fired flag doesn't match current reviewer timestamp

If all conditions met, calls growth_recorder.handle_cycle_complete
to trigger record_success, update_mastery, create_aar, and all
Group 3 extension tools.

Fail-open: never blocks, never raises to caller.
"""

import json
import logging
import os
import sys

logger = logging.getLogger(__name__)

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.join(os.path.dirname(HOOKS_DIR), "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

DEV_FLOW_STATE_FILE = ".dev-flow-state"
CYCLE_COMPLETE_FIRED_FLAG = ".cycle-complete-fired"
MAX_CYCLE_NAME_LEN = 200


def should_trigger_cycle_complete(hooks_dir: str) -> bool:
    """Check if cycle_complete should be auto-triggered.

    Reads .dev-flow-state and checks:
    1. impl > 0
    2. reviewer > 0
    3. review_issues_pending is None or count == 0
    4. .cycle-complete-fired doesn't match reviewer timestamp

    Args:
        hooks_dir: Path to hooks directory containing state files.

    Returns:
        True if cycle_complete should fire, False otherwise.
    """
    state_file = os.path.join(hooks_dir, DEV_FLOW_STATE_FILE)
    if not os.path.isfile(state_file):
        return False

    try:
        with open(state_file, "r", encoding="utf-8") as f:
            df = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read dev-flow-state: %s", e)
        return False

    if not isinstance(df, dict):
        return False

    impl_time = df.get("impl", 0) or 0
    reviewer_time = df.get("reviewer", 0) or 0

    # Condition 1 & 2: both impl and reviewer must have run
    if impl_time <= 0 or reviewer_time <= 0:
        return False

    # Condition 3: no pending review issues
    review_issues = df.get("review_issues_pending")
    if review_issues is not None:
        if isinstance(review_issues, dict) and review_issues.get("count", 0) > 0:
            return False

    # Condition 4: check already-fired flag
    flag_file = os.path.join(hooks_dir, CYCLE_COMPLETE_FIRED_FLAG)
    if os.path.isfile(flag_file):
        try:
            with open(flag_file, "r", encoding="utf-8") as f:
                fired_ts = f.read().strip()
            if fired_ts == str(reviewer_time):
                return False
        except OSError:
            pass

    return True


def _read_cycle_name(hooks_dir: str) -> str:
    """Extract cycle name from .dev-flow-state or gap analysis.

    Falls back to 'auto-cycle' if no meaningful name can be derived.

    Args:
        hooks_dir: Path to hooks directory.

    Returns:
        Cycle name string.
    """
    # Try to read from dev-flow-state (may have cycle_name field)
    state_file = os.path.join(hooks_dir, DEV_FLOW_STATE_FILE)
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            df = json.load(f)
        if isinstance(df, dict) and df.get("cycle_name"):
            return str(df["cycle_name"])[:MAX_CYCLE_NAME_LEN]
    except (json.JSONDecodeError, OSError):
        pass

    return "auto-cycle"


def _write_fired_flag(hooks_dir: str, reviewer_time) -> None:
    """Write the cycle-complete-fired flag to prevent double firing.

    Uses atomic temp+rename pattern.

    Args:
        hooks_dir: Path to hooks directory.
        reviewer_time: Reviewer timestamp to record.
    """
    flag_file = os.path.join(hooks_dir, CYCLE_COMPLETE_FIRED_FLAG)
    tmp_file = flag_file + ".tmp." + str(os.getpid())
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            f.write(str(reviewer_time))
        os.replace(tmp_file, flag_file)
    except OSError as e:
        logger.warning("Failed to write fired flag: %s", e)
        # Clean up temp file if rename failed
        try:
            os.unlink(tmp_file)
        except OSError:
            pass


def run_cycle_complete(growth_dir: str, cycle_name: str = "",
                       test_count: int = 0) -> dict:
    """Run cycle_complete via growth_recorder.

    Args:
        growth_dir: Path to growth data directory.
        cycle_name: Cycle identifier.
        test_count: Number of tests passed.

    Returns:
        Result dict from handle_cycle_complete.
    """
    try:
        # Import here to avoid circular imports
        if HOOKS_DIR not in sys.path:
            sys.path.insert(0, HOOKS_DIR)

        import growth_recorder

        stdin_data = json.dumps({
            "cycle_name": cycle_name or "auto-cycle",
            "completed_gaps": [],
            "test_count": test_count,
            "review_result": "APPROVE",
        })

        os.makedirs(growth_dir, exist_ok=True)
        return growth_recorder.handle_cycle_complete(stdin_data, growth_dir)
    except Exception as e:
        logger.error("run_cycle_complete failed: %s", e)
        return {"success": False, "event_type": "cycle_complete", "error": str(e)}


def get_growth_dir() -> str:
    """Resolve growth directory from environment or default."""
    if os.environ.get("GROWTH_DIR"):
        return os.environ["GROWTH_DIR"]
    # Default: project_root/growth/ (same as memory_mcp_server.py GROWTH_DIR)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(project_root, "growth")


def main(hooks_dir: str = "") -> bool:
    """Main entry point: check conditions and auto-fire cycle_complete.

    Args:
        hooks_dir: Override hooks directory (for testing).

    Returns:
        True if cycle_complete was fired, False otherwise.
    """
    if not hooks_dir:
        hooks_dir = HOOKS_DIR

    if not should_trigger_cycle_complete(hooks_dir):
        return False

    # Read reviewer timestamp for fired flag
    state_file = os.path.join(hooks_dir, DEV_FLOW_STATE_FILE)
    reviewer_time = 0
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            df = json.load(f)
        reviewer_time = df.get("reviewer", 0) or 0
    except (json.JSONDecodeError, OSError):
        pass

    cycle_name = _read_cycle_name(hooks_dir)
    growth_dir = get_growth_dir()

    result = run_cycle_complete(growth_dir, cycle_name=cycle_name)

    # Write fired flag to prevent double-trigger — only on success
    # On failure, omit flag so the next reviewer stop can retry
    if reviewer_time > 0 and result.get("success"):
        _write_fired_flag(hooks_dir, reviewer_time)

    logger.info("Auto cycle_complete fired: %s", result)
    return True


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="[auto_cycle_complete] %(message)s",
        stream=sys.stderr,
    )
    fired = main()
    if fired:
        print("[AutoCycleComplete] cycle_complete auto-triggered", file=sys.stderr)
