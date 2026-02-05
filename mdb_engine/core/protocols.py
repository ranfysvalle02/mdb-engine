"""
Service Protocols for MDB-Engine

This module defines Protocol classes (structural typing) for all major services.
These protocols enable:
1. Dependency injection without tight coupling
2. Easy mocking for unit tests
3. Type checking without import dependencies
4. Documentation of service contracts

Usage:
    from mdb_engine.core.protocols import LLMServiceProtocol

    def my_function(llm: LLMServiceProtocol) -> str:
        return await llm.chat_completion([{"role": "user", "content": "Hello"}])

    # Works with any object that has the right methods
    mock_llm = Mock(spec=LLMServiceProtocol)
    my_function(mock_llm)
"""

from typing import Any, Protocol, runtime_checkable

# =============================================================================
# LLM Service Protocol
# =============================================================================


@runtime_checkable
class LLMServiceProtocol(Protocol):
    """
    Protocol for LLM services.

    Implementations: LLMService, LLMProvider

    Required for:
    - GraphService (entity extraction)
    - CognitiveMemoryService (fact extraction, importance assessment)

    Standalone: Yes - only requires litellm package and API keys
    """

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> str:
        """
        Generate a chat completion.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Optional model override
            temperature: Optional temperature override
            **kwargs: Additional provider-specific options

        Returns:
            Generated text response
        """
        ...

    def chat_completion_sync(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> str:
        """
        Synchronous version of chat_completion.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Optional model override
            temperature: Optional temperature override
            **kwargs: Additional provider-specific options

        Returns:
            Generated text response
        """
        ...


# =============================================================================
# Embedding Service Protocol
# =============================================================================


@runtime_checkable
class EmbeddingServiceProtocol(Protocol):
    """
    Protocol for embedding services.

    Implementations: EmbeddingService, EmbeddingProvider

    Required for:
    - CognitiveMemoryService (vector search)
    - GraphService (hybrid search)

    Standalone: Yes - requires semantic-text-splitter and openai packages
    """

    async def embed(
        self,
        texts: str | list[str],
        model: str | None = None,
    ) -> list[list[float]]:
        """
        Generate embeddings for texts.

        Args:
            texts: Single text or list of texts to embed
            model: Optional model override

        Returns:
            List of embedding vectors
        """
        ...


@runtime_checkable
class TextChunkerProtocol(Protocol):
    """
    Protocol for text chunking services.

    Implementations: EmbeddingService (has chunk_text method)
    """

    async def chunk_text(
        self,
        text: str,
        max_tokens: int | None = None,
    ) -> list[str]:
        """
        Split text into semantic chunks.

        Args:
            text: Text to chunk
            max_tokens: Optional max tokens per chunk

        Returns:
            List of text chunks
        """
        ...


# =============================================================================
# Graph Service Protocol
# =============================================================================


@runtime_checkable
class GraphServiceProtocol(Protocol):
    """
    Protocol for graph services (knowledge graph).

    Implementations: GraphService

    Required for:
    - GraphRAG (graph-based retrieval augmented generation)
    - Entity relationship traversal
    - Hybrid search (vector + graph)

    Standalone: Yes - requires MongoDB collection
    Optional: LLMService (for extraction), EmbeddingService (for hybrid search)
    """

    @property
    def enabled(self) -> bool:
        """Whether the graph service is enabled."""
        ...

    def upsert_node(
        self,
        node_id: str,
        node_type: str,
        name: str,
        properties: dict[str, Any] | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Create or update a node.

        Args:
            node_id: Unique node ID (e.g., 'person:alex')
            node_type: Node type (e.g., 'person', 'interest')
            name: Display name
            properties: Optional properties dict
            user_id: Optional user ID for scoping

        Returns:
            Created/updated node document
        """
        ...

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """
        Get a node by ID.

        Args:
            node_id: Node ID

        Returns:
            Node document or None
        """
        ...

    def add_edge(
        self,
        source_id: str,
        relation: str,
        target_id: str,
        properties: dict[str, Any] | None = None,
        weight: float = 1.0,
    ) -> bool:
        """
        Add an edge between nodes.

        Args:
            source_id: Source node ID
            relation: Relationship type
            target_id: Target node ID
            properties: Optional edge properties
            weight: Edge weight (0.0-1.0)

        Returns:
            True if edge added successfully
        """
        ...

    def traverse(
        self,
        start_id: str,
        max_depth: int = 2,
        relation_filter: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Traverse the graph from a starting node.

        Args:
            start_id: Starting node ID
            max_depth: Maximum traversal depth
            relation_filter: Optional list of relations to follow

        Returns:
            List of traversal results with node and hop_distance
        """
        ...

    def get_stats(self) -> dict[str, Any]:
        """
        Get graph statistics.

        Returns:
            Dict with node counts, edge counts, etc.
        """
        ...


# =============================================================================
# Memory Service Protocol
# =============================================================================


@runtime_checkable
class MemoryServiceProtocol(Protocol):
    """
    Protocol for memory services.

    Implementations: CognitiveMemoryService, BaseMemoryService subclasses

    Required for:
    - Long-term memory storage and retrieval
    - Fact extraction and storage
    - Semantic search

    Standalone: Partial - requires MongoDB collection and EmbeddingService
    Optional: LLMService (for fact extraction), GraphService (for GraphRAG)
    """

    def add(
        self,
        messages: str | list[dict[str, Any]],
        user_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[str]:
        """
        Add memories from messages with LLM fact extraction.

        Args:
            messages: Raw text or list of message dicts
            user_id: User ID for scoping
            metadata: Optional metadata

        Returns:
            List of created memory IDs
        """
        ...

    def inject(
        self,
        memories: list[str] | list[dict[str, Any]],
        user_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[str]:
        """
        Inject memories directly without LLM extraction.

        Args:
            memories: List of memory strings or dicts
            user_id: User ID for scoping
            metadata: Optional metadata

        Returns:
            List of created memory IDs
        """
        ...

    def search(
        self,
        query: str,
        user_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Search memories by semantic similarity.

        Args:
            query: Search query
            user_id: User ID for scoping
            limit: Maximum results

        Returns:
            List of matching memories
        """
        ...

    def get_all(
        self,
        user_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Get all memories for a user.

        Args:
            user_id: User ID
            limit: Maximum results

        Returns:
            List of memories
        """
        ...

    def delete(self, memory_id: str, user_id: str | None = None) -> bool:
        """
        Delete a memory.

        Args:
            memory_id: Memory ID to delete
            user_id: Optional user ID for security scoping

        Returns:
            True if deleted
        """
        ...


# =============================================================================
# Convenience type aliases
# =============================================================================

# For type hints that accept any of the service protocols
AnyLLMService = LLMServiceProtocol
AnyEmbeddingService = EmbeddingServiceProtocol
AnyGraphService = GraphServiceProtocol
AnyMemoryService = MemoryServiceProtocol
