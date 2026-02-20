"""
Semantic Text Splitting and Embedding Service

This module provides intelligent text chunking and embedding capabilities:
1. Semantic text splitting using Rust-based semantic-text-splitter
2. Embedding generation via custom embed functions (users provide their own)
3. MongoDB storage with proper document structure

Key Features:
- Token-aware chunking (never exceeds model limits)
- Semantic boundary preservation (splits on sentences/paragraphs)
- Custom embed functions (users implement their own embedding logic)
- Batch processing for efficiency
- Automatic metadata tracking
- Platform-level defaults (users don't need to configure tokenizer - defaults to "gpt-3.5-turbo")

Dependencies:
    pip install semantic-text-splitter
"""

import logging
import os
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from ..observability.tracing import create_span

# Optional OpenAI SDK import
try:
    from openai import AsyncAzureOpenAI, AsyncOpenAI

    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    AsyncOpenAI = None
    AsyncAzureOpenAI = None

# Optional LiteLLM import
try:
    import litellm

    LITELLM_AVAILABLE = True
except ImportError:
    LITELLM_AVAILABLE = False
    litellm = None  # type: ignore[assignment]

# Optional dependencies
try:
    from semantic_text_splitter import TextSplitter

    SEMANTIC_SPLITTER_AVAILABLE = True
except ImportError:
    SEMANTIC_SPLITTER_AVAILABLE = False
    TextSplitter = None

from ..exceptions import MongoDBEngineError

logger = logging.getLogger(__name__)


class EmbeddingServiceError(MongoDBEngineError):
    """Base exception for embedding service failures."""

    pass


class BaseEmbeddingProvider(ABC):
    """
    Abstract base class for embedding providers.
    """

    @abstractmethod
    async def embed(self, text: str | list[str], model: str | None = None) -> list[list[float]]:
        """
        Generate embeddings for text.

        Args:
            text: A single string or list of strings to embed
            model: Optional model identifier

        Returns:
            List[List[float]]: List of embedding vectors
        """
        pass


class OpenAIEmbeddingProvider(BaseEmbeddingProvider):
    """
    OpenAI embedding provider.

    Uses OpenAI's embedding API. Requires OPENAI_API_KEY environment variable.
    """

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "text-embedding-3-small",
    ):
        """
        Initialize OpenAI embedding provider.

        Args:
            api_key: OpenAI API key (defaults to OPENAI_API_KEY env var)
            default_model: Default embedding model (default: "text-embedding-3-small")
        """
        if not OPENAI_AVAILABLE:
            raise EmbeddingServiceError("OpenAI SDK not available. Install with: pip install openai")

        api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise EmbeddingServiceError("OpenAI API key not found. Set OPENAI_API_KEY environment variable.")

        self.client = AsyncOpenAI(api_key=api_key)
        self.default_model = default_model

    async def embed(self, text: str | list[str], model: str | None = None) -> list[list[float]]:
        """Generate embeddings using OpenAI."""
        model = model or self.default_model

        # Normalize to list
        if isinstance(text, str):
            text = [text]

        try:
            response = await self.client.embeddings.create(model=model, input=text)

            # Extract embeddings
            vectors = [item.embedding for item in response.data]
            return vectors

        except (
            ImportError,
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            ConnectionError,
            OSError,
        ) as e:
            logger.exception(f"OpenAI embedding failed: {e}")
            raise EmbeddingServiceError(f"OpenAI embedding failed: {str(e)}") from e


