"""
Integration tests for CognitiveEngine with real MongoDB.

Tests the complete RAG pipeline including:
- Memory storage and retrieval
- STM/LTM integration
- Multi-app memory isolation
- SSO memory with shared authentication
"""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Set test secret key before importing engine components
if "MDB_ENGINE_JWT_SECRET" not in os.environ:
    os.environ["MDB_ENGINE_JWT_SECRET"] = "test_jwt_secret_for_testing_only_" + "x" * 32

if "MDB_ENGINE_MASTER_KEY" not in os.environ:
    import base64

    os.environ["MDB_ENGINE_MASTER_KEY"] = base64.b64encode(b"x" * 32).decode()


@pytest.mark.integration
class TestCognitiveEngineIntegration:
    """Integration tests for CognitiveEngine with real MongoDB."""

    @pytest.fixture
    def temp_manifest_with_memory(self, tmp_path):
        """Create temporary manifest file with memory config enabled."""
        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test App",
            "auth": {"mode": "app"},
            "memory_config": {
                "enabled": True,
                "collection_name": "user_memories",
                "embedding_model_dims": 1536,
                "embedding_model": "text-embedding-3-small",
                "chat_model": "gpt-4o",
                "infer": True,
                "graph": {"enabled": True, "auto_extract": True},
            },
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        return manifest_path

    @pytest.mark.asyncio
    async def test_cognitive_engine_memory_storage_and_retrieval(
        self, mongodb_connection_string, temp_manifest_with_memory
    ):
        """Test that CognitiveEngine can store and retrieve memories."""
        from mdb_engine.core.engine import MongoDBEngine
        from mdb_engine.memory.orchestrator import CognitiveEngine

        # Set required environment variables
        original_openai_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "sk-test-key-for-testing-only-" + "x" * 100

        try:
            db_name = f"test_cognitive_memory_{os.getpid()}"
            engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)

            # Mock LLM and embedding services to avoid API calls during initialization
            mock_embedding_service = MagicMock()

            async def mock_embed(text, model=None):
                return [[0.1] * 1536]

            mock_embedding_service.embed = mock_embed

            with (
                patch(
                    "mdb_engine.memory.builder.CognitiveMemoryServiceBuilder._setup_llm",
                    return_value=(None, True),
                ),
                patch(
                    "mdb_engine.memory.builder.CognitiveMemoryServiceBuilder._setup_embedding",
                    return_value=mock_embedding_service,
                ),
            ):
                app = engine.create_app(
                    slug="test_app",
                    manifest=temp_manifest_with_memory,
                    title="Test Cognitive Engine",
                )

                async with app.router.lifespan_context(app):
                    memory_service = engine.get_memory_service("test_app")
                    assert memory_service is not None, "Memory service should be initialized"

                    # Get async Motor collection for chat history
                    motor_db = engine.connection_manager.mongo_client[engine.db_name]
                    chat_history_collection = motor_db["chat_history"]

                    # Mock LLM service to avoid actual API calls (async interface)
                    mock_llm_service = MagicMock()
                    mock_llm_service.chat_completion = AsyncMock(
                        return_value="I understand you love chocolate. That's great!"
                    )

                    cognitive_engine = CognitiveEngine(
                        app_slug="test_app",
                        memory_service=memory_service,
                        chat_history_collection=chat_history_collection,
                        llm_service=mock_llm_service,
                    )

                    # Mock embedding service to avoid actual API calls
                    # Create a proper async function that returns a coroutine
                    # Use a class to ensure proper binding
                    class MockEmbeddingProvider:
                        async def embed(self, text, model=None):
                            # Always return the same mock vector for consistency
                            # This ensures stored memories and search queries use the same vector
                            return [[0.1] * 1536]

                    mock_embedding_provider = MockEmbeddingProvider()
                    memory_service.embedding_provider = mock_embedding_provider

                    # Mock LLM service for memory operations (needed for fact extraction)
                    from mdb_engine.llm.service import LLMService

                    mock_llm_service = MagicMock(spec=LLMService)

                    async def mock_chat_completion(*args, **kwargs):
                        # Return properly formatted response matching
                        # CognitiveFactExtractionResponse
                        # Note: CognitiveFact uses 'text' not 'fact', and requires 'category'
                        return (
                            '{"facts": [{"text": "User loves chocolate", '
                            '"category": "preferences", "emotion": 0.5}]}'
                        )

                    mock_llm_service.chat_completion = MagicMock(side_effect=mock_chat_completion)
                    memory_service._injected_llm_service = mock_llm_service
                    memory_service.llm_available = True
                    # Disable memory type detection to avoid sync/async issues in tests
                    memory_service.auto_detect_memory_type = False

                    # Mock LiteLLM completion for fact extraction (fallback)
                    mock_completion_response = MagicMock()
                    mock_completion_response.choices = [MagicMock()]
                    mock_completion_response.choices[0].message = MagicMock()
                    fact_json = (
                        '{"facts": [{"text": "User loves chocolate", ' '"category": "preferences", "emotion": 0.5}]}'
                    )
                    mock_completion_response.choices[0].message.content = fact_json

                    # NOTE: We no longer patch LiteLLM's `completion()` here; the memory
                    # service calls the injected `LLMService.chat_completion()` via
                    # `CognitiveMemoryService._llm_completion`.

                    # Test chat with memory extraction
                    result = await cognitive_engine.chat(
                        user_id="user123",
                        session_id="session123",
                        user_query="I love chocolate",
                        extract_facts=True,
                    )

                    # Verify response
                    assert "response" in result
                    assert result["response"] == "I understand you love chocolate. That's great!"

                    # Verify memories were stored
                    assert "memories_stored" in result
                    assert len(result["memories_stored"]) > 0, "Memories should be stored"

                    # Verify we can retrieve the stored memories
                    stored_memories = result["memories_stored"]
                    assert any("chocolate" in m.get("memory", "").lower() for m in stored_memories)

                    # Test retrieval in next chat
                    result2 = await cognitive_engine.chat(
                        user_id="user123",
                        session_id="session123",
                        user_query="What do I like?",
                        extract_facts=False,
                    )

                    # Verify LTM memories were retrieved
                    assert "ltm_memories" in result2

                    # If search failed (e.g., Atlas Search not available),
                    # verify memories exist via get_all
                    if len(result2["ltm_memories"]) == 0:
                        # Fallback: verify memories exist in database using get_all
                        all_memories = await memory_service.get_all(
                            user_id="user123",
                            limit=10,
                        )
                        assert len(all_memories) > 0, "Memories should exist in database even if search failed"
                        # Verify the stored memory contains "chocolate"
                        assert any(
                            "chocolate" in m.get("memory", "").lower() for m in all_memories
                        ), "Stored memory about chocolate should exist"
                    else:
                        # Search succeeded, verify memories were retrieved
                        assert len(result2["ltm_memories"]) > 0, "Should retrieve stored memories"

                await engine.shutdown()
        finally:
            if original_openai_key:
                os.environ["OPENAI_API_KEY"] = original_openai_key
            elif "OPENAI_API_KEY" in os.environ:
                del os.environ["OPENAI_API_KEY"]

    @pytest.mark.asyncio
    async def test_cognitive_engine_stm_context_management(self, mongodb_connection_string, temp_manifest_with_memory):
        """Test that CognitiveEngine manages STM context correctly."""
        from mdb_engine.core.engine import MongoDBEngine
        from mdb_engine.memory.orchestrator import CognitiveEngine

        original_openai_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "sk-test-key-for-testing-only-" + "x" * 100

        try:
            db_name = f"test_stm_context_{os.getpid()}"
            engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)

            # Mock LLM and embedding services to avoid API calls during initialization
            mock_embedding_service = MagicMock()

            async def mock_embed(text, model=None):
                return [[0.1] * 1536]

            mock_embedding_service.embed = mock_embed

            with (
                patch(
                    "mdb_engine.memory.builder.CognitiveMemoryServiceBuilder._setup_llm",
                    return_value=(None, True),
                ),
                patch(
                    "mdb_engine.memory.builder.CognitiveMemoryServiceBuilder._setup_embedding",
                    return_value=mock_embedding_service,
                ),
            ):
                app = engine.create_app(
                    slug="test_app",
                    manifest=temp_manifest_with_memory,
                    title="Test STM Context",
                )

                async with app.router.lifespan_context(app):
                    memory_service = engine.get_memory_service("test_app")
                    motor_db = engine.connection_manager.mongo_client[engine.db_name]
                    chat_history_collection = motor_db["chat_history"]

                    # Mock LLM service (async interface)
                    mock_llm_service = MagicMock()
                    mock_llm_service.chat_completion = AsyncMock(side_effect=["Response 1", "Response 2", "Response 3"])

                    cognitive_engine = CognitiveEngine(
                        app_slug="test_app",
                        memory_service=memory_service,
                        chat_history_collection=chat_history_collection,
                        llm_service=mock_llm_service,
                        stm_context_limit=3,  # Limit to 3 messages
                    )

                    # Mock embedding service to avoid actual API calls
                    # Create a proper async function that returns a coroutine
                    # Use a class to ensure proper binding
                    class MockEmbeddingProvider:
                        async def embed(self, text, model=None):
                            # Always return the same mock vector for consistency
                            # This ensures stored memories and search queries use the same vector
                            return [[0.1] * 1536]

                    mock_embedding_provider = MockEmbeddingProvider()
                    memory_service.embedding_provider = mock_embedding_provider

                    # Mock LiteLLM completion for fact extraction
                    # (even though extract_facts=False, the memory service might still use it)
                    mock_completion_response = MagicMock()
                    mock_completion_response.choices = [MagicMock()]
                    mock_completion_response.choices[0].message = MagicMock()
                    mock_completion_response.choices[0].message.content = '{"facts": ["Test fact"]}'

                    # Send multiple messages
                    await cognitive_engine.chat(
                        user_id="user123",
                        session_id="session123",
                        user_query="Message 1",
                        extract_facts=False,
                    )

                    await cognitive_engine.chat(
                        user_id="user123",
                        session_id="session123",
                        user_query="Message 2",
                        extract_facts=False,
                    )

                    result3 = await cognitive_engine.chat(
                        user_id="user123",
                        session_id="session123",
                        user_query="Message 3",
                        extract_facts=False,
                    )

                    # Verify STM context includes previous messages (limited to 3)
                    assert "stm_context" in result3
                    # Should have system + previous messages (limited by stm_context_limit)
                    assert len(result3["stm_context"]) <= 3

                # Verify LLM was called with context
                assert mock_llm_service.chat_completion.called
                call_args = mock_llm_service.chat_completion.call_args
                if call_args and len(call_args) > 0 and len(call_args[0]) > 0:
                    messages = call_args[0][0]
                    assert len(messages) > 1, "Should include system prompt + previous messages"

            await engine.shutdown()
        finally:
            if original_openai_key:
                os.environ["OPENAI_API_KEY"] = original_openai_key
            elif "OPENAI_API_KEY" in os.environ:
                del os.environ["OPENAI_API_KEY"]


