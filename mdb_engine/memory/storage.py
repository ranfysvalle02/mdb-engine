"""
Storage Mixin for CognitiveMemoryService.

Provides CRUD operations (get, get_all, update, delete, delete_all),
graph link helpers (add_memory_with_links, mark_contradiction), and
timeline operations (fork_timeline, get_current_timeline, switch_timeline).
"""

import logging
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.errors import OperationFailure, PyMongoError

from ..core.validation import validate_user_id

logger = logging.getLogger(__name__)


class StorageMixin:
    """Mixin that provides CRUD, graph-link, and timeline methods for CognitiveMemoryService."""

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def get(self, memory_id: str, user_id: str | None = None, **kwargs) -> dict[str, Any] | None:
        """Retrieve a single memory by ID."""
        user_id = validate_user_id(user_id, allow_none=True)
        try:
            query: dict[str, Any] = {"_id": ObjectId(memory_id)}
            if user_id:
                query["user_id"] = str(user_id)

            doc = await self.collection.find_one(query)
            if not doc:
                return None

            result: dict[str, Any] = {
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
        except (InvalidId, PyMongoError) as e:
            logger.warning("Failed to get memory %s: %s", memory_id, e)
            return None

    async def get_all(
        self,
        user_id: str | None = None,
        limit: int = 100,
        filters: dict[str, Any] | None = None,
        **kwargs,
    ) -> list[dict[str, Any]]:
        """Retrieve all memories with basic filtering.

        Raises:
            InputValidationError: If user_id is not provided.
        """
        user_id = validate_user_id(user_id, allow_none=False)
        query: dict[str, Any] = {"user_id": str(user_id)}

        if filters:
            for k, v in filters.items():
                if k == "metadata" and isinstance(v, dict):
                    for mk, mv in v.items():
                        query[f"metadata.{mk}"] = mv
                elif k != "user_id":  # Protect user_id from filter override
                    query[k] = v

        try:
            logger.debug(
                f"[Memory get_all] Querying collection: {self.collection_name}, "
                f"db: {self.db_name}, user_id={user_id}"
            )
            total_count = await self.collection.count_documents({})
            user_count = await self.collection.count_documents(query) if query else total_count
            logger.info(f"[Memory get_all] Collection stats: total={total_count}, " f"matching query={user_count}")

            cursor = self.collection.find(query).sort("created_at", -1).limit(limit)
            docs = await cursor.to_list(length=limit)
            results: list[dict[str, Any]] = []
            for doc in docs:
                result: dict[str, Any] = {
                    "id": str(doc["_id"]),
                    "memory": doc.get("text", ""),
                    "metadata": doc.get("metadata", {}),
                    "user_id": doc.get("user_id"),
                    "created_at": doc.get("created_at").isoformat() if doc.get("created_at") else None,
                }

                # Add cognitive fields if present
                if self.enable_cognitive and "importance" in doc:
                    result["importance"] = doc.get("importance", 0.5)
                    result["access_count"] = doc.get("access_count", 0)

                results.append(result)

            logger.info(
                f"[Memory get_all] Returning {len(results)} memories from " f"collection {self.collection_name}"
            )
            return results
        except (PyMongoError, OperationFailure, ValueError, TypeError, RuntimeError) as e:
            logger.error(f"[Memory get_all] Failed: {e}", exc_info=True)
            return []

    async def update(
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
        from .cognitive import CognitiveMemoryServiceError

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
            query: dict[str, Any] = {"_id": ObjectId(memory_id)}
            if user_id:
                query["user_id"] = str(user_id)

            existing = await self.collection.find_one(query)
            if not existing:
                logger.warning(f"Memory {memory_id} not found for update.")
                return None

            update_fields: dict[str, Any] = {"updated_at": datetime.now(timezone.utc)}

            # 1. Handle Text Update (Requires Re-Embedding)
            if new_text and new_text.strip() != existing.get("text"):
                logger.info(f"Updating text and regenerating embedding for {memory_id}")
                update_fields["text"] = new_text.strip()
                update_fields["embedding"] = await self._get_embedding(new_text.strip())

                # Re-assess importance if cognitive enabled
                if self.enable_cognitive:
                    update_fields["importance"] = await self._assess_importance(new_text.strip())

            # 2. Handle Metadata Update
            if metadata:
                # Merge with existing metadata
                current_meta = existing.get("metadata", {})
                current_meta.update(metadata)
                update_fields["metadata"] = current_meta

            await self.collection.update_one(query, {"$set": update_fields})

            # Return fresh doc
            return await self.get(memory_id, user_id)

        except (PyMongoError, OperationFailure, InvalidId) as e:
            logger.exception("Update failed")
            raise CognitiveMemoryServiceError(f"Update operation failed: {e}") from e

    async def delete(self, memory_id: str, user_id: str | None = None, **kwargs) -> bool:
        """Delete a single memory.

        Raises:
            InputValidationError: If user_id is not provided.
        """
        user_id = validate_user_id(user_id, allow_none=False)
        try:
            query: dict[str, Any] = {"_id": ObjectId(memory_id), "user_id": str(user_id)}
            result = await self.collection.delete_one(query)
            deleted = result.deleted_count > 0

            # Clean up graph nodes linked to this memory (best-effort)
            if deleted and getattr(self, "_graph_service", None):
                try:
                    await self._graph_service.remove_memory_references(memory_id)
                except (AttributeError, NotImplementedError):
                    pass  # Graph service doesn't support cleanup
                except (PyMongoError, ConnectionError, TimeoutError, ValueError, RuntimeError) as ge:
                    logger.warning(f"[Graph Cleanup] Non-fatal error on delete: {ge}")

            return deleted
        except (PyMongoError, OperationFailure, InvalidId) as e:
            logger.warning("Failed to delete memory %s: %s", memory_id, e)
            return False

    async def delete_all(self, user_id: str | None = None, hard_delete: bool = ..., **kwargs) -> bool:
        """
        Delete all memories for a user.

        Args:
            user_id: User ID whose memories should be deleted
            hard_delete: REQUIRED - If True, permanently remove all memories.
                         If False, soft-delete by marking as deleted (for legal retention).
                         Must be explicitly specified - no default for safety.

        Returns:
            True if deletion was successful, False otherwise

        Raises:
            TypeError: If hard_delete is not explicitly provided
        """
        from .cognitive import _REQUIRED_HARD_DELETE

        # Require explicit hard_delete parameter - no default for safety
        if hard_delete is _REQUIRED_HARD_DELETE:
            raise TypeError(
                "delete_all() requires explicit 'hard_delete' parameter. "
                "Specify hard_delete=True for GDPR-compliant deletion or "
                "hard_delete=False for legal retention."
            )
        user_id = validate_user_id(user_id, allow_none=False)

        try:
            query: dict[str, Any] = {"user_id": str(user_id)}

            if hard_delete:
                # Collect memory IDs before deletion for graph cleanup
                graph_service = getattr(self, "_graph_service", None)
                memory_ids: list[str] = []
                if graph_service:
                    cursor = self.collection.find(query, {"_id": 1})
                    memory_ids = [str(doc["_id"]) async for doc in cursor]

                # GDPR-compliant hard delete - permanently remove all memories
                result = await self.collection.delete_many(query)
                logger.info(f"[GDPR] Hard deleted {result.deleted_count} memories for user {user_id}")

                # Clean up graph nodes linked to deleted memories (best-effort)
                if graph_service and memory_ids:
                    for mid in memory_ids:
                        try:
                            await graph_service.remove_memory_references(mid)
                        except (AttributeError, NotImplementedError):
                            break  # Service doesn't support it
                        except (PyMongoError, ConnectionError, TimeoutError, ValueError, RuntimeError) as ge:
                            logger.warning(f"[Graph Cleanup] Non-fatal error on delete_all: {ge}")

                return result.deleted_count > 0
            else:
                # Soft delete - mark as deleted but preserve for legal retention
                now = datetime.now(timezone.utc)
                result = await self.collection.update_many(
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
                    f"[GDPR] Soft deleted {result.modified_count} memories for user {user_id} "
                    f"(preserved for legal retention)"
                )
                return result.modified_count > 0
        except (PyMongoError, OperationFailure):
            logger.exception(f"[GDPR] Error deleting memories for user {user_id}")
            return False

    # ------------------------------------------------------------------
    # Graph link operations
    # ------------------------------------------------------------------

    async def add_memory_with_links(
        self,
        content: str,
        user_id: str,
        derived_from: list[str] | None = None,
        contradicts: list[str] | None = None,
        timeline_id: str = "root",
        confidence: float = 0.8,
        **kwargs,
    ) -> dict[str, Any]:
        """
        Add memory with explicit graph links.

        This method allows creating memories with explicit derivation and contradiction
        links for the Cognitive Operating System's graph-based memory structure.

        Args:
            content: Memory content text
            user_id: User ID
            derived_from: List of memory IDs this memory is derived from
            contradicts: List of memory IDs this memory contradicts
            timeline_id: Timeline ID (default: "root")
            confidence: Confidence score (default: 0.8)
            **kwargs: Additional parameters for inject() method

        Returns:
            Created memory document
        """
        return await self.inject(
            memory=content,
            user_id=user_id,
            derived_from=derived_from,
            contradicts=contradicts,
            timeline_id=timeline_id,
            confidence=confidence,
            **kwargs,
        )

    async def mark_contradiction(
        self,
        new_memory_id: str,
        contradicted_memory_id: str,
        user_id: str,
    ) -> None:
        """
        Mark a memory as contradicting another (Bayesian update).

        When new information contradicts existing memory:
        1. Update old memory: set deprecated=True, lower confidence to 0.1
        2. Update new memory: add contradicted memory ID to contradicts list
        3. Create bidirectional graph links

        This preserves audit trail while allowing the system to answer:
        "You used to like pizza (2024), but as of 2026, you dislike it."

        Args:
            new_memory_id: ID of the new memory that contradicts the old one
            contradicted_memory_id: ID of the old memory being contradicted
            user_id: User ID for security scoping
        """
        from .cognitive import CognitiveMemoryServiceError

        try:
            now = datetime.now(timezone.utc)

            # 1. Update old memory: mark as deprecated and lower confidence
            await self.collection.update_one(
                {
                    "_id": ObjectId(contradicted_memory_id),
                    "user_id": str(user_id),
                },
                {
                    "$set": {
                        "graph_links.deprecated": True,
                        "metadata.confidence": 0.1,
                        "updated_at": now,
                    },
                    "$addToSet": {
                        "graph_links.contradicts": str(new_memory_id),
                    },
                },
            )

            # 2. Update new memory: add to contradicts list
            await self.collection.update_one(
                {
                    "_id": ObjectId(new_memory_id),
                    "user_id": str(user_id),
                },
                {
                    "$addToSet": {
                        "graph_links.contradicts": str(contradicted_memory_id),
                    },
                    "$set": {
                        "updated_at": now,
                    },
                },
            )

            logger.info(f"[Contradiction] Marked memory {contradicted_memory_id} as contradicted by {new_memory_id}")

        except (PyMongoError, OperationFailure, InvalidId) as e:
            logger.exception(f"Failed to mark contradiction: {e}")
            raise CognitiveMemoryServiceError(f"Failed to mark contradiction: {e}") from e

    # ------------------------------------------------------------------
    # Timeline operations
    # ------------------------------------------------------------------

    async def fork_timeline(
        self,
        current_timeline: str,
        new_name: str,
        user_id: str,
    ) -> str:
        """
        Create a new timeline branching from the current one.

        Enables multiverse/counterfactual reasoning by creating parallel memory timelines.
        New memories can be added to this branch without affecting the parent timeline.

        Args:
            current_timeline: Current timeline ID to fork from
            new_name: Display name for the new timeline
            user_id: User ID for the new timeline

        Returns:
            New timeline ID
        """
        from .cognitive import CognitiveMemoryServiceError

        if not self.timeline_service:
            raise CognitiveMemoryServiceError("Timeline service not initialized")

        return await self.timeline_service.fork_timeline(
            current_timeline=current_timeline,
            new_name=new_name,
            user_id=user_id,
            app_slug=self.app_slug,
        )

    async def get_current_timeline(self, user_id: str) -> str:
        """
        Get user's current active timeline.

        Delegates to ``TimelineService.get_active_timeline()`` which persists the
        choice in MongoDB. Falls back to ``"root"`` if the timeline service is not
        available.

        Args:
            user_id: User ID

        Returns:
            Current timeline ID (default: "root")
        """
        if self.timeline_service:
            return await self.timeline_service.get_active_timeline(user_id)
        return "root"

    async def switch_timeline(self, user_id: str, timeline_id: str) -> None:
        """
        Switch user's active timeline context.

        Persists the active timeline for the user via ``TimelineService``.
        Raises if the timeline service is not available.

        Args:
            user_id: User ID
            timeline_id: Timeline ID to switch to

        Raises:
            CognitiveMemoryServiceError: If timeline service is not initialized
        """
        from .cognitive import CognitiveMemoryServiceError

        if not self.timeline_service:
            raise CognitiveMemoryServiceError("Timeline service not initialized")

        await self.timeline_service.set_active_timeline(user_id, timeline_id)
        logger.info(f"[Timeline] User {user_id} switched to timeline {timeline_id}")
