"""Tests for HIGH H8-H17 fixes: Performance + Code Quality."""
import os
import sys
import math
import time
import unittest
from collections import OrderedDict
from unittest.mock import patch, MagicMock

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)


class TestSessionFileCache(unittest.TestCase):
    """H8: episode_recall._list_session_files should cache results."""
    def test_session_file_cache_exists(self):
        from episode_recall import _session_files_cache
        self.assertIsInstance(_session_files_cache, dict)


class TestVectorSearchTopK(unittest.TestCase):
    """H9: Python fallback should use heapq.nlargest."""
    def test_python_fallback_uses_heapq(self):
        import inspect
        from vector_search import _vec_search_python
        source = inspect.getsource(_vec_search_python)
        self.assertIn("heapq", source)


class TestActivationSurfaceSingleLoad(unittest.TestCase):
    """H10: surface() should call _load_all_episodes only once."""
    def test_load_called_once(self):
        import activation_surface
        with patch.object(activation_surface, "_load_all_episodes", return_value=[]) as mock_load:
            activation_surface.surface(os.path.expanduser("~"))
            self.assertEqual(mock_load.call_count, 1)


class TestSTMFIFOTrimEveryWrite(unittest.TestCase):
    """H11: FIFO trim should run on every write."""
    def test_write_enforces_fifo_limit(self):
        from short_term_store import write_entry, MAX_ENTRIES, _empty_store
        store = _empty_store()
        for i in range(MAX_ENTRIES + 10):
            store = write_entry(store, "entry " + str(i))
        entries = store.get("entries", [])
        self.assertLessEqual(len(entries), MAX_ENTRIES)


class TestSemanticIndexSingleton(unittest.TestCase):
    """H12: SemanticIndex should be cached as singleton."""
    def test_singleton_function_exists(self):
        import memory_mcp_server
        self.assertTrue(hasattr(memory_mcp_server, "_get_semantic_index"))


class TestResolveMemoryDir(unittest.TestCase):
    """H13: Files should use env var or resolve_memory_dir."""
    def test_message_memory_handler_uses_env(self):
        import message_memory_handler
        source = open(message_memory_handler.__file__, "r", encoding="utf-8").read()
        has_env = "CLAUDE_MEMORY_DIR" in source or "os.environ" in source
        has_resolve = "resolve_memory_dir" in source
        self.assertTrue(has_env or has_resolve)

    def test_stats_updater_uses_env(self):
        import stats_updater
        source = open(stats_updater.__file__, "r", encoding="utf-8").read()
        has_env = "CLAUDE_MEMORY_DIR" in source or "os.environ" in source
        has_resolve = "resolve_memory_dir" in source
        self.assertTrue(has_env or has_resolve)


class TestTimestampParsing(unittest.TestCase):
    """H14: hook_stats.py should use fromisoformat not slicing."""
    def test_hourly_stats_no_slicing(self):
        import inspect
        from hook_stats import compute_hourly_stats
        source = inspect.getsource(compute_hourly_stats)
        self.assertNotIn("ts[11:13]", source)

    def test_daily_firing_trend_no_slicing(self):
        import inspect
        from hook_stats import daily_firing_trend
        source = inspect.getsource(daily_firing_trend)
        self.assertNotIn("ts[:10]", source)

    def test_hourly_stats_still_works(self):
        from hook_stats import compute_hourly_stats
        entries = [
            {"ts": "2026-03-20T09:00:00.000Z"},
            {"ts": "2026-03-20T14:30:00.000Z"},
            {"ts": "2026-03-20T09:15:00.000Z"},
        ]
        result = compute_hourly_stats(entries)
        self.assertEqual(result[9], 2)
        self.assertEqual(result[14], 1)

    def test_daily_firing_trend_still_works(self):
        from hook_stats import daily_firing_trend
        entries = [
            {"ts": "2026-03-20T09:00:00.000Z"},
            {"ts": "2026-03-20T14:30:00.000Z"},
            {"ts": "2026-03-21T09:15:00.000Z"},
        ]
        result = daily_firing_trend(entries)
        self.assertEqual(result["2026-03-20"], 2)
        self.assertEqual(result["2026-03-21"], 1)


class TestDeadCodeRemoval(unittest.TestCase):
    """H17: No no-op total_surfaced += 0."""
    def test_no_noop_addition(self):
        import spontaneous_surfacing
        source = open(spontaneous_surfacing.__file__, "r", encoding="utf-8").read()
        self.assertNotIn("total_surfaced += 0", source)




class TestSharedUtils(unittest.TestCase):
    """H15: shared_utils.py should provide common utility functions."""
    def test_shared_utils_importable(self):
        from shared_utils import _now_iso, _parse_iso, _atomic_write_json, _load_json
        self.assertTrue(callable(_now_iso))
        self.assertTrue(callable(_parse_iso))
        self.assertTrue(callable(_atomic_write_json))
        self.assertTrue(callable(_load_json))

    def test_now_iso_returns_iso_string(self):
        from shared_utils import _now_iso
        result = _now_iso()
        self.assertIn("T", result)
        self.assertIn("+", result)

    def test_parse_iso_roundtrip(self):
        from shared_utils import _now_iso, _parse_iso
        ts = _now_iso()
        parsed = _parse_iso(ts)
        self.assertIsNotNone(parsed)

    def test_parse_iso_handles_none(self):
        from shared_utils import _parse_iso
        self.assertIsNone(_parse_iso(""))
        self.assertIsNone(_parse_iso(None))

    def test_atomic_write_and_load(self):
        import tempfile, shutil
        from pathlib import Path
        from shared_utils import _atomic_write_json, _load_json
        d = tempfile.mkdtemp()
        try:
            fp = Path(d) / "test.json"
            data = {"key": "value", "num": 42}
            _atomic_write_json(fp, data)
            loaded = _load_json(fp)
            self.assertEqual(loaded, data)
        finally:
            shutil.rmtree(d)

    def test_load_json_returns_none_on_missing(self):
        from pathlib import Path
        from shared_utils import _load_json
        self.assertIsNone(_load_json(Path("/nonexistent/file.json")))

if __name__ == "__main__":
    unittest.main()
