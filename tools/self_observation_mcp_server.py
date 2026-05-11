#!/usr/bin/env python3
"""MCP server for self-observation and behavior analysis tools.

Split from memory_mcp_server.py to stay under Claude Code's deferred tools limit.
Contains: 14 tools (9 observation pipeline + self_snapshot + 2 behavior analysis + 2 hook infra)

IMPORTANT: For stdio transport, never print() to stdout.
Use print(..., file=sys.stderr) for debug logging.
"""

import io
import json
import os
import re
import sys
import time

# Ensure UTF-8 stderr on Windows
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace"
    )

# Add the tools directory to sys.path so we can import modules
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from file_io import resolve_project_root  # noqa: E402

_PROJECT_ROOT = resolve_project_root()

from continuity_strain import evaluate_strain as strain_evaluate
from emotion_dynamics import load_dynamics_state

# Import emotion/dynamics state (needed by long_term_record and self_snapshot)
from emotion_state import load_state
from identity_coherence import assess_coherence as coherence_assess
from long_term_dynamics import (
    format_stats as lt_format_stats,
)
from long_term_dynamics import (
    get_long_term_stats as lt_get_stats,
)
from long_term_dynamics import (
    record_observation as lt_record_observation,
)
from mcp.server.fastmcp import FastMCP
from self_image_integration import integrate_self_image as self_image_integrate

# Import self-observation modules
from self_model import observe as self_model_observe
from stability_valve import (
    check_stability as stability_check_fn,
)
from temporal_self_difference import compute_difference as self_diff_compute
from tone_modulation import compute_tone as tone_compute

# Default memory directory (overridable via MEMORY_DIR env var)
# Resolved at import time via resolve_memory_dir(). In test environments
# where resolution may fail, falls back to None — tests monkeypatch
# DEFAULT_MEMORY_DIR before calling any tool functions.
try:
    from file_io import resolve_memory_dir as _resolve_memory_dir
    DEFAULT_MEMORY_DIR = _resolve_memory_dir()
except Exception:
    DEFAULT_MEMORY_DIR = None  # type: ignore[assignment]

# Initialize MCP server
mcp = FastMCP("self-observation")


# --- Self-Observation Pipeline (9 tools) ---


@mcp.tool()
def self_observe() -> str:
    """Observe current internal state as an integrated snapshot.

    READ-ONLY observation of emotion, dynamics, change history, and memory.
    Returns abstract descriptions without raw numbers.
    No parameters needed - automatically gathers all information.
    """
    memory_dir = DEFAULT_MEMORY_DIR
    try:
        result = self_model_observe(memory_dir)

        lines = []
        # Section 1: Emotion
        emo = result.get("emotion", {})
        lines.append("=== Emotion ===")
        lines.append(emo.get("description", ""))
        lines.append("")

        # Section 2: Changes
        chg = result.get("change", {})
        lines.append("=== Changes ===")
        lines.append(chg.get("description", ""))
        lines.append("")

        # Section 3: Memory
        mem = result.get("memory", {})
        lines.append("=== Memory ===")
        lines.append(mem.get("description", ""))
        lines.append("")

        # Section 4: Integrated
        lines.append("=== Integrated ===")
        lines.append(result.get("integrated", ""))

        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def self_difference() -> str:
    """Observe how internal state has changed compared to previous observation.

    Takes a snapshot of current state, compares with the most recent past snapshot,
    and returns abstract descriptions of the differences.
    No parameters needed.
    """
    memory_dir = DEFAULT_MEMORY_DIR
    try:
        result = self_diff_compute(memory_dir)

        lines = []
        lines.append("=== Self-Difference ===")
        lines.append(f"Change detected: {'yes' if result['has_difference'] else 'no'}")
        lines.append(f"Magnitude: {result['magnitude']}")
        lines.append(f"Nature: {result['nature']}")
        lines.append("")

        # Component details
        lines.append("Components:")
        for comp_name, comp_data in result["components"].items():
            ct = comp_data["change_type"]
            if ct != "unchanged":
                lines.append(f"  {comp_name}: {ct} ({comp_data['from']} -> {comp_data['to']})")
            else:
                lines.append(f"  {comp_name}: unchanged")
        lines.append("")

        # Integrated description
        lines.append(result["integrated_description"])

        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def continuity_strain() -> str:
    """Observe self-continuity strain from persistent self-differences.

    Calls self_difference internally, then evaluates whether the difference
    has been persistent enough to generate a sense of discontinuity.
    No parameters needed.
    """
    memory_dir = DEFAULT_MEMORY_DIR
    try:
        result = strain_evaluate(memory_dir)

        lines = []
        lines.append("=== Continuity Strain ===")
        lines.append(f"Strain present: {'yes' if result['strain_present'] else 'no'}")
        lines.append(f"Level: {result['level']}")
        lines.append(f"Persistence: {result['persistence']}")
        lines.append(f"Trend: {result['trend']}")
        lines.append("")
        lines.append(result["description"])
        lines.append("")

        # Include self-difference summary
        sd = result.get("self_difference", {})
        lines.append("--- Self-Difference (this observation) ---")
        lines.append(f"Change detected: {'yes' if sd.get('has_difference') else 'no'}")
        lines.append(f"Magnitude: {sd.get('magnitude', 'unknown')}")
        lines.append(f"Nature: {sd.get('nature', 'unknown')}")
        if sd.get("integrated_description"):
            lines.append(sd["integrated_description"])

        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def self_image() -> str:
    """Generate a provisional self-image by integrating observation systems.

    Combines self_observe, self_difference, and continuity_strain into
    a unified, temporary image of "how the current self appears to be".

    This is READ-ONLY introspection. The image is:
    - Always provisional (never fixed or saved)
    - Never used for decisions
    - Described in tentative language only
    - Regenerated fresh each call

    No parameters needed.
    """
    memory_dir = DEFAULT_MEMORY_DIR
    try:
        result = self_image_integrate(memory_dir)

        lines = []
        lines.append("=== Provisional Self-Image ===")
        lines.append(f"Overall Impression: {result['overall_impression']}")
        lines.append(f"Emotional Tone: {result['emotional_tone']}")
        lines.append(f"Tendency Hint: {result['tendency_hint']}")
        lines.append(f"Stability Feeling: {result['stability_feeling']}")
        lines.append(f"Change Presence: {result['change_presence']}")
        lines.append(f"Continuity Feeling: {result['continuity_feeling']}")
        lines.append(f"Complete: {result['is_complete']}")
        lines.append("")

        if result["contradictions"]:
            lines.append("Tensions (coexisting):")
            for c in result["contradictions"]:
                lines.append(f"  - {c}")
            lines.append("")

        lines.append(result["integrated_description"])

        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def identity_coherence() -> str:
    """Assess identity coherence by detecting overlap of shift signals.

    Observes four sources of internal shift:
    - temporal_difference: has the self-state changed significantly?
    - continuity_strain: has change persisted enough to feel like strain?
    - self_image_flux: is the self-image stability wavering?
    - emotional_turbulence: is the emotional tone stirred/mixed/intense?

    When multiple shift signals overlap, coherence decreases:
    - 0 active: stable (self feels continuous)
    - 1 active: slightly_shifting (something feels a bit off)
    - 2 active: unsettled (hard to grasp sense of self)
    - 3-4 active: disconnected (feels distant from earlier self)

    Completely stateless (no persistence, no history).
    READ-ONLY (does not modify any other system).
    For introspection/awareness only; NO impact on decisions.

    No parameters needed.
    """
    memory_dir = DEFAULT_MEMORY_DIR
    try:
        result = coherence_assess(memory_dir)

        lines = []
        lines.append("=== Identity Coherence ===")
        lines.append(f"Coherence Level: {result['coherence_level']}")
        lines.append(f"Overlap Intensity: {result['overlap_intensity']}")
        lines.append("")

        if result["shift_sources"]:
            lines.append("Active Shift Sources:")
            for source in result["shift_sources"]:
                lines.append(f"  - {source}")
            lines.append("")

        lines.append(result["description"])

        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def stability_check() -> str:
    """Check current stability valve status (extremity indicators and dampening).

    Observes three extremity indicators:
    - emotion_saturation: any emotion axis at extreme values (abs >= 0.8)
    - change_fixation: recent changes all pushing same axis same direction
    - dynamics_stagnation: PEAK or REBOUND phase exceeding expected duration

    Returns a dampening_factor (0.3-1.0) that is automatically applied to
    emotion_react's amplitude. 1.0 = no dampening, lower = more suppression.

    This is READ-ONLY observation. No state is modified.
    No parameters needed.
    """
    memory_dir = DEFAULT_MEMORY_DIR
    try:
        result = stability_check_fn(memory_dir)

        lines = []
        lines.append("=== Stability Valve ===")
        lines.append(f"Active: {'yes' if result['is_active'] else 'no'}")
        lines.append(f"Dampening Factor: {result['dampening_factor']:.2f}")
        lines.append(f"Overall Extremity: {result['overall_extremity']:.2f}")
        lines.append("")
        lines.append("Indicators:")
        for name, val in result["indicators"].items():
            lines.append(f"  {name}: {val:.4f}")
        lines.append("")
        lines.append(result["description"])

        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def long_term_record() -> str:
    """Record a long-term dynamics observation of the current emotion state.

    Takes a snapshot of the current emotion state and dynamics phase,
    adds it to an observation buffer. When the buffer reaches the window
    size (10 observations), automatically aggregates into a long-term entry.

    This is PASSIVE observation -- it never changes emotion state.
    Called automatically after emotion_react, but can also be called manually.

    No parameters needed -- reads current state automatically.
    """
    memory_dir = DEFAULT_MEMORY_DIR
    try:
        # Read current emotion state
        emotion_state = load_state(memory_dir)

        # Read current dynamics phase
        dynamics_state = load_dynamics_state(memory_dir)
        phase = dynamics_state.get("phase", "normal")

        result = lt_record_observation(
            memory_dir,
            emotion_state=emotion_state,
            dynamics_phase=phase,
        )

        status = result["status"]
        buf_size = result["buffer_size"]

        if status == "aggregated":
            entry = result["entry"]
            obs_count = entry.get("observation_count", 0)
            entry_id = entry.get("entry_id", 0)
            return (
                f"Long-term observation recorded and aggregated into entry #{entry_id} "
                f"({obs_count} observations).\nBuffer reset to 0."
            )
        else:
            return f"Long-term observation buffered ({buf_size}/10)."
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def long_term_stats(last_n: int = 10) -> str:
    """Get long-term emotion dynamics statistics.

    Returns aggregated statistics over recent observation windows:
    - Per-axis averages and variance trends
    - Dynamics phase distribution (normal/peak/rebound)
    - Change frequency
    - Trend direction (rising/falling/stable) per axis

    This is READ-ONLY -- no state is modified.

    Args:
        last_n: Number of recent entries to analyze (default 10)
    """
    memory_dir = DEFAULT_MEMORY_DIR
    try:
        stats = lt_get_stats(memory_dir, last_n=last_n)
        return lt_format_stats(stats)
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def tone_check() -> str:
    """Check recommended response tone based on current emotion state.

    Computes tone bias from the current 3-axis emotion state
    (fulfillment/tension/affinity) and dynamics phase (NORMAL/PEAK/REBOUND).

    Returns the recommended primary tone and weights for all 5 tones:
    - neutral: balanced, default tone
    - light: playful, lighthearted
    - serious: careful, thoughtful
    - warm: gentle, caring
    - reserved: minimal, restrained

    This is completely stateless and read-only. It does not modify
    emotion state or persist anything.

    No parameters needed.
    """
    memory_dir = DEFAULT_MEMORY_DIR
    try:
        result = tone_compute(memory_dir)

        lines = []
        lines.append("=== Tone Check ===")
        lines.append(f"Primary Tone: {result['primary_tone']}")
        lines.append("")
        lines.append("Weights:")
        for tone, weight in sorted(result["tone_weights"].items(), key=lambda x: -x[1]):
            bar = "#" * int(weight * 20)
            lines.append(f"  {tone:10s}: {weight:.3f} {bar}")
        lines.append("")
        lines.append(result["description"])

        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: {e}"


