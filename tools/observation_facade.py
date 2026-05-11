#!/usr/bin/env python3
"""Facade for self-observation modules.

Provides a thin delegation layer so that memory_mcp_server.py can access
self-observation functionality through a single import instead of importing
7 individual observation modules directly.

This module:
- Is the ONLY observation import needed by memory_mcp_server.py
- Returns raw dicts/values from each module (no string formatting)
- Does not hold state — each call delegates directly to the underlying module
- Must not be imported by the observation modules themselves (no circular refs)
"""

import os
import sys

# Ensure tools directory is on path
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from self_model import observe as self_model_observe
from temporal_self_difference import compute_difference as self_diff_compute
from continuity_strain import evaluate_strain as strain_evaluate
from self_image_integration import integrate_self_image as self_image_integrate
from identity_coherence import assess_coherence as coherence_assess
from stability_valve import (
    check_stability as stability_check_fn,
    get_dampening_factor as stability_get_dampening,
)
from tone_modulation import compute_tone as tone_compute
from long_term_dynamics import record_observation as lt_record_observation


def run_snapshot(memory_dir: str) -> dict:
    """Run the full 7-module observation pipeline.

    Returns a dict with raw module outputs keyed by layer name:
        observe, difference, strain, self_image, coherence, stability, tone
    """
    return {
        "observe": self_model_observe(memory_dir),
        "difference": self_diff_compute(memory_dir),
        "strain": strain_evaluate(memory_dir),
        "self_image": self_image_integrate(memory_dir),
        "coherence": coherence_assess(memory_dir),
        "stability": stability_check_fn(memory_dir),
        "tone": tone_compute(memory_dir),
    }


def run_mini_snapshot(memory_dir: str) -> dict:
    """Run the lightweight 3-module observation (for session_end).

    Returns a dict with raw module outputs keyed by layer name:
        observe, self_image, tone
    """
    return {
        "observe": self_model_observe(memory_dir),
        "self_image": self_image_integrate(memory_dir),
        "tone": tone_compute(memory_dir),
    }


def get_dampening_factor(memory_dir: str) -> float:
    """Get the stability valve dampening factor.

    Returns a float in [0.0, 1.0]. 1.0 means no dampening (inactive).
    """
    return stability_get_dampening(memory_dir)


def record_long_term(memory_dir: str, emotion_state: dict, dynamics_phase: str) -> dict:
    """Record an observation to long-term dynamics.

    Returns the raw result dict from long_term_dynamics.record_observation.
    """
    return lt_record_observation(
        memory_dir,
        emotion_state=emotion_state,
        dynamics_phase=dynamics_phase,
    )
