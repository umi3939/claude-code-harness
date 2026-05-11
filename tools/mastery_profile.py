#!/usr/bin/env python3
"""Mastery Experience Tracker — domain-level growth trajectory tracking.

Aggregates success/failure events by capability domain, computing mastery scores,
trends, and identifying strengths and growth areas.

Key design decisions:
- fail-open: profile errors never block other operations
- atomic writes: temp file + rename to prevent corruption
- independent storage: mastery_profile.json, no existing data modified
- no auto-classification: domain is explicitly specified by caller
"""

import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

PROFILE_FILENAME = "mastery_profile.json"
MAX_DOMAIN_LEN = 100
MAX_APPROACH_LEN = 500
MAX_DOMAINS = 50
MAX_RECENT_RESULTS = 10
MIN_TOTAL_FOR_SCORE = 3
TREND_THRESHOLD = 0.2

# G58 prompt-injection defense: characters disallowed in domain names and
# scrubbed from legacy output. Must cover every code point that can forge a
# newline or a role-marker seam when the text is injected into Claude's
# context. A single regex backs both input validation (_sanitize_domain)
# and output scrubbing (_scrub_control_chars) — keeping them in lock-step so
# a bypass cannot exist in one layer but not the other.
#
# Coverage:
#   \x00-\x1f       C0 controls incl. LF, CR, NUL, TAB, ESC
#   \x7f            DEL
#   \x80-\x9f       C1 controls incl. U+0085 NEL (line break in Unicode-aware
#                   parsers and some terminals)
#   \u2028          LINE SEPARATOR
#   \u2029          PARAGRAPH SEPARATOR
#   \u200b-\u200f   zero-width space/non-joiner/joiner + LTR/RTL marks
#   \u202a-\u202e   bidi embedding/override (can visually reorder text)
#   \u2066-\u2069   bidi isolates
#   \ufeff          BOM / zero-width no-break space
_DISALLOWED_CHARS_RE = re.compile(
    r"[\x00-\x1f\x7f\x80-\x9f"
    r"\u2028\u2029"
    r"\u200b-\u200f\u202a-\u202e\u2066-\u2069"
    r"\ufeff"
    r"]"
)
# Aliases kept for readability at call sites. Both point at the exact same
# pattern so input-rejection and output-scrubbing can never diverge.
_DISALLOWED_DOMAIN_CHARS_RE = _DISALLOWED_CHARS_RE
_CONTROL_CHARS_RE = _DISALLOWED_CHARS_RE


def _sanitize_domain(domain: str) -> str:
    """Validate a domain name for safe storage and context injection.

    Rejects (raises ValueError) any domain that:
    - is not a string
    - is empty after stripping whitespace
    - exceeds MAX_DOMAIN_LEN characters
    - contains any disallowed character: C0/C1 controls (\\x00-\\x1f,
      \\x80-\\x9f), DEL (\\x7f), Unicode line/paragraph separators
      (U+2028, U+2029), zero-width marks (U+200B-U+200F), bidi
      embedding/override/isolate controls (U+202A-U+202E, U+2066-U+2069),
      or BOM (U+FEFF).

    Why fail-closed at input: a domain string flows into context-injection
    text via get_mastery_summary(); any of the above can forge a newline
    seam or visually confuse Claude into parsing fake [SYSTEM] / role
    markers as instructions.

    Returns the (unchanged) domain when valid.
    """
    if not isinstance(domain, str):
        raise ValueError(f"domain must be str, got {type(domain).__name__}")
    if not domain or not domain.strip():
        raise ValueError("domain must be a non-empty string")
    if len(domain) > MAX_DOMAIN_LEN:
        raise ValueError(
            f"domain exceeds max length {MAX_DOMAIN_LEN} (got {len(domain)})"
        )
    if _DISALLOWED_DOMAIN_CHARS_RE.search(domain):
        raise ValueError(
            "domain contains disallowed control characters "
            "(C0/C1/DEL, Unicode line/paragraph separators, "
            "zero-width marks, bidi overrides, or BOM)"
        )
    return domain


def _scrub_control_chars(text: str) -> str:
    """Replace C0/C1 control chars (incl. \\n, \\r, NUL, \\x7f) with a single space.

    Defense-in-depth for legacy mastery_profile.json entries that may have been
    written before _sanitize_domain existed. Used on the output side of
    get_mastery_summary so context injection can never carry control chars,
    regardless of how the on-disk data got there.
    """
    if not isinstance(text, str):
        return ""
    return _CONTROL_CHARS_RE.sub(" ", text)