# --- Self Snapshot (Unified Observation) ---


@mcp.tool()
def self_snapshot() -> str:
    """Run the full self-observation pipeline in one call.

    Executes all 7 observation layers in pipeline order and returns
    a unified view of the current internal state:

    1. self_observe - emotion/change/memory snapshot
    2. self_difference - temporal change detection
    3. continuity_strain - persistent difference tracking
    4. self_image - provisional self-image integration
    5. identity_coherence - shift signal overlap
    6. stability_check - extremity monitoring
    7. tone_check - recommended response tone

    All layers are READ-ONLY. No state is modified except:
    - self_difference adds a snapshot to its FIFO history
    - continuity_strain updates its observation history

    No parameters needed.
    """
    memory_dir = DEFAULT_MEMORY_DIR
    sections = []

    # 1. Self Observe
    try:
        obs = self_model_observe(memory_dir)
        sections.append(f"[self_observe] {obs['integrated']}")
    except Exception as e:
        sections.append(f"[self_observe] ERROR: {e}")

    # 2. Self Difference
    try:
        diff = self_diff_compute(memory_dir)
        sections.append(f"[self_difference] magnitude={diff['magnitude']}, nature={diff['nature']} — {diff['integrated_description']}")
    except Exception as e:
        sections.append(f"[self_difference] ERROR: {e}")

    # 3. Continuity Strain
    try:
        strain = strain_evaluate(memory_dir)
        sections.append(f"[continuity_strain] level={strain['level']}, persistence={strain['persistence']}, trend={strain['trend']} — {strain['description']}")
    except Exception as e:
        sections.append(f"[continuity_strain] ERROR: {e}")

    # 4. Self Image
    try:
        img = self_image_integrate(memory_dir)
        sections.append(f"[self_image] tone={img['emotional_tone']}, stability={img['stability_feeling']}, change={img['change_presence']}, continuity={img['continuity_feeling']}, overall={img['overall_impression']}")
        if img['contradictions']:
            sections.append(f"  contradictions: {'; '.join(img['contradictions'])}")
        sections.append(f"  {img['integrated_description']}")
    except Exception as e:
        sections.append(f"[self_image] ERROR: {e}")

    # 5. Identity Coherence
    try:
        coh = coherence_assess(memory_dir)
        sources_str = ", ".join(coh['shift_sources']) if coh['shift_sources'] else "none"
        sections.append(f"[identity_coherence] level={coh['coherence_level']}, intensity={coh['overlap_intensity']}, sources=[{sources_str}] — {coh['description']}")
    except Exception as e:
        sections.append(f"[identity_coherence] ERROR: {e}")

    # 6. Stability Check
    try:
        stab = stability_check_fn(memory_dir)
        sections.append(f"[stability_check] active={stab['is_active']}, dampening={stab['dampening_factor']} — {stab['description']}")
    except Exception as e:
        sections.append(f"[stability_check] ERROR: {e}")

    # 7. Tone Check
    try:
        tone = tone_compute(memory_dir)
        top3 = sorted(tone['tone_weights'].items(), key=lambda x: -x[1])[:3]
        top3_str = ", ".join(f"{t}={w:.2f}" for t, w in top3)
        sections.append(f"[tone_check] primary={tone['primary_tone']} ({top3_str}) — {tone['description']}")
    except Exception as e:
        sections.append(f"[tone_check] ERROR: {e}")

    return "\n".join(sections)


