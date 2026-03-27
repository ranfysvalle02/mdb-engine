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

    Implementation: LLMService

    Required for:
    - CognitiveEngine (chat generation, summarization)
    - GraphService (entity extraction)
    - CognitiveMemoryService (fact extraction, importance assessment)

    Standalone: Yes - only requires provider SDKs and API keys
    """

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        provider_name: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> str:
        """
        Generate a chat completion.

        Args:
            messages: List of message dicts with 'role' and 'content'
            provider_name: Named provider (e.g. "chat", "extraction").
                          Defaults to the service's default provider.
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
# Procedural Memory Protocol
# =============================================================================


@runtime_checkable
class ProceduralMemoryProtocol(Protocol):
    """
    Protocol for procedural memory services (skills, tools, workflows).

    Implementations: ProceduralMemory

    Required for:
    - Skill retrieval during CognitiveEngine.chat()
    - Storing executable procedures extracted from episodes
    - Tracking skill success rates for self-improvement

    Standalone: Yes - requires MongoDB collection and EmbeddingService
    """

    async def store_procedure(
        self,
        name: str,
        task_type: str,
        steps: list[str] | None = None,
        code_snippet: str | None = None,
        tool_schema: dict[str, Any] | None = None,
        success_rate: float = 1.0,
        metadata: dict[str, Any] | None = None,
        is_successful_procedure: bool = True,
    ) -> dict[str, Any]:
        """
        Store a procedural memory (skill/tool/workflow).

        Args:
            name: Name/identifier of the procedure
            task_type: Type of task (e.g., "deployment", "debugging")
            steps: List of step descriptions
            code_snippet: Executable code snippet
            tool_schema: JSON Schema for tool definition
            success_rate: Success rate (0.0 to 1.0)
            metadata: Optional metadata dictionary
            is_successful_procedure: Mark as successful procedure

        Returns:
            Created procedure document
        """
        ...

    async def search_procedures(
        self,
        task_description: str,
        task_type: str | None = None,
        min_success_rate: float = 0.7,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Search for procedures using vector similarity.

        Args:
            task_description: Description of the task
            task_type: Optional filter by task type
            min_success_rate: Minimum success rate threshold
            limit: Maximum number of results

        Returns:
            List of matching procedure documents
        """
        ...

    async def get_procedure(self, name: str) -> dict[str, Any] | None:
        """
        Retrieve a procedure by name.

        Args:
            name: Procedure name

        Returns:
            Procedure document or None if not found
        """
        ...

    async def mark_procedure_used(self, name: str, success: bool = True) -> None:
        """
        Mark a procedure as used and update success rate.

        Args:
            name: Procedure name
            success: Whether the usage was successful
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

    async def add(
        self,
        messages: str | list[dict[str, str]],
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """
        Add memories from messages with LLM fact extraction.

        Args:
            messages: Raw text or list of message dicts
            user_id: User ID for scoping (optional but recommended)
            metadata: Optional metadata
            **kwargs: Additional provider-specific arguments

        Returns:
            List of created memory objects with their IDs and metadata
        """
        ...

    async def inject(
        self,
        memory: str | dict[str, Any],
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Inject a memory directly without LLM extraction.

        Args:
            memory: Memory content as a string or dict
            user_id: User ID for scoping (optional but recommended)
            metadata: Optional metadata
            **kwargs: Additional provider-specific arguments

        Returns:
            Created memory object with ID and metadata
        """
        ...

    async def search(
        self,
        query: str,
        user_id: str | None = None,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """
        Search memories by semantic similarity.

        Args:
            query: Search query
            user_id: User ID for scoping (optional)
            limit: Maximum results
            filters: Optional metadata filters
            **kwargs: Additional provider-specific arguments

        Returns:
            List of matching memories
        """
        ...

    async def get_all(
        self,
        user_id: str | None = None,
        limit: int = 100,
        filters: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """
        Get all memories for a user.

        Args:
            user_id: User ID (optional)
            limit: Maximum results
            filters: Optional metadata filters
            **kwargs: Additional provider-specific arguments

        Returns:
            List of memories
        """
        ...

    async def delete(self, memory_id: str, user_id: str | None = None, **kwargs: Any) -> bool:
        """
        Delete a memory.

        Args:
            memory_id: Memory ID to delete
            user_id: Optional user ID for security scoping
            **kwargs: Additional provider-specific arguments

        Returns:
            True if deleted
        """
        ...


# =============================================================================
# Memory Strategy Protocols (re-exported for public API convenience)
# =============================================================================

from mdb_engine.memory.strategies import (  # noqa: E402
    DecayStrategy,
    ExtractionStrategy,
    ImportanceStrategy,
    PersonaStrategy,
    ReflectionStrategy,
    ScoringStrategy,
)

# =============================================================================
# Convenience type aliases
# =============================================================================

# For type hints that accept any of the service protocols
AnyLLMService = LLMServiceProtocol
AnyEmbeddingService = EmbeddingServiceProtocol
AnyGraphService = GraphServiceProtocol
AnyMemoryService = MemoryServiceProtocol
AnyProceduralMemory = ProceduralMemoryProtocol
AnyScoringStrategy = ScoringStrategy
AnyDecayStrategy = DecayStrategy
AnyExtractionStrategy = ExtractionStrategy
AnyImportanceStrategy = ImportanceStrategy
AnyPersonaStrategy = PersonaStrategy
AnyReflectionStrategy = ReflectionStrategy
