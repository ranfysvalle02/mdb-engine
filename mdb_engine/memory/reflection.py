"""
Reflection Service for Memory Consolidation

This module implements periodic memory consolidation that summarizes atomic
memories into narrative reflections, preventing memory bloat and improving
retrieval quality.

The Reflection Service:
1. Fetches recent memories (time-based or count-based trigger)
2. Uses LLM to consolidate into a narrative summary
3. Stores reflection in a separate collection
4. Optionally prunes low-salience memories after consolidation

Usage:
    reflection_service = ReflectionService(
        collection=memories_collection,
        reflections_collection=reflections_collection,
        config={
            "enabled": True,
            "interval_hours": 24,
            "message_threshold": 50,
            "min_salience_to_keep": 0.4,
            "store_reflections": True,
        }
    )

    # Run reflection for a user
    result = await reflection_service.run_reflection(user_id="user123")
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# LiteLLM imports no longer needed - using LLMService instead

# PyMongo imports
try:
    from pymongo.errors import OperationFailure, PyMongoError
except ImportError:
    raise ImportError("pip install pymongo") from None


class ReflectionServiceError(Exception):
    """Base exception for Reflection Service failures."""

    pass


class ReflectionService:
    """
    Service for periodic memory consolidation and reflection.

    The reflection process:
    1. Collects memories from a time period or count threshold
    2. Uses LLM to create a narrative summary (reflection)
    3. Stores the reflection for future context retrieval
    4. Optionally prunes low-importance memories to prevent bloat

    Configuration:
        enabled: bool - Enable reflection service (default: False)
        interval_hours: int - Hours between reflections (default: 24)
        message_threshold: int - Memory count to trigger reflection (default: 50)
        min_salience_to_keep: float - Min importance to keep after reflection (default: 0.4)
        store_reflections: bool - Store reflection summaries (default: True)
        llm_model: str - LLM model for reflection generation
        temperature: float - LLM temperature (default: 0.3)
    """

    def __init__(
        self,
        app_slug: str,
        memories_collection: Any,
        reflections_collection: Any | None = None,
        config: dict[str, Any] | None = None,
        graph_service: Any | None = None,
        llm_service: Any | None = None,
    ):
        """
        Initialize ReflectionService.

        Args:
            app_slug: Application slug
            memories_collection: PyMongo collection for memories
            reflections_collection: PyMongo collection for reflections (optional)
            config: Configuration dictionary
            graph_service: Optional graph service for entity linking
            llm_service: Optional LLM service for reflection generation
        """
        self.app_slug = app_slug
        self.memories_collection = memories_collection
        self.reflections_collection = (
            reflections_collection or memories_collection.database[f"{app_slug}_reflections"]
        )
        self.graph_service = graph_service
        self.llm_service = llm_service
        self.config = config or {}

        # Configuration
        self.enabled = self.config.get("enabled", False)
        self.interval_hours = self.config.get("interval_hours", 24)
        self.message_threshold = self.config.get("message_threshold", 50)
        self.min_salience_to_keep = self.config.get("min_salience_to_keep", 0.4)
        self.store_reflections = self.config.get("store_reflections", True)
        # Use llm_service's default model if available, otherwise use config
        if self.llm_service and hasattr(self.llm_service, "llm_provider"):
            self.llm_model = self.config.get("llm_model") or getattr(
                self.llm_service.llm_provider, "default_model", None
            )
        else:
            self.llm_model = self.config.get("llm_model")

        if not self.llm_model and not self.llm_service:
            logger.warning(
                "⚠️ ReflectionService: llm_model not set in config and llm_service not provided. "
                "Reflection operations will be skipped."
            )
        self.temperature = self.config.get("temperature", 0.3)

        # Consolidation configuration (Cognitive Blueprint v2.0)
        consolidation_config = self.config.get("consolidation", {})
        self.extract_entities = consolidation_config.get("extract_entities", True)
        self.route_by_type = consolidation_config.get("route_by_type", True)
        self.link_to_graph = consolidation_config.get("link_to_graph", True)
        self.extract_procedural = consolidation_config.get("extract_procedural", True)

        if self.enabled:
            logger.info(
                f"✅ ReflectionService initialized: "
                f"interval={self.interval_hours}h, threshold={self.message_threshold}, "
                f"min_salience={self.min_salience_to_keep}"
            )
        else:
            logger.info("⏸️ ReflectionService initialized but DISABLED")

    def should_reflect(self, user_id: str) -> tuple[bool, str]:
        """
        Check if reflection should be triggered for a user.

        Args:
            user_id: User ID to check

        Returns:
            Tuple of (should_reflect: bool, reason: str)
        """
        if not self.enabled:
            return False, "Reflection service disabled"

        try:
            # Check time-based trigger
            if self.reflections_collection:
                last_reflection = self.reflections_collection.find_one(
                    {"user_id": user_id},
                    sort=[("created_at", -1)],
                )

                if last_reflection:
                    last_time = last_reflection.get("created_at")
                    if last_time:
                        hours_since = (
                            datetime.now(timezone.utc) - last_time
                        ).total_seconds() / 3600
                        if hours_since < self.interval_hours:
                            return False, f"Last reflection was {hours_since:.1f}h ago"

            # Check count-based trigger
            cutoff = datetime.now(timezone.utc) - timedelta(hours=self.interval_hours)
            memory_count = self.memories_collection.count_documents(
                {
                    "user_id": user_id,
                    "created_at": {"$gte": cutoff},
                }
            )

            if memory_count >= self.message_threshold:
                return (
                    True,
                    f"Memory count ({memory_count}) exceeds threshold ({self.message_threshold})",
                )

            # Check if it's time for periodic reflection
            if self.reflections_collection:
                last_reflection = self.reflections_collection.find_one(
                    {"user_id": user_id},
                    sort=[("created_at", -1)],
                )

                if not last_reflection:
                    # No previous reflection - check if enough memories exist
                    total_memories = self.memories_collection.count_documents({"user_id": user_id})
                    if total_memories >= self.message_threshold:
                        return True, "No previous reflection and enough memories accumulated"
                else:
                    last_time = last_reflection.get("created_at")
                    if last_time:
                        hours_since = (
                            datetime.now(timezone.utc) - last_time
                        ).total_seconds() / 3600
                        if hours_since >= self.interval_hours:
                            return (
                                True,
                                f"Time-based trigger: {hours_since:.1f}h since last reflection",
                            )

            return False, "No reflection trigger conditions met"

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"⚠️ Error checking reflection trigger: {e}")
            return False, f"Error: {e}"

    def run_reflection(self, user_id: str, force: bool = False) -> dict[str, Any]:
        """
        Run the reflection process for a user.

        This method:
        1. Fetches memories from the last period
        2. Uses LLM to create a narrative summary
        3. Stores the reflection (if enabled)
        4. Prunes low-salience memories (if enabled)

        Args:
            user_id: User ID to run reflection for
            force: Force reflection even if trigger conditions not met

        Returns:
            Dictionary with reflection results
        """
        if not self.enabled and not force:
            return {
                "success": False,
                "reason": "Reflection service disabled",
                "memories_processed": 0,
            }

        # Check if we should reflect
        should, reason = self.should_reflect(user_id)
        if not should and not force:
            return {
                "success": False,
                "reason": reason,
                "memories_processed": 0,
            }

        logger.info(f"🔄 [Reflection] Starting reflection for user {user_id}: {reason}")

        try:
            # 1. Fetch memories from the last period
            cutoff = datetime.now(timezone.utc) - timedelta(hours=self.interval_hours)
            memories_cursor = self.memories_collection.find(
                {
                    "user_id": user_id,
                    "created_at": {"$gte": cutoff},
                }
            ).sort("created_at", 1)  # Oldest first

            memories = list(memories_cursor)

            if not memories:
                logger.info(f"🔄 [Reflection] No memories to reflect for user {user_id}")
                return {
                    "success": True,
                    "reason": "No memories in period",
                    "memories_processed": 0,
                }

            logger.info(f"🔄 [Reflection] Found {len(memories)} memories to consolidate")

            # 2. Enhanced consolidation (Cognitive Blueprint v2.0)
            entities_extracted = 0
            procedural_extracted = 0

            if self.extract_entities or self.route_by_type:
                # Extract entities from episodic memories
                episodic_memories = [m for m in memories if m.get("memory_type") == "episodic"]
                if episodic_memories and self.extract_entities:
                    entities_extracted = self._extract_entities(episodic_memories, user_id)

                # Extract procedural knowledge
                if self.extract_procedural:
                    procedural_extracted = self._extract_procedural(memories, user_id)

                # Route memories to appropriate types
                if self.route_by_type:
                    self._route_memories(memories, user_id)

            # 3. Generate reflection using LLM
            reflection_content = self._generate_reflection(memories, user_id)

            if not reflection_content:
                return {
                    "success": False,
                    "reason": "Failed to generate reflection",
                    "memories_processed": len(memories),
                }

            # 4. Store the reflection
            reflection_doc = None
            if self.store_reflections and self.reflections_collection:
                reflection_doc = {
                    "user_id": user_id,
                    "content": reflection_content,
                    "period_start": cutoff,
                    "period_end": datetime.now(timezone.utc),
                    "type": "periodic_summary",
                    "memories_consolidated": len(memories),
                    "memory_ids": [str(m["_id"]) for m in memories],
                    "created_at": datetime.now(timezone.utc),
                }

                result = self.reflections_collection.insert_one(reflection_doc)
                logger.info(f"✅ [Reflection] Stored reflection: {result.inserted_id}")

            # 5. Prune low-salience memories
            pruned_count = 0
            if self.min_salience_to_keep > 0:
                pruned_count = self._prune_low_salience_memories(memories, user_id)

            return {
                "success": True,
                "reason": reason,
                "memories_processed": len(memories),
                "reflection_content": reflection_content,
                "reflection_id": str(reflection_doc.get("_id")) if reflection_doc else None,
                "memories_pruned": pruned_count,
                "entities_extracted": entities_extracted,
                "procedural_extracted": procedural_extracted,
            }

        except (PyMongoError, OperationFailure) as e:
            logger.exception(f"❌ [Reflection] Failed for user {user_id}: {e}")
            return {
                "success": False,
                "reason": f"Database error: {e}",
                "memories_processed": 0,
            }

    def _generate_reflection(self, memories: list[dict], user_id: str) -> str | None:
        """
        Generate a reflection summary using LLM.

        Args:
            memories: List of memory documents
            user_id: User ID for context

        Returns:
            Reflection content string, or None if generation failed
        """
        if not self.llm_service:
            logger.debug("LLM service not available for reflection generation")
            return None

        # Format memories for the prompt
        memory_texts = []
        for m in memories:
            text = m.get("text", "")
            importance = m.get("importance", 0.5)
            category = m.get("category") or m.get("metadata", {}).get("category") or "biographical"
            created = m.get("created_at", "")
            if isinstance(created, datetime):
                created = created.strftime("%Y-%m-%d %H:%M")

            memory_texts.append(f"- [{category}] (importance: {importance:.2f}) {text}")

        memories_str = "\n".join(memory_texts)

        reflection_prompt = (
            "You are a memory consolidation system. Your task is to review "
            "a user's recent memories and create a high-level summary that "
            "captures the essential information.\n\n"
            f"RECENT MEMORIES:\n{memories_str}\n\n"
            "INSTRUCTIONS:\n"
            "1. Create a concise narrative summary (2-4 paragraphs) of the "
            "user's current state\n"
            "2. Focus on:\n"
            "   - Key facts and changes in the user's life/preferences\n"
            "   - Evolving goals or projects\n"
            "   - Important relationships or decisions mentioned\n"
            "   - Patterns or themes across memories\n"
            '3. Use third person ("The user...")\n'
            "4. Prioritize higher-importance memories\n"
            "5. Omit trivial or redundant information\n"
            "6. Note any contradictions or changes in preferences\n\n"
            "Generate a reflection summary:"
        )

        try:
            # Call async chat_completion from sync context
            try:
                asyncio.get_running_loop()
                # We're in an async context, use async version instead
                logger.debug(
                    "Sync _generate_reflection called from async context, "
                    "use _generate_reflection_async instead"
                )
                return None
            except RuntimeError:
                # No running loop, safe to use asyncio.run()
                content = asyncio.run(
                    self.llm_service.chat_completion(
                        messages=[{"role": "user", "content": reflection_prompt}],
                        model=self.llm_model,
                        temperature=self.temperature,
                    )
                )
                logger.info(f"✅ [Reflection] Generated reflection ({len(content)} chars)")
                return content

        except (
            AttributeError,
            RuntimeError,
            ConnectionError,
            TimeoutError,
            ValueError,
            TypeError,
        ) as e:
            logger.warning(f"❌ [Reflection] LLM call failed: {e}")
            return None

    async def _generate_reflection_async(self, memories: list[dict], user_id: str) -> str | None:
        """
        Async version of reflection generation.

        Args:
            memories: List of memory documents
            user_id: User ID for context

        Returns:
            Reflection content string, or None if generation failed
        """
        if not self.llm_service:
            logger.debug("LLM service not available for reflection generation")
            return None

        # Format memories for the prompt
        memory_texts = []
        for m in memories:
            text = m.get("text", "")
            importance = m.get("importance", 0.5)
            category = m.get("category") or m.get("metadata", {}).get("category") or "biographical"
            created = m.get("created_at", "")
            if isinstance(created, datetime):
                created = created.strftime("%Y-%m-%d %H:%M")

            memory_texts.append(f"- [{category}] (importance: {importance:.2f}) {text}")

        memories_str = "\n".join(memory_texts)

        reflection_prompt = (
            "You are a memory consolidation system. Your task is to review "
            "a user's recent memories and create a high-level summary that "
            "captures the essential information.\n\n"
            f"RECENT MEMORIES:\n{memories_str}\n\n"
            "INSTRUCTIONS:\n"
            "1. Create a concise narrative summary (2-4 paragraphs) of the "
            "user's current state\n"
            "2. Focus on:\n"
            "   - Key facts and changes in the user's life/preferences\n"
            "   - Evolving goals or projects\n"
            "   - Important relationships or decisions mentioned\n"
            "   - Patterns or themes across memories\n"
            '3. Use third person ("The user...")\n'
            "4. Prioritize higher-importance memories\n"
            "5. Omit trivial or redundant information\n"
            "6. Note any contradictions or changes in preferences\n\n"
            "Generate a reflection summary:"
        )

        try:
            content = await self.llm_service.chat_completion(
                messages=[{"role": "user", "content": reflection_prompt}],
                model=self.llm_model,
                temperature=self.temperature,
            )

            logger.info(f"⚡ [Reflection Async] Generated reflection ({len(content)} chars)")
            return content

        except (
            AttributeError,
            RuntimeError,
            ConnectionError,
            TimeoutError,
            ValueError,
            TypeError,
        ) as e:
            logger.warning(f"❌ [Reflection Async] LLM call failed: {e}")
            return None

    async def should_reflect_async(self, user_id: str) -> tuple[bool, str]:
        """
        Async version of should_reflect with parallel DB queries.

        Args:
            user_id: User ID to check

        Returns:
            Tuple of (should_reflect: bool, reason: str)
        """
        if not self.enabled:
            return False, "Reflection service disabled"

        try:
            # Run independent queries in parallel
            async def _get_last_reflection():
                if self.reflections_collection:
                    return await asyncio.to_thread(
                        self.reflections_collection.find_one,
                        {"user_id": user_id},
                        sort=[("created_at", -1)],
                    )
                return None

            async def _count_recent_memories():
                cutoff = datetime.now(timezone.utc) - timedelta(hours=self.interval_hours)
                return await asyncio.to_thread(
                    self.memories_collection.count_documents,
                    {"user_id": user_id, "created_at": {"$gte": cutoff}},
                )

            async def _count_total_memories():
                return await asyncio.to_thread(
                    self.memories_collection.count_documents,
                    {"user_id": user_id},
                )

            # Parallel fetch
            last_reflection, memory_count, total_memories = await asyncio.gather(
                _get_last_reflection(),
                _count_recent_memories(),
                _count_total_memories(),
            )

            logger.info(
                f"⚡ [Reflection] Parallel check: last_reflection={bool(last_reflection)}, "
                f"recent={memory_count}, total={total_memories}"
            )

            # Check time-based trigger
            if last_reflection:
                last_time = last_reflection.get("created_at")
                if last_time:
                    hours_since = (datetime.now(timezone.utc) - last_time).total_seconds() / 3600
                    if hours_since < self.interval_hours:
                        return False, f"Last reflection was {hours_since:.1f}h ago"
                    if hours_since >= self.interval_hours:
                        return True, f"Time-based trigger: {hours_since:.1f}h since last reflection"

            # Check count-based trigger
            if memory_count >= self.message_threshold:
                return (
                    True,
                    f"Memory count ({memory_count}) exceeds threshold ({self.message_threshold})",
                )

            # No previous reflection - check if enough memories exist
            if not last_reflection and total_memories >= self.message_threshold:
                return True, "No previous reflection and enough memories accumulated"

            return False, "No reflection trigger conditions met"

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"⚠️ Error checking reflection trigger: {e}")
            return False, f"Error: {e}"

    async def run_reflection_async(self, user_id: str, force: bool = False) -> dict[str, Any]:
        """
        Async version of run_reflection with parallel operations.

        This method:
        1. Fetches memories from the last period
        2. Uses async LLM to create a narrative summary
        3. Stores the reflection (if enabled)
        4. Prunes low-salience memories (if enabled)

        Args:
            user_id: User ID to run reflection for
            force: Force reflection even if trigger conditions not met

        Returns:
            Dictionary with reflection results
        """
        if not self.enabled and not force:
            return {
                "success": False,
                "reason": "Reflection service disabled",
                "memories_processed": 0,
            }

        # Check if we should reflect (using async version)
        should, reason = await self.should_reflect_async(user_id)
        if not should and not force:
            return {
                "success": False,
                "reason": reason,
                "memories_processed": 0,
            }

        logger.info(f"⚡ [Reflection Async] Starting for user {user_id}: {reason}")

        try:
            # 1. Fetch memories from the last period
            cutoff = datetime.now(timezone.utc) - timedelta(hours=self.interval_hours)

            memories = await asyncio.to_thread(
                lambda: list(
                    self.memories_collection.find(
                        {
                            "user_id": user_id,
                            "created_at": {"$gte": cutoff},
                        }
                    ).sort("created_at", 1)
                )
            )

            if not memories:
                logger.info(f"⚡ [Reflection Async] No memories to reflect for user {user_id}")
                return {
                    "success": True,
                    "reason": "No memories in period",
                    "memories_processed": 0,
                }

            logger.info(f"⚡ [Reflection Async] Found {len(memories)} memories to consolidate")

            # 2. Generate reflection using async LLM
            reflection_content = await self._generate_reflection_async(memories, user_id)

            if not reflection_content:
                return {
                    "success": False,
                    "reason": "Failed to generate reflection",
                    "memories_processed": len(memories),
                }

            # 3. Store the reflection
            reflection_doc = None
            if self.store_reflections and self.reflections_collection:
                reflection_doc = {
                    "user_id": user_id,
                    "content": reflection_content,
                    "period_start": cutoff,
                    "period_end": datetime.now(timezone.utc),
                    "type": "periodic_summary",
                    "memories_consolidated": len(memories),
                    "memory_ids": [str(m["_id"]) for m in memories],
                    "created_at": datetime.now(timezone.utc),
                }

                result = await asyncio.to_thread(
                    self.reflections_collection.insert_one, reflection_doc
                )
                logger.info(f"⚡ [Reflection Async] Stored reflection: {result.inserted_id}")

            # 4. Prune low-salience memories
            pruned_count = 0
            if self.min_salience_to_keep > 0:
                pruned_count = await asyncio.to_thread(
                    self._prune_low_salience_memories, memories, user_id
                )

            return {
                "success": True,
                "reason": reason,
                "memories_processed": len(memories),
                "reflection_content": reflection_content,
                "reflection_id": str(reflection_doc.get("_id")) if reflection_doc else None,
                "memories_pruned": pruned_count,
            }

        except (PyMongoError, OperationFailure) as e:
            logger.exception(f"❌ [Reflection Async] Failed for user {user_id}: {e}")
            return {
                "success": False,
                "reason": f"Database error: {e}",
                "memories_processed": 0,
            }

    def _prune_low_salience_memories(self, memories: list[dict], user_id: str) -> int:
        """
        Prune low-importance memories after reflection.

        Args:
            memories: List of memory documents that were reflected
            user_id: User ID

        Returns:
            Number of memories pruned
        """
        if self.min_salience_to_keep <= 0:
            return 0

        try:
            # Find memories below the salience threshold
            low_salience_ids = [
                m["_id"] for m in memories if m.get("importance", 0.5) < self.min_salience_to_keep
            ]

            if not low_salience_ids:
                logger.info("🔄 [Reflection] No low-salience memories to prune")
                return 0

            # Delete low-salience memories
            result = self.memories_collection.delete_many(
                {
                    "_id": {"$in": low_salience_ids},
                    "user_id": user_id,
                }
            )

            pruned = result.deleted_count
            logger.info(
                f"🗑️ [Reflection] Pruned {pruned} memories with importance < "
                f"{self.min_salience_to_keep}"
            )

            return pruned

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"⚠️ [Reflection] Failed to prune memories: {e}")
            return 0

    def get_recent_reflections(
        self,
        user_id: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Get recent reflections for a user.

        Args:
            user_id: User ID
            limit: Maximum number of reflections to return

        Returns:
            List of reflection documents
        """
        if not self.reflections_collection:
            return []

        try:
            cursor = self.reflections_collection.find(
                {"user_id": user_id},
                sort=[("created_at", -1)],
                limit=limit,
            )

            reflections = []
            for doc in cursor:
                reflections.append(
                    {
                        "id": str(doc["_id"]),
                        "content": doc.get("content", ""),
                        "type": doc.get("type", "periodic_summary"),
                        "memories_consolidated": doc.get("memories_consolidated", 0),
                        "period_start": doc.get("period_start"),
                        "period_end": doc.get("period_end"),
                        "created_at": doc.get("created_at"),
                    }
                )

            return reflections

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"⚠️ Failed to get reflections: {e}")
            return []

    def get_stats(self, user_id: str) -> dict[str, Any]:
        """
        Get reflection statistics for a user.

        Args:
            user_id: User ID

        Returns:
            Dictionary with reflection stats
        """
        try:
            stats = {
                "enabled": self.enabled,
                "interval_hours": self.interval_hours,
                "message_threshold": self.message_threshold,
                "min_salience_to_keep": self.min_salience_to_keep,
            }

            if self.reflections_collection:
                stats["total_reflections"] = self.reflections_collection.count_documents(
                    {"user_id": user_id}
                )

                last = self.reflections_collection.find_one(
                    {"user_id": user_id},
                    sort=[("created_at", -1)],
                )
                if last:
                    stats["last_reflection"] = last.get("created_at")
                    stats["last_memories_consolidated"] = last.get("memories_consolidated", 0)

            # Check if reflection is due
            should, reason = self.should_reflect(user_id)
            stats["reflection_due"] = should
            stats["reflection_reason"] = reason

            return stats

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"⚠️ Failed to get reflection stats: {e}")
            return {"enabled": self.enabled, "error": str(e)}

    def _extract_entities(self, memories: list[dict], user_id: str) -> int:
        """
        Extract entities from episodic memories and create entity memories.

        Args:
            memories: List of episodic memory documents
            user_id: User ID

        Returns:
            Number of entities extracted
        """
        if not self.extract_entities or not self.llm_service:
            return 0

        try:
            # Combine episodic memories for entity extraction
            episodic_texts = [
                m.get("text", "") for m in memories if m.get("memory_type") == "episodic"
            ]
            if not episodic_texts:
                return 0

            combined_text = "\n".join(episodic_texts[:10])  # Limit to avoid token limits

            prompt = (
                f"""Extract entities (people, projects, objects, concepts) """
                f"""from these episodic memories.
For each entity, extract structured facts.

Memories:
{combined_text}

Return JSON array:
[
    {{
        "entity_name": "Project Phoenix",
        "entity_type": "project",
        "attributes": {{
            "status": "active",
            "lead": "Alice"
        }}
    }},
    ...
]

Return ONLY valid JSON array."""
            )

            # Call async chat_completion from sync context
            try:
                asyncio.get_running_loop()
                # We're in an async context, skip for now
                logger.debug("Sync _extract_entities called from async context")
                return 0
            except RuntimeError:
                # No running loop, safe to use asyncio.run()
                result_text = asyncio.run(
                    self.llm_service.chat_completion(
                        messages=[{"role": "user", "content": prompt}],
                        model=self.llm_model,
                        temperature=0.3,
                    )
                )

            import json

            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0].strip()
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0].strip()

            entities = json.loads(result_text)

            # Create entity memories
            count = 0
            for entity in entities:
                entity_name = entity.get("entity_name", "")
                if not entity_name:
                    continue

                # Create entity memory document
                entity_doc = {
                    "memory_type": "entity",
                    "text": f"{entity_name}: {json.dumps(entity.get('attributes', {}))}",
                    "entity_name": entity_name,
                    "entity_type": entity.get("entity_type", "general"),
                    "attributes": entity.get("attributes", {}),
                    "user_id": str(user_id),
                    "created_at": datetime.now(timezone.utc),
                    "updated_at": datetime.now(timezone.utc),
                    "is_active": True,
                }

                # Add embedding if possible (would need embedding service)
                # For now, store without embedding (can be added later)

                self.memories_collection.insert_one(entity_doc)
                count += 1

                # Link to GraphService if available
                if self.link_to_graph and self.graph_service:
                    try:
                        entity_type = entity.get("entity_type", "entity")
                        node_id = f"{entity_type}:" f"{entity_name.lower().replace(' ', '_')}"
                        self.graph_service.upsert_node(
                            node_id=node_id,
                            node_type=entity_type,
                            name=entity_name,
                            properties=entity.get("attributes", {}),
                            user_id=str(user_id),
                        )
                    except (AttributeError, TypeError, ValueError, RuntimeError) as e:
                        logger.debug(f"Failed to link entity to graph: {e}")

            logger.info(f"✅ [Consolidation] Extracted {count} entities")
            return count

        except (
            AttributeError,
            RuntimeError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
            PyMongoError,
        ) as e:
            logger.warning(f"⚠️ Entity extraction failed: {e}")
            return 0

    def _extract_procedural(self, memories: list[dict], user_id: str) -> int:
        """
        Extract procedural knowledge from memories.

        Args:
            memories: List of memory documents
            user_id: User ID

        Returns:
            Number of procedural memories extracted
        """
        if not self.extract_procedural:
            return 0

        try:
            # Look for procedural content in memories
            count = 0
            for memory in memories:
                text = memory.get("text", "")
                if not text:
                    continue

                # Simple heuristic: check for procedural indicators
                procedural_indicators = [
                    "step",
                    "how to",
                    "procedure",
                    "workflow",
                    "def ",
                    "import ",
                ]
                if any(indicator in text.lower() for indicator in procedural_indicators):
                    # Update memory type to procedural if not already
                    if memory.get("memory_type") != "procedural":
                        self.memories_collection.update_one(
                            {"_id": memory["_id"]},
                            {"$set": {"memory_type": "procedural"}},
                        )
                        count += 1

            if count > 0:
                logger.info(f"✅ [Consolidation] Extracted {count} procedural memories")
            return count

        except (
            AttributeError,
            RuntimeError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
            PyMongoError,
        ) as e:
            logger.warning(f"⚠️ Procedural extraction failed: {e}")
            return 0

    def _route_memories(self, memories: list[dict], user_id: str) -> None:
        """
        Route memories to appropriate types based on content.

        Args:
            memories: List of memory documents
            user_id: User ID
        """
        if not self.route_by_type:
            return

        try:
            # This is a simplified routing - in practice, would use LLM classification
            # For now, mark episodic memories as consolidated
            episodic_ids = [
                str(m["_id"])
                for m in memories
                if m.get("memory_type") == "episodic" and not m.get("consolidated", False)
            ]

            if episodic_ids:
                from bson import ObjectId

                self.memories_collection.update_many(
                    {"_id": {"$in": [ObjectId(id) for id in episodic_ids]}},
                    {"$set": {"consolidated": True}},
                )
                logger.info(
                    f"✅ [Consolidation] Marked {len(episodic_ids)} "
                    f"episodic memories as consolidated"
                )

        except (PyMongoError, AttributeError, TypeError, ValueError) as e:
            logger.warning(f"⚠️ Memory routing failed: {e}")


def create_reflection_service(
    app_slug: str,
    memories_collection: Any,
    reflections_collection: Any | None = None,
    config: dict[str, Any] | None = None,
) -> ReflectionService:
    """
    Factory function to create a ReflectionService.

    Args:
        app_slug: Application slug
        memories_collection: PyMongo collection for memories
        reflections_collection: PyMongo collection for reflections
        config: Reflection configuration from manifest

    Returns:
        Configured ReflectionService instance
    """
    return ReflectionService(
        app_slug=app_slug,
        memories_collection=memories_collection,
        reflections_collection=reflections_collection,
        config=config,
        graph_service=None,  # Graph service should be passed when creating ReflectionService
    )
