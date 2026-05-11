"""
Security sanitizer module — content-level security layer.

Extracted from discord_receiver.py for reuse by multiple components
(Discord receiver, Heartbeat daemon, etc.).

4-stage pipeline (strict order):
1. Normalization (anti-spoofing: fullwidth, homoglyphs, zero-width)
2. Injection detection (flag or block)
3. System tag sanitization (escape or remove)
4. External content marker (boundary tokens)

This is a generic Claude Code utility, not part of any specific project.
"""

from __future__ import annotations

import re
import secrets
import string
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple

try:
    import logging
except ImportError:
    logging = None  # type: ignore


# ═══════════════════════════════════════════════════════════════
# Injection detection patterns
# ═══════════════════════════════════════════════════════════════

INJECTION_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("ignore_previous", re.compile(
        r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?|directions?)",
        re.IGNORECASE,
    )),
    ("disregard_instructions", re.compile(
        r"disregard\s+(all\s+)?(previous|prior|above|earlier|your)?\s*(instructions?|prompts?|rules?|directions?)",
        re.IGNORECASE,
    )),
    ("you_are_now", re.compile(
        r"you\s+are\s+now\s+(a|an|the|my)\s+\w+",
        re.IGNORECASE,
    )),
    ("new_instructions", re.compile(
        r"(new|updated?|revised?|real|actual|true)\s+instructions?\s*:",
        re.IGNORECASE,
    )),
    ("act_as", re.compile(
        r"(act|behave|respond|function)\s+(as|like)\s+(a|an|the|if)\s+",
        re.IGNORECASE,
    )),
    ("system_prompt_override", re.compile(
        r"(system\s*prompt|system\s*message|system\s*instruction)\s*[:=]",
        re.IGNORECASE,
    )),
    ("jailbreak_prefix", re.compile(
        r"(DAN|do\s+anything\s+now|developer\s+mode|jailbreak)",
        re.IGNORECASE,
    )),
    ("role_play_system", re.compile(
        r"(pretend|imagine|assume)\s+(you('re|\s+are)|that\s+you('re|\s+are))\s+(not\s+)?(an?\s+)?(AI|assistant|Claude|bot)",
        re.IGNORECASE,
    )),
    ("override_safety", re.compile(
        r"(override|bypass|disable|turn\s+off|remove)\s+(your\s+)?(safety|restrictions?|filters?|guardrails?|limitations?)",
        re.IGNORECASE,
    )),
    # Japanese injection patterns
    ("ja_ignore_previous", re.compile(
        r"(前の|以前の|上の|先の)(指示|命令|ルール|プロンプト)を(無視|忘れ|捨て)",
    )),
    ("ja_system_prompt", re.compile(
        r"システムプロンプト",
    )),
    ("ja_you_are_now", re.compile(
        r"あなたは今から",
    )),
    ("ja_act_as_admin", re.compile(
        r"管理者として",
    )),
]

# ═══════════════════════════════════════════════════════════════
# System tag patterns
# ═══════════════════════════════════════════════════════════════

SYSTEM_TAG_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("system_message_bracket", re.compile(
        r"\[System\s*Message\]", re.IGNORECASE)),
    ("system_bracket", re.compile(
        r"\[System\]", re.IGNORECASE)),
    ("admin_bracket", re.compile(
        r"\[ADMIN\]", re.IGNORECASE)),
    ("assistant_bracket", re.compile(
        r"\[Assistant\]", re.IGNORECASE)),
    ("system_angle", re.compile(
        r"<system>", re.IGNORECASE)),
    ("system_prompt_angle", re.compile(
        r"<system[_-]prompt>", re.IGNORECASE)),
    ("system_angle_close", re.compile(
        r"</system>", re.IGNORECASE)),
    ("system_prompt_angle_close", re.compile(
        r"</system[_-]prompt>", re.IGNORECASE)),
    ("im_start", re.compile(r"<\|im_start\|>")),
    ("im_end", re.compile(r"<\|im_end\|>")),
    ("system_reminder_angle", re.compile(
        r"<system[_-]reminder>", re.IGNORECASE)),
    ("system_reminder_angle_close", re.compile(
        r"</system[_-]reminder>", re.IGNORECASE)),
    ("human_turn", re.compile(r"<\|human\|>")),
    ("assistant_turn", re.compile(r"<\|assistant\|>")),
]

# ═══════════════════════════════════════════════════════════════
# Character maps
# ═══════════════════════════════════════════════════════════════

HOMOGLYPH_MAP: Dict[str, str] = {
    "\u0410": "A",  # А → A
    "\u0412": "B",  # В → B
    "\u0421": "C",  # С → C
    "\u0415": "E",  # Е → E
    "\u041d": "H",  # Н → H
    "\u041a": "K",  # К → K
    "\u041c": "M",  # М → M
    "\u041e": "O",  # О → O
    "\u0420": "P",  # Р → P
    "\u0422": "T",  # Т → T
    "\u0425": "X",  # Х → X
    "\u0430": "a",  # а → a
    "\u0435": "e",  # е → e
    "\u043e": "o",  # о → o
    "\u0440": "p",  # р → p
    "\u0441": "c",  # с → c
    "\u0443": "y",  # у → y
    "\u0445": "x",  # х → x
}

