"""
Memory Orchestrator
Integrates Short-Term Memory (Chat History) and Long-Term Memory (Vector Store).

This module provides a complete cognitive architecture:
- Short-Term Memory (STM): Raw chat history for immediate context
- Long-Term Memory (LTM): Vector store for semantic retrieval of facts
- Cognitive Engine: Orchestrates both to provide context-aware responses
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mdb_engine.core.protocols import GraphServiceProtocol, ProceduralMemoryProtocol
    from mdb_engine.database.scoped_wrapper import ScopedCollectionWrapper

from ..observability.tracing import create_span
from .base import BaseMemoryService
from .chat_history import ChatHistoryService
from .cognitive import CognitiveMemoryService, CognitiveMemoryServiceError
from .context_engineering import ContextEngineer

try:
    from pymongo import ASCENDING
    from pymongo.errors import PyMongoError
except ImportError:
    raise ImportError("pip install pymongo") from None

from ..core.validation import (
    validate_identifier,
    validate_session_id,
    validate_text_content,
    validate_user_id,
)

logger = logging.getLogger(__name__)


@dataclass
class _FormattedContext:
    """Intermediate container passed between ``chat()`` phases."""

    ltm_text: str = ""
    graph_text: str = ""
    skills_text: str = ""
    triggers_text: str = ""
    stm_messages: list[dict[str, str]] = field(default_factory=list)
    stm_summary: str | None = None


class CognitiveEngine:
    """
    The Brain: Combines LTM (Vectors) and STM (Chat History) to generate responses.

    This orchestrator provides a complete RAG pipeline:
    1. Save user message to STM
    2. Search LTM for relevant facts
    3. Fetch STM context (last K messages)
    4. Generate LLM response
    5. Save AI response to STM
    6. Extract new facts to LTM (async)
    """

    def __init__(
        self,
        app_slug: str,
        memory_service: BaseMemoryService | None = None,
        chat_history_collection: ScopedCollectionWrapper | None = None,
        memory_collection: ScopedCollectionWrapper | None = None,
        stm_context_limit: int = 10,
        ltm_search_limit: int = 5,
        auto_summarize_threshold: int = 20,
        llm_service: Any = None,
        *,
        graph_service: Any = None,
        procedural_memory: Any = None,
        enable_context_engineering: bool = True,
        stm_raw_window: int = 5,
        enable_entity_extraction: bool = True,
        enable_dynamic_persona: bool = True,
        graph_min_hop_distance: int = 2,
        graph_min_edges: int = 1,
        graph_deduplication_threshold: float = 0.70,
        graph_min_nodes: int = 2,
        summary_staleness_threshold: int = 10,
        max_prompt_tokens: int | None = None,
    ):
        """
        Initialize the Cognitive Engine.

        Args:
            app_slug: Application slug (required)
            memory_service: Optional BaseMemoryService instance (CognitiveMemoryService).
                           Will create CognitiveMemoryService if None.
            chat_history_collection: Motor AsyncIOMotorCollection instance (REQUIRED - must be from
                                    MDB-Engine connection manager)
            memory_collection: Motor AsyncIOMotorCollection instance (REQUIRED if memory_service not provided
                              - must be from MDB-Engine connection manager)
            stm_context_limit: Number of recent messages to include in context
            ltm_search_limit: Number of relevant memories to retrieve from LTM
            auto_summarize_threshold: Number of messages before auto-summarization kicks in
            llm_service: LLM service with async ``chat_completion`` method
                        (e.g. ``mdb_engine.llm.LLMService``)
            graph_service: Optional GraphService instance for GraphRAG functionality.
            procedural_memory: Optional ProceduralMemory instance for skill retrieval
                during chat.  Falls back to ``ltm._procedural_memory`` if not provided.
            enable_context_engineering: Enable Context Engineering features (default: True).
            stm_raw_window: Number of recent STM messages to keep raw before summarizing (default: 5).
            enable_entity_extraction: Enable entity fact extraction from memories (default: True).
            enable_dynamic_persona: Enable dynamic persona adaptation (default: True).
            graph_min_hop_distance: Minimum hop distance for graph_context nodes (default: 2).
            graph_min_edges: Minimum edges required for graph_context nodes (default: 1).
            graph_deduplication_threshold: Similarity threshold for deduplication (default: 0.70).
            graph_min_nodes: Minimum graph nodes required to include graph context (default: 2).
            summary_staleness_threshold: New messages before re-summarizing STM (default: 10).
            max_prompt_tokens: Optional token budget for system prompts. When set,
                ``TokenBudget`` from ``core.prompt_safety`` truncates the lowest-priority
                sections to stay within this limit. When ``None`` (default) prompts are
                assembled without truncation.
        """
        if chat_history_collection is None:
            raise ValueError(
                "chat_history_collection is REQUIRED. CognitiveEngine must use "
                "MDB-Engine's connection pool. "
                "Pass a Motor AsyncIOMotorCollection obtained from "
                "MDB-Engine's connection manager."
            )

        # Fail fast if a synchronous collection or string was passed.
        # A sync pymongo.Collection.create_index() returns a string, and
        # ``await "string"`` raises TypeError at runtime -- catch it here.
        if isinstance(chat_history_collection, str):
            raise TypeError(
                f"chat_history_collection must be an async Motor collection or "
                f"ScopedCollectionWrapper, not a string ('{chat_history_collection}'). "
                f"Use engine.get_scoped_db(slug) to get async collections."
            )
        try:
            from pymongo.collection import Collection as SyncCollection

            if isinstance(chat_history_collection, SyncCollection):
                raise TypeError(
                    "chat_history_collection must be an ASYNC Motor collection "
                    "(AsyncIOMotorCollection) or ScopedCollectionWrapper, not a synchronous "
                    "pymongo.Collection. Use engine.get_scoped_db(slug) to get async collections."
                )
        except ImportError:
            pass

        self.app_slug = app_slug
        self.stm_context_limit = stm_context_limit
        self.ltm_search_limit = ltm_search_limit
        self.auto_summarize_threshold = auto_summarize_threshold

        # Context Engineering configuration
        self.enable_context_engineering = enable_context_engineering
        self.stm_raw_window = stm_raw_window
        self.summary_staleness_threshold = summary_staleness_threshold
        self.enable_entity_extraction = enable_entity_extraction
        self.enable_dynamic_persona = enable_dynamic_persona

        # Token budget
        self._max_prompt_tokens = max_prompt_tokens

        # Graph filtering configuration (Phase 5)
        self.graph_min_hop_distance = graph_min_hop_distance
        self.graph_min_edges = graph_min_edges
        self.graph_deduplication_threshold = graph_deduplication_threshold
        self.graph_min_nodes = graph_min_nodes

        # Initialize Short-Term Memory
        self.stm = ChatHistoryService(collection=chat_history_collection)

        # Initialize Long-Term Memory
        if memory_service:
            self.ltm = memory_service
            ltm_id = id(memory_service)  # Memory address for debugging
            logger.info(
                f"🔧 [CognitiveEngine] Using provided memory service (id={ltm_id}): "
                f"db={getattr(memory_service, 'db_name', 'unknown')}, "
                f"collection={getattr(memory_service, 'collection_name', 'unknown')}, "
                f"app_slug={getattr(memory_service, 'app_slug', 'unknown')}"
            )
        else:
            if memory_collection is None:
                raise ValueError(
                    "memory_collection is REQUIRED when memory_service is not provided. "
                    "CognitiveEngine must use MDB-Engine's connection pool. "
                    "Pass a Motor AsyncIOMotorCollection obtained from "
                    "MDB-Engine's connection manager."
                )
            logger.info("Creating new CognitiveMemoryService using MDB-Engine connection")
            self.ltm = CognitiveMemoryService(
                app_slug=self.app_slug,
                collection=memory_collection,
            )
            ltm_id = id(self.ltm)
            logger.info(
                f"🔧 [CognitiveEngine] Created new memory service (id={ltm_id}): "
                f"collection={getattr(self.ltm, 'collection_name', 'unknown')}"
            )

        # Initialize LLM Service (must expose async chat_completion)
        if llm_service:
            self.llm_service = llm_service
            logger.info("Using provided LLMService")
        else:
            self.llm_service = None
            logger.warning("No LLM service available. Fact extraction will be disabled.")

        # Initialize Prospective Memory (if available on memory service)
        self._prospective_memory = getattr(self.ltm, "_prospective_memory", None)

        # Store GraphService for GraphRAG functionality
        # Priority: explicit graph_service > memory service's _graph_service
        if graph_service:
            self._graph_service = graph_service
            logger.info("Using provided GraphService for GraphRAG")
        else:
            # Fall back to memory service's graph service
            self._graph_service = getattr(self.ltm, "_graph_service", None)
            if self._graph_service:
                logger.info("Using GraphService from memory service")
            else:
                logger.debug("GraphService not available - GraphRAG disabled")

        # Store ProceduralMemory for skill retrieval during chat
        # Priority: explicit procedural_memory > memory service's _procedural_memory
        if procedural_memory:
            self._procedural_memory: ProceduralMemoryProtocol | None = procedural_memory
            logger.info("Using provided ProceduralMemory for skill retrieval")
        else:
            self._procedural_memory = getattr(self.ltm, "_procedural_memory", None)
            if self._procedural_memory:
                logger.info("Using ProceduralMemory from memory service")
            else:
                logger.debug("ProceduralMemory not available - skill retrieval disabled")

        # Initialize Context Engineer (composed object for prompt construction)
        self._context_engineer = ContextEngineer(
            ltm=self.ltm,
            stm=self.stm,
            graph_service=self._graph_service,
            embedding_service=getattr(self.ltm, "embedding_provider", None),
            llm_service=self.llm_service,
            config={
                "enable_context_engineering": self.enable_context_engineering,
                "stm_raw_window": self.stm_raw_window,
                "enable_entity_extraction": self.enable_entity_extraction,
                "enable_dynamic_persona": self.enable_dynamic_persona,
                "graph_min_hop_distance": self.graph_min_hop_distance,
                "graph_min_edges": self.graph_min_edges,
                "graph_deduplication_threshold": self.graph_deduplication_threshold,
                "graph_min_nodes": self.graph_min_nodes,
                "summary_staleness_threshold": self.summary_staleness_threshold,
            },
            profile_service=getattr(self.ltm, "_profile_service", None),
        )

        # Wire neuroplasticity engine into context engineer for per-user trait overrides
        neuroplasticity_engine = getattr(self.ltm, "_neuroplasticity_engine", None)
        if neuroplasticity_engine:
            self._context_engineer._neuroplasticity_engine = neuroplasticity_engine  # noqa: SLF001
            logger.info("NeuroplasticityEngine wired into ContextEngineer")

    @property
    def graph_service(self) -> GraphServiceProtocol | None:
        """
        Public property to access the graph service.

        Returns:
            GraphService instance if available, None otherwise.
        """
        return self._graph_service

    @property
    def has_graph_service(self) -> bool:
        """
        Public property to check if graph service is available.

        Returns:
            True if graph service is available, False otherwise.
        """
        return self._graph_service is not None

    async def chat(
        self,
        user_id: str,
        session_id: str,
        user_query: str,
        system_prompt: str | None = None,
        extract_facts: bool = True,
        bucket_id: str | None = None,
        bucket_type: str | None = None,
        search_filters: dict[str, Any] | None = None,
        search_strategy: str | None = None,
        skill_feedback: dict[str, bool] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """
        Full RAG Pipeline: Combines STM and LTM to generate a response.

        This method orchestrates the complete RAG pipeline:
        1. Saves user message to Short-Term Memory (STM)
        2. Searches Long-Term Memory (LTM) for relevant memories
        3. Retrieves STM context (recent conversation history)
        4. Generates LLM response with combined context
        5. Saves AI response to STM
        6. Optionally extracts facts from conversation to LTM

        Args:
            user_id: User identifier for scoping memory operations
            session_id: Session identifier for grouping related messages
            user_query: User's message/query
            system_prompt: Optional custom system prompt. If None, uses default.
            extract_facts: Whether to extract facts to LTM (default: True).
                          When True, uses LLM to extract factual information from
                          the user's message and stores it as long-term memories.
            bucket_id: Optional bucket ID for memory isolation. When provided:
                      - LTM search is filtered to only return memories from this bucket
                      - New memories are stored with this bucket_id
                      Enables "bucket awareness" where memories in 'work' bucket won't
                      appear when searching from 'personal' bucket.
            bucket_type: Optional bucket type (e.g., "conversation", "category", "file").
                        Used in conjunction with bucket_id for memory organization.
            search_filters: Optional additional filters for LTM search.
                           Example: {"metadata": {"category": "work"}}
            search_strategy: Optional GraphRAG search strategy override.
                            When None or "auto", the query classifier decides which
                            strategy to use (default behavior). When set to an explicit
                            value, the classifier is bypassed entirely.
                            Valid values: "local", "global", "drift", "basic", "auto".
                            Use this for deterministic features ("Generate Report" always
                            uses "global"), cost control (disable expensive strategies),
                            or debugging (test specific strategies in isolation).
            skill_feedback: Optional explicit feedback for skills used in the *previous*
                           exchange.  Maps skill name to success boolean, e.g.
                           ``{"Clear Cache Workflow": True}``.  When provided, updates
                           ``ProceduralMemory.mark_procedure_used()`` for each entry.
            **kwargs: Additional arguments passed to LLM provider (e.g., model, temperature)

        Returns:
            Dict[str, Any] with the following keys:
                - response (str): AI-generated response text
                - stm_context (List[Dict]): Short-term memory context used for generation
                - ltm_memories (List[Dict]): Long-term memories retrieved and used
                - graph_context (Dict|None): GraphRAG context with entry_nodes and graph_context
                                            (related nodes via $graphLookup traversal)
                - skills_matched (List[Dict]): Procedural skills matched for this query.
                                              Empty list if no skills available or matched.
                - session_message_count (int): Number of messages in the session
                - memories_stored (List[Dict]): New memories stored during this chat call.
                                              Empty list if extract_facts=False or no memories
                                              extracted. Useful for triggering UI updates via
                                              WebSocket.
                - persona_used (Dict|None): Persona document used
                    (if Context Engineering enabled)
                - entity_facts (Dict): Entity facts extracted and used
                    (if Context Engineering enabled)
                - dynamic_instructions (str): Dynamic persona instructions applied
                    (if Context Engineering enabled)
                - stm_summary (str|None): Summary of older STM messages
                    (if Context Engineering enabled and summary created)

        Raises:
            CognitiveMemoryServiceError: If LLM provider is not available or memory
                                        operations fail

        Example:
            ```python
            # Basic usage (no bucket filtering - searches all memories)
            result = await cognitive_engine.chat(
                user_id="user123",
                session_id="session456",
                user_query="I love chocolate",
                extract_facts=True
            )

            # Bucket-aware usage (only searches/stores in 'work' bucket)
            result = await cognitive_engine.chat(
                user_id="user123",
                session_id="session456",
                user_query="What meetings do I have?",
                bucket_id="category:work:user123",
                bucket_type="category",
                extract_facts=True
            )
            print(result["response"])  # AI response
            # [{"id": "...", "memory": "User loves chocolate", ...}]
            print(result["memories_stored"])

            # Force a specific GraphRAG search strategy (bypass classifier)
            result = await cognitive_engine.chat(
                user_id="user123",
                session_id="session456",
                user_query="Generate a summary of all project risks",
                search_strategy="global"  # Always use global map-reduce
            )
            ```
        """
        with create_span(
            "chat_engine.chat",
            {
                "chat.user_id": user_id,
                "chat.session_id": session_id,
                "chat.extract_facts": extract_facts,
            },
        ) as parent_span:
            # Phase 0: Validate & process skill feedback
            self._validate_chat_inputs(user_id, session_id, user_query, bucket_id, search_strategy)
            await self._process_skill_feedback(skill_feedback)

            # Phase 1: Store user message in STM
            with create_span("chat_engine.chat.save_to_stm"):
                await self._store_user_message(session_id, user_id, user_query)

            # Phase 2-3: Parallel context retrieval (LTM, STM, Graph, Skills, Triggers)
            with create_span("chat_engine.chat.fetch_context"):
                ltm, stm_ctx, stm_summary, graph, skills, triggers = await self._fetch_all_context(
                    user_id,
                    session_id,
                    user_query,
                    bucket_id,
                    bucket_type,
                    search_filters,
                    search_strategy,
                )

            parent_span.set_attribute("chat.ltm_count", len(ltm) if ltm else 0)

            # Phase 4: Format all context into prompt-ready strings
            with create_span("chat_engine.chat.format_context"):
                formatted = await self._format_context(ltm, stm_ctx, stm_summary, graph, skills, triggers, user_id)

            # Phase 5: Construct system prompt
            with create_span("chat_engine.chat.build_system_prompt"):
                prompt_meta = await self._build_system_prompt(
                    system_prompt,
                    ltm,
                    formatted,
                    user_id,
                    **kwargs,
                )

            # Phase 6: Generate LLM response
            with create_span("chat_engine.chat.llm_generate"):
                ai_response = await self._generate_response(
                    prompt_meta["system_prompt"], formatted.stm_messages, **kwargs
                )

            # Phase 7: Save assistant response to STM
            with create_span("chat_engine.chat.save_response"):
                await self._store_assistant_response(session_id, user_id, ai_response)

            # Phase 8: Extract & store facts in LTM
            with create_span("chat_engine.chat.extract_facts"):
                memories_stored = await self._extract_and_store_facts(
                    extract_facts,
                    user_id,
                    session_id,
                    user_query,
                    bucket_id,
                    bucket_type,
                )

            parent_span.set_attribute("chat.memories_stored", len(memories_stored) if memories_stored else 0)

            # Phase 9: Build result dict
            session_message_count = await self.stm.get_session_count(session_id, user_id)
            if session_message_count > self.auto_summarize_threshold:
                logger.info("Session %s has %d messages, considering summarization", session_id, session_message_count)

            # Fetch neuroplasticity adaptations (async) before building sync result
            adaptations = await self._fetch_active_adaptations(user_id)

            return self._build_chat_result(
                ai_response=ai_response,
                stm_context=formatted.stm_messages,
                relevant_memories=ltm,
                graph_results=graph,
                skills_results=skills,
                session_message_count=session_message_count,
                memories_stored=memories_stored,
                prompt_meta=prompt_meta,
                user_id=user_id,
                adaptations=adaptations,
            )

    # ------------------------------------------------------------------
    # chat() helper methods — one per phase
    # ------------------------------------------------------------------

    def _validate_chat_inputs(
        self,
        user_id: str,
        session_id: str,
        user_query: str,
        bucket_id: str | None,
        search_strategy: str | None,
    ) -> None:
        """Phase 0a: defence-in-depth input validation."""
        validate_user_id(user_id)
        validate_session_id(session_id)
        validate_text_content(user_query, field_name="user_query")
        if bucket_id is not None:
            validate_identifier(bucket_id, "bucket_id")

        from ..core.types import VALID_SEARCH_STRATEGIES

        if search_strategy not in VALID_SEARCH_STRATEGIES:
            raise ValueError(
                f"Invalid search_strategy '{search_strategy}'. "
                f"Must be one of: local, global, drift, basic, auto (or None)."
            )

    async def _process_skill_feedback(self, skill_feedback: dict[str, bool] | None) -> None:
        """Phase 0b: record explicit skill feedback from the previous exchange."""
        if not skill_feedback or not self._procedural_memory:
            return
        for skill_name, success in skill_feedback.items():
            try:
                await self._procedural_memory.mark_procedure_used(name=skill_name, success=success)
                logger.info("[Skills] Feedback recorded: %s (%s)", skill_name, "success" if success else "failure")
            except (ValueError, RuntimeError, AttributeError) as e:
                logger.warning("[Skills] Failed to record feedback for %s: %s", skill_name, e)

    async def _store_user_message(self, session_id: str, user_id: str, user_query: str) -> None:
        """Phase 1: ingest the user message into STM."""
        await self.stm.add_message(
            session_id=session_id,
            role="user",
            content=user_query,
            user_id=user_id,
            metadata={"source": "cognitive_engine"},
        )

    # -- parallel fetchers (used by _fetch_all_context) ------------------

    async def _fetch_ltm(
        self,
        user_query: str,
        user_id: str,
        bucket_id: str | None,
        bucket_type: str | None,
        search_filters: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Fetch relevant memories from LTM with optional bucket filtering."""
        try:
            ltm_filters = dict(search_filters) if search_filters else {}
            if bucket_id:
                ltm_filters.setdefault("metadata", {})["associated_bucket_id"] = bucket_id
                logger.info(
                    "[CognitiveEngine] Bucket-aware search: associated_bucket_id=%s, bucket_type=%s",
                    bucket_id,
                    bucket_type,
                )

            # NO FALLBACK — if no results, that's explicit
            results = await self.ltm.search(
                query=user_query,
                user_id=user_id,
                limit=self.ltm_search_limit,
                filters=ltm_filters if ltm_filters else None,
            )

            if not results and bucket_id:
                logger.error(
                    "[CognitiveEngine] NO MEMORIES FOUND with bucket filter. "
                    "Search bucket_id='%s', filter=%s. "
                    "Verify stored memories have metadata.associated_bucket_id='%s'",
                    bucket_id,
                    ltm_filters,
                    bucket_id,
                )
            elif results:
                logger.info("[CognitiveEngine] Found %d memories for bucket_id='%s'", len(results), bucket_id)

            return results
        except (ValueError, RuntimeError, AttributeError) as e:
            logger.warning("LTM search failed: %s. Continuing without LTM context.", e, exc_info=True)
            return []

    async def _fetch_stm(
        self,
        session_id: str,
        user_id: str,
    ) -> tuple[list[dict[str, str]], str | None]:
        """Fetch chat context from STM, optionally optimised with a sliding window."""
        full_context = await self.stm.get_context(
            session_id=session_id,
            limit=self.stm_context_limit,
            user_id=user_id,
        )
        if self.enable_context_engineering:
            recent, summary = await self._context_engineer.optimize_stm_context(full_context, session_id, user_id)
            return recent, summary
        return full_context, None

    async def _fetch_graph(
        self,
        user_query: str,
        user_id: str,
        search_strategy: str | None,
    ) -> dict[str, Any] | None:
        """Fetch graph context via GraphRAG query classification and routing."""
        if not self._graph_service:
            return None
        try:
            if search_strategy and search_strategy != "auto":
                query_type = search_strategy
                logger.info("[GraphRAG] Using explicit strategy: %s", query_type)
            else:
                query_type = self._graph_service.classify_query(user_query)
                logger.info("[GraphRAG] Query classified as: %s", query_type)

            search_fn = {
                "osi_metric": lambda: self._graph_service.metric_search(query=user_query, user_id=user_id, max_depth=2),
                "local": lambda: self._graph_service.local_search(query=user_query, user_id=user_id, max_depth=2),
                "global": lambda: self._graph_service.global_search(
                    query=user_query,
                    user_id=user_id,
                    max_communities=10,
                ),
                "drift": lambda: self._graph_service.drift_search(query=user_query, user_id=user_id, max_depth=2),
            }
            result = await search_fn.get(
                query_type,
                lambda: self._graph_service.hybrid_search(query=user_query, user_id=user_id, max_depth=2),
            )()

            if isinstance(result, dict):
                result["search_strategy_used"] = query_type
            return result
        except (ValueError, RuntimeError, AttributeError) as e:
            logger.warning("Graph search failed (non-fatal): %s. Continuing without graph context.", e, exc_info=True)
            return None

    async def _fetch_skills(self, user_query: str) -> list[dict[str, Any]]:
        """Fetch relevant skills/procedures for the current query."""
        if not self._procedural_memory:
            return []
        try:
            procedures = await self._procedural_memory.search_procedures(task_description=user_query, limit=3)
            if procedures:
                logger.info("[Skills] Found %d relevant skills for query", len(procedures))
            return procedures
        except (ValueError, RuntimeError, AttributeError) as e:
            logger.warning("Skill search failed (non-fatal): %s. Continuing without skill context.", e, exc_info=True)
            return []

    async def _check_prospective_triggers(self, user_query: str, user_id: str) -> list[dict[str, Any]]:
        """Check if any prospective memory triggers should fire."""
        if not self._prospective_memory:
            return []
        try:
            fired = await self._prospective_memory.check_triggers(current_context=user_query, user_id=user_id)
            for trigger in fired:
                await self._prospective_memory.mark_triggered(trigger["trigger_id"])
            return fired
        except (PyMongoError, ConnectionError, TimeoutError, ValueError) as e:
            logger.warning("Prospective trigger check failed: %s", e)
            return []

    # -- aggregated fetch ------------------------------------------------

    async def _fetch_all_context(
        self,
        user_id: str,
        session_id: str,
        user_query: str,
        bucket_id: str | None,
        bucket_type: str | None,
        search_filters: dict[str, Any] | None,
        search_strategy: str | None,
    ) -> tuple[
        list[dict[str, Any]],  # LTM memories
        list[dict[str, str]],  # STM messages
        str | None,  # STM summary
        dict[str, Any] | None,  # Graph results
        list[dict[str, Any]],  # Skills
        list[dict[str, Any]],  # Prospective triggers
    ]:
        """Phase 2-3: run all context fetches in parallel."""
        logger.info(
            "[CognitiveEngine] Parallel fetch: LTM (service_id=%d) + STM (user=%s, session=%s)",
            id(self.ltm),
            user_id,
            session_id,
        )

        fetch_tasks: list[Any] = [
            self._fetch_ltm(user_query, user_id, bucket_id, bucket_type, search_filters),
            self._fetch_stm(session_id, user_id),
        ]
        if self._graph_service:
            fetch_tasks.append(self._fetch_graph(user_query, user_id, search_strategy))
        if self._procedural_memory:
            fetch_tasks.append(self._fetch_skills(user_query))
        if self._prospective_memory:
            fetch_tasks.append(self._check_prospective_triggers(user_query, user_id))

        results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

        # Log failures
        for i, r in enumerate(results):
            if isinstance(r, BaseException):
                logger.error("Parallel fetch task %d failed: %s", i, r, exc_info=r)

        # Unpack required results
        ltm = results[0] if not isinstance(results[0], BaseException) else []
        stm_raw = results[1] if not isinstance(results[1], BaseException) else ([], None)
        stm_ctx, stm_summary = stm_raw if isinstance(stm_raw, tuple) else (stm_raw, None)

        # Unpack optional results
        idx = 2
        graph: dict[str, Any] | None = None
        skills: list[dict[str, Any]] = []
        triggers: list[dict[str, Any]] = []
        if self._graph_service:
            raw = results[idx] if idx < len(results) else None
            graph = raw if not isinstance(raw, BaseException) else None
            idx += 1
        if self._procedural_memory:
            raw = results[idx] if idx < len(results) else []
            skills = raw if not isinstance(raw, BaseException) else []
            idx += 1
        if self._prospective_memory:
            raw = results[idx] if idx < len(results) else []
            triggers = raw if not isinstance(raw, BaseException) else []

        logger.info(
            "[Parallel Fetch] LTM: %d memories, STM: %d messages, Graph: %s, Skills: %d",
            len(ltm) if ltm else 0,
            len(stm_ctx) if stm_ctx else 0,
            graph.get("total_nodes", 0) if graph else "disabled",
            len(skills),
        )
        return ltm, stm_ctx, stm_summary, graph, skills, triggers

    # -- formatting ------------------------------------------------------

    async def _format_context(
        self,
        ltm: list[dict[str, Any]],
        stm_ctx: list[dict[str, str]],
        stm_summary: str | None,
        graph: dict[str, Any] | None,
        skills: list[dict[str, Any]],
        triggers: list[dict[str, Any]],
        user_id: str,
    ) -> _FormattedContext:
        """Phase 4: format raw context into prompt-ready strings."""
        # LTM
        ltm_text = ""
        if ltm:
            ltm_text = "RELEVANT FACTS FROM LONG-TERM MEMORY:\n"
            for mem in ltm:
                memory_text = mem.get("memory", "")
                if memory_text:
                    ltm_text += f"- {memory_text}\n"
            ltm_text += "\n"

        # Prospective triggers
        triggers_text = ""
        if triggers:
            triggers_text = "PROSPECTIVE MEMORY TRIGGERS (action reminders):\n"
            for t in triggers:
                action = t.get("action", "")
                condition = t.get("condition", "")
                if action:
                    triggers_text += f"- REMINDER: {action} (triggered by: {condition})\n"
            triggers_text += "\n"
            logger.info("%d prospective triggers fired", len(triggers))

        # Skills
        from .procedural import ProceduralMemory

        skills_text = ProceduralMemory.format_for_prompt(skills) if skills else ""

        # Graph (deduplicate + format)
        graph_text = await self._format_graph_context(graph, ltm, user_id)

        return _FormattedContext(
            ltm_text=ltm_text,
            graph_text=graph_text,
            skills_text=skills_text,
            triggers_text=triggers_text,
            stm_messages=stm_ctx,
            stm_summary=stm_summary,
        )

    async def _format_graph_context(
        self,
        graph_results: dict[str, Any] | None,
        ltm: list[dict[str, Any]],
        user_id: str,
    ) -> str:
        """Deduplicate graph nodes against LTM and format for the prompt."""
        if not graph_results:
            return ""
        if not (
            graph_results.get("entry_nodes") or graph_results.get("context_nodes") or graph_results.get("graph_context")
        ):
            return ""

        # Normalise format
        if "context_nodes" in graph_results and "graph_context" not in graph_results:
            graph_results["graph_context"] = [{"node": n} for n in graph_results.get("context_nodes", [])]

        graph_results = await self._context_engineer.deduplicate_graph_against_memories(
            graph_results,
            ltm,
            similarity_threshold=self.graph_deduplication_threshold,
            user_id=user_id,
        )

        context_nodes = graph_results.get("context_nodes", [])
        if not context_nodes and graph_results.get("graph_context"):
            context_nodes = [item.get("node", item) for item in graph_results.get("graph_context", [])]
        entry_count = len(graph_results.get("entry_nodes", []))
        total = len(context_nodes) + entry_count

        if total < self.graph_min_nodes:
            logger.debug("Skipping graph context - only %d nodes (minimum: %d)", total, self.graph_min_nodes)
            return ""

        if not self._graph_service:
            return ""

        graph_text = self._render_graph_text(graph_results, context_nodes, entry_count, total)
        if graph_text:
            graph_text += "\n\n"
        return graph_text

    def _render_graph_text(
        self,
        graph_results: dict[str, Any],
        context_nodes: list[Any],
        entry_count: int,
        total: int,
    ) -> str:
        """Render graph results into a prompt string based on query type."""
        query_type = graph_results.get("query_type")

        # OSI Metric
        if query_type == "osi_metric" and graph_results.get("metrics"):
            text = "[GRAPH INSIGHTS - GOVERNED METRIC]\n"
            for m in graph_results["metrics"]:
                text += f"Metric: {m['name']}\n"
                text += f"  Governed definition: {m.get('expression', 'N/A')}\n"
                if m.get("description"):
                    text += f"  Description: {m['description']}\n"
                text += f"  Source model: {m.get('model', 'N/A')}\n"
                synonyms = m.get("synonyms", [])
                if synonyms:
                    text += f"  Also known as: {', '.join(synonyms)}\n"
            text += (
                "\nNote: This is a governed metric definition from the OSI semantic layer. "
                "Present the definition to the user and explain what it measures.\n"
            )
            if graph_results.get("graph_context"):
                text += "\n[RELATED KNOWLEDGE]\n"
                text += self._graph_service.format_graph_context(graph_results, max_nodes=5, include_edges=True)
            return text

        # Global Search
        if graph_results.get("synthesized_answer"):
            text = f"[GRAPH INSIGHTS - GLOBAL ANALYSIS]\n{graph_results['synthesized_answer']}\n"
            comms = graph_results.get("communities", [])
            if comms:
                text += f"\nBased on {len(comms)} communities.\n"
            return text

        # Local / DRIFT with community summaries
        summaries = graph_results.get("community_summaries") or graph_results.get("community_context", [])
        if summaries:
            text = "[GRAPH INSIGHTS - COMMUNITY CONTEXT]\n"
            for s in summaries[:5]:
                cs = s.get("summary", "")
                if cs:
                    text += f"- {cs}\n"
            text += "\n[GRAPH CONNECTIONS]\n"
            text += self._graph_service.format_graph_context(graph_results, max_nodes=10, include_edges=True)
            return text

        # Advanced search (backward compatibility)
        if graph_results.get("strategy"):
            strategy = graph_results["strategy"]
            raw_narrative = self._graph_service.format_graph_narrative(graph_results, max_nodes=8)
            if raw_narrative:
                heading = "PATHS FOUND" if strategy == "pathfinding" else "CONTEXT"
                blurb = (
                    "The system found direct connections between entities in the query."
                    if strategy == "pathfinding"
                    else "Network neighborhood around key concepts:"
                )
                return f"[GRAPH INSIGHTS - {heading}]\n{blurb}\n{raw_narrative}\n"
            return ""

        # Fallback
        text = self._graph_service.format_graph_context(graph_results, max_nodes=8, include_edges=True)
        if text:
            logger.info(
                "Formatted graph context (type: %s): %d entry, %d context (total: %d)",
                query_type or "standard",
                entry_count,
                len(context_nodes),
                total,
            )
        return text

    # -- system prompt ---------------------------------------------------

    async def _build_system_prompt(
        self,
        system_prompt_override: str | None,
        ltm: list[dict[str, Any]],
        formatted: _FormattedContext,
        user_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Phase 5: construct or pass through the system prompt.

        Returns a dict with ``system_prompt`` plus context-engineering metadata.
        """
        meta: dict[str, Any] = {
            "persona_used": None,
            "entity_facts": {},
            "dynamic_instructions": "",
            "system_prompt": "",
        }

        if system_prompt_override:
            meta["system_prompt"] = system_prompt_override
            return meta

        ltm_context = formatted.ltm_text + formatted.triggers_text

        if self.enable_context_engineering:
            persona = await self._get_persona()
            meta["persona_used"] = persona

            entity_facts: dict[str, str] = {}
            if self.enable_entity_extraction:
                entity_facts = self._context_engineer.extract_entity_facts(user_id, ltm)
            meta["entity_facts"] = entity_facts

            dynamic_instructions = ""
            if self.enable_dynamic_persona:
                dynamic_instructions = await self._context_engineer.build_dynamic_persona(
                    persona,
                    entity_facts,
                    ltm,
                    user_id=user_id,
                )
            meta["dynamic_instructions"] = dynamic_instructions

            meta["system_prompt"] = self._context_engineer.construct_context_engineered_prompt(
                persona=persona,
                entity_facts=entity_facts,
                ltm_context=ltm_context,
                graph_context=formatted.graph_text,
                dynamic_instructions=dynamic_instructions,
                stm_summary=formatted.stm_summary,
                skills_context=formatted.skills_text,
                max_prompt_tokens=self._max_prompt_tokens,
                model=kwargs.get("model") or "gpt-4o",
            )
        else:
            sections = [s for s in (formatted.skills_text, formatted.graph_text, ltm_context) if s]
            if sections:
                meta["system_prompt"] = (
                    "You are a helpful AI assistant.\n"
                    "Use the following context to answer if relevant.\n"
                    "Use the Chat History to maintain conversation flow.\n\n" + "".join(sections)
                )
            else:
                meta["system_prompt"] = (
                    "You are a helpful AI assistant.\nUse the Chat History to maintain conversation flow."
                )

        return meta

    async def _get_persona(self) -> dict[str, Any] | None:
        """Retrieve the current persona from PersonaEngine, if available."""
        persona_engine = getattr(self.ltm, "persona_engine", None)
        if not persona_engine:
            return None
        try:
            persona = await persona_engine.get_persona()
            role = persona.get("role", "Unknown") if persona else "None"
            logger.info("Retrieved persona: %s", role)
            return persona
        except (PyMongoError, AttributeError, TypeError) as e:
            logger.warning("Failed to get persona: %s", e)
            return None

    # -- LLM call --------------------------------------------------------

    async def _generate_response(
        self,
        system_prompt: str,
        stm_context: list[dict[str, str]],
        **kwargs: Any,
    ) -> str:
        """Phase 6: call the LLM with the assembled messages."""
        if not self.llm_service:
            raise CognitiveMemoryServiceError("No LLM service available for chat generation")

        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        messages.extend(stm_context)
        logger.debug("Prepared %d messages for LLM (1 system + %d chat history)", len(messages), len(stm_context))

        chat_model = kwargs.pop("model", None)
        return await self.llm_service.chat_completion(messages=messages, model=chat_model, **kwargs)

    # -- store response --------------------------------------------------

    async def _store_assistant_response(self, session_id: str, user_id: str, ai_response: str) -> None:
        """Phase 7: persist the assistant reply in STM."""
        await self.stm.add_message(
            session_id=session_id,
            role="assistant",
            content=ai_response,
            user_id=user_id,
            metadata={"source": "cognitive_engine"},
        )

    # -- extract & store facts -------------------------------------------

    async def _extract_and_store_facts(
        self,
        extract_facts: bool,
        user_id: str,
        session_id: str,
        user_query: str,
        bucket_id: str | None,
        bucket_type: str | None,
    ) -> list[dict[str, Any]]:
        """Phase 8: extract facts from the user query and store in LTM."""
        if not extract_facts:
            logger.info("[CognitiveEngine] Skipping memory storage (extract_facts=False)")
            return []

        if not self.ltm:
            logger.error("[CognitiveEngine] Cannot store memories: ltm (memory service) is None!")
            return []

        try:
            storage_bucket_id = bucket_id or f"session:{session_id}"
            storage_bucket_type = bucket_type or "conversation"
            storage_metadata = {
                "source": "chat_session",
                "session_id": session_id,
                "associated_bucket_id": storage_bucket_id,
            }

            logger.info(
                "[CognitiveEngine] Storing memory: user_id=%s, session=%s, bucket=%s",
                user_id,
                session_id,
                storage_bucket_id,
            )
            stored = await self.ltm.add(
                messages=user_query,
                user_id=user_id,
                metadata=storage_metadata,
                bucket_id=storage_bucket_id,
                bucket_type=storage_bucket_type,
            )
            logger.info("[CognitiveEngine] Memory storage SUCCESS: %d memories stored", len(stored) if stored else 0)
            return stored if stored else []
        except (
            PyMongoError,
            CognitiveMemoryServiceError,
            ValueError,
            TypeError,
            RuntimeError,
            ConnectionError,
            OSError,
        ) as e:
            logger.error("[CognitiveEngine] Failed to extract facts to LTM: %s", e, exc_info=True)
            return []

    # -- neuroplasticity -------------------------------------------------

    async def _fetch_active_adaptations(self, user_id: str) -> list[dict[str, Any]]:
        """Fetch active neuroplasticity adaptations for the user."""
        engine = getattr(self.ltm, "_neuroplasticity_engine", None)
        if not engine:
            return []
        try:
            active = await engine.get_active_adaptations(user_id)
            return [{"type": a["type"], "key": a["key"], "value": a["value"]} for a in active]
        except (AttributeError, RuntimeError, ValueError, TypeError) as e:
            logger.debug("Could not fetch active adaptations: %s", e)
            return []

    # -- result assembly -------------------------------------------------

    def _build_chat_result(
        self,
        *,
        ai_response: str,
        stm_context: list[dict[str, str]],
        relevant_memories: list[dict[str, Any]],
        graph_results: dict[str, Any] | None,
        skills_results: list[dict[str, Any]],
        session_message_count: int,
        memories_stored: list[dict[str, Any]],
        prompt_meta: dict[str, Any],
        user_id: str,
        adaptations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Phase 9: assemble the final return dict."""
        result: dict[str, Any] = {
            "response": ai_response,
            "stm_context": stm_context,
            "ltm_memories": relevant_memories,
            "graph_context": graph_results,
            "skills_matched": skills_results,
            "session_message_count": session_message_count,
            "memories_stored": memories_stored,
        }

        if self.enable_context_engineering:
            result["persona_used"] = prompt_meta.get("persona_used")
            result["entity_facts"] = prompt_meta.get("entity_facts", {})
            result["dynamic_instructions"] = prompt_meta.get("dynamic_instructions", "")
            result["stm_summary"] = prompt_meta.get("stm_summary")

        # Neuroplasticity metadata
        if adaptations:
            result["adaptations_active"] = adaptations
        if relevant_memories and getattr(self.ltm, "_neuroplasticity_engine", None):
            type_counts: dict[str, int] = {}
            for mem in relevant_memories:
                et = mem.get("emotion_type", "neutral")
                type_counts[et] = type_counts.get(et, 0) + 1
            result["emotion_types_matched"] = type_counts

        return result

    async def summarize_session(
        self,
        session_id: str,
        user_id: str,
        messages_to_summarize: int = 10,
    ) -> str | None:
        """
        Summarizes old messages in a session and stores the summary in LTM.

        This implements "Medium-Term Memory" - converting STM chunks into LTM facts.

        Args:
            session_id: Session identifier
            user_id: User identifier
            messages_to_summarize: Number of oldest messages to summarize

        Returns:
            Summary text if successful, None otherwise
        """
        validate_user_id(user_id)
        validate_session_id(session_id)

        if not self.llm_service:
            logger.warning("No LLM service available for summarization")
            return None

        # Get oldest messages
        query = {"session_id": session_id, "user_id": str(user_id)}
        cursor = self.stm.collection.find(query).sort("created_at", ASCENDING).limit(messages_to_summarize)
        old_messages = await cursor.to_list(length=messages_to_summarize)

        if not old_messages:
            return None

        # Format messages for summarization
        conversation_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in old_messages])

        summary_prompt = (
            "Summarize the following conversation into 2-3 key facts or insights "
            "that would be useful to remember long-term. Focus on user preferences, "
            "important information, or decisions made.\n\n"
            f"Conversation:\n{conversation_text}\n\n"
            "Summary:"
        )

        try:
            # Generate summary using async LLM provider
            summary = await self.llm_service.chat_completion(
                messages=[{"role": "user", "content": summary_prompt}],
                temperature=0.3,
            )

            # Store summary in LTM
            await self.ltm.inject(
                memory=summary,
                user_id=user_id,
                metadata={
                    "type": "session_summary",
                    "session_id": session_id,
                    "source": "auto_summarization",
                },
                bucket_id=f"session:{session_id}",
                bucket_type="summary",
            )

            # Delete summarized messages from STM
            message_ids = [msg["_id"] for msg in old_messages]
            await self.stm.collection.delete_many({"_id": {"$in": message_ids}})

            logger.info(f"Summarized {len(old_messages)} messages from session {session_id}")
            return summary

        except (
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            ConnectionError,
            OSError,
            KeyError,
            PyMongoError,
            CognitiveMemoryServiceError,
        ) as e:
            logger.exception(f"Failed to summarize session {session_id}: {e}")
            return None

    async def get_full_context(
        self,
        user_id: str,
        session_id: str,
        query: str | None = None,
    ) -> dict[str, Any]:
        """
        Retrieves full context (STM + LTM) for a session.

        Useful for debugging or manual context inspection.

        Args:
            user_id: User identifier
            session_id: Session identifier
            query: Optional query for LTM search (uses session_id if None)

        Returns:
            Dict with stm_context and ltm_memories
        """
        validate_user_id(user_id)
        validate_session_id(session_id)
        if query is not None:
            validate_text_content(query, field_name="query")

        stm_context = await self.stm.get_context(
            session_id=session_id,
            limit=self.stm_context_limit,
            user_id=user_id,
        )

        search_query = query or f"session {session_id}"
        ltm_memories = await self.ltm.search(
            query=search_query,
            user_id=user_id,
            limit=self.ltm_search_limit,
            filters={"metadata.session_id": session_id},
        )

        return {
            "stm_context": stm_context,
            "ltm_memories": ltm_memories,
            "session_id": session_id,
            "user_id": user_id,
        }

    async def inject_thought(
        self,
        user_id: str,
        thought: str,
        session_id: str | None = None,
        visibility: str = "private",
        metadata: dict[str, Any] | None = None,
    ):
        """
        Stores an internal "thought" or reasoning trace in LTM.

        Useful for storing AI reasoning that the user didn't see.

        Args:
            user_id: User identifier
            thought: The thought/reasoning to store
            session_id: Optional session identifier
            visibility: Visibility level ("private", "shared", etc.)
            metadata: Additional metadata
        """
        validate_user_id(user_id)
        validate_text_content(thought, field_name="thought")
        if session_id is not None:
            validate_session_id(session_id)

        final_metadata = metadata or {}
        final_metadata.update(
            {
                "type": "internal_thought",
                "visibility": visibility,
            }
        )
        if session_id:
            final_metadata["session_id"] = session_id

        await self.ltm.inject(
            memory=thought,
            user_id=user_id,
            metadata=final_metadata,
            bucket_type="thought",
        )
        logger.debug(f"Injected thought for user {user_id}")


# Public alias — simpler name for the conversation orchestrator.
# "CognitiveEngine" remains importable for backwards compatibility.
ChatEngine = CognitiveEngine
