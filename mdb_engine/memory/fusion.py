"""
Memory Fusion Service for Intelligent Fact Consolidation

This module provides LLM-powered semantic fusion of extracted facts,
preventing duplicate memories by intelligently merging related facts.

Key Features:
- Embedding-based clustering of related facts
- Parallel LLM fusion of clusters (async)
- Smart fallback chain: LLM -> simple merge -> pass-through
- Configurable via manifest.json

Usage:
    fusion_service = MemoryFusionService(
        config={"similarity_threshold": 0.8, "use_llm": True},
        embedding_fn=memory_service._get_embedding,
        llm_model="openai/gpt-4o",
    )

    # Fuse extracted facts
    fused_facts = await fusion_service.fuse_all(extracted_facts)
"""

import asyncio
import json
import logging
import math
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# LiteLLM import for async LLM calls
try:
    from litellm import acompletion
    from litellm.exceptions import (
        APIError,
        AuthenticationError,
        NotFoundError,
        RateLimitError,
    )

    LITELLM_AVAILABLE = True
except ImportError:
    LITELLM_AVAILABLE = False
    acompletion = None
    APIError = RuntimeError
    AuthenticationError = RuntimeError
    NotFoundError = RuntimeError
    RateLimitError = RuntimeError

# Import helper for provider-aware structured output
try:
    from ..llm.service import _format_response_format_for_provider
except ImportError:
    # Fallback if import fails
    def _format_response_format_for_provider(response_format: Any, model: str | None = None) -> Any:
        return response_format


# Pydantic import for structured output
try:
    from pydantic import BaseModel, Field

    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    BaseModel = None
    Field = None


class MemoryFusionError(Exception):
    """Base exception for Memory Fusion Service failures."""

    pass


# Pydantic model for structured LLM output
if PYDANTIC_AVAILABLE:

    class FusedFact(BaseModel):
        """Structured output model for LLM fusion response."""

        text: str = Field(description="The synthesized memory text")
        category: str = Field(description="Memory category")
        emotion: float = Field(ge=0.0, le=1.0, description="Emotional intensity 0.0-1.0")
        confidence: float = Field(ge=0.0, le=1.0, description="Fusion confidence 0.0-1.0")
        reasoning: str = Field(description="Brief explanation of synthesis choices")

else:
    FusedFact = None


# LLM Prompts for fusion
FUSION_SYSTEM_PROMPT = """You are a cognitive memory consolidation engine.

Given related facts about the same topic, synthesize ONE optimal memory that:
1. Captures ALL unique information from every fact
2. Eliminates redundancy without losing meaning
3. Uses clear, declarative language starting with "User..."
4. Preserves the highest emotional intensity

CATEGORIES (choose most specific - NEVER use "general"):
- biographical: Personal identity, life events, background
- preferences: Likes, dislikes, favorites, opinions
- temporal: Time-specific events, schedules, plans
- relational: Relationships, connections to others

Every fact MUST be assigned to one of these four categories. Never use "general".

EMOTION SCALE (0.0-1.0):
- 0.0-0.2: Mundane facts
- 0.3-0.5: Moderately important
- 0.6-0.7: Significant events
- 0.8-1.0: Highly emotional (life events, strong feelings)"""

FUSION_USER_PROMPT = """Synthesize these {count} related facts into ONE optimal memory:

{facts_formatted}

Return ONLY valid JSON:
{{
  "text": "The synthesized memory starting with 'User...'",
  "category": "most appropriate category",
  "emotion": 0.0 to 1.0,
  "confidence": 0.0 to 1.0,
  "reasoning": "brief explanation"
}}"""


