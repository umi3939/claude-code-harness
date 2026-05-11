#!/usr/bin/env python3
"""GitHub→Discord notifier.

Polls GitHub REST API for repository events and sends notifications
to Discord via discord_send MCP tool.

Uses stdlib urllib.request for HTTP (no MCP inter-dependency).
Config stored in discord_data/github_notifier_config.json.

IMPORTANT: For stdio transport, never print() to stdout.
Use print(..., file=sys.stderr) for debug logging.
"""

import json
import logging
import os
import ssl
import tempfile
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))

from file_io import resolve_project_root  # noqa: E402

_PROJECT_ROOT = resolve_project_root()

DISCORD_DATA_DIR = os.path.join(_PROJECT_ROOT, "discord_data")
DEFAULT_CONFIG_PATH = os.path.join(DISCORD_DATA_DIR, "github_notifier_config.json")

GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_TIMEOUT = 30  # seconds
MAX_EVENTS_PER_REPO = 100  # GitHub API default page size
MAX_COMMIT_MSG_LEN = 120  # Truncate long commit messages
MAX_TITLE_LEN = 100  # Truncate long PR/issue titles
MAX_NOTIFICATION_LEN = 1800  # Leave room under Discord's 2000 char limit

# Token key stripped from saved config (security)
_SENSITIVE_KEYS = {"github_token"}

DEFAULT_CONFIG = {
    "github_token": "",
    "repositories": [],
    "check_interval_seconds": 300,
    "last_check": {},
    "event_types": ["PushEvent", "PullRequestEvent", "IssuesEvent", "CreateEvent"],
}


# ═══════════════════════════════════════════════════════════════
# Config I/O
# ═══════════════════════════════════════════════════════════════


