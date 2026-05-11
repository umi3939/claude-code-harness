"""
Tests for message_event_hooks.py — Message Event Hook Dispatcher.

TDD: Tests written before implementation.
Covers:
- MessageEventContext dataclass
- HookDefinition dataclass + config loading
- Hook execution engine (subprocess, stdin JSON)
- Hook execution log (JSONL + pruning + file lock)
- HookDispatcher (event selection, sequential execution, safety valves)
  - Debounce (consecutive fire suppression)
  - Reentry prevention (_dispatch_depth)
  - Per-hook timeout + global timeout
  - Consecutive failure auto-disable
  - fire_and_forget async mode (default)
  - Graceful shutdown (subprocess kill)
- Integration helpers (normalize Discord data -> MessageEventContext)
"""

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, mock_open
from dataclasses import asdict

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from message_event_hooks import (
    MessageEventContext,
    HookDefinition,
    HookExecutionRecord,
    HookExecutionLog,
    HookDispatcher,
    load_hook_definitions,
    normalize_discord_message,
    EVENT_RECEIVED,
    EVENT_FILTERED,
    EVENT_BUFFERED,
    EVENT_SANITIZED,
    EVENT_SENT,
    ALL_EVENTS,
    DEFAULT_HOOK_TIMEOUT,
    DEFAULT_GLOBAL_TIMEOUT,
    DEFAULT_CONSECUTIVE_FAILURE_LIMIT,
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_LOG_MAX_LINES,
)


# ═══════════════════════════════════════════════════════════════
# Phase 1: MessageEventContext
# ═══════════════════════════════════════════════════════════════


class TestMessageEventContext(unittest.TestCase):
    """Test MessageEventContext dataclass."""

    def test_create_basic_context(self):
        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="hello",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
        )
        self.assertEqual(ctx.event, "message:received")
        self.assertEqual(ctx.source, "discord")
        self.assertEqual(ctx.sender_id, "123")
        self.assertEqual(ctx.content, "hello")

    def test_metadata_defaults_to_empty_dict(self):
        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="hello",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
        )
        self.assertEqual(ctx.metadata, {})

    def test_extra_fields_for_filtered_event(self):
        ctx = MessageEventContext(
            event="message:filtered",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="hello",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
            filter_passed=True,
            filter_reason="",
        )
        self.assertTrue(ctx.filter_passed)

    def test_extra_fields_for_sanitized_event(self):
        ctx = MessageEventContext(
            event="message:sanitized",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="sanitized text",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
            sanitize_findings=["injection_flagged"],
        )
        self.assertEqual(ctx.sanitize_findings, ["injection_flagged"])

    def test_extra_fields_for_buffered_event(self):
        ctx = MessageEventContext(
            event="message:buffered",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="hello",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
            buffer_entry_id="buf-001",
        )
        self.assertEqual(ctx.buffer_entry_id, "buf-001")

    def test_extra_fields_for_sent_event(self):
        ctx = MessageEventContext(
            event="message:sent",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="hello",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
            send_success=True,
            send_error="",
        )
        self.assertTrue(ctx.send_success)

    def test_to_dict(self):
        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="hello",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
        )
        d = asdict(ctx)
        self.assertIsInstance(d, dict)
        self.assertEqual(d["event"], "message:received")

    def test_serializable_to_json(self):
        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="hello",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
            metadata={"author_name": "TestUser"},
        )
        j = json.dumps(asdict(ctx))
        parsed = json.loads(j)
        self.assertEqual(parsed["metadata"]["author_name"], "TestUser")


# ═══════════════════════════════════════════════════════════════
# Phase 1: HookDefinition + config loading
# ═══════════════════════════════════════════════════════════════


