"""
Merging Mixin for CognitiveMemoryService.

Provides memory merging (LLM-based text combination), and the fact-processing
pipeline methods used by ``add`` to handle duplicates, reinforcements,
merges, and new-fact creation in bulk.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo.errors import OperationFailure, PyMongoError

logger = logging.getLogger(__name__)


class MergingMixin:
    """Mixin that provides merging / fact-processing methods for CognitiveMemoryService."""

    # ------------------------------------------------------------------
    # Core merge
    # ------------------------------------------------------------------

    async def _merge_memories(
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
                "These two texts contain related information about a user. "
                "Combine them into a single cohesive text that preserves all "
                "important details from both without redundancy.\n\n"
                "CRITICAL RULE: Always refer to the person in the THIRD PERSON "
                'as "User" (e.g., "User loves chocolate", "User\'s sister works '
                'in a beauty salon"). NEVER use first person ("I", "my", "me"). '
                "This is a memory record about the user, not written by the user.\n\n"
                f"TEXT 1: {new_text}\n\n"
                f"TEXT 2: {existing_text}\n\n"
                "Combine these texts into one unified memory using third-person "
                '"User" perspective.'
            )

            response = await self._llm_completion(
                messages=[{"role": "user", "content": merge_prompt}],
                model=self.memory_llm_model,
            )

            merged_text = response.choices[0].message.content.strip()

            # Average embeddings
            merged_embedding = [(a + b) / 2.0 for a, b in zip(new_embedding, existing_embedding, strict=False)]

            # Use higher importance, boosted slightly
            merged_importance = min(max(new_importance, existing_importance) * 1.1, 1.0)

            # Batch-fetch both documents in a single query instead of two find_one calls
            docs_by_id: dict[Any, dict[str, Any]] = {}
            async for doc in self.collection.find({"_id": {"$in": [existing_memory_id, new_memory_id]}}):
                docs_by_id[doc["_id"]] = doc

            existing_doc = docs_by_id.get(existing_memory_id)
            existing_access = existing_doc.get("access_count", 0) if existing_doc else 0
            existing_category = (
                existing_doc.get("category") or existing_doc.get("metadata", {}).get("category")
                if existing_doc
                else None
            )
            # If category is missing, detect from text
            if not existing_category:
                existing_category = self._detect_category_from_text(existing_text) if existing_text else "biographical"

            new_doc = docs_by_id.get(new_memory_id)
            new_category = new_doc.get("category") or new_doc.get("metadata", {}).get("category") if new_doc else None
            # If category is missing, detect from text
            if not new_category:
                new_category = self._detect_category_from_text(new_text) if new_text else "biographical"

            # Use category priority to choose best
            best_category = await self._get_best_category(
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
                logger.info(f"[Merge] Category updated: " f"{existing_category}/{new_category} → {best_category}")

            # Update the new memory with merged content (await async call)
            await self.collection.update_one(
                {"_id": new_memory_id},
                {"$set": update_fields},
            )

            # Delete the old memory (await async call)
            await self.collection.delete_one({"_id": existing_memory_id})

            # Clean up graph references for the deleted memory (best-effort)
            graph_service = getattr(self, "_graph_service", None)
            if graph_service:
                try:
                    await graph_service.remove_memory_references(str(existing_memory_id))
                except (AttributeError, NotImplementedError):
                    pass  # Graph service doesn't support cleanup
                except (PyMongoError, ConnectionError, TimeoutError, ValueError, RuntimeError) as ge:
                    logger.warning(f"[Graph Cleanup] Non-fatal error on merge: {ge}")

            logger.info(f"Merged memory {existing_memory_id} into {new_memory_id}")
            return True
        except (PyMongoError, OperationFailure):
            logger.exception("Failed to merge memories")
            return False

    # ------------------------------------------------------------------
    # Fact-processing pipeline helpers (called from add)
    # ------------------------------------------------------------------

    async def _process_duplicate_facts(
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

                duplicate_result = await self._check_for_duplicate(
                    text=fact_text,
                    user_id=user_id,
                    embedding=fact_embedding,
                )
                if duplicate_result:
                    stored_memories.append(duplicate_result)
            except (PyMongoError, OperationFailure) as e:
                logger.warning(f"Duplicate handling failed: {e}")
        return stored_memories

    async def _process_reinforcement_facts(
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

                result = await self._reinforce_memory(
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
                    await self.collection.update_one(
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
                logger.warning(f"Reinforcement failed: {e}")
        return stored_memories

    async def _process_merge_facts(
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
        # Collect successfully-merged IDs and their default importances so we
        # can batch-fetch the final merged documents in a single query.
        merged_ids: list[tuple[Any, float]] = []  # (memory_id, fallback_importance)

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
            fact_emotion_type = fact_data.get("emotion_type", "neutral")
            vector = valid_embeddings[idx]

            fact_metadata = dict(final_metadata)
            if self.categories_enabled and category:
                fact_metadata["category"] = category

            importance = importance_map.get(fact, 0.5)
            memory_type = self.default_memory_type
            if self.memory_types_enabled and self.auto_detect_memory_type:
                memory_type = await self._detect_memory_type(fact)

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
                    "emotion_type": fact_emotion_type,
                    "confidence": 0.8,  # Default confidence
                    "graph_links": self._init_graph_links(),
                }

                # Ensure timeline_id in metadata
                if "timeline_id" not in fact_metadata:
                    fact_metadata["timeline_id"] = "root"
                    temp_doc["metadata"] = fact_metadata

                if self.memory_types_enabled:
                    temp_doc["memory_type"] = memory_type
                    if memory_type == "episodic":
                        temp_doc["expires_at"] = now + timedelta(days=self.episodic_retention_days)

                result = await self.collection.insert_one(temp_doc)
                new_memory_id = result.inserted_id

                if await self._merge_memories(
                    new_memory_id=new_memory_id,
                    existing_memory_id=similar["id"],
                    new_text=fact,
                    existing_text=similar["memory"],
                    new_embedding=vector,
                    existing_embedding=similar.get("embedding", vector),
                    new_importance=importance,
                    existing_importance=similar["importance"],
                ):
                    merged_ids.append((new_memory_id, importance))
            except (PyMongoError, OperationFailure) as e:
                logger.warning(f"Merge failed: {e}")

        # Batch-fetch all successfully merged documents in one query
        stored_memories: list[dict[str, Any]] = []
        if merged_ids:
            all_ids = [mid for mid, _ in merged_ids]
            importance_by_id = {mid: imp for mid, imp in merged_ids}
            async for merged_doc in self.collection.find({"_id": {"$in": all_ids}}):
                stored_memories.append(
                    {
                        "id": str(merged_doc["_id"]),
                        "memory": merged_doc.get("text", ""),
                        "metadata": merged_doc.get("metadata", {}),
                        "user_id": str(user_id) if user_id else None,
                        "importance": merged_doc.get("importance", importance_by_id.get(merged_doc["_id"], 0.5)),
                        "action": "merged",
                    }
                )
        return stored_memories

    async def _process_new_facts(
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
            fact_emotion_type = fact_data.get("emotion_type", "neutral")
            vector = valid_embeddings[idx]

            fact_metadata = dict(final_metadata)
            if self.categories_enabled and category:
                fact_metadata["category"] = category

            # Ensure timeline_id in metadata (default to root)
            if "timeline_id" not in fact_metadata:
                fact_metadata["timeline_id"] = "root"

            # Detect memory type for this fact
            memory_type = self.default_memory_type

            if self.memory_types_enabled and self.auto_detect_memory_type:
                memory_type = await self._detect_memory_type(fact)

            if self.enable_cognitive:
                importance = importance_map.get(fact, 0.5)
                # CRITICAL: Store confidence in BOTH top level AND metadata for search compatibility
                # Top level for backward compatibility, metadata for vector search filter
                fact_confidence = 0.8  # Default confidence
                if "confidence" not in fact_metadata:
                    fact_metadata["confidence"] = fact_confidence

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
                    "emotion_type": fact_emotion_type,
                    "confidence": fact_confidence,  # Top level for backward compatibility
                    "graph_links": self._init_graph_links(),
                }

                # Add memory type (Cognitive Blueprint v2.0)
                if self.memory_types_enabled:
                    doc["memory_type"] = memory_type
                    # Add TTL for episodic memories
                    if memory_type == "episodic":
                        doc["expires_at"] = now + timedelta(days=self.episodic_retention_days)

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
                    "graph_links": self._init_graph_links(),
                }

                # Add memory type even if cognitive features disabled
                if self.memory_types_enabled:
                    doc["memory_type"] = memory_type
                    if memory_type == "episodic":
                        doc["expires_at"] = now + timedelta(days=self.episodic_retention_days)

                if self.categories_enabled and category:
                    doc["category"] = category

            docs_to_insert.append((idx, doc, fact, category, memory_type))

        stored_memories = []
        if docs_to_insert:
            try:
                docs = [d[1] for d in docs_to_insert]
                result = await self.collection.insert_many(docs)

                for i, inserted_id in enumerate(result.inserted_ids):
                    idx, doc, fact, category, mem_type = docs_to_insert[i]
                    memory_result = self._build_memory_result(inserted_id, doc, fact, category, mem_type, user_id)
                    stored_memories.append(memory_result)

                logger.info(f"[Bulk Insert] Inserted {len(result.inserted_ids)} memories")
            except (PyMongoError, OperationFailure) as e:
                logger.warning(f"Bulk insert failed, falling back to individual inserts: {e}")
                for _idx, doc, fact, category, mem_type in docs_to_insert:
                    try:
                        result = await self.collection.insert_one(doc)
                        memory_result = self._build_memory_result(
                            result.inserted_id, doc, fact, category, mem_type, user_id
                        )
                        stored_memories.append(memory_result)
                    except (PyMongoError, OperationFailure) as e2:
                        logger.warning(f"Individual insert failed: {e2}")
        return stored_memories

    def _build_memory_result(
        self,
        inserted_id: Any,
        doc: dict[str, Any],
        fact: str,
        category: str,
        mem_type: str,
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
            memory_result["confidence"] = doc.get("confidence", 0.8)

        if self.memory_types_enabled and "memory_type" in doc:
            memory_result["memory_type"] = doc["memory_type"]

        return memory_result

    async def _execute_async_actions(
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
        """Execute DB operations for add()."""
        stored_memories = []
        now = datetime.now(timezone.utc)

        # Process duplicates
        stored_memories.extend(
            await self._process_duplicate_facts(user_id, valid_facts, valid_embeddings, facts_to_duplicate)
        )

        # Process reinforcements
        stored_memories.extend(
            await self._process_reinforcement_facts(user_id, valid_facts, valid_embeddings, facts_to_reinforce, now)
        )

        # Process merges
        stored_memories.extend(
            await self._process_merge_facts(
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
            await self._process_new_facts(
                user_id,
                valid_facts,
                valid_embeddings,
                importance_map,
                final_metadata,
                facts_to_create,
                now,
            )
        )

        # Post-processing (reinforcement only - Perfect Recall: no pruning)
        if self.enable_cognitive and facts_to_create:
            if valid_embeddings and facts_to_create:
                first_new_idx = facts_to_create[0]
                await self._update_importance_reinforcement(user_id, valid_embeddings[first_new_idx])
            # Perfect Recall: No pruning - all memories preserved forever

        return stored_memories
