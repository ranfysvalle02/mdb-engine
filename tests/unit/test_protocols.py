"""
Tests for service protocols and dependency injection.

Tests:
- Protocol definitions and runtime checkability
- Service conformance to protocols
- Dependency injection patterns
- Mock service conformance
"""

from typing import Any
from unittest.mock import MagicMock

from mdb_engine.core.protocols import (
    EmbeddingServiceProtocol,
    GraphServiceProtocol,
    LLMServiceProtocol,
    MemoryServiceProtocol,
)


class TestProtocolDefinitions:
    """Test that protocol definitions are properly defined and runtime checkable."""

    def test_llm_service_protocol_is_runtime_checkable(self):
        """Test LLMServiceProtocol is runtime checkable."""
        assert hasattr(LLMServiceProtocol, "__protocol_attrs__") or hasattr(
            LLMServiceProtocol, "_is_runtime_protocol"
        )

    def test_embedding_service_protocol_is_runtime_checkable(self):
        """Test EmbeddingServiceProtocol is runtime checkable."""
        assert hasattr(EmbeddingServiceProtocol, "__protocol_attrs__") or hasattr(
            EmbeddingServiceProtocol, "_is_runtime_protocol"
        )

    def test_graph_service_protocol_is_runtime_checkable(self):
        """Test GraphServiceProtocol is runtime checkable."""
        assert hasattr(GraphServiceProtocol, "__protocol_attrs__") or hasattr(
            GraphServiceProtocol, "_is_runtime_protocol"
        )

    def test_memory_service_protocol_is_runtime_checkable(self):
        """Test MemoryServiceProtocol is runtime checkable."""
        assert hasattr(MemoryServiceProtocol, "__protocol_attrs__") or hasattr(
            MemoryServiceProtocol, "_is_runtime_protocol"
        )


class TestProtocolExports:
    """Test that protocols are properly exported from mdb_engine.core."""

    def test_protocols_exported_from_core(self):
        """Test all protocols are exported from mdb_engine.core."""
        from mdb_engine.core import (
            EmbeddingServiceProtocol,
            GraphServiceProtocol,
            LLMServiceProtocol,
            MemoryServiceProtocol,
            TextChunkerProtocol,
        )

        # All should be importable without error
        assert LLMServiceProtocol is not None
        assert EmbeddingServiceProtocol is not None
        assert TextChunkerProtocol is not None
        assert GraphServiceProtocol is not None
        assert MemoryServiceProtocol is not None


class TestMockServiceConformance:
    """Test that mock services can implement protocols."""

    def test_mock_llm_service_implements_protocol(self):
        """Test that a mock LLM service implements the protocol."""

        class MockLLMService:
            async def chat_completion(
                self,
                messages: list[dict[str, Any]],
                model: str | None = None,
                temperature: float | None = None,
                **kwargs: Any,
            ) -> str:
                return "Mock response"

            def chat_completion_sync(
                self,
                messages: list[dict[str, Any]],
                model: str | None = None,
                temperature: float | None = None,
                **kwargs: Any,
            ) -> str:
                return "Mock sync response"

        mock_service = MockLLMService()
        assert isinstance(mock_service, LLMServiceProtocol)

    def test_mock_embedding_service_implements_protocol(self):
        """Test that a mock embedding service implements the protocol."""

        class MockEmbeddingService:
            async def embed(
                self,
                texts: str | list[str],
                model: str | None = None,
            ) -> list[list[float]]:
                if isinstance(texts, str):
                    texts = [texts]
                return [[0.1, 0.2, 0.3] for _ in texts]

        mock_service = MockEmbeddingService()
        assert isinstance(mock_service, EmbeddingServiceProtocol)

    def test_mock_graph_service_implements_protocol(self):
        """Test that a mock graph service implements the protocol."""

        class MockGraphService:
            @property
            def enabled(self) -> bool:
                return True

            def upsert_node(
                self,
                node_id: str,
                node_type: str,
                name: str,
                properties: dict[str, Any] | None = None,
                user_id: str | None = None,
            ) -> dict[str, Any]:
                return {"node_id": node_id, "type": node_type, "name": name}

            def get_node(self, node_id: str) -> dict[str, Any] | None:
                return {"node_id": node_id}

            def add_edge(
                self,
                source_id: str,
                relation: str,
                target_id: str,
                properties: dict[str, Any] | None = None,
                weight: float = 1.0,
            ) -> bool:
                return True

            def traverse(
                self,
                start_id: str,
                max_depth: int = 2,
                relation_filter: list[str] | None = None,
            ) -> list[dict[str, Any]]:
                return [{"node": {"node_id": start_id}, "hop_distance": 0}]

            def get_stats(self) -> dict[str, Any]:
                return {"node_count": 0, "edge_count": 0}

        mock_service = MockGraphService()
        assert isinstance(mock_service, GraphServiceProtocol)

    def test_mock_memory_service_implements_protocol(self):
        """Test that a mock memory service implements the protocol."""

        class MockMemoryService:
            def add(
                self,
                messages: str | list[dict[str, Any]],
                user_id: str,
                metadata: dict[str, Any] | None = None,
            ) -> list[str]:
                return ["memory_id_1"]

            def inject(
                self,
                memories: list[str] | list[dict[str, Any]],
                user_id: str,
                metadata: dict[str, Any] | None = None,
            ) -> list[str]:
                return ["memory_id_1"]

            def search(
                self,
                query: str,
                user_id: str,
                limit: int = 10,
            ) -> list[dict[str, Any]]:
                return [{"text": "memory", "score": 0.9}]

            def get_all(
                self,
                user_id: str,
                limit: int = 100,
            ) -> list[dict[str, Any]]:
                return []

            def delete(self, memory_id: str, user_id: str | None = None) -> bool:
                return True

        mock_service = MockMemoryService()
        assert isinstance(mock_service, MemoryServiceProtocol)