class TestHookDefinition(unittest.TestCase):
    """Test HookDefinition dataclass and config loading."""

    def test_create_hook_definition(self):
        hd = HookDefinition(
            id="log-received",
            events=["message:received"],
            command="python log_hook.py",
            enabled=True,
        )
        self.assertEqual(hd.id, "log-received")
        self.assertEqual(hd.events, ["message:received"])
        self.assertTrue(hd.enabled)

    def test_default_timeout(self):
        hd = HookDefinition(
            id="test",
            events=["message:received"],
            command="echo test",
        )
        self.assertEqual(hd.timeout, DEFAULT_HOOK_TIMEOUT)

    def test_source_filter_default_none(self):
        hd = HookDefinition(
            id="test",
            events=["message:received"],
            command="echo test",
        )
        self.assertIsNone(hd.source_filter)

    def test_load_hook_definitions_from_file(self):
        config = {
            "hooks": [
                {
                    "id": "hook1",
                    "events": ["message:received", "message:sent"],
                    "command": "python hook1.py",
                    "enabled": True,
                    "timeout": 5.0,
                },
                {
                    "id": "hook2",
                    "events": ["message:filtered"],
                    "command": "python hook2.py",
                    "enabled": False,
                    "source_filter": ["discord"],
                },
            ]
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config, f)
            f.flush()
            path = f.name
        try:
            hooks = load_hook_definitions(path)
            self.assertEqual(len(hooks), 2)
            self.assertEqual(hooks[0].id, "hook1")
            self.assertEqual(hooks[0].timeout, 5.0)
            self.assertFalse(hooks[1].enabled)
            self.assertEqual(hooks[1].source_filter, ["discord"])
        finally:
            os.unlink(path)

    def test_load_hook_definitions_missing_file(self):
        hooks = load_hook_definitions("/nonexistent/path.json")
        self.assertEqual(hooks, [])

    def test_load_hook_definitions_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not valid json{{{")
            f.flush()
            path = f.name
        try:
            hooks = load_hook_definitions(path)
            self.assertEqual(hooks, [])
        finally:
            os.unlink(path)

    def test_load_hook_definitions_empty_hooks(self):
        config = {"hooks": []}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config, f)
            f.flush()
            path = f.name
        try:
            hooks = load_hook_definitions(path)
            self.assertEqual(hooks, [])
        finally:
            os.unlink(path)


# ═══════════════════════════════════════════════════════════════
# Phase 1: Hook execution engine
# ═══════════════════════════════════════════════════════════════


class TestHookExecution(unittest.TestCase):
    """Test hook execution via subprocess."""

    def test_execute_hook_success(self):
        """Hook runs, receives JSON via stdin, returns exit 0."""
        dispatcher = HookDispatcher(hooks=[], log_path=None)
        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="hello",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
        )
        hook = HookDefinition(
            id="test-hook",
            events=["message:received"],
            command="python -c \"import sys,json; data=json.load(sys.stdin); print('ok')\"",
        )

        async def run():
            record = await dispatcher._execute_single_hook(hook, ctx)
            self.assertTrue(record.success)
            self.assertIn("ok", record.stdout)
            self.assertEqual(record.exit_code, 0)

        asyncio.run(run())

    def test_execute_hook_timeout(self):
        """Hook that exceeds timeout is killed."""
        dispatcher = HookDispatcher(hooks=[], log_path=None)
        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="hello",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
        )
        hook = HookDefinition(
            id="slow-hook",
            events=["message:received"],
            command="python -c \"import time; time.sleep(60)\"",
            timeout=0.5,
        )

        async def run():
            record = await dispatcher._execute_single_hook(hook, ctx)
            self.assertFalse(record.success)
            self.assertIn("timeout", record.error.lower())

        asyncio.run(run())

    def test_execute_hook_nonzero_exit(self):
        """Hook with nonzero exit code is recorded as failure."""
        dispatcher = HookDispatcher(hooks=[], log_path=None)
        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="hello",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
        )
        hook = HookDefinition(
            id="fail-hook",
            events=["message:received"],
            command="python -c \"import sys; sys.exit(1)\"",
        )

        async def run():
            record = await dispatcher._execute_single_hook(hook, ctx)
            self.assertFalse(record.success)
            self.assertEqual(record.exit_code, 1)

        asyncio.run(run())

    def test_execute_hook_exit2_blocking(self):
        """Hook with exit code 2 + stderr is recorded specially."""
        dispatcher = HookDispatcher(hooks=[], log_path=None)
        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="hello",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
        )
        hook = HookDefinition(
            id="block-hook",
            events=["message:received"],
            command="python -c \"import sys; sys.stderr.write('blocked!'); sys.exit(2)\"",
        )

        async def run():
            record = await dispatcher._execute_single_hook(hook, ctx)
            self.assertFalse(record.success)
            self.assertEqual(record.exit_code, 2)
            self.assertIn("blocked!", record.stderr)

        asyncio.run(run())

    def test_stdin_receives_valid_json(self):
        """Hook receives valid JSON on stdin with all context fields."""
        dispatcher = HookDispatcher(hooks=[], log_path=None)
        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="user1",
            channel_id="ch1",
            message_id="msg1",
            content="test message",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
            metadata={"author_name": "Tester"},
        )
        # Script that validates JSON fields and prints them
        script = (
            "import sys,json; d=json.load(sys.stdin); "
            "assert d['event']=='message:received'; "
            "assert d['sender_id']=='user1'; "
            "assert d['metadata']['author_name']=='Tester'; "
            "print('valid')"
        )
        hook = HookDefinition(
            id="validate-hook",
            events=["message:received"],
            command=f'python -c "{script}"',
        )

        async def run():
            record = await dispatcher._execute_single_hook(hook, ctx)
            self.assertTrue(record.success, f"Hook failed: {record.error} {record.stderr}")
            self.assertIn("valid", record.stdout)

        asyncio.run(run())


