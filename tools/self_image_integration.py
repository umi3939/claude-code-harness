#!/usr/bin/env python3
"""Self-image integration module for Claude Code MCP.

Integrates three observation systems (self_model, temporal_self_difference,
continuity_strain) into a unified, provisional self-image.

Design: design_self_image_integration_mcp.md

Key principles:
- Completely stateless: no persistence, no accumulation
- READ-ONLY: never writes to any observed system
- Abstract descriptions only: no raw numbers exposed
- No evaluation: no "good"/"bad"/"healthy"/"abnormal" judgments
- No assertion: uses "appears to be" / "seems like" language only
- Regenerated every call: never fixed or saved
"""

import os
import sys

# Add the tools directory to sys.path
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import self_model
import temporal_self_difference
import continuity_strain


# --- EmotionalTone ---

def _determine_emotional_tone(self_obs: dict | None) -> str:
    """Determine emotional tone from self_model observation.

    Returns: calm, stirred, mixed, intense, muted, or undefined
    """
    if self_obs is None:
        return "undefined"

    emo = self_obs.get("emotion", {})
    categories = [
        emo.get("fulfillment", "neutral"),
        emo.get("tension", "neutral"),
        emo.get("affinity", "neutral"),
    ]
    phase = emo.get("dynamics_phase", "normal")

    all_neutral = all(c == "neutral" for c in categories)
    has_strong = any(
        c in ("strongly_positive", "strongly_negative") for c in categories
    )
    unique_categories = len(set(categories))
    has_non_neutral = any(c != "neutral" for c in categories)

    if all_neutral and phase == "normal":
        return "calm"
    if has_strong and phase == "peak":
        return "intense"
    if unique_categories >= 3:
        return "mixed"
    if has_non_neutral and phase == "normal":
        return "stirred"
    if all_neutral and phase != "normal":
        return "stirred"
    return "muted"


# --- TendencyHint ---

def _determine_tendency_hint(self_obs: dict | None) -> str:
    """Determine tendency hint from self_model change trends.

    Returns: none_apparent, slight_inclination, forming_pattern,
             established_way, or undefined
    """
    if self_obs is None:
        return "undefined"

    chg = self_obs.get("change", {})
    trends = chg.get("trends", {})

    if not trends:
        return "undefined"

    directional_count = sum(
        1 for t in trends.values() if t in ("rising", "falling")
    )

    if directional_count == 0:
        return "none_apparent"
    if directional_count == 1:
        return "slight_inclination"
    if directional_count == 2:
        return "forming_pattern"
    return "established_way"


# --- StabilityFeeling ---

def _determine_stability_feeling(
    self_obs: dict | None,
    strain_result: dict | None,
) -> str:
    """Determine stability feeling from dynamics phase, change frequency,
    and continuity strain level.

    Returns: grounded, mostly_settled, wavering, turbulent, or undefined
    """
    strain_level = None
    if strain_result is not None:
        strain_level = strain_result.get("level", "at_ease")

    # Strain takes precedence
    if strain_level == "alienated":
        return "turbulent"

    phase = None
    frequency = None
    if self_obs is not None:
        emo = self_obs.get("emotion", {})
        phase = emo.get("dynamics_phase", "normal")
        chg = self_obs.get("change", {})
        frequency = chg.get("frequency", "none")

    if strain_level == "dissonant":
        return "wavering"
    if phase == "rebound":
        return "wavering"
    if strain_level == "unsettled":
        return "mostly_settled"
    if frequency == "high":
        return "mostly_settled"

    if phase is not None and frequency is not None and strain_level is not None:
        if phase == "normal" and frequency in ("none", "low", "moderate") and strain_level == "at_ease":
            return "grounded"
        return "mostly_settled"

    if phase is not None and frequency is not None:
        if phase == "normal" and frequency in ("none", "low", "moderate"):
            return "grounded"
        return "mostly_settled"

    if strain_level is not None:
        if strain_level == "at_ease":
            return "grounded"
        return "mostly_settled"

    if self_obs is None and strain_result is None:
        return "undefined"

    return "mostly_settled"


# --- ChangePresence ---

