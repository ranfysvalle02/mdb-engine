"""
Embeddings Service Module

Provides EmbeddingService for semantic text splitting and embedding generation.
Examples should implement their own LLM clients directly using the OpenAI SDK.

For memory functionality, use mdb_engine.memory.Mem0MemoryService which
handles embeddings and LLM via environment variables (.env).

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

Example EmbeddingService usage:
    from mdb_engine.embeddings import EmbeddingService, get_embedding_service

    # In FastAPI route
    @app.post("/embed")
    async def embed_text(embedding_service: EmbeddingService = Depends(get_embedding_service)):
        embeddings = await embedding_service.embed_chunks(["Hello world"])
        return {"embeddings": embeddings}
"""

from .dependencies import (create_embedding_dependency,
                           get_embedding_service_dep,
                           get_embedding_service_dependency,
                           get_embedding_service_for_app, get_global_engine,
                           set_global_engine)
from .service import (AzureOpenAIEmbeddingProvider, BaseEmbeddingProvider,
                      EmbeddingProvider, EmbeddingService,
                      EmbeddingServiceError, OpenAIEmbeddingProvider,
                      get_embedding_service)

__all__ = [
    "EmbeddingService",
    "EmbeddingServiceError",
    "BaseEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "AzureOpenAIEmbeddingProvider",
    "EmbeddingProvider",
    "get_embedding_service",
    "get_embedding_service_for_app",
    "create_embedding_dependency",
    "set_global_engine",
    "get_global_engine",
    "get_embedding_service_dependency",
    "get_embedding_service_dep",
]
