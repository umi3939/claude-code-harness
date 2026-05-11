#!/usr/bin/env python3
"""MCP server for Discord messaging (send-only).

Provides tools for sending messages to Discord via Bot API.
Uses aiohttp for direct REST API calls (no discord.py dependency).

This is a generic Claude Code utility, not part of any specific project.

IMPORTANT: For stdio transport, never print() to stdout.
Use print(..., file=sys.stderr) for debug logging.
"""

import io
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Ensure UTF-8 stderr on Windows
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace"
    )

# Add the tools directory to sys.path
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from mcp.server.fastmcp import FastMCP  # noqa: E402

try:
    import aiohttp
except ImportError:
    aiohttp = None

# ═══════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════

# Project root (tools/ の親ディレクトリ)
from file_io import resolve_project_root  # noqa: E402

_PROJECT_ROOT = resolve_project_root()

DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_DATA_DIR = os.path.join(_PROJECT_ROOT, "discord_data")
CONFIG_FILE = os.path.join(DISCORD_DATA_DIR, "config.json")
SEND_LOG_FILE = os.path.join(DISCORD_DATA_DIR, "send_log.jsonl")

# Safety valve constants
DISCORD_MESSAGE_LIMIT = 2000
MAX_MESSAGE_LENGTH = 20000  # Max total message length we accept
MAX_SPLITS = 10  # Max number of message chunks
RATE_LIMIT_WINDOW_MS = 60000  # 1 minute
RATE_LIMIT_MAX_SENDS = 20  # Max sends per window
MAX_CONNECT_RETRIES = 3
SEND_LOG_MAX_BYTES = 1_000_000  # 1MB
SEND_LOG_MAX_LINES = 1000


# ═══════════════════════════════════════════════════════════════
# File I/O helpers
# ═══════════════════════════════════════════════════════════════


def _ensure_dir(dir_path: str) -> None:
    """Create directory if it doesn't exist."""
    os.makedirs(dir_path, exist_ok=True)


def _now_iso() -> str:
    """Return current time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _load_config() -> Dict[str, Any]:
    """Load config from file."""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.loads(f.read())
        if isinstance(data, dict):
            return data
        return {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


_TOKEN_KEYS = {"bot_token", "token"}


def _save_config(config: Dict[str, Any]) -> None:
    """Save config to file.

    Token keys (bot_token, token) are stripped before writing.
    Tokens must be provided via DISCORD_BOT_TOKEN environment variable.
    """
    sanitized = {k: v for k, v in config.items() if k not in _TOKEN_KEYS}
    _ensure_dir(DISCORD_DATA_DIR)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(json.dumps(sanitized, indent=2, ensure_ascii=False))


def _append_send_log(entry: Dict[str, Any]) -> None:
    """Append entry to send log (JSONL)."""
    _ensure_dir(DISCORD_DATA_DIR)
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with open(SEND_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)
    _prune_send_log()


def _prune_send_log() -> None:
    """Prune send log if it exceeds size limit."""
    try:
        stat = os.stat(SEND_LOG_FILE)
    except OSError:
        return

    if stat.st_size <= SEND_LOG_MAX_BYTES:
        return

    try:
        with open(SEND_LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return

    if len(lines) > SEND_LOG_MAX_LINES:
        kept = lines[-SEND_LOG_MAX_LINES:]
        _ensure_dir(DISCORD_DATA_DIR)
        with open(SEND_LOG_FILE, "w", encoding="utf-8") as f:
            f.writelines(kept)


# ═══════════════════════════════════════════════════════════════
# Rate limiter
# ═══════════════════════════════════════════════════════════════


class RateLimiter:
    """Fixed-window rate limiter for send operations."""

    def __init__(self, window_ms: int = RATE_LIMIT_WINDOW_MS,
                 max_requests: int = RATE_LIMIT_MAX_SENDS):
        self.window_ms = window_ms
        self.max_requests = max_requests
        self._timestamps: List[float] = []

    def is_rate_limited(self) -> bool:
        """Check if we've exceeded the rate limit."""
        now = time.time() * 1000
        cutoff = now - self.window_ms
        self._timestamps = [t for t in self._timestamps if t > cutoff]
        return len(self._timestamps) >= self.max_requests

    def record_send(self) -> None:
        """Record a send event."""
        self._timestamps.append(time.time() * 1000)


