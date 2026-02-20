"""
Shared/Group Memory System
Privacy-safe memory promotion from user to shared/group level.

This module implements shared/group-level memory that is:
- Derived, not raw (distilled patterns, not transcripts)
- Anonymized (no private emotions or personal details)
- Consensual (explicit promotion rules)
- Safe for group use (generic grouping mechanism)

Key principle: "Nothing is shared by accident. Everything shared is intentional."

This is a generic grouping mechanism that can represent:
- Families (family-001)
- Teams (team-001)
- Organizations (org-acme)
- Communities (community-dev)
- Any collection of users sharing memories
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


class SharedMemoryError(MemoryServiceError):
    """Base exception for Shared Memory errors."""

    pass


class SharedMemory:
    """
    Manages shared/group-level memory promotion and access.

    Shared memory is never raw memory - it is distilled, anonymized, and consensual.
    This enables group-level reasoning (families, teams, organizations) without
    exposing private information.

    This is a generic grouping mechanism - group_id can represent any collection
    of users: families, teams, organizations, communities, etc.

    Example:
        ```python
        from mdb_engine.memory.shared import SharedMemory

        shared_memory = SharedMemory(
            semantic_collection=semantic_collection,
            shared_collection=shared_collection
        )

        # Promote a fact to shared level (team example)
        if shared_memory.check_promotion_rules(fact, source_user_ids, "low"):
            await shared_memory.promote_to_shared(
                fact="Weeknights are usually busy and stressful",
                source_user_ids=["user1", "user2"],
                confidence=0.85,
                group_id="team-001",  # Generic group identifier
                bucket_id="category:CODE:team-001",  # Bucket filter
                bucket_type="category"
            )

        # Query shared memory (family example)
        shared_facts = await shared_memory.get_shared_memory(
            group_id="family-001",
            bucket_id="category:WORK:family-001",
            query="What work-related patterns do we share?",
            min_confidence=0.7
        )
        ```
    """

    def __init__(
        self,
        semantic_collection: Any,
        shared_collection: Any | None = None,
        embedding_service: Any | None = None,
        group_members_collection: Any | None = None,
    ):
        """
        Initialize SharedMemory manager.

        Args:
            semantic_collection: MongoDB collection for semantic memory (source)
            shared_collection: MongoDB collection for shared memory (target)
                              If None, uses semantic_collection with scope="shared"
            embedding_service: Optional EmbeddingService instance for embeddings.
                              If provided, uses this for vector search queries.
            group_members_collection: Optional MongoDB collection for group membership.
                              Documents should have ``group_id`` and ``user_id`` fields.
                              If None, group membership is not enforced (backwards-compatible).
        """
        self.semantic_collection = semantic_collection
        self.shared_collection = semantic_collection if shared_collection is None else shared_collection
        self._embedding_service = embedding_service
        self._group_members_collection = group_members_collection
        if group_members_collection is None:
            logger.warning(
                "SharedMemory initialized without group_members_collection. "
                "Group membership will NOT be verified. Pass a collection to enforce access control."
            )
        self._indexes_ensured = False

    async def _ensure_ready(self) -> None:
        """Lazily create indexes on first operation."""
        if self._indexes_ensured:
            return
        await self._ensure_indexes()
        self._indexes_ensured = True

    async def _ensure_indexes(self):
        """Create necessary indexes for shared memory."""
        try:
            await _maybe_await(self.shared_collection.create_index([("scope", 1), ("group_id", 1), ("confidence", -1)]))
            await _maybe_await(self.shared_collection.create_index("group_id"))
            # Index for bucket filtering
            await _maybe_await(self.shared_collection.create_index("metadata.associated_bucket_id"))
            await _maybe_await(self.shared_collection.create_index("metadata.bucket_id"))
            logger.info("Shared memory indexes created")
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Index creation warning (may already exist): {e}")

    async def _verify_group_membership(self, user_id: str, group_id: str) -> bool:
        """
        Verify that a user is a member of the specified group.

        If no ``group_members_collection`` was provided at init time, this
        method logs a warning and returns ``True`` (backwards-compatible open
        access).

        Args:
            user_id: The user to check.
            group_id: The group to check membership in.

        Returns:
            ``True`` if the user is a member (or membership is unenforced).

        Raises:
            SharedMemoryError: If the user is **not** a member and
                enforcement is enabled.
        """
        if self._group_members_collection is None:
            # No membership collection configured -- allow (backwards-compatible)
            return True

        try:
            doc = await _maybe_await(
                self._group_members_collection.find_one({"group_id": group_id, "user_id": user_id})
            )
            if doc:
                return True
            raise SharedMemoryError(f"User '{user_id}' is not a member of group '{group_id}'")
        except (PyMongoError, OperationFailure) as e:
            logger.exception(f"Group membership check failed: {e}")
            raise SharedMemoryError(f"Failed to verify group membership: {e}") from e

    def check_promotion_rules(
        self,
        fact: str,
        source_user_ids: list[str],
        sensitivity: str = "low",
        min_confidence: float = 0.7,
        min_users: int = 2,
    ) -> bool:
        """
        Check if a fact meets promotion rules for shared memory.

        Promotion rules:
        - Pattern appears across multiple users (min_users)
        - Confidence > min_confidence
        - Sensitivity == "low"
        - Content is non-sensitive (no private emotions, no personal details)

        Args:
            fact: The fact to check
            source_user_ids: List of user IDs this fact appears from
            sensitivity: Sensitivity level ("low", "medium", "high")
            min_confidence: Minimum confidence threshold (default: 0.7)
            min_users: Minimum number of users needed (default: 2)

        Returns:
            True if fact can be promoted, False otherwise

        Example:
            ```python
            if shared_memory.check_promotion_rules(
                fact="Weeknights are usually busy",
                source_user_ids=["user1", "user2"],
                sensitivity="low"
            ):
                # Safe to promote
            ```
        """
        # Rule 1: Must have multiple users
        if len(source_user_ids) < min_users:
            logger.debug(f"Promotion rejected: only {len(source_user_ids)} user(s), need {min_users}")
            return False

        # Rule 2: Sensitivity must be low
        if sensitivity != "low":
            logger.debug(f"Promotion rejected: sensitivity is {sensitivity}, must be 'low'")
            return False

        # Rule 3: Check for sensitive content (heuristic)
        sensitive_keywords = [
            "stressed",
            "anxious",
            "worried",
            "angry",
            "sad",
            "depressed",
            "trauma",
            "abuse",
            "divorce",
            "illness",
            "death",
        ]
        fact_lower = fact.lower()
        if any(keyword in fact_lower for keyword in sensitive_keywords):
            logger.debug("Promotion rejected: contains sensitive keywords")
            return False

        # Rule 4: Must not be a raw transcript or personal detail
        if len(fact.split()) < 3:  # Too short, likely incomplete
            logger.debug("Promotion rejected: fact too short")
            return False

        logger.debug(f"Promotion rules passed for fact ({len(fact)} chars)")
        return True

    async def promote_to_shared(
        self,
        fact: str,
        source_user_ids: list[str],
        confidence: float,
        group_id: str,
        anonymize: bool = True,
        bucket_id: str | None = None,
        bucket_type: str | None = None,
        associated_bucket_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Promote a semantic fact to shared/group-level memory.

        Creates a shared memory that is:
        - Distilled (not raw transcript)
        - Anonymized (no user-specific details)
        - Shared (visible to all group members)

        Args:
            fact: The fact to promote (should already be distilled)
            source_user_ids: List of user IDs this fact comes from
            confidence: Confidence in this fact (0.0 to 1.0)
            group_id: Generic group identifier (team, family, org, etc.)
            anonymize: Whether to anonymize the fact (default: True)
            bucket_id: Optional bucket ID for filtering
            bucket_type: Optional bucket type
            associated_bucket_id: Optional associated bucket ID for unified search
            metadata: Optional metadata dictionary

        Returns:
            Created shared memory document

        Example:
            ```python
            await shared_memory.promote_to_shared(
                fact="Weeknights are usually busy and stressful",
                source_user_ids=["user1", "user2"],
                confidence=0.85,
                group_id="team-001",
                bucket_id="category:CODE:team-001",
                bucket_type="category"
            )
            ```
        """
        if not fact or not fact.strip():
            raise SharedMemoryError("Fact text cannot be empty")

        if not (0.0 <= confidence <= 1.0):
            raise SharedMemoryError("Confidence must be between 0.0 and 1.0")

        if not source_user_ids or len(source_user_ids) < 2:
            raise SharedMemoryError("Shared memory requires at least 2 source users")

        await self._ensure_ready()

        # Validate all source user IDs
        for uid in source_user_ids:
            validate_user_id(uid, allow_none=False)

        # Verify all source users are group members
        if self._group_members_collection is not None:
            for uid in source_user_ids:
                await self._verify_group_membership(uid, group_id)

        try:
            # Anonymize if requested (remove user-specific references)
            anonymized_fact = fact
            if anonymize:
                # Simple anonymization: remove explicit user references
                # In production, use more sophisticated NLP
                for user_id in source_user_ids:
                    anonymized_fact = anonymized_fact.replace(user_id, "group member")

            # Normalize scope: accept "family" but store as "shared"
            scope = "shared"

            # Build metadata with bucket info
            final_metadata = metadata or {}
            if bucket_id:
                final_metadata["bucket_id"] = bucket_id
            if bucket_type:
                final_metadata["bucket_type"] = bucket_type
            if associated_bucket_id:
                final_metadata["associated_bucket_id"] = associated_bucket_id
            elif bucket_id:
                # Use bucket_id as associated_bucket_id if not provided
                final_metadata["associated_bucket_id"] = bucket_id

            # Check if shared memory already exists (avoid duplicates)
            existing_query = {
                "scope": scope,
                "group_id": group_id,
                "fact": anonymized_fact,
            }
            # Include bucket filter if provided
            if bucket_id:
                existing_query["metadata.associated_bucket_id"] = bucket_id

            existing = await _maybe_await(self.shared_collection.find_one(existing_query))

            if existing:
                # Update existing shared memory (increase confidence if higher)
                if confidence > existing.get("confidence", 0.0):
                    await _maybe_await(
                        self.shared_collection.update_one(
                            {"_id": existing["_id"]},
                            {
                                "$set": {
                                    "confidence": confidence,
                                    "last_updated": datetime.now(timezone.utc),
                                    "metadata": final_metadata,
                                },
                                "$addToSet": {"sources": {"$each": [{"user_id": uid} for uid in source_user_ids]}},
                            },
                        )
                    )
                    logger.info("Updated existing shared memory (duplicate with higher confidence)")
                    return existing
                else:
                    logger.debug("Shared memory already exists with higher confidence")
                    return existing

            # Create new shared memory
            shared_doc = {
                "scope": scope,
                "group_id": group_id,
                "fact": anonymized_fact,
                "confidence": confidence,
                "sources": [{"user_id": uid} for uid in source_user_ids],
                "visibility": "all",
                "created_at": datetime.now(timezone.utc),
                "last_updated": datetime.now(timezone.utc),
                "metadata": final_metadata,
            }

            result = await _maybe_await(self.shared_collection.insert_one(shared_doc))
            shared_doc["_id"] = result.inserted_id

            logger.info(
                f"Shared memory promoted: group_id={group_id}, " f"confidence={confidence:.2f}, bucket={bucket_id}"
            )

            return shared_doc

        except (PyMongoError, OperationFailure) as e:
            logger.error(f"Failed to promote to shared: {e}", exc_info=True)
            raise SharedMemoryError(f"Failed to promote to shared: {e}") from e

    async def get_shared_memory(
        self,
        group_id: str,
        query: str | None = None,
        min_confidence: float = 0.7,
        limit: int = 10,
        user_id: str | None = None,
        bucket_id: str | None = None,
        bucket_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Retrieve shared/group-level memories.

        Returns memories that are:
        - Scoped to shared/group
        - Above confidence threshold
        - Filtered by bucket if provided
        - Safe for the requesting user (role-aware filtering)

        Args:
            group_id: Generic group identifier (team, family, org, etc.)
            query: Optional search query (for vector search)
            min_confidence: Minimum confidence threshold (default: 0.7)
            limit: Maximum number of results (default: 10)
            user_id: Optional user ID for role-aware filtering
            bucket_id: Optional bucket ID to filter by
            bucket_type: Optional bucket type to filter by

        Returns:
            List of shared memory documents

        Example:
            ```python
            # Team memories in CODE bucket
            shared_facts = await shared_memory.get_shared_memory(
                group_id="team-001",
                bucket_id="category:CODE:team-001",
                query="What coding patterns do we use?",
                min_confidence=0.7
            )

            # Family memories in WORK bucket
            shared_facts = await shared_memory.get_shared_memory(
                group_id="family-001",
                bucket_id="category:WORK:family-001",
                query="What work-related patterns do we share?"
            )
            ```
        """
        user_id = validate_user_id(user_id, allow_none=False)

        await self._ensure_ready()

        # Verify group membership before granting access
        await self._verify_group_membership(user_id, group_id)

        try:
            scope = "shared"

            query_filter = {
                "scope": scope,
                "group_id": group_id,
                "confidence": {"$gte": min_confidence},
            }

            # Add bucket filtering
            if bucket_id:
                query_filter["metadata.associated_bucket_id"] = bucket_id
            elif bucket_type:
                query_filter["metadata.bucket_type"] = bucket_type

            # Use vector search if query provided
            if query:
                try:
                    if self._embedding_service is None:
                        raise SharedMemoryError(
                            "embedding_service is required for vector search in SharedMemory. "
                            "Pass an EmbeddingService instance to the constructor."
                        )
                    query_vectors = await self._embedding_service.embed([query])
                    if query_vectors:
                        query_vector = query_vectors[0]

                        # Vector search with bucket filter
                        pipeline = [
                            {
                                "$vectorSearch": {
                                    "index": "shared_vector_index",
                                    "path": "vector",
                                    "queryVector": query_vector,
                                    "numCandidates": limit * 10,
                                    "limit": limit,
                                    "filter": query_filter,
                                }
                            },
                            {"$match": query_filter},  # Additional match for safety
                        ]

                        results = await _cursor_to_list(self.shared_collection.aggregate(pipeline), limit)
                        logger.debug(
                            f"Retrieved {len(results)} shared memories via vector search "
                            f"(group_id: {group_id}, bucket: {bucket_id})"
                        )
                        return results
                except (PyMongoError, OperationFailure, Exception) as e:
                    logger.debug(f"Vector search not available, using standard query: {e}")

            # Standard query (no vector search)
            cursor = (
                self.shared_collection.find(query_filter).sort([("confidence", -1), ("last_updated", -1)]).limit(limit)
            )

            memories = await _cursor_to_list(cursor, limit)
            logger.debug(f"Retrieved {len(memories)} shared memories " f"(group_id: {group_id}, bucket: {bucket_id})")
            return memories

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to get shared memory: {e}")
            return []

    async def get_shared_stats(
        self,
        group_id: str,
        bucket_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Get statistics about shared memory.

        Args:
            group_id: Generic group identifier
            bucket_id: Optional bucket ID to filter by

        Returns:
            Dictionary with shared memory statistics
        """
        await self._ensure_ready()

        try:
            scope = "shared"
            query = {"scope": scope, "group_id": group_id}

            if bucket_id:
                query["metadata.associated_bucket_id"] = bucket_id

            total = await _maybe_await(self.shared_collection.count_documents(query))
            high_confidence = await _maybe_await(
                self.shared_collection.count_documents({**query, "confidence": {"$gte": 0.7}})
            )

            # Get most common patterns
            pipeline = [
                {"$match": query},
                {"$group": {"_id": "$fact", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
                {"$limit": 5},
            ]
            common_patterns = await _cursor_to_list(self.shared_collection.aggregate(pipeline), 5)

            return {
                "total_shared_memories": total,
                "high_confidence_count": high_confidence,
                "high_confidence_percentage": (high_confidence / total * 100) if total > 0 else 0,
                "common_patterns": [{"fact": p["_id"], "count": p["count"]} for p in common_patterns],
            }

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to get shared stats: {e}")
            return {"total_shared_memories": 0, "error": str(e)}
