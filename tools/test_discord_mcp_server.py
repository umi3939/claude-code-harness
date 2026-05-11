"""
Tests for discord_mcp_server.py.

Tests the MCP tool functions directly (not via MCP transport).
Discord API calls are mocked via aiohttp.ClientSession mock.
"""

import asyncio
import json
import os
import shutil

# Setup sys.path
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)


def run_async(coro):
    """Helper to run async functions in tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ═══════════════════════════════════════════════════════════════
# Message splitting tests (pure functions, no mock needed)
# ═══════════════════════════════════════════════════════════════


class TestSplitMessage(unittest.TestCase):
    """Test message splitting logic."""

    def test_short_message_no_split(self):
        from discord_mcp_server import split_message
        result = split_message("Hello world")
        self.assertEqual(result, ["Hello world"])

    def test_exact_limit_no_split(self):
        from discord_mcp_server import split_message
        msg = "x" * 2000
        result = split_message(msg, limit=2000)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], msg)

    def test_split_at_paragraph_boundary(self):
        from discord_mcp_server import split_message
        para1 = "a" * 1000
        para2 = "b" * 1000
        msg = para1 + "\n\n" + para2
        result = split_message(msg, limit=2000)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], para1)
        self.assertEqual(result[1], para2)

    def test_split_at_line_boundary(self):
        from discord_mcp_server import split_message
        line1 = "a" * 1500
        line2 = "b" * 800
        msg = line1 + "\n" + line2
        result = split_message(msg, limit=2000)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], line1)
        self.assertEqual(result[1], line2)

    def test_force_split_no_boundary(self):
        from discord_mcp_server import split_message
        msg = "x" * 5000
        result = split_message(msg, limit=2000)
        self.assertGreater(len(result), 1)
        for chunk in result:
            self.assertLessEqual(len(chunk), 2000)

    def test_empty_message(self):
        from discord_mcp_server import split_message
        result = split_message("")
        self.assertEqual(result, [""])

    def test_max_splits_limit(self):
        from discord_mcp_server import MAX_SPLITS, split_message
        # Create a very long message that would require many splits
        msg = "x" * (2000 * (MAX_SPLITS + 5))
        result = split_message(msg, limit=2000)
        self.assertLessEqual(len(result), MAX_SPLITS)


# ═══════════════════════════════════════════════════════════════
# Rate limiter tests
# ═══════════════════════════════════════════════════════════════


class TestRateLimiter(unittest.TestCase):
    """Test rate limiter logic."""

    def test_allows_under_limit(self):
        from discord_mcp_server import RateLimiter
        limiter = RateLimiter(window_ms=60000, max_requests=5)
        for _ in range(4):
            self.assertFalse(limiter.is_rate_limited())
            limiter.record_send()
        # 4 sends, limit is 5, should not be limited
        self.assertFalse(limiter.is_rate_limited())

    def test_blocks_over_limit(self):
        from discord_mcp_server import RateLimiter
        limiter = RateLimiter(window_ms=60000, max_requests=3)
        for _ in range(3):
            limiter.record_send()
        self.assertTrue(limiter.is_rate_limited())

    def test_expires_old_entries(self):
        from discord_mcp_server import RateLimiter
        limiter = RateLimiter(window_ms=100, max_requests=2)
        limiter.record_send()
        limiter.record_send()
        self.assertTrue(limiter.is_rate_limited())
        # Force old timestamps
        limiter._timestamps = [time.time() * 1000 - 200]
        self.assertFalse(limiter.is_rate_limited())


# ═══════════════════════════════════════════════════════════════
# Config file tests
# ═══════════════════════════════════════════════════════════════


class TestConfig(unittest.TestCase):
    """Test config load/save."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.orig_config = None
        # Patch CONFIG_FILE
        import discord_mcp_server as mod
        self.mod = mod
        self.orig_config_file = mod.CONFIG_FILE
        self.orig_data_dir = mod.DISCORD_DATA_DIR
        mod.DISCORD_DATA_DIR = self.tmpdir
        mod.CONFIG_FILE = os.path.join(self.tmpdir, "config.json")

    def tearDown(self):
        self.mod.CONFIG_FILE = self.orig_config_file
        self.mod.DISCORD_DATA_DIR = self.orig_data_dir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_missing_config(self):
        config = self.mod._load_config()
        self.assertEqual(config, {})

    def test_save_and_load(self):
        self.mod._save_config({"default_target": "12345", "default_target_type": "dm"})
        config = self.mod._load_config()
        self.assertEqual(config["default_target"], "12345")
        self.assertEqual(config["default_target_type"], "dm")

    def test_load_invalid_json(self):
        config_path = os.path.join(self.tmpdir, "config.json")
        with open(config_path, "w") as f:
            f.write("not json")
        config = self.mod._load_config()
        self.assertEqual(config, {})

    def test_load_non_dict_json(self):
        config_path = os.path.join(self.tmpdir, "config.json")
        with open(config_path, "w") as f:
            f.write('"just a string"')
        config = self.mod._load_config()
        self.assertEqual(config, {})


# ═══════════════════════════════════════════════════════════════
# Send log tests
# ═══════════════════════════════════════════════════════════════


