"""
Discord CLI executor, response sender, and prompt template.

Handles Claude CLI subprocess execution with rate limiting,
Discord REST API response sending, and prompt template rendering.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional, List, Dict, Any

try:
    import aiohttp
except ImportError:
    aiohttp = None

from discord_receiver_models import (
    ReceiveConfig,
    DISCORD_API_BASE,
    DEFAULT_PROMPT_TEMPLATE,
    DEFAULT_RESPONSE_MAX_LENGTH,
    DEFAULT_RESPONSE_MAX_SPLITS,
    ALLOWED_PERMISSION_MODES,
    BLOCKED_PERMISSION_MODES,
)


class PromptTemplate:
    """Manages prompt template for wrapping received messages.

    The template provides a security boundary: received messages are always
    embedded within the template, never passed directly to CLI.
    """

    def __init__(self, template: str = DEFAULT_PROMPT_TEMPLATE):
        self.template = template

    def render(self, message: str, sender_id: str = "") -> str:
        """Render the template with a message embedded.

        Args:
            message: The Discord message content.
            sender_id: The sender's Discord user ID.

        Returns:
            The rendered prompt string.
        """
        # Replace sender_id first, then message -- prevents user message
        # containing "{sender_id}" from being substituted with real ID
        result = self.template.replace("{sender_id}", sender_id)
        result = result.replace("{message}", message)
        return result


class CLIExecutor:
    """Executes Claude CLI as async subprocess with rate limiting.

    - Uses asyncio.create_subprocess_exec to avoid blocking the event loop
    - Enforces permission mode whitelist (bypassPermissions structurally rejected)
    - Enforces global and per-sender rate limits
    - Enforces CLI execution timeout
    """

    def __init__(self, config: ReceiveConfig, logger=None):
        self.config = config
        self.logger = logger
        self._global_timestamps: List[float] = []
        self._sender_timestamps: Dict[str, List[float]] = {}

    def _log(self, level: str, msg: str) -> None:
        if self.logger:
            getattr(self.logger, level, self.logger.info)(msg)

    def validate_permission_mode(self, mode: str) -> bool:
        """Validate that the permission mode is allowed.

        Returns True if allowed, False if blocked.
        bypassPermissions and any unknown modes are rejected.
        """
        if mode in BLOCKED_PERMISSION_MODES:
            return False
        if mode not in ALLOWED_PERMISSION_MODES:
            return False
        return True

    def _clean_timestamps(self, timestamps: List[float],
                          window: int) -> List[float]:
        """Remove expired timestamps outside the rate limit window."""
        now = time.monotonic()
        cutoff = now - window
        return [t for t in timestamps if t > cutoff]

    def check_rate_limit(self, sender_id: str = "") -> tuple[bool, str]:
        """Check if CLI execution is allowed under rate limits.

        Returns (allowed, reason).
        """
        now = time.monotonic()

        # Global rate limit
        self._global_timestamps = self._clean_timestamps(
            self._global_timestamps, self.config.rate_limit_global_window
        )
        if len(self._global_timestamps) >= self.config.rate_limit_global_max:
            return False, "global_rate_limit"

        # Per-sender rate limit
        if sender_id:
            sender_ts = self._sender_timestamps.get(sender_id, [])
            sender_ts = self._clean_timestamps(
                sender_ts, self.config.rate_limit_per_sender_window
            )
            self._sender_timestamps[sender_id] = sender_ts
            if len(sender_ts) >= self.config.rate_limit_per_sender_max:
                return False, f"sender_rate_limit ({sender_id})"

        return True, ""

    def record_execution(self, sender_id: str = "") -> None:
        """Record a CLI execution timestamp for rate limiting."""
        now = time.monotonic()
        self._global_timestamps.append(now)
        if sender_id:
            if sender_id not in self._sender_timestamps:
                self._sender_timestamps[sender_id] = []
            self._sender_timestamps[sender_id].append(now)

    async def execute(self, prompt: str) -> tuple[bool, str, str]:
        """Execute Claude CLI with the given prompt.

        Returns (success, output, error).
        """
        mode = self.config.permission_mode
        if not self.validate_permission_mode(mode):
            return False, "", f"Permission mode '{mode}' is not allowed"

        try:
            proc = await asyncio.create_subprocess_exec(
                "claude", "--print",
                "--permission-mode", mode,
                "--no-session-persistence",
                "-p", prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self.config.cli_timeout_seconds,
                )
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await proc.wait()
                except ProcessLookupError:
                    pass
                return False, "", f"Timeout after {self.config.cli_timeout_seconds}s"

            output = stdout.decode("utf-8", errors="replace") if stdout else ""
            err = stderr.decode("utf-8", errors="replace") if stderr else ""

            if proc.returncode == 0:
                return True, output, ""
            else:
                return False, output, err or f"Exit code: {proc.returncode}"

        except FileNotFoundError:
            return False, "", "claude CLI not found"
        except OSError as e:
            return False, "", f"OS error: {e}"


class ResponseSender:
    """Sends CLI output back to Discord via REST API.

    Independent HTTP client -- does not import from discord_mcp_server.py
    (runs in a separate process).
    """

    def __init__(self, token: str, logger=None):
        self.token = token
        self.logger = logger
        self._session: Optional[aiohttp.ClientSession] = None

    def _log(self, level: str, msg: str) -> None:
        if self.logger:
            getattr(self.logger, level, self.logger.info)(msg)

    async def _ensure_session(self) -> None:
        """Create aiohttp session if needed."""
        if aiohttp is None:
            raise RuntimeError("aiohttp is required")
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bot {self.token}",
                    "Content-Type": "application/json",
                }
            )

    async def _resolve_channel(self, sender_id: str,
                                sender_type: str,
                                channel_id: str) -> str:
        """Resolve to a channel ID for sending.

        For DM: creates/gets DM channel with user.
        For channel: returns the channel_id directly.
        """
        await self._ensure_session()

        if sender_type == "channel":
            return channel_id

        # DM: open DM channel
        async with self._session.post(
            f"{DISCORD_API_BASE}/users/@me/channels",
            json={"recipient_id": sender_id}
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(
                    f"Failed to open DM channel (status {resp.status}): {body[:200]}"
                )
            data = await resp.json()
            return data["id"]

    async def _send_to_channel(self, channel_id: str, content: str) -> None:
        """Send a message to a specific channel."""
        await self._ensure_session()

        async with self._session.post(
            f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
            json={"content": content}
        ) as resp:
            if resp.status == 429:
                body = await resp.json()
                retry_after = body.get("retry_after", "unknown")
                raise RuntimeError(
                    f"Discord rate limited. Retry after {retry_after}s."
                )
            elif resp.status not in (200, 201):
                body = await resp.text()
                raise RuntimeError(
                    f"Discord API error (status {resp.status}): {body[:200]}"
                )

    async def send_response(self, text: str, sender_id: str,
                             sender_type: str, channel_id: str) -> None:
        """Send CLI output back to the original sender.

        Long messages are split into chunks of DEFAULT_RESPONSE_MAX_LENGTH.
        """
        if not text.strip():
            text = "(No output)"

        # Split into chunks
        chunks = []
        remaining = text
        while remaining:
            if len(remaining) <= DEFAULT_RESPONSE_MAX_LENGTH:
                chunks.append(remaining)
                break
            # Find last newline within limit for cleaner splits
            split_pos = remaining[:DEFAULT_RESPONSE_MAX_LENGTH].rfind("\n")
            if split_pos < DEFAULT_RESPONSE_MAX_LENGTH // 2:
                split_pos = DEFAULT_RESPONSE_MAX_LENGTH
            chunks.append(remaining[:split_pos])
            remaining = remaining[split_pos:].lstrip("\n")

        # Enforce max splits
        if len(chunks) > DEFAULT_RESPONSE_MAX_SPLITS:
            chunks = chunks[:DEFAULT_RESPONSE_MAX_SPLITS]
            chunks[-1] += "\n...(truncated)"

        try:
            resolved_channel = await self._resolve_channel(
                sender_id, sender_type, channel_id
            )
            for chunk in chunks:
                await self._send_to_channel(resolved_channel, chunk)
        except Exception as e:
            self._log("error", f"Failed to send response: {e}")
            raise

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