class AzureOpenAIEmbeddingProvider(BaseEmbeddingProvider):
    """
    Azure OpenAI embedding provider.

    Uses Azure OpenAI's embedding API. Requires:
    - AZURE_OPENAI_API_KEY environment variable
    - AZURE_OPENAI_ENDPOINT environment variable
    """

    def __init__(
        self,
        api_key: str | None = None,
        endpoint: str | None = None,
        api_version: str | None = None,
        default_model: str = "text-embedding-3-small",
    ):
        """
        Initialize Azure OpenAI embedding provider.

        Args:
            api_key: Azure OpenAI API key (defaults to AZURE_OPENAI_API_KEY env var)
            endpoint: Azure OpenAI endpoint (defaults to AZURE_OPENAI_ENDPOINT env var)
            api_version: API version (defaults to AZURE_OPENAI_API_VERSION or
                OPENAI_API_VERSION env var)
            default_model: Default embedding model/deployment name
                (default: "text-embedding-3-small")
        """
        if not OPENAI_AVAILABLE:
            raise EmbeddingServiceError("OpenAI SDK not available. Install with: pip install openai")

        api_key = api_key or os.getenv("AZURE_OPENAI_API_KEY")
        endpoint = endpoint or os.getenv("AZURE_OPENAI_ENDPOINT")
        api_version = (
            api_version
            or os.getenv("AZURE_OPENAI_API_VERSION")
            or os.getenv("OPENAI_API_VERSION", "2024-02-15-preview")
        )

        if not api_key or not endpoint:
            raise EmbeddingServiceError(
                "Azure OpenAI credentials not found. Set "
                "AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT environment "
                "variables."
            )

        # Use AsyncAzureOpenAI for Azure (not AsyncOpenAI with Azure params)
        self.client = AsyncAzureOpenAI(api_key=api_key, api_version=api_version, azure_endpoint=endpoint)
        self.default_model = default_model

    async def embed(self, text: str | list[str], model: str | None = None) -> list[list[float]]:
        """Generate embeddings using Azure OpenAI."""
        model = model or self.default_model

        # Normalize to list
        if isinstance(text, str):
            text = [text]

        try:
            response = await self.client.embeddings.create(model=model, input=text)

            # Extract embeddings
            vectors = [item.embedding for item in response.data]
            return vectors

        except (
            ImportError,
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            ConnectionError,
            OSError,
        ) as e:
            logger.exception(f"Azure OpenAI embedding failed: {e}")
            raise EmbeddingServiceError(f"Azure OpenAI embedding failed: {str(e)}") from e


class LiteLLMEmbeddingProvider(BaseEmbeddingProvider):
    """
    LiteLLM embedding provider — supports 20+ embedding providers through a unified API.

    LiteLLM routes to the correct provider based on the model string prefix:
        - "text-embedding-3-small"          → OpenAI
        - "cohere/embed-english-v3.0"       → Cohere
        - "bedrock/amazon.titan-embed-text-v1" → AWS Bedrock
        - "vertex_ai/textembedding-gecko"   → Google Vertex AI
        - "huggingface/BAAI/bge-large-en"   → HuggingFace
        - "voyage/voyage-02"                → Voyage AI
        - "mistral/mistral-embed"           → Mistral

    Requires ``pip install mdb-engine[ai]`` (litellm is included).
    Provider-specific API keys must be set as environment variables
    (e.g., ``OPENAI_API_KEY``, ``COHERE_API_KEY``).

    See https://docs.litellm.ai/docs/embedding/supported_embedding for the full list.
    """

    def __init__(
        self,
        default_model: str = "text-embedding-3-small",
        **litellm_kwargs: Any,
    ):
        """
        Initialize LiteLLM embedding provider.

        Args:
            default_model: Default embedding model string. Use the LiteLLM model
                format with a provider prefix (e.g., ``"cohere/embed-english-v3.0"``).
                Defaults to ``"text-embedding-3-small"`` (OpenAI).
            **litellm_kwargs: Additional keyword arguments passed to every
                ``litellm.aembedding()`` call (e.g., ``api_key``, ``api_base``).
        """
        if not LITELLM_AVAILABLE:
            raise EmbeddingServiceError("LiteLLM not available. Install with: pip install mdb-engine[ai]")
        self.default_model = default_model
        self._litellm_kwargs = litellm_kwargs

    async def embed(self, text: str | list[str], model: str | None = None) -> list[list[float]]:
        """Generate embeddings using LiteLLM (supports 20+ providers)."""
        model = model or self.default_model

        if isinstance(text, str):
            text = [text]

        try:
            response = await litellm.aembedding(model=model, input=text, **self._litellm_kwargs)
            vectors = [item["embedding"] for item in response.data]
            return vectors
        except (
            ImportError,
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            ConnectionError,
            OSError,
            KeyError,
        ) as e:
            logger.exception(f"LiteLLM embedding failed: {e}")
            raise EmbeddingServiceError(f"LiteLLM embedding failed: {str(e)}") from e