# ═══════════════════════════════════════════════════════════════
# Phase 1: Hook execution log
# ═══════════════════════════════════════════════════════════════


class TestHookExecutionLog(unittest.TestCase):
    """Test JSONL log with auto-pruning."""

    def test_append_and_read(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            log = HookExecutionLog(path, max_lines=100)
            record = HookExecutionRecord(
                timestamp="2026-03-22T00:00:00+00:00",
                hook_id="test-hook",
                event="message:received",
                success=True,
                duration_ms=42.0,
                exit_code=0,
                stdout="ok",
                stderr="",
                error="",
            )

            async def run():
                await log.append(record)
                lines = log.read_all()
                self.assertEqual(len(lines), 1)
                self.assertEqual(lines[0]["hook_id"], "test-hook")

            asyncio.run(run())
        finally:
            os.unlink(path)

    def test_auto_pruning(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            max_lines = 5
            log = HookExecutionLog(path, max_lines=max_lines)

            async def run():
                for i in range(10):
                    record = HookExecutionRecord(
                        timestamp=f"2026-03-22T00:00:{i:02d}+00:00",
                        hook_id=f"hook-{i}",
                        event="message:received",
                        success=True,
                        duration_ms=1.0,
                        exit_code=0,
                        stdout="",
                        stderr="",
                        error="",
                    )
                    await log.append(record)

                lines = log.read_all()
                self.assertLessEqual(len(lines), max_lines)
                # Oldest entries should be pruned
                ids = [l["hook_id"] for l in lines]
                self.assertNotIn("hook-0", ids)

            asyncio.run(run())
        finally:
            os.unlink(path)

    def test_log_none_path_no_error(self):
        """When log_path is None, appending should not error."""
        log = HookExecutionLog(None, max_lines=100)
        record = HookExecutionRecord(
            timestamp="2026-03-22T00:00:00+00:00",
            hook_id="test",
            event="message:received",
            success=True,
            duration_ms=1.0,
            exit_code=0,
            stdout="",
            stderr="",
            error="",
        )

        async def run():
            await log.append(record)  # Should not raise

        asyncio.run(run())


# ═══════════════════════════════════════════════════════════════
# Phase 1: HookDispatcher - event selection
# ═══════════════════════════════════════════════════════════════


class TestHookDispatcherEventSelection(unittest.TestCase):
    """Test hook selection by event type and source filter."""

    def test_select_hooks_by_event(self):
        hooks = [
            HookDefinition(id="h1", events=["message:received"], command="echo 1"),
            HookDefinition(id="h2", events=["message:sent"], command="echo 2"),
            HookDefinition(id="h3", events=["message:received", "message:sent"], command="echo 3"),
        ]
        dispatcher = HookDispatcher(hooks=hooks, log_path=None)
        selected = dispatcher._select_hooks("message:received", "discord")
        ids = [h.id for h in selected]
        self.assertIn("h1", ids)
        self.assertNotIn("h2", ids)
        self.assertIn("h3", ids)

    def test_skip_disabled_hooks(self):
        hooks = [
            HookDefinition(id="h1", events=["message:received"], command="echo 1", enabled=True),
            HookDefinition(id="h2", events=["message:received"], command="echo 2", enabled=False),
        ]
        dispatcher = HookDispatcher(hooks=hooks, log_path=None)
        selected = dispatcher._select_hooks("message:received", "discord")
        ids = [h.id for h in selected]
        self.assertIn("h1", ids)
        self.assertNotIn("h2", ids)

    def test_source_filter_match(self):
        hooks = [
            HookDefinition(id="h1", events=["message:received"], command="echo 1",
                           source_filter=["discord"]),
            HookDefinition(id="h2", events=["message:received"], command="echo 2",
                           source_filter=["slack"]),
        ]
        dispatcher = HookDispatcher(hooks=hooks, log_path=None)
        selected = dispatcher._select_hooks("message:received", "discord")
        ids = [h.id for h in selected]
        self.assertIn("h1", ids)
        self.assertNotIn("h2", ids)

    def test_source_filter_none_matches_all(self):
        hooks = [
            HookDefinition(id="h1", events=["message:received"], command="echo 1",
                           source_filter=None),
        ]
        dispatcher = HookDispatcher(hooks=hooks, log_path=None)
        selected = dispatcher._select_hooks("message:received", "anything")
        self.assertEqual(len(selected), 1)

    def test_no_hooks_for_event(self):
        hooks = [
            HookDefinition(id="h1", events=["message:sent"], command="echo 1"),
        ]
        dispatcher = HookDispatcher(hooks=hooks, log_path=None)
        selected = dispatcher._select_hooks("message:received", "discord")
        self.assertEqual(len(selected), 0)


# ═══════════════════════════════════════════════════════════════
# Phase 1: HookDispatcher - debounce
# ═══════════════════════════════════════════════════════════════


class TestHookDispatcherDebounce(unittest.TestCase):
    """Test consecutive fire suppression."""

    def test_debounce_suppresses_rapid_fire(self):
        hooks = [
            HookDefinition(id="h1", events=["message:received"], command="echo ok"),
        ]
        dispatcher = HookDispatcher(hooks=hooks, log_path=None, debounce_seconds=1.0)
        # Simulate a recent execution
        dispatcher._last_dispatch_time[("h1", "message:received")] = time.monotonic()

        selected = dispatcher._select_hooks("message:received", "discord")
        debounced = dispatcher._apply_debounce(selected, "message:received")
        self.assertEqual(len(debounced), 0)

    def test_debounce_allows_after_interval(self):
        hooks = [
            HookDefinition(id="h1", events=["message:received"], command="echo ok"),
        ]
        dispatcher = HookDispatcher(hooks=hooks, log_path=None, debounce_seconds=0.1)
        dispatcher._last_dispatch_time[("h1", "message:received")] = time.monotonic() - 1.0

        selected = dispatcher._select_hooks("message:received", "discord")
        debounced = dispatcher._apply_debounce(selected, "message:received")
        self.assertEqual(len(debounced), 1)

    def test_debounce_zero_means_no_suppression(self):
        hooks = [
            HookDefinition(id="h1", events=["message:received"], command="echo ok"),
        ]
        dispatcher = HookDispatcher(hooks=hooks, log_path=None, debounce_seconds=0.0)
        dispatcher._last_dispatch_time[("h1", "message:received")] = time.monotonic()

        selected = dispatcher._select_hooks("message:received", "discord")
        debounced = dispatcher._apply_debounce(selected, "message:received")
        self.assertEqual(len(debounced), 1)


# ═══════════════════════════════════════════════════════════════
# Phase 1: HookDispatcher - reentry prevention
# ═══════════════════════════════════════════════════════════════


class TestHookDispatcherReentryPrevention(unittest.TestCase):
    """Test _dispatch_depth prevents reentry."""

    def test_reentry_rejected(self):
        dispatcher = HookDispatcher(hooks=[], log_path=None)
        dispatcher._dispatch_depth = 1  # Simulate active dispatch

        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="hello",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
        )

        async def run():
            result = await dispatcher.dispatch(ctx)
            self.assertFalse(result)  # Should be rejected

        asyncio.run(run())
        # Depth should still be 1 (not incremented)
        self.assertEqual(dispatcher._dispatch_depth, 1)

    def test_normal_dispatch_increments_and_decrements_depth(self):
        hooks = [
            HookDefinition(id="h1", events=["message:received"],
                           command="python -c \"print('ok')\""),
        ]
        dispatcher = HookDispatcher(hooks=hooks, log_path=None)

        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="hello",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
        )

        async def run():
            await dispatcher.dispatch(ctx)
            self.assertEqual(dispatcher._dispatch_depth, 0)

        asyncio.run(run())


