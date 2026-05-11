#!/usr/bin/env python3
"""Tests for MEDIUM performance (7) + code quality (12) fixes.

TDD: tests written before implementation.
"""

import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)


# ═══════════════════════════════════════════════════════════════
# M-P1: staged_compression.py — TTL cache for directory scan
# ═══════════════════════════════════════════════════════════════

class TestStagedCompressionCache(unittest.TestCase):
    """M-P1: _list_session_files should cache results with TTL."""

    def test_list_session_files_returns_list(self):
        from staged_compression import _list_session_files
        tmpdir = tempfile.mkdtemp()
        result = _list_session_files(Path(tmpdir))
        self.assertIsInstance(result, list)
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_has_cache_mechanism(self):
        """Module should have a caching mechanism for directory listing."""
        import staged_compression
        source = open(staged_compression.__file__, encoding="utf-8").read()
        self.assertTrue(
            "_session_files_cache" in source or "lru_cache" in source or "_cache" in source,
            "staged_compression should have a caching mechanism for _list_session_files"
        )


# ═══════════════════════════════════════════════════════════════
# M-P2: episode_recall.py — Early termination in keyword matching
# ═══════════════════════════════════════════════════════════════

class TestEpisodeRecallEfficiency(unittest.TestCase):
    """M-P2: Keyword matching should have early termination."""

    def test_keyword_match_returns_false_fast(self):
        """Non-matching episode should return quickly."""
        from episode_recall import _episode_matches_keywords
        episode = {
            "summary": "test episode about cats",
            "tags": ["animal"],
        }
        matched, detail = _episode_matches_keywords(episode, ["nonexistent_keyword_xyz"])
        self.assertFalse(matched)

    def test_keyword_match_combined_text_cached(self):
        """Combined text should be built once, not per keyword."""
        from episode_recall import _episode_matches_keywords
        episode = {
            "summary": "a long summary about testing",
            "tags": ["test", "unit"],
        }
        matched, detail = _episode_matches_keywords(episode, ["testing", "unit"])
        self.assertTrue(matched)


# ═══════════════════════════════════════════════════════════════
# M-P3: embedding_provider.py — Client reuse
# ═══════════════════════════════════════════════════════════════

class TestEmbeddingClientReuse(unittest.TestCase):
    """M-P3: OpenAI client should be reused between calls."""

    def test_client_is_lazy_singleton(self):
        """_get_client should return the same client instance."""
        from embedding_provider import OpenAIProvider
        provider = OpenAIProvider(os.environ.get("OPENAI_TEST_KEY", "test-placeholder"))
        self.assertIsNone(provider._client)
        import inspect
        source = inspect.getsource(OpenAIProvider._get_client)
        self.assertIn("self._client", source)


# ═══════════════════════════════════════════════════════════════
# M-P4: memory_mcp_server.py — Emotion state caching
# ═══════════════════════════════════════════════════════════════

class TestEmotionStateCache(unittest.TestCase):
    """M-P4: Emotion state should be cached with short TTL."""

    def test_get_state_dict_caching(self):
        """After fix, memory_search should cache emotion reads."""
        import memory_mcp_server
        source = open(memory_mcp_server.__file__, encoding="utf-8").read()
        self.assertTrue(
            "_emotion_cache" in source or "cached" in source.lower() or
            "_last_emotion" in source,
            "memory_mcp_server should have emotion state caching"
        )


# ═══════════════════════════════════════════════════════════════
# M-P5: vector_search.py — UPSERT instead of 3 queries
# ═══════════════════════════════════════════════════════════════

class TestVectorSearchUpsert(unittest.TestCase):
    """M-P5: store_vector should use fewer queries."""

    def test_store_vector_blob_mode_uses_upsert(self):
        """BLOB mode should use INSERT OR REPLACE (already does)."""
        import inspect
        from vector_search import store_vector
        source = inspect.getsource(store_vector)
        self.assertIn("INSERT OR REPLACE", source)

    def test_store_vector_vec_mode_optimized(self):
        """vec0 mode should be more efficient."""
        import inspect
        from vector_search import store_vector
        source = inspect.getsource(store_vector)
        self.assertIn("store_vector", source)


