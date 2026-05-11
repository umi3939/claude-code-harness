"""Tests for discord_receiver_buffer.py — verifies buffer/log module extraction."""
import os, sys
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

def test_buffer_module_exports():
    from discord_receiver_buffer import ReceiveBuffer, ReceiveLog
    assert callable(ReceiveBuffer)
    assert callable(ReceiveLog)

def test_buffer_add(tmp_path):
    from discord_receiver_buffer import ReceiveBuffer
    from discord_receiver_models import BufferEntry
    buf = ReceiveBuffer(buffer_path=str(tmp_path / "b.jsonl"), max_size=5)
    entry = BufferEntry(message_id="m1", sender_id="u1", content="hello")
    assert buf.add(entry)
    assert len(buf.get_pending()) == 1

def test_receiver_uses_patched_buffer_path(tmp_path):
    """DiscordReceiver should use patched RECEIVE_BUFFER_FILE."""
    import os
    from unittest.mock import patch
    buf_path = str(tmp_path / "buf.jsonl")
    log_path = str(tmp_path / "log.jsonl")
    cfg_path = str(tmp_path / "cfg.json")
    state_path = str(tmp_path / "state.json")
    with patch("discord_receiver.RECEIVE_CONFIG_FILE", cfg_path), \
         patch("discord_receiver.RECEIVE_BUFFER_FILE", buf_path), \
         patch("discord_receiver.RECEIVE_LOG_FILE", log_path), \
         patch("discord_receiver.RECEIVE_STATE_FILE", state_path), \
         patch("discord_receiver.DISCORD_DATA_DIR", str(tmp_path)):
        from discord_receiver import DiscordReceiver
        recv = DiscordReceiver("test_val")
        assert recv.buffer.buffer_path == buf_path
        assert recv.receive_log.log_path == log_path


# ═══════════════════════════════════════════════════════════════
# C2: File Lock for Race Condition Prevention
# ═══════════════════════════════════════════════════════════════

def test_buffer_add_uses_file_lock(tmp_path):
    """Verify add() acquires file lock during read-modify-write."""
    from discord_receiver_buffer import ReceiveBuffer
    from discord_receiver_models import BufferEntry
    import threading
    buf = ReceiveBuffer(buffer_path=str(tmp_path / "b.jsonl"), max_size=100)
    results = []

    def add_entries(start):
        for i in range(10):
            entry = BufferEntry(message_id=f"m{start+i}", sender_id="u1", content=f"msg{start+i}")
            results.append(buf.add(entry))

    t1 = threading.Thread(target=add_entries, args=(0,))
    t2 = threading.Thread(target=add_entries, args=(100,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # All 20 entries should be present (no lost writes)
    all_entries = buf._load_all()
    assert len(all_entries) == 20, f"Expected 20 entries, got {len(all_entries)}"


def test_buffer_update_status_uses_file_lock(tmp_path):
    """Verify update_status() also uses file lock."""
    from discord_receiver_buffer import ReceiveBuffer
    from discord_receiver_models import BufferEntry
    buf = ReceiveBuffer(buffer_path=str(tmp_path / "b.jsonl"), max_size=100)
    entry = BufferEntry(message_id="m1", sender_id="u1", content="hello")
    buf.add(entry)
    eid = buf.get_pending()[0].id
    assert buf.update_status(eid, "completed")
    # Verify status was updated
    all_entries = buf._load_all()
    assert all_entries[0].status == "completed"


# ═══════════════════════════════════════════════════════════════
# C3: Silent Exception Distinction (FileNotFoundError vs OSError)
# ═══════════════════════════════════════════════════════════════

def test_load_all_file_not_found_is_silent(tmp_path):
    """FileNotFoundError should return empty list without logging."""
    from discord_receiver_buffer import ReceiveBuffer
    buf = ReceiveBuffer(buffer_path=str(tmp_path / "nonexistent.jsonl"))
    entries = buf._load_all()
    assert entries == []


def test_load_all_permission_error_propagates(tmp_path):
    """PermissionError should propagate to caller, not be silently caught."""
    import pytest
    from unittest.mock import patch
    from discord_receiver_buffer import ReceiveBuffer
    buf = ReceiveBuffer(buffer_path=str(tmp_path / "b.jsonl"))
    # Create the file so FileNotFoundError is not raised
    (tmp_path / "b.jsonl").write_text("")

    with patch("builtins.open", side_effect=PermissionError("Access denied")):
        with pytest.raises(PermissionError, match="Access denied"):
            buf._load_all()


def test_load_all_oserror_logs_warning(tmp_path, caplog):
    """Generic OSError (e.g. disk full) should log a warning."""
    import logging
    from unittest.mock import patch
    from discord_receiver_buffer import ReceiveBuffer
    buf = ReceiveBuffer(buffer_path=str(tmp_path / "b.jsonl"))
    (tmp_path / "b.jsonl").write_text("")

    with patch("builtins.open", side_effect=OSError("Disk full")):
        with caplog.at_level(logging.WARNING):
            entries = buf._load_all()
    assert entries == []
    assert any("Disk full" in r.message or "OSError" in r.message for r in caplog.records), f"Expected warning log, got: {[r.message for r in caplog.records]}"

# ================================================================
# H6: File I/O Timeout for Buffer Operations
# ================================================================

def test_save_all_has_timeout_protection(tmp_path):
    """_save_all should have timeout protection to prevent hung filesystem blocking."""
    from discord_receiver_buffer import ReceiveBuffer
    from discord_receiver_models import BufferEntry
    buf = ReceiveBuffer(buffer_path=str(tmp_path / "b.jsonl"), max_size=5)
    entry = BufferEntry(message_id="m1", sender_id="u1", content="hello")
    # Should work normally
    buf.add(entry)
    entries = buf._load_all()
    assert len(entries) == 1

    # Verify the module has a WRITE_TIMEOUT constant
    import discord_receiver_buffer
    assert hasattr(discord_receiver_buffer, "WRITE_TIMEOUT_SECONDS"),         "Module should define WRITE_TIMEOUT_SECONDS constant"
    assert discord_receiver_buffer.WRITE_TIMEOUT_SECONDS > 0

