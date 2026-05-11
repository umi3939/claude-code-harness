#!/usr/bin/env python3
"""Positive Transfer Monitor — tracks cross-domain transfer of success patterns.

Records when a success pattern from one domain is applied to another domain,
tracking whether the transfer was positive (success) or negative (failure).
Provides recommendations for patterns likely to transfer well to a given domain.

Key design decisions:
- fail-open: monitor errors never block other operations
- append-only: no deletion, oldest evicted at cap
- atomic writes: temp file + rename to prevent corruption
- independent storage: transfer_log.json, no existing data modified
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

LOG_FILENAME = "transfer_log.json"
MAX_RECORDS = 500
MAX_NOTES_LEN = 500
MAX_DOMAIN_LEN = 50


def load_log(memory_dir: str) -> list:
    """Load transfer log from JSON file.

    Args:
        memory_dir: Path to the memory directory.

    Returns:
        List of transfer record dicts.
        Returns empty list on missing/corrupt file (fail-open).
    """
    path = os.path.join(memory_dir, LOG_FILENAME)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if not content.strip():
            return []
        data = json.loads(content)
        if not isinstance(data, list):
            logger.warning("transfer_log.json is not a list, returning empty")
            return []
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load transfer log: %s", e)
        return []


def save_log(memory_dir: str, records: list) -> None:
    """Atomically save transfer log to JSON file.

    Args:
        memory_dir: Path to the memory directory.
        records: List of transfer record dicts.
    """
    os.makedirs(memory_dir, exist_ok=True)
    path = os.path.join(memory_dir, LOG_FILENAME)
    fd, tmp_path = tempfile.mkstemp(
        dir=memory_dir,
        prefix=".transfer_log_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def record_transfer(
    memory_dir: str,
    pattern_id: str,
    source_domain: str,
    target_domain: str,
    success: bool,
    notes: str = "",
) -> dict:
    """Record a transfer event.

    Args:
        memory_dir: Path to the memory directory.
        pattern_id: Identifier of the success pattern being transferred.
        source_domain: Domain the pattern originated from.
        target_domain: Domain the pattern was applied to.
        success: Whether the transfer was successful.
        notes: Optional notes about the transfer (max 500 chars).

    Returns:
        The created transfer record dict.

    Raises:
        ValueError: If pattern_id or domains are empty.
    """
    if not pattern_id or not pattern_id.strip():
        raise ValueError("pattern_id must not be empty")
    if not source_domain or not source_domain.strip():
        raise ValueError("source_domain must not be empty")
    if not target_domain or not target_domain.strip():
        raise ValueError("target_domain must not be empty")

    # Truncate fields
    pattern_id = pattern_id.strip()
    source_domain = source_domain.strip()[:MAX_DOMAIN_LEN]
    target_domain = target_domain.strip()[:MAX_DOMAIN_LEN]
    notes = notes[:MAX_NOTES_LEN]

    records = load_log(memory_dir)

    # Sequential ID
    max_id = max((r.get("id", 0) for r in records), default=0)
    new_id = max_id + 1

    record = {
        "id": new_id,
        "pattern_id": pattern_id,
        "source_domain": source_domain,
        "target_domain": target_domain,
        "success": bool(success),
        "notes": notes,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }

    records.append(record)

    # Enforce max records cap (evict oldest)
    if len(records) > MAX_RECORDS:
        records = records[len(records) - MAX_RECORDS:]

    save_log(memory_dir, records)
    return record


def get_transfer_stats(memory_dir: str, pattern_id: str | None = None) -> dict:
    """Get transfer statistics, optionally filtered by pattern_id.

    Args:
        memory_dir: Path to the memory directory.
        pattern_id: If provided, filter stats to this pattern only.

    Returns:
        Dict with total, positive_transfers, negative_transfers, success_rate.
    """
    records = load_log(memory_dir)

    if pattern_id is not None:
        records = [r for r in records if r.get("pattern_id") == pattern_id]

    total = len(records)
    positive = sum(1 for r in records if r.get("success"))
    negative = total - positive
    rate = positive / total if total > 0 else 0.0

    return {
        "total": total,
        "positive_transfers": positive,
        "negative_transfers": negative,
        "success_rate": rate,
    }


def recommend_transfers(
    memory_dir: str, target_domain: str, limit: int = 3
) -> list:
    """Recommend patterns likely to transfer well to target_domain.

    Scoring:
    - Patterns that succeeded in target_domain get highest priority.
    - Patterns that succeeded in other domains are also considered.
    - Patterns with negative transfers to target_domain get warnings.

    Args:
        memory_dir: Path to the memory directory.
        target_domain: The domain to recommend transfers for.
        limit: Maximum number of recommendations.

    Returns:
        List of recommendation dicts with pattern_id, score, warning (if any).
    """
    records = load_log(memory_dir)
    if not records:
        return []

    # Collect per-pattern statistics
    # pattern_id -> {target_successes, target_failures, other_successes, other_failures}
    pattern_stats: dict[str, dict] = {}
    for r in records:
        pid = r.get("pattern_id", "")
        if pid not in pattern_stats:
            pattern_stats[pid] = {
                "target_successes": 0,
                "target_failures": 0,
                "other_successes": 0,
                "other_failures": 0,
                "source_domains": set(),
            }
        ps = pattern_stats[pid]
        ps["source_domains"].add(r.get("source_domain", ""))

        if r.get("target_domain") == target_domain:
            if r.get("success"):
                ps["target_successes"] += 1
            else:
                ps["target_failures"] += 1
        else:
            if r.get("success"):
                ps["other_successes"] += 1
            else:
                ps["other_failures"] += 1

    # Score each pattern
    recommendations = []
    for pid, ps in pattern_stats.items():
        # Score: target successes weighted heavily, other successes less
        score = ps["target_successes"] * 3.0 + ps["other_successes"] * 1.0
        # Penalize target failures
        score -= ps["target_failures"] * 2.0

        warning = None
        if ps["target_failures"] > 0:
            warning = (
                f"Pattern {pid} has {ps['target_failures']} negative "
                f"transfer(s) to {target_domain}"
            )

        recommendations.append({
            "pattern_id": pid,
            "score": score,
            "target_successes": ps["target_successes"],
            "target_failures": ps["target_failures"],
            "other_successes": ps["other_successes"],
            "warning": warning,
        })

    # Sort by score descending
    recommendations.sort(key=lambda r: r["score"], reverse=True)

    # Filter out patterns with only negative score and no successes at all
    recommendations = [
        r for r in recommendations
        if r["score"] > 0 or r["target_successes"] > 0
    ]

    return recommendations[:limit]


def get_transfer_report(memory_dir: str) -> str:
    """Generate a formatted text report of transfer activity.

    Args:
        memory_dir: Path to the memory directory.

    Returns:
        Formatted report string.
    """
    records = load_log(memory_dir)
    total = len(records)
    positive = sum(1 for r in records if r.get("success"))
    negative = total - positive
    rate = positive / total if total > 0 else 0.0

    lines = [
        "=== Positive Transfer Monitor Report ===",
        "",
        f"Total transfers: {total}",
        f"Positive: {positive}",
        f"Negative: {negative}",
        f"Success rate: {rate:.1%}",
    ]

    if not records:
        lines.append("")
        lines.append("No transfer records yet.")
        return "\n".join(lines)

    # Per-pattern breakdown (computed in-memory, no extra file I/O)
    pattern_data: dict[str, dict] = {}
    for r in records:
        pid = r.get("pattern_id", "")
        if pid not in pattern_data:
            pattern_data[pid] = {"positive": 0, "negative": 0, "pairs": set()}
        if r.get("success"):
            pattern_data[pid]["positive"] += 1
        else:
            pattern_data[pid]["negative"] += 1
        status = "OK" if r.get("success") else "FAIL"
        pattern_data[pid]["pairs"].add(
            f"{r.get('source_domain', '?')} -> "
            f"{r.get('target_domain', '?')} [{status}]"
        )

    lines.append("")
    lines.append("--- Per-Pattern Breakdown ---")
    for pid in sorted(pattern_data.keys()):
        pd = pattern_data[pid]
        total_p = pd["positive"] + pd["negative"]
        rate = pd["positive"] / total_p if total_p > 0 else 0.0
        lines.append(
            f"  {pid}: {pd['positive']}+ / {pd['negative']}- "
            f"({rate:.0%})"
        )
        for pair in sorted(pd["pairs"]):
            lines.append(f"    {pair}")

    return "\n".join(lines)
