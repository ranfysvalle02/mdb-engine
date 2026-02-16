"""GraphService — Knowledge Graph Service with $graphLookup Traversal.

Composed from mixins:
    - NodeOperationsMixin   (nodes.py)
    - EdgeOperationsMixin   (edges.py)
    - TraversalMixin        (traversal.py)
    - SearchMixin           (search.py)
    - ExtractionMixin       (extraction.py)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .base import BaseGraphService, GraphServiceError
from .edges import EdgeOperationsMixin
from .extraction import ExtractionMixin
from .nodes import NodeOperationsMixin
from .search import SearchMixin
from .traversal import TraversalMixin

if TYPE_CHECKING:
    from ..database.scoped_wrapper import ScopedCollectionWrapper
    from ..embeddings.service import EmbeddingService
    from ..llm.service import LLMService

logger = logging.getLogger(__name__)

# PyMongo imports
try:
    from pymongo import ASCENDING
    from pymongo.errors import OperationFailure, PyMongoError
except ImportError:
    raise ImportError("pip install pymongo") from None


# ============================================================================
# GraphService Class
# ============================================================================


class GraphService(
    NodeOperationsMixin,
    EdgeOperationsMixin,
    TraversalMixin,
    SearchMixin,
    ExtractionMixin,
    BaseGraphService,
):
    """
    Knowledge Graph Service with MongoDB $graphLookup traversal.

    Provides:
    - Node CRUD operations
    - Edge management with weights and temporal flags
    - Multi-hop graph traversal using $graphLookup
    - Hybrid search (vector + graph)
    - LLM-powered entity extraction

    Configuration:
        enabled: bool - Enable graph service (default: False)
        collection_name: str - Collection name (default: "kg")
        auto_extract: bool - Auto-extract from text (default: True)
        llm_model: str - LLM for extraction (default: from llm_service)
        default_max_depth: int - Default traversal depth (default: 2)
        vector_index_name: str - Name of vector index (default: "graph_vector_index")
        node_types: list - Allowed node types
    """

    # Default node types
    DEFAULT_NODE_TYPES = [
        "person",
        "interest",
        "event",
        "location",
        "organization",
        "product",
        "concept",
    ]

    def __init__(
        self,
        app_slug: str,
        collection: ScopedCollectionWrapper,
        config: dict[str, Any] | None = None,
        llm_service: LLMService | None = None,
        embedding_service: EmbeddingService | None = None,
    ):
        """
        Initialize GraphService.

        Args:
            app_slug: Application slug for scoping
            collection: Motor AsyncIOMotorCollection for graph nodes
                (REQUIRED - must be from MDB-Engine connection manager)
            config: Configuration dictionary
            llm_service: LLMService instance for entity extraction
            embedding_service: EmbeddingService instance for hybrid search embeddings

        Raises:
            GraphServiceError: If collection is None
        """
        if collection is None:
            raise GraphServiceError(
                "Collection is REQUIRED. GraphService must use MDB-Engine's connection pool. "
                "Pass a Motor AsyncIOMotorCollection obtained from MDB-Engine's connection manager."
            )

        self._app_slug = app_slug
        self.collection = collection
        self.config = config or {}
        self.llm_service = llm_service
        self.embedding_service = embedding_service

        # Configuration
        # Graph service is enabled by default (opt-out via manifest.json)
        self._enabled = self.config.get("enabled", True)
        self.auto_extract = self.config.get("auto_extract", True)
        self.llm_model = self.config.get("llm_model")  # None means use llm_service default
        self.temperature = self.config.get("temperature", 0.0)
        self.default_max_depth = self.config.get("default_max_depth", 2)
        self.vector_index_name = self.config.get("vector_index_name", "graph_vector_index")
        self.node_types = self.config.get("node_types", self.DEFAULT_NODE_TYPES)

        # OSI integration (optional) -- Tier 1 via osi_models_path or Tier 2 via _osi_registry
        self._osi_models: list[dict] = []
        self._osi_prompt_context: str | None = None
        self._osi_discovery_enabled: bool = False

        # Check for injected OSI registry (Tier 2, from service_initialization)
        osi_registry = self.config.get("_osi_registry")
        if osi_registry is not None:
            self._osi_prompt_context = osi_registry.get_prompt_context()
            osi_types = osi_registry.get_node_types()
            if osi_types:
                self.node_types = list(dict.fromkeys(self.node_types + osi_types))
            self._osi_discovery_enabled = True
            logger.info(
                f"OSI integration (registry): loaded prompt context, " f"{len(osi_types)} additional node types"
            )
        else:
            # Tier 1 fallback: load from osi_models_path config
            osi_models_path = self.config.get("osi_models_path")
            if osi_models_path:
                try:
                    from .osi_loader import (
                        extract_node_types_from_osi,
                        format_osi_for_prompt,
                        load_osi_models,
                    )

                    self._osi_models = load_osi_models(osi_models_path)
                    if self._osi_models:
                        self._osi_prompt_context = format_osi_for_prompt(self._osi_models)
                        osi_types = extract_node_types_from_osi(self._osi_models)
                        if osi_types:
                            self.node_types = list(dict.fromkeys(self.node_types + osi_types))
                        self._osi_discovery_enabled = True
                        logger.info(
                            f"OSI integration (Tier 1): loaded {len(self._osi_models)} models, "
                            f"{len(osi_types)} additional node types"
                        )
                except ImportError:
                    logger.debug("OSI loader not available; PyYAML may not be installed")

        # Indexes are created lazily on first async call via _ensure_ready()
        self._indexes_created = False

        if self._enabled:
            logger.info(
                f"GraphService initialized: app_slug={app_slug}, "
                f"auto_extract={self.auto_extract}, max_depth={self.default_max_depth}"
            )
        else:
            logger.info("GraphService initialized but DISABLED")

        # --- Apply shared resilience to key async methods ---
        try:
            from ..core.resilience import (
                circuit_breaker_from_config,
                policy_from_config,
                resilient,
            )

            resilience_cfg = self.config.get("resilience", {})
            _policy = policy_from_config(
                resilience_cfg,
                name="graph",
                default_retries=2,
                default_backoff_base=1.0,
                default_backoff_max=15.0,
                default_timeout=45.0,
                extra_retryable=(GraphServiceError,),
            )
            self._circuit_breaker = circuit_breaker_from_config(
                resilience_cfg,
                name="graph",
            )

            # Wrap extract_graph_from_text
            _original_extract = self.extract_graph_from_text

            @resilient(_policy, circuit_breaker=self._circuit_breaker)
            async def _resilient_extract(*args, **kwargs):
                return await _original_extract(*args, **kwargs)

            self.extract_graph_from_text = _resilient_extract  # type: ignore[assignment]

            # Wrap hybrid_search
            _original_search = self.hybrid_search

            @resilient(_policy, circuit_breaker=self._circuit_breaker)
            async def _resilient_search(*args, **kwargs):
                return await _original_search(*args, **kwargs)

            self.hybrid_search = _resilient_search  # type: ignore[assignment]

            logger.debug(f"Graph resilience enabled: retries={_policy.max_retries}, " f"timeout={_policy.timeout}s")
        except ImportError:
            logger.debug("Resilience module not available; graph calls will not be retried")

    @property
    def enabled(self) -> bool:
        """Whether the graph service is enabled."""
        return self._enabled

    @property
    def app_slug(self) -> str:
        """The application slug this service is scoped to."""
        return self._app_slug

    async def _ensure_ready(self) -> None:
        """Lazily create indexes on first async call (Motor requires await)."""
        if self._indexes_created:
            return
        self._indexes_created = True
        try:
            # Compound index for app-scoped queries
            await self.collection.create_index(
                [("app_slug", ASCENDING), ("type", ASCENDING)],
                name="app_type_idx",
                background=True,
            )
            # Index for user-scoped queries
            await self.collection.create_index(
                [("app_slug", ASCENDING), ("user_id", ASCENDING)],
                name="app_user_idx",
                background=True,
            )
            # Index for edge traversal
            await self.collection.create_index(
                [("edges.target", ASCENDING)],
                name="edge_target_idx",
                background=True,
            )
            logger.debug("GraphService indexes created")
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to create GraphService indexes: {e}")

    async def _get_embedding(self, text: str) -> list[float] | None:
        """Generate embedding using the embedding service."""
        if not self.embedding_service:
            return None

        try:
            vectors = await self.embedding_service.embed(text)
            if vectors and len(vectors) > 0:
                return vectors[0]
            return None
        except (AttributeError, TypeError, ValueError, RuntimeError) as e:
            logger.warning(f"Failed to generate embedding: {e}")
            return None

    async def get_stats(self) -> dict[str, Any]:
        """Get graph service statistics."""
        await self._ensure_ready()
        try:
            pipeline = [
                {"$match": {"app_slug": self._app_slug}},
                {
                    "$group": {
                        "_id": "$type",
                        "count": {"$sum": 1},
                        "edge_count": {"$sum": {"$size": {"$ifNull": ["$edges", []]}}},
                    }
                },
            ]

            cursor = self.collection.aggregate(pipeline)
            type_stats = await cursor.to_list(length=None)

            total_nodes = sum(t["count"] for t in type_stats)
            total_edges = sum(t["edge_count"] for t in type_stats)

            return {
                "enabled": self._enabled,
                "total_nodes": total_nodes,
                "total_edges": total_edges,
                "nodes_by_type": {t["_id"]: t["count"] for t in type_stats},
                "app_slug": self._app_slug,
            }

        except (PyMongoError, OperationFailure) as e:
            return {"enabled": self._enabled, "error": str(e)}


# ============================================================================
# Factory Function
# ============================================================================


def get_graph_service(
    app_slug: str,
    collection: ScopedCollectionWrapper,
    config: dict[str, Any] | None = None,
    llm_service: LLMService | None = None,
    embedding_service: EmbeddingService | None = None,
) -> GraphService:
    """
    Factory function to create a GraphService.

    Args:
        app_slug: Application slug
        collection: Motor AsyncIOMotorCollection for graph nodes (REQUIRED - must be from MDB-Engine connection manager)
        config: Graph service configuration from manifest
        llm_service: LLMService instance for entity extraction
        embedding_service: EmbeddingService instance for hybrid search

    Returns:
        Configured GraphService instance
    """
    return GraphService(
        app_slug=app_slug,
        collection=collection,
        config=config,
        llm_service=llm_service,
        embedding_service=embedding_service,
    )
