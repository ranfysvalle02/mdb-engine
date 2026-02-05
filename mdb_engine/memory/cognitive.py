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
- Memory Decay: Less relevant memories fade over time
- Memory Merging: Related memories are combined
- Memory Pruning: Least important memories removed when capacity exceeded
- Effective Importance: Combines raw importance with access frequency

Cognitive features are enabled by default but can be disabled or customized via config.
"""

import asyncio
import json
import logging
import math
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

# Import redaction service from standalone module
from ..redaction import get_redaction_service
from .base import BaseMemoryService, MemoryServiceError

# Optional Pydantic import for structured output
try:
    from pydantic import BaseModel, Field

    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    BaseModel = None
    Field = None

# Required: Direct PyMongo access
try:
    from bson import ObjectId
    from bson.errors import InvalidId
    from pymongo.errors import OperationFailure, PyMongoError
except ImportError:
    raise ImportError(
        "Missing critical dependencies. Please install: pip install pymongo"
    ) from None

# Optional LiteLLM import for LLM fact extraction (supports 100+ providers)
try:
    import litellm
    from litellm import acompletion, completion
    from litellm.exceptions import (
        APIError,
        AuthenticationError,
        NotFoundError,
        RateLimitError,
    )

    LITELLM_AVAILABLE = True
except ImportError:
    LITELLM_AVAILABLE = False
    litellm = None
    completion = None
    acompletion = None
    APIError = RuntimeError
    AuthenticationError = RuntimeError
    NotFoundError = RuntimeError
    RateLimitError = RuntimeError

# Concurrent futures for parallel execution
import concurrent.futures

# ============================================================================
# Internal Parallelization Constants (not user-configurable)
# ============================================================================
_PARALLEL_SEMAPHORE_LIMIT = 10  # Max concurrent operations for vector search/importance
_EMBEDDING_BATCH_SIZE = 100  # Split large embedding batches (OpenAI limit ~2048)

# Sentinel value for required parameter (cannot be passed by user)
_REQUIRED_HARD_DELETE = object()

logger = logging.getLogger(__name__)

# Sentinel value for required parameter (cannot be passed by user)
_REQUIRED_HARD_DELETE = object()

# ============================================================================
# Cognitive Math Layer - Ebbinghaus Forgetting Curve & Spacing Effect
# ============================================================================


class CognitiveMath:
    """
    Mathematical layer implementing biological memory dynamics.

    Based on the Ebbinghaus Forgetting Curve and Spacing Effect, this class
    provides formulas for calculating memory strength over time.

    Key formulas:
    - Retrieval Strength: S = R * exp(-t / H)
      - S: Current strength (how "present" the memory is)
      - R: Raw importance (0.1 to 1.0)
      - t: Time since last access (hours)
      - H: Stability (half-life in hours, increases with rehearsal)

    - Spacing Effect: H_new = H_old * (1.2 + similarity + emotion * 1.5)
      - Every time a memory is retrieved, it becomes harder to forget
      - High emotion creates "flashbulb" memories that resist decay
    """

    # Default stability in hours (24 hours = 1 day half-life)
    DEFAULT_STABILITY_HOURS = 24.0

    # Minimum stability to prevent division by zero
    MIN_STABILITY = 0.1

    # Maximum stability (effectively permanent memory)
    MAX_STABILITY = 10000.0

    # Flashbulb memory threshold (high emotion)
    FLASHBULB_THRESHOLD = 0.7

    # Maximum stability multiplier for high-emotion memories
    MAX_STABILITY_MULTIPLIER = 100.0

    @staticmethod
    def get_current_strength(doc: dict) -> float:
        """
        Calculate current Retrieval Strength using the Ebbinghaus Forgetting Curve.

        Formula: S = R * exp(-t / H)

        Where:
        - S: Retrieval Strength (0.0 to 1.0)
        - R: Raw importance (from doc['importance'])
        - t: Hours since last access
        - H: Stability (from doc['stability'], defaults to 24 hours)

        Args:
            doc: Memory document with 'importance', 'stability', 'last_accessed'

        Returns:
            Current retrieval strength (0.0 to 1.0)
        """
        importance = doc.get("importance", 0.5)
        stability = doc.get("stability", CognitiveMath.DEFAULT_STABILITY_HOURS)
        last_accessed = doc.get("last_accessed")

        # Ensure minimum stability to prevent division issues
        stability = max(stability, CognitiveMath.MIN_STABILITY)

        # If no last_accessed, treat as freshly created (full strength)
        if not last_accessed:
            return importance

        # Calculate time elapsed in hours
        now = datetime.now(timezone.utc)
        if isinstance(last_accessed, str):
            # Parse ISO format string
            try:
                last_accessed = datetime.fromisoformat(last_accessed.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                return importance
        elif isinstance(last_accessed, datetime):
            # Handle naive datetime from MongoDB (assume UTC)
            if last_accessed.tzinfo is None:
                last_accessed = last_accessed.replace(tzinfo=timezone.utc)

        t_hours = (now - last_accessed).total_seconds() / 3600.0

        # Prevent negative time (shouldn't happen, but be safe)
        t_hours = max(t_hours, 0.0)

        # S = R * exp(-t / H)
        strength = importance * math.exp(-t_hours / stability)

        # Ensure strength is between 0.01 and 1.0
        return max(min(strength, 1.0), 0.01)

    @staticmethod
    def grow_stability(
        current_stability: float,
        similarity: float = 0.0,
        emotion: float = 0.0,
    ) -> float:
        """
        Implement the Spacing Effect - stability increases with retrieval.

        Every time a memory is successfully retrieved (rehearsed), it becomes
        harder to forget. This mimics how human memory strengthens through use.

        Formula: H_new = H_old * (1.2 + similarity + emotion * 1.5)

        Args:
            current_stability: Current stability value (H)
            similarity: How relevant this retrieval was (0.0 to 1.0)
            emotion: Emotional intensity of the retrieval context (0.0 to 1.0)

        Returns:
            New stability value (capped at MAX_STABILITY)
        """
        # Base growth factor (20% increase just for being retrieved)
        growth_factor = 1.2

        # Relevance boost (higher similarity = more reinforcement)
        growth_factor += similarity

        # Emotional boost (high emotion = flashbulb effect)
        growth_factor += emotion * 1.5

        # Calculate new stability
        new_stability = current_stability * growth_factor

        # Cap at maximum stability (effectively permanent)
        return min(new_stability, CognitiveMath.MAX_STABILITY)

    @staticmethod
    def calculate_initial_stability(
        emotion: float,
        default_hours: float = 24.0,
        max_multiplier: float = 100.0,
    ) -> float:
        """
        Calculate initial stability for a new memory based on emotional intensity.

        Implements the Flashbulb Memory effect - highly emotional events are
        remembered with exceptional clarity and persistence.

        Formula: H_initial = default + (emotion * max_multiplier)

        Args:
            emotion: Emotional intensity (0.0 to 1.0)
            default_hours: Base stability for neutral memories (default: 24)
            max_multiplier: Maximum hours to add for high-emotion (default: 100)

        Returns:
            Initial stability value in hours
        """
        # Base stability
        stability = default_hours

        # Add emotional boost (flashbulb effect)
        # High emotion (0.9) → stability = 24 + 90 = 114 hours
        # Low emotion (0.1) → stability = 24 + 10 = 34 hours
        stability += emotion * max_multiplier

        return stability

    @staticmethod
    def calculate_combined_score(
        similarity: float,
        strength: float,
        weight_similarity: float = 0.6,
        weight_strength: float = 0.4,
    ) -> float:
        """
        Calculate combined score for ranking search results.

        Combines vector similarity with temporal strength for decay-aware ranking.

        Args:
            similarity: Vector search similarity score (0.0 to 1.0)
            strength: Current retrieval strength (0.0 to 1.0)
            weight_similarity: Weight for similarity (default: 0.6)
            weight_strength: Weight for strength (default: 0.4)

        Returns:
            Combined score for ranking
        """
        # Simple weighted average
        return (similarity * weight_similarity) + (strength * weight_strength)


# ============================================================================
# Pruning Reasons - Cold Storage Audit Trail
# ============================================================================


class PruningReason:
    """Constants for pruning reasons (audit trail)."""

    CAPACITY_LIMIT = "capacity_limit_reached"
    LOW_STRENGTH = "low_retrieval_strength"
    STALE_MEMORY = "stale_memory"
    USER_REQUESTED = "user_requested"
    CONFLICT_RESOLUTION = "conflict_resolution"
    REFLECTION_CONSOLIDATION = "reflection_consolidation"


# Pydantic models for fact extraction structured output
if PYDANTIC_AVAILABLE:

    class FactExtractionResponse(BaseModel):
        """Structured response for fact extraction (legacy, no categories)."""

        facts: list[str] = Field(
            description=(
                "List of extracted facts as strings. "
                "Each fact should be standalone and meaningful."
            )
        )

    class CategorizedFact(BaseModel):
        """A fact with its category."""

        text: str = Field(description="The extracted fact about the user.")
        category: str = Field(
            description=(
                "Category of the fact. One of: biographical, preferences, "
                "temporal, relational. Every fact MUST be assigned to one of these categories."
            )
        )

    class CategorizedFactExtractionResponse(BaseModel):
        """Structured response for fact extraction with categories."""

        facts: list[CategorizedFact] = Field(
            description=(
                "List of extracted facts with categories. "
                "Each fact should be standalone and meaningful."
            )
        )

    class CognitiveFact(BaseModel):
        """A fact with category and emotional intensity (Flashbulb Memory support)."""

        text: str = Field(description="The extracted fact about the user.")
        category: str = Field(
            description=(
                "Category of the fact. One of: biographical, preferences, "
                "temporal, relational. Every fact MUST be assigned to one of these categories."
            )
        )
        emotion: float = Field(
            ge=0.0,
            le=1.0,
            default=0.3,
            description=(
                "Emotional intensity of this fact (0.0 to 1.0). "
                "High values (>0.7) indicate significant life events, "
                "strong preferences, or impactful information. "
                "Low values (<0.3) indicate mundane facts."
            ),
        )

    class CognitiveFactExtractionResponse(BaseModel):
        """Structured response for cognitive fact extraction with emotion tagging."""

        facts: list[CognitiveFact] = Field(
            description=(
                "List of extracted facts with categories and emotional intensity. "
                "Each fact should be standalone and meaningful."
            )
        )


# Memory categories
# Note: "general" is NOT a memory category - it's only used for bucket_type filtering
MEMORY_CATEGORIES = {
    "biographical": "Personal info: name, age, occupation, family, location, education",
    "preferences": "Likes, dislikes, preferences, brand loyalties, favorites",
    "temporal": "Current projects, deadlines, short-term goals, recent events",
    "relational": "Relationships, feelings about others, communication preferences",
}

# Memory types (Cognitive Blueprint v2.0)
MEMORY_TYPES = {
    "semantic": "General knowledge, rules, facts (permanent)",
    "entity": "Structured facts about users/objects (permanent)",
    "procedural": "How-to workflows, code snippets, procedures (permanent)",
    "episodic": "Historical logs, chat transcripts (1-2 year retention)",
    "working": "Active context, topic tracking (session-based, TTL)",
}


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


class CognitiveMemoryServiceError(MemoryServiceError):
    """Base exception for Cognitive Memory Service failures."""

    pass


# ============================================================================
# Persona Engine - App-level Identity Filter
# ============================================================================


class PersonaEngine:
    """
    Manages app-level persona that filters memory retrieval and interpretation.

    Persona acts as a "filter" through which all data passes, determining:
    - Salience: What is worth remembering?
    - Tone & Synthesis: How is a memory retrieved?
    - Consistency: Ensures memories align with agent's established "self"
    """

    def __init__(
        self,
        app_slug: str,
        collection: Any,
        embedding_service: Any,
        config: dict[str, Any] | None = None,
    ):
        """
        Initialize Persona Engine.

        Args:
            app_slug: Application slug
            collection: MongoDB collection for persona storage
            embedding_service: Embedding service for persona vector generation
            config: Persona configuration dictionary
        """
        self.app_slug = app_slug
        self.collection = collection
        self.embedding_service = embedding_service
        self.config = config or {}

        # Persona collection name
        self.persona_collection_name = f"{app_slug}_persona"
        self.persona_collection = self.collection.database[self.persona_collection_name]

        # Default persona configuration
        self.enabled = self.config.get("enabled", True)
        self.default_role = self.config.get("default_role", "Helpful Assistant")
        self.default_description = self.config.get(
            "default_description",
            "A helpful AI assistant focused on providing accurate and useful information.",
        )
        self.default_traits = self.config.get(
            "default_traits",
            {
                "technical_focus": 0.5,
                "humor": 0.2,
                "formality": 0.7,
                "empathy": 0.6,
                "creativity": 0.4,
            },
        )

        # Initialize persona if enabled
        if self.enabled:
            self._ensure_persona_exists()

    def _ensure_persona_exists(self):
        """Ensure persona document exists, create default if not."""
        existing = self.persona_collection.find_one({"app_slug": self.app_slug})
        if not existing:
            # Create default persona
            persona_doc = {
                "app_slug": self.app_slug,
                "role": self.default_role,
                "description": self.default_description,
                "traits": self.default_traits,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }

            # Generate embedding for persona
            persona_text = f"{self.default_role}. {self.default_description}"
            try:
                if self.embedding_service:
                    # Use async embedding service
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # If loop is running, use thread pool
                        with concurrent.futures.ThreadPoolExecutor() as executor:
                            future = executor.submit(
                                lambda: asyncio.run(self.embedding_service.embed(persona_text))
                            )
                            embeddings = future.result(timeout=30)
                    else:
                        embeddings = asyncio.run(self.embedding_service.embed(persona_text))

                    if embeddings and len(embeddings) > 0:
                        persona_doc["vector"] = embeddings[0]
            except (
                RuntimeError,
                AttributeError,
                TimeoutError,
                ValueError,
                TypeError,
            ) as e:
                logger.warning(f"Failed to generate persona embedding: {e}")

            self.persona_collection.insert_one(persona_doc)
            logger.info(f"✅ Created default persona for {self.app_slug}: {self.default_role}")

    def get_persona(self) -> dict[str, Any] | None:
        """
        Get current persona for the app.

        Returns:
            Persona document or None if not found
        """
        if not self.enabled:
            return None

        persona = self.persona_collection.find_one({"app_slug": self.app_slug})
        if persona:
            persona["_id"] = str(persona["_id"])
        return persona

    def get_persona_vector(self) -> list[float] | None:
        """
        Get persona vector for similarity matching.

        Returns:
            Persona embedding vector or None
        """
        persona = self.get_persona()
        if persona and "vector" in persona:
            return persona["vector"]
        return None

    def update_persona(
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
        """
        if not self.enabled:
            raise CognitiveMemoryServiceError("Persona feature is disabled")

        update_doc: dict[str, Any] = {"updated_at": datetime.now(timezone.utc)}

        if role:
            update_doc["role"] = role
        if description:
            update_doc["description"] = description
        if traits:
            update_doc["traits"] = traits

        # Regenerate embedding if role or description changed
        if role or description:
            current = self.get_persona()
            final_role = (
                role or current.get("role", self.default_role) if current else self.default_role
            )
            final_description = (
                description or current.get("description", self.default_description)
                if current
                else self.default_description
            )

            persona_text = f"{final_role}. {final_description}"
            try:
                if self.embedding_service:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        with concurrent.futures.ThreadPoolExecutor() as executor:
                            future = executor.submit(
                                lambda: asyncio.run(self.embedding_service.embed(persona_text))
                            )
                            embeddings = future.result(timeout=30)
                    else:
                        embeddings = asyncio.run(self.embedding_service.embed(persona_text))

                    if embeddings and len(embeddings) > 0:
                        update_doc["vector"] = embeddings[0]
            except (
                RuntimeError,
                AttributeError,
                TimeoutError,
                ValueError,
                TypeError,
            ) as e:
                logger.warning(f"Failed to regenerate persona embedding: {e}")

        self.persona_collection.update_one(
            {"app_slug": self.app_slug}, {"$set": update_doc}, upsert=True
        )

        logger.info(f"✅ Updated persona for {self.app_slug}")
        return self.get_persona() or {}