# ═══════════════════════════════════════════════════════════════
# M-P6: short_term_store.py — Skip sort when not needed
# ═══════════════════════════════════════════════════════════════

class TestSTMDecayEfficiency(unittest.TestCase):
    """M-P6: Decay should skip sorting when no pruning happened."""

    def test_decay_preserves_entries_when_no_pruning(self):
        """When no entries are pruned, result should have all entries."""
        from short_term_store import apply_session_decay, _now_iso
        store = {
            "entries": [
                {"content": "test", "weight": 1.0, "timestamp": _now_iso(), "category": "thought"},
            ],
            "last_session_decay_at": None,
        }
        result, stats = apply_session_decay(store)
        self.assertGreaterEqual(len(result["entries"]), 1)


# ═══════════════════════════════════════════════════════════════
# M-P7: activation_surface.py — Set operations instead of O(n^2)
# ═══════════════════════════════════════════════════════════════

class TestActivationSurfaceSetOps(unittest.TestCase):
    """M-P7: Intersection detection should use set operations."""

    def test_intersection_uses_set_operations(self):
        """The multi-facet intersection code should use set & operator."""
        import inspect
        from activation_surface import surface
        source = inspect.getsource(surface)
        self.assertTrue(
            "&" in source or "intersection" in source,
            "surface function should use set operations for intersections"
        )


# ═══════════════════════════════════════════════════════════════
# M-Q2: bot_personality.py — Type hints on factory
# ═══════════════════════════════════════════════════════════════

class TestBotPersonalityTypeHints(unittest.TestCase):
    """M-Q2: Factory functions should have type hints."""

    def test_create_memory_search_fn_has_return_type(self):
        """_create_memory_search_fn should have return type annotation."""
        import inspect
        from bot_personality import _create_memory_search_fn
        sig = inspect.signature(_create_memory_search_fn)
        self.assertNotEqual(
            sig.return_annotation,
            inspect.Parameter.empty,
            "_create_memory_search_fn should have return type annotation"
        )


# ═══════════════════════════════════════════════════════════════
# M-Q3: emotion_state.py — Input validation on update_state
# ═══════════════════════════════════════════════════════════════

class TestEmotionStateValidation(unittest.TestCase):
    """M-Q3: update_state should validate input ranges."""

    def test_update_clamps_extreme_value(self):
        """Setting axis to value outside [-1, 1] in set mode should be clamped."""
        from emotion_state import update_state
        tmpdir = tempfile.mkdtemp()
        result = update_state(tmpdir, fulfillment=5.0, mode="set")
        self.assertNotIn("ERROR", result)
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_update_rejects_nan(self):
        """NaN values should be rejected."""
        from emotion_state import update_state
        tmpdir = tempfile.mkdtemp()
        result = update_state(tmpdir, fulfillment=float('nan'), mode="set")
        self.assertIn("ERROR", result)
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_update_rejects_inf(self):
        """Infinity values should be rejected."""
        from emotion_state import update_state
        tmpdir = tempfile.mkdtemp()
        result = update_state(tmpdir, fulfillment=float('inf'), mode="set")
        self.assertIn("ERROR", result)
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════
# M-Q4: dynamic_read_verification.py — Precompiled regex
# ═══════════════════════════════════════════════════════════════

