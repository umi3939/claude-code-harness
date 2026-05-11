#!/usr/bin/env python3
"""Skill metadata parser and validator (G38: SKILL.md Versioning & Metadata).

Parses version, compatibility, and dependency metadata from skill definition
frontmatter. Read-only: never modifies skill files or affects execution pipeline.

Usage:
    from skill_metadata import parse_skill_metadata, scan_all_skills
    meta = parse_skill_metadata("path/to/skill.md")
    result = scan_all_skills("path/to/commands/")
"""

import logging
import os
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Semantic version pattern: MAJOR.MINOR.PATCH
_SEMVER_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


@dataclass
class SkillMetadata:
    """Parsed metadata from a skill definition file."""

    name: str
    file_path: str
    description: str | None = None
    version: str | None = None
    requires: str | None = None
    depends_on: list[str] = field(default_factory=list)
    last_updated: str | None = None


def _parse_semver(version_str: str) -> tuple[int, int, int] | None:
    """Parse a semantic version string into (major, minor, patch) tuple.

    Returns None if the string is not a valid semver.
    """
    if not version_str:
        return None
    m = _SEMVER_PATTERN.match(version_str.strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _parse_frontmatter(content: str) -> dict:
    """Parse YAML-like frontmatter from markdown content.

    Uses simple line-by-line parsing to avoid external YAML dependency.
    Handles: string values, quoted strings, lists (both inline and multi-line).

    Returns empty dict on parse failure (fail-open: metadata skip, no crash).
    """
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}

    # Find closing ---
    end_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break

    if end_idx < 0:
        return {}

    result = {}
    current_key = None
    current_list = None

    try:
        for line in lines[1:end_idx]:
            stripped = line.strip()
            if not stripped:
                continue

            # Check for list continuation (starts with -)
            if stripped.startswith("- ") and current_key and current_list is not None:
                val = stripped[2:].strip().strip('"').strip("'")
                current_list.append(val)
                continue

            # Key-value pair
            colon_idx = stripped.find(":")
            if colon_idx < 0:
                continue

            key = stripped[:colon_idx].strip()
            value = stripped[colon_idx + 1:].strip()

            # Save previous list if any
            if current_key and current_list is not None:
                result[current_key] = current_list

            current_key = key
            current_list = None

            if not value:
                # Possible multi-line list follows
                current_list = []
                continue

            # Inline list: ["a", "b"]
            if value.startswith("[") and value.endswith("]"):
                inner = value[1:-1]
                if inner.strip():
                    items = [
                        item.strip().strip('"').strip("'")
                        for item in inner.split(",")
                        if item.strip()
                    ]
                    result[key] = items
                else:
                    result[key] = []
                current_key = None
                continue

            # Scalar value
            result[key] = value.strip('"').strip("'")
            current_key = None

        # Save final list if any
        if current_key and current_list is not None:
            result[current_key] = current_list

    except Exception as e:
        logger.warning("Frontmatter parse error (fail-open, returning partial): %s", e)

    return result


def parse_skill_metadata(file_path: str) -> SkillMetadata | None:
    """Parse metadata from a skill definition file.

    Args:
        file_path: Absolute path to the .md skill file.

    Returns:
        SkillMetadata dataclass, or None if file does not exist.
    """
    if not os.path.isfile(file_path):
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        logger.error("Failed to read skill file %s: %s", file_path, e)
        return None

    # Derive name from filename (without extension)
    name = os.path.splitext(os.path.basename(file_path))[0]

    fm = _parse_frontmatter(content)

    # Normalize depends_on to list
    depends_on_raw = fm.get("depends_on", [])
    if isinstance(depends_on_raw, str):
        depends_on = [depends_on_raw] if depends_on_raw else []
    elif isinstance(depends_on_raw, list):
        depends_on = depends_on_raw
    else:
        depends_on = []

    return SkillMetadata(
        name=name,
        file_path=file_path,
        description=fm.get("description"),
        version=fm.get("version"),
        requires=fm.get("requires"),
        depends_on=depends_on,
        last_updated=fm.get("last_updated"),
    )


