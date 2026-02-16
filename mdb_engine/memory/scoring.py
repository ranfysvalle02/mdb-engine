"""
Scoring Mixin for CognitiveMemoryService.

Provides importance assessment (sync/async/parallel), category detection
and priority, new-information detection, and Perfect Recall search ranking.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from pymongo.errors import OperationFailure, PyMongoError

from .base import MemoryServiceError
from .constants import CATEGORY_PRIORITY
from .extraction import MEMORY_CATEGORIES
from .strategies import ImportanceContext, MemoryDocument, QueryContext

_PARALLEL_SEMAPHORE_LIMIT = 10

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------
# Module-level constants (allocated once, not per-call)
# -------------------------------------------------------------------------

# Re-export from constants for backward compatibility
_CATEGORY_PRIORITY = CATEGORY_PRIORITY

_RELATIONAL_KEYWORDS = frozenset(
    [
        "sister",
        "brother",
        "mother",
        "father",
        "mom",
        "dad",
        "parent",
        "daughter",
        "son",
        "child",
        "children",
        "family",
        "families",
        "wife",
        "husband",
        "spouse",
        "partner",
        "uncle",
        "aunt",
        "cousin",
        "grandmother",
        "grandfather",
        "grandma",
        "grandpa",
        "grandparent",
        "brother-in-law",
        "sister-in-law",
        "mother-in-law",
        "father-in-law",
        "niece",
        "nephew",
        "friend",
        "friends",
        "colleague",
        "colleagues",
        "relationship",
        "relationships",
        "connected",
        "knows",
        "know",
    ]
)

_PREFERENCES_KEYWORDS = frozenset(
    [
        "likes",
        "loves",
        "prefers",
        "favorite",
        "favourite",
        "enjoys",
        "dislikes",
        "hates",
        "avoids",
        "wants",
        "desires",
        "interested in",
        "passion",
        "passionate",
        "fond of",
        "into",
        "fan of",
    ]
)

_BIOGRAPHICAL_KEYWORDS = frozenset(
    [
        "user's name",
        "user is",
        "user works",
        "user lives",
        "user has",
        "user was",
        "user went",
        "user got",
        "user became",
        "user studied",
        "user graduated",
        "user born",
        "user from",
        "user age",
        "user's age",
        "user's",
        "user",
        "i am",
        "i'm",
        "i work",
        "i live",
        "i have",
        "my name",
        "my age",
        "i was",
        "i went",
        "i got",
        "i became",
    ]
)

_TEMPORAL_KEYWORDS = frozenset(
    [
        "due",
        "deadline",
        "schedule",
        "appointment",
        "meeting",
        "project",
        "working on",
        "planning",
        "upcoming",
        "next week",
        "next month",
        "tomorrow",
        "today",
        "recently",
        "soon",
        "later",
    ]
)

_STOPWORDS = frozenset(
    [
        # Articles / determiners
        "the",
        "a",
        "an",
        "this",
        "that",
        "these",
        "those",
        # Pronouns
        "i",
        "me",
        "my",
        "you",
        "your",
        "he",
        "she",
        "it",
        "we",
        "they",
        # Auxiliaries
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "has",
        "have",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "can",
        "must",
        "shall",
        # Prepositions / conjunctions
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "and",
        "or",
        "but",
        "so",
        "if",
        "then",
        "than",
        # Memory-domain filler
        "user",
        "loves",
        "likes",
        "prefers",
        "wants",
        "needs",
        "said",
        "mentioned",
        "told",
        "asked",
        # Misc
        "very",
        "really",
        "just",
        "also",
        "too",
        "much",
        "more",
        "about",
        "some",
        "all",
        "any",
        "not",
        "no",
        "yes",
    ]
)


class ScoringMixin:
    """Mixin that provides scoring / ranking methods for CognitiveMemoryService."""

    # ------------------------------------------------------------------
    # Importance assessment
    # ------------------------------------------------------------------

    async def _assess_importance(self, text: str) -> float:
        """
        Assess the importance of a memory (async).

        Delegates to ``self._importance_strategy`` if available, otherwise
        falls back to the built-in LLM-based assessment.

        Returns importance score between 0.1 and 1.0.
        """
        strategy = getattr(self, "_importance_strategy", None)
        if strategy is not None:
            ctx = ImportanceContext(
                app_slug=getattr(self, "app_slug", ""),
                user_id=None,
            )
            try:
                return await strategy.assess(text, ctx)
            except (RuntimeError, ValueError, TypeError, AttributeError, OSError, ConnectionError) as e:
                logger.warning(f"Importance strategy failed, using fallback: {e}")

        # Fallback: inline LLM-based assessment (original behaviour)
        if not self.llm_available:
            logger.warning("No LLM client available, using default importance")
            return 0.5

        importance_prompt = (
            "On a scale of 1-10, rate the importance of remembering this information "
            "long-term. Consider factors like: uniqueness of information, "
            "actionability, personal significance, and whether it contains key facts "
            "or decisions. Respond with just a number.\n\n"
            f"Text to evaluate: {text}"
        )

        try:
            response = await self._llm_completion(
                messages=[{"role": "user", "content": importance_prompt}],
                model=self.memory_llm_model,
            )

            rating_text = response.choices[0].message.content

            # Extract numeric rating
            try:
                rating = float("".join(c for c in rating_text if c.isdigit() or c == "."))
                # Normalize to 0.1-1.0 range
                importance = min(max(rating / 10.0, 0.1), 1.0)
                return importance
            except ValueError:
                logger.warning(f"Could not parse importance rating: {rating_text}")
                return 0.5
        except (
            MemoryServiceError,
            RuntimeError,
            ConnectionError,
            OSError,
        ) as e:
            logger.warning(f"Importance assessment failed: {e}")
            return 0.5

    async def _assess_importance_parallel(self, texts: list[str]) -> dict[str, float]:
        """
        Assess importance for multiple texts in parallel.

        Args:
            texts: List of texts to assess

        Returns:
            Dictionary mapping each text to its importance score
        """
        if not texts:
            return {}

        semaphore = asyncio.Semaphore(_PARALLEL_SEMAPHORE_LIMIT)

        async def assess_with_semaphore(text: str) -> tuple[str, float]:
            async with semaphore:
                importance = await self._assess_importance(text)
                return (text, importance)

        # Run all assessments in parallel
        results = await asyncio.gather(*[assess_with_semaphore(t) for t in texts], return_exceptions=True)

        # Build result dict, handling exceptions
        importance_map: dict[str, float] = {}
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(f"Importance assessment {i} failed: {result}")
                importance_map[texts[i]] = 0.5  # Default importance
            else:
                text, importance = result
                importance_map[text] = importance

        logger.info(f"[Parallel Importance] Assessed {len(texts)} texts concurrently")
        return importance_map

    # ------------------------------------------------------------------
    # Category detection / priority
    # ------------------------------------------------------------------

    def _get_category_priority(self, category: str) -> int:
        """Return priority score for *category* (higher = more specific, 0 = unknown)."""
        return _CATEGORY_PRIORITY.get(category, 0)

    def _detect_category_from_text(self, text: str) -> str:
        """
        Detect the best category for *text* using keyword heuristics,
        falling back to ``"biographical"`` as the safest default.

        This is the fast, sync path used in tight inner loops.
        Never returns ``"general"``.
        """
        if not text:
            return "biographical"

        text_lower = text.lower()

        # Relational (but not if clearly biographical)
        if any(kw in text_lower for kw in _RELATIONAL_KEYWORDS):
            if not any(kw in text_lower for kw in _BIOGRAPHICAL_KEYWORDS):
                return "relational"

        if any(kw in text_lower for kw in _PREFERENCES_KEYWORDS):
            return "preferences"

        if any(kw in text_lower for kw in _BIOGRAPHICAL_KEYWORDS):
            return "biographical"

        if any(kw in text_lower for kw in _TEMPORAL_KEYWORDS):
            return "temporal"

        return "biographical"

    async def _detect_category_from_text_llm(self, text: str) -> str:
        """
        Detect category using keyword heuristics with async LLM fallback.

        Use this when LLM-quality classification is needed and you're in
        an async context.  Never returns ``"general"``.
        """
        # Try fast keyword path first
        result = self._detect_category_from_text(text)
        # If keywords matched a non-default category, return it
        if result != "biographical" or not text:
            return result

        # Check if text actually contains biographical keywords (a true match, not default)
        text_lower = text.lower()
        if any(kw in text_lower for kw in _BIOGRAPHICAL_KEYWORDS):
            return "biographical"

        # Heuristics exhausted -- try LLM
        if self.llm_available:
            try:
                response = await self._llm_completion(
                    messages=[
                        {
                            "role": "user",
                            "content": (
                                "Classify this memory into ONE category: biographical, "
                                "preferences, temporal, or relational.\n\n"
                                f"Memory: {text}\n\n"
                                "Return ONLY the category name (one word)."
                            ),
                        }
                    ],
                    model=self.memory_llm_model,
                    temperature=0.0,
                )
                detected = response.choices[0].message.content.strip().lower()
                if detected in _CATEGORY_PRIORITY:
                    return detected
            except (
                MemoryServiceError,
                AttributeError,
                RuntimeError,
                ValueError,
                TypeError,
                KeyError,
            ) as e:
                logger.warning(f"LLM category detection failed: {e}")

        return "biographical"

    async def _get_best_category(
        self,
        existing_category: str,
        new_text: str,
        new_category: str | None = None,
    ) -> str:
        """
        Choose the best category between existing and new, using priority and detection.

        Args:
            existing_category: Current category of the memory (must be valid)
            new_text: New text that may suggest a different category
            new_category: Optional category from new fact extraction (if available)

        Returns:
            Best category (higher priority wins, or detected from text if needed)
        """
        # If existing_category is missing or invalid, detect from text
        if not existing_category or existing_category not in MEMORY_CATEGORIES:
            existing_category = self._detect_category_from_text(new_text) if new_text else "biographical"

        # If we have a new category from extraction, use it
        if new_category:
            # If new_category is invalid, detect from text
            if new_category not in MEMORY_CATEGORIES:
                new_category = self._detect_category_from_text(new_text) if new_text else "biographical"

            existing_priority = self._get_category_priority(existing_category)
            new_priority = self._get_category_priority(new_category)

            # Use the category with higher priority
            if new_priority > existing_priority:
                logger.debug(
                    f"[Category] Upgrading category: {existing_category} → {new_category} "
                    f"(priority {existing_priority} → {new_priority})"
                )
                return new_category
            return existing_category

        # Otherwise, detect category from new text
        detected_category = self._detect_category_from_text(new_text)
        existing_priority = self._get_category_priority(existing_category)
        detected_priority = self._get_category_priority(detected_category)

        # Use the category with higher priority
        if detected_priority > existing_priority:
            logger.debug(
                f"[Category] Upgrading category: {existing_category} → {detected_category} "
                f"(priority {existing_priority} → {detected_priority})"
            )
            return detected_category

        return existing_category

    # ------------------------------------------------------------------
    # New information detection
    # ------------------------------------------------------------------

    def _has_new_information(self, existing_text: str, new_text: str) -> bool:
        """Return ``True`` if *new_text* contains meaningful words not in *existing_text*."""
        if not existing_text or not new_text:
            return False

        existing_words = set(existing_text.lower().split())
        novel = set(new_text.lower().split()) - existing_words - _STOPWORDS
        # Drop single chars and bare numbers
        novel = {w for w in novel if len(w) > 1 and not w.isdigit()}
        return bool(novel)

    # ------------------------------------------------------------------
    # Search ranking
    # ------------------------------------------------------------------

    async def _search(
        self,
        query_vector: list[float],
        user_id: str | None,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
        update_access: bool = True,
        timeline_id: str = "root",
        min_confidence: float = 0.5,
    ) -> list[dict[str, Any]]:
        """
        Semantic search ranked by similarity * effective_importance.

        Uses effective_importance = importance * (1 + ln(access_count + 1)).
        All memories are permanently accessible.

        Supports timeline inheritance and confidence filtering.

        Args:
            query_vector: Query embedding vector
            user_id: User ID for filtering (required)
            limit: Maximum results to return
            filters: Additional filters to apply
            update_access: Whether to update access counts
            timeline_id: Timeline ID to search in (default: "root")
            min_confidence: Minimum confidence threshold (default: 0.5)
        """
        from ..core.validation import validate_user_id

        user_id = validate_user_id(user_id, allow_none=False)
        search_filter = {"user_id": str(user_id)}

        # Resolve timeline ancestry for inheritance (if timeline service available)
        accessible_timelines = [timeline_id]  # Default to current timeline
        if self.timeline_service:
            try:
                # Use async version since we're in async context
                accessible_timelines = await self.timeline_service.get_timeline_ancestry(timeline_id)
            except (PyMongoError, OperationFailure, ValueError) as e:
                logger.warning(f"Failed to resolve timeline ancestry: {e}")
                accessible_timelines = [timeline_id]

        # Add timeline filter with inheritance
        search_filter["metadata.timeline_id"] = {"$in": accessible_timelines}

        # Add confidence filter - CRITICAL FIX: confidence is stored in metadata for vector search
        # Memories now store confidence in both top-level (backward compat) and metadata (for search)
        search_filter["metadata.confidence"] = {"$gte": min_confidence}

        # Memory type filtering (Cognitive Blueprint v2.0)
        memory_type_filter = filters.get("memory_type") if filters else None
        if memory_type_filter:
            search_filter["memory_type"] = memory_type_filter

        if filters:
            for key, value in filters.items():
                if key == "metadata" and isinstance(value, dict):
                    for k, v in value.items():
                        # Don't override timeline_id or confidence if already set
                        if k not in ["timeline_id", "confidence"]:
                            search_filter[f"metadata.{k}"] = v
                elif key not in [
                    "OR",
                    "AND",
                    "memory_type",
                    "user_id",  # Protect user_id from filter override
                ]:
                    search_filter[key] = value

        pipeline = [
            {
                "$vectorSearch": {
                    "index": self.index_name,
                    "path": "embedding",
                    "queryVector": query_vector,
                    "numCandidates": limit * 20,
                    "limit": limit * 2,
                    "filter": search_filter if search_filter else {},
                }
            },
            {"$addFields": {"similarity": {"$meta": "vectorSearchScore"}}},
            {
                "$project": {
                    "_id": 1,
                    "text": 1,
                    "metadata": 1,
                    "user_id": 1,
                    "created_at": 1,
                    "similarity": 1,
                    "importance": {"$ifNull": ["$importance", 0.5]},
                    "access_count": {"$ifNull": ["$access_count", 0]},
                    "confidence": {"$ifNull": ["$confidence", 0.8]},
                    "emotion": {"$ifNull": ["$emotion", 0.3]},
                    "emotion_type": {"$ifNull": ["$emotion_type", "neutral"]},
                    "category": 1,
                }
            },
        ]

        cursor = self.collection.aggregate(pipeline)
        docs = await cursor.to_list(length=limit * 2)
        results = []
        memory_ids_to_update = []

        # Per-user scoring weight overrides (from neuroplasticity adaptations)
        scoring_weights = await self._get_user_scoring_weights(user_id)

        # Build query context for the scoring strategy
        query_ctx = QueryContext(
            now=datetime.now(timezone.utc),
            user_id=user_id,
            scoring_weights=scoring_weights,
        )

        scoring_strategy = getattr(self, "_scoring_strategy", None)

        for doc in docs:
            similarity = doc.get("similarity", 0.0)
            importance = doc.get("importance", 0.5)
            access_count = doc.get("access_count", 0)
            emotion_type = doc.get("emotion_type", "neutral")

            # Build a MemoryDocument for the strategy
            mem_doc = MemoryDocument.from_doc(doc)

            # Delegate scoring to the pluggable strategy
            if scoring_strategy is not None:
                try:
                    combined_score = await scoring_strategy.score(mem_doc, query_ctx)
                except (RuntimeError, ValueError, TypeError, AttributeError, OSError, ConnectionError) as e:
                    logger.warning(f"Scoring strategy failed for doc, using fallback: {e}")
                    combined_score = similarity * importance
            else:
                combined_score = similarity * importance

            results.append(
                {
                    "id": str(doc["_id"]),
                    "memory": doc.get("text", ""),
                    "metadata": doc.get("metadata", {}),
                    "user_id": doc.get("user_id"),
                    "score": combined_score,
                    "similarity": similarity,
                    "importance": importance,
                    "effective_importance": combined_score / max(similarity, 1e-9),
                    "confidence": doc.get("confidence", 0.8),
                    "emotion": doc.get("emotion"),
                    "emotion_type": emotion_type,
                    "access_count": access_count,
                    "category": doc.get("category"),
                    "created_at": doc.get("created_at").isoformat() if doc.get("created_at") else None,
                }
            )
            memory_ids_to_update.append(doc["_id"])

        # Sort and limit
        results.sort(key=lambda x: x["score"], reverse=True)
        results = results[:limit]

        # Update access counts for ranking
        if update_access and memory_ids_to_update:
            await self._update_access_counts(memory_ids_to_update)

        return results

    async def _get_user_scoring_weights(self, user_id: str | None) -> dict[str, float]:
        """Get per-user scoring weight overrides from the neuroplasticity engine.

        Returns an empty dict if no engine is available or no adaptations exist,
        in which case the scoring formula falls back to hardcoded defaults.
        Zero overhead for non-adapted users.
        """
        if not user_id:
            return {}

        neuroplasticity = getattr(self, "_neuroplasticity_engine", None)
        if neuroplasticity is None:
            return {}

        try:
            return await neuroplasticity.get_scoring_weights(str(user_id))
        except (AttributeError, RuntimeError, ValueError, TypeError) as e:
            logger.debug(f"[Scoring] Could not fetch user scoring weights: {e}")
            return {}
