"""Tests for memory_search hang prevention (4th reoccurrence fix).

Verifies:
1. _embed_with_timeout default reduced from 60.0s to 15.0s.
2. _run_with_watchdog helper enforces upper-bound timeout on memory_search.
3. _bg_sync_vectors uses a module-level lock to prevent duplicate concurrent runs
   that would saturate the embedding API rate-limit slots.

Design refs: tools/semantic_index.py:_embed_with_timeout,
             tools/memory_mcp_server.py:_run_with_watchdog, _bg_sync_vectors.
"""

import inspect
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

# Add tools/ to import path
sys.path.insert(0, str(Path(__file__).parent.parent))

import memory_mcp_server  # noqa: E402
import semantic_index  # noqa: E402

# =============================================================================
# TestEmbedTimeoutDefault
# =============================================================================


class TestEmbedTimeoutDefault:
    """_embed_with_timeout default must be 15.0s (was 60.0s — caused hang loops)."""

    def test_default_is_15_seconds(self):
        """The default timeout parameter must be 15.0 seconds.

        4th hang reoccurrence root cause: commit 0cb1ced loosened 5s -> 60s.
        Reverting to a tighter default (15s) prevents the MCP disconnect loop.
        """
        sig = inspect.signature(semantic_index._embed_with_timeout)
        timeout_param = sig.parameters.get("timeout")
        assert timeout_param is not None, "timeout parameter must exist"
        assert timeout_param.default == 15.0, (
            f"Expected default timeout=15.0, got {timeout_param.default}. "
            "60s is too loose and caused 4 reoccurrences of memory_search hang."
        )

    def test_slow_embed_returns_none(self):
        """If the underlying embed is slower than the timeout, return None (FTS fallback)."""
        provider = MagicMock()
        slow_event = threading.Event()

        def slow_embed(text):
            # Block far longer than the timeout we'll set.
            slow_event.wait(timeout=5.0)
            return [0.1] * 384

        provider.embed_single.side_effect = slow_embed

        start = time.time()
        result = semantic_index._embed_with_timeout(provider, "any", timeout=0.2)
        elapsed = time.time() - start

        # Release the slow thread so we don't leak a daemon waiting on the event
        slow_event.set()

        assert result is None, "Slow embed must return None (FTS-only fallback)"
        assert elapsed < 1.0, f"Timeout enforcement broken: elapsed={elapsed}s"

    def test_fast_embed_returns_vector(self):
        """If embed completes within the timeout, return its vector unchanged."""
        provider = MagicMock()
        provider.embed_single.return_value = [0.5, 0.5, 0.5]

        result = semantic_index._embed_with_timeout(provider, "any", timeout=2.0)
        assert result == [0.5, 0.5, 0.5]


# =============================================================================
# TestMemorySearchWatchdog
# =============================================================================


class TestMemorySearchWatchdog:
    """_run_with_watchdog enforces an upper-bound timeout on any callable."""

    def test_helper_exists(self):
        """memory_mcp_server._run_with_watchdog must be defined."""
        assert hasattr(memory_mcp_server, "_run_with_watchdog"), (
            "memory_mcp_server._run_with_watchdog helper is required for "
            "memory_search timeout enforcement."
        )

    def test_fast_function_returns_value(self):
        """Fast functions return (value, None)."""
        def fast(a, b, *, c=0):
            return a + b + c

        value, error = memory_mcp_server._run_with_watchdog(
            fast, args=(1, 2), kwargs={"c": 3}, timeout=2.0
        )
        assert value == 6
        assert error is None

    def test_timeout_returns_timeout_error(self):
        """A function exceeding the timeout returns (None, TimeoutError)."""
        slow_event = threading.Event()

        def slow():
            slow_event.wait(timeout=5.0)
            return "should not arrive"

        start = time.time()
        value, error = memory_mcp_server._run_with_watchdog(
            slow, args=(), kwargs={}, timeout=0.2
        )
        elapsed = time.time() - start

        # Release the daemon thread
        slow_event.set()

        assert value is None
        assert isinstance(error, TimeoutError), (
            f"Expected TimeoutError, got {type(error).__name__}"
        )
        assert elapsed < 1.0, (
            f"Watchdog did not enforce timeout (elapsed={elapsed}s)"
        )

    def test_exception_propagates(self):
        """If the wrapped function raises, the watchdog returns (None, exc)."""
        def boom():
            raise ValueError("kaboom")

        value, error = memory_mcp_server._run_with_watchdog(
            boom, args=(), kwargs={}, timeout=2.0
        )
        assert value is None
        assert isinstance(error, ValueError)
        assert "kaboom" in str(error)

    def test_baseexception_in_runner_returns_runtime_error_not_none(self):
        """BaseException must NOT silently return (None, None).

        Reviewer/Red Team WEAK-1: without completion sentinel, KeyboardInterrupt/
        SystemExit in the worker leaves error_box=None, watchdog returns (None, None),
        and memory_search would return NoneType to MCP — masking the failure.
        """
        def angry():
            raise KeyboardInterrupt('user interrupt')

        value, error = memory_mcp_server._run_with_watchdog(
            angry, args=(), kwargs={}, timeout=2.0
        )
        assert value is None
        assert error is not None
        assert isinstance(error, RuntimeError)
        assert 'without completing' in str(error)


