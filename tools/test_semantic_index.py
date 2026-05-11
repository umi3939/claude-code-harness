#!/usr/bin/env python3
"""Tests for semantic_index.py — FTS5 full-text search index for memory system."""

import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from semantic_index import (
    SemanticIndex,
    _bm25_rank_to_score,
    _episode_to_text,
    _lesson_to_text,
    _text_hash,
    get_lessons_mtime,
    tokenize_for_index,
    tokenize_for_query,
    tokenize_japanese,
)


class TestJapaneseTokenizer(unittest.TestCase):
    """Tests for the Japanese tokenizer."""

    def test_kanji_bigram(self):
        """Kanji sequences should be split into overlapping bigrams."""
        tokens = tokenize_japanese("機械学習")
        self.assertEqual(tokens, ["機械", "械学", "学習"])

    def test_single_kanji(self):
        """Single kanji should produce single token."""
        tokens = tokenize_japanese("学")
        self.assertEqual(tokens, ["学"])

    def test_two_kanji(self):
        """Two kanji should produce single bigram."""
        tokens = tokenize_japanese("感情")
        self.assertEqual(tokens, ["感情"])

    def test_katakana_single_token(self):
        """Katakana sequence should be kept as single token."""
        tokens = tokenize_japanese("セマンティック")
        self.assertEqual(tokens, ["セマンティック"])

    def test_hiragana_preserved(self):
        """Hiragana sequences should be kept as single token."""
        tokens = tokenize_japanese("おはよう")
        self.assertEqual(tokens, ["おはよう"])

    def test_ascii_words(self):
        """ASCII text should be split on whitespace and lowercased."""
        tokens = tokenize_japanese("Hello World")
        self.assertEqual(tokens, ["hello", "world"])

    def test_mixed_text(self):
        """Mixed kanji, katakana, ASCII should be split at script boundaries."""
        tokens = tokenize_japanese("自然言語Processing")
        self.assertIn("自然", tokens)
        self.assertIn("然言", tokens)
        self.assertIn("言語", tokens)
        self.assertIn("processing", tokens)

    def test_kanji_katakana_boundary(self):
        """Script boundary between kanji and katakana."""
        tokens = tokenize_japanese("感情モデル")
        self.assertEqual(tokens, ["感情", "モデル"])

    def test_stop_word_removal(self):
        """Stop words should be removed when flag is set."""
        tokens = tokenize_japanese("これは テスト です", remove_stop_words=True)
        # "は" should be removed
        self.assertNotIn("は", tokens)
        self.assertIn("テスト", tokens)

    def test_stop_words_kept_without_flag(self):
        """Stop words should be kept when flag is not set."""
        # "は" in hiragana context merges with surrounding hiragana.
        # Use isolated hiragana stop word between script boundaries.
        tokens = tokenize_japanese("テスト は OK", remove_stop_words=False)
        self.assertIn("は", tokens)

    def test_empty_input(self):
        """Empty input should return empty list."""
        self.assertEqual(tokenize_japanese(""), [])
        self.assertEqual(tokenize_japanese(None), [])

    def test_punctuation_only(self):
        """Punctuation-only input should return empty list."""
        tokens = tokenize_japanese("。、！？")
        self.assertEqual(tokens, [])

    def test_mixed_with_punctuation(self):
        """Punctuation should be stripped, tokens preserved."""
        tokens = tokenize_japanese("感情、モデル。テスト")
        self.assertIn("感情", tokens)
        self.assertIn("モデル", tokens)
        self.assertIn("テスト", tokens)

    def test_numbers_ascii(self):
        """Numbers in ASCII should be tokenized."""
        tokens = tokenize_japanese("version 2.0")
        self.assertIn("version", tokens)
        # "2" and "0" should appear (split by period which is "other")
        self.assertIn("2", tokens)
        self.assertIn("0", tokens)

    def test_full_width_ascii(self):
        """Full-width ASCII should NOT be treated as regular ASCII."""
        # Full-width letters have different Unicode category
        tokens = tokenize_japanese("Ａ Ｂ")
        # These are not ASCII, so behavior depends on unicode category
        # They should not crash
        self.assertIsInstance(tokens, list)


class TestTokenizeForIndex(unittest.TestCase):
    """Tests for tokenize_for_index."""

    def test_basic(self):
        result = tokenize_for_index("機械学習 テスト")
        self.assertIn("機械", result)
        self.assertIn("テスト", result)

    def test_empty(self):
        self.assertEqual(tokenize_for_index(""), "")


class TestTokenizeForQuery(unittest.TestCase):
    """Tests for tokenize_for_query."""

    def test_basic_query(self):
        result = tokenize_for_query("感情モデル")
        self.assertIsNotNone(result)
        # Should contain quoted tokens joined with OR
        self.assertIn("OR", result)
        self.assertIn('"感情"', result)
        self.assertIn('"モデル"', result)

    def test_stop_words_only_returns_none(self):
        """Query with only stop words should return None."""
        result = tokenize_for_query("の は が")
        self.assertIsNone(result)

    def test_empty_query_returns_none(self):
        result = tokenize_for_query("")
        self.assertIsNone(result)

    def test_mixed_query(self):
        result = tokenize_for_query("memory search")
        self.assertIsNotNone(result)
        self.assertIn('"memory"', result)
        self.assertIn('"search"', result)


class TestTextExtraction(unittest.TestCase):
    """Tests for episode and lesson text extraction."""

    def test_episode_to_text(self):
        ep = {
            "summary": "Test episode summary",
            "user_utterances": [
                {"text": "user said this"},
                {"text": "and this"},
            ],
        }
        text = _episode_to_text(ep)
        self.assertIn("Test episode summary", text)
        self.assertIn("user said this", text)
        self.assertIn("and this", text)

    def test_episode_no_utterances(self):
        ep = {"summary": "Only summary"}
        text = _episode_to_text(ep)
        self.assertEqual(text, "Only summary")

    def test_episode_empty(self):
        text = _episode_to_text({})
        self.assertEqual(text, "")

    def test_lesson_to_text(self):
        lesson = {
            "action": "Did something",
            "why": "Because reason",
            "fix": "Fixed it",
            "lesson": "Learned this",
            "rule": "Follow that",
        }
        text = _lesson_to_text(lesson)
        self.assertIn("Did something", text)
        self.assertIn("Because reason", text)
        self.assertIn("Learned this", text)

    def test_lesson_partial(self):
        lesson = {"action": "Only action", "lesson": "Only lesson"}
        text = _lesson_to_text(lesson)
        self.assertIn("Only action", text)
        self.assertIn("Only lesson", text)


class TestBM25Score(unittest.TestCase):
    """Tests for BM25 rank to score conversion."""

    def test_negative_rank(self):
        """Negative rank (more relevant) should give higher score."""
        score = _bm25_rank_to_score(-5.0)
        self.assertGreater(score, 0.5)

    def test_zero_rank(self):
        score = _bm25_rank_to_score(0.0)
        self.assertAlmostEqual(score, 1.0)

    def test_positive_rank(self):
        score = _bm25_rank_to_score(1.0)
        self.assertAlmostEqual(score, 0.5)

    def test_score_range(self):
        """Score should always be in [0, 1]."""
        for rank in [-100, -10, -1, 0, 1, 10, 100]:
            score = _bm25_rank_to_score(float(rank))
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 1.0)


class TestSemanticIndex(unittest.TestCase):
    """Tests for SemanticIndex database operations."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.idx = SemanticIndex(self.temp_dir)

    def tearDown(self):
        self.idx.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_episode(self, episode_id="ep001", summary="Test summary",
                      user_texts=None, tags=None, timestamp="2026-03-15T10:00:00Z",
                      session_id="session_test", episode_type="observation"):
        ep = {
            "episode_id": episode_id,
            "summary": summary,
            "user_utterances": [{"text": t} for t in (user_texts or [])],
            "tags": tags or [],
            "timestamp": timestamp,
            "session_id": session_id,
            "episode_type": episode_type,
        }
        return ep

    def _make_lesson(self, action="action", why="why", fix="fix",
                     lesson="lesson text", rule="rule", date="2026-03-15"):
        return {
            "action": action,
            "why": why,
            "fix": fix,
            "lesson": lesson,
            "rule": rule,
            "date": date,
        }

    # --- Add/Index tests ---

    def test_add_episode(self):
        ep = self._make_episode()
        result = self.idx.add_episode(ep)
        self.assertTrue(result)

    def test_add_episode_duplicate(self):
        ep = self._make_episode()
        self.idx.add_episode(ep)
        result = self.idx.add_episode(ep)
        self.assertFalse(result)  # Same hash, skip

    def test_add_episode_no_id(self):
        ep = {"summary": "no id"}
        result = self.idx.add_episode(ep)
        self.assertFalse(result)

    def test_add_episode_empty_text(self):
        ep = self._make_episode(summary="", user_texts=[])
        result = self.idx.add_episode(ep)
        self.assertFalse(result)

    def test_add_lesson(self):
        lesson = self._make_lesson()
        result = self.idx.add_lesson(lesson, 1)
        self.assertTrue(result)

    def test_add_lesson_duplicate(self):
        lesson = self._make_lesson()
        self.idx.add_lesson(lesson, 1)
        result = self.idx.add_lesson(lesson, 1)
        self.assertFalse(result)

    # --- Sync tests ---

    def test_sync_episodes(self):
        episodes = [
            self._make_episode(episode_id="ep001", summary="First episode"),
            self._make_episode(episode_id="ep002", summary="Second episode"),
        ]
        count = self.idx.sync_episodes(episodes)
        self.assertEqual(count, 2)

    def test_sync_episodes_incremental(self):
        ep1 = self._make_episode(episode_id="ep001", summary="First")
        self.idx.sync_episodes([ep1])

        ep2 = self._make_episode(episode_id="ep002", summary="Second")
        count = self.idx.sync_episodes([ep1, ep2])
        self.assertEqual(count, 1)  # Only ep2 is new

    def test_sync_lessons(self):
        lessons = [
            self._make_lesson(action="action1", lesson="lesson1"),
            self._make_lesson(action="action2", lesson="lesson2"),
        ]
        count = self.idx.sync_lessons(lessons)
        self.assertEqual(count, 2)

    # --- Rebuild tests ---

    def test_rebuild(self):
        episodes = [self._make_episode(episode_id="ep001")]
        lessons = [self._make_lesson()]
        result = self.idx.rebuild(episodes, lessons)
        self.assertEqual(result["episodes_indexed"], 1)
        self.assertEqual(result["lessons_indexed"], 1)

    def test_rebuild_clears_old_data(self):
        # Add some data
        self.idx.add_episode(self._make_episode(episode_id="ep_old"))
        # Rebuild with different data
        result = self.idx.rebuild(
            [self._make_episode(episode_id="ep_new")],
            [],
        )
        self.assertEqual(result["episodes_indexed"], 1)
        # Old episode should be gone
        ids = self.idx.get_indexed_episode_ids()
        self.assertNotIn("ep_old", ids)
        self.assertIn("ep_new", ids)

    # --- Search tests ---

    def test_search_basic(self):
        ep = self._make_episode(summary="感情モデルのテスト実装")
        self.idx.add_episode(ep)

        results = self.idx.search("感情モデル")
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["source_type"], "episode")

    def test_search_english(self):
        ep = self._make_episode(summary="memory search implementation test")
        self.idx.add_episode(ep)

        results = self.idx.search("memory search")
        self.assertGreater(len(results), 0)

    def test_search_lesson(self):
        lesson = self._make_lesson(lesson="Always check the root cause")
        self.idx.add_lesson(lesson, 1)

        results = self.idx.search("root cause")
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["source_type"], "lesson")

    def test_search_no_results(self):
        ep = self._make_episode(summary="something completely different")
        self.idx.add_episode(ep)

        results = self.idx.search("xyzzyx nonexistent")
        self.assertEqual(len(results), 0)

    def test_search_empty_query(self):
        results = self.idx.search("")
        self.assertEqual(len(results), 0)

    def test_search_stop_words_only(self):
        results = self.idx.search("の は が")
        self.assertEqual(len(results), 0)

    def test_search_with_tags_filter(self):
        ep1 = self._make_episode(
            episode_id="ep001", summary="感情テスト", tags=["emotion", "test"]
        )
        ep2 = self._make_episode(
            episode_id="ep002", summary="感情確認", tags=["memory"]
        )
        self.idx.add_episode(ep1)
        self.idx.add_episode(ep2)

        # Search with tag filter
        results = self.idx.search("感情", tags=["emotion"])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source_id"], "ep001")

    def test_search_with_last_filter(self):
        ep_recent = self._make_episode(
            episode_id="ep_recent", summary="感情テスト",
            timestamp="2026-03-15T10:00:00Z",
        )
        ep_old = self._make_episode(
            episode_id="ep_old", summary="感情古い",
            timestamp="2020-01-01T00:00:00Z",
        )
        self.idx.add_episode(ep_recent)
        self.idx.add_episode(ep_old)

        # Search with time filter (last 7 days)
        results = self.idx.search("感情", last="7d")
        # Only recent should match (assuming test runs near 2026-03-15)
        source_ids = [r["source_id"] for r in results]
        self.assertNotIn("ep_old", source_ids)

    def test_search_tags_filter_passes_lessons(self):
        """Lessons should pass through tag filter since they don't have tags."""
        lesson = self._make_lesson(lesson="Important lesson about emotions")
        self.idx.add_lesson(lesson, 1)

        results = self.idx.search("emotions", tags=["nonexistent"])
        # Lesson should still appear even with tag filter
        lesson_results = [r for r in results if r["source_type"] == "lesson"]
        self.assertEqual(len(lesson_results), 1)

    def test_search_limit(self):
        for i in range(10):
            ep = self._make_episode(
                episode_id=f"ep{i:03d}",
                summary=f"テスト episode {i}",
            )
            self.idx.add_episode(ep)

        results = self.idx.search("テスト", limit=3)
        self.assertLessEqual(len(results), 3)

    def test_search_results_have_score(self):
        ep = self._make_episode(summary="記憶検索テスト")
        self.idx.add_episode(ep)

        results = self.idx.search("記憶検索")
        self.assertGreater(len(results), 0)
        self.assertIn("score", results[0])
        self.assertGreater(results[0]["score"], 0.0)

    def test_search_special_characters(self):
        """Special characters in query should not crash."""
        ep = self._make_episode(summary="test content")
        self.idx.add_episode(ep)

        # These should not raise
        self.idx.search("test*")
        self.idx.search("test OR content")
        self.idx.search('"test"')

    # --- Ghost handling ---

    def test_ghost_episode_skipped(self):
        """If FTS entry exists but document row is missing, result should be skipped."""
        ep = self._make_episode(summary="ghost test content")
        self.idx.add_episode(ep)

        # Manually delete the document row but leave FTS
        conn = self.idx._get_conn()
        conn.execute("DELETE FROM documents WHERE doc_id = 'episode:ep001'")
        conn.commit()

        results = self.idx.search("ghost test")
        self.assertEqual(len(results), 0)

    # --- Dirty flag tests ---

    def test_dirty_flag_lifecycle(self):
        self.assertFalse(self.idx.is_dirty())
        self.idx.set_dirty()
        self.assertTrue(self.idx.is_dirty())
        self.idx.clear_dirty()
        self.assertFalse(self.idx.is_dirty())

    def test_dirty_flag_set_after_clear(self):
        self.idx.set_dirty()
        self.idx.clear_dirty()
        self.idx.set_dirty()
        self.assertTrue(self.idx.is_dirty())

    # --- Stats tests ---

    def test_stats(self):
        self.idx.add_episode(self._make_episode())
        self.idx.add_lesson(self._make_lesson(), 1)

        stats = self.idx.get_stats()
        self.assertEqual(stats["episode_count"], 1)
        self.assertEqual(stats["lesson_count"], 1)
        self.assertIn(stats["schema_version"], (1, 2))  # Phase 1 or Phase 2

    def test_indexed_episode_ids(self):
        self.idx.add_episode(self._make_episode(episode_id="ep001"))
        self.idx.add_episode(self._make_episode(episode_id="ep002"))

        ids = self.idx.get_indexed_episode_ids()
        self.assertEqual(ids, {"ep001", "ep002"})

    # --- Lessons mtime ---

    def test_get_lessons_mtime_no_file(self):
        mtime = get_lessons_mtime(self.temp_dir)
        self.assertEqual(mtime, 0.0)

    def test_get_lessons_mtime_with_file(self):
        path = os.path.join(self.temp_dir, "lessons_registry.md")
        with open(path, "w") as f:
            f.write("# test")
        mtime = get_lessons_mtime(self.temp_dir)
        self.assertGreater(mtime, 0.0)


