#!/usr/bin/env python3
"""Runner script for hook eligibility checking.

Called from behavior-guard.js to avoid shell injection via python -c.
Takes the eligibility config file path as a command-line argument.

Usage:
    python hook_eligibility_runner.py <eligibility_config_path>
"""

import json
import os
import sys


def main() -> None:
    if len(sys.argv) != 2:
        print("[]")
        sys.exit(0)

    config_path = sys.argv[1]

    # Validate the config path is a real file (not a traversal attack)
    config_path = os.path.abspath(config_path)
    if not os.path.isfile(config_path):
        print("[]")
        sys.exit(0)

    # Add hooks directory to path for importing hook_eligibility
    hooks_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, hooks_dir)

    try:
        from hook_eligibility import load_eligibility_config, get_ineligible_rule_ids
        config = load_eligibility_config(config_path)
        result = list(get_ineligible_rule_ids(config))
        print(json.dumps(result))
    except Exception as e:
        # Fail-open: on any error, return empty list (all eligible)
        sys.stderr.write(f"[hook_eligibility_runner] Error: {e}\n")
        print("[]")


if __name__ == "__main__":
    main()
