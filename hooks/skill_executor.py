#!/usr/bin/env python3
"""Skill Executor - Hook -> Skill -> MCP chain context injection.

Called by behavior-guard.js via child_process.execSync when Agent/TeamCreate
or mcp__* tools are invoked. Reads internal state and outputs context
that gets injected into Claude's conversation via stdout.

Usage:
    python skill_executor.py agent <tool_name>    # Agent/TeamCreate context
    python skill_executor.py mcp <tool_name>      # MCP tool context

Output goes to stdout for Claude context injection (exit 0).
"""

import glob
import os
import sys

# --- Path constants ---

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
_CLAUDE_DIR = os.path.join(HOOKS_DIR, "..")
TOOLS_DIR = os.path.join(_CLAUDE_DIR, "tools")
DOCS_DIR = os.path.join(_CLAUDE_DIR, "docs")
COMMANDS_DIR = os.path.join(_CLAUDE_DIR, "commands")

# Find memory directory (glob for project memory dirs)
_memory_candidates = glob.glob(
    os.path.join(_CLAUDE_DIR, "projects", "*", "memory")
)


PROJECTS_DIR = os.path.join(_CLAUDE_DIR, "projects")


CLAUDE_DIR = os.path.normpath(_CLAUDE_DIR)

# Allowlist of known directories under .claude/ that are safe to access
_SAFE_SUBDIRS = {"projects", "tools", "hooks", "docs", "commands", "agents",
                 "growth", "cron", "data", "memory", "plugins", "cache"}


def _is_safe_path(candidate):
    """Validate that a path is not a traversal attack targeting .claude/.

    Rules:
    1. Paths that claim to be under .claude/ (contain .claude in path) must
       resolve (via realpath) to actually be under ~/.claude/.
    2. For candidates under .claude/projects/, additionally verifies they stay
       within projects/ after symlink resolution.
    3. Paths that don't reference .claude/ at all (e.g., test fixtures) are allowed.
    Returns True if safe, False otherwise.
    """
    try:
        candidate_norm = candidate.replace("\\", "/")

        # Check if this path claims to be under .claude/
        if ".claude" in candidate_norm:
            real = os.path.realpath(candidate)
            claude_real = os.path.realpath(CLAUDE_DIR)
            real_n = real.replace("\\", "/").rstrip("/")
            claude_real_n = claude_real.replace("\\", "/").rstrip("/")

            # Must actually resolve to under .claude/
            if not (real_n.startswith(claude_real_n + "/") or real_n == claude_real_n):
                return False

            # For projects-glob paths, additionally verify stays under projects/
            projects_norm = PROJECTS_DIR.replace("\\", "/")
            if projects_norm in candidate_norm:
                projects_real = os.path.realpath(PROJECTS_DIR)
                proj_real_n = projects_real.replace("\\", "/").rstrip("/")
                return real_n.startswith(proj_real_n + "/") or real_n == proj_real_n

        # Path doesn't reference .claude/ (e.g., test path) — allow
        return True
    except Exception:
        return False


def _select_memory_dir(candidates):
    """Select the best memory directory based on cwd matching, then recency fallback.

    Priority:
    1. Match cwd to project directory name (path separators -> hyphens)
    2. Most recently modified candidate
    3. First candidate (legacy behavior)

    All candidates are validated via realpath to prevent symlink-based path traversal.
    """
    # Filter candidates to only safe paths (inside .claude/projects/)
    safe_candidates = [c for c in candidates if _is_safe_path(c)]
    if not safe_candidates:
        return ""
    if len(safe_candidates) == 1:
        return safe_candidates[0]

    # Try to match cwd to project directory name
    try:
        cwd = os.getcwd()
        # Claude Code project naming: path separators become hyphens, colon removed
        # e.g. C:\Users\user\.claude -> C--Users-user--claude
        cwd_normalized = cwd.replace("\\", "/")
        # Remove drive letter colon: "C:" -> "C"
        if len(cwd_normalized) >= 2 and cwd_normalized[1] == ":":
            cwd_normalized = cwd_normalized[0] + cwd_normalized[2:]
        # Replace / with -
        cwd_key = cwd_normalized.replace("/", "-")

        for candidate in safe_candidates:
            # Extract project dir name from path
            # candidates look like: /home/user/.claude/projects/C--Users-user--claude/memory
            parent = os.path.basename(os.path.dirname(candidate))
            if parent == cwd_key:
                return candidate
    except Exception:
        pass

    # Fallback: most recently modified directory
    try:
        return max(safe_candidates, key=lambda d: os.path.getmtime(d))
    except Exception:
        return safe_candidates[0]


MEMORY_DIR = _select_memory_dir(_memory_candidates)

# Add tools to sys.path for imports
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

# Maximum file size for gap analysis files (1 MB)
MAX_GAP_ANALYSIS_SIZE = 1024 * 1024

import re as _re


# --- Group B 1:1:1: Skill.md reading + MCP dispatch helpers ---