class TestSemanticIndexUserUtteranceSearch(unittest.TestCase):
    """Tests that user utterances are searchable."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.idx = SemanticIndex(self.temp_dir)

    def tearDown(self):
        self.idx.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_search_user_utterance(self):
        ep = {
            "episode_id": "ep_utt",
            "summary": "general conversation",
            "user_utterances": [{"text": "セマンティック検索の実装について話したい"}],
            "tags": [],
            "timestamp": "2026-03-15T10:00:00Z",
            "session_id": "test",
            "episode_type": "user_request",
        }
        self.idx.add_episode(ep)

        results = self.idx.search("セマンティック検索")
        self.assertGreater(len(results), 0)


class TestMemorySearchQueryIntegration(unittest.TestCase):
    """Tests for query/keywords mutual exclusion in memory_search."""

    def test_query_keywords_exclusive(self):
        """query and keywords should be mutually exclusive."""
        # Import memory_search
        import sys
        tools_dir = os.path.dirname(os.path.abspath(__file__))
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)

        from memory_mcp_server import memory_search

        result = memory_search(keywords="test", query="test")
        self.assertIn("ERROR", result)
        self.assertIn("mutually exclusive", result)

    def test_no_params_error(self):
        """No params should return error."""
        from memory_mcp_server import memory_search

        result = memory_search()
        self.assertIn("ERROR", result)

    def test_query_alone_accepted(self):
        """query alone should not produce the 'at least one' error."""
        from memory_mcp_server import memory_search

        # This will either work or give an FTS error, but not the param error
        result = memory_search(query="test query")
        self.assertNotIn("At least one of", result)


class TestGracefulDegradation(unittest.TestCase):
    """Tests for graceful degradation when semantic_index is unavailable."""

    def test_import_flag(self):
        """_SEMANTIC_AVAILABLE should be True when module is importable."""
        from memory_mcp_server import _SEMANTIC_AVAILABLE
        self.assertTrue(_SEMANTIC_AVAILABLE)


class TestSemanticIndexLessonMtimeSync(unittest.TestCase):
    """Tests for lesson mtime-based sync detection."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.idx = SemanticIndex(self.temp_dir)

    def tearDown(self):
        self.idx.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_mtime_changes_detected(self):
        """Changes to lessons file mtime should be detectable."""
        path = os.path.join(self.temp_dir, "lessons_registry.md")

        # No file initially
        mtime1 = get_lessons_mtime(self.temp_dir)
        self.assertEqual(mtime1, 0.0)

        # Create file
        with open(path, "w") as f:
            f.write("# initial")
        mtime2 = get_lessons_mtime(self.temp_dir)
        self.assertGreater(mtime2, 0.0)

        # Modify file (sleep to ensure mtime changes)
        time.sleep(0.1)
        with open(path, "w") as f:
            f.write("# modified")
        mtime3 = get_lessons_mtime(self.temp_dir)
        self.assertGreaterEqual(mtime3, mtime2)


class TestTokenizerEdgeCases(unittest.TestCase):
    """Edge case tests for the tokenizer."""

    def test_very_long_kanji(self):
        """Long kanji sequence should produce correct number of bigrams."""
        text = "自然言語処理機械学習"  # 10 chars -> 9 bigrams
        tokens = tokenize_japanese(text)
        self.assertEqual(len(tokens), 9)

    def test_mixed_scripts_rapid_change(self):
        """Rapid script changes should produce correct tokens."""
        text = "AテストB試験C"
        tokens = tokenize_japanese(text)
        self.assertIn("a", tokens)
        self.assertIn("テスト", tokens)
        self.assertIn("b", tokens)
        self.assertIn("試験", tokens)
        self.assertIn("c", tokens)

    def test_whitespace_only(self):
        tokens = tokenize_japanese("   ")
        self.assertEqual(tokens, [])

    def test_emoji_handling(self):
        """Emoji should not crash tokenizer."""
        tokens = tokenize_japanese("テスト😀完了")
        # Emoji is "other", so it splits but doesn't crash
        self.assertIn("テスト", tokens)
        self.assertIn("完了", tokens)


class TestMED1CachedEpisodeLoading(unittest.TestCase):
    """MED #1: Verify _fts_search does not call _load_all_episodes multiple times."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.idx = SemanticIndex(self.temp_dir)

    def tearDown(self):
        self.idx.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_fts_search_caches_episodes(self):
        """_fts_search should call _load_all_episodes at most once."""
        import memory_mcp_server as mms
        original_load = mms._load_all_episodes
        call_count = 0

        def counting_load(memory_dir):
            nonlocal call_count
            call_count += 1
            return original_load(memory_dir)

        # Add an episode to the index
        ep = {
            "episode_id": "ep001",
            "summary": "cache test content",
            "user_utterances": [],
            "tags": [],
            "timestamp": "2026-03-15T10:00:00Z",
            "session_id": "s1",
            "episode_type": "observation",
        }
        self.idx.add_episode(ep)
        self.idx.set_dirty()  # Force sync path

        with patch.object(mms, "_load_all_episodes", side_effect=counting_load):
            mms._fts_search(self.temp_dir, "cache test", "", "", 10, None)

        self.assertLessEqual(call_count, 1,
            f"_load_all_episodes called {call_count} times, expected at most 1")


class TestMED2TimeRangeFilter(unittest.TestCase):
    """MED #2: Time range filter should use UNIX timestamp comparison."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.idx = SemanticIndex(self.temp_dir)

    def tearDown(self):
        self.idx.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_episode(self, episode_id, summary, timestamp):
        return {
            "episode_id": episode_id,
            "summary": summary,
            "user_utterances": [],
            "tags": [],
            "timestamp": timestamp,
            "session_id": "test",
            "episode_type": "observation",
        }

    def test_empty_timestamp_excluded(self):
        """Episodes with empty timestamp should be excluded by time filter."""
        ep = self._make_episode("ep_empty", "テスト内容 empty ts", "")
        self.idx.add_episode(ep)

        results = self.idx.search("テスト内容", last="7d")
        source_ids = [r["source_id"] for r in results]
        self.assertNotIn("ep_empty", source_ids)

    def test_timezone_mixed_comparison(self):
        """Timestamps with different timezone offsets should compare correctly."""
        from datetime import datetime as dt, timezone as tz, timedelta as td
        # Both represent the same moment: recent (1 hour ago)
        now = dt.now(tz.utc)
        recent = now - td(hours=1)
        # UTC format
        ts_utc = recent.strftime("%Y-%m-%dT%H:%M:%SZ")
        # +09:00 format (same moment)
        jst = tz(td(hours=9))
        ts_jst = recent.astimezone(jst).strftime("%Y-%m-%dT%H:%M:%S+09:00")

        ep_utc = self._make_episode("ep_utc", "タイムゾーンテスト utc", ts_utc)
        ep_jst = self._make_episode("ep_jst", "タイムゾーンテスト jst", ts_jst)
        self.idx.add_episode(ep_utc)
        self.idx.add_episode(ep_jst)

        results = self.idx.search("タイムゾーンテスト", last="1d")
        source_ids = [r["source_id"] for r in results]
        self.assertIn("ep_utc", source_ids)
        self.assertIn("ep_jst", source_ids)

    def test_old_episode_filtered_out(self):
        """Old episodes should be filtered by time range."""
        ep_old = self._make_episode("ep_ancient", "フィルタテスト古い", "2020-01-01T00:00:00Z")
        self.idx.add_episode(ep_old)

        results = self.idx.search("フィルタテスト", last="7d")
        source_ids = [r["source_id"] for r in results]
        self.assertNotIn("ep_ancient", source_ids)

    def test_unparseable_timestamp_excluded(self):
        """Episodes with unparseable timestamps should be excluded by time filter."""
        ep = self._make_episode("ep_bad_ts", "テスト不正タイムスタンプ", "not-a-date")
        self.idx.add_episode(ep)

        results = self.idx.search("テスト不正", last="7d")
        source_ids = [r["source_id"] for r in results]
        self.assertNotIn("ep_bad_ts", source_ids)


