#!/usr/bin/env python3
"""Growth Recorder — hook-driven growth module bridge.

Called from hooks (auto-test-runner.js, stop-session-end.js) to record
growth events into project_root/growth/ via success_registry, mastery_profile,
and growth_metrics modules.

Entry point: python growth_recorder.py <event_type>
Reads JSON from stdin. Writes to GROWTH_DIR (env var or project_root/growth/).

Fail-open: growth recording errors never block hook execution.
"""

import json
import logging
import os
import sys

# Add tools directory to path for growth module imports
TOOLS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tools",
)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import after_action_review  # noqa: E402
import growth_metrics  # noqa: E402
import mastery_profile  # noqa: E402
import observation_writer  # noqa: E402
import success_registry  # noqa: E402
import trajectory_store  # noqa: E402

logger = logging.getLogger(__name__)

# Max stdin read size (1MB)
MAX_STDIN = 1024 * 1024


def get_growth_dir(environ: dict | None = None) -> str:
    """Resolve growth directory from environment or default.

    Args:
        environ: Environment dict (defaults to os.environ).

    Returns:
        Absolute path to growth directory.
    """
    if environ is None:
        environ = os.environ
    if environ.get("GROWTH_DIR"):
        return environ["GROWTH_DIR"]
    # Default: project_root/growth/ (same as memory_mcp_server.py GROWTH_DIR)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(project_root, "growth")


def _parse_stdin(raw: str) -> dict:
    """Parse stdin JSON, returning empty dict on failure."""
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        return {}
    except (json.JSONDecodeError, ValueError):
        return {}


def handle_test_pass(raw_stdin: str, growth_dir: str) -> dict:
    """Handle test_pass event: all tests passed.

    Records success in success_registry and updates mastery_profile.

    Args:
        raw_stdin: JSON string with optional test_file, test_count.
        growth_dir: Path to growth data directory.

    Returns:
        Result dict with success status.
    """
    data = _parse_stdin(raw_stdin)
    test_file = str(data.get("test_file", "unknown"))[:500]
    test_count = data.get("test_count", 0)

    os.makedirs(growth_dir, exist_ok=True)

    context = f"Tests passed: {test_file} ({test_count} tests)"
    why = "All tests passed on first execution after code change"

    # Record success pattern
    try:
        success_registry.record_success(
            growth_dir,
            "test_pass",
            context,
            why,
            tags=["auto", "test_pass"],
        )
        observation_writer.log_internal_tool_call("record_success_tool", {"pattern": "test_pass", "context": context[:80]})
    except Exception as e:
        logger.error("success_registry.record_success failed: %s", e)
        return {"success": False, "event_type": "test_pass", "error": str(e)}

    # Update mastery profile
    try:
        mastery_profile.update_mastery(
            growth_dir,
            "testing",
            success=True,
            approach=f"Passed {test_count} tests in {test_file}",
        )
        observation_writer.log_internal_tool_call("update_mastery", {"domain": "testing"})
    except Exception as e:
        logger.warning("mastery_profile.update_mastery failed: %s", e)
        # Non-fatal: success_registry already recorded

    return {"success": True, "event_type": "test_pass", "context": context}


def handle_review_pass(raw_stdin: str, growth_dir: str) -> dict:
    """Handle review_pass event: reviewer completed with no MED+ issues.

    Records review_zero success in success_registry.

    Args:
        raw_stdin: JSON string with optional review_summary.
        growth_dir: Path to growth data directory.

    Returns:
        Result dict with success status.
    """
    data = _parse_stdin(raw_stdin)
    summary = str(data.get("review_summary", "Review passed with zero issues"))[:500]

    os.makedirs(growth_dir, exist_ok=True)

    context = f"Review clean: {summary}"
    why = "Code passed review without medium or higher severity issues"

    try:
        success_registry.record_success(
            growth_dir,
            "review_zero",
            context,
            why,
            tags=["auto", "review_pass"],
        )
        observation_writer.log_internal_tool_call("record_success_tool", {"pattern": "review_zero", "context": context[:80]})
    except Exception as e:
        logger.error("success_registry.record_success failed: %s", e)
        return {"success": False, "event_type": "review_zero", "error": str(e)}

    return {"success": True, "event_type": "review_zero", "context": context}