# --- Behavior Analysis Tools ---


@mcp.tool()
def behavior_analyze(
    last_n: int = 200,
    tool_filter: str = "",
) -> str:
    """Analyze observation logs to detect behavioral patterns and suggest new rules.

    Reads observations.jsonl (auto-recorded by PostToolUse hook) and reports:
    - Tool usage frequency
    - File access patterns (most touched files)
    - Tool sequences (repeated patterns)
    - Time clustering (burst activity)
    - Suggested rules for behavior-rules.json

    Args:
        last_n: Number of recent observations to analyze (default 200)
        tool_filter: Optional tool name filter (e.g. "Write" to analyze only Write calls)
    """
    import collections
    from datetime import datetime

    data_dir = os.path.join(_PROJECT_ROOT, "data")
    obs_file = os.path.join(data_dir, "observations.jsonl")

    if not os.path.exists(obs_file):
        return "No observations.jsonl found. The observation-logger hook may not have run yet."

    # Load observations
    observations = []
    try:
        with open(obs_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        observations.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except Exception as e:
        return f"Error reading observations: {e}"

    if not observations:
        return "observations.jsonl is empty."

    # Take last_n
    observations = observations[-last_n:]

    # Apply filter
    if tool_filter:
        observations = [o for o in observations if o.get("tool") == tool_filter]
        if not observations:
            return f"No observations found for tool '{tool_filter}'."

    parts = [f"=== Behavior Analysis ({len(observations)} observations) ===\n"]

    # 1. Tool frequency
    tool_counts = collections.Counter(o.get("tool", "?") for o in observations)
    parts.append("## Tool Usage Frequency")
    for tool, count in tool_counts.most_common(15):
        bar = "#" * min(count, 30)
        parts.append(f"  {tool:20s} {count:4d} {bar}")
    parts.append("")

    # 2. File access patterns
    file_counts = collections.Counter()
    for o in observations:
        f = o.get("params", {}).get("file", "")
        if f:
            file_counts[f] += 1
    if file_counts:
        parts.append("## Most Accessed Files")
        for fp, count in file_counts.most_common(10):
            # Shorten path
            home = os.path.expanduser("~")
            short = fp.replace(home + "\\", "~/").replace(home + "/", "~/").replace(home.replace("\\", "/") + "/", "~/")
            parts.append(f"  {short:60s} {count:4d}")
        parts.append("")

    # 3. Tool sequences (bigrams)
    if len(observations) >= 2:
        bigrams = collections.Counter()
        for i in range(len(observations) - 1):
            a = observations[i].get("tool", "?")
            b = observations[i + 1].get("tool", "?")
            bigrams[(a, b)] += 1
        parts.append("## Tool Sequences (most common pairs)")
        for (a, b), count in bigrams.most_common(10):
            parts.append(f"  {a} → {b}: {count}")
        parts.append("")

    # 4. Burst detection (>5 calls within 60 seconds)
    bursts = []
    if len(observations) >= 5:
        for i in range(len(observations) - 4):
            try:
                t0 = datetime.fromisoformat(observations[i]["ts"].replace("Z", "+00:00"))
                t4 = datetime.fromisoformat(observations[i + 4]["ts"].replace("Z", "+00:00"))
                delta = (t4 - t0).total_seconds()
                if delta < 60:
                    tools_in_burst = [observations[i + j].get("tool", "?") for j in range(5)]
                    bursts.append({
                        "time": observations[i]["ts"],
                        "tools": tools_in_burst,
                        "seconds": round(delta, 1),
                    })
            except (KeyError, ValueError):
                continue
    if bursts:
        parts.append(f"## Burst Activity ({len(bursts)} detected)")
        for b in bursts[:5]:
            parts.append(f"  {b['time']}: {' → '.join(b['tools'])} ({b['seconds']}s)")
        parts.append("")

    # 5. Session distribution
    sessions = collections.Counter(o.get("sid", "?") for o in observations)
    parts.append(f"## Sessions: {len(sessions)} unique")
    parts.append("")

    # 6. Pattern-based suggestions
    suggestions = []
    # Detect Write-heavy sessions
    write_count = tool_counts.get("Write", 0)
    total = len(observations)
    if total > 10 and write_count / total > 0.3:
        suggestions.append(
            f"Write calls are {write_count}/{total} ({write_count*100//total}%) of all calls. "
            "Consider: is this creation-heavy work justified, or is it tool mass-production?"
        )
    # Detect repeated file edits
    for fp, count in file_counts.most_common(3):
        if count > 10:
            home = os.path.expanduser("~")
            short = fp.replace(home + "\\", "~/").replace(home + "/", "~/").replace(home.replace("\\", "/") + "/", "~/")
            suggestions.append(
                f"File '{short}' accessed {count} times. Repeated edits may indicate "
                "incremental fixing instead of thinking through changes upfront."
            )
    # Detect Read-without-action
    read_count = tool_counts.get("Read", 0)
    edit_count = tool_counts.get("Edit", 0) + tool_counts.get("Write", 0)
    if read_count > 20 and edit_count < read_count * 0.1:
        suggestions.append(
            f"Read {read_count} vs Edit/Write {edit_count}. "
            "Heavy reading with little action — are you going in circles?"
        )
    # Detect bash failures
    bash_fails = [o for o in observations
                  if o.get("tool") == "Bash" and o.get("params", {}).get("exit")]
    if len(bash_fails) > 3:
        fail_cmds = [o["params"].get("cmd", "?") for o in bash_fails]
        suggestions.append(
            f"{len(bash_fails)} failed Bash commands detected. "
            f"Recent failures: {', '.join(fail_cmds[-3:])}"
        )

    if suggestions:
        parts.append("## Suggested Observations")
        for s in suggestions:
            parts.append(f"  - {s}")
    else:
        parts.append("## No anomalous patterns detected")

    return "\n".join(parts)


@mcp.tool()
def behavior_evolve() -> str:
    """Compare lessons registry against behavior rules to show coverage.

    Does NOT judge hookability — that requires human/LLM understanding of lesson content.
    Simply shows which lessons have corresponding behavior-rules.json entries
    and which don't.
    """
    memory_dir = DEFAULT_MEMORY_DIR
    hooks_dir = os.path.join(_PROJECT_ROOT, "hooks")
    rules_file = os.path.join(hooks_dir, "behavior-rules.json")

    # Load lessons — search multiple candidate paths
    lessons_candidates = [
        os.path.join(_PROJECT_ROOT, "growth", "lessons_registry.md"),
        os.path.join(os.getcwd(), "growth", "lessons_registry.md"),
        os.path.join(memory_dir, "lessons_registry.md") if memory_dir else None,
    ]
    lessons_path = None
    for candidate in lessons_candidates:
        if candidate and os.path.exists(candidate):
            lessons_path = candidate
            break
    if lessons_path is None:
        home_dir = os.path.expanduser("~")
        safe_root = _PROJECT_ROOT.replace(home_dir, "~") if _PROJECT_ROOT else "None"
        safe_cwd = os.getcwd().replace(home_dir, "~")
        safe_memory = str(memory_dir).replace(home_dir, "~") if memory_dir else "None"
        return (
            f"No lessons_registry.md found. "
            f"Searched: _PROJECT_ROOT={safe_root}, cwd={safe_cwd}, "
            f"memory_dir={safe_memory}"
        )

    lessons = []
    try:
        with open(lessons_path, "r", encoding="utf-8") as f:
            content = f.read()
        current_num = 0
        current_summary = ""
        in_action = False
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("## Lesson:"):
                # Save previous lesson if exists
                if current_num > 0 and current_summary:
                    lessons.append({"number": current_num, "summary": current_summary})
                current_num += 1
                current_summary = ""
                in_action = False
            elif stripped == "### Action":
                in_action = True
            elif stripped.startswith("### ") and stripped != "### Action":
                # Reached next section (Why/Fix/Lesson/etc.), stop collecting action
                in_action = False
            elif in_action and stripped:
                # First non-empty line after ### Action is the summary
                if not current_summary:
                    current_summary = stripped
        # Save last lesson
        if current_num > 0 and current_summary:
            lessons.append({"number": current_num, "summary": current_summary})
    except Exception as e:
        return f"Error parsing lessons: {e}"

    if not lessons:
        return "No lessons found in registry."

    # Load rules
    rules = []
    try:
        with open(rules_file, "r", encoding="utf-8") as f:
            rules_data = json.load(f)
            rules = rules_data.get("rules", [])
    except Exception:
        pass

    # Extract lesson references from rules
    rule_lessons = set()
    for rule in rules:
        lesson_ref = rule.get("lesson", "")
        # Extract "#N" patterns
        refs = re.findall(r"#(\d+)", lesson_ref)
        for r in refs:
            rule_lessons.add(f"#{r}")

    # Compare
    covered = []
    uncovered = []
    parts = [f"=== Lesson-Rule Coverage ({len(lessons)} lessons, {len(rules)} rules) ===\n"]

    for lesson in lessons:
        num = lesson["number"]
        summary = lesson["summary"][:60]
        ref = f"#{num}"
        if ref in rule_lessons:
            covered.append(lesson)
            parts.append(f"  [COVERED] #{num}: {summary}")
        else:
            uncovered.append(lesson)
            parts.append(f"  [NO RULE] #{num}: {summary}")

    parts.append("\n## Summary")
    parts.append(f"  Covered by rules: {len(covered)}/{len(lessons)}")
    parts.append(f"  No rule: {len(uncovered)}/{len(lessons)}")
    parts.append("\nNote: Not all lessons are hookable. Some are mindset-based and")
    parts.append("handled by SessionStart reminders instead of PreToolUse hooks.")

    return "\n".join(parts)


# --- Path Validation ---


# Allowed base directories for path arguments from external/untrusted sources
_ALLOWED_PATH_ROOTS = [
    _PROJECT_ROOT,
    os.path.join(os.path.expanduser("~"), ".claude"),
]


def _validate_path(path: str, allowed_dirs: list[str] | None = None) -> str:
    """Validate and resolve a path, preventing path traversal.

    Args:
        path: The path to validate.
        allowed_dirs: List of allowed base directories. Defaults to _ALLOWED_PATH_ROOTS.

    Returns:
        The resolved absolute path.

    Raises:
        ValueError: If path is empty or resolves outside allowed directories.
    """
    if not path or not path.strip():
        raise ValueError("Path must not be empty")

    if allowed_dirs is None:
        allowed_dirs = _ALLOWED_PATH_ROOTS

    resolved = os.path.realpath(path)

    for allowed in allowed_dirs:
        allowed_resolved = os.path.realpath(allowed)
        # Normalize for comparison (handle Windows drive letter case)
        resolved_norm = resolved.replace("\\", "/").rstrip("/")
        allowed_norm = allowed_resolved.replace("\\", "/").rstrip("/")
        if resolved_norm == allowed_norm or resolved_norm.startswith(allowed_norm + "/"):
            return resolved

    raise ValueError(
        f"Path '{path}' resolves to '{resolved}' which is outside allowed directories"
    )


# --- Hook Infrastructure Tools (Group A 1:1:1 compliance) ---


# Import existing hook logic (no new logic — just wrapping)
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "hooks"))
from msg_hook_health_check import (
    DEFAULT_HOOK_CONFIG_PATH as _HC_DEFAULT_CONFIG,
)
from msg_hook_health_check import (
    DEFAULT_LOG_PATH as _HC_DEFAULT_LOG,
)
from msg_hook_health_check import (
    detect_auto_disabled,
    format_health_summary,
    load_hook_config,
    scan_recent_failures,
)
from sync_to_global import (
    DEFAULT_GLOBAL_HOOKS_DIR as _SYNC_DEFAULT_GLOBAL,
)
from sync_to_global import (
    sync_hooks_to_global as _sync_hooks_fn,
)

