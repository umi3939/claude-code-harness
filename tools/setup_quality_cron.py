#!/usr/bin/env python3
"""Setup weekly quality scan cron job via persistent_cron_add.

Creates a persistent cron job that runs `ruff check tools/ hooks/`
weekly and logs the results.

Usage:
    python setup_quality_cron.py          # Print the cron config (dry run)
    python setup_quality_cron.py --apply  # Actually add the cron job
"""

import os
import sys

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
CLAUDE_DIR = os.path.dirname(_TOOLS_DIR)
TOOLS_DIR = os.path.join(CLAUDE_DIR, "tools")
HOOKS_DIR = os.path.join(CLAUDE_DIR, "hooks")
LOG_DIR = os.path.join(CLAUDE_DIR, "data")


def get_ruff_command():
    """Build the ruff check command for quality scanning.

    Returns:
        Command string to execute ruff check on tools/ and hooks/
    """
    ruff_config = os.path.join(CLAUDE_DIR, "ruff.toml")
    config_arg = f"--config {ruff_config}" if os.path.isfile(ruff_config) else ""
    ignore_arg = "--ignore E402,S110,S603"

    tools_path = TOOLS_DIR.replace("\\", "/")
    hooks_path = HOOKS_DIR.replace("\\", "/")

    return f"ruff check {config_arg} {ignore_arg} {tools_path}/ {hooks_path}/".strip()


def build_cron_job_config():
    """Build the cron job configuration dict.

    Returns:
        dict with name, command, schedule, and description
    """
    log_file = os.path.join(LOG_DIR, "weekly_quality_scan.log").replace("\\", "/")

    ruff_cmd = get_ruff_command()
    # Append logging: write timestamp + results to log file
    full_command = (
        f'echo "=== Quality Scan $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> {log_file} && '
        f"{ruff_cmd} >> {log_file} 2>&1; "
        f'echo "Exit code: $?" >> {log_file}'
    )

    return {
        "name": "weekly-quality-scan",
        "command": full_command,
        "schedule": "0 0 * * 0",  # Every Sunday at 00:00
        "description": "Weekly ruff quality scan of tools/ and hooks/ directories",
    }


def main():
    config = build_cron_job_config()

    if "--apply" in sys.argv:
        # Try to add via cron_scheduler
        try:
            sys.path.insert(0, TOOLS_DIR)
            from cron_scheduler import add_job

            cron_dir = os.path.join(CLAUDE_DIR, "cron")
            result = add_job(
                cron_dir=cron_dir,
                name=config["name"],
                command=config["command"],
                schedule=config["schedule"],
            )
            print(f"Cron job added: {result}")
        except Exception as e:
            print(f"Failed to add cron job: {e}", file=sys.stderr)
            print("You can add it manually via persistent_cron_add MCP tool:")
            print(f"  name: {config['name']}")
            print(f"  command: {config['command']}")
            print(f"  schedule: {config['schedule']}")
            sys.exit(1)
    else:
        import json

        print("=== Weekly Quality Scan Cron Job Config ===")
        print(json.dumps(config, indent=2, ensure_ascii=False))
        print("\nRun with --apply to add the cron job.")
        print("Or use persistent_cron_add MCP tool with these values.")


if __name__ == "__main__":
    main()