def _detect_provider_from_env() -> str:
    """
    Detect embedding provider from environment variables.

    Detection order:
        1. Azure OpenAI — if ``AZURE_OPENAI_API_KEY`` **and** ``AZURE_OPENAI_ENDPOINT`` are set.
        2. OpenAI — if ``OPENAI_API_KEY`` is set.
        3. LiteLLM — if ``litellm`` is installed and *any* common provider key is set
           (``COHERE_API_KEY``, ``VOYAGE_API_KEY``, ``MISTRAL_API_KEY``,
           ``HF_TOKEN``, ``ANTHROPIC_API_KEY``, ``GEMINI_API_KEY``).
        4. Falls back to ``"openai"`` (will fail at init if no key is present).

    Returns:
        One of ``"azure"``, ``"openai"``, or ``"litellm"``.
    """
    if os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("AZURE_OPENAI_ENDPOINT"):
        return "azure"
    elif os.getenv("OPENAI_API_KEY"):
        return "openai"
    elif LITELLM_AVAILABLE and any(
        os.getenv(k)
        for k in (
            "COHERE_API_KEY",
            "VOYAGE_API_KEY",
            "MISTRAL_API_KEY",
            "HF_TOKEN",
            "ANTHROPIC_API_KEY",
            "GEMINI_API_KEY",
        )
    ):
        return "litellm"
    else:
        return "openai"