def check_compatibility(
    version: str | None, requires: str | None
) -> dict:
    """Check if a version satisfies a minimum required version.

    Args:
        version: The current version string (semver).
        requires: The minimum required version string (semver).

    Returns:
        Dict with 'compatible' (bool) and optional 'reason' (str).
    """
    if requires is None:
        return {"compatible": True, "reason": "No minimum version specified"}

    if version is None:
        return {"compatible": True, "reason": "Version unmanaged (no version field)"}

    parsed_version = _parse_semver(version)
    parsed_requires = _parse_semver(requires)

    if parsed_version is None:
        return {
            "compatible": True,
            "reason": f"Invalid version format: '{version}' (treated as compatible)",
        }

    if parsed_requires is None:
        return {
            "compatible": True,
            "reason": f"Invalid requires format: '{requires}' (treated as compatible)",
        }

    if parsed_version >= parsed_requires:
        return {"compatible": True}
    else:
        return {
            "compatible": False,
            "reason": f"Version {version} < required {requires}",
        }


def detect_circular_dependencies(dep_map: dict[str, list[str]]) -> list[list[str]]:
    """Detect circular dependencies in a dependency graph.

    Args:
        dep_map: Mapping of skill name -> list of dependency names.

    Returns:
        List of cycles found (each cycle is a list of skill names).
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in dep_map}
    cycles = []

    def dfs(node, path):
        color[node] = GRAY
        path.append(node)

        for dep in dep_map.get(node, []):
            if dep not in color:
                # Unknown dependency: skip (no crash)
                continue
            if color[dep] == GRAY:
                # Found cycle: extract from dep's position in path
                cycle_start = path.index(dep)
                cycle = path[cycle_start:] + [dep]
                cycles.append(cycle)
            elif color[dep] == WHITE:
                dfs(dep, path)

        path.pop()
        color[node] = BLACK

    for node in dep_map:
        if color[node] == WHITE:
            dfs(node, [])

    return cycles


def scan_all_skills(directory: str) -> dict:
    """Scan a directory for all skill files and analyze metadata.

    Args:
        directory: Path to the skill definitions directory.

    Returns:
        Dict with 'skills' (list of SkillMetadata), 'cycles' (list of cycles),
        'unversioned' (list of skill names without version).
    """
    skills = []
    unversioned = []

    if not os.path.isdir(directory):
        logger.warning("Skill directory not found: %s", directory)
        return {"skills": [], "cycles": [], "unversioned": []}

    try:
        entries = sorted(os.listdir(directory))
    except OSError as e:
        logger.error("Failed to list skill directory %s: %s", directory, e)
        return {"skills": [], "cycles": [], "unversioned": []}

    for entry in entries:
        if not entry.endswith(".md"):
            continue
        file_path = os.path.join(directory, entry)
        meta = parse_skill_metadata(file_path)
        if meta is not None:
            skills.append(meta)
            if meta.version is None:
                unversioned.append(meta.name)

    # Build dependency map and detect cycles
    dep_map = {s.name: s.depends_on for s in skills}
    cycles = detect_circular_dependencies(dep_map)

    return {
        "skills": skills,
        "cycles": cycles,
        "unversioned": unversioned,
    }


def format_scan_result(scan_result: dict) -> str:
    """Format scan_all_skills result as human-readable text.

    Args:
        scan_result: Output of scan_all_skills().

    Returns:
        Formatted string.
    """
    skills = scan_result["skills"]
    cycles = scan_result["cycles"]
    unversioned = scan_result["unversioned"]

    lines = [f"=== Skill Metadata Report ({len(skills)} skills) ===\n"]

    # Skills table
    lines.append("## Skills")
    for s in skills:
        ver = s.version or "(unversioned)"
        req = f" requires>={s.requires}" if s.requires else ""
        deps = f" deps=[{', '.join(s.depends_on)}]" if s.depends_on else ""
        lines.append(f"  {s.name:30s} v{ver}{req}{deps}")

    lines.append("")

    # Cycles warning
    if cycles:
        lines.append(f"## WARNING: Circular Dependencies ({len(cycles)} cycles)")
        for cycle in cycles:
            lines.append(f"  {' -> '.join(cycle)}")
    else:
        lines.append("## No circular dependencies detected")

    lines.append("")

    # Unversioned
    if unversioned:
        lines.append(f"## Unversioned Skills ({len(unversioned)})")
        for name in unversioned:
            lines.append(f"  - {name}")
    else:
        lines.append("## All skills have version metadata")

    lines.append(f"\n## Summary")
    lines.append(f"  Total: {len(skills)}")
    lines.append(f"  Versioned: {len(skills) - len(unversioned)}")
    lines.append(f"  Unversioned: {len(unversioned)}")
    lines.append(f"  Circular deps: {len(cycles)}")

    return "\n".join(lines)