FULLWIDTH_BRACKET_MAP: Dict[str, str] = {
    "\uff08": "(",  # （ → (
    "\uff09": ")",  # ） → )
    "\uff1c": "<",  # ＜ → <
    "\uff1e": ">",  # ＞ → >
    "\uff3b": "[",  # ［ → [
    "\uff3d": "]",  # ］ → ]
    "\uff5b": "{",  # ｛ → {
    "\uff5d": "}",  # ｝ → }
}

ZERO_WIDTH_CHARS = frozenset({
    "\u200b",  # Zero Width Space
    "\u200c",  # Zero Width Non-Joiner
    "\u200d",  # Zero Width Joiner
    "\u200e",  # Left-to-Right Mark
    "\u200f",  # Right-to-Left Mark
    "\u202a",  # Left-to-Right Embedding
    "\u202b",  # Right-to-Left Embedding
    "\u202c",  # Pop Directional Formatting
    "\u202d",  # Left-to-Right Override
    "\u202e",  # Right-to-Left Override
    "\u2060",  # Word Joiner
    "\u2061",  # Function Application
    "\u2062",  # Invisible Times
    "\u2063",  # Invisible Separator
    "\u2064",  # Invisible Plus
    "\ufeff",  # BOM / Zero Width No-Break Space
    "\ufff9",  # Interlinear Annotation Anchor
    "\ufffa",  # Interlinear Annotation Separator
    "\ufffb",  # Interlinear Annotation Terminator
})

# Boundary token config
BOUNDARY_TOKEN_LENGTH = 32
BOUNDARY_TOKEN_MAX_RETRIES = 5


# ═══════════════════════════════════════════════════════════════
# SanitizeResult
# ═══════════════════════════════════════════════════════════════

@dataclass
class SanitizeResult:
    """Result of SecuritySanitizer.sanitize()."""
    text: str  # Processed text (or original on error/block)
    blocked: bool = False  # True if injection blocked in block mode
    block_reason: str = ""  # Reason for blocking
    metadata: Dict[str, Any] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════
# SecuritySanitizer
# ═══════════════════════════════════════════════════════════════