# ═══════════════════════════════════════════════════════════════
# Phase 1: HookDispatcher - global timeout
# ═══════════════════════════════════════════════════════════════


class TestHookDispatcherGlobalTimeout(unittest.TestCase):
    """Test global timeout for all hooks in one event."""

    def test_global_timeout_skips_remaining_hooks(self):
        hooks = [
            HookDefinition(id="slow1", events=["message:received"],
                           command="python -c \"import time; time.sleep(5)\"",
                           timeout=10.0),
            HookDefinition(id="slow2", events=["message:received"],
                           command="python -c \"import time; time.sleep(5)\"",
                           timeout=10.0),
        ]
        dispatcher = HookDispatcher(
            hooks=hooks, log_path=None, global_timeout=1.0,
        )

        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="hello",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
        )

        async def run():
            # fire_and_forget=False to test synchronous mode
            result = await dispatcher.dispatch(ctx, fire_and_forget=False)
            # At least one should be skipped due to global timeout
            self.assertTrue(result)  # dispatch itself succeeded

        asyncio.run(run())


# ═══════════════════════════════════════════════════════════════
# Phase 1: HookDispatcher - consecutive failure auto-disable
# ═══════════════════════════════════════════════════════════════


class TestHookDispatcherAutoDisable(unittest.TestCase):
    """Test consecutive failure auto-disable."""

    def test_auto_disable_after_consecutive_failures(self):
        hooks = [
            HookDefinition(id="bad-hook", events=["message:received"],
                           command="python -c \"import sys; sys.exit(1)\""),
        ]
        limit = 3
        dispatcher = HookDispatcher(
            hooks=hooks, log_path=None,
            consecutive_failure_limit=limit,
            debounce_seconds=0.0,
        )

        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="hello",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
        )

        async def run():
            for _ in range(limit + 1):
                await dispatcher.dispatch(ctx, fire_and_forget=False)

            # Hook should now be disabled
            self.assertIn("bad-hook", dispatcher._auto_disabled)

        asyncio.run(run())

    def test_success_resets_failure_count(self):
        hooks = [
            HookDefinition(id="flaky-hook", events=["message:received"],
                           command="python -c \"print('ok')\""),
        ]
        dispatcher = HookDispatcher(
            hooks=hooks, log_path=None,
            consecutive_failure_limit=3,
        )
        # Simulate 2 consecutive failures
        dispatcher._consecutive_failures["flaky-hook"] = 2

        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="hello",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
        )

        async def run():
            await dispatcher.dispatch(ctx, fire_and_forget=False)
            self.assertEqual(dispatcher._consecutive_failures.get("flaky-hook", 0), 0)

        asyncio.run(run())

    def test_auto_disabled_hooks_not_selected(self):
        hooks = [
            HookDefinition(id="disabled-hook", events=["message:received"], command="echo ok"),
        ]
        dispatcher = HookDispatcher(hooks=hooks, log_path=None)
        dispatcher._auto_disabled.add("disabled-hook")

        selected = dispatcher._select_hooks("message:received", "discord")
        self.assertEqual(len(selected), 0)


