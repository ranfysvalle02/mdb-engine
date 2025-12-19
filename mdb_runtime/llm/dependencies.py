"""
LLM Service Dependency Injection for FastAPI

This module provides FastAPI dependency functions to inject LLM services
into route handlers. The LLM service is automatically initialized from
the app's manifest.json configuration.
"""

from typing import Optional, Any
from functools import lru_cache

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


def get_llm_service_for_app(
    app_slug: str,
    engine: Optional[Any] = None
) -> Optional[Any]:
    """
    Get LLM service for a specific app.
    
    This is a helper function that can be used with FastAPI's Depends()
    to inject the LLM service into route handlers.
    
    Args:
        app_slug: App slug (typically extracted from route context)
        engine: RuntimeEngine instance (optional, will try to get from context)
    
    Returns:
        LLMService instance if LLM is enabled for this app, None otherwise
    
    Example:
        ```python
        from fastapi import Depends
        from mdb_runtime.llm.dependencies import get_llm_service_for_app
        
        @app.get("/chat")
        async def chat_endpoint(
            llm_service = Depends(lambda: get_llm_service_for_app("my_app"))
        ):
            if not llm_service:
                raise HTTPException(503, "LLM service not available")
            response = await llm_service.chat("Hello!")
            return {"response": response}
        ```
    """
    # Try to get engine from context if not provided
    if engine is None:
        # Try to get from thread-local or context variable
        # This is a common pattern in FastAPI apps
        try:
            from contextvars import copy_context
            # In a real implementation, you'd store the engine in a context variable
            # For now, we'll require it to be passed or use a global registry
            pass
        except ImportError:
            pass
    
    if engine is None:
        return None
    
    return engine.get_llm_service(app_slug)


def create_llm_dependency(app_slug: str, engine: Optional[Any] = None):
    """
    Create a FastAPI dependency function for LLM service.
    
    This creates a dependency function that can be used with Depends()
    to inject the LLM service into route handlers.
    
    Args:
        app_slug: App slug
        engine: RuntimeEngine instance (optional)
    
    Returns:
        Dependency function that returns LLMService or raises HTTPException
    
    Example:
        ```python
        from fastapi import Depends
        from mdb_runtime.llm.dependencies import create_llm_dependency
        
        llm_dep = create_llm_dependency("my_app", engine)
        
        @app.get("/chat")
        async def chat_endpoint(llm_service = Depends(llm_dep)):
            response = await llm_service.chat("Hello!")
            return {"response": response}
        ```
    """
    def _get_llm_service() -> Any:
        llm_service = get_llm_service_for_app(app_slug, engine)
        if llm_service is None:
            if FASTAPI_AVAILABLE:
                raise HTTPException(
                    status_code=503,
                    detail=f"LLM service not available for app '{app_slug}'. "
                           "Ensure 'llm_config.enabled' is true in manifest.json and "
                           "LLM dependencies are installed."
                )
            else:
                raise RuntimeError(
                    f"LLM service not available for app '{app_slug}'"
                )
        return llm_service
    
    return _get_llm_service


# Global engine registry (for apps that don't pass engine explicitly)
_global_engine: Optional[Any] = None


def set_global_engine(engine: Any) -> None:
    """
    Set global RuntimeEngine instance for LLM dependency injection.
    
    This is useful when you have a single engine instance that you want
    to use across all apps. Call this during application startup.
    
    Args:
        engine: RuntimeEngine instance
    """
    global _global_engine
    _global_engine = engine


def get_global_engine() -> Optional[Any]:
    """
    Get global RuntimeEngine instance.
    
    Returns:
        RuntimeEngine instance if set, None otherwise
    """
    return _global_engine


def get_llm_service_dependency(app_slug: str):
    """
    Get LLM service dependency using global engine.
    
    This is a convenience function that uses the global engine registry.
    Set the engine with set_global_engine() during app startup.
    
    Args:
        app_slug: App slug
    
    Returns:
        Dependency function for FastAPI Depends()
    
    Example:
        ```python
        from fastapi import FastAPI, Depends
        from mdb_runtime.llm.dependencies import set_global_engine, get_llm_service_dependency
        
        app = FastAPI()
        
        # During startup
        set_global_engine(engine)
        
        # In routes
        @app.get("/chat")
        async def chat(llm = Depends(get_llm_service_dependency("my_app"))):
            return await llm.chat("Hello!")
        ```
    """
    return create_llm_dependency(app_slug, _global_engine)

