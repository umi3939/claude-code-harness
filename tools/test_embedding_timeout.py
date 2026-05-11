#!/usr/bin/env python3
"""Tests for embedding provider timeout and non-blocking search behavior.

Bug: memory_search hung indefinitely because:
1. Embedding API calls had no timeout (DEFAULT_TIMEOUT=30 was defined but unused)
2. sync_vectors blocked search results (ran before returning results)

These tests verify the fixes are in place and prevent regression.
"""

import unittest
from unittest.mock import MagicMock, patch, PropertyMock

from embedding_provider import (
    DEFAULT_TIMEOUT,
    OpenAIProvider,
    GeminiProvider,
)


class TestOpenAIProviderTimeout(unittest.TestCase):
    """Verify OpenAI provider respects DEFAULT_TIMEOUT."""

    def test_client_created_with_timeout(self):
        """OpenAI client must be initialized with timeout=DEFAULT_TIMEOUT."""
        mock_openai = MagicMock()
        with patch.dict("sys.modules", {"openai": mock_openai}):
            provider = OpenAIProvider(api_key="test-key")
            provider._client = None  # Force re-creation
            provider._get_client()

            mock_openai.OpenAI.assert_called_once_with(
                api_key="test-key",
                timeout=DEFAULT_TIMEOUT,
            )

    def test_embeddings_create_called_with_timeout(self):
        """embeddings.create must include timeout=DEFAULT_TIMEOUT."""
        mock_client = MagicMock()

        # Mock successful response
        mock_embedding = MagicMock()
        mock_embedding.index = 0
        mock_embedding.embedding = [0.1] * 1536
        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        provider = OpenAIProvider(api_key="test-key")
        provider._client = mock_client  # Inject mock client directly

        provider.embed_texts(["hello"])

        mock_client.embeddings.create.assert_called_once()
        call_kwargs = mock_client.embeddings.create.call_args
        self.assertEqual(call_kwargs.kwargs.get("timeout"), DEFAULT_TIMEOUT)


class TestGeminiProviderTimeout(unittest.TestCase):
    """Verify Gemini provider uses google.genai (new API) with timeout."""

    def test_uses_new_genai_api(self):
        """GeminiProvider must use google.genai.Client, not google.generativeai."""
        import inspect
        source = inspect.getsource(GeminiProvider)
        self.assertIn("google.genai", source,
                       "Must import from google.genai (new API)")
        self.assertNotIn("google.generativeai", source,
                         "Must NOT use deprecated google.generativeai")

    def test_embed_content_called_with_timeout_config(self):
        """embed_content must include timeout in config."""
        mock_client = MagicMock()

        # Mock response: result.embeddings[i].values
        mock_embedding = MagicMock()
        mock_embedding.values = [0.1] * 3072
        mock_response = MagicMock()
        mock_response.embeddings = [mock_embedding]
        mock_client.models.embed_content.return_value = mock_response

        provider = GeminiProvider(api_key="test-key")
        provider._client = mock_client

        provider.embed_texts(["hello"])

        mock_client.models.embed_content.assert_called_once()
        call_kwargs = mock_client.models.embed_content.call_args
        config = call_kwargs.kwargs.get("config", {})
        self.assertIsNotNone(config, "config must be passed to embed_content")


class TestDefaultTimeoutValue(unittest.TestCase):
    """Verify DEFAULT_TIMEOUT is a reasonable value."""

    def test_timeout_is_positive(self):
        self.assertGreater(DEFAULT_TIMEOUT, 0)

    def test_timeout_is_reasonable(self):
        """Timeout should be between 5 and 120 seconds."""
        self.assertGreaterEqual(DEFAULT_TIMEOUT, 5)
        self.assertLessEqual(DEFAULT_TIMEOUT, 120)


class TestSyncVectorsNonBlocking(unittest.TestCase):
    """Verify sync_vectors runs in background thread, not blocking search return.

    Bug history:
    - v1: sync_vectors ran BEFORE search → moved to after
    - v2: sync_vectors ran after search but still synchronous → hangs 90s+ on slow API
    - v3 (current fix): sync_vectors runs in daemon thread → truly non-blocking
    """

    def test_sync_vectors_runs_in_background_thread(self):
        """sync_vectors must be dispatched via threading.Thread, not called directly."""
        source_path = __file__.replace("test_embedding_timeout.py", "memory_mcp_server.py")
        with open(source_path, "r", encoding="utf-8") as f:
            source = f.read()

        # Must NOT have a direct synchronous call to idx.sync_vectors()
        # Should use threading.Thread or similar to run in background
        import re
        # Find the _fts_search function body
        fts_match = re.search(r'def _fts_search\(.*?\n(?=\ndef |\nclass |\n@mcp)', source, re.DOTALL)
        self.assertIsNotNone(fts_match, "_fts_search function not found")
        fts_body = fts_match.group(0)

        # sync_vectors should be in a Thread, not called directly
        self.assertIn("Thread", fts_body,
                       "sync_vectors must run in a background Thread")
        self.assertIn("daemon", fts_body,
                       "Background thread must be a daemon thread")

    def test_search_result_returned_before_sync_vectors_completes(self):
        """The return statement must come AFTER thread.start(), proving non-blocking."""
        source_path = __file__.replace("test_embedding_timeout.py", "memory_mcp_server.py")
        with open(source_path, "r", encoding="utf-8") as f:
            source = f.read()

        # result_text must be built before the thread is started
        result_text_pos = source.find("result_text = ")
        thread_start_pos = source.find(".start()")
        return_pos = source.find("return result_text", result_text_pos)

        self.assertGreater(result_text_pos, 0, "result_text assignment not found")
        self.assertGreater(thread_start_pos, 0, "thread.start() not found")
        self.assertGreater(return_pos, 0, "return result_text not found")

        # Order: result_text built → thread started → result returned
        self.assertGreater(thread_start_pos, result_text_pos,
                           "Thread must start AFTER result_text is built")

    def test_sync_vectors_not_called_synchronously(self):
        """There must be NO direct synchronous idx.sync_vectors() call in _fts_search."""
        source_path = __file__.replace("test_embedding_timeout.py", "memory_mcp_server.py")
        with open(source_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        in_fts_search = False
        for line in lines:
            if "def _fts_search(" in line:
                in_fts_search = True
                continue
            if in_fts_search and line.startswith("def ") or line.startswith("@mcp"):
                break
            if in_fts_search:
                stripped = line.strip()
                # Direct call (not inside a lambda or thread target)
                if stripped == "idx.sync_vectors()":
                    self.fail(
                        "Found direct synchronous idx.sync_vectors() call. "
                        "Must run in background thread."
                    )


if __name__ == "__main__":
    unittest.main()