class TestMED3IncrementalLessonSync(unittest.TestCase):
    """MED #3: Lesson sync should be incremental using synced lesson count."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.idx = SemanticIndex(self.temp_dir)

    def tearDown(self):
        self.idx.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_lesson(self, action, lesson_text):
        return {
            "action": action,
            "why": "test why",
            "fix": "test fix",
            "lesson": lesson_text,
            "rule": "test rule",
            "date": "2026-03-15",
        }

    def test_incremental_sync_skips_existing(self):
        """Second sync with appended lessons should only index new ones."""
        lessons_v1 = [
            self._make_lesson("action1", "lesson one"),
            self._make_lesson("action2", "lesson two"),
        ]
        count1 = self.idx.sync_lessons(lessons_v1)
        self.assertEqual(count1, 2)

        # Append a new lesson (append-only)
        lessons_v2 = lessons_v1 + [self._make_lesson("action3", "lesson three")]
        count2 = self.idx.sync_lessons(lessons_v2)
        self.assertEqual(count2, 1)  # Only the 3rd lesson is new

    def test_synced_lesson_count_stored_in_meta(self):
        """synced_lesson_count should be stored in meta table."""
        lessons = [self._make_lesson("a1", "l1"), self._make_lesson("a2", "l2")]
        self.idx.sync_lessons(lessons)

        conn = self.idx._get_conn()
        cur = conn.execute("SELECT value FROM meta WHERE key = 'synced_lesson_count'")
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "2")

    def test_rebuild_resets_synced_lesson_count(self):
        """rebuild() should reset synced_lesson_count to 0 before re-syncing."""
        lessons = [self._make_lesson("a1", "l1")]
        self.idx.sync_lessons(lessons)

        # Rebuild with different data
        self.idx.rebuild([], [self._make_lesson("a2", "l2")])

        conn = self.idx._get_conn()
        cur = conn.execute("SELECT value FROM meta WHERE key = 'synced_lesson_count'")
        row = cur.fetchone()
        # After rebuild + sync, count should be 1 (the new lesson)
        self.assertEqual(row[0], "1")


class TestLOW4DoubleQuoteInQuery(unittest.TestCase):
    """LOW #4: Double quotes in query should not cause FTS5 syntax errors."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.idx = SemanticIndex(self.temp_dir)

    def tearDown(self):
        self.idx.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_double_quote_in_query_returns_results(self):
        """Query containing double quotes should still find results."""
        ep = {
            "episode_id": "ep_quote",
            "summary": "test content for quote handling",
            "user_utterances": [],
            "tags": [],
            "timestamp": "2026-03-15T10:00:00Z",
            "session_id": "test",
            "episode_type": "observation",
        }
        self.idx.add_episode(ep)

        # This should not crash and should find results
        results = self.idx.search('"test" content')
        self.assertGreater(len(results), 0)

    def test_tokenize_for_query_strips_quotes(self):
        """tokenize_for_query should strip double quotes from tokens."""
        result = tokenize_for_query('"test" content')
        self.assertIsNotNone(result)
        # Should not have nested quotes
        self.assertNotIn('""', result)

    def test_only_double_quotes_returns_none(self):
        """Query of only double quotes should return None."""
        result = tokenize_for_query('"""')
        self.assertIsNone(result)


class TestParseIsoTimestamp(unittest.TestCase):
    """Tests for the _parse_iso_timestamp helper."""

    def test_utc_z_suffix(self):
        from semantic_index import _parse_iso_timestamp
        ts = _parse_iso_timestamp("2026-03-15T10:00:00Z")
        self.assertIsNotNone(ts)
        self.assertIsInstance(ts, float)

    def test_timezone_offset(self):
        from semantic_index import _parse_iso_timestamp
        ts = _parse_iso_timestamp("2026-03-15T19:00:00+09:00")
        self.assertIsNotNone(ts)

    def test_empty_string(self):
        from semantic_index import _parse_iso_timestamp
        self.assertIsNone(_parse_iso_timestamp(""))

    def test_none(self):
        from semantic_index import _parse_iso_timestamp
        self.assertIsNone(_parse_iso_timestamp(None))

    def test_invalid_string(self):
        from semantic_index import _parse_iso_timestamp
        self.assertIsNone(_parse_iso_timestamp("not-a-date"))

    def test_utc_and_jst_same_moment(self):
        """UTC and JST timestamps representing the same moment should parse to same value."""
        from semantic_index import _parse_iso_timestamp
        ts_utc = _parse_iso_timestamp("2026-03-15T10:00:00Z")
        ts_jst = _parse_iso_timestamp("2026-03-15T19:00:00+09:00")
        self.assertIsNotNone(ts_utc)
        self.assertIsNotNone(ts_jst)
        self.assertAlmostEqual(ts_utc, ts_jst, places=0)


class TestORSearchBehavior(unittest.TestCase):
    """Tests for OR-based FTS5 search: partial matches and BM25 scoring."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.idx = SemanticIndex(self.temp_dir)

    def tearDown(self):
        self.idx.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_episode(self, episode_id, summary, **kwargs):
        ep = {
            "episode_id": episode_id,
            "summary": summary,
            "user_utterances": [],
            "tags": kwargs.get("tags", []),
            "timestamp": kwargs.get("timestamp", "2026-03-15T10:00:00Z"),
            "session_id": kwargs.get("session_id", "test"),
            "episode_type": kwargs.get("episode_type", "observation"),
        }
        return ep

    def test_partial_match_hits(self):
        """OR search should find documents even when only some tokens match."""
        # "あの時の失敗" tokenizes to tokens including "失敗" (after stop word removal)
        # An episode containing "失敗" but not "あの" or "時" should still be found
        ep = self._make_episode(
            "ep_fail", "設計判断で失敗した経験を記録した"
        )
        self.idx.add_episode(ep)

        results = self.idx.search("あの時の失敗")
        self.assertGreater(len(results), 0, "Partial match on '失敗' should return results")

    def test_full_match_scores_higher_than_partial(self):
        """Documents matching all query tokens should score higher than partial matches."""
        # Episode with both "感情" and "モデル"
        ep_full = self._make_episode(
            "ep_full", "感情モデルの設計と実装"
        )
        # Episode with only "感情"
        ep_partial = self._make_episode(
            "ep_partial", "感情の分析結果"
        )
        self.idx.add_episode(ep_full)
        self.idx.add_episode(ep_partial)

        results = self.idx.search("感情モデル")
        self.assertGreater(len(results), 0)

        # Find both episodes in results
        scores = {r["source_id"]: r["score"] for r in results}
        self.assertIn("ep_full", scores, "Full match episode should be in results")
        self.assertIn("ep_partial", scores, "Partial match episode should be in results")
        self.assertGreater(
            scores["ep_full"], scores["ep_partial"],
            "Full match should have higher BM25 score than partial match"
        )

    def test_ano_toki_no_shippai_query(self):
        """The motivating example: 'あの時の失敗' should find episodes containing '失敗'."""
        ep = self._make_episode(
            "ep_shippai", "リーダーがコードを直接書いてしまい失敗した"
        )
        self.idx.add_episode(ep)

        results = self.idx.search("あの時の失敗")
        source_ids = [r["source_id"] for r in results]
        self.assertIn("ep_shippai", source_ids)

    def test_or_query_format(self):
        """tokenize_for_query should produce OR-joined tokens."""
        result = tokenize_for_query("記憶検索テスト")
        self.assertIsNotNone(result)
        self.assertIn("OR", result)
        self.assertNotIn("AND", result)


# ========================================================================
# Phase 2 Tests: Embedding Provider, Vector Search, Hybrid Merge
# ========================================================================


class TestEmbeddingProviderAbstraction(unittest.TestCase):
    """Tests for the embedding provider abstraction layer."""

    def test_import_embedding_provider(self):
        """embedding_provider module should be importable."""
        import embedding_provider
        self.assertTrue(hasattr(embedding_provider, "EmbeddingProvider"))
        self.assertTrue(hasattr(embedding_provider, "auto_select_provider"))

    def test_auto_select_no_keys(self):
        """auto_select_provider should return None when no API keys are set."""
        from embedding_provider import auto_select_provider
        with patch.dict(os.environ, {}, clear=True):
            # Remove any API keys
            env = os.environ.copy()
            env.pop("OPENAI_API_KEY", None)
            env.pop("GEMINI_API_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                provider = auto_select_provider()
                self.assertIsNone(provider)

    def test_openai_provider_created_with_key(self):
        """OpenAI provider should be created when OPENAI_API_KEY is set."""
        from embedding_provider import auto_select_provider
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key-123"}):
            provider = auto_select_provider()
            self.assertIsNotNone(provider)
            self.assertEqual(provider.provider_name, "openai")
            self.assertEqual(provider.model_id, "text-embedding-3-small")
            self.assertEqual(provider.dimensions, 1536)

    def test_gemini_provider_created_with_key(self):
        """Gemini provider should be created when GEMINI_API_KEY is set."""
        from embedding_provider import auto_select_provider
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key-456"}, clear=False):
            # Remove OpenAI key to fall through to Gemini
            env = os.environ.copy()
            env.pop("OPENAI_API_KEY", None)
            env["GEMINI_API_KEY"] = "test-key-456"
            with patch.dict(os.environ, env, clear=True):
                provider = auto_select_provider()
                self.assertIsNotNone(provider)
                self.assertEqual(provider.provider_name, "gemini")
                self.assertEqual(provider.model_id, "gemini-embedding-001")
                self.assertEqual(provider.dimensions, 3072)

    def test_openai_priority_over_gemini(self):
        """OpenAI should be selected over Gemini when both keys exist."""
        from embedding_provider import auto_select_provider
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "openai-key",
            "GEMINI_API_KEY": "gemini-key",
        }):
            provider = auto_select_provider()
            self.assertIsNotNone(provider)
            self.assertEqual(provider.provider_name, "openai")

    def test_provider_embed_single(self):
        """embed_single should call embed_texts with single-item list."""
        from embedding_provider import OpenAIProvider
        provider = OpenAIProvider("test-key")
        mock_result = [[0.1, 0.2, 0.3]]
        with patch.object(provider, "embed_texts", return_value=mock_result):
            result = provider.embed_single("test text")
            self.assertEqual(result, [0.1, 0.2, 0.3])

    def test_provider_embed_single_failure(self):
        """embed_single should return None on failure."""
        from embedding_provider import OpenAIProvider
        provider = OpenAIProvider("test-key")
        with patch.object(provider, "embed_texts", return_value=[None]):
            result = provider.embed_single("test text")
            self.assertIsNone(result)

    def test_provider_embed_empty_list(self):
        """embed_texts with empty list should return empty list."""
        from embedding_provider import OpenAIProvider
        provider = OpenAIProvider("test-key")
        result = provider.embed_texts([])
        self.assertEqual(result, [])


class TestVectorSearchModule(unittest.TestCase):
    """Tests for vector_search.py module."""

    def test_import_vector_search(self):
        """vector_search module should be importable."""
        import vector_search
        self.assertTrue(hasattr(vector_search, "hybrid_merge"))
        self.assertTrue(hasattr(vector_search, "vector_to_blob"))
        self.assertTrue(hasattr(vector_search, "blob_to_vector"))

    def test_vector_serialization_roundtrip(self):
        """Vector should survive serialization/deserialization."""
        from vector_search import vector_to_blob, blob_to_vector
        original = [0.1, 0.2, 0.3, 0.4, 0.5]
        blob = vector_to_blob(original)
        restored = blob_to_vector(blob, len(original))
        for o, r in zip(original, restored):
            self.assertAlmostEqual(o, r, places=5)

    def test_cosine_similarity(self):
        """Cosine similarity should be correct for known vectors."""
        from vector_search import _cosine_similarity
        # Identical vectors -> max similarity (1.0 after normalization)
        sim = _cosine_similarity([1.0, 0.0], [1.0, 0.0])
        self.assertAlmostEqual(sim, 1.0, places=5)

        # Orthogonal vectors -> 0.5 (cosine=0, normalized to [0,1])
        sim = _cosine_similarity([1.0, 0.0], [0.0, 1.0])
        self.assertAlmostEqual(sim, 0.5, places=5)

        # Opposite vectors -> 0.0
        sim = _cosine_similarity([1.0, 0.0], [-1.0, 0.0])
        self.assertAlmostEqual(sim, 0.0, places=5)

    def test_cosine_similarity_zero_vector(self):
        """Cosine similarity with zero vector should return 0."""
        from vector_search import _cosine_similarity
        sim = _cosine_similarity([0.0, 0.0], [1.0, 0.0])
        self.assertAlmostEqual(sim, 0.0, places=5)

    def test_min_max_normalize(self):
        """Min-max normalization should produce [0, 1] range."""
        from vector_search import _min_max_normalize
        scores = {"a": 1.0, "b": 5.0, "c": 3.0}
        normalized = _min_max_normalize(scores)
        self.assertAlmostEqual(normalized["a"], 0.0)
        self.assertAlmostEqual(normalized["b"], 1.0)
        self.assertAlmostEqual(normalized["c"], 0.5)

    def test_min_max_normalize_all_same(self):
        """All same scores should normalize to 1.0."""
        from vector_search import _min_max_normalize
        scores = {"a": 5.0, "b": 5.0}
        normalized = _min_max_normalize(scores)
        self.assertAlmostEqual(normalized["a"], 1.0)
        self.assertAlmostEqual(normalized["b"], 1.0)

    def test_min_max_normalize_empty(self):
        """Empty scores should return empty dict."""
        from vector_search import _min_max_normalize
        self.assertEqual(_min_max_normalize({}), {})


class TestHybridMerge(unittest.TestCase):
    """Tests for hybrid score merging."""

    def test_fts_only(self):
        """When no vector results, FTS results should pass through."""
        from vector_search import hybrid_merge
        fts = [
            {"doc_id": "a", "score": 0.9, "text": "test"},
            {"doc_id": "b", "score": 0.5, "text": "test2"},
        ]
        result = hybrid_merge(fts, [])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["doc_id"], "a")

    def test_vector_only_returns_results(self):
        """When no FTS results, return vector-only results with _vec_only marker."""
        from vector_search import hybrid_merge
        vec = [("a", 0.9), ("b", 0.5)]
        result = hybrid_merge([], vec)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["doc_id"], "a")
        self.assertTrue(result[0].get("_vec_only", False))

    def test_both_empty_returns_empty(self):
        """Both empty should return empty."""
        from vector_search import hybrid_merge
        result = hybrid_merge([], [])
        self.assertEqual(len(result), 0)

    def test_hybrid_score_computation(self):
        """Hybrid scores should combine FTS and vector scores with weights."""
        from vector_search import hybrid_merge
        fts = [
            {"doc_id": "a", "score": 0.8, "text": "test"},
            {"doc_id": "b", "score": 0.4, "text": "test2"},
        ]
        vec = [("a", 0.6), ("b", 0.9)]
        result = hybrid_merge(fts, vec, vector_weight=0.7, fts_weight=0.3)

        self.assertEqual(len(result), 2)
        # Both docs have both scores, so hybrid merge should work
        scores = {r["doc_id"]: r["score"] for r in result}
        self.assertIn("a", scores)
        self.assertIn("b", scores)

    def test_hybrid_reorders_by_combined_score(self):
        """Documents should be reordered by hybrid score."""
        from vector_search import hybrid_merge
        # FTS ranks "a" higher, but vector strongly prefers "b"
        fts = [
            {"doc_id": "a", "score": 0.9, "text": "a"},
            {"doc_id": "b", "score": 0.1, "text": "b"},
        ]
        vec = [("b", 0.99), ("a", 0.01)]
        # With vector_weight=0.7, "b" should rank higher
        result = hybrid_merge(fts, vec, vector_weight=0.9, fts_weight=0.1)
        self.assertEqual(result[0]["doc_id"], "b")

    def test_fts_only_doc_in_hybrid(self):
        """Documents only in FTS should get FTS-only score."""
        from vector_search import hybrid_merge
        fts = [
            {"doc_id": "a", "score": 0.9, "text": "test"},
            {"doc_id": "b", "score": 0.5, "text": "test2"},
        ]
        # Only "a" has vector result
        vec = [("a", 0.8)]
        result = hybrid_merge(fts, vec, vector_weight=0.7, fts_weight=0.3)
        self.assertEqual(len(result), 2)


class TestSqliteVecIntegration(unittest.TestCase):
    """Tests for sqlite-vec vector storage (if available)."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        # Check if sqlite-vec is available
        from vector_search import _check_sqlite_vec
        self.vec_available = _check_sqlite_vec()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _get_db(self):
        import vector_search
        db = sqlite3.connect(os.path.join(self.temp_dir, "test.db"))
        if self.vec_available:
            vector_search._load_sqlite_vec(db)
        return db

    def test_store_and_search_sqlite_vec(self):
        """Store and search vectors using sqlite-vec (or fallback)."""
        from vector_search import (
            create_vector_tables, create_doc_map_table,
            store_vector, vector_search as vec_search,
            get_vector_count,
        )
        db = self._get_db()
        use_vec = self.vec_available

        create_doc_map_table(db)
        create_vector_tables(db, 4, use_vec)

        # Store vectors
        store_vector(db, "doc:1", [1.0, 0.0, 0.0, 0.0], "hash1", use_vec)
        store_vector(db, "doc:2", [0.0, 1.0, 0.0, 0.0], "hash2", use_vec)
        store_vector(db, "doc:3", [0.7, 0.7, 0.0, 0.0], "hash3", use_vec)

        self.assertEqual(get_vector_count(db, use_vec), 3)

        # Search for similar to [1, 0, 0, 0]
        results = vec_search(db, [1.0, 0.0, 0.0, 0.0], 2, 4, use_vec)
        self.assertGreater(len(results), 0)
        # doc:1 should be most similar
        self.assertEqual(results[0][0], "doc:1")

        db.close()

    def test_store_vector_update(self):
        """Storing a vector for an existing doc should update it."""
        from vector_search import (
            create_vector_tables, create_doc_map_table,
            store_vector, get_vector_hash, get_vector_count,
        )
        db = self._get_db()
        use_vec = self.vec_available

        create_doc_map_table(db)
        create_vector_tables(db, 4, use_vec)

        store_vector(db, "doc:1", [1.0, 0.0, 0.0, 0.0], "hash1", use_vec)
        self.assertEqual(get_vector_hash(db, "doc:1", use_vec), "hash1")

        store_vector(db, "doc:1", [0.0, 1.0, 0.0, 0.0], "hash2", use_vec)
        self.assertEqual(get_vector_hash(db, "doc:1", use_vec), "hash2")
        self.assertEqual(get_vector_count(db, use_vec), 1)

        db.close()

    def test_drop_vector_data(self):
        """drop_vector_data should clear all vectors."""
        from vector_search import (
            create_vector_tables, create_doc_map_table,
            store_vector, drop_vector_data, get_vector_count,
        )
        db = self._get_db()
        use_vec = self.vec_available

        create_doc_map_table(db)
        create_vector_tables(db, 4, use_vec)

        store_vector(db, "doc:1", [1.0, 0.0, 0.0, 0.0], "hash1", use_vec)
        store_vector(db, "doc:2", [0.0, 1.0, 0.0, 0.0], "hash2", use_vec)
        self.assertEqual(get_vector_count(db, use_vec), 2)

        drop_vector_data(db, use_vec)
        self.assertEqual(get_vector_count(db, use_vec), 0)

        db.close()


