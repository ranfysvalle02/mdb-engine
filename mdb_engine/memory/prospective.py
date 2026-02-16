"""
Prospective Memory System
"Remember to do X when Y happens" -- intention-based triggers.

This module implements Prospective Memory, an AI superpower that enables the system
to set and check context-dependent triggers. Unlike retrospective memory (remembering
past events), prospective memory is about remembering future intentions.

Examples:
- "When the user mentions the project deadline, remind about the risk assessment"
- "If the user asks about pricing, suggest the enterprise plan"
- "When deployment is mentioned, warn about the staging environment issue"

Triggers are stored with condition embeddings and checked against every incoming
query via vector similarity. When a trigger fires, the action is surfaced to the
orchestrator for inclusion in the system prompt.
"""

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ..core.validation import validate_user_id
from .base import MemoryServiceError
from .embedding import cosine_similarity

if TYPE_CHECKING:
    from mdb_engine.database.scoped_wrapper import ScopedCollectionWrapper
    from mdb_engine.embeddings.service import EmbeddingService

try:
    from bson import ObjectId
    from bson.errors import InvalidId
    from pymongo.errors import OperationFailure, PyMongoError
except ImportError:
    raise ImportError("pip install pymongo") from None

logger = logging.getLogger(__name__)


class ProspectiveMemoryError(MemoryServiceError):
    """Base exception for Prospective Memory errors."""

    pass