# =============================================================================
# TestBgSyncDuplicatePrevention
# =============================================================================


class TestBgSyncDuplicatePrevention:
    """Concurrent _bg_sync_vectors invocations must not both run.

    The embedding API rate limiter has only 2 slots; duplicate concurrent
    sync threads exhaust them and block all subsequent memory_search calls.
    """

    def test_lock_is_threading_lock(self):
        """memory_mcp_server._bg_sync_lock must be a threading.Lock instance."""
        assert hasattr(memory_mcp_server, "_bg_sync_lock"), (
            "_bg_sync_lock module-level guard is required."
        )
        lock = memory_mcp_server._bg_sync_lock
        # threading.Lock() returns a builtin; check via acquire/release contract
        assert hasattr(lock, "acquire") and hasattr(lock, "release"), (
            "_bg_sync_lock must implement the lock protocol"
        )

    def test_running_flag_exists(self):
        """The _bg_sync_running module-level flag must be defined."""
        assert hasattr(memory_mcp_server, "_bg_sync_running"), (
            "_bg_sync_running flag is required to coordinate duplicate prevention."
        )

    def test_second_call_returns_immediately_while_first_running(self, monkeypatch, tmp_path):
        """While one _bg_sync_vectors is running, a second call must return immediately."""
        # Patch SemanticIndex so sync_vectors blocks on an Event we control.
        first_started = threading.Event()
        release_first = threading.Event()

        class FakeIndex:
            def __init__(self, mem_dir):
                self.mem_dir = mem_dir

            def sync_vectors(self):
                first_started.set()
                release_first.wait(timeout=5.0)

            def close(self):
                pass

        monkeypatch.setattr(memory_mcp_server, "SemanticIndex", FakeIndex)

        # Reset module state under monkeypatch so other tests aren't affected.
        monkeypatch.setattr(memory_mcp_server, "_bg_sync_running", False)

        mem_dir = str(tmp_path)

        # Kick off the first sync — it will block inside FakeIndex.sync_vectors()
        t1 = threading.Thread(
            target=memory_mcp_server._bg_sync_vectors, args=(mem_dir,), daemon=True
        )
        t1.start()
        assert first_started.wait(timeout=2.0), "First sync did not start"

        # Now invoke a second time directly — it should return immediately
        # because _bg_sync_running is True under the lock.
        start = time.time()
        memory_mcp_server._bg_sync_vectors(mem_dir)
        elapsed = time.time() - start

        assert elapsed < 0.5, (
            f"Second _bg_sync_vectors call did not return immediately "
            f"(elapsed={elapsed}s). Duplicate prevention is broken."
        )

        # Clean up: release the first thread
        release_first.set()
        t1.join(timeout=2.0)

    def test_flag_resets_after_completion(self, monkeypatch, tmp_path):
        """After _bg_sync_vectors finishes, _bg_sync_running must be False."""
        class FakeIndex:
            def __init__(self, mem_dir):
                self.mem_dir = mem_dir

            def sync_vectors(self):
                return None

            def close(self):
                pass

        monkeypatch.setattr(memory_mcp_server, "SemanticIndex", FakeIndex)
        monkeypatch.setattr(memory_mcp_server, "_bg_sync_running", False)

        memory_mcp_server._bg_sync_vectors(str(tmp_path))

        assert memory_mcp_server._bg_sync_running is False, (
            "Flag must reset to False after sync completes (incl. on exception)."
        )

    def test_flag_resets_on_exception(self, monkeypatch, tmp_path):
        """If sync_vectors raises, _bg_sync_running must still reset to False."""
        class FakeIndex:
            def __init__(self, mem_dir):
                self.mem_dir = mem_dir

            def sync_vectors(self):
                raise RuntimeError("simulated failure")

            def close(self):
                pass

        monkeypatch.setattr(memory_mcp_server, "SemanticIndex", FakeIndex)
        monkeypatch.setattr(memory_mcp_server, "_bg_sync_running", False)

        # Should not raise (fail-open)
        memory_mcp_server._bg_sync_vectors(str(tmp_path))

        assert memory_mcp_server._bg_sync_running is False, (
            "Flag must reset even when sync_vectors raises."
        )

    def test_stale_running_flag_can_be_overridden(self, monkeypatch, tmp_path):
        """If _bg_sync_running=True with a stale started_at (>300s old), a new call must take over.

        Reviewer MED issue: a hung embedding API call could leave _bg_sync_running=True
        indefinitely, silently disabling vector index updates for the remainder of the
        server lifetime. The stale-flag detection lets a fresh call reclaim the slot.
        """
        sync_invocation_count = [0]

        class FakeIndex:
            def __init__(self, mem_dir):
                self.mem_dir = mem_dir

            def sync_vectors(self):
                sync_invocation_count[0] += 1

            def close(self):
                pass

        monkeypatch.setattr(memory_mcp_server, "SemanticIndex", FakeIndex)

        # Simulate a stuck previous run: flag=True, started_at=400s ago (> 300s threshold)
        monkeypatch.setattr(memory_mcp_server, "_bg_sync_running", True)
        monkeypatch.setattr(
            memory_mcp_server,
            "_bg_sync_started_at",
            time.time() - 400.0,
        )

        memory_mcp_server._bg_sync_vectors(str(tmp_path))

        assert sync_invocation_count[0] == 1, (
            "Stale flag (>STALE_SECONDS old) must be overridden — sync must run."
        )
        assert memory_mcp_server._bg_sync_running is False, (
            "Flag must reset to False after the new run completes."
        )

    def test_fresh_running_flag_blocks_concurrent_call(self, monkeypatch, tmp_path):
        """If _bg_sync_running=True with a recent started_at, a concurrent call must skip."""
        sync_invocation_count = [0]

        class FakeIndex:
            def __init__(self, mem_dir):
                self.mem_dir = mem_dir

            def sync_vectors(self):
                sync_invocation_count[0] += 1

            def close(self):
                pass

        monkeypatch.setattr(memory_mcp_server, "SemanticIndex", FakeIndex)

        # Simulate a fresh in-flight run: flag=True, started 1s ago
        monkeypatch.setattr(memory_mcp_server, "_bg_sync_running", True)
        monkeypatch.setattr(
            memory_mcp_server,
            "_bg_sync_started_at",
            time.time() - 1.0,
        )

        memory_mcp_server._bg_sync_vectors(str(tmp_path))

        assert sync_invocation_count[0] == 0, (
            "Fresh in-flight flag must cause the second call to skip (no double-run)."
        )


