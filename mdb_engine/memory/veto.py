"""
Memory Veto System
Explicit "never share" flags for memories.

This module implements memory vetoes - a way for users to mark memories
as "never share, even abstractly". This ensures user control over
what gets promoted to family or system memory.

Key principle: "Users control their privacy boundaries"
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

from ..core.validation import validate_user_id
from .base import MemoryServiceError

logger = logging.getLogger(__name__)


class MemoryVetoError(MemoryServiceError):
    """Base exception for Memory Veto errors."""

    pass


class MemoryVeto:
    """
    Manages memory vetoes (never share, even abstractly).

    Memory vetoes allow users to explicitly mark memories as private,
    preventing them from being promoted to family or system memory.

    Example:
        ```python
        from mdb_engine.memory.veto import MemoryVeto

        veto = MemoryVeto(collection=veto_collection)

        # Add a veto
        await veto.add_veto(
            memory_id="memory123",
            user_id="user123",
            reason="Contains sensitive personal information"
        )

        # Check if a memory is vetoed
        if await veto.check_veto("memory123", "user123"):
            # Don't promote this memory
            pass
        ```
    """

    def __init__(self, collection: Any):
        """
        Initialize MemoryVeto manager.

        Args:
            collection: MongoDB collection for memory vetoes
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
        """Create necessary indexes for memory vetoes."""
        try:
            await _maybe_await(self.collection.create_index([("memory_id", 1), ("user_id", 1)], unique=True))
            await _maybe_await(self.collection.create_index("user_id"))
            await _maybe_await(self.collection.create_index("memory_id"))
            logger.info("Memory veto indexes created")
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Index creation warning (may already exist): {e}")

    async def add_veto(
        self,
        memory_id: str,
        user_id: str,
        reason: str | None = None,
        scope: str = "all",
    ) -> dict[str, Any]:
        """
        Add a veto to a memory (mark as "never share").

        Args:
            memory_id: Memory document ID to veto
            user_id: User ID who owns the memory
            reason: Optional reason for the veto
            scope: Veto scope ("all", "family", "system")
                  "all" = never share in any context
                  "family" = don't promote to family memory
                  "system" = don't promote to system memory

        Returns:
            Created veto document

        Example:
            ```python
            await veto.add_veto(
                memory_id="memory123",
                user_id="user123",
                reason="Contains sensitive personal information",
                scope="all"
            )
            ```
        """
        if not memory_id:
            raise MemoryVetoError("Memory ID is required")
        user_id = validate_user_id(user_id, allow_none=False)

        await self._ensure_ready()

        try:
            veto_doc = {
                "memory_id": str(memory_id),
                "user_id": str(user_id),
                "scope": scope,
                "created_at": datetime.now(timezone.utc),
            }

            if reason:
                veto_doc["reason"] = reason

            # Upsert (update if exists, insert if new)
            await _maybe_await(
                self.collection.update_one(
                    {"memory_id": str(memory_id), "user_id": str(user_id)},
                    {"$set": veto_doc, "$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
                    upsert=True,
                )
            )

            logger.info(f"Memory veto added: memory_id={memory_id}, " f"user_id={user_id}, scope={scope}")

            return veto_doc

        except (PyMongoError, OperationFailure) as e:
            logger.error(f"Failed to add veto: {e}", exc_info=True)
            raise MemoryVetoError(f"Failed to add veto: {e}") from e

    async def check_veto(
        self,
        memory_id: str,
        user_id: str,
        target_scope: str = "family",
    ) -> bool:
        """
        Check if a memory is vetoed for a specific scope.

        Args:
            memory_id: Memory document ID to check
            user_id: User ID who owns the memory
            target_scope: Target scope to check ("family", "system", "all")

        Returns:
            True if memory is vetoed for the target scope, False otherwise

        Example:
            ```python
            if await veto.check_veto("memory123", "user123", "family"):
                # Don't promote to family memory
                return
            ```
        """
        user_id = validate_user_id(user_id, allow_none=False)
        await self._ensure_ready()
        try:
            # Check for "all" scope veto (blocks everything)
            all_veto = await _maybe_await(
                self.collection.find_one(
                    {
                        "memory_id": str(memory_id),
                        "user_id": str(user_id),
                        "scope": "all",
                    }
                )
            )
            if all_veto:
                return True

            # Check for specific scope veto
            if target_scope != "all":
                scope_veto = await _maybe_await(
                    self.collection.find_one(
                        {
                            "memory_id": str(memory_id),
                            "user_id": str(user_id),
                            "scope": target_scope,
                        }
                    )
                )
                if scope_veto:
                    return True

            return False

        except (PyMongoError, OperationFailure) as e:
            logger.error(f"Veto check failed, blocking memory for safety: {e}", exc_info=True)
            return True  # Fail closed: assume vetoed if we can't verify

    async def remove_veto(
        self,
        memory_id: str,
        user_id: str,
        scope: str | None = None,
    ) -> bool:
        """
        Remove a veto from a memory.

        Args:
            memory_id: Memory document ID
            user_id: User ID who owns the memory
            scope: Optional scope filter (if None, removes all vetoes for this memory)

        Returns:
            True if veto was removed, False otherwise

        Example:
            ```python
            await veto.remove_veto("memory123", "user123", scope="family")
            ```
        """
        user_id = validate_user_id(user_id, allow_none=False)
        await self._ensure_ready()
        try:
            query = {
                "memory_id": str(memory_id),
                "user_id": str(user_id),
            }
            if scope:
                query["scope"] = scope

            result = await _maybe_await(self.collection.delete_many(query))

            if result.deleted_count > 0:
                logger.info(
                    f"Memory veto removed: memory_id={memory_id}, " f"user_id={user_id}, scope={scope or 'all'}"
                )
                return True
            return False

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to remove veto: {e}")
            return False

    async def get_user_vetoes(
        self,
        user_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Get all vetoes for a user.

        Args:
            user_id: User ID
            limit: Maximum number of results

        Returns:
            List of veto documents
        """
        user_id = validate_user_id(user_id, allow_none=False)
        await self._ensure_ready()
        try:
            cursor = self.collection.find({"user_id": str(user_id)}).sort("created_at", -1).limit(limit)
            return await _cursor_to_list(cursor, limit)

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to get user vetoes: {e}")
            return []

    async def get_veto_stats(self, user_id: str | None = None) -> dict[str, Any]:
        """
        Get statistics about memory vetoes.

        Args:
            user_id: Optional user ID filter

        Returns:
            Dictionary with veto statistics
        """
        user_id = validate_user_id(user_id, allow_none=True)
        await self._ensure_ready()
        try:
            query = {}
            if user_id:
                query["user_id"] = str(user_id)

            total = await _maybe_await(self.collection.count_documents(query))
            all_scope = await _maybe_await(self.collection.count_documents({**query, "scope": "all"}))
            family_scope = await _maybe_await(self.collection.count_documents({**query, "scope": "family"}))
            system_scope = await _maybe_await(self.collection.count_documents({**query, "scope": "system"}))

            return {
                "total_vetoes": total,
                "all_scope_count": all_scope,
                "family_scope_count": family_scope,
                "system_scope_count": system_scope,
            }

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to get veto stats: {e}")
            return {"total_vetoes": 0, "error": str(e)}
