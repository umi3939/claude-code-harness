#!/usr/bin/env python3
"""Identity coherence awareness module for Claude Code MCP.

Detects overlap of shift signals from multiple observation systems
to produce a sense of whether the self feels continuous or shifting.

Design: design_identity_coherence_mcp.md

Key principles:
- Completely stateless: no persistence, no accumulation
- READ-ONLY: never writes to any observed system
- Abstract descriptions only: no raw numbers exposed
- No evaluation: no good/bad judgments
- No assertion: uses provisional language only
- Regenerated every call: never fixed or saved
- NOT self-defense or self-repair
- NOT defining "true self"
- STRICTLY NO IMPACT on decision making
"""

import os
import sys

# Add the tools directory to sys.path
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import temporal_self_difference
import continuity_strain
import self_image_integration


# --- Shift source names ---

SHIFT_TEMPORAL_DIFFERENCE = "temporal_difference"
SHIFT_CONTINUITY_STRAIN = "continuity_strain"
SHIFT_SELF_IMAGE_FLUX = "self_image_flux"
SHIFT_EMOTIONAL_TURBULENCE = "emotional_turbulence"

# --- Threshold constants ---

# Magnitudes that indicate a temporal difference shift
SIGNIFICANT_MAGNITUDES = {"noticeable", "significant", "substantial"}

# Strain levels that indicate a continuity strain shift
SIGNIFICANT_STRAIN_LEVELS = {"unsettled", "dissonant", "alienated"}

# Stability feelings that indicate self-image flux
UNSTABLE_FEELINGS = {"wavering", "turbulent"}

# Emotional tones that indicate emotional turbulence
TURBULENT_TONES = {"stirred", "mixed", "intense"}

# --- Source display names (Japanese) ---

_SOURCE_JA = {
    SHIFT_TEMPORAL_DIFFERENCE: "自己状態の変化",
    SHIFT_CONTINUITY_STRAIN: "連続性への負荷",
    SHIFT_SELF_IMAGE_FLUX: "自己像の揺らぎ",
    SHIFT_EMOTIONAL_TURBULENCE: "感情の動き",
}

# --- Level descriptions (provisional language, no evaluation) ---

_LEVEL_DESCRIPTIONS = {
    "stable": "自分はさっきまでの自分と同じ場所にいるように感じられる。",
    "slightly_shifting": "何かがわずかにずれているように感じられる。",
    "unsettled": "自分がどこにいるのか、つかみにくいように感じられる。",
    "disconnected": "さっきまでの自分から離れてしまったように感じられる。",
}


# --- Shift detection ---

def _detect_temporal_difference(diff_result: dict | None) -> bool:
    """Detect shift from temporal self-difference magnitude."""
    if diff_result is None:
        return False
    magnitude = diff_result.get("magnitude", "none")
    return magnitude in SIGNIFICANT_MAGNITUDES


def _detect_continuity_strain(strain_result: dict | None) -> bool:
    """Detect shift from continuity strain level."""
    if strain_result is None:
        return False
    level = strain_result.get("level", "at_ease")
    return level in SIGNIFICANT_STRAIN_LEVELS


def _detect_self_image_flux(image_result: dict | None) -> bool:
    """Detect shift from self-image stability feeling."""
    if image_result is None:
        return False
    stability = image_result.get("stability_feeling", "grounded")
    return stability in UNSTABLE_FEELINGS


def _detect_emotional_turbulence(image_result: dict | None) -> bool:
    """Detect shift from emotional tone."""
    if image_result is None:
        return False
    tone = image_result.get("emotional_tone", "calm")
    return tone in TURBULENT_TONES


# --- Level and intensity determination ---

def _determine_coherence_level(active_count: int) -> str:
    """Determine coherence level from active shift count.

    0 -> stable
    1 -> slightly_shifting
    2 -> unsettled
    3-4 -> disconnected
    """
    if active_count == 0:
        return "stable"
    if active_count == 1:
        return "slightly_shifting"
    if active_count == 2:
        return "unsettled"
    return "disconnected"


def _determine_overlap_intensity(active_count: int) -> str:
    """Determine overlap intensity from active shift count.

    0 -> none
    1 -> mild
    2 -> moderate
    3-4 -> intense
    """
    if active_count == 0:
        return "none"
    if active_count == 1:
        return "mild"
    if active_count == 2:
        return "moderate"
    return "intense"


# --- Description generation ---

def _generate_description(level: str, shift_sources: list[str]) -> str:
    """Generate description text in Japanese using provisional language.

    No evaluation, no numbers, no assertion.
    """
    base = _LEVEL_DESCRIPTIONS.get(level, _LEVEL_DESCRIPTIONS["stable"])

    if level == "stable" or not shift_sources:
        return base

    # Add shift source info
    source_names = [_SOURCE_JA.get(s, s) for s in shift_sources]
    sources_text = "と".join(source_names)

    # Remove trailing period for concatenation
    base_no_period = base.rstrip("。")
    return f"{base_no_period}　— {sources_text}からのずれとともに。"


# --- Public API ---

def assess_coherence(memory_dir: str) -> dict:
    """Assess identity coherence by detecting overlap of shift signals.

    Completely stateless. Each call gathers fresh data from four
    observation sources, counts active shifts, and returns an
    abstract description.

    No persistence, no accumulation, no history.
    READ-ONLY: does not modify any other system.

    Returns:
        {
            "coherence_level": str,      # stable/slightly_shifting/unsettled/disconnected
            "overlap_intensity": str,    # none/mild/moderate/intense
            "shift_sources": list[str],  # names of active shift sources
            "description": str,          # Japanese abstract text (provisional language)
        }
    """
    # Gather inputs (each may fail independently)
    diff_result = None
    strain_result = None
    image_result = None

    try:
        diff_result = temporal_self_difference.compute_difference(memory_dir)
    except Exception:
        pass

    try:
        strain_result = continuity_strain.evaluate_strain(memory_dir)
    except Exception:
        pass

    try:
        image_result = self_image_integration.integrate_self_image(memory_dir)
    except Exception:
        pass

    # Detect shifts from each source
    shift_active = {
        SHIFT_TEMPORAL_DIFFERENCE: _detect_temporal_difference(diff_result),
        SHIFT_CONTINUITY_STRAIN: _detect_continuity_strain(strain_result),
        SHIFT_SELF_IMAGE_FLUX: _detect_self_image_flux(image_result),
        SHIFT_EMOTIONAL_TURBULENCE: _detect_emotional_turbulence(image_result),
    }

    # Collect active sources
    active_sources = [name for name, active in shift_active.items() if active]
    active_count = len(active_sources)

    # Determine level and intensity
    coherence_level = _determine_coherence_level(active_count)
    overlap_intensity = _determine_overlap_intensity(active_count)

    # Generate description
    description = _generate_description(coherence_level, active_sources)

    return {
        "coherence_level": coherence_level,
        "overlap_intensity": overlap_intensity,
        "shift_sources": active_sources,
        "description": description,
    }