def handle_session_summary(raw_stdin: str, growth_dir: str) -> dict:
    """Handle session_summary event: emit growth health summary at session end.

    Calls growth_metrics.get_health_summary and logs the result.

    Args:
        raw_stdin: Unused (session_end doesn't pass meaningful data).
        growth_dir: Path to growth data directory.

    Returns:
        Result dict with success status and summary string.
    """
    os.makedirs(growth_dir, exist_ok=True)

    try:
        summary = growth_metrics.get_health_summary(growth_dir)
    except Exception as e:
        logger.warning("growth_metrics.get_health_summary failed: %s", e)
        summary = "Growth: metrics unavailable"

    result = {"success": True, "event_type": "session_summary", "summary": summary}

    # Group 2 extension: behavior_analyze
    try:
        from self_observation_mcp_server import behavior_analyze
        ba_result = behavior_analyze(last_n=100)
        result["behavior_analyze"] = str(ba_result)[:500] if ba_result else ""
    except Exception as e:
        logger.warning("session_summary: behavior_analyze failed: %s", e)
        result["behavior_analyze"] = ""

    # Group 2 extension: long_term_stats
    try:
        from self_observation_mcp_server import long_term_stats
        lts_result = long_term_stats(last_n=5)
        result["long_term_stats"] = str(lts_result)[:500] if lts_result else ""
    except Exception as e:
        logger.warning("session_summary: long_term_stats failed: %s", e)
        result["long_term_stats"] = ""

    # Group 2 extension: growth_dashboard
    try:
        from memory_mcp_server import growth_dashboard
        gd_result = growth_dashboard()
        result["growth_dashboard"] = str(gd_result)[:500] if gd_result else ""
    except Exception as e:
        logger.warning("session_summary: growth_dashboard failed: %s", e)
        result["growth_dashboard"] = ""

    return result


OBS_LIMIT = 500


def _normalize_session_id(session_id: str) -> str:
    """Normalize session_id to match observation-logger.js format.

    observation-logger.js generates sid as: 's' + epoch, truncated to 12 chars.
    session-end.js passes raw epoch from .session-start-time (no 's' prefix).
    This function ensures both formats produce the same matching key.

    Args:
        session_id: Raw session ID (may or may not have 's' prefix).

    Returns:
        Normalized sid in observation-logger format (e.g. 's17753765506').
    """
    if not session_id:
        return ""
    sid = session_id.strip()
    if not sid.startswith("s"):
        sid = "s" + sid
    return sid[:12]


