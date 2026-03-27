"""
Cognitive Memory System Core
Multi-tier memory architecture using native LLM SDKs and MongoDB.

This module implements the core CognitiveMemory class that distinguishes between:
- Working Memory: Short-term, TTL-indexed active context
- Episodic Memory: Chronological stream of interactions with vector search
- Semantic Memory: Structured entity facts and world knowledge
- Procedural Memory: Executable skills, tools, and workflows

Architecture:
- Uses native SDKs for model abstraction (OpenAI, Azure, Gemini)
- MongoDB as unified storage engine
- Vector search for semantic/episodic retrieval
- TTL indexes for automatic working memory eviction
"""

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ._async_compat import cursor_to_list as _cursor_to_list
from ._async_compat import maybe_await as _maybe_await

if TYPE_CHECKING:
    from mdb_engine.database.scoped_wrapper import (
        ScopedCollectionWrapper,
        ScopedMongoWrapper,
    )
    from mdb_engine.embeddings.service import EmbeddingService
    from mdb_engine.memory.procedural import ProceduralMemory

try:
    from pymongo.errors import OperationFailure, PyMongoError

    PYMONGO_AVAILABLE = True
except ImportError:
    raise ImportError("pip install pymongo") from None

from .base import MemoryServiceError

logger = logging.getLogger(__name__)


class CognitiveMemoryError(MemoryServiceError):
    """Base exception for Cognitive Memory System errors."""

    pass