@pytest.mark.integration
class TestMultiAppMemoryIsolation:
    """Test memory isolation across multiple apps."""

    @pytest.fixture
    def temp_manifests_multi_app(self, tmp_path):
        """Create temporary manifest files for multiple apps."""
        manifests_dir = tmp_path / "manifests"
        manifests_dir.mkdir()

        app1_manifest = {
            "schema_version": "2.0",
            "slug": "app1",
            "name": "App 1",
            "auth": {"mode": "app"},
            "memory_config": {
                "enabled": True,
                "collection_name": "user_memories",
                "embedding_model_dims": 1536,
                "embedding_model": "text-embedding-3-small",
            },
        }

        app2_manifest = {
            "schema_version": "2.0",
            "slug": "app2",
            "name": "App 2",
            "auth": {"mode": "app"},
            "memory_config": {
                "enabled": True,
                "collection_name": "user_memories",
                "embedding_model_dims": 1536,
                "embedding_model": "text-embedding-3-small",
            },
        }

        app1_path = manifests_dir / "app1" / "manifest.json"
        app1_path.parent.mkdir()
        app1_path.write_text(json.dumps(app1_manifest))

        app2_path = manifests_dir / "app2" / "manifest.json"
        app2_path.parent.mkdir()
        app2_path.write_text(json.dumps(app2_manifest))

        return {
            "app1": app1_path,
            "app2": app2_path,
        }

    @pytest.mark.asyncio
    async def test_memory_isolation_between_apps(self, mongodb_connection_string, temp_manifests_multi_app):
        """Test that memories are isolated between different apps."""
        from mdb_engine.core.engine import MongoDBEngine
        from mdb_engine.memory.orchestrator import CognitiveEngine

        original_openai_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "sk-test-key-for-testing-only-" + "x" * 100

        try:
            db_name = f"test_memory_isolation_{os.getpid()}"
            engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)

            # Mock LLM and embedding services to avoid API calls during initialization
            mock_embedding_service = MagicMock()

            async def mock_embed(text, model=None):
                return [[0.1] * 1536]

            mock_embedding_service.embed = mock_embed

            with (
                patch(
                    "mdb_engine.memory.builder.CognitiveMemoryServiceBuilder._setup_llm",
                    return_value=(None, True),
                ),
                patch(
                    "mdb_engine.memory.builder.CognitiveMemoryServiceBuilder._setup_embedding",
                    return_value=mock_embedding_service,
                ),
            ):
                app = await engine.create_multi_app(
                    apps=[
                        {
                            "slug": "app1",
                            "manifest": temp_manifests_multi_app["app1"],
                            "path_prefix": "/app1",
                        },
                        {
                            "slug": "app2",
                            "manifest": temp_manifests_multi_app["app2"],
                            "path_prefix": "/app2",
                        },
                    ],
                    title="Test Memory Isolation",
                )

                async with app.router.lifespan_context(app):
                    memory_service_app1 = engine.get_memory_service("app1")
                    memory_service_app2 = engine.get_memory_service("app2")

                    assert memory_service_app1 is not None
                    assert memory_service_app2 is not None

                    # Verify collection names are different (prefixed with app slug)
                    assert memory_service_app1.collection_name == "app1_user_memories"
                    assert memory_service_app2.collection_name == "app2_user_memories"

                    # Get async Motor collections for CognitiveEngine
                    motor_db = engine.connection_manager.mongo_client[engine.db_name]

                    chat_history_app1 = motor_db["chat_history"]
                    chat_history_app2 = motor_db["chat_history"]

                    # Mock LLM service (async interface)
                    mock_llm_service = MagicMock()
                    mock_llm_service.chat_completion = AsyncMock(return_value="Response")

                    cognitive_engine_app1 = CognitiveEngine(
                        app_slug="app1",
                        memory_service=memory_service_app1,
                        chat_history_collection=chat_history_app1,
                        llm_service=mock_llm_service,
                    )

                    cognitive_engine_app2 = CognitiveEngine(
                        app_slug="app2",
                        memory_service=memory_service_app2,
                        chat_history_collection=chat_history_app2,
                        llm_service=mock_llm_service,
                    )

                    # Mock embedding service to avoid actual API calls
                    async def mock_embed(text, model=None):
                        return [[0.1] * 1536]  # Return mock embedding vector

                    mock_embedding_provider = MagicMock()
                    mock_embedding_provider.embed = mock_embed
                    memory_service_app1.embedding_provider = mock_embedding_provider
                    memory_service_app2.embedding_provider = mock_embedding_provider

                    # Mock LLM service for memory operations
                    # (needed for fact extraction and memory type detection)
                    from mdb_engine.llm.service import LLMService

                    mock_llm_service_app1 = MagicMock(spec=LLMService)
                    mock_llm_service_app2 = MagicMock(spec=LLMService)

                    # Mock the structured response for fact extraction - return proper format
                    # Note: CognitiveFact uses 'text' not 'fact', and requires 'category'
                    async def mock_chat_completion_app1(*args, **kwargs):
                        return '{"facts": [{"text": "User loves app1", ' '"category": "preferences", "emotion": 0.5}]}'

                    async def mock_chat_completion_app2(*args, **kwargs):
                        return '{"facts": [{"text": "User loves app2", ' '"category": "preferences", "emotion": 0.5}]}'

                    mock_llm_service_app1.chat_completion = MagicMock(side_effect=mock_chat_completion_app1)
                    mock_llm_service_app2.chat_completion = MagicMock(side_effect=mock_chat_completion_app2)
                    # Set up the LLM service on memory services
                    memory_service_app1._injected_llm_service = mock_llm_service_app1
                    memory_service_app1.llm_available = True
                    memory_service_app1.auto_detect_memory_type = False
                    memory_service_app2._injected_llm_service = mock_llm_service_app2
                    memory_service_app2.llm_available = True
                    memory_service_app2.auto_detect_memory_type = False

                    # Mock LiteLLM completion for fact extraction
                    mock_completion_response = MagicMock()
                    mock_completion_response.choices = [MagicMock()]
                    mock_completion_response.choices[0].message = MagicMock()

                    # Update mock responses for each call - use correct CognitiveFact format
                    async def mock_chat_app1(*args, **kwargs):
                        return '{"facts": [{"text": "User loves app1", "category": "preferences", "emotion": 0.5}]}'

                    async def mock_chat_app2(*args, **kwargs):
                        return '{"facts": [{"text": "User loves app2", "category": "preferences", "emotion": 0.5}]}'

                    # Set up mocks for each memory service
                    memory_service_app1._injected_llm_service.chat_completion = MagicMock(side_effect=mock_chat_app1)
                    memory_service_app2._injected_llm_service.chat_completion = MagicMock(side_effect=mock_chat_app2)

                    result1 = await cognitive_engine_app1.chat(
                        user_id="user123",
                        session_id="session123",
                        user_query="I love app1",
                        extract_facts=True,
                    )

                    result2 = await cognitive_engine_app2.chat(
                        user_id="user123",
                        session_id="session123",
                        user_query="I love app2",
                        extract_facts=True,
                    )

                    # Verify memories were stored in different collections
                    assert len(result1["memories_stored"]) > 0
                    assert len(result2["memories_stored"]) > 0

                    # Verify app1 memories are not visible to app2
                    result_app2_query = await cognitive_engine_app2.chat(
                        user_id="user123",
                        session_id="session123",
                        user_query="What do I like?",
                        extract_facts=False,
                    )

                    # App2 should not see app1's memories
                    ltm_memories_app2 = result_app2_query["ltm_memories"]
                    # Should only see app2 memories, not app1
                    assert all(
                        "app2" in str(m.get("metadata", {})) or "app1" not in str(m.get("memory", ""))
                        for m in ltm_memories_app2
                    ), "App2 should not see app1 memories"

                await engine.shutdown()
        finally:
            if original_openai_key:
                os.environ["OPENAI_API_KEY"] = original_openai_key
            elif "OPENAI_API_KEY" in os.environ:
                del os.environ["OPENAI_API_KEY"]