class TestPythonFallbackSearch(unittest.TestCase):
    """Tests for Python cosine similarity fallback (no sqlite-vec)."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_python_fallback_search(self):
        """Python fallback should find similar vectors."""
        from vector_search import (
            create_vector_tables, store_vector,
            _vec_search_python,
        )
        db = sqlite3.connect(os.path.join(self.temp_dir, "test.db"))

        # Create BLOB table (not vec0)
        create_vector_tables(db, 4, use_sqlite_vec=False)

        store_vector(db, "doc:1", [1.0, 0.0, 0.0, 0.0], "h1", False)
        store_vector(db, "doc:2", [0.0, 1.0, 0.0, 0.0], "h2", False)
        store_vector(db, "doc:3", [0.9, 0.1, 0.0, 0.0], "h3", False)

        results = _vec_search_python(db, [1.0, 0.0, 0.0, 0.0], 3, 4)
        self.assertGreater(len(results), 0)
        # doc:1 should be most similar
        self.assertEqual(results[0][0], "doc:1")
        # doc:3 should be second (most similar after doc:1)
        self.assertEqual(results[1][0], "doc:3")

        db.close()


class TestSemanticIndexPhase2Integration(unittest.TestCase):
    """Tests for Phase 2 integration in SemanticIndex."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.idx = SemanticIndex(self.temp_dir)

    def tearDown(self):
        self.idx.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_episode(self, episode_id="ep001", summary="Test summary",
                      timestamp="2026-03-15T10:00:00Z"):
        return {
            "episode_id": episode_id,
            "summary": summary,
            "user_utterances": [],
            "tags": [],
            "timestamp": timestamp,
            "session_id": "test",
            "episode_type": "observation",
        }

    def test_phase2_schema_migration(self):
        """Phase 2 migration should add meta entries."""
        stats = self.idx.get_stats()
        self.assertEqual(stats["schema_version"], 2)

    def test_hybrid_search_fts_only_without_provider(self):
        """hybrid_search should fall back to FTS when no provider available."""
        ep = self._make_episode(summary="感情モデルのテスト実装")
        self.idx.add_episode(ep)

        # No API keys set -> FTS-only
        with patch.dict(os.environ, {}, clear=True):
            env = os.environ.copy()
            env.pop("OPENAI_API_KEY", None)
            env.pop("GEMINI_API_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                # Reset provider cache
                self.idx._provider_initialized = False
                self.idx._provider = None
                results = self.idx.hybrid_search("感情モデル")
                self.assertGreater(len(results), 0)
                self.assertEqual(results[0]["source_type"], "episode")

    def test_hybrid_search_with_mock_provider(self):
        """hybrid_search should combine FTS and vector results with mock provider."""
        from embedding_provider import EmbeddingProvider

        ep1 = self._make_episode(
            episode_id="ep001", summary="感情モデルの設計と実装"
        )
        ep2 = self._make_episode(
            episode_id="ep002", summary="感情の分析結果"
        )
        self.idx.add_episode(ep1)
        self.idx.add_episode(ep2)

        # Create mock provider
        class MockProvider(EmbeddingProvider):
            @property
            def provider_name(self): return "mock"
            @property
            def model_id(self): return "mock-model"
            @property
            def dimensions(self): return 4

            def embed_texts(self, texts):
                # Return simple vectors
                results = []
                for t in texts:
                    if "設計" in t:
                        results.append([1.0, 0.0, 0.0, 0.0])
                    elif "分析" in t:
                        results.append([0.0, 1.0, 0.0, 0.0])
                    else:
                        results.append([0.5, 0.5, 0.0, 0.0])
                return results

        # Inject mock provider
        self.idx._provider = MockProvider()
        self.idx._provider_initialized = True

        # Sync vectors with mock provider
        self.idx.sync_vectors()

        # Verify vectors were stored
        conn = self.idx._get_conn()
        from vector_search import get_vector_count
        count = get_vector_count(conn, self.idx._use_sqlite_vec)
        self.assertEqual(count, 2)

        # Hybrid search (query vector will be [0.5, 0.5, 0, 0])
        results = self.idx.hybrid_search("感情モデル")
        self.assertGreater(len(results), 0)

    def test_sync_vectors_no_provider(self):
        """sync_vectors should return 0 when no provider available."""
        with patch.dict(os.environ, {}, clear=True):
            env = os.environ.copy()
            env.pop("OPENAI_API_KEY", None)
            env.pop("GEMINI_API_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                self.idx._provider_initialized = False
                self.idx._provider = None
                count = self.idx.sync_vectors()
                self.assertEqual(count, 0)

    def test_provider_switch_detection(self):
        """Provider switch should invalidate existing vectors."""
        from embedding_provider import EmbeddingProvider

        class MockProviderA(EmbeddingProvider):
            @property
            def provider_name(self): return "provider_a"
            @property
            def model_id(self): return "model_a"
            @property
            def dimensions(self): return 4
            def embed_texts(self, texts):
                return [[1.0, 0.0, 0.0, 0.0]] * len(texts)

        class MockProviderB(EmbeddingProvider):
            @property
            def provider_name(self): return "provider_b"
            @property
            def model_id(self): return "model_b"
            @property
            def dimensions(self): return 4
            def embed_texts(self, texts):
                return [[0.0, 1.0, 0.0, 0.0]] * len(texts)

        ep = self._make_episode(summary="テスト文書")
        self.idx.add_episode(ep)

        # First provider
        self.idx._provider = MockProviderA()
        self.idx._provider_initialized = True
        self.idx.sync_vectors()

        conn = self.idx._get_conn()
        from vector_search import get_vector_count
        self.assertEqual(get_vector_count(conn, self.idx._use_sqlite_vec), 1)

        # Switch provider
        self.idx._provider = MockProviderB()
        switched = self.idx._check_provider_switch(conn)
        self.assertTrue(switched)

        # Vectors should be invalidated
        self.assertEqual(get_vector_count(conn, self.idx._use_sqlite_vec), 0)

    def test_provider_absence_preserves_vectors(self):
        """Provider going absent should NOT invalidate vectors."""
        from embedding_provider import EmbeddingProvider

        class MockProvider(EmbeddingProvider):
            @property
            def provider_name(self): return "mock"
            @property
            def model_id(self): return "mock-model"
            @property
            def dimensions(self): return 4
            def embed_texts(self, texts):
                return [[1.0, 0.0, 0.0, 0.0]] * len(texts)

        ep = self._make_episode(summary="テスト文書")
        self.idx.add_episode(ep)

        # Generate vectors with provider
        self.idx._provider = MockProvider()
        self.idx._provider_initialized = True
        self.idx.sync_vectors()

        conn = self.idx._get_conn()
        from vector_search import get_vector_count
        self.assertEqual(get_vector_count(conn, self.idx._use_sqlite_vec), 1)

        # Provider goes absent (API key removed)
        self.idx._provider = None
        self.idx._provider_initialized = True
        switched = self.idx._check_provider_switch(conn)
        self.assertFalse(switched)

        # Vectors should still be there
        self.assertEqual(get_vector_count(conn, self.idx._use_sqlite_vec), 1)

    def test_partial_embedding_failure(self):
        """Failed embeddings for some docs should not prevent others."""
        from embedding_provider import EmbeddingProvider

        class PartialFailProvider(EmbeddingProvider):
            @property
            def provider_name(self): return "partial"
            @property
            def model_id(self): return "partial-model"
            @property
            def dimensions(self): return 4
            def embed_texts(self, texts):
                # First succeeds, second fails
                results = []
                for i, _ in enumerate(texts):
                    if i % 2 == 0:
                        results.append([1.0, 0.0, 0.0, 0.0])
                    else:
                        results.append(None)
                return results

        self.idx.add_episode(self._make_episode(episode_id="ep001", summary="doc one"))
        self.idx.add_episode(self._make_episode(episode_id="ep002", summary="doc two"))

        self.idx._provider = PartialFailProvider()
        self.idx._provider_initialized = True
        count = self.idx.sync_vectors()

        # At least one should succeed
        self.assertGreaterEqual(count, 1)

    def test_rebuild_clears_vectors(self):
        """rebuild should also clear vector data."""
        from embedding_provider import EmbeddingProvider

        class MockProvider(EmbeddingProvider):
            @property
            def provider_name(self): return "mock"
            @property
            def model_id(self): return "mock-model"
            @property
            def dimensions(self): return 4
            def embed_texts(self, texts):
                return [[1.0, 0.0, 0.0, 0.0]] * len(texts)

        ep = self._make_episode(summary="original doc")
        self.idx.add_episode(ep)

        self.idx._provider = MockProvider()
        self.idx._provider_initialized = True
        self.idx.sync_vectors()

        conn = self.idx._get_conn()
        from vector_search import get_vector_count
        self.assertGreater(get_vector_count(conn, self.idx._use_sqlite_vec), 0)

        # Rebuild
        self.idx.rebuild(
            [self._make_episode(episode_id="ep_new", summary="new doc")],
            [],
        )
        self.assertEqual(get_vector_count(conn, self.idx._use_sqlite_vec), 0)

    def test_stats_include_phase2_info(self):
        """get_stats should include Phase 2 fields."""
        stats = self.idx.get_stats()
        self.assertIn("vector_count", stats)
        self.assertIn("vector_enabled", stats)
        self.assertIn("use_sqlite_vec", stats)


class TestGracefulDegradationPhase2(unittest.TestCase):
    """Tests for graceful degradation when Phase 2 modules fail."""

    def test_phase2_available_flag(self):
        """_PHASE2_AVAILABLE should be True when modules are importable."""
        from semantic_index import _PHASE2_AVAILABLE
        self.assertTrue(_PHASE2_AVAILABLE)

    def test_hybrid_search_same_as_fts_without_vectors(self):
        """hybrid_search without vectors should return same as search."""
        temp_dir = tempfile.mkdtemp()
        try:
            idx = SemanticIndex(temp_dir)
            ep = {
                "episode_id": "ep001",
                "summary": "テスト内容",
                "user_utterances": [],
                "tags": [],
                "timestamp": "2026-03-15T10:00:00Z",
                "session_id": "test",
                "episode_type": "observation",
            }
            idx.add_episode(ep)

            fts_results = idx.search("テスト内容")
            hybrid_results = idx.hybrid_search("テスト内容")

            self.assertEqual(len(fts_results), len(hybrid_results))
            if fts_results:
                self.assertEqual(
                    fts_results[0]["doc_id"], hybrid_results[0]["doc_id"]
                )
            idx.close()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestMEDFixVerification(unittest.TestCase):
    """Tests verifying MED issue fixes from code review."""

    def test_hybrid_merge_vector_only_returns_results(self):
        """Fix 1: hybrid_merge should return results when only vector results exist."""
        from vector_search import hybrid_merge
        vec = [("doc:a", 0.9), ("doc:b", 0.5)]
        result = hybrid_merge([], vec)
        self.assertGreater(len(result), 0)
        self.assertEqual(result[0]["doc_id"], "doc:a")
        self.assertGreater(result[0]["score"], result[1]["score"])
        # _vec_only marker should be present for caller enrichment
        self.assertTrue(result[0].get("_vec_only", False))

    def test_hybrid_search_vector_only_enriched(self):
        """Fix 1: hybrid_search should enrich vector-only results with metadata."""
        from embedding_provider import EmbeddingProvider

        temp_dir = tempfile.mkdtemp()
        try:
            idx = SemanticIndex(temp_dir)

            # Add episodes - one with term "alpha", one with term "beta"
            ep1 = {
                "episode_id": "ep_alpha",
                "summary": "alpha unique content",
                "user_utterances": [],
                "tags": ["tag1"],
                "timestamp": "2026-03-15T10:00:00Z",
                "session_id": "s1",
                "episode_type": "observation",
            }
            ep2 = {
                "episode_id": "ep_beta",
                "summary": "beta different content",
                "user_utterances": [],
                "tags": ["tag2"],
                "timestamp": "2026-03-15T11:00:00Z",
                "session_id": "s2",
                "episode_type": "decision",
            }
            idx.add_episode(ep1)
            idx.add_episode(ep2)

            class MockProvider(EmbeddingProvider):
                @property
                def provider_name(self): return "mock"
                @property
                def model_id(self): return "mock-model"
                @property
                def dimensions(self): return 4
                def embed_texts(self, texts):
                    results = []
                    for t in texts:
                        if "alpha" in t:
                            results.append([1.0, 0.0, 0.0, 0.0])
                        else:
                            results.append([0.0, 1.0, 0.0, 0.0])
                    return results

            idx._provider = MockProvider()
            idx._provider_initialized = True
            idx.sync_vectors()

            # Search with a query that FTS won't match but vector will
            # Use a term not in any document for FTS, but vector will match
            # "gamma" won't match FTS, but embed_single will return a vector
            # close to alpha's vector
            results = idx.hybrid_search("gamma")
            # FTS returns nothing for "gamma", vector search returns results
            # Because FTS returns empty, we need to verify vector-only path
            # Actually FTS returns empty for gamma, so merged = hybrid_merge([], vec_results)
            # which now returns vector-only results

            # For this test, the query vector for "gamma" will be [0.5, 0.5, 0, 0]
            # since it doesn't contain "alpha", so embed_texts returns [0, 1, 0, 0]
            # which is closest to ep_beta

            # But tokenize_for_query("gamma") returns a valid FTS query token
            # FTS won't find "gamma" in any document, so fts_results = []
            # Vector search will find results (closest to query vector)
            # hybrid_merge([], vec_results) now returns vector-only results
            # hybrid_search enriches them with metadata from documents table

            # The results should have metadata fields
            if results:
                r = results[0]
                self.assertIn("source_type", r)
                self.assertIn("original_text", r)
                self.assertIn("source_id", r)
                self.assertNotIn("_vec_only", r)

            idx.close()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_gemini_empty_embeddings_response(self):
        """Fix 2: GeminiProvider should handle empty embedding response gracefully."""
        from embedding_provider import GeminiProvider

        provider = GeminiProvider("fake-key")

        # Mock the client to return empty embeddings
        class MockClient:
            def embed_content(self, model, content):
                return {"embedding": []}

        provider._client = MockClient()

        result = provider.embed_texts(["test text"])
        self.assertEqual(result, [None])

    def test_openai_retry_fallthrough_return(self):
        """Fix 3: OpenAI _embed_batch_with_retry should return safely on loop completion."""
        from embedding_provider import OpenAIProvider, DEFAULT_MAX_RETRIES

        provider = OpenAIProvider("fake-key")

        # With DEFAULT_MAX_RETRIES > 0, the for loop always returns inside.
        # This tests the structural safety net. We verify the method exists
        # and returns correctly on error.
        class MockClient:
            def __init__(self):
                self.embeddings = self

            def create(self, model, input):
                raise RuntimeError("API error")

        provider._client = MockClient()
        result = provider._embed_batch_with_retry(MockClient(), ["test"])
        self.assertEqual(result, [None])

    def test_gemini_retry_fallthrough_return(self):
        """Fix 3: Gemini _embed_batch_with_retry should return safely on loop completion."""
        from embedding_provider import GeminiProvider

        provider = GeminiProvider("fake-key")

        class MockClient:
            def embed_content(self, model, content):
                raise RuntimeError("API error")

        result = provider._embed_batch_with_retry(MockClient(), ["test"])
        self.assertEqual(result, [None])

    def test_sqlite_vec_batch_rowid_lookup(self):
        """Fix 4: sqlite-vec search should use batch rowid->doc_id lookup."""
        from vector_search import (
            create_vector_tables, create_doc_map_table,
            store_vector, vector_search as vec_search,
            _check_sqlite_vec,
        )
        temp_dir = tempfile.mkdtemp()
        try:
            db = sqlite3.connect(os.path.join(temp_dir, "test.db"))
            use_vec = _check_sqlite_vec()
            if use_vec:
                from vector_search import _load_sqlite_vec
                _load_sqlite_vec(db)

            create_doc_map_table(db)
            create_vector_tables(db, 4, use_vec)

            # Store multiple vectors with distinct directions
            store_vector(db, "doc:0", [1.0, 0.0, 0.0, 0.0], "hash0", use_vec)
            store_vector(db, "doc:1", [0.0, 1.0, 0.0, 0.0], "hash1", use_vec)
            store_vector(db, "doc:2", [0.0, 0.0, 1.0, 0.0], "hash2", use_vec)
            store_vector(db, "doc:3", [0.0, 0.0, 0.0, 1.0], "hash3", use_vec)

            # Search should work with batch lookup (no N+1)
            results = vec_search(db, [1.0, 0.0, 0.0, 0.0], 4, 4, use_vec)
            self.assertGreater(len(results), 0)
            # First result should be doc:0 (exact match)
            self.assertEqual(results[0][0], "doc:0")
            db.close()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_get_all_vector_hashes(self):
        """Fix 5: get_all_vector_hashes should return all hashes in one query."""
        from vector_search import (
            create_vector_tables, create_doc_map_table,
            store_vector, get_all_vector_hashes,
            _check_sqlite_vec,
        )
        temp_dir = tempfile.mkdtemp()
        try:
            db = sqlite3.connect(os.path.join(temp_dir, "test.db"))
            use_vec = _check_sqlite_vec()
            if use_vec:
                from vector_search import _load_sqlite_vec
                _load_sqlite_vec(db)

            create_doc_map_table(db)
            create_vector_tables(db, 4, use_vec)

            store_vector(db, "doc:a", [1.0, 0.0, 0.0, 0.0], "hashA", use_vec)
            store_vector(db, "doc:b", [0.0, 1.0, 0.0, 0.0], "hashB", use_vec)

            hashes = get_all_vector_hashes(db, use_vec)
            self.assertEqual(len(hashes), 2)
            self.assertEqual(hashes["doc:a"], "hashA")
            self.assertEqual(hashes["doc:b"], "hashB")

            db.close()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_get_all_vector_hashes_empty(self):
        """Fix 5: get_all_vector_hashes on empty table returns empty dict."""
        from vector_search import (
            create_vector_tables, create_doc_map_table,
            get_all_vector_hashes, _check_sqlite_vec,
        )
        temp_dir = tempfile.mkdtemp()
        try:
            db = sqlite3.connect(os.path.join(temp_dir, "test.db"))
            use_vec = _check_sqlite_vec()
            if use_vec:
                from vector_search import _load_sqlite_vec
                _load_sqlite_vec(db)

            create_doc_map_table(db)
            create_vector_tables(db, 4, use_vec)

            hashes = get_all_vector_hashes(db, use_vec)
            self.assertEqual(hashes, {})

            db.close()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_get_all_vector_hashes_no_table(self):
        """Fix 5: get_all_vector_hashes returns empty dict when table doesn't exist."""
        from vector_search import get_all_vector_hashes
        temp_dir = tempfile.mkdtemp()
        try:
            db = sqlite3.connect(os.path.join(temp_dir, "test.db"))
            hashes = get_all_vector_hashes(db, True)
            self.assertEqual(hashes, {})
            hashes = get_all_vector_hashes(db, False)
            self.assertEqual(hashes, {})
            db.close()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_sync_vectors_uses_batch_hash_lookup(self):
        """Fix 5: sync_vectors should use batch hash lookup, not N+1 queries."""
        from embedding_provider import EmbeddingProvider

        temp_dir = tempfile.mkdtemp()
        try:
            idx = SemanticIndex(temp_dir)

            # Add multiple episodes
            for i in range(5):
                ep = {
                    "episode_id": f"ep{i:03d}",
                    "summary": f"Document number {i} content",
                    "user_utterances": [],
                    "tags": [],
                    "timestamp": "2026-03-15T10:00:00Z",
                    "session_id": "test",
                    "episode_type": "observation",
                }
                idx.add_episode(ep)

            class MockProvider(EmbeddingProvider):
                @property
                def provider_name(self): return "mock"
                @property
                def model_id(self): return "mock-model"
                @property
                def dimensions(self): return 4
                def embed_texts(self, texts):
                    return [[1.0, 0.0, 0.0, 0.0]] * len(texts)

            idx._provider = MockProvider()
            idx._provider_initialized = True

            # First sync: all 5 should be embedded
            count = idx.sync_vectors()
            self.assertEqual(count, 5)

            # Second sync: none should need re-embedding
            count = idx.sync_vectors()
            self.assertEqual(count, 0)

            idx.close()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)



# ========================================================================
# P3-6: Snippet generation and score breakdown tests
# ========================================================================


class TestExtractQueryTerms(unittest.TestCase):
    """Tests for extract_query_terms() — extracting match terms from query."""

    def test_simple_ascii_query(self):
        """ASCII query should split on whitespace and lowercase."""
        from semantic_index import extract_query_terms
        terms = extract_query_terms("Hello World")
        self.assertEqual(terms, ["hello", "world"])

    def test_japanese_query_splits_on_space(self):
        """Japanese query with spaces should split on spaces."""
        from semantic_index import extract_query_terms
        terms = extract_query_terms("感情 記憶")
        self.assertEqual(terms, ["感情", "記憶"])

    def test_japanese_query_no_space(self):
        """Japanese query without spaces should be kept as single term."""
        from semantic_index import extract_query_terms
        terms = extract_query_terms("感情記憶")
        self.assertEqual(terms, ["感情記憶"])

    def test_stop_words_removed(self):
        """Hiragana stop words should be removed."""
        from semantic_index import extract_query_terms
        terms = extract_query_terms("これ は テスト の 結果")
        # "は" and "の" are stop words
        self.assertNotIn("は", terms)
        self.assertNotIn("の", terms)
        self.assertIn("これ", terms)
        self.assertIn("テスト", terms)
        self.assertIn("結果", terms)

    def test_empty_query(self):
        """Empty query should return empty list."""
        from semantic_index import extract_query_terms
        terms = extract_query_terms("")
        self.assertEqual(terms, [])

    def test_mixed_script_query(self):
        """Mixed script query should split correctly."""
        from semantic_index import extract_query_terms
        terms = extract_query_terms("memory 検索")
        self.assertIn("memory", terms)
        self.assertIn("検索", terms)


class TestGenerateSnippet(unittest.TestCase):
    """Tests for generate_snippet() — extracting match context from text."""

    def test_simple_match(self):
        """Should find query term and surround with markers."""
        from semantic_index import generate_snippet
        text = "This is a long text with the word memory in the middle of it."
        snippet = generate_snippet(text, ["memory"])
        self.assertIn("<<memory>>", snippet)

    def test_context_window(self):
        """Snippet should include context around match."""
        from semantic_index import generate_snippet
        text = "A" * 100 + "TARGET" + "B" * 100
        snippet = generate_snippet(text, ["TARGET"], context_chars=30)
        self.assertIn("<<TARGET>>", snippet)
        # Should have context before and after
        self.assertTrue(len(snippet) < len(text))

    def test_no_match_returns_fallback(self):
        """When no term matches, should return None (caller does fallback)."""
        from semantic_index import generate_snippet
        text = "This text has nothing relevant."
        snippet = generate_snippet(text, ["xyz_not_found"])
        self.assertIsNone(snippet)

    def test_case_insensitive_match(self):
        """Match should be case-insensitive."""
        from semantic_index import generate_snippet
        text = "The Memory system works well."
        snippet = generate_snippet(text, ["memory"])
        self.assertIn("<<Memory>>", snippet)

    def test_japanese_match(self):
        """Should match Japanese query terms in text."""
        from semantic_index import generate_snippet
        text = "感情エンジンの設計では感情記憶の結合が重要です。"
        snippet = generate_snippet(text, ["感情記憶"])
        self.assertIn("<<感情記憶>>", snippet)

    def test_multiple_terms_first_match_only(self):
        """With multiple terms, should use first match found."""
        from semantic_index import generate_snippet
        text = "alpha beta gamma delta epsilon"
        snippet = generate_snippet(text, ["gamma", "alpha"])
        # Should contain at least one match marker
        self.assertTrue("<<gamma>>" in snippet or "<<alpha>>" in snippet)

    def test_snippet_length_limit(self):
        """Snippet should not exceed reasonable length."""
        from semantic_index import generate_snippet
        text = "X" * 1000 + "MATCH" + "Y" * 1000
        snippet = generate_snippet(text, ["MATCH"], context_chars=50)
        # Should be roughly 50+len(MATCH)+50 + markers + ellipsis
        self.assertTrue(len(snippet) < 200)

    def test_match_at_start(self):
        """Match at start of text should handle correctly."""
        from semantic_index import generate_snippet
        text = "memory is the key to everything in this system."
        snippet = generate_snippet(text, ["memory"])
        self.assertIn("<<memory>>", snippet)

    def test_match_at_end(self):
        """Match at end of text should handle correctly."""
        from semantic_index import generate_snippet
        text = "The key to everything is memory"
        snippet = generate_snippet(text, ["memory"])
        self.assertIn("<<memory>>", snippet)

    def test_empty_text(self):
        """Empty text should return None."""
        from semantic_index import generate_snippet
        snippet = generate_snippet("", ["test"])
        self.assertIsNone(snippet)

    def test_empty_terms(self):
        """Empty terms should return None."""
        from semantic_index import generate_snippet
        snippet = generate_snippet("some text", [])
        self.assertIsNone(snippet)


class TestHybridMergeScoreBreakdown(unittest.TestCase):
    """Tests for hybrid_merge score breakdown fields."""

    def test_both_fts_and_vec_have_breakdown(self):
        """When both FTS and vec results exist, breakdown should show both."""
        from vector_search import hybrid_merge
        fts_results = [
            {"doc_id": "doc1", "score": 0.5, "source_type": "episode"},
        ]
        vec_results = [("doc1", 0.8)]
        merged = hybrid_merge(fts_results, vec_results, 0.7, 0.3)
        self.assertEqual(len(merged), 1)
        result = merged[0]
        # Should have score breakdown fields
        self.assertIn("fts_raw_score", result)
        self.assertIn("vec_raw_score", result)
        self.assertIn("fts_weight", result)
        self.assertIn("vec_weight", result)
        self.assertAlmostEqual(result["fts_raw_score"], 0.5)
        self.assertAlmostEqual(result["vec_raw_score"], 0.8)
        self.assertAlmostEqual(result["fts_weight"], 0.3)
        self.assertAlmostEqual(result["vec_weight"], 0.7)

    def test_fts_only_result_breakdown(self):
        """FTS-only result should show FTS score and None for vec."""
        from vector_search import hybrid_merge
        fts_results = [
            {"doc_id": "doc1", "score": 0.6, "source_type": "episode"},
        ]
        vec_results = [("doc2", 0.9)]  # Different doc
        merged = hybrid_merge(fts_results, vec_results, 0.7, 0.3)
        doc1_result = [r for r in merged if r["doc_id"] == "doc1"][0]
        self.assertAlmostEqual(doc1_result["fts_raw_score"], 0.6)
        self.assertIsNone(doc1_result["vec_raw_score"])

    def test_vec_only_result_breakdown(self):
        """Vec-only result should show vec score and None for FTS."""
        from vector_search import hybrid_merge
        fts_results = [
            {"doc_id": "doc1", "score": 0.5, "source_type": "episode"},
        ]
        vec_results = [("doc2", 0.9)]  # Different doc
        merged = hybrid_merge(fts_results, vec_results, 0.7, 0.3)
        # doc2 should be _vec_only but should still have breakdown
        # Note: doc2 has _vec_only flag, so it may not have all fields
        # but it should have the vec score breakdown
        doc2_results = [r for r in merged if r["doc_id"] == "doc2"]
        if doc2_results:
            self.assertIsNone(doc2_results[0].get("fts_raw_score"))
            self.assertAlmostEqual(doc2_results[0]["vec_raw_score"], 0.9)

    def test_no_vec_results_no_breakdown(self):
        """When no vec results, FTS results should pass through unchanged."""
        from vector_search import hybrid_merge
        fts_results = [
            {"doc_id": "doc1", "score": 0.5, "source_type": "episode"},
        ]
        merged = hybrid_merge(fts_results, [], 0.7, 0.3)
        self.assertEqual(len(merged), 1)
        # Should be identical to input
        self.assertEqual(merged[0]["doc_id"], "doc1")

    def test_breakdown_preserves_existing_fields(self):
        """Score breakdown should not overwrite existing result fields."""
        from vector_search import hybrid_merge
        fts_results = [
            {"doc_id": "doc1", "score": 0.5, "source_type": "episode",
             "original_text": "test text", "timestamp": "2026-01-01"},
        ]
        vec_results = [("doc1", 0.8)]
        merged = hybrid_merge(fts_results, vec_results, 0.7, 0.3)
        result = merged[0]
        self.assertEqual(result["source_type"], "episode")
        self.assertEqual(result["original_text"], "test text")
        self.assertEqual(result["timestamp"], "2026-01-01")


class TestFormatScoreBreakdown(unittest.TestCase):
    """Tests for format_score_breakdown() output formatting."""

    def test_hybrid_format(self):
        """Hybrid result should show both FTS and Vec components."""
        from semantic_index import format_score_breakdown
        result = {
            "score": 0.82,
            "fts_raw_score": 0.35,
            "vec_raw_score": 0.98,
            "fts_weight": 0.3,
            "vec_weight": 0.7,
        }
        formatted = format_score_breakdown(result)
        # Components are labelled "raw" to clarify they are pre-normalization
        self.assertIn("raw FTS:", formatted)
        self.assertIn("raw Vec:", formatted)
        self.assertIn("0.82", formatted)

    def test_fts_only_format(self):
        """FTS-only result should show only FTS component."""
        from semantic_index import format_score_breakdown
        result = {
            "score": 0.65,
            "fts_raw_score": 0.65,
            "vec_raw_score": None,
            "fts_weight": 0.3,
            "vec_weight": 0.7,
        }
        formatted = format_score_breakdown(result)
        self.assertIn("raw FTS:", formatted)
        self.assertNotIn("raw Vec:", formatted)

    def test_no_breakdown_fields(self):
        """Result without breakdown fields should show score only."""
        from semantic_index import format_score_breakdown
        result = {"score": 0.75}
        formatted = format_score_breakdown(result)
        self.assertIn("0.75", formatted)

    def test_vec_only_format(self):
        """Vec-only result should show only Vec component."""
        from semantic_index import format_score_breakdown
        result = {
            "score": 0.63,
            "fts_raw_score": None,
            "vec_raw_score": 0.9,
            "fts_weight": 0.3,
            "vec_weight": 0.7,
        }
        formatted = format_score_breakdown(result)
        self.assertNotIn("raw FTS:", formatted)
        self.assertIn("raw Vec:", formatted)


# ========================================================================
# P3-2: Temporal decay tests
# ========================================================================


class TestApplyTemporalDecay(unittest.TestCase):
    """Tests for apply_temporal_decay() — time-based score adjustment."""

    def test_recent_episode_no_decay(self):
        """Episode created just now should have decay_factor ~1.0."""
        from vector_search import apply_temporal_decay
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        results = [
            {"doc_id": "episode:ep001", "score": 0.8, "source_type": "episode",
             "timestamp": now.isoformat()},
        ]
        decayed = apply_temporal_decay(results, now=now)
        self.assertEqual(len(decayed), 1)
        self.assertAlmostEqual(decayed[0]["decay_factor"], 1.0, places=2)
        self.assertAlmostEqual(decayed[0]["score"], 0.8, places=2)

    def test_old_episode_decays(self):
        """Episode from 60 days ago should have decay_factor < 1.0."""
        from vector_search import apply_temporal_decay
        from datetime import datetime, timezone, timedelta

        now = datetime.now(timezone.utc)
        old_time = now - timedelta(days=60)
        results = [
            {"doc_id": "episode:ep001", "score": 0.8, "source_type": "episode",
             "timestamp": old_time.isoformat()},
        ]
        decayed = apply_temporal_decay(results, now=now)
        self.assertLess(decayed[0]["decay_factor"], 1.0)
        self.assertLess(decayed[0]["score"], 0.8)

    def test_lesson_not_decayed(self):
        """Lessons should never have decay applied."""
        from vector_search import apply_temporal_decay
        from datetime import datetime, timezone, timedelta

        now = datetime.now(timezone.utc)
        old_time = now - timedelta(days=365)
        results = [
            {"doc_id": "lesson:001", "score": 0.9, "source_type": "lesson",
             "timestamp": old_time.isoformat()},
        ]
        decayed = apply_temporal_decay(results, now=now)
        self.assertAlmostEqual(decayed[0]["score"], 0.9)
        self.assertNotIn("decay_factor", decayed[0])

    def test_decay_floor(self):
        """Very old episode should not decay below floor value."""
        from vector_search import apply_temporal_decay
        from datetime import datetime, timezone, timedelta

        now = datetime.now(timezone.utc)
        very_old = now - timedelta(days=3650)  # 10 years
        results = [
            {"doc_id": "episode:ep001", "score": 1.0, "source_type": "episode",
             "timestamp": very_old.isoformat()},
        ]
        decayed = apply_temporal_decay(results, floor=0.3, now=now)
        self.assertGreaterEqual(decayed[0]["decay_factor"], 0.3)
        self.assertGreaterEqual(decayed[0]["score"], 0.3)

    def test_custom_half_life(self):
        """Custom half_life_days should control decay rate."""
        from vector_search import apply_temporal_decay
        from datetime import datetime, timezone, timedelta

        now = datetime.now(timezone.utc)
        half_life = 30.0
        results = [
            {"doc_id": "episode:ep001", "score": 1.0, "source_type": "episode",
             "timestamp": (now - timedelta(days=30)).isoformat()},
        ]
        decayed = apply_temporal_decay(results, half_life_days=half_life, now=now)
        # At exactly one half-life, factor should be ~0.5
        self.assertAlmostEqual(decayed[0]["decay_factor"], 0.5, places=1)

    def test_missing_timestamp_no_decay(self):
        """Episode with no timestamp should get decay_factor 1.0."""
        from vector_search import apply_temporal_decay
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        results = [
            {"doc_id": "episode:ep001", "score": 0.7, "source_type": "episode",
             "timestamp": ""},
        ]
        decayed = apply_temporal_decay(results, now=now)
        self.assertAlmostEqual(decayed[0]["decay_factor"], 1.0)
        self.assertAlmostEqual(decayed[0]["score"], 0.7)

    def test_null_timestamp_no_decay(self):
        """Episode with None timestamp should get decay_factor 1.0."""
        from vector_search import apply_temporal_decay
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        results = [
            {"doc_id": "episode:ep001", "score": 0.7, "source_type": "episode",
             "timestamp": None},
        ]
        decayed = apply_temporal_decay(results, now=now)
        self.assertAlmostEqual(decayed[0]["decay_factor"], 1.0)

    def test_invalid_timestamp_no_decay(self):
        """Episode with unparseable timestamp should get decay_factor 1.0."""
        from vector_search import apply_temporal_decay
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        results = [
            {"doc_id": "episode:ep001", "score": 0.7, "source_type": "episode",
             "timestamp": "not-a-date"},
        ]
        decayed = apply_temporal_decay(results, now=now)
        self.assertAlmostEqual(decayed[0]["decay_factor"], 1.0)

    def test_empty_results(self):
        """Empty result list should return empty list."""
        from vector_search import apply_temporal_decay
        decayed = apply_temporal_decay([])
        self.assertEqual(decayed, [])

    def test_mixed_episode_lesson(self):
        """Mixed results: only episodes should be decayed."""
        from vector_search import apply_temporal_decay
        from datetime import datetime, timezone, timedelta

        now = datetime.now(timezone.utc)
        old_time = now - timedelta(days=60)
        results = [
            {"doc_id": "episode:ep001", "score": 0.8, "source_type": "episode",
             "timestamp": old_time.isoformat()},
            {"doc_id": "lesson:001", "score": 0.8, "source_type": "lesson",
             "timestamp": old_time.isoformat()},
        ]
        decayed = apply_temporal_decay(results, now=now)
        ep = [r for r in decayed if r["source_type"] == "episode"][0]
        ls = [r for r in decayed if r["source_type"] == "lesson"][0]
        self.assertLess(ep["score"], 0.8)
        self.assertAlmostEqual(ls["score"], 0.8)
        self.assertIn("decay_factor", ep)
        self.assertNotIn("decay_factor", ls)

    def test_results_resorted_after_decay(self):
        """Results should be re-sorted by score after decay."""
        from vector_search import apply_temporal_decay
        from datetime import datetime, timezone, timedelta

        now = datetime.now(timezone.utc)
        results = [
            {"doc_id": "episode:ep001", "score": 0.9, "source_type": "episode",
             "timestamp": (now - timedelta(days=120)).isoformat()},
            {"doc_id": "lesson:001", "score": 0.5, "source_type": "lesson",
             "timestamp": (now - timedelta(days=120)).isoformat()},
        ]
        decayed = apply_temporal_decay(results, now=now)
        # Lesson should now be ranked higher (no decay) than heavily decayed episode
        scores = [r["score"] for r in decayed]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_decay_disabled(self):
        """When enabled=False, no decay should be applied."""
        from vector_search import apply_temporal_decay
        from datetime import datetime, timezone, timedelta

        now = datetime.now(timezone.utc)
        old_time = now - timedelta(days=365)
        results = [
            {"doc_id": "episode:ep001", "score": 0.8, "source_type": "episode",
             "timestamp": old_time.isoformat()},
        ]
        decayed = apply_temporal_decay(results, enabled=False, now=now)
        self.assertAlmostEqual(decayed[0]["score"], 0.8)
        self.assertNotIn("decay_factor", decayed[0])


# ========================================================================
# P3-9: Type-based weight tests
# ========================================================================


class TestApplyTypeWeights(unittest.TestCase):
    """Tests for apply_type_weights() — source_type-specific vec/fts weight adjustment."""

    def test_episode_gets_vector_heavy_weights(self):
        """Episodes should get vector-heavy weight distribution."""
        from vector_search import apply_type_weights, _min_max_normalize

        fts_results = [
            {"doc_id": "episode:ep001", "score": 0.5, "source_type": "episode"},
        ]
        vec_results = [("episode:ep001", 0.9)]
        type_weights = {
            "episode": {"vector_weight": 0.8, "fts_weight": 0.2},
            "lesson": {"vector_weight": 0.3, "fts_weight": 0.7},
        }
        results = apply_type_weights(fts_results, vec_results, type_weights)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["type_weights"], {"vector_weight": 0.8, "fts_weight": 0.2})

    def test_lesson_gets_fts_heavy_weights(self):
        """Lessons should get FTS-heavy weight distribution."""
        from vector_search import apply_type_weights

        fts_results = [
            {"doc_id": "lesson:001", "score": 0.5, "source_type": "lesson"},
        ]
        vec_results = [("lesson:001", 0.9)]
        type_weights = {
            "episode": {"vector_weight": 0.8, "fts_weight": 0.2},
            "lesson": {"vector_weight": 0.3, "fts_weight": 0.7},
        }
        results = apply_type_weights(fts_results, vec_results, type_weights)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["type_weights"], {"vector_weight": 0.3, "fts_weight": 0.7})

    def test_mixed_types_different_weights(self):
        """Episode and lesson in same search should get different weights."""
        from vector_search import apply_type_weights

        fts_results = [
            {"doc_id": "episode:ep001", "score": 0.6, "source_type": "episode"},
            {"doc_id": "lesson:001", "score": 0.6, "source_type": "lesson"},
        ]
        vec_results = [("episode:ep001", 0.6), ("lesson:001", 0.6)]
        type_weights = {
            "episode": {"vector_weight": 0.8, "fts_weight": 0.2},
            "lesson": {"vector_weight": 0.3, "fts_weight": 0.7},
        }
        results = apply_type_weights(fts_results, vec_results, type_weights)
        ep = [r for r in results if r["source_type"] == "episode"][0]
        ls = [r for r in results if r["source_type"] == "lesson"][0]
        # Different type_weights should be recorded
        self.assertEqual(ep["type_weights"]["vector_weight"], 0.8)
        self.assertEqual(ls["type_weights"]["vector_weight"], 0.3)

    def test_unknown_source_type_gets_default(self):
        """Unknown source_type should get default weights (0.7/0.3)."""
        from vector_search import apply_type_weights, DEFAULT_VECTOR_WEIGHT, DEFAULT_FTS_WEIGHT

        fts_results = [
            {"doc_id": "unknown:001", "score": 0.5, "source_type": "unknown"},
        ]
        vec_results = [("unknown:001", 0.8)]
        type_weights = {
            "episode": {"vector_weight": 0.8, "fts_weight": 0.2},
            "lesson": {"vector_weight": 0.3, "fts_weight": 0.7},
        }
        results = apply_type_weights(fts_results, vec_results, type_weights)
        r = results[0]
        self.assertEqual(r["type_weights"]["vector_weight"], DEFAULT_VECTOR_WEIGHT)
        self.assertEqual(r["type_weights"]["fts_weight"], DEFAULT_FTS_WEIGHT)

    def test_normalization_is_unified(self):
        """Min-max normalization should be across ALL types, not per-type.

        This is the MED #1 fix: if normalization were per-type,
        a low-scoring lesson could be inflated to match high-scoring episodes.
        """
        from vector_search import apply_type_weights

        fts_results = [
            {"doc_id": "episode:ep001", "score": 0.8, "source_type": "episode"},
            {"doc_id": "episode:ep002", "score": 0.6, "source_type": "episode"},
            {"doc_id": "lesson:001", "score": 0.3, "source_type": "lesson"},
            {"doc_id": "lesson:002", "score": 0.2, "source_type": "lesson"},
        ]
        vec_results = [
            ("episode:ep001", 0.9),
            ("episode:ep002", 0.7),
            ("lesson:001", 0.3),
            ("lesson:002", 0.2),
        ]
        type_weights = {
            "episode": {"vector_weight": 0.7, "fts_weight": 0.3},
            "lesson": {"vector_weight": 0.3, "fts_weight": 0.7},
        }
        results = apply_type_weights(fts_results, vec_results, type_weights)
        # Lesson:001 (raw fts=0.3) should NOT have a score >= episode:ep001 (raw fts=0.8)
        ep1 = [r for r in results if r["doc_id"] == "episode:ep001"][0]
        ls1 = [r for r in results if r["doc_id"] == "lesson:001"][0]
        self.assertGreater(ep1["score"], ls1["score"])

    def test_fts_only_no_vec(self):
        """When no vector results, apply_type_weights should handle gracefully."""
        from vector_search import apply_type_weights

        fts_results = [
            {"doc_id": "episode:ep001", "score": 0.5, "source_type": "episode"},
        ]
        vec_results = []
        type_weights = {
            "episode": {"vector_weight": 0.8, "fts_weight": 0.2},
        }
        results = apply_type_weights(fts_results, vec_results, type_weights)
        self.assertEqual(len(results), 1)

    def test_vec_only_no_fts(self):
        """When no FTS results, apply_type_weights should handle gracefully."""
        from vector_search import apply_type_weights

        fts_results = []
        vec_results = [("episode:ep001", 0.8)]
        type_weights = {
            "episode": {"vector_weight": 0.8, "fts_weight": 0.2},
        }
        results = apply_type_weights(fts_results, vec_results, type_weights)
        self.assertEqual(len(results), 1)

    def test_empty_results(self):
        """Empty inputs should return empty list."""
        from vector_search import apply_type_weights
        results = apply_type_weights([], [], {})
        self.assertEqual(results, [])

    def test_preserves_metadata_fields(self):
        """Existing metadata fields should be preserved in results."""
        from vector_search import apply_type_weights

        fts_results = [
            {"doc_id": "episode:ep001", "score": 0.5, "source_type": "episode",
             "original_text": "test text", "timestamp": "2026-01-01"},
        ]
        vec_results = [("episode:ep001", 0.8)]
        type_weights = {
            "episode": {"vector_weight": 0.7, "fts_weight": 0.3},
        }
        results = apply_type_weights(fts_results, vec_results, type_weights)
        r = results[0]
        self.assertEqual(r["original_text"], "test text")
        self.assertEqual(r["timestamp"], "2026-01-01")

    def test_results_sorted_by_score(self):
        """Results should be sorted by adjusted score descending."""
        from vector_search import apply_type_weights

        fts_results = [
            {"doc_id": "episode:ep001", "score": 0.3, "source_type": "episode"},
            {"doc_id": "episode:ep002", "score": 0.8, "source_type": "episode"},
        ]
        vec_results = [("episode:ep001", 0.3), ("episode:ep002", 0.9)]
        type_weights = {
            "episode": {"vector_weight": 0.7, "fts_weight": 0.3},
        }
        results = apply_type_weights(fts_results, vec_results, type_weights)
        scores = [r["score"] for r in results]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_raw_scores_preserved(self):
        """fts_raw_score and vec_raw_score should be preserved."""
        from vector_search import apply_type_weights

        fts_results = [
            {"doc_id": "episode:ep001", "score": 0.5, "source_type": "episode"},
        ]
        vec_results = [("episode:ep001", 0.8)]
        type_weights = {
            "episode": {"vector_weight": 0.7, "fts_weight": 0.3},
        }
        results = apply_type_weights(fts_results, vec_results, type_weights)
        r = results[0]
        self.assertIn("fts_raw_score", r)
        self.assertIn("vec_raw_score", r)
        self.assertAlmostEqual(r["fts_raw_score"], 0.5)
        self.assertAlmostEqual(r["vec_raw_score"], 0.8)


