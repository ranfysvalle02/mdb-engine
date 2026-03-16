"""
Cognitive Memory Service
Advanced, customizable memory system with optional cognitive features.

Core Features (always available):
- Auto-Extraction of Facts (LLM-based)
- Direct Injection (Bypass LLM)
- Metadata Filtering (Bucket/User scoping)
- Automatic Re-embedding on Updates
- MongoDB Atlas Vector Search

Optional Cognitive Features (configurable):
- Importance Assessment: AI evaluates memory significance (0.1-1.0 scale)
- Memory Reinforcement: Similar memories strengthen existing memories
- Memory Merging: Related memories are combined
- Effective Importance: Combines raw importance with access frequency
- Perfect Recall: All memories preserved forever, ranked by importance + access

Cognitive features are enabled by default but can be disabled or customized via config.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from collections.abc import Callable, Coroutine
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from pymongo.errors import OperationFailure, PyMongoError

from ..core.validation import validate_bucket_id, validate_user_id
from ..llm.temperature import adjust_temperature_for_model
from ..observability.tracing import create_span
from .base import BaseMemoryService, MemoryServiceError
from .embedding import EmbeddingMixin
from .extraction import ExtractionMixin
from .merging import MergingMixin
from .reinforcement import ReinforcementMixin
from .scoring import ScoringMixin
from .storage import StorageMixin
from .verification import VerificationMixin

try:
    from litellm.exceptions import (
        APIError,
        AuthenticationError,
        NotFoundError,
        RateLimitError,
    )

    LITELLM_AVAILABLE = True
except ImportError:
    LITELLM_AVAILABLE = False
    APIError = RuntimeError
    AuthenticationError = RuntimeError
    NotFoundError = RuntimeError
    RateLimitError = RuntimeError

if TYPE_CHECKING:
    from mdb_engine.core.protocols import GraphServiceProtocol
    from mdb_engine.database.scoped_wrapper import (
        ScopedCollectionWrapper,
        ScopedMongoWrapper,
    )
    from mdb_engine.profile.service import ProfileService

# Sentinel value for required parameter (cannot be passed by user)
_REQUIRED_HARD_DELETE = object()

logger = logging.getLogger(__name__)


class CognitiveMemoryServiceError(MemoryServiceError):
    """Base exception for Cognitive Memory Service failures."""

    pass


# ============================================================================
# Cognitive Memory Service
# ============================================================================


class CognitiveMemoryService(
    StorageMixin,
    ExtractionMixin,
    ScoringMixin,
    ReinforcementMixin,
    MergingMixin,
    EmbeddingMixin,
    VerificationMixin,
    BaseMemoryService,
):
    """
    Advanced, customizable memory service with optional cognitive features.

    This is THE memory service - it includes all core functionality plus optional
    cognitive features that can be enabled/disabled via configuration.

    Core features (always available):
    - LLM fact extraction
    - Direct injection
    - MongoDB Atlas Vector Search
    - Metadata filtering
    - Automatic re-embedding

    Cognitive features (optional, enabled by default):
    - Importance assessment
    - Memory reinforcement
    - Memory merging
    - Access tracking
    - Perfect recall (all memories preserved forever)

    All memories are permanently accessible via semantic search.
    Ranking uses importance + access frequency.
    """

    def __init__(
        self,
        app_slug: str | None = None,
        config: dict[str, Any] | None = None,
        collection: ScopedCollectionWrapper | None = None,
        *,
        graph_service: Any = None,
        embedding_service: Any = None,
        llm_service: Any = None,
        _resolved: Any = None,
    ):
        """
        Initialize Cognitive Memory Service.

        Prefer using the builder via ``get_memory_service()`` or
        ``CognitiveMemoryServiceBuilder(...).build()``.
        Direct construction is still supported for backward compatibility.

        Args:
            app_slug: Application slug (required unless _resolved is provided)
            config: Configuration dictionary
            collection: ScopedCollectionWrapper instance (REQUIRED)
            graph_service: Optional GraphService instance
            embedding_service: Optional EmbeddingService instance
            llm_service: Optional LLMService instance
            _resolved: Pre-resolved config from the builder (internal use)
        """
        # Builder path -- all setup already done
        if _resolved is not None:
            self._apply_resolved(_resolved)
            return

        # Legacy direct-construction path -- delegate to the builder
        from .builder import CognitiveMemoryServiceBuilder

        builder = CognitiveMemoryServiceBuilder(
            app_slug=app_slug,
            config=config,
            collection=collection,
            graph_service=graph_service,
            embedding_service=embedding_service,
            llm_service=llm_service,
        )
        resolved = builder.build()
        # ``builder.build()`` already called ``__init__`` with ``_resolved``,
        # so we just need to copy the attributes from the built instance.
        self.__dict__.update(resolved.__dict__)

    def _apply_resolved(self, rc: Any) -> None:
        """Assign all attributes from a ``_ResolvedConfig``."""
        # Core
        self.config = rc.config
        self.app_slug = rc.app_slug
        self.collection_name = rc.collection_name
        self.index_name = rc.index_name
        self.embedding_model = rc.embedding_model
        self.chat_model = rc.chat_model
        self.memory_llm_model = rc.memory_llm_model
        self.embedding_dims = rc.embedding_dims
        self.infer = rc.infer
        self.extraction_provider = rc.extraction_provider
        self.temperature = rc.temperature

        # Cognitive
        self.enable_cognitive = rc.enable_cognitive
        self.similarity_threshold = rc.similarity_threshold
        self.reinforcement_factor = rc.reinforcement_factor
        self.merge_threshold_low = rc.merge_threshold_low
        self.merge_threshold_high = rc.merge_threshold_high
        self.duplicate_threshold = rc.duplicate_threshold

        # Categories
        self.categories_enabled = rc.categories_enabled
        self.custom_categories = rc.custom_categories

        # Memory types
        self.memory_types_enabled = rc.memory_types_enabled
        self.auto_detect_memory_type = rc.auto_detect_memory_type
        self.default_memory_type = rc.default_memory_type
        self.episodic_retention_days = rc.episodic_retention_days
        self.working_ttl_hours = rc.working_ttl_hours

        # Database
        self.collection = rc.collection
        self._db: ScopedMongoWrapper = rc.db
        self._client = rc.client
        self.db_name = rc.db_name

        # Services
        self._injected_llm_service = rc.injected_llm_service
        self._injected_embedding_service = rc.embedding_provider
        self.llm_available = rc.llm_available
        self.embedding_provider = rc.embedding_provider

        # Cognitive readiness
        self._cognitive_fields_ensured = rc.cognitive_fields_ensured
        self._cognitive_ready = rc.cognitive_ready

        # Verification
        self.verification_enabled = rc.verification_enabled
        self.verification_max_file_size_kb = rc.verification_max_file_size_kb

        # Schedule eager cognitive-field backfill if an event loop is running
        if self.enable_cognitive and not self._cognitive_fields_ensured:
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(self._ensure_cognitive_fields())
                task.add_done_callback(self._on_cognitive_fields_done)
            except RuntimeError:
                logger.debug("No event loop in __init__; cognitive fields will be ensured lazily")

        # Sub-systems
        self._graph_service = rc.graph_service
        self._graph_auto_extract = rc.graph_auto_extract
        self._profile_service: Any = None  # Set post-init via set_profile_service()
        self.timeline_service = rc.timeline_service
        self.persona_engine = rc.persona_engine

        # Neuroplasticity engine (initialized lazily when first async call is made)
        self._neuroplasticity_enabled = rc.neuroplasticity_enabled
        self._neuroplasticity_config = rc.neuroplasticity_config
        self._neuroplasticity_engine: Any = None
        if rc.neuroplasticity_enabled:
            try:
                from .neuroplasticity import NeuroplasticityEngine

                adaptations_collection = rc.db.get_collection(f"{rc.app_slug}_adaptations")
                reflective_collection = rc.db.get_collection(f"{rc.app_slug}_reflective_memory")
                self._neuroplasticity_engine = NeuroplasticityEngine(
                    app_slug=rc.app_slug,
                    adaptations_collection=adaptations_collection,
                    reflective_collection=reflective_collection,
                    memories_collection=rc.collection,
                    llm_service=rc.injected_llm_service,
                    config=rc.neuroplasticity_config,
                )
                logger.info("NeuroplasticityEngine attached to CognitiveMemoryService")
            except (ImportError, AttributeError, TypeError, ValueError) as e:
                logger.warning(f"Failed to initialize NeuroplasticityEngine: {e}")

        # Pluggable strategies (from builder)
        self._scoring_strategy = rc.scoring_strategy
        self._decay_strategy = rc.decay_strategy
        self._extraction_strategy = rc.extraction_strategy
        self._importance_strategy = rc.importance_strategy
        self._persona_strategy = rc.persona_strategy
        self._reflection_strategy = rc.reflection_strategy

        # Wire the LLM completion function into the LLMImportance default
        # strategy so it can call the service's LLM without a direct reference.
        if self._importance_strategy is not None:
            _llm_fn = getattr(self._importance_strategy, "_llm_fn", None)
            if _llm_fn is None and hasattr(self._importance_strategy, "_llm_fn"):
                try:
                    setattr(self._importance_strategy, "_llm_fn", self._llm_completion)  # noqa: B010
                except (AttributeError, TypeError):
                    pass

        # Auxiliary indexes deferred from builder (Motor requires await)
        self._aux_indexes_created = False

    async def _ensure_aux_indexes(self) -> None:
        """Create auxiliary indexes lazily (Motor requires ``await``)."""
        if self._aux_indexes_created:
            return
        self._aux_indexes_created = True
        try:
            from .builder import CognitiveMemoryServiceBuilder

            await CognitiveMemoryServiceBuilder.create_auxiliary_indexes(self.collection)
        except (ImportError, AttributeError, TypeError, ValueError) as e:
            logger.debug(f"Auxiliary index creation skipped: {e}")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def graph_service(self) -> GraphServiceProtocol | None:
        """Get the injected GraphService for GraphRAG functionality."""
        return self._graph_service

    @property
    def profile_service(self) -> ProfileService | None:
        """Get the injected ProfileService (None if not configured)."""
        return self._profile_service

    @property
    def embedding_service(self) -> Any | None:
        """Get the EmbeddingService (or EmbeddingProvider) used by this memory service."""
        return self._injected_embedding_service

    @property
    def llm_service(self) -> Any | None:
        """Get the LLMService used by this memory service (None if not injected)."""
        return self._injected_llm_service

    def set_profile_service(self, service: Any) -> None:
        """Inject the profile service after initialization.

        Called by ``ServiceInitializer`` after both memory and profile
        services are initialized (profile depends on memory).
        """
        self._profile_service = service
        logger.info(f"[Memory] Profile service injected for app '{self.app_slug}'")

    @staticmethod
    def _fallback_persona_blend(
        query_vector: list[float],
        persona_vector: list[float],
    ) -> list[float]:
        """Inline 80/20 persona blending fallback (original behaviour)."""
        blended = [0.8 * q + 0.2 * p for q, p in zip(query_vector, persona_vector, strict=False)]
        magnitude = math.sqrt(sum(x * x for x in blended))
        if magnitude > 0:
            blended = [x / magnitude for x in blended]
        return blended

    # ------------------------------------------------------------------
    # Cognitive field lazy initialization
    # ------------------------------------------------------------------

    def _on_cognitive_fields_done(self, task: asyncio.Task) -> None:
        """Callback for the eager cognitive-fields background task."""
        exc = task.exception()
        if exc:
            logger.warning(f"Failed to ensure cognitive fields in background: {exc}")
        else:
            self._cognitive_fields_ensured = True
        # Always signal readiness so waiting methods don't hang forever.
        # On failure the flag stays False and _maybe_ensure will retry.
        self._cognitive_ready.set()

    async def _maybe_ensure_cognitive_fields(self) -> None:
        """Lazily ensure cognitive fields exist (called on first async operation).

        If the eager background task is still running, waits for it to finish
        (up to 10 s) before falling back to a synchronous attempt.
        """
        if self._cognitive_fields_ensured:
            return
        # Wait for the background task started in __init__ (if any).
        try:
            await asyncio.wait_for(self._cognitive_ready.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("Timed out waiting for cognitive fields background task")
        if not self._cognitive_fields_ensured and self.enable_cognitive:
            await self._ensure_cognitive_fields()
            self._cognitive_fields_ensured = True
            self._cognitive_ready.set()

    # ------------------------------------------------------------------
    # Graph link helpers
    # ------------------------------------------------------------------

    def _init_graph_links(
        self,
        derived_from: list[str] | None = None,
        contradicts: list[str] | None = None,
        deprecated: bool = False,
    ) -> dict[str, Any]:
        """
        Initialize graph_links field for memory documents.

        Args:
            derived_from: List of memory IDs this memory is derived from
            contradicts: List of memory IDs this memory contradicts
            deprecated: Whether this memory is deprecated

        Returns:
            graph_links dictionary
        """
        return {
            "derived_from": derived_from if derived_from else [],
            "contradicts": contradicts if contradicts else [],
            "deprecated": deprecated,
        }

    # ------------------------------------------------------------------
    # LLM client helpers (kept here – tightly coupled with __init__)
    # ------------------------------------------------------------------

    def _get_adjusted_temperature(self, model: str | None = None) -> float:
        """
        Get temperature adjusted for model requirements.

        - Gemini models: Always use temperature=1.0
        - Azure OpenAI and OpenAI: Always use temperature=0.3

        Args:
            model: Model name (e.g., "gemini/gemini-3-flash-preview",
                "openai/gpt-4o", "azure/gpt-4o")

        Returns:
            Adjusted temperature value
        """
        if not model:
            return self.temperature

        return adjust_temperature_for_model(
            model=model,
            requested_temperature=self.temperature,
            log=logger,
        )

    def _init_llm_client(self):
        """
        Initialize LiteLLM for LLM fact extraction
        (fact extraction, importance assessment, merging).

        Uses LiteLLM to support 100+ LLM providers.
        Auto-detects from environment variables.
        Model format: "provider/model"
        (e.g., "openai/gpt-4o", "azure/gpt-4o", "gemini/gemini-3-flash-preview")
        """
        if not LITELLM_AVAILABLE:
            if self.infer:
                logger.warning(
                    "LiteLLM not available. Memory extraction will fail when infer=True. "
                    "Install with: pip install litellm"
                )
            self.llm_available = False
            return

        # Check if any LLM credentials are available
        has_openai = bool(os.getenv("OPENAI_API_KEY"))
        has_azure = bool(os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("AZURE_OPENAI_ENDPOINT"))
        has_gemini = bool(os.getenv("GEMINI_API_KEY"))
        has_anthropic = bool(os.getenv("ANTHROPIC_API_KEY"))

        if not (has_openai or has_azure or has_gemini or has_anthropic):
            if self.infer:
                logger.warning(
                    "No LLM API keys found. Set OPENAI_API_KEY, AZURE_OPENAI_API_KEY, "
                    "GEMINI_API_KEY, ANTHROPIC_API_KEY, or other provider keys. "
                    "Memory extraction will fail when infer=True."
                )
            self.llm_available = False
            return

        try:
            self.llm_available = True
            logger.info(f"Using LiteLLM for memory operations (model: {self.memory_llm_model})")
        except (
            ValueError,
            TypeError,
            AttributeError,
            RuntimeError,
            ImportError,
        ) as e:
            # Catch specific exceptions that can occur during LiteLLM initialization
            logger.exception(f"Failed to initialize LiteLLM: {e}")
            self.llm_available = False

    async def _llm_completion(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = None,
        response_format: Any = None,
        **kwargs: Any,
    ) -> Any:
        """
        Execute an LLM completion using injected LLMService.

        This method provides a unified async interface for LLM calls using the
        injected LLMService.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model to use (defaults to self.memory_llm_model)
            temperature: Temperature setting (auto-adjusted for certain models)
            response_format: Optional response format for structured outputs
            **kwargs: Additional arguments passed to LLM service

        Returns:
            LiteLLM-style response object (wrapped from LLMService string response)

        Raises:
            MemoryServiceError: If LLM service is not available
        """
        if self._injected_llm_service is None:
            raise MemoryServiceError("LLM service not available. _llm_completion requires an injected LLMService.")

        model = model or self.memory_llm_model
        if temperature is None:
            temperature = self._get_adjusted_temperature(model)

        try:
            result = await self._injected_llm_service.chat_completion(
                messages=messages,
                provider_name="chat",
                model=model,
                temperature=temperature,
                response_format=response_format,
                **kwargs,
            )

            # Wrap result in a LiteLLM-like response object
            from types import SimpleNamespace

            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=result))])
        except (
            RuntimeError,
            AttributeError,
            ValueError,
            TypeError,
            ConnectionError,
            TimeoutError,
            MemoryServiceError,
        ) as e:
            logger.error(f"LLM completion failed: {e}", exc_info=True)
            raise MemoryServiceError(f"LLM completion failed: {str(e)}") from e

    # ------------------------------------------------------------------
    # Cognitive field maintenance
    # ------------------------------------------------------------------

    async def _ensure_cognitive_fields(self):
        """Ensure all existing memories have cognitive fields."""
        try:
            now = datetime.now(timezone.utc)
            # Add default importance and access_count to existing memories without them
            await self.collection.update_many(
                {"importance": {"$exists": False}},
                {
                    "$set": {
                        "importance": 0.5,
                        "access_count": 0,
                        "last_accessed": now,
                    }
                },
            )
            # Add deduplication fields (last_mentioned, mention_count) if missing
            await self.collection.update_many(
                {"mention_count": {"$exists": False}},
                {
                    "$set": {
                        "mention_count": 1,
                        "last_mentioned": now,
                    }
                },
            )
            # Add emotion_type to existing memories without it (backward compat)
            await self.collection.update_many(
                {"emotion_type": {"$exists": False}},
                {"$set": {"emotion_type": "neutral"}},
            )
            # Backfill metadata.confidence for $vectorSearch pre-filter compatibility.
            # First, copy root-level confidence into metadata for docs that have it.
            await self.collection.update_many(
                {
                    "metadata.confidence": {"$exists": False},
                    "confidence": {"$exists": True},
                },
                [{"$set": {"metadata.confidence": "$confidence"}}],
            )
            # Then default remaining docs (no confidence anywhere) to 0.8.
            await self.collection.update_many(
                {"metadata.confidence": {"$exists": False}},
                {"$set": {"metadata.confidence": 0.8}},
            )
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to ensure cognitive fields: {e}")

    # ------------------------------------------------------------------
    # Persona helpers
    # ------------------------------------------------------------------

    async def get_persona(self) -> dict[str, Any] | None:
        """
        Get current persona for the app.

        Returns:
            Persona document or None if persona is disabled
        """
        if not self.persona_engine:
            return None
        return await self.persona_engine.get_persona()

    async def update_persona(
        self,
        role: str | None = None,
        description: str | None = None,
        traits: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """
        Update persona configuration.

        Args:
            role: New persona role
            description: New persona description
            traits: New persona traits dictionary

        Returns:
            Updated persona document

        Raises:
            CognitiveMemoryServiceError: If persona is disabled
        """
        if not self.persona_engine:
            raise CognitiveMemoryServiceError("Persona feature is disabled")
        return await self.persona_engine.update_persona(role=role, description=description, traits=traits)

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    async def get_memory_analytics(
        self,
        user_id: str,
    ) -> dict[str, Any]:
        """
        Get comprehensive analytics about a user's memory health.

        Perfect Recall: All memories are accessible forever.

        Args:
            user_id: User ID to analyze

        Returns:
            Analytics dictionary with various metrics including:
            - Memory growth trends
            - Access patterns
            - Quality metrics with health score
            - Graph connectivity
            - Timeline organization
            - Category trends
            - Top memories
            - Merge statistics
        """
        from .analytics import get_memory_analytics

        return await get_memory_analytics(
            collection=self.collection,
            user_id=user_id,
            enable_cognitive=self.enable_cognitive,
            detect_category_fn=self._detect_category_from_text,
        )

    # --- Conflict Resolution Layer (Integrity Check) ---

    async def detect_knowledge_conflict(
        self,
        user_id: str,
        new_fact: str,
        similarity_threshold: float = 0.85,
        llm_model: str | None = None,
    ) -> str | None:
        """
        Check if new information conflicts with existing knowledge.

        This implements the "Integrity Layer" that prevents the AI from developing
        "digital dementia" - holding contradictory facts as equally true.

        Args:
            user_id: User ID to check conflicts for
            new_fact: The new fact/information to check
            similarity_threshold: Minimum similarity to consider related (default: 0.85)
            llm_model: Override LLM model for conflict detection

        Returns:
            Conflict description if found, None if no conflict
        """
        if not self.llm_available:
            logger.warning("[Conflict Check] LLM not available, skipping conflict detection")
            return None
        from .conflict import detect_knowledge_conflict

        return await detect_knowledge_conflict(
            user_id=user_id,
            new_fact=new_fact,
            similarity_threshold=similarity_threshold,
            llm_model=llm_model,
            get_embedding_fn=self._get_embedding,
            find_similar_fn=self._find_similar_memories,
            llm_completion_fn=self._llm_completion,
            memory_llm_model=self.memory_llm_model,
        )

    # --- Core Operations ---

    async def inject(
        self,
        memory: str | dict[str, Any],
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        bucket_id: str | None = None,
        bucket_type: str | None = None,
        raw_content: str | None = None,
        citations: list[dict[str, Any]] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """
        Direct Injection: Bypasses LLM extraction. Useful for system instructions or manual entries.
        """
        user_id = validate_user_id(user_id, allow_none=True)
        bucket_id = validate_bucket_id(bucket_id, allow_none=True)
        await self._maybe_ensure_cognitive_fields()

        # 1. Parse Input
        if isinstance(memory, dict):
            memory_content = memory.get("memory") or memory.get("text")
            if "metadata" in memory:
                merged_meta = memory["metadata"]
                if metadata:
                    merged_meta.update(metadata)
                metadata = merged_meta
        else:
            memory_content = str(memory)

        if not memory_content:
            raise ValueError("Memory content cannot be empty")

        # 2. Metadata Setup
        final_metadata = dict(metadata) if metadata else {}
        if bucket_id:
            final_metadata["bucket_id"] = bucket_id
        if bucket_type:
            final_metadata["bucket_type"] = bucket_type
        if raw_content:
            final_metadata["raw_content"] = raw_content
        if citations:
            final_metadata["citations"] = citations

        # 4. Store
        vector = await self._get_embedding(memory_content)

        # Extract optional cognitive parameters from kwargs, falling back to metadata dict
        _meta = metadata or {}
        importance = kwargs.get("importance", _meta.get("importance", 0.5))
        emotion = kwargs.get("emotion", _meta.get("emotion", 0.3))
        emotion_type = kwargs.get("emotion_type", _meta.get("emotion_type", "neutral"))
        confidence = kwargs.get("confidence", _meta.get("confidence", 0.8))
        memory_type = kwargs.get("memory_type")
        timeline_id = kwargs.get("timeline_id", "root")  # Default to root timeline
        derived_from = kwargs.get("derived_from")
        contradicts = kwargs.get("contradicts")

        # Detect memory type if not provided and auto-detection is enabled
        if not memory_type and self.memory_types_enabled and self.auto_detect_memory_type:
            memory_type = await self._detect_memory_type(memory_content)
        elif not memory_type:
            memory_type = self.default_memory_type

        # Ensure timeline_id in metadata
        if "timeline_id" not in final_metadata:
            final_metadata["timeline_id"] = timeline_id

        # Store confidence in metadata for $vectorSearch pre-filter compatibility
        if "confidence" not in final_metadata:
            final_metadata["confidence"] = confidence

        doc = {
            "text": memory_content,
            "embedding": vector,
            "user_id": str(user_id) if user_id else None,
            "metadata": final_metadata,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "graph_links": self._init_graph_links(derived_from=derived_from, contradicts=contradicts),
        }

        # Add memory type (Cognitive Blueprint v2.0)
        if self.memory_types_enabled:
            doc["memory_type"] = memory_type

            # Add TTL for episodic memories
            if memory_type == "episodic":
                expires_at = datetime.now(timezone.utc) + timedelta(days=self.episodic_retention_days)
                doc["expires_at"] = expires_at

        # Add cognitive fields if enabled
        if self.enable_cognitive:
            doc["importance"] = importance
            doc["access_count"] = 0
            doc["last_accessed"] = datetime.now(timezone.utc)
            doc["mention_count"] = 1
            doc["last_mentioned"] = datetime.now(timezone.utc)
            doc["emotion"] = emotion
            doc["emotion_type"] = emotion_type
            doc["confidence"] = confidence

        result = await self.collection.insert_one(doc)
        logger.info(f"Injected memory {result.inserted_id}")

        result_dict = {
            "id": str(result.inserted_id),
            "memory": memory_content,
            "metadata": final_metadata,
            "user_id": str(user_id) if user_id else None,
            "importance": doc.get("importance"),
            "emotion": doc.get("emotion"),
            "emotion_type": doc.get("emotion_type", "neutral"),
            "confidence": doc.get("confidence", 0.8),
            "created_at": doc["created_at"].isoformat(),
        }

        # Add memory_type to result (Cognitive Blueprint v2.0)
        if self.memory_types_enabled and "memory_type" in doc:
            result_dict["memory_type"] = doc["memory_type"]

        return result_dict

    async def add(
        self,
        messages: str | list[dict[str, str]],
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        bucket_id: str | None = None,
        bucket_type: str | None = None,
        raw_content: str | None = None,
        citations: list[dict[str, Any]] | None = None,
        progress_callback: Callable[[str, str, int], Coroutine[Any, Any, None] | None]
        | None = None,  # Can be sync (returns None) or async (returns Coroutine)
        **kwargs,
    ) -> list[dict[str, Any]]:
        """
        Intelligent Add with optional cognitive memory management.

        Uses parallel processing internally for optimal performance:
        - Batch embedding (single API call for all facts)
        - Parallel vector searches
        - Parallel importance assessments
        - Bulk database operations

        Pipeline:
        1. Extract facts (sequential -- single LLM call)
        2. Batch embed ALL facts (single API call)
        3. Parallel vector searches (concurrent with semaphore)
        4. Classify: reinforce, merge, or create new
        5. Parallel importance assessments (concurrent with semaphore)
        6. Bulk DB operations
        7. Graph extraction (if ``memory_config.graph.enabled``, non-blocking)
        """
        user_id = validate_user_id(user_id, allow_none=True)
        bucket_id = validate_bucket_id(bucket_id, allow_none=True)

        with create_span("memory.add", {"memory.user_id": user_id or ""}) as parent_span:
            await self._maybe_ensure_cognitive_fields()
            await self._ensure_aux_indexes()

            # Phase 1: normalise input
            with create_span("memory.add.normalize_input"):
                input_text = self._normalize_add_input(messages)
                if not input_text:
                    return []

            # Phase 2: extract facts
            with create_span("memory.add.extract_facts"):
                extracted_facts = await self._extract_facts_for_add(input_text, **kwargs)
                if not extracted_facts:
                    logger.info("[Memory Add] No facts extracted.")
                    return []

            parent_span.set_attribute("memory.extracted_fact_count", len(extracted_facts))

            # Phase 3: intra-message deduplication
            with create_span("memory.add.dedup_extracted"):
                extracted_facts = await self._dedup_extracted(extracted_facts, progress_callback)

            # Phase 4: prepare metadata
            with create_span("memory.add.prepare_metadata"):
                final_metadata = self._prepare_add_metadata(metadata, bucket_id, bucket_type, raw_content, citations)

            # Phase 5: batch embed
            with create_span("memory.add.batch_embed"):
                valid_facts, valid_embeddings = await self._embed_facts(extracted_facts, progress_callback)
                if not valid_facts:
                    logger.warning("[Memory Add] No valid embeddings generated")
                    return []

            # Phase 6: parallel similarity search
            with create_span("memory.add.vector_search"):
                all_similar = await self._find_similar_for_facts(user_id, valid_embeddings)

            # Phase 7: classify into actions
            with create_span("memory.add.classify"):
                classified = self._classify_facts(valid_facts, all_similar)

            # Phase 8: importance assessment
            with create_span("memory.add.importance_assessment"):
                importance_map = await self._assess_importance_for_add(
                    valid_facts,
                    classified,
                    progress_callback,
                )

            # Phase 9: execute DB operations
            with create_span("memory.add.db_operations"):
                await self._notify_progress(progress_callback, "storing", "Storing memories...", 85)
                stored_memories = await self._execute_async_actions(
                    user_id=user_id,
                    input_text=input_text,
                    valid_facts=valid_facts,
                    valid_embeddings=valid_embeddings,
                    importance_map=importance_map,
                    final_metadata=final_metadata,
                    facts_to_duplicate=classified["duplicate"],
                    facts_to_reinforce=classified["reinforce"],
                    facts_to_merge=classified["merge"],
                    facts_to_create=classified["create"],
                    messages=messages,
                )

            # Phase 10: post-processing (graph extraction, profile update)
            with create_span("memory.add.post_processing"):
                await self._post_process_add(user_id, stored_memories)

            parent_span.set_attribute("memory.stored_count", len(stored_memories))

            await self._notify_progress(
                progress_callback,
                "complete",
                f"Stored {len(stored_memories)} memories",
                100,
            )
            logger.info(
                "[Memory Add] Complete: %d memories stored (from %d extracted facts)",
                len(stored_memories),
                len(extracted_facts),
            )
            return stored_memories

    # ------------------------------------------------------------------
    # add() helper methods — one per phase
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_add_input(messages: str | list[dict[str, str]]) -> str:
        """Phase 1: normalise messages into a single text string."""
        if isinstance(messages, list):
            return "\n".join(m.get("content", "") for m in messages if m.get("content"))
        return str(messages).strip()

    async def _extract_facts_for_add(
        self,
        input_text: str,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Phase 2: extract facts using the appropriate strategy."""
        infer = kwargs.get("infer", self.infer)
        logger.info("[Memory Add] Processing (infer=%s, cognitive=%s)", infer, self.enable_cognitive)

        use_cognitive = self.enable_cognitive and self.config.get("use_emotion_extraction", True)

        if infer and use_cognitive:
            return await self._extract_facts_cognitive(input_text)

        if infer and self.categories_enabled:
            categorized = await self._extract_facts_with_categories(input_text)
            return [
                {"text": f["text"], "category": f["category"], "emotion": 0.3, "emotion_type": "neutral"}
                for f in categorized
            ]

        if infer:
            facts = await self._extract_facts(input_text)
            return [{"text": f, "category": "general", "emotion": 0.3, "emotion_type": "neutral"} for f in facts]

        return [{"text": input_text, "category": "general", "emotion": 0.3, "emotion_type": "neutral"}]

    async def _dedup_extracted(
        self,
        facts: list[dict[str, Any]],
        progress_callback: Any = None,
    ) -> list[dict[str, Any]]:
        """Phase 3: deduplicate extracted facts within the same batch."""
        if len(facts) <= 1:
            return facts

        await self._notify_progress(
            progress_callback,
            "deduplicating",
            f"Checking for duplicates in {len(facts)} facts...",
            55,
        )
        deduped = await self._deduplicate_extracted_facts(facts)
        await self._notify_progress(
            progress_callback,
            "deduplicating",
            f"Deduplication complete: {len(deduped)} unique facts",
            60,
        )
        return deduped

    @staticmethod
    def _prepare_add_metadata(
        metadata: dict[str, Any] | None,
        bucket_id: str | None,
        bucket_type: str | None,
        raw_content: str | None,
        citations: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Phase 4: build the final metadata dict."""
        final = dict(metadata) if metadata else {}
        if bucket_id:
            final["bucket_id"] = bucket_id
            final.setdefault("associated_bucket_id", bucket_id)
        if bucket_type:
            final["bucket_type"] = bucket_type
        if raw_content:
            final["raw_content"] = raw_content
        if citations:
            final["citations"] = citations
        return final

    async def _embed_facts(
        self,
        facts: list[dict[str, Any]],
        progress_callback: Any = None,
    ) -> tuple[list[dict[str, Any]], list[list[float]]]:
        """Phase 5: batch-embed all facts in a single API call."""
        texts = [f.get("text", "") for f in facts]
        logger.info("[Batch Embed] Embedding %d facts...", len(texts))

        await self._notify_progress(
            progress_callback,
            "embedding",
            f"Generating embeddings for {len(texts)} facts...",
            62,
        )
        embeddings_map = await self._get_embeddings_batch(texts)
        await self._notify_progress(
            progress_callback,
            "embedding",
            f"Generated {len(embeddings_map)} embeddings",
            65,
        )

        valid_facts: list[dict[str, Any]] = []
        valid_embeddings: list[list[float]] = []
        for fact_data in facts:
            text = fact_data.get("text", "").replace("\n", " ").strip()
            if text in embeddings_map:
                valid_facts.append(fact_data)
                valid_embeddings.append(embeddings_map[text])

        logger.info("[Batch Embed] Got %d embeddings", len(valid_embeddings))
        return valid_facts, valid_embeddings

    async def _find_similar_for_facts(
        self,
        user_id: str | None,
        embeddings: list[list[float]],
    ) -> list[list[dict[str, Any]]]:
        """Phase 6: parallel vector search for each fact's embedding."""
        if not self.enable_cognitive:
            return [[] for _ in embeddings]

        logger.info("[Parallel Search] Running %d searches...", len(embeddings))
        return await self._find_similar_memories_parallel(
            user_id=user_id,
            embeddings=embeddings,
            top_n=5,
        )

    def _classify_facts(
        self,
        valid_facts: list[dict[str, Any]],
        all_similar: list[list[dict[str, Any]]],
    ) -> dict[str, list[Any]]:
        """Phase 7: classify each fact into duplicate/reinforce/merge/create."""
        classified: dict[str, list[Any]] = {
            "duplicate": [],
            "reinforce": [],
            "merge": [],
            "create": [],
        }

        for idx, (_fact, similar) in enumerate(zip(valid_facts, all_similar, strict=False)):
            if not self.enable_cognitive or not similar:
                classified["create"].append(idx)
                continue

            top = similar[0]
            sim = top.get("similarity", 0.0)

            if sim > self.duplicate_threshold:
                classified["duplicate"].append((idx, top))
            elif sim > self.merge_threshold_high:
                classified["reinforce"].append((idx, top))
            elif sim > self.merge_threshold_low:
                classified["merge"].append((idx, top))
            else:
                classified["create"].append(idx)

        logger.info(
            "[Classify] %d duplicates, %d reinforce, %d merge, %d new",
            len(classified["duplicate"]),
            len(classified["reinforce"]),
            len(classified["merge"]),
            len(classified["create"]),
        )
        return classified

    async def _assess_importance_for_add(
        self,
        valid_facts: list[dict[str, Any]],
        classified: dict[str, list[Any]],
        progress_callback: Any = None,
    ) -> dict[str, float]:
        """Phase 8: parallel importance assessment for new + merge facts."""
        importance_map: dict[str, float] = {}

        if not self.enable_cognitive:
            return importance_map

        if classified["create"]:
            texts = [valid_facts[idx].get("text", "") for idx in classified["create"]]
            await self._notify_progress(
                progress_callback,
                "importance",
                f"Assessing importance for {len(texts)} facts...",
                75,
            )
            importance_map = await self._assess_importance_parallel(texts)
            await self._notify_progress(
                progress_callback,
                "importance",
                "Importance assessment complete",
                80,
            )

        if classified["merge"]:
            merge_texts = [valid_facts[idx].get("text", "") for idx, _ in classified["merge"]]
            merge_imp = await self._assess_importance_parallel(merge_texts)
            importance_map.update(merge_imp)

        return importance_map

    async def _post_process_add(
        self,
        user_id: str | None,
        stored: list[dict[str, Any]],
    ) -> None:
        """Phase 10: graph extraction + profile update (non-blocking)."""
        if not user_id or not stored:
            return

        if self._graph_auto_extract:
            try:
                for mem in stored:
                    text = mem.get("memory", "")
                    if text:
                        await self._graph_service.extract_graph_from_text(
                            text,
                            str(user_id),
                            source_memory_id=mem.get("id"),
                        )
                logger.info("[Graph] Extracted graph nodes from %d memories", len(stored))
            except (ConnectionError, TimeoutError, ValueError, RuntimeError) as e:
                logger.warning("[Graph] Extraction failed (non-fatal): %s", e)

        if self._profile_service:
            try:
                await self._profile_service.incremental_update(
                    user_id=str(user_id),
                    new_memories=stored,
                )
                logger.info("[Profile] Incremental update triggered for %d memories", len(stored))
            except (ConnectionError, TimeoutError, ValueError, RuntimeError) as e:
                logger.warning("[Profile] Incremental update failed (non-fatal): %s", e)

    @staticmethod
    async def _notify_progress(
        callback: Any,
        stage: str,
        message: str,
        progress: int,
    ) -> None:
        """Fire a progress callback if provided (handles sync or async)."""
        if not callback:
            return
        result = callback(stage=stage, message=message, progress=progress)
        if asyncio.iscoroutine(result):
            await result

    # --- Search Operations ---

    async def search(
        self,
        query: str,
        user_id: str | None = None,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
        timeline_id: str = "root",
        min_confidence: float = 0.5,
        **kwargs,
    ) -> list[dict[str, Any]]:
        """
        Perfect Recall Semantic Search.

        Results ranked by: similarity * effective_importance
        Where effective_importance = importance * (1 + ln(access_count + 1))

        All memories are permanently accessible.

        Args:
            query: Search query string
            user_id: User ID to scope search
            limit: Maximum results to return
            filters: Additional filters to apply
            timeline_id: Timeline ID to search in (default: "root")
            min_confidence: Minimum confidence threshold (default: 0.5)
            **kwargs: Additional arguments:
                - update_access: Update access counts for retrieved memories (default: True)
        """
        # Validate user_id BEFORE the try/except so the error propagates
        user_id = validate_user_id(user_id, allow_none=False)
        await self._ensure_aux_indexes()
        await self._maybe_ensure_cognitive_fields()

        update_access = kwargs.get("update_access", True)

        logger.info(
            f"[Memory Search] query_len={len(query)}, "
            f"user_id={user_id}, limit={limit}, timeline_id={timeline_id}, min_confidence={min_confidence}"
        )
        try:
            # 1. Vectorize Query
            query_vector = await self._get_embedding(query)
            if not query_vector:
                logger.warning("[Memory Search] Failed to generate embedding for query")
                return []

            # 1b. Apply persona filtering if enabled (via persona strategy)
            if self.persona_engine:
                persona_vector = await self.persona_engine.get_persona_vector()
                if persona_vector and len(persona_vector) == len(query_vector):
                    persona_strategy = getattr(self, "_persona_strategy", None)
                    if persona_strategy is not None:
                        persona_cfg = self.config.get("persona_blending", {})
                        try:
                            query_vector = await persona_strategy.blend(
                                query_vector,
                                persona_vector,
                                persona_cfg,
                            )
                        except (RuntimeError, ValueError, TypeError, AttributeError, OSError) as e:
                            logger.warning(f"Persona strategy failed, using fallback: {e}")
                            query_vector = self._fallback_persona_blend(
                                query_vector,
                                persona_vector,
                            )
                    else:
                        query_vector = self._fallback_persona_blend(
                            query_vector,
                            persona_vector,
                        )
                    logger.debug("Applied persona filtering to search query")

            # 2. Semantic search ranked by similarity * effective_importance
            timeline_id = kwargs.get("timeline_id", timeline_id)
            min_confidence = kwargs.get("min_confidence", min_confidence)
            memories = await self._search(
                query_vector=query_vector,
                user_id=user_id,
                limit=limit,
                filters=filters,
                update_access=update_access,
                timeline_id=timeline_id,
                min_confidence=min_confidence,
            )

            # 3. JIT Verification
            if self.verification_enabled:
                memories = await self.verify_memories(memories)

            return memories

        except (
            PyMongoError,
            OperationFailure,
            CognitiveMemoryServiceError,
            ValueError,
            TypeError,
            RuntimeError,
            ConnectionError,
            OSError,
        ) as e:
            logger.error(f"[Memory Search] Search failed: {e}", exc_info=True)
            return []

    # --- CRUD, Graph Link, and Timeline operations provided by StorageMixin ---
    # (get, get_all, update, delete, delete_all,
    #  add_memory_with_links, mark_contradiction,
    #  fork_timeline, get_current_timeline, switch_timeline)


# Public alias — simpler name for the primary memory service.
# "CognitiveMemoryService" remains importable for backwards compatibility.
MemoryService = CognitiveMemoryService
