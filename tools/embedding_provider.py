#!/usr/bin/env python3
"""Embedding provider abstraction layer for semantic search Phase 2.

Provides a unified interface for generating text embeddings from remote API
providers (OpenAI, Gemini). Uses auto-selection based on available API keys.
Graceful degradation: returns None when no provider is available.

Does NOT import semantic_index.py or memory_mcp_server.py.
"""

import logging
import os
import threading
import time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

# --- Configuration ---

# Batch size for embedding API calls
DEFAULT_BATCH_SIZE = 64

# API timeout in seconds
DEFAULT_TIMEOUT = 30

# Retry configuration
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BASE_DELAY = 1.0  # seconds, exponential backoff

# Rate limiting configuration
DEFAULT_MAX_CONCURRENT = 2  # Max concurrent embedding API requests


class TokenBucketRateLimiter:
    """Simple semaphore-based rate limiter for concurrent API requests.

    Limits the number of concurrent requests to prevent API rate limit bans.
    """

    def __init__(self, max_concurrent: int = DEFAULT_MAX_CONCURRENT,
                 refill_rate: float = 1.0):
        self._semaphore = threading.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._refill_rate = refill_rate

    def acquire(self, timeout: float = 30.0) -> bool:
        """Acquire a slot. Returns True if acquired, False on timeout."""
        return self._semaphore.acquire(timeout=timeout)

    def release(self) -> None:
        """Release a slot."""
        self._semaphore.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()


# --- Abstract Base ---


