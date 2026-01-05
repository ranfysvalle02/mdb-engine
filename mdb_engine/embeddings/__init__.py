"""
Embeddings Service Module

Provides EmbeddingService for semantic text splitting and embedding generation.
Examples should implement their own LLM clients directly using the OpenAI SDK.

For memory functionality, use mdb_engine.memory.Mem0MemoryService which
handles embeddings and LLM via environment variables (.env).

FastAPI Dependency Injection:
    # RECOMMENDED: Use request-scoped dependencies
    from mdb_engine.dependencies import get_embedding_service

    @app.post("/embed")
    async def embed_text(embedding_service=Depends(get_embedding_service)):
        embeddings = await embedding_service.embed_chunks(["Hello world"])
        return {"embeddings": embeddings}

Standalone Usage:
    from mdb_engine.embeddings import EmbeddingService, get_embedding_service

    # Auto-detects OpenAI or Azure from environment variables
    service = get_embedding_service(config={"default_embedding_model": "text-embedding-3-small"})
    embeddings = await service.embed_chunks(["Hello world"])

Example LLM implementation:
    from openai import AzureOpenAI
    from dotenv import load_dotenv
    import os

    load_dotenv()

    client = AzureOpenAI(
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.getenv("AZURE_OPENAI_API_KEY")
    )

    completion = client.chat.completions.create(
        model=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"),
        messages=[...]
    )
"""

from .dependencies import get_embedding_service_for_app
from .service import (
    AzureOpenAIEmbeddingProvider,
    BaseEmbeddingProvider,
    EmbeddingProvider,
    EmbeddingService,
    EmbeddingServiceError,
    OpenAIEmbeddingProvider,
    get_embedding_service,
)

__all__ = [
    # Core service classes
    "EmbeddingService",
    "EmbeddingServiceError",
    "EmbeddingProvider",
    # Embedding providers
    "BaseEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "AzureOpenAIEmbeddingProvider",
    # Factory function
    "get_embedding_service",
    # Utility for standalone usage
    "get_embedding_service_for_app",
]