class ProspectiveMemory:
    """
    Manages intention-based memory triggers ("remember to do X when Y happens").

    Prospective memory stores future intentions as condition-action pairs with
    vector embeddings on the condition. When incoming context matches a condition
    via vector similarity, the trigger fires and the action is surfaced.

    Example:
        ```python
        from mdb_engine.memory.prospective import ProspectiveMemory

        prospective = ProspectiveMemory(
            collection=prospective_collection,
            embedding_model="text-embedding-3-small"
        )

        # Set a trigger
        trigger_id = prospective.set_trigger(
            condition="user mentions project deadline or timeline",
            action="Remind the user about the pending risk assessment for Project Alpha",
            user_id="user123"
        )

        # Check triggers against current context
        fired = await prospective.check_triggers(
            current_context="When is the project deadline?",
            user_id="user123"
        )
        # Returns: [{"trigger_id": "...", "action": "Remind the user about...", "similarity": 0.91}]

        # Mark trigger as fired
        prospective.mark_triggered(trigger_id)
        ```
    """

    def __init__(
        self,
        collection: "ScopedCollectionWrapper",
        embedding_model: str = "text-embedding-3-small",
        embedding_dims: int = 1536,
        embedding_service: "EmbeddingService | None" = None,
    ):
        """
        Initialize ProspectiveMemory manager.

        Args:
            collection: MongoDB collection (ScopedCollectionWrapper) for prospective triggers
            embedding_model: Embedding model for condition vectors (default: text-embedding-3-small)
            embedding_dims: Embedding dimensions (default: 1536)
            embedding_service: Optional EmbeddingService for vector generation.
                When provided, takes priority over direct LiteLLM calls.
        """
        self.collection = collection
        self.embedding_model = embedding_model
        self.embedding_dims = embedding_dims
        self._embedding_service = embedding_service
        self._index_ensured = False

    async def _ensure_indexes(self) -> None:
        """Create necessary indexes for prospective memory (async, idempotent)."""
        if self._index_ensured:
            return

        try:
            await self.collection.create_index([("user_id", 1), ("triggered", 1), ("is_active", 1)])
            await self.collection.create_index("created_at")
            self._index_ensured = True
            logger.info("Prospective memory indexes ensured")
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Index creation warning (may already exist): {e}")

    async def _get_embedding(self, text: str) -> list[float]:
        """
        Generate embedding vector for text (async) via the injected embedding service.

        Args:
            text: Text to embed

        Returns:
            Embedding vector as list of floats
        """
        if self._embedding_service is None:
            raise ProspectiveMemoryError(
                "No embedding service available. " "Inject an EmbeddingService via the constructor."
            )

        try:
            vectors = await self._embedding_service.embed([text])
            return vectors[0] if vectors else []
        except (ConnectionError, TimeoutError, ValueError, RuntimeError) as e:
            logger.warning(f"Failed to generate embedding: {e}")
            return []

    async def set_trigger(
        self,
        condition: str,
        action: str,
        user_id: str,
        metadata: dict[str, Any] | None = None,
        one_shot: bool = True,
    ) -> str:
        """
        Set a prospective memory trigger.

        Args:
            condition: Natural language description of when to trigger
                       (e.g., "user mentions project deadline")
            action: What to do when triggered
                    (e.g., "Remind about the risk assessment")
            user_id: User ID for scoping
            metadata: Optional metadata dictionary
            one_shot: If True, trigger fires once and is deactivated (default: True)

        Returns:
            Trigger document ID

        Example:
            ```python
            trigger_id = await prospective.set_trigger(
                condition="user asks about pricing or costs",
                action="Suggest the enterprise plan with volume discounts",
                user_id="user123"
            )
            ```
        """
        if not condition or not condition.strip():
            raise ProspectiveMemoryError("Condition cannot be empty")
        if not action or not action.strip():
            raise ProspectiveMemoryError("Action cannot be empty")

        user_id = validate_user_id(user_id, allow_none=False)

        await self._ensure_indexes()

        try:
            # Generate embedding for the condition
            condition_embedding = await self._get_embedding(condition)
            if not condition_embedding:
                raise ProspectiveMemoryError("Failed to generate condition embedding")

            trigger_doc: dict[str, Any] = {
                "condition": condition.strip(),
                "action": action.strip(),
                "condition_embedding": condition_embedding,
                "user_id": str(user_id),
                "triggered": False,
                "trigger_count": 0,
                "is_active": True,
                "one_shot": one_shot,
                "created_at": datetime.now(timezone.utc),
            }

            if metadata:
                trigger_doc["metadata"] = metadata

            result = await self.collection.insert_one(trigger_doc)
            trigger_id = str(result.inserted_id)

            logger.info(
                f"Prospective trigger set: '{condition[:50]}...' -> '{action[:50]}...' "
                f"(id={trigger_id}, one_shot={one_shot})"
            )

            return trigger_id

        except (PyMongoError, OperationFailure) as e:
            logger.error(f"Failed to set prospective trigger: {e}", exc_info=True)
            raise ProspectiveMemoryError(f"Failed to set trigger: {e}") from e

    async def check_triggers(
        self,
        current_context: str,
        user_id: str,
        threshold: float = 0.85,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Check if any prospective memory triggers should fire for the current context.

        Uses vector similarity between the current context and stored condition
        embeddings to determine if any triggers match.

        Args:
            current_context: Current user query or context text
            user_id: User ID for scoping
            threshold: Minimum similarity to fire a trigger (default: 0.85)
            limit: Maximum number of triggers to return (default: 5)

        Returns:
            List of fired trigger dicts with keys:
            - trigger_id: Document ID
            - action: The action to take
            - condition: The original condition
            - similarity: How closely the context matched

        Example:
            ```python
            fired = await prospective.check_triggers(
                current_context="What's the timeline for the project?",
                user_id="user123",
                threshold=0.85
            )
            for trigger in fired:
                print(f"FIRE: {trigger['action']} (similarity={trigger['similarity']:.2f})")
            ```
        """
        if not current_context or not current_context.strip():
            return []

        user_id = validate_user_id(user_id, allow_none=False)

        await self._ensure_indexes()

        try:
            # Generate embedding for current context
            context_embedding = await self._get_embedding(current_context)
            if not context_embedding:
                return []

            # Try vector search first
            try:
                pipeline = [
                    {
                        "$vectorSearch": {
                            "index": "prospective_vector_index",
                            "path": "condition_embedding",
                            "queryVector": context_embedding,
                            "numCandidates": limit * 20,
                            "limit": limit * 2,
                            "filter": {
                                "user_id": str(user_id),
                                "is_active": True,
                                "triggered": False,
                            },
                        }
                    },
                    {"$addFields": {"similarity": {"$meta": "vectorSearchScore"}}},
                    {"$match": {"similarity": {"$gte": threshold}}},
                    {"$limit": limit},
                ]

                cursor = self.collection.aggregate(pipeline)
                docs = await cursor.to_list(length=limit)

            except (PyMongoError, OperationFailure) as e:
                # Fallback: fetch all active triggers and compute similarity manually
                logger.debug(f"Vector search not available for prospective triggers, using fallback: {e}")
                docs = await self._fallback_check(context_embedding, user_id, threshold, limit)

            # Format results
            fired_triggers: list[dict[str, Any]] = []
            for doc in docs:
                similarity = doc.get("similarity", 0.0)
                fired_triggers.append(
                    {
                        "trigger_id": str(doc["_id"]),
                        "action": doc.get("action", ""),
                        "condition": doc.get("condition", ""),
                        "similarity": similarity,
                        "metadata": doc.get("metadata", {}),
                        "created_at": doc.get("created_at"),
                    }
                )

            if fired_triggers:
                logger.info(f"Prospective triggers fired: {len(fired_triggers)} " f"for user {user_id}")

            return fired_triggers

        except (ConnectionError, TimeoutError, ValueError, RuntimeError) as e:
            logger.warning(f"Failed to check prospective triggers: {e}", exc_info=True)
            return []

    async def _fallback_check(
        self,
        context_embedding: list[float],
        user_id: str,
        threshold: float,
        limit: int,
    ) -> list[dict[str, Any]]:
        """
        Fallback trigger check using manual cosine similarity.

        Used when vector search index is not available.

        Args:
            context_embedding: Embedding of current context
            user_id: User ID for filtering
            threshold: Minimum similarity threshold
            limit: Max results

        Returns:
            List of matching trigger documents with similarity field added
        """
        query = {
            "user_id": str(user_id),
            "is_active": True,
            "triggered": False,
        }

        cursor = self.collection.find(query)
        all_triggers = await cursor.to_list(length=100)  # Cap at 100

        results: list[dict[str, Any]] = []
        for trigger in all_triggers:
            stored_embedding = trigger.get("condition_embedding", [])
            if not stored_embedding or len(stored_embedding) != len(context_embedding):
                continue

            similarity = cosine_similarity(context_embedding, stored_embedding)

            if similarity >= threshold:
                trigger["similarity"] = similarity
                results.append(trigger)

        # Sort by similarity descending
        results.sort(key=lambda x: x.get("similarity", 0), reverse=True)
        return results[:limit]

    async def mark_triggered(
        self,
        trigger_id: str,
    ) -> bool:
        """
        Mark a prospective trigger as fired.

        For one-shot triggers, this deactivates them. For recurring triggers,
        this increments the trigger count.

        Args:
            trigger_id: Trigger document ID

        Returns:
            True if update was successful
        """
        try:
            trigger = await self.collection.find_one({"_id": ObjectId(trigger_id)})
            if not trigger:
                return False

            update: dict[str, Any] = {
                "$set": {
                    "last_triggered": datetime.now(timezone.utc),
                },
                "$inc": {"trigger_count": 1},
            }

            # One-shot triggers get deactivated
            if trigger.get("one_shot", True):
                update["$set"]["triggered"] = True
                update["$set"]["is_active"] = False

            result = await self.collection.update_one({"_id": ObjectId(trigger_id)}, update)

            if result.modified_count > 0:
                logger.info(f"Prospective trigger fired: {trigger_id}")
                return True
            return False

        except (PyMongoError, OperationFailure, InvalidId) as e:
            logger.warning(f"Failed to mark trigger: {e}")
            return False

    async def get_active_triggers(
        self,
        user_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Get all active (unfired) triggers for a user.

        Args:
            user_id: User ID
            limit: Maximum number of triggers to return

        Returns:
            List of active trigger documents
        """
        user_id = validate_user_id(user_id, allow_none=False)

        try:
            await self._ensure_indexes()

            query = {
                "user_id": str(user_id),
                "is_active": True,
                "triggered": False,
            }

            cursor = (
                self.collection.find(
                    query,
                    {"condition_embedding": 0},  # Exclude large embedding from response
                )
                .sort("created_at", -1)
                .limit(limit)
            )

            triggers = await cursor.to_list(length=limit)

            return [
                {
                    "trigger_id": str(t["_id"]),
                    "condition": t.get("condition", ""),
                    "action": t.get("action", ""),
                    "one_shot": t.get("one_shot", True),
                    "trigger_count": t.get("trigger_count", 0),
                    "created_at": t.get("created_at"),
                    "metadata": t.get("metadata", {}),
                }
                for t in triggers
            ]

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to get active triggers: {e}")
            return []

    async def deactivate_trigger(
        self,
        trigger_id: str,
    ) -> bool:
        """
        Manually deactivate a trigger.

        Args:
            trigger_id: Trigger document ID

        Returns:
            True if deactivation was successful
        """
        try:
            result = await self.collection.update_one(
                {"_id": ObjectId(trigger_id)},
                {"$set": {"is_active": False, "deactivated_at": datetime.now(timezone.utc)}},
            )
            if result.modified_count > 0:
                logger.info(f"Prospective trigger deactivated: {trigger_id}")
                return True
            return False

        except (PyMongoError, OperationFailure, InvalidId) as e:
            logger.warning(f"Failed to deactivate trigger: {e}")
            return False
