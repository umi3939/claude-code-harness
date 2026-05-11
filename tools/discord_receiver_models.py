"""
Discord receiver models, constants, helpers, and I/O functions.

Shared base layer for all discord_receiver_* modules.
No circular dependencies — this module depends on nothing else in the receiver.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ═══════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════

DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_GATEWAY_VERSION = "10"
DISCORD_GATEWAY_ENCODING = "json"

# Project root (tools/ の親ディレクトリ)
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))

from file_io import resolve_project_root  # noqa: E402

_PROJECT_ROOT = resolve_project_root()

# Data directory
DISCORD_DATA_DIR = os.path.join(_PROJECT_ROOT, "discord_data")
RECEIVE_CONFIG_FILE = os.path.join(DISCORD_DATA_DIR, "receive_config.json")
RECEIVE_BUFFER_FILE = os.path.join(DISCORD_DATA_DIR, "receive_buffer.jsonl")
RECEIVE_LOG_FILE = os.path.join(DISCORD_DATA_DIR, "receive_log.jsonl")
RECEIVE_STATE_FILE = os.path.join(DISCORD_DATA_DIR, "receive_state.json")

# Safety valve defaults
DEFAULT_BUFFER_MAX_SIZE = 100
DEFAULT_MESSAGE_MAX_LENGTH = 4000
DEFAULT_RECEIVE_LOG_MAX_BYTES = 1_000_000  # 1MB
DEFAULT_RECEIVE_LOG_MAX_LINES = 1000
DEFAULT_LOG_BODY_TRUNCATE = 200  # Characters of message body kept in log

# Phase 2: CLI execution defaults
DEFAULT_CLI_TIMEOUT_SECONDS = 300
DEFAULT_RATE_LIMIT_GLOBAL_MAX = 10  # Max CLI executions per window
DEFAULT_RATE_LIMIT_GLOBAL_WINDOW = 3600  # 1 hour window (seconds)
DEFAULT_RATE_LIMIT_PER_SENDER_MAX = 3  # Max per sender per window
DEFAULT_RATE_LIMIT_PER_SENDER_WINDOW = 3600  # 1 hour window (seconds)
DEFAULT_MAX_RETRIES = 2  # Max retries for failed entries
DEFAULT_RESPONSE_MAX_LENGTH = 2000  # Discord message limit
DEFAULT_RESPONSE_MAX_SPLITS = 10  # Max message chunks for long responses
ALLOWED_PERMISSION_MODES = frozenset({"plan", "default"})  # Whitelist
BLOCKED_PERMISSION_MODES = frozenset({"bypassPermissions"})  # Explicit deny

DEFAULT_PROMPT_TEMPLATE = (
    "You are responding to a Discord DM. Keep your reply concise and natural.\n"
    "Do NOT use emojis. Respond in the same language as the message.\n\n"
    "Message from user:\n{message}"
)

# Gateway reconnect
MAX_RECONNECT_ATTEMPTS = 10
RECONNECT_BASE_DELAY = 1.0  # seconds
RECONNECT_MAX_DELAY = 60.0  # seconds

# Gateway opcodes
OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_STATUS_UPDATE = 3
OP_VOICE_STATE = 4
OP_RESUME = 6
OP_RECONNECT = 7
OP_REQUEST_MEMBERS = 8
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11

# Gateway intents
INTENT_GUILDS = 1 << 0
INTENT_GUILD_MESSAGES = 1 << 9
INTENT_GUILD_MESSAGE_CONTENT = 1 << 15
INTENT_DIRECT_MESSAGES = 1 << 12
INTENT_DIRECT_MESSAGE_CONTENT = 1 << 15  # Same bit covers both

# Combined intents for receiving messages with content
REQUIRED_INTENTS = (
    INTENT_GUILDS
    | INTENT_GUILD_MESSAGES
    | INTENT_GUILD_MESSAGE_CONTENT
    | INTENT_DIRECT_MESSAGES
)


# ═══════════════════════════════════════════════════════════════
# File I/O helpers (shared patterns from cron_scheduler)
# ═══════════════════════════════════════════════════════════════


def _ensure_dir(dir_path: str) -> None:
    """Create directory if it doesn't exist."""
    os.makedirs(dir_path, exist_ok=True)


