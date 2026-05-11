#!/usr/bin/env python3
"""Spawn Template — standard context collection for agent startup.

Provides a structured template with all required information
for agent spawn prompts (CLAUDE_OPERATIONS.md section: エージェント起動テンプレート).

Usage:
    from spawn_template import collect_spawn_context, format_spawn_prompt
    ctx = collect_spawn_context("implementer")
    prompt = format_spawn_prompt("reviewer", "レビューしてください", ["file1.py"])
"""

import glob
import os

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
CLAUDE_DIR = os.path.normpath(os.path.join(HOOKS_DIR, ".."))
DOCS_DIR = os.path.join(CLAUDE_DIR, "docs")


def _get_recent_analysis_notes():
    """Extract HIGH/MED findings from the most recent analysis file."""
    try:
        if not os.path.isdir(DOCS_DIR):
            return ""
        analysis_files = sorted(
            [f for f in os.listdir(DOCS_DIR) if "analysis" in f.lower()],
            key=lambda f: os.path.getmtime(os.path.join(DOCS_DIR, f)),
            reverse=True,
        )
        if not analysis_files:
            return ""
        filepath = os.path.join(DOCS_DIR, analysis_files[0])
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        # Extract HIGH/MED lines
        findings = []
        for line in content.split("\n"):
            stripped = line.strip()
            if "HIGH" in stripped or "MED" in stripped:
                findings.append(stripped)
                if len(findings) >= 5:
                    break
        if findings:
            return "Recent analysis findings:\n" + "\n".join(findings)
        return ""
    except Exception:
        return ""


def _get_dev_flow_position():
    """Get current dev flow position for context."""
    try:
        import json

        state_file = os.path.join(HOOKS_DIR, ".dev-flow-state")
        if not os.path.isfile(state_file):
            return ""
        with open(state_file, "r", encoding="utf-8") as f:
            df = json.load(f)
        if not isinstance(df, dict):
            return ""
        # Find latest phase
        phases = ["design", "planner", "pre_analysis", "impl", "post_analysis", "reviewer"]
        latest = ""
        latest_time = 0
        for p in phases:
            t = df.get(p, 0) or 0
            if t > latest_time:
                latest = p
                latest_time = t
        return f"Current dev flow position: {latest}" if latest else ""
    except Exception:
        return ""


def collect_spawn_context(subagent_type):
    """Collect standard context for agent spawn.

    Args:
        subagent_type: The type of agent being spawned (e.g., "implementer", "reviewer")

    Returns:
        dict with keys: subagent_type, claude_md_instruction, task_description_placeholder,
                       constraint_notes, analysis_notes, dev_flow_position
    """
    ctx = {
        "subagent_type": subagent_type,
        "claude_md_instruction": "CLAUDE.mdとCLAUDE_OPERATIONS.mdを全文読んでください。",
        "task_description_placeholder": "[タスクの説明をここに記入]",
        "constraint_notes": "",
        "analysis_notes": "",
        "dev_flow_position": "",
    }

    # Add analysis notes if available
    analysis = _get_recent_analysis_notes()
    if analysis:
        ctx["analysis_notes"] = analysis
        ctx["constraint_notes"] = "注意: 解析で検出された問題があります。上記の指摘事項を考慮してください。"

    # Add dev flow position
    flow_pos = _get_dev_flow_position()
    if flow_pos:
        ctx["dev_flow_position"] = flow_pos

    return ctx


def format_spawn_prompt(subagent_type, task_description, input_files=None, constraints=None):
    """Format a complete spawn prompt for an agent.

    Args:
        subagent_type: Agent type (e.g., "implementer", "reviewer")
        task_description: Clear description of the task
        input_files: List of file paths the agent should read
        constraints: Optional list of constraints/notes

    Returns:
        Formatted prompt string ready for Agent tool
    """
    ctx = collect_spawn_context(subagent_type)

    parts = []

    # 1. CLAUDE.md instruction (always first)
    parts.append(ctx["claude_md_instruction"])
    parts.append("")

    # 2. Task description
    parts.append(f"## タスク")
    parts.append(task_description)
    parts.append("")

    # 3. Input files
    if input_files:
        parts.append("## 入力ファイル（全て読むこと）")
        for f in input_files:
            parts.append(f"- {f}")
        parts.append("")

    # 4. Constraints
    all_constraints = []
    if ctx["constraint_notes"]:
        all_constraints.append(ctx["constraint_notes"])
    if constraints:
        all_constraints.extend(constraints)
    if all_constraints:
        parts.append("## 制約・注意点")
        for c in all_constraints:
            parts.append(f"- {c}")
        parts.append("")

    # 5. Analysis notes
    if ctx["analysis_notes"]:
        parts.append(f"## {ctx['analysis_notes']}")
        parts.append("")

    # 6. Dev flow position
    if ctx["dev_flow_position"]:
        parts.append(f"## {ctx['dev_flow_position']}")

    return "\n".join(parts)


if __name__ == "__main__":
    import sys
    import json

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    subagent = sys.argv[1] if len(sys.argv) > 1 else "implementer"
    ctx = collect_spawn_context(subagent)
    print(json.dumps(ctx, ensure_ascii=False, indent=2))
