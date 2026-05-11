"""Tests for C6: Rate Limiting in embedding_provider.py"""
import os
import sys
import time
import threading
import unittest
from unittest.mock import patch, MagicMock

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)


class TestRateLimiting(unittest.TestCase):
    """Verify token bucket rate limiting on embedding API calls."""

    def test_rate_limiter_exists(self):
        """Module should have a rate limiter."""
        from embedding_provider import TokenBucketRateLimiter
        limiter = TokenBucketRateLimiter(max_concurrent=2, refill_rate=10.0)
        self.assertIsNotNone(limiter)

    def test_rate_limiter_limits_concurrent(self):
        """Rate limiter should limit concurrent requests."""
        from embedding_provider import TokenBucketRateLimiter
        limiter = TokenBucketRateLimiter(max_concurrent=1, refill_rate=100.0)

        acquired = []
        blocked = []

        def try_acquire(result_list):
            got = limiter.acquire(timeout=0.1)
            result_list.append(got)

        # First acquire should succeed
        assert limiter.acquire(timeout=1.0) is True

        # Second acquire (while first held) should block/fail with short timeout
        t = threading.Thread(target=try_acquire, args=(blocked,))
        t.start()
        t.join()

        # Release the first
        limiter.release()
        assert blocked[0] is False  # Should have timed out

    def test_rate_limiter_releases_properly(self):
        """After release, next acquire should succeed."""
        from embedding_provider import TokenBucketRateLimiter
        limiter = TokenBucketRateLimiter(max_concurrent=1, refill_rate=100.0)
        assert limiter.acquire(timeout=1.0) is True
        limiter.release()
        assert limiter.acquire(timeout=1.0) is True
        limiter.release()

    def test_openai_provider_uses_rate_limiter(self):
        """OpenAI provider should use rate limiter for API calls."""
        from embedding_provider import OpenAIProvider
        provider = OpenAIProvider(api_key=os.environ.get("OPENAI_API_KEY", "dummy"))
        # Check that the provider has a rate limiter
        assert hasattr(provider, "_rate_limiter")

    def test_gemini_provider_uses_rate_limiter(self):
        """Gemini provider should use rate limiter for API calls."""
        from embedding_provider import GeminiProvider
        provider = GeminiProvider(api_key=os.environ.get("GEMINI_API_KEY", "dummy"))
        assert hasattr(provider, "_rate_limiter")


if __name__ == "__main__":
    unittest.main()