# ========================================================================
# P3-2+P3-9: Integration in hybrid_search
# ========================================================================


class TestHybridSearchTypeWeightsIntegration(unittest.TestCase):
    """Tests for hybrid_search integration of type weights and temporal decay."""

    def test_explicit_weights_disable_type_weights(self):
        """When vector_weight/fts_weight are explicitly passed, type weights are disabled."""
        # This tests the backward compatibility path from design §4.3
        temp_dir = tempfile.mkdtemp()
        try:
            idx = SemanticIndex(temp_dir)
            # Add an episode
            ep = {
                "episode_id": "ep001",
                "summary": "test content for search",
                "user_utterances": [],
                "tags": [],
                "timestamp": "2026-03-15T10:00:00Z",
                "session_id": "test",
                "episode_type": "observation",
            }
            idx.add_episode(ep)
            # FTS-only search with explicit weights — should not error
            results = idx.hybrid_search(
                "test content", vector_weight=0.5, fts_weight=0.5
            )
            self.assertGreater(len(results), 0)
            idx.close()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_decay_disabled_param(self):
        """hybrid_search with temporal_decay=False should not apply decay."""
        temp_dir = tempfile.mkdtemp()
        try:
            idx = SemanticIndex(temp_dir)
            ep = {
                "episode_id": "ep001",
                "summary": "test content for searching",
                "user_utterances": [],
                "tags": [],
                "timestamp": "2020-01-01T00:00:00Z",
                "session_id": "test",
                "episode_type": "observation",
            }
            idx.add_episode(ep)
            results = idx.hybrid_search("test content", temporal_decay=False)
            for r in results:
                self.assertNotIn("decay_factor", r)
            idx.close()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)




