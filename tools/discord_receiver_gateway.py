"""
Discord Gateway WebSocket client.

Handles WebSocket connection, heartbeat, identify/resume, message dispatch,
and reconnection with exponential backoff.
"""

from __future__ import annotations

import asyncio
import json
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

from discord_receiver_models import (
    DISCORD_API_BASE,
    DISCORD_GATEWAY_ENCODING,
    DISCORD_GATEWAY_VERSION,
    MAX_RECONNECT_ATTEMPTS,
    OP_DISPATCH,
    OP_HEARTBEAT,
    OP_HEARTBEAT_ACK,
    OP_HELLO,
    OP_IDENTIFY,
    OP_INVALID_SESSION,
    OP_RECONNECT,
    OP_RESUME,
    RECONNECT_BASE_DELAY,
    RECONNECT_MAX_DELAY,
    REQUIRED_INTENTS,
    _now_iso,
)


async def get_gateway_url(token: str) -> str:
    """Get Gateway WebSocket URL from Discord REST API.

    Uses /gateway/bot endpoint which also returns shard info.
    """
    if aiohttp is None:
        raise RuntimeError("aiohttp is required. Install with: pip install aiohttp")

    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    }
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(f"{DISCORD_API_BASE}/gateway/bot") as resp:
            if resp.status == 401:
                raise RuntimeError("Invalid bot token (401 Unauthorized)")
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(
                    f"Failed to get gateway URL (status {resp.status}): {body[:200]}"
                )
            data = await resp.json()
            url = data.get("url", "")
            if not url:
                raise RuntimeError("Gateway URL not found in response")
            return url


async def get_bot_user(token: str) -> Dict[str, Any]:
    """Get bot user info from Discord REST API."""
    if aiohttp is None:
        raise RuntimeError("aiohttp is required")

    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    }
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(f"{DISCORD_API_BASE}/users/@me") as resp:
            if resp.status != 200:
                raise RuntimeError(f"Failed to get bot info (status {resp.status})")
            return await resp.json()


