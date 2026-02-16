"""
Reflective Memory System
Meta-cognitive beliefs about the system itself.

This module implements Reflective Memory - an AI-only superpower that allows
the system to store thoughts about its own memories and behavior patterns.

Examples:
- "I tend to over-weight recent conversations"
- "This user changes preferences often"
- "This belief caused errors before"

This is where actual intelligence emerges - the system becomes self-aware.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from ._async_compat import cursor_to_list as _cursor_to_list
from ._async_compat import maybe_await as _maybe_await

try:
    from pymongo.errors import OperationFailure, PyMongoError

    PYMONGO_AVAILABLE = True
except ImportError:
    raise ImportError("pip install pymongo") from None

from .base import MemoryServiceError

logger = logging.getLogger(__name__)


class ReflectiveMemoryError(MemoryServiceError):
    """Base exception for Reflective Memory errors."""

    pass


class ReflectiveMemory:
    """
    Manages meta-cognitive beliefs about the system.

    Reflective memory stores thoughts about memories, enabling the system to:
    - Recognize its own biases
    - Learn from past mistakes
    - Adapt behavior based on self-awareness
    - Build trust through transparency

    Example:
        ```python
        from mdb_engine.memory.reflective import ReflectiveMemory

        reflective = ReflectiveMemory(collection=reflective_collection)

        # Store a reflection about system behavior
        await reflective.store_reflection(
            reflection="I tend to over-weight recent conversations",
            trigger="performance_review",
            confidence=0.7,
            scope="user",
            user_id="user123"
        )

        # Retrieve reflections for context
        reflections = await reflective.get_reflections(
            scope="user",
            user_id="user123",
            min_confidence=0.5
        )
        ```
    """

    def __init__(self, collection: Any):
        """
        Initialize ReflectiveMemory manager.

        Args:
            collection: MongoDB collection for reflective memory
        """
        self.collection = collection
        self._indexes_ensured = False

    async def _ensure_ready(self) -> None:
        """Lazily create indexes on first operation."""
        if self._indexes_ensured:
            return
        await self._ensure_indexes()
        self._indexes_ensured = True

    async def _ensure_indexes(self):
        """Create necessary indexes for reflective memory."""
        try:
            await _maybe_await(self.collection.create_index([("scope", 1), ("user_id", 1), ("confidence", -1)]))
            await _maybe_await(self.collection.create_index([("trigger", 1), ("created_at", -1)]))
            await _maybe_await(self.collection.create_index("created_at"))
            logger.info("Reflective memory indexes created")
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Index creation warning (may already exist): {e}")

    async def store_reflection(
        self,
        reflection: str,
        trigger: str,
        confidence: float,
        scope: str = "user",
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        group_id: str | None = None,
        bucket_id: str | None = None,
        bucket_type: str | None = None,
    ) -> dict[str, Any]:
        """
        Store a meta-cognitive reflection.

        Args:
            reflection: The reflection text (e.g., "I tend to over-weight recent conversations")
            trigger: What triggered this reflection (e.g., "performance_review", "error_analysis")
            confidence: Confidence in this reflection (0.0 to 1.0)
            scope: Memory scope ("user", "shared", "system")
            user_id: User ID if scope is "user"
            group_id: Generic group identifier if scope is "shared"
            metadata: Optional metadata dictionary
            bucket_id: Optional bucket ID for filtering
            bucket_type: Optional bucket type

        Returns:
            Created reflection document

        Example:
            ```python
            await reflective.store_reflection(
                reflection="I often over-trust recent data",
                trigger="error_analysis",
                confidence=0.75,
                scope="user",
                user_id="user123"
            )
            ```
        """
        if not reflection or not reflection.strip():
            raise ReflectiveMemoryError("Reflection text cannot be empty")

        if not (0.0 <= confidence <= 1.0):
            raise ReflectiveMemoryError("Confidence must be between 0.0 and 1.0")

        await self._ensure_ready()

        try:
            normalized_scope = "shared" if scope == "family" else scope

            reflection_doc = {
                "reflection": reflection.strip(),
                "trigger": trigger,
                "confidence": confidence,
                "scope": normalized_scope,
                "created_at": datetime.now(timezone.utc),
            }

            if user_id:
                reflection_doc["user_id"] = str(user_id)
            if group_id:
                reflection_doc["group_id"] = group_id

            # Add bucket info to metadata
            final_metadata = metadata or {}
            if bucket_id:
                final_metadata["bucket_id"] = bucket_id
            if bucket_type:
                final_metadata["bucket_type"] = bucket_type
            if bucket_id and "associated_bucket_id" not in final_metadata:
                final_metadata["associated_bucket_id"] = bucket_id

            if final_metadata:
                reflection_doc["metadata"] = final_metadata

            result = await _maybe_await(self.collection.insert_one(reflection_doc))
            reflection_doc["_id"] = result.inserted_id

            logger.info(f"Reflection stored: confidence={confidence:.2f}, scope={scope}")

            return reflection_doc

        except (PyMongoError, OperationFailure) as e:
            logger.error(f"Failed to store reflection: {e}", exc_info=True)
            raise ReflectiveMemoryError(f"Failed to store reflection: {e}") from e

    async def get_reflections(
        self,
        scope: str = "user",
        user_id: str | None = None,
        min_confidence: float = 0.5,
        limit: int = 10,
        trigger: str | None = None,
        group_id: str | None = None,
        bucket_id: str | None = None,
        bucket_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Retrieve reflections matching criteria.

        Args:
            scope: Memory scope to filter by ("user", "shared", "system")
            user_id: User ID if scope is "user"
            group_id: Generic group identifier if scope is "shared"
            min_confidence: Minimum confidence threshold (default: 0.5)
            limit: Maximum number of results (default: 10)
            trigger: Optional trigger filter
            bucket_id: Optional bucket ID to filter by
            bucket_type: Optional bucket type to filter by

        Returns:
            List of reflection documents, sorted by confidence (descending)

        Example:
            ```python
            reflections = await reflective.get_reflections(
                scope="user",
                user_id="user123",
                min_confidence=0.6,
                limit=5,
                bucket_id="category:CODE:user123"
            )
            ```
        """
        await self._ensure_ready()

        try:
            normalized_scope = "shared" if scope == "family" else scope

            query = {
                "scope": normalized_scope,
                "confidence": {"$gte": min_confidence},
            }

            if user_id:
                query["user_id"] = str(user_id)
            if group_id:
                query["group_id"] = group_id
            if trigger:
                query["trigger"] = trigger

            # Add bucket filtering
            if bucket_id:
                query["metadata.associated_bucket_id"] = bucket_id
            elif bucket_type:
                query["metadata.bucket_type"] = bucket_type

            cursor = self.collection.find(query).sort([("confidence", -1), ("created_at", -1)]).limit(limit)

            reflections = await _cursor_to_list(cursor, limit)
            logger.debug(f"Retrieved {len(reflections)} reflections (scope: {scope})")
            return reflections

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to get reflections: {e}")
            return []

    async def update_confidence(
        self,
        reflection_id: str,
        new_confidence: float,
        reason: str | None = None,
    ) -> bool:
        """
        Update confidence in a reflection.

        Useful when new evidence confirms or contradicts the reflection.

        Args:
            reflection_id: Reflection document ID
            new_confidence: New confidence value (0.0 to 1.0)
            reason: Optional reason for the update

        Returns:
            True if update was successful, False otherwise
        """
        if not (0.0 <= new_confidence <= 1.0):
            raise ReflectiveMemoryError("Confidence must be between 0.0 and 1.0")

        await self._ensure_ready()

        try:
            from bson import ObjectId

            update_doc = {
                "$set": {
                    "confidence": new_confidence,
                    "last_updated": datetime.now(timezone.utc),
                }
            }

            if reason:
                update_doc["$push"] = {
                    "confidence_history": {
                        "$each": [
                            {
                                "confidence": new_confidence,
                                "reason": reason,
                                "timestamp": datetime.now(timezone.utc),
                            }
                        ],
                        "$slice": -100,
                    }
                }

            result = await _maybe_await(self.collection.update_one({"_id": ObjectId(reflection_id)}, update_doc))

            if result.modified_count > 0:
                logger.info(f"Updated reflection confidence: {reflection_id} → {new_confidence:.2f}")
                return True
            return False

        except (PyMongoError, OperationFailure, ValueError) as e:
            logger.warning(f"Failed to update reflection confidence: {e}")
            return False

    async def get_reflection_stats(
        self,
        scope: str = "user",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Get statistics about reflections.

        Args:
            scope: Memory scope
            user_id: User ID if scope is "user"

        Returns:
            Dictionary with reflection statistics
        """
        await self._ensure_ready()

        try:
            query = {"scope": scope}
            if user_id:
                query["user_id"] = str(user_id)

            total = await _maybe_await(self.collection.count_documents(query))
            high_confidence = await _maybe_await(
                self.collection.count_documents({**query, "confidence": {"$gte": 0.7}})
            )

            # Get most common triggers
            pipeline = [
                {"$match": query},
                {"$group": {"_id": "$trigger", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
                {"$limit": 5},
            ]
            common_triggers = await _cursor_to_list(self.collection.aggregate(pipeline), 5)

            return {
                "total_reflections": total,
                "high_confidence_count": high_confidence,
                "high_confidence_percentage": (high_confidence / total * 100) if total > 0 else 0,
                "common_triggers": [{"trigger": t["_id"], "count": t["count"]} for t in common_triggers],
            }

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to get reflection stats: {e}")
            return {"total_reflections": 0, "error": str(e)}
