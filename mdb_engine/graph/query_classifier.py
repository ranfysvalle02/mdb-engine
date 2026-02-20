"""
Query Classification for GraphRAG

This module classifies queries to determine the appropriate GraphRAG search method:
- Local Search: Entity-focused, specific factual questions
- Global Search: Thematic, holistic questions requiring understanding of entire dataset
- DRIFT Search: Entity-focused but needs community context
- Basic Search: Simple lookups, fallback to vector search
"""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..llm.service import LLMService

logger = logging.getLogger(__name__)


class QueryClassifier:
    """
    Classifies queries to route to appropriate GraphRAG search method.

    Uses pattern matching for fast classification, with LLM fallback for ambiguous cases.
    """

    # Patterns for local queries (entity-focused)
    LOCAL_PATTERNS = [
        r"\b(what|who|where|when|how)\s+(is|are|was|were|does|do|did)\s+",
        r"\b(what|who|where|when)\s+\w+\s+(is|are|was|were|does|do|did)\s+",
        r"\b(tell me about|describe|explain)\s+",
        r"\b(what are|what is|who is|who are)\s+",
        r"\b(relationship|connection|link)\s+(between|to|with)\s+",
        r"\b(worked|works|knows|likes|loves|hates)\s+",
    ]

    # Patterns for global queries (thematic, holistic)
    GLOBAL_PATTERNS = [
        r"\b(what are|what is)\s+(the|all|some)\s+(main|key|major|significant|important)\s+",
        r"\b(what are|what is)\s+(the|all|some)\s+(themes?|patterns?|trends?|insights?)\s+",
        r"\b(summarize|overview|big picture|holistic|overall)\s+",
        r"\b(what patterns|what themes|what insights)\s+",
        r"\b(across|throughout|in general|in summary)\s+",
        r"\b(all|every|entire|whole|complete)\s+",
    ]

    # Patterns for DRIFT queries (entity + community context)
    DRIFT_PATTERNS = [
        r"\b(context|background|surrounding|related)\s+",
        r"\b(community|group|network|cluster)\s+",
        r"\b(around|nearby|connected|associated)\s+",
    ]

    def __init__(
        self,
        llm_service: LLMService | None = None,
        use_llm: bool = True,
        cache_results: bool = True,
        osi_registry: Any | None = None,
    ):
        """
        Initialize QueryClassifier.

        Args:
            llm_service: Optional LLMService for LLM-based classification
            use_llm: Whether to use LLM for ambiguous queries (default: True)
            cache_results: Whether to cache classification results (default: True)
            osi_registry: Optional OsiModelRegistry for metric-aware classification
        """
        self.llm_service = llm_service
        self.use_llm = use_llm
        self.cache_results = cache_results
        self.osi_registry = osi_registry
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._max_cache_size = 1000

        # Compile regex patterns for performance
        self.local_regexes = [re.compile(pattern, re.IGNORECASE) for pattern in self.LOCAL_PATTERNS]
        self.global_regexes = [re.compile(pattern, re.IGNORECASE) for pattern in self.GLOBAL_PATTERNS]
        self.drift_regexes = [re.compile(pattern, re.IGNORECASE) for pattern in self.DRIFT_PATTERNS]

        # Build OSI metric keywords for governed metric detection
        self._metric_keywords: set[str] = set()
        if self.osi_registry:
            try:
                self._metric_keywords = self.osi_registry.get_all_metric_keywords()
                if self._metric_keywords:
                    logger.info(f"QueryClassifier: loaded {len(self._metric_keywords)} metric keywords for OSI routing")
            except (AttributeError, TypeError) as e:
                logger.debug("Could not load OSI metric keywords: %s", e)

    async def classify_query(self, query: str) -> str:
        """
        Classify a query to determine the appropriate search method.

        Args:
            query: User query string

        Returns:
            One of: "local", "global", "drift", "osi_metric", or "basic"
        """
        query_lower = query.lower().strip()

        # Check cache
        if self.cache_results and query_lower in self._cache:
            return self._cache[query_lower]

        # Pattern-based classification
        classification = self._pattern_classify(query)

        # If ambiguous and LLM available, use LLM
        if classification == "basic" and self.use_llm and self.llm_service:
            llm_classification = await self._llm_classify(query)
            if llm_classification:
                classification = llm_classification

        # Cache result (bounded to prevent memory leaks)
        if self.cache_results:
            self._cache[query_lower] = classification
            if len(self._cache) > self._max_cache_size:
                self._cache.popitem(last=False)  # evict oldest

        logger.debug(f"Query classified as '{classification}': {query[:50]}...")
        return classification

    def _pattern_classify(self, query: str) -> str:
        """Classify query using pattern matching."""
        query_lower = query.lower()

        # Check OSI metric keywords FIRST (highest priority for governed data)
        if self._metric_keywords:
            for keyword in self._metric_keywords:
                if keyword in query_lower:
                    return "osi_metric"

        # Check for global patterns first (more specific)
        for pattern in self.global_regexes:
            if pattern.search(query_lower):
                return "global"

        # Check for DRIFT patterns
        for pattern in self.drift_regexes:
            if pattern.search(query_lower):
                return "drift"

        # Check for local patterns
        for pattern in self.local_regexes:
            if pattern.search(query_lower):
                return "local"

        # Default to basic (fallback to vector search)
        return "basic"

    async def _llm_classify(self, query: str) -> str | None:
        """Classify query using LLM."""
        if not self.llm_service:
            return None

        try:
            # Sanitize user query before interpolation into prompt
            try:
                from ..core.prompt_safety import sanitize_for_prompt

                safe_query = sanitize_for_prompt(query, tag="query")
            except ImportError:
                safe_query = query

            prompt = f"""Classify this query to determine the best GraphRAG search method.

Query: {safe_query}

Choose ONE of:
- "local": Specific factual questions about entities (e.g., "What does Alex like?", "Who worked on Project X?")
- "global": Thematic, holistic questions requiring understanding of entire dataset (e.g., "What are the main themes?", "What patterns emerge?")
- "drift": Entity-focused but needs community context (e.g., "What is the context around Alex?", "What communities is Project X part of?")
- "basic": Simple lookups that don't need graph traversal (e.g., "What is X?", "Tell me about Y")

Return ONLY the classification word (local, global, drift, or basic), nothing else."""

            provider_name = "chat"
            response = await self.llm_service.chat_completion(
                provider_name=provider_name,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a query classification expert. Return only the classification word.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,  # Deterministic classification
            )

            classification = response.strip().lower()

            # Validate response
            if classification in ["local", "global", "drift", "basic"]:
                return classification
            else:
                logger.warning(f"Invalid LLM classification: {classification}")
                return None

        except (ConnectionError, TimeoutError, ValueError) as e:
            logger.warning(f"LLM classification failed: {e}")
            return None

    def clear_cache(self) -> None:
        """Clear the classification cache."""
        self._cache.clear()
        logger.debug("Query classification cache cleared")


def get_query_classifier(
    llm_service: LLMService | None = None,
    use_llm: bool = True,
    cache_results: bool = True,
    osi_registry: Any | None = None,
) -> QueryClassifier:
    """
    Factory function to create a QueryClassifier.

    Args:
        llm_service: Optional LLMService for LLM-based classification
        use_llm: Whether to use LLM for ambiguous queries
        cache_results: Whether to cache classification results
        osi_registry: Optional OsiModelRegistry for metric-aware classification

    Returns:
        Configured QueryClassifier instance
    """
    return QueryClassifier(
        llm_service=llm_service,
        use_llm=use_llm,
        cache_results=cache_results,
        osi_registry=osi_registry,
    )
