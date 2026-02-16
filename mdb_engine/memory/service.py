"""
Memory Service Factory

Factory function to create memory service instances.
"""

import logging
from typing import TYPE_CHECKING, Any

from .base import BaseMemoryService

if TYPE_CHECKING:
    from mdb_engine.database.scoped_wrapper import ScopedCollectionWrapper

logger = logging.getLogger(__name__)


def get_memory_service(
    app_slug: str,
    config: dict[str, Any] | None = None,
    provider: str = "cognitive",
    collection: "ScopedCollectionWrapper | None" = None,
    *,
    graph_service: Any = None,
    embedding_service: Any = None,
    llm_service: Any = None,
    scoring_strategy: Any = None,
    decay_strategy: Any = None,
    extraction_strategy: Any = None,
    importance_strategy: Any = None,
    persona_strategy: Any = None,
    reflection_strategy: Any = None,
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
        collection: ScopedCollectionWrapper instance (REQUIRED - must be from MDB-Engine connection manager)
                     collection.database returns ScopedMongoWrapper, ensuring all collections are automatically scoped
        graph_service: Optional GraphService instance for GraphRAG functionality.
                      If provided, memory service will use it for graph extraction.
                      Implements GraphServiceProtocol.
        embedding_service: Optional EmbeddingService instance for embeddings.
                          If provided, uses this instead of creating internally.
                          Implements EmbeddingServiceProtocol.
        llm_service: Optional LLMService instance for LLM operations.
                    If provided, uses this instead of creating internally.
                    Implements LLMServiceProtocol.
        scoring_strategy: Optional ScoringStrategy instance for custom ranking.
        decay_strategy: Optional DecayStrategy instance for memory lifecycle.
        extraction_strategy: Optional ExtractionStrategy instance for fact extraction.
        importance_strategy: Optional ImportanceStrategy instance for importance assessment.
        persona_strategy: Optional PersonaStrategy instance for persona blending.
        reflection_strategy: Optional ReflectionStrategy instance for consolidation triggers.

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

        # With custom strategies
        from mdb_engine.memory.strategies import RecencyDecayScoring, ExponentialDecay

        memory = get_memory_service(
            app_slug="my_app",
            collection=collection,
            scoring_strategy=RecencyDecayScoring(half_life_hours=72),
            decay_strategy=ExponentialDecay(half_life_hours=168),
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
            "Pass a ScopedCollectionWrapper obtained from MDB-Engine's connection manager. "
            "collection.database returns ScopedMongoWrapper, ensuring all collections are automatically scoped."
        )

    if provider == "cognitive":
        from .builder import CognitiveMemoryServiceBuilder

        return CognitiveMemoryServiceBuilder(
            app_slug=app_slug,
            config=config,
            collection=collection,
            graph_service=graph_service,
            embedding_service=embedding_service,
            llm_service=llm_service,
            scoring_strategy=scoring_strategy,
            decay_strategy=decay_strategy,
            extraction_strategy=extraction_strategy,
            importance_strategy=importance_strategy,
            persona_strategy=persona_strategy,
            reflection_strategy=reflection_strategy,
        ).build()
    else:
        raise ValueError(
            f"Unsupported memory provider: {provider}. "
            f"Supported providers: cognitive. "
            f"Future providers can be added by implementing BaseMemoryService."
        )
