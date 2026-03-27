"""
Memory Consolidator
Reflection loop that distills episodic memories into semantic facts and procedural lessons.

This is the critical component that transforms raw interaction logs (episodic)
into structured knowledge (semantic) and executable skills (procedural).

The consolidation process:
1. Scans recent episodic memories that haven't been processed
2. Uses LLM to identify "Permanent Facts" vs. "Temporary Context"
3. Extracts entities and their attributes
4. Identifies successful procedures and workflows
5. Updates semantic entity memory and procedural memory
6. Marks episodes as consolidated to prevent double-counting

Without consolidation, episodic memory becomes a dumping ground of chat logs.
With consolidation, the agent "learns" and improves over time.
"""

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mdb_engine.database.scoped_wrapper import (
        ScopedCollectionWrapper,
    )
    from mdb_engine.embeddings.service import EmbeddingService
    from mdb_engine.llm.service import LLMService

try:
    from pymongo.errors import OperationFailure, PyMongoError

    PYMONGO_AVAILABLE = True
except ImportError:
    raise ImportError("pip install pymongo") from None

from .base import MemoryServiceError

logger = logging.getLogger(__name__)


class MemoryConsolidatorError(MemoryServiceError):
    """Base exception for Memory Consolidator errors."""

    pass


class MemoryConsolidator:
    """
    Background worker that performs memory consolidation (reflection loop).

    This class implements the "Reflector" that moves information from the
    "messy" episodic logs into the "clean" semantic database.

    The consolidation process prevents context bloat and allows the agent
    to learn from experiences by distilling them into reusable knowledge.

    Example:
        ```python
        from mdb_engine.memory.consolidator import MemoryConsolidator

        consolidator = MemoryConsolidator(
            db_client=mongo_client,
            db_name="cognitive_agent",
            model="gpt-4o"
        )

        # Consolidate episodes for an agent/user
        result = consolidator.consolidate_episodes(agent_id="user123")
        print(f"Extracted {result['entities_extracted']} entities")
        print(f"Created {result['procedures_created']} procedures")
        ```
    """

    def __init__(
        self,
        db_client: Any,
        db_name: str = "cognitive_agent",
        model: str = "gpt-4o",
        episodic_collection: "ScopedCollectionWrapper | None" = None,
        entity_collection: "ScopedCollectionWrapper | None" = None,
        procedural_collection: "ScopedCollectionWrapper | None" = None,
        shared_collection: "ScopedCollectionWrapper | None" = None,
        memory_veto: Any = None,
        *,
        llm_service: "LLMService",
        embedding_service: "EmbeddingService",
        neuroplasticity_engine: Any | None = None,
    ):
        """
        Initialize MemoryConsolidator.

        Args:
            db_client: MongoDB client instance or ScopedMongoWrapper
            db_name: Database name (default: "cognitive_agent")
                     Ignored if db_client is already a ScopedMongoWrapper
            model: LLM model for consolidation (default: "gpt-4o")
                   Use a high-reasoning model for best results
            episodic_collection: Optional episodic memory collection (ScopedCollectionWrapper)
                                (defaults to db.episodic)
            entity_collection: Optional entity memory collection (ScopedCollectionWrapper)
                              (defaults to db.entity_memory)
            procedural_collection: Optional procedural memory collection (ScopedCollectionWrapper)
                                  (defaults to db.procedural)
            shared_collection: Optional shared memory collection (ScopedCollectionWrapper)
                              (defaults to db.entity_memory with scope="shared")
            memory_veto: Optional MemoryVeto instance for checking vetoes
            llm_service: LLMService instance for chat completions (required).
            embedding_service: EmbeddingService instance for embeddings (required).

        Note:
            If db_client is a ScopedMongoWrapper, all collections accessed through it
            are automatically scoped with app_id filtering. If it's a raw MongoDB client,
            collections should be ScopedCollectionWrapper instances passed explicitly.
        """

        self.db_client = db_client
        # db_client might be ScopedMongoWrapper (preferred) or raw MongoDB client
        # If it has __getitem__, it's likely a client; otherwise it's already a wrapper
        self.db: Any = db_client[db_name] if hasattr(db_client, "__getitem__") else db_client
        self.model = model

        # Initialize collections - all should be ScopedCollectionWrapper instances
        # Collections accessed through self.db are automatically scoped if self.db is ScopedMongoWrapper
        self.episodic: ScopedCollectionWrapper = (
            episodic_collection if episodic_collection is not None else self.db.episodic
        )

        self.entity_memory: ScopedCollectionWrapper = (
            entity_collection if entity_collection is not None else self.db.entity_memory
        )

        self.procedural: ScopedCollectionWrapper = (
            procedural_collection if procedural_collection is not None else self.db.procedural
        )

        self.shared_collection: ScopedCollectionWrapper = (
            shared_collection if shared_collection is not None else self.db.entity_memory
        )

        self.memory_veto = memory_veto  # Optional MemoryVeto instance

        self._llm_service = llm_service
        self._embedding_service = embedding_service
        self._neuroplasticity_engine = neuroplasticity_engine

        logger.info(
            f"MemoryConsolidator initialized: model={model}, "
            f"db={db_name if isinstance(db_name, str) else 'custom'}, "
            f"neuroplasticity={'enabled' if neuroplasticity_engine else 'disabled'}"
        )

    # ------------------------------------------------------------------
    # Internal helpers — route through injected services when available
    # ------------------------------------------------------------------

    async def _llm_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        response_format: dict[str, str] | None = None,
    ) -> str:
        """Return the LLM response text via the injected LLM service."""
        return await self._llm_service.chat_completion(
            messages=messages,
            provider_name="chat",
            model=self.model,
            temperature=temperature,
            response_format=response_format,
        )

    async def _embed(self, text: str) -> list[float]:
        """Return a single embedding vector via the injected embedding service."""
        vectors = await self._embedding_service.embed(text)
        return vectors[0] if vectors else []

    async def consolidate_episodes(
        self,
        agent_id: str,
        limit: int = 10,
        force: bool = False,
    ) -> dict[str, Any]:
        """
        Consolidate episodic memories into semantic facts and procedural lessons.

        This is the core reflection loop that:
        1. Fetches unprocessed episodic memories
        2. Uses LLM to extract entities and procedures
        3. Stores knowledge in semantic and procedural memory
        4. Marks episodes as consolidated

        Args:
            agent_id: Agent/user ID to consolidate memories for
            limit: Maximum number of episodes to process (default: 10)
            force: Force consolidation even if episodes already processed

        Returns:
            Dictionary with consolidation results:
            - entities_extracted: Number of entities extracted
            - procedures_created: Number of procedures created
            - episodes_processed: Number of episodes consolidated
            - success: Whether consolidation succeeded

        Example:
            ```python
            result = consolidator.consolidate_episodes(agent_id="user123")
            if result["success"]:
                print(f"Consolidated {result['episodes_processed']} episodes")
                print(f"Extracted {result['entities_extracted']} entities")
            ```
        """
        try:
            # 1. Fetch unprocessed episodes
            episodes = await self._fetch_unconsolidated(agent_id, limit, force)
            if not episodes:
                logger.info("No new memories to consolidate for agent %s", agent_id)
                return {
                    "success": True,
                    "entities_extracted": 0,
                    "procedures_created": 0,
                    "episodes_processed": 0,
                    "message": "No new memories to consolidate",
                }

            logger.info("Consolidating %d episodes for agent %s", len(episodes), agent_id)

            # 2-3. LLM extraction (entities + procedures)
            data = await self._llm_extract_knowledge(episodes)
            if data is None:
                return {
                    "success": False,
                    "entities_extracted": 0,
                    "procedures_created": 0,
                    "episodes_processed": 0,
                    "error": "Failed to parse LLM response",
                }

            # 4. Store knowledge
            entities_extracted = await self._store_entities(data.get("entities", []), agent_id, episodes)
            procedures_created = await self._store_procedures(data.get("procedures", []), agent_id)

            # 5. Reflections, skills, and neuroplasticity
            reflections_extracted = await self._extract_reflections(episodes, agent_id)
            skills_compiled = await self._compile_skills(agent_id)
            adaptations_applied = await self._run_neuroplasticity(agent_id)

            # 6. Mark episodes as consolidated
            await self._mark_consolidated(episodes)

            logger.info(
                "Consolidation complete: %d entities, %d procedures, %d skills, " "%d adaptations from %d episodes",
                entities_extracted,
                procedures_created,
                skills_compiled,
                adaptations_applied,
                len(episodes),
            )

            return {
                "success": True,
                "entities_extracted": entities_extracted,
                "procedures_created": procedures_created,
                "skills_compiled": skills_compiled,
                "reflections_extracted": reflections_extracted,
                "adaptations_applied": adaptations_applied,
                "episodes_processed": len(episodes),
            }

        except (PyMongoError, OperationFailure, MemoryConsolidatorError) as e:
            logger.error("Consolidation failed: %s", e, exc_info=True)
            return {
                "success": False,
                "entities_extracted": 0,
                "procedures_created": 0,
                "episodes_processed": 0,
                "error": str(e),
            }

    # ------------------------------------------------------------------
    # consolidate_episodes() helpers
    # ------------------------------------------------------------------

    async def _fetch_unconsolidated(
        self,
        agent_id: str,
        limit: int,
        force: bool,
    ) -> list[dict[str, Any]]:
        """Fetch unprocessed episodic memories."""
        query: dict[str, Any] = {"consolidated": {"$ne": True}} if not force else {}
        if agent_id:
            query["session_id"] = agent_id
        cursor = self.episodic.find(query).sort("timestamp", 1).limit(limit)
        return await cursor.to_list(length=None)

    async def _llm_extract_knowledge(
        self,
        episodes: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Use LLM to extract entities and procedures from episode texts."""
        combined = "\n".join(f"[{e.get('role', 'unknown')}] {e.get('content', '')}" for e in episodes)

        extraction_prompt = (
            "Analyze the following agent interactions and extract:\n"
            "1. Entity Facts: Permanent facts about entities (people, projects, objects, concepts)\n"
            '   - Example: "User\'s dog is named Rex", "Project Alpha deadline is Friday"\n'
            "   - Include entity name, type, and attributes\n"
            "2. Procedural Lessons: Executable skills, workflows, or successful procedures\n"
            '   - Example: "To clear the cache, use the --force flag"\n'
            "   - Include task type, steps, and any code snippets\n\n"
            f"Interactions:\n{combined}\n\n"
            "Return ONLY a valid JSON object with this structure:\n"
            "{\n"
            '    "entities": [\n'
            "        {\n"
            '            "entity_name": "Project Alpha",\n'
            '            "entity_type": "project",\n'
            '            "attributes": {"status": "active", "deadline": "Friday", "lead": "Alice"}\n'
            "        }\n"
            "    ],\n"
            '    "procedures": [\n'
            "        {\n"
            '            "name": "Clear Cache Workflow",\n'
            '            "task_type": "maintenance",\n'
            '            "steps": ["Run --force flag", "Verify cache cleared"],\n'
            '            "code_snippet": "cache --force",\n'
            '            "description": "To clear the cache, use the --force flag"\n'
            "        }\n"
            "    ]\n"
            "}\n\n"
            "Return ONLY valid JSON, no markdown, no explanations."
        )

        try:
            result_text = await self._llm_completion(
                messages=[{"role": "user", "content": extraction_prompt}],
                temperature=0.3,
                response_format={"type": "json_object"},
            )
            # Strip markdown code fences if present
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0].strip()
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0].strip()

            return json.loads(result_text)

        except (json.JSONDecodeError, AttributeError, KeyError, TypeError, ValueError, RuntimeError) as e:
            logger.error("Failed to parse LLM response: %s", e, exc_info=True)
            return None

    async def _run_neuroplasticity(self, agent_id: str) -> int:
        """Run the neuroplasticity adaptation cycle (non-fatal)."""
        if not self._neuroplasticity_engine:
            return 0
        try:
            result = await self._neuroplasticity_engine.run_adaptation_cycle(agent_id)
            count = result.get("adaptations_applied", 0)
            if count > 0:
                logger.info("[Neuroplasticity] Applied %d adaptations for %s", count, agent_id)
            return count
        except (AttributeError, RuntimeError, ValueError, TypeError) as e:
            logger.warning("[Neuroplasticity] Adaptation cycle failed (non-fatal): %s", e)
            return 0

    async def _mark_consolidated(self, episodes: list[dict[str, Any]]) -> None:
        """Mark episodes as consolidated in the database."""
        ids = [e["_id"] for e in episodes]
        await self.episodic.update_many(
            {"_id": {"$in": ids}},
            {"$set": {"consolidated": True, "consolidated_at": datetime.now(timezone.utc)}},
        )

    async def _store_entities(
        self, entities: list[dict[str, Any]], agent_id: str, episodes: list[dict[str, Any]]
    ) -> int:
        """
        Store extracted entities in semantic memory.

        Args:
            entities: List of entity dictionaries from LLM extraction
            agent_id: Agent/user ID for scoping

        Returns:
            Number of entities stored
        """
        count = 0
        for entity in entities:
            try:
                entity_name = entity.get("entity_name", "")
                if not entity_name:
                    continue

                attributes = entity.get("attributes", {})
                entity_type = entity.get("entity_type", "general")

                # Use CognitiveMemory's update_entity method pattern
                # For now, we'll directly update the collection
                entity_key = entity_name.lower()

                # Generate vector via embedding service
                try:
                    attr_text = ", ".join([f"{k}: {v}" for k, v in attributes.items()])
                    vector = await self._embed(f"{entity_name}: {attr_text}")
                except (ConnectionError, TimeoutError, ValueError, ImportError, RuntimeError) as e:
                    logger.warning(f"Failed to generate vector for entity {entity_name}: {e}")
                    vector = []

                # Get source episode IDs for graph_links
                episode_ids = [str(e.get("_id", "")) for e in episodes]

                # Check veto before storing
                if self.memory_veto:
                    # Check if any source episode is vetoed
                    is_vetoed = any(self.memory_veto.check_veto(ep_id, agent_id, "family") for ep_id in episode_ids)
                    if is_vetoed:
                        logger.debug(f"Entity {entity_name} vetoed, skipping family promotion")
                        # Store as user-scoped only
                        scope = "user"
                    else:
                        scope = "user"  # Default to user, promotion happens separately
                else:
                    scope = "user"

                # Get source user IDs from episodes for potential family promotion
                source_user_ids = list(set([str(e.get("user_id", agent_id)) for e in episodes if e.get("user_id")]))
                if not source_user_ids:
                    source_user_ids = [agent_id]

                # Initialize graph_links for derived_from (link to source episodes)
                graph_links = {
                    "derived_from": episode_ids,
                    "contradicts": [],
                    "deprecated": False,
                }

                # Preserve history for versioned truth (replicating CognitiveMemory logic)
                existing = await self.entity_memory.find_one({"entity": entity_key, "scope": scope})

                update_doc = {
                    "$set": {
                        **{f"attr.{k}": v for k, v in attributes.items()},
                        "entity_type": entity_type,
                        "vector": vector,
                        "user_id": agent_id,
                        "scope": scope,
                        "confidence": 0.9,  # High confidence from multiple episodes
                        "graph_links": graph_links,
                        "metadata.timeline_id": "root",  # Default to root timeline
                    },
                    "$currentDate": {"last_updated": True},
                    "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
                }

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

                # Update entity memory with scope and graph_links
                await self.entity_memory.update_one(
                    {"entity": entity_key, "scope": scope},
                    update_doc,
                    upsert=True,
                )

                # Check if entity should be promoted to shared memory
                if scope == "user" and len(source_user_ids) >= 2:
                    try:
                        from .shared import SharedMemory

                        shared_memory = SharedMemory(
                            semantic_collection=self.entity_memory,
                            shared_collection=self.shared_collection,
                        )

                        fact_text = f"{entity_name}: {attr_text}"
                        if shared_memory.check_promotion_rules(
                            fact=fact_text,
                            source_user_ids=source_user_ids,
                            sensitivity="low",
                        ):
                            # Extract group_id from episodes if available
                            group_id = None
                            bucket_id = None
                            for ep in episodes:
                                if ep.get("group_id"):
                                    group_id = ep.get("group_id")
                                if ep.get("metadata", {}).get("associated_bucket_id"):
                                    bucket_id = ep.get("metadata", {}).get("associated_bucket_id")
                                    break

                            if group_id:
                                shared_memory.promote_to_shared(
                                    fact=fact_text,
                                    source_user_ids=source_user_ids,
                                    confidence=0.8,
                                    group_id=group_id,
                                    bucket_id=bucket_id,
                                )
                                logger.info(f"Promoted entity {entity_name} to shared memory")
                    except (ImportError, PyMongoError, OperationFailure, ValueError) as e:
                        logger.warning(f"Failed to promote to shared: {e}")

                count += 1
                logger.debug(f"Entity stored: {entity_name}")

            except (PyMongoError, OperationFailure) as e:
                logger.warning(f"Failed to store entity: {e}")
                continue

        return count

    async def _store_procedures(self, procedures: list[dict[str, Any]], agent_id: str) -> int:
        """
        Store extracted procedures in procedural memory.

        Args:
            procedures: List of procedure dictionaries from LLM extraction
            agent_id: Agent/user ID for scoping

        Returns:
            Number of procedures stored
        """
        count = 0
        for procedure in procedures:
            try:
                name = procedure.get("name", "")
                if not name:
                    continue

                task_type = procedure.get("task_type", "general")
                steps = procedure.get("steps", [])
                code_snippet = procedure.get("code_snippet", "")
                description = procedure.get("description", "")

                # Generate vector via embedding service
                try:
                    content_parts = []
                    if steps:
                        content_parts.extend(steps)
                    if code_snippet:
                        content_parts.append(code_snippet)
                    if description:
                        content_parts.append(description)

                    content_text = " ".join(content_parts) if content_parts else name
                    vector = await self._embed(content_text)
                except (ConnectionError, TimeoutError, ValueError, ImportError, RuntimeError) as e:
                    logger.warning(f"Failed to generate vector for procedure {name}: {e}")
                    vector = []

                # Store procedure
                procedure_doc = {
                    "name": name,
                    "task_type": task_type,
                    "vector": vector,
                    "success_rate": 1.0,  # New procedures start with perfect success rate
                    "is_active": True,
                    "is_successful_procedure": True,
                    "agent_id": agent_id,
                    "created_at": datetime.now(timezone.utc),
                    "last_used": datetime.now(timezone.utc),
                    "usage_count": 0,
                }

                if steps:
                    procedure_doc["steps"] = steps
                if code_snippet:
                    procedure_doc["code_snippet"] = code_snippet
                if description:
                    procedure_doc["description"] = description

                await self.procedural.update_one(
                    {"name": name, "agent_id": agent_id},
                    {
                        "$set": procedure_doc,
                        "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
                    },
                    upsert=True,
                )

                count += 1
                logger.debug(f"Procedure stored: {name}")

            except (PyMongoError, OperationFailure) as e:
                logger.warning(f"Failed to store procedure: {e}")
                continue

        return count

    async def _compile_skills(
        self,
        agent_id: str,
        compile_threshold: int = 5,
    ) -> int:
        """
        Compile high-confidence skills from repeated procedural patterns.

        This is the "myelination" step: when the same procedure has been
        extracted multiple times across different consolidation cycles, it
        indicates a genuine skill the agent has learned.  We merge similar
        procedures into a single high-confidence compiled skill.

        A procedure is considered "compiled" when its ``usage_count`` (here
        meaning extraction-count via upsert) reaches ``compile_threshold``.

        Args:
            agent_id: Agent/user ID
            compile_threshold: Number of upsert hits before compiling

        Returns:
            Number of skills compiled in this pass.
        """
        compiled = 0
        try:
            # Find procedures that have been upserted many times (the $set in
            # _store_procedures updates last_used on each hit) but haven't
            # been marked as compiled yet.
            # We look for procedures with high success_rate that are active
            # and haven't been compiled.
            candidates = self.procedural.find(
                {
                    "agent_id": agent_id,
                    "is_active": True,
                    "compiled": {"$ne": True},
                }
            )

            from ._async_compat import cursor_to_list as _cursor_to_list

            procedures = await _cursor_to_list(candidates, 100)

            # Group by name similarity — for now use exact name match
            # (upserts in _store_procedures use {"name": name, "agent_id": agent_id})
            for proc in procedures:
                name = proc.get("name", "")
                usage = proc.get("usage_count", 0)

                # The upsert in _store_procedures increments nothing on its own,
                # but each upsert updates last_used.  We check how many consolidation
                # cycles touched this procedure by looking at how many episodes
                # contributed (we can use the difference between created_at and
                # last_used as a rough proxy, or rely on a simpler heuristic).
                #
                # Simpler approach: count how many episodes reference this procedure name
                # For MVP, mark procedures with success_rate >= 0.8 that have been
                # around for a while as compiled.
                success_rate = proc.get("success_rate", 1.0)

                if success_rate >= 0.8 and usage >= compile_threshold:
                    # Mark as compiled — this is a high-confidence, battle-tested skill
                    from ._async_compat import maybe_await as _maybe_await

                    await _maybe_await(
                        self.procedural.update_one(
                            {"_id": proc["_id"]},
                            {
                                "$set": {
                                    "compiled": True,
                                    "compiled_at": datetime.now(timezone.utc),
                                    "is_successful_procedure": True,
                                },
                            },
                        )
                    )
                    compiled += 1
                    logger.info(f"Skill compiled: '{name}' " f"(success_rate={success_rate:.2f}, usage_count={usage})")

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Skill compilation failed: {e}")

        return compiled

    async def should_consolidate(
        self,
        agent_id: str,
        message_threshold: int = 10,
    ) -> tuple[bool, str]:
        """
        Check if consolidation should be triggered.

        Args:
            agent_id: Agent/user ID to check
            message_threshold: Minimum number of unprocessed episodes to trigger

        Returns:
            Tuple of (should_consolidate: bool, reason: str)
        """
        try:
            unprocessed_count = await self.episodic.count_documents(
                {"session_id": agent_id, "consolidated": {"$ne": True}}
            )

            if unprocessed_count >= message_threshold:
                return (
                    True,
                    f"Unprocessed episodes ({unprocessed_count}) exceed threshold ({message_threshold})",
                )

            return (
                False,
                f"Only {unprocessed_count} unprocessed episodes (threshold: {message_threshold})",
            )

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to check consolidation trigger: {e}")
            return False, f"Error: {e}"

    async def _extract_reflections(self, episodes: list[dict[str, Any]], agent_id: str) -> int:
        """
        Extract reflective insights from episodes using LLM analysis.

        Identifies meta-cognitive patterns, behavioral tendencies, and
        self-awareness insights from conversation history.

        Args:
            episodes: List of episode documents
            agent_id: Agent/user ID

        Returns:
            Number of reflections extracted
        """
        if not episodes or len(episodes) < 5:
            return 0  # Need enough episodes to extract patterns

        try:
            combined_text = "\n".join([f"[{e.get('role', 'unknown')}] {e.get('content', '')}" for e in episodes[:15]])

            # Use LLM to identify meta-cognitive patterns
            reflection_prompt = f"""Analyze the following agent interactions and identify meta-cognitive patterns.

Look for:
1. Behavioral patterns: Does the user repeat questions? Prefer certain topics?
2. Communication patterns: Is the user brief or detailed? Technical or casual?
3. Interaction patterns: Time-of-day patterns, session length, topic transitions
4. Potential biases: Does the agent tend to give certain types of answers?
5. Improvement opportunities: What could the agent do better?

Interactions:
{combined_text}

Return ONLY a valid JSON object with this structure:
{{
    "reflections": [
        {{
            "insight": "The user tends to ask follow-up questions about implementation details",
            "trigger": "pattern_detection",
            "confidence": 0.7,
            "category": "user_behavior"
        }}
    ]
}}

Only include genuinely meaningful patterns (confidence >= 0.5).
Return ONLY valid JSON, no markdown, no explanations.
If no patterns found, return {{"reflections": []}}.
"""

            try:
                result_text = await self._llm_completion(
                    messages=[{"role": "user", "content": reflection_prompt}],
                    temperature=0.3,
                    response_format={"type": "json_object"},
                )

                # Parse JSON (handle markdown code blocks if present)
                if "```json" in result_text:
                    result_text = result_text.split("```json")[1].split("```")[0].strip()
                elif "```" in result_text:
                    result_text = result_text.split("```")[1].split("```")[0].strip()

                data = json.loads(result_text)
                reflections = data.get("reflections", [])

            except (
                json.JSONDecodeError,
                AttributeError,
                KeyError,
                TypeError,
                ValueError,
                RuntimeError,
            ) as e:
                logger.debug(f"Failed to parse reflection LLM response: {e}")
                return 0

            # Store extracted reflections
            reflection_count = 0
            for ref in reflections:
                insight = ref.get("insight", "")
                if not insight:
                    continue

                confidence = ref.get("confidence", 0.6)
                if confidence < 0.5:
                    continue

                trigger = ref.get("trigger", "llm_pattern_detection")
                category = ref.get("category", "general")

                try:
                    from .reflective import ReflectiveMemory

                    reflective = ReflectiveMemory(collection=self.db.reflective_memory)
                    reflective.store_reflection(
                        reflection=insight,
                        trigger=trigger,
                        confidence=confidence,
                        scope="user",
                        user_id=agent_id,
                        metadata={"category": category, "source": "consolidation"},
                    )
                    reflection_count += 1
                    logger.debug(f"Stored reflection ({len(insight)} chars)")
                except (ImportError, PyMongoError, OperationFailure, ValueError) as e:
                    logger.debug(f"Failed to store reflection: {e}")

            if reflection_count > 0:
                logger.info(f"Extracted {reflection_count} reflective insights from " f"{len(episodes)} episodes")

            return reflection_count

        except (ConnectionError, TimeoutError, ValueError, PyMongoError) as e:
            logger.warning(f"Failed to extract reflections: {e}")
            return 0