def load_profile(memory_dir: str) -> dict:
    """Load mastery profile from JSON file.

    Args:
        memory_dir: Path to the memory directory.

    Returns:
        Dict of domain -> stats. Empty dict on missing/corrupt file (fail-open).
    """
    path = os.path.join(memory_dir, PROFILE_FILENAME)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if not content.strip():
            return {}
        data = json.loads(content)
        if not isinstance(data, dict):
            logger.warning("mastery_profile.json is not a dict, returning empty")
            return {}
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load mastery profile: %s", e)
        return {}


def save_profile(memory_dir: str, profile: dict) -> None:
    """Atomically save mastery profile to JSON file.

    Args:
        memory_dir: Path to the memory directory.
        profile: Dict of domain -> stats.
    """
    os.makedirs(memory_dir, exist_ok=True)
    path = os.path.join(memory_dir, PROFILE_FILENAME)
    fd, tmp_path = tempfile.mkstemp(
        dir=memory_dir,
        prefix=".mastery_profile_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _compute_trend(recent_results: list) -> str:
    """Compute trend from recent results.

    Compares success rate of latter half vs first half.
    Returns 'improving', 'declining', or 'stable'.
    """
    if len(recent_results) < MAX_RECENT_RESULTS:
        return "stable"
    mid = len(recent_results) // 2
    first_half = recent_results[:mid]
    second_half = recent_results[mid:]
    first_rate = sum(first_half) / len(first_half) if first_half else 0
    second_rate = sum(second_half) / len(second_half) if second_half else 0
    diff = second_rate - first_rate
    if diff > TREND_THRESHOLD:
        return "improving"
    if diff < -TREND_THRESHOLD:
        return "declining"
    return "stable"


def update_mastery(
    memory_dir: str,
    domain: str,
    success: bool,
    approach: str = "",
) -> dict:
    """Update mastery data for a domain.

    Args:
        memory_dir: Path to the memory directory.
        domain: Capability domain name. Must be a non-empty string up to
            MAX_DOMAIN_LEN chars with no control characters (G58 defense).
        success: True for success, False for failure.
        approach: Description of approach used (truncated to MAX_APPROACH_LEN
            chars, recorded on success only).

    Returns:
        The updated domain stats dict.

    Raises:
        ValueError: If domain is invalid (empty, too long, contains control
            chars, or wrong type), or if adding a new domain would exceed the
            MAX_DOMAINS limit.
    """
    # G58: fail-closed at the input boundary. Reject control chars / newlines
    # so they cannot reach context-injection via get_mastery_summary().
    domain = _sanitize_domain(domain)
    # G58 re-fix: approach flows into best_approach → generate_report() →
    # mastery_report MCP tool → Claude context, so it needs the same scrub
    # as domain. Fail-open (scrub, not reject) to keep growth_recorder's
    # auto-logged approach strings resilient. Scrub BEFORE truncate so a
    # control char landing at byte 500 cannot survive truncation.
    if approach:
        approach = _scrub_control_chars(approach)[:MAX_APPROACH_LEN]
    else:
        approach = ""

    profile = load_profile(memory_dir)

    # Domain limit check for new domains
    if domain not in profile and len(profile) >= MAX_DOMAINS:
        raise ValueError(
            f"domain limit reached ({MAX_DOMAINS}). "
            f"Cannot add new domain: {domain!r}"
        )

    # Get or create domain entry
    entry = profile.get(domain, {})
    success_count = entry.get("success_count", 0)
    total_count = entry.get("total_count", 0)
    recent_results = entry.get("recent_results", [])
    best_approach = entry.get("best_approach", "")

    # Update counts
    total_count += 1
    if success:
        success_count += 1

    # Update recent results (fixed-length 10)
    recent_results.append(success)
    if len(recent_results) > MAX_RECENT_RESULTS:
        recent_results = recent_results[-MAX_RECENT_RESULTS:]

    # Compute mastery score
    mastery_score = None
    if total_count >= MIN_TOTAL_FOR_SCORE:
        mastery_score = success_count / total_count

    # Compute trend
    trend = _compute_trend(recent_results)

    # Update best approach (only on success)
    if success and approach:
        best_approach = approach

    entry = {
        "success_count": success_count,
        "total_count": total_count,
        "mastery_score": mastery_score,
        "trend": trend,
        "best_approach": best_approach,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "recent_results": recent_results,
    }

    profile[domain] = entry
    save_profile(memory_dir, profile)
    return entry


TREND_JP = {
    "improving": "改善中",
    "declining": "低下中",
    "stable": "安定",
}

MASTERY_SUMMARY_MAX_LEN = 100


def get_mastery_summary(memory_dir: str, n: int = 3) -> str:
    """Get a short mastery summary string for context injection.

    Returns top N domains as "domain:score(trend_jp)" joined by space.
    Truncated to 100 characters. Returns empty string if no scored domains.

    Args:
        memory_dir: Path to the memory directory.
        n: Number of top domains to include.

    Returns:
        Summary string (max 100 chars), or empty string.
    """
    try:
        strengths = get_strengths(memory_dir, n=n)
    except Exception:
        return ""
    if not strengths:
        return ""

    parts = []
    for s in strengths:
        # G58 defense-in-depth: scrub control chars from legacy data so context
        # injection cannot carry forged [SYSTEM] markers even if old entries
        # bypassed input validation. Every field that reaches the output
        # string must be scrubbed — including the unknown-trend fallback
        # path (TREND_JP.get returns the raw trend for unrecognized values).
        domain = _scrub_control_chars(s.get("domain", ""))
        score = s.get("mastery_score", 0)
        trend = s.get("trend", "stable")
        trend_jp = _scrub_control_chars(TREND_JP.get(trend, trend))
        # Format score: use g to avoid trailing zeros, but ensure at least 1 decimal
        score_str = f"{score:.2f}".rstrip("0")
        if score_str.endswith("."):
            score_str += "0"
        parts.append(f"{domain}:{score_str}({trend_jp})")

    result = " ".join(parts)
    # Final scrub on the joined result — paranoid but cheap.
    result = _scrub_control_chars(result)
    if len(result) > MASTERY_SUMMARY_MAX_LEN:
        result = result[:MASTERY_SUMMARY_MAX_LEN]
    return result


def get_strengths(memory_dir: str, n: int = 5) -> list:
    """Get top N domains by mastery score.

    Args:
        memory_dir: Path to the memory directory.
        n: Number of top domains to return.

    Returns:
        List of dicts with 'domain' and domain stats, sorted by score descending.
        Domains with score=None are excluded.
    """
    profile = load_profile(memory_dir)
    scored = []
    for domain, stats in profile.items():
        score = stats.get("mastery_score")
        if score is not None:
            scored.append({"domain": domain, **stats})
    scored.sort(key=lambda x: x.get("mastery_score", 0), reverse=True)
    return scored[:n]


def get_growth_areas(memory_dir: str, n: int = 5) -> list:
    """Get bottom N domains by mastery score.

    Args:
        memory_dir: Path to the memory directory.
        n: Number of bottom domains to return.

    Returns:
        List of dicts with 'domain' and domain stats, sorted by score ascending.
        Domains with score=None are excluded.
    """
    profile = load_profile(memory_dir)
    scored = []
    for domain, stats in profile.items():
        score = stats.get("mastery_score")
        if score is not None:
            scored.append({"domain": domain, **stats})
    scored.sort(key=lambda x: x.get("mastery_score", 0))
    return scored[:n]


def generate_report(memory_dir: str) -> str:
    """Generate a formatted mastery profile report.

    Args:
        memory_dir: Path to the memory directory.

    Returns:
        Formatted text report with strengths, growth areas, and overall stats.
    """
    profile = load_profile(memory_dir)
    lines = ["=== Mastery Profile Report ===", ""]

    total_domains = len(profile)
    lines.append(f"Overall: {total_domains} domains tracked")
    lines.append("")

    strengths = get_strengths(memory_dir, n=5)
    lines.append("--- Strengths (top 5) ---")
    if strengths:
        for s in strengths:
            score = s.get("mastery_score", 0)
            # G58: scrub every field read from legacy on-disk data before
            # emitting it into the report. Even though new writes go through
            # _sanitize_domain/_scrub_control_chars, pre-existing entries
            # may carry exotic code points.
            domain = _scrub_control_chars(s.get("domain", ""))
            trend = _scrub_control_chars(s.get("trend", "stable"))
            lines.append(f"  {domain}: {score:.1%} ({trend})")
            approach = s.get("best_approach")
            if approach:
                lines.append(
                    f"    Best approach: "
                    f"{_scrub_control_chars(approach)[:120]}"
                )
    else:
        lines.append("  (none yet)")
    lines.append("")

    growth = get_growth_areas(memory_dir, n=5)
    lines.append("--- Growth Areas (bottom 5) ---")
    if growth:
        for g in growth:
            score = g.get("mastery_score", 0)
            domain = _scrub_control_chars(g.get("domain", ""))
            trend = _scrub_control_chars(g.get("trend", "stable"))
            lines.append(f"  {domain}: {score:.1%} ({trend})")
    else:
        lines.append("  (none yet)")

    return "\n".join(lines)
