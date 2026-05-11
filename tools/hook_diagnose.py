#!/usr/bin/env python3
"""Read-only diagnostic for hook state files.

Purpose:
    When the user (or Claude) is blocked by hooks and cannot determine which
    state file is in an unexpected condition, hook_diagnose() reads a fixed,
    explicitly-enumerated set of hook state files and produces a structured
    text report. It NEVER writes, edits, or deletes anything.

Design constraints (structurally enforced):
    - Read-only: every open() uses mode "r" (or "rb" for size probing).
      Tests assert that no write/append/delete syscall is invoked.
    - No pattern matching for targets: each file is enumerated by name.
    - All paths normalized via os.path.realpath() before use, and constrained
      to the project_root supplied at call time.
    - Per-file read size cap (1 MB) to prevent DoS via oversized state files.
    - fail-open: any exception during inspection of a single file is captured
      into that file's report entry; the overall diagnose() never raises.

This is the diagnostic phase of design_hook_recover.md only. There is no
approval phase, no fix phase, no thinker integration. The user reads the
report and decides manually what (if anything) to do.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

# Per-file read size cap (DoS guard).
MAX_READ_BYTES = 1024 * 1024  # 1 MB

# Observation tail line count.
OBS_TAIL_LINES = 10

# The fixed list of hook state files we inspect. Each entry:
#   (logical_id, relative_path_under_project_root, kind)
# kind in {"flag", "json", "epoch", "jsonl_tail"}
#
# This list is the entire surface of the diagnostic. Adding to it requires
# code review (no pattern matching, no glob).
#
# Note: deprecated session readiness flag files are intentionally excluded;
# session readiness is now stored in behavior-guard-state.json.
_TARGETS: list[tuple[str, str, str]] = [
    ("session-start-done",   "hooks/.session-start-done",        "flag"),
    ("memory-search-done",   "hooks/.memory-search-done",        "flag"),
    ("dev-flow-state",       "hooks/.dev-flow-state",            "json"),
    ("behavior-guard-state", "hooks/.behavior-guard-state.json", "json"),
    ("session-start-time",   "hooks/.session-start-time",        "epoch"),
    ("team-created",         "hooks/.team-created",              "flag"),
    ("observations",         "data/observations.jsonl",          "jsonl_tail"),
]


def _project_root() -> str:
    """Return the realpath of the project root (parent of tools/)."""
    return os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))


def _safe_resolve(project_root: str, rel_path: str) -> str | None:
    """Realpath-normalize and constrain to project_root.

    Returns the absolute path if it stays inside project_root, else None.
    """
    project_root_real = os.path.realpath(project_root)
    candidate = os.path.realpath(os.path.join(project_root_real, rel_path))
    pr_norm = project_root_real.replace("\\", "/").rstrip("/")
    cand_norm = candidate.replace("\\", "/").rstrip("/")
    if cand_norm == pr_norm or cand_norm.startswith(pr_norm + "/"):
        return candidate
    return None


def _read_text_capped(path: str) -> tuple[str, bool]:
    """Read up to MAX_READ_BYTES from path. Returns (text, truncated)."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        data = f.read(MAX_READ_BYTES + 1)
    if len(data) > MAX_READ_BYTES:
        return data[:MAX_READ_BYTES], True
    return data, False


def _file_size(path: str) -> int:
    return os.path.getsize(path)


def _inspect_flag(path: str) -> dict[str, Any]:
    out: dict[str, Any] = {"exists": False}
    if not os.path.exists(path):
        return out
    out["exists"] = True
    try:
        st = os.stat(path)
        out["mtime"] = st.st_mtime
        out["mtime_iso"] = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)
        )
        out["size"] = st.st_size
    except OSError as e:
        out["anomaly"] = f"stat failed: {e}"
    return out


