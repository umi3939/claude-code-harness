"""G66 真因再現テスト — ThreadPoolExecutor.Future.cancel() 失敗の再現.

設計: docs/design_g66_root_cause_v2.md
計画: docs/plan_g66_root_cause_v2.md (Phase 1)
解析: docs/analysis_g66_root_cause_pre_impl.md (HIGH-1, MED-1, MED-3 反映)

Pre-fix 実測: elapsed=300.02s で FAIL (block_event の wait timeout に張り付き).
Post-fix 期待: elapsed≈2s で PASS (asyncio.wait + FIRST_COMPLETED race).

真因:
    asyncio.wait_for + asyncio.to_thread(=run_in_executor) パターンは、
    ThreadPoolExecutor の Future が RUNNING 状態だと cancel() が失敗する
    (Python の concurrent.futures._base.Future.cancel は RUNNING 中は False
    を返す)。結果、wait_for は timeout 経過後も Future の完了を待ち続け、
    "watchdog による timeout 配信" 自体がブロックされる。

このテストは _memory_search_impl を「永遠に近く block する関数」に差し替え、
memory_search が _MEMORY_SEARCH_WATCHDOG_TIMEOUT 経過直後に必ず
TIMEOUT 文字列を返すことを assert する。

Pre-fix: cancel 失敗で elapsed が pytest --timeout (30s) に到達して FAIL
Post-fix: asyncio.wait + FIRST_COMPLETED で timer 先着、elapsed ≈ 2.0s で PASS
"""
import asyncio
import sys
import threading
import time
from pathlib import Path

import pytest

# Add tools/ to import path
sys.path.insert(0, str(Path(__file__).parent.parent))

import memory_mcp_server as srv  # noqa: E402

# Cleanup events shared across tests so daemon threads don't leak between cases.
_CLEANUP_EVENTS: list[threading.Event] = []


@pytest.fixture(autouse=True)
def _release_blocked_threads():
    """Ensure any block_event created during a test is set after the test ends."""
    yield
    while _CLEANUP_EVENTS:
        ev = _CLEANUP_EVENTS.pop()
        ev.set()


def test_memory_search_returns_within_timeout_when_impl_blocks_indefinitely(
    monkeypatch,
):
    """G66 真因再現: _memory_search_impl が block しても WATCHDOG 経過直後に TIMEOUT 配信.

    Pre-fix の挙動 (asyncio.wait_for + run_in_executor):
        - watchdog timeout (2.0s) 経過時、wait_for は内部 future に cancel() を送る
        - だが ThreadPoolExecutor の Future は RUNNING 中で cancel() を拒否
        - wait_for は cancel 失敗を検知できず、impl thread の完了を待ち続ける
        - elapsed >> 3.0s に達し、最終的に pytest --timeout で殺される

    Post-fix の挙動 (asyncio.wait + FIRST_COMPLETED race):
        - timer_task (asyncio.sleep) は cancel に依存せず必ず timeout で完了
        - timer 先着で done set に入り、TIMEOUT 文字列を即座に return
        - elapsed ≈ 2.0s で PASS (impl thread はバックグラウンドで生存)
    """
    block_event = threading.Event()
    _CLEANUP_EVENTS.append(block_event)

    def _blocking_impl(**_kwargs):
        # 永遠に近い block。テスト終了時に fixture が set() する。
        block_event.wait(timeout=300.0)
        return "should not reach here"

    # HIGH-1 反映: monkeypatch 直後に sanity assert で差し替え成功を保証
    monkeypatch.setattr(srv, "_memory_search_impl", _blocking_impl)
    assert srv._memory_search_impl is _blocking_impl, (
        "monkeypatch failed to replace _memory_search_impl — global lookup may "
        "be bypassed (closure capture)."
    )

    # MED-3 反映: テスト時間圧縮のため WATCHDOG を 2.0s に短縮
    monkeypatch.setattr(srv, "_MEMORY_SEARCH_WATCHDOG_TIMEOUT", 2.0)

    # Measure elapsed INSIDE the runner, before asyncio.run() closes the loop.
    # asyncio.run() at exit calls loop.shutdown_default_executor(), which
    # waits for the still-running _blocking_impl thread (its block_event has
    # not been set yet) — so the wall clock around asyncio.run() conflates
    # memory_search latency with executor shutdown latency. Capture elapsed
    # first, then release the impl thread so cleanup is fast.
    elapsed_box: list[float] = []

    async def _runner():
        t0 = time.time()
        result = await srv.memory_search(query="test", limit=1)
        elapsed_box.append(time.time() - t0)
        block_event.set()
        return result

    result = asyncio.run(_runner())
    assert elapsed_box, "runner did not capture elapsed (memory_search did not return)"
    elapsed = elapsed_box[0]

    # tolerance 1.0s: watchdog (2.0) + asyncio scheduling overhead
    assert elapsed < 3.0, (
        f"memory_search did not return within watchdog+tolerance (elapsed={elapsed:.2f}s). "
        "Pre-fix: ThreadPoolExecutor.Future.cancel() failed during RUNNING state; "
        "wait_for waited for the blocked thread instead of honoring the timeout."
    )
    assert "=== TIMEOUT ===" in result, (
        f"Expected '=== TIMEOUT ===' in result on watchdog fire; got: {result!r}"
    )