class CognitiveMemory:
    """
    Core controller for multi-tier Cognitive Memory System.

    This class manages four distinct memory layers:
    1. Working Memory: Short-term active context (TTL-indexed)
    2. Episodic Memory: Chronological interaction logs with vectors
    3. Semantic Memory: Structured entity facts and relationships
    4. Procedural Memory: Skills, tools, and executable workflows

    The system uses native SDKs for model abstraction, allowing seamless switching
    between OpenAI, Azure OpenAI, and Google Gemini.

    All public methods are async and should be awaited.

    Example:
        ```python
        from mdb_engine.memory.system import CognitiveMemory

        memory = CognitiveMemory(
            collection=scoped_collection,
            model="gpt-4-turbo",
            embed_model="text-embedding-3-small"
        )

        # Record an episode
        await memory.record_episode(
            session_id="session123",
            role="user",
            content="I love Python programming"
        )

        # Update entity facts
        await memory.update_entity(
            entity_name="user_preferences",
            attributes={"favorite_language": "Python"}
        )

        # Set working context
        await memory.set_working_context(
            session_id="session123",
            data={"current_task": "debugging", "focus": "memory system"}
        )
        ```
    """

    def __init__(
        self,
        collection: "ScopedCollectionWrapper",
        model: str = "gpt-4-turbo",
        embed_model: str = "text-embedding-3-small",
        embedding_service: "EmbeddingService | None" = None,
    ):
        """
        Initialize CognitiveMemory system.

        Args:
            collection: ScopedCollectionWrapper instance (REQUIRED - must be from MDB-Engine connection manager).
                       Uses this collection's database/client for all operations.
                       collection.database returns ScopedMongoWrapper, ensuring all
                       collections are automatically scoped.
            model: LLM model for completions (default: "gpt-4-turbo")
            embed_model: Embedding model for vectors (default: "text-embedding-3-small")
            embedding_service: Optional EmbeddingService for generating embeddings.
                              When provided, ``_get_vector`` routes through this service
                              instead of calling provider SDKs directly.

        Raises:
            CognitiveMemoryError: If embedding_service is not available,
                or if collection is invalid

        Note:
            CognitiveMemory MUST use MDB-Engine's connection pool. Pass a ScopedCollectionWrapper
            obtained from the engine's connection manager. Direct MongoDB connections are not supported.
            All collections accessed through collection.database are automatically scoped with app_id filtering.
        """
        if embedding_service is None:
            raise CognitiveMemoryError("embedding_service is required. Pass an EmbeddingService instance.")

        if collection is None:
            raise CognitiveMemoryError(
                "Collection is REQUIRED. CognitiveMemory MUST use MDB-Engine's connection pool. "
                "Pass a Motor AsyncIOMotorCollection obtained from the engine's connection manager."
            )

        self.model = model
        self.embed_model = embed_model
        self._embedding_service = embedding_service

        # Use provided collection (from MDB-Engine connection pool)
        # collection.database returns ScopedMongoWrapper (not raw database)
        # This ensures all collections accessed through db are automatically scoped
        self.db: ScopedMongoWrapper = collection.database
        self.client = self.db.client
        logger.info(f"CognitiveMemory using MDB-Engine collection: " f"db={self.db.name}, collection={collection.name}")

        # Initialize collections - all accessed through scoped database wrapper
        # These are ScopedCollectionWrapper instances with automatic app_id filtering
        self.working_memory: ScopedCollectionWrapper = self.db.working_memory
        self.episodic: ScopedCollectionWrapper = self.db.episodic
        self.entity_memory: ScopedCollectionWrapper = self.db.entity_memory
        self.procedural: ScopedCollectionWrapper = self.db.procedural
        # New collections for "perfect brain" features
        self.reflective_memory: ScopedCollectionWrapper = self.db.reflective_memory
        self.predictive_memory: ScopedCollectionWrapper = self.db.predictive_memory
        self.memory_vetoes: ScopedCollectionWrapper = self.db.memory_vetoes
        self._indexes_ensured = False

    async def _ensure_ready(self) -> None:
        """Lazily create indexes on first operation."""
        if self._indexes_ensured:
            return
        await self._ensure_indexes()
        self._indexes_ensured = True

    async def _ensure_indexes(self):
        """Create necessary indexes for efficient queries and TTL eviction."""
        try:
            # Working Memory: TTL index for automatic eviction (24 hours default)
            await _maybe_await(
                self.working_memory.create_index(
                    "last_accessed",
                    expireAfterSeconds=24 * 3600,  # 24 hours
                    name="working_memory_ttl_idx",
                )
            )
            logger.info("Working memory TTL index created (24h expiration)")

            # Episodic: Indexes for session and timestamp queries
            await _maybe_await(self.episodic.create_index([("session_id", 1), ("timestamp", -1)]))
            await _maybe_await(self.episodic.create_index([("consolidated", 1), ("timestamp", 1)]))
            logger.info("Episodic memory indexes created")

            # Entity Memory: Index for entity lookups
            await _maybe_await(self.entity_memory.create_index("entity", unique=True))
            await _maybe_await(self.entity_memory.create_index("last_updated"))
            logger.info("Entity memory indexes created")

            # Procedural: Index for task-based retrieval
            await _maybe_await(self.procedural.create_index([("task_type", 1), ("success_rate", -1)]))
            logger.info("Procedural memory indexes created")

            # Reflective Memory: Indexes for scope and confidence
            await _maybe_await(self.reflective_memory.create_index([("scope", 1), ("user_id", 1), ("confidence", -1)]))
            logger.info("Reflective memory indexes created")

            # Predictive Memory: Indexes for scope, validation, and confidence
            await _maybe_await(
                self.predictive_memory.create_index([("scope", 1), ("validated", 1), ("confidence", -1)])
            )
            logger.info("Predictive memory indexes created")

            # Memory Vetoes: Index for memory_id and user_id
            await _maybe_await(self.memory_vetoes.create_index([("memory_id", 1), ("user_id", 1)], unique=True))
            logger.info("Memory veto indexes created")

            # Add scope indexes to existing collections
            await _maybe_await(self.entity_memory.create_index([("scope", 1), ("group_id", 1)]))
            await _maybe_await(self.episodic.create_index([("scope", 1), ("group_id", 1)]))
            # Bucket indexes for all collections
            await _maybe_await(self.entity_memory.create_index("metadata.associated_bucket_id"))
            await _maybe_await(self.episodic.create_index("metadata.associated_bucket_id"))
            logger.info("Scope and bucket indexes created")

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Index creation warning (may already exist): {e}")

    async def _get_vector(self, text: str) -> list[float]:
        """
        Generate embedding vector for text via the injected embedding service.

        Args:
            text: Text to embed

        Returns:
            List of floats representing the embedding vector

        Raises:
            CognitiveMemoryError: If embedding generation fails
        """
        if self._embedding_service is None:
            raise CognitiveMemoryError(
                "No embedding service available. " "Pass an EmbeddingService instance to the constructor."
            )

        try:
            vectors = await self._embedding_service.embed(text)
            if not vectors or not vectors[0]:
                raise CognitiveMemoryError("Empty embedding response from service")
            return vectors[0]
        except CognitiveMemoryError:
            raise
        except (AttributeError, KeyError, TypeError, ValueError, RuntimeError) as e:
            logger.error(f"Embedding generation via service failed: {e}", exc_info=True)
            raise CognitiveMemoryError(f"Failed to generate embedding: {e}") from e

    # --- SHORT TERM / WORKING MEMORY ---

    async def set_working_context(self, session_id: str, data: dict[str, Any]):
        """
        Update the agent's immediate workspace (expires via TTL).

        Working memory is short-term and automatically expires after 24 hours
        (configurable via TTL index). Use this for active conversation context
        and "scratchpad" reasoning.

        Args:
            session_id: Session identifier
            data: Dictionary of context data to store

        Example:
            ```python
            await memory.set_working_context(
                session_id="session123",
                data={
                    "current_task": "debugging",
                    "focus": "memory system",
                    "step": 3
                }
            )
            ```
        """
        await self._ensure_ready()

        try:
            await _maybe_await(
                self.working_memory.update_one(
                    {"session_id": session_id},
                    {"$set": {**data, "last_accessed": datetime.now(timezone.utc)}},
                    upsert=True,
                )
            )
            logger.debug(f"Working context updated for session {session_id}")
        except (PyMongoError, OperationFailure) as e:
            logger.error(f"Failed to set working context: {e}", exc_info=True)
            raise CognitiveMemoryError(f"Failed to set working context: {e}") from e

    async def get_working_context(self, session_id: str) -> dict[str, Any] | None:
        """
        Retrieve working context for a session.

        Args:
            session_id: Session identifier

        Returns:
            Working context dictionary or None if not found
        """
        await self._ensure_ready()

        try:
            doc = await _maybe_await(self.working_memory.find_one({"session_id": session_id}))
            if doc:
                # Remove MongoDB _id and last_accessed for cleaner return
                doc.pop("_id", None)
                doc.pop("last_accessed", None)
                return doc
            return None
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to get working context: {e}")
            return None

    # --- EPISODIC (The Stream of Experience) ---

    async def record_episode(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        scope: str = "user",
        user_id: str | None = None,
        shareable: bool = False,
        bucket_id: str | None = None,
        bucket_type: str | None = None,
        associated_bucket_id: str | None = None,
        group_id: str | None = None,
    ):
        """
        Log a raw interaction for future reflection.

        Episodic memory stores chronological interactions as a stream of experiences.
        These are later consolidated into semantic facts via the reflection loop.

        Args:
            session_id: Session identifier
            role: Role of the speaker ("user", "assistant", "system")
            content: Message content
            metadata: Optional metadata dictionary
            scope: Memory scope ("user", "shared", "system")
            user_id: User ID if scope is "user"
            group_id: Generic group identifier if scope is "shared"
            shareable: Whether this episode can be promoted to shared memory
            bucket_id: Optional bucket ID for filtering
            bucket_type: Optional bucket type
            associated_bucket_id: Optional associated bucket ID for unified search

        Example:
            ```python
            await memory.record_episode(
                session_id="session123",
                role="user",
                content="I'm working on a Python project",
                metadata={"tokens_used": 10, "tool_calls_made": 0}
            )
            ```
        """
        await self._ensure_ready()

        try:
            # Generate vector embedding for semantic search
            vector = await self._get_vector(content)

            episode_doc = {
                "session_id": session_id,
                "role": role,
                "content": content,
                "timestamp": datetime.now(timezone.utc),
                "vector": vector,
                "consolidated": False,
                "scope": scope,
                "shareable": shareable,
            }

            if user_id:
                episode_doc["user_id"] = str(user_id)

            if group_id:
                episode_doc["group_id"] = group_id

            # Add bucket info to metadata
            if metadata is None:
                metadata = {}
            if bucket_id:
                metadata["bucket_id"] = bucket_id
            if bucket_type:
                metadata["bucket_type"] = bucket_type
            if associated_bucket_id:
                metadata["associated_bucket_id"] = associated_bucket_id
            elif bucket_id:
                metadata["associated_bucket_id"] = bucket_id

            if metadata:
                episode_doc["metadata"] = metadata

            await _maybe_await(self.episodic.insert_one(episode_doc))
            logger.debug(f"Episode recorded: {role} in session {session_id}")
        except (PyMongoError, OperationFailure, CognitiveMemoryError) as e:
            logger.error(f"Failed to record episode: {e}", exc_info=True)
            raise CognitiveMemoryError(f"Failed to record episode: {e}") from e

    async def get_episodes(
        self,
        session_id: str | None = None,
        limit: int = 10,
        consolidated: bool | None = None,
    ) -> list[dict[str, Any]]:
        """
        Retrieve episodic memories.

        Args:
            session_id: Optional session ID to filter by
            limit: Maximum number of episodes to return
            consolidated: Filter by consolidation status (None = all)

        Returns:
            List of episode documents
        """
        await self._ensure_ready()

        try:
            query = {}
            if session_id:
                query["session_id"] = session_id
            if consolidated is not None:
                query["consolidated"] = consolidated

            cursor = self.episodic.find(query).sort("timestamp", -1).limit(limit)
            return await _cursor_to_list(cursor, limit)
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to get episodes: {e}")
            return []

    # --- SEMANTIC (Structured Entity Facts) ---

    async def update_entity(
        self,
        entity_name: str,
        attributes: dict[str, Any],
        confidence: float = 1.0,
        scope: str = "user",
        user_id: str | None = None,
        bucket_id: str | None = None,
        bucket_type: str | None = None,
        associated_bucket_id: str | None = None,
        group_id: str | None = None,
    ):
        """
        Upsert a fact. Handles conflict by timestamping updates.

        Semantic memory stores structured facts about entities. This implements
        "Versioned Truth" - old facts are preserved in history rather than deleted,
        allowing the agent to reason about changes over time.

        Args:
            entity_name: Name/identifier of the entity (e.g., "user_preferences")
            attributes: Dictionary of attribute key-value pairs
            confidence: Confidence level (0.0 to 1.0) for this fact

        Example:
            ```python
            await memory.update_entity(
                entity_name="user_preferences",
                attributes={"theme": "dark", "language": "Python"},
                confidence=0.9
            )
            ```
        """
        await self._ensure_ready()

        try:
            entity_key = entity_name.lower()

            # Generate vector for the combined attributes (for similarity search)
            attr_text = ", ".join([f"{k}: {v}" for k, v in attributes.items()])
            vector = await self._get_vector(f"{entity_name}: {attr_text}")

            # Get existing entity to preserve history
            existing = await _maybe_await(self.entity_memory.find_one({"entity": entity_key}))

            update_doc = {
                "$set": {
                    **{f"attr.{k}": v for k, v in attributes.items()},
                    "vector": vector,
                    "confidence": confidence,
                    "scope": scope,
                },
                "$currentDate": {"last_updated": True},
            }

            if user_id:
                update_doc["$set"]["user_id"] = str(user_id)

            if group_id:
                update_doc["$set"]["group_id"] = group_id

            # Add bucket info to metadata
            if bucket_id or bucket_type or associated_bucket_id:
                if "metadata" not in update_doc["$set"]:
                    # Get existing metadata or create new
                    existing_metadata = existing.get("metadata", {}) if existing else {}
                    update_doc["$set"]["metadata"] = existing_metadata

                if bucket_id:
                    update_doc["$set"]["metadata.bucket_id"] = bucket_id
                if bucket_type:
                    update_doc["$set"]["metadata.bucket_type"] = bucket_type
                if associated_bucket_id:
                    update_doc["$set"]["metadata.associated_bucket_id"] = associated_bucket_id
                elif bucket_id:
                    update_doc["$set"]["metadata.associated_bucket_id"] = bucket_id

            # Preserve history for versioned truth
            if existing:
                # Move old attributes to history
                old_attrs = existing.get("attr", {})
                if old_attrs:
                    history_entry = {
                        "attributes": old_attrs,
                        "timestamp": existing.get("last_updated", datetime.now(timezone.utc)),
                        "confidence": existing.get("confidence", 1.0),
                    }
                    update_doc["$push"] = {"history": {"$each": [history_entry], "$slice": -100}}
            else:
                # First time creating this entity
                update_doc["$setOnInsert"] = {"created_at": datetime.now(timezone.utc)}

            await _maybe_await(
                self.entity_memory.update_one(
                    {"entity": entity_key},
                    update_doc,
                    upsert=True,
                )
            )
            logger.debug(f"Entity updated: {entity_name}")
        except (PyMongoError, OperationFailure, CognitiveMemoryError) as e:
            logger.error(f"Failed to update entity: {e}", exc_info=True)
            raise CognitiveMemoryError(f"Failed to update entity: {e}") from e

    async def get_entity(self, entity_name: str) -> dict[str, Any] | None:
        """
        Retrieve an entity with its attributes and history.

        Args:
            entity_name: Entity identifier

        Returns:
            Entity document or None if not found
        """
        await self._ensure_ready()

        try:
            return await _maybe_await(self.entity_memory.find_one({"entity": entity_name.lower()}))
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to get entity: {e}")
            return None

    async def search_entities(
        self,
        query: str,
        limit: int = 5,
        scope: str = "user",
        user_id: str | None = None,
        group_id: str | None = None,
        bucket_id: str | None = None,
        bucket_type: str | None = None,
        min_confidence: float = 0.5,
    ) -> list[dict[str, Any]]:
        """
        Search entities using vector similarity.

        Args:
            query: Search query string
            limit: Maximum number of results

        Returns:
            List of matching entity documents
        """
        await self._ensure_ready()

        try:
            query_vector = await self._get_vector(query)

            # Build filter for vector search
            vector_filter: dict[str, Any] = {
                "scope": scope,
                "is_active": True,
                "confidence": {"$gte": min_confidence},
            }

            if user_id and scope == "user":
                vector_filter["user_id"] = str(user_id)
            if group_id and scope == "shared":
                vector_filter["group_id"] = group_id

            # Add bucket filtering
            if bucket_id:
                vector_filter["metadata.associated_bucket_id"] = bucket_id
            elif bucket_type:
                vector_filter["metadata.bucket_type"] = bucket_type

            # Use MongoDB Atlas Vector Search - NO FALLBACK
            # If vector search is not available, this is a failure that needs to be addressed
            try:
                pipeline = [
                    {
                        "$vectorSearch": {
                            "index": "entity_vector_index",
                            "path": "vector",
                            "queryVector": query_vector,
                            "numCandidates": limit * 10,
                            "limit": limit,
                            "filter": vector_filter,
                        }
                    },
                    {"$match": vector_filter},  # Additional match for safety
                ]
                results = self.entity_memory.aggregate(pipeline)
                return await _cursor_to_list(results, limit)
            except (PyMongoError, OperationFailure) as e:
                logger.exception(
                    f"Vector search FAILED: {e}. NO FALLBACK - This needs to be fixed. "
                    f"query='{query[:100]}...'. "
                    f"Vector search index 'entity_vector_index' may not be configured. "
                    f"Configure the index properly or fix the error."
                )
                # NO FALLBACK: Failures are explicit - raise error so caller can handle it
                raise RuntimeError(
                    f"Vector search failed: {e}. Vector search index may not be configured. " f"This needs to be fixed."
                ) from e
        except (PyMongoError, OperationFailure, CognitiveMemoryError) as e:
            logger.warning(f"Entity search failed: {e}")
            return []

    # --- REFLECTIVE MEMORY (Meta-cognition) ---

    async def store_reflection(
        self,
        reflection: str,
        trigger: str,
        confidence: float,
        scope: str = "user",
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        bucket_id: str | None = None,
        bucket_type: str | None = None,
        group_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Store a meta-cognitive reflection.

        Args:
            reflection: The reflection text
            trigger: What triggered this reflection
            confidence: Confidence in this reflection (0.0 to 1.0)
            scope: Memory scope ("user", "shared", "system")
            user_id: User ID if scope is "user"
            group_id: Generic group identifier if scope is "shared"
            metadata: Optional metadata dictionary
            bucket_id: Optional bucket ID for filtering
            bucket_type: Optional bucket type

        Returns:
            Created reflection document
        """
        await self._ensure_ready()

        from .reflective import ReflectiveMemory

        # Normalize scope: accept "family" but store as "shared"
        normalized_scope = "shared" if scope == "family" else scope

        # Add bucket info to metadata
        final_metadata = metadata or {}
        if bucket_id:
            final_metadata["bucket_id"] = bucket_id
        if bucket_type:
            final_metadata["bucket_type"] = bucket_type
        if bucket_id and "associated_bucket_id" not in final_metadata:
            final_metadata["associated_bucket_id"] = bucket_id

        reflective = ReflectiveMemory(collection=self.reflective_memory)
        return await reflective.store_reflection(
            reflection=reflection,
            trigger=trigger,
            confidence=confidence,
            scope=normalized_scope,
            user_id=user_id,
            group_id=group_id,
            metadata=final_metadata,
        )

    async def get_reflections(
        self,
        scope: str = "user",
        user_id: str | None = None,
        min_confidence: float = 0.5,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Retrieve reflections.

        Args:
            scope: Memory scope
            user_id: User ID if scope is "user"
            min_confidence: Minimum confidence threshold
            limit: Maximum number of results

        Returns:
            List of reflection documents
        """
        await self._ensure_ready()

        from .reflective import ReflectiveMemory

        reflective = ReflectiveMemory(collection=self.reflective_memory)
        return await reflective.get_reflections(
            scope=scope,
            user_id=user_id,
            min_confidence=min_confidence,
            limit=limit,
        )

    # --- PREDICTIVE MEMORY (Counterfactuals & Futures) ---

    async def store_prediction(
        self,
        scenario: str,
        origin: str,
        confidence: float,
        validated: bool = False,
        scope: str = "user",
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        bucket_id: str | None = None,
        bucket_type: str | None = None,
        group_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Store a prediction or counterfactual scenario.

        Args:
            scenario: Description of the prediction/scenario
            origin: Origin of the prediction
            confidence: Confidence in this prediction (0.0 to 1.0)
            validated: Whether this prediction has been validated
            scope: Memory scope ("user", "shared", "system")
            user_id: User ID if scope is "user"
            group_id: Generic group identifier if scope is "shared"
            metadata: Optional metadata dictionary
            bucket_id: Optional bucket ID for filtering
            bucket_type: Optional bucket type

        Returns:
            Created prediction document
        """
        await self._ensure_ready()

        from .predictive import PredictiveMemory

        # Normalize scope: accept "family" but store as "shared"
        normalized_scope = "shared" if scope == "family" else scope

        # Add bucket info to metadata
        final_metadata = metadata or {}
        if bucket_id:
            final_metadata["bucket_id"] = bucket_id
        if bucket_type:
            final_metadata["bucket_type"] = bucket_type
        if bucket_id and "associated_bucket_id" not in final_metadata:
            final_metadata["associated_bucket_id"] = bucket_id

        predictive = PredictiveMemory(collection=self.predictive_memory)
        return await predictive.store_prediction(
            scenario=scenario,
            origin=origin,
            confidence=confidence,
            validated=validated,
            scope=normalized_scope,
            user_id=user_id,
            group_id=group_id,
            metadata=final_metadata,
        )

    async def get_predictions(
        self,
        scope: str = "user",
        user_id: str | None = None,
        validated: bool | None = None,
        min_confidence: float = 0.5,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Retrieve predictions.

        Args:
            scope: Memory scope
            user_id: User ID if scope is "user"
            validated: Filter by validation status
            min_confidence: Minimum confidence threshold
            limit: Maximum number of results

        Returns:
            List of prediction documents
        """
        await self._ensure_ready()

        from .predictive import PredictiveMemory

        predictive = PredictiveMemory(collection=self.predictive_memory)
        return await predictive.get_predictions(
            scope=scope,
            user_id=user_id,
            validated=validated,
            min_confidence=min_confidence,
            limit=limit,
        )

    # ------------------------------------------------------------------
    # Procedural Memory (skills, tools, workflows)
    # ------------------------------------------------------------------

    def _get_procedural_memory(self) -> "ProceduralMemory":
        """Lazily create a ProceduralMemory instance backed by ``self.procedural``."""
        from .procedural import ProceduralMemory

        return ProceduralMemory(
            collection=self.procedural,
            embedding_service=self._embedding_service,
            embed_model=self.embed_model,
        )

    async def store_skill(
        self,
        name: str,
        task_type: str,
        steps: list[str] | None = None,
        code_snippet: str | None = None,
        tool_schema: dict[str, Any] | None = None,
        success_rate: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Store a procedural skill (tool/workflow).

        Delegates to :class:`ProceduralMemory.store_procedure`.

        Args:
            name: Skill name
            task_type: Task category (e.g. "deployment", "debugging")
            steps: Ordered step descriptions
            code_snippet: Executable code snippet
            tool_schema: JSON Schema for a tool definition
            success_rate: Initial success rate (0.0-1.0)
            metadata: Optional metadata dict

        Returns:
            Created skill document
        """
        await self._ensure_ready()
        proc = self._get_procedural_memory()
        return await proc.store_procedure(
            name=name,
            task_type=task_type,
            steps=steps,
            code_snippet=code_snippet,
            tool_schema=tool_schema,
            success_rate=success_rate,
            metadata=metadata,
        )

    async def search_skills(
        self,
        task_description: str,
        task_type: str | None = None,
        min_success_rate: float = 0.7,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Search for skills relevant to a task using vector similarity.

        Delegates to :class:`ProceduralMemory.search_procedures`.

        Args:
            task_description: Natural-language description of the task
            task_type: Optional task-type filter
            min_success_rate: Minimum success rate threshold
            limit: Maximum results

        Returns:
            List of matching skill documents
        """
        await self._ensure_ready()
        proc = self._get_procedural_memory()
        return await proc.search_procedures(
            task_description=task_description,
            task_type=task_type,
            min_success_rate=min_success_rate,
            limit=limit,
        )

    async def get_skill(self, name: str) -> dict[str, Any] | None:
        """
        Retrieve a single skill by name.

        Delegates to :class:`ProceduralMemory.get_procedure`.

        Args:
            name: Skill name

        Returns:
            Skill document or ``None`` if not found / inactive.
        """
        await self._ensure_ready()
        proc = self._get_procedural_memory()
        return await proc.get_procedure(name)


# Public alias — simpler name for the multi-tier memory system.
# "CognitiveMemory" remains importable for backwards compatibility.
MultiTierMemory = CognitiveMemory