class DiscordGatewayClient:
    """Discord Gateway WebSocket client.

    Manages:
    - WebSocket connection to Discord Gateway
    - Heartbeat maintenance
    - Identify/Resume
    - Message event dispatch
    - Reconnection with backoff
    """

    def __init__(self, token: str, intents: int = REQUIRED_INTENTS,
                 logger=None, on_reconnect=None):
        self.token = token
        self.intents = intents
        self.logger = logger
        self._on_reconnect = on_reconnect  # callback after successful reconnect

        # Connection state (volatile)
        self._ws = None
        self._session_id: Optional[str] = None
        self._sequence: Optional[int] = None
        self._heartbeat_interval: Optional[float] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._last_heartbeat_ack: bool = True
        self._reconnect_count: int = 0
        self._gateway_url: Optional[str] = None
        self._bot_id: Optional[str] = None
        self._bot_name: Optional[str] = None
        self._connected: bool = False
        self._should_resume: bool = False
        self._resume_gateway_url: Optional[str] = None

        # Callback
        self._on_message_create = None

        # Stats
        self._messages_received: int = 0
        self._messages_filtered: int = 0
        self._messages_buffered: int = 0
        self._connected_since: Optional[str] = None

    def _log(self, level: str, msg: str) -> None:
        """Log a message if logger is available."""
        if self.logger:
            getattr(self.logger, level, self.logger.info)(msg)

    def set_on_message_create(self, callback) -> None:
        """Set callback for MESSAGE_CREATE events.

        Callback signature: async def callback(message_data: dict) -> None
        """
        self._on_message_create = callback

    async def connect(self) -> None:
        """Establish initial Gateway connection.

        Gets gateway URL, connects WebSocket, identifies.
        """
        if websockets is None:
            raise RuntimeError("websockets package is required. Install with: pip install websockets")

        # Get bot info
        bot_info = await get_bot_user(self.token)
        self._bot_id = bot_info.get("id", "")
        self._bot_name = bot_info.get("username", "Unknown")
        self._log("info", f"Bot user: {self._bot_name} (ID: {self._bot_id})")

        # Get gateway URL
        self._gateway_url = await get_gateway_url(self.token)
        self._log("info", f"Gateway URL: {self._gateway_url}")

        # Connect
        await self._connect_ws()

    async def _connect_ws(self) -> None:
        """Connect to Gateway WebSocket and begin event loop."""
        url = self._resume_gateway_url or self._gateway_url
        if not url:
            raise RuntimeError("No gateway URL available")

        ws_url = f"{url}?v={DISCORD_GATEWAY_VERSION}&encoding={DISCORD_GATEWAY_ENCODING}"
        self._log("info", f"Connecting to Gateway: {ws_url}")

        self._ws = await ws_connect(ws_url)
        self._connected = True
        self._connected_since = _now_iso()
        self._reconnect_count = 0

    async def run(self) -> None:
        """Main event loop. Processes Gateway events until disconnected.

        Handles reconnection automatically up to MAX_RECONNECT_ATTEMPTS.
        Uses try-finally to ensure heartbeat task is always cleaned up.
        """
        while True:
            try:
                await self._event_loop()
                # Normal return = reconnect signal (OP_RECONNECT or OP_INVALID_SESSION)
                self._connected = False
                self._log("info", "Reconnecting after gateway request...")
                await asyncio.sleep(RECONNECT_BASE_DELAY)
                try:
                    if self._ws:
                        try:
                            await self._ws.close()
                        except Exception:
                            pass
                    await self._connect_ws()
                    if self._on_reconnect:
                        try:
                            self._on_reconnect()
                        except Exception as e:
                            self._log("debug", f"Reconnect callback warning: {e}")
                except Exception as conn_err:
                    self._log("error", f"Reconnect after gateway request failed: {conn_err}")
                    # Fall through to while loop -> next iteration
                continue
            except (websockets.ConnectionClosed, ConnectionError, OSError) as e:
                self._connected = False
                self._log("warning", f"Gateway connection lost: {e}")

                if self._reconnect_count >= MAX_RECONNECT_ATTEMPTS:
                    self._log("error",
                              f"Max reconnect attempts ({MAX_RECONNECT_ATTEMPTS}) reached. Stopping.")
                    raise

                self._reconnect_count += 1
                delay = min(
                    RECONNECT_BASE_DELAY * (2 ** (self._reconnect_count - 1)),
                    RECONNECT_MAX_DELAY
                )
                self._log("info",
                          f"Reconnecting (attempt {self._reconnect_count}/{MAX_RECONNECT_ATTEMPTS}) "
                          f"in {delay:.1f}s...")
                await asyncio.sleep(delay)

                try:
                    self._should_resume = self._session_id is not None
                    await self._connect_ws()
                    # Notify on successful reconnect (for state persistence)
                    if self._on_reconnect:
                        try:
                            self._on_reconnect()
                        except Exception as e:
                            self._log("debug", f"Reconnect callback warning: {e}")
                except Exception as conn_err:
                    self._log("error", f"Reconnection failed: {conn_err}")
                    continue

            except asyncio.CancelledError:
                self._log("info", "Event loop cancelled")
                break
            except Exception as e:
                self._log("error", f"Unexpected error in event loop: {e}")
                raise
            finally:
                # Ensure heartbeat task is always cleaned up
                if self._heartbeat_task and not self._heartbeat_task.done():
                    self._heartbeat_task.cancel()
                    try:
                        await self._heartbeat_task
                    except (asyncio.CancelledError, Exception):
                        pass

    async def _event_loop(self) -> None:
        """Process Gateway events from WebSocket."""
        async for raw_message in self._ws:
            try:
                data = json.loads(raw_message)
            except (json.JSONDecodeError, TypeError):
                continue

            op = data.get("op")
            event_data = data.get("d")
            seq = data.get("s")
            event_name = data.get("t")

            # Update sequence number
            if seq is not None:
                self._sequence = seq

            if op == OP_HELLO:
                await self._handle_hello(event_data)
            elif op == OP_HEARTBEAT:
                await self._send_heartbeat()
            elif op == OP_HEARTBEAT_ACK:
                self._last_heartbeat_ack = True
            elif op == OP_RECONNECT:
                self._log("info", "Gateway requested reconnect")
                self._should_resume = True
                await self._ws.close()
                return
            elif op == OP_INVALID_SESSION:
                resumable = event_data if isinstance(event_data, bool) else False
                self._log("info", f"Invalid session (resumable={resumable})")
                if not resumable:
                    self._session_id = None
                    self._sequence = None
                self._should_resume = resumable
                await asyncio.sleep(1 + 4 * (1 - int(resumable)))  # 1s or 5s
                await self._ws.close()
                return
            elif op == OP_DISPATCH:
                await self._handle_dispatch(event_name, event_data)

    async def _handle_hello(self, data: Dict[str, Any]) -> None:
        """Handle HELLO event: start heartbeat and identify/resume."""
        self._heartbeat_interval = data.get("heartbeat_interval", 41250) / 1000.0
        self._log("info", f"Heartbeat interval: {self._heartbeat_interval:.1f}s")

        # Start heartbeat
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        # Identify or Resume
        if self._should_resume and self._session_id:
            await self._send_resume()
        else:
            await self._send_identify()

    async def _send_identify(self) -> None:
        """Send IDENTIFY payload."""
        payload = {
            "op": OP_IDENTIFY,
            "d": {
                "token": self.token,
                "intents": self.intents,
                "properties": {
                    "os": sys.platform,
                    "browser": "claude-discord-receiver",
                    "device": "claude-discord-receiver",
                },
            },
        }
        await self._ws.send(json.dumps(payload))
        self._log("info", "Sent IDENTIFY")

    async def _send_resume(self) -> None:
        """Send RESUME payload."""
        payload = {
            "op": OP_RESUME,
            "d": {
                "token": self.token,
                "session_id": self._session_id,
                "seq": self._sequence,
            },
        }
        await self._ws.send(json.dumps(payload))
        self._log("info", f"Sent RESUME (session={self._session_id}, seq={self._sequence})")

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats."""
        try:
            # Initial jitter heartbeat (use secrets for S311 compliance)
            import secrets
            await asyncio.sleep(self._heartbeat_interval * (secrets.randbelow(1000) / 1000.0))
            await self._send_heartbeat()

            while True:
                await asyncio.sleep(self._heartbeat_interval)
                if not self._last_heartbeat_ack:
                    self._log("warning", "No heartbeat ACK received, reconnecting...")
                    if self._ws:
                        await self._ws.close()
                    return
                await self._send_heartbeat()
        except asyncio.CancelledError:
            pass

    async def _send_heartbeat(self) -> None:
        """Send a heartbeat."""
        self._last_heartbeat_ack = False
        payload = {"op": OP_HEARTBEAT, "d": self._sequence}
        try:
            await self._ws.send(json.dumps(payload))
        except Exception as e:
            self._log("debug", f"Heartbeat send failed: {e}")

    async def _handle_dispatch(self, event_name: str,
                               event_data: Dict[str, Any]) -> None:
        """Handle DISPATCH events."""
        if event_name == "READY":
            self._session_id = event_data.get("session_id")
            self._resume_gateway_url = event_data.get("resume_gateway_url")
            self._log("info", f"READY (session={self._session_id})")
            self._should_resume = False

        elif event_name == "RESUMED":
            self._log("info", "Session RESUMED")
            self._should_resume = False

        elif event_name == "MESSAGE_CREATE":
            self._messages_received += 1
            if self._on_message_create:
                try:
                    await self._on_message_create(event_data)
                except Exception as e:
                    self._log("error", f"Error in message handler: {e}")

    async def close(self) -> None:
        """Close the Gateway connection."""
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception as e:
                self._log("debug", f"WS close warning: {e}")
        self._connected = False

    def get_stats(self) -> Dict[str, Any]:
        """Get connection statistics."""
        return {
            "connected": self._connected,
            "connected_since": self._connected_since,
            "bot_id": self._bot_id,
            "bot_name": self._bot_name,
            "session_id": self._session_id,
            "sequence": self._sequence,
            "reconnect_count": self._reconnect_count,
            "messages_received": self._messages_received,
            "messages_filtered": self._messages_filtered,
            "messages_buffered": self._messages_buffered,
        }