_SYNC_DEFAULT_PROJECT = os.path.join(_PROJECT_ROOT, "hooks")


def _hook_health_check_impl(
    config_path: str = "",
    log_path: str = "",
) -> str:
    """Implementation for hook_health_check MCP tool.

    Wraps existing msg_hook_health_check functions.
    Validates paths from external sources.
    """
    try:
        # Resolve paths with traversal protection
        if config_path and config_path.strip():
            config_path = _validate_path(config_path)
        else:
            config_path = _HC_DEFAULT_CONFIG

        if log_path and log_path.strip():
            log_path = _validate_path(log_path)
        else:
            log_path = _HC_DEFAULT_LOG

        hooks = load_hook_config(config_path)
        failure_counts = scan_recent_failures(log_path)
        auto_disabled = detect_auto_disabled(log_path)
        return format_health_summary(hooks, failure_counts, auto_disabled)
    except ValueError as e:
        return f"ERROR: {e}"
    except Exception as e:
        print(f"hook_health_check error: {e}", file=sys.stderr)
        return f"ERROR: {e}"


def _sync_hooks_to_global_impl(
    project_hooks_dir: str = "",
    global_hooks_dir: str = "",
) -> str:
    """Implementation for sync_hooks_to_global MCP tool.

    Wraps existing sync_to_global.sync_hooks_to_global function.
    Validates paths from external sources.
    """
    try:
        # Resolve paths with traversal protection
        if project_hooks_dir and project_hooks_dir.strip():
            project_hooks_dir = _validate_path(project_hooks_dir)
        else:
            project_hooks_dir = _SYNC_DEFAULT_PROJECT

        if global_hooks_dir and global_hooks_dir.strip():
            global_hooks_dir = _validate_path(global_hooks_dir)
        else:
            global_hooks_dir = _SYNC_DEFAULT_GLOBAL

        result = _sync_hooks_fn(project_hooks_dir, global_hooks_dir)

        # Format result as human-readable string
        lines = ["[sync-hooks] Sync Result:"]
        if result["copied"]:
            lines.append(f"  Copied: {', '.join(result['copied'])}")
        if result["skipped"]:
            lines.append(f"  Skipped (not found): {', '.join(result['skipped'])}")
        if result["errors"]:
            lines.append(f"  Errors: {', '.join(result['errors'])}")
        if not result["copied"] and not result["errors"]:
            lines.append("  No files to copy (all skipped).")
        return "\n".join(lines)
    except ValueError as e:
        return f"ERROR: {e}"
    except Exception as e:
        print(f"sync_hooks_to_global error: {e}", file=sys.stderr)
        return f"ERROR: {e}"