class TestSendLog(unittest.TestCase):
    """Test send log append and pruning."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import discord_mcp_server as mod
        self.mod = mod
        self.orig_log_file = mod.SEND_LOG_FILE
        self.orig_data_dir = mod.DISCORD_DATA_DIR
        mod.DISCORD_DATA_DIR = self.tmpdir
        mod.SEND_LOG_FILE = os.path.join(self.tmpdir, "send_log.jsonl")

    def tearDown(self):
        self.mod.SEND_LOG_FILE = self.orig_log_file
        self.mod.DISCORD_DATA_DIR = self.orig_data_dir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_append_log(self):
        self.mod._append_send_log({"timestamp": "2026-01-01", "success": True})
        with open(self.mod.SEND_LOG_FILE, "r") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 1)
        data = json.loads(lines[0])
        self.assertTrue(data["success"])

    def test_append_multiple(self):
        for i in range(5):
            self.mod._append_send_log({"index": i})
        with open(self.mod.SEND_LOG_FILE, "r") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 5)

    def test_prune_large_log(self):
        # Write many lines to exceed the limit
        orig_max = self.mod.SEND_LOG_MAX_BYTES
        self.mod.SEND_LOG_MAX_BYTES = 100  # Very small for testing
        self.mod.SEND_LOG_MAX_LINES = 3
        try:
            for i in range(10):
                self.mod._append_send_log({"index": i, "data": "x" * 50})
            with open(self.mod.SEND_LOG_FILE, "r") as f:
                lines = f.readlines()
            self.assertLessEqual(len(lines), 3)
        finally:
            self.mod.SEND_LOG_MAX_BYTES = orig_max
            self.mod.SEND_LOG_MAX_LINES = 1000


# ═══════════════════════════════════════════════════════════════
# Discord client tests (mocked API)
# ═══════════════════════════════════════════════════════════════


def _make_mock_response(status=200, json_data=None, text_data=""):
    """Create a mock aiohttp response."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=json_data or {})
    mock_resp.text = AsyncMock(return_value=text_data)
    # Support async context manager
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_mock_session(responses=None):
    """Create a mock aiohttp.ClientSession.

    responses: list of (method, url_fragment, mock_response) tuples.
    """
    mock_session = AsyncMock()
    mock_session.closed = False
    mock_session.close = AsyncMock()

    call_log = []  # noqa: F841

    if responses:
        get_responses = [r for m, _, r in responses if m == "get"]
        post_responses = [r for m, _, r in responses if m == "post"]

        if get_responses:
            mock_session.get = MagicMock(side_effect=get_responses)
        else:
            mock_session.get = MagicMock(return_value=_make_mock_response())

        if post_responses:
            mock_session.post = MagicMock(side_effect=post_responses)
        else:
            mock_session.post = MagicMock(return_value=_make_mock_response())
    else:
        mock_session.get = MagicMock(return_value=_make_mock_response())
        mock_session.post = MagicMock(return_value=_make_mock_response())

    return mock_session