def _inspect_json(path: str) -> dict[str, Any]:
    out: dict[str, Any] = {"exists": False}
    if not os.path.exists(path):
        return out
    out["exists"] = True
    try:
        size = _file_size(path)
        out["size"] = size
        if size > MAX_READ_BYTES:
            out["anomaly"] = f"size {size} exceeds cap {MAX_READ_BYTES}"
            return out
        st = os.stat(path)
        out["mtime"] = st.st_mtime
        out["mtime_iso"] = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)
        )
        text, truncated = _read_text_capped(path)
        if truncated:
            out["anomaly"] = "read truncated at cap"
            return out
        try:
            parsed = json.loads(text) if text.strip() else None
            out["json_valid"] = True
            if isinstance(parsed, dict):
                out["top_level_keys"] = sorted(parsed.keys())
            elif parsed is None:
                out["anomaly"] = "empty file"
        except json.JSONDecodeError as e:
            out["json_valid"] = False
            out["anomaly"] = f"JSON parse error: {e.msg} (line {e.lineno})"
    except OSError as e:
        out["anomaly"] = f"OS error: {e}"
    return out


def _inspect_epoch(path: str) -> dict[str, Any]:
    out: dict[str, Any] = {"exists": False}
    if not os.path.exists(path):
        return out
    out["exists"] = True
    try:
        st = os.stat(path)
        out["mtime"] = st.st_mtime
        out["size"] = st.st_size
        if st.st_size > 64:
            out["anomaly"] = (
                f"unexpectedly large for epoch file ({st.st_size} bytes)"
            )
            return out
        text, _ = _read_text_capped(path)
        text = text.strip()
        out["raw"] = text
        try:
            value = float(text)
            out["epoch"] = value
            out["epoch_iso"] = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(value)
            )
            now = time.time()
            if value > now + 60:
                out["anomaly"] = "epoch in the future"
            elif value < now - 7 * 24 * 3600:
                out["anomaly"] = "epoch older than 7 days"
        except ValueError:
            out["anomaly"] = "content is not a numeric epoch"
    except OSError as e:
        out["anomaly"] = f"OS error: {e}"
    return out


def _inspect_jsonl_tail(path: str) -> dict[str, Any]:
    out: dict[str, Any] = {"exists": False}
    if not os.path.exists(path):
        return out
    out["exists"] = True
    try:
        size = _file_size(path)
        out["size"] = size
        with open(path, "rb") as f:
            if size > MAX_READ_BYTES:
                f.seek(-MAX_READ_BYTES, os.SEEK_END)
                out["truncated_to_tail"] = True
            chunk = f.read()
        try:
            text = chunk.decode("utf-8", errors="replace")
        except Exception as e:
            out["anomaly"] = f"decode error: {e}"
            return out
        lines = [ln for ln in text.splitlines() if ln.strip()]
        tail = lines[-OBS_TAIL_LINES:]
        out["tail_line_count"] = len(tail)
        latest_ts: float | None = None
        parsed_count = 0
        for ln in tail:
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            parsed_count += 1
            ts = rec.get("ts") if isinstance(rec, dict) else None
            if isinstance(ts, (int, float)):
                if latest_ts is None or ts > latest_ts:
                    latest_ts = ts
        out["tail_parsed"] = parsed_count
        if latest_ts is not None:
            out["latest_ts"] = latest_ts
            out["latest_ts_iso"] = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(latest_ts)
            )
            age = time.time() - latest_ts
            out["latest_age_seconds"] = age
        elif parsed_count == 0 and tail:
            out["anomaly"] = "tail lines present but none parseable as JSON"
    except OSError as e:
        out["anomaly"] = f"OS error: {e}"
    return out


_INSPECTORS = {
    "flag": _inspect_flag,
    "json": _inspect_json,
    "epoch": _inspect_epoch,
    "jsonl_tail": _inspect_jsonl_tail,
}


