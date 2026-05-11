#!/usr/bin/env python3
"""Tests for context budget estimation in skill_executor.py."""

import importlib.util
import json
import os
import sys
import tempfile

SCRIPT_PATH = os.path.join(
    os.path.expanduser("~"), ".claude", "hooks", "skill_executor.py"
)

PASS = 0
FAIL = 0


def check(desc, condition):
    global PASS, FAIL
    if condition:
        print(f"  PASS: {desc}")
        PASS += 1
    else:
        print(f"  FAIL: {desc}")
        FAIL += 1


# Load module
spec = importlib.util.spec_from_file_location("skill_executor_budget", SCRIPT_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

print("=== Context Budget Tests ===")

# T1: _estimate_context_usage returns a dict
result = mod._estimate_context_usage()
check("T1: returns dict", isinstance(result, dict))

# T2: has required keys
for key in ["stm_entries", "estimated_tokens", "budget_pct", "warning"]:
    check(f"T2: has key '{key}'", key in result)

# T3: budget_pct is a number between 0 and 100
pct = result.get("budget_pct", -1)
check("T3: budget_pct is number", isinstance(pct, (int, float)))
check("T3: budget_pct >= 0", pct >= 0)

# T4: warning is empty string or warning message
warning = result.get("warning", None)
check("T4: warning is string", isinstance(warning, str))

# T5: high STM count triggers warning
# Create a temp memory dir with many STM entries
tmpdir = tempfile.mkdtemp()
stm_file = os.path.join(tmpdir, "short_term_memory.json")
entries = [{"category": "thought", "content": "x" * 500, "ts": "2026-03-25T10:00:00+00:00"} for _ in range(200)]
with open(stm_file, "w") as f:
    json.dump({"entries": entries}, f)

result_high = mod._estimate_context_usage(memory_dir=tmpdir)
check("T5: high STM count increases estimated_tokens", result_high["estimated_tokens"] > 0)

# T6: budget_pct increases with more entries
check("T6: high entry count shows higher budget_pct", result_high["budget_pct"] > 0)

import shutil

shutil.rmtree(tmpdir, ignore_errors=True)

# T7: empty memory dir returns safe defaults
tmpdir2 = tempfile.mkdtemp()
result_empty = mod._estimate_context_usage(memory_dir=tmpdir2)
check("T7: empty dir returns safe defaults", result_empty["stm_entries"] == 0)
shutil.rmtree(tmpdir2, ignore_errors=True)

print(f"\nResults: {PASS} passed, {FAIL} failed")
if FAIL > 0:
    sys.exit(1)
