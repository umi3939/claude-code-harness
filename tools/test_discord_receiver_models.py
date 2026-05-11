"""Tests for discord_receiver_models.py — verifies module extraction is correct.

These tests ensure that the extracted models module contains all expected
symbols and that they behave identically to the originals.
"""

import os
import sys
import json

import pytest

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)


class TestModelsModuleExports:
    """Verify that discord_receiver_models exports all expected symbols."""

    def test_constants_exist(self):
        from discord_receiver_models import (
            DISCORD_API_BASE, DISCORD_GATEWAY_VERSION, DISCORD_GATEWAY_ENCODING,
            DISCORD_DATA_DIR, RECEIVE_CONFIG_FILE, RECEIVE_BUFFER_FILE,
            RECEIVE_LOG_FILE, RECEIVE_STATE_FILE,
            DEFAULT_BUFFER_MAX_SIZE, DEFAULT_MESSAGE_MAX_LENGTH,
            DEFAULT_RECEIVE_LOG_MAX_BYTES, DEFAULT_RECEIVE_LOG_MAX_LINES,
            DEFAULT_LOG_BODY_TRUNCATE, DEFAULT_CLI_TIMEOUT_SECONDS,
            DEFAULT_RATE_LIMIT_GLOBAL_MAX, DEFAULT_RATE_LIMIT_GLOBAL_WINDOW,
            DEFAULT_RATE_LIMIT_PER_SENDER_MAX, DEFAULT_RATE_LIMIT_PER_SENDER_WINDOW,
            DEFAULT_MAX_RETRIES, DEFAULT_RESPONSE_MAX_LENGTH,
            DEFAULT_RESPONSE_MAX_SPLITS, ALLOWED_PERMISSION_MODES,
            BLOCKED_PERMISSION_MODES, DEFAULT_PROMPT_TEMPLATE,
            MAX_RECONNECT_ATTEMPTS, RECONNECT_BASE_DELAY, RECONNECT_MAX_DELAY,
            OP_DISPATCH, OP_HEARTBEAT, OP_IDENTIFY, OP_STATUS_UPDATE,
            OP_VOICE_STATE, OP_RESUME, OP_RECONNECT, OP_REQUEST_MEMBERS,
            OP_INVALID_SESSION, OP_HELLO, OP_HEARTBEAT_ACK,
            INTENT_GUILDS, INTENT_GUILD_MESSAGES, INTENT_GUILD_MESSAGE_CONTENT,
            INTENT_DIRECT_MESSAGES, INTENT_DIRECT_MESSAGE_CONTENT,
            REQUIRED_INTENTS,
            STATUS_PENDING, STATUS_PROCESSING, STATUS_COMPLETED,
            STATUS_FAILED, STATUS_DISCARDED,
        )
        assert DISCORD_API_BASE == "https://discord.com/api/v10"
        assert OP_HELLO == 10
        assert STATUS_PENDING == "pending"

    def test_helpers_exist(self):
        from discord_receiver_models import _ensure_dir, _now_iso, _parse_iso
        ts = _now_iso()
        assert "T" in ts
        dt = _parse_iso(ts)
        assert dt is not None

    def test_dataclasses_exist(self):
        from discord_receiver_models import (
            ReceiveConfig, BufferEntry, ReceiveLogEntry,
        )
        cfg = ReceiveConfig()
        assert cfg.allowed_users == []
        entry = BufferEntry(id="e1")
        assert entry.status == "pending"
        log = ReceiveLogEntry()
        assert log.timestamp == ""

    def test_io_functions_exist(self):
        from discord_receiver_models import (
            load_receive_config, save_receive_config,
            save_receive_state, load_receive_state,
            resolve_bot_token,
        )
        assert callable(load_receive_config)
        assert callable(save_receive_config)
        assert callable(save_receive_state)
        assert callable(load_receive_state)
        assert callable(resolve_bot_token)

    def test_config_save_load_via_models(self, tmp_path):
        """Config save/load works through models module directly."""
        from unittest.mock import patch
        from discord_receiver_models import (
            ReceiveConfig, save_receive_config, load_receive_config,
        )
        config_file = os.path.join(str(tmp_path), "cfg.json")
        with patch("discord_receiver_models.RECEIVE_CONFIG_FILE", config_file), \
             patch("discord_receiver_models.DISCORD_DATA_DIR", str(tmp_path)):
            cfg = ReceiveConfig(allowed_users=["u1"], message_max_length=500)
            save_receive_config(cfg)
            loaded = load_receive_config()
            assert loaded.allowed_users == ["u1"]
            assert loaded.message_max_length == 500

    def test_state_save_load_via_models(self, tmp_path):
        """State save/load works through models module directly."""
        from unittest.mock import patch
        from discord_receiver_models import (
            save_receive_state, load_receive_state,
        )
        state_file = os.path.join(str(tmp_path), "state.json")
        with patch("discord_receiver_models.RECEIVE_STATE_FILE", state_file), \
             patch("discord_receiver_models.DISCORD_DATA_DIR", str(tmp_path)):
            save_receive_state({"running": True, "count": 42})
            loaded = load_receive_state()
            assert loaded["running"] is True
            assert loaded["count"] == 42