class TestDiscordClientConnect(unittest.TestCase):
    """Test Discord client connect logic."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import discord_mcp_server as mod
        self.mod = mod
        self.orig_config_file = mod.CONFIG_FILE
        self.orig_data_dir = mod.DISCORD_DATA_DIR
        self.orig_log_file = mod.SEND_LOG_FILE
        mod.DISCORD_DATA_DIR = self.tmpdir
        mod.CONFIG_FILE = os.path.join(self.tmpdir, "config.json")
        mod.SEND_LOG_FILE = os.path.join(self.tmpdir, "send_log.jsonl")

    def tearDown(self):
        self.mod.CONFIG_FILE = self.orig_config_file
        self.mod.DISCORD_DATA_DIR = self.orig_data_dir
        self.mod.SEND_LOG_FILE = self.orig_log_file
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_connect_no_token(self):
        client = self.mod.DiscordClient()
        with patch.dict(os.environ, {}, clear=True):
            # Also ensure no env token
            env = os.environ.copy()
            env.pop("DISCORD_BOT_TOKEN", None)
            with patch.dict(os.environ, env, clear=True):
                result = run_async(client.connect())
        self.assertIn("ERROR", result)
        self.assertIn("No Discord bot token", result)

    @patch("discord_mcp_server.aiohttp")
    def test_connect_success(self, mock_aiohttp):
        client = self.mod.DiscordClient()

        mock_resp = _make_mock_response(200, {"username": "TestBot", "id": "123"})
        mock_session = _make_mock_session([("get", "users/@me", mock_resp)])
        mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)

        result = run_async(client.connect(token="fake-token"))  # noqa: S106
        self.assertIn("Connected to Discord", result)
        self.assertIn("TestBot", result)
        self.assertTrue(client._connected)

    @patch("discord_mcp_server.aiohttp")
    def test_connect_invalid_token(self, mock_aiohttp):
        client = self.mod.DiscordClient()

        mock_resp = _make_mock_response(401)
        mock_session = _make_mock_session([("get", "users/@me", mock_resp)])
        mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)

        result = run_async(client.connect(token="bad-token"))  # noqa: S106
        self.assertIn("ERROR", result)
        self.assertIn("Invalid bot token", result)
        self.assertFalse(client._connected)

    @patch("discord_mcp_server.aiohttp")
    def test_connect_api_error(self, mock_aiohttp):
        client = self.mod.DiscordClient()

        mock_resp = _make_mock_response(500)
        mock_session = _make_mock_session([("get", "users/@me", mock_resp)])
        mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)

        result = run_async(client.connect(token="token"))  # noqa: S106
        self.assertIn("ERROR", result)
        self.assertIn("500", result)

    @patch("discord_mcp_server.aiohttp")
    def test_connect_saves_default_target(self, mock_aiohttp):
        client = self.mod.DiscordClient()

        mock_resp = _make_mock_response(200, {"username": "Bot", "id": "1"})
        mock_session = _make_mock_session([("get", "users/@me", mock_resp)])
        mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)

        run_async(client.connect(
            token="token",  # noqa: S106
            default_target="999",
            default_target_type="dm"
        ))

        config = self.mod._load_config()
        self.assertEqual(config["default_target"], "999")
        self.assertEqual(config["default_target_type"], "dm")

    @patch("discord_mcp_server.aiohttp")
    def test_connect_env_token_priority(self, mock_aiohttp):
        client = self.mod.DiscordClient()

        # Save a config token
        self.mod._save_config({"bot_token": "config-token"})

        mock_resp = _make_mock_response(200, {"username": "EnvBot", "id": "1"})
        mock_session = _make_mock_session([("get", "users/@me", mock_resp)])
        mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)

        with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "env-token"}):
            result = run_async(client.connect())

        self.assertIn("EnvBot", result)
        self.assertEqual(client._token_source, "env")


class TestDiscordClientSend(unittest.TestCase):
    """Test Discord client send logic."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import discord_mcp_server as mod
        self.mod = mod
        self.orig_config_file = mod.CONFIG_FILE
        self.orig_data_dir = mod.DISCORD_DATA_DIR
        self.orig_log_file = mod.SEND_LOG_FILE
        mod.DISCORD_DATA_DIR = self.tmpdir
        mod.CONFIG_FILE = os.path.join(self.tmpdir, "config.json")
        mod.SEND_LOG_FILE = os.path.join(self.tmpdir, "send_log.jsonl")

    def tearDown(self):
        self.mod.CONFIG_FILE = self.orig_config_file
        self.mod.DISCORD_DATA_DIR = self.orig_data_dir
        self.mod.SEND_LOG_FILE = self.orig_log_file
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_connected_client(self):
        """Create a client in connected state."""
        client = self.mod.DiscordClient()
        client._connected = True
        client._token = "fake-token"  # noqa: S105
        client._token_source = "test"  # noqa: S105
        client._bot_name = "TestBot"
        client._bot_id = "123"
        return client

    def test_send_not_connected_no_token(self):
        client = self.mod.DiscordClient()
        with patch.dict(os.environ, {}, clear=True):
            env = os.environ.copy()
            env.pop("DISCORD_BOT_TOKEN", None)
            with patch.dict(os.environ, env, clear=True):
                result = run_async(client.send_message("hello"))
        self.assertIn("ERROR", result)

    def test_send_no_target(self):
        client = self._make_connected_client()
        result = run_async(client.send_message("hello"))
        self.assertIn("ERROR", result)
        self.assertIn("No target", result)

    def test_send_empty_message(self):
        client = self._make_connected_client()
        self.mod._save_config({"default_target": "999"})
        result = run_async(client.send_message(""))
        self.assertIn("ERROR", result)
        self.assertIn("empty", result)

    def test_send_message_too_long(self):
        client = self._make_connected_client()
        self.mod._save_config({"default_target": "999"})
        result = run_async(client.send_message("x" * 30000))
        self.assertIn("ERROR", result)
        self.assertIn("too long", result)

    def test_send_rate_limited(self):
        client = self._make_connected_client()
        self.mod._save_config({"default_target": "999"})
        # Fill up rate limiter
        for _ in range(self.mod.RATE_LIMIT_MAX_SENDS):
            client._rate_limiter.record_send()
        result = run_async(client.send_message("hello"))
        self.assertIn("ERROR", result)
        self.assertIn("Rate limit", result)

    def test_send_dm_success(self):
        client = self._make_connected_client()
        self.mod._save_config({"default_target": "user123", "default_target_type": "dm"})

        # Mock: DM channel creation, then message send
        dm_resp = _make_mock_response(200, {"id": "dm-channel-1"})
        msg_resp = _make_mock_response(200, {"id": "msg-1"})

        mock_session = AsyncMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        mock_session.post = MagicMock(side_effect=[dm_resp, msg_resp])

        client._session = mock_session

        result = run_async(client.send_message("Hello!"))
        self.assertIn("sent successfully", result)
        self.assertIn("user123", result)

        # Verify log was written
        with open(self.mod.SEND_LOG_FILE, "r") as f:
            log_data = json.loads(f.readline())
        self.assertTrue(log_data["success"])

    def test_send_channel_success(self):
        client = self._make_connected_client()

        msg_resp = _make_mock_response(200, {"id": "msg-1"})
        mock_session = AsyncMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        mock_session.post = MagicMock(return_value=msg_resp)

        client._session = mock_session

        result = run_async(client.send_message(
            "Hello channel!",
            target="chan123",
            target_type="channel"
        ))
        self.assertIn("sent successfully", result)
        self.assertIn("chan123", result)

    def test_send_dm_channel_creation_fails(self):
        client = self._make_connected_client()

        dm_resp = _make_mock_response(403, text_data="Forbidden")
        mock_session = AsyncMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        mock_session.post = MagicMock(return_value=dm_resp)

        client._session = mock_session

        result = run_async(client.send_message(
            "Hello",
            target="user123",
            target_type="dm"
        ))
        self.assertIn("ERROR", result)
        self.assertIn("Failed to resolve", result)

    def test_send_message_api_error(self):
        client = self._make_connected_client()

        # DM channel success, message send fails
        dm_resp = _make_mock_response(200, {"id": "dm-1"})
        msg_resp = _make_mock_response(500, text_data="Server Error")

        mock_session = AsyncMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        mock_session.post = MagicMock(side_effect=[dm_resp, msg_resp])

        client._session = mock_session

        result = run_async(client.send_message(
            "Hello",
            target="user123",
            target_type="dm"
        ))
        self.assertIn("ERROR", result)

    def test_send_discord_rate_limit_response(self):
        client = self._make_connected_client()

        dm_resp = _make_mock_response(200, {"id": "dm-1"})
        rate_resp = _make_mock_response(429, {"retry_after": 5.0})

        mock_session = AsyncMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        mock_session.post = MagicMock(side_effect=[dm_resp, rate_resp])

        client._session = mock_session

        result = run_async(client.send_message(
            "Hello",
            target="user123",
            target_type="dm"
        ))
        self.assertIn("ERROR", result)
        self.assertIn("rate limited", result)

    def test_send_multipart_message(self):
        client = self._make_connected_client()

        # Create responses for DM channel + 2 message chunks
        dm_resp = _make_mock_response(200, {"id": "dm-1"})
        msg_resp1 = _make_mock_response(200, {"id": "msg-1"})
        msg_resp2 = _make_mock_response(200, {"id": "msg-2"})

        mock_session = AsyncMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        mock_session.post = MagicMock(side_effect=[dm_resp, msg_resp1, msg_resp2])

        client._session = mock_session

        # Message that needs splitting (>2000 chars)
        long_msg = "a" * 1500 + "\n\n" + "b" * 1500
        result = run_async(client.send_message(
            long_msg,
            target="user123",
            target_type="dm"
        ))
        self.assertIn("sent successfully", result)
        self.assertIn("Chunks: 2", result)

    @patch("discord_mcp_server.aiohttp")
    def test_send_lazy_connect_success(self, mock_aiohttp):
        """Test that send_message auto-connects when token is available in env."""
        client = self.mod.DiscordClient()  # Not connected
        self.mod._save_config({"default_target": "user123", "default_target_type": "dm"})

        # Mock connect: GET /users/@me
        connect_resp = _make_mock_response(200, {"username": "LazyBot", "id": "42"})
        # Mock DM channel creation + message send
        dm_resp = _make_mock_response(200, {"id": "dm-1"})
        msg_resp = _make_mock_response(200, {"id": "msg-1"})

        mock_session = AsyncMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        mock_session.get = MagicMock(return_value=connect_resp)
        mock_session.post = MagicMock(side_effect=[dm_resp, msg_resp])
        mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)

        with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "lazy-token"}):
            result = run_async(client.send_message("Hello lazy!"))
        self.assertIn("sent successfully", result)
        self.assertTrue(client._connected)

    def test_send_with_default_target(self):
        client = self._make_connected_client()
        self.mod._save_config({
            "default_target": "default-user",
            "default_target_type": "dm"
        })

        dm_resp = _make_mock_response(200, {"id": "dm-1"})
        msg_resp = _make_mock_response(200, {"id": "msg-1"})

        mock_session = AsyncMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        mock_session.post = MagicMock(side_effect=[dm_resp, msg_resp])

        client._session = mock_session

        result = run_async(client.send_message("Hello"))
        self.assertIn("sent successfully", result)
        self.assertIn("default-user", result)