def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Calculate cosine similarity between two vectors."""
    if len(vec1) != len(vec2):
        return 0.0

    try:
        dot_product = sum(a * b for a, b in zip(vec1, vec2, strict=False))
        magnitude1 = math.sqrt(sum(a * a for a in vec1))
        magnitude2 = math.sqrt(sum(a * a for a in vec2))

        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0

        return dot_product / (magnitude1 * magnitude2)
    except (ValueError, ZeroDivisionError, TypeError):
        return 0.0


class MemoryFusionService:
    """
    Intelligent memory fusion using LLM-powered semantic synthesis.

    This service prevents storing duplicate/similar facts by:
    1. Clustering facts by embedding similarity
    2. Using LLM to synthesize clusters into single optimal memories
    3. Falling back to simple merge if LLM fails

    Features:
    - Embedding-based clustering using Union-Find algorithm
    - Parallel async LLM calls with semaphore rate limiting
    - Smart fallback chain: LLM -> simple merge -> pass-through
    - Configurable via manifest

    Example:
        fusion_service = MemoryFusionService(
            config={"similarity_threshold": 0.8},
            embedding_fn=get_embedding,
            llm_model="openai/gpt-4o",
        )

        fused = await fusion_service.fuse_all([
            {
                "text": "User loves chocolate",
                "category": "preferences",
                "emotion": 0.5,
            },
            {
                "text": "Chocolate is user's favorite candy",
                "category": "preferences",
                "emotion": 0.4,
            },
        ])
        # Returns: [{"text": "User loves chocolate, which is their favorite candy", ...}]
    """

    # Category priority: more specific = higher priority
    # Note: "general" is NOT a memory category - it's only for bucket_type filtering
    CATEGORY_PRIORITY = {
        "biographical": 4,
        "preferences": 3,
        "temporal": 2,
        "relational": 1,
    }

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        embedding_fn: Callable[[str], list[float] | None] | None = None,
        embedding_fn_batch: Callable[[list[str]], dict[str, list[float]]] | None = None,
        llm_model: str | None = None,
        temperature: float = 0.0,
    ):
        """
        Initialize the Memory Fusion Service.

        Args:
            config: Configuration dict with:
                - enabled: Enable fusion (default: True)
                - use_llm: Use LLM for fusion (default: True)
                - similarity_threshold: Clustering threshold (default: 0.8)
                - fallback_to_simple: Fall back to simple merge on LLM failure (default: True)
                - parallel_limit: Max concurrent LLM calls (default: 5)
                - timeout_seconds: LLM call timeout (default: 10)
            embedding_fn: Function to generate single embedding (used if batch not available)
            embedding_fn_batch: Function to batch generate embeddings (preferred, ~5x faster)
            llm_model: LiteLLM model string (e.g., "openai/gpt-4o")
            temperature: LLM temperature (default: 0.0 for deterministic)
        """
        self.config = config or {}
        self.embedding_fn = embedding_fn
        self.embedding_fn_batch = embedding_fn_batch
        # llm_model should be set by caller (from LLM service config)
        # Only use hardcoded default as last resort (should not happen in normal operation)
        self.llm_model = llm_model
        if not self.llm_model:
            logger.warning(
                "⚠️ MemoryFusionService: llm_model not provided. "
                "Services should inherit LLM model from app's llm_config. "
                "Falling back to 'openai/gpt-4o'."
            )
            self.llm_model = "openai/gpt-4o"
        self.temperature = temperature

        # Configuration
        self.enabled = self.config.get("enabled", True)
        self.use_llm = self.config.get("use_llm", True) and LITELLM_AVAILABLE
        self.similarity_threshold = self.config.get("similarity_threshold", 0.8)
        self.fallback_to_simple = self.config.get("fallback_to_simple", True)
        self.parallel_limit = self.config.get("parallel_limit", 5)
        self.timeout_seconds = self.config.get("timeout_seconds", 10)

        if not LITELLM_AVAILABLE and self.use_llm:
            logger.warning(
                "⚠️ [Fusion] LiteLLM not available, falling back to simple merge. "
                "Install with: pip install litellm"
            )
            self.use_llm = False

        logger.info(
            f"✅ [Fusion] MemoryFusionService initialized: "
            f"use_llm={self.use_llm}, threshold={self.similarity_threshold}, "
            f"parallel_limit={self.parallel_limit}"
        )

    def cluster_facts(
        self,
        facts: list[dict[str, Any]],
        threshold: float | None = None,
    ) -> list[list[dict[str, Any]]]:
        """
        Cluster facts by embedding similarity using Union-Find algorithm.

        Groups facts that are semantically similar (cosine similarity > threshold)
        into clusters. Uses Union-Find for efficient transitive clustering.

        Args:
            facts: List of fact dicts with 'text', 'category', 'emotion'
            threshold: Similarity threshold (default: self.similarity_threshold)

        Returns:
            List of clusters, where each cluster is a list of similar facts
        """
        if threshold is None:
            threshold = self.similarity_threshold

        if len(facts) <= 1:
            return [facts] if facts else []

        # Generate embeddings for all facts
        # Use batch embedding if available (~5x faster), otherwise fall back to sequential
        facts_with_embeddings = []

        if self.embedding_fn_batch:
            # OPTIMIZED: Batch embed all facts in a single call
            texts = [fact["text"] for fact in facts]
            logger.info(f"⚡ [Fusion] Batch embedding {len(texts)} facts...")

            try:
                embeddings_map = self.embedding_fn_batch(texts)

                for fact in facts:
                    text = fact["text"].replace("\n", " ").strip()
                    embedding = embeddings_map.get(text)
                    facts_with_embeddings.append(
                        {**fact, "_embedding": embedding, "_idx": len(facts_with_embeddings)}
                    )
                    if not embedding:
                        logger.warning(f"⚠️ [Fusion] No embedding for: '{text[:50]}...'")

                logger.info(f"⚡ [Fusion] Batch embedded {len(embeddings_map)} facts")
            except (RuntimeError, ValueError, TypeError) as e:
                logger.warning(f"⚠️ [Fusion] Batch embedding failed: {e}, using sequential")
                # Fall back to sequential
                self.embedding_fn_batch = None  # Disable for this session
                facts_with_embeddings = []  # Reset to retry with sequential

        # Sequential fallback if batch not available or failed
        if not facts_with_embeddings:
            for fact in facts:
                if self.embedding_fn:
                    embedding = self.embedding_fn(fact["text"])
                    if embedding:
                        facts_with_embeddings.append(
                            {**fact, "_embedding": embedding, "_idx": len(facts_with_embeddings)}
                        )
                    else:
                        logger.warning(f"⚠️ [Fusion] Failed to embed: '{fact['text'][:50]}...'")
                        facts_with_embeddings.append(
                            {**fact, "_embedding": None, "_idx": len(facts_with_embeddings)}
                        )
                else:
                    facts_with_embeddings.append(
                        {**fact, "_embedding": None, "_idx": len(facts_with_embeddings)}
                    )

        n = len(facts_with_embeddings)
        if n == 0:
            return []

        # Union-Find for clustering
        parent = list(range(n))

        def find(x: int) -> int:
            if parent[x] != x:
                parent[x] = find(parent[x])
            return parent[x]

        def union(x: int, y: int) -> None:
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py

        # Compare all pairs and union similar facts
        for i in range(n):
            for j in range(i + 1, n):
                emb_i = facts_with_embeddings[i].get("_embedding")
                emb_j = facts_with_embeddings[j].get("_embedding")

                if emb_i is not None and emb_j is not None:
                    similarity = cosine_similarity(emb_i, emb_j)
                    if similarity > threshold:
                        union(i, j)
                        logger.debug(
                            f"🔗 [Fusion] Clustered (sim={similarity:.2f}): "
                            f"'{facts_with_embeddings[i]['text'][:30]}...' + "
                            f"'{facts_with_embeddings[j]['text'][:30]}...'"
                        )

        # Group by cluster
        clusters_map: dict[int, list[dict[str, Any]]] = {}
        for i, fact in enumerate(facts_with_embeddings):
            root = find(i)
            if root not in clusters_map:
                clusters_map[root] = []
            # Remove internal fields before adding to cluster
            clean_fact = {k: v for k, v in fact.items() if not k.startswith("_")}
            clusters_map[root].append(clean_fact)

        clusters = list(clusters_map.values())
        logger.info(
            f"🔄 [Fusion] Clustered {n} facts into {len(clusters)} cluster(s): "
            f"{[len(c) for c in clusters]}"
        )

        return clusters

    async def fuse_cluster_llm(self, cluster: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Use LLM to synthesize multiple facts into one optimal memory.

        Args:
            cluster: List of similar facts to fuse

        Returns:
            Single fused fact dict with 'text', 'category', 'emotion'

        Raises:
            MemoryFusionError: If LLM call fails
        """
        if not self.use_llm or not LITELLM_AVAILABLE:
            raise MemoryFusionError("LLM not available for fusion")

        if len(cluster) == 1:
            return cluster[0]

        # Format facts for prompt
        # Ensure all facts have valid categories (not "general")
        formatted_facts = []
        for fact in cluster:
            fact_cat = fact.get("category")
            # If category is missing, we'll let LLM detect it
            # But for display, show what we have
            if not fact_cat or fact_cat == "general":
                fact_cat = "unknown"
            formatted_facts.append(
                f"- Fact {len(formatted_facts)+1}: \"{fact['text']}\" "
                f"[category: {fact_cat}, emotion: {fact.get('emotion', 0.3):.1f}]"
            )
        facts_formatted = "\n".join(formatted_facts)

        user_prompt = FUSION_USER_PROMPT.format(
            count=len(cluster),
            facts_formatted=facts_formatted,
        )

        try:
            # Use asyncio.wait_for for timeout
            response = await asyncio.wait_for(
                acompletion(
                    model=self.llm_model,
                    messages=[
                        {"role": "system", "content": FUSION_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=self.temperature,
                    response_format=(
                        _format_response_format_for_provider(FusedFact, self.llm_model)
                        if PYDANTIC_AVAILABLE and FusedFact
                        else None
                    ),
                ),
                timeout=self.timeout_seconds,
            )

            content = response.choices[0].message.content.strip()
            logger.debug(f"🤖 [Fusion] LLM response: {content[:200]}...")

            # Safely parse structured response (handles markdown code blocks, empty responses, etc.)
            if PYDANTIC_AVAILABLE and FusedFact:
                try:
                    from ..llm.service import _parse_structured_response

                    fused = _parse_structured_response(content, FusedFact)
                    if not isinstance(fused, FusedFact):
                        # Fallback: try direct parsing if helper returned dict
                        fused = FusedFact.model_validate(fused)
                    result = {
                        "text": fused.text,
                        "category": fused.category,
                        "emotion": fused.emotion,
                        "_fusion_confidence": fused.confidence,
                        "_fusion_reasoning": fused.reasoning,
                    }
                except (ValueError, TypeError, AttributeError, KeyError) as parse_err:
                    logger.warning(f"⚠️ [Fusion] Parse failed: {parse_err}, trying fallback")
                    # Fallback: try direct JSON parsing
                    try:
                        import re

                        json_match = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
                        if json_match:
                            content = json_match.group(1).strip()
                        data = json.loads(content)
                        # Get category from data or cluster, ensuring it's valid
                        fallback_cat = cluster[0].get("category", "").lower() if cluster else ""
                        if fallback_cat not in self.CATEGORY_PRIORITY:
                            fallback_cat = "biographical"  # Default to biographical
                        result = {
                            "text": data.get("text", cluster[0]["text"]),
                            "category": data.get("category", fallback_cat),
                            "emotion": float(data.get("emotion", 0.5)),
                            "_fusion_confidence": float(data.get("confidence", 0.8)),
                            "_fusion_reasoning": data.get("reasoning", ""),
                        }
                        # Validate category
                        if result["category"].lower() not in self.CATEGORY_PRIORITY:
                            result["category"] = fallback_cat
                    except (json.JSONDecodeError, KeyError, ValueError):
                        logger.exception("❌ [Fusion] Complete parse failure")
                        # Last resort: return first fact from cluster
                        result = {
                            "text": cluster[0]["text"],
                            "category": cluster[0].get("category") or "biographical",
                            "emotion": 0.5,
                            "_fusion_confidence": 0.5,
                            "_fusion_reasoning": "Parse failed, using first fact",
                        }
            else:
                # Fallback JSON parsing
                try:
                    import re

                    json_match = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
                    if json_match:
                        content = json_match.group(1).strip()
                    data = json.loads(content)
                    result = {
                        "text": data.get("text", cluster[0]["text"]),
                        "category": data.get("category", cluster[0].get("category", "general")),
                        "emotion": float(data.get("emotion", 0.5)),
                        "_fusion_confidence": float(data.get("confidence", 0.8)),
                        "_fusion_reasoning": data.get("reasoning", ""),
                    }
                except (json.JSONDecodeError, KeyError, ValueError):
                    logger.exception("❌ [Fusion] JSON parse failure")
                    result = {
                        "text": cluster[0]["text"],
                        "category": cluster[0].get("category", "general"),
                        "emotion": 0.5,
                        "_fusion_confidence": 0.5,
                        "_fusion_reasoning": "Parse failed",
                    }

            # Validate category - ensure it's a valid category
            # If LLM returns invalid category, detect from cluster or text
            result_category = result.get("category", "").lower()
            if result_category not in self.CATEGORY_PRIORITY:
                # Find valid categories from cluster
                cluster_categories = [
                    f.get("category", "").lower()
                    for f in cluster
                    if f.get("category") and f.get("category").lower() in self.CATEGORY_PRIORITY
                ]
                if cluster_categories:
                    # Use category with highest priority from cluster
                    best_cluster_category = max(
                        cluster_categories, key=lambda cat: self.CATEGORY_PRIORITY.get(cat, 0)
                    )
                    result["category"] = best_cluster_category
                    logger.info(
                        f"🔄 [Fusion] Corrected invalid category "
                        f"'{result_category}' to '{best_cluster_category}' "
                        f"based on cluster categories: {set(cluster_categories)}"
                    )
                else:
                    # Fallback: detect from merged text
                    # (would need embedding_fn, but use first fact's text)
                    # For now, default to biographical
                    result["category"] = "biographical"
                    logger.warning(
                        "⚠️ [Fusion] No valid categories in cluster, defaulting to 'biographical'"
                    )

            logger.info(
                f"✅ [Fusion] LLM fused {len(cluster)} facts → "
                f"'{result['text'][:50]}...' "
                f"[{result['category']}, emotion={result['emotion']:.2f}]"
            )

            return result

        except asyncio.TimeoutError:
            logger.warning(f"⚠️ [Fusion] LLM timeout after {self.timeout_seconds}s")
            raise MemoryFusionError(f"LLM fusion timed out after {self.timeout_seconds}s") from None
        except (
            APIError,
            AuthenticationError,
            NotFoundError,
            RateLimitError,
            json.JSONDecodeError,
        ) as e:
            logger.warning(f"⚠️ [Fusion] LLM fusion failed: {e}")
            raise MemoryFusionError(f"LLM fusion failed: {e}") from e

    def fuse_cluster_simple(self, cluster: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Simple fallback merge: longest text, highest emotion, best category.

        This is used when LLM fusion fails or is disabled.

        Args:
            cluster: List of similar facts to fuse

        Returns:
            Single fused fact dict
        """
        if len(cluster) == 1:
            return cluster[0]

        def get_category_priority(cat: str) -> int:
            return self.CATEGORY_PRIORITY.get(cat, 0)

        # Find best attributes
        best_text = max(cluster, key=lambda f: len(f.get("text", "")))["text"]
        best_emotion = max(f.get("emotion", 0.3) for f in cluster)

        # Find best category from cluster, filtering out invalid categories
        valid_categories = [
            f.get("category", "").lower()
            for f in cluster
            if f.get("category") and f.get("category").lower() in self.CATEGORY_PRIORITY
        ]
        if valid_categories:
            best_category = max(valid_categories, key=get_category_priority)
        else:
            # No valid categories in cluster, default to biographical
            best_category = "biographical"

        result = {
            "text": best_text,
            "category": best_category,
            "emotion": best_emotion,
            "_fusion_method": "simple",
        }

        logger.info(
            f"🔄 [Fusion] Simple merged {len(cluster)} facts → "
            f"'{result['text'][:50]}...' [{result['category']}]"
        )

        return result

    async def _fuse_with_fallback(self, cluster: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Try LLM fusion, fall back to simple merge on failure.

        Implements the fallback chain:
        LLM Fusion → Simple Merge → Pass-through

        Args:
            cluster: List of facts to fuse

        Returns:
            Single fused fact
        """
        if len(cluster) == 1:
            return cluster[0]

        # Try LLM fusion
        if self.use_llm:
            try:
                return await self.fuse_cluster_llm(cluster)
            except MemoryFusionError as e:
                if self.fallback_to_simple:
                    logger.info(f"🔄 [Fusion] Falling back to simple merge: {e}")
                else:
                    logger.warning(f"⚠️ [Fusion] LLM failed, no fallback: {e}")
                    return cluster[0]  # Pass-through first fact

        # Simple merge fallback
        if self.fallback_to_simple:
            return self.fuse_cluster_simple(cluster)

        # Final fallback: pass-through
        return cluster[0]

    async def fuse_all(self, facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Full fusion pipeline with parallel execution.

        1. Cluster facts by embedding similarity
        2. Pass-through singletons (no LLM call)
        3. Parallel LLM fusion for multi-fact clusters
        4. Handle failures with fallback chain

        Args:
            facts: List of extracted facts

        Returns:
            List of fused facts (deduplicated)
        """
        if not self.enabled:
            logger.debug("🔄 [Fusion] Disabled, passing through")
            return facts

        if len(facts) <= 1:
            return facts

        logger.info(f"🔄 [Fusion] Starting fusion of {len(facts)} facts")

        # Step 1: Cluster facts
        clusters = self.cluster_facts(facts)

        # Step 2: Separate singletons from multi-fact clusters
        singletons = [c[0] for c in clusters if len(c) == 1]
        multi_clusters = [c for c in clusters if len(c) > 1]

        if not multi_clusters:
            logger.info(f"🔄 [Fusion] No clusters to fuse, returning {len(singletons)} singletons")
            return singletons

        logger.info(
            f"🔄 [Fusion] Processing {len(multi_clusters)} multi-fact cluster(s), "
            f"{len(singletons)} singleton(s)"
        )

        # Step 3: Parallel LLM fusion with semaphore rate limiting
        semaphore = asyncio.Semaphore(self.parallel_limit)

        async def bounded_fuse(cluster: list[dict[str, Any]]) -> dict[str, Any]:
            async with semaphore:
                return await self._fuse_with_fallback(cluster)

        # Execute fusion in parallel
        fused_results = await asyncio.gather(
            *[bounded_fuse(c) for c in multi_clusters],
            return_exceptions=True,
        )

        # Step 4: Handle any exceptions by falling back
        results = []
        for i, result in enumerate(fused_results):
            if isinstance(result, Exception):
                logger.warning(f"⚠️ [Fusion] Cluster {i} fusion failed: {result}")
                # Final fallback: simple merge or pass-through
                if self.fallback_to_simple:
                    results.append(self.fuse_cluster_simple(multi_clusters[i]))
                else:
                    results.append(multi_clusters[i][0])
            else:
                results.append(result)

        final_facts = singletons + results

        logger.info(f"✅ [Fusion] Complete: {len(facts)} facts → {len(final_facts)} fused facts")

        return final_facts

    def fuse_all_sync(self, facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Synchronous wrapper for fuse_all.

        Handles running in both async and sync contexts.

        Args:
            facts: List of extracted facts

        Returns:
            List of fused facts
        """
        import concurrent.futures

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Running in async context, use thread pool
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(lambda: asyncio.run(self.fuse_all(facts)))
                    return future.result(timeout=self.timeout_seconds * len(facts) + 10)
            else:
                # Not in async context, run directly
                return asyncio.run(self.fuse_all(facts))
        except RuntimeError:
            # No event loop, create new one
            return asyncio.run(self.fuse_all(facts))
