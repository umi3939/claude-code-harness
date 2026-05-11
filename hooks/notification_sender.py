#!/usr/bin/env python3
"""Notification sender: sends Claude Code Notification events to Discord DM.

Called as a child process from notification-discord.js hook.
Reads event JSON from argv[1] (or stdin fallback), checks filter,
formats message, and sends via DiscordClient.

Fail-open: always exit(0). Never blocks Claude Code.
Never exit(2) — notification failure must not affect session.

A2 risk note: Uses asyncio.run() with WindowsSelectorEventLoopPolicy
for aiohttp compatibility on Windows.
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone

# Configure logging to stderr only (never stdout — hook protocol)
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="[NotificationSender] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Constants
TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
DEFAULT_FILTER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notification_filter.json")
SEND_TIMEOUT_SECONDS = 10


def load_filter(filter_path: str) -> list:
    """Load allowed matchers from filter config file.

    Returns empty list if file is missing, unreadable, or malformed.
    Empty list = reject all notifications (whitelist approach).
    """
    try:
        with open(filter_path, "r", encoding="utf-8") as f:
            data = json.loads(f.read())
        if isinstance(data, dict):
            matchers = data.get("allowed_matchers", [])
            if isinstance(matchers, list):
                return matchers
        return []
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError) as e:
        logger.info("Filter load failed (%s): %s", type(e).__name__, e)
        return []


def should_notify(matcher: str, allowed_matchers: list) -> bool:
    """Check if this matcher value should trigger a notification."""
    if not matcher:
        return False
    return matcher in allowed_matchers


def format_message(event: dict) -> str:
    """Format notification event data into a human-readable Discord message.

    Template-based only — no interpretation or summarization of content.
    """
    matcher = str(event.get("matcher", "unknown"))[:100]
    session_id = str(event.get("session_id", "unknown"))[:50]
    cwd = str(event.get("cwd", ""))[:200]
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Map matcher to readable label
    matcher_labels = {
        "permission_prompt": "Permission Required",
        "idle_prompt": "Session Idle (60s+)",
        "auth_success": "Authentication Success",
        "elicitation_dialog": "MCP Input Required",
    }
    label = matcher_labels.get(matcher, matcher)

    lines = [
        f"**[Claude Code Notification]** {label}",
        f"Time: {now_str}",
        f"Session: {session_id}",
    ]
    if cwd:
        lines.append(f"Directory: {cwd}")

    return "\n".join(lines)


def _get_discord_client_class():
    """Import and return DiscordClient class.

    Separated for testability (mock injection point).
    """
    # Add tools dir to path for discord_mcp_server import
    if TOOLS_DIR not in sys.path:
        sys.path.insert(0, TOOLS_DIR)
    from discord_mcp_server import DiscordClient
    return DiscordClient


async def send_notification(message: str) -> bool:
    """Send a message to Discord via DiscordClient.

    Returns True on success, False on failure.
    Always closes the aiohttp session to avoid resource leaks (A3 risk).
    """
    client = None
    try:
        client_class = _get_discord_client_class()
        client = client_class()
        result = await client.send_message(message=message)
        if isinstance(result, str) and result.startswith("ERROR"):
            logger.error("Discord send failed: %s", result[:200])
            return False
        return True
    except Exception as e:
        logger.error("Discord send exception: %s", e)
        return False
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception as e:
                logger.warning("Session close error: %s", e)


def main(event_json: str, filter_path: str = DEFAULT_FILTER_PATH) -> int:
    """Main orchestration. Returns exit code (always 0).

    Args:
        event_json: Raw JSON string from Notification event stdin.
        filter_path: Path to notification_filter.json.
    """
    # Parse event JSON
    try:
        event = json.loads(event_json)
        if not isinstance(event, dict):
            logger.info("Event data is not a dict, ignoring")
            return 0
    except (json.JSONDecodeError, ValueError) as e:
        logger.info("Invalid event JSON: %s", e)
        return 0

    # Load filter
    allowed = load_filter(filter_path)
    if not allowed:
        logger.info("No allowed matchers configured, skipping notification")
        return 0

    # Check matcher
    matcher = event.get("matcher", "")
    if not should_notify(matcher, allowed):
        logger.info("Matcher '%s' not in allowed list, skipping", matcher)
        return 0

    # Format message
    message = format_message(event)

    # Send via Discord (A2 risk: Windows EventLoopPolicy)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    success = asyncio.run(send_notification(message))
    if success:
        logger.info("Notification sent for matcher '%s'", matcher)
    else:
        logger.error("Notification send failed for matcher '%s'", matcher)

    # Always exit 0 — fail-open
    return 0


if __name__ == "__main__":
    # Read event data from argv[1] (passed by JS hook) or stdin
    if len(sys.argv) > 1:
        raw = sys.argv[1]
    else:
        try:
            raw = sys.stdin.read()
        except Exception:
            raw = ""

    exit_code = main(raw)
    sys.exit(exit_code)