# ═══════════════════════════════════════════════════════════════
# Status tests
# ═══════════════════════════════════════════════════════════════


class TestDiscordClientStatus(unittest.TestCase):
    """Test Discord client status reporting."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import discord_mcp_server as mod
        self.mod = mod
        self.orig_config_file = mod.CONFIG_FILE
        self.orig_data_dir = mod.DISCORD_DATA_DIR
        self.orig_log_file = mod.SEND_LOG_FILE
        mod.DISCORD_DATA_DIR = self.tmpdir
        mod.CONFIG_FILE = os.path.join(self.tmpdir, "config.json")
        mod.SEND_LOG_FILE = os.path.join(self.tmpdir, "send_log.jsonl")

    def tearDown(self):
        self.mod.CONFIG_FILE = self.orig_config_file
        self.mod.DISCORD_DATA_DIR = self.orig_data_dir
        self.mod.SEND_LOG_FILE = self.orig_log_file
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_status_disconnected(self):
        client = self.mod.DiscordClient()
        with patch.dict(os.environ, {}, clear=True):
            env = os.environ.copy()
            env.pop("DISCORD_BOT_TOKEN", None)
            with patch.dict(os.environ, env, clear=True):
                result = client.get_status()
        self.assertIn("not set", result)
        self.assertIn("Connected: no", result)

    def test_status_connected(self):
        client = self.mod.DiscordClient()
        client._connected = True
        client._bot_name = "TestBot"
        client._bot_id = "123"
        client._last_send_time = "2026-03-14T12:00:00+00:00"
        result = client.get_status()
        self.assertIn("Connected: yes", result)
        self.assertIn("TestBot", result)
        self.assertIn("Last send:", result)

    def test_status_with_env_token(self):
        client = self.mod.DiscordClient()
        with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "env-token"}):
            result = client.get_status()
        self.assertIn("environment variable", result)
        # Must NOT contain actual token
        self.assertNotIn("env-token", result)

    def test_status_without_env_token_shows_not_set(self):
        """With no env var, status should show 'not set' even if config had token."""
        client = self.mod.DiscordClient()
        self.mod._save_config({"bot_token": "old"})
        with patch.dict(os.environ, {}, clear=True):
            env = os.environ.copy()
            env.pop("DISCORD_BOT_TOKEN", None)
            with patch.dict(os.environ, env, clear=True):
                result = client.get_status()
        self.assertIn("not set", result)

    def test_status_with_default_target(self):
        client = self.mod.DiscordClient()
        self.mod._save_config({"default_target": "user456", "default_target_type": "dm"})
        result = client.get_status()
        self.assertIn("user456", result)
        self.assertIn("dm", result)

    def test_status_no_default_target(self):
        client = self.mod.DiscordClient()
        result = client.get_status()
        self.assertIn("Default target: not set", result)

    def test_status_with_connect_failures(self):
        client = self.mod.DiscordClient()
        client._connect_failures = 2
        result = client.get_status()
        self.assertIn("Connect failures: 2", result)

    def test_status_send_log_empty(self):
        client = self.mod.DiscordClient()
        result = client.get_status()
        self.assertIn("Send log: empty", result)

    def test_status_send_log_exists(self):
        client = self.mod.DiscordClient()
        self.mod._append_send_log({"test": True})
        result = client.get_status()
        self.assertIn("Send log size:", result)


# ═══════════════════════════════════════════════════════════════
# Close tests
# ═══════════════════════════════════════════════════════════════


class TestDiscordClientClose(unittest.TestCase):
    """Test Discord client close logic."""

    def test_close_with_active_session(self):
        import discord_mcp_server as mod
        client = mod.DiscordClient()
        mock_session = AsyncMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        client._session = mock_session

        run_async(client.close())
        mock_session.close.assert_awaited_once()
        self.assertIsNone(client._session)

    def test_close_without_session(self):
        import discord_mcp_server as mod
        client = mod.DiscordClient()
        client._session = None
        # Should not raise
        run_async(client.close())
        self.assertIsNone(client._session)

    def test_close_already_closed_session(self):
        import discord_mcp_server as mod
        client = mod.DiscordClient()
        mock_session = AsyncMock()
        mock_session.closed = True  # Already closed
        mock_session.close = AsyncMock()
        client._session = mock_session

        run_async(client.close())
        # close() should not be called on already-closed session
        mock_session.close.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════
# MCP tool wrapper tests
# ═══════════════════════════════════════════════════════════════


class TestMCPTools(unittest.TestCase):
    """Test MCP tool wrappers."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import discord_mcp_server as mod
        self.mod = mod
        self.orig_config_file = mod.CONFIG_FILE
        self.orig_data_dir = mod.DISCORD_DATA_DIR
        self.orig_log_file = mod.SEND_LOG_FILE
        self.orig_client = mod._client
        mod.DISCORD_DATA_DIR = self.tmpdir
        mod.CONFIG_FILE = os.path.join(self.tmpdir, "config.json")
        mod.SEND_LOG_FILE = os.path.join(self.tmpdir, "send_log.jsonl")
        mod._client = mod.DiscordClient()

    def tearDown(self):
        self.mod.CONFIG_FILE = self.orig_config_file
        self.mod.DISCORD_DATA_DIR = self.orig_data_dir
        self.mod.SEND_LOG_FILE = self.orig_log_file
        self.mod._client = self.orig_client
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_discord_status_tool(self):
        from discord_mcp_server import discord_status
        result = run_async(discord_status())
        self.assertIn("Discord Messaging Status", result)

    @patch("discord_mcp_server.aiohttp")
    def test_discord_connect_tool(self, mock_aiohttp):
        from discord_mcp_server import discord_connect

        mock_resp = _make_mock_response(200, {"username": "ToolBot", "id": "1"})
        mock_session = _make_mock_session([("get", "users/@me", mock_resp)])
        mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)

        result = run_async(discord_connect(token="test-token"))  # noqa: S106
        self.assertIn("Connected", result)

    def test_discord_send_tool_no_connection(self):
        from discord_mcp_server import discord_send
        with patch.dict(os.environ, {}, clear=True):
            env = os.environ.copy()
            env.pop("DISCORD_BOT_TOKEN", None)
            with patch.dict(os.environ, env, clear=True):
                result = run_async(discord_send(message="hello"))
        self.assertIn("ERROR", result)