def test_memory_search_does_not_leak_pending_task_when_timer_wins(monkeypatch):
    """MED-4 検証: timer 先着時、impl_task は done callback 経由で消費される.

    add_done_callback(_swallow_stale_result) を経由して stale impl_task の例外/結果を
    silent に捨てるが、event loop が close される前に impl_task が完了するなら
    "Task was destroyed but it is pending!" warning は出ない。

    本テストは block_event.set() で impl_task を解放した後、loop close で warning が
    出ないことを確認するスモーク。完璧な再現は難しいので、最低限
    elapsed < 4.0s かつ TIMEOUT 文字列が返ることだけを assert する。
    """
    block_event = threading.Event()
    _CLEANUP_EVENTS.append(block_event)

    def _short_block_impl(**_kwargs):
        # WATCHDOG (2.0s) の後 0.5s で release されるよう設定
        block_event.wait(timeout=2.5)
        return "stale result"

    monkeypatch.setattr(srv, "_memory_search_impl", _short_block_impl)
    monkeypatch.setattr(srv, "_MEMORY_SEARCH_WATCHDOG_TIMEOUT", 2.0)

    async def _runner():
        result = await srv.memory_search(query="test", limit=1)
        # impl_task が完了する余地を与える (callback fire)
        block_event.set()
        await asyncio.sleep(0.6)
        return result

    t0 = time.time()
    result = asyncio.run(_runner())
    elapsed = time.time() - t0

    assert elapsed < 4.0, f"runner exceeded budget (elapsed={elapsed:.2f}s)"
    assert "=== TIMEOUT ===" in result


def test_memory_search_cancellation_swallows_stale_impl_task(monkeypatch):
    """G66 v2 MED-1 reproduction: 外部 cancel で stale impl_task が
    ``_swallow_stale_result`` に消費され、event loop close 時の
    "Task was destroyed but it is pending!" / "Task exception was never
    retrieved" warning が出ないことを保証する。

    実機シナリオ: クライアント切断や session shutdown で FastMCP が
    memory_search coroutine に CancelledError を送信。impl_task は
    ThreadPoolExecutor で RUNNING のまま impl_task.cancel() が空振り。
    本テストはその状態で warning が出ないことを smoke 検証する。
    """
    block_event = threading.Event()
    _CLEANUP_EVENTS.append(block_event)

    def _blocking_impl(**_kwargs):
        block_event.wait(timeout=300.0)
        return "should not reach here"

    monkeypatch.setattr(srv, "_memory_search_impl", _blocking_impl)
    monkeypatch.setattr(srv, "_MEMORY_SEARCH_WATCHDOG_TIMEOUT", 5.0)

    async def _runner():
        task = asyncio.create_task(
            srv.memory_search(query="cancel-test", limit=1)
        )
        await asyncio.sleep(0.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    import warnings
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        asyncio.run(_runner())
    # 解放してバックグラウンド thread を終了させる
    block_event.set()

    relevant = [
        w for w in captured
        if "Task" in str(w.message) and "destroyed" in str(w.message)
    ]
    assert not relevant, (
        f"unexpected pending-task warning: "
        f"{[str(w.message) for w in relevant]}"
    )
