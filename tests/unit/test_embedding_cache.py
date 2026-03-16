"""
Unit tests for EmbeddingService in-memory embedding cache.

Tests cache hits, misses, eviction, stats, clearing, mixed batches,
and model-based key isolation.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mdb_engine.embeddings.service import EmbeddingService


def _make_service(cache_max_size: int = 100) -> EmbeddingService:
    """Create an EmbeddingService with a mocked provider (no real API calls)."""
    mock_provider = MagicMock()
    mock_provider.embed = AsyncMock(side_effect=lambda texts, model=None: [[0.1] * 3 for _ in texts])

    with patch("mdb_engine.embeddings.service.SEMANTIC_SPLITTER_AVAILABLE", True):
        svc = EmbeddingService(
            embedding_provider=mock_provider,
            cache_max_size=cache_max_size,
        )
    return svc


class TestEmbeddingCacheHitsAndMisses:
    """Verify that cached embeddings skip the provider call."""

    @pytest.mark.asyncio
    async def test_cache_hit_skips_provider_call(self):
        svc = _make_service()
        await svc.embed("hello")
        await svc.embed("hello")

        assert svc.embedding_provider.embed.call_count == 1

    @pytest.mark.asyncio
    async def test_cache_miss_calls_provider(self):
        svc = _make_service()
        await svc.embed("hello")
        await svc.embed("world")

        assert svc.embedding_provider.embed.call_count == 2

    @pytest.mark.asyncio
    async def test_mixed_hits_and_misses(self):
        svc = _make_service()
        svc.embedding_provider.embed = AsyncMock(
            side_effect=lambda texts, model=None: [[float(hash(t) % 100)] for t in texts]
        )

        await svc.embed(["A", "B"])
        assert svc.embedding_provider.embed.call_count == 1

        # B is cached, C is new — provider should only be called with ["C"]
        result = await svc.embed(["B", "C"])
        assert svc.embedding_provider.embed.call_count == 2
        last_call_texts = svc.embedding_provider.embed.call_args[0][0]
        assert last_call_texts == ["C"]

        assert len(result) == 2


class TestEmbeddingCacheEviction:
    """Verify bounded cache with LRU eviction."""

    @pytest.mark.asyncio
    async def test_cache_eviction_at_max_size(self):
        svc = _make_service(cache_max_size=2)

        await svc.embed("A")
        await svc.embed("B")
        await svc.embed("C")

        assert svc.cache_stats["size"] == 2
        # "A" should have been evicted (oldest)
        key_a = EmbeddingService._cache_key("A", None)
        assert key_a not in svc._cache


class TestEmbeddingCacheStats:
    """Verify cache_stats and clear_cache."""

    @pytest.mark.asyncio
    async def test_cache_stats_returns_correct_values(self):
        svc = _make_service(cache_max_size=500)

        assert svc.cache_stats == {"size": 0, "max_size": 500}

        await svc.embed("test")
        assert svc.cache_stats == {"size": 1, "max_size": 500}

    @pytest.mark.asyncio
    async def test_clear_cache(self):
        svc = _make_service()

        await svc.embed("one")
        await svc.embed("two")
        assert svc.cache_stats["size"] == 2

        svc.clear_cache()
        assert svc.cache_stats["size"] == 0

        # After clearing, the next call must hit the provider again
        await svc.embed("one")
        assert svc.embedding_provider.embed.call_count == 3


class TestEmbeddingCacheModelIsolation:
    """Verify that the same text with different models produces separate cache entries."""

    @pytest.mark.asyncio
    async def test_cache_key_includes_model(self):
        svc = _make_service()

        await svc.embed("hello", model="model-a")
        await svc.embed("hello", model="model-b")

        assert svc.embedding_provider.embed.call_count == 2
        assert svc.cache_stats["size"] == 2


class TestEmbeddingCacheDisabled:
    """Verify behaviour when caching is disabled (cache_max_size=0)."""

    @pytest.mark.asyncio
    async def test_cache_disabled_always_calls_provider(self):
        svc = _make_service(cache_max_size=0)

        await svc.embed("hello")
        await svc.embed("hello")

        assert svc.embedding_provider.embed.call_count == 2
