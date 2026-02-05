"""
Unit tests for Memory Orchestrator components.

HIGH-VALUE TESTS ONLY:
- LLM Provider abstraction (testable in isolation)
- ChatHistoryService (testable in isolation)
- CognitiveEngine public API contracts

NOTE: CognitiveEngine.chat() behavior is tested via integration tests
in test_memory_cognitive_engine.py, not via mock orchestration here.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from mdb_engine.memory.orchestrator import (
    ChatHistoryService,
    CognitiveEngine,
    GeminiProvider,
    LLMProvider,
    OpenAIProvider,
)


class TestLLMProvider:
    """Test LLM Provider abstraction - high value, tests real behavior."""

    def test_llm_provider_is_abstract(self):
        """Test that LLMProvider is abstract and cannot be instantiated."""
        with pytest.raises(TypeError):
            LLMProvider()

    def test_openai_provider_initialization(self):
        """Test OpenAIProvider initialization."""
        mock_client = MagicMock()
        provider = OpenAIProvider(mock_client)

        assert provider.client == mock_client
        assert isinstance(provider, LLMProvider)

    def test_openai_provider_generate_chat_completion(self):
        """Test OpenAIProvider chat completion generation."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Test response"
        mock_client.chat.completions.create.return_value = mock_response

        provider = OpenAIProvider(mock_client)
        messages = [{"role": "user", "content": "Hello"}]

        result = provider.generate_chat_completion(messages, model="gpt-4o")

        assert result == "Test response"
        mock_client.chat.completions.create.assert_called_once()

    def test_gemini_provider_initialization(self):
        """Test GeminiProvider initialization."""
        mock_client = MagicMock()
        provider = GeminiProvider(mock_client, default_model="gemini-3-flash-preview")

        assert provider.client == mock_client
        assert provider.default_model == "gemini-3-flash-preview"
        assert isinstance(provider, LLMProvider)

    def test_gemini_provider_generate_chat_completion(self):
        """Test GeminiProvider chat completion generation."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Gemini response"
        mock_client.models.generate_content.return_value = mock_response

        provider = GeminiProvider(mock_client, default_model="gemini-3-flash-preview")
        messages = [{"role": "user", "content": "Hello"}]

        result = provider.generate_chat_completion(messages)

        assert result == "Gemini response"
        mock_client.models.generate_content.assert_called_once()


class TestChatHistoryService:
    """Test ChatHistoryService (STM) - high value, self-contained component."""

    @pytest.fixture
    def mock_collection(self):
        """Create a mock MongoDB collection."""
        collection = MagicMock()
        collection.insert_one = MagicMock()
        collection.find = MagicMock()
        return collection

    @pytest.fixture
    def chat_history_service(self, mock_collection):
        """Create a ChatHistoryService instance."""
        with (
            patch("mdb_engine.memory.orchestrator.ASCENDING", 1),
            patch("mdb_engine.memory.orchestrator.DESCENDING", -1),
        ):
            service = ChatHistoryService(mock_collection, "test_chat_history")
        return service

    def test_initialization(self, mock_collection):
        """Test ChatHistoryService initialization."""
        with (
            patch("mdb_engine.memory.orchestrator.ASCENDING", 1),
            patch("mdb_engine.memory.orchestrator.DESCENDING", -1),
        ):
            service = ChatHistoryService(mock_collection, "test_chat_history")

        assert service.collection == mock_collection
        assert mock_collection.create_index.called

    def test_requires_collection(self):
        """Test that ChatHistoryService requires a collection."""
        with pytest.raises(ValueError, match="Collection is REQUIRED"):
            ChatHistoryService(None)

    def test_add_message(self, chat_history_service, mock_collection):
        """Test adding a message to chat history."""
        chat_history_service.add_message(
            session_id="session123", role="user", content="Hello", user_id="user123"
        )

        assert mock_collection.insert_one.called
        call_args = mock_collection.insert_one.call_args[0][0]
        assert call_args["session_id"] == "session123"
        assert call_args["role"] == "user"
        assert call_args["content"] == "Hello"
        assert call_args["user_id"] == "user123"

    def test_get_context(self, chat_history_service, mock_collection):
        """Test retrieving chat context."""
        mock_cursor = MagicMock()
        mock_cursor.sort.return_value = mock_cursor
        mock_cursor.limit.return_value = [
            {"role": "user", "content": "Hello", "created_at": datetime.now(timezone.utc)},
            {"role": "assistant", "content": "Hi!", "created_at": datetime.now(timezone.utc)},
        ]
        mock_collection.find.return_value = mock_cursor

        context = chat_history_service.get_context("session123", limit=10)

        assert len(context) == 2
        assert context[0]["role"] == "user"
        assert context[1]["role"] == "assistant"

    def test_get_message_count(self, chat_history_service, mock_collection):
        """Test getting message count for a session."""
        mock_collection.count_documents.return_value = 5

        count = chat_history_service.get_message_count("session123")

        assert count == 5


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

    def test_auto_detect_openai_provider(self):
        """Test auto-detection of OpenAI provider from client."""
        mock_memory_service = MagicMock()
        mock_chat_collection = MagicMock()
        mock_chat_collection.create_index = MagicMock()

        # Mock OpenAI client signature
        mock_openai_client = MagicMock()
        mock_openai_client.chat = MagicMock()
        mock_openai_client.chat.completions = MagicMock()

        with (
            patch("mdb_engine.memory.orchestrator.ASCENDING", 1),
            patch("mdb_engine.memory.orchestrator.DESCENDING", -1),
        ):
            engine = CognitiveEngine(
                app_slug="test_app",
                memory_service=mock_memory_service,
                chat_history_collection=mock_chat_collection,
                llm_client=mock_openai_client,
                graph_service=None,
            )

        assert isinstance(engine.llm_provider, OpenAIProvider)

    def test_initialization_stores_dependencies(self):
        """Test that CognitiveEngine stores its dependencies correctly."""
        mock_memory_service = MagicMock()
        mock_chat_collection = MagicMock()
        mock_chat_collection.create_index = MagicMock()
        mock_llm_provider = MagicMock(spec=LLMProvider)

        with (
            patch("mdb_engine.memory.orchestrator.ASCENDING", 1),
            patch("mdb_engine.memory.orchestrator.DESCENDING", -1),
        ):
            engine = CognitiveEngine(
                app_slug="test_app",
                memory_service=mock_memory_service,
                chat_history_collection=mock_chat_collection,
                llm_provider=mock_llm_provider,
                graph_service=None,
            )

        assert engine.app_slug == "test_app"
        assert engine.ltm == mock_memory_service
        assert engine.llm_provider == mock_llm_provider


class TestContextEngineeringHelpers:
    """Test Context Engineering helper methods."""

    @pytest.fixture
    def cognitive_engine(self):
        """Create a CognitiveEngine instance with mocks."""
        mock_memory_service = MagicMock()
        mock_chat_collection = MagicMock()
        mock_chat_collection.create_index = MagicMock()
        mock_llm_provider = MagicMock(spec=LLMProvider)

        with (
            patch("mdb_engine.memory.orchestrator.ASCENDING", 1),
            patch("mdb_engine.memory.orchestrator.DESCENDING", -1),
        ):
            engine = CognitiveEngine(
                app_slug="test_app",
                memory_service=mock_memory_service,
                chat_history_collection=mock_chat_collection,
                llm_provider=mock_llm_provider,
                enable_context_engineering=True,
                enable_entity_extraction=True,
                enable_dynamic_persona=True,
            )
        return engine

    def test_extract_entity_facts_biographical(self, cognitive_engine):
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

        facts = cognitive_engine._extract_entity_facts("user123", memories)

        assert "Name" in facts
        assert facts["Name"] == "Alice"
        assert "OS" in facts
        assert facts["OS"] == "Ubuntu"
        assert "Language" in facts
        assert facts["Language"] == "Python"
        assert "Expertise" in facts
        assert facts["Expertise"] == "expert"

    def test_extract_entity_facts_preferences(self, cognitive_engine):
        """Test entity fact extraction from preferences."""
        memories = [
            {
                "memory": "User prefers dark mode interface",
                "category": "preferences",
            },
        ]

        facts = cognitive_engine._extract_entity_facts("user123", memories)

        assert "UI_Preference" in facts
        assert facts["UI_Preference"] == "dark mode"

    def test_extract_entity_facts_disabled(self, cognitive_engine):
        """Test that entity extraction can be disabled."""
        cognitive_engine.enable_entity_extraction = False
        memories = [{"memory": "User's name is Alice", "category": "biographical"}]

        facts = cognitive_engine._extract_entity_facts("user123", memories)

        assert facts == {}

    def test_build_dynamic_persona_expert(self, cognitive_engine):
        """Test dynamic persona adaptation for expert users."""
        persona = {"role": "Assistant", "description": "Helpful", "traits": {}}
        entity_facts = {"Expertise": "expert"}
        memories = []

        instructions = cognitive_engine._build_dynamic_persona(persona, entity_facts, memories)

        assert "expert" in instructions.lower()
        assert "concise" in instructions.lower() or "technical" in instructions.lower()

    def test_build_dynamic_persona_beginner(self, cognitive_engine):
        """Test dynamic persona adaptation for beginner users."""
        persona = {"role": "Assistant", "description": "Helpful", "traits": {}}
        entity_facts = {"Expertise": "beginner"}
        memories = []

        instructions = cognitive_engine._build_dynamic_persona(persona, entity_facts, memories)

        assert "beginner" in instructions.lower() or "learning" in instructions.lower()
        assert "educational" in instructions.lower() or "patient" in instructions.lower()

    def test_build_dynamic_persona_high_emotion(self, cognitive_engine):
        """Test dynamic persona adaptation for high-emotion memories."""
        persona = {"role": "Assistant", "description": "Helpful", "traits": {}}
        entity_facts = {}
        memories = [
            {"emotion": 0.8},
            {"emotion": 0.9},
        ]

        instructions = cognitive_engine._build_dynamic_persona(persona, entity_facts, memories)

        assert "empathetic" in instructions.lower() or "emotion" in instructions.lower()

    def test_build_dynamic_persona_traits(self, cognitive_engine):
        """Test dynamic persona adaptation based on traits."""
        persona = {
            "role": "Assistant",
            "description": "Helpful",
            "traits": {"humor": 0.8, "formality": 0.3, "empathy": 0.9},
        }
        entity_facts = {}
        memories = []

        instructions = cognitive_engine._build_dynamic_persona(persona, entity_facts, memories)

        assert "humor" in instructions.lower() or "friendly" in instructions.lower()
        assert "casual" in instructions.lower()
        assert "empathy" in instructions.lower()

    def test_optimize_stm_context_short(self, cognitive_engine):
        """Test STM optimization with short context (no summary needed)."""
        stm_context = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]

        recent, summary = cognitive_engine._optimize_stm_context(
            stm_context, "session123", "user123"
        )

        assert len(recent) == 2
        assert summary is None

    def test_optimize_stm_context_long(self, cognitive_engine):
        """Test STM optimization with long context (summary created)."""
        stm_context = [{"role": "user", "content": f"Message {i}"} for i in range(10)]

        recent, summary = cognitive_engine._optimize_stm_context(
            stm_context, "session123", "user123"
        )

        assert len(recent) == cognitive_engine.stm_raw_window
        assert summary is not None
        assert "Previous conversation context" in summary

    def test_format_persona_layer(self, cognitive_engine):
        """Test persona layer formatting."""
        persona = {
            "role": "Senior Python Architect",
            "description": "Expert in Python and system design",
            "traits": {"technical_focus": 0.9, "humor": 0.2},
        }

        formatted = cognitive_engine._format_persona_layer(persona)

        assert "[PERSONA LAYER]" in formatted
        assert "Senior Python Architect" in formatted
        assert "Expert in Python" in formatted
        assert "technical_focus" in formatted

    def test_format_persona_layer_none(self, cognitive_engine):
        """Test persona layer formatting with None persona."""
        formatted = cognitive_engine._format_persona_layer(None)

        assert formatted == ""

    def test_format_entity_memory(self, cognitive_engine):
        """Test entity memory formatting."""
        entity_facts = {"Name": "Alice", "OS": "Ubuntu", "Language": "Python"}

        formatted = cognitive_engine._format_entity_memory(entity_facts)

        assert "[USER CONTEXT]" in formatted
        assert "Name: Alice" in formatted
        assert "OS: Ubuntu" in formatted
        assert "Language: Python" in formatted

    def test_format_entity_memory_empty(self, cognitive_engine):
        """Test entity memory formatting with empty facts."""
        formatted = cognitive_engine._format_entity_memory({})

        assert formatted == ""

    def test_format_memory_layer(self, cognitive_engine):
        """Test memory layer formatting."""
        ltm_context = "RELEVANT FACTS FROM LONG-TERM MEMORY:\n- User loves Python\n"
        graph_context = "[GRAPH CONTEXT]\nNode: Python\n"

        formatted = cognitive_engine._format_memory_layer(ltm_context, graph_context)

        assert "[GRAPH CONTEXT]" in formatted
        assert "[RELEVANT MEMORY]" in formatted
        assert "User loves Python" in formatted

    def test_format_stm_layer(self, cognitive_engine):
        """Test STM layer formatting."""
        recent_messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        summary = "Previous conversation about Python"

        formatted = cognitive_engine._format_stm_layer(recent_messages, summary)

        assert "[PREVIOUS CONTEXT]" in formatted
        assert "Previous conversation about Python" in formatted
        assert "[CHAT HISTORY]" in formatted

    def test_construct_context_engineered_prompt(self, cognitive_engine):
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

        prompt = cognitive_engine._construct_context_engineered_prompt(
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
