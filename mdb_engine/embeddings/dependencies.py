"""
Embedding Service Dependency Injection for FastAPI

This module provides FastAPI dependency functions to inject embedding services
into route handlers. The embedding service is automatically initialized from
the app's manifest.json configuration.
"""

from typing import Any, Optional

# Optional FastAPI import (only needed if FastAPI is available)
try:
    from fastapi import Depends, HTTPException

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

    # Stub for when FastAPI is not available
    def Depends(*args, **kwargs):
        return None

    class HTTPException(Exception):
        pass


from .service import EmbeddingService, get_embedding_service

# Global engine registry (for apps that don't pass engine explicitly)
_global_engine: Optional[Any] = None
_global_app_slug: Optional[str] = None


def set_global_engine(engine: Any, app_slug: Optional[str] = None) -> None:
    """
    Set global MongoDBEngine instance for embedding dependency injection.

    This is useful when you have a single engine instance that you want
    to use across all apps. Call this during application startup.

    Args:
        engine: MongoDBEngine instance
        app_slug: Optional app slug
    """
    global _global_engine, _global_app_slug
    _global_engine = engine
    _global_app_slug = app_slug


def get_global_engine() -> Optional[Any]:
    """
    Get global MongoDBEngine instance.

    Returns:
        MongoDBEngine instance if set, None otherwise
    """
    return _global_engine


def get_embedding_service_for_app(
    app_slug: str, engine: Optional[Any] = None
) -> Optional[EmbeddingService]:
    """
    Get embedding service for a specific app.

    This is a helper function that can be used with FastAPI's Depends()
    to inject the embedding service into route handlers.

    Args:
        app_slug: App slug (typically extracted from route context)
        engine: MongoDBEngine instance (optional, will try to get from context)

    Returns:
        EmbeddingService instance if embedding is enabled for this app, None otherwise

    Example:
        ```python
        from fastapi import Depends
        from mdb_engine.embeddings.dependencies import get_embedding_service_for_app

        @app.post("/embed")
        async def embed_endpoint(
            embedding_service = Depends(lambda: get_embedding_service_for_app("my_app"))
        ):
            if not embedding_service:
                raise HTTPException(503, "Embedding service not available")
            embeddings = await embedding_service.embed_chunks(["Hello world"])
            return {"embeddings": embeddings}
        ```
    """
    # Try to get engine from context if not provided
    if engine is None:
        engine = _global_engine

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


def create_embedding_dependency(app_slug: str, engine: Optional[Any] = None):
    """
    Create a FastAPI dependency function for embedding service.

    This creates a dependency function that can be used with Depends()
    to inject the embedding service into route handlers.

    Args:
        app_slug: App slug
        engine: MongoDBEngine instance (optional)

    Returns:
        Dependency function that returns EmbeddingService or raises HTTPException

    Example:
        ```python
        from fastapi import Depends
        from mdb_engine.embeddings.dependencies import create_embedding_dependency

        embedding_dep = create_embedding_dependency("my_app", engine)

        @app.post("/embed")
        async def embed_endpoint(embedding_service = Depends(embedding_dep)):
            embeddings = await embedding_service.embed_chunks(["Hello world"])
            return {"embeddings": embeddings}
        ```
    """

    def _get_embedding_service() -> EmbeddingService:
        embedding_service = get_embedding_service_for_app(app_slug, engine)
        if embedding_service is None:
            if FASTAPI_AVAILABLE:
                raise HTTPException(
                    status_code=503,
                    detail=f"Embedding service not available for app '{app_slug}'. "
                    "Ensure 'embedding_config.enabled' is true in manifest.json and "
                    "embedding dependencies are installed.",
                )
            else:
                raise RuntimeError(
                    f"Embedding service not available for app '{app_slug}'"
                )
        return embedding_service

    return _get_embedding_service


def get_embedding_service_dependency(app_slug: str):
    """
    Get embedding service dependency using global engine.

    This is a convenience function that uses the global engine registry.
    Set the engine with set_global_engine() during app startup.

    Args:
        app_slug: App slug

    Returns:
        Dependency function for FastAPI Depends()

    Example:
        ```python
        from fastapi import FastAPI, Depends
        from mdb_engine.embeddings.dependencies import (
            set_global_engine, get_embedding_service_dependency
        )

        app = FastAPI()

        # During startup
        set_global_engine(engine, app_slug="my_app")

        # In routes
        @app.post("/embed")
        async def embed(embedding_service = Depends(get_embedding_service_dependency("my_app"))):
            return await embedding_service.embed_chunks(["Hello world"])
        ```
    """
    return create_embedding_dependency(app_slug, _global_engine)


# Alias for backward compatibility
get_embedding_service_dep = get_embedding_service_dependency
