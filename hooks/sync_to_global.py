"""Sync project hooks to global ~/.claude/hooks/ directory.

Called from SessionStart hook (startup and resume) to ensure
project-side hook files are always propagated to the global location.
This prevents the two-location management problem where one side
gets updated but not the other.
"""

import logging
import os
import shutil
import sys
import tempfile

logger = logging.getLogger(__name__)

# Files to sync from project hooks/ to global ~/.claude/hooks/
SYNC_TARGET_FILES = [
    "behavior-guard.js",
    "behavior-rules.json",
    "skill_executor.py",
    "coherence_alert.py",
    "coherence_alert_runner.py",
]

# Default paths
DEFAULT_GLOBAL_HOOKS_DIR = os.path.join(
    os.path.expanduser("~"), ".claude", "hooks"
)


def sync_hooks_to_global(
    project_hooks_dir: str, global_hooks_dir: str
) -> dict:
    """Copy target hook files from project to global directory.

    Uses temp-file + rename for each copy to avoid partial writes.

    Args:
        project_hooks_dir: Path to the project's hooks/ directory.
        global_hooks_dir: Path to ~/.claude/hooks/ directory.

    Returns:
        dict with keys: copied (list), skipped (list), errors (list).
    """
    result = {"copied": [], "skipped": [], "errors": []}

    # Create global dir if it doesn't exist
    try:
        os.makedirs(global_hooks_dir, exist_ok=True)
    except OSError as exc:
        logger.error(
            "Failed to create global hooks directory %s: %s",
            global_hooks_dir,
            exc,
        )
        # All files become errors
        for fname in SYNC_TARGET_FILES:
            result["errors"].append(f"{fname}: {exc}")
        return result

    for fname in SYNC_TARGET_FILES:
        src = os.path.join(project_hooks_dir, fname)

        if not os.path.isfile(src):
            result["skipped"].append(fname)
            continue

        dst = os.path.join(global_hooks_dir, fname)

        try:
            # Use temp file + rename for atomic-ish copy
            fd, tmp_path = tempfile.mkstemp(
                dir=global_hooks_dir, prefix=f".{fname}.tmp."
            )
            os.close(fd)
            try:
                shutil.copy2(src, tmp_path)
                # On Windows, os.replace works for overwriting
                os.replace(tmp_path, dst)
            except BaseException:
                # Clean up temp file on failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

            result["copied"].append(fname)
            logger.info("Synced %s -> %s", src, dst)

        except (OSError, PermissionError) as exc:
            result["errors"].append(f"{fname}: {exc}")
            logger.warning("Failed to sync %s: %s", fname, exc)

    return result


def main(
    project_hooks_dir: str = None, global_hooks_dir: str = None
) -> int:
    """Entry point for SessionStart hook invocation.

    Args:
        project_hooks_dir: Override for project hooks path (for testing).
        global_hooks_dir: Override for global hooks path (for testing).

    Returns:
        0 always (sync failures are non-fatal).
    """
    logging.basicConfig(
        level=logging.INFO,
        format="[sync_to_global] %(levelname)s: %(message)s",
    )

    if project_hooks_dir is None:
        project_hooks_dir = os.path.join(os.getcwd(), "hooks")
    if global_hooks_dir is None:
        global_hooks_dir = DEFAULT_GLOBAL_HOOKS_DIR

    logger.info(
        "Syncing hooks: %s -> %s", project_hooks_dir, global_hooks_dir
    )

    result = sync_hooks_to_global(project_hooks_dir, global_hooks_dir)

    if result["copied"]:
        logger.info("Copied: %s", ", ".join(result["copied"]))
    if result["skipped"]:
        logger.info("Skipped (not found): %s", ", ".join(result["skipped"]))
    if result["errors"]:
        logger.warning("Errors: %s", ", ".join(result["errors"]))

    # Always return 0 - sync failure should not block session start
    return 0


if __name__ == "__main__":
    sys.exit(main())