# ═══════════════════════════════════════════════════════════════
# C7: FTS5 Pagination (offset parameter)
# ═══════════════════════════════════════════════════════════════


class TestFTS5Pagination(unittest.TestCase):
    """Test pagination support in SemanticIndex.search()."""

    def test_search_accepts_offset_parameter(self):
        """search() should accept an offset parameter."""
        import tempfile, shutil
        temp_dir = tempfile.mkdtemp()
        try:
            idx = SemanticIndex(temp_dir)
            # Add some episodes
            for i in range(5):
                ep = {
                    "episode_id": f"ep-{i}",
                    "session_id": "s1",
                    "episode_type": "observation",
                    "timestamp": f"2026-03-20T{10+i}:00:00Z",
                    "summary": f"test content number {i} about searching",
                    "user_utterances": [],
                    "tags": ["test"],
                }
                idx.add_episode(ep)

            # Search with limit only
            results_all = idx.search("test content", limit=10)
            # Search with offset
            results_offset = idx.search("test content", limit=10, offset=2)
            # Offset results should be a subset
            self.assertLessEqual(len(results_offset), len(results_all))
            idx.close()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_search_offset_skips_results(self):
        """search() with offset should skip the first N results."""
        import tempfile, shutil
        temp_dir = tempfile.mkdtemp()
        try:
            idx = SemanticIndex(temp_dir)
            for i in range(10):
                ep = {
                    "episode_id": f"ep-{i}",
                    "session_id": "s1",
                    "episode_type": "observation",
                    "timestamp": f"2026-03-20T{10+i}:00:00Z",
                    "summary": f"common search term number {i}",
                    "user_utterances": [],
                    "tags": [],
                }
                idx.add_episode(ep)

            all_results = idx.search("common search term", limit=10)
            offset_results = idx.search("common search term", limit=10, offset=3)
            # offset=3 should skip first 3 results
            if len(all_results) > 3:
                self.assertEqual(len(offset_results), len(all_results) - 3)
                # First result after offset should match 4th original result
                self.assertEqual(offset_results[0]["doc_id"], all_results[3]["doc_id"])
            idx.close()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_search_offset_zero_same_as_no_offset(self):
        """search() with offset=0 should return same as no offset."""
        import tempfile, shutil
        temp_dir = tempfile.mkdtemp()
        try:
            idx = SemanticIndex(temp_dir)
            ep = {
                "episode_id": "ep-1",
                "session_id": "s1",
                "episode_type": "observation",
                "timestamp": "2026-03-20T10:00:00Z",
                "summary": "unique search test",
                "user_utterances": [],
                "tags": [],
            }
            idx.add_episode(ep)

            r1 = idx.search("unique search test", limit=10)
            r2 = idx.search("unique search test", limit=10, offset=0)
            self.assertEqual(len(r1), len(r2))
            idx.close()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestHybridSearchEmbedTimeout(unittest.TestCase):
    """Tests for embed_single timeout guard in hybrid_search."""

    def _make_index_with_fts_data(self):
        """Create a SemanticIndex with FTS data and mock Phase 2 available."""
        temp_dir = tempfile.mkdtemp()
        idx = SemanticIndex(temp_dir)
        ep = {
            "episode_id": "ep-timeout-test",
            "timestamp": "2026-01-01T00:00:00+09:00",
            "episode_type": "action",
            "summary": "timeout guard test episode",
            "user_utterances": [{"text": "test query for embed timeout"}],
            "tags": ["test"],
        }
        idx.add_episode(ep)
        return idx, temp_dir

    @patch("semantic_index._PHASE2_AVAILABLE", True)
    @patch("semantic_index.get_vector_count", return_value=10)
    def test_slow_embed_single_falls_back_to_fts(self, mock_vec_count):
        """When embed_single blocks beyond timeout, hybrid_search should
        return FTS-only results within a reasonable time (not hang 90s+)."""
        import time as time_mod

        idx, temp_dir = self._make_index_with_fts_data()
        try:
            # Enable vector search path
            idx._vector_enabled = True

            # Mock provider with a slow embed_single (10 seconds)
            mock_provider = unittest.mock.MagicMock()
            def slow_embed(q):
                time_mod.sleep(10)
                return [0.1] * 256
            mock_provider.embed_single.side_effect = slow_embed
            idx._provider = mock_provider
            idx._get_provider = lambda: mock_provider

            # Patch timeout to 2s for fast test (default is 60s)
            import semantic_index
            original_fn = semantic_index._embed_with_timeout
            def patched_embed_with_timeout(prov, text, timeout=2.0):
                return original_fn(prov, text, timeout=2.0)

            start = time_mod.time()
            with patch("semantic_index._embed_with_timeout", side_effect=patched_embed_with_timeout):
                results = idx.hybrid_search("timeout guard test episode", limit=10)
            elapsed = time_mod.time() - start

            # Must return within 5 seconds (timeout is 2s + overhead)
            self.assertLess(elapsed, 5.0, f"hybrid_search took {elapsed:.1f}s, expected < 5s")
            # Should still return FTS results
            self.assertGreater(len(results), 0, "Should return FTS-only results on timeout")
        finally:
            idx.close()
            shutil.rmtree(temp_dir, ignore_errors=True)

    @patch("semantic_index._PHASE2_AVAILABLE", True)
    @patch("semantic_index.get_vector_count", return_value=10)
    def test_fast_embed_single_uses_vectors(self, mock_vec_count):
        """When embed_single is fast, it should be called and its result used."""
        idx, temp_dir = self._make_index_with_fts_data()
        try:
            # Enable vector search path
            idx._vector_enabled = True

            mock_provider = unittest.mock.MagicMock()
            mock_provider.embed_single.return_value = [0.1] * 256
            mock_provider.dimensions = 256
            idx._provider = mock_provider
            idx._get_provider = lambda: mock_provider

            # hybrid_search should call embed_single
            results = idx.hybrid_search("timeout guard test episode", limit=10)
            mock_provider.embed_single.assert_called_once()
        finally:
            idx.close()
            shutil.rmtree(temp_dir, ignore_errors=True)


    @patch("semantic_index._PHASE2_AVAILABLE", True)
    @patch("semantic_index.get_vector_count", return_value=10)
    def test_embed_single_exception_falls_back_to_fts(self, mock_vec_count):
        """When embed_single raises an exception, hybrid_search should
        return FTS-only results (query_vec stays None)."""
        idx, temp_dir = self._make_index_with_fts_data()
        try:
            idx._vector_enabled = True

            mock_provider = unittest.mock.MagicMock()
            mock_provider.embed_single.side_effect = RuntimeError("API connection refused")
            idx._provider = mock_provider
            idx._get_provider = lambda: mock_provider

            results = idx.hybrid_search("timeout guard test episode", limit=10)
            # Should return FTS results despite embed exception
            self.assertGreater(len(results), 0, "Should return FTS-only results on exception")
        finally:
            idx.close()
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