class CognitiveMemoryService(BaseMemoryService):
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
    - Memory decay
    - Memory merging
    - Memory pruning
    - Access tracking

    To disable cognitive features, set max_depth=None or enable_cognitive=False.
    """

    def __init__(
        self,
        app_slug: str,
        config: dict[str, Any] | None = None,
        collection: Any = None,
        *,
        graph_service: Any = None,
        embedding_service: Any = None,
        llm_service: Any = None,
    ):
        """
        Initialize Cognitive Memory Service.

        Args:
            app_slug: Application slug (required)
            config: Configuration dictionary with:
                # Core configuration
                - collection_name: Collection name (default: {app_slug}_memories)
                - index_name: Vector search index name (default: "vector_index")
                - embedding_model: Embedding model name (default: "text-embedding-3-small")
                - chat_model: LLM model for fact extraction (default: "gpt-4o")
                - memory_llm_model: LiteLLM model string for memory operations
                  (default: chat_model)
                - temperature: Temperature for LLM inference
                  (default: 0, can be set via MEMORY_LLM_TEMPERATURE env var)
                - embedding_dims: Embedding dimensions (default: 1536)
                - infer: Enable LLM inference (default: True)

                # Cognitive features (optional, enabled by default)
                - enable_cognitive: Enable cognitive features (default: True)
                - max_depth: Maximum memories per user (default: 100, None=unlimited)
                - similarity_threshold: Threshold for reinforcement (default: 0.7)
                - reinforcement_factor: Strength of reinforcement (default: 1.1)
                - decay_factor: Rate of memory decay (default: 0.99)
                - merge_threshold_low: Lower bound for merging (default: 0.7)
                - merge_threshold_high: Upper bound for merging (default: 0.85)
            collection: PyMongo Collection instance (REQUIRED - must be from
                       MDB-Engine connection pool)
            graph_service: Optional GraphService instance for GraphRAG functionality.
                          If provided, memory service will use it for graph extraction.
                          Use mdb_engine.graph.GraphService for standalone graph operations.
            embedding_service: Optional EmbeddingService instance for embeddings.
                              If provided, uses this instead of creating internally.
                              Implements EmbeddingServiceProtocol.
            llm_service: Optional LLMService instance for LLM operations.
                        If provided, uses this instead of creating internally.
                        Implements LLMServiceProtocol.
        """
        if collection is None:
            raise CognitiveMemoryServiceError(
                "Collection is REQUIRED. CognitiveMemoryService must use "
                "MDB-Engine's connection pool. Pass a PyMongo Collection instance "
                "obtained from MDB-Engine's connection manager."
            )

        self.config = config or {}

        # Core Configuration Defaults
        self.app_slug = app_slug
        self.collection_name = self.config.get("collection_name", f"{app_slug}_memories")
        self.index_name = self.config.get("index_name", "vector_index")
        self.embedding_model = self.config.get("embedding_model", "text-embedding-3-small")
        # memory_llm_model: LiteLLM model string for memory operations
        # (fact extraction, importance assessment)
        # Format: "provider/model"
        # (e.g., "openai/gpt-4o", "azure/gpt-4o", "gemini/gemini-3-flash-preview")
        # Falls back to chat_model if not specified (for backwards compatibility)
        # This allows using different models for chat vs memory operations
        chat_model_raw = self.config.get("chat_model", "gpt-4o")
        memory_llm_model_raw = self.config.get("memory_llm_model", chat_model_raw)

        logger.debug(
            f"🧠 [Memory Config] chat_model={chat_model_raw}, "
            f"memory_llm_model_raw={memory_llm_model_raw}"
        )

        # Convert to LiteLLM format if needed (legacy support)
        if "/" not in memory_llm_model_raw:
            # Legacy format: detect provider from model name patterns first,
            # then fall back to env vars
            model_lower = memory_llm_model_raw.lower()

            # Check if it's a Gemini model by name pattern (PRIORITY: model name > env vars)
            if "gemini" in model_lower:
                self.memory_llm_model = f"gemini/{memory_llm_model_raw}"
                logger.info(
                    f"🧠 [Memory Config] Detected Gemini model from name pattern: "
                    f"{memory_llm_model_raw} → {self.memory_llm_model}"
                )
            # Check if it's an Anthropic model
            elif "claude" in model_lower:
                self.memory_llm_model = f"anthropic/{memory_llm_model_raw}"
                logger.info(
                    f"🧠 [Memory Config] Detected Anthropic model from name pattern: "
                    f"{memory_llm_model_raw} → {self.memory_llm_model}"
                )
            # Otherwise, detect from environment variables
            elif os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("AZURE_OPENAI_ENDPOINT"):
                deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", memory_llm_model_raw)
                self.memory_llm_model = f"azure/{deployment}"
                logger.info(
                    f"🧠 [Memory Config] Detected Azure OpenAI from env vars: "
                    f"{memory_llm_model_raw} → {self.memory_llm_model}"
                )
            elif os.getenv("GEMINI_API_KEY"):
                self.memory_llm_model = f"gemini/{memory_llm_model_raw}"
                logger.info(
                    f"🧠 [Memory Config] Detected Gemini from env vars: "
                    f"{memory_llm_model_raw} → {self.memory_llm_model}"
                )
            else:
                # Default to OpenAI
                self.memory_llm_model = f"openai/{memory_llm_model_raw}"
                logger.info(
                    f"🧠 [Memory Config] Defaulting to OpenAI: "
                    f"{memory_llm_model_raw} → {self.memory_llm_model}"
                )
        else:
            # Already in LiteLLM format - use as-is
            self.memory_llm_model = memory_llm_model_raw
            logger.info(
                f"🧠 [Memory Config] Using LiteLLM format model as-is: {self.memory_llm_model}"
            )

        self.chat_model = chat_model_raw  # Keep for reference/compatibility
        self.embedding_dims = self.config.get("embedding_dims", 1536)
        self.infer = self.config.get("infer", True)

        # Temperature configuration: can be set via manifest or environment variable
        # Defaults to 0 for deterministic fact extraction
        self.temperature = float(
            self.config.get("temperature", os.getenv("MEMORY_LLM_TEMPERATURE", "0"))
        )
        logger.info(f"🧠 [Memory Config] LLM temperature: {self.temperature}")

        # Cognitive Configuration (enabled by default)
        self.enable_cognitive = self.config.get("enable_cognitive", True)
        self.max_depth = self.config.get("max_depth", 100)  # None = unlimited
        self.similarity_threshold = self.config.get("similarity_threshold", 0.7)
        self.reinforcement_factor = self.config.get("reinforcement_factor", 1.1)
        self.decay_factor = self.config.get("decay_factor", 0.99)
        self.merge_threshold_low = self.config.get("merge_threshold_low", 0.7)
        self.merge_threshold_high = self.config.get("merge_threshold_high", 0.85)
        self.duplicate_threshold = self.config.get("duplicate_threshold", 0.90)

        # Disable cognitive features if max_depth is explicitly None or enable_cognitive is False
        if self.max_depth is None or not self.enable_cognitive:
            self.enable_cognitive = False
            logger.info("Cognitive features disabled")

        # Initialize Redaction Service for privacy protection (disabled by default)
        redaction_config = self.config.get("redaction", {})
        self.redaction_service = get_redaction_service(config=redaction_config)
        self.redaction_enabled = redaction_config.get("enabled", False)  # Disabled by default
        if self.redaction_enabled:
            logger.info("🔒 Redaction layer enabled for memory privacy protection")

        # Memory categories configuration
        categories_config = self.config.get("categories", {})
        self.categories_enabled = categories_config.get("enabled", True)
        self.custom_categories = categories_config.get("custom_categories", [])

        # Memory types configuration (Cognitive Blueprint v2.0)
        memory_types_config = self.config.get("memory_types", {})
        self.memory_types_enabled = memory_types_config.get("enabled", True)
        self.auto_detect_memory_type = memory_types_config.get("auto_detect", True)
        self.default_memory_type = memory_types_config.get("default_type", "semantic")
        self.episodic_retention_days = memory_types_config.get("episodic_retention_days", 730)
        self.working_ttl_hours = memory_types_config.get("working_ttl_hours", 24)

        # Use provided collection (from MDB-Engine)
        self.collection = collection
        # Extract client and db from collection for reference
        self._db = collection.database
        self._client = self._db.client
        self.db_name = self._db.name
        logger.info(
            f"✅ Memory Service using MDB-Engine collection: "
            f"db={self.db_name}, collection={self.collection_name}, app_slug={self.app_slug}"
        )

        # Verify collection exists (or will be created on first insert)
        try:
            collections = self._db.list_collection_names()
            logger.debug(f"📋 Available collections in {self.db_name}: {collections}")
            if self.collection_name in collections:
                count = self.collection.count_documents({})
                logger.info(
                    f"📊 Collection {self.collection_name} exists with {count} total documents"
                )
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Could not verify collection existence: {e}")

        # Initialize LLM service (use injected service or create internally)
        # Dependency Injection: llm_service parameter allows external control
        self._injected_llm_service = llm_service
        if llm_service is not None:
            self.llm_available = True
            logger.info("✅ Using injected LLM service")
        else:
            # Fall back to internal LiteLLM initialization
            self._init_llm_client()

        # Initialize Embedding Service (use injected service or create internally)
        # Dependency Injection: embedding_service parameter allows external control
        self._injected_embedding_service = embedding_service
        if embedding_service is not None:
            self.embedding_provider = embedding_service
            logger.info("✅ Using injected embedding service")
        else:
            # Fall back to internal initialization (async, will be used via asyncio.run)
            self._init_embedding_service()

        # Ensure cognitive fields if cognitive features enabled
        if self.enable_cognitive:
            self._ensure_cognitive_fields()

        # Initialize Memory Fusion Service for intelligent deduplication
        self._init_fusion_service()

        # Store injected GraphService for GraphRAG functionality
        # GraphService is initialized separately by ServiceInitializer and passed in
        self._graph_service = graph_service

        # Initialize Persona Engine (enabled by default)
        persona_config = self.config.get("persona", {})
        if persona_config.get("enabled", True):
            try:
                self.persona_engine = PersonaEngine(
                    app_slug=self.app_slug,
                    collection=self.collection,
                    embedding_service=self.embedding_provider,
                    config=persona_config,
                )
                logger.info("✅ Persona Engine initialized")
            except (
                AttributeError,
                TypeError,
                ValueError,
                RuntimeError,
            ) as e:
                logger.warning(f"⚠️ Failed to initialize Persona Engine: {e}")
                self.persona_engine = None
        else:
            self.persona_engine = None
            logger.info("Persona Engine disabled")

        # Initialize Perception Engine (enabled by default)
        from .perceptions import PerceptionEngine

        perceptions_config = self.config.get("perceptions", {})
        if perceptions_config.get("enabled", True):
            try:
                self.perception_engine = PerceptionEngine(
                    app_slug=self.app_slug,
                    collection=self.collection,
                    embedding_service=self.embedding_provider,
                    llm_service=self._injected_llm_service,
                    config=perceptions_config,
                )
                logger.info("✅ Perception Engine initialized")
            except (
                AttributeError,
                TypeError,
                ValueError,
                RuntimeError,
            ) as e:
                logger.warning(f"⚠️ Failed to initialize Perception Engine: {e}")
                self.perception_engine = None
        else:
            self.perception_engine = None
            logger.info("Perception Engine disabled")

        # Initialize Procedural Memory Service (Cognitive Blueprint v2.0)
        from .procedural import ProceduralMemoryService

        procedural_config = self.config.get("procedural", {})
        if procedural_config.get("enabled", True):
            try:
                self.procedural_service = ProceduralMemoryService(
                    app_slug=self.app_slug,
                    collection=self.collection,
                    llm_service=self._injected_llm_service,
                    config=procedural_config,
                )
                logger.info("✅ Procedural Memory Service initialized")
            except (
                AttributeError,
                TypeError,
                ValueError,
                RuntimeError,
            ) as e:
                logger.warning(f"⚠️ Failed to initialize Procedural Memory Service: {e}")
                self.procedural_service = None
        else:
            self.procedural_service = None
            logger.info("Procedural Memory Service disabled")

        # Initialize Entity Store for structured user facts (enabled by default)
        logger.info(
            f"✅ Cognitive Memory Service initialized: "
            f"cognitive_features={self.enable_cognitive}, "
            f"max_depth={self.max_depth if self.enable_cognitive else 'N/A'}, "
            f"graph_enabled={self._graph_service is not None}, "
            f"persona_enabled={self.persona_engine is not None}"
        )

    @property
    def graph_service(self) -> Any:
        """Get the GraphService for GraphRAG functionality."""
        return self._graph_service

    @property
    def graph_store(self) -> Any:
        """Alias for graph_service (backward compatibility)."""
        return self._graph_service

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

        model_lower = model.lower()

        # Gemini: Always use 1.0
        if model_lower.startswith("gemini/"):
            if self.temperature != 1.0:
                logger.info(
                    f"⚠️  Enforcing temperature=1.0 for Gemini model '{model}'. "
                    f"Gemini models require temperature=1.0. "
                    f"Requested temperature ({self.temperature}) was adjusted."
                )
            return 1.0

        # Azure OpenAI and OpenAI: Always use 0.3
        elif model_lower.startswith("azure/") or model_lower.startswith("openai/"):
            if self.temperature != 0.3:
                logger.info(
                    f"⚠️  Enforcing temperature=0.3 for OpenAI/Azure model '{model}'. "
                    f"Requested temperature ({self.temperature}) was adjusted."
                )
            return 0.3

        return self.temperature

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
                    "⚠️ LiteLLM not available. Memory extraction will fail when infer=True. "
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
                    "⚠️ No LLM API keys found. Set OPENAI_API_KEY, AZURE_OPENAI_API_KEY, "
                    "GEMINI_API_KEY, ANTHROPIC_API_KEY, or other provider keys. "
                    "Memory extraction will fail when infer=True."
                )
            self.llm_available = False
            return

        try:
            self.llm_available = True
            logger.info(f"✅ Using LiteLLM for memory operations (model: {self.memory_llm_model})")
        except (
            ValueError,
            TypeError,
            AttributeError,
            RuntimeError,
            ImportError,
        ) as e:
            # Catch specific exceptions that can occur during LiteLLM initialization
            # ValueError: Invalid configuration
            # TypeError: Type errors
            # AttributeError: Missing attributes
            # RuntimeError: Runtime issues
            # ImportError: Import errors (shouldn't happen after check, but safe)
            logger.exception(f"Failed to initialize LiteLLM: {e}")
            self.llm_available = False

    def _llm_completion(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = None,
        response_format: Any = None,
        **kwargs: Any,
    ) -> Any:
        """
        Execute an LLM completion using injected LLMService.

        This method provides a unified interface for LLM calls using the injected LLMService.
        It wraps the async chat_completion method for use in sync contexts.

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
            raise MemoryServiceError(
                "LLM service not available. _llm_completion requires an injected LLMService."
            )

        model = model or self.memory_llm_model
        if temperature is None:
            temperature = self._get_adjusted_temperature(model)

        try:
            # Call async chat_completion from sync context
            # Check if we're in an async context
            try:
                asyncio.get_running_loop()
                # We're in an async context, this shouldn't happen for sync method
                # But if it does, we can't use asyncio.run()
                raise MemoryServiceError(
                    "_llm_completion called from async context. " "Use async LLM methods instead."
                )
            except RuntimeError:
                # No running loop, safe to use asyncio.run()
                result = asyncio.run(
                    self._injected_llm_service.chat_completion(
                        messages=messages,
                        model=model,
                        temperature=temperature,
                        response_format=response_format,
                        **kwargs,
                    )
                )

            # Wrap result in a LiteLLM-like response object for compatibility
            from types import SimpleNamespace

            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=result))]
            )
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

    def _init_embedding_service(self):
        """Initialize embedding service using mdb_engine.embeddings."""
        try:
            from ..embeddings import EmbeddingProvider, EmbeddingServiceError

            # Auto-detect provider from environment
            embedding_config = {"default_embedding_model": self.embedding_model}
            self.embedding_provider = EmbeddingProvider(config=embedding_config)
            logger.info(f"✅ Embedding service initialized (model: {self.embedding_model})")
        except (
            ImportError,
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            ConnectionError,
            OSError,
            EmbeddingServiceError,
        ) as e:
            logger.exception("Failed to initialize embedding service")
            raise CognitiveMemoryServiceError(f"Failed to initialize embedding service: {e}") from e

    def _init_fusion_service(self):
        """Initialize Memory Fusion Service for intelligent fact deduplication."""
        fusion_config = self.config.get("fusion", {})
        fusion_enabled = fusion_config.get("enabled", True)

        if not fusion_enabled:
            self.fusion_service = None
            logger.info("🔄 Memory Fusion Service disabled via config")
            return

        try:
            from .fusion import MemoryFusionService

            self.fusion_service = MemoryFusionService(
                config=fusion_config,
                embedding_fn=self._get_embedding,
                embedding_fn_batch=self._get_embeddings_batch_sync,  # ~5x faster
                llm_model=self.memory_llm_model,
                temperature=self._get_adjusted_temperature(self.memory_llm_model),
            )
            logger.info("✅ Memory Fusion Service initialized (with batch embedding)")
        except (ImportError, ValueError, TypeError, RuntimeError) as e:
            logger.warning(
                f"⚠️ Failed to initialize Fusion Service: {e}. Using simple deduplication."
            )
            self.fusion_service = None

    def _get_embedding(self, text: str, retries: int = 3) -> list[float]:
        """
        Generates vector embedding for text with robust retry logic.
        Uses the async EmbeddingProvider via asyncio.run.
        """
        text = text.replace("\n", " ").strip()
        if not text:
            return []

        # Run async embedding in sync context
        for attempt in range(retries):
            try:
                # Try to get existing event loop, or create new one
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # If loop is running, we need to use a different approach
                        # Create a new thread with a new event loop
                        import concurrent.futures

                        with concurrent.futures.ThreadPoolExecutor() as executor:
                            future = executor.submit(
                                lambda: asyncio.run(
                                    self.embedding_provider.embed(
                                        [text], model=self.embedding_model
                                    )
                                )
                            )
                            vectors = future.result(timeout=30)
                    else:
                        vectors = loop.run_until_complete(
                            self.embedding_provider.embed([text], model=self.embedding_model)
                        )
                except RuntimeError:
                    # No event loop, create new one
                    vectors = asyncio.run(
                        self.embedding_provider.embed([text], model=self.embedding_model)
                    )

                if vectors and len(vectors) > 0:
                    return vectors[0]
                return []
            except (
                ImportError,
                AttributeError,
                TypeError,
                ValueError,
                RuntimeError,
                ConnectionError,
                OSError,
            ) as e:
                if attempt == retries - 1:
                    logger.exception(f"❌ Embedding failed after {retries} attempts")
                    raise CognitiveMemoryServiceError(f"Embedding generation failed: {e}") from e
                time.sleep(1 * (attempt + 1))  # Exponential backoff
        return []

    async def _get_embeddings_batch(
        self, texts: list[str], retries: int = 3
    ) -> dict[str, list[float]]:
        """
        Batch generate embeddings for multiple texts in a single API call.

        This is significantly faster than calling _get_embedding() for each text
        individually. The embedding provider already supports batch embedding.

        Args:
            texts: List of texts to embed
            retries: Number of retry attempts

        Returns:
            Dictionary mapping each text to its embedding vector
        """
        if not texts:
            return {}

        # Clean texts (same preprocessing as _get_embedding)
        cleaned_texts = [t.replace("\n", " ").strip() for t in texts]
        valid_indices = [i for i, t in enumerate(cleaned_texts) if t]
        valid_texts = [cleaned_texts[i] for i in valid_indices]

        if not valid_texts:
            return {}

        # Split into batches if needed
        result_embeddings: dict[str, list[float]] = {}

        for batch_start in range(0, len(valid_texts), _EMBEDDING_BATCH_SIZE):
            batch_texts = valid_texts[batch_start : batch_start + _EMBEDDING_BATCH_SIZE]

            for attempt in range(retries):
                try:
                    # Use async embedding provider directly
                    vectors = await self.embedding_provider.embed(
                        batch_texts, model=self.embedding_model
                    )

                    # Map results back to original texts
                    for text, vector in zip(batch_texts, vectors, strict=False):
                        if vector:
                            result_embeddings[text] = vector
                    break  # Success, move to next batch

                except (
                    ImportError,
                    AttributeError,
                    TypeError,
                    ValueError,
                    RuntimeError,
                    ConnectionError,
                    OSError,
                ) as e:
                    if attempt == retries - 1:
                        logger.warning(f"⚠️ Batch embedding failed after {retries} attempts: {e}")
                        # Don't raise - return partial results
                    else:
                        await asyncio.sleep(1 * (attempt + 1))

        logger.info(
            f"⚡ [Batch Embed] Generated {len(result_embeddings)} embeddings "
            f"for {len(texts)} texts in batches of {_EMBEDDING_BATCH_SIZE}"
        )
        return result_embeddings

    def _get_embeddings_batch_sync(
        self, texts: list[str], retries: int = 3
    ) -> dict[str, list[float]]:
        """
        Synchronous wrapper for _get_embeddings_batch().

        Handles event loop management for sync callers.
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop is running, use thread pool
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        lambda: asyncio.run(self._get_embeddings_batch(texts, retries))
                    )
                    return future.result(timeout=60)
            else:
                return loop.run_until_complete(self._get_embeddings_batch(texts, retries))
        except RuntimeError:
            # No event loop, create new one
            return asyncio.run(self._get_embeddings_batch(texts, retries))

    def _extract_facts(self, text: str) -> list[str]:
        """
        Uses LLM to extract atomic, de-duplicated facts from raw text.
        """
        logger.info(f"🔬 [Fact Extraction] Starting extraction for text: '{text[:100]}...'")

        if not self.llm_available:
            logger.warning("⚠️ [Fact Extraction] No LLM client available. Falling back to raw text.")
            result = [text] if text.strip() else []
            logger.info(f"🔬 [Fact Extraction] Fallback result: {len(result)} facts")
            return result

        system_prompt = (
            "You are a memory extraction engine. Your goal is to extract key facts "
            "from the user input that are worth remembering long-term.\n"
            "Rules:\n"
            "1. Extract distinct, standalone facts about the USER (their preferences, "
            "facts about them, etc.).\n"
            "2. Only extract DECLARATIVE STATEMENTS where the user reveals something "
            "about themselves. DO NOT extract questions, requests, or queries.\n"
            "3. If the user expresses a preference or fact about themselves "
            "(e.g., 'I love chocolate', 'I like pizza'), extract it as a fact.\n"
            "4. Return an EMPTY array if the input contains no extractable facts "
            "(questions, greetings, requests, meta-conversation).\n"
            "5. NEVER describe what the user is doing (e.g., 'User is asking about X'). "
            "Only extract what the user IS or LIKES/DISLIKES.\n\n"
            "Example Input: 'I love chocolate'\n"
            'Example Output: {"facts": ["User loves chocolate"]}\n'
            "Example Input: 'My name is John and I love coding in Python.'\n"
            'Example Output: {"facts": ["User\'s name is John", "User loves coding in Python"]}\n'
            "Example Input: 'Do I like vanilla?'\n"
            'Example Output: {"facts": []}\n'
            "Example Input: 'What did I say about pizza?'\n"
            'Example Output: {"facts": []}\n'
            "Example Input: 'Hello, how are you?'\n"
            'Example Output: {"facts": []}'
        )

        try:
            if not self.llm_available:
                logger.warning("🔬 [Fact Extraction] LLM not available, skipping extraction")
                return [text]

            logger.info(f"🔬 [Fact Extraction] Using LiteLLM model: {self.memory_llm_model}")
            logger.info(f"🔬 [Fact Extraction] Calling LLM with text length: {len(text)}")

            # Use LiteLLM for fact extraction with Pydantic structured output
            # Format response_format for provider-optimal structured output
            # - Gemini: Pydantic models → response_schema (via LiteLLM)
            # - OpenAI/Azure: Pydantic models → function calling format
            #   (structured outputs via LiteLLM)
            if PYDANTIC_AVAILABLE:
                from ..llm.service import _format_response_format_for_provider

                formatted_response_format = _format_response_format_for_provider(
                    FactExtractionResponse, self.memory_llm_model
                )

                response = completion(
                    model=self.memory_llm_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": text},
                    ],
                    response_format=formatted_response_format,
                    temperature=self._get_adjusted_temperature(self.memory_llm_model),
                )

                content = response.choices[0].message.content
                logger.info(f"🔬 [Fact Extraction] LLM response: '{content[:200]}...'")

                # Safely parse structured response
                # (handles markdown code blocks, empty responses, etc.)
                try:
                    from ..llm.service import _parse_structured_response

                    extraction_result = _parse_structured_response(content, FactExtractionResponse)
                    if isinstance(extraction_result, FactExtractionResponse):
                        facts = extraction_result.facts
                    else:
                        # Fallback: try direct parsing if helper returned dict
                        extraction_result = FactExtractionResponse.model_validate(extraction_result)
                        facts = extraction_result.facts
                except (ValueError, TypeError, AttributeError, KeyError) as e:
                    logger.warning(f"⚠️ [Fact Extraction] Failed to parse response: {e}")
                    # Fallback: try direct JSON parsing
                    import json

                    try:
                        data = json.loads(content.strip())
                        facts = data.get("facts", [])
                    except (json.JSONDecodeError, AttributeError, KeyError):
                        logger.exception(
                            "❌ [Fact Extraction] Complete parse failure, " "returning raw text"
                        )
                        return [text]
            else:
                # Fallback to JSON mode if Pydantic not available
                response = completion(
                    model=self.memory_llm_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": text},
                    ],
                    response_format={"type": "json_object"},
                    temperature=self._get_adjusted_temperature(self.memory_llm_model),
                )

                content = response.choices[0].message.content
                logger.info(f"🔬 [Fact Extraction] LLM response: '{content[:200]}...'")

                data = json.loads(content)
                facts = data.get("facts", [])

            facts_count = len(facts) if isinstance(facts, list) else 0
            logger.info(f"🔬 [Fact Extraction] Parsed facts: {facts_count} facts")
            if facts:
                for i, fact in enumerate(facts[:3]):  # Log first 3
                    logger.info(f"  Fact {i+1}: '{fact[:50]}...'")

            # Sanity check
            if not isinstance(facts, list):
                logger.warning(
                    "🔬 [Fact Extraction] LLM returned 'facts' but not as a list. "
                    "Falling back to raw text."
                )
                return [text]

            filtered_facts = [f for f in facts if isinstance(f, str) and f.strip()]
            logger.info(
                f"🔬 [Fact Extraction] Final result: {len(filtered_facts)} facts after filtering"
            )

            if not filtered_facts:
                logger.warning(
                    f"🔬 [Fact Extraction] ⚠️ No valid facts extracted! "
                    f"Original text: '{text[:100]}...'"
                )
                # Fallback: use the text itself if it's meaningful
                # Check if it contains user preferences or facts (not just greetings)
                text_lower = text.lower().strip()
                trivial_patterns = [
                    "hello",
                    "hi",
                    "hey",
                    "thanks",
                    "thank you",
                    "bye",
                    "goodbye",
                    "how are you",
                    "what's up",
                ]
                is_trivial = (
                    any(pattern in text_lower for pattern in trivial_patterns)
                    and len(text.strip()) < 30
                )

                if not is_trivial and len(text.strip()) > 5:
                    # Prepend "User" to make it a fact about the user
                    fact_text = (
                        f"User {text.strip()}"
                        if not text.strip().startswith(("User ", "I ", "My "))
                        else text.strip()
                    )
                    logger.info(
                        f"🔬 [Fact Extraction] Using text as single fact (fallback): "
                        f"'{fact_text[:50]}...'"
                    )
                    return [fact_text]
                else:
                    logger.info(
                        f"🔬 [Fact Extraction] Text is too trivial, skipping: '{text[:50]}...'"
                    )

            return filtered_facts

        except (
            APIError,
            AuthenticationError,
            NotFoundError,
            RateLimitError,
            json.JSONDecodeError,
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
            RuntimeError,
            ConnectionError,
            OSError,
        ) as e:
            logger.error(f"❌ [Fact Extraction] Fact extraction failed: {e}", exc_info=True)
            # Fallback: Treat the whole text as one fact to ensure no data loss
            fallback = [text] if text.strip() else []
            logger.info(f"🔬 [Fact Extraction] Exception fallback: {len(fallback)} facts")
            return fallback

    def _detect_memory_type(self, fact_text: str) -> str:
        """
        Classify memory type using LLM (Cognitive Blueprint v2.0).

        Memory types:
        - semantic: General knowledge, rules, facts
        - entity: Facts about specific people, projects, objects
        - procedural: How-to workflows, code snippets, step-by-step instructions
        - episodic: Historical events, chat transcripts, temporal logs
        - working: Active context, topic tracking (handled separately)

        Args:
            fact_text: The memory text to classify

        Returns:
            Memory type string (default: "semantic")
        """
        if not self.memory_types_enabled or not self.auto_detect_memory_type:
            return self.default_memory_type

        if not self.llm_available:
            logger.debug("LLM not available for memory type detection, using default")
            return self.default_memory_type

        try:
            prompt = f"""Classify this memory into one of these types:
- semantic: General knowledge, rules, facts
  (e.g., "User prefers dark mode", "Python is a programming language")
- entity: Facts about specific people, projects, objects
  (e.g., "Project Phoenix is active", "Alice is the lead")
- procedural: How-to workflows, code snippets, step-by-step instructions
  (e.g., "To clean data: load CSV, drop nulls, export")
- episodic: Historical events, chat transcripts, temporal logs
  (e.g., "User said X yesterday", conversation logs)

Memory: {fact_text}

Return ONLY the type name (semantic, entity, procedural, or episodic)."""

            response = self._llm_completion(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a memory classification engine. " "Return only the type name."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,  # Deterministic classification
            )

            if hasattr(response, "choices") and response.choices:
                result = response.choices[0].message.content.strip().lower()
            else:
                result = str(response).strip().lower()

            # Validate result
            valid_types = ["semantic", "entity", "procedural", "episodic"]
            if result in valid_types:
                logger.debug(f"✅ [Memory Type] Classified as: {result}")
                return result
            else:
                logger.warning(f"⚠️ [Memory Type] Invalid type '{result}', using default")
                return self.default_memory_type

        except (
            AttributeError,
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
        ) as e:
            logger.warning(f"⚠️ [Memory Type] Detection failed: {e}, using default")
            return self.default_memory_type

    def _extract_facts_with_categories(self, text: str) -> list[dict[str, str]]:
        """
        Uses LLM to extract atomic facts with categories from raw text.

        Categories:
        - biographical: Personal info (name, age, occupation, family, location)
        - preferences: Likes, dislikes, preferences, favorites
        - temporal: Current projects, deadlines, short-term goals
        - relational: Relationships, feelings about others
        - general: Other facts

        Returns:
            List of dicts with 'text' and 'category' keys
        """
        logger.info(f"🔬 [Categorized Extraction] Starting for text: '{text[:100]}...'")

        if not self.llm_available or not PYDANTIC_AVAILABLE:
            logger.warning("⚠️ [Categorized Extraction] LLM/Pydantic not available. Fallback.")
            if text.strip():
                detected_category = self._detect_category_from_text(text)
                return [{"text": text, "category": detected_category}]
            return []

        # Build category descriptions including custom categories
        category_descriptions = "\n".join(
            f"- {cat}: {desc}" for cat, desc in MEMORY_CATEGORIES.items()
        )
        if self.custom_categories:
            for custom_cat in self.custom_categories:
                category_descriptions += f"\n- {custom_cat}: Custom category"

        system_prompt = f"""You are a memory extraction engine that extracts and categorizes facts.

Your goal is to extract key facts from the user input and assign each a category.

CATEGORIES:
{category_descriptions}

RULES:
1. Extract distinct, standalone facts about the USER (preferences, facts about them).
2. Only extract DECLARATIVE STATEMENTS where the user reveals something about themselves.
3. DO NOT extract questions, requests, or queries.
4. Return an EMPTY array if no extractable facts (greetings, questions, requests).
5. NEVER describe what the user is doing. Only extract what the user IS or
   LIKES/DISLIKES.
6. Assign the most appropriate category to each fact. Every fact MUST be
   assigned to one of: biographical, preferences, temporal, or relational.
   NEVER use "general" as a category.

Example Input: 'My name is John and I work at Google. I love pizza.'
Example Output: {{"facts": [
    {{"text": "User's name is John", "category": "biographical"}},
    {{"text": "User works at Google", "category": "biographical"}},
    {{"text": "User loves pizza", "category": "preferences"}}
]}}

Example Input: 'I'm working on a project due Friday and I prefer Slack for communication'
Example Output: {{"facts": [
    {{"text": "User has a project due Friday", "category": "temporal"}},
    {{"text": "User prefers Slack for communication", "category": "relational"}}
]}}

Example Input: 'My sister Emily is a doctor at Mayo Clinic and my '
               'brother-in-law David is a professor at MIT'
Example Output: {{"facts": [
    {{"text": "User's sister Emily is a doctor at Mayo Clinic", "category": "relational"}},
    {{"text": "User's brother-in-law David is a professor at MIT", "category": "relational"}}
]}}

Example Input: 'My family lives in Boston and we own a vacation home in Vermont'
Example Output: {{"facts": [
    {{"text": "User's family lives in Boston", "category": "relational"}},
    {{"text": "User's family owns a vacation home in Vermont", "category": "relational"}}
]}}

