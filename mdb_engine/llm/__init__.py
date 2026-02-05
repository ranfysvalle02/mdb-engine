"""
LLM Service Module

Provides LLMService for chat completions and text generation.
Powered by LiteLLM - supports 100+ LLM providers.

FastAPI Dependency Injection:
    # RECOMMENDED: Use request-scoped dependencies
    from mdb_engine.llm.dependencies import get_llm_service_dependency

    @app.post("/chat")
    async def chat(llm_service=Depends(get_llm_service_dependency)):
        response = await llm_service.chat_completion(
            messages=[{"role": "user", "content": "Hello"}]
        )
        return {"response": response}

Standalone Usage:
    from mdb_engine.llm import LLMService, get_llm_service

    # Auto-detects provider from environment variables or manifest config
    service = get_llm_service(config={"default_model": "openai/gpt-4o"})
    response = await service.chat_completion(
        messages=[{"role": "user", "content": "Hello"}]
    )
"""

from .dependencies import get_llm_service_for_app
from .service import (
    LLMProvider,
    LLMService,
    LLMServiceError,
    get_llm_service,
)

__all__ = [
    # Core service classes
    "LLMService",
    "LLMServiceError",
    "LLMProvider",
    # Factory function
    "get_llm_service",
    # Utility for standalone usage
    "get_llm_service_for_app",
]