# ═══════════════════════════════════════════════════════════════
# Phase 1: HookDispatcher - fire_and_forget
# ═══════════════════════════════════════════════════════════════


class TestHookDispatcherFireAndForget(unittest.TestCase):
    """Test fire_and_forget async mode (default)."""

    def test_fire_and_forget_returns_immediately(self):
        hooks = [
            HookDefinition(id="slow-hook", events=["message:received"],
                           command="python -c \"import time; time.sleep(2); print('done')\""),
        ]
        dispatcher = HookDispatcher(hooks=hooks, log_path=None)

        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="hello",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
        )

        async def run():
            start = time.monotonic()
            result = await dispatcher.dispatch(ctx, fire_and_forget=True)
            elapsed = time.monotonic() - start
            self.assertTrue(result)
            # fire_and_forget should return well before the hook finishes
            self.assertLess(elapsed, 1.0)

        asyncio.run(run())

    def test_default_is_fire_and_forget(self):
        """dispatch() with no explicit fire_and_forget defaults to True."""
        hooks = [
            HookDefinition(id="h1", events=["message:received"],
                           command="python -c \"import time; time.sleep(2)\""),
        ]
        dispatcher = HookDispatcher(hooks=hooks, log_path=None)

        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="hello",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
        )

        async def run():
            start = time.monotonic()
            await dispatcher.dispatch(ctx)  # default fire_and_forget=True
            elapsed = time.monotonic() - start
            self.assertLess(elapsed, 1.0)

        asyncio.run(run())


# ═══════════════════════════════════════════════════════════════
# Phase 1: HookDispatcher - graceful shutdown
# ═══════════════════════════════════════════════════════════════