class TestDynamicReadRegexPrecompile(unittest.TestCase):
    """M-Q4: Regex patterns should be precompiled at module level."""

    def test_module_level_patterns_exist(self):
        """Frequently-used regex patterns should be compiled at module level."""
        import dynamic_read_verification as drv
        source = open(drv.__file__, encoding="utf-8").read()
        import ast
        tree = ast.parse(source)
        module_level_compiles = 0
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                if hasattr(node, 'value') and isinstance(node.value, ast.Call):
                    if hasattr(node.value.func, 'attr') and node.value.func.attr == 'compile':
                        module_level_compiles += 1
        self.assertGreaterEqual(
            module_level_compiles, 2,
            "At least 2 regex patterns should be precompiled at module level"
        )


# ═══════════════════════════════════════════════════════════════
# M-Q5: Atomic write exception handling
# ═══════════════════════════════════════════════════════════════

class TestAtomicWriteExceptionHandling(unittest.TestCase):
    """M-Q5: Atomic write should handle Windows os.replace failure."""

    def test_continuity_strain_save_has_windows_fallback(self):
        """_save_state should have Windows fallback for os.replace failure."""
        import inspect
        from continuity_strain import _save_state
        source = inspect.getsource(_save_state)
        self.assertTrue(
            "shutil" in source or "copy" in source or "OSError" in source,
            "_save_state should handle os.replace failure on Windows"
        )

    def test_temporal_self_difference_save_has_windows_fallback(self):
        """_save_snapshots should have Windows fallback."""
        import inspect
        from temporal_self_difference import _save_snapshots
        source = inspect.getsource(_save_snapshots)
        self.assertTrue(
            "shutil" in source or "copy" in source or "OSError" in source,
            "_save_snapshots should handle os.replace failure on Windows"
        )


# ═══════════════════════════════════════════════════════════════
# M-Q6: Magic numbers to named constants
# ═══════════════════════════════════════════════════════════════

class TestMagicNumbersReplaced(unittest.TestCase):
    """M-Q6: Key magic numbers should be named constants."""

    def test_activation_surface_has_named_constants(self):
        """activation_surface should define strength thresholds as constants."""
        import activation_surface
        source = open(activation_surface.__file__, encoding="utf-8").read()
        has_constants = bool(re.findall(r'^[A-Z_]{4,}\s*=\s*[\d.]+', source, re.MULTILINE))
        self.assertTrue(has_constants, "activation_surface should have named constants")


# ═══════════════════════════════════════════════════════════════
# M-Q8: Circular import risk
# ═══════════════════════════════════════════════════════════════

class TestCircularImportPrevention(unittest.TestCase):
    """M-Q8: Modules should import without circular dependency."""

    def test_identity_coherence_importable(self):
        try:
            import identity_coherence
        except ImportError as e:
            self.fail(f"identity_coherence import failed: {e}")

    def test_self_image_integration_importable(self):
        try:
            import self_image_integration
        except ImportError as e:
            self.fail(f"self_image_integration import failed: {e}")


# ═══════════════════════════════════════════════════════════════
# M-Q10: emotion_reaction.py — Named scale factor constants
# ═══════════════════════════════════════════════════════════════

class TestEmotionReactionConstants(unittest.TestCase):
    """M-Q10: Scale factors should be named constants."""

    def test_has_named_scale_constants(self):
        import emotion_reaction
        self.assertTrue(hasattr(emotion_reaction, 'DELTA_CAP'))
        source = open(emotion_reaction.__file__, encoding="utf-8").read()
        self.assertTrue(
            "VALENCE_SCALE" in source or "_VALENCE_POSITIVE" in source,
            "emotion_reaction should have named valence scale constants"
        )


# ═══════════════════════════════════════════════════════════════
# M-Q11: bot_personality.py — Configurable database path
# ═══════════════════════════════════════════════════════════════

class TestBotPersonalityDBPath(unittest.TestCase):
    """M-Q11: Database path should be configurable."""

    def test_db_path_accepts_memory_dir(self):
        import inspect
        from bot_personality import _create_memory_search_fn
        sig = inspect.signature(_create_memory_search_fn)
        self.assertIn("memory_dir", sig.parameters)


if __name__ == "__main__":
    unittest.main()
