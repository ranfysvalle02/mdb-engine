"""
Memory Service Factory

Factory function to create memory service instances.
"""

import logging
from typing import Any

from .base import BaseMemoryService

logger = logging.getLogger(__name__)


def get_memory_service(
    app_slug: str,
    config: dict[str, Any] | None = None,
    provider: str = "cognitive",
    collection: Any = None,
    *,
    graph_service: Any = None,
    embedding_service: Any = None,
    llm_service: Any = None,
) -> BaseMemoryService:
    """
    Factory function to create a memory service instance.

    Supports dependency injection for modular, testable architectures.
    When services are not injected, CognitiveMemoryService creates them internally.

    Args:
        app_slug: Application slug for scoping (required)
        config: Memory service configuration dictionary
        provider: Memory provider to use (default: "cognitive")
                     - "cognitive": CognitiveMemoryService (default, customizable)
                     - "custom": Alias for cognitive (backwards compatibility)
        collection: PyMongo Collection instance (REQUIRED - must be from MDB-Engine connection pool)
        graph_service: Optional GraphService instance for GraphRAG functionality.
                      If provided, memory service will use it for graph extraction.
                      Implements GraphServiceProtocol.
        embedding_service: Optional EmbeddingService instance for embeddings.
                          If provided, uses this instead of creating internally.
                          Implements EmbeddingServiceProtocol.
        llm_service: Optional LLMService instance for LLM operations.
                    If provided, uses this instead of creating internally.
                    Implements LLMServiceProtocol.

    Returns:
        BaseMemoryService instance (CognitiveMemoryService)

    Raises:
        ValueError: If provider is not supported or required parameters are missing

    Example:
        # With dependency injection (for testing or modular setup)
        from mdb_engine.llm import LLMService
        from mdb_engine.embeddings import EmbeddingProvider

        llm = LLMService(config={"default_model": "gpt-4o"})
        embeddings = EmbeddingProvider()

        memory = get_memory_service(
            app_slug="my_app",
            collection=collection,
            llm_service=llm,
            embedding_service=embeddings,
        )

        # Without injection (services created internally - default behavior)
        memory = get_memory_service(
            app_slug="my_app",
            collection=collection,
        )
    """
    if collection is None:
        raise ValueError(
            "Collection is REQUIRED. Memory service must use MDB-Engine's connection pool. "
            "Pass a PyMongo Collection instance obtained from MDB-Engine's connection manager."
        )

    # Both "custom" and "cognitive" use CognitiveMemoryService
    # "custom" is kept for backwards compatibility
    if provider in ["custom", "cognitive"]:
        from .cognitive import CognitiveMemoryService

        return CognitiveMemoryService(
            app_slug=app_slug,
            config=config,
            collection=collection,
            graph_service=graph_service,
            embedding_service=embedding_service,
            llm_service=llm_service,
        )
    else:
        raise ValueError(
            f"Unsupported memory provider: {provider}. "
            f"Supported providers: cognitive (or 'custom' for backwards compatibility). "
            f"Future providers can be added by implementing BaseMemoryService."
        )
