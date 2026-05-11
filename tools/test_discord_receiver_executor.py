"""Tests for discord_receiver_executor.py — verifies executor module extraction."""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch, AsyncMock, MagicMock

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

def test_executor_module_exports():
    from discord_receiver_executor import PromptTemplate, CLIExecutor, ResponseSender
    t = PromptTemplate("msg: {message}")
    assert t.render("hi") == "msg: hi"

# ═══════════════════════════════════════════════════════════════
# H1: Permission Mode Whitelist Strict Enforcement
# ═══════════════════════════════════════════════════════════════


class TestPermissionModeWhitelist(unittest.TestCase):
    """Verify permission mode whitelist is enforced strictly before every subprocess call."""

    def test_validate_blocks_unknown_modes(self):
        from discord_receiver_executor import CLIExecutor
        from discord_receiver_models import ReceiveConfig
        config = ReceiveConfig()
        executor = CLIExecutor(config)
        # Unknown mode should be blocked
        self.assertFalse(executor.validate_permission_mode("unknown_mode"))
        self.assertFalse(executor.validate_permission_mode("bypassPermissions"))
        self.assertFalse(executor.validate_permission_mode(""))

    def test_validate_allows_known_modes(self):
        from discord_receiver_executor import CLIExecutor
        from discord_receiver_models import ReceiveConfig, ALLOWED_PERMISSION_MODES
        config = ReceiveConfig()
        executor = CLIExecutor(config)
        for mode in ALLOWED_PERMISSION_MODES:
            self.assertTrue(executor.validate_permission_mode(mode),
                           f"Mode {mode} should be allowed")

    def test_execute_rejects_invalid_mode_before_subprocess(self):
        """execute() should reject invalid mode BEFORE creating subprocess."""
        from discord_receiver_executor import CLIExecutor
        from discord_receiver_models import ReceiveConfig
        config = ReceiveConfig()
        config.permission_mode = "bypassPermissions"
        executor = CLIExecutor(config)

        async def run():
            with patch("discord_receiver_executor.asyncio.create_subprocess_exec") as mock_exec:
                success, output, error = await executor.execute("test prompt")
                self.assertFalse(success)
                self.assertIn("not allowed", error)
                # Subprocess should NOT have been called
                mock_exec.assert_not_called()

        asyncio.run(run())

    def test_execute_validates_mode_every_call(self):
        """Permission mode should be validated on every execute() call, not just init."""
        from discord_receiver_executor import CLIExecutor
        from discord_receiver_models import ReceiveConfig
        config = ReceiveConfig()
        config.permission_mode = "plan"
        executor = CLIExecutor(config)

        # Change mode to invalid after init
        config.permission_mode = "bypassPermissions"

        async def run():
            success, output, error = await executor.execute("test prompt")
            self.assertFalse(success)
            self.assertIn("not allowed", error)

        asyncio.run(run())