# ═══════════════════════════════════════════════════════════════
# Message splitting
# ═══════════════════════════════════════════════════════════════


def split_message(text: str, limit: int = DISCORD_MESSAGE_LIMIT) -> List[str]:
    """Split a message into chunks that fit within Discord's limit.

    Splits at paragraph boundaries (\n\n) first, then line boundaries (\n),
    then forces a split at the character limit.
    """
    if len(text) <= limit:
        return [text]

    chunks = []
    remaining = text

    while remaining and len(chunks) < MAX_SPLITS:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        # Try to split at paragraph boundary
        split_pos = remaining.rfind("\n\n", 0, limit)
        if split_pos > 0:
            chunks.append(remaining[:split_pos])
            remaining = remaining[split_pos + 2:]
            continue

        # Try to split at line boundary
        split_pos = remaining.rfind("\n", 0, limit)
        if split_pos > 0:
            chunks.append(remaining[:split_pos])
            remaining = remaining[split_pos + 1:]
            continue

        # Force split at limit
        chunks.append(remaining[:limit])
        remaining = remaining[limit:]

    # If there's remaining text after max splits, append to last chunk warning
    if remaining and len(chunks) >= MAX_SPLITS:
        truncation = "\n[Message truncated]"
        max_body = limit - len(truncation)
        if max_body < 0:
            max_body = 0
        chunks[-1] = chunks[-1][:max_body] + truncation

    return chunks


# ═══════════════════════════════════════════════════════════════
# Discord API client
# ═══════════════════════════════════════════════════════════════


