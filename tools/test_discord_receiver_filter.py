"""Tests for discord_receiver_filter.py — verifies filter module extraction."""
import os, sys
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

def test_filter_module_exports():
    from discord_receiver_filter import MessageFilter
    from discord_receiver_models import ReceiveConfig
    cfg = ReceiveConfig(allowed_users=["u1"])
    f = MessageFilter(bot_id="bot1", config=cfg)
    passed, reason = f.check({"author": {"id": "u1"}, "channel_id": "c1", "content": "hi"})
    assert passed
