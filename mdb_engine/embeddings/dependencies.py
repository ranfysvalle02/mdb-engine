"""
Embedding Service Utilities

This module provides utility functions for creating embedding services.
For FastAPI dependency injection, use the request-scoped dependencies
from `mdb_engine.dependencies` instead.

Usage:
    # For FastAPI routes (RECOMMENDED):
    from mdb_engine.dependencies import get_embedding_service

    @app.post("/embed")
    async def embed(embedding_service=Depends(get_embedding_service)):
        ...

    # For standalone/utility usage:
    from mdb_engine.embeddings.dependencies import get_embedding_service_for_app

    service = get_embedding_service_for_app("my_app", engine)
"""

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ..core.engine import MongoDBEngine

from .service import EmbeddingService, get_embedding_service


def get_embedding_service_for_app(
    app_slug: str, engine: "MongoDBEngine"
) -> Optional[EmbeddingService]:
    """
    Get embedding service for a specific app using the engine instance.

    This is a utility function for cases where you need to create an
    embedding service outside of a FastAPI request context (e.g., in
    background tasks, CLI tools, or tests).

    For FastAPI routes, use `mdb_engine.dependencies.get_embedding_service` instead.

    Args:
        app_slug: App slug to get embedding config from
        engine: MongoDBEngine instance

    Returns:
        EmbeddingService instance if embedding is enabled, None otherwise

    Example:
        # In a background task or CLI
        engine = MongoDBEngine(...)
        await engine.initialize()

        service = get_embedding_service_for_app("my_app", engine)
        if service:
            embeddings = await service.embed_chunks(["Hello world"])
    """
    if engine is None:
        return None

    # Get app config to extract embedding_config
    app_config = engine.get_app(app_slug)
    if not app_config:
        return None

    embedding_config = app_config.get("embedding_config", {})
    if not embedding_config.get("enabled", True):
        return None

    # Create embedding service with config
    return get_embedding_service(config=embedding_config)


__all__ = [
    "get_embedding_service_for_app",
]