@mcp.tool()
def hook_health_check(
    config_path: str = "",
    log_path: str = "",
) -> str:
    """Check health status of message event hooks.

    Reads message_hooks.json and message_hook_log.jsonl to produce
    a status summary of all registered message event hooks.

    Reports:
    - Total hooks registered and enabled/disabled state
    - Recent failure counts from log
    - Hooks that may have been auto-disabled (consecutive failures)

    Args:
        config_path: Path to message_hooks.json (optional, uses default)
        log_path: Path to message_hook_log.jsonl (optional, uses default)
    """
    return _hook_health_check_impl(config_path=config_path, log_path=log_path)


@mcp.tool()
def sync_hooks_to_global(
    project_hooks_dir: str = "",
    global_hooks_dir: str = "",
) -> str:
    """Sync project hook files to global ~/.claude/hooks/ directory.

    Copies target hook files (behavior-guard.js, behavior-rules.json, etc.)
    from the project hooks/ directory to the global hooks directory.
    Uses temp-file + rename for safe writes.

    Args:
        project_hooks_dir: Path to project hooks/ directory (optional, uses default)
        global_hooks_dir: Path to global hooks directory (optional, uses default)
    """
    return _sync_hooks_to_global_impl(
        project_hooks_dir=project_hooks_dir,
        global_hooks_dir=global_hooks_dir,
    )


# --- Group B 1:1:1 compliance: behavior_guidance + psyche_drive ---


# Import behavior_guidance (tools/ — already on sys.path)
# Import psyche_drive (hooks/ — already added to sys.path above for Group A)
from behavior_guidance import generate_guidance as _bg_generate_guidance
from psyche_drive import run_psyche_drive


def _behavior_guidance_impl(
    memory_dir: str = "",
    docs_dir: str = "",
) -> str:
    """Implementation for behavior_guidance MCP tool.

    Wraps existing behavior_guidance.generate_guidance() function.
    Validates paths from external sources.
    """
    try:
        # Resolve memory_dir with traversal protection
        if memory_dir and memory_dir.strip():
            memory_dir = _validate_path(memory_dir)
            if not os.path.isdir(memory_dir):
                return f"ERROR: memory_dir does not exist: {memory_dir}"
        else:
            memory_dir = DEFAULT_MEMORY_DIR if DEFAULT_MEMORY_DIR else ""

        # Resolve docs_dir with traversal protection
        if docs_dir and docs_dir.strip():
            docs_dir = _validate_path(docs_dir)
        else:
            docs_dir = os.path.join(_PROJECT_ROOT, "docs") if _PROJECT_ROOT else ""

        return _bg_generate_guidance(memory_dir, docs_dir)
    except ValueError as e:
        return f"ERROR: {e}"
    except Exception as e:
        print(f"behavior_guidance error: {e}", file=sys.stderr)
        return f"ERROR: {e}"


def _psyche_drive_impl(
    memory_dir: str = "",
) -> str:
    """Implementation for psyche_drive MCP tool.

    Wraps existing psyche_drive.run_psyche_drive() function.
    Validates paths from external sources.
    Returns a summary string (run_psyche_drive itself returns None).
    """
    try:
        # Resolve memory_dir with traversal protection
        if memory_dir and memory_dir.strip():
            memory_dir = _validate_path(memory_dir)
            if not os.path.isdir(memory_dir):
                return f"ERROR: memory_dir does not exist: {memory_dir}"
        else:
            memory_dir = DEFAULT_MEMORY_DIR if DEFAULT_MEMORY_DIR else ""

        run_psyche_drive(memory_dir)
        return "psyche_drive executed successfully"
    except ValueError as e:
        return f"ERROR: {e}"
    except Exception as e:
        print(f"psyche_drive error: {e}", file=sys.stderr)
        return f"ERROR: {e}"


@mcp.tool()
def behavior_guidance(
    memory_dir: str = "",
    docs_dir: str = "",
) -> str:
    """Generate behavioral guidance from emotion state and Gap Analysis.

    Reads current emotion state (fulfillment/tension/affinity) and Gap Analysis
    documents, then produces actionable guidance text including:
    - Current emotional state description
    - Recommended next actions based on gap priorities
    - Cautions based on tension/affinity levels

    This is READ-ONLY observation. No state is modified.

    Args:
        memory_dir: Directory containing emotion_state.json (optional, uses default)
        docs_dir: Directory containing gap_analysis_*.md files (optional, uses default)
    """
    return _behavior_guidance_impl(memory_dir=memory_dir, docs_dir=docs_dir)


@mcp.tool()
def psyche_drive(
    memory_dir: str = "",
) -> str:
    """Run automatic psyche state updates (emotion, observation, activation).

    Evaluates time-based and phase-transition triggers, then executes:
    - Emotion updates (via emotion_react chain)
    - Self-observation snapshots (7-module pipeline)
    - Activation surface updates

    Updates are written to state files. Execution respects internal timeouts
    (5s overall, 3s per category) and exponential backoff on failures.

    Args:
        memory_dir: Directory containing psyche state files (optional, uses default)
    """
    return _psyche_drive_impl(memory_dir=memory_dir)


# --- Group C 1:1:1 compliance: observation_log (Python equivalent of JS hook) ---


# Data directory for observations (same as JS hook's DATA_DIR)
_OBSERVATION_DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
_OBSERVATION_FILE_NAME = "observations.jsonl"
_MAX_OBSERVATION_FILE_SIZE = 5 * 1024 * 1024  # 5MB, same as JS hook
_MAX_TOOL_NAME_LEN = 100
_MAX_SESSION_ID_LEN = 12
_MAX_BASH_CMD_LEN = 50
_MAX_GREP_PATTERN_LEN = 30
_MAX_MCP_PARAM_STR_LEN = 80


def _extract_key_params(tool_name: str, tool_input: dict) -> dict:
    """Extract key parameters from tool input, matching JS hook logic.

    Mirrors extractKeyParams() in observation-logger.js exactly.
    """
    if tool_name in ("Read", "Write", "Edit"):
        return {"file": tool_input.get("file_path", "") or ""}
    elif tool_name == "Bash":
        cmd = (tool_input.get("command", "") or "")[:_MAX_BASH_CMD_LEN]
        return {"cmd": cmd}
    elif tool_name == "Grep":
        pattern = (tool_input.get("pattern", "") or "")[:_MAX_GREP_PATTERN_LEN]
        path = tool_input.get("path", "") or ""
        return {"pattern": pattern, "path": path}
    elif tool_name == "Glob":
        return {"pattern": tool_input.get("pattern", "") or ""}
    elif tool_name == "Agent":
        return {
            "desc": tool_input.get("description", "") or "",
            "type": tool_input.get("subagent_type", "") or "",
        }
    elif tool_name.startswith("mcp__"):
        params = {}
        for k, v in tool_input.items():
            if isinstance(v, str):
                params[k] = v[:_MAX_MCP_PARAM_STR_LEN]
            elif isinstance(v, (int, float, bool)):
                params[k] = v
        return params
    else:
        return {}


