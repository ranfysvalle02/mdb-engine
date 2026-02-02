"""
Base Memory Service Interface

Abstract base class for memory service implementations.
This allows for extensibility with different memory providers (Mem0, LangChain, custom, etc.)
while maintaining a consistent API.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from abc import ABC, abstractmethod
from typing import Any


class MemoryServiceError(Exception):
    """Base exception for all memory service errors."""

    pass


class BaseMemoryService(ABC):
    """
    Abstract base class for memory service implementations.

    This class defines the interface that all memory service implementations must follow.
    Concrete implementations (e.g., Mem0MemoryService) inherit from this class and
    implement the abstract methods.

    All memory operations are scoped per user_id for safety and data isolation.
    """

    @abstractmethod
    def add(
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
            bucket_id: Bucket ID for organizing memories
            bucket_type: Type of bucket (e.g., "general", "file", "conversation")
            raw_content: Raw content to store alongside extracted facts
            **kwargs: Additional provider-specific arguments

        Returns:
            List of created memory objects with their IDs and metadata
        """
        pass

    @abstractmethod
    def inject(
        self,
        memory: str | dict[str, Any],
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
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
            **kwargs: Additional provider-specific arguments

        Returns:
            Created memory object with ID and metadata

        Raises:
            MemoryServiceError: If injection operation fails
            ValueError: If memory content is invalid or empty
        """
        pass

    @abstractmethod
    def get_all(
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
    def search(
        self,
        query: str,
        user_id: str | None = None,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
        **kwargs,
    ) -> list[dict[str, Any]]:
        """
        Perform semantic search across memories.

        Args:
            query: Search query string
            user_id: User ID to scope search (optional)
            limit: Maximum number of results to return
            filters: Additional metadata filters to apply
            **kwargs: Additional provider-specific arguments

        Returns:
            List of memory objects matching the query, ordered by relevance
        """
        pass

    @abstractmethod
    def get(
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
    def delete(
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
    def delete_all(
        self,
        user_id: str | None = None,
        **kwargs,
    ) -> bool:
        """
        Delete all memories for a user.

        Args:
            user_id: User ID whose memories should be deleted (optional)
            **kwargs: Additional provider-specific arguments

        Returns:
            True if deletion was successful, False otherwise
        """
        pass

    @abstractmethod
    def update(
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
