"""
LLM Service Module (Enterprise Edition)
---------------------------------------
This module provides a centralized gateway for all Large Language Model (LLM) 
and Embedding interactions within the application.

It acts as a Unified Interface over:
1. Generation: OpenAI, Anthropic, Gemini, Llama3 (via LiteLLM + Instructor)
2. Embeddings: VoyageAI, OpenAI, Cohere (via LiteLLM)

Key Features:
- **Provider Agnostic**: Switch from GPT-4 to Claude-3 or VoyageAI by changing a config string.
- **Resilient**: Automatic retries with exponential backoff for 429s (Rate Limits) and 5xx errors.
- **Type Safe**: Returns validated Pydantic objects, not raw dictionaries.
- **Observability**: Structured logging for latency, model usage, and token costs.
- **Async Native**: Non-blocking I/O optimized for FastAPI/Uvicorn workers.

Dependencies:
    pip install litellm instructor pydantic pydantic-settings tenacity
"""

from .service import LLMService, LLMServiceError, LLMSettings, get_llm_service, get_settings
from .dependencies import (
    get_llm_service_for_app,
    create_llm_dependency,
    set_global_engine,
    get_global_engine,
    get_llm_service_dependency,
)

__all__ = [
    "LLMService",
    "LLMServiceError",
    "LLMSettings",
    "get_llm_service",
    "get_settings",
    "get_llm_service_for_app",
    "create_llm_dependency",
    "set_global_engine",
    "get_global_engine",
    "get_llm_service_dependency",
]