# ═══════════════════════════════════════════════════════════════
# Token security tests
# ═══════════════════════════════════════════════════════════════


class TestTokenSecurity(unittest.TestCase):
    """Verify token is never exposed in outputs."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import discord_mcp_server as mod
        self.mod = mod
        self.orig_config_file = mod.CONFIG_FILE
        self.orig_data_dir = mod.DISCORD_DATA_DIR
        self.orig_log_file = mod.SEND_LOG_FILE
        mod.DISCORD_DATA_DIR = self.tmpdir
        mod.CONFIG_FILE = os.path.join(self.tmpdir, "config.json")
        mod.SEND_LOG_FILE = os.path.join(self.tmpdir, "send_log.jsonl")

    def tearDown(self):
        self.mod.CONFIG_FILE = self.orig_config_file
        self.mod.DISCORD_DATA_DIR = self.orig_data_dir
        self.mod.SEND_LOG_FILE = self.orig_log_file
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_status_never_shows_token(self):
        test_token = "mySuperSecretToken123456"  # noqa: S105
        self.mod._save_config({"bot_token": test_token})

        client = self.mod.DiscordClient()
        client._token = test_token
        with patch.dict(os.environ, {}, clear=True):
            env = os.environ.copy()
            env.pop("DISCORD_BOT_TOKEN", None)
            with patch.dict(os.environ, env, clear=True):
                status = client.get_status()
        self.assertNotIn(test_token, status)

    @patch("discord_mcp_server.aiohttp")
    def test_connect_success_never_shows_token(self, mock_aiohttp):
        test_token = "anotherSecretToken789"  # noqa: S105
        client = self.mod.DiscordClient()

        mock_resp = _make_mock_response(200, {"username": "Bot", "id": "1"})
        mock_session = _make_mock_session([("get", "users/@me", mock_resp)])
        mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)

        result = run_async(client.connect(token=test_token))
        self.assertNotIn(test_token, result)

    @patch("discord_mcp_server.aiohttp")
    def test_connect_failure_never_shows_token(self, mock_aiohttp):
        test_token = "failToken456"  # noqa: S105
        client = self.mod.DiscordClient()

        mock_resp = _make_mock_response(401)
        mock_session = _make_mock_session([("get", "users/@me", mock_resp)])
        mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)

        result = run_async(client.connect(token=test_token))
        self.assertNotIn(test_token, result)

    def test_send_log_never_contains_token(self):
        test_token = "logTestToken"  # noqa: S105
        client = self.mod.DiscordClient()
        client._connected = True
        client._token = test_token
        client._bot_name = "Bot"

        # Send a message that fails (no target) - log should not have token
        self.mod._save_config({"default_target": "user1"})

        # Mock session with a failure
        dm_resp = _make_mock_response(403, text_data="Forbidden")
        mock_session = AsyncMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        mock_session.post = MagicMock(return_value=dm_resp)
        client._session = mock_session

        run_async(client.send_message("test", target="user1", target_type="dm"))

        # Check log file
        try:
            with open(self.mod.SEND_LOG_FILE, "r") as f:
                content = f.read()
            self.assertNotIn(test_token, content)
        except FileNotFoundError:
            pass  # No log = no token exposure


# ═══════════════════════════════════════════════════════════════
# Discord Receive MCP tool tests (Phase 3)
# ═══════════════════════════════════════════════════════════════


class TestReceiveStatus(unittest.TestCase):
    """Test discord_receive_status tool."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import discord_mcp_server as mod
        self.mod = mod
        self.orig_data_dir = mod.DISCORD_DATA_DIR
        self.orig_config_file = mod.RECEIVE_CONFIG_FILE
        self.orig_buffer_file = mod.RECEIVE_BUFFER_FILE
        self.orig_state_file = mod.RECEIVE_STATE_FILE
        mod.DISCORD_DATA_DIR = self.tmpdir
        mod.RECEIVE_CONFIG_FILE = os.path.join(self.tmpdir, "receive_config.json")
        mod.RECEIVE_BUFFER_FILE = os.path.join(self.tmpdir, "receive_buffer.jsonl")
        mod.RECEIVE_STATE_FILE = os.path.join(self.tmpdir, "receive_state.json")

    def tearDown(self):
        self.mod.DISCORD_DATA_DIR = self.orig_data_dir
        self.mod.RECEIVE_CONFIG_FILE = self.orig_config_file
        self.mod.RECEIVE_BUFFER_FILE = self.orig_buffer_file
        self.mod.RECEIVE_STATE_FILE = self.orig_state_file
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_status_no_files(self):
        """Status when no daemon has ever run."""
        from discord_mcp_server import discord_receive_status
        result = run_async(discord_receive_status())
        self.assertIn("Discord Receive Daemon Status", result)
        self.assertIn("not running", result)
        self.assertIn("Buffer: empty", result)
        self.assertIn("Allow list: empty", result)

    def test_status_with_state(self):
        """Status with daemon state file present."""
        state = {
            "connection_state": "connected",
            "bot_name": "TestBot",
            "started_at": "2026-03-15T10:00:00+00:00",
            "last_heartbeat": "2026-03-15T10:05:00+00:00",
            "reconnect_count": 2,
            "stats": {
                "messages_received": 50,
                "messages_filtered": 10,
                "messages_processed": 40,
            },
        }
        with open(self.mod.RECEIVE_STATE_FILE, "w") as f:
            json.dump(state, f)

        from discord_mcp_server import discord_receive_status
        result = run_async(discord_receive_status())
        self.assertIn("connected", result)
        self.assertIn("TestBot", result)
        self.assertIn("Reconnects: 2", result)
        self.assertIn("Messages received: 50", result)
        self.assertIn("Messages filtered: 10", result)

    def test_status_with_buffer_entries(self):
        """Status with entries in the buffer."""
        entries = [
            {"id": "1", "status": "pending", "sender_id": "u1"},
            {"id": "2", "status": "pending", "sender_id": "u2"},
            {"id": "3", "status": "completed", "sender_id": "u1"},
        ]
        with open(self.mod.RECEIVE_BUFFER_FILE, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        from discord_mcp_server import discord_receive_status
        result = run_async(discord_receive_status())
        self.assertIn("3 entries", result)
        self.assertIn("pending: 2", result)
        self.assertIn("completed: 1", result)

    def test_status_with_allow_list(self):
        """Status with allowed users/channels configured."""
        config = {
            "allowed_users": ["user1", "user2"],
            "allowed_channels": ["chan1"],
        }
        with open(self.mod.RECEIVE_CONFIG_FILE, "w") as f:
            json.dump(config, f)

        from discord_mcp_server import discord_receive_status
        result = run_async(discord_receive_status())
        self.assertIn("user1", result)
        self.assertIn("user2", result)
        self.assertIn("chan1", result)

    def test_status_with_empty_stats(self):
        """Status with state file but no stats."""
        state = {"connection_state": "disconnected"}
        with open(self.mod.RECEIVE_STATE_FILE, "w") as f:
            json.dump(state, f)

        from discord_mcp_server import discord_receive_status
        result = run_async(discord_receive_status())
        self.assertIn("disconnected", result)
        self.assertIn("Receive stats: none", result)

    def test_status_with_invalid_state_json(self):
        """Status gracefully handles corrupted state file."""
        with open(self.mod.RECEIVE_STATE_FILE, "w") as f:
            f.write("{corrupted json data")

        from discord_mcp_server import discord_receive_status
        result = run_async(discord_receive_status())
        # Should fall back to "not running" (empty dict from _load_receive_state)
        self.assertIn("not running", result)
        self.assertIn("Discord Receive Daemon Status", result)

    def test_status_with_non_dict_state_json(self):
        """Status gracefully handles non-dict state file."""
        with open(self.mod.RECEIVE_STATE_FILE, "w") as f:
            f.write('"just a string"')

        from discord_mcp_server import discord_receive_status
        result = run_async(discord_receive_status())
        self.assertIn("not running", result)


class TestReceiveAllow(unittest.TestCase):
    """Test discord_receive_allow tool."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import discord_mcp_server as mod
        self.mod = mod
        self.orig_data_dir = mod.DISCORD_DATA_DIR
        self.orig_config_file = mod.RECEIVE_CONFIG_FILE
        mod.DISCORD_DATA_DIR = self.tmpdir
        mod.RECEIVE_CONFIG_FILE = os.path.join(self.tmpdir, "receive_config.json")

    def tearDown(self):
        self.mod.DISCORD_DATA_DIR = self.orig_data_dir
        self.mod.RECEIVE_CONFIG_FILE = self.orig_config_file
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_allow_user(self):
        from discord_mcp_server import discord_receive_allow
        result = run_async(discord_receive_allow(id="12345"))
        self.assertIn("Added user 12345", result)
        self.assertIn("1 users", result)

        # Verify persisted
        config = self.mod._load_receive_config()
        self.assertIn("12345", config["allowed_users"])

    def test_allow_channel(self):
        from discord_mcp_server import discord_receive_allow
        result = run_async(discord_receive_allow(id="chan99", id_type="channel"))
        self.assertIn("Added channel chan99", result)
        self.assertIn("1 channels", result)

    def test_allow_duplicate_user(self):
        from discord_mcp_server import discord_receive_allow
        run_async(discord_receive_allow(id="12345"))
        result = run_async(discord_receive_allow(id="12345"))
        self.assertIn("already in the allow list", result)

    def test_allow_duplicate_channel(self):
        from discord_mcp_server import discord_receive_allow
        run_async(discord_receive_allow(id="chan1", id_type="channel"))
        result = run_async(discord_receive_allow(id="chan1", id_type="channel"))
        self.assertIn("already in the allow list", result)

    def test_allow_empty_id(self):
        from discord_mcp_server import discord_receive_allow
        result = run_async(discord_receive_allow(id=""))
        self.assertIn("ERROR", result)

    def test_allow_invalid_type(self):
        from discord_mcp_server import discord_receive_allow
        result = run_async(discord_receive_allow(id="123", id_type="guild"))
        self.assertIn("ERROR", result)

    def test_allow_multiple_users(self):
        from discord_mcp_server import discord_receive_allow
        run_async(discord_receive_allow(id="u1"))
        run_async(discord_receive_allow(id="u2"))
        result = run_async(discord_receive_allow(id="u3"))
        self.assertIn("3 users", result)

    def test_allow_preserves_existing_config(self):
        """Adding a user preserves existing config fields."""
        self.mod._save_receive_config({
            "allowed_users": [],
            "allowed_channels": ["existing_chan"],
            "message_max_length": 5000,
        })
        from discord_mcp_server import discord_receive_allow
        run_async(discord_receive_allow(id="new_user"))
        config = self.mod._load_receive_config()
        self.assertIn("new_user", config["allowed_users"])
        self.assertIn("existing_chan", config["allowed_channels"])
        self.assertEqual(config["message_max_length"], 5000)


class TestReceivePending(unittest.TestCase):
    """Test discord_receive_pending tool."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import discord_mcp_server as mod
        self.mod = mod
        self.orig_data_dir = mod.DISCORD_DATA_DIR
        self.orig_buffer_file = mod.RECEIVE_BUFFER_FILE
        mod.DISCORD_DATA_DIR = self.tmpdir
        mod.RECEIVE_BUFFER_FILE = os.path.join(self.tmpdir, "receive_buffer.jsonl")

    def tearDown(self):
        self.mod.DISCORD_DATA_DIR = self.orig_data_dir
        self.mod.RECEIVE_BUFFER_FILE = self.orig_buffer_file
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_pending_no_buffer(self):
        from discord_mcp_server import discord_receive_pending
        result = run_async(discord_receive_pending())
        self.assertIn("No pending messages", result)

    def test_pending_empty_buffer(self):
        # Create empty file
        with open(self.mod.RECEIVE_BUFFER_FILE, "w") as f:  # noqa: F841
            pass
        from discord_mcp_server import discord_receive_pending
        result = run_async(discord_receive_pending())
        self.assertIn("No pending messages", result)

    def test_pending_with_entries(self):
        entries = [
            {"id": "aaa-111", "status": "pending", "sender_id": "u1",
             "received_at": "2026-03-15T10:00:00+00:00", "content": "Hello"},
            {"id": "bbb-222", "status": "completed", "sender_id": "u2",
             "received_at": "2026-03-15T10:01:00+00:00", "content": "Done"},
            {"id": "ccc-333", "status": "failed", "sender_id": "u3",
             "received_at": "2026-03-15T10:02:00+00:00", "content": "Retry me",
             "retry_count": 1},
        ]
        with open(self.mod.RECEIVE_BUFFER_FILE, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        from discord_mcp_server import discord_receive_pending
        result = run_async(discord_receive_pending())
        self.assertIn("Pending messages: 2", result)
        self.assertIn("aaa-111", result)
        self.assertIn("ccc-333", result)
        self.assertNotIn("bbb-222", result)  # completed entry should not show
        self.assertIn("retries: 1", result)
        self.assertIn("Hello", result)

    def test_pending_limit(self):
        entries = [
            {"id": f"id-{i}", "status": "pending", "sender_id": "u1",
             "content": f"msg {i}"}
            for i in range(10)
        ]
        with open(self.mod.RECEIVE_BUFFER_FILE, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        from discord_mcp_server import discord_receive_pending
        result = run_async(discord_receive_pending(limit=3))
        self.assertIn("Pending messages: 10", result)
        # Should show at most 3 entries
        self.assertIn("id-0", result)
        self.assertIn("id-2", result)
        self.assertNotIn("id-3", result)

    def test_pending_long_content_truncated(self):
        entries = [
            {"id": "long-1", "status": "pending", "sender_id": "u1",
             "content": "x" * 200},
        ]
        with open(self.mod.RECEIVE_BUFFER_FILE, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        from discord_mcp_server import discord_receive_pending
        result = run_async(discord_receive_pending())
        self.assertIn("...", result)

    def test_pending_all_completed(self):
        """All entries completed — no pending."""
        entries = [
            {"id": "1", "status": "completed"},
            {"id": "2", "status": "discarded"},
        ]
        with open(self.mod.RECEIVE_BUFFER_FILE, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        from discord_mcp_server import discord_receive_pending
        result = run_async(discord_receive_pending())
        self.assertIn("No pending messages", result)
        self.assertIn("Total entries: 2", result)

    def test_pending_limit_clamped(self):
        """Limit is clamped to 1-100."""
        from discord_mcp_server import discord_receive_pending
        # Should not raise with extreme values
        result = run_async(discord_receive_pending(limit=0))
        self.assertIsInstance(result, str)
        result = run_async(discord_receive_pending(limit=9999))
        self.assertIsInstance(result, str)


# ═══════════════════════════════════════════════════════════════
# Receive config helper tests
# ═══════════════════════════════════════════════════════════════


class TestReceiveConfigHelpers(unittest.TestCase):
    """Test receive config load/save helpers."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import discord_mcp_server as mod
        self.mod = mod
        self.orig_data_dir = mod.DISCORD_DATA_DIR
        self.orig_config_file = mod.RECEIVE_CONFIG_FILE
        mod.DISCORD_DATA_DIR = self.tmpdir
        mod.RECEIVE_CONFIG_FILE = os.path.join(self.tmpdir, "receive_config.json")

    def tearDown(self):
        self.mod.DISCORD_DATA_DIR = self.orig_data_dir
        self.mod.RECEIVE_CONFIG_FILE = self.orig_config_file
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_missing(self):
        result = self.mod._load_receive_config()
        self.assertEqual(result, {})

    def test_save_and_load(self):
        data = {"allowed_users": ["u1"], "allowed_channels": ["c1"]}
        self.mod._save_receive_config(data)
        result = self.mod._load_receive_config()
        self.assertEqual(result["allowed_users"], ["u1"])
        self.assertEqual(result["allowed_channels"], ["c1"])

    def test_load_invalid_json(self):
        with open(self.mod.RECEIVE_CONFIG_FILE, "w") as f:
            f.write("not json")
        result = self.mod._load_receive_config()
        self.assertEqual(result, {})

    def test_load_non_dict(self):
        with open(self.mod.RECEIVE_CONFIG_FILE, "w") as f:
            f.write("[1, 2, 3]")
        result = self.mod._load_receive_config()
        self.assertEqual(result, {})


# ═══════════════════════════════════════════════════════════════
# Token environment variable migration tests (CRITICAL-4,5)
# ═══════════════════════════════════════════════════════════════


class TestTokenEnvMigration(unittest.TestCase):
    """Ensure bot_token is never saved to config file."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import discord_mcp_server as mod
        self.mod = mod
        self.orig_config_file = mod.CONFIG_FILE
        self.orig_data_dir = mod.DISCORD_DATA_DIR
        self.orig_log_file = mod.SEND_LOG_FILE
        mod.DISCORD_DATA_DIR = self.tmpdir
        mod.CONFIG_FILE = os.path.join(self.tmpdir, "config.json")
        mod.SEND_LOG_FILE = os.path.join(self.tmpdir, "send_log.jsonl")

    def tearDown(self):
        self.mod.CONFIG_FILE = self.orig_config_file
        self.mod.DISCORD_DATA_DIR = self.orig_data_dir
        self.mod.SEND_LOG_FILE = self.orig_log_file
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_save_config_strips_bot_token(self):
        """_save_config must strip bot_token key before writing."""
        self.mod._save_config({"bot_token": "tv1", "default_target": "123"})
        config = self.mod._load_config()
        self.assertNotIn("bot_token", config)
        self.assertEqual(config["default_target"], "123")

    def test_save_config_strips_token_key(self):
        """_save_config must strip 'token' key as well."""
        self.mod._save_config({"token": "tv2", "default_target": "456"})
        config = self.mod._load_config()
        self.assertNotIn("token", config)
        self.assertEqual(config["default_target"], "456")

    @patch("discord_mcp_server.aiohttp")
    def test_connect_does_not_save_token_to_config(self, mock_aiohttp):
        """connect() with explicit token must NOT persist it to config."""
        client = self.mod.DiscordClient()

        mock_resp = _make_mock_response(200, {"username": "Bot", "id": "1"})
        mock_session = _make_mock_session([("get", "users/@me", mock_resp)])
        mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)

        run_async(client.connect(token="tv3", default_target="999"))  # noqa: S106

        config = self.mod._load_config()
        self.assertNotIn("bot_token", config)
        self.assertNotIn("token", config)
        # Non-secret config should still be saved
        self.assertEqual(config.get("default_target"), "999")

    def test_config_file_does_not_contain_token_string(self):
        """Config file on disk must not contain any token value."""
        self.mod._save_config({
            "bot_token": "diskv",
            "default_target": "user1"
        })
        with open(self.mod.CONFIG_FILE, "r") as f:
            raw = f.read()
        self.assertNotIn("diskv", raw)
        self.assertNotIn("bot_token", raw)

    def test_get_status_no_config_token_reference(self):
        """get_status should not report 'config file' token source
        since tokens should only come from env var."""
        client = self.mod.DiscordClient()
        # Even if old config has bot_token, status should not say "config file"
        self.mod._save_config({"bot_token": "old"})
        with patch.dict(os.environ, {}, clear=True):
            env = os.environ.copy()
            env.pop("DISCORD_BOT_TOKEN", None)
            with patch.dict(os.environ, env, clear=True):
                result = client.get_status()
        self.assertIn("not set", result)


if __name__ == "__main__":
    unittest.main()