def diagnose(project_root: str | None = None) -> dict[str, Any]:
    """Run the read-only hook diagnostic.

    Args:
        project_root: Optional override (mainly for tests). Defaults to
            the parent directory of this file.

    Returns:
        A dict with shape:
            {
              "generated_at": <epoch>,
              "project_root": <str>,
              "entries": [
                  {"id": ..., "rel_path": ..., "kind": ..., "path": ...,
                   "abnormal": bool, "info": {...}, "error": Optional[str]},
                  ...
              ],
              "summary": {"total": N, "missing": M, "abnormal": K, "ok": O},
            }
    """
    root = project_root or _project_root()
    entries: list[dict[str, Any]] = []

    for logical_id, rel_path, kind in _TARGETS:
        entry: dict[str, Any] = {
            "id": logical_id,
            "rel_path": rel_path,
            "kind": kind,
            "path": None,
            "abnormal": False,
            "info": {},
            "error": None,
        }
        try:
            resolved = _safe_resolve(root, rel_path)
            if resolved is None:
                entry["error"] = "path resolves outside project root"
                entry["abnormal"] = True
                entries.append(entry)
                continue
            entry["path"] = resolved
            inspector = _INSPECTORS.get(kind)
            if inspector is None:
                entry["error"] = f"unknown kind: {kind}"
                entry["abnormal"] = True
                entries.append(entry)
                continue
            info = inspector(resolved)
            entry["info"] = info
            if not info.get("exists", False):
                entry["abnormal"] = True
            if "anomaly" in info:
                entry["abnormal"] = True
            if info.get("json_valid") is False:
                entry["abnormal"] = True
        except Exception as e:  # fail-open
            entry["error"] = f"unexpected error: {e!r}"
            entry["abnormal"] = True
        entries.append(entry)

    total = len(entries)
    missing = sum(1 for e in entries if not e["info"].get("exists", False))
    abnormal = sum(1 for e in entries if e["abnormal"])
    ok = total - abnormal

    return {
        "generated_at": time.time(),
        "project_root": root,
        "entries": entries,
        "summary": {
            "total": total,
            "missing": missing,
            "abnormal": abnormal,
            "ok": ok,
        },
    }


def format_report(result: dict[str, Any]) -> str:
    """Format a diagnose() result as a human-readable text report."""
    lines: list[str] = []
    lines.append("=== hook_diagnose (read-only) ===")
    gen = result.get("generated_at")
    if isinstance(gen, (int, float)):
        lines.append(
            "generated: "
            + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(gen))
        )
    lines.append(f"project_root: {result.get('project_root', '?')}")
    summary = result.get("summary", {})
    lines.append(
        "summary: total={total} ok={ok} abnormal={abnormal} missing={missing}".format(
            total=summary.get("total", 0),
            ok=summary.get("ok", 0),
            abnormal=summary.get("abnormal", 0),
            missing=summary.get("missing", 0),
        )
    )
    lines.append("")
    for entry in result.get("entries", []):
        marker = "!!" if entry.get("abnormal") else "OK"
        lines.append(
            f"[{marker}] {entry.get('id')}  ({entry.get('kind')})  "
            f"{entry.get('rel_path')}"
        )
        if entry.get("error"):
            lines.append(f"     error: {entry['error']}")
        info = entry.get("info") or {}
        if not info.get("exists", False):
            lines.append("     status: MISSING")
        else:
            mtime_iso = info.get("mtime_iso") or info.get("epoch_iso")
            size = info.get("size")
            bits = []
            if size is not None:
                bits.append(f"size={size}")
            if mtime_iso:
                bits.append(f"mtime={mtime_iso}")
            if "json_valid" in info:
                bits.append(f"json_valid={info['json_valid']}")
            if "top_level_keys" in info:
                bits.append(f"keys={info['top_level_keys']}")
            if "tail_line_count" in info:
                bits.append(f"tail_lines={info['tail_line_count']}")
            if "latest_ts_iso" in info:
                bits.append(f"latest_ts={info['latest_ts_iso']}")
            if "latest_age_seconds" in info:
                bits.append(f"age_s={info['latest_age_seconds']:.0f}")
            if bits:
                lines.append("     " + "  ".join(bits))
            if "anomaly" in info:
                lines.append(f"     anomaly: {info['anomaly']}")
    lines.append("")
    lines.append("NOTE: This tool is read-only. It does not modify any state.")
    lines.append("      Review the report and take action manually if needed.")
    return "\n".join(lines)
