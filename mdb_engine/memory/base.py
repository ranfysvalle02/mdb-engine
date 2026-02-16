"""
Base Memory Service Interface

Abstract base class for memory service implementations.
This allows for extensibility with different memory providers (custom, cognitive, LangChain, etc.)
while maintaining a consistent API.

This module is part of MDB_ENGINE - MongoDB Engine.

Error Handling Convention
-------------------------
All memory service methods follow these rules:

1. **Raise** for invalid inputs and programming errors:
   - Missing required parameters, invalid types, empty content.
   - Use ``ValueError`` / ``InputValidationError`` for caller mistakes.
   - Use ``MemoryServiceError`` (or subclass) for internal failures that
     the caller should handle (e.g. LLM unavailable, DB connection lost).

2. **Return ``[]``** for search / query methods with no results or recoverable
   failures (e.g. embedding generation fails during ``search``).
   Callers can always iterate over the result without None-checking.

3. **Return ``None``** for single-item lookups that found nothing
   (e.g. ``get()`` with an unknown ID, ``detect_knowledge_conflict``
   finding no conflict).

4. **Return ``False``** for boolean operations that did not succeed
   (e.g. ``delete`` when the memory was not found).

Every public method should document its error behaviour in the docstring.
"""

from abc import ABC, abstractmethod
from typing import Any

from ..exceptions import MongoDBEngineError


class MemoryServiceError(MongoDBEngineError):
    """Base exception for all memory service errors."""

    pass


