#!/usr/bin/env python3
"""Hook eligibility checker (G37: Hook Eligibility Checking).

Checks whether hooks meet their environmental prerequisites before firing.
Read-only: never modifies hook rules, environment, or state.

Fail-open design: any error in eligibility checking results in the hook
being treated as eligible (safe default to avoid blocking operations).

Usage:
    from hook_eligibility import load_eligibility_config, get_ineligible_rule_ids
    config = load_eligibility_config("hooks/hook-eligibility.json")
    skip_ids = get_ineligible_rule_ids(config)
"""

import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class EligibilityResult:
    """Result of an eligibility check for a single rule."""

    rule_id: str
    eligible: bool
    missing_binaries: list[str] = field(default_factory=list)
    missing_env_vars: list[str] = field(default_factory=list)
    os_mismatch: bool = False

    def summary(self) -> str:
        """Human-readable summary of eligibility result."""
        if self.eligible:
            return f"{self.rule_id}: eligible"

        reasons = []
        if self.missing_binaries:
            reasons.append(f"missing binaries: {', '.join(self.missing_binaries)}")
        if self.missing_env_vars:
            reasons.append(f"missing env vars: {', '.join(self.missing_env_vars)}")
        if self.os_mismatch:
            reasons.append(f"OS mismatch (current: {sys.platform})")
        return f"{self.rule_id}: ineligible ({'; '.join(reasons)})"


def load_eligibility_config(config_path: str) -> dict:
    """Load eligibility configuration from JSON file.

    Fail-open: returns empty dict on any error.

    Args:
        config_path: Path to hook-eligibility.json.

    Returns:
        Dict mapping rule_id -> eligibility requirements.
        Empty dict if file not found or parse error.
    """
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        logger.info("Eligibility config not found: %s (fail-open: all eligible)", config_path)
        return {}
    except OSError as e:
        logger.warning("Failed to read eligibility config %s: %s (fail-open)", config_path, e)
        return {}

    if not raw.strip():
        return {}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("Eligibility config parse error: %s (fail-open)", e)
        return {}

    if not isinstance(data, dict):
        logger.warning("Eligibility config is not a JSON object (fail-open)")
        return {}

    # Extract rules section
    rules = data.get("rules", {})
    if not isinstance(rules, dict):
        return {}

    return rules


def check_rule_eligibility(
    rule_id: str, eligibility_config: dict
) -> EligibilityResult:
    """Check if a single rule meets its eligibility requirements.

    Args:
        rule_id: The rule ID to check.
        eligibility_config: Dict from load_eligibility_config().

    Returns:
        EligibilityResult with check details.
    """
    if rule_id not in eligibility_config:
        # No eligibility config = always eligible (backward compatible)
        return EligibilityResult(rule_id=rule_id, eligible=True)

    rule_config = eligibility_config[rule_id]
    if not isinstance(rule_config, dict):
        # Malformed config entry -> fail-open
        return EligibilityResult(rule_id=rule_id, eligible=True)

    missing_binaries = []
    missing_env_vars = []
    os_mismatch = False

    # Check required binaries
    required_bins = rule_config.get("required_binaries", [])
    if isinstance(required_bins, list):
        for binary in required_bins:
            if not shutil.which(binary):
                missing_binaries.append(binary)

    # Check required environment variables (existence only, not value)
    required_vars = rule_config.get("required_env_vars", [])
    if isinstance(required_vars, list):
        for var in required_vars:
            if var not in os.environ:
                missing_env_vars.append(var)

    # Check supported OS
    supported_os = rule_config.get("supported_os", [])
    if isinstance(supported_os, list) and supported_os:
        if sys.platform not in supported_os:
            os_mismatch = True

    eligible = (
        len(missing_binaries) == 0
        and len(missing_env_vars) == 0
        and not os_mismatch
    )

    return EligibilityResult(
        rule_id=rule_id,
        eligible=eligible,
        missing_binaries=missing_binaries,
        missing_env_vars=missing_env_vars,
        os_mismatch=os_mismatch,
    )


def check_all_eligibility(
    eligibility_config: dict,
) -> dict[str, EligibilityResult]:
    """Check eligibility for all rules in the config.

    Args:
        eligibility_config: Dict from load_eligibility_config().

    Returns:
        Dict mapping rule_id -> EligibilityResult.
    """
    results = {}
    for rule_id in eligibility_config:
        results[rule_id] = check_rule_eligibility(rule_id, eligibility_config)
    return results


def get_ineligible_rule_ids(eligibility_config: dict) -> set[str]:
    """Get the set of rule IDs that are currently ineligible.

    Convenience function for behavior-guard.js integration.

    Args:
        eligibility_config: Dict from load_eligibility_config().

    Returns:
        Set of ineligible rule IDs.
    """
    results = check_all_eligibility(eligibility_config)
    return {rid for rid, r in results.items() if not r.eligible}