Example Input: 'Hello, how are you?'
Example Output: {{"facts": []}}"""

        try:
            logger.info(f"🔬 [Categorized Extraction] Using model: {self.memory_llm_model}")

            # Format response_format for provider-optimal structured output
            from ..llm.service import _format_response_format_for_provider

            response = completion(
                model=self.memory_llm_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                response_format=_format_response_format_for_provider(
                    CategorizedFactExtractionResponse, self.memory_llm_model
                ),
                temperature=self._get_adjusted_temperature(self.memory_llm_model),
            )

            content = response.choices[0].message.content
            logger.info(f"🔬 [Categorized Extraction] Response: '{content[:200]}...'")

            # Safely parse structured response (handles markdown code blocks, empty responses, etc.)
            try:
                from ..llm.service import _parse_structured_response

                extraction_result = _parse_structured_response(
                    content, CategorizedFactExtractionResponse
                )
                if not isinstance(extraction_result, CategorizedFactExtractionResponse):
                    # Fallback: try direct parsing if helper returned dict
                    extraction_result = CategorizedFactExtractionResponse.model_validate(
                        extraction_result
                    )
            except (ValueError, TypeError, AttributeError, KeyError) as e:
                logger.warning(f"⚠️ [Categorized Extraction] Failed to parse response: {e}")
                raise

            # Convert to list of dicts
            categorized_facts = [
                {"text": f.text, "category": f.category}
                for f in extraction_result.facts
                if f.text.strip()
            ]

            logger.info(f"🔬 [Categorized Extraction] Extracted {len(categorized_facts)} facts")
            for i, fact in enumerate(categorized_facts[:3]):
                logger.info(f"  Fact {i+1}: [{fact['category']}] '{fact['text'][:50]}...'")

            if not categorized_facts and text.strip() and len(text.strip()) > 5:
                # Fallback for non-trivial text
                text_lower = text.lower().strip()
                trivial = ["hello", "hi", "hey", "thanks", "thank you", "bye", "goodbye"]
                if not any(t in text_lower for t in trivial) or len(text.strip()) > 30:
                    fact_text = (
                        f"User {text.strip()}"
                        if not text.strip().startswith(("User ", "I ", "My "))
                        else text.strip()
                    )
                    logger.info(f"🔬 [Categorized Extraction] Fallback: '{fact_text[:50]}...'")
                    detected_category = self._detect_category_from_text(fact_text)
                    return [{"text": fact_text, "category": detected_category}]

            return categorized_facts

        except (
            APIError,
            AuthenticationError,
            NotFoundError,
            RateLimitError,
            json.JSONDecodeError,
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
            RuntimeError,
            ConnectionError,
            OSError,
        ) as e:
            logger.error(f"❌ [Categorized Extraction] Failed: {e}", exc_info=True)
            # Fallback: detect category from text
            if text.strip():
                detected_category = self._detect_category_from_text(text)
                return [{"text": text, "category": detected_category}]
            return []

    def _extract_facts_cognitive(self, text: str) -> list[dict[str, Any]]:
        """
        Extract facts with categories AND emotional intensity (Flashbulb Memory support).

        This is the full cognitive extraction that includes:
        - Fact text
        - Category (biographical, preferences, temporal, relational)
        - Emotion (0.0 to 1.0) - for Flashbulb Memory effect

        High emotion facts get higher initial stability, making them harder to forget.

        Returns:
            List of dicts with 'text', 'category', and 'emotion' keys
        """
        logger.info(f"🧠 [Cognitive Extraction] Starting for text: '{text[:100]}...'")

        if not self.llm_available or not PYDANTIC_AVAILABLE:
            logger.warning("⚠️ [Cognitive Extraction] LLM/Pydantic not available. Fallback.")
            if text.strip():
                detected_category = self._detect_category_from_text(text)
                return [{"text": text, "category": detected_category, "emotion": 0.3}]
            return []

        # Build category descriptions including custom categories
        category_descriptions = "\n".join(
            f"- {cat}: {desc}" for cat, desc in MEMORY_CATEGORIES.items()
        )
        if self.custom_categories:
            for custom_cat in self.custom_categories:
                category_descriptions += f"\n- {custom_cat}: Custom category"

        system_prompt = (
            "You are a cognitive memory extraction engine that extracts "
            "and analyzes facts.\n\n"
            "Your goal is to extract key facts from the user input, "
            "categorize them, and assess their emotional intensity.\n\n"
            f"CATEGORIES:\n{category_descriptions}\n\n"
            "EMOTION SCALE (0.0 to 1.0):\n"
            "- 0.0-0.2: Mundane facts (routine, trivial information)\n"
            "- 0.3-0.5: Moderately important (preferences, regular habits)\n"
            "- 0.6-0.7: Significant (career changes, important relationships)\n"
            "- 0.8-1.0: Highly emotional (life events, trauma, major achievements, "
            "strong feelings)\n\n"
            "RULES:\n"
            "1. Extract distinct, standalone facts about the USER.\n"
            "2. Only extract DECLARATIVE STATEMENTS where the user reveals something "
            "about themselves.\n"
            "3. DO NOT extract questions, requests, or queries.\n"
            "4. Return an EMPTY array if no extractable facts.\n"
            "5. NEVER describe what the user is doing - only extract what the user "
            "IS or LIKES/DISLIKES.\n"
            "6. Assign the most appropriate category to each fact. Every fact "
            "MUST be assigned to one of: biographical, preferences, temporal, "
            'or relational. NEVER use "general" as a category.\n'
            "7. Assess emotional intensity based on the language and content.\n\n"
            "EXAMPLES:\n\n"
            "Input: \"I just got promoted to Senior Engineer! I've been working "
            'towards this for 3 years."\n'
            'Output: {{"facts": [\n'
            '    {{"text": "User got promoted to Senior Engineer", "category": "biographical", '
            '"emotion": 0.9}},\n'
            '    {{"text": "User has been working towards promotion for 3 years", '
            '"category": "temporal", "emotion": 0.7}}\n'
            "]}}\n\n"
            'Input: "My grandmother passed away last week."\n'
            'Output: {{"facts": [\n'
            '    {{"text": "User\'s grandmother passed away recently", "category": "biographical", '
            '"emotion": 0.95}}\n'
            "]}}\n\n"
            'Input: "I usually drink coffee in the morning."\n'
            'Output: {{"facts": [\n'
            '    {{"text": "User usually drinks coffee in the morning", "category": "preferences", '
            '"emotion": 0.2}}\n'
            "]}}\n\n"
            'Input: "My sister Emily is a doctor at Mayo Clinic and my '
            'brother-in-law David is a professor at MIT."\n'
            'Output: {{"facts": [\n'
            '    {{"text": "User\'s sister Emily is a doctor at Mayo Clinic", '
            '"category": "relational", '
            '"emotion": 0.5}},\n'
            '    {{"text": "User\'s brother-in-law David is a professor at '
            'MIT", "category": "relational", '
            '"emotion": 0.5}}\n'
            "]}}\n\n"
            'Input: "My family lives in Boston and we enjoy skiing and jazz music."\n'
            'Output: {{"facts": [\n'
            '    {{"text": "User\'s family lives in Boston", '
            '"category": "relational", "emotion": 0.4}},\n'
            '    {{"text": "User\'s family enjoys skiing and jazz music", '
            '"category": "relational", '
            '"emotion": 0.4}}\n'
            "]}}\n\n"
            'Input: "Hello, how are you?"\n'
            'Output: {{"facts": []}}'
        )

        try:
            logger.info(f"🧠 [Cognitive Extraction] Using model: {self.memory_llm_model}")

            # Format response_format for provider-optimal structured output
            from ..llm.service import _format_response_format_for_provider

            response = completion(
                model=self.memory_llm_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                response_format=_format_response_format_for_provider(
                    CognitiveFactExtractionResponse, self.memory_llm_model
                ),
                temperature=self._get_adjusted_temperature(self.memory_llm_model),
            )

            content = response.choices[0].message.content
            logger.info(f"🧠 [Cognitive Extraction] Response: '{content[:200]}...'")

            # Safely parse structured response (handles markdown code blocks, empty responses, etc.)
            try:
                from ..llm.service import _parse_structured_response

                extraction_result = _parse_structured_response(
                    content, CognitiveFactExtractionResponse
                )
                if not isinstance(extraction_result, CognitiveFactExtractionResponse):
                    # Fallback: try direct parsing if helper returned dict
                    extraction_result = CognitiveFactExtractionResponse.model_validate(
                        extraction_result
                    )
            except (ValueError, TypeError, AttributeError, KeyError) as e:
                logger.warning(f"⚠️ [Cognitive Extraction] Failed to parse response: {e}")
                raise

            # Convert to list of dicts
            cognitive_facts = [
                {
                    "text": f.text,
                    "category": f.category,
                    "emotion": f.emotion,
                }
                for f in extraction_result.facts
                if f.text.strip()
            ]

            logger.info(f"🧠 [Cognitive Extraction] Extracted {len(cognitive_facts)} facts")
            for i, fact in enumerate(cognitive_facts[:3]):
                logger.info(
                    f"  Fact {i+1}: [{fact['category']}] (emotion: {fact['emotion']:.2f}) "
                    f"'{fact['text'][:50]}...'"
                )

            # Fallback for non-trivial text with no extracted facts
            if not cognitive_facts and text.strip() and len(text.strip()) > 5:
                text_lower = text.lower().strip()
                trivial = ["hello", "hi", "hey", "thanks", "thank you", "bye", "goodbye"]
                if not any(t in text_lower for t in trivial) or len(text.strip()) > 30:
                    fact_text = (
                        f"User {text.strip()}"
                        if not text.strip().startswith(("User ", "I ", "My "))
                        else text.strip()
                    )
                    logger.info(f"🧠 [Cognitive Extraction] Fallback: '{fact_text[:50]}...'")
                    detected_category = self._detect_category_from_text(fact_text)
                    return [{"text": fact_text, "category": detected_category, "emotion": 0.3}]

            return cognitive_facts

        except (
            APIError,
            AuthenticationError,
            NotFoundError,
            RateLimitError,
            json.JSONDecodeError,
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
            RuntimeError,
            ConnectionError,
            OSError,
        ) as e:
            logger.error(f"❌ [Cognitive Extraction] Failed: {e}", exc_info=True)
            # Fallback: detect category from text
            if text.strip():
                detected_category = self._detect_category_from_text(text)
                return [{"text": text, "category": detected_category, "emotion": 0.3}]
            return []

    def _deduplicate_extracted_facts(
        self,
        facts: list[dict[str, Any]],
        similarity_threshold: float = 0.85,
    ) -> list[dict[str, Any]]:
        """
        Deduplicate facts extracted from the same message using embeddings.

        Prevents storing semantically duplicate facts like:
        - "User loves chocolate"
        - "The user's favorite candy is chocolate"

        When multiple similar facts are found, merge them by keeping:
        - The longer/richer text
        - The higher emotion score
        - The more specific category (biographical > preferences > temporal > relational)

        Args:
            facts: List of fact dicts with 'text', 'category', 'emotion'
            similarity_threshold: Cosine similarity threshold for deduplication (default: 0.85)

        Returns:
            Deduplicated list of facts with merged attributes
        """
        if len(facts) <= 1:
            return facts

        # Category priority: more specific = higher priority
        category_priority = {
            "biographical": 5,
            "preferences": 4,
            "temporal": 3,
            "relational": 2,
        }

        def get_category_priority(cat: str) -> int:
            return category_priority.get(cat, 0)

        def merge_facts(existing: dict, new: dict) -> dict:
            """Merge two similar facts, keeping the best attributes."""
            # Keep longer/richer text
            if len(new["text"]) > len(existing["text"]):
                merged_text = new["text"]
            else:
                merged_text = existing["text"]

            # Keep higher emotion
            merged_emotion = max(existing.get("emotion", 0.3), new.get("emotion", 0.3))

            # Keep more specific category
            # If category missing, detect from text
            existing_cat = existing.get("category") or self._detect_category_from_text(
                existing.get("text", "")
            )
            new_cat = new.get("category") or self._detect_category_from_text(new.get("text", ""))
            if get_category_priority(new_cat) > get_category_priority(existing_cat):
                merged_category = new_cat
            else:
                merged_category = existing_cat

            return {
                "text": merged_text,
                "category": merged_category,
                "emotion": merged_emotion,
            }

        logger.info(f"🔄 [Dedup] Starting deduplication of {len(facts)} extracted facts")

        # Generate embeddings for all facts upfront
        facts_with_embeddings = []
        for fact in facts:
            embedding = self._get_embedding(fact["text"])
            if embedding:
                facts_with_embeddings.append(
                    {
                        **fact,
                        "_embedding": embedding,
                    }
                )
            else:
                logger.warning(f"⚠️ [Dedup] Failed to get embedding for: '{fact['text'][:50]}...'")

        if not facts_with_embeddings:
            return facts  # Fallback to original if all embeddings failed

        # Deduplicate using embeddings
        deduplicated: list[dict[str, Any]] = []

        for fact in facts_with_embeddings:
            merged = False
            fact_embedding = fact["_embedding"]

            for i, existing in enumerate(deduplicated):
                existing_embedding = existing["_embedding"]
                similarity = cosine_similarity(fact_embedding, existing_embedding)

                if similarity > similarity_threshold:
                    # Merge into existing fact
                    merged_fact = merge_facts(existing, fact)
                    # Keep the embedding of the longer text
                    if len(fact["text"]) > len(existing["text"]):
                        merged_fact["_embedding"] = fact_embedding
                    else:
                        merged_fact["_embedding"] = existing_embedding

                    deduplicated[i] = merged_fact
                    merged = True
                    logger.info(
                        f"🔄 [Dedup] Merged (sim={similarity:.2f}): "
                        f"'{fact['text'][:30]}...' → '{merged_fact['text'][:30]}...'"
                    )
                    break

            if not merged:
                deduplicated.append(fact)

        # Remove internal embeddings before returning
        result = [{k: v for k, v in f.items() if k != "_embedding"} for f in deduplicated]

        if len(result) < len(facts):
            logger.info(f"🔄 [Dedup] Reduced {len(facts)} facts → {len(result)} unique facts")
        else:
            logger.info(f"🔄 [Dedup] No duplicates found in {len(facts)} facts")

        return result

    def _ensure_cognitive_fields(self):
        """Ensure all existing memories have cognitive fields."""
        try:
            now = datetime.now(timezone.utc)
            # Add default importance and access_count to existing memories without them
            self.collection.update_many(
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
            self.collection.update_many(
                {"mention_count": {"$exists": False}},
                {
                    "$set": {
                        "mention_count": 1,
                        "last_mentioned": now,
                    }
                },
            )
            # Add cognitive decay fields (stability, emotion) if missing
            # New memories will have proper values; existing ones get defaults
            self.collection.update_many(
                {"stability": {"$exists": False}},
                {
                    "$set": {
                        "stability": CognitiveMath.DEFAULT_STABILITY_HOURS,
                        "emotion": 0.3,  # Neutral emotion default
                    }
                },
            )
            # Add soft-delete field (is_active) if missing
            # All existing memories are considered active
            self.collection.update_many(
                {"is_active": {"$exists": False}},
                {
                    "$set": {
                        "is_active": True,
                    }
                },
            )
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to ensure cognitive fields: {e}")

    def _assess_importance(self, text: str) -> float:
        """
        Assess the importance of a memory using LLM.

        Returns importance score between 0.1 and 1.0.
        """
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
            if not self.llm_available:
                logger.warning("LLM not available for importance assessment, using default")
                return 0.5

            # Use LiteLLM for importance assessment
            # Temperature is auto-adjusted for Gemini 3 models
            response = completion(
                model=self.memory_llm_model,
                messages=[{"role": "user", "content": importance_prompt}],
                temperature=self._get_adjusted_temperature(self.memory_llm_model),
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
        except (PyMongoError, OperationFailure):
            logger.exception("Failed to assess importance")
            return 0.5

    async def _assess_importance_async(self, text: str) -> float:
        """
        Async importance assessment using acompletion.

        This allows multiple importance assessments to run in parallel.
        """
        if not self.llm_available or acompletion is None:
            logger.warning("LLM not available for async importance assessment")
            return 0.5

        importance_prompt = (
            "On a scale of 1-10, rate the importance of remembering this information "
            "long-term. Consider factors like: uniqueness of information, "
            "actionability, personal significance, and whether it contains key facts "
            "or decisions. Respond with just a number.\n\n"
            f"Text to evaluate: {text}"
        )

        try:
            response = await acompletion(
                model=self.memory_llm_model,
                messages=[{"role": "user", "content": importance_prompt}],
                temperature=self._get_adjusted_temperature(self.memory_llm_model),
            )

            rating_text = response.choices[0].message.content

            try:
                rating = float("".join(c for c in rating_text if c.isdigit() or c == "."))
                importance = min(max(rating / 10.0, 0.1), 1.0)
                return importance
            except ValueError:
                logger.warning(f"Could not parse importance rating: {rating_text}")
                return 0.5
        except (
            APIError,
            AuthenticationError,
            NotFoundError,
            RateLimitError,
            RuntimeError,
            ConnectionError,
            OSError,
        ) as e:
            logger.warning(f"⚠️ Async importance assessment failed: {e}")
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
                importance = await self._assess_importance_async(text)
                return (text, importance)

        # Run all assessments in parallel
        results = await asyncio.gather(
            *[assess_with_semaphore(t) for t in texts], return_exceptions=True
        )

        # Build result dict, handling exceptions
        importance_map: dict[str, float] = {}
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(f"⚠️ Importance assessment {i} failed: {result}")
                importance_map[texts[i]] = 0.5  # Default importance
            else:
                text, importance = result
                importance_map[text] = importance

        logger.info(f"⚡ [Parallel Importance] Assessed {len(texts)} texts concurrently")
        return importance_map

    def _check_for_duplicate(
        self,
        text: str,
        user_id: str | None,
        embedding: list[float],
        threshold: float | None = None,
    ) -> dict[str, Any] | None:
        """
        Check for near-exact duplicate memories and update if found.

        This implements the "Last Seen" pattern - instead of creating duplicate
        memories, we update the existing memory's last_mentioned timestamp,
        increment mention_count, and boost importance.

        Args:
            text: The memory text to check
            user_id: User ID for scoping
            embedding: Vector embedding of the text
            threshold: Similarity threshold for duplicate detection
                (default: self.duplicate_threshold)

        Returns:
            Updated memory dict if duplicate found and updated, None otherwise
        """
        if threshold is None:
            threshold = self.duplicate_threshold

        try:
            # Find very similar memories (near-exact match)
            similar = self._find_similar_memories(
                user_id=user_id,
                embedding=embedding,
                top_n=1,
            )

            if not similar:
                return None

            top_match = similar[0]
            if top_match["similarity"] < threshold:
                return None

            # Found a near-duplicate - update instead of insert
            logger.info(
                f"🔄 [Dedup] Found duplicate memory (similarity={top_match['similarity']:.3f}): "
                f"'{top_match['memory'][:50]}...' (skipping duplicate: '{text[:50]}...')"
            )

            # Get current importance and category
            current_importance = top_match.get("importance", 0.5)
            existing_category = top_match.get("metadata", {}).get("category")
            # Also check top-level category field if it exists
            existing_doc = self.collection.find_one({"_id": top_match["id"]})
            if existing_doc:
                existing_category = existing_doc.get("category") or existing_category
            # If category is missing, detect from text
            if not existing_category:
                existing_text = top_match.get("memory", "")
                existing_category = (
                    self._detect_category_from_text(existing_text)
                    if existing_text
                    else "biographical"
                )

            # Boost importance when duplicate is found (reinforcement)
            new_importance = min(current_importance * self.reinforcement_factor, 1.0)

            # Check if new text suggests a better category
            best_category = self._get_best_category(
                existing_category=existing_category,
                new_text=text,
                new_category=None,  # We don't have extracted category here, detect from text
            )

            # Prepare update fields
            update_fields = {
                "last_mentioned": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
                "importance": new_importance,
            }

            # Update category if it changed
            if best_category != existing_category:
                update_fields["category"] = best_category
                update_fields["metadata.category"] = best_category
                logger.info(f"🔄 [Dedup] Category updated: {existing_category} → {best_category}")

            # Update the existing memory with boosted importance and corrected category
            update_result = self.collection.update_one(
                {"_id": top_match["id"]},
                {
                    "$set": update_fields,
                    "$inc": {
                        "mention_count": 1,
                        "access_count": 1,
                    },
                },
            )

            if update_result.modified_count > 0:
                logger.info(
                    f"✅ [Dedup] Boosted existing memory instead of creating "
                    f"duplicate: id={top_match['id']}, importance "
                    f"{current_importance:.2f} → {new_importance:.2f}"
                )

                # Return the updated memory info
                updated_metadata = top_match.get("metadata", {}).copy()
                updated_metadata["category"] = best_category

                return {
                    "id": str(top_match["id"]),
                    "memory": top_match["memory"],
                    "metadata": updated_metadata,
                    "user_id": str(user_id) if user_id else None,
                    "importance": new_importance,
                    "category": best_category,
                    "action": "deduplicated",
                    "similarity": top_match["similarity"],
                    "mention_count": top_match.get("access_count", 0) + 1,
                }

            return None

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"⚠️ [Dedup] Duplicate check failed: {e}")
            return None

    def _find_similar_memories(
        self,
        user_id: str,
        embedding: list[float],
        top_n: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Find similar memories using vector search.

        Returns memories with similarity scores and effective importance.
        """
        try:
            pipeline = [
                {
                    "$vectorSearch": {
                        "index": self.index_name,
                        "path": "embedding",
                        "queryVector": embedding,
                        "numCandidates": top_n * 20,
                        "limit": top_n,
                        "filter": {"user_id": str(user_id)} if user_id else {},
                    }
                },
                {"$addFields": {"similarity": {"$meta": "vectorSearchScore"}}},
                {
                    "$project": {
                        "_id": 1,
                        "text": 1,
                        "embedding": 1,
                        "importance": {"$ifNull": ["$importance", 0.5]},
                        "access_count": {"$ifNull": ["$access_count", 0]},
                        "similarity": 1,
                        "metadata": 1,
                        "created_at": 1,
                        "last_accessed": 1,
                    }
                },
            ]

            cursor = self.collection.aggregate(pipeline)
            results = []

            for doc in cursor:
                importance = doc.get("importance", 0.5)
                access_count = doc.get("access_count", 0)

                # Calculate effective importance: importance * (1 + ln(access_count + 1))
                effective_importance = importance * (1 + math.log(access_count + 1))

                results.append(
                    {
                        "id": str(doc["_id"]),
                        "memory": doc.get("text", ""),
                        "importance": importance,
                        "effective_importance": effective_importance,
                        "access_count": access_count,
                        "similarity": doc.get("similarity", 0.0),
                        "embedding": doc.get("embedding"),
                        "metadata": doc.get("metadata", {}),
                    }
                )

            return results
        except (PyMongoError, OperationFailure):
            logger.exception("Error finding similar memories")
            return []

    async def _find_similar_memories_async(
        self,
        user_id: str,
        embedding: list[float],
        top_n: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Async version of _find_similar_memories for parallel execution.

        Uses asyncio.to_thread to run the blocking MongoDB operation
        in a thread pool, allowing multiple searches to run concurrently.
        """
        return await asyncio.to_thread(self._find_similar_memories, user_id, embedding, top_n)

    async def _find_similar_memories_parallel(
        self,
        user_id: str,
        embeddings: list[list[float]],
        top_n: int = 5,
    ) -> list[list[dict[str, Any]]]:
        """
        Run multiple similarity searches in parallel with rate limiting.

        Args:
            user_id: User ID for filtering
            embeddings: List of embedding vectors to search
            top_n: Number of results per search

        Returns:
            List of search results (one per embedding, in same order)
        """
        if not embeddings:
            return []

        semaphore = asyncio.Semaphore(_PARALLEL_SEMAPHORE_LIMIT)

        async def search_with_semaphore(embedding: list[float]) -> list[dict[str, Any]]:
            async with semaphore:
                return await self._find_similar_memories_async(user_id, embedding, top_n)

        # Run all searches in parallel
        results = await asyncio.gather(
            *[search_with_semaphore(emb) for emb in embeddings], return_exceptions=True
        )

        # Handle any exceptions gracefully
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(f"⚠️ Parallel search {i} failed: {result}")
                processed_results.append([])
            else:
                processed_results.append(result)

        logger.info(f"⚡ [Parallel Search] Completed {len(embeddings)} searches concurrently")
        return processed_results

    def _get_decay_aware_pipeline(
        self,
        query_vector: list[float],
        user_id: str | None,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Build a MongoDB aggregation pipeline that performs decay-aware ranking.

        This moves the Ebbinghaus Forgetting Curve calculation to the database
        using MongoDB's $exp operator, enabling efficient server-side ranking.

        Formula: strength = importance * exp(-t / stability)
        Where t = (now - last_accessed) in hours

        Final ranking: final_score = similarity * strength

        Args:
            query_vector: Query embedding vector
            user_id: User ID for filtering
            limit: Number of results to return
            filters: Additional filters to apply

        Returns:
            MongoDB aggregation pipeline stages
        """
        now = datetime.now(timezone.utc)

        # Build the search filter
        search_filter = {"is_active": True}  # Only search active memories
        if user_id:
            search_filter["user_id"] = str(user_id)

        # Memory type filtering (Cognitive Blueprint v2.0)
        memory_type_filter = filters.get("memory_type") if filters else None
        if memory_type_filter:
            search_filter["memory_type"] = memory_type_filter

        if filters:
            for key, value in filters.items():
                if key == "metadata" and isinstance(value, dict):
                    for k, v in value.items():
                        search_filter[f"metadata.{k}"] = v
                elif key not in [
                    "OR",
                    "AND",
                    "memory_type",
                ]:  # Exclude memory_type (already handled)
                    search_filter[key] = value

        pipeline = [
            # Stage 1: Vector Search with is_active filter
            {
                "$vectorSearch": {
                    "index": self.index_name,
                    "path": "embedding",
                    "queryVector": query_vector,
                    "numCandidates": limit * 20,
                    "limit": limit * 3,  # Get more candidates for decay filtering
                    "filter": search_filter if search_filter else {},
                }
            },
            # Stage 2: Add similarity score and calculate time elapsed
            {
                "$addFields": {
                    "similarity": {"$meta": "vectorSearchScore"},
                    # Calculate t = (now - last_accessed) in hours
                    # MongoDB stores dates as milliseconds, so divide by 3600000
                    "t_hours": {
                        "$divide": [
                            {"$subtract": [now, {"$ifNull": ["$last_accessed", now]}]},
                            3600000,  # milliseconds to hours
                        ]
                    },
                    # Ensure stability has a minimum value to prevent division issues
                    "_stability": {
                        "$max": [
                            {"$ifNull": ["$stability", CognitiveMath.DEFAULT_STABILITY_HOURS]},
                            CognitiveMath.MIN_STABILITY,
                        ]
                    },
                    "_importance": {"$ifNull": ["$importance", 0.5]},
                }
            },
            # Stage 3: Calculate retrieval strength using Ebbinghaus formula
            # S = R * exp(-t / H)
            {
                "$addFields": {
                    "strength": {
                        "$multiply": [
                            "$_importance",
                            {"$exp": {"$divide": [{"$multiply": ["$t_hours", -1]}, "$_stability"]}},
                        ]
                    }
                }
            },
            # Stage 4: Calculate final score (similarity * strength)
            {"$addFields": {"final_score": {"$multiply": ["$similarity", "$strength"]}}},
            # Stage 5: Sort by final_score (decay-aware ranking)
            {"$sort": {"final_score": -1}},
            # Stage 6: Limit results
            {"$limit": limit},
            # Stage 7: Project final fields
            {
                "$project": {
                    "_id": 1,
                    "text": 1,
                    "metadata": 1,
                    "user_id": 1,
                    "created_at": 1,
                    "similarity": 1,
                    "strength": 1,
                    "final_score": 1,
                    "importance": "$_importance",
                    "stability": "$_stability",
                    "emotion": {"$ifNull": ["$emotion", 0.3]},
                    "access_count": {"$ifNull": ["$access_count", 0]},
                    "last_accessed": 1,
                    "category": 1,
                }
            },
        ]

        return pipeline

    def _search_with_decay(
        self,
        query_vector: list[float],
        user_id: str | None,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
        update_access: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Perform decay-aware search using server-side MongoDB aggregation.

        Uses the Ebbinghaus Forgetting Curve to rank memories by their
        current retrieval strength, combined with vector similarity.

        Args:
            query_vector: Query embedding vector
            user_id: User ID for filtering
            limit: Number of results to return
            filters: Additional filters to apply
            update_access: Whether to update access counts and stability

        Returns:
            List of memory results with decay-aware ranking
        """
        logger.info(f"🔍 [Decay Search] Using server-side decay pipeline for user_id={user_id}")

        try:
            # Build and execute decay-aware pipeline
            pipeline = self._get_decay_aware_pipeline(
                query_vector=query_vector,
                user_id=user_id,
                limit=limit,
                filters=filters,
            )

            cursor = self.collection.aggregate(pipeline)

            results = []
            memory_ids_to_update = []
            similarity_map = {}  # For stability growth calculation

            for doc in cursor:
                memory_id = str(doc["_id"])
                similarity = doc.get("similarity", 0.0)
                strength = doc.get("strength", 0.0)
                final_score = doc.get("final_score", similarity * strength)

                results.append(
                    {
                        "id": memory_id,
                        "memory": doc.get("text", ""),
                        "metadata": doc.get("metadata", {}),
                        "user_id": doc.get("user_id"),
                        "score": final_score,
                        "similarity": similarity,
                        "strength": strength,
                        "importance": doc.get("importance", 0.5),
                        "stability": doc.get("stability", CognitiveMath.DEFAULT_STABILITY_HOURS),
                        "emotion": doc.get("emotion", 0.3),
                        "access_count": doc.get("access_count", 0),
                        "category": doc.get("category"),
                        "last_accessed": doc.get("last_accessed").isoformat()
                        if doc.get("last_accessed")
                        else None,
                        "created_at": doc.get("created_at").isoformat()
                        if doc.get("created_at")
                        else None,
                    }
                )

                memory_ids_to_update.append(doc["_id"])
                similarity_map[memory_id] = similarity

            logger.info(f"🔍 [Decay Search] Found {len(results)} results with decay-aware ranking")

            # Update access counts and grow stability (Spacing Effect)
            if update_access and memory_ids_to_update:
                self._update_access_with_stability_growth(
                    memory_ids=memory_ids_to_update,
                    similarity_map=similarity_map,
                )

            return results

        except OperationFailure as e:
            error_msg = str(e)
            if "index" in error_msg.lower() or "vectorSearch" in error_msg.lower():
                logger.exception(
                    f"❌ [Decay Search] Vector search index error: {error_msg}. "
                    f"Falling back to standard search."
                )
                # Fallback to standard search without decay
                return self._search_without_decay(
                    query_vector=query_vector,
                    user_id=user_id,
                    limit=limit,
                    filters=filters,
                    update_access=update_access,
                )
            raise
        except (PyMongoError, ValueError, TypeError, RuntimeError) as e:
            logger.exception(f"❌ [Decay Search] Failed: {e}")
            return []

    def _search_without_decay(
        self,
        query_vector: list[float],
        user_id: str | None,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
        update_access: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Standard search without decay (fallback method).

        Uses effective_importance = importance * (1 + ln(access_count + 1))
        """
        search_filter = {"is_active": True}
        if user_id:
            search_filter["user_id"] = str(user_id)

        # Memory type filtering (Cognitive Blueprint v2.0)
        memory_type_filter = filters.get("memory_type") if filters else None
        if memory_type_filter:
            search_filter["memory_type"] = memory_type_filter

        if filters:
            for key, value in filters.items():
                if key == "metadata" and isinstance(value, dict):
                    for k, v in value.items():
                        search_filter[f"metadata.{k}"] = v
                elif key not in [
                    "OR",
                    "AND",
                    "memory_type",
                ]:  # Exclude memory_type (already handled)
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
                    "stability": {"$ifNull": ["$stability", CognitiveMath.DEFAULT_STABILITY_HOURS]},
                    "emotion": {"$ifNull": ["$emotion", 0.3]},
                    "category": 1,
                }
            },
        ]

        cursor = self.collection.aggregate(pipeline)
        results = []
        memory_ids_to_update = []

        for doc in cursor:
            similarity = doc.get("similarity", 0.0)
            importance = doc.get("importance", 0.5)
            access_count = doc.get("access_count", 0)

            effective_importance = importance * (1 + math.log(access_count + 1))
            combined_score = similarity * effective_importance

            results.append(
                {
                    "id": str(doc["_id"]),
                    "memory": doc.get("text", ""),
                    "metadata": doc.get("metadata", {}),
                    "user_id": doc.get("user_id"),
                    "score": combined_score,
                    "similarity": similarity,
                    "importance": importance,
                    "effective_importance": effective_importance,
                    "stability": doc.get("stability"),
                    "emotion": doc.get("emotion"),
                    "access_count": access_count,
                    "category": doc.get("category"),
                    "created_at": doc.get("created_at").isoformat()
                    if doc.get("created_at")
                    else None,
                }
            )
            memory_ids_to_update.append(doc["_id"])

        # Sort and limit
        results.sort(key=lambda x: x["score"], reverse=True)
        results = results[:limit]

        # Update access counts
        if update_access and memory_ids_to_update:
            self.collection.update_many(
                {"_id": {"$in": memory_ids_to_update}},
                {
                    "$inc": {"access_count": 1},
                    "$set": {"last_accessed": datetime.now(timezone.utc)},
                },
            )

        return results

    def _update_access_with_stability_growth(
        self,
        memory_ids: list,
        similarity_map: dict[str, float],
    ) -> None:
        """
        Update access counts and grow stability for retrieved memories.

        Implements the Spacing Effect - each retrieval makes the memory
        harder to forget by increasing its stability.

        Args:
            memory_ids: List of ObjectIds to update
            similarity_map: Map of memory_id to similarity score
        """
        now = datetime.now(timezone.utc)

        try:
            # Fetch current documents to calculate new stability
            for memory_id in memory_ids:
                doc = self.collection.find_one({"_id": memory_id})
                if not doc:
                    continue

                current_stability = doc.get("stability", CognitiveMath.DEFAULT_STABILITY_HOURS)
                similarity = similarity_map.get(str(memory_id), 0.5)
                emotion = doc.get("emotion", 0.3)

                # Calculate new stability (Spacing Effect)
                new_stability = CognitiveMath.grow_stability(
                    current_stability=current_stability,
                    similarity=similarity,
                    emotion=emotion,
                )

                # Update the memory
                self.collection.update_one(
                    {"_id": memory_id},
                    {
                        "$inc": {"access_count": 1},
                        "$set": {
                            "last_accessed": now,
                            "stability": new_stability,
                        },
                    },
                )

                logger.debug(
                    f"📈 [Spacing Effect] Memory {memory_id}: "
                    f"stability {current_stability:.1f} → {new_stability:.1f}"
                )

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to update access with stability growth: {e}")

    def _get_category_priority(self, category: str) -> int:
        """
        Get priority score for a category (higher = more specific).

        Args:
            category: Category name

        Returns:
            Priority score (biographical=4, preferences=3, temporal=2, relational=1)
            Note: "general" is not a valid memory category - it's only for bucket_type
        """
        category_priority = {
            "biographical": 4,
            "preferences": 3,
            "temporal": 2,
            "relational": 1,
        }
        return category_priority.get(category, 0)

    def _detect_category_from_text(self, text: str) -> str:
        """
        Detect the most appropriate category for a memory text using heuristics.

        Uses keyword matching to detect category. Never returns "general" - if heuristics
        fail, uses LLM if available, otherwise defaults to "biographical" (most common).

        Args:
            text: Memory text to categorize

        Returns:
            Detected category string (biographical, preferences, temporal, or relational)
        """
        if not text:
            # Empty text defaults to biographical (most common category)
            return "biographical"

        text_lower = text.lower()

        # Relational keywords: family relationships, connections to others
        relational_keywords = [
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

        # Preferences keywords: likes, dislikes, favorites
        preferences_keywords = [
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

        # Biographical keywords: about the user themselves
        biographical_keywords = [
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

        # Temporal keywords: time-bound, deadlines, schedules
        temporal_keywords = [
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

        # Check for relational (family/relationships)
        if any(keyword in text_lower for keyword in relational_keywords):
            # But exclude if it's clearly about the user themselves
            if not any(bio_keyword in text_lower for bio_keyword in biographical_keywords):
                return "relational"

        # Check for preferences
        if any(keyword in text_lower for keyword in preferences_keywords):
            return "preferences"

        # Check for biographical (about user)
        if any(keyword in text_lower for keyword in biographical_keywords):
            return "biographical"

        # Check for temporal
        if any(keyword in text_lower for keyword in temporal_keywords):
            return "temporal"

        # If heuristics fail, try LLM if available
        if self.llm_available:
            try:
                # Use a simple LLM call to detect category
                category_prompt = (
                    f"Classify this memory into ONE category: biographical, "
                    f"preferences, temporal, or relational.\n\n"
                    f"Memory: {text}\n\n"
                    f"Categories:\n"
                    f"- biographical: Personal info about the user "
                    f"(name, age, occupation, location, education)\n"
                    f"- preferences: Likes, dislikes, preferences, favorites\n"
                    f"- temporal: Current projects, deadlines, short-term goals, "
                    f"recent events\n"
                    f"- relational: Relationships, feelings about others, "
                    f"family members\n\n"
                    f"Return ONLY the category name (one word)."
                )

                response = completion(
                    model=self.memory_llm_model,
                    messages=[{"role": "user", "content": category_prompt}],
                    temperature=0.0,  # Deterministic
                )

                detected = response.choices[0].message.content.strip().lower()
                # Validate the response
                valid_categories = ["biographical", "preferences", "temporal", "relational"]
                if detected in valid_categories:
                    logger.debug(f"🤖 [Category Detection] LLM detected: {detected}")
                    return detected
            except (
                AttributeError,
                RuntimeError,
                ValueError,
                TypeError,
                KeyError,
            ) as e:
                logger.warning(
                    f"⚠️ LLM category detection failed: {e}, " f"defaulting to biographical"
                )

        # Final fallback: default to biographical (most common and safest)
        logger.debug("🔄 [Category Detection] No match found, defaulting to biographical")
        return "biographical"

    def _get_best_category(
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
            existing_category = (
                self._detect_category_from_text(new_text) if new_text else "biographical"
            )

        # If we have a new category from extraction, use it
        if new_category:
            # If new_category is invalid, detect from text
            if new_category not in MEMORY_CATEGORIES:
                new_category = (
                    self._detect_category_from_text(new_text) if new_text else "biographical"
                )

            existing_priority = self._get_category_priority(existing_category)
            new_priority = self._get_category_priority(new_category)

            # Use the category with higher priority
            if new_priority > existing_priority:
                logger.debug(
                    f"🔄 [Category] Upgrading category: {existing_category} → {new_category} "
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
                f"🔄 [Category] Upgrading category: {existing_category} → {detected_category} "
                f"(priority {existing_priority} → {detected_priority})"
            )
            return detected_category

        return existing_category

    def _has_new_information(self, existing_text: str, new_text: str) -> bool:
        """
        Detect if new_text contains meaningful information not in existing_text.

        This helps identify when a memory should be merged even if the new text
        isn't longer (e.g., "User loves chocolate mushrooms" vs "User loves chocolate").

        Args:
            existing_text: The current memory text
            new_text: The incoming text to compare

        Returns:
            True if new_text contains novel meaningful words/concepts
        """
        if not existing_text or not new_text:
            return False

        # Tokenize both texts (simple word splitting, lowercased)
        existing_words = set(existing_text.lower().split())
        new_words = set(new_text.lower().split())

        # Words in new text that aren't in existing
        novel_words = new_words - existing_words

        # Common stopwords to filter out - these don't represent new information
        stopwords = {
            # Articles and determiners
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
            # Common verbs and auxiliaries
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
            # Prepositions and conjunctions
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
            # Common memory-related words (these appear in most memories)
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
            # Other common words
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
        }

        # Filter to meaningful novel words
        meaningful_novel = novel_words - stopwords

        # Also filter out single characters and numbers
        meaningful_novel = {w for w in meaningful_novel if len(w) > 1 and not w.isdigit()}

        if meaningful_novel:
            logger.debug(
                f"🔍 [New Info Detection] Found novel words: {meaningful_novel} "
                f"(existing: '{existing_text[:30]}...', new: '{new_text[:30]}...')"
            )

        return len(meaningful_novel) > 0

    def _reinforce_memory(
        self,
        memory_id: str,
        similarity: float,
        new_text: str | None = None,
        new_embedding: list[float] | None = None,
    ) -> dict[str, Any] | bool:
        """
        Reinforce an existing memory (similarity > merge_threshold_high).

        If new_text is provided and contains more specific information than the
        existing memory, the texts will be merged using LLM to preserve the
        additional details (e.g., "User loves dark chocolate" updates
        "User loves chocolate").

        Args:
            memory_id: ID of the memory to reinforce
            similarity: Cosine similarity score between new and existing memory
            new_text: Optional new text that may contain more specific information
            new_embedding: Optional embedding for the new text (for updating)

        Returns:
            dict with updated memory info if text was merged, True if only reinforced,
            False if failed.
        """
        try:
            doc = self.collection.find_one({"_id": memory_id})
            if not doc:
                return False

            existing_text = doc.get("text", "")
            current_importance = doc.get("importance", 0.5)
            current_access = doc.get("access_count", 0)
            existing_category = doc.get("category") or doc.get("metadata", {}).get("category")
            # If category is missing, detect from text
            if not existing_category:
                existing_category = (
                    self._detect_category_from_text(existing_text)
                    if existing_text
                    else "biographical"
                )

            # Check if new text provides more specific information
            should_merge_text = False
            merged_text = existing_text

            if new_text and self.llm_available:
                # Heuristics to detect if new text might be more specific:
                # 1. New text is longer than existing
                # 2. New text contains novel words/concepts not in existing
                # 3. Similarity is high but not identical (0.85-0.98)
                new_is_longer = len(new_text) > len(existing_text)
                has_new_info = self._has_new_information(existing_text, new_text)
                not_identical = similarity < 0.98  # Leave room for slight differences

                if (new_is_longer or has_new_info) and not_identical:
                    should_merge_text = True
                    merge_reason = "longer" if new_is_longer else "new information"
                    logger.info(
                        f"🔄 [Reinforce] New text has {merge_reason}, merging: "
                        f"'{existing_text[:50]}...' + '{new_text[:50]}...'"
                    )

                    # Use LLM to intelligently merge the texts
                    merge_prompt = (
                        "You are updating a memory with more specific information. "
                        "The new text contains additional details that should be preserved. "
                        "Combine these into a single, concise memory that includes ALL details:\n\n"
                        f"EXISTING MEMORY: {existing_text}\n\n"
                        f"NEW INFORMATION: {new_text}\n\n"
                        "Output a single updated memory that preserves all specific details. "
                        "Be concise but don't lose any information."
                    )

                    try:
                        response = completion(
                            model=self.memory_llm_model,
                            messages=[{"role": "user", "content": merge_prompt}],
                            temperature=self._get_adjusted_temperature(self.memory_llm_model),
                        )
                        merged_text = response.choices[0].message.content.strip()
                        logger.info(f"🔄 [Reinforce] Merged text: '{merged_text[:80]}...'")
                    except (APIError, AuthenticationError, NotFoundError, RateLimitError) as e:
                        logger.warning(f"⚠️ LLM merge failed, keeping existing text: {e}")
                        merged_text = existing_text
                        should_merge_text = False

            # Reinforce: increase importance and access count
            new_importance = min(current_importance * self.reinforcement_factor, 1.0)
            new_access_count = current_access + 1

            # Check if new text suggests a better category
            best_category = existing_category
            if new_text:
                best_category = self._get_best_category(
                    existing_category=existing_category,
                    new_text=new_text if not should_merge_text else merged_text,
                    new_category=None,
                )

            update_fields = {
                "importance": new_importance,
                "access_count": new_access_count,
                "last_accessed": datetime.now(timezone.utc),
            }

            # Update category if it changed
            if best_category != existing_category:
                update_fields["category"] = best_category
                update_fields["metadata.category"] = best_category
                logger.info(
                    f"🔄 [Reinforce] Category updated: {existing_category} → {best_category}"
                )

            # If we merged text, also update the text and embedding
            if should_merge_text and merged_text != existing_text:
                update_fields["text"] = merged_text
                update_fields["metadata.merged"] = True
                update_fields["metadata.last_merged_at"] = datetime.now(timezone.utc).isoformat()

                # Update embedding if we have a new one, or generate a new one
                if new_embedding:
                    # Average the embeddings to capture both semantic spaces
                    existing_embedding = doc.get("embedding", [])
                    if existing_embedding and len(existing_embedding) == len(new_embedding):
                        update_fields["embedding"] = [
                            (a + b) / 2.0
                            for a, b in zip(new_embedding, existing_embedding, strict=False)
                        ]
                    else:
                        update_fields["embedding"] = new_embedding
                elif self.embedding_service:
                    # Generate new embedding for merged text
                    try:
                        new_emb = self._get_embedding(merged_text)
                        if new_emb:
                            update_fields["embedding"] = new_emb
                    except (ValueError, RuntimeError) as e:
                        logger.warning(f"⚠️ Failed to re-embed merged text: {e}")

            self.collection.update_one(
                {"_id": memory_id},
                {"$set": update_fields},
            )

            if should_merge_text:
                logger.info(
                    f"🔄 [Reinforce+Merge] Memory {memory_id}: "
                    f"'{existing_text[:30]}...' → '{merged_text[:30]}...', "
                    f"importance {current_importance:.2f} → {new_importance:.2f}"
                )
                return {
                    "id": str(memory_id),
                    "memory": merged_text,
                    "importance": new_importance,
                    "category": best_category,
                    "merged": True,
                }
            else:
                logger.debug(
                    f"Reinforced memory {memory_id}: importance "
                    f"{current_importance:.2f} -> {new_importance:.2f}"
                )
                return True

        except (PyMongoError, OperationFailure):
            logger.exception(f"Failed to reinforce memory {memory_id}")
            return False

    def _merge_memories(
        self,
        new_memory_id: str,
        existing_memory_id: str,
        new_text: str,
        existing_text: str,
        new_embedding: list[float],
        existing_embedding: list[float],
        new_importance: float,
        existing_importance: float,
    ) -> bool:
        """
        Merge two similar memories into one.

        Returns True if merge occurred.
        """
        if not self.llm_available:
            logger.warning("No LLM client available for memory merging")
            return False

        try:
            # Use LLM to combine the memories
            merge_prompt = (
                "These two texts contain related information. Combine them into a "
                "single cohesive text that preserves all important details from both "
                "without redundancy:\n\n"
                f"TEXT 1: {new_text}\n\n"
                f"TEXT 2: {existing_text}\n\n"
                "Combine these texts effectively into one unified memory."
            )

            if not self.llm_available:
                logger.warning("LLM not available for memory merging, skipping merge")
                return False

            # Use LiteLLM for memory merging
            # Temperature is auto-adjusted for Gemini 3 models
            response = completion(
                model=self.memory_llm_model,
                messages=[{"role": "user", "content": merge_prompt}],
                temperature=self._get_adjusted_temperature(self.memory_llm_model),
            )

            merged_text = response.choices[0].message.content.strip()

            # Average embeddings
            merged_embedding = [
                (a + b) / 2.0 for a, b in zip(new_embedding, existing_embedding, strict=False)
            ]

            # Use higher importance, boosted slightly
            merged_importance = min(max(new_importance, existing_importance) * 1.1, 1.0)

            # Get existing access count and categories
            existing_doc = self.collection.find_one({"_id": existing_memory_id})
            existing_access = existing_doc.get("access_count", 0) if existing_doc else 0
            existing_category = (
                existing_doc.get("category") or existing_doc.get("metadata", {}).get("category")
                if existing_doc
                else None
            )
            # If category is missing, detect from text
            if not existing_category:
                existing_category = (
                    self._detect_category_from_text(existing_text)
                    if existing_text
                    else "biographical"
                )

            # Get new memory category
            new_doc = self.collection.find_one({"_id": new_memory_id})
            new_category = (
                new_doc.get("category") or new_doc.get("metadata", {}).get("category")
                if new_doc
                else None
            )
            # If category is missing, detect from text
            if not new_category:
                new_category = (
                    self._detect_category_from_text(new_text) if new_text else "biographical"
                )

            # Use category priority to choose best
            best_category = self._get_best_category(
                existing_category=existing_category,
                new_text=merged_text,
                new_category=new_category,
            )

            # Prepare update fields
            update_fields = {
                "text": merged_text,
                "embedding": merged_embedding,
                "importance": merged_importance,
                "access_count": existing_access + 1,
                "last_accessed": datetime.now(timezone.utc),
                "metadata.merged": True,
                "category": best_category,
                "metadata.category": best_category,
            }

            if best_category != existing_category and best_category != new_category:
                logger.info(
                    f"🔄 [Merge] Category updated: "
                    f"{existing_category}/{new_category} → {best_category}"
                )

            # Update the new memory with merged content
            self.collection.update_one(
                {"_id": new_memory_id},
                {"$set": update_fields},
            )

            # Delete the old memory
            self.collection.delete_one({"_id": existing_memory_id})

            logger.info(f"Merged memory {existing_memory_id} into {new_memory_id}")
            return True
        except (PyMongoError, OperationFailure):
            logger.exception("Failed to merge memories")
            return False

    def _update_importance_decay(self, user_id: str, new_embedding: list[float]):
        """
        Update importance of other memories based on similarity to new content.

        Similar memories are reinforced, others decay.
        """
        try:
            cursor = self.collection.find({"user_id": str(user_id)})

            for doc in cursor:
                doc_id = doc["_id"]
                memory_embedding = doc.get("embedding")

                if not memory_embedding:
                    continue

                similarity = cosine_similarity(new_embedding, memory_embedding)
                current_importance = doc.get("importance", 0.5)

                if similarity > self.similarity_threshold:
                    # Reinforce similar memories
                    new_importance = min(current_importance * self.reinforcement_factor, 1.0)
                    new_access_count = doc.get("access_count", 0) + 1
                else:
                    # Decay less relevant memories
                    new_importance = max(current_importance * self.decay_factor, 0.1)
                    new_access_count = doc.get("access_count", 0)

                self.collection.update_one(
                    {"_id": doc_id},
                    {
                        "$set": {
                            "importance": new_importance,
                            "access_count": new_access_count,
                            "last_accessed": datetime.now(timezone.utc),
                        }
                    },
                )
        except (PyMongoError, OperationFailure):
            logger.exception("Failed to update importance decay")

    def _prune_memories(self, user_id: str):
        """
        Soft-delete weakest memories when count exceeds max_depth.

        Instead of hard-deleting, memories are moved to "cold storage" by:
        - Setting is_active = False
        - Recording pruned_at timestamp
        - Recording pruning_reason for audit trail

        This preserves data for analytics and potential recovery.
        """
        return self.prune_memories(
            user_id=user_id,
            max_capacity=self.max_depth,
            reason=PruningReason.CAPACITY_LIMIT,
        )

    def prune_memories(
        self,
        user_id: str,
        max_capacity: int | None = None,
        prune_percentage: float = 0.1,
        reason: str = PruningReason.CAPACITY_LIMIT,
        use_strength: bool = True,
    ) -> int:
        """
        Soft-delete the weakest memories when capacity is exceeded.

        This implements biological "forgetting" - weak memories fade to cold storage
        rather than being permanently deleted. The cold storage creates a paper trail
        for analytics (what do users forget?) and potential recovery.

        Args:
            user_id: User ID to prune memories for
            max_capacity: Maximum active memories allowed (default: self.max_depth)
            prune_percentage: Extra percentage to prune to avoid constant triggers (default: 0.1)
            reason: Pruning reason for audit trail (default: "capacity_limit_reached")
            use_strength: Use decay-aware strength for scoring (default: True)

        Returns:
            Number of memories moved to cold storage
        """
        if max_capacity is None:
            max_capacity = self.max_depth

        if max_capacity is None:
            return 0

        try:
            # 1. Count active memories for this user
            active_count = self.collection.count_documents(
                {
                    "user_id": str(user_id),
                    "is_active": True,
                }
            )

            if active_count <= max_capacity:
                return 0

            logger.info(
                f"🧹 [Prune] User {user_id} has {active_count} active memories "
                f"(max: {max_capacity}), starting pruning..."
            )

            # 2. Calculate how many to prune (with buffer to avoid constant triggers)
            to_prune_count = (active_count - max_capacity) + int(max_capacity * prune_percentage)

            # 3. Fetch active memories and calculate strength scores
            cursor = self.collection.find(
                {
                    "user_id": str(user_id),
                    "is_active": True,
                }
            )

            scored_memories = []
            for doc in cursor:
                if use_strength:
                    # Use decay-aware strength (Ebbinghaus)
                    strength = CognitiveMath.get_current_strength(doc)
                else:
                    # Fall back to effective importance
                    importance = doc.get("importance", 0.5)
                    access_count = doc.get("access_count", 0)
                    strength = importance * (1 + math.log(access_count + 1))

                scored_memories.append(
                    {
                        "_id": doc["_id"],
                        "strength": strength,
                        "text": doc.get("text", "")[:50],
                    }
                )

            # 4. Sort by strength (weakest first) and select targets
            scored_memories.sort(key=lambda x: x["strength"])
            targets = scored_memories[:to_prune_count]
            target_ids = [m["_id"] for m in targets]

            if not target_ids:
                return 0

            # 5. Soft-delete: Move to cold storage with audit trail
            now = datetime.now(timezone.utc)
            result = self.collection.update_many(
                {"_id": {"$in": target_ids}},
                {
                    "$set": {
                        "is_active": False,
                        "pruned_at": now,
                        "pruning_reason": reason,
                    }
                },
            )

            pruned_count = result.modified_count
            logger.info(
                f"✅ [Prune] Moved {pruned_count} memories to cold storage for user {user_id} "
                f"(reason: {reason})"
            )

            # Log some examples of pruned memories
            for m in targets[:3]:
                logger.debug(f"  🗑️ Pruned: '{m['text']}...' (strength: {m['strength']:.4f})")

            return pruned_count

        except (PyMongoError, OperationFailure) as e:
            logger.exception(f"Failed to prune memories for user {user_id}: {e}")
            return 0

    def get_cold_storage(
        self,
        user_id: str,
        limit: int = 100,
        include_reason: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Retrieve memories from cold storage (pruned/inactive memories).

        Cold storage contains memories that have been soft-deleted, providing:
        - Audit trail for what was forgotten
        - Analytics on user memory patterns
        - Recovery capability if needed

        Args:
            user_id: User ID to retrieve cold storage for
            limit: Maximum memories to return
            include_reason: Include pruning reason in results

        Returns:
            List of pruned memory documents
        """
        try:
            query = {
                "user_id": str(user_id),
                "is_active": False,
            }

            cursor = self.collection.find(query).sort("pruned_at", -1).limit(limit)

            results = []
            for doc in cursor:
                result = {
                    "id": str(doc["_id"]),
                    "memory": doc.get("text", ""),
                    "metadata": doc.get("metadata", {}),
                    "user_id": doc.get("user_id"),
                    "importance": doc.get("importance", 0.5),
                    "stability": doc.get("stability"),
                    "emotion": doc.get("emotion"),
                    "pruned_at": doc.get("pruned_at").isoformat() if doc.get("pruned_at") else None,
                    "created_at": doc.get("created_at").isoformat()
                    if doc.get("created_at")
                    else None,
                }

                if include_reason:
                    result["pruning_reason"] = doc.get("pruning_reason", "unknown")

                results.append(result)

            logger.info(
                f"📦 [Cold Storage] Retrieved {len(results)} pruned memories for user {user_id}"
            )
            return results

        except (PyMongoError, OperationFailure) as e:
            logger.exception(f"Failed to retrieve cold storage for user {user_id}: {e}")
            return []

    def restore_from_cold_storage(
        self,
        memory_id: str,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Restore a memory from cold storage to active status.

        Args:
            memory_id: Memory ID to restore
            user_id: User ID for security scoping

        Returns:
            Restored memory document, or None if not found
        """
        try:
            query = {"_id": ObjectId(memory_id), "is_active": False}
            if user_id:
                query["user_id"] = str(user_id)

            now = datetime.now(timezone.utc)
            result = self.collection.update_one(
                query,
                {
                    "$set": {
                        "is_active": True,
                        "last_accessed": now,
                        "restored_at": now,
                    },
                    "$unset": {
                        "pruned_at": "",
                        "pruning_reason": "",
                    },
                },
            )

            if result.modified_count > 0:
                logger.info(f"♻️ [Restore] Memory {memory_id} restored from cold storage")
                return self.get(memory_id, user_id)

            return None

        except (PyMongoError, OperationFailure, InvalidId) as e:
            logger.exception(f"Failed to restore memory {memory_id}: {e}")
            return None

    def get_persona(self) -> dict[str, Any] | None:
        """
        Get current persona for the app.

        Returns:
            Persona document or None if persona is disabled
        """
        if not self.persona_engine:
            return None
        return self.persona_engine.get_persona()

    def update_persona(
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
        return self.persona_engine.update_persona(role=role, description=description, traits=traits)

    def get_memory_analytics(
        self,
        user_id: str,
    ) -> dict[str, Any]:
        """
        Get analytics about a user's memory health.

        Returns metrics useful for understanding memory usage and decay patterns.

        Args:
            user_id: User ID to analyze

        Returns:
            Analytics dictionary with various metrics
        """
        try:
            # Count active vs cold storage
            active_count = self.collection.count_documents(
                {
                    "user_id": str(user_id),
                    "is_active": True,
                }
            )
            cold_count = self.collection.count_documents(
                {
                    "user_id": str(user_id),
                    "is_active": False,
                }
            )

            # Get active memories for strength analysis
            active_cursor = self.collection.find(
                {
                    "user_id": str(user_id),
                    "is_active": True,
                }
            )

            strengths = []
            stabilities = []
            emotions = []
            categories = {}

            for doc in active_cursor:
                strength = CognitiveMath.get_current_strength(doc)
                strengths.append(strength)
                stabilities.append(doc.get("stability", CognitiveMath.DEFAULT_STABILITY_HOURS))
                emotions.append(doc.get("emotion", 0.3))

                cat = doc.get("category")
                # If category is missing, detect from text
                if not cat:
                    cat = (
                        self._detect_category_from_text(doc.get("text", ""))
                        if doc.get("text")
                        else "biographical"
                    )
                categories[cat] = categories.get(cat, 0) + 1

            # Calculate statistics
            avg_strength = sum(strengths) / len(strengths) if strengths else 0
            avg_stability = sum(stabilities) / len(stabilities) if stabilities else 0
            avg_emotion = sum(emotions) / len(emotions) if emotions else 0
            weak_memories = sum(1 for s in strengths if s < 0.3)
            strong_memories = sum(1 for s in strengths if s > 0.7)

            return {
                "user_id": str(user_id),
                "active_memories": active_count,
                "cold_storage_memories": cold_count,
                "total_memories": active_count + cold_count,
                "capacity_used": active_count / self.max_depth if self.max_depth else 0,
                "average_strength": round(avg_strength, 4),
                "average_stability": round(avg_stability, 2),
                "average_emotion": round(avg_emotion, 4),
                "weak_memories": weak_memories,
                "strong_memories": strong_memories,
                "categories": categories,
            }

        except (PyMongoError, OperationFailure) as e:
            logger.exception(f"Failed to get analytics for user {user_id}: {e}")
            return {
                "user_id": str(user_id),
                "error": str(e),
            }

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

        The check uses vector similarity to find related memories, then uses
        LLM reasoning to detect logical contradictions.

        Args:
            user_id: User ID to check conflicts for
            new_fact: The new fact/information to check
            similarity_threshold: Minimum similarity to consider related (default: 0.85)
            llm_model: Override LLM model for conflict detection

        Returns:
            Conflict description if found, None if no conflict
        """
        if not self.llm_available:
            logger.warning("⚠️ [Conflict Check] LLM not available, skipping conflict detection")
            return None

        try:
            # 1. Generate embedding for new fact
            fact_embedding = self._get_embedding(new_fact)
            if not fact_embedding:
                return None

            # 2. Find similar existing memories
            similar_memories = self._find_similar_memories(
                user_id=user_id,
                embedding=fact_embedding,
                top_n=3,
            )

            if not similar_memories:
                return None

            # 3. Filter by similarity threshold
            relevant_memories = [
                m for m in similar_memories if m.get("similarity", 0) >= similarity_threshold
            ]

            if not relevant_memories:
                return None

            # 4. Build context for LLM conflict check
            existing_context = "\n".join([f"- {m['memory']}" for m in relevant_memories])

            conflict_prompt = (
                "You are a logical consistency engine. Your job is to detect contradictions.\n\n"
                f"EXISTING KNOWLEDGE:\n{existing_context}\n\n"
                f"NEW INFORMATION:\n{new_fact}\n\n"
                "Does the 'NEW INFORMATION' logically contradict any of the "
                "'EXISTING KNOWLEDGE'?\n\n"
                "Rules:\n"
                "1. A contradiction means two statements cannot both be true at the same time.\n"
                "2. Updates to information are NOT contradictions (e.g., "
                '"User moved to NYC" doesn\'t contradict "User lives in LA" - it\'s an update).\n'
                "3. Different preferences at different times are NOT contradictions.\n"
                "4. Only flag clear logical contradictions.\n\n"
                "If you find a CONTRADICTION, explain it briefly in 1-2 sentences.\n"
                "If there is NO CONTRADICTION, respond with exactly: CLEAN"
            )

            # 5. Call LLM for consistency check
            model = llm_model or self.memory_llm_model

            # Use adjusted temperature (Gemini 3 requires 1.0, but we prefer lower for consistency)
            # For consistency checks, we still need to respect Gemini 3's requirement
            consistency_temp = 0  # Prefer deterministic for consistency checks
            if model and "gemini-3" in model.lower():
                consistency_temp = 1.0  # Gemini 3 requires 1.0 minimum
                logger.debug(
                    f"⚠️  Using temperature=1.0 for Gemini 3 consistency check "
                    f"(model: {model}). Gemini 3 requires temperature=1.0."
                )

            response = completion(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a logical consistency engine."},
                    {"role": "user", "content": conflict_prompt},
                ],
                temperature=consistency_temp,
            )

            result = response.choices[0].message.content.strip()

            # 6. Parse response
            if "CLEAN" in result.upper():
                logger.debug(f"✅ [Conflict Check] No conflict detected for: '{new_fact[:50]}...'")
                return None

            logger.warning(
                f"⚠️ [Conflict Check] Conflict detected for: '{new_fact[:50]}...'\n"
                f"   Conflict: {result}"
            )
            return result

        except (
            APIError,
            AuthenticationError,
            NotFoundError,
            RateLimitError,
            json.JSONDecodeError,
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
            RuntimeError,
            ConnectionError,
            OSError,
        ) as e:
            logger.error(f"❌ [Conflict Check] Failed: {e}", exc_info=True)
            return None

    def detect_knowledge_conflict_sync(
        self,
        user_id: str,
        new_fact: str,
        similarity_threshold: float = 0.85,
        llm_model: str | None = None,
    ) -> str | None:
        """
        Synchronous wrapper for detect_knowledge_conflict.

        Use this when you need to call conflict detection from synchronous code.
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Use thread pool for running async in sync context
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        lambda: asyncio.run(
                            self.detect_knowledge_conflict(
                                user_id=user_id,
                                new_fact=new_fact,
                                similarity_threshold=similarity_threshold,
                                llm_model=llm_model,
                            )
                        )
                    )
                    return future.result(timeout=30)
            else:
                return loop.run_until_complete(
                    self.detect_knowledge_conflict(
                        user_id=user_id,
                        new_fact=new_fact,
                        similarity_threshold=similarity_threshold,
                        llm_model=llm_model,
                    )
                )
        except RuntimeError:
            return asyncio.run(
                self.detect_knowledge_conflict(
                    user_id=user_id,
                    new_fact=new_fact,
                    similarity_threshold=similarity_threshold,
                    llm_model=llm_model,
                )
            )

    # --- Core Operations ---

    def inject(
        self,
        memory: str | dict[str, Any],
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        bucket_id: str | None = None,
        bucket_type: str | None = None,
        raw_content: str | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """
        Direct Injection: Bypasses LLM extraction. Useful for system instructions or manual entries.
        """
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

        # 2. Apply Redaction (privacy protection)
        if self.redaction_enabled:
            original_length = len(memory_content)
            memory_content = self.redaction_service.redact(memory_content)
            if len(memory_content) != original_length:
                logger.info("🔒 [Memory Inject] Sensitive data redacted before storage")

        # 3. Metadata Setup
        final_metadata = dict(metadata) if metadata else {}
        if bucket_id:
            final_metadata["bucket_id"] = bucket_id
        if bucket_type:
            final_metadata["bucket_type"] = bucket_type
        if raw_content:
            final_metadata["raw_content"] = raw_content

        # 4. Store
        vector = self._get_embedding(memory_content)

        # Extract optional cognitive parameters from kwargs
        importance = kwargs.get("importance", 0.5)
        emotion = kwargs.get("emotion", 0.3)
        stability = kwargs.get("stability")
        memory_type = kwargs.get("memory_type")

        # Detect memory type if not provided and auto-detection is enabled
        if not memory_type and self.memory_types_enabled and self.auto_detect_memory_type:
            memory_type = self._detect_memory_type(memory_content)
        elif not memory_type:
            memory_type = self.default_memory_type

        doc = {
            "text": memory_content,
            "embedding": vector,
            "user_id": str(user_id) if user_id else None,
            "metadata": final_metadata,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }

        # Add memory type (Cognitive Blueprint v2.0)
        if self.memory_types_enabled:
            doc["memory_type"] = memory_type

            # Add TTL for episodic memories
            if memory_type == "episodic":
                expires_at = datetime.now(timezone.utc) + timedelta(
                    days=self.episodic_retention_days
                )
                doc["expires_at"] = expires_at

        # Add cognitive fields if enabled
        if self.enable_cognitive:
            doc["importance"] = importance
            doc["access_count"] = 0
            doc["last_accessed"] = datetime.now(timezone.utc)
            doc["mention_count"] = 1
            doc["last_mentioned"] = datetime.now(timezone.utc)
            # New cognitive decay fields
            doc["emotion"] = emotion
            # Calculate initial stability based on emotion (Flashbulb Memory effect)
            doc["stability"] = (
                stability
                if stability is not None
                else CognitiveMath.calculate_initial_stability(
                    emotion,
                    default_hours=self.config.get("default_stability_hours", 24.0),
                    max_multiplier=self.config.get("max_stability_multiplier", 100.0),
                )
            )
            # Soft-delete flag (active by default)
            doc["is_active"] = True

        result = self.collection.insert_one(doc)
        logger.info(f"💉 Injected memory {result.inserted_id}")

        result_dict = {
            "id": str(result.inserted_id),
            "memory": memory_content,
            "metadata": final_metadata,
            "user_id": str(user_id) if user_id else None,
            "importance": doc.get("importance"),
            "emotion": doc.get("emotion"),
            "stability": doc.get("stability"),
            "created_at": doc["created_at"].isoformat(),
        }

        # Add memory_type to result (Cognitive Blueprint v2.0)
        if self.memory_types_enabled and "memory_type" in doc:
            result_dict["memory_type"] = doc["memory_type"]

        return result_dict

    def add(
        self,
        messages: str | list[dict[str, str]],
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        bucket_id: str | None = None,
        bucket_type: str | None = None,
        raw_content: str | None = None,
        **kwargs,
    ) -> list[dict[str, Any]]:
        """
        Intelligent Add with optional cognitive memory management.

        Uses parallel processing internally for optimal performance:
        - Batch embedding (single API call for all facts)
        - Parallel vector searches
        - Parallel importance assessments
        - Bulk database operations

        This is ~5x faster than sequential processing for multiple facts.

        If cognitive features are enabled, includes:
        - Importance assessment
        - Memory reinforcement
        - Memory merging
        - Importance decay
        - Memory pruning

        If disabled, works as basic memory service.
        """
        # Delegate to async implementation for parallel processing
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop is running, use thread pool to avoid blocking
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        lambda: asyncio.run(
                            self.add_async(
                                messages=messages,
                                user_id=user_id,
                                metadata=metadata,
                                bucket_id=bucket_id,
                                bucket_type=bucket_type,
                                raw_content=raw_content,
                                **kwargs,
                            )
                        )
                    )
                    return future.result(timeout=120)
            else:
                return loop.run_until_complete(
                    self.add_async(
                        messages=messages,
                        user_id=user_id,
                        metadata=metadata,
                        bucket_id=bucket_id,
                        bucket_type=bucket_type,
                        raw_content=raw_content,
                        **kwargs,
                    )
                )
        except RuntimeError:
            # No event loop, create new one
            return asyncio.run(
                self.add_async(
                    messages=messages,
                    user_id=user_id,
                    metadata=metadata,
                    bucket_id=bucket_id,
                    bucket_type=bucket_type,
                    raw_content=raw_content,
                    **kwargs,
                )
            )

    def _add_sequential(
        self,
        messages: str | list[dict[str, str]],
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        bucket_id: str | None = None,
        bucket_type: str | None = None,
        raw_content: str | None = None,
        **kwargs,
    ) -> list[dict[str, Any]]:
        """
        Legacy sequential add method (kept as fallback).

        Use add() instead for automatic parallel processing.
        """
        # 1. Normalize Input
        if isinstance(messages, list):
            input_text = "\n".join([m.get("content", "") for m in messages if m.get("content")])
        else:
            input_text = str(messages)

        if not input_text.strip():
            return []

        # 2. Apply Redaction (privacy protection)
        if self.redaction_enabled:
            original_length = len(input_text)
            input_text = self.redaction_service.redact(input_text)
            if len(input_text) != original_length:
                logger.info("🔒 [Memory Add] Sensitive data redacted before processing")

        # 3. Extract Facts (or bypass if infer=False)
        infer = kwargs.get("infer", self.infer)
        logger.info(
            f"🧠 [Memory Add] Processing memory add "
            f"(infer={infer}, cognitive={self.enable_cognitive}, "
            f"categories={self.categories_enabled}, user={user_id})"
        )
        logger.info(
            f"🧠 [Memory Add] Collection: {self.collection_name}, "
            f"DB: {self.db_name}, App: {self.app_slug}"
        )
        logger.info(f"🧠 [Memory Add] Input text: '{input_text[:100]}...'")

        # Determine extraction method based on configuration
        use_cognitive_extraction = self.enable_cognitive and self.config.get(
            "use_emotion_extraction", True
        )

        # Extract facts with appropriate method
        extracted_facts = []  # List of dicts with text, category, emotion

        if infer and use_cognitive_extraction:
            # Full cognitive extraction with emotion tagging
            extracted_facts = self._extract_facts_cognitive(input_text)
        elif infer and self.categories_enabled:
            # Categorized extraction (no emotion)
            categorized_facts = self._extract_facts_with_categories(input_text)
            extracted_facts = [
                {"text": f["text"], "category": f["category"], "emotion": 0.3}
                for f in categorized_facts
            ]
        elif infer:
            # Basic extraction (no categories or emotion)
            facts = self._extract_facts(input_text)
            extracted_facts = [
                {"text": f, "category": "biographical", "emotion": 0.3} for f in facts
            ]
        else:
            # No inference - store raw text
            extracted_facts = [{"text": input_text, "category": "biographical", "emotion": 0.3}]

        if not extracted_facts:
            logger.info("🧠 [Memory Add] No facts extracted.")
            return []

        # Fuse/deduplicate facts from the same message before processing
        # This prevents storing semantically duplicate facts like:
        # "User loves chocolate" AND "User's favorite candy is chocolate"
        original_count = len(extracted_facts)
        if len(extracted_facts) > 1:
            # Use Memory Fusion Service if available (LLM-powered, parallel)
            if self.fusion_service is not None:
                try:
                    extracted_facts = self.fusion_service.fuse_all_sync(extracted_facts)
                    logger.info(
                        f"🧠 [Memory Add] Fused {original_count} facts → "
                        f"{len(extracted_facts)} (via Fusion Service)"
                    )
                except (RuntimeError, ValueError, TypeError) as e:
                    logger.warning(f"⚠️ [Memory Add] Fusion failed: {e}. Using simple dedup.")
                    extracted_facts = self._deduplicate_extracted_facts(extracted_facts)
            else:
                # Fallback to simple deduplication
                extracted_facts = self._deduplicate_extracted_facts(extracted_facts)

        logger.info(
            f"🧠 [Memory Add] Extracted {original_count} facts, "
            f"{len(extracted_facts)} after fusion/deduplication"
        )

        # 4. Prepare Metadata
        final_metadata = dict(metadata) if metadata else {}
        if bucket_id:
            final_metadata["bucket_id"] = bucket_id
        if bucket_type:
            final_metadata["bucket_type"] = bucket_type
        if raw_content:
            final_metadata["raw_content"] = raw_content

        stored_memories = []

        # 5. Process Each Fact (with or without cognitive features)
        for fact_data in extracted_facts:
            # Extract fact components
            fact = fact_data.get("text", "")
            category = fact_data.get("category")
            # If category is missing, detect from text
            if not category:
                category = self._detect_category_from_text(fact) if fact else "biographical"
            fact_emotion = fact_data.get("emotion", 0.3)

            # Store emotion for use in memory creation
            self._current_fact_emotion = fact_emotion

            try:
                # Generate embedding
                vector = self._get_embedding(fact)

                # Add category to this fact's metadata
                fact_metadata = dict(final_metadata)
                if self.categories_enabled and category:
                    fact_metadata["category"] = category

                if not vector:
                    continue

                # Use cognitive features if enabled
                if self.enable_cognitive:
                    # First check for exact/near-duplicates (similarity > duplicate_threshold)
                    # This prevents storing semantically identical memories with different wording
                    duplicate_result = self._check_for_duplicate(
                        text=fact,
                        user_id=user_id,
                        embedding=vector,
                    )
                    if duplicate_result:
                        # Duplicate found and boosted - skip creating new memory
                        stored_memories.append(duplicate_result)
                        continue

                    # Find similar memories
                    similar_memories = self._find_similar_memories(
                        user_id=user_id,
                        embedding=vector,
                        top_n=5,
                    )

                    # Check for reinforcement (very similar memories)
                    # If similarity > duplicate_threshold, it was already caught above
                    # So here we check for similarity between merge_threshold_high
                    # and duplicate_threshold
                    reinforced = False
                    for similar in similar_memories:
                        if (
                            similar["similarity"] > self.merge_threshold_high
                            and similar["similarity"] < self.duplicate_threshold
                        ):
                            # Reinforce existing memory with new text for potential merging
                            result = self._reinforce_memory(
                                similar["id"],
                                similar["similarity"],
                                new_text=fact,
                                new_embedding=vector,
                            )

                            # Determine the final memory text (merged or original)
                            if isinstance(result, dict) and result.get("merged"):
                                final_memory = result.get("memory", similar["memory"])
                                final_importance = result.get("importance", similar["importance"])
                                action = "reinforced+merged"
                            else:
                                final_memory = similar["memory"]
                                final_importance = similar["importance"]
                                action = "reinforced"
                                # Track access for non-merged reinforcement
                                self.collection.update_one(
                                    {"_id": similar["id"]},
                                    {
                                        "$inc": {"access_count": 1},
                                        "$set": {"last_accessed": datetime.now(timezone.utc)},
                                    },
                                )

                            stored_memories.append(
                                {
                                    "id": similar["id"],
                                    "memory": final_memory,
                                    "metadata": similar.get("metadata", {}),
                                    "user_id": str(user_id) if user_id else None,
                                    "importance": final_importance,
                                    "action": action,
                                }
                            )
                            reinforced = True
                            break

                    if reinforced:
                        continue

                    # Check for merging (moderately similar memories)
                    # Skip if similarity is too high (already handled as duplicate or reinforcement)
                    merged = False
                    for similar in similar_memories:
                        if (
                            self.merge_threshold_low
                            < similar["similarity"]
                            <= self.merge_threshold_high
                        ):
                            # Assess importance for new fact
                            importance = self._assess_importance(fact)

                            # Get emotion from extracted data (if available) or default
                            fact_emotion = getattr(self, "_current_fact_emotion", 0.3)

                            # Create temporary memory document
                            temp_doc = {
                                "text": fact,
                                "embedding": vector,
                                "user_id": str(user_id) if user_id else None,
                                "metadata": fact_metadata,
                                "importance": importance,
                                "access_count": 0,
                                "created_at": datetime.now(timezone.utc),
                                "updated_at": datetime.now(timezone.utc),
                                "last_accessed": datetime.now(timezone.utc),
                                "mention_count": 1,
                                "last_mentioned": datetime.now(timezone.utc),
                                # New cognitive decay fields
                                "emotion": fact_emotion,
                                "stability": CognitiveMath.calculate_initial_stability(
                                    fact_emotion,
                                    default_hours=self.config.get("default_stability_hours", 24.0),
                                    max_multiplier=self.config.get(
                                        "max_stability_multiplier", 100.0
                                    ),
                                ),
                                # Soft-delete flag (active by default)
                                "is_active": True,
                            }

                            result = self.collection.insert_one(temp_doc)
                            new_memory_id = result.inserted_id

                            # Merge with existing memory
                            if self._merge_memories(
                                new_memory_id=new_memory_id,
                                existing_memory_id=similar["id"],
                                new_text=fact,
                                existing_text=similar["memory"],
                                new_embedding=vector,
                                existing_embedding=similar.get("embedding", vector),
                                new_importance=importance,
                                existing_importance=similar["importance"],
                            ):
                                # Get merged memory
                                merged_doc = self.collection.find_one({"_id": new_memory_id})
                                if merged_doc:
                                    stored_memories.append(
                                        {
                                            "id": str(merged_doc["_id"]),
                                            "memory": merged_doc.get("text", ""),
                                            "metadata": merged_doc.get("metadata", {}),
                                            "user_id": str(user_id) if user_id else None,
                                            "importance": merged_doc.get("importance", importance),
                                            "action": "merged",
                                        }
                                    )
                                    merged = True
                                    break

                    if merged:
                        continue

                    # Create new memory (no similar memories found)
                    importance = self._assess_importance(fact)

                    # Get emotion from extracted data (if available) or default
                    # fact_emotion will be set during cognitive extraction later
                    fact_emotion = getattr(self, "_current_fact_emotion", 0.3)

                    doc = {
                        "text": fact,
                        "embedding": vector,
                        "user_id": str(user_id) if user_id else None,
                        "metadata": fact_metadata,
                        "importance": importance,
                        "access_count": 0,
                        "created_at": datetime.now(timezone.utc),
                        "updated_at": datetime.now(timezone.utc),
                        "last_accessed": datetime.now(timezone.utc),
                        "mention_count": 1,
                        "last_mentioned": datetime.now(timezone.utc),
                        # New cognitive decay fields
                        "emotion": fact_emotion,
                        "stability": CognitiveMath.calculate_initial_stability(
                            fact_emotion,
                            default_hours=self.config.get("default_stability_hours", 24.0),
                            max_multiplier=self.config.get("max_stability_multiplier", 100.0),
                        ),
                        # Soft-delete flag (active by default)
                        "is_active": True,
                    }

                    # Add category as top-level field for easier querying
                    if self.categories_enabled and category:
                        doc["category"] = category

                    result = self.collection.insert_one(doc)
                    logger.info(
                        f"✅ [Memory Add] Stored memory in {self.collection_name}: "
                        f"id={result.inserted_id}, user_id={str(user_id)}, "
                        f"category={category}, text='{fact[:50]}...'"
                    )

                    stored_memories.append(
                        {
                            "id": str(result.inserted_id),
                            "memory": fact,
                            "metadata": fact_metadata,
                            "user_id": str(user_id) if user_id else None,
                            "importance": importance,
                            "emotion": doc.get("emotion"),
                            "stability": doc.get("stability"),
                            "category": category,
                            "action": "created",
                            "created_at": doc["created_at"].isoformat(),
                        }
                    )

                    # Update importance of other memories
                    self._update_importance_decay(user_id, vector)

                    # Prune if needed
                    self._prune_memories(user_id)
                else:
                    # Basic mode: just store the fact (still include is_active for consistency)
                    doc = {
                        "text": fact,
                        "embedding": vector,
                        "user_id": str(user_id) if user_id else None,
                        "metadata": fact_metadata,
                        "created_at": datetime.now(timezone.utc),
                        "updated_at": datetime.now(timezone.utc),
                        "is_active": True,  # Include soft-delete flag even in basic mode
                    }

                    # Add category as top-level field for easier querying
                    if self.categories_enabled and category:
                        doc["category"] = category

                    result = self.collection.insert_one(doc)
                    logger.info(
                        f"✅ [Memory Add] Stored memory (basic mode) in "
                        f"{self.collection_name}: id={result.inserted_id}, "
                        f"user_id={str(user_id)}, category={category}, text='{fact[:50]}...'"
                    )

                    stored_memories.append(
                        {
                            "id": str(result.inserted_id),
                            "memory": fact,
                            "metadata": fact_metadata,
                            "user_id": str(user_id) if user_id else None,
                            "category": category,
                            "created_at": doc["created_at"].isoformat(),
                        }
                    )

            except (
                PyMongoError,
                OperationFailure,
                CognitiveMemoryServiceError,
                ValueError,
                TypeError,
                RuntimeError,
            ) as e:
                logger.exception(f"Failed to process fact '{fact[:30]}...': {e}")
                continue

        # Graph extraction for GraphRAG (extract entities and relationships)
        if self._graph_service and stored_memories:
            graph_auto_extract = self.config.get("graph", {}).get("auto_extract", True)
            if graph_auto_extract:
                try:
                    graph_result = self._graph_service.extract_graph_from_memory(
                        memory_text=input_text,
                        user_id=str(user_id) if user_id else "anonymous",
                    )
                    if (
                        graph_result.get("nodes_created", 0) > 0
                        or graph_result.get("edges_created", 0) > 0
                    ):
                        nodes_created = graph_result.get("nodes_created", 0)
                        edges_created = graph_result.get("edges_created", 0)
                        logger.info(
                            f"🔗 [GraphRAG] Extracted {nodes_created} nodes, "
                            f"{edges_created} edges"
                        )
                except (RuntimeError, ValueError, TypeError) as e:
                    logger.warning(f"⚠️ Graph extraction failed: {e}")

        logger.info(
            f"✅ Stored {len(stored_memories)} memories (cognitive={self.enable_cognitive}, "
            f"graph={self._graph_service is not None})."
        )
        return stored_memories

    async def add_async(
        self,
        messages: str | list[dict[str, str]],
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        bucket_id: str | None = None,
        bucket_type: str | None = None,
        raw_content: str | None = None,
        **kwargs,
    ) -> list[dict[str, Any]]:
        """
        Async version of add() with parallel processing for optimal performance.

        Pipeline:
        1. Extract facts (sequential - single LLM call)
        2. Batch embed ALL facts (single API call)
        3. Parallel vector searches (concurrent with semaphore)
        4. Classify: reinforce, merge, or create new
        5. Parallel importance assessments (concurrent with semaphore)
        6. Bulk DB operations

        This is ~5x faster than the sequential add() for multiple facts.
        """
        # 1. Normalize Input
        if isinstance(messages, list):
            input_text = "\n".join([m.get("content", "") for m in messages if m.get("content")])
        else:
            input_text = str(messages)

        if not input_text.strip():
            return []

        # 2. Apply Redaction (privacy protection)
        if self.redaction_enabled:
            original_length = len(input_text)
            input_text = self.redaction_service.redact(input_text)
            if len(input_text) != original_length:
                logger.info("🔒 [Memory Add Async] Sensitive data redacted")

        # 3. Extract Facts
        infer = kwargs.get("infer", self.infer)
        logger.info(
            f"⚡ [Memory Add Async] Processing (infer={infer}, cognitive={self.enable_cognitive})"
        )

        use_cognitive_extraction = self.enable_cognitive and self.config.get(
            "use_emotion_extraction", True
        )

        extracted_facts = []

        if infer and use_cognitive_extraction:
            extracted_facts = self._extract_facts_cognitive(input_text)
        elif infer and self.categories_enabled:
            categorized_facts = self._extract_facts_with_categories(input_text)
            extracted_facts = [
                {"text": f["text"], "category": f["category"], "emotion": 0.3}
                for f in categorized_facts
            ]
        elif infer:
            facts = self._extract_facts(input_text)
            extracted_facts = [{"text": f, "category": "general", "emotion": 0.3} for f in facts]
        else:
            extracted_facts = [{"text": input_text, "category": "general", "emotion": 0.3}]

        if not extracted_facts:
            logger.info("⚡ [Memory Add Async] No facts extracted.")
            return []

        # Fuse/deduplicate facts
        original_count = len(extracted_facts)
        if len(extracted_facts) > 1 and self.fusion_service is not None:
            try:
                extracted_facts = self.fusion_service.fuse_all_sync(extracted_facts)
                logger.info(f"⚡ Fused {original_count} → {len(extracted_facts)} facts")
            except (RuntimeError, ValueError, TypeError) as e:
                logger.warning(f"⚠️ Fusion failed: {e}")
                extracted_facts = self._deduplicate_extracted_facts(extracted_facts)
        elif len(extracted_facts) > 1:
            extracted_facts = self._deduplicate_extracted_facts(extracted_facts)

        # 4. Prepare Metadata
        final_metadata = dict(metadata) if metadata else {}
        if bucket_id:
            final_metadata["bucket_id"] = bucket_id
        if bucket_type:
            final_metadata["bucket_type"] = bucket_type
        if raw_content:
            final_metadata["raw_content"] = raw_content

        # ============================================================
        # PARALLEL PIPELINE STARTS HERE
        # ============================================================

        # Step 1: Batch embed ALL facts in a single API call
        fact_texts = [f.get("text", "") for f in extracted_facts]
        logger.info(f"⚡ [Batch Embed] Embedding {len(fact_texts)} facts...")

        embeddings_map = await self._get_embeddings_batch(fact_texts)

        # Filter out facts that failed to embed
        valid_facts = []
        valid_embeddings = []
        for fact_data in extracted_facts:
            text = fact_data.get("text", "").replace("\n", " ").strip()
            if text in embeddings_map:
                valid_facts.append(fact_data)
                valid_embeddings.append(embeddings_map[text])

        if not valid_facts:
            logger.warning("⚠️ [Memory Add Async] No valid embeddings generated")
            return []

        logger.info(f"⚡ [Batch Embed] Got {len(valid_embeddings)} embeddings")

        # Step 2: Parallel vector searches (only for cognitive mode)
        all_similar_memories = []
        if self.enable_cognitive:
            logger.info(f"⚡ [Parallel Search] Running {len(valid_embeddings)} searches...")
            all_similar_memories = await self._find_similar_memories_parallel(
                user_id=user_id,
                embeddings=valid_embeddings,
                top_n=5,
            )
        else:
            all_similar_memories = [[] for _ in valid_facts]

        # Step 3: Classify facts into actions
        # - duplicate: exact/near-duplicate exists (similarity > duplicate_threshold)
        # - reinforce: very similar memory exists
        #   (between merge_threshold_high and duplicate_threshold)
        # - merge: moderately similar memory exists
        #   (between merge_threshold_low and merge_threshold_high)
        # - create: no similar memory found
        facts_to_duplicate = []  # (fact_idx, similar_memory) - will be boosted, not created
        facts_to_reinforce = []  # (fact_idx, similar_memory)
        facts_to_merge = []  # (fact_idx, similar_memory)
        facts_to_create = []  # fact_idx

        for idx, (_fact_data, similar_memories) in enumerate(
            zip(valid_facts, all_similar_memories, strict=False)
        ):
            if not self.enable_cognitive:
                facts_to_create.append(idx)
                continue

            action_taken = False

            # First check for duplicates (similarity > duplicate_threshold)
            # These are semantically identical and should be boosted, not created
            for similar in similar_memories:
                if similar["similarity"] >= self.duplicate_threshold:
                    facts_to_duplicate.append((idx, similar))
                    action_taken = True
                    break

            if action_taken:
                continue

            # Check for reinforcement (very similar, but not duplicate)
            # Similarity between merge_threshold_high and duplicate_threshold
            for similar in similar_memories:
                if (
                    similar["similarity"] > self.merge_threshold_high
                    and similar["similarity"] < self.duplicate_threshold
                ):
                    facts_to_reinforce.append((idx, similar))
                    action_taken = True
                    break

            if action_taken:
                continue

            # Check for merging (moderately similar)
            # Similarity between merge_threshold_low and merge_threshold_high
            for similar in similar_memories:
                if self.merge_threshold_low < similar["similarity"] <= self.merge_threshold_high:
                    facts_to_merge.append((idx, similar))
                    action_taken = True
                    break

            if not action_taken:
                facts_to_create.append(idx)

        logger.info(
            f"⚡ [Classification] duplicate={len(facts_to_duplicate)}, "
            f"reinforce={len(facts_to_reinforce)}, "
            f"merge={len(facts_to_merge)}, create={len(facts_to_create)}"
        )

        # Step 4: Parallel importance assessment for facts that need it
        # (merge and create actions need importance scores)
        facts_needing_importance = [valid_facts[idx]["text"] for idx in facts_to_create] + [
            valid_facts[idx]["text"] for idx, _ in facts_to_merge
        ]

        importance_map: dict[str, float] = {}
        if facts_needing_importance and self.enable_cognitive:
            logger.info(
                f"⚡ [Parallel Importance] Assessing {len(facts_needing_importance)} facts..."
            )
            importance_map = await self._assess_importance_parallel(facts_needing_importance)

        # Step 5: Execute DB operations
        stored_memories = self._execute_async_actions(
            user_id=user_id,
            input_text=input_text,
            valid_facts=valid_facts,
            valid_embeddings=valid_embeddings,
            importance_map=importance_map,
            final_metadata=final_metadata,
            facts_to_duplicate=facts_to_duplicate,
            facts_to_reinforce=facts_to_reinforce,
            facts_to_merge=facts_to_merge,
            facts_to_create=facts_to_create,
            messages=messages,  # Pass messages for perception analysis
        )

        # Graph extraction for GraphRAG (extract entities and relationships)
        if self._graph_service and stored_memories:
            graph_auto_extract = self.config.get("graph", {}).get("auto_extract", True)
            if graph_auto_extract:
                try:
                    # Extract graph from the original input text using async method
                    graph_result = await self._graph_service.extract_graph_from_text(
                        text=input_text,
                        user_id=str(user_id) if user_id else "anonymous",
                    )
                    if (
                        graph_result.get("nodes_created", 0) > 0
                        or graph_result.get("edges_created", 0) > 0
                    ):
                        nodes_created = graph_result.get("nodes_created", 0)
                        edges_created = graph_result.get("edges_created", 0)
                        logger.info(
                            f"🔗 [GraphRAG] Extracted {nodes_created} nodes, "
                            f"{edges_created} edges from memory"
                        )
                except (RuntimeError, ValueError, TypeError) as e:
                    logger.warning(f"⚠️ Graph extraction failed: {e}")

        logger.info(
            f"⚡ [Memory Add Async] Complete: {len(stored_memories)} memories "
            f"(cognitive={self.enable_cognitive}, graph={self._graph_service is not None})"
        )
        return stored_memories

    def _process_duplicate_facts(
        self,
        user_id: str | None,
        valid_facts: list[dict[str, Any]],
        valid_embeddings: list[list[float]],
        facts_to_duplicate: list[tuple[int, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """Process duplicate facts by boosting existing memories."""
        stored_memories = []
        for idx, _similar in facts_to_duplicate:
            try:
                fact_data = valid_facts[idx]
                fact_text = fact_data.get("text", "")
                fact_embedding = valid_embeddings[idx]

                duplicate_result = self._check_for_duplicate(
                    text=fact_text,
                    user_id=user_id,
                    embedding=fact_embedding,
                )
                if duplicate_result:
                    stored_memories.append(duplicate_result)
            except (PyMongoError, OperationFailure) as e:
                logger.warning(f"⚠️ Duplicate handling failed: {e}")
        return stored_memories

    def _process_reinforcement_facts(
        self,
        user_id: str | None,
        valid_facts: list[dict[str, Any]],
        valid_embeddings: list[list[float]],
        facts_to_reinforce: list[tuple[int, dict[str, Any]]],
        now: datetime,
    ) -> list[dict[str, Any]]:
        """Process reinforcement facts by updating existing memories."""
        stored_memories = []
        for idx, similar in facts_to_reinforce:
            try:
                fact_data = valid_facts[idx]
                fact_text = fact_data.get("text", "")
                fact_embedding = valid_embeddings[idx]

                result = self._reinforce_memory(
                    similar["id"],
                    similar["similarity"],
                    new_text=fact_text,
                    new_embedding=fact_embedding,
                )

                if isinstance(result, dict) and result.get("merged"):
                    final_memory = result.get("memory", similar["memory"])
                    final_importance = result.get("importance", similar["importance"])
                    action = "reinforced+merged"
                else:
                    final_memory = similar["memory"]
                    final_importance = similar["importance"]
                    action = "reinforced"
                    self.collection.update_one(
                        {"_id": similar["id"]},
                        {
                            "$inc": {"access_count": 1},
                            "$set": {"last_accessed": now},
                        },
                    )

                stored_memories.append(
                    {
                        "id": similar["id"],
                        "memory": final_memory,
                        "metadata": similar.get("metadata", {}),
                        "user_id": str(user_id) if user_id else None,
                        "importance": final_importance,
                        "action": action,
                    }
                )
            except (PyMongoError, OperationFailure) as e:
                logger.warning(f"⚠️ Reinforcement failed: {e}")
        return stored_memories

    def _process_merge_facts(
        self,
        user_id: str | None,
        valid_facts: list[dict[str, Any]],
        valid_embeddings: list[list[float]],
        importance_map: dict[str, float],
        final_metadata: dict[str, Any],
        facts_to_merge: list[tuple[int, dict[str, Any]]],
        now: datetime,
    ) -> list[dict[str, Any]]:
        """Process merge facts by creating new memories and merging."""
        stored_memories = []
        for idx, similar in facts_to_merge:
            fact_data = valid_facts[idx]
            fact = fact_data.get("text", "")
            category = fact_data.get("category")
            if not category:
                category = (
                    self._detect_category_from_text(fact_data.get("text", ""))
                    if fact_data.get("text")
                    else "biographical"
                )
            fact_emotion = fact_data.get("emotion", 0.3)
            vector = valid_embeddings[idx]

            fact_metadata = dict(final_metadata)
            if self.categories_enabled and category:
                fact_metadata["category"] = category

            importance = importance_map.get(fact, 0.5)
            memory_type = self.default_memory_type
            if self.memory_types_enabled and self.auto_detect_memory_type:
                memory_type = self._detect_memory_type(fact)

            try:
                temp_doc = {
                    "text": fact,
                    "embedding": vector,
                    "user_id": str(user_id) if user_id else None,
                    "metadata": fact_metadata,
                    "importance": importance,
                    "access_count": 0,
                    "created_at": now,
                    "updated_at": now,
                    "last_accessed": now,
                    "mention_count": 1,
                    "last_mentioned": now,
                    "emotion": fact_emotion,
                    "stability": CognitiveMath.calculate_initial_stability(
                        fact_emotion,
                        default_hours=self.config.get("default_stability_hours", 24.0),
                        max_multiplier=self.config.get("max_stability_multiplier", 100.0),
                    ),
                    "is_active": True,
                }

                if self.memory_types_enabled:
                    temp_doc["memory_type"] = memory_type
                    if memory_type == "episodic":
                        temp_doc["expires_at"] = now + timedelta(days=self.episodic_retention_days)

                result = self.collection.insert_one(temp_doc)
                new_memory_id = result.inserted_id

                if self._merge_memories(
                    new_memory_id=new_memory_id,
                    existing_memory_id=similar["id"],
                    new_text=fact,
                    existing_text=similar["memory"],
                    new_embedding=vector,
                    existing_embedding=similar.get("embedding", vector),
                    new_importance=importance,
                    existing_importance=similar["importance"],
                ):
                    merged_doc = self.collection.find_one({"_id": new_memory_id})
                    if merged_doc:
                        stored_memories.append(
                            {
                                "id": str(merged_doc["_id"]),
                                "memory": merged_doc.get("text", ""),
                                "metadata": merged_doc.get("metadata", {}),
                                "user_id": str(user_id) if user_id else None,
                                "importance": merged_doc.get("importance", importance),
                                "action": "merged",
                            }
                        )
            except (PyMongoError, OperationFailure) as e:
                logger.warning(f"⚠️ Merge failed: {e}")
        return stored_memories

    def _process_new_facts(
        self,
        user_id: str | None,
        valid_facts: list[dict[str, Any]],
        valid_embeddings: list[list[float]],
        importance_map: dict[str, float],
        final_metadata: dict[str, Any],
        facts_to_create: list[int],
        now: datetime,
    ) -> list[dict[str, Any]]:
        """Process new facts by creating new memories."""
        docs_to_insert = []
        for idx in facts_to_create:
            fact_data = valid_facts[idx]
            fact = fact_data.get("text", "")
            category = fact_data.get("category")
            # If category is missing, detect from text
            if not category:
                category = (
                    self._detect_category_from_text(fact_data.get("text", ""))
                    if fact_data.get("text")
                    else "biographical"
                )
            fact_emotion = fact_data.get("emotion", 0.3)
            vector = valid_embeddings[idx]

            fact_metadata = dict(final_metadata)
            if self.categories_enabled and category:
                fact_metadata["category"] = category

            # Detect memory type for this fact
            memory_type = self.default_memory_type
            procedural_info = None

            if self.memory_types_enabled and self.auto_detect_memory_type:
                memory_type = self._detect_memory_type(fact)

                # If procedural, extract procedural information
                if memory_type == "procedural" and self.procedural_service:
                    procedural_info = self.procedural_service.detect_procedural_content(fact)

            if self.enable_cognitive:
                importance = importance_map.get(fact, 0.5)
                doc = {
                    "text": fact,
                    "embedding": vector,
                    "user_id": str(user_id) if user_id else None,
                    "metadata": fact_metadata,
                    "importance": importance,
                    "access_count": 0,
                    "created_at": now,
                    "updated_at": now,
                    "last_accessed": now,
                    "mention_count": 1,
                    "last_mentioned": now,
                    "emotion": fact_emotion,
                    "stability": CognitiveMath.calculate_initial_stability(
                        fact_emotion,
                        default_hours=self.config.get("default_stability_hours", 24.0),
                        max_multiplier=self.config.get("max_stability_multiplier", 100.0),
                    ),
                    "is_active": True,
                }

                # Add memory type (Cognitive Blueprint v2.0)
                if self.memory_types_enabled:
                    doc["memory_type"] = memory_type
                    # Add TTL for episodic memories
                    if memory_type == "episodic":
                        doc["expires_at"] = now + timedelta(days=self.episodic_retention_days)
                    # Add procedural metadata if detected
                    if memory_type == "procedural" and procedural_info:
                        doc["task_type"] = procedural_info.get("task_type", "General")
                        doc["steps"] = procedural_info.get("steps", [])
                        doc["associated_tools"] = procedural_info.get("associated_tools", [])
                        doc["success_count"] = 0

                if self.categories_enabled and category:
                    doc["category"] = category
            else:
                doc = {
                    "text": fact,
                    "embedding": vector,
                    "user_id": str(user_id) if user_id else None,
                    "metadata": fact_metadata,
                    "created_at": now,
                    "updated_at": now,
                    "is_active": True,
                }

                # Add memory type even if cognitive features disabled
                if self.memory_types_enabled:
                    doc["memory_type"] = memory_type
                    if memory_type == "episodic":
                        doc["expires_at"] = now + timedelta(days=self.episodic_retention_days)
                    if memory_type == "procedural" and procedural_info:
                        doc["task_type"] = procedural_info.get("task_type", "General")
                        doc["steps"] = procedural_info.get("steps", [])
                        doc["associated_tools"] = procedural_info.get("associated_tools", [])
                        doc["success_count"] = 0

                if self.categories_enabled and category:
                    doc["category"] = category

            docs_to_insert.append((idx, doc, fact, category, memory_type, procedural_info))

        stored_memories = []
        if docs_to_insert:
            try:
                docs = [d[1] for d in docs_to_insert]
                result = self.collection.insert_many(docs)

                for i, inserted_id in enumerate(result.inserted_ids):
                    idx, doc, fact, category, mem_type, proc_info = docs_to_insert[i]
                    memory_result = self._build_memory_result(
                        inserted_id, doc, fact, category, mem_type, proc_info, user_id
                    )
                    stored_memories.append(memory_result)

                logger.info(f"⚡ [Bulk Insert] Inserted {len(result.inserted_ids)} memories")
            except (PyMongoError, OperationFailure) as e:
                logger.warning(f"⚠️ Bulk insert failed, falling back to individual inserts: {e}")
                for _idx, doc, fact, category, mem_type, proc_info in docs_to_insert:
                    try:
                        result = self.collection.insert_one(doc)
                        memory_result = self._build_memory_result(
                            result.inserted_id, doc, fact, category, mem_type, proc_info, user_id
                        )
                        stored_memories.append(memory_result)
                    except (PyMongoError, OperationFailure) as e2:
                        logger.warning(f"⚠️ Individual insert failed: {e2}")
        return stored_memories

    def _build_memory_result(
        self,
        inserted_id: Any,
        doc: dict[str, Any],
        fact: str,
        category: str,
        mem_type: str,
        proc_info: dict[str, Any] | None,
        user_id: str | None,
    ) -> dict[str, Any]:
        """Build memory result dictionary for new memories."""
        memory_result = {
            "id": str(inserted_id),
            "memory": fact,
            "metadata": doc.get("metadata", {}),
            "user_id": str(user_id) if user_id else None,
            "category": category,
            "created_at": doc["created_at"].isoformat(),
            "action": "created",
        }

        if self.enable_cognitive:
            memory_result["importance"] = doc.get("importance")
            memory_result["emotion"] = doc.get("emotion")
            memory_result["stability"] = doc.get("stability")

        if self.memory_types_enabled and "memory_type" in doc:
            memory_result["memory_type"] = doc["memory_type"]
            if mem_type == "procedural" and proc_info:
                memory_result["task_type"] = proc_info.get("task_type")
                memory_result["steps"] = proc_info.get("steps", [])
                memory_result["associated_tools"] = proc_info.get("associated_tools", [])

        return memory_result

    def _process_perception_analysis(
        self,
        user_id: str | None,
        messages: str | list[dict[str, str]] | None,
        stored_memories: list[dict[str, Any]],
    ) -> None:
        """Process perception analysis if enabled."""
        if not (
            self.perception_engine and user_id and isinstance(messages, list) and len(messages) >= 2
        ):
            return
        try:
            user_messages = [
                m.get("content", "")
                for m in messages
                if isinstance(m, dict) and m.get("role") == "user"
            ]
            assistant_messages = [
                m.get("content", "")
                for m in messages
                if isinstance(m, dict) and m.get("role") == "assistant"
            ]

            if user_messages and assistant_messages:
                user_input = user_messages[-1]  # Most recent user message
                robot_response = assistant_messages[-1]  # Most recent assistant message

                # Get persona context
                persona_context = None
                if self.persona_engine:
                    persona = self.persona_engine.get_persona()
                    if persona:
                        persona_context = persona.get("role")

                # Analyze interaction (async)
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # If loop is running, use thread pool
                        with concurrent.futures.ThreadPoolExecutor() as executor:
                            future = executor.submit(
                                lambda: asyncio.run(
                                    self.perception_engine.analyze_interaction(
                                        user_input=user_input,
                                        robot_response=robot_response,
                                        user_id=user_id,
                                        persona_context=persona_context,
                                    )
                                )
                            )
                            perceptions = future.result(timeout=30)
                    else:
                        perceptions = asyncio.run(
                            self.perception_engine.analyze_interaction(
                                user_input=user_input,
                                robot_response=robot_response,
                                user_id=user_id,
                                persona_context=persona_context,
                            )
                        )

                    # Update perceptions if analysis succeeded
                    if perceptions:
                        user_view = perceptions.get("user_view", {})
                        self_view = perceptions.get("self_view", {})

                        # Update user perception
                        if user_view:
                            self.perception_engine.update_user_perception(
                                user_id=user_id,
                                attributes={
                                    "perceived_emotion": user_view.get(
                                        "perceived_emotion", "neutral"
                                    ),
                                    "skill_level_estimate": user_view.get(
                                        "skill_level_estimate", "intermediate"
                                    ),
                                    "communication_style": user_view.get(
                                        "communication_style", "polite"
                                    ),
                                    "engagement_level": user_view.get("engagement_level", 0.5),
                                },
                                justification=f"Based on interaction: {user_input[:100]}",
                                persona_context=persona_context,
                            )

                        # Update self-perception
                        if self_view:
                            self.perception_engine.update_self_perception(
                                attributes={
                                    "status": self_view.get("status", "helpful_assistant"),
                                    "internal_state": self_view.get(
                                        "internal_state", "medium_confidence"
                                    ),
                                    "action_taken": self_view.get(
                                        "action_taken", "provided_answer"
                                    ),
                                    "efficacy_score": self_view.get("efficacy_score", 0.7),
                                },
                                justification=(
                                    f"Based on interaction response: " f"{robot_response[:100]}"
                                ),
                                persona_context=persona_context,
                            )

                        # Embed perceptions in memory documents (for new memories)
                        if stored_memories and user_view:
                            for memory in stored_memories:
                                if memory.get("action") == "created":
                                    memory_id = memory.get("id")
                                    if memory_id:
                                        try:
                                            self.collection.update_one(
                                                {"_id": ObjectId(memory_id)},
                                                {
                                                    "$set": {
                                                        "perceptions": {
                                                            "user_view": user_view,
                                                            "self_view": self_view,
                                                        }
                                                    }
                                                },
                                            )
                                        except (
                                            AttributeError,
                                            TypeError,
                                            ValueError,
                                            RuntimeError,
                                        ) as e:
                                            logger.debug(
                                                f"Failed to embed perceptions " f"in memory: {e}"
                                            )
                except (
                    AttributeError,
                    RuntimeError,
                    ValueError,
                    TypeError,
                ) as e:
                    logger.debug(f"Perception analysis failed: {e}")
        except (
            AttributeError,
            RuntimeError,
            ValueError,
            TypeError,
        ) as e:
            logger.debug(f"Perception processing error: {e}")

    def _execute_async_actions(
        self,
        user_id: str | None,
        input_text: str,
        valid_facts: list[dict[str, Any]],
        valid_embeddings: list[list[float]],
        importance_map: dict[str, float],
        final_metadata: dict[str, Any],
        facts_to_duplicate: list[tuple[int, dict[str, Any]]],
        facts_to_reinforce: list[tuple[int, dict[str, Any]]],
        facts_to_merge: list[tuple[int, dict[str, Any]]],
        facts_to_create: list[int],
        messages: str | list[dict[str, str]] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute DB operations for add_async."""
        stored_memories = []
        now = datetime.now(timezone.utc)

        # Process duplicates
        stored_memories.extend(
            self._process_duplicate_facts(
                user_id, valid_facts, valid_embeddings, facts_to_duplicate
            )
        )

        # Process reinforcements
        stored_memories.extend(
            self._process_reinforcement_facts(
                user_id, valid_facts, valid_embeddings, facts_to_reinforce, now
            )
        )

        # Process merges
        stored_memories.extend(
            self._process_merge_facts(
                user_id,
                valid_facts,
                valid_embeddings,
                importance_map,
                final_metadata,
                facts_to_merge,
                now,
            )
        )

        # Process new memories
        stored_memories.extend(
            self._process_new_facts(
                user_id,
                valid_facts,
                valid_embeddings,
                importance_map,
                final_metadata,
                facts_to_create,
                now,
            )
        )

        # Post-processing (decay update and pruning)
        if self.enable_cognitive and facts_to_create:
            if valid_embeddings and facts_to_create:
                first_new_idx = facts_to_create[0]
                self._update_importance_decay(user_id, valid_embeddings[first_new_idx])
            self._prune_memories(user_id)

        # Perception analysis
        self._process_perception_analysis(user_id, messages, stored_memories)

        return stored_memories

    def search(
        self,
        query: str,
        user_id: str | None = None,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
        version: str | None = None,
        **kwargs,
    ) -> list[dict[str, Any]]:
        """
        Semantic Search with optional decay-aware ranking.

        If cognitive features enabled with decay:
            Results ranked by final_score = similarity * retrieval_strength
            Where retrieval_strength = importance * exp(-t / stability)

        If cognitive features enabled without decay:
            Results ranked by effective_importance = importance * (1 + ln(access_count + 1))

        Otherwise: Results ranked by similarity score.

        Args:
            query: Search query string
            user_id: User ID to scope search
            limit: Maximum results to return
            filters: Additional filters to apply
            version: Optional version parameter (for compatibility)
            **kwargs: Additional arguments:
                - use_decay: Enable decay-aware ranking (default: True if cognitive enabled)
                - update_access: Update access counts for retrieved memories (default: True)
        """
        # Check if decay-aware search should be used
        use_decay = kwargs.get(
            "use_decay", self.enable_cognitive and self.config.get("use_server_side_decay", True)
        )
        update_access = kwargs.get("update_access", True)

        logger.info(
            f"🔍 [Memory Search] Starting search: query='{query[:50]}...', "
            f"user_id={user_id}, limit={limit}, use_decay={use_decay}"
        )
        try:
            # 1. Vectorize Query
            query_vector = self._get_embedding(query)
            if not query_vector:
                logger.warning("⚠️ [Memory Search] Failed to generate embedding for query")
                return []

            # 1b. Apply persona filtering if enabled
            if self.persona_engine:
                persona_vector = self.persona_engine.get_persona_vector()
                if persona_vector and len(persona_vector) == len(query_vector):
                    # Combine query vector with persona vector (weighted average)
                    # This boosts memories that align with the persona
                    persona_weight = 0.2  # 20% persona influence
                    query_weight = 0.8  # 80% query influence
                    query_vector = [
                        query_weight * q + persona_weight * p
                        for q, p in zip(query_vector, persona_vector, strict=False)
                    ]
                    # Normalize the combined vector
                    magnitude = math.sqrt(sum(x * x for x in query_vector))
                    if magnitude > 0:
                        query_vector = [x / magnitude for x in query_vector]
                    logger.debug("✅ Applied persona filtering to search query")

            # 2. Use decay-aware pipeline if enabled
            if use_decay and self.enable_cognitive:
                return self._search_with_decay(
                    query_vector=query_vector,
                    user_id=user_id,
                    limit=limit,
                    filters=filters,
                    update_access=update_access,
                )

            # 3. Build standard Search Pipeline (non-decay path)
            search_filter = {"is_active": True}  # Only search active memories
            if user_id:
                search_filter["user_id"] = str(user_id)
                logger.info(f"🔍 [Memory Search] Filtering by user_id: {str(user_id)}")

            if filters:
                for key, value in filters.items():
                    if key == "metadata" and isinstance(value, dict):
                        for k, v in value.items():
                            search_filter[f"metadata.{k}"] = v
                    elif key not in ["OR", "AND"]:
                        search_filter[key] = value

            # Check if memories exist for this user (for debugging)
            if user_id:
                logger.info(
                    f"🔍 [Memory Search] Searching in collection: "
                    f"{self.collection_name} (db: {self.db_name})"
                )
                total_memories = self.collection.count_documents({"user_id": str(user_id)})
                logger.info(
                    f"🔍 [Memory Search] Total memories in DB for user_id {user_id}: "
                    f"{total_memories}"
                )
                if total_memories == 0:
                    # Check if there are any memories at all
                    any_memories = self.collection.count_documents({})
                    logger.warning(
                        f"⚠️ [Memory Search] No memories found for user_id {user_id}, "
                        f"but {any_memories} total memories exist in collection "
                        f"{self.collection_name}"
                    )
                    # Sample a few memories to see their user_id format
                    sample = list(self.collection.find({}).limit(3))
                    if sample:
                        logger.info(f"  Found {len(sample)} sample memories:")
                        for s in sample:
                            sample_user_id = s.get("user_id")
                            sample_text = s.get("text", "")[:50]
                            user_type = type(sample_user_id).__name__
                            logger.info(
                                f"    - user_id: {sample_user_id} (type: {user_type}), "
                                f"text: '{sample_text}...'"
                            )
                    else:
                        logger.warning(
                            f"  ⚠️ Collection {self.collection_name} is completely empty!"
                        )

            # Build projection based on cognitive features
            project_fields = {
                "_id": 1,
                "text": 1,
                "metadata": 1,
                "user_id": 1,
                "created_at": 1,
                "similarity": 1,
            }

            if self.enable_cognitive:
                project_fields["importance"] = {"$ifNull": ["$importance", 0.5]}
                project_fields["access_count"] = {"$ifNull": ["$access_count", 0]}

            logger.info(
                f"🔍 [Memory Search] Using index: {self.index_name}, "
                f"collection: {self.collection_name}"
            )
            cognitive_limit = limit * 2 if self.enable_cognitive else limit
            logger.info(
                f"🔍 [Memory Search] Vector search params: "
                f"numCandidates={limit * 20}, limit={cognitive_limit}, "
                f"filter={search_filter}"
            )

            pipeline = [
                {
                    "$vectorSearch": {
                        "index": self.index_name,
                        "path": "embedding",
                        "queryVector": query_vector,
                        "numCandidates": limit * 20,
                        "limit": limit * 2 if self.enable_cognitive else limit,
                        "filter": search_filter if search_filter else {},
                    }
                },
                {"$addFields": {"similarity": {"$meta": "vectorSearchScore"}}},
                {"$project": project_fields},
            ]

            logger.info("🔍 [Memory Search] Executing vector search pipeline...")
            try:
                cursor = self.collection.aggregate(pipeline)
            except OperationFailure as e:
                error_msg = str(e)
                if "index" in error_msg.lower() or "vectorSearch" in error_msg.lower():
                    logger.exception(
                        f"❌ [Memory Search] Vector search index error: {error_msg}. "
                        f"Index '{self.index_name}' may not exist or is not queryable. "
                        f"Check if the index is defined in 'managed_indexes' in manifest.json "
                        f"and ensure it's been created in MongoDB Atlas."
                    )
                raise
            except (PyMongoError, ValueError, TypeError, RuntimeError) as e:
                logger.exception(
                    f"❌ [Memory Search] Failed to execute vector search pipeline: {e}"
                )
                raise

            results = []
            memory_ids_to_update = []
            raw_docs_count = 0

            try:
                for doc in cursor:
                    raw_docs_count += 1
                    similarity = doc.get("similarity", 0.0)

                    if self.enable_cognitive:
                        importance = doc.get("importance", 0.5)
                        access_count = doc.get("access_count", 0)

                        # Calculate effective importance
                        effective_importance = importance * (1 + math.log(access_count + 1))

                        # Combined score: similarity * effective_importance
                        combined_score = similarity * effective_importance

                        results.append(
                            {
                                "id": str(doc["_id"]),
                                "memory": doc.get("text", ""),
                                "metadata": doc.get("metadata", {}),
                                "user_id": doc.get("user_id"),
                                "score": combined_score,
                                "similarity": similarity,
                                "importance": importance,
                                "effective_importance": effective_importance,
                                "access_count": access_count,
                                "created_at": doc.get("created_at").isoformat()
                                if doc.get("created_at")
                                else None,
                            }
                        )
                    else:
                        results.append(
                            {
                                "id": str(doc["_id"]),
                                "memory": doc.get("text", ""),
                                "metadata": doc.get("metadata", {}),
                                "user_id": doc.get("user_id"),
                                "score": similarity,
                                "created_at": doc.get("created_at").isoformat()
                                if doc.get("created_at")
                                else None,
                            }
                        )

                    memory_ids_to_update.append(doc["_id"])
            except OperationFailure as e:
                error_msg = str(e)
                if "index" in error_msg.lower() or "vectorSearch" in error_msg.lower():
                    logger.exception(
                        f"❌ [Memory Search] Vector search index error while iterating "
                        f"results: {error_msg}. Index '{self.index_name}' may not exist or "
                        f"is not queryable. Check if the index is defined in "
                        f"'managed_indexes' in manifest.json and ensure it's been created "
                        f"in MongoDB Atlas."
                    )
                raise
            except (PyMongoError, ValueError, TypeError, KeyError, RuntimeError) as e:
                logger.exception(f"❌ [Memory Search] Error while processing search results: {e}")
                raise

            # Sort by score (descending)
            results.sort(key=lambda x: x["score"], reverse=True)
            results = results[:limit]

            logger.info(
                f"🔍 [Memory Search] Found {raw_docs_count} raw docs, returning "
                f"{len(results)} results after filtering/sorting"
            )
            if results:
                for i, r in enumerate(results):
                    score = r.get("score", "N/A")
                    score_str = f"{score:.4f}" if isinstance(score, int | float) else score
                    logger.info(
                        f"  Result {i+1}: '{r.get('memory', '')[:50]}...' " f"(score: {score_str})"
                    )
            else:
                if raw_docs_count == 0:
                    logger.warning(
                        f"⚠️ [Memory Search] No results found for query: '{query[:50]}...' "
                        f"(user_id={user_id}). "
                        f"Vector search returned 0 documents. Possible causes: "
                        f"1) Index '{self.index_name}' doesn't exist or isn't queryable "
                        f"2) No memories match the filter criteria "
                        f"3) Embeddings don't match the query vector. "
                        f"Check if the index is defined in 'managed_indexes' "
                        f"and ensure it's been created."
                    )
                else:
                    logger.warning(
                        f"⚠️ [Memory Search] No results found after filtering/sorting "
                        f"(found {raw_docs_count} raw docs)"
                    )

            # Update access counts for retrieved memories (cognitive only)
            if self.enable_cognitive and memory_ids_to_update:
                self.collection.update_many(
                    {"_id": {"$in": memory_ids_to_update}},
                    {
                        "$inc": {"access_count": 1},
                        "$set": {"last_accessed": datetime.now(timezone.utc)},
                    },
                )

            return results

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
            logger.error(f"❌ [Memory Search] Search failed: {e}", exc_info=True)
            return []

    def get(self, memory_id: str, user_id: str | None = None, **kwargs) -> dict[str, Any] | None:
        """Retrieve a single memory by ID."""
        try:
            query = {"_id": ObjectId(memory_id)}
            if user_id:
                query["user_id"] = str(user_id)

            doc = self.collection.find_one(query)
            if not doc:
                return None

            result = {
                "id": str(doc["_id"]),
                "memory": doc.get("text", ""),
                "metadata": doc.get("metadata", {}),
                "user_id": doc.get("user_id"),
                "created_at": doc.get("created_at").isoformat() if doc.get("created_at") else None,
                "updated_at": doc.get("updated_at").isoformat() if doc.get("updated_at") else None,
            }

            # Add cognitive fields if present
            if self.enable_cognitive and "importance" in doc:
                result["importance"] = doc.get("importance", 0.5)
                result["access_count"] = doc.get("access_count", 0)

            return result
        except (InvalidId, PyMongoError):
            return None

    def get_all(
        self,
        user_id: str | None = None,
        limit: int = 100,
        filters: dict[str, Any] | None = None,
        **kwargs,
    ) -> list[dict[str, Any]]:
        """Retrieve all memories with basic filtering."""
        query = {}
        if user_id:
            query["user_id"] = str(user_id)

        if filters:
            for k, v in filters.items():
                if k == "metadata" and isinstance(v, dict):
                    for mk, mv in v.items():
                        query[f"metadata.{mk}"] = mv
                else:
                    query[k] = v

        try:
            logger.info(
                f"📋 [Memory get_all] Querying collection: {self.collection_name}, "
                f"db: {self.db_name}, user_id: {user_id}, query: {query}"
            )
            total_count = self.collection.count_documents({})
            user_count = self.collection.count_documents(query) if query else total_count
            logger.info(
                f"📋 [Memory get_all] Collection stats: total={total_count}, "
                f"matching query={user_count}"
            )

            cursor = self.collection.find(query).sort("created_at", -1).limit(limit)
            results = []
            for doc in cursor:
                result = {
                    "id": str(doc["_id"]),
                    "memory": doc.get("text", ""),
                    "metadata": doc.get("metadata", {}),
                    "user_id": doc.get("user_id"),
                    "created_at": doc.get("created_at").isoformat()
                    if doc.get("created_at")
                    else None,
                }

                # Add cognitive fields if present
                if self.enable_cognitive and "importance" in doc:
                    result["importance"] = doc.get("importance", 0.5)
                    result["access_count"] = doc.get("access_count", 0)

                results.append(result)

            logger.info(
                f"📋 [Memory get_all] Returning {len(results)} memories from "
                f"collection {self.collection_name}"
            )
            return results
        except (PyMongoError, OperationFailure, ValueError, TypeError, RuntimeError) as e:
            logger.error(f"❌ [Memory get_all] Failed: {e}", exc_info=True)
            return []

    def update(
        self,
        memory_id: str,
        user_id: str | None = None,
        memory: str | None = None,
        data: str | dict[str, Any] | None = None,
        messages: str | list[dict[str, str]] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs,
    ) -> dict[str, Any] | None:
        """
        Updates a memory. Automatically regenerates embeddings if text changes.
        """
        # Normalize input text
        new_text = None
        if memory:
            new_text = memory
        elif isinstance(data, str):
            new_text = data
        elif isinstance(data, dict):
            new_text = data.get("memory") or data.get("text")
        elif isinstance(messages, str):
            new_text = messages
        elif isinstance(messages, list):
            new_text = "\n".join([m.get("content", "") for m in messages if m.get("content")])

        try:
            query = {"_id": ObjectId(memory_id)}
            if user_id:
                query["user_id"] = str(user_id)

            existing = self.collection.find_one(query)
            if not existing:
                logger.warning(f"Memory {memory_id} not found for update.")
                return None

            update_fields = {"updated_at": datetime.now(timezone.utc)}

            # 1. Handle Text Update (Requires Re-Embedding)
            if new_text and new_text.strip() != existing.get("text"):
                logger.info(f"🔄 Updating text and regenerating embedding for {memory_id}")
                update_fields["text"] = new_text.strip()
                update_fields["embedding"] = self._get_embedding(new_text.strip())

                # Re-assess importance if cognitive enabled
                if self.enable_cognitive:
                    update_fields["importance"] = self._assess_importance(new_text.strip())

            # 2. Handle Metadata Update
            if metadata:
                # Merge with existing metadata
                current_meta = existing.get("metadata", {})
                current_meta.update(metadata)
                update_fields["metadata"] = current_meta

            self.collection.update_one(query, {"$set": update_fields})

            # Return fresh doc
            return self.get(memory_id, user_id)

        except (PyMongoError, OperationFailure, InvalidId) as e:
            logger.exception("Update failed")
            raise CognitiveMemoryServiceError(f"Update operation failed: {e}") from e

    def delete(self, memory_id: str, user_id: str | None = None, **kwargs) -> bool:
        """Delete a single memory."""
        try:
            query = {"_id": ObjectId(memory_id)}
            if user_id:
                query["user_id"] = str(user_id)
            result = self.collection.delete_one(query)
            return result.deleted_count > 0
        except (PyMongoError, OperationFailure, InvalidId):
            return False

    def delete_all(
        self, user_id: str | None = None, hard_delete: bool = _REQUIRED_HARD_DELETE, **kwargs
    ) -> bool:
        """
        Delete all memories for a user.

        Args:
            user_id: User ID whose memories should be deleted
            hard_delete: REQUIRED - If True, permanently remove all memories including cold storage.
                         If False, soft-delete by marking as deleted (for legal retention).
                         Must be explicitly specified - no default for safety.

        Returns:
            True if deletion was successful, False otherwise

        Raises:
            TypeError: If hard_delete is not explicitly provided
        """
        # Require explicit hard_delete parameter - no default for safety
        if hard_delete is _REQUIRED_HARD_DELETE:
            raise TypeError(
                "delete_all() requires explicit 'hard_delete' parameter. "
                "Specify hard_delete=True for GDPR-compliant deletion or "
                "hard_delete=False for legal retention."
            )
        if not user_id:
            logger.warning("delete_all requires user_id safety check.")
            return False

        try:
            query = {"user_id": str(user_id)}

            if hard_delete:
                # GDPR-compliant hard delete - remove all memories including cold storage
                result = self.collection.delete_many(query)
                logger.info(
                    f"✅ [GDPR] Hard deleted {result.deleted_count} memories "
                    f"(including cold storage) for user {user_id}"
                )
                return result.deleted_count > 0
            else:
                # Soft delete - mark as deleted but preserve for legal retention
                now = datetime.now(timezone.utc)
                result = self.collection.update_many(
                    query,
                    {
                        "$set": {
                            "is_active": False,
                            "gdpr_deleted": True,
                            "deleted_at": now,
                        }
                    },
                )
                logger.info(
                    f"✅ [GDPR] Soft deleted {result.modified_count} memories for user {user_id} "
                    f"(preserved for legal retention)"
                )
                return result.modified_count > 0
        except (PyMongoError, OperationFailure):
            logger.exception(f"❌ [GDPR] Error deleting memories for user {user_id}")
            return False