def read_observations(data_dir: str, session_id: str, limit: int = OBS_LIMIT) -> list[dict]:
    """Read observations.jsonl filtered by session_id.

    Matches entries in two ways:
    1. By normalized sid (observation-logger.js format entries)
    2. By timestamp range for entries without sid (MCP-format entries)

    Args:
        data_dir: Path to the data directory containing observations.jsonl.
        session_id: Session ID to filter by (raw epoch or 's'+epoch format).
        limit: Maximum number of lines to read from file.

    Returns:
        List of observation dicts matching session_id.
    """
    path = os.path.join(data_dir, "observations.jsonl")
    try:
        with open(path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
        # Take the last `limit` lines (newest entries at end of file)
        lines = all_lines[-limit:] if limit < len(all_lines) else all_lines
    except FileNotFoundError:
        return []
    except OSError as e:
        logger.warning("Failed to read observations: %s", e)
        return []

    normalized_sid = _normalize_session_id(session_id)
    if not normalized_sid:
        return []

    # Derive session start timestamp for MCP-format entries (no sid field).
    # Extract epoch from normalized sid (strip 's' prefix).
    try:
        # session-start-time is ms epoch; observations ts is ISO string
        session_start_ms = int(session_id.lstrip("s"))
    except (ValueError, IndexError):
        session_start_ms = 0

    results = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue

        entry_sid = entry.get("sid", "")
        if entry_sid:
            # observation-logger.js format: match by normalized sid
            if _normalize_session_id(entry_sid) == normalized_sid:
                results.append(entry)
        elif session_start_ms > 0:
            # MCP-format entries (no sid): match by timestamp range
            # Include entries from session start to +24h
            ts_str = entry.get("ts", "")
            if ts_str:
                try:
                    from datetime import datetime

                    entry_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    entry_ms = int(entry_dt.timestamp() * 1000)
                    # Within session window: start to start+24h
                    if session_start_ms <= entry_ms <= session_start_ms + 86_400_000:
                        results.append(entry)
                except (ValueError, TypeError):
                    continue

    return results


def handle_trajectory(raw_stdin: str, growth_dir: str) -> dict:
    """Handle trajectory event: record successful execution trajectory.

    Reads observations to find Agent calls in the session, then records
    the sequence as a trajectory via trajectory_store.

    Args:
        raw_stdin: JSON string with review_summary, session_id, data_dir.
        growth_dir: Path to growth data directory.

    Returns:
        Result dict with success status.
    """
    data = _parse_stdin(raw_stdin)
    if not data:
        return {"success": True, "event_type": "trajectory", "reason": "skipped: no input"}

    session_id = str(data.get("session_id", ""))
    data_dir = str(data.get("data_dir", ""))
    review_summary = str(data.get("review_summary", ""))[:500]

    if not session_id or not data_dir:
        return {"success": True, "event_type": "trajectory", "reason": "skipped: missing session_id or data_dir"}

    obs = read_observations(data_dir, session_id)
    agent_obs = [o for o in obs if o.get("tool") == "Agent"]

    if len(agent_obs) < 2:
        return {"success": True, "event_type": "trajectory", "reason": "skipped: fewer than 2 Agent calls"}

    # Build steps from Agent observations
    steps = []
    agent_types = []
    for o in agent_obs:
        params = o.get("params", {})
        subagent_type = str(params.get("subagent_type", "unknown"))
        description = str(params.get("description", ""))[:200]
        agent_types.append(subagent_type)
        steps.append({
            "action": subagent_type,
            "tool": "Agent",
            "approach": description,
            "result": "",
        })

    task_class = "-".join(agent_types)
    outcome = review_summary if review_summary else "Review passed"

    os.makedirs(growth_dir, exist_ok=True)

    try:
        trajectory_store.record_trajectory(
            growth_dir,
            task_class,
            steps,
            outcome,
            transferability=0.5,
        )
        observation_writer.log_internal_tool_call("record_trajectory", {"task_class": task_class[:80]})
    except Exception as e:
        logger.error("trajectory_store.record_trajectory failed: %s", e)
        return {"success": False, "event_type": "trajectory", "reason": f"skipped: {e}"}

    return {"success": True, "event_type": "trajectory", "task_class": task_class}


MAX_CYCLE_NAME_LEN = 200
MAX_GAPS_COUNT = 50
MAX_GAP_LEN = 200


def _extract_domain(cycle_name: str) -> str:
    """Extract a domain name from cycle_name for mastery tracking.

    Strips leading cycle ID prefix (e.g. 'C23-') and returns the remainder.
    Falls back to 'development' if nothing meaningful remains.

    Args:
        cycle_name: The cycle identifier string.

    Returns:
        A domain string suitable for mastery_profile.
    """
    # Strip common cycle ID prefixes like "C23-", "c10-"
    name = cycle_name.strip()
    if len(name) > 1 and name[0].upper() == "C" and "-" in name:
        dash_idx = name.index("-")
        # Check if prefix before dash is "C" + digits
        prefix = name[1:dash_idx]
        if prefix.isdigit():
            name = name[dash_idx + 1:]
    name = name.strip("-").strip()
    if not name:
        return "development"
    # Convert hyphens to underscores, take first 50 chars
    return name.replace("-", "_")[:50]


def handle_cycle_complete(raw_stdin: str, growth_dir: str) -> dict:
    """Handle cycle_complete event: record growth after reviewer APPROVE.

    Calls three growth tools independently (fail-open per tool):
    1. record_success — logs the cycle completion as a success pattern
    2. update_mastery — updates domain mastery based on cycle category
    3. create_aar — creates an After-Action Review for the cycle

    Args:
        raw_stdin: JSON string with cycle_name, completed_gaps,
                   test_count, review_result.
        growth_dir: Path to growth data directory.

    Returns:
        Result dict with success status and per-tool results.
    """
    data = _parse_stdin(raw_stdin)

    cycle_name = str(data.get("cycle_name", "unknown"))[:MAX_CYCLE_NAME_LEN]
    completed_gaps = data.get("completed_gaps", [])
    if not isinstance(completed_gaps, list):
        completed_gaps = [str(completed_gaps)]
    completed_gaps = [str(g)[:MAX_GAP_LEN] for g in completed_gaps[:MAX_GAPS_COUNT]]
    test_count = data.get("test_count", 0)
    if not isinstance(test_count, (int, float)):
        test_count = 0
    test_count = int(test_count)
    review_result = str(data.get("review_result", "unknown"))[:100]

    os.makedirs(growth_dir, exist_ok=True)

    gaps_str = ", ".join(completed_gaps) if completed_gaps else "none"
    context = f"Cycle {cycle_name} completed. Gaps: {gaps_str}"[:500]
    why = f"Tests passed ({test_count} tests), reviewer {review_result}"[:1000]

    result = {
        "success": True,
        "event_type": "cycle_complete",
        "record_success": "ok",
        "update_mastery": "ok",
        "create_aar": "ok",
    }

    # Step 1: record_success
    try:
        success_registry.record_success(
            growth_dir,
            "review_zero",
            context,
            why,
            tags=["auto", "cycle_complete"],
        )
        observation_writer.log_internal_tool_call("record_success_tool", {"pattern": "review_zero", "context": context[:80]})
    except Exception as e:
        logger.error("cycle_complete: record_success failed: %s", e)
        result["record_success"] = "failed"

    # Step 2: update_mastery
    domain = _extract_domain(cycle_name)
    try:
        mastery_profile.update_mastery(
            growth_dir,
            domain,
            success=True,
            approach=f"Cycle {cycle_name}: {test_count} tests, {review_result}",
        )
        observation_writer.log_internal_tool_call("update_mastery", {"domain": domain})
    except Exception as e:
        logger.error("cycle_complete: update_mastery failed: %s", e)
        result["update_mastery"] = "failed"

    # Step 3: create_aar
    try:
        after_action_review.create_aar(
            growth_dir,
            intent=f"Complete cycle: {cycle_name}",
            actual=f"Completed gaps: {gaps_str}",
            why_success=why,
            replicable=f"TDD + reviewer APPROVE flow with {test_count} tests",
            context_dependent=f"Cycle-specific: {cycle_name}",
            transferable=f"Domain: {domain}, pattern: cycle completion recording",
            tags=["auto", "cycle_complete"],
        )
        observation_writer.log_internal_tool_call("create_aar", {"intent": f"Complete cycle: {cycle_name}"[:80]})
    except Exception as e:
        logger.error("cycle_complete: create_aar failed: %s", e)
        result["create_aar"] = "failed"

    # Group 3 extensions: additional growth tools (all fail-open)

    # mastery_report
    try:
        from memory_mcp_server import mastery_report
        mr = mastery_report()
        result["mastery_report"] = "ok" if mr else "empty"
    except Exception as e:
        logger.warning("cycle_complete: mastery_report failed: %s", e)
        result["mastery_report"] = "failed"

    # workflow_crystallize
    try:
        from self_observation_mcp_server import workflow_crystallize
        wc = workflow_crystallize(last_n=500, min_occurrences=2, max_candidates=10)
        result["workflow_crystallize"] = "ok" if wc else "empty"
    except Exception as e:
        logger.warning("cycle_complete: workflow_crystallize failed: %s", e)
        result["workflow_crystallize"] = "failed"

    # transfer_report
    try:
        from memory_mcp_server import transfer_report
        tr = transfer_report()
        result["transfer_report"] = "ok" if tr else "empty"
    except Exception as e:
        logger.warning("cycle_complete: transfer_report failed: %s", e)
        result["transfer_report"] = "failed"

    # search_successes_tool
    try:
        from memory_mcp_server import search_successes_tool
        ss = search_successes_tool(query=cycle_name, limit=5)
        result["search_successes"] = "ok" if ss else "empty"
    except Exception as e:
        logger.warning("cycle_complete: search_successes_tool failed: %s", e)
        result["search_successes"] = "failed"

    # detect_lesson_conflicts
    try:
        from memory_mcp_server import detect_lesson_conflicts
        dlc = detect_lesson_conflicts()
        result["detect_lesson_conflicts"] = "ok" if dlc else "empty"
    except Exception as e:
        logger.warning("cycle_complete: detect_lesson_conflicts failed: %s", e)
        result["detect_lesson_conflicts"] = "failed"

    # aar_report
    try:
        from memory_mcp_server import aar_report
        ar = aar_report(limit=3)
        result["aar_report"] = "ok" if ar else "empty"
    except Exception as e:
        logger.warning("cycle_complete: aar_report failed: %s", e)
        result["aar_report"] = "failed"

    return result


MSG_TRUNCATE_LEN = 500


def handle_subagent_stop(raw_stdin: str, growth_dir: str) -> dict:
    """Handle subagent_stop event: record agent lifecycle end.

    Receives SubagentStop event data (reason, agent_id, transcript path,
    last message) and records it for growth tracking. Updates mastery
    profile on successful completion (end_turn).

    Args:
        raw_stdin: JSON string with reason, agent_id,
                   agent_transcript_path, last_assistant_message.
        growth_dir: Path to growth data directory.

    Returns:
        Result dict with success status and event details.
    """
    data = _parse_stdin(raw_stdin)
    if not data:
        return {
            "success": True,
            "event_type": "subagent_stop",
            "reason_detail": "skipped: no input",
        }

    reason = str(data.get("reason", "unknown"))[:100]
    agent_id = str(data.get("agent_id", "unknown"))[:200]
    last_message = str(data.get("last_assistant_message", ""))[:MSG_TRUNCATE_LEN]

    os.makedirs(growth_dir, exist_ok=True)

    # Update mastery profile on successful completion (end_turn)
    if reason == "end_turn":
        try:
            mastery_profile.update_mastery(
                growth_dir,
                "agent_lifecycle",
                success=True,
                approach=f"Agent {agent_id} completed normally",
            )
            observation_writer.log_internal_tool_call("update_mastery", {"domain": "agent_lifecycle"})
        except Exception as e:
            logger.warning("mastery_profile.update_mastery failed: %s", e)

    return {
        "success": True,
        "event_type": "subagent_stop",
        "reason": reason,
        "agent_id": agent_id,
        "last_message": last_message,
    }


def handle_session_aar(raw_stdin: str, growth_dir: str) -> dict:
    """Handle session_aar event: create After-Action Review at session end.

    Combines session summary data with observations statistics to create
    a structured AAR record.

    Args:
        raw_stdin: JSON string with summary, completed, pending, decisions,
                   session_id, data_dir, memory_dir.
        growth_dir: Path to growth data directory (used as memory_dir for AAR).

    Returns:
        Result dict with success status.
    """
    data = _parse_stdin(raw_stdin)
    if not data:
        return {"success": True, "event_type": "session_aar", "reason": "skipped: no input"}

    summary = str(data.get("summary", ""))[:1000]
    completed = data.get("completed", [])
    pending = data.get("pending", [])
    session_id = str(data.get("session_id", ""))
    data_dir = str(data.get("data_dir", ""))

    # intent from summary
    intent = summary.strip()
    if not intent:
        return {"success": True, "event_type": "session_aar", "reason": "skipped: empty intent"}

    # actual from completed
    if isinstance(completed, list):
        actual = "; ".join(str(c) for c in completed[:20])[:1000]
    else:
        actual = str(completed)[:1000]
    if not actual:
        actual = "No completed items recorded"

    # Collect observation statistics
    agent_counts: dict[str, int] = {}
    tool_counts: dict[str, int] = {}
    obs = []
    if data_dir:
        obs = read_observations(data_dir, session_id) if session_id else []

    for o in obs:
        tool = o.get("tool", "unknown")
        tool_counts[tool] = tool_counts.get(tool, 0) + 1
        if tool == "Agent":
            subtype = str(o.get("params", {}).get("subagent_type", "unknown"))
            agent_counts[subtype] = agent_counts.get(subtype, 0) + 1

    # why_success from agent counts + tool counts
    parts = []
    for agent_type, count in sorted(agent_counts.items()):
        parts.append(f"{agent_type} {count}x")
    why_success = ", ".join(parts)

    # If no agents were used, summarize from tool counts instead of skipping
    if not why_success.strip():
        if tool_counts:
            top_tools = sorted(tool_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            tool_parts = [f"{t} {c}x" for t, c in top_tools]
            why_success = f"Tools used: {', '.join(tool_parts)}"
        elif obs:
            why_success = f"{len(obs)} observations recorded (no agent calls)"
        else:
            why_success = "Session completed (no observations matched)"

    # replicable: which flow steps were observed
    flow_steps = ["designer", "planner", "analyzer", "implementer", "reviewer"]
    observed_steps = [s for s in flow_steps if s in agent_counts]
    replicable = f"Flow steps observed: {', '.join(observed_steps)}" if observed_steps else "No standard flow steps observed"

    # context_dependent from pending
    if isinstance(pending, list):
        context_dependent = "; ".join(str(p) for p in pending[:20])[:1000]
    else:
        context_dependent = str(pending)[:1000]
    if not context_dependent:
        context_dependent = "No pending items"

    # transferable: agent types used
    transferable = f"Agents used: {', '.join(sorted(agent_counts.keys()))}" if agent_counts else "No agents used"

    os.makedirs(growth_dir, exist_ok=True)

    try:
        after_action_review.create_aar(
            growth_dir,
            intent=intent,
            actual=actual,
            why_success=why_success,
            replicable=replicable,
            context_dependent=context_dependent,
            transferable=transferable,
            tags=["auto", "session"],
        )
        observation_writer.log_internal_tool_call("create_aar", {"intent": intent[:80]})
    except Exception as e:
        logger.error("after_action_review.create_aar failed: %s", e)
        return {"success": False, "event_type": "session_aar", "reason": f"skipped: {e}"}

    return {"success": True, "event_type": "session_aar"}


# Event type dispatch table
EVENT_HANDLERS = {
    "test_pass": handle_test_pass,
    "review_pass": handle_review_pass,
    "session_summary": handle_session_summary,
    "trajectory": handle_trajectory,
    "session_aar": handle_session_aar,
    "subagent_stop": handle_subagent_stop,
    "cycle_complete": handle_cycle_complete,
}


def main(argv: list | None = None) -> int:
    """CLI entry point.

    Usage: python growth_recorder.py <event_type>
    Reads JSON from stdin.

    Args:
        argv: Command line arguments (defaults to sys.argv).

    Returns:
        0 on success, 1 on error.
    """
    if argv is None:
        argv = sys.argv

    if len(argv) < 2:
        print("Usage: growth_recorder.py <event_type>", file=sys.stderr)
        print(f"  event_types: {', '.join(EVENT_HANDLERS.keys())}", file=sys.stderr)
        return 1

    event_type = argv[1]
    handler = EVENT_HANDLERS.get(event_type)
    if handler is None:
        print(
            f"Unknown event_type: {event_type!r}. "
            f"Valid: {', '.join(EVENT_HANDLERS.keys())}",
            file=sys.stderr,
        )
        return 1

    growth_dir = get_growth_dir()

    try:
        raw_stdin = sys.stdin.read(MAX_STDIN)
    except Exception:
        raw_stdin = ""

    try:
        result = handler(raw_stdin, growth_dir)
        if result.get("summary"):
            print(f"[Growth] {result['summary']}", file=sys.stderr)
        elif result.get("context"):
            print(f"[Growth] Recorded: {result['context']}", file=sys.stderr)
        return 0 if result.get("success") else 1
    except Exception as e:
        print(f"[Growth] Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