class EmbeddingProvider:
    """
    Standalone embedding provider wrapper.

    Auto-detects OpenAI, AzureOpenAI, or LiteLLM from environment variables.
    Supports OpenAI, AzureOpenAI, and LiteLLM (20+ providers).

    Example:
        # Auto-detects from environment variables
        provider = EmbeddingProvider()

        # Or explicitly provide a provider
        from mdb_engine.embeddings import OpenAIEmbeddingProvider
        provider = EmbeddingProvider(embedding_provider=OpenAIEmbeddingProvider())

        # Use LiteLLM for Cohere, Voyage, HuggingFace, etc.
        from mdb_engine.embeddings import LiteLLMEmbeddingProvider
        provider = EmbeddingProvider(
            embedding_provider=LiteLLMEmbeddingProvider(default_model="cohere/embed-english-v3.0")
        )
    """

    def __init__(
        self,
        embedding_provider: BaseEmbeddingProvider | None = None,
        config: dict[str, Any] | None = None,
    ):
        """
        Initialize Embedding Provider.

        Args:
            embedding_provider: BaseEmbeddingProvider instance (optional, will auto-detect if None)
            config: Optional dict with embedding configuration (from manifest.json embedding_config)
                   Supports: default_embedding_model

        Raises:
            EmbeddingServiceError: If provider cannot be auto-detected and none is provided
        """
        if embedding_provider is not None:
            if not isinstance(embedding_provider, BaseEmbeddingProvider):
                raise EmbeddingServiceError(
                    f"embedding_provider must be an instance of BaseEmbeddingProvider, "
                    f"got {type(embedding_provider)}"
                )
            self.embedding_provider = embedding_provider
        else:
            # Auto-detect provider from environment variables
            provider_type = _detect_provider_from_env()
            default_model = (config or {}).get("default_embedding_model", "text-embedding-3-small")

            if provider_type == "azure":
                self.embedding_provider = AzureOpenAIEmbeddingProvider(default_model=default_model)
                logger.info(f"Auto-detected Azure OpenAI embedding provider (model: {default_model})")
            elif provider_type == "litellm":
                self.embedding_provider = LiteLLMEmbeddingProvider(default_model=default_model)
                logger.info(f"Auto-detected LiteLLM embedding provider (model: {default_model})")
            else:
                self.embedding_provider = OpenAIEmbeddingProvider(default_model=default_model)
                logger.info(f"Auto-detected OpenAI embedding provider (model: {default_model})")

        # Store config for potential future use
        self.config = config or {}

        # --- Apply shared resilience (retry + backoff + circuit breaker) ---
        try:
            from ..core.resilience import (
                circuit_breaker_from_config,
                policy_from_config,
                resilient,
            )

            resilience_cfg = self.config.get("resilience", {})
            _policy = policy_from_config(
                resilience_cfg,
                name="embeddings",
                default_retries=3,
                default_backoff_base=0.5,
                default_backoff_max=15.0,
                default_timeout=30.0,
                extra_retryable=(EmbeddingServiceError,),
            )
            self._circuit_breaker = circuit_breaker_from_config(
                resilience_cfg,
                name="embeddings",
            )
            _original_embed = self.embed

            @resilient(_policy, circuit_breaker=self._circuit_breaker)
            async def _resilient_embed(*args, **kwargs):
                return await _original_embed(*args, **kwargs)

            self.embed = _resilient_embed  # type: ignore[assignment]
            logger.debug(f"Embedding resilience enabled: retries={_policy.max_retries}, " f"timeout={_policy.timeout}s")
        except ImportError:
            logger.debug("Resilience module not available; embedding calls will not be retried")

    async def embed(self, text: str | list[str], model: str | None = None) -> list[list[float]]:
        """
        Generates vector embeddings for a string or list of strings.

        Args:
            text: A single string document or a list of documents.
            model: Optional model identifier (overrides default)

        Returns:
            List[List[float]]: A list of vectors.
                               If input was a single string, returns a list containing one vector.

        Example:
            ```python
            # Batch embedding (Faster)
            docs = ["Apple", "Banana", "Cherry"]
            vectors = await provider.embed(docs, model="text-embedding-3-small")

            # vectors is [[0.1, ...], [0.2, ...], [0.3, ...]]
            ```
        """
        start_time = time.time()
        item_count = 1 if isinstance(text, str) else len(text)
        model_to_use = model or getattr(self, "model", "unknown")

        with create_span(
            "gen_ai.embed",
            {
                "gen_ai.system": "embedding",
                "gen_ai.request.model": str(model_to_use),
                "embedding.input_count": item_count,
            },
        ) as span:
            try:
                vectors = await self.embedding_provider.embed(text, model)

                duration = time.time() - start_time
                span.set_attribute("embedding.dimensions", len(vectors[0]) if vectors else 0)

                logger.info(
                    "EMBED_SUCCESS",
                    extra={"count": item_count, "latency_sec": round(duration, 3)},
                )
                return vectors

            except (AttributeError, TypeError, ValueError, RuntimeError, KeyError) as e:
                span.set_attribute("error", True)
                logger.exception(f"EMBED_FAILED: {str(e)}")
                raise EmbeddingServiceError(f"Embedding failed: {str(e)}") from e


