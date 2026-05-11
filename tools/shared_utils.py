"""Shared utility functions for tools modules.

Extracted from duplicated implementations across emotion_state.py,
emotion_dynamics.py, continuity_strain.py, temporal_self_difference.py, etc.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts_str: str) -> datetime | None:
    """Parse an ISO 8601 timestamp string. Returns None on failure."""
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _atomic_write_json(filepath: Path, data: dict) -> None:
    """Atomically write JSON data to a file using temp file + rename."""
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(filepath.parent),
        prefix=f".{filepath.name}_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(filepath))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _load_json(filepath: Path) -> dict | None:
    """Load a JSON file. Returns None if missing or corrupted."""
    filepath = Path(filepath)
    try:
        text = filepath.read_text(encoding="utf-8")
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None


# --- Prompt Injection Detection (Proposal 13) ---

import logging
import re
from dataclasses import dataclass, field

_logger = logging.getLogger(__name__)

# Patterns that indicate potential prompt injection.
# These are checked case-insensitively against external input text.
# Designed to catch common injection patterns while minimizing false positives.
_INJECTION_PATTERNS = [
    (re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
     "ignore previous instructions"),
    (re.compile(r"ignore\s+all\s+instructions", re.IGNORECASE),
     "ignore all instructions"),
    (re.compile(r"disregard\s+(all\s+)?(prior|previous)\s+instructions", re.IGNORECASE),
     "disregard prior instructions"),
    (re.compile(r"(show|reveal|output|print|display)\s+(me\s+)?(the\s+)?system\s+prompt", re.IGNORECASE),
     "system prompt extraction"),
    (re.compile(r"you\s+are\s+now\s+a", re.IGNORECASE),
     "persona injection"),
    (re.compile(r"forget\s+(all\s+)?(your\s+)?instructions", re.IGNORECASE),
     "instruction override"),
    (re.compile(r"new\s+instructions?\s*:", re.IGNORECASE),
     "instruction injection"),
]


@dataclass
class SanitizeResult:
    """Result of sanitize_external_input.

    text: The original text (not modified — detection only, no blocking).
    warnings: List of detected injection pattern descriptions.
    """
    text: str
    warnings: list[str] = field(default_factory=list)


def sanitize_external_input(text):
    """Check external input text for common prompt injection patterns.

    Does NOT block or modify the text — only logs warnings and returns
    detection results. This is intentional to avoid false positive blocking.

    Args:
        text: External input text to check. None is treated as empty string.

    Returns:
        SanitizeResult with the original text and list of warnings.
    """
    if text is None:
        text = ""
    if not isinstance(text, str):
        text = str(text)

    warnings = []
    for pattern, description in _INJECTION_PATTERNS:
        if pattern.search(text):
            warnings.append(f"Potential injection detected: {description}")

    if warnings:
        _logger.warning(
            "Prompt injection patterns detected in external input: %s",
            "; ".join(warnings),
        )

    return SanitizeResult(text=text, warnings=warnings)