# =============================================================================
# TestMemorySearchEndToEndTimeout
# =============================================================================


class TestMemorySearchEndToEndTimeout:
    """memory_search public wrapper must return a TIMEOUT diagnostic string when
    the watchdog fires, instead of raising or hanging the MCP transport.
    """

    def test_memory_search_returns_timeout_string_when_watchdog_fires(self, monkeypatch):
        """When _memory_search_impl exceeds the watchdog timeout, the public
        memory_search must return a string containing '=== TIMEOUT ==='.

        memory_search is async (G66 fix: prevents FastMCP asyncio loop block);
        run via asyncio.run for this synchronous test.
        """
        import asyncio as _asyncio

        def slow_impl(*args, **kwargs):
            time.sleep(2.0)
            return "should never be returned"

        monkeypatch.setattr(memory_mcp_server, "_memory_search_impl", slow_impl)
        monkeypatch.setattr(memory_mcp_server, "_MEMORY_SEARCH_WATCHDOG_TIMEOUT", 0.3)

        result = _asyncio.run(memory_mcp_server.memory_search(query="anything"))

        assert isinstance(result, str), "memory_search must return a string"
        assert "=== TIMEOUT ===" in result, (
            f"Expected '=== TIMEOUT ===' in result on watchdog fire; got: {result!r}"
        )

    def test_memory_search_does_not_block_event_loop_during_watchdog(self, monkeypatch):
        """G66 regression guard: while memory_search waits for the watchdog,
        the asyncio event loop must continue scheduling other coroutines.

        With the previous sync implementation, the loop was frozen for the
        entire watchdog duration (90s in production), which caused the MCP
        transport to take 300+s to deliver the response. The async version
        must yield control back to the loop while the worker thread runs.
        """
        import asyncio as _asyncio

        def slow_impl(*args, **kwargs):
            time.sleep(0.5)
            return "should not arrive"

        monkeypatch.setattr(memory_mcp_server, "_memory_search_impl", slow_impl)
        monkeypatch.setattr(memory_mcp_server, "_MEMORY_SEARCH_WATCHDOG_TIMEOUT", 0.2)

        async def runner():
            tick_counts = [0]

            async def ticker():
                # If the loop is unblocked, this should run many times during
                # the 0.2s watchdog wait. If the loop is blocked, it never runs.
                while True:
                    tick_counts[0] += 1
                    await _asyncio.sleep(0.01)

            ticker_task = _asyncio.create_task(ticker())
            try:
                result = await memory_mcp_server.memory_search(query="anything")
            finally:
                ticker_task.cancel()
                try:
                    await ticker_task
                except _asyncio.CancelledError:
                    pass

            return result, tick_counts[0]

        result, ticks = _asyncio.run(runner())
        assert "=== TIMEOUT ===" in result
        # During a 0.2s watchdog wait with a 0.01s ticker, we expect roughly
        # 15-20 ticks. Anything below 5 indicates the loop is being blocked.
        assert ticks >= 5, (
            f"Event loop appears blocked during memory_search; got only {ticks} ticks "
            "(expected >=5 over 0.2s with 0.01s sleep). G66 regression."
        )
