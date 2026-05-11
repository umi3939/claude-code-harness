#!/usr/bin/env python3
"""Workflow Crystallizer (G40: Workflow Crystallizer).

Analyzes tool usage observation logs to detect repeated tool sequences
and propose them as skill candidates.

Read-only: never modifies observation logs or creates skill files.
Output is informational only (candidate proposals, not auto-creation).

Usage:
    from workflow_crystallizer import crystallize_workflows
    result = crystallize_workflows(obs_file_path, skills_dir)
"""

import collections
import json
import logging
import os

logger = logging.getLogger(__name__)

# Safety valves (from design doc)
DEFAULT_MIN_OCCURRENCES = 3
DEFAULT_MAX_CANDIDATES = 20
DEFAULT_LAST_N = 1000
DEFAULT_MIN_PATTERN_LEN = 2
DEFAULT_MAX_PATTERN_LEN = 5

# Self-exclusion: these tool names are filtered from pattern detection
# to prevent the crystallizer from detecting its own execution pattern
SELF_TOOL_NAMES = frozenset([
    "workflow_crystallize",
    "mcp__self-observation__workflow_crystallize",
])


def extract_tool_sequences(observations: list[dict]) -> list[str]:
    """Extract tool name sequence from observation records.

    Filters out self-referential tool names to prevent circular detection.

    Args:
        observations: List of observation dicts with 'tool' field.

    Returns:
        List of tool name strings in order.
    """
    tools = []
    for obs in observations:
        tool = obs.get("tool")
        if not tool:
            continue
        if tool in SELF_TOOL_NAMES:
            continue
        tools.append(tool)
    return tools


def find_repeated_patterns(
    tools: list[str],
    min_len: int = DEFAULT_MIN_PATTERN_LEN,
    max_len: int = DEFAULT_MAX_PATTERN_LEN,
    min_occurrences: int = DEFAULT_MIN_OCCURRENCES,
) -> list[dict]:
    """Find repeated subsequences in a tool sequence using sliding window.

    Args:
        tools: List of tool names in chronological order.
        min_len: Minimum pattern length to search for.
        max_len: Maximum pattern length to search for.
        min_occurrences: Minimum times a pattern must appear.

    Returns:
        List of dicts with 'pattern' (tuple) and 'count' (int),
        sorted by count descending.
    """
    if len(tools) < min_len:
        return []

    pattern_counts: dict[tuple, int] = collections.Counter()

    for window_size in range(min_len, min(max_len, len(tools)) + 1):
        for i in range(len(tools) - window_size + 1):
            pattern = tuple(tools[i:i + window_size])
            pattern_counts[pattern] += 1

    # Safety valve: limit Counter to top 100 entries to prevent unbounded growth
    COUNTER_LIMIT = 100
    results = []
    for pattern, count in pattern_counts.most_common(COUNTER_LIMIT):
        if count >= min_occurrences:
            results.append({"pattern": pattern, "count": count})

    results.sort(key=lambda x: (-x["count"], -len(x["pattern"])))
    return results


def _normalize_tool_name(name: str) -> str:
    """Normalize a tool name for comparison (lowercase, hyphens to underscores)."""
    return name.lower().replace("-", "_")


def filter_existing_skills(
    patterns: list[dict], skills_dir: str
) -> list[dict]:
    """Filter out patterns that are already covered by existing skills.

    A pattern is excluded if ALL tool names in the pattern match an existing
    skill file name (after normalization: lowercase, hyphens to underscores).

    Args:
        patterns: List of pattern dicts from find_repeated_patterns.
        skills_dir: Path to skill definitions directory.

    Returns:
        Filtered list of patterns.
    """
    if not patterns:
        return []

    existing_names = set()
    if os.path.isdir(skills_dir):
        try:
            for entry in os.listdir(skills_dir):
                if entry.endswith(".md"):
                    raw_name = os.path.splitext(entry)[0]
                    existing_names.add(_normalize_tool_name(raw_name))
        except OSError:
            logger.warning("Failed to list skills directory: %s", skills_dir)

    if not existing_names:
        return patterns

    filtered = []
    for p in patterns:
        normalized_tools = [_normalize_tool_name(t) for t in p["pattern"]]
        if all(t in existing_names for t in normalized_tools):
            continue
        filtered.append(p)
    return filtered


def _read_last_n_lines(file_path: str, n: int) -> list[str]:
    """Read last n lines from a file.

    Args:
        file_path: Path to the JSONL file.
        n: Number of lines to read from the end.

    Returns:
        List of line strings (last n lines).
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return lines[-n:] if len(lines) > n else lines
    except FileNotFoundError:
        return []
    except OSError as e:
        logger.warning("Failed to read %s: %s", file_path, e)
        return []


def crystallize_workflows(
    obs_file_path: str,
    skills_dir: str,
    last_n: int = DEFAULT_LAST_N,
    min_occurrences: int = DEFAULT_MIN_OCCURRENCES,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> dict:
    """Main entry point: analyze observations and propose skill candidates.

    Args:
        obs_file_path: Path to observations.jsonl.
        skills_dir: Path to skill definitions directory.
        last_n: Number of recent observations to analyze.
        min_occurrences: Minimum pattern occurrences to report.
        max_candidates: Maximum number of candidates to return.

    Returns:
        Dict with 'candidates', 'observations_analyzed', 'patterns_found'.
    """
    lines = _read_last_n_lines(obs_file_path, last_n)

    observations = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            observations.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not observations:
        return {"candidates": [], "observations_analyzed": 0, "patterns_found": 0}

    tools = extract_tool_sequences(observations)

    patterns = find_repeated_patterns(
        tools,
        min_len=DEFAULT_MIN_PATTERN_LEN,
        max_len=DEFAULT_MAX_PATTERN_LEN,
        min_occurrences=min_occurrences,
    )

    patterns = filter_existing_skills(patterns, skills_dir)

    candidates = []
    for p in patterns[:max_candidates]:
        candidates.append({
            "pattern": p["pattern"],
            "count": p["count"],
            "suggested_name": "-".join(t.lower().replace("_", "-") for t in p["pattern"]),
        })

    return {
        "candidates": candidates,
        "observations_analyzed": len(observations),
        "patterns_found": len(patterns),
    }


def format_crystallize_result(result: dict) -> str:
    """Format crystallize_workflows result as human-readable text."""
    candidates = result["candidates"]
    analyzed = result["observations_analyzed"]
    found = result["patterns_found"]

    lines = [f"=== Workflow Crystallizer ({analyzed} observations analyzed) ===\n"]

    if not candidates:
        lines.append("No repeated workflow patterns detected.")
        lines.append(f"(Patterns must appear {DEFAULT_MIN_OCCURRENCES}+ times to be reported)")
        return "\n".join(lines)

    lines.append(f"## Skill Candidates ({len(candidates)} of {found} patterns)")
    lines.append("")

    for i, c in enumerate(candidates, 1):
        pattern_str = " -> ".join(c["pattern"])
        lines.append(f"  {i}. {pattern_str}")
        lines.append(f"     Occurrences: {c['count']}")
        lines.append(f"     Suggested name: {c['suggested_name']}")
        lines.append("")

    lines.append("## Notes")
    lines.append("  - Candidates are proposals only; no skills are auto-created")
    lines.append("  - Review patterns before creating skills")
    lines.append("  - Higher occurrence count = stronger signal")

    return "\n".join(lines)
