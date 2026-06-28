"""
Unit tests for Memory Orchestrator components.

HIGH-VALUE TESTS ONLY:
- ChatHistoryService (testable in isolation)
- CognitiveEngine public API contracts

NOTE: CognitiveEngine.chat() behavior is tested via integration tests
in test_memory_cognitive_engine.py, not via mock orchestration here.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mdb_engine.memory.chat_history import ChatHistoryService
from mdb_engine.memory.context_engineering import ContextEngineer
from mdb_engine.memory.orchestrator import CognitiveEngine


class TestChatHistoryService:
    """Test ChatHistoryService (STM) - high value, self-contained component."""

    @pytest.fixture
    def mock_collection(self):
        """Create a mock MongoDB collection."""
        collection = MagicMock()
        collection.insert_one = AsyncMock()
        collection.find = MagicMock()
        collection.create_index = AsyncMock()
        collection.find_one = AsyncMock(return_value=None)
        collection.count_documents = AsyncMock(return_value=0)
        collection.update_one = AsyncMock()
        collection.delete_many = AsyncMock()
        return collection

    @pytest.fixture
    def chat_history_service(self, mock_collection):
        """Create a ChatHistoryService instance."""
        service = ChatHistoryService(mock_collection, "test_chat_history")
        return service

    def test_initialization(self, mock_collection):
        """Test ChatHistoryService initialization (lazy indexes)."""
        service = ChatHistoryService(mock_collection, "test_chat_history")

        assert service.collection == mock_collection
        # Indexes are now created lazily, not in __init__
        assert not service._indexes_ensured

    def test_requires_collection(self):
        """Test that ChatHistoryService requires a collection."""
        with pytest.raises(ValueError, match="Collection is REQUIRED"):
            ChatHistoryService(None)

    @pytest.mark.asyncio
    async def test_add_message(self, chat_history_service, mock_collection):
        """Test adding a message to chat history."""
        await chat_history_service.add_message(session_id="session123", role="user", content="Hello", user_id="user123")

        assert mock_collection.insert_one.called
        call_args = mock_collection.insert_one.call_args[0][0]
        assert call_args["session_id"] == "session123"
        assert call_args["role"] == "user"
        assert call_args["content"] == "Hello"
        assert call_args["user_id"] == "user123"

    @pytest.mark.asyncio
    async def test_get_context(self, chat_history_service, mock_collection):
        """Test retrieving chat context."""
        # MongoDB returns newest-first because get_context sorts DESCENDING + limit.
        mock_cursor = MagicMock()
        mock_cursor.sort.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.to_list = AsyncMock(
            return_value=[
                {
                    "role": "assistant",
                    "content": "Hi!",
                    "created_at": datetime(2026, 6, 28, 12, 1, tzinfo=timezone.utc),
                },
                {"role": "user", "content": "Hello", "created_at": datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)},
            ]
        )
        mock_collection.find.return_value = mock_cursor

        context = await chat_history_service.get_context("session123", limit=10)

        # Reversed back into chronological order (oldest -> newest) for the LLM.
        assert len(context) == 2
        assert context[0]["role"] == "user"
        assert context[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_get_context_returns_newest_in_chronological_order(self, chat_history_service, mock_collection):
        """STM context must select the NEWEST messages and return them oldest -> newest.

        Regression: a session longer than ``limit`` previously returned the OLDEST
        messages (ASCENDING sort + limit), silently dropping recent context. The
        query must sort DESCENDING (newest first), apply the limit, then reverse so
        the LLM still receives chronological order.
        """
        from pymongo import DESCENDING

        # MongoDB returns these newest-first because of the DESCENDING sort + limit.
        # For a session with messages 1..15 and limit=3, the newest window is 15,14,13.
        newest_first = [
            {"role": "user", "content": "msg 15", "created_at": datetime(2026, 6, 28, 15, 0, tzinfo=timezone.utc)},
            {"role": "assistant", "content": "msg 14", "created_at": datetime(2026, 6, 28, 14, 0, tzinfo=timezone.utc)},
            {"role": "user", "content": "msg 13", "created_at": datetime(2026, 6, 28, 13, 0, tzinfo=timezone.utc)},
        ]
        mock_cursor = MagicMock()
        mock_cursor.sort.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.to_list = AsyncMock(return_value=list(newest_first))
        mock_collection.find.return_value = mock_cursor

        context = await chat_history_service.get_context("session123", limit=3)

        # Newest messages are selected via DESCENDING sort + limit.
        mock_cursor.sort.assert_called_once_with("created_at", DESCENDING)
        mock_cursor.limit.assert_called_once_with(3)

        # Returned to the LLM oldest -> newest (reversed from the DESCENDING result).
        assert [m["content"] for m in context] == ["msg 13", "msg 14", "msg 15"]

    @pytest.mark.asyncio
    async def test_get_message_count(self, chat_history_service, mock_collection):
        """Test getting message count for a session."""
        mock_collection.count_documents = AsyncMock(return_value=5)

        count = await chat_history_service.get_message_count("session123")

        assert count == 5

    # --- Collection type safety tests (regression prevention) ---

    def test_rejects_string_collection(self):
        """Passing a string instead of a collection must raise TypeError."""
        with pytest.raises(TypeError, match="not a string"):
            ChatHistoryService("chat_history")

    def test_rejects_sync_pymongo_collection(self):
        """Passing a synchronous pymongo.Collection must raise TypeError."""
        from pymongo.collection import Collection as SyncCollection

        sync_col = MagicMock(spec=SyncCollection)
        # MagicMock(spec=SyncCollection) passes isinstance checks
        with pytest.raises(TypeError, match="synchronous pymongo.Collection"):
            ChatHistoryService(sync_col)

    def test_accepts_mock_async_collection(self, mock_collection):
        """A MagicMock (simulating async Motor collection) should be accepted."""
        service = ChatHistoryService(mock_collection, "test")
        assert service.collection is mock_collection

    def test_accepts_scoped_collection_wrapper(self):
        """A mock ScopedCollectionWrapper should be accepted."""
        from mdb_engine.database.scoped_wrapper import ScopedCollectionWrapper

        mock_scw = MagicMock(spec=ScopedCollectionWrapper)
        # ScopedCollectionWrapper is not a pymongo.Collection, so it should pass
        service = ChatHistoryService(mock_scw, "test")
        assert service.collection is mock_scw


class TestCognitiveEnginePublicAPI:
    """Test CognitiveEngine public API contracts - ensures backwards compatibility."""

    def test_requires_memory_service_or_collection(self):
        """Test that CognitiveEngine requires memory_service or memory_collection."""
        mock_collection = MagicMock()

        with pytest.raises(ValueError, match="memory_collection is REQUIRED"):
            CognitiveEngine(app_slug="test_app", chat_history_collection=mock_collection)

    def test_accepts_graph_service_parameter(self):
        """Test that CognitiveEngine accepts graph_service for dependency injection."""
        import inspect

        sig = inspect.signature(CognitiveEngine.__init__)
        assert "graph_service" in sig.parameters

    def test_initialization_stores_dependencies(self):
        """Test that CognitiveEngine stores its dependencies correctly."""
        mock_memory_service = MagicMock()
        mock_chat_collection = MagicMock()
        mock_chat_collection.create_index = MagicMock()
        # Create an async-compatible mock (simulates LLMService)
        mock_llm_service = MagicMock()
        mock_llm_service.chat_completion = AsyncMock(return_value="ok")

        with (
            patch("mdb_engine.memory.chat_history.ASCENDING", 1),
            patch("mdb_engine.memory.chat_history.DESCENDING", -1),
        ):
            engine = CognitiveEngine(
                app_slug="test_app",
                memory_service=mock_memory_service,
                chat_history_collection=mock_chat_collection,
                llm_service=mock_llm_service,
                graph_service=None,
            )

        assert engine.app_slug == "test_app"
        assert engine.ltm == mock_memory_service
        assert engine.llm_service == mock_llm_service

    def test_build_chat_result_threads_stm_summary(self):
        """Regression: result['stm_summary'] must reflect the summary used, not always be None."""
        mock_memory_service = MagicMock()
        mock_memory_service._neuroplasticity_engine = None
        mock_chat_collection = MagicMock()

        with (
            patch("mdb_engine.memory.chat_history.ASCENDING", 1),
            patch("mdb_engine.memory.chat_history.DESCENDING", -1),
        ):
            engine = CognitiveEngine(
                app_slug="test_app",
                memory_service=mock_memory_service,
                chat_history_collection=mock_chat_collection,
                enable_context_engineering=True,
            )

        prompt_meta = {
            "persona_used": {"role": "assistant"},
            "entity_facts": {"Name": "Alice"},
            "dynamic_instructions": "Be concise.",
            "system_prompt": "...",
            "stm_summary": "Earlier the user discussed project timelines.",
        }

        result = engine._build_chat_result(
            ai_response="hi",
            stm_context=[],
            relevant_memories=[],
            graph_results=None,
            skills_results=[],
            session_message_count=3,
            memories_stored=[],
            prompt_meta=prompt_meta,
            user_id="user_1",
            adaptations=[],
        )

        assert result["stm_summary"] == "Earlier the user discussed project timelines."

    # --- Collection type safety tests (regression prevention) ---

    def test_rejects_string_chat_history_collection(self):
        """Passing a string for chat_history_collection must raise TypeError."""
        mock_memory_service = MagicMock()
        with pytest.raises(TypeError, match="not a string"):
            CognitiveEngine(
                app_slug="test_app",
                memory_service=mock_memory_service,
                chat_history_collection="chat_history",
            )

    def test_rejects_sync_pymongo_chat_history_collection(self):
        """Passing a synchronous pymongo.Collection must raise TypeError."""
        from pymongo.collection import Collection as SyncCollection

        mock_memory_service = MagicMock()
        sync_col = MagicMock(spec=SyncCollection)
        with pytest.raises(TypeError, match="synchronous pymongo.Collection"):
            CognitiveEngine(
                app_slug="test_app",
                memory_service=mock_memory_service,
                chat_history_collection=sync_col,
            )


class TestContextEngineeringHelpers:
    """Test Context Engineering helper methods on ContextEngineer."""

    @pytest.fixture
    def context_engineer(self):
        """Create a ContextEngineer instance with mocks."""
        mock_ltm = MagicMock()
        mock_stm = MagicMock()
        mock_graph_service = None
        mock_embedding_service = None
        # Create an async-compatible mock (simulates LLMService)
        mock_llm_service = MagicMock()
        mock_llm_service.chat_completion = AsyncMock(return_value="Summary of conversation.")

        engineer = ContextEngineer(
            ltm=mock_ltm,
            stm=mock_stm,
            graph_service=mock_graph_service,
            embedding_service=mock_embedding_service,
            llm_service=mock_llm_service,
            config={
                "enable_context_engineering": True,
                "enable_entity_extraction": True,
                "enable_dynamic_persona": True,
            },
        )
        return engineer

    def test_extract_entity_facts_biographical(self, context_engineer):
        """Test entity fact extraction from biographical memories."""
        memories = [
            {
                "memory": "User's name is Alice",
                "category": "biographical",
            },
            {
                "memory": "User uses Ubuntu 22.04",
                "category": "biographical",
            },
            {
                "memory": "User is a Python expert",
                "category": "biographical",
            },
        ]

        facts = context_engineer.extract_entity_facts("user123", memories)

        assert "Name" in facts
        assert facts["Name"] == "Alice"
        assert "OS" in facts
        assert facts["OS"] == "Ubuntu"
        assert "Language" in facts
        assert facts["Language"] == "Python"
        assert "Expertise" in facts
        assert facts["Expertise"] == "expert"

    def test_extract_entity_facts_preferences(self, context_engineer):
        """Test entity fact extraction from preferences."""
        memories = [
            {
                "memory": "User prefers dark mode interface",
                "category": "preferences",
            },
        ]

        facts = context_engineer.extract_entity_facts("user123", memories)

        assert "UI_Preference" in facts
        assert facts["UI_Preference"] == "dark mode"

    def test_extract_entity_facts_disabled(self, context_engineer):
        """Test that entity extraction can be disabled."""
        context_engineer.enable_entity_extraction = False
        memories = [{"memory": "User's name is Alice", "category": "biographical"}]

        facts = context_engineer.extract_entity_facts("user123", memories)

        assert facts == {}

    @pytest.mark.asyncio
    async def test_build_dynamic_persona_expert(self, context_engineer):
        """Test dynamic persona adaptation for expert users."""
        persona = {"role": "Assistant", "description": "Helpful", "traits": {}}
        entity_facts = {"Expertise": "expert"}
        memories = []

        instructions = await context_engineer.build_dynamic_persona(persona, entity_facts, memories)

        assert "expert" in instructions.lower()
        assert "concise" in instructions.lower() or "technical" in instructions.lower()

    @pytest.mark.asyncio
    async def test_build_dynamic_persona_beginner(self, context_engineer):
        """Test dynamic persona adaptation for beginner users."""
        persona = {"role": "Assistant", "description": "Helpful", "traits": {}}
        entity_facts = {"Expertise": "beginner"}
        memories = []

        instructions = await context_engineer.build_dynamic_persona(persona, entity_facts, memories)

        assert "beginner" in instructions.lower() or "learning" in instructions.lower()
        assert "educational" in instructions.lower() or "patient" in instructions.lower()

    @pytest.mark.asyncio
    async def test_build_dynamic_persona_high_emotion(self, context_engineer):
        """Test dynamic persona adaptation for high-emotion memories."""
        persona = {"role": "Assistant", "description": "Helpful", "traits": {}}
        entity_facts = {}
        memories = [
            {"emotion": 0.8},
            {"emotion": 0.9},
        ]

        instructions = await context_engineer.build_dynamic_persona(persona, entity_facts, memories)

        assert "empathetic" in instructions.lower() or "emotion" in instructions.lower()

    @pytest.mark.asyncio
    async def test_build_dynamic_persona_traits(self, context_engineer):
        """Test dynamic persona adaptation based on traits."""
        persona = {
            "role": "Assistant",
            "description": "Helpful",
            "traits": {"humor": 0.8, "formality": 0.3, "empathy": 0.9},
        }
        entity_facts = {}
        memories = []

        instructions = await context_engineer.build_dynamic_persona(persona, entity_facts, memories)

        assert "humor" in instructions.lower() or "friendly" in instructions.lower()
        assert "casual" in instructions.lower()
        assert "empathy" in instructions.lower()

    @pytest.mark.asyncio
    async def test_optimize_stm_context_short(self, context_engineer):
        """Test STM optimization with short context (no summary needed)."""
        stm_context = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]

        recent, summary = await context_engineer.optimize_stm_context(stm_context, "session123", "user123")

        assert len(recent) == 2
        assert summary is None

    @pytest.mark.asyncio
    async def test_optimize_stm_context_long(self, context_engineer):
        """Test STM optimization with long context (summary created)."""
        stm_context = [{"role": "user", "content": f"Message {i}"} for i in range(10)]

        # Mock the STM cache (no cached summary) — Motor methods are async
        context_engineer.stm.get_cached_summary = AsyncMock(return_value=None)
        context_engineer.stm.store_cached_summary = AsyncMock()
        context_engineer.summary_staleness_threshold = 10

        # Mock LLM service for summary generation (async interface)
        if context_engineer.llm_service:
            context_engineer.llm_service.chat_completion = AsyncMock(
                return_value="Summary of previous conversation messages."
            )

        recent, summary = await context_engineer.optimize_stm_context(stm_context, "session123", "user123")

        assert len(recent) == context_engineer.stm_raw_window
        assert summary is not None

    def test_format_persona_layer(self, context_engineer):
        """Test persona layer formatting."""
        persona = {
            "role": "Senior Python Architect",
            "description": "Expert in Python and system design",
            "traits": {"technical_focus": 0.9, "humor": 0.2},
        }

        formatted = context_engineer._format_persona_layer(persona)

        assert "[PERSONA LAYER]" in formatted
        assert "Senior Python Architect" in formatted
        assert "Expert in Python" in formatted
        assert "technical_focus" in formatted

    def test_format_persona_layer_none(self, context_engineer):
        """Test persona layer formatting with None persona."""
        formatted = context_engineer._format_persona_layer(None)

        assert formatted == ""

    def test_format_entity_memory(self, context_engineer):
        """Test entity memory formatting (values are sanitized with XML tags)."""
        entity_facts = {"Name": "Alice", "OS": "Ubuntu", "Language": "Python"}

        formatted = context_engineer._format_entity_memory(entity_facts)

        assert "[USER CONTEXT]" in formatted
        # Values are wrapped by sanitize_for_prompt: <fact>Alice</fact>
        assert "Alice" in formatted
        assert "Ubuntu" in formatted
        assert "Python" in formatted

    def test_format_entity_memory_empty(self, context_engineer):
        """Test entity memory formatting with empty facts."""
        formatted = context_engineer._format_entity_memory({})

        assert formatted == ""

    def test_format_memory_layer(self, context_engineer):
        """Test memory layer formatting."""
        ltm_context = "RELEVANT FACTS FROM LONG-TERM MEMORY:\n- User loves Python\n"
        graph_context = "[GRAPH CONTEXT]\nNode: Python\n"

        formatted = context_engineer._format_memory_layer(ltm_context, graph_context)

        assert "[GRAPH CONTEXT]" in formatted
        assert "[RELEVANT MEMORY]" in formatted
        assert "User loves Python" in formatted

    def test_format_stm_layer(self, context_engineer):
        """Test STM layer formatting."""
        recent_messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        summary = "Previous conversation about Python"

        formatted = context_engineer._format_stm_layer(recent_messages, summary)

        assert "[PREVIOUS CONTEXT]" in formatted
        assert "Previous conversation about Python" in formatted
        assert "[CHAT HISTORY]" in formatted

    def test_construct_context_engineered_prompt(self, context_engineer):
        """Test full context-engineered prompt construction."""
        persona = {
            "role": "Assistant",
            "description": "Helpful AI",
            "traits": {"empathy": 0.7},
        }
        entity_facts = {"Name": "Alice", "Expertise": "expert"}
        ltm_context = "RELEVANT FACTS:\n- User loves Python\n"
        graph_context = ""
        dynamic_instructions = "Be concise. User is an expert."
        stm_summary = "Previous conversation about Python"

        prompt = context_engineer.construct_context_engineered_prompt(
            persona=persona,
            entity_facts=entity_facts,
            ltm_context=ltm_context,
            graph_context=graph_context,
            dynamic_instructions=dynamic_instructions,
            stm_summary=stm_summary,
        )

        assert "[PERSONA LAYER]" in prompt
        assert "[META-INSTRUCTIONS]" in prompt
        assert "[USER CONTEXT]" in prompt
        assert "[RELEVANT MEMORY]" in prompt
        assert "[PREVIOUS CONTEXT]" in prompt
        assert "Alice" in prompt
        assert "expert" in prompt
        assert "User loves Python" in prompt


class TestSearchStrategyOverride:
    """Test the search_strategy override parameter on CognitiveEngine.chat().

    Verifies that an explicit search_strategy bypasses the automatic
    QueryClassifier and routes directly to the correct graph search method.
    """

    @pytest.fixture
    def _cognitive_engine(self):
        """Create a CognitiveEngine with fully mocked dependencies."""
        mock_chat_collection = MagicMock()
        mock_chat_collection.create_index = AsyncMock()

        mock_memory_service = MagicMock()
        mock_memory_service.search = AsyncMock(return_value=[])
        mock_memory_service.timeline_service = None

        mock_llm_service = MagicMock()
        mock_llm_service.chat_completion = AsyncMock(return_value="AI response")

        # Graph service mock with all search methods
        mock_graph_service = MagicMock()
        mock_graph_service.classify_query = MagicMock(return_value="basic")
        mock_graph_service.hybrid_search = AsyncMock(return_value={"entry_nodes": [], "graph_context": []})
        mock_graph_service.local_search = AsyncMock(
            return_value={"entry_nodes": [], "graph_context": [], "community_summaries": []}
        )
        mock_graph_service.global_search = AsyncMock(
            return_value={"synthesized_answer": "summary", "communities_used": 5}
        )
        mock_graph_service.drift_search = AsyncMock(
            return_value={"entry_nodes": [], "graph_context": [], "community_context": []}
        )

        with (
            patch("mdb_engine.memory.chat_history.ASCENDING", 1),
            patch("mdb_engine.memory.chat_history.DESCENDING", -1),
        ):
            engine = CognitiveEngine(
                app_slug="test_app",
                memory_service=mock_memory_service,
                chat_history_collection=mock_chat_collection,
                llm_service=mock_llm_service,
                graph_service=mock_graph_service,
                enable_context_engineering=False,
            )

        # Mock STM methods used inside chat()
        engine.stm.add_message = AsyncMock()
        engine.stm.get_context = AsyncMock(return_value=[])
        engine.stm.get_session_count = AsyncMock(return_value=1)

        # Disable prospective memory to avoid unrelated mocking
        engine._prospective_memory = None

        return engine

    @pytest.mark.asyncio
    async def test_search_strategy_none_uses_classifier(self, _cognitive_engine):
        """search_strategy=None (default) should use the automatic classifier."""
        engine = _cognitive_engine
        await engine.chat(
            user_id="user1",
            session_id="sess1",
            user_query="Tell me about Alice",
            search_strategy=None,
            extract_facts=False,
        )

        engine._graph_service.classify_query.assert_called_once_with("Tell me about Alice")

    @pytest.mark.asyncio
    async def test_search_strategy_auto_uses_classifier(self, _cognitive_engine):
        """search_strategy='auto' should behave the same as None — use the classifier."""
        engine = _cognitive_engine
        await engine.chat(
            user_id="user1",
            session_id="sess1",
            user_query="Tell me about Alice",
            search_strategy="auto",
            extract_facts=False,
        )

        engine._graph_service.classify_query.assert_called_once_with("Tell me about Alice")

    @pytest.mark.asyncio
    async def test_search_strategy_global_skips_classifier(self, _cognitive_engine):
        """search_strategy='global' should bypass the classifier and call global_search."""
        engine = _cognitive_engine
        await engine.chat(
            user_id="user1",
            session_id="sess1",
            user_query="Tell me about Alice",
            search_strategy="global",
            extract_facts=False,
        )

        engine._graph_service.classify_query.assert_not_called()
        engine._graph_service.global_search.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_search_strategy_local_skips_classifier(self, _cognitive_engine):
        """search_strategy='local' should bypass the classifier and call local_search."""
        engine = _cognitive_engine
        await engine.chat(
            user_id="user1",
            session_id="sess1",
            user_query="What are the main themes?",
            search_strategy="local",
            extract_facts=False,
        )

        engine._graph_service.classify_query.assert_not_called()
        engine._graph_service.local_search.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_search_strategy_drift_skips_classifier(self, _cognitive_engine):
        """search_strategy='drift' should bypass the classifier and call drift_search."""
        engine = _cognitive_engine
        await engine.chat(
            user_id="user1",
            session_id="sess1",
            user_query="Hello",
            search_strategy="drift",
            extract_facts=False,
        )

        engine._graph_service.classify_query.assert_not_called()
        engine._graph_service.drift_search.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_search_strategy_basic_skips_classifier(self, _cognitive_engine):
        """search_strategy='basic' should bypass the classifier and call hybrid_search."""
        engine = _cognitive_engine
        await engine.chat(
            user_id="user1",
            session_id="sess1",
            user_query="What are the main themes?",
            search_strategy="basic",
            extract_facts=False,
        )

        engine._graph_service.classify_query.assert_not_called()
        engine._graph_service.hybrid_search.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_search_strategy_invalid_raises_error(self, _cognitive_engine):
        """An invalid search_strategy value should raise ValueError immediately."""
        engine = _cognitive_engine
        with pytest.raises(ValueError, match="Invalid search_strategy 'foobar'"):
            await engine.chat(
                user_id="user1",
                session_id="sess1",
                user_query="Hello",
                search_strategy="foobar",
            )