def _now_iso() -> str:
    """Return current time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: str) -> Optional[datetime]:
    """Parse ISO-8601 string to datetime, returning None on failure."""
    if not s or not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# ═══════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════


@dataclass
class ReceiveConfig:
    """Receive configuration (persistent)."""
    allowed_users: List[str] = field(default_factory=list)
    allowed_channels: List[str] = field(default_factory=list)
    message_max_length: int = DEFAULT_MESSAGE_MAX_LENGTH
    buffer_max_size: int = DEFAULT_BUFFER_MAX_SIZE
    log_body_truncate: int = DEFAULT_LOG_BODY_TRUNCATE
    # Phase 2 fields
    prompt_template: str = DEFAULT_PROMPT_TEMPLATE
    permission_mode: str = "plan"
    cli_timeout_seconds: int = DEFAULT_CLI_TIMEOUT_SECONDS
    rate_limit_global_max: int = DEFAULT_RATE_LIMIT_GLOBAL_MAX
    rate_limit_global_window: int = DEFAULT_RATE_LIMIT_GLOBAL_WINDOW
    rate_limit_per_sender_max: int = DEFAULT_RATE_LIMIT_PER_SENDER_MAX
    rate_limit_per_sender_window: int = DEFAULT_RATE_LIMIT_PER_SENDER_WINDOW
    max_retries: int = DEFAULT_MAX_RETRIES
    # Security sanitizer fields
    security_injection_mode: str = "flag"      # "flag" or "block"
    security_sanitize_mode: str = "escape"     # "escape" or "remove"
    security_fail_open: bool = True            # fail-open by default
    # Personality context injection (Phase 1: G1)
    personality_enabled: bool = True           # Enable personality context injection

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ReceiveConfig":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)

    def is_allowed(self, user_id: str, channel_id: str) -> bool:
        """Check if a sender is in the allow list.

        Returns True if user_id is in allowed_users OR
        channel_id is in allowed_channels.
        Returns False if both lists are empty (default deny-all).
        """
        if not self.allowed_users and not self.allowed_channels:
            return False
        if user_id in self.allowed_users:
            return True
        if channel_id in self.allowed_channels:
            return True
        return False


# Buffer entry status
STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_DISCARDED = "discarded"


@dataclass
class BufferEntry:
    """A single message in the receive buffer."""
    id: str = ""
    message_id: str = ""
    sender_id: str = ""
    sender_type: str = ""  # "dm" or "channel"
    channel_id: str = ""
    content: str = ""
    received_at: str = ""
    status: str = STATUS_PENDING
    result: str = ""  # processing result summary
    retry_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BufferEntry":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)


@dataclass
class ReceiveLogEntry:
    """A single receive log entry."""
    timestamp: str = ""
    sender_id: str = ""
    channel_id: str = ""
    message_id: str = ""
    body_preview: str = ""  # Truncated message body
    filter_result: str = ""  # "passed" / "rejected"
    reject_reason: str = ""  # Reason for rejection if rejected
    processing_result: str = ""  # For Phase 2

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return {k: v for k, v in d.items() if v}  # Drop empty strings


# ═══════════════════════════════════════════════════════════════
# Config management
# ═══════════════════════════════════════════════════════════════


def load_receive_config() -> ReceiveConfig:
    """Load receive config from file.

    Uses module-level RECEIVE_CONFIG_FILE via sys.modules lookup so that
    patch("discord_receiver_models.RECEIVE_CONFIG_FILE", ...) works correctly.
    """
    _self = sys.modules[__name__]
    try:
        with open(_self.RECEIVE_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.loads(f.read())
        if isinstance(data, dict):
            return ReceiveConfig.from_dict(data)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return ReceiveConfig()


def save_receive_config(config: ReceiveConfig) -> None:
    """Save receive config to file."""
    _self = sys.modules[__name__]
    _ensure_dir(_self.DISCORD_DATA_DIR)
    with open(_self.RECEIVE_CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(json.dumps(config.to_dict(), indent=2, ensure_ascii=False))


# ═══════════════════════════════════════════════════════════════
# Receive State (persistent daemon state)
# ═══════════════════════════════════════════════════════════════


def save_receive_state(state: Dict[str, Any]) -> None:
    """Save receiver state to file."""
    _self = sys.modules[__name__]
    _ensure_dir(_self.DISCORD_DATA_DIR)
    with open(_self.RECEIVE_STATE_FILE, "w", encoding="utf-8") as f:
        f.write(json.dumps(state, indent=2, ensure_ascii=False))


def load_receive_state() -> Dict[str, Any]:
    """Load receiver state from file."""
    _self = sys.modules[__name__]
    try:
        with open(_self.RECEIVE_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.loads(f.read())
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {}


# ═══════════════════════════════════════════════════════════════
# Gateway Token Resolution (shared with send side)
# ═══════════════════════════════════════════════════════════════


def resolve_bot_token() -> Optional[str]:
    """Resolve bot token from DISCORD_BOT_TOKEN environment variable.

    Tokens are never read from config files.
    """
    env_token = os.environ.get("DISCORD_BOT_TOKEN")
    if env_token:
        return env_token

    return None
