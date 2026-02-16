"""Centralized configuration constants for the memory subsystem.

All hardcoded thresholds and operational limits live here as frozen
dataclasses with sensible defaults.  The ``CognitiveMemoryServiceBuilder``
(or callers) may override individual values via manifest config; the
defaults below are the fallback.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Similarity thresholds
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SimilarityThresholds:
    """Cosine-similarity bands that govern the memory pipeline."""

    duplicate: float = 0.90
    """Similarity >= this → skip (memory already exists)."""

    reinforce_high: float = 0.85
    """Similarity in [reinforce_high, duplicate) → boost existing."""

    merge_low: float = 0.70
    """Similarity in [merge_low, reinforce_high) → LLM-merge."""

    conflict: float = 0.85
    """Minimum similarity to check for logical contradictions."""

    trigger: float = 0.85
    """Default threshold for prospective-memory trigger firing."""

    graph_dedup: float = 0.70
    """Threshold for deduplicating graph nodes against LTM facts."""

    cross_check: float = 0.80
    """Pairwise threshold for QueryAwareRecall cross-checking."""

    fact_dedup: float = 0.85
    """Intra-message deduplication of extracted facts."""

    text_merge: float = 0.98
    """Above this, texts are considered identical (reinforcement)."""


# ---------------------------------------------------------------------------
# Operational limits
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OperationalLimits:
    """Hard caps and batch sizes that prevent runaway resource usage."""

    embedding_batch_size: int = 100
    """Max texts sent in a single embedding API call."""

    parallel_semaphore: int = 10
    """Concurrency limit for parallel vector searches."""

    working_memory_ttl_hours: int = 24
    """TTL for working-memory documents (MongoDB TTL index)."""

    timeline_max_depth: int = 100
    """Max ancestry hops when resolving timeline inheritance."""

    history_array_cap: int = 100
    """$slice cap for unbounded $push arrays (confidence_history, etc.)."""

    max_pairwise_comparison: int = 200
    """Guard: truncate lists before O(n²) pairwise loops."""

    vector_search_candidates_multiplier: int = 20
    """numCandidates = limit × this multiplier for $vectorSearch."""

    fallback_trigger_cap: int = 100
    """Max triggers loaded for manual-similarity fallback."""

    default_importance: float = 0.5
    """Fallback importance when scoring is unavailable."""

    max_importance: float = 1.0
    """Hard ceiling for importance values."""

    min_confidence: float = 0.5
    """Default minimum confidence for memory retrieval."""


# ---------------------------------------------------------------------------
# Recall policy limits
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecallPolicyLimits:
    """Max-result caps per task type / latency budget."""

    default: int = 10
    fast_answer: int = 3
    critical_decision: int = 20
    exploration: int = 15
    fast_budget_cap: int = 5
    deep_budget_cap: int = 50


# ---------------------------------------------------------------------------
# Module-level singletons (importable everywhere)
# ---------------------------------------------------------------------------

SIMILARITY = SimilarityThresholds()
LIMITS = OperationalLimits()
RECALL = RecallPolicyLimits()
