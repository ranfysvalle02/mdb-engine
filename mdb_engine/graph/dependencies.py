"""
FastAPI Dependency Injection for Graph Service.

Provides dependency functions for injecting GraphService into FastAPI routes.

Usage:
    from mdb_engine.graph.dependencies import get_graph_service_dependency

    @app.post("/graph/extract")
    async def extract_graph(
        text: str,
        graph_service: GraphService = Depends(get_graph_service_dependency)
    ):
        result = await graph_service.extract_graph_from_text(text, "user123")
        return result
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import Request

from .base import GraphServiceError
from .service import GraphService, get_graph_service

if TYPE_CHECKING:
    from ..embeddings.service import EmbeddingService
    from ..llm.service import LLMService

logger = logging.getLogger(__name__)


async def get_graph_service_dependency(request: Request) -> GraphService:
    """
    FastAPI dependency for request-scoped GraphService.

    Retrieves the GraphService from app state, or creates one if not cached.

    Args:
        request: FastAPI request object

    Returns:
        GraphService instance

    Raises:
        GraphServiceError: If service cannot be created

    Example:
        @app.post("/graph/extract")
        async def extract_graph(
            text: str,
            graph_service: GraphService = Depends(get_graph_service_dependency)
        ):
            result = await graph_service.extract_graph_from_text(text, "user123")
            return result
    """
    app = request.app

    # Check if service is already cached in app state
    if hasattr(app.state, "graph_service") and app.state.graph_service is not None:
        return app.state.graph_service

    # Try to get from engine (if using MDB-Engine)
    if hasattr(app.state, "engine") and app.state.engine is not None:
        engine = app.state.engine
        slug = getattr(app.state, "app_slug", None) or getattr(app.state, "slug", None)
        if slug:
            service = engine.get_graph_service(slug)
            if service:
                app.state.graph_service = service
                return service

    # Try to create from manifest config
    if hasattr(app.state, "manifest") and app.state.manifest is not None:
        manifest = app.state.manifest
        graph_config = manifest.get("graph_config", {})

        if graph_config.get("enabled", True):
            # Get required dependencies
            llm_service = getattr(app.state, "llm_service", None)
            embedding_service = getattr(app.state, "embedding_service", None)

            # Get collection (requires engine)
            if hasattr(app.state, "engine") and app.state.engine is not None:
                engine = app.state.engine
                slug = manifest.get("slug", "default")
                # Default to "kg", then prefix with slug
                base_collection_name = graph_config.get("collection_name", "kg")
                if base_collection_name.startswith(f"{slug}_"):
                    collection_name = base_collection_name
                else:
                    collection_name = f"{slug}_{base_collection_name}"

                try:
                    # Use Motor async collections directly - forward-facing DI
                    motor_client = engine._connection_manager.mongo_client  # noqa: SLF001
                    motor_db = motor_client[engine.db_name]
                    collection = motor_db[collection_name]  # Motor AsyncIOMotorCollection

                    service = get_graph_service(
                        app_slug=slug,
                        collection=collection,
                        config=graph_config,
                        llm_service=llm_service,
                        embedding_service=embedding_service,
                    )
                    app.state.graph_service = service
                    return service
                except (AttributeError, RuntimeError, KeyError) as e:
                    logger.warning(f"Failed to create GraphService from manifest: {e}")

    raise GraphServiceError(
        "GraphService not available. Ensure graph_config.enabled=true in manifest "
        "and the engine is properly initialized."
    )


def get_graph_service_for_app(
    app_slug: str,
    config: dict[str, Any],
    collection: Any,
    llm_service: LLMService | None = None,
    embedding_service: EmbeddingService | None = None,
) -> GraphService:
    """
    Create a GraphService instance for a specific app.

    Utility function for creating app-scoped graph services outside of
    FastAPI dependency injection.

    Args:
        app_slug: Application slug
        config: Graph service configuration
        collection: Motor AsyncIOMotorCollection for graph nodes (REQUIRED - must be from MDB-Engine connection manager)
        llm_service: Optional LLMService instance
        embedding_service: Optional EmbeddingService instance

    Returns:
        GraphService instance

    Example:
        from mdb_engine.graph.dependencies import get_graph_service_for_app

        graph_service = get_graph_service_for_app(
            app_slug="my_app",
            config={"enabled": True, "auto_extract": True},
            collection=db.knowledge_graph,
            llm_service=llm_service,
            embedding_service=embedding_service,
        )
    """
    return get_graph_service(
        app_slug=app_slug,
        collection=collection,
        config=config,
        llm_service=llm_service,
        embedding_service=embedding_service,
    )
