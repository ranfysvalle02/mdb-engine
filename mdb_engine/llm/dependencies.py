"""
FastAPI dependency injection helpers for LLM Service.

Provides request-scoped and app-scoped LLM service instances.
"""

import logging
from typing import Any

from fastapi import Request

from .service import LLMService, get_llm_service

logger = logging.getLogger(__name__)


def get_llm_service_for_app(app_slug: str, config: dict[str, Any] | None = None) -> LLMService:
    """
    Get LLM service for a specific app.

    Reads llm_config from app's manifest and creates configured LLM service.

    Args:
        app_slug: Application slug
        config: Optional override config (will merge with manifest config)

    Returns:
        LLMService instance configured for the app

    Example:
        llm_service = get_llm_service_for_app("my-app")
        response = await llm_service.chat_completion([...])
    """
    # Get manifest config from engine (if available)
    # For now, use provided config or auto-detect from env
    return get_llm_service(config=config)


async def get_llm_service_dependency(request: Request) -> LLMService:
    """
    FastAPI dependency to get LLM service from request state.

    Looks for llm_service in app.state, or creates a new one.

    Args:
        request: FastAPI Request object

    Returns:
        LLMService instance

    Example:
        @app.post("/chat")
        async def chat(llm_service: LLMService = Depends(get_llm_service_dependency)):
            response = await llm_service.chat_completion([...])
            return {"response": response}
    """
    # Check if service is already in app state
    if hasattr(request.app.state, "llm_service"):
        return request.app.state.llm_service

    # Try to get config from manifest (if available)
    config = None
    if hasattr(request.app.state, "manifest"):
        manifest = request.app.state.manifest
        config = manifest.get("llm_config", {})

    # Create new service
    service = get_llm_service(config=config)

    # Cache in app state for future requests
    request.app.state.llm_service = service

    return service