class DiscordClient:
    """Discord REST API client using aiohttp."""

    def __init__(self):
        self._token: Optional[str] = None
        self._token_source: Optional[str] = None  # "env" or "config"
        self._connected: bool = False
        self._bot_name: Optional[str] = None
        self._bot_id: Optional[str] = None
        self._last_send_time: Optional[str] = None
        self._session = None  # aiohttp.ClientSession
        self._connect_failures: int = 0
        self._rate_limiter = RateLimiter()

    def _resolve_token(self) -> Optional[str]:
        """Resolve bot token from DISCORD_BOT_TOKEN environment variable.

        Tokens are never read from config files.
        """
        env_token = os.environ.get("DISCORD_BOT_TOKEN")
        if env_token:
            self._token = env_token
            self._token_source = "env"  # noqa: S105
            return env_token

        return None

    async def _ensure_session(self):
        """Ensure aiohttp session exists."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self._auth_headers()
            )

    def _auth_headers(self) -> Dict[str, str]:
        """Build authorization headers."""
        return {
            "Authorization": f"Bot {self._token}",
            "Content-Type": "application/json",
        }

    async def connect(self, token: Optional[str] = None,
                      default_target: Optional[str] = None,
                      default_target_type: Optional[str] = None) -> str:
        """Connect to Discord and verify bot token.

        Args:
            token: Bot token. If not provided, resolved from env/config.
            default_target: Default send target (user ID or channel ID).
            default_target_type: "dm" or "channel".

        Returns:
            Status message string.
        """
        # Handle token
        if token:
            self._token = token
            self._token_source = "provided"  # noqa: S105
        else:
            resolved = self._resolve_token()
            if not resolved:
                return ("ERROR: No Discord bot token found. "
                        "Set DISCORD_BOT_TOKEN environment variable or "
                        "provide token parameter.")

        # Check retry limit
        if self._connect_failures >= MAX_CONNECT_RETRIES:
            self._connect_failures = 0  # Reset on explicit connect attempt

        # Verify token by fetching bot user info
        try:
            if aiohttp is None:
                return "ERROR: aiohttp is required. Install with: pip install aiohttp"

            # Close existing session
            if self._session and not self._session.closed:
                await self._session.close()

            self._session = aiohttp.ClientSession(
                headers=self._auth_headers()
            )

            async with self._session.get(
                f"{DISCORD_API_BASE}/users/@me"
            ) as resp:
                if resp.status == 401:
                    self._connected = False
                    self._connect_failures += 1
                    await self._session.close()
                    self._session = None
                    return "ERROR: Invalid bot token. Authentication failed."
                elif resp.status != 200:
                    self._connected = False
                    self._connect_failures += 1
                    await self._session.close()
                    self._session = None
                    return f"ERROR: Discord API returned status {resp.status}."

                data = await resp.json()
                self._bot_name = data.get("username", "Unknown")
                self._bot_id = data.get("id")
                self._connected = True
                self._connect_failures = 0

        except Exception as e:
            self._connected = False
            self._connect_failures += 1
            if self._session and not self._session.closed:
                await self._session.close()
            self._session = None
            return f"ERROR: Failed to connect to Discord: {e}"

        # Save config (token is never persisted to disk)
        config = _load_config()
        if default_target:
            config["default_target"] = default_target
        if default_target_type:
            config["default_target_type"] = default_target_type
        _save_config(config)

        parts = [
            f"Connected to Discord as {self._bot_name}.",
            f"  Token source: {self._token_source}",
        ]
        if default_target:
            parts.append(f"  Default target: {default_target} ({default_target_type or 'dm'})")
        return "\n".join(parts)

    async def send_message(self, message: str,
                           target: Optional[str] = None,
                           target_type: Optional[str] = None) -> str:
        """Send a message to Discord.

        Args:
            message: The message text to send.
            target: User ID (for DM) or channel ID. Falls back to default.
            target_type: "dm" or "channel". Falls back to default or "dm".

        Returns:
            Status message string.
        """
        # Validate connection
        if not self._connected or not self._token:
            # Try lazy connect
            token = self._resolve_token()
            if token:
                result = await self.connect()
                if result.startswith("ERROR"):
                    return result
            else:
                return ("ERROR: Not connected to Discord. "
                        "Use discord_connect first or set DISCORD_BOT_TOKEN.")

        # Resolve target
        config = _load_config()
        actual_target = target or config.get("default_target")
        actual_type = target_type or config.get("default_target_type", "dm")

        if not actual_target:
            return ("ERROR: No target specified and no default target configured. "
                    "Provide target parameter or set default with discord_connect.")

        # Validate message
        if not message or not message.strip():
            return "ERROR: Message cannot be empty."

        if len(message) > MAX_MESSAGE_LENGTH:
            return (f"ERROR: Message too long ({len(message)} chars). "
                    f"Maximum is {MAX_MESSAGE_LENGTH} chars.")

        # Check rate limit
        if self._rate_limiter.is_rate_limited():
            return ("ERROR: Rate limit exceeded. "
                    f"Maximum {RATE_LIMIT_MAX_SENDS} messages per minute.")

        # Split message if needed
        chunks = split_message(message)

        # Resolve channel ID
        try:
            channel_id = await self._resolve_channel(actual_target, actual_type)
        except Exception as e:
            error_msg = str(e)
            _append_send_log({
                "timestamp": _now_iso(),
                "target": actual_target,
                "target_type": actual_type,
                "success": False,
                "error": error_msg[:500],
            })
            return f"ERROR: Failed to resolve target: {error_msg}"

        # Send chunks sequentially
        sent_count = 0
        for chunk in chunks:
            try:
                await self._send_to_channel(channel_id, chunk)
                sent_count += 1
                self._rate_limiter.record_send()
            except Exception as e:
                error_msg = str(e)
                _append_send_log({
                    "timestamp": _now_iso(),
                    "target": actual_target,
                    "target_type": actual_type,
                    "success": False,
                    "error": error_msg[:500],
                    "chunks_sent": sent_count,
                    "chunks_total": len(chunks),
                })
                return (f"ERROR: Failed to send message (chunk {sent_count + 1}/{len(chunks)}): "
                        f"{error_msg}")

        self._last_send_time = _now_iso()

        # Log success
        _append_send_log({
            "timestamp": self._last_send_time,
            "target": actual_target,
            "target_type": actual_type,
            "success": True,
            "chunks": len(chunks),
            "total_length": len(message),
        })

        parts = [
            "Message sent successfully.",
            f"  Target: {actual_target} ({actual_type})",
            f"  Time: {self._last_send_time}",
        ]
        if len(chunks) > 1:
            parts.append(f"  Chunks: {len(chunks)}")

        return "\n".join(parts)

    async def _resolve_channel(self, target: str, target_type: str) -> str:
        """Resolve target to a channel ID.

        For DM: creates/gets DM channel with user.
        For channel: returns the channel ID directly.
        """
        await self._ensure_session()

        if target_type == "channel":
            return target

        # DM: open DM channel
        async with self._session.post(
            f"{DISCORD_API_BASE}/users/@me/channels",
            json={"recipient_id": target}
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise Exception(
                    f"Failed to open DM channel (status {resp.status}): {body[:200]}"
                )
            data = await resp.json()
            return data["id"]

    async def _send_to_channel(self, channel_id: str, content: str) -> Dict:
        """Send a message to a specific channel."""
        await self._ensure_session()

        async with self._session.post(
            f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
            json={"content": content}
        ) as resp:
            if resp.status == 429:
                # Discord rate limit
                body = await resp.json()
                retry_after = body.get("retry_after", "unknown")
                raise Exception(
                    f"Discord rate limited. Retry after {retry_after}s."
                )
            elif resp.status not in (200, 201):
                body = await resp.text()
                raise Exception(
                    f"Discord API error (status {resp.status}): {body[:200]}"
                )
            return await resp.json()

    def get_status(self) -> str:
        """Get current connection status."""
        config = _load_config()

        # Determine token status (env var only, tokens are not stored in config)
        env_token = os.environ.get("DISCORD_BOT_TOKEN")

        if env_token:
            token_status = "set (from environment variable)"  # noqa: S105
        else:
            token_status = "not set"  # noqa: S105

        lines = [
            "Discord Messaging Status:",
            f"  Token: {token_status}",
            f"  Connected: {'yes' if self._connected else 'no'}",
        ]

        if self._bot_name:
            lines.append(f"  Bot name: {self._bot_name}")
        if self._bot_id:
            lines.append(f"  Bot ID: {self._bot_id}")

        default_target = config.get("default_target")
        if default_target:
            default_type = config.get("default_target_type", "dm")
            lines.append(f"  Default target: {default_target} ({default_type})")
        else:
            lines.append("  Default target: not set")

        if self._last_send_time:
            lines.append(f"  Last send: {self._last_send_time}")

        if self._connect_failures > 0:
            lines.append(f"  Connect failures: {self._connect_failures}")

        # Send log stats
        try:
            stat = os.stat(SEND_LOG_FILE)
            lines.append(f"  Send log size: {stat.st_size} bytes")
        except OSError:
            lines.append("  Send log: empty")

        return "\n".join(lines)

    async def close(self):
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None


# ═══════════════════════════════════════════════════════════════
# MCP Server
# ═══════════════════════════════════════════════════════════════

# Initialize MCP server
mcp = FastMCP("discord-messaging")

# Shared client instance
_client = DiscordClient()


@mcp.tool()
async def discord_connect(
    token: str = "",
    default_target: str = "",
    default_target_type: str = "",
) -> str:
    """Connect to Discord and verify bot token.

    Establishes connection to Discord Bot API. Token can be provided
    directly, via DISCORD_BOT_TOKEN environment variable, or from
    saved config.

    Args:
        token: Discord bot token. If empty, resolved from env/config.
                NOTE: Token value is never included in any output.
        default_target: Default send target (Discord user ID for DM,
                       or channel ID for channel messages).
        default_target_type: "dm" or "channel" (default: "dm").
    """
    return await _client.connect(
        token=token if token else None,
        default_target=default_target if default_target else None,
        default_target_type=default_target_type if default_target_type else None,
    )


@mcp.tool()
async def discord_send(
    message: str,
    target: str = "",
    target_type: str = "",
) -> str:
    """Send a message to Discord.

    Sends a text message to a Discord user (DM) or channel.
    Messages exceeding 2000 characters are automatically split.
    If not connected, attempts lazy connection using saved/env token.

    Args:
        message: The message text to send.
        target: Discord user ID (for DM) or channel ID.
                If empty, uses configured default target.
        target_type: "dm" or "channel". If empty, uses default ("dm").
    """
    return await _client.send_message(
        message=message,
        target=target if target else None,
        target_type=target_type if target_type else None,
    )


@mcp.tool()
async def discord_status() -> str:
    """Check Discord messaging connection status.

    Returns current bot connection state, token configuration
    (set/unset only, never the token value), default target,
    and send statistics.
    """
    return _client.get_status()


# ═══════════════════════════════════════════════════════════════
# Discord Receive MCP Tools (Phase 3)
#
# These tools interact with the receive daemon via filesystem:
# - receive_config.json: allow list and settings
# - receive_buffer.jsonl: pending/processed messages
# - receive_state.json: daemon state (connection, stats)
# No direct process communication — all file-based.
# ═══════════════════════════════════════════════════════════════

# Receive data file paths (same as discord_receiver.py constants)
RECEIVE_CONFIG_FILE = os.path.join(DISCORD_DATA_DIR, "receive_config.json")
RECEIVE_BUFFER_FILE = os.path.join(DISCORD_DATA_DIR, "receive_buffer.jsonl")
RECEIVE_STATE_FILE = os.path.join(DISCORD_DATA_DIR, "receive_state.json")


def _load_receive_config() -> Dict[str, Any]:
    """Load receive config from file."""
    try:
        with open(RECEIVE_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.loads(f.read())
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {}


def _save_receive_config(config: Dict[str, Any]) -> None:
    """Save receive config to file atomically using tmp+replace pattern."""
    _ensure_dir(DISCORD_DATA_DIR)
    data = json.dumps(config, indent=2, ensure_ascii=False)
    fd, tmp = tempfile.mkstemp(dir=DISCORD_DATA_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        try:
            os.replace(tmp, RECEIVE_CONFIG_FILE)
        except OSError:
            # Windows fallback: copy + unlink
            shutil.copy2(tmp, RECEIVE_CONFIG_FILE)
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_receive_state() -> Dict[str, Any]:
    """Load receive daemon state from file."""
    try:
        with open(RECEIVE_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.loads(f.read())
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {}


def _load_receive_buffer() -> List[Dict[str, Any]]:
    """Load all entries from receive buffer file."""
    entries = []
    try:
        with open(RECEIVE_BUFFER_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if isinstance(data, dict):
                        entries.append(data)
                except (json.JSONDecodeError, TypeError):
                    continue
    except (FileNotFoundError, OSError):
        pass
    return entries


@mcp.tool()
async def discord_receive_status() -> str:
    """Check Discord receive daemon status.

    Returns the receive daemon's connection state, receive statistics,
    buffer contents summary, and allow list configuration.
    Reads from receive_state.json and receive_config.json written
    by the daemon process.
    """
    state = _load_receive_state()
    config = _load_receive_config()
    buffer_entries = _load_receive_buffer()

    lines = ["Discord Receive Daemon Status:"]

    # Connection state
    if not state:
        lines.append("  Daemon: not running (no state file)")
    else:
        conn = state.get("connection_state", "unknown")
        lines.append(f"  Daemon: {conn}")
        if state.get("bot_name"):
            lines.append(f"  Bot: {state['bot_name']}")
        if state.get("started_at"):
            lines.append(f"  Started: {state['started_at']}")
        if state.get("last_heartbeat"):
            lines.append(f"  Last heartbeat: {state['last_heartbeat']}")
        reconnects = state.get("reconnect_count", 0)
        if reconnects > 0:
            lines.append(f"  Reconnects: {reconnects}")

    # Receive statistics
    stats = state.get("stats", {})
    if stats:
        lines.append("  Receive stats:")
        lines.append(f"    Messages received: {stats.get('messages_received', 0)}")
        lines.append(f"    Messages filtered: {stats.get('messages_filtered', 0)}")
        lines.append(f"    Messages processed: {stats.get('messages_processed', 0)}")
    else:
        lines.append("  Receive stats: none")

    # Buffer summary
    if buffer_entries:
        status_counts: Dict[str, int] = {}
        for entry in buffer_entries:
            s = entry.get("status", "unknown")
            status_counts[s] = status_counts.get(s, 0) + 1
        lines.append(f"  Buffer: {len(buffer_entries)} entries")
        for s, c in sorted(status_counts.items()):
            lines.append(f"    {s}: {c}")
    else:
        lines.append("  Buffer: empty")

    # Allow list
    allowed_users = config.get("allowed_users", [])
    allowed_channels = config.get("allowed_channels", [])
    if allowed_users or allowed_channels:
        lines.append(f"  Allowed users: {', '.join(allowed_users) if allowed_users else 'none'}")
        lines.append(f"  Allowed channels: {', '.join(allowed_channels) if allowed_channels else 'none'}")
    else:
        lines.append("  Allow list: empty (all messages rejected)")

    return "\n".join(lines)


@mcp.tool()
async def discord_receive_allow(
    id: str,
    id_type: str = "user",
) -> str:
    """Add a user or channel to the receive allow list.

    Messages from IDs not on the allow list are rejected by the daemon.
    Changes take effect when the daemon reloads config (next message check).

    Args:
        id: Discord user ID or channel ID to allow.
        id_type: "user" or "channel" (default: "user").
    """
    if not id or not id.strip():
        return "ERROR: id cannot be empty."

    id = id.strip()
    id_type = id_type.strip().lower()

    if id_type not in ("user", "channel"):
        return "ERROR: id_type must be 'user' or 'channel'."

    config = _load_receive_config()

    if id_type == "user":
        users = config.get("allowed_users", [])
        if id in users:
            return f"User {id} is already in the allow list."
        users.append(id)
        config["allowed_users"] = users
    else:
        channels = config.get("allowed_channels", [])
        if id in channels:
            return f"Channel {id} is already in the allow list."
        channels.append(id)
        config["allowed_channels"] = channels

    _save_receive_config(config)

    total = len(config.get("allowed_users", [])) + len(config.get("allowed_channels", []))
    return (
        f"Added {id_type} {id} to allow list.\n"
        f"  Total allowed: {total} ({len(config.get('allowed_users', []))} users, "
        f"{len(config.get('allowed_channels', []))} channels)"
    )


@mcp.tool()
async def discord_receive_pending(
    limit: int = 20,
) -> str:
    """Check pending (unprocessed) messages in the receive buffer.

    Shows messages waiting to be processed by the receive daemon's
    CLI execution loop.

    Args:
        limit: Maximum number of entries to show (default: 20, max: 100).
    """
    limit = max(1, min(100, limit))
    entries = _load_receive_buffer()

    # Filter to pending/failed (retriable) entries
    pending = [e for e in entries if e.get("status") in ("pending", "failed")]

    if not pending:
        total = len(entries)
        return f"No pending messages in buffer. (Total entries: {total})"

    lines = [f"Pending messages: {len(pending)} (showing up to {limit})"]

    for entry in pending[:limit]:
        eid = entry.get("id", "?")[:8]
        sender = entry.get("sender_id", "?")
        status = entry.get("status", "?")
        received = entry.get("received_at", "?")
        content = entry.get("content", "")
        preview = content[:100] + ("..." if len(content) > 100 else "")
        retry = entry.get("retry_count", 0)

        lines.append(f"\n  [{eid}] status={status} sender={sender}")
        lines.append(f"    received: {received}")
        if retry > 0:
            lines.append(f"    retries: {retry}")
        lines.append(f"    preview: {preview}")

    return "\n".join(lines)


def main():
    print("Discord messaging MCP server starting on stdio...", file=sys.stderr)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
