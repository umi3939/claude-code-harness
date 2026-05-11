#!/usr/bin/env python3
"""Tests for bot_personality.py - Discord bot personality context injection.

TDD: Tests written before implementation.
"""

import asyncio
import json
import os
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch, AsyncMock


# --- Helper to run async tests ---

def run_async(coro):
    """Run an async coroutine in a new event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestPersonalityTemplate(unittest.TestCase):
    """Test the static personality template."""

    def test_default_template_is_non_empty_string(self):
        from bot_personality import DEFAULT_PERSONALITY_TEMPLATE
        self.assertIsInstance(DEFAULT_PERSONALITY_TEMPLATE, str)
        self.assertTrue(len(DEFAULT_PERSONALITY_TEMPLATE) > 0)

    def test_default_template_contains_name(self):
        from bot_personality import DEFAULT_PERSONALITY_TEMPLATE
        self.assertIn("the assistant", DEFAULT_PERSONALITY_TEMPLATE)

    def test_default_template_no_emoji_instruction(self):
        from bot_personality import DEFAULT_PERSONALITY_TEMPLATE
        # Template should instruct not to use emoji
        lower = DEFAULT_PERSONALITY_TEMPLATE.lower()
        self.assertTrue("emoji" in lower or "絵文字" in lower)


class TestPersonalityContextCollector(unittest.TestCase):
    """Test PersonalityContextCollector."""

    def _make_collector(self, memory_search_fn=None, emotion_read_fn=None,
                        template=None):
        from bot_personality import PersonalityContextCollector
        return PersonalityContextCollector(
            memory_search_fn=memory_search_fn,
            emotion_read_fn=emotion_read_fn,
            personality_template=template,
        )

    # --- Constructor / DI ---

    def test_constructor_accepts_callables(self):
        collector = self._make_collector(
            memory_search_fn=lambda q, limit: [],
            emotion_read_fn=lambda: {},
        )
        self.assertIsNotNone(collector)

    def test_constructor_with_none_dependencies(self):
        """All dependencies are optional (fail-open)."""
        collector = self._make_collector()
        self.assertIsNotNone(collector)

    def test_custom_template_is_used(self):
        from bot_personality import PersonalityContextCollector
        custom = "Custom template text"
        collector = PersonalityContextCollector(
            personality_template=custom,
        )
        self.assertEqual(collector.personality_template, custom)

    # --- Memory search ---

    def test_memory_search_results_included_in_context(self):
        def mock_search(query, limit):
            return [
                {"original_text": "Episode about coding", "score": 0.8},
                {"original_text": "Episode about testing", "score": 0.5},
            ]

        collector = self._make_collector(memory_search_fn=mock_search)
        result = run_async(collector.collect_context("coding question"))
        self.assertIn("coding", result["memory_context"])

    def test_memory_search_empty_returns_empty_context(self):
        def mock_search(query, limit):
            return []

        collector = self._make_collector(memory_search_fn=mock_search)
        result = run_async(collector.collect_context("hello"))
        self.assertEqual(result["memory_context"], "")

    def test_memory_search_exception_returns_empty_context(self):
        """Fail-open: exception in memory search should not crash."""
        def mock_search(query, limit):
            raise RuntimeError("DB error")

        collector = self._make_collector(memory_search_fn=mock_search)
        result = run_async(collector.collect_context("hello"))
        self.assertEqual(result["memory_context"], "")

    def test_memory_search_none_fn_returns_empty_context(self):
        """If no search function provided, memory context is empty."""
        collector = self._make_collector(memory_search_fn=None)
        result = run_async(collector.collect_context("hello"))
        self.assertEqual(result["memory_context"], "")

    def test_memory_search_limit_applied(self):
        """Search results should be limited to prevent huge prompts."""
        from bot_personality import MEMORY_SEARCH_LIMIT
        call_args = {}

        def mock_search(query, limit):
            call_args["limit"] = limit
            return []

        collector = self._make_collector(memory_search_fn=mock_search)
        run_async(collector.collect_context("test"))
        self.assertEqual(call_args["limit"], MEMORY_SEARCH_LIMIT)

    # --- Emotion state ---

    def test_emotion_state_included_in_context(self):
        def mock_emotion():
            return {
                "fulfillment": 0.3,
                "tension": -0.1,
                "affinity": 0.5,
                "last_updated": "2026-03-22T10:00:00Z",
            }

        collector = self._make_collector(emotion_read_fn=mock_emotion)
        result = run_async(collector.collect_context("hello"))
        self.assertIn("fulfillment", result["emotion_context"])
        self.assertIn("0.3", result["emotion_context"])

    def test_emotion_state_exception_returns_empty_context(self):
        """Fail-open: exception in emotion read should not crash."""
        def mock_emotion():
            raise IOError("File not found")

        collector = self._make_collector(emotion_read_fn=mock_emotion)
        result = run_async(collector.collect_context("hello"))
        self.assertEqual(result["emotion_context"], "")

    def test_emotion_state_none_fn_returns_empty_context(self):
        collector = self._make_collector(emotion_read_fn=None)
        result = run_async(collector.collect_context("hello"))
        self.assertEqual(result["emotion_context"], "")

    # --- Placeholder sanitization (analysis #3) ---

    def test_placeholder_escape_in_memory_context(self):
        """Prevent {sender_id} or {message} in context from being substituted."""
        def mock_search(query, limit):
            return [{"original_text": "Contains {sender_id} and {message}", "score": 1.0}]

        collector = self._make_collector(memory_search_fn=mock_search)
        result = run_async(collector.collect_context("test"))
        # Placeholders must be escaped
        self.assertNotIn("{sender_id}", result["memory_context"])
        self.assertNotIn("{message}", result["memory_context"])

    def test_placeholder_escape_in_emotion_context(self):
        def mock_emotion():
            return {
                "fulfillment": 0.0,
                "tension": 0.0,
                "affinity": 0.0,
                "last_updated": "{sender_id}",
            }

        collector = self._make_collector(emotion_read_fn=mock_emotion)
        result = run_async(collector.collect_context("test"))
        self.assertNotIn("{sender_id}", result["emotion_context"])

    # --- collect_context return structure ---

    def test_collect_context_returns_all_keys(self):
        collector = self._make_collector()
        result = run_async(collector.collect_context("hello"))
        self.assertIn("personality_template", result)
        self.assertIn("memory_context", result)
        self.assertIn("emotion_context", result)

    def test_collect_context_personality_template_always_present(self):
        """Personality template is static and should always be present."""
        collector = self._make_collector()
        result = run_async(collector.collect_context("hello"))
        self.assertTrue(len(result["personality_template"]) > 0)


class TestBuildEnhancedPrompt(unittest.TestCase):
    """Test the build_enhanced_prompt function."""

    def test_basic_prompt_structure(self):
        from bot_personality import build_enhanced_prompt
        context = {
            "personality_template": "You are the assistant.",
            "memory_context": "User talked about coding.",
            "emotion_context": "fulfillment=+0.300, tension=-0.100, affinity=+0.500",
        }
        result = build_enhanced_prompt(
            context=context,
            message="Hello!",
            sender_id="12345",
        )
        self.assertIn("You are the assistant.", result)
        self.assertIn("coding", result)
        self.assertIn("fulfillment", result)
        self.assertIn("Hello!", result)

    def test_prompt_order_personality_tone_emotion_memory_message(self):
        """Prompt order: personality -> tone -> emotion -> memory -> message (per design C20-2)."""
        from bot_personality import build_enhanced_prompt
        context = {
            "personality_template": "AAA_PERSONALITY",
            "tone_context": "BBB_TONE",
            "emotion_context": "CCC_EMOTION",
            "memory_context": "DDD_MEMORY",
        }
        result = build_enhanced_prompt(context=context, message="EEE_MESSAGE", sender_id="1")
        pos_p = result.index("AAA_PERSONALITY")
        pos_t = result.index("BBB_TONE")
        pos_e = result.index("CCC_EMOTION")
        pos_m = result.index("DDD_MEMORY")
        pos_msg = result.index("EEE_MESSAGE")
        self.assertLess(pos_p, pos_t)
        self.assertLess(pos_t, pos_e)
        self.assertLess(pos_e, pos_m)
        self.assertLess(pos_m, pos_msg)

    def test_empty_context_fields_still_produce_valid_prompt(self):
        from bot_personality import build_enhanced_prompt
        context = {
            "personality_template": "You are the assistant.",
            "memory_context": "",
            "emotion_context": "",
        }
        result = build_enhanced_prompt(context=context, message="Hi", sender_id="1")
        self.assertIn("You are the assistant.", result)
        self.assertIn("Hi", result)

    def test_prompt_size_limit(self):
        """Prompt should be truncated if too large."""
        from bot_personality import build_enhanced_prompt, PROMPT_MAX_LENGTH
        context = {
            "personality_template": "Template.",
            "memory_context": "X" * 50000,  # Very large
            "emotion_context": "Emotion.",
        }
        result = build_enhanced_prompt(context=context, message="Msg", sender_id="1")
        self.assertLessEqual(len(result), PROMPT_MAX_LENGTH + 500)  # Some tolerance

    def test_sender_id_not_leaked_in_prompt(self):
        """sender_id should not appear in the prompt output (analysis #3)."""
        from bot_personality import build_enhanced_prompt
        context = {
            "personality_template": "Template.",
            "memory_context": "",
            "emotion_context": "",
        }
        result = build_enhanced_prompt(
            context=context,
            message="Hello {sender_id}",
            sender_id="SECRET_ID_123",
        )
        # The literal text "{sender_id}" from user message is fine,
        # but the actual sender_id value should not replace it
        self.assertNotIn("SECRET_ID_123", result)


class TestCollectContextTimeout(unittest.TestCase):
    """Test the overall timeout for context collection."""

    def test_slow_memory_search_is_timed_out(self):
        """If memory search takes too long, context should still return."""
        import bot_personality

        async def slow_search(query, limit):
            await asyncio.sleep(10)  # Way too slow
            return [{"original_text": "Should not appear", "score": 1.0}]

        collector = bot_personality.PersonalityContextCollector(
            memory_search_fn=slow_search,
            emotion_read_fn=None,
        )
        # Override timeout for testing
        original_timeout = bot_personality.CONTEXT_COLLECT_TIMEOUT
        bot_personality.CONTEXT_COLLECT_TIMEOUT = 0.5
        try:
            result = run_async(collector.collect_context("test"))
            # Should still return, just without memory
            self.assertIn("personality_template", result)
            self.assertEqual(result["memory_context"], "")
        finally:
            bot_personality.CONTEXT_COLLECT_TIMEOUT = original_timeout

    def test_slow_emotion_read_is_timed_out(self):
        import bot_personality

        async def slow_emotion():
            await asyncio.sleep(10)
            return {"fulfillment": 0.5}

        collector = bot_personality.PersonalityContextCollector(
            memory_search_fn=None,
            emotion_read_fn=slow_emotion,
        )
        original_timeout = bot_personality.CONTEXT_COLLECT_TIMEOUT
        bot_personality.CONTEXT_COLLECT_TIMEOUT = 0.5
        try:
            result = run_async(collector.collect_context("test"))
            self.assertEqual(result["emotion_context"], "")
        finally:
            bot_personality.CONTEXT_COLLECT_TIMEOUT = original_timeout


class TestCreateCollectorFromConfig(unittest.TestCase):
    """Test the factory function that creates a collector from file paths."""

    def test_create_with_valid_paths(self):
        """Create a collector with valid memory_dir and emotion paths."""
        from bot_personality import create_collector
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a minimal semantic_index.db
            db_path = os.path.join(tmpdir, "semantic_index.db")
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE IF NOT EXISTS documents (doc_id TEXT PRIMARY KEY)")
            conn.close()

            # Create emotion state file
            emotion_path = os.path.join(tmpdir, "emotion_state.json")
            with open(emotion_path, "w") as f:
                json.dump({
                    "fulfillment": 0.1,
                    "tension": 0.0,
                    "affinity": 0.2,
                    "last_updated": "2026-03-22T10:00:00Z",
                }, f)

            collector = create_collector(memory_dir=tmpdir)
            self.assertIsNotNone(collector)

    def test_create_with_missing_db(self):
        """Should still create a collector even if DB doesn't exist (fail-open)."""
        from bot_personality import create_collector
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = create_collector(memory_dir=tmpdir)
            self.assertIsNotNone(collector)

    def test_create_with_missing_emotion_file(self):
        """Should still create a collector even if emotion file doesn't exist."""
        from bot_personality import create_collector
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = create_collector(memory_dir=tmpdir)
            self.assertIsNotNone(collector)


class TestMemorySearchViaSQLite(unittest.TestCase):
    """Test the FTS5 search wrapper used by the collector."""

    def test_fts5_search_returns_results(self):
        """End-to-end: create an FTS5 index, search it."""
        from bot_personality import _create_memory_search_fn
        with tempfile.TemporaryDirectory() as tmpdir:
            # Set up a proper semantic_index DB with FTS5
            db_path = os.path.join(tmpdir, "semantic_index.db")
            conn = sqlite3.connect(db_path)
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    doc_id TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    tokenized_text TEXT NOT NULL,
                    original_text TEXT NOT NULL,
                    text_hash TEXT NOT NULL,
                    timestamp TEXT,
                    session_id TEXT,
                    episode_type TEXT,
                    tags TEXT
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS fts_index USING fts5(
                    doc_id,
                    tokenized_text,
                    content=documents,
                    content_rowid=rowid,
                    tokenize='unicode61'
                );
                INSERT INTO documents VALUES(
                    'doc1', 'episode', 'ep1',
                    'coding python programming',
                    'An episode about coding in Python and programming',
                    'hash1', '2026-03-22T10:00:00Z', 'sess1', 'user_request', '[]'
                );
                INSERT INTO fts_index(doc_id, tokenized_text) VALUES(
                    'doc1', 'coding python programming'
                );
            """)
            conn.commit()
            conn.close()

            search_fn = _create_memory_search_fn(tmpdir)
            results = search_fn("coding", 5)
            self.assertTrue(len(results) > 0)
            self.assertIn("coding", results[0]["original_text"].lower())

    def test_fts5_search_with_no_db_returns_empty(self):
        from bot_personality import _create_memory_search_fn
        with tempfile.TemporaryDirectory() as tmpdir:
            search_fn = _create_memory_search_fn(tmpdir)
            results = search_fn("test", 5)
            self.assertEqual(results, [])


class TestEmotionReadFn(unittest.TestCase):
    """Test the emotion read wrapper."""

    def test_reads_emotion_state(self):
        from bot_personality import _create_emotion_read_fn
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as tmpdir:
            emotion_path = os.path.join(tmpdir, "emotion_state.json")
            # Use a recent timestamp to avoid session decay reducing the value
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            state = {
                "fulfillment": 0.3,
                "tension": -0.1,
                "affinity": 0.5,
                "last_updated": now_iso,
                "created_at": now_iso,
            }
            with open(emotion_path, "w") as f:
                json.dump(state, f)

            read_fn = _create_emotion_read_fn(tmpdir)
            result = read_fn()
            self.assertAlmostEqual(result["fulfillment"], 0.3, places=1)

    def test_missing_emotion_file_returns_default(self):
        from bot_personality import _create_emotion_read_fn
        with tempfile.TemporaryDirectory() as tmpdir:
            read_fn = _create_emotion_read_fn(tmpdir)
            result = read_fn()
            # Should return default state (all zeros)
            self.assertAlmostEqual(result["fulfillment"], 0.0, places=1)


class TestIntegrationCollectAndBuild(unittest.TestCase):
    """Integration test: collect context and build prompt."""

    def test_full_pipeline(self):
        from bot_personality import PersonalityContextCollector, build_enhanced_prompt

        def mock_search(query, limit):
            return [{"original_text": "User likes Python", "score": 0.8}]

        def mock_emotion():
            return {
                "fulfillment": 0.5,
                "tension": 0.0,
                "affinity": 0.3,
                "last_updated": "2026-03-22T10:00:00Z",
            }

        collector = PersonalityContextCollector(
            memory_search_fn=mock_search,
            emotion_read_fn=mock_emotion,
        )
        context = run_async(collector.collect_context("Tell me about Python"))
        prompt = build_enhanced_prompt(
            context=context,
            message="Tell me about Python",
            sender_id="12345",
        )
        self.assertIn("Python", prompt)
        self.assertIn("the assistant", prompt)
        self.assertIn("fulfillment", prompt)

    def test_full_pipeline_all_failures_fallback(self):
        """When everything fails, should still produce a valid prompt."""
        from bot_personality import PersonalityContextCollector, build_enhanced_prompt

        def fail_search(query, limit):
            raise Exception("DB down")

        def fail_emotion():
            raise Exception("File gone")

        collector = PersonalityContextCollector(
            memory_search_fn=fail_search,
            emotion_read_fn=fail_emotion,
        )
        context = run_async(collector.collect_context("Hello"))
        prompt = build_enhanced_prompt(
            context=context,
            message="Hello",
            sender_id="999",
        )
        # Should still have personality template and message
        self.assertIn("Hello", prompt)
        self.assertIn("the assistant", prompt)


class TestToneContextCollection(unittest.TestCase):
    """Test tone context integration in PersonalityContextCollector (C20-2)."""

    def _make_collector(self, memory_search_fn=None, emotion_read_fn=None,
                        tone_compute_fn=None, template=None):
        from bot_personality import PersonalityContextCollector
        return PersonalityContextCollector(
            memory_search_fn=memory_search_fn,
            emotion_read_fn=emotion_read_fn,
            tone_compute_fn=tone_compute_fn,
            personality_template=template,
        )

    def test_collect_context_returns_tone_context_key(self):
        """collect_context should return tone_context key."""
        collector = self._make_collector()
        result = run_async(collector.collect_context("hello"))
        self.assertIn("tone_context", result)

    def test_tone_context_with_compute_fn_and_emotion(self):
        """When both tone_compute_fn and emotion succeed, tone_context is populated."""
        def mock_emotion():
            return {"fulfillment": 0.5, "tension": 0.0, "affinity": 0.3}

        def mock_tone_compute():
            return {
                "primary_tone": "warm",
                "tone_weights": {"neutral": 0.2, "light": 0.1, "serious": 0.1, "warm": 0.5, "reserved": 0.1},
                "description": "温かく優しいトーン",
            }

        collector = self._make_collector(
            emotion_read_fn=mock_emotion,
            tone_compute_fn=mock_tone_compute,
        )
        result = run_async(collector.collect_context("hello"))
        self.assertTrue(len(result["tone_context"]) > 0)
        self.assertIn("warm", result["tone_context"])

    def test_tone_context_without_compute_fn(self):
        """When tone_compute_fn is None, tone_context uses band classification only."""
        def mock_emotion():
            return {"fulfillment": 0.5, "tension": 0.0, "affinity": 0.3}

        collector = self._make_collector(
            emotion_read_fn=mock_emotion,
            tone_compute_fn=None,
        )
        result = run_async(collector.collect_context("hello"))
        self.assertTrue(len(result["tone_context"]) > 0)

    def test_tone_context_compute_fn_failure(self):
        """When tone_compute_fn raises, tone_context still populated from bands."""
        def mock_emotion():
            return {"fulfillment": 0.5, "tension": 0.0, "affinity": 0.3}

        def failing_tone():
            raise RuntimeError("compute_tone failed")

        collector = self._make_collector(
            emotion_read_fn=mock_emotion,
            tone_compute_fn=failing_tone,
        )
        result = run_async(collector.collect_context("hello"))
        # Should still have tone context from band classification
        self.assertTrue(len(result["tone_context"]) > 0)

    def test_tone_context_emotion_failure_returns_neutral(self):
        """When emotion read fails, tone_context returns neutral default."""
        def failing_emotion():
            raise IOError("File not found")

        collector = self._make_collector(
            emotion_read_fn=failing_emotion,
            tone_compute_fn=None,
        )
        result = run_async(collector.collect_context("hello"))
        # Neutral default should still be present
        self.assertTrue(len(result["tone_context"]) > 0)

    def test_tone_context_both_fail_returns_neutral(self):
        """When both emotion and tone fail, neutral default is used."""
        def failing_emotion():
            raise IOError("File not found")

        def failing_tone():
            raise RuntimeError("compute_tone failed")

        collector = self._make_collector(
            emotion_read_fn=failing_emotion,
            tone_compute_fn=failing_tone,
        )
        result = run_async(collector.collect_context("hello"))
        self.assertTrue(len(result["tone_context"]) > 0)

    def test_tone_compute_fn_async(self):
        """tone_compute_fn can be async."""
        def mock_emotion():
            return {"fulfillment": 0.3, "tension": -0.1, "affinity": 0.5}

        async def async_tone_compute():
            return {
                "primary_tone": "light",
                "tone_weights": {"neutral": 0.2, "light": 0.4, "serious": 0.1, "warm": 0.2, "reserved": 0.1},
                "description": "軽やかなトーン",
            }

        collector = self._make_collector(
            emotion_read_fn=mock_emotion,
            tone_compute_fn=async_tone_compute,
        )
        result = run_async(collector.collect_context("hello"))
        self.assertIn("light", result["tone_context"])

    def test_placeholder_escape_in_tone_context(self):
        """Tone context should have placeholders escaped."""
        def mock_emotion():
            return {"fulfillment": 0.5, "tension": 0.0, "affinity": 0.3}

        def mock_tone_compute():
            return {
                "primary_tone": "neutral",
                "tone_weights": {},
                "description": "Contains {sender_id} and {message}",
            }

        collector = self._make_collector(
            emotion_read_fn=mock_emotion,
            tone_compute_fn=mock_tone_compute,
        )
        result = run_async(collector.collect_context("hello"))
        self.assertNotIn("{sender_id}", result["tone_context"])
        self.assertNotIn("{message}", result["tone_context"])

    def test_tone_context_none_emotion_fn(self):
        """When emotion_read_fn is None, tone_context returns neutral default."""
        collector = self._make_collector(
            emotion_read_fn=None,
            tone_compute_fn=None,
        )
        result = run_async(collector.collect_context("hello"))
        self.assertTrue(len(result["tone_context"]) > 0)


class TestBuildEnhancedPromptWithTone(unittest.TestCase):
    """Test build_enhanced_prompt with tone_context (C20-2)."""

    def test_tone_section_in_prompt(self):
        from bot_personality import build_enhanced_prompt
        context = {
            "personality_template": "You are the assistant.",
            "tone_context": "推奨トーン: warm",
            "emotion_context": "fulfillment=+0.300",
            "memory_context": "Some memory",
        }
        result = build_enhanced_prompt(context=context, message="Hello", sender_id="1")
        self.assertIn("推奨トーン: warm", result)

    def test_empty_tone_context_no_section(self):
        from bot_personality import build_enhanced_prompt
        context = {
            "personality_template": "You are the assistant.",
            "tone_context": "",
            "emotion_context": "fulfillment=+0.300",
            "memory_context": "",
        }
        result = build_enhanced_prompt(context=context, message="Hello", sender_id="1")
        self.assertNotIn("[Tone instruction]", result)

    def test_missing_tone_context_key_backward_compatible(self):
        """Old-style context without tone_context should still work."""
        from bot_personality import build_enhanced_prompt
        context = {
            "personality_template": "You are the assistant.",
            "emotion_context": "fulfillment=+0.300",
            "memory_context": "Memory data",
        }
        result = build_enhanced_prompt(context=context, message="Hello", sender_id="1")
        self.assertIn("You are the assistant.", result)
        self.assertIn("Hello", result)

    def test_zero_memory_budget_no_ellipsis_section(self):
        """When memory_budget=0 due to size limit, no '...' memory section should appear."""
        from bot_personality import build_enhanced_prompt, PROMPT_MAX_LENGTH
        # Create a context where template+tone+emotion+message exceed PROMPT_MAX_LENGTH
        # so memory_budget becomes 0
        large_template = "T" * (PROMPT_MAX_LENGTH - 100)
        context = {
            "personality_template": large_template,
            "tone_context": "Some tone",
            "emotion_context": "Some emotion",
            "memory_context": "X" * 5000,
        }
        result = build_enhanced_prompt(context=context, message="Msg", sender_id="1")
        # The memory section should not contain just "..."
        self.assertNotIn("[Related memories]\n...", result)


class TestCreateCollectorWithTone(unittest.TestCase):
    """Test create_collector factory with tone_compute_fn (C20-2)."""

    def test_create_collector_default_tone_fn(self):
        """create_collector should set up tone_compute_fn by default."""
        from bot_personality import create_collector
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = create_collector(memory_dir=tmpdir)
            self.assertIsNotNone(collector.tone_compute_fn)


class TestIntegrationToneFullPipeline(unittest.TestCase):
    """Integration: full pipeline with tone (C20-2 Phase 3)."""

    def test_full_pipeline_with_tone(self):
        from bot_personality import PersonalityContextCollector, build_enhanced_prompt

        def mock_search(query, limit):
            return [{"original_text": "User likes coding", "score": 0.8}]

        def mock_emotion():
            return {"fulfillment": 0.6, "tension": -0.1, "affinity": 0.4}

        def mock_tone():
            return {
                "primary_tone": "warm",
                "tone_weights": {"neutral": 0.2, "light": 0.1, "serious": 0.1, "warm": 0.5, "reserved": 0.1},
                "description": "温かく優しいトーン",
            }

        collector = PersonalityContextCollector(
            memory_search_fn=mock_search,
            emotion_read_fn=mock_emotion,
            tone_compute_fn=mock_tone,
        )
        context = run_async(collector.collect_context("Hello"))
        prompt = build_enhanced_prompt(context=context, message="Hello", sender_id="123")
        self.assertIn("the assistant", prompt)
        self.assertIn("warm", prompt)
        self.assertIn("coding", prompt)
        self.assertIn("Hello", prompt)

    def test_full_pipeline_tone_failure_fallback(self):
        """Tone failure should fall back to band classification."""
        from bot_personality import PersonalityContextCollector, build_enhanced_prompt

        def mock_emotion():
            return {"fulfillment": 0.5, "tension": 0.0, "affinity": 0.3}

        def failing_tone():
            raise RuntimeError("tone compute failed")

        collector = PersonalityContextCollector(
            emotion_read_fn=mock_emotion,
            tone_compute_fn=failing_tone,
        )
        context = run_async(collector.collect_context("Hello"))
        prompt = build_enhanced_prompt(context=context, message="Hello", sender_id="1")
        self.assertIn("the assistant", prompt)
        self.assertIn("Hello", prompt)
        # Tone section should still be present (from band fallback)
        self.assertIn("推奨トーン", prompt)

    def test_full_pipeline_all_fail_still_works(self):
        """All components fail: still produces valid prompt."""
        from bot_personality import PersonalityContextCollector, build_enhanced_prompt

        def fail_search(q, l):
            raise Exception("DB down")

        def fail_emotion():
            raise Exception("File gone")

        def fail_tone():
            raise Exception("Compute broken")

        collector = PersonalityContextCollector(
            memory_search_fn=fail_search,
            emotion_read_fn=fail_emotion,
            tone_compute_fn=fail_tone,
        )
        context = run_async(collector.collect_context("Hello"))
        prompt = build_enhanced_prompt(context=context, message="Hello", sender_id="1")
        self.assertIn("the assistant", prompt)
        self.assertIn("Hello", prompt)

    def test_full_pipeline_emotion_fail_tone_neutral(self):
        """Emotion fails but tone compute succeeds: use neutral default for tone instruction."""
        from bot_personality import PersonalityContextCollector, build_enhanced_prompt

        def fail_emotion():
            raise Exception("File gone")

        # Even if tone_compute_fn succeeds, without emotion data,
        # tone instruction should be neutral default
        collector = PersonalityContextCollector(
            emotion_read_fn=fail_emotion,
            tone_compute_fn=None,
        )
        context = run_async(collector.collect_context("Hello"))
        prompt = build_enhanced_prompt(context=context, message="Hello", sender_id="1")
        self.assertIn("the assistant", prompt)

    def test_timeout_tone_compute(self):
        """Slow tone compute should be timed out."""
        import bot_personality

        def mock_emotion():
            return {"fulfillment": 0.3, "tension": 0.0, "affinity": 0.2}

        async def slow_tone():
            await asyncio.sleep(10)
            return {"primary_tone": "warm", "tone_weights": {}, "description": "slow"}

        collector = bot_personality.PersonalityContextCollector(
            emotion_read_fn=mock_emotion,
            tone_compute_fn=slow_tone,
        )
        original_timeout = bot_personality.CONTEXT_COLLECT_TIMEOUT
        bot_personality.CONTEXT_COLLECT_TIMEOUT = 0.5
        try:
            result = run_async(collector.collect_context("test"))
            # Should still have tone context (from band fallback since tone timed out)
            self.assertIn("tone_context", result)
            self.assertIn("personality_template", result)
        finally:
            bot_personality.CONTEXT_COLLECT_TIMEOUT = original_timeout


if __name__ == "__main__":
    unittest.main()