def _observation_log_impl(
    tool_name: str = "",
    tool_input: str = "",
    session_id: str = "",
) -> str:
    """Implementation for observation_log MCP tool.

    Python equivalent of hooks/observation-logger.js.
    Writes observation records to observations.jsonl with same format.

    Args:
        tool_name: Name of the tool that was called.
        tool_input: JSON string of the tool input parameters.
        session_id: Session ID (optional, falls back to env var or 'unknown').

    Returns:
        Status message string.
    """
    from datetime import datetime, timezone

    try:
        # Parse tool_input JSON
        parsed_input = {}
        if tool_input and tool_input.strip():
            try:
                parsed_input = json.loads(tool_input)
                if not isinstance(parsed_input, dict):
                    parsed_input = {}
            except (json.JSONDecodeError, TypeError):
                parsed_input = {}

        # Resolve tool_name
        effective_tool_name = (tool_name or "unknown")[:_MAX_TOOL_NAME_LEN]
        if not effective_tool_name:
            effective_tool_name = "unknown"

        # Resolve session_id
        effective_sid = session_id or os.environ.get("CLAUDE_SESSION_ID", "")
        if not effective_sid:
            # Fallback: try .session-start-time file (same as JS hook)
            sst_file = os.path.join(_PROJECT_ROOT, "hooks", ".session-start-time")
            if os.path.exists(sst_file):
                try:
                    with open(sst_file, "r", encoding="utf-8") as f:
                        epoch = f.read().strip()
                    effective_sid = "s" + epoch
                except Exception:
                    effective_sid = "unknown"
            else:
                effective_sid = "unknown"
        effective_sid = effective_sid[:_MAX_SESSION_ID_LEN]

        # Extract key params (same logic as JS hook)
        key_params = _extract_key_params(effective_tool_name, parsed_input)

        # Build observation record (same format as JS hook)
        observation = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "sid": effective_sid,
            "tool": effective_tool_name,
            "params": key_params,
        }

        line = json.dumps(observation, ensure_ascii=False) + "\n"

        # Ensure data dir exists
        data_dir = _OBSERVATION_DATA_DIR
        if not os.path.isdir(data_dir):
            os.makedirs(data_dir, exist_ok=True)

        obs_file = os.path.join(data_dir, _OBSERVATION_FILE_NAME)

        # Rotate if file exceeds size limit (same as JS hook)
        # Use try/except around getsize+rename to handle TOCTOU races
        # (file may be deleted/rotated by another process between check and rename)
        try:
            file_size = os.path.getsize(obs_file)
            if file_size > _MAX_OBSERVATION_FILE_SIZE:
                archive = obs_file.replace(
                    ".jsonl", f".{int(time.time() * 1000)}.jsonl"
                )
                try:
                    os.rename(obs_file, archive)
                except OSError:
                    pass  # another process may have rotated already
        except OSError:
            pass  # file may not exist yet or was just rotated

        # Append record
        with open(obs_file, "a", encoding="utf-8") as f:
            f.write(line)

        return f"Observation logged: {effective_tool_name}"

    except Exception as e:
        print(f"observation_log error: {e}", file=sys.stderr)
        return f"ERROR: {e}"


@mcp.tool()
def observation_log(
    tool_name: str = "",
    tool_input: str = "",
    session_id: str = "",
) -> str:
    """Log a tool observation to observations.jsonl (Python equivalent of JS hook).

    Records the same data format as the PostToolUse JS hook (observation-logger.js).
    This MCP tool exists for 1:1:1 structural compliance and provides:
    - Manual observation logging (e.g., from Claude for testing/batch processing)
    - Same record format: {ts, sid, tool, params}
    - Same rotation logic (5MB limit)

    NOTE: During normal operation, the PostToolUse JS hook handles automatic
    observation logging for performance reasons. This MCP tool is the
    structural counterpart for 1:1:1 compliance.

    Args:
        tool_name: Name of the tool (e.g., "Read", "Bash", "mcp__memory__search")
        tool_input: JSON string of tool input parameters
        session_id: Session ID (optional, auto-detected from env/file)
    """
    return _observation_log_impl(
        tool_name=tool_name,
        tool_input=tool_input,
        session_id=session_id,
    )


# --- GitHub Notifier (G4: GitHub→Discord notification) ---


# --- Skill Metadata (G38: SKILL.md Versioning & Metadata) ---


from skill_metadata import (
    format_scan_result as _sm_format_scan_result,
)
from skill_metadata import (
    scan_all_skills as _sm_scan_all_skills,
)

# Default skill commands directory
_SKILL_COMMANDS_DIR = os.path.join(_PROJECT_ROOT, ".claude", "commands")


@mcp.tool()
def skill_metadata(
    commands_dir: str = "",
) -> str:
    """Scan skill definition files and report version/dependency metadata.

    Reads .md files in the commands directory, extracts version, requires,
    depends_on, and last_updated fields from frontmatter, and reports:
    - All skills with their metadata
    - Circular dependency warnings
    - Unversioned skills

    This is READ-ONLY: no skill files are modified.

    Args:
        commands_dir: Path to commands directory (optional, uses .claude/commands/)
    """
    try:
        if commands_dir and commands_dir.strip():
            commands_dir = _validate_path(commands_dir)
        else:
            commands_dir = _SKILL_COMMANDS_DIR

        result = _sm_scan_all_skills(commands_dir)
        return _sm_format_scan_result(result)
    except ValueError as e:
        return f"ERROR: {e}"
    except Exception as e:
        print(f"skill_metadata error: {e}", file=sys.stderr)
        return f"ERROR: {e}"


# --- Workflow Crystallizer (G40) ---


from workflow_crystallizer import (
    crystallize_workflows as _wc_crystallize,
)
from workflow_crystallizer import (
    format_crystallize_result as _wc_format,
)

_WC_DEFAULT_OBS_FILE = os.path.join(_PROJECT_ROOT, 'data', 'observations.jsonl')


@mcp.tool()
def workflow_crystallize(
    last_n: int = 1000,
    min_occurrences: int = 3,
    max_candidates: int = 20,
) -> str:
    """Detect repeated tool usage patterns and propose skill candidates.

    Reads observations.jsonl (last N lines only) and finds tool sequences
    that appear repeatedly, suggesting them as potential new skills.

    Read-only: never modifies observation logs or creates skill files.
    Output is informational only (candidate proposals).

    Args:
        last_n: Number of recent observations to analyze (default 1000)
        min_occurrences: Minimum pattern occurrences to report (default 3)
        max_candidates: Maximum candidates to return (default 20)
    """
    try:
        result = _wc_crystallize(
            obs_file_path=_WC_DEFAULT_OBS_FILE,
            skills_dir=_SKILL_COMMANDS_DIR,
            last_n=last_n,
            min_occurrences=min_occurrences,
            max_candidates=max_candidates,
        )
        return _wc_format(result)
    except Exception as e:
        print(f"workflow_crystallize error: {e}", file=sys.stderr)
        return f"ERROR: {e}"