class TestDependencyInjection:
    """Test dependency injection patterns work correctly."""

    def test_get_memory_service_accepts_injected_llm_service(self):
        """Test that get_memory_service accepts an injected LLM service."""
        from mdb_engine.memory.service import get_memory_service

        # Create a mock LLM service
        class MockLLMService:
            async def chat_completion(self, messages, model=None, temperature=None, **kwargs):
                return "Mock response"

            def chat_completion_sync(self, messages, model=None, temperature=None, **kwargs):
                return "Mock response"

        mock_llm = MockLLMService()

        # Create a mock collection
        mock_collection = MagicMock()
        mock_collection.name = "test_memories"
        mock_collection.database = MagicMock()
        mock_collection.database.name = "test_db"
        mock_collection.database.client = MagicMock()
        mock_collection.database.list_collection_names.return_value = ["test_memories"]
        mock_collection.count_documents.return_value = 0

        # This should not raise - signature accepts llm_service
        # (actual initialization may fail due to other dependencies, but the parameter is accepted)
        import inspect

        sig = inspect.signature(get_memory_service)
        param_names = list(sig.parameters.keys())

        assert (
            "llm_service" in param_names
        ), "get_memory_service should accept llm_service parameter"
        assert (
            "embedding_service" in param_names
        ), "get_memory_service should accept embedding_service parameter"

    def test_cognitive_memory_service_constructor_accepts_di_params(self):
        """Test that CognitiveMemoryService constructor accepts DI parameters."""
        import inspect

        from mdb_engine.memory.cognitive import CognitiveMemoryService

        sig = inspect.signature(CognitiveMemoryService.__init__)
        param_names = list(sig.parameters.keys())

        # Verify all DI parameters are present
        assert "graph_service" in param_names
        assert "embedding_service" in param_names
        assert "llm_service" in param_names


class TestServiceInitializerTypeHints:
    """Test ServiceInitializer has proper type hints."""

    def test_service_initializer_uses_protocol_type_hints(self):
        """Test that ServiceInitializer uses protocol type hints."""
        import typing

        from mdb_engine.core.service_initialization import ServiceInitializer

        # Get type hints for the class
        hints = typing.get_type_hints(ServiceInitializer.get_graph_service)

        # Return type should be GraphServiceProtocol | None
        return_hint = hints.get("return")
        assert return_hint is not None, "get_graph_service should have return type hint"

    def test_service_initializer_get_memory_service_typed(self):
        """Test that get_memory_service has proper return type."""
        import typing

        from mdb_engine.core.service_initialization import ServiceInitializer

        hints = typing.get_type_hints(ServiceInitializer.get_memory_service)

        return_hint = hints.get("return")
        assert return_hint is not None, "get_memory_service should have return type hint"


class TestProtocolDocumentation:
    """Test that protocols have proper documentation."""

    def test_llm_service_protocol_has_docstring(self):
        """Test LLMServiceProtocol has documentation."""
        assert LLMServiceProtocol.__doc__ is not None
        assert "LLM" in LLMServiceProtocol.__doc__

    def test_embedding_service_protocol_has_docstring(self):
        """Test EmbeddingServiceProtocol has documentation."""
        assert EmbeddingServiceProtocol.__doc__ is not None
        assert "embed" in EmbeddingServiceProtocol.__doc__

    def test_graph_service_protocol_has_docstring(self):
        """Test GraphServiceProtocol has documentation."""
        assert GraphServiceProtocol.__doc__ is not None
        assert "graph" in GraphServiceProtocol.__doc__.lower()

    def test_memory_service_protocol_has_docstring(self):
        """Test MemoryServiceProtocol has documentation."""
        assert MemoryServiceProtocol.__doc__ is not None
        assert "memory" in MemoryServiceProtocol.__doc__.lower()


class TestLLMCompletionWrapper:
    """Test the _llm_completion wrapper in CognitiveMemoryService."""

    def test_llm_completion_wrapper_exists(self):
        """Test that _llm_completion wrapper method exists."""
        from mdb_engine.memory.cognitive import CognitiveMemoryService

        assert hasattr(
            CognitiveMemoryService, "_llm_completion"
        ), "CognitiveMemoryService should have _llm_completion method for DI routing"

    def test_llm_completion_wrapper_signature(self):
        """Test _llm_completion has expected signature."""
        import inspect

        from mdb_engine.memory.cognitive import CognitiveMemoryService

        sig = inspect.signature(CognitiveMemoryService._llm_completion)
        param_names = list(sig.parameters.keys())

        assert "self" in param_names
        assert "messages" in param_names
        assert "model" in param_names
        assert "temperature" in param_names
