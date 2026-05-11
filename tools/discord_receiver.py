"""
Discord receiver core module.

Provides Gateway WebSocket connection, message filtering, receive buffer,
receive logging, CLI execution, and response sending for Discord message
reception and processing.

This is a generic Claude Code utility, not part of any specific project.

Phase 1: Receive infrastructure (Gateway, filter, buffer, log).
Phase 2: CLI execution and response sending.

Sub-modules:
- discord_receiver_models: Constants, dataclasses, helpers, I/O
- discord_receiver_gateway: Gateway WebSocket client
- discord_receiver_filter: Message filtering
- discord_receiver_buffer: Buffer and log
- discord_receiver_executor: CLI execution, response sending, prompt
- discord_receiver_consumer: Buffer consumer loop
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Dict, Optional

try:
    import aiohttp
except ImportError:
    aiohttp = None

try:
    import websockets
    from websockets.asyncio.client import connect as ws_connect
except ImportError:
    websockets = None
    ws_connect = None

# ═══════════════════════════════════════════════════════════════
# Re-export from discord_receiver_models
# ═══════════════════════════════════════════════════════════════

from discord_receiver_models import (  # noqa: E402
    STATUS_PENDING,
    BufferEntry,
    ReceiveConfig,
    ReceiveLogEntry,
    # Constants
    _ensure_dir,
    _now_iso,
)

# ═══════════════════════════════════════════════════════════════
# I/O functions (defined here so patch("discord_receiver.XXX") works)
# ═══════════════════════════════════════════════════════════════
# These functions look up file path constants from THIS module's namespace
# via sys.modules, so patch("discord_receiver.RECEIVE_CONFIG_FILE", ...)
# correctly affects the file path used.


def load_receive_config() -> ReceiveConfig:
    """Load receive config from file."""
    _mod = sys.modules[__name__]
    try:
        with open(_mod.RECEIVE_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.loads(f.read())
        if isinstance(data, dict):
            return ReceiveConfig.from_dict(data)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return ReceiveConfig()


def save_receive_config(config: ReceiveConfig) -> None:
    """Save receive config to file."""
    _mod = sys.modules[__name__]
    _ensure_dir(_mod.DISCORD_DATA_DIR)
    with open(_mod.RECEIVE_CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(json.dumps(config.to_dict(), indent=2, ensure_ascii=False))


# ═══════════════════════════════════════════════════════════════
# Re-export from discord_receiver_buffer and discord_receiver_filter
# ═══════════════════════════════════════════════════════════════

from discord_receiver_buffer import ReceiveBuffer, ReceiveLog  # noqa: E402
from discord_receiver_filter import MessageFilter  # noqa: E402

# ═══════════════════════════════════════════════════════════════
# Receive State (persistent daemon state)
# ═══════════════════════════════════════════════════════════════


def save_receive_state(state: Dict[str, Any]) -> None:
    """Save receiver state to file."""
    _mod = sys.modules[__name__]
    _ensure_dir(_mod.DISCORD_DATA_DIR)
    with open(_mod.RECEIVE_STATE_FILE, "w", encoding="utf-8") as f:
        f.write(json.dumps(state, indent=2, ensure_ascii=False))


def load_receive_state() -> Dict[str, Any]:
    """Load receiver state from file."""
    _mod = sys.modules[__name__]
    try:
        with open(_mod.RECEIVE_STATE_FILE, "r", encoding="utf-8") as f:
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


# ═══════════════════════════════════════════════════════════════
# Re-export from discord_receiver_gateway
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# Re-export from discord_receiver_executor
# ═══════════════════════════════════════════════════════════════
from discord_receiver_executor import (  # noqa: E402
    CLIExecutor,
    PromptTemplate,
    ResponseSender,
)
from discord_receiver_gateway import (  # noqa: E402
    DiscordGatewayClient,
)

# ═══════════════════════════════════════════════════════════════
# Phase 2: Security Sanitizer (imported from security_sanitizer.py)
# ═══════════════════════════════════════════════════════════════
from security_sanitizer import (  # noqa: E402
    SecuritySanitizer,
)

# ═══════════════════════════════════════════════════════════════
# Phase 3: Message Event Hooks (imported from message_event_hooks.py)
# ═══════════════════════════════════════════════════════════════

try:
    from message_event_hooks import (  # noqa: E402
        DEFAULT_HOOK_CONFIG_PATH,
        DEFAULT_HOOK_LOG_PATH,
        EVENT_BUFFERED,
        EVENT_FILTERED,
        EVENT_RECEIVED,
        EVENT_SANITIZED,  # noqa: F401
        EVENT_SENT,  # noqa: F401
        HookDefinition,  # noqa: F401
        HookDispatcher,
        MessageEventContext,  # noqa: F401
        load_hook_definitions,
        normalize_discord_message,
    )
    _HOOKS_AVAILABLE = True
except ImportError:
    _HOOKS_AVAILABLE = False
    HookDispatcher = None  # type: ignore


# ═══════════════════════════════════════════════════════════════
# Re-export from discord_receiver_consumer
# ═══════════════════════════════════════════════════════════════

from discord_receiver_consumer import BufferConsumer  # noqa: E402

# ═══════════════════════════════════════════════════════════════
# Discord Receiver (orchestrates components)
# ═══════════════════════════════════════════════════════════════


class DiscordReceiver:
    """Orchestrates Gateway client, filter, buffer, log, and buffer consumer.

    This is the main entry point for the receive daemon.
    Phase 1: Gateway connection, filtering, buffering.
    Phase 2: CLI execution, response sending via BufferConsumer.
    """

    def __init__(self, token: str, logger=None):
        self.token = token
        self.logger = logger
        self.config = load_receive_config()
        _mod = sys.modules[__name__]
        self.buffer = ReceiveBuffer(
            buffer_path=_mod.RECEIVE_BUFFER_FILE,
            max_size=self.config.buffer_max_size,
        )
        self.receive_log = ReceiveLog(log_path=_mod.RECEIVE_LOG_FILE)
        self.gateway = DiscordGatewayClient(
            token=token, logger=logger,
            on_reconnect=self._save_state,
        )
        self.filter: Optional[MessageFilter] = None
        self._running = False
        # Phase 2 components
        self.template = PromptTemplate(self.config.prompt_template)
        self.executor = CLIExecutor(config=self.config, logger=logger)
        self.response_sender = ResponseSender(token=token, logger=logger)
        self.sanitizer = SecuritySanitizer(
            injection_mode=self.config.security_injection_mode,
            sanitize_mode=self.config.security_sanitize_mode,
            fail_open=self.config.security_fail_open,
            logger=logger,
        )
        # Phase 3: Message event hooks (optional)
        self.hook_dispatcher: Optional[HookDispatcher] = None
        if _HOOKS_AVAILABLE:
            hook_defs = load_hook_definitions(DEFAULT_HOOK_CONFIG_PATH)
            if hook_defs:
                self.hook_dispatcher = HookDispatcher(
                    hooks=hook_defs,
                    log_path=DEFAULT_HOOK_LOG_PATH,
                    logger=logger,
                )
        # Phase G1: Personality context injection (fail-open)
        self._personality_collector = None
        if self.config.personality_enabled:
            try:
                from bot_personality import create_collector
                _tools_dir = os.path.dirname(os.path.abspath(__file__))
                _project_root = os.path.dirname(_tools_dir)
                memory_dir = os.path.join(_project_root, "memory")
                self._personality_collector = create_collector(memory_dir=memory_dir)
                if logger:
                    logger.info("Personality context collector initialized")
            except Exception as e:
                if logger:
                    logger.warning(f"Personality collector init failed (fail-open): {e}")
        self.consumer = BufferConsumer(
            buffer=self.buffer,
            executor=self.executor,
            template=self.template,
            sender=self.response_sender,
            receive_log=self.receive_log,
            config=self.config,
            logger=logger,
            sanitizer=self.sanitizer,
            hook_dispatcher=self.hook_dispatcher,
            personality_collector=self._personality_collector,
        )
        self._consumer_task: Optional[asyncio.Task] = None

    def _log(self, level: str, msg: str) -> None:
        if self.logger:
            getattr(self.logger, level, self.logger.info)(msg)

    async def start(self) -> None:
        """Start the receiver: connect, begin receiving, start consumer."""
        self._running = True

        # Connect to Gateway (also fetches bot info)
        await self.gateway.connect()

        # Initialize filter with bot ID
        self.filter = MessageFilter(
            bot_id=self.gateway._bot_id or "",
            config=self.config,
        )

        # Set message handler
        self.gateway.set_on_message_create(self._handle_message)

        # Start buffer consumer task (Phase 2)
        self._consumer_task = asyncio.create_task(self.consumer.run())

        # Save initial state
        self._save_state()

        self._log("info", "Receiver started (with buffer consumer)")

        # Run Gateway event loop (blocking)
        try:
            await self.gateway.run()
        finally:
            self._running = False
            self.consumer.stop()
            if self._consumer_task and not self._consumer_task.done():
                self._consumer_task.cancel()
                try:
                    await self._consumer_task
                except asyncio.CancelledError:
                    pass
            self._save_state()

    async def _handle_message(self, message_data: Dict[str, Any]) -> None:
        """Handle a MESSAGE_CREATE event: filter and buffer."""
        author = message_data.get("author", {})
        sender_id = author.get("id", "")
        channel_id = message_data.get("channel_id", "")
        message_id = message_data.get("id", "")
        content = message_data.get("content", "")

        # Determine sender type
        # guild_id is absent for DM messages
        is_dm = "guild_id" not in message_data or message_data.get("guild_id") is None
        sender_type = "dm" if is_dm else "channel"

        # Dispatch message:received hook
        if self.hook_dispatcher and _HOOKS_AVAILABLE:
            ctx = normalize_discord_message(
                event=EVENT_RECEIVED, message_data=message_data, is_dm=is_dm)
            await self.hook_dispatcher.dispatch(ctx)

        # Apply filter
        passed, reject_reason = self.filter.check(message_data)

        # Dispatch message:filtered hook (both pass and reject)
        if self.hook_dispatcher and _HOOKS_AVAILABLE:
            ctx = normalize_discord_message(
                event=EVENT_FILTERED, message_data=message_data, is_dm=is_dm,
                filter_passed=passed, filter_reason=reject_reason or "")
            await self.hook_dispatcher.dispatch(ctx)

        # Log the receive event
        body_preview = content[:self.config.log_body_truncate] if content else ""
        log_entry = ReceiveLogEntry(
            timestamp=_now_iso(),
            sender_id=sender_id,
            channel_id=channel_id,
            message_id=message_id,
            body_preview=body_preview,
            filter_result="passed" if passed else "rejected",
            reject_reason=reject_reason,
        )
        self.receive_log.append(log_entry)

        if not passed:
            self.gateway._messages_filtered += 1
            self._log("debug", f"Message filtered: {reject_reason} (from {sender_id})")
            return

        # Add to buffer
        entry = BufferEntry(
            message_id=message_id,
            sender_id=sender_id,
            sender_type=sender_type,
            channel_id=channel_id,
            content=content[:self.config.message_max_length],
            received_at=_now_iso(),
            status=STATUS_PENDING,
        )
        added = self.buffer.add(entry)

        if added:
            self.gateway._messages_buffered += 1
            self._log("info",
                       f"Message buffered from {sender_id} ({sender_type}): "
                       f"{content[:50]}...")

            # Dispatch message:buffered hook
            if self.hook_dispatcher and _HOOKS_AVAILABLE:
                ctx = normalize_discord_message(
                    event=EVENT_BUFFERED, message_data=message_data, is_dm=is_dm,
                    buffer_entry_id=entry.id)
                await self.hook_dispatcher.dispatch(ctx)
        else:
            self.gateway._messages_filtered += 1
            self._log("warning", f"Buffer full, message from {sender_id} rejected")
            # Log the buffer-full rejection
            overflow_log = ReceiveLogEntry(
                timestamp=_now_iso(),
                sender_id=sender_id,
                channel_id=channel_id,
                message_id=message_id,
                body_preview=body_preview,
                filter_result="rejected",
                reject_reason="buffer_full",
            )
            self.receive_log.append(overflow_log)

    def _save_state(self) -> None:
        """Save current receiver state to file."""
        stats = self.gateway.get_stats()
        buffer_stats = self.buffer.get_stats()
        consumer_stats = self.consumer.get_stats()
        state = {
            "last_updated": _now_iso(),
            "running": self._running,
            "gateway": stats,
            "buffer": buffer_stats,
            "consumer": consumer_stats,
        }
        save_receive_state(state)

    async def stop(self) -> None:
        """Stop the receiver."""
        self._running = False
        self.consumer.stop()
        if self._consumer_task and not self._consumer_task.done():
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
        # Shutdown hook dispatcher (kill running subprocesses)
        if self.hook_dispatcher:
            await self.hook_dispatcher.shutdown()
        await self.response_sender.close()
        await self.gateway.close()
        self._save_state()
        self._log("info", "Receiver stopped")