# --- Hook Diagnose (read-only diagnostic) ---

from hook_diagnose import diagnose as _hd_diagnose
from hook_diagnose import format_report as _hd_format


@mcp.tool()
def hook_diagnose() -> str:
    """Read-only diagnostic of hook state files.

    Inspects a fixed, enumerated set of hook state files and returns a
    structured text report showing: existence, mtime, JSON validity,
    epoch sanity, and detected anomalies.

    Use this when hooks are blocking you and you need to understand which
    state file is in an unexpected condition.

    This tool is STRICTLY READ-ONLY. It never writes, edits, or deletes
    any file. The user reviews the report and takes manual action.

    No arguments needed - auto-detects project root.
    """
    try:
        result = _hd_diagnose()
        return _hd_format(result)
    except Exception as e:
        print(f"hook_diagnose error: {e}", file=sys.stderr)
        return f"ERROR: {e}"


from github_notifier import DEFAULT_CONFIG_PATH as _GITHUB_DEFAULT_CONFIG
from github_notifier import check_and_notify as _github_check_and_notify


@mcp.tool()
def github_notify() -> str:
    """Check GitHub repos for new events and send Discord notifications.

    Polls configured GitHub repositories for new events (push, PR, issue, etc.)
    and sends formatted notifications to Discord.

    Configuration is read from discord_data/github_notifier_config.json.
    Returns event count or "Not configured" if setup is incomplete.

    No arguments needed - reads from config file.
    """
    try:
        return _github_check_and_notify(config_path=_GITHUB_DEFAULT_CONFIG)
    except Exception as e:
        print(f"github_notify error: {e}", file=sys.stderr)
        return f"ERROR: {e}"


# Import growth modules (same as memory_mcp_server uses)

# Import observation_writer for orchestrator sub-tool call logging
from tool_usage_tracker import (
    format_usage_report,
    get_all_mcp_tools,
    get_default_server_files,
    get_session_tool_usage,
)

# Growth directory (same as memory_mcp_server.py)
_GROWTH_DIR = os.path.join(_PROJECT_ROOT, "growth")


@mcp.tool()
def tool_usage_status() -> str:
    """Show which MCP tools have been used in the current session.

    Scans all MCP server source files to extract the full tool list,
    then checks observations.jsonl for which tools were actually called.
    Returns a formatted report with per-server grouping, checkmarks,
    usage percentage, and unused tool list.

    No arguments needed - auto-detects server files and observations.
    """
    try:
        data_dir = os.path.join(_PROJECT_ROOT, "data")
        obs_file = os.path.join(data_dir, "observations.jsonl")
        server_files = get_default_server_files(_PROJECT_ROOT)
        all_tools = get_all_mcp_tools(server_files)
        used = get_session_tool_usage(obs_file)
        return format_usage_report(all_tools, used)
    except Exception as e:
        print(f"tool_usage_status error: {e}", file=sys.stderr)
        return f"ERROR: {e}"


# --- Orchestrator Tools ---

import after_action_review  # noqa: E402
import growth_metrics  # noqa: E402
import lesson_conflict  # noqa: E402
import mastery_profile  # noqa: E402
import observation_writer  # noqa: E402
import transfer_monitor  # noqa: E402


@mcp.tool()
def orchestrate_system_health() -> str:
    """System health check: runs 7 diagnostic tools and returns unified report.

    Sub-tools: hook_diagnose, hook_health_check, memory_status, persistent_cron_status,
    discord_status, discord_receive_status, tool_usage_status.
    All sub-calls are fail-open (one failure does not block others).
    Read-only: no state is modified.
    """
    sections = []

    # 1. hook_diagnose
    try:
        result = _hd_diagnose()
        sections.append(f"## Hook Diagnose\n{_hd_format(result)}")
        observation_writer.log_internal_tool_call("hook_diagnose")
    except Exception as e:
        sections.append(f"## Hook Diagnose\n[ERROR] {e}")

    # 2. hook_health_check
    try:
        result = _hook_health_check_impl()
        sections.append(f"## Hook Health Check\n{result}")
        observation_writer.log_internal_tool_call("hook_health_check")
    except Exception as e:
        sections.append(f"## Hook Health Check\n[ERROR] {e}")

    # 3. memory_status (import from memory_mcp_server's underlying module)
    try:
        from episode_recall import get_compression_status

        mem_dir = DEFAULT_MEMORY_DIR
        mem_result = get_compression_status(memory_dir=mem_dir)
        sections.append(f"## Memory Status\n{mem_result}")
        observation_writer.log_internal_tool_call("memory_status")
    except Exception as e:
        sections.append(f"## Memory Status\n[ERROR] {e}")

    # 4. persistent_cron_status
    try:
        from cron_mcp_server import persistent_cron_status as _cron_status_fn

        cron_result = _cron_status_fn()
        sections.append(f"## Cron Status\n{cron_result}")
        observation_writer.log_internal_tool_call("persistent_cron_status")
    except Exception as e:
        sections.append(f"## Cron Status\n[ERROR] {e}")

    # 5. discord_status (sync method on _client)
    try:
        from discord_mcp_server import _client as _discord_client

        discord_result = _discord_client.get_status()
        sections.append(f"## Discord Status\n{discord_result}")
        observation_writer.log_internal_tool_call("discord_status")
    except Exception as e:
        sections.append(f"## Discord Status\n[ERROR] {e}")

    # 6. discord_receive_status (file-based, sync internals)
    try:
        from discord_mcp_server import (
            _load_receive_buffer,
            _load_receive_config,
            _load_receive_state,
        )

        state = _load_receive_state()
        config = _load_receive_config()
        buffer_entries = _load_receive_buffer()
        lines = ["Discord Receive Daemon Status:"]
        if not state:
            lines.append("  Daemon: not running (no state file)")
        else:
            conn = state.get("connection_state", "unknown")
            lines.append(f"  Daemon: {conn}")
        lines.append(f"  Allow list entries: {len(config.get('allow_list', []))}")
        lines.append(f"  Buffer entries: {len(buffer_entries)}")
        sections.append("## Discord Receive Status\n" + "\n".join(lines))
        observation_writer.log_internal_tool_call("discord_receive_status")
    except Exception as e:
        sections.append(f"## Discord Receive Status\n[ERROR] {e}")

    # 7. tool_usage_status
    try:
        data_dir = os.path.join(_PROJECT_ROOT, "data")
        obs_file = os.path.join(data_dir, "observations.jsonl")
        server_files = get_default_server_files(_PROJECT_ROOT)
        all_tools = get_all_mcp_tools(server_files)
        used = get_session_tool_usage(obs_file)
        sections.append(f"## Tool Usage Status\n{format_usage_report(all_tools, used)}")
        observation_writer.log_internal_tool_call("tool_usage_status")
    except Exception as e:
        sections.append(f"## Tool Usage Status\n[ERROR] {e}")

    return "\n\n".join(sections) if sections else "No data available"