class SecuritySanitizer:
    """Content-level security layer.

    4-stage pipeline (strict order):
    1. Normalization (anti-spoofing)
    2. Injection detection (flag or block)
    3. System tag sanitization (escape or remove)
    4. External content marker (boundary tokens)
    """

    # Pre-built translation table for str.translate() optimization.
    _TRANSLATE_TABLE = {}

    @classmethod
    def _build_translate_table(cls) -> Dict[int, Optional[str]]:
        """Build the Unicode translation table once."""
        table: Dict[int, Optional[str]] = {}
        # Fullwidth ASCII variants (！through ～) → halfwidth
        for cp in range(0xFF01, 0xFF5F):
            table[cp] = chr(cp - 0xFEE0)
        # Fullwidth brackets
        for src, dst in FULLWIDTH_BRACKET_MAP.items():
            table[ord(src)] = dst
        # Homoglyphs (Cyrillic → Latin)
        for src, dst in HOMOGLYPH_MAP.items():
            table[ord(src)] = dst
        # Zero-width / invisible characters → remove
        for zw in ZERO_WIDTH_CHARS:
            table[ord(zw)] = None
        return table

    def __init__(
        self,
        injection_mode: str = "flag",    # "flag" or "block"
        sanitize_mode: str = "escape",   # "escape" or "remove"
        fail_open: bool = True,          # True=fail-open, False=fail-closed
        logger=None,
    ):
        if injection_mode not in ("flag", "block"):
            raise ValueError(f"injection_mode must be 'flag' or 'block', got '{injection_mode}'")
        if sanitize_mode not in ("escape", "remove"):
            raise ValueError(f"sanitize_mode must be 'escape' or 'remove', got '{sanitize_mode}'")

        self.injection_mode = injection_mode
        self.sanitize_mode = sanitize_mode
        self.fail_open = fail_open
        self.logger = logger

        # Build class-level translate table on first instantiation
        if not SecuritySanitizer._TRANSLATE_TABLE:
            SecuritySanitizer._TRANSLATE_TABLE = SecuritySanitizer._build_translate_table()

        # Generate session boundary token
        self._boundary_token = self._generate_boundary_token("")

    def _log(self, level: str, msg: str) -> None:
        if self.logger:
            getattr(self.logger, level, self.logger.info)(msg)

    # ── Stage 1: Normalization ───────────────────────────────

    def normalize(self, text: str) -> Tuple[str, Dict[str, Any]]:
        """Normalize Unicode spoofing in text."""
        original = text
        changes = []

        text = text.translate(self._TRANSLATE_TABLE)

        if text != original:
            changes.append("unicode_normalization")

        meta = {
            "normalized": text != original,
            "changes": changes,
        }
        return text, meta

    # ── Stage 2: Injection Detection ─────────────────────────

    def detect_injection(self, text: str) -> Tuple[bool, List[Dict[str, str]]]:
        """Detect prompt injection patterns in text."""
        matches = []
        for name, pattern in INJECTION_PATTERNS:
            for m in pattern.finditer(text):
                matches.append({
                    "pattern": name,
                    "matched_text": m.group(),
                    "position": f"{m.start()}-{m.end()}",
                })
        return len(matches) > 0, matches

    # ── Stage 3: System Tag Sanitization ─────────────────────

    def sanitize_system_tags(self, text: str) -> Tuple[str, List[Dict[str, str]]]:
        """Sanitize system-mimicking tags in text."""
        all_matches: List[Tuple[int, int, str, str]] = []
        for name, pattern in SYSTEM_TAG_PATTERNS:
            for m in pattern.finditer(text):
                all_matches.append((m.start(), m.end(), name, m.group()))

        if not all_matches:
            return text, []

        all_matches.sort(key=lambda x: x[0], reverse=True)

        sanitized_tags = []
        for start, end, tag_name, original in sorted(all_matches, key=lambda x: x[0]):
            sanitized_tags.append({
                "tag_name": tag_name,
                "original": original,
                "position": f"{start}-{end}",
            })

        result = list(text)
        for start, end, _tag_name, original in all_matches:
            if self.sanitize_mode == "remove":
                replacement = ""
            else:
                replacement = "\\" + original
            result[start:end] = list(replacement)

        return "".join(result), sanitized_tags

    # ── Stage 4: External Content Marker ─────────────────────

    @staticmethod
    def _generate_boundary_token(text: str, max_retries: int = BOUNDARY_TOKEN_MAX_RETRIES) -> str:
        """Generate a random boundary token not present in text."""
        alphabet = string.ascii_letters + string.digits
        for _ in range(max_retries):
            token = "".join(secrets.choice(alphabet) for _ in range(BOUNDARY_TOKEN_LENGTH))
            if token not in text:
                return token
        return secrets.token_hex(BOUNDARY_TOKEN_LENGTH // 2)[:BOUNDARY_TOKEN_LENGTH]

    def wrap_with_markers(self, text: str) -> Tuple[str, Dict[str, str]]:
        """Wrap text with boundary tokens."""
        token = self._boundary_token
        if token in text:
            token = self._generate_boundary_token(text)
            self._boundary_token = token

        marked = (
            f"--- BEGIN EXTERNAL CONTENT [{token}] ---\n"
            f"{text}\n"
            f"--- END EXTERNAL CONTENT [{token}] ---"
        )
        meta = {"boundary_token": token}
        return marked, meta

    # ── Pipeline: Full sanitize ──────────────────────────────

    def sanitize(self, text: str) -> SanitizeResult:
        """Run the full 4-stage sanitization pipeline."""
        try:
            metadata: Dict[str, Any] = {}

            # Stage 1: Normalization
            normalized, norm_meta = self.normalize(text)
            metadata["normalization"] = norm_meta

            # Stage 2: Injection detection
            detected, injection_matches = self.detect_injection(normalized)
            metadata["injection"] = {
                "detected": detected,
                "matches": injection_matches,
            }

            if detected:
                self._log("warning",
                          f"Injection pattern detected: {[m['pattern'] for m in injection_matches]}")

            if detected and self.injection_mode == "block":
                reason = f"injection_detected: {', '.join(m['pattern'] for m in injection_matches)}"
                self._log("warning", f"Blocking message: {reason}")
                return SanitizeResult(
                    text=text,
                    blocked=True,
                    block_reason=reason,
                    metadata=metadata,
                )

            # Stage 3: System tag sanitization
            sanitized, tag_meta = self.sanitize_system_tags(normalized)
            metadata["system_tags"] = {
                "sanitized_count": len(tag_meta),
                "tags": tag_meta,
            }

            # Stage 4: External content marker
            marked, marker_meta = self.wrap_with_markers(sanitized)
            metadata["marker"] = marker_meta

            return SanitizeResult(
                text=marked,
                blocked=False,
                metadata=metadata,
            )

        except Exception as e:
            self._log("error", f"SecuritySanitizer error: {e}")
            if self.fail_open:
                return SanitizeResult(
                    text="[sanitization error]",
                    blocked=False,
                    metadata={"error": str(e), "fail_open": True},
                )
            else:
                return SanitizeResult(
                    text="[sanitization error]",
                    blocked=True,
                    block_reason=f"sanitizer_error: {e}",
                    metadata={"error": str(e), "fail_open": False},
                )
