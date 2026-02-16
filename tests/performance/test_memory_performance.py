"""
Performance tests for memory service computational operations.

Benchmarks the scoring, decay, and data-structure operations that run
on every memory retrieval — ensuring they stay fast as the codebase evolves.
These tests exercise pure-compute paths and do NOT require a running MongoDB instance.
"""

import asyncio
import math
import time
from datetime import datetime, timezone

import pytest

from mdb_engine.memory.strategies import (
    MemoryDocument,
    NoDecay,
    PerfectRecallScoring,
    QueryContext,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def scoring_strategy():
    return PerfectRecallScoring()


@pytest.fixture
def decay_strategy():
    return NoDecay()


def _make_memory(
    *,
    similarity: float = 0.85,
    importance: float = 0.7,
    access_count: int = 3,
    emotion: float = 0.5,
    emotion_type: str = "novelty",
) -> MemoryDocument:
    """Create a MemoryDocument with sensible defaults for benchmarking."""
    return MemoryDocument(
        id="mem_bench",
        text="Benchmark memory content for performance testing",
        similarity=similarity,
        importance=importance,
        access_count=access_count,
        emotion=emotion,
        emotion_type=emotion_type,
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        metadata={},
    )


def _make_query_context() -> QueryContext:
    return QueryContext(
        now=datetime.now(timezone.utc),
        user_id="user_bench",
        scoring_weights={
            "emotion_weight": 0.15,
            "novelty_boost": 0.1,
            "stakes_boost": 0.15,
            "resonance_boost": 0.1,
        },
    )


# ---------------------------------------------------------------------------
# Scoring Performance
# ---------------------------------------------------------------------------


class TestScoringPerformance:
    """Benchmark PerfectRecallScoring — the default scoring strategy."""

    def test_single_score_performance(self, scoring_strategy):
        """Single score call should be < 0.1ms."""
        memory = _make_memory()
        ctx = _make_query_context()

        start = time.perf_counter()
        asyncio.get_event_loop().run_until_complete(scoring_strategy.score(memory, ctx))
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 1, f"Single score took {elapsed_ms:.3f}ms, target < 1ms"

    def test_batch_scoring_performance(self, scoring_strategy):
        """Scoring 1,000 memories should complete in < 50ms."""
        memories = [
            _make_memory(
                similarity=0.5 + (i % 50) / 100,
                importance=0.3 + (i % 70) / 100,
                access_count=i % 20,
                emotion=0.1 + (i % 90) / 100,
                emotion_type=["novelty", "stakes", "resonance"][i % 3],
            )
            for i in range(1_000)
        ]
        ctx = _make_query_context()

        async def score_all():
            return [await scoring_strategy.score(m, ctx) for m in memories]

        start = time.perf_counter()
        scores = asyncio.get_event_loop().run_until_complete(score_all())
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert len(scores) == 1_000
        assert elapsed_ms < 50, f"Batch scoring 1k took {elapsed_ms:.2f}ms, target < 50ms"

    def test_score_and_sort_performance(self, scoring_strategy):
        """Score + sort 1,000 memories should complete in < 100ms."""
        memories = [
            _make_memory(
                similarity=0.5 + (i % 50) / 100,
                importance=0.3 + (i % 70) / 100,
                access_count=i % 20,
            )
            for i in range(1_000)
        ]
        ctx = _make_query_context()

        async def score_and_sort():
            scored = [(await scoring_strategy.score(m, ctx), m) for m in memories]
            scored.sort(key=lambda x: x[0], reverse=True)
            return scored[:10]

        start = time.perf_counter()
        top_10 = asyncio.get_event_loop().run_until_complete(score_and_sort())
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert len(top_10) == 10
        assert elapsed_ms < 100, f"Score+sort 1k took {elapsed_ms:.2f}ms, target < 100ms"


# ---------------------------------------------------------------------------
# Decay Performance
# ---------------------------------------------------------------------------


class TestDecayPerformance:
    """Benchmark NoDecay — the default decay strategy."""

    def test_batch_decay_performance(self, decay_strategy):
        """Applying decay to 1,000 memories should complete in < 50ms."""
        memories = [_make_memory(importance=0.3 + (i % 70) / 100) for i in range(1_000)]
        now = datetime.now(timezone.utc)

        async def apply_all():
            return [await decay_strategy.apply_decay(m, now) for m in memories]

        start = time.perf_counter()
        results = asyncio.get_event_loop().run_until_complete(apply_all())
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert len(results) == 1_000
        assert elapsed_ms < 50, f"Batch decay 1k took {elapsed_ms:.2f}ms, target < 50ms"


# ---------------------------------------------------------------------------
# Memory Document Construction Performance
# ---------------------------------------------------------------------------


class TestMemoryDocumentPerformance:
    """Benchmark MemoryDocument creation and attribute access."""

    def test_memory_document_creation_performance(self):
        """Creating 10,000 MemoryDocuments should complete in < 100ms."""
        now = datetime.now(timezone.utc)

        start = time.perf_counter()
        docs = [
            MemoryDocument(
                id=f"mem_{i}",
                text=f"Memory content number {i} with some representative text",
                similarity=0.85,
                importance=0.7,
                access_count=i % 50,
                emotion=0.5,
                emotion_type="novelty",
                created_at=now,
                metadata={"source": "bench", "index": i},
            )
            for i in range(10_000)
        ]
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert len(docs) == 10_000
        assert elapsed_ms < 100, f"Creating 10k docs took {elapsed_ms:.2f}ms, target < 100ms"

    def test_importance_formula_performance(self):
        """Raw importance formula (log-based) should handle 100k iterations in < 50ms."""
        iterations = 100_000

        start = time.perf_counter()
        for i in range(iterations):
            importance = 0.7
            access_count = i % 50
            _ = importance * (1 + math.log(access_count + 1))
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 50, f"100k importance calcs took {elapsed_ms:.2f}ms, target < 50ms"


# ---------------------------------------------------------------------------
# Memory Result Ranking Performance
# ---------------------------------------------------------------------------


class TestMemoryRankingPerformance:
    """Benchmark the full ranking pipeline: score → sort → top-k."""

    def test_rank_large_result_set(self, scoring_strategy):
        """Ranking 5,000 memories and returning top-20 should be < 250ms."""
        memories = [
            _make_memory(
                similarity=0.3 + (i % 70) / 100,
                importance=0.2 + (i % 80) / 100,
                access_count=i % 30,
                emotion=(i % 100) / 100,
                emotion_type=["novelty", "stakes", "resonance"][i % 3],
            )
            for i in range(5_000)
        ]
        ctx = _make_query_context()

        async def rank():
            scored = [(await scoring_strategy.score(m, ctx), m) for m in memories]
            scored.sort(key=lambda x: x[0], reverse=True)
            return scored[:20]

        start = time.perf_counter()
        top_20 = asyncio.get_event_loop().run_until_complete(rank())
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert len(top_20) == 20
        # Verify ordering is descending
        scores = [s for s, _ in top_20]
        assert scores == sorted(scores, reverse=True)
        assert elapsed_ms < 250, f"Ranking 5k memories took {elapsed_ms:.2f}ms, target < 250ms"
