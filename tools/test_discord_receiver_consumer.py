"""Tests for discord_receiver_consumer.py — verifies consumer module extraction."""
import os, sys
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

def test_consumer_module_exports():
    from discord_receiver_consumer import BufferConsumer
    assert callable(BufferConsumer)