class EmbeddingProvider(ABC):
    """Abstract base class for embedding providers."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Unique provider identifier string."""
        ...

    @property
    @abstractmethod
    def model_id(self) -> str:
        """Model identifier string."""
        ...

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Embedding vector dimensionality."""
        ...

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float] | None]:
        """Generate embeddings for a batch of texts.

        Args:
            texts: List of text strings to embed.

        Returns:
            List of embedding vectors (float lists) or None for failed items.
            Length matches input list. Individual failures return None at that
            position; other items are still returned.
        """
        ...

    def embed_single(self, text: str) -> list[float] | None:
        """Generate embedding for a single text.

        Returns None on failure.
        """
        results = self.embed_texts([text])
        return results[0] if results else None


# --- OpenAI Provider ---


class OpenAIProvider(EmbeddingProvider):
    """OpenAI text-embedding-3-small provider.

    Requires OPENAI_API_KEY environment variable.
    Supports Japanese and English text (multilingual model).
    """

    _PROVIDER_NAME = "openai"
    _MODEL_ID = "text-embedding-3-small"
    _DIMENSIONS = 1536

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._client = None
        self._rate_limiter = TokenBucketRateLimiter(max_concurrent=DEFAULT_MAX_CONCURRENT)

    def _get_client(self):
        if self._client is None:
            import openai
            self._client = openai.OpenAI(
                api_key=self._api_key,
                timeout=DEFAULT_TIMEOUT,
            )
        return self._client

    @property
    def provider_name(self) -> str:
        return self._PROVIDER_NAME

    @property
    def model_id(self) -> str:
        return self._MODEL_ID

    @property
    def dimensions(self) -> int:
        return self._DIMENSIONS

    def embed_texts(self, texts: list[str]) -> list[list[float] | None]:
        if not texts:
            return []

        client = self._get_client()
        results: list[list[float] | None] = [None] * len(texts)

        # Process in batches
        for batch_start in range(0, len(texts), DEFAULT_BATCH_SIZE):
            batch = texts[batch_start:batch_start + DEFAULT_BATCH_SIZE]
            batch_results = self._embed_batch_with_retry(client, batch)
            for i, vec in enumerate(batch_results):
                results[batch_start + i] = vec

        return results

    def _embed_batch_with_retry(
        self, client, texts: list[str]
    ) -> list[list[float] | None]:
        """Embed a batch with exponential backoff retry and rate limiting."""
        if not self._rate_limiter.acquire(timeout=DEFAULT_TIMEOUT):
            logger.warning("Rate limiter timeout for OpenAI embed batch")
            return [None] * len(texts)
        try:
            for attempt in range(DEFAULT_MAX_RETRIES):
                try:
                    response = client.embeddings.create(
                        model=self._MODEL_ID,
                        input=texts,
                        timeout=DEFAULT_TIMEOUT,
                    )
                    # Sort by index to maintain order
                    sorted_data = sorted(response.data, key=lambda x: x.index)
                    return [item.embedding for item in sorted_data]
                except Exception as e:
                    if attempt < DEFAULT_MAX_RETRIES - 1:
                        delay = DEFAULT_RETRY_BASE_DELAY * (2 ** attempt)
                        logger.warning(
                            "OpenAI embed retry %d/%d after error: %s (delay=%.1fs)",
                            attempt + 1, DEFAULT_MAX_RETRIES, e, delay,
                        )
                        time.sleep(delay)
                    else:
                        logger.error("OpenAI embed failed after %d retries: %s",
                                     DEFAULT_MAX_RETRIES, e)
                        return [None] * len(texts)
            # Unreachable if DEFAULT_MAX_RETRIES > 0, but safe fallback
            return [None] * len(texts)

        finally:
            self._rate_limiter.release()

# --- Gemini Provider ---


class GeminiProvider(EmbeddingProvider):
    """Google Gemini embedding provider.

    Requires GEMINI_API_KEY environment variable.
    Supports Japanese and English text (multilingual model).
    Uses google.genai (new API).
    """

    _PROVIDER_NAME = "gemini"
    _MODEL_ID = "gemini-embedding-001"
    _DIMENSIONS = 3072

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._client = None
        self._rate_limiter = TokenBucketRateLimiter(max_concurrent=DEFAULT_MAX_CONCURRENT)

    def _get_client(self):
        if self._client is None:
            from google import genai  # google.genai (new API)
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    @property
    def provider_name(self) -> str:
        return self._PROVIDER_NAME

    @property
    def model_id(self) -> str:
        return self._MODEL_ID

    @property
    def dimensions(self) -> int:
        return self._DIMENSIONS

    def embed_texts(self, texts: list[str]) -> list[list[float] | None]:
        if not texts:
            return []

        client = self._get_client()
        results: list[list[float] | None] = [None] * len(texts)

        # Process in batches
        for batch_start in range(0, len(texts), DEFAULT_BATCH_SIZE):
            batch = texts[batch_start:batch_start + DEFAULT_BATCH_SIZE]
            batch_results = self._embed_batch_with_retry(client, batch)
            for i, vec in enumerate(batch_results):
                results[batch_start + i] = vec

        return results

    def _embed_batch_with_retry(
        self, client, texts: list[str]
    ) -> list[list[float] | None]:
        """Embed a batch with exponential backoff retry and rate limiting."""
        if not self._rate_limiter.acquire(timeout=DEFAULT_TIMEOUT):
            logger.warning("Rate limiter timeout for Gemini embed batch")
            return [None] * len(texts)
        try:
            for attempt in range(DEFAULT_MAX_RETRIES):
                try:
                    result = client.models.embed_content(
                        model=self._MODEL_ID,
                        contents=texts,
                        config={"http_options": {"timeout": DEFAULT_TIMEOUT * 1000}},
                    )
                    # New API: result.embeddings[i].values
                    if not result.embeddings:
                        return [None] * len(texts)
                    return [e.values for e in result.embeddings]
                except Exception as e:
                    if attempt < DEFAULT_MAX_RETRIES - 1:
                        delay = DEFAULT_RETRY_BASE_DELAY * (2 ** attempt)
                        logger.warning(
                            "Gemini embed retry %d/%d after error: %s (delay=%.1fs)",
                            attempt + 1, DEFAULT_MAX_RETRIES, e, delay,
                        )
                        time.sleep(delay)
                    else:
                        logger.error("Gemini embed failed after %d retries: %s",
                                     DEFAULT_MAX_RETRIES, e)
                        return [None] * len(texts)
            # Unreachable if DEFAULT_MAX_RETRIES > 0, but safe fallback
            return [None] * len(texts)

        finally:
            self._rate_limiter.release()

# --- Auto-selection ---

# Provider registry: (env_var_name, provider_class)
_PROVIDER_REGISTRY: list[tuple[str, type[EmbeddingProvider]]] = [
    ("OPENAI_API_KEY", OpenAIProvider),
    ("GEMINI_API_KEY", GeminiProvider),
]


def auto_select_provider() -> EmbeddingProvider | None:
    """Auto-select an embedding provider based on available API keys.

    Checks environment variables in priority order. Returns None if no
    provider is available (graceful degradation).
    """
    for env_var, provider_class in _PROVIDER_REGISTRY:
        api_key = os.environ.get(env_var, "").strip()
        if api_key:
            try:
                provider = provider_class(api_key)
                logger.info(
                    "Selected embedding provider: %s (model=%s, dim=%d)",
                    provider.provider_name, provider.model_id, provider.dimensions,
                )
                return provider
            except Exception as e:
                logger.warning(
                    "Failed to initialize provider %s: %s",
                    provider_class.__name__, e,
                )
                continue
    logger.info("No embedding provider available (no API keys set)")
    return None