def _determine_change_presence(diff_result: dict | None) -> str:
    """Determine change presence from temporal self-difference magnitude.

    Returns: no_change_sensed, subtle_shift, noticeable_change,
             significant_shift, or undefined
    """
    if diff_result is None:
        return "undefined"

    magnitude = diff_result.get("magnitude", "none")

    mapping = {
        "none": "no_change_sensed",
        "minimal": "subtle_shift",
        "noticeable": "noticeable_change",
        "significant": "significant_shift",
        "substantial": "significant_shift",
    }
    return mapping.get(magnitude, "no_change_sensed")


# --- ContinuityFeeling ---

def _determine_continuity_feeling(
    strain_result: dict | None,
    diff_result: dict | None,
) -> str:
    """Determine continuity feeling from strain level and difference.

    Returns: continuous, mostly_familiar, somewhat_different,
             disconnected, or undefined
    """
    # Primary: strain state
    if strain_result is not None:
        level = strain_result.get("level", "at_ease")
        strain_present = strain_result.get("strain_present", False)

        if not strain_present and level == "at_ease":
            # No strain, check diff as secondary
            pass
        else:
            mapping = {
                "at_ease": "continuous",
                "unsettled": "mostly_familiar",
                "dissonant": "somewhat_different",
                "alienated": "disconnected",
            }
            return mapping.get(level, "continuous")

    # Secondary or fallback: difference
    if diff_result is not None:
        has_diff = diff_result.get("has_difference", False)
        magnitude = diff_result.get("magnitude", "none")

        if has_diff and magnitude in ("significant", "substantial"):
            return "somewhat_different"
        if has_diff:
            return "mostly_familiar"
        return "continuous"

    if strain_result is not None:
        # strain was at_ease, no diff available
        return "continuous"

    return "undefined"


# --- OverallImpression ---

def _determine_overall_impression(
    emotional_tone: str,
    tendency_hint: str,
    stability_feeling: str,
    change_presence: str,
    continuity_feeling: str,
) -> str:
    """Determine overall impression from the five facets.

    Returns: settled, active, transitional, uncertain, conflicted, or undefined
    """
    undefined_count = sum([
        emotional_tone == "undefined",
        stability_feeling == "undefined",
        continuity_feeling == "undefined",
    ])

    if undefined_count >= 2:
        return "undefined"

    if emotional_tone == "mixed" or continuity_feeling == "disconnected":
        return "conflicted"

    if change_presence in ("noticeable_change", "significant_shift") or stability_feeling == "turbulent":
        return "transitional"

    if stability_feeling == "wavering" or continuity_feeling == "somewhat_different":
        return "uncertain"

    if emotional_tone in ("stirred", "intense") or tendency_hint in ("forming_pattern", "established_way"):
        return "active"

    return "settled"


# --- Contradiction Detection ---

_CONTRADICTION_PAIRS = [
    (
        lambda et, sf, cp, cf: et == "calm" and sf == "turbulent",
        "感情は穏やかに見えるが、内的には不安定な印象がある",
    ),
    (
        lambda et, sf, cp, cf: cp == "no_change_sensed" and cf == "disconnected",
        "変化は感じられないのに、過去の自分との断絶感がある",
    ),
    (
        lambda et, sf, cp, cf: et == "intense" and sf == "grounded",
        "感情は強いが、安定している印象がある",
    ),
    (
        lambda et, sf, cp, cf: cp == "significant_shift" and cf == "continuous",
        "大きな変化があるのに、連続性が感じられる",
    ),
]


def _detect_contradictions(
    emotional_tone: str,
    stability_feeling: str,
    change_presence: str,
    continuity_feeling: str,
) -> list[str]:
    """Detect contradictions between facets. Contradictions are allowed to coexist."""
    result = []
    for check_fn, desc in _CONTRADICTION_PAIRS:
        if check_fn(emotional_tone, stability_feeling, change_presence, continuity_feeling):
            result.append(desc)
    return result


# --- Description Generation ---

_IMPRESSION_OPENERS = {
    "settled": "自分は落ち着いた状態にあるように見える",
    "active": "自分は何かに動かされているように見える",
    "transitional": "自分は移行期にあるように見える",
    "uncertain": "自分はどこか不確かな印象がある",
    "conflicted": "自分の中に何らかの緊張があるように見える",
    "undefined": "今の自分の全体像はまだはっきりしない",
}

