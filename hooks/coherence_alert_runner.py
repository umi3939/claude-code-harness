#!/usr/bin/env python3
"""Runner script for coherence alert, called by behavior-guard.js.

Loads identity coherence, checks cooldown, and outputs alert to stdout.
behavior-guard.js reads stdout and uses exit code to determine blocking.

exit(2) when coherence is unsettled/disconnected (blocking).
exit(0) otherwise.
"""

import os
import sys

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.join(HOOKS_DIR, "..", "tools")
DATA_DIR = os.path.join(HOOKS_DIR, "..", "data")

if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)


def _find_memory_dir():
    """Find the memory directory (same logic as skill_executor.py)."""
    import glob
    claude_dir = os.path.join(HOOKS_DIR, "..")
    candidates = glob.glob(os.path.join(claude_dir, "projects", "*", "memory"))
    if not candidates:
        return None
    try:
        cwd = os.getcwd().replace("\\", "/").replace("/", "-").replace(":", "-")
        for c in candidates:
            parent = os.path.basename(os.path.dirname(c))
            if parent == cwd:
                return c
    except Exception:
        pass
    try:
        return max(candidates, key=lambda d: os.path.getmtime(d))
    except Exception:
        return candidates[0] if candidates else None


def main():
    try:
        memory_dir = _find_memory_dir()
        if not memory_dir:
            return 0

        from identity_coherence import assess_coherence
        result = assess_coherence(memory_dir)
        coherence_level = result.get("coherence_level", "")

        os.makedirs(DATA_DIR, exist_ok=True)
        from coherence_alert import check_and_notify
        alert = check_and_notify(coherence_level, DATA_DIR)

        if alert["text"]:
            print(alert["text"])

        if alert["should_block"]:
            return 2

        return 0
    except Exception as e:
        sys.stderr.write("coherence_alert_runner: error: %s\n" % str(e))
        return 0


if __name__ == "__main__":
    sys.exit(main())
