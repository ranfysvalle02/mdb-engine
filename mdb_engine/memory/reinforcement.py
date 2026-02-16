"""
Reinforcement Mixin for CognitiveMemoryService.

Provides similarity search (single/async/parallel), duplicate detection,
memory reinforcement (importance boost + text merge), and access count tracking.
"""

import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Any

from pymongo.errors import OperationFailure, PyMongoError

from .base import MemoryServiceError

_PARALLEL_SEMAPHORE_LIMIT = 10

logger = logging.getLogger(__name__)


class ReinforcementMixin:
    """Mixin that provides reinforcement / similarity methods for CognitiveMemoryService."""

    # ------------------------------------------------------------------
    # Similarity search
    # ------------------------------------------------------------------

    async def _find_similar_memories(
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
            docs = await cursor.to_list(length=top_n)
            results = []

            for doc in docs:
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
        """Thin wrapper kept for call-site clarity in parallel helpers."""
        return await self._find_similar_memories(user_id, embedding, top_n)

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
        results = await asyncio.gather(*[search_with_semaphore(emb) for emb in embeddings], return_exceptions=True)

        # Handle any exceptions gracefully
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(f"Parallel search {i} failed: {result}")
                processed_results.append([])
            else:
                processed_results.append(result)

        logger.info(f"[Parallel Search] Completed {len(embeddings)} searches concurrently")
        return processed_results

    # ------------------------------------------------------------------
    # Duplicate detection
    # ------------------------------------------------------------------

    async def _check_for_duplicate(
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
            similar = await self._find_similar_memories(
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
                f"[Dedup] Found duplicate memory (similarity={top_match['similarity']:.3f}): "
                f"'{top_match['memory'][:50]}...' (skipping duplicate: '{text[:50]}...')"
            )

            # Get current importance and category
            current_importance = top_match.get("importance", 0.5)
            existing_category = top_match.get("metadata", {}).get("category")
            # Also check top-level category field if it exists
            existing_doc = await self.collection.find_one({"_id": top_match["id"]})
            if existing_doc:
                existing_category = existing_doc.get("category") or existing_category
            # If category is missing, detect from text
            if not existing_category:
                existing_text = top_match.get("memory", "")
                existing_category = self._detect_category_from_text(existing_text) if existing_text else "biographical"

            # Boost importance when duplicate is found (reinforcement)
            new_importance = min(current_importance * self.reinforcement_factor, 1.0)

            # Check if new text suggests a better category
            best_category = await self._get_best_category(
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
                logger.info(f"[Dedup] Category updated: {existing_category} → {best_category}")

            # Update the existing memory with boosted importance and corrected category
            update_result = await self.collection.update_one(
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
                    f"[Dedup] Boosted existing memory instead of creating "
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
            logger.warning(f"[Dedup] Duplicate check failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Reinforcement
    # ------------------------------------------------------------------

    async def _reinforce_memory(
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
            doc = await self.collection.find_one({"_id": memory_id})
            if not doc:
                return False

            existing_text = doc.get("text", "")
            current_importance = doc.get("importance", 0.5)
            current_access = doc.get("access_count", 0)
            existing_category = doc.get("category") or doc.get("metadata", {}).get("category")
            # If category is missing, detect from text
            if not existing_category:
                existing_category = self._detect_category_from_text(existing_text) if existing_text else "biographical"

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
                        f"[Reinforce] New text has {merge_reason}, merging: "
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
                        response = await self._llm_completion(
                            messages=[{"role": "user", "content": merge_prompt}],
                            model=self.memory_llm_model,
                        )
                        merged_text = response.choices[0].message.content.strip()
                        logger.debug(f"[Reinforce] Merged text ({len(merged_text)} chars)")
                    except (MemoryServiceError, RuntimeError, ConnectionError) as e:
                        logger.warning(f"LLM merge failed, keeping existing text: {e}")
                        merged_text = existing_text
                        should_merge_text = False

            # Reinforce: increase importance and access count
            new_importance = min(current_importance * self.reinforcement_factor, 1.0)
            new_access_count = current_access + 1

            # Check if new text suggests a better category
            best_category = existing_category
            if new_text:
                best_category = await self._get_best_category(
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
                logger.info(f"[Reinforce] Category updated: {existing_category} → {best_category}")

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
                            (a + b) / 2.0 for a, b in zip(new_embedding, existing_embedding, strict=False)
                        ]
                    else:
                        update_fields["embedding"] = new_embedding
                elif self.embedding_provider:
                    # Generate new embedding for merged text
                    try:
                        new_emb = await self._get_embedding(merged_text)
                        if new_emb:
                            update_fields["embedding"] = new_emb
                    except (ValueError, RuntimeError) as e:
                        logger.warning(f"Failed to re-embed merged text: {e}")

            await self.collection.update_one(
                {"_id": memory_id},
                {"$set": update_fields},
            )

            if should_merge_text:
                logger.info(
                    f"[Reinforce+Merge] Memory {memory_id}: "
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
                    f"Reinforced memory {memory_id}: importance " f"{current_importance:.2f} -> {new_importance:.2f}"
                )
                return True

        except (PyMongoError, OperationFailure):
            logger.exception(f"Failed to reinforce memory {memory_id}")
            return False

    # ------------------------------------------------------------------
    # Importance reinforcement (batch)
    # ------------------------------------------------------------------

    async def _update_importance_reinforcement(self, user_id: str, new_embedding: list[float]):
        """
        Reinforce similar memories based on similarity to new content.

        Uses $vectorSearch for efficient server-side ANN lookup instead of
        loading all user memories into Python.

        Perfect Recall: Similar memories are reinforced, but memories NEVER decay.
        All memories remain accessible forever with their original importance.
        """
        try:
            similar_memories = await self._find_similar_memories(
                user_id=user_id,
                embedding=new_embedding,
                top_n=20,
            )

            for mem in similar_memories:
                similarity = mem.get("similarity", 0.0)
                if similarity <= self.similarity_threshold:
                    continue

                current_importance = mem.get("importance", 0.5)
                new_importance = min(current_importance * self.reinforcement_factor, 1.0)
                new_access_count = mem.get("access_count", 0) + 1

                await self.collection.update_one(
                    {"_id": mem["id"]},
                    {
                        "$set": {
                            "importance": new_importance,
                            "access_count": new_access_count,
                            "last_accessed": datetime.now(timezone.utc),
                        }
                    },
                )
        except (PyMongoError, OperationFailure):
            logger.exception("Failed to update importance reinforcement")

    # ------------------------------------------------------------------
    # Access count tracking
    # ------------------------------------------------------------------

    async def _update_access_counts(
        self,
        memory_ids: list,
    ) -> None:
        """
        Update access counts for retrieved memories (Perfect Recall mode).

        Perfect Recall: Memories never decay, but we track access for ranking.

        Args:
            memory_ids: List of ObjectIds to update
        """
        now = datetime.now(timezone.utc)

        try:
            # Update access counts and last_accessed timestamp
            await self.collection.update_many(
                {"_id": {"$in": memory_ids}},
                {
                    "$inc": {"access_count": 1},
                    "$set": {"last_accessed": now},
                },
            )

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to update access counts: {e}")