class TestHookDispatcherShutdown(unittest.TestCase):
    """Test graceful shutdown kills subprocesses."""

    def test_shutdown_kills_running_processes(self):
        hooks = [
            HookDefinition(id="long-hook", events=["message:received"],
                           command="python -c \"import time; time.sleep(60)\""),
        ]
        dispatcher = HookDispatcher(hooks=hooks, log_path=None)

        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="hello",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
        )

        async def run():
            # Fire and forget, so it starts in background
            await dispatcher.dispatch(ctx, fire_and_forget=True)
            await asyncio.sleep(0.3)  # Let subprocess start
            # Now shutdown
            await dispatcher.shutdown()
            # All tracked processes should be cleaned up
            self.assertEqual(len(dispatcher._running_processes), 0)

        asyncio.run(run())

    def test_shutdown_on_empty_is_noop(self):
        dispatcher = HookDispatcher(hooks=[], log_path=None)

        async def run():
            await dispatcher.shutdown()  # Should not raise

        asyncio.run(run())


# ═══════════════════════════════════════════════════════════════
# Phase 2: normalize_discord_message helper
# ═══════════════════════════════════════════════════════════════


class TestNormalizeDiscordMessage(unittest.TestCase):
    """Test Discord-specific data -> MessageEventContext conversion."""

    def test_basic_dm_message(self):
        message_data = {
            "id": "msg123",
            "author": {"id": "user456", "username": "TestUser", "discriminator": "0001"},
            "channel_id": "ch789",
            "content": "Hello world",
            "timestamp": "2026-03-22T00:00:00+00:00",
        }
        ctx = normalize_discord_message(
            event="message:received",
            message_data=message_data,
            is_dm=True,
        )
        self.assertEqual(ctx.event, "message:received")
        self.assertEqual(ctx.source, "discord")
        self.assertEqual(ctx.sender_id, "user456")
        self.assertEqual(ctx.channel_id, "ch789")
        self.assertEqual(ctx.message_id, "msg123")
        self.assertEqual(ctx.content, "Hello world")
        self.assertEqual(ctx.conversation_type, "dm")
        self.assertEqual(ctx.metadata.get("author_name"), "TestUser")

    def test_channel_message(self):
        message_data = {
            "id": "msg001",
            "author": {"id": "user002", "username": "ChannelUser"},
            "channel_id": "ch003",
            "content": "In a channel",
            "timestamp": "2026-03-22T01:00:00+00:00",
            "guild_id": "guild004",
        }
        ctx = normalize_discord_message(
            event="message:received",
            message_data=message_data,
            is_dm=False,
        )
        self.assertEqual(ctx.conversation_type, "channel")
        self.assertEqual(ctx.metadata.get("guild_id"), "guild004")

    def test_filtered_event_with_result(self):
        message_data = {
            "id": "msg123",
            "author": {"id": "user456", "username": "TestUser"},
            "channel_id": "ch789",
            "content": "Hello",
            "timestamp": "2026-03-22T00:00:00+00:00",
        }
        ctx = normalize_discord_message(
            event="message:filtered",
            message_data=message_data,
            is_dm=True,
            filter_passed=True,
            filter_reason="",
        )
        self.assertEqual(ctx.event, "message:filtered")
        self.assertTrue(ctx.filter_passed)

    def test_missing_author_fields_handled(self):
        message_data = {
            "id": "msg1",
            "author": {},
            "channel_id": "ch1",
            "content": "test",
            "timestamp": "2026-03-22T00:00:00+00:00",
        }
        ctx = normalize_discord_message(
            event="message:received",
            message_data=message_data,
            is_dm=True,
        )
        self.assertEqual(ctx.sender_id, "")
        self.assertEqual(ctx.metadata.get("author_name", ""), "")


# ═══════════════════════════════════════════════════════════════
# Phase 3: Integration test - dispatch with no hooks
# ═══════════════════════════════════════════════════════════════


