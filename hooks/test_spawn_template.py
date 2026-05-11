#!/usr/bin/env python3
"""Tests for spawn_template.py — agent spawn template context collection."""

import importlib.util
import os
import sys

SCRIPT_PATH = os.path.join(
    os.path.expanduser("~"), ".claude", "hooks", "spawn_template.py"
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
spec = importlib.util.spec_from_file_location("spawn_template", SCRIPT_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

print("=== Spawn Template Tests ===")

# T1: collect_spawn_context returns a dict
ctx = mod.collect_spawn_context("implementer")
check("T1: returns dict", isinstance(ctx, dict))

# T2: contains required keys
required_keys = ["subagent_type", "claude_md_instruction", "task_description_placeholder",
                 "constraint_notes"]
for key in required_keys:
    check(f"T2: has key '{key}'", key in ctx)

# T3: claude_md_instruction always tells agent to read CLAUDE.md
check("T3: claude_md_instruction mentions CLAUDE.md",
      "CLAUDE.md" in ctx.get("claude_md_instruction", ""))

# T4: subagent_type is preserved
check("T4: subagent_type preserved", ctx.get("subagent_type") == "implementer")

# T5: format_spawn_prompt returns a string
prompt = mod.format_spawn_prompt("reviewer", "レビューしてください", ["file1.py"])
check("T5: format_spawn_prompt returns string", isinstance(prompt, str))

# T6: prompt contains CLAUDE.md instruction
check("T6: prompt contains CLAUDE.md", "CLAUDE.md" in prompt)

# T7: prompt contains the task description
check("T7: prompt contains task", "レビューしてください" in prompt)

# T8: prompt contains file list
check("T8: prompt contains file", "file1.py" in prompt)

# T9: empty file list is handled
prompt_no_files = mod.format_spawn_prompt("designer", "設計してください", [])
check("T9: empty file list handled", isinstance(prompt_no_files, str))

# T10: unknown subagent_type doesn't crash
ctx_unknown = mod.collect_spawn_context("unknown_type")
check("T10: unknown subagent_type safe", isinstance(ctx_unknown, dict))

print(f"\nResults: {PASS} passed, {FAIL} failed")
if FAIL > 0:
    sys.exit(1)