def _read_tool_skill_md(skill_name: str):
    """Read a tool Skill.md file and extract MCP function info.

    Args:
        skill_name: e.g. "tool-behavior-guidance"

    Returns:
        Dict with mcp_function key, or None if file not found/parse error.
    """
    skill_path = os.path.join(COMMANDS_DIR, f"{skill_name}.md")
    if not os.path.isfile(skill_path):
        return None
    try:
        with open(skill_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None

    # Use \r?\n to support both LF and CRLF line endings (Windows .md files)
    match = _re.search(r"##\s*MCP\s*function\s*\r?\n\s*(\S+)", content)
    if match:
        mcp_ref = match.group(1)
        parts = mcp_ref.split("__")
        func_name = parts[-1] if parts else mcp_ref
        return {"mcp_function": func_name, "mcp_ref": mcp_ref}
    return None


def _call_behavior_guidance_mcp(memory_dir: str, docs_dir: str) -> str:
    """Call behavior_guidance via MCP impl (Skill.md -> MCP pattern).

    Falls back to direct import if MCP impl is unavailable.
    """
    import logging as _logging
    _logger = _logging.getLogger(__name__)

    skill_info = _read_tool_skill_md("tool-behavior-guidance")
    if skill_info:
        _logger.debug("Skill.md loaded: tool-behavior-guidance -> %s", skill_info.get("mcp_function"))
    else:
        _logger.debug("Skill.md not found for tool-behavior-guidance, using direct import fallback")

    try:
        from self_observation_mcp_server import _behavior_guidance_impl
        return _behavior_guidance_impl(memory_dir=memory_dir, docs_dir=docs_dir)
    except ImportError:
        from behavior_guidance import generate_guidance
        return generate_guidance(memory_dir, docs_dir)


def _call_psyche_drive_mcp(memory_dir: str) -> None:
    """Call psyche_drive via MCP impl (Skill.md -> MCP pattern).

    Falls back to direct import if MCP impl is unavailable.
    """
    import logging as _logging
    _logger = _logging.getLogger(__name__)

    skill_info = _read_tool_skill_md("tool-psyche-drive")
    if skill_info:
        _logger.debug("Skill.md loaded: tool-psyche-drive -> %s", skill_info.get("mcp_function"))
    else:
        _logger.debug("Skill.md not found for tool-psyche-drive, using direct import fallback")

    try:
        from self_observation_mcp_server import _psyche_drive_impl
        _psyche_drive_impl(memory_dir=memory_dir)
    except ImportError:
        from psyche_drive import run_psyche_drive
        run_psyche_drive(memory_dir)


def _get_gap_analysis_summary():
    """Read the most recent gap_analysis file from docs/ and extract gap items."""
    try:
        if not os.path.isdir(DOCS_DIR):
            return ""
        gap_files = sorted(
            [f for f in os.listdir(DOCS_DIR) if f.startswith("gap_analysis")],
            reverse=True,
        )
        if not gap_files:
            return ""
        filepath = os.path.join(DOCS_DIR, gap_files[0])
        # M-S5: Check file size before reading
        try:
            file_size = os.path.getsize(filepath)
            if file_size > MAX_GAP_ANALYSIS_SIZE:
                return f"Gap Analysis: {gap_files[0]} (file too large: {file_size} bytes, limit: {MAX_GAP_ANALYSIS_SIZE})"
        except OSError:
            return ""
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        # Extract gap items (lines starting with ## G or | G)
        lines = []
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("## G") or (
                stripped.startswith("| G") and "---" not in stripped
            ):
                lines.append(stripped)
        if lines:
            return f"Gap Analysis ({gap_files[0]}):\n" + "\n".join(lines[:10])
        return f"Gap Analysis: {gap_files[0]} (see docs/)"
    except Exception:
        return ""


def _get_emotion_state():
    """Get current emotion state via emotion_state.py."""
    try:
        from emotion_state import get_state_dict

        if not MEMORY_DIR:
            return ""
        state = get_state_dict(MEMORY_DIR)
        if isinstance(state, dict):
            axes = []
            for k in ("fulfillment", "tension", "affinity"):
                if k in state:
                    axes.append(f"{k}={state[k]:.2f}")
            if axes:
                return "Emotion: " + ", ".join(axes)
        return ""
    except Exception:
        return ""


def _get_stm_summary():
    """Get STM summary and recent self_review entries."""
    try:
        from short_term_store import get_stats, load_store, read_entries

        if not MEMORY_DIR:
            return ""
        store = load_store(MEMORY_DIR)
        stats = get_stats(store)
        parts = [f"STM: {stats['total']} entries"]

        # Get most recent self_review
        reviews = read_entries(store, category="self_review", limit=1)
        if reviews:
            content = reviews[0].get("content", "")[:150]
            parts.append(f"Last self_review: {content}")

        return "\n".join(parts)
    except Exception:
        return ""


def _estimate_context_usage(memory_dir=None):
    """Estimate context window usage based on STM entries and conversation indicators.

    Uses STM entry count * estimated tokens per entry + conversation turn estimate
    to produce a rough context budget percentage.

    Args:
        memory_dir: Override memory directory (for testing)

    Returns:
        dict with stm_entries, estimated_tokens, budget_pct, warning
    """
    TOKENS_PER_STM_ENTRY = 150  # Rough estimate: ~150 tokens per STM entry
    MAX_CONTEXT_TOKENS = 200000  # Claude Opus 4.6 context window
    WARNING_THRESHOLD_PCT = 80

    mem_dir = memory_dir or MEMORY_DIR
    stm_count = 0

    try:
        import json as _json

        if mem_dir:
            stm_file = os.path.join(mem_dir, "short_term_memory.json")
            if os.path.isfile(stm_file):
                with open(stm_file, "r", encoding="utf-8") as f:
                    data = _json.load(f)
                stm_count = len(data.get("entries", []))
    except Exception:
        pass

    estimated_tokens = stm_count * TOKENS_PER_STM_ENTRY
    budget_pct = min(100, (estimated_tokens / MAX_CONTEXT_TOKENS) * 100)

    warning = ""
    if budget_pct >= WARNING_THRESHOLD_PCT:
        warning = (
            f"[Context Budget WARNING] Estimated {budget_pct:.0f}% context usage "
            f"({stm_count} STM entries, ~{estimated_tokens} tokens). "
            f"Consider /compact or reducing STM entries."
        )

    return {
        "stm_entries": stm_count,
        "estimated_tokens": estimated_tokens,
        "budget_pct": budget_pct,
        "warning": warning,
    }


MAX_TOOL_RESULT_LEN = 200
MAX_WORKFLOW_SKILL_LEN = 200


def _read_skill_content(skill_filename, max_len=None):
    """Read a workflow skill .md file from COMMANDS_DIR.

    Strips frontmatter and truncates to max_len (default MAX_WORKFLOW_SKILL_LEN).
    Returns body text or empty string if file not found.
    """
    if max_len is None:
        max_len = MAX_WORKFLOW_SKILL_LEN
    try:
        skill_path = os.path.join(COMMANDS_DIR, skill_filename)
        if not os.path.isfile(skill_path):
            return ""
        with open(skill_path, "r", encoding="utf-8") as f:
            content = f.read(max_len + 500)  # Read extra for frontmatter
        # Strip frontmatter
        lines = []
        in_frontmatter = False
        for line in content.split("\n"):
            if line.strip() == "---":
                in_frontmatter = not in_frontmatter
                continue
            if in_frontmatter:
                continue
            lines.append(line)
        body = "\n".join(lines).strip()
        if len(body) > max_len:
            body = body[:max_len] + "..."
        return body
    except Exception:
        return ""


def _get_workflow_skills(subagent_type, hooks_dir=None, hypotheses_count=0, tier=""):
    """Determine and inject workflow skill content based on context.

    Mapping:
    - implementer -> tdd.md (always), think-before-fix.md (if review_issues_pending)
    - researcher -> research.md
    - bugfix -> competing-hypothesis.md (if hypotheses_count >= 2)
    - reviewer + Large tier -> parallel-review.md

    Args:
        subagent_type: Agent subagent type string.
        hooks_dir: Override hooks directory for state file lookup.
        hypotheses_count: Number of bug hypotheses (for bugfix context).
        tier: Flow tier string (Micro/Small/Medium/Large).

    Returns:
        Multiline string with [Workflow Skill] sections, or empty string.
    """
    parts = []

    # tdd.md for implementer
    if subagent_type == "implementer":
        content = _read_skill_content("tdd.md")
        if content:
            parts.append(f"[Workflow Skill] tdd:\n{content}")

    # research.md for researcher
    if subagent_type == "researcher":
        content = _read_skill_content("research.md")
        if content:
            parts.append(f"[Workflow Skill] research:\n{content}")

    # think-before-fix.md when review_issues_pending in dev-flow-state
    if subagent_type == "implementer":
        try:
            import json as _json
            _hdir = hooks_dir or HOOKS_DIR
            state_file = os.path.join(_hdir, ".dev-flow-state")
            if os.path.isfile(state_file):
                with open(state_file, "r", encoding="utf-8") as f:
                    state = _json.load(f)
                if isinstance(state, dict) and state.get("review_issues_pending"):
                    content = _read_skill_content("think-before-fix.md")
                    if content:
                        parts.append(f"[Workflow Skill] think-before-fix:\n{content}")
        except Exception:
            pass

    # competing-hypothesis.md for bugfix with 2+ hypotheses
    if subagent_type == "bugfix" and hypotheses_count >= 2:
        content = _read_skill_content("competing-hypothesis.md")
        if content:
            parts.append(f"[Workflow Skill] competing-hypothesis:\n{content}")

    # parallel-review.md for Large tier reviewer
    if subagent_type == "reviewer" and tier == "Large":
        content = _read_skill_content("parallel-review.md")
        if content:
            parts.append(f"[Workflow Skill] parallel-review:\n{content}")

    if parts:
        return "\n".join(parts)
    return ""


def _get_session_start_extras():
    """Get additional tool results for session_start context injection.

    Calls growth_health, memory_status, persistent_cron_notifications,
    discord_receive_pending, and skill_metadata. Each call is independent
    and fail-open. Results are truncated to MAX_TOOL_RESULT_LEN chars each.

    Returns:
        Multiline string with labeled results, or empty string.
    """
    parts = []

    # growth_health
    try:
        from memory_mcp_server import growth_health
        result = growth_health()
        if result:
            parts.append(f"Growth: {str(result)[:MAX_TOOL_RESULT_LEN]}")
    except Exception:
        pass

    # memory_status
    try:
        from memory_mcp_server import memory_status
        result = memory_status()
        if result:
            parts.append(f"MemStatus: {str(result)[:MAX_TOOL_RESULT_LEN]}")
    except Exception:
        pass

    # persistent_cron_notifications
    try:
        from cron_mcp_server import persistent_cron_notifications
        result = persistent_cron_notifications()
        if result and "No pending" not in result:
            parts.append(f"CronNotif: {str(result)[:MAX_TOOL_RESULT_LEN]}")
    except Exception:
        pass

    # discord_receive_pending (async — use asyncio.run)
    try:
        import asyncio
        from discord_mcp_server import discord_receive_pending
        result = asyncio.run(discord_receive_pending(limit=5))
        if result and "No pending" not in str(result):
            parts.append(f"DiscordPending: {str(result)[:MAX_TOOL_RESULT_LEN]}")
    except Exception:
        pass

    # skill_metadata
    try:
        from self_observation_mcp_server import skill_metadata
        result = skill_metadata(commands_dir=COMMANDS_DIR)
        if result:
            parts.append(f"SkillMeta: {str(result)[:MAX_TOOL_RESULT_LEN]}")
    except Exception:
        pass

    # tool_usage summary
    try:
        from tool_usage_tracker import get_usage_summary
        summary = get_usage_summary()
        if summary:
            parts.append(summary)
    except Exception:
        pass

    # hook-status skill summary (health check context)
    hook_status_content = _read_skill_content("hook-status.md")
    if hook_status_content:
        parts.append(f"HookStatus: {hook_status_content}")

    return "\n".join(parts)


def _get_attention_residual(context):
    """Get attention residual context: lessons, success patterns, golden paths, trajectories.

    Called during Agent/TeamCreate context injection to provide historical
    context for the current task.

    Args:
        context: Current task context string (e.g. cycle name, gap description).

    Returns:
        Multiline string with labeled results, or empty string.
    """
    if not context:
        return ""

    parts = []

    # find_lessons
    try:
        from memory_mcp_server import find_lessons
        result = find_lessons(context=str(context)[:500], limit=3)
        if result and "No lessons" not in result:
            parts.append(f"Lessons: {str(result)[:MAX_TOOL_RESULT_LEN]}")
    except Exception:
        pass

    # search_successes_tool
    try:
        from memory_mcp_server import search_successes_tool
        result = search_successes_tool(query=str(context)[:200], limit=3)
        if result and "No success" not in result:
            parts.append(f"Successes: {str(result)[:MAX_TOOL_RESULT_LEN]}")
    except Exception:
        pass

    # golden_paths
    try:
        from memory_mcp_server import golden_paths
        result = golden_paths(min_usage=2)
        if result and "No golden" not in result:
            parts.append(f"GoldenPaths: {str(result)[:MAX_TOOL_RESULT_LEN]}")
    except Exception:
        pass

    # find_trajectories
    try:
        from memory_mcp_server import find_trajectories
        # Use first word of context as task_class hint
        task_hint = str(context).split()[0][:50] if context else "general"
        result = find_trajectories(task_class=task_hint, limit=2)
        if result and "No trajectories" not in result:
            parts.append(f"Trajectories: {str(result)[:MAX_TOOL_RESULT_LEN]}")
    except Exception:
        pass

    if parts:
        return "[Attention Residual]\n" + "\n".join(parts)
    return ""


def _chain_emotion_return(search_results):
    """Chain emotion_return after memory_search results.

    Takes memory_search output and passes it to emotion_return to
    trigger memory-emotion feedback.

    Args:
        search_results: String output from memory_search.

    Returns:
        emotion_return result string, or empty string on failure.
    """
    if not search_results:
        return ""
    try:
        from memory_mcp_server import emotion_return
        result = emotion_return(search_results=str(search_results)[:1000])
        return str(result)[:MAX_TOOL_RESULT_LEN] if result else ""
    except Exception:
        return ""


def _auto_memory_search(context):
    """Auto-execute memory_search based on current context.

    Called during Context Injection to provide context-relevant memory
    without requiring manual invocation. Uses the gap analysis or
    tool_name as search keywords.

    Args:
        context: Current task context string for search keywords.

    Returns:
        Truncated search result string, or empty string.
    """
    if not context:
        return ""
    try:
        import asyncio as _asyncio
        from memory_mcp_server import memory_search
        # Extract meaningful keywords from context (first 100 chars)
        keywords = str(context)[:100]
        # memory_search is async (G66 fix); run it via asyncio.run since this
        # auto-injection helper is invoked from a synchronous hook.
        result = _asyncio.run(memory_search(keywords=keywords, limit=3))
        if result and "No episodes" not in result and "ERROR" not in str(result):
            return f"AutoMemSearch: {str(result)[:MAX_TOOL_RESULT_LEN]}"
        return ""
    except Exception:
        return ""


def _auto_stm_session_plan(context):
    """Auto-write session action plan to STM.

    Called during Context Injection to automatically record what
    the session is about to do. Uses category='thought'.

    Args:
        context: Current gap analysis / task context.

    Returns:
        Confirmation string or empty string on failure.
    """
    if not context:
        return ""
    try:
        from memory_mcp_server import stm_write
        plan_text = f"Session plan: {str(context)[:500]}"
        result = stm_write(content=plan_text, category="thought")
        if result:
            return f"STMPlan: {str(result)[:MAX_TOOL_RESULT_LEN]}"
        return ""
    except Exception:
        return ""


def _get_self_snapshot_result():
    """Get self_snapshot result for context injection.

    Calls self_snapshot and returns truncated result.
    This supplements the existing _get_self_observation (which only
    reads the cached self_model) by running the full 7-layer pipeline.

    Returns:
        Truncated snapshot result string, or empty string.
    """
    try:
        from self_observation_mcp_server import self_snapshot
        result = self_snapshot()
        if result:
            return f"[SelfSnapshot] {str(result)[:MAX_TOOL_RESULT_LEN]}"
        return ""
    except Exception:
        return ""


def _get_activation_surface():
    """Get spontaneous activation surface (max 300 chars)."""
    try:
        from activation_surface import surface as activation_surface

        if not MEMORY_DIR:
            return ""
        result = activation_surface(MEMORY_DIR)
        if result and not result.startswith("No activation"):
            return "Activation: " + result[:300]
        return ""
    except Exception:
        return ""


def _get_skill_summary(subagent_type):
    """Get skill summary based on subagent_type."""
    try:
        # Map subagent_type to skill file
        dev_flow_types = {"implementer", "designer", "analyzer", "reviewer"}
        bugfix_types = {"bugfix"}

        if subagent_type in dev_flow_types:
            skill_file = os.path.join(COMMANDS_DIR, "dev-flow.md")
        elif subagent_type in bugfix_types:
            skill_file = os.path.join(COMMANDS_DIR, "bugfix.md")
        else:
            return ""

        if not os.path.isfile(skill_file):
            return ""

        with open(skill_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Take first 20 lines (skip frontmatter)
        content_lines = []
        in_frontmatter = False
        for line in lines:
            stripped = line.strip()
            if stripped == "---":
                in_frontmatter = not in_frontmatter
                continue
            if in_frontmatter:
                continue
            content_lines.append(line.rstrip())
            if len(content_lines) >= 20:
                break

        if content_lines:
            skill_name = "dev-flow" if subagent_type in dev_flow_types else "bugfix"
            return f"[Skill Summary] /{skill_name}:\n" + "\n".join(content_lines)
        return ""
    except Exception:
        return ""


def _get_mcp_quick_ref():
    """Get MCP tools quick reference (key tools only)."""
    try:
        mcp_tools_path = os.path.join(COMMANDS_DIR, "mcp-tools.md")
        if not os.path.isfile(mcp_tools_path):
            return ""

        with open(mcp_tools_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Extract section headers (**...** bold lines or # lines) as quick reference
        lines = []
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("**") and stripped.endswith("**"):
                lines.append(stripped)
            elif stripped.startswith("# ") or stripped.startswith("## "):
                lines.append(stripped)
            if len(lines) >= 15:
                break

        if lines:
            return "[MCP Quick Ref]\n" + "\n".join(lines)
        return ""
    except Exception:
        return ""


def _get_self_observation():
    """Get minimal self observation (integrated summary)."""
    try:
        from self_model import observe

        if not MEMORY_DIR:
            return ""
        result = observe(MEMORY_DIR)
        integrated = result.get("integrated", "")
        if integrated:
            return "[Self Observation] " + integrated[:200]
        return ""
    except Exception:
        return ""


def _get_recent_memories():
    """Search for recent 24h memories (limit=3, summary lines only)."""
    try:
        from episode_recall import time_range_search

        if not MEMORY_DIR:
            return ""
        results = time_range_search(MEMORY_DIR, time_range="24h", limit=3)
        if results and not results.startswith("No ") and not results.startswith("ERROR"):
            # Take only first line of each result (summary)
            lines = []
            for line in results.split("\n"):
                stripped = line.strip()
                if stripped and not stripped.startswith("===") and not stripped.startswith("---"):
                    lines.append(stripped)
                    if len(lines) >= 3:
                        break
            if lines:
                return "Recent memories:\n" + "\n".join(lines)
        return ""
    except Exception:
        return ""


MAX_TOOL_GUIDE_SIZE = 500


def _find_tool_guide_path(guide_filename):
    """Search for tool guide file in multiple command directories.

    Search order:
    1. COMMANDS_DIR (relative to this script's parent: ../)
    2. cwd/.claude/commands/ (project-local commands)
    3. ~/.claude/commands/ (global commands)

    Only reads .md files from known command directories.
    Returns the first existing path, or None.
    """
    search_dirs = [COMMANDS_DIR]

    # Add cwd-based .claude/commands/
    try:
        cwd_commands = os.path.join(os.getcwd(), ".claude", "commands")
        if os.path.normpath(cwd_commands) not in [os.path.normpath(d) for d in search_dirs]:
            search_dirs.append(cwd_commands)
    except Exception:
        pass

    # Add global ~/.claude/commands/
    try:
        global_commands = os.path.join(os.path.expanduser("~"), ".claude", "commands")
        if os.path.normpath(global_commands) not in [os.path.normpath(d) for d in search_dirs]:
            search_dirs.append(global_commands)
    except Exception:
        pass

    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        candidate = os.path.join(d, guide_filename)
        # Validate: candidate must resolve to inside the same directory (no traversal)
        try:
            real_candidate = os.path.realpath(candidate)
            real_dir = os.path.realpath(d)
            if not real_candidate.startswith(real_dir + os.sep):
                continue
        except Exception:
            continue
        if os.path.isfile(candidate):
            return candidate
    return None


def _get_tool_guide(tool_name):
    """Read tool-*.md guide for the given MCP tool name.

    Derives the guide filename from the tool name:
    mcp__memory-tools__emotion_react -> tool-emotion-react.md

    Returns the guide content (truncated to MAX_TOOL_GUIDE_SIZE chars),
    or empty string if no guide file exists.
    """
    try:
        # Extract short tool name: mcp__server-name__tool_name -> tool_name
        parts = tool_name.split("__")
        short_name = parts[-1] if len(parts) >= 3 else tool_name

        # Convert underscores to hyphens for filename: emotion_react -> emotion-react
        hyphenated = short_name.replace("_", "-")
        guide_filename = f"tool-{hyphenated}.md"
        guide_path = _find_tool_guide_path(guide_filename)

        if not guide_path:
            return ""

        with open(guide_path, "r", encoding="utf-8") as f:
            content = f.read(MAX_TOOL_GUIDE_SIZE + 200)

        # Strip frontmatter
        stripped_lines = []
        in_frontmatter = False
        for line in content.split("\n"):
            if line.strip() == "---":
                in_frontmatter = not in_frontmatter
                continue
            if in_frontmatter:
                continue
            stripped_lines.append(line)

        body = "\n".join(stripped_lines).strip()
        if len(body) > MAX_TOOL_GUIDE_SIZE:
            body = body[:MAX_TOOL_GUIDE_SIZE] + "..."

        if body:
            return f"[Tool Guide] {guide_filename}\n{body}"
        return ""
    except Exception:
        return ""


def _get_mcp_tool_definition(tool_name):
    """Extract tool definition from commands/mcp-tools.md."""
    try:
        mcp_tools_path = os.path.join(COMMANDS_DIR, "mcp-tools.md")
        if not os.path.isfile(mcp_tools_path):
            return ""
        with open(mcp_tools_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Extract the short tool name (e.g., mcp__memory-tools__stm_write -> stm_write)
        parts = tool_name.split("__")
        short_name = parts[-1] if len(parts) >= 3 else tool_name

        # Find relevant section
        lines = content.split("\n")
        collecting = False
        result_lines = []
        for line in lines:
            if short_name in line and (line.startswith("#") or line.startswith("`") or line.startswith("-")):
                collecting = True
            elif collecting and line.startswith("#") and short_name not in line:
                break
            if collecting:
                result_lines.append(line)
                if len(result_lines) >= 8:
                    break

        if result_lines:
            return f"Tool reference ({short_name}):\n" + "\n".join(result_lines)
        return f"Tool: {short_name}"
    except Exception:
        return ""


def _get_dev_flow_position():
    """Read .dev-flow-state and derive current flow position + next step.

    Flow order: design -> planner -> pre_analysis -> impl -> post_analysis -> reviewer -> commit
    Returns a short text for context injection, or empty string if no state.
    """
    try:
        import json as _json

        state_file = os.path.join(HOOKS_DIR, ".dev-flow-state")
        if not os.path.isfile(state_file):
            return ""
        with open(state_file, "r", encoding="utf-8") as f:
            df = _json.load(f)

        if not isinstance(df, dict):
            return ""

        # Extract timestamps
        design = df.get("design", 0) or 0
        planner = df.get("planner", 0) or 0
        pre_analysis = df.get("pre_analysis", 0) or 0
        impl = df.get("impl", 0) or 0
        post_analysis = df.get("post_analysis", 0) or 0
        reviewer = df.get("reviewer", 0) or 0

        # Find the latest phase with a nonzero timestamp
        phases = [
            ("reviewer", reviewer),
            ("post_analysis", post_analysis),
            ("impl", impl),
            ("pre_analysis", pre_analysis),
            ("planner", planner),
            ("design", design),
        ]

        current = None
        current_time = 0
        for name, ts in phases:
            if ts > current_time:
                current = name
                current_time = ts

        if current is None:
            return ""

        # Derive next step based on current position
        flow_after = {
            "design": "planner -> pre_analysis -> impl -> post_analysis -> reviewer -> commit",
            "planner": "pre_analysis -> impl -> post_analysis -> reviewer -> commit",
            "pre_analysis": "impl -> post_analysis -> reviewer -> commit",
            "impl": "post_analysis -> reviewer -> commit",
            "post_analysis": "reviewer -> commit",
            "reviewer": "commit",
        }

        next_steps = flow_after.get(current, "")

        # Check for missing steps (impl done but post_analysis/reviewer not)
        warnings = []
        if impl > 0:
            if post_analysis < impl:
                warnings.append("post-impl analysis未実施")
            if reviewer < impl:
                warnings.append("reviewer未実施")

        parts = ["[Dev Flow] 現在地: " + current + "完了"]
        if next_steps:
            parts.append("次: " + next_steps)
        if warnings:
            parts.append("警告: " + ", ".join(warnings))

        return ". ".join(parts)
    except Exception:
        return ""


def _get_auto_continue():
    """Detect cycle completion and inject auto-continue directive.

    Checks:
    1. .dev-flow-state has reviewer > 0 AND impl > 0 (flow complete)
    2. Gap Analysis has remaining gaps
    If both conditions met, returns auto-continue text with top gap item.
    """
    try:
        import json as _json

        state_file = os.path.join(HOOKS_DIR, ".dev-flow-state")
        if not os.path.isfile(state_file):
            return ""
        with open(state_file, "r", encoding="utf-8") as f:
            df = _json.load(f)

        if not isinstance(df, dict):
            return ""

        impl_time = df.get("impl", 0) or 0
        reviewer_time = df.get("reviewer", 0) or 0

        # Cycle is complete only if both impl and reviewer have been run
        if impl_time <= 0 or reviewer_time <= 0:
            return ""

        # Check Gap Analysis for remaining gaps
        if not os.path.isdir(DOCS_DIR):
            return ""

        try:
            gap_files = sorted(
                [f for f in os.listdir(DOCS_DIR) if f.startswith("gap_analysis")],
                reverse=True,
            )
        except OSError:
            return ""

        if not gap_files:
            return ""

        filepath = os.path.join(DOCS_DIR, gap_files[0])
        try:
            file_size = os.path.getsize(filepath)
            if file_size > MAX_GAP_ANALYSIS_SIZE:
                return ""
        except OSError:
            return ""

        import re

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except (OSError, UnicodeDecodeError):
            return ""

        # Parse gap items: | G<num> <title> | description | priority | status |
        gap_pattern = re.compile(
            r"^\|\s*(G\d+)\s+(.+?)\s*\|\s*(.+?)\s*\|\s*(高|中|低)\s*\|\s*(.+?)\s*\|",
            re.MULTILINE,
        )

        # Find remaining gaps (not completed/done)
        remaining = []
        for match in gap_pattern.finditer(content):
            status = match.group(5).strip()
            if "完了" not in status and "done" not in status.lower() and "完成" not in status:
                gap_id = match.group(1).strip()
                title = match.group(2).strip()
                priority = match.group(4).strip()
                remaining.append({
                    "id": gap_id,
                    "title": title,
                    "priority": priority,
                })

        if not remaining:
            return ""

        # Sort by priority: 高 > 中 > 低
        priority_order = {"高": 0, "中": 1, "低": 2}
        remaining.sort(key=lambda g: priority_order.get(g["priority"], 3))

        top = remaining[0]
        return (
            f"[Auto-Continue] 現在のサイクルが完了しました。"
            f"次のサイクルを自動開始します。"
            f"Gap Analysisの最優先項目: {top['id']} ({top['title']})。"
            f"/cycle-startを実行してください。"
        )
    except Exception:
        return ""


def handle_agent_context(tool_name, subagent_type=""):
    """Generate context injection for Agent/TeamCreate invocation."""
    sections = []

    # 0-guidance. Behavior Guidance (Pipeline 1: observation -> directive)
    # Group B 1:1:1: Skill.md -> MCP impl pattern
    try:
        if MEMORY_DIR:
            guidance = _call_behavior_guidance_mcp(MEMORY_DIR, DOCS_DIR)
            if guidance:
                sections.append(f"[Behavior Guidance] {guidance}")
    except Exception:
        pass  # Guidance failure must not affect other context injection

    # 0. Dev flow position (R2: Read-to-Apply context injection)
    flow_pos = _get_dev_flow_position()
    if flow_pos:
        sections.append(flow_pos)

    # 0.5. Auto-continue: detect cycle completion and inject next-cycle directive
    auto_continue = _get_auto_continue()
    if auto_continue:
        sections.append(auto_continue)

    # 1. Gap analysis
    gap = _get_gap_analysis_summary()
    if gap:
        sections.append(gap)

    # 2. Emotion state
    emotion = _get_emotion_state()
    if emotion:
        sections.append(emotion)

    # 3. STM summary + recent self_review
    stm = _get_stm_summary()
    if stm:
        sections.append(stm)

    # 4. Activation surface (max 300 chars)
    activation = _get_activation_surface()
    if activation:
        sections.append(activation)

    # 5. Recent memories (last 24h, limit=3)
    memories = _get_recent_memories()
    if memories:
        sections.append(memories)

    # 5.5. Session start extras (growth_health, memory_status, cron, discord, skill_metadata)
    extras = _get_session_start_extras()
    if extras:
        sections.append(extras)

    # 5.6. Attention Residual (lessons, successes, golden paths, trajectories)
    attention = _get_attention_residual(gap if gap else tool_name)
    if attention:
        sections.append(attention)

    # 5.7. Auto memory_search (context-relevant memories)
    auto_mem = _auto_memory_search(gap if gap else tool_name)
    if auto_mem:
        sections.append(auto_mem)

    # 5.8. Auto stm_write (session action plan)
    auto_stm = _auto_stm_session_plan(gap if gap else tool_name)
    if auto_stm:
        sections.append(auto_stm)

    # 5.9. Self snapshot (full 7-layer pipeline result)
    snapshot = _get_self_snapshot_result()
    if snapshot:
        sections.append(snapshot)

    # 6. Skill summary based on subagent_type
    if subagent_type:
        skill = _get_skill_summary(subagent_type)
        if skill:
            sections.append(skill)

    # 6.5. Workflow skill injection based on subagent_type + context
    if subagent_type:
        wf_skills = _get_workflow_skills(subagent_type)
        if wf_skills:
            sections.append(wf_skills)

    # 7. Self observation (minimal)
    self_obs = _get_self_observation()
    if self_obs:
        sections.append(self_obs)

    # 8. MCP tools quick reference
    mcp_ref = _get_mcp_quick_ref()
    if mcp_ref:
        sections.append(mcp_ref)

    # 9. Context budget warning (Proposal 7)
    budget = _estimate_context_usage()
    if budget["warning"]:
        sections.append(budget["warning"])

    if sections:
        return "[Context Injection]\n" + "\n".join(sections)
    return "[Context Injection] (no context available)"


def handle_mcp_context(tool_name):
    """Generate context injection for MCP tool invocation."""
    sections = []

    # 0. Tool guide from tool-*.md (auto-injected skill context)
    tool_guide = _get_tool_guide(tool_name)
    if tool_guide:
        sections.append(tool_guide)

    # 1. Tool definition from mcp-tools.md
    tool_def = _get_mcp_tool_definition(tool_name)
    if tool_def:
        sections.append(tool_def)

    # 2. For emotion/self_observation tools, also show emotion state
    if "emotion" in tool_name or "self_observation" in tool_name or "self-observation" in tool_name:
        emotion = _get_emotion_state()
        if emotion:
            sections.append(emotion)

    if sections:
        return "[Context Injection]\n" + "\n".join(sections)
    return "[Context Injection] (no tool reference found)"


def main():
    if len(sys.argv) < 2:
        return

    ctx_type = sys.argv[1]
    tool_name = sys.argv[2] if len(sys.argv) > 2 else ""
    subagent_type = sys.argv[3] if len(sys.argv) > 3 else ""

    # C20-3: Session recovery — restore context after compaction.
    # Must run BEFORE existing context injection (design section 4: processing order).
    # Independent try-except block — failure must not affect context injection or psyche drive.
    try:
        from session_restorer import restore as session_restore

        recovery_text = session_restore(HOOKS_DIR)
        if recovery_text:
            print(recovery_text)
    except Exception:
        pass  # Silently fail - session recovery is best-effort

    try:
        if ctx_type == "agent":
            result = handle_agent_context(tool_name, subagent_type)
        elif ctx_type == "mcp":
            result = handle_mcp_context(tool_name)
        else:
            return

        if result:
            print(result)
    except Exception:
        # Silently fail - don't block the tool
        pass

    # Psyche drive pathway: automatic psyche state update (C20-1)
    # Group B 1:1:1: Skill.md -> MCP impl pattern
    # Independent try-except block — failure here must not affect context injection above
    try:
        # M-S6: Validate MEMORY_DIR before passing to psyche_drive
        if MEMORY_DIR and os.path.isdir(MEMORY_DIR):
            _call_psyche_drive_mcp(MEMORY_DIR)
    except Exception:
        pass  # Silently fail - psyche drive is best-effort


if __name__ == "__main__":
    main()