class TestHookDispatcherIntegration(unittest.TestCase):
    """Integration-level tests for the full dispatch flow."""

    def test_dispatch_with_no_hooks_succeeds(self):
        dispatcher = HookDispatcher(hooks=[], log_path=None)
        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="hello",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
        )

        async def run():
            result = await dispatcher.dispatch(ctx)
            self.assertTrue(result)

        asyncio.run(run())

    def test_dispatch_full_flow_with_real_hook(self):
        """Full dispatch with a real hook that succeeds."""
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            log_path = f.name

        hooks = [
            HookDefinition(id="echo-hook", events=["message:received"],
                           command="python -c \"import sys,json; d=json.load(sys.stdin); print(d['event'])\""),
        ]
        dispatcher = HookDispatcher(hooks=hooks, log_path=log_path)

        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="hello",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
        )

        try:
            async def run():
                result = await dispatcher.dispatch(ctx, fire_and_forget=False)
                self.assertTrue(result)
                # Check log was written
                log = HookExecutionLog(log_path, max_lines=100)
                lines = log.read_all()
                self.assertEqual(len(lines), 1)
                self.assertEqual(lines[0]["hook_id"], "echo-hook")
                self.assertTrue(lines[0]["success"])

            asyncio.run(run())
        finally:
            os.unlink(log_path)

    def test_dispatch_pipeline_continues_on_hook_failure(self):
        """Hook failure does not prevent dispatch from returning True."""
        hooks = [
            HookDefinition(id="bad-hook", events=["message:received"],
                           command="python -c \"import sys; sys.exit(1)\""),
        ]
        dispatcher = HookDispatcher(hooks=hooks, log_path=None)

        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="hello",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
        )

        async def run():
            result = await dispatcher.dispatch(ctx, fire_and_forget=False)
            self.assertTrue(result)  # Pipeline continues despite hook failure

        asyncio.run(run())

    def test_dispatch_multiple_hooks_sequential(self):
        """Multiple hooks execute in order."""
        hooks = [
            HookDefinition(id="h1", events=["message:received"],
                           command="python -c \"print('first')\""),
            HookDefinition(id="h2", events=["message:received"],
                           command="python -c \"print('second')\""),
        ]
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            log_path = f.name

        dispatcher = HookDispatcher(hooks=hooks, log_path=log_path)

        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="hello",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
        )

        try:
            async def run():
                await dispatcher.dispatch(ctx, fire_and_forget=False)
                log = HookExecutionLog(log_path, max_lines=100)
                lines = log.read_all()
                self.assertEqual(len(lines), 2)
                self.assertEqual(lines[0]["hook_id"], "h1")
                self.assertEqual(lines[1]["hook_id"], "h2")

            asyncio.run(run())
        finally:
            os.unlink(log_path)


# ═══════════════════════════════════════════════════════════════
# Phase 3: Event constants
# ═══════════════════════════════════════════════════════════════


class TestEventConstants(unittest.TestCase):
    """Verify event constant values."""

    def test_event_values(self):
        self.assertEqual(EVENT_RECEIVED, "message:received")
        self.assertEqual(EVENT_FILTERED, "message:filtered")
        self.assertEqual(EVENT_BUFFERED, "message:buffered")
        self.assertEqual(EVENT_SANITIZED, "message:sanitized")
        self.assertEqual(EVENT_SENT, "message:sent")

    def test_all_events_contains_all(self):
        self.assertIn(EVENT_RECEIVED, ALL_EVENTS)
        self.assertIn(EVENT_FILTERED, ALL_EVENTS)
        self.assertIn(EVENT_BUFFERED, ALL_EVENTS)
        self.assertIn(EVENT_SANITIZED, ALL_EVENTS)
        self.assertIn(EVENT_SENT, ALL_EVENTS)
        self.assertEqual(len(ALL_EVENTS), 5)


# ═══════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════


class TestEdgeCases(unittest.TestCase):
    """Edge case tests."""

    def test_empty_content_message(self):
        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
        )
        self.assertEqual(ctx.content, "")

    def test_very_large_content(self):
        large = "x" * 100000
        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content=large,
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
        )
        # Should be serializable
        j = json.dumps(asdict(ctx))
        self.assertIn(large[:100], j)

    def test_special_chars_in_content(self):
        content = 'hello "world"\nnewline\ttab\\backslash'
        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content=content,
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
        )
        j = json.dumps(asdict(ctx))
        parsed = json.loads(j)
        self.assertEqual(parsed["content"], content)

    def test_dispatcher_with_unknown_event_type(self):
        """Unknown event type just results in no hooks selected."""
        hooks = [
            HookDefinition(id="h1", events=["message:received"], command="echo 1"),
        ]
        dispatcher = HookDispatcher(hooks=hooks, log_path=None)
        selected = dispatcher._select_hooks("message:unknown", "discord")
        self.assertEqual(len(selected), 0)

    def test_hook_command_not_found(self):
        """Command that doesn't exist should fail gracefully."""
        dispatcher = HookDispatcher(hooks=[], log_path=None)
        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="hello",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
        )
        hook = HookDefinition(
            id="bad-cmd",
            events=["message:received"],
            command="/nonexistent/command/xyz123",
        )

        async def run():
            record = await dispatcher._execute_single_hook(hook, ctx)
            self.assertFalse(record.success)
            self.assertTrue(len(record.error) > 0)

        asyncio.run(run())