class EmbeddingService:
    """
    Service for semantic text splitting and embedding generation.

    This service combines:
    1. Semantic text splitting (Rust-based, fast and accurate)
    2. Embedding generation (via OpenAI or AzureOpenAI, auto-detected from env vars)
    3. MongoDB storage (structured document format)

    Example:
        from mdb_engine.embeddings import EmbeddingService

        # Initialize (auto-detects OpenAI or AzureOpenAI from environment variables)
        embedding_service = EmbeddingService()

        # Process and store
        await embedding_service.process_and_store(
            text_content="Your long document here...",
            source_id="doc_101",
            collection=db.knowledge_base,
            max_tokens_per_chunk=1000
        )
    """

    def __init__(
        self,
        embedding_provider: EmbeddingProvider | None = None,
        default_max_tokens: int = 1000,
        default_tokenizer_model: str = "gpt-3.5-turbo",
        config: dict[str, Any] | None = None,
    ):
        """
        Initialize Embedding Service.

        Args:
            embedding_provider: EmbeddingProvider instance (optional, will create default if None)
            default_max_tokens: Default max tokens per chunk (default: 1000)
            default_tokenizer_model: Tokenizer model name for counting tokens
                (default: "gpt-3.5-turbo").
                This is ONLY for token counting during chunking, NOT for
                embeddings.
                Must be a valid OpenAI model name (e.g., "gpt-3.5-turbo",
                "gpt-4").
            config: Optional configuration dict (from manifest.json embedding_config)

        Raises:
            EmbeddingServiceError: If required dependencies are not available
        """
        if not SEMANTIC_SPLITTER_AVAILABLE:
            raise EmbeddingServiceError(
                "semantic-text-splitter not available. Install with: " "pip install semantic-text-splitter"
            )

        # Create embedding provider if not provided
        if embedding_provider is None:
            embedding_provider = EmbeddingProvider(config=config)

        self.embedding_provider = embedding_provider
        self.default_max_tokens = default_max_tokens
        self.default_tokenizer_model = default_tokenizer_model

    def _create_splitter(self, max_tokens: int, tokenizer_model: str | None = None) -> TextSplitter:
        """
        Create a TextSplitter instance.

        Args:
            max_tokens: Maximum tokens per chunk
            tokenizer_model: Tokenizer encoding for counting
                (default: uses default_tokenizer_model).
                This is ONLY for token counting, NOT for embeddings.

        Returns:
            TextSplitter instance
        """
        # Use provided tokenizer, or fall back to default (gpt-3.5-turbo)
        model = tokenizer_model or self.default_tokenizer_model
        return TextSplitter.from_tiktoken_model(model, max_tokens)

    async def chunk_text(
        self,
        text_content: str,
        max_tokens: int | None = None,
        tokenizer_model: str | None = None,
    ) -> list[str]:
        """
        Split text into semantic chunks.

        Uses Rust-based semantic-text-splitter for fast, accurate chunking
        that respects token limits and semantic boundaries.

        Args:
            text_content: The text to chunk
            max_tokens: Max tokens per chunk (default: uses default_max_tokens)
            tokenizer_model: Tokenizer model name for counting (optional,
                defaults to "gpt-3.5-turbo").
                This is ONLY for token counting, NOT for embeddings.
                Must be a valid OpenAI model name (e.g., "gpt-3.5-turbo",
                "gpt-4").

        Returns:
            List of text chunks

        Example:
            chunks = await service.chunk_text("Long document...", max_tokens=1000)
            print(f"Generated {len(chunks)} chunks")
        """
        max_tokens = max_tokens or self.default_max_tokens
        splitter = self._create_splitter(max_tokens, tokenizer_model)

        try:
            chunks = splitter.chunks(text_content)
            logger.info(f"Generated {len(chunks)} chunks (max_tokens={max_tokens})")
            return chunks
        except (ImportError, AttributeError, TypeError, ValueError, RuntimeError) as e:
            logger.error(f"Error chunking text: {e}", exc_info=True)
            raise EmbeddingServiceError(f"Chunking failed: {str(e)}") from e

    async def embed(self, text: str | list[str], model: str | None = None) -> list[list[float]]:
        """
        Generate embeddings for text or a list of texts.

        Natural API that works with both single strings and lists.

        Args:
            text: A single string or list of strings to embed
            model: Optional model identifier (passed to embedding provider)

        Returns:
            List of embedding vectors (each is a list of floats).
            If input was a single string, returns a list containing one vector.

        Example:
            # Single string
            vectors = await service.embed("Hello world", model="text-embedding-3-small")
            # vectors is [[0.1, 0.2, ...]]

            # List of strings (batch - more efficient)
            vectors = await service.embed(["chunk 1", "chunk 2"], model="text-embedding-3-small")
            # vectors is [[0.1, ...], [0.2, ...]]
        """
        # Normalize to list
        chunks = [text] if isinstance(text, str) else text

        if not chunks:
            return []

        try:
            # Use EmbeddingProvider's embed method (handles retries, logging, etc.)
            vectors = await self.embedding_provider.embed(chunks, model=model)
            logger.info(f"Generated {len(vectors)} embedding(s)")
            return vectors
        except (
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            ConnectionError,
            OSError,
        ) as e:
            logger.error(f"Error generating embeddings: {e}", exc_info=True)
            raise EmbeddingServiceError(f"Embedding generation failed: {str(e)}") from e

    async def process_and_store(
        self,
        text_content: str,
        source_id: str,
        collection: Any,  # MongoDB collection (AppDB Collection or Motor collection)
        max_tokens: int | None = None,
        tokenizer_model: str | None = None,
        embedding_model: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Process text and store chunks with embeddings in MongoDB.

        This is the main method that:
        1. Chunks the text semantically
        2. Generates embeddings for each chunk
        3. Stores documents in MongoDB with proper structure

        Args:
            text_content: The text to process
            source_id: Unique identifier for the source document
            collection: MongoDB collection (AppDB Collection or Motor collection)
            max_tokens: Max tokens per chunk (default: uses default_max_tokens)
            tokenizer_model: Tokenizer model for counting (default: uses default_tokenizer_model)
            embedding_model: Embedding model (default: uses EmbeddingProvider default)
            metadata: Additional metadata to store with each chunk

        Returns:
            Dict with processing results:
            {
                "chunks_created": int,
                "documents_inserted": int,
                "source_id": str
            }

        Example:
            result = await service.process_and_store(
                text_content="Long document...",
                source_id="doc_101",
                collection=db.knowledge_base,
                max_tokens=1000
            )
            print(f"Created {result['chunks_created']} chunks")
        """
        logger.info(f"Processing source: {source_id}")

        # Step 1: Chunk the text
        chunks = await self.chunk_text(text_content, max_tokens=max_tokens, tokenizer_model=tokenizer_model)

        if not chunks:
            logger.warning(f"No chunks generated for source: {source_id}")
            return {
                "chunks_created": 0,
                "documents_inserted": 0,
                "source_id": source_id,
            }

        # Step 2: Generate embeddings (batch for efficiency)
        try:
            vectors = await self.embed(chunks, model=embedding_model)
        except (
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            ConnectionError,
            OSError,
        ) as e:
            logger.exception(f"Failed to generate embeddings for {source_id}: {e}")
            raise EmbeddingServiceError(f"Embedding generation failed: {str(e)}") from e

        if len(vectors) != len(chunks):
            raise EmbeddingServiceError(f"Mismatch: {len(chunks)} chunks but {len(vectors)} embeddings")

        # Step 3: Prepare documents for insertion
        documents_to_insert = []
        for i, (chunk_text, vector) in enumerate(zip(chunks, vectors, strict=False)):
            doc = {
                "source_id": source_id,
                "chunk_index": i,
                "text": chunk_text,
                "embedding": vector,
                "metadata": {
                    "model": embedding_model or "custom",
                    "token_count": len(chunk_text),  # Approximation
                    "created_at": datetime.now(timezone.utc),
                },
            }

            # Add custom metadata if provided
            if metadata:
                doc["metadata"].update(metadata)

            documents_to_insert.append(doc)

        # Step 4: Store in MongoDB
        try:
            # Handle both AppDB Collection and Motor collection
            if hasattr(collection, "insert_many"):
                # AppDB Collection wrapper
                result = await collection.insert_many(documents_to_insert)
                inserted_count = len(result.inserted_ids)
            else:
                # Direct Motor collection
                result = await collection.insert_many(documents_to_insert)
                inserted_count = len(result.inserted_ids)

            logger.info(f"Successfully inserted {inserted_count} documents for source: {source_id}")

            return {
                "chunks_created": len(chunks),
                "documents_inserted": inserted_count,
                "source_id": source_id,
            }

        except (
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            KeyError,
            ConnectionError,
        ) as e:
            logger.error(f"Failed to store documents for {source_id}: {e}", exc_info=True)
            raise EmbeddingServiceError(f"Storage failed: {str(e)}") from e

    async def process_text(
        self,
        text_content: str,
        max_tokens: int | None = None,
        tokenizer_model: str | None = None,
        embedding_model: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Process text and return chunks with embeddings (without storing).

        Useful when you want to process text but handle storage yourself.

        Args:
            text_content: The text to process
            max_tokens: Max tokens per chunk (default: uses default_max_tokens)
            tokenizer_model: Tokenizer model for counting (default: uses default_tokenizer_model)
            embedding_model: Embedding model (default: uses EmbeddingProvider default)

        Returns:
            List of dicts, each containing:
            {
                "chunk_index": int,
                "text": str,
                "embedding": List[float],
                "metadata": Dict[str, Any]
            }

        Example::

            results = await service.process_text("Long document...")
            for result in results:
                print(f"Chunk {result['chunk_index']}: {result['text']}")
        """
        # Chunk the text
        chunks = await self.chunk_text(text_content, max_tokens=max_tokens, tokenizer_model=tokenizer_model)

        if not chunks:
            return []

        # Generate embeddings
        vectors = await self.embed(chunks, model=embedding_model)

        if len(vectors) != len(chunks):
            raise EmbeddingServiceError(f"Mismatch: {len(chunks)} chunks but {len(vectors)} embeddings")

        # Prepare results
        results = []
        for i, (chunk_text, vector) in enumerate(zip(chunks, vectors, strict=False)):
            results.append(
                {
                    "chunk_index": i,
                    "text": chunk_text,
                    "embedding": vector,
                    "metadata": {
                        "model": embedding_model or "custom",
                        "token_count": len(chunk_text),
                        "created_at": datetime.now(timezone.utc),
                    },
                }
            )

        return results


# Dependency injection helper
def get_embedding_service(
    embedding_provider: BaseEmbeddingProvider | None = None,
    config: dict[str, Any] | None = None,
) -> EmbeddingService:
    """
    Create EmbeddingService instance with auto-detected or provided embedding provider.

    Auto-detects OpenAI or AzureOpenAI from environment variables.
    Requires either OPENAI_API_KEY or AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT.

    Args:
        embedding_provider: Optional BaseEmbeddingProvider instance (will auto-detect if None)
        config: Optional configuration dict (from manifest.json
            embedding_config)
            Supports: max_tokens_per_chunk, tokenizer_model (optional,
            defaults to "gpt-3.5-turbo"), default_embedding_model

    Returns:
        EmbeddingService instance

    Example:
        from mdb_engine.embeddings import get_embedding_service

        # Auto-detects from environment variables
        embedding_service = get_embedding_service(
            config={
                "max_tokens_per_chunk": 1000,
                "default_embedding_model": "text-embedding-3-small"
            }
        )
    """
    # Platform-level defaults (users don't need to think about these)
    default_max_tokens = 1000
    # Model name for tiktoken (uses cl100k_base encoding internally)
    default_tokenizer_model = "gpt-3.5-turbo"

    # Override from config if provided (but not required)
    if config:
        default_max_tokens = config.get("max_tokens_per_chunk", default_max_tokens)
        # tokenizer_model is optional - only override if explicitly provided
        if "tokenizer_model" in config:
            default_tokenizer_model = config["tokenizer_model"]

    # Create embedding provider (auto-detects if embedding_provider is None)
    provider = EmbeddingProvider(embedding_provider=embedding_provider, config=config)

    return EmbeddingService(
        embedding_provider=provider,
        default_max_tokens=default_max_tokens,
        default_tokenizer_model=default_tokenizer_model,
        config=config,
    )