class BaseMemoryService(ABC):
    """
    Abstract base class for memory service implementations.

    This class defines the interface that all memory service implementations must follow.
    Concrete implementations (e.g., CognitiveMemoryService) "
    "inherit from this class and implement the abstract methods.

    All memory operations are scoped per user_id for safety and data isolation.
    """

    @abstractmethod
    async def add(
        self,
        messages: str | list[dict[str, str]],
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        bucket_id: str | None = None,
        bucket_type: str | None = None,
        raw_content: str | None = None,
        **kwargs,
    ) -> list[dict[str, Any]]:
        """
        Add memories with user scoping and metadata convenience.

        Args:
            messages: Memory content as a string or list of message dicts
            user_id: User ID for scoping (optional but recommended)
            metadata: Additional metadata to store with the memory
            bucket_id: Bucket ID for organizing memories (optional, stored in metadata if provided)
            bucket_type: Type of bucket (optional, e.g., "general", "file", "conversation")
            raw_content: Raw content to store alongside extracted facts (optional)
            **kwargs: Additional provider-specific arguments

        Note:
            bucket_id and bucket_type are OPTIONAL. Memories work perfectly fine without them.
            They are only added to metadata if provided.

        Returns:
            List of created memory objects with their IDs and metadata
        """
        pass

    @abstractmethod
    async def inject(
        self,
        memory: str | dict[str, Any],
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        bucket_id: str | None = None,
        bucket_type: str | None = None,
        raw_content: str | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """
        Manually inject a memory without LLM inference.

        This method allows direct insertion of memories without going through
        the inference pipeline. Useful for manually adding facts, preferences,
        or other structured data.

        Args:
            memory: Memory content as a string or dict with memory/text/content key
            user_id: User ID for scoping (optional but recommended)
            metadata: Additional metadata to store with the memory
            bucket_id: Bucket ID for organizing memories (optional, stored in metadata if provided)
            bucket_type: Type of bucket (optional, e.g., "general", "file", "conversation")
            raw_content: Raw content to store alongside the memory (optional)
            **kwargs: Additional provider-specific arguments

        Note:
            bucket_id and bucket_type are OPTIONAL. Memories work perfectly fine without them.
            They are only added to metadata if provided.

        Returns:
            Created memory object with ID and metadata

        Raises:
            MemoryServiceError: If injection operation fails
            ValueError: If memory content is invalid or empty
        """
        pass

    @abstractmethod
    async def get_all(
        self,
        user_id: str | None = None,
        limit: int = 100,
        filters: dict[str, Any] | None = None,
        **kwargs,
    ) -> list[dict[str, Any]]:
        """
        Get all memories with optional filtering.

        Args:
            user_id: User ID to filter memories (optional)
            limit: Maximum number of memories to return
            filters: Additional filters to apply (provider-specific)
            **kwargs: Additional provider-specific arguments

        Returns:
            List of memory objects
        """
        pass

    @abstractmethod
    async def search(
        self,
        query: str,
        user_id: str | None = None,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
        **kwargs,
    ) -> list[dict[str, Any]]:
        """
        Perform semantic search across memories using MongoDB Atlas Vector Search.

        Args:
            query: Search query string
            user_id: User ID to scope search (optional)
            limit: Maximum number of results to return
            filters: Filter structure for metadata filtering
                     Example: {"metadata": {"bucket_id": "conversation:123"}}
            **kwargs: Additional provider-specific arguments

        Returns:
            List of memory objects matching the query, ordered by relevance
        """
        pass

    @abstractmethod
    async def get(
        self,
        memory_id: str,
        user_id: str | None = None,
        **kwargs,
    ) -> dict[str, Any] | None:
        """
        Get a single memory by ID.

        Args:
            memory_id: Unique identifier for the memory
            user_id: User ID for security scoping (optional)
            **kwargs: Additional provider-specific arguments

        Returns:
            Memory object if found, None otherwise
        """
        pass

    @abstractmethod
    async def delete(
        self,
        memory_id: str,
        user_id: str | None = None,
        **kwargs,
    ) -> bool:
        """
        Delete a single memory by ID.

        Args:
            memory_id: Unique identifier for the memory to delete
            user_id: User ID for security scoping (optional)
            **kwargs: Additional provider-specific arguments

        Returns:
            True if deletion was successful, False otherwise
        """
        pass

    @abstractmethod
    async def delete_all(
        self,
        user_id: str | None = None,
        hard_delete: bool = ...,  # Ellipsis indicates required parameter
        **kwargs,
    ) -> bool:
        """
        Delete all memories for a user.

        Args:
            user_id: User ID whose memories should be deleted (optional)
            hard_delete: REQUIRED - If True, permanently remove all memories.
                         If False, soft-delete by marking as deleted (for legal retention).
                         Must be explicitly specified - no default for safety.
            **kwargs: Additional provider-specific arguments

        Returns:
            True if deletion was successful, False otherwise
        """
        pass

    @abstractmethod
    async def update(
        self,
        memory_id: str,
        user_id: str | None = None,
        memory: str | None = None,
        data: str | dict[str, Any] | None = None,
        messages: str | list[dict[str, str]] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs,
    ) -> dict[str, Any] | None:
        """
        Update an existing memory's content and/or metadata.

        Args:
            memory_id: Unique identifier for the memory to update (required)
            user_id: User ID for security scoping (optional)
            memory: New memory content as a string (optional)
            data: Alternative parameter for content (string or dict) (optional)
            messages: Alternative way to provide content as messages (optional)
            metadata: Metadata updates (optional)
            **kwargs: Additional provider-specific arguments

        Returns:
            Updated memory object if successful, None if memory not found

        Raises:
            MemoryServiceError: If update operation fails
            ValueError: If memory_id is invalid or empty
        """
        pass

    # --- Cognitive Memory Methods (Optional) ---
    # These methods are part of the cognitive memory architecture.
    # Implementations that don't support cognitive features can raise NotImplementedError.

    async def restore_from_cold_storage(
        self,
        memory_id: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        """
        Restore a memory from cold storage to active status.

        Args:
            memory_id: Memory ID to restore
            user_id: User ID for security scoping

        Returns:
            Restored memory document, or None if not found
        """
        raise NotImplementedError("Cognitive memory features not supported by this provider")

    async def get_memory_analytics(
        self,
        user_id: str,
    ) -> dict[str, Any]:
        """
        Get analytics about a user's memory health.

        Returns metrics useful for understanding memory usage (Perfect Recall mode).

        Args:
            user_id: User ID to analyze

        Returns:
            Analytics dictionary with various metrics
        """
        raise NotImplementedError("Cognitive memory features not supported by this provider")

    async def detect_knowledge_conflict(
        self,
        user_id: str,
        new_fact: str,
        similarity_threshold: float = 0.85,
        llm_model: str | None = None,
    ) -> str | None:
        """
        Check if new information conflicts with existing knowledge.

        This implements the "Integrity Layer" that prevents the AI from developing
        "digital dementia" - holding contradictory facts as equally true.

        Args:
            user_id: User ID to check conflicts for
            new_fact: The new fact/information to check
            similarity_threshold: Minimum similarity to consider related
            llm_model: Override LLM model for conflict detection

        Returns:
            Conflict description if found, None if no conflict
        """
        raise NotImplementedError("Cognitive memory features not supported by this provider")