_EMOTIONAL_QUALIFIERS = {
    "calm": "穏やかな感情の色合いとともに",
    "stirred": "何かが感情的に動いている気配とともに",
    "mixed": "複数の感情が共存しているように見え",
    "intense": "強い感情の存在感とともに",
}

_CHANGE_NOTES = {
    "noticeable_change": "何らかの変化が感じられる",
    "significant_shift": "顕著な変化があったように見える",
}

_TENDENCY_NOTES = {
    "forming_pattern": "何かパターンが形成されつつある気配がある",
    "established_way": "ある種の傾向が定着しているように見える",
}


def _generate_integrated_description(
    emotional_tone: str,
    tendency_hint: str,
    stability_feeling: str,
    change_presence: str,
    continuity_feeling: str,
    overall_impression: str,
    contradictions: list[str],
) -> str:
    """Generate integrated description in Japanese using provisional language."""
    if overall_impression == "undefined":
        return "今の自分の全体像はまだはっきりしない。"

    parts = []

    opener = _IMPRESSION_OPENERS.get(overall_impression, "今の自分の像が浮かんでいる")
    parts.append(opener)

    qualifier = _EMOTIONAL_QUALIFIERS.get(emotional_tone)
    if qualifier and emotional_tone not in ("undefined", "muted"):
        parts.append(qualifier)

    change_note = _CHANGE_NOTES.get(change_presence)
    if change_note:
        parts.append(change_note)

    tendency_note = _TENDENCY_NOTES.get(tendency_hint)
    if tendency_note:
        parts.append(tendency_note)

    description = "、".join(parts) + "。"

    if contradictions:
        contradiction_text = "；".join(contradictions)
        description += f" ただし、{contradiction_text}。"

    return description


# --- Public API ---

def integrate_self_image(memory_dir: str) -> dict:
    """Integrate three observation systems into a provisional self-image.

    Completely stateless, READ-ONLY integration.
    Each call produces a fresh image from current observations.
    No accumulation, no history tracking, no persistence.

    Returns:
        {
            "emotional_tone": str,
            "tendency_hint": str,
            "stability_feeling": str,
            "change_presence": str,
            "continuity_feeling": str,
            "overall_impression": str,
            "contradictions": list[str],
            "integrated_description": str,
            "is_complete": bool,
        }
    """
    # Gather inputs (each may fail independently)
    self_obs = None
    diff_result = None
    strain_result = None

    try:
        self_obs = self_model.observe(memory_dir)
    except Exception:
        pass

    try:
        diff_result = temporal_self_difference.compute_difference(memory_dir)
    except Exception:
        pass

    try:
        strain_result = continuity_strain.evaluate_strain(memory_dir)
    except Exception:
        pass

    # Note: if continuity_strain succeeded, it already called compute_difference
    # internally. We use diff_result from our own call for ChangePresence,
    # and strain_result for ContinuityFeeling. Both are independent reads.

    # Determine each facet
    emotional_tone = _determine_emotional_tone(self_obs)
    tendency_hint = _determine_tendency_hint(self_obs)
    stability_feeling = _determine_stability_feeling(self_obs, strain_result)
    change_presence = _determine_change_presence(diff_result)
    continuity_feeling = _determine_continuity_feeling(strain_result, diff_result)

    # Overall impression
    overall_impression = _determine_overall_impression(
        emotional_tone,
        tendency_hint,
        stability_feeling,
        change_presence,
        continuity_feeling,
    )

    # Contradictions
    contradictions = _detect_contradictions(
        emotional_tone,
        stability_feeling,
        change_presence,
        continuity_feeling,
    )

    # Integrated description
    integrated_description = _generate_integrated_description(
        emotional_tone,
        tendency_hint,
        stability_feeling,
        change_presence,
        continuity_feeling,
        overall_impression,
        contradictions,
    )

    # Completeness
    is_complete = (
        emotional_tone != "undefined"
        and stability_feeling != "undefined"
        and continuity_feeling != "undefined"
    )

    return {
        "emotional_tone": emotional_tone,
        "tendency_hint": tendency_hint,
        "stability_feeling": stability_feeling,
        "change_presence": change_presence,
        "continuity_feeling": continuity_feeling,
        "overall_impression": overall_impression,
        "contradictions": contradictions,
        "integrated_description": integrated_description,
        "is_complete": is_complete,
    }