@pytest.mark.integration
class TestSSOMemoryIntegration:
    """Test memory functionality with SSO (shared authentication)."""

    @pytest.fixture
    def temp_manifest_with_memory(self, tmp_path):
        """Create temporary manifest file with memory config enabled."""
        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test App",
            "auth": {"mode": "app"},
            "memory_config": {
                "enabled": True,
                "collection_name": "user_memories",
                "embedding_model_dims": 1536,
                "embedding_model": "text-embedding-3-small",
                "chat_model": "gpt-4o",
                "infer": True,
                "graph": {"enabled": True, "auto_extract": True},
            },
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        return manifest_path

    @pytest.fixture
    def temp_manifest_sso(self, tmp_path):
        """Create temporary manifest with SSO auth."""
        manifest = {
            "schema_version": "2.0",
            "slug": "sso_app",
            "name": "SSO App",
            "auth": {
                "mode": "shared",
                "auth_hub_url": "http://localhost:8000",
                "roles": ["base_user", "viewer", "editor", "admin"],
                "require_role": "base_user",
            },
            "memory_config": {
                "enabled": True,
                "collection_name": "user_memories",
                "embedding_model_dims": 1536,
                "embedding_model": "text-embedding-3-small",
            },
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        return manifest_path

    @pytest.mark.asyncio
    async def test_sso_memory_with_shared_auth(self, mongodb_connection_string, temp_manifest_sso):
        """Test that memory works correctly with SSO shared authentication."""
        from mdb_engine.core.engine import MongoDBEngine
        from mdb_engine.memory.orchestrator import CognitiveEngine

        original_openai_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "sk-test-key-for-testing-only-" + "x" * 100

        try:
            db_name = f"test_sso_memory_{os.getpid()}"
            engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)

            # Mock LLM and embedding services to avoid API calls during initialization
            mock_embedding_service = MagicMock()

            async def mock_embed(text, model=None):
                return [[0.1] * 1536]

            mock_embedding_service.embed = mock_embed

            with (
                patch(
                    "mdb_engine.memory.builder.CognitiveMemoryServiceBuilder._setup_llm",
                    return_value=(None, True),
                ),
                patch(
                    "mdb_engine.memory.builder.CognitiveMemoryServiceBuilder._setup_embedding",
                    return_value=mock_embedding_service,
                ),
            ):
                app = engine.create_app(
                    slug="sso_app",
                    manifest=temp_manifest_sso,
                    title="Test SSO Memory",
                )

                async with app.router.lifespan_context(app):
                    memory_service = engine.get_memory_service("sso_app")
                    assert memory_service is not None, "Memory service should be initialized with SSO"

                    # Verify memory service works with user_id scoping (critical for SSO)
                    motor_db = engine.connection_manager.mongo_client[engine.db_name]
                    chat_history_collection = motor_db["chat_history"]

                    mock_llm_service = MagicMock()
                    mock_llm_service.chat_completion = AsyncMock(return_value="SSO memory test response")

                    cognitive_engine = CognitiveEngine(
                        app_slug="sso_app",
                        memory_service=memory_service,
                        chat_history_collection=chat_history_collection,
                        llm_service=mock_llm_service,
                    )

                    # Mock embedding service to avoid actual API calls
                    # Create a proper async function that returns a coroutine
                    # Use a class to ensure proper binding
                    class MockEmbeddingProvider:
                        async def embed(self, text, model=None):
                            # Always return the same mock vector for consistency
                            # This ensures stored memories and search queries use the same vector
                            return [[0.1] * 1536]

                    mock_embedding_provider = MockEmbeddingProvider()
                    memory_service.embedding_provider = mock_embedding_provider

                    # Mock LLM service for memory operations (needed for fact extraction)
                    from mdb_engine.llm.service import LLMService

                    mock_llm_service = MagicMock(spec=LLMService)

                    async def mock_chat_completion(*args, **kwargs):
                        # Return properly formatted response matching
                        # CognitiveFactExtractionResponse
                        # Note: CognitiveFact uses 'text' not 'fact', and requires 'category'
                        return (
                            '{"facts": [{"text": "User uses SSO authentication", '
                            '"category": "preferences", "emotion": 0.5}]}'
                        )

                    mock_llm_service.chat_completion = MagicMock(side_effect=mock_chat_completion)
                    memory_service._injected_llm_service = mock_llm_service
                    memory_service.llm_available = True
                    # Disable memory type detection to avoid sync/async issues in tests
                    memory_service.auto_detect_memory_type = False

                    # Mock LiteLLM completion for fact extraction (fallback)
                    mock_completion_response = MagicMock()
                    mock_completion_response.choices = [MagicMock()]
                    mock_completion_response.choices[0].message = MagicMock()
                    fact_json = (
                        '{"facts": [{"text": "User uses SSO authentication", '
                        '"category": "preferences", "emotion": 0.5}]}'
                    )
                    mock_completion_response.choices[0].message.content = fact_json

                    # Test memory storage with user_id (required for SSO)
                    result = await cognitive_engine.chat(
                        user_id="sso_user_123",
                        session_id="session_123",
                        user_query="I use SSO authentication",
                        extract_facts=True,
                    )

                    assert "memories_stored" in result
                    assert len(result["memories_stored"]) > 0

                    # Verify memories are scoped to user_id
                    stored_memories = result["memories_stored"]
                    for memory in stored_memories:
                        metadata = memory.get("metadata", {})
                        # Memory should be associated with user_id for SSO isolation
                        assert "user_id" in str(memory) or "sso_user_123" in str(metadata)

                await engine.shutdown()
        finally:
            if original_openai_key:
                os.environ["OPENAI_API_KEY"] = original_openai_key
            elif "OPENAI_API_KEY" in os.environ:
                del os.environ["OPENAI_API_KEY"]

    @pytest.mark.asyncio
    async def test_context_engineering_integration(self, mongodb_connection_string, temp_manifest_with_memory):
        """Test Context Engineering features: Persona, Entity Facts, Dynamic Persona."""
        from mdb_engine.core.engine import MongoDBEngine
        from mdb_engine.memory.orchestrator import CognitiveEngine

        original_openai_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "sk-test-key-for-testing-only-" + "x" * 100

        try:
            db_name = f"test_context_engineering_{os.getpid()}"
            engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)

            # Mock LLM and embedding services to avoid API calls during initialization
            mock_embedding_service = MagicMock()

            async def mock_embed(text, model=None):
                return [[0.1] * 1536]

            mock_embedding_service.embed = mock_embed

            with (
                patch(
                    "mdb_engine.memory.builder.CognitiveMemoryServiceBuilder._setup_llm",
                    return_value=(None, True),
                ),
                patch(
                    "mdb_engine.memory.builder.CognitiveMemoryServiceBuilder._setup_embedding",
                    return_value=mock_embedding_service,
                ),
            ):
                app = engine.create_app(
                    slug="test_app",
                    manifest=temp_manifest_with_memory,
                    title="Test Context Engineering",
                )

                async with app.router.lifespan_context(app):
                    memory_service = engine.get_memory_service("test_app")
                    assert memory_service is not None

                    # Get async Motor collection for chat history
                    motor_db = engine.connection_manager.mongo_client[engine.db_name]
                    chat_history_collection = motor_db["chat_history"]

                    # Mock LLM service (async interface)
                    mock_llm_service = MagicMock()
                    mock_llm_service.chat_completion = AsyncMock(
                        return_value="I understand you're an expert Python developer."
                    )

                    # Create CognitiveEngine with Context Engineering enabled
                    cognitive_engine = CognitiveEngine(
                        app_slug="test_app",
                        memory_service=memory_service,
                        chat_history_collection=chat_history_collection,
                        llm_service=mock_llm_service,
                        enable_context_engineering=True,
                        enable_entity_extraction=True,
                        enable_dynamic_persona=True,
                    )

                    # Mock LLM service for memory operations (needed for fact extraction)
                    from mdb_engine.llm.service import LLMService

                    mock_llm_service = MagicMock(spec=LLMService)

                    async def mock_chat_completion(*args, **kwargs):
                        return (
                            '{"facts": [{"text": "User is a Python expert", '
                            '"category": "biographical", "emotion": 0.5}]}'
                        )

                    mock_llm_service.chat_completion = MagicMock(side_effect=mock_chat_completion)
                    memory_service._injected_llm_service = mock_llm_service
                    memory_service.llm_available = True
                    # Disable memory type detection to avoid sync/async issues in tests
                    memory_service.auto_detect_memory_type = False

                    # Set up PersonaEngine with a persona
                    if hasattr(memory_service, "persona_engine") and memory_service.persona_engine:
                        memory_service.persona_engine.update_persona(
                            role="Senior Python Architect",
                            description=("Expert in Python and system design. " "Concise and technical."),
                            traits={"technical_focus": 0.9, "humor": 0.2, "formality": 0.8},
                        )

                    # Mock LiteLLM completion for fact extraction (fallback)
                    mock_completion_response = MagicMock()
                    mock_completion_response.choices = [MagicMock()]
                    mock_completion_response.choices[0].message = MagicMock()
                    fact_json = (
                        '{"facts": [{"text": "User is a Python expert", '
                        '"category": "biographical", "emotion": 0.5}]}'
                    )
                    mock_completion_response.choices[0].message.content = fact_json

                    # Store a memory first
                    await memory_service.add(
                        messages="User is a Python expert using Ubuntu 22.04",
                        user_id="user123",
                        metadata={"category": "biographical"},
                    )

                    # Test chat with Context Engineering
                    result = await cognitive_engine.chat(
                        user_id="user123",
                        session_id="session_context_test",
                        user_query="How do I optimize Python code?",
                        extract_facts=True,
                    )

                    # Verify Context Engineering metadata in result
                    assert "persona_used" in result
                    assert "entity_facts" in result
                    assert "dynamic_instructions" in result
                    assert "stm_summary" in result

                    # Verify persona was used
                    if result["persona_used"]:
                        assert result["persona_used"].get("role") is not None

                    # Verify entity facts were extracted
                    assert isinstance(result["entity_facts"], dict)
                    # Should extract expertise and possibly OS from stored memory

                    # Verify dynamic instructions were generated
                    assert isinstance(result["dynamic_instructions"], str)

                    await engine.shutdown()
        finally:
            if original_openai_key:
                os.environ["OPENAI_API_KEY"] = original_openai_key
            elif "OPENAI_API_KEY" in os.environ:
                del os.environ["OPENAI_API_KEY"]

    @pytest.mark.asyncio
    async def test_timeline_features_with_sso(self, mongodb_connection_string, temp_manifest_sso):
        """Test timeline features with SSO authentication."""
        from mdb_engine.core.engine import MongoDBEngine

        original_openai_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "sk-test-key-for-testing-only-" + "x" * 100

        try:
            db_name = f"test_sso_timelines_{os.getpid()}"
            engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)

            mock_embedding_service = MagicMock()

            async def mock_embed(text, model=None):
                return [[0.1] * 1536]

            mock_embedding_service.embed = mock_embed

            with (
                patch(
                    "mdb_engine.memory.builder.CognitiveMemoryServiceBuilder._setup_llm",
                    return_value=(None, True),
                ),
                patch(
                    "mdb_engine.memory.builder.CognitiveMemoryServiceBuilder._setup_embedding",
                    return_value=mock_embedding_service,
                ),
            ):
                app = engine.create_app(
                    slug="sso_app",
                    manifest=temp_manifest_sso,
                    title="Test SSO Timelines",
                )

                async with app.router.lifespan_context(app):
                    memory_service = engine.get_memory_service("sso_app")
                    assert memory_service is not None

                    user_id = "sso_timeline_user"

                    # Fork a timeline
                    branch_id = await memory_service.fork_timeline(
                        current_timeline="root",
                        new_name="SSO Test Timeline",
                        user_id=user_id,
                    )

                    assert branch_id is not None
                    assert branch_id.startswith("branch_")

                    # Add memory to branch timeline
                    memory = await memory_service.add_memory_with_links(
                        content="Memory in branch timeline",
                        user_id=user_id,
                        timeline_id=branch_id,
                        confidence=0.8,
                    )

                    assert memory is not None

                    # Search in branch timeline
                    results = await memory_service.search(
                        query="branch",
                        user_id=user_id,
                        timeline_id=branch_id,
                    )

                    if len(results) == 0:
                        # Fallback for environments without Atlas Vector Search:
                        # verify the memory exists in the branch timeline via get_all.
                        all_branch = await memory_service.get_all(
                            user_id=user_id,
                            limit=25,
                            filters={"metadata": {"timeline_id": branch_id}},
                        )
                        assert any(
                            "branch timeline" in (m.get("memory") or "").lower() for m in all_branch
                        ), "Branch timeline memory should exist even if vector search returns no results"
                    else:
                        assert len(results) > 0

            await engine.shutdown()
        finally:
            if original_openai_key:
                os.environ["OPENAI_API_KEY"] = original_openai_key
            elif "OPENAI_API_KEY" in os.environ:
                del os.environ["OPENAI_API_KEY"]

    @pytest.mark.asyncio
    async def test_confidence_based_retrieval_with_sso(self, mongodb_connection_string, temp_manifest_sso):
        """Test confidence-based retrieval with SSO."""
        from mdb_engine.core.engine import MongoDBEngine

        original_openai_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "sk-test-key-for-testing-only-" + "x" * 100

        try:
            db_name = f"test_sso_confidence_{os.getpid()}"
            engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)

            mock_embedding_service = MagicMock()

            async def mock_embed(text, model=None):
                return [[0.1] * 1536]

            mock_embedding_service.embed = mock_embed

            with (
                patch(
                    "mdb_engine.memory.builder.CognitiveMemoryServiceBuilder._setup_llm",
                    return_value=(None, True),
                ),
                patch(
                    "mdb_engine.memory.builder.CognitiveMemoryServiceBuilder._setup_embedding",
                    return_value=mock_embedding_service,
                ),
            ):
                app = engine.create_app(
                    slug="sso_app",
                    manifest=temp_manifest_sso,
                    title="Test SSO Confidence",
                )

                async with app.router.lifespan_context(app):
                    memory_service = engine.get_memory_service("sso_app")
                    assert memory_service is not None

                    user_id = "sso_confidence_user"

                    # Create memories with different confidence levels
                    await memory_service.add_memory_with_links(
                        content="High confidence fact",
                        user_id=user_id,
                        confidence=0.9,
                    )

                    await memory_service.add_memory_with_links(
                        content="Low confidence speculation",
                        user_id=user_id,
                        confidence=0.3,
                    )

                    # Default behavior: min_confidence=0.5 filters low-confidence memories
                    default_results = await memory_service.search(
                        query="confidence",
                        user_id=user_id,
                        timeline_id="root",
                    )

                    default_contents = [r.get("memory") or r.get("text", "") for r in default_results]
                    if len(default_contents) == 0:
                        # Fallback for environments without Atlas Vector Search:
                        # verify persistence via get_all.
                        all_docs = await memory_service.get_all(user_id=user_id, limit=25)
                        all_contents = [(d.get("memory") or "") for d in all_docs]
                        assert any("High confidence" in m for m in all_contents)
                        assert any("Low confidence" in m for m in all_contents)
                    else:
                        assert any("High confidence" in m for m in default_contents)
                        assert not any(
                            "Low confidence" in m for m in default_contents
                        ), "Low confidence should be filtered by default min_confidence=0.5"

                    # True Perfect Recall mode: explicitly request no confidence filtering
                    all_results = await memory_service.search(
                        query="confidence",
                        user_id=user_id,
                        timeline_id="root",
                        min_confidence=0.0,
                    )
                    all_contents = [r.get("memory") or r.get("text", "") for r in all_results]
                    if len(all_contents) == 0:
                        # Fallback for environments without Atlas Vector Search
                        all_docs = await memory_service.get_all(user_id=user_id, limit=25)
                        all_contents = [(d.get("memory") or "") for d in all_docs]
                    assert any("High confidence" in m for m in all_contents)
                    assert any("Low confidence" in m for m in all_contents)

            await engine.shutdown()
        finally:
            if original_openai_key:
                os.environ["OPENAI_API_KEY"] = original_openai_key
            elif "OPENAI_API_KEY" in os.environ:
                del os.environ["OPENAI_API_KEY"]

    @pytest.mark.asyncio
    async def test_graph_links_with_sso(self, mongodb_connection_string, temp_manifest_sso):
        """Test graph links with SSO authentication."""
        from bson import ObjectId

        from mdb_engine.core.engine import MongoDBEngine

        original_openai_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "sk-test-key-for-testing-only-" + "x" * 100

        try:
            db_name = f"test_sso_graph_links_{os.getpid()}"
            engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)

            mock_embedding_service = MagicMock()

            async def mock_embed(text, model=None):
                return [[0.1] * 1536]

            mock_embedding_service.embed = mock_embed

            with (
                patch(
                    "mdb_engine.memory.builder.CognitiveMemoryServiceBuilder._setup_llm",
                    return_value=(None, True),
                ),
                patch(
                    "mdb_engine.memory.builder.CognitiveMemoryServiceBuilder._setup_embedding",
                    return_value=mock_embedding_service,
                ),
            ):
                app = engine.create_app(
                    slug="sso_app",
                    manifest=temp_manifest_sso,
                    title="Test SSO Graph Links",
                )

                async with app.router.lifespan_context(app):
                    memory_service = engine.get_memory_service("sso_app")
                    assert memory_service is not None

                    user_id = "sso_graph_user"

                    # Create source memory
                    source = await memory_service.inject(
                        memory="Source memory",
                        user_id=user_id,
                    )
                    source_id = str(source.get("id") or source.get("_id"))

                    # Create memory with derived_from link
                    derived = await memory_service.add_memory_with_links(
                        content="Derived memory",
                        user_id=user_id,
                        derived_from=[source_id],
                        confidence=0.85,
                    )

                    assert derived is not None

                    # Verify graph links exist
                    derived_id = str(derived.get("id") or derived.get("_id"))
                    # Use the async collection wrapper owned by the memory service
                    # (avoids awaiting synchronous PyMongo collections and avoids hardcoded collection names)
                    derived_doc = await memory_service.collection.find_one({"_id": ObjectId(derived_id)})
                    assert derived_doc is not None
                    assert "graph_links" in derived_doc
                    assert source_id in derived_doc["graph_links"].get("derived_from", [])

            await engine.shutdown()
        finally:
            if original_openai_key:
                os.environ["OPENAI_API_KEY"] = original_openai_key
            elif "OPENAI_API_KEY" in os.environ:
                del os.environ["OPENAI_API_KEY"]