# ═══════════════════════════════════════════════════════════════
# C1: Shell Injection Fix — create_subprocess_exec + shlex.split
# ═══════════════════════════════════════════════════════════════


class TestShellInjectionFix(unittest.TestCase):
    """Verify shell injection is prevented by using exec instead of shell."""

    def test_uses_shlex_split(self):
        """Verify that _execute_single_hook uses shlex.split to parse commands."""
        import shlex
        dispatcher = HookDispatcher(hooks=[], log_path=None)
        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="hello",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
        )
        hook = HookDefinition(
            id="shlex-test",
            events=["message:received"],
            command="python -c \"print('safe')\"",
        )

        async def run():
            with patch("message_event_hooks.asyncio.create_subprocess_exec") as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.communicate = AsyncMock(return_value=(b"safe\n", b""))
                mock_proc.returncode = 0
                mock_proc.kill = MagicMock()
                mock_exec.return_value = mock_proc

                record = await dispatcher._execute_single_hook(hook, ctx)
                # Verify create_subprocess_exec was called (not shell)
                mock_exec.assert_called_once()
                call_args = mock_exec.call_args
                # First positional args should be shlex.split result
                expected_parts = shlex.split(hook.command)
                actual_args = call_args[0]
                self.assertEqual(list(actual_args), expected_parts)

        asyncio.run(run())

    def test_no_create_subprocess_shell_used(self):
        """Verify create_subprocess_shell is NOT used anywhere in execution."""
        import inspect
        source = inspect.getsource(HookDispatcher._execute_single_hook)
        self.assertNotIn("create_subprocess_shell", source)
        self.assertIn("create_subprocess_exec", source)

    def test_exec_with_simple_command(self):
        """Verify a simple command still works with exec mode."""
        dispatcher = HookDispatcher(hooks=[], log_path=None)
        ctx = MessageEventContext(
            event="message:received",
            source="discord",
            sender_id="123",
            channel_id="456",
            message_id="789",
            content="hello",
            timestamp="2026-03-22T00:00:00+00:00",
            conversation_type="dm",
        )
        hook = HookDefinition(
            id="exec-test",
            events=["message:received"],
            command="python -c \"import sys,json; data=json.load(sys.stdin); print('exec-ok')\"",
        )

        async def run():
            record = await dispatcher._execute_single_hook(hook, ctx)
            self.assertTrue(record.success)
            self.assertIn("exec-ok", record.stdout)

        asyncio.run(run())


class TestHookExceptionSpecificity(unittest.TestCase):
    """M-S9: Broad except Exception should catch specific types and log unexpected ones."""

    def test_file_not_found_caught_specifically(self):
        """FileNotFoundError (bad command) should be caught and reported."""
        dispatcher = HookDispatcher(hooks=[], log_path=None)
        hook = HookDefinition(
            id="bad-cmd",
            events=["received"],
            command="/nonexistent/command arg1",
            timeout=5.0,
        )
        ctx = MessageEventContext(
            event="received",
            source="test",
            sender_id="456",
            channel_id="123",
            message_id="test",
            content="hello",
            timestamp="2026-01-01T00:00:00Z",
            conversation_type="dm",
        )
        import asyncio
        record = asyncio.run(dispatcher._execute_single_hook(hook, ctx))
        self.assertFalse(record.success)
        # Error should be logged, not silently swallowed
        self.assertTrue(len(record.error) > 0)

    def test_unexpected_exception_logged(self):
        """Unexpected exceptions should be logged (not silently swallowed)."""
        import shlex as _shlex
        dispatcher = HookDispatcher(hooks=[], log_path=None)
        hook = HookDefinition(
            id="test-hook",
            events=["received"],
            command="echo test",
            timeout=5.0,
        )
        ctx = MessageEventContext(
            event="received",
            source="test",
            sender_id="456",
            channel_id="123",
            message_id="test",
            content="hello",
            timestamp="2026-01-01T00:00:00Z",
            conversation_type="dm",
        )
        # Monkeypatch shlex.split to throw an unexpected error
        import asyncio
        import message_event_hooks
        original_split = message_event_hooks.shlex.split
        message_event_hooks.shlex.split = lambda cmd: (_ for _ in ()).throw(RuntimeError("unexpected"))
        try:
            record = asyncio.run(dispatcher._execute_single_hook(hook, ctx))
            self.assertFalse(record.success)
            self.assertIn("unexpected", record.error)
        finally:
            message_event_hooks.shlex.split = original_split


if __name__ == "__main__":
    unittest.main()