def load_config(config_path: str = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    """Load config from JSON file. Returns default config on any error."""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return dict(DEFAULT_CONFIG)
        data = json.loads(content)
        if not isinstance(data, dict):
            return dict(DEFAULT_CONFIG)
        # Merge with defaults for missing keys
        result = dict(DEFAULT_CONFIG)
        result.update(data)
        return result
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load config from %s: %s", config_path, e)
        return dict(DEFAULT_CONFIG)


def save_config(config: Dict[str, Any], config_path: str = DEFAULT_CONFIG_PATH) -> None:
    """Save config to file. Strips sensitive keys (github_token).

    Uses temp-file + rename for atomic writes.
    """
    sanitized = {k: v for k, v in config.items() if k not in _SENSITIVE_KEYS}

    config_dir = os.path.dirname(config_path)
    os.makedirs(config_dir, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=config_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(sanitized, f, indent=2, ensure_ascii=False)
        try:
            os.replace(tmp_path, config_path)
        except OSError:
            # Windows fallback: remove then rename
            try:
                os.remove(config_path)
            except OSError:
                pass
            os.rename(tmp_path, config_path)
    except Exception:
        # Cleanup temp file on failure
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def validate_config(config: Dict[str, Any]) -> Tuple[bool, str]:
    """Validate config for required fields.

    Returns (ok, error_message). error_message is empty if ok.
    """
    token = config.get("github_token", "")
    if not token or not token.strip():
        return False, "GitHub token not configured. Set 'github_token' in config."

    repos = config.get("repositories", [])
    if not repos:
        return False, "No repositories configured. Add repos to 'repositories' list."

    return True, ""


# ═══════════════════════════════════════════════════════════════
# Event formatting
# ═══════════════════════════════════════════════════════════════


def _truncate(text: str, max_len: int) -> str:
    """Truncate text with ellipsis if too long."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _get_repo_short_name(event: Dict[str, Any]) -> str:
    """Extract short repo name from event."""
    full_name = event.get("repo", {}).get("name", "unknown")
    # "owner/repo" -> "repo"
    if "/" in full_name:
        return full_name.split("/", 1)[1]
    return full_name


def format_event(event: Dict[str, Any]) -> str:
    """Format a GitHub event into a Discord notification string.

    Returns a human-readable string with emoji prefix.
    """
    event_type = event.get("type", "UnknownEvent")
    repo_short = _get_repo_short_name(event)
    actor = event.get("actor", {}).get("login", "unknown")
    payload = event.get("payload", {})

    if event_type == "PushEvent":
        size = payload.get("size", 0)
        commits = payload.get("commits", [])
        msg = ""
        if commits:
            first_msg = commits[0].get("message", "").split("\n")[0]
            msg = _truncate(first_msg, MAX_COMMIT_MSG_LEN)
        plural = "commits" if size != 1 else "commit"
        return f"[{repo_short}] Push by {actor}: \"{msg}\" ({size} {plural})"

    elif event_type == "PullRequestEvent":
        action = payload.get("action", "unknown")
        pr = payload.get("pull_request", {})
        number = payload.get("number", 0)
        title = _truncate(pr.get("title", ""), MAX_TITLE_LEN)
        return f"[{repo_short}] PR #{number} {action} by {actor}: \"{title}\""

    elif event_type == "IssuesEvent":
        action = payload.get("action", "unknown")
        issue = payload.get("issue", {})
        number = issue.get("number", 0)
        title = _truncate(issue.get("title", ""), MAX_TITLE_LEN)
        return f"[{repo_short}] Issue #{number} {action}: \"{title}\""

    elif event_type == "CreateEvent":
        ref_type = payload.get("ref_type", "unknown")
        ref = payload.get("ref", "")
        if ref:
            return f"[{repo_short}] Created {ref_type}: {ref} by {actor}"
        return f"[{repo_short}] Created {ref_type} by {actor}"

    else:
        return f"[{repo_short}] {event_type} by {actor}"


# ═══════════════════════════════════════════════════════════════
# Event filtering
# ═══════════════════════════════════════════════════════════════


def filter_events(
    events: List[Dict[str, Any]],
    allowed_types: List[str],
    last_check: Optional[str],
) -> List[Dict[str, Any]]:
    """Filter events by type and timestamp.

    Args:
        events: List of GitHub event dicts.
        allowed_types: List of allowed event type strings.
        last_check: ISO timestamp string of last check, or None.

    Returns:
        Filtered list of events (newest last).
    """
    allowed_set = set(allowed_types)
    result = []

    last_check_dt = None
    if last_check:
        try:
            last_check_dt = datetime.fromisoformat(
                last_check.replace("Z", "+00:00")
            )
        except (ValueError, TypeError):
            logger.warning("Invalid last_check timestamp: %s", last_check)

    for event in events:
        # Filter by type
        if event.get("type") not in allowed_set:
            continue

        # Filter by timestamp
        if last_check_dt:
            created = event.get("created_at", "")
            try:
                event_dt = datetime.fromisoformat(
                    created.replace("Z", "+00:00")
                )
                if event_dt <= last_check_dt:
                    continue
            except (ValueError, TypeError):
                # If we can't parse the timestamp, include the event
                pass

        result.append(event)

    return result


# ═══════════════════════════════════════════════════════════════
# Rate limit parsing
# ═══════════════════════════════════════════════════════════════


def parse_rate_limit(headers: Dict[str, str]) -> Dict[str, Any]:
    """Parse GitHub API rate limit headers.

    Returns dict with remaining, limit, reset fields (None if missing).
    """
    remaining = headers.get("X-RateLimit-Remaining")
    limit = headers.get("X-RateLimit-Limit")
    reset = headers.get("X-RateLimit-Reset")

    return {
        "remaining": int(remaining) if remaining is not None else None,
        "limit": int(limit) if limit is not None else None,
        "reset": int(reset) if reset is not None else None,
    }


# ═══════════════════════════════════════════════════════════════
# GitHub API access (urllib)
# ═══════════════════════════════════════════════════════════════


def _fetch_events(
    repo: str, token: str
) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Fetch events for a repository from GitHub API.

    Args:
        repo: Repository in "owner/repo" format.
        token: GitHub personal access token.

    Returns:
        Tuple of (events list, rate_limit info dict or None).

    Raises:
        urllib.error.URLError on network errors.
    """
    url = f"{GITHUB_API_BASE}/repos/{repo}/events?per_page={MAX_EVENTS_PER_REPO}"

    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ClaudeCodeHarnessGitHubNotifier/1.0",
    }
    if token:
        headers["Authorization"] = f"token {token}"

    req = urllib.request.Request(url, headers=headers)  # noqa: S310

    # Create SSL context (use default certs)
    ctx = ssl.create_default_context()

    response = urllib.request.urlopen(req, timeout=GITHUB_API_TIMEOUT, context=ctx)  # noqa: S310  # nosec B310
    body = response.read().decode("utf-8")
    events = json.loads(body)

    if not isinstance(events, list):
        logger.warning("Unexpected API response type: %s", type(events).__name__)
        events = []

    # Parse rate limit from response headers
    rate_info = parse_rate_limit(dict(response.headers))

    return events, rate_info


# ═══════════════════════════════════════════════════════════════
# Discord notification sending
# ═══════════════════════════════════════════════════════════════


def _send_discord_notification(message: str) -> str:
    """Send a notification message to Discord via discord_send MCP tool.

    Imports discord_mcp_server and calls the send function directly.
    Falls back to logging if Discord is not available.
    """
    try:
        import asyncio

        from discord_mcp_server import DiscordClient

        client = DiscordClient()

        async def _send():
            return await client.send_message(message=message)

        # Run async send
        try:
            loop = asyncio.get_running_loop()
            # Already in async context — create task
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                result = loop.run_in_executor(pool, lambda: asyncio.run(_send()))
        except RuntimeError:
            # No running loop — safe to use asyncio.run
            result = asyncio.run(_send())

        return str(result)
    except ImportError:
        logger.warning("discord_mcp_server not available, logging message instead")
        logger.info("Discord notification: %s", message)
        return "Discord not available"
    except Exception as e:
        logger.error("Failed to send Discord notification: %s", e)
        return f"Send failed: {e}"


# ═══════════════════════════════════════════════════════════════
# Main check-and-notify logic
# ═══════════════════════════════════════════════════════════════


def check_and_notify(config_path: str = DEFAULT_CONFIG_PATH) -> str:
    """Check GitHub repos for new events and send Discord notifications.

    This is the main entry point, called by the github_notify MCP tool.

    Args:
        config_path: Path to github_notifier_config.json.

    Returns:
        Status message (e.g., "2 new events notified" or "Not configured").
    """
    config = load_config(config_path)

    ok, err = validate_config(config)
    if not ok:
        return f"Not configured: {err}"

    token = config["github_token"]
    repos = config["repositories"]
    event_types = config.get("event_types", DEFAULT_CONFIG["event_types"])
    last_check_map = config.get("last_check", {})

    total_new = 0
    errors = []
    all_formatted = []

    for repo in repos:
        # Validate repo format
        if "/" not in repo or len(repo.split("/")) != 2:
            errors.append(f"Invalid repo format: {repo}")
            continue

        try:
            events, rate_info = _fetch_events(repo, token)
        except urllib.error.HTTPError as e:
            errors.append(f"{repo}: HTTP {e.code}")
            logger.error("GitHub API error for %s: %s", repo, e)
            continue
        except urllib.error.URLError as e:
            errors.append(f"{repo}: {e.reason}")
            logger.error("Network error for %s: %s", repo, e)
            continue
        except Exception as e:
            errors.append(f"{repo}: {e}")
            logger.error("Unexpected error for %s: %s", repo, e)
            continue

        # Log rate limit info
        if rate_info and rate_info.get("remaining") is not None:
            logger.info(
                "Rate limit for %s: %d/%d remaining",
                repo,
                rate_info["remaining"],
                rate_info.get("limit", 0),
            )
            if rate_info["remaining"] < 10:
                logger.warning(
                    "Low rate limit for %s: %d remaining", repo, rate_info["remaining"]
                )

        # Filter events
        repo_last_check = last_check_map.get(repo)
        new_events = filter_events(events, event_types, repo_last_check)

        if new_events:
            for event in new_events:
                formatted = format_event(event)
                all_formatted.append(formatted)

            # Update last_check to newest event timestamp
            newest_ts = None
            for event in new_events:
                ts = event.get("created_at")
                if ts:
                    if newest_ts is None or ts > newest_ts:
                        newest_ts = ts
            if newest_ts:
                last_check_map[repo] = newest_ts

            total_new += len(new_events)

    # Save updated last_check
    if total_new > 0:
        config["last_check"] = last_check_map
        try:
            save_config(config, config_path)
        except Exception as e:
            logger.error("Failed to save config: %s", e)
            errors.append(f"Config save error: {e}")

    # Send Discord notification
    if all_formatted:
        # Batch messages to stay under Discord limit
        batch = []
        batch_len = 0
        for line in all_formatted:
            if batch_len + len(line) + 1 > MAX_NOTIFICATION_LEN and batch:
                msg = "\n".join(batch)
                _send_discord_notification(msg)
                batch = []
                batch_len = 0
            batch.append(line)
            batch_len += len(line) + 1

        if batch:
            msg = "\n".join(batch)
            _send_discord_notification(msg)

    # Build result
    if total_new == 0 and not errors:
        return "No new events"
    elif total_new > 0 and not errors:
        return f"{total_new} new events notified"
    elif total_new > 0 and errors:
        return f"{total_new} new events notified (errors: {'; '.join(errors)})"
    else:
        return f"No new events (errors: {'; '.join(errors)})"