@mcp.tool()
def orchestrate_growth_report() -> str:
    """Growth system report: runs 6 growth analysis tools and returns unified report.

    Sub-tools: growth_dashboard, mastery_report, aar_report, transfer_report,
    detect_lesson_conflicts, behavior_evolve.
    All sub-calls are fail-open. Read-only: no state is modified.
    """
    sections = []

    # 1. growth_dashboard
    try:
        sections.append(f"## Growth Dashboard\n{growth_metrics.generate_dashboard(_GROWTH_DIR)}")
        observation_writer.log_internal_tool_call("growth_dashboard")
    except Exception as e:
        sections.append(f"## Growth Dashboard\n[ERROR] {e}")

    # 2. mastery_report
    try:
        sections.append(f"## Mastery Report\n{mastery_profile.generate_report(_GROWTH_DIR)}")
        observation_writer.log_internal_tool_call("mastery_report")
    except Exception as e:
        sections.append(f"## Mastery Report\n[ERROR] {e}")

    # 3. aar_report (limit=3)
    try:
        sections.append(f"## AAR Report\n{after_action_review.get_aar_report(_GROWTH_DIR, limit=3)}")
        observation_writer.log_internal_tool_call("aar_report")
    except Exception as e:
        sections.append(f"## AAR Report\n[ERROR] {e}")

    # 4. transfer_report
    try:
        sections.append(f"## Transfer Report\n{transfer_monitor.get_transfer_report(_GROWTH_DIR)}")
        observation_writer.log_internal_tool_call("transfer_report")
    except Exception as e:
        sections.append(f"## Transfer Report\n[ERROR] {e}")

    # 5. detect_lesson_conflicts
    try:
        sections.append(f"## Lesson Conflicts\n{lesson_conflict.get_conflict_report(_GROWTH_DIR)}")
        observation_writer.log_internal_tool_call("detect_lesson_conflicts")
    except Exception as e:
        sections.append(f"## Lesson Conflicts\n[ERROR] {e}")

    # 6. behavior_evolve (already defined in this server)
    try:
        sections.append(f"## Behavior Evolve\n{behavior_evolve()}")
        observation_writer.log_internal_tool_call("behavior_evolve")
    except Exception as e:
        sections.append(f"## Behavior Evolve\n[ERROR] {e}")

    return "\n\n".join(sections) if sections else "No data available"


@mcp.tool()
def orchestrate_session_health() -> str:
    """Session health check: runs 6 session state tools and returns unified report.

    Sub-tools: emotion_get, emotion_history, stm_read, long_term_stats,
    behavior_analyze, workflow_crystallize.
    All sub-calls are fail-open. Read-only: no state is modified.
    """
    sections = []
    memory_dir = DEFAULT_MEMORY_DIR

    # 1. emotion_get
    try:
        from emotion_state import get_state

        sections.append(f"## Emotion State\n{get_state(memory_dir)}")
        observation_writer.log_internal_tool_call("emotion_get")
    except Exception as e:
        sections.append(f"## Emotion State\n[ERROR] {e}")

    # 2. emotion_history (limit=5)
    try:
        from emotion_state import get_history

        history = get_history(memory_dir, limit=5)
        if isinstance(history, list):
            lines = []
            for entry in history:
                lines.append(str(entry))
            sections.append("## Emotion History\n" + "\n".join(lines) if lines else "No history")
        else:
            sections.append(f"## Emotion History\n{history}")
        observation_writer.log_internal_tool_call("emotion_history")
    except Exception as e:
        sections.append(f"## Emotion History\n[ERROR] {e}")

    # 3. stm_read (limit=10)
    try:
        from short_term_store import read_stm

        stm_entries = read_stm(memory_dir, limit=10)
        if isinstance(stm_entries, list):
            lines = []
            for entry in stm_entries:
                if isinstance(entry, dict):
                    cat = entry.get("category", "?")
                    content = entry.get("content", "")[:100]
                    lines.append(f"  [{cat}] {content}")
                else:
                    lines.append(f"  {str(entry)[:100]}")
            sections.append("## STM (last 10)\n" + "\n".join(lines) if lines else "No STM entries")
        else:
            sections.append(f"## STM (last 10)\n{stm_entries}")
        observation_writer.log_internal_tool_call("stm_read")
    except Exception as e:
        sections.append(f"## STM (last 10)\n[ERROR] {e}")

    # 4. long_term_stats (last_n=5)
    try:
        stats = lt_get_stats(memory_dir, last_n=5)
        sections.append(f"## Long-Term Stats\n{lt_format_stats(stats)}")
        observation_writer.log_internal_tool_call("long_term_stats")
    except Exception as e:
        sections.append(f"## Long-Term Stats\n[ERROR] {e}")

    # 5. behavior_analyze (last_n=100)
    try:
        ba_result = behavior_analyze(last_n=100)
        sections.append(f"## Behavior Analysis\n{ba_result}")
        observation_writer.log_internal_tool_call("behavior_analyze")
    except Exception as e:
        sections.append(f"## Behavior Analysis\n[ERROR] {e}")

    # 6. workflow_crystallize
    try:
        wc_result = _wc_crystallize(
            obs_file_path=_WC_DEFAULT_OBS_FILE,
            skills_dir=_SKILL_COMMANDS_DIR,
            last_n=1000,
            min_occurrences=3,
            max_candidates=20,
        )
        sections.append(f"## Workflow Crystallize\n{_wc_format(wc_result)}")
        observation_writer.log_internal_tool_call("workflow_crystallize")
    except Exception as e:
        sections.append(f"## Workflow Crystallize\n[ERROR] {e}")

    return "\n\n".join(sections) if sections else "No data available"


from growth_recorder import handle_cycle_complete as _gr_handle_cycle_complete  # noqa: E402


@mcp.tool()
def orchestrate_cycle_complete(
    cycle_name: str = "",
    completed_gaps: str = "",
    test_count: int = 0,
    review_result: str = "APPROVE",
) -> str:
    """Cycle completion orchestrator: records success, updates mastery, saves trajectory, creates AAR.

    Wraps growth_recorder.handle_cycle_complete() as an MCP tool.
    Internally calls record_success, update_mastery, create_aar, and additional
    growth tools (mastery_report, workflow_crystallize, transfer_report, etc.).

    Args:
        cycle_name: Name of the completed cycle (e.g. "C25")
        completed_gaps: Comma-separated gap IDs (e.g. "G30,G31,G32")
        test_count: Number of tests passed
        review_result: Review outcome (default "APPROVE")
    """
    try:
        # Build JSON input matching growth_recorder's expected format
        gaps_list = [g.strip() for g in completed_gaps.split(",") if g.strip()] if completed_gaps else []
        raw_input = json.dumps({
            "cycle_name": cycle_name,
            "completed_gaps": gaps_list,
            "test_count": test_count,
            "review_result": review_result,
        })

        result = _gr_handle_cycle_complete(raw_input, _GROWTH_DIR)

        # Log the orchestrator call itself
        observation_writer.log_internal_tool_call(
            "orchestrate_cycle_complete",
            {"cycle_name": cycle_name, "test_count": test_count},
        )

        # Format result as readable report
        lines = [f"## Cycle Complete: {cycle_name}"]
        overall = "SUCCESS" if result.get("success") else "PARTIAL"
        lines.append(f"Overall: {overall}")
        for key, value in result.items():
            if key in ("success", "event_type"):
                continue
            status = "OK" if value == "ok" else ("EMPTY" if value == "empty" else "FAILED")
            lines.append(f"  {key}: [{status}]")

        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: orchestrate_cycle_complete failed: {e}"


def main():
    print("Self-observation MCP server starting on stdio...", file=sys.stderr)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
