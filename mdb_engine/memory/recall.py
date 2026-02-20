"""
Query-Aware Recall System
Policy-driven memory retrieval based on task/goal.

This module implements query-aware recall - memory retrieval that adapts
based on the current task, risk tolerance, and latency budget.

Key principle: "Memory retrieval becomes policy-driven, not instinctive"
"""

import logging
from typing import TYPE_CHECKING, Any

from ..core.validation import validate_user_id
from .base import MemoryServiceError
from .embedding import cosine_similarity

if TYPE_CHECKING:
    from mdb_engine.embeddings.service import EmbeddingService

try:
    from pymongo.errors import OperationFailure, PyMongoError

    PYMONGO_AVAILABLE = True
except ImportError:
    raise ImportError("pip install pymongo") from None

logger = logging.getLogger(__name__)


class QueryAwareRecallError(MemoryServiceError):
    """Base exception for Query-Aware Recall errors."""

    pass


class QueryAwareRecall:
    """
    Policy-driven memory retrieval based on task/goal.

    This enables memory access that adapts to:
    - Task type (fast answer vs critical decision)
    - Risk tolerance (low = high confidence, high = include lower confidence)
    - Latency budget (fast = limit results, deep = exhaustive)

    Example:
        ```python
        from mdb_engine.memory.recall import QueryAwareRecall

        recall = QueryAwareRecall()

        # Fast answer (shallow recall)
        result = recall.recall(
            query="What's the user's favorite color?",
            user_id="user123",
            task_type="fast_answer",
            risk_tolerance="low",
            latency_budget="fast",
            scope="user"
        )

        # Critical decision (deep recall + cross-checks)
        result = recall.recall(
            query="Should I recommend this medication?",
            user_id="user123",
            task_type="critical_decision",
            risk_tolerance="low",
            latency_budget="deep",
            scope="user"
        )
        ```
    """

    def __init__(
        self,
        embedding_service: "EmbeddingService | None" = None,
    ):
        """Initialize QueryAwareRecall manager.

        Args:
            embedding_service: EmbeddingService for generating query embeddings.
        """
        self._embedding_service = embedding_service

    def _cross_check_memories(
        self,
        memories: list[dict[str, Any]],
        similarity_threshold: float = 0.80,
    ) -> list[dict[str, Any]]:
        """
        Cross-check memories for potential contradictions.

        Compares pairs of retrieved memories. If two memories have high
        embedding similarity (discussing the same topic) but different
        textual content, they are flagged as potential contradictions.

        This uses a lightweight approach: pairwise cosine similarity of
        embeddings, not an LLM call.

        Args:
            memories: List of memory dictionaries (must have 'embedding' or 'vector' field)
            similarity_threshold: Similarity above which memories are compared for contradiction
                                  (default: 0.80)

        Returns:
            Same memories list with optional 'contradictions' field added
            to memories that have potential contradictions
        """
        if len(memories) < 2:
            return memories

        # Build list of (index, embedding) pairs for memories that have embeddings
        indexed_embeddings: list[tuple[int, list[float]]] = []
        for i, mem in enumerate(memories):
            emb = mem.get("embedding") or mem.get("vector")
            if emb:
                indexed_embeddings.append((i, emb))

        if len(indexed_embeddings) < 2:
            return memories

        # Guard against O(n^2) blowup
        max_pairwise = 200
        if len(indexed_embeddings) > max_pairwise:
            logger.warning(
                "[CrossCheck] Truncating %d memories to %d for pairwise comparison",
                len(indexed_embeddings),
                max_pairwise,
            )
            indexed_embeddings = indexed_embeddings[:max_pairwise]

        # Pairwise comparison
        contradiction_pairs: list[tuple[int, int, float]] = []
        for i_idx in range(len(indexed_embeddings)):
            for j_idx in range(i_idx + 1, len(indexed_embeddings)):
                idx_a, emb_a = indexed_embeddings[i_idx]
                idx_b, emb_b = indexed_embeddings[j_idx]

                sim = cosine_similarity(emb_a, emb_b)

                if sim >= similarity_threshold:
                    # High similarity = same topic. Check if content differs.
                    text_a = memories[idx_a].get("content", "") or memories[idx_a].get("text", "")
                    text_b = memories[idx_b].get("content", "") or memories[idx_b].get("text", "")

                    # Simple heuristic: if texts are highly similar embeddings but
                    # different text content, flag as potential contradiction
                    if text_a and text_b and text_a.strip() != text_b.strip():
                        contradiction_pairs.append((idx_a, idx_b, sim))

        # Annotate memories with contradiction info
        if contradiction_pairs:
            for idx_a, idx_b, sim in contradiction_pairs:
                mem_a_id = memories[idx_a].get("_id") or memories[idx_a].get("id", "")
                mem_b_id = memories[idx_b].get("_id") or memories[idx_b].get("id", "")

                # Add contradiction flags
                if "contradictions" not in memories[idx_a]:
                    memories[idx_a]["contradictions"] = []
                memories[idx_a]["contradictions"].append({"memory_id": str(mem_b_id), "similarity": sim})

                if "contradictions" not in memories[idx_b]:
                    memories[idx_b]["contradictions"] = []
                memories[idx_b]["contradictions"].append({"memory_id": str(mem_a_id), "similarity": sim})

            logger.info(
                f"Cross-check found {len(contradiction_pairs)} potential contradictions " f"in {len(memories)} memories"
            )
        else:
            logger.debug(f"Cross-check: no contradictions found in {len(memories)} memories")

        return memories

    def _build_recall_policy(
        self,
        task_type: str = "general",
        risk_tolerance: str = "medium",
        latency_budget: str = "normal",
    ) -> dict[str, Any]:
        """
        Build recall policy based on task parameters.

        Args:
            task_type: Type of task ("fast_answer", "critical_decision", "general", "exploration")
            risk_tolerance: Risk tolerance ("low", "medium", "high")
            latency_budget: Latency budget ("fast", "normal", "deep")

        Returns:
            Dictionary with recall policy parameters
        """
        policy: dict[str, Any] = {
            "max_results": 10,
            "cross_check": False,
            "exhaustive": False,
        }

        # Task type adjustments
        if task_type == "fast_answer":
            policy["max_results"] = 3
        elif task_type == "critical_decision":
            policy["max_results"] = 20
            policy["cross_check"] = True
            policy["exhaustive"] = True
        elif task_type == "exploration":
            policy["max_results"] = 15

        # Risk tolerance adjustments
        if risk_tolerance == "low":
            policy["cross_check"] = True
        # True Perfect Recall: no confidence filtering needed.
        # Ranking (similarity, importance, emotion, recency) handles relevance.

        # Latency budget adjustments
        if latency_budget == "fast":
            policy["max_results"] = min(policy["max_results"], 5)
            policy["exhaustive"] = False
        elif latency_budget == "deep":
            policy["max_results"] = 50
            policy["exhaustive"] = True

        return policy

    async def recall(
        self,
        query: str,
        user_id: str,
        collection: Any,
        task_type: str = "general",
        risk_tolerance: str = "medium",
        latency_budget: str = "normal",
        scope: str = "user",
        memory_veto: Any | None = None,
        bucket_id: str | None = None,
        bucket_type: str | None = None,
        associated_bucket_id: str | None = None,
        group_id: str | None = None,
        query_vector: list[float] | None = None,
    ) -> dict[str, Any]:
        """
        Recall memories using policy-driven approach.

        Args:
            query: Search query string
            collection: MongoDB collection to search (semantic, episodic, etc.)
            user_id: User ID for scoping
            task_type: Type of task ("fast_answer", "critical_decision", "general", "exploration")
            risk_tolerance: Risk tolerance ("low", "medium", "high")
            latency_budget: Latency budget ("fast", "normal", "deep")
            scope: Memory scope ("user", "shared", "system")
            group_id: Generic group identifier if scope is "shared"
            memory_veto: Optional MemoryVeto instance to check vetoes
            bucket_id: Optional bucket ID for filtering
            bucket_type: Optional bucket type
            associated_bucket_id: Optional associated bucket ID for unified search
            query_vector: Optional pre-computed embedding vector for the query.
                         When provided, skips the internal embedding call.
                         Callers using an async EmbeddingService should pre-compute
                         this with ``await embedding_service.embed(query)`` and pass
                         the result here.

        Returns:
            Dictionary with recalled memories and policy info

        Example:
            ```python
            result = recall.recall(
                query="User preferences",
                user_id="user123",
                collection=semantic_collection,
                task_type="fast_answer",
                risk_tolerance="low"
            )
            memories = result["memories"]
            ```
        """
        if not query or not query.strip():
            raise QueryAwareRecallError("Query cannot be empty")

        user_id = validate_user_id(user_id, allow_none=False)

        # Build recall policy
        policy = self._build_recall_policy(task_type, risk_tolerance, latency_budget)

        try:
            normalized_scope = "shared" if scope == "family" else scope

            # Build query filter
            # True Perfect Recall: no confidence filter. All memories always searchable.
            query_filter: dict[str, Any] = {
                "scope": normalized_scope,
            }

            if user_id and normalized_scope == "user":
                query_filter["user_id"] = str(user_id)
            if group_id and normalized_scope == "shared":
                query_filter["group_id"] = group_id

            # Add bucket filtering
            if bucket_id:
                query_filter["metadata.associated_bucket_id"] = bucket_id
            elif associated_bucket_id:
                query_filter["metadata.associated_bucket_id"] = associated_bucket_id
            elif bucket_type:
                query_filter["metadata.bucket_type"] = bucket_type

            # Try vector search first if query provided
            try:
                if query_vector is None:
                    if self._embedding_service is None:
                        raise QueryAwareRecallError(
                            "embedding_service is required for vector search. "
                            "Pass an EmbeddingService instance to the constructor."
                        )
                    vectors = await self._embedding_service.embed([query])
                    if vectors:
                        query_vector = vectors[0]

                if query_vector:
                    # Use vector search with bucket filter
                    pipeline = [
                        {
                            "$vectorSearch": {
                                "index": "vector_index",  # Will use appropriate index
                                "path": "embedding",  # or "vector" depending on collection
                                "queryVector": query_vector,
                                "numCandidates": policy["max_results"] * 10,
                                "limit": policy["max_results"],
                                "filter": query_filter,
                            }
                        },
                        {"$match": query_filter},  # Additional match for safety
                    ]

                    if not policy["exhaustive"]:
                        pipeline.append({"$limit": policy["max_results"]})

                    results = await collection.aggregate(pipeline).to_list(length=policy["max_results"])
                    logger.debug(f"Vector search returned {len(results)} results")

                    # Apply vetoes if provided
                    if memory_veto:
                        filtered_results = []
                        for mem in results:
                            mem_id = str(mem.get("_id", ""))
                            if not memory_veto.check_veto(mem_id, user_id, target_scope=normalized_scope):
                                filtered_results.append(mem)
                            else:
                                logger.debug(f"Memory {mem_id} vetoed, excluding from recall")
                        results = filtered_results

                    return {
                        "memories": results,
                        "policy": policy,
                        "count": len(results),
                        "scope": normalized_scope,
                    }
            except (PyMongoError, OperationFailure) as e:
                logger.debug(f"Vector search not available, using standard query: {e}")

            # Standard query (no vector search)
            cursor = collection.find(query_filter).sort([("confidence", -1), ("last_updated", -1)])

            if not policy["exhaustive"]:
                cursor = cursor.limit(policy["max_results"])

            memories = await cursor.to_list(length=policy["max_results"] if not policy["exhaustive"] else None)

            # Apply vetoes if provided
            if memory_veto:
                filtered_memories = []
                for mem in memories:
                    mem_id = str(mem.get("_id", ""))
                    if not memory_veto.check_veto(mem_id, user_id, target_scope=normalized_scope):
                        filtered_memories.append(mem)
                    else:
                        logger.debug(f"Memory {mem_id} vetoed, excluding from recall")
                memories = filtered_memories

            # Cross-check if required (verify consistency between memories)
            if policy["cross_check"] and len(memories) > 1:
                memories = self._cross_check_memories(memories)

            logger.info(
                f"Recalled {len(memories)} memories "
                f"(task: {task_type}, risk: {risk_tolerance}, latency: {latency_budget})"
            )

            return {
                "memories": memories,
                "policy": policy,
                "count": len(memories),
                "scope": normalized_scope,
            }

        except (PyMongoError, OperationFailure) as e:
            logger.error(f"Failed to recall memories: {e}", exc_info=True)
            raise QueryAwareRecallError(f"Failed to recall memories: {e}") from e

    async def recall_multi_scope(
        self,
        query: str,
        user_id: str,
        collections: dict[str, Any],
        allowed_scopes: list[str],
        task_type: str = "general",
        risk_tolerance: str = "medium",
        latency_budget: str = "normal",
        memory_veto: Any | None = None,
        bucket_id: str | None = None,
        bucket_type: str | None = None,
        group_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Recall memories from multiple scopes.

        Useful for queries that need user + shared + system context.

        Args:
            query: Search query string
            user_id: User ID for scoping
            collections: Dictionary mapping scope to collection
                       e.g., {"user": user_collection, "shared": shared_collection}
            allowed_scopes: List of scopes to search ("user", "shared", "system")
            task_type: Type of task
            risk_tolerance: Risk tolerance
            latency_budget: Latency budget
            group_id: Generic group identifier if needed
            memory_veto: Optional MemoryVeto instance
            bucket_id: Optional bucket ID for filtering
            bucket_type: Optional bucket type

        Returns:
            Dictionary with memories grouped by scope

        Example:
            ```python
            result = recall.recall_multi_scope(
                query="How is our team doing?",
                user_id="user123",
                collections={
                    "user": user_collection,
                    "shared": shared_collection,
                    "system": system_collection
                },
                allowed_scopes=["user", "shared", "system"],
                bucket_id="category:CODE:team-001"
            )
            user_memories = result["user"]
            shared_memories = result["shared"]
            ```
        """
        user_id = validate_user_id(user_id, allow_none=False)

        results = {}

        for scope in allowed_scopes:
            if scope not in collections:
                logger.warning(f"Collection not provided for scope: {scope}")
                continue

            try:
                # Normalize scope: accept "family" but query as "shared"
                normalized_scope = "shared" if scope == "family" else scope

                scope_result = await self.recall(
                    query=query,
                    user_id=user_id,
                    collection=collections[normalized_scope]
                    if normalized_scope in collections
                    else collections.get(scope, collections.get("shared")),
                    task_type=task_type,
                    risk_tolerance=risk_tolerance,
                    latency_budget=latency_budget,
                    scope=normalized_scope,
                    group_id=group_id,
                    memory_veto=memory_veto,
                    bucket_id=bucket_id,
                    bucket_type=bucket_type,
                )
                results[normalized_scope] = scope_result["memories"]
            except (PyMongoError, OperationFailure, ValueError) as e:
                logger.warning(f"Failed to recall from scope {scope}: {e}")
                results[scope] = []

        total_memories = sum(len(mems) for mems in results.values())

        logger.info(f"Multi-scope recall: {total_memories} total memories " f"from {len(allowed_scopes)} scopes")

        return {
            "memories_by_scope": results,
            "total_memories": total_memories,
            "scopes_searched": allowed_scopes,
        }
