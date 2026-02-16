"""
Embedding Management Mixin for CognitiveMemoryService.

Provides async vector embedding generation (single and batch) using the
EmbeddingProvider, with retry logic.
"""

import logging
import math

from .base import MemoryServiceError

_EMBEDDING_BATCH_SIZE = 100

logger = logging.getLogger(__name__)


def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Calculate cosine similarity between two vectors."""
    if len(vec1) != len(vec2):
        return 0.0

    try:
        dot_product = sum(a * b for a, b in zip(vec1, vec2, strict=False))
        magnitude1 = math.sqrt(sum(a * a for a in vec1))
        magnitude2 = math.sqrt(sum(a * a for a in vec2))

        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0

        return dot_product / (magnitude1 * magnitude2)
    except (ValueError, ZeroDivisionError, TypeError):
        return 0.0


def cosine_similarity_prenorm(
    vec1: list[float],
    vec2: list[float],
    norm1: float,
    norm2: float,
) -> float:
    """Cosine similarity with pre-computed norms (avoids redundant magnitude calculations).

    Use this in tight loops where the same vectors are compared multiple times
    and norms have already been computed via ``math.sqrt(sum(x*x for x in vec))``.
    """
    if norm1 == 0 or norm2 == 0:
        return 0.0
    try:
        dot_product = sum(a * b for a, b in zip(vec1, vec2, strict=False))
        return dot_product / (norm1 * norm2)
    except (ValueError, ZeroDivisionError, TypeError):
        return 0.0


class EmbeddingMixin:
    """Mixin that provides async embedding generation methods for CognitiveMemoryService."""

    def _init_embedding_service(self):
        """Initialize embedding service using mdb_engine.embeddings."""
        try:
            from ..embeddings import EmbeddingProvider, EmbeddingServiceError

            # Auto-detect provider from environment
            embedding_config = {"default_embedding_model": self.embedding_model}
            self.embedding_provider = EmbeddingProvider(config=embedding_config)
            logger.info(f"Embedding service initialized (model: {self.embedding_model})")
        except (
            ImportError,
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            ConnectionError,
            OSError,
            EmbeddingServiceError,
        ) as e:
            logger.exception("Failed to initialize embedding service")
            raise MemoryServiceError(f"Failed to initialize embedding service: {e}") from e

    async def _get_embedding(self, text: str) -> list[float]:
        """Generate vector embedding for text.

        Retry / backoff is handled by the shared resilience layer
        (``mdb_engine.core.resilience``) which is applied to the
        ``EmbeddingProvider.embed`` call during initialization.
        """
        text = text.replace("\n", " ").strip()
        if not text:
            return []

        try:
            vectors = await self.embedding_provider.embed([text], model=self.embedding_model)
            if vectors and len(vectors) > 0:
                return vectors[0]
            return []
        except (
            ImportError,
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            ConnectionError,
            OSError,
        ) as e:
            logger.exception("Embedding failed")
            raise MemoryServiceError(f"Embedding generation failed: {e}") from e

    async def _get_embeddings_batch(self, texts: list[str]) -> dict[str, list[float]]:
        """Batch generate embeddings for multiple texts in a single API call.

        Retry / backoff is handled by the shared resilience layer applied to
        ``EmbeddingProvider.embed`` during initialization.

        Args:
            texts: List of texts to embed

        Returns:
            Dictionary mapping each text to its embedding vector
        """
        if not texts:
            return {}

        # Clean texts (same preprocessing as _get_embedding)
        cleaned_texts = [t.replace("\n", " ").strip() for t in texts]
        valid_indices = [i for i, t in enumerate(cleaned_texts) if t]
        valid_texts = [cleaned_texts[i] for i in valid_indices]

        if not valid_texts:
            return {}

        # Split into batches if needed
        result_embeddings: dict[str, list[float]] = {}

        for batch_start in range(0, len(valid_texts), _EMBEDDING_BATCH_SIZE):
            batch_texts = valid_texts[batch_start : batch_start + _EMBEDDING_BATCH_SIZE]

            try:
                vectors = await self.embedding_provider.embed(batch_texts, model=self.embedding_model)

                # Map results back to original texts
                for text, vector in zip(batch_texts, vectors, strict=False):
                    if vector:
                        result_embeddings[text] = vector

            except (
                ImportError,
                AttributeError,
                TypeError,
                ValueError,
                RuntimeError,
                ConnectionError,
                OSError,
            ) as e:
                logger.warning(f"Batch embedding failed for batch starting at {batch_start}: {e}")
                # Don't raise - return partial results

        logger.info(
            f"[Batch Embed] Generated {len(result_embeddings)} embeddings "
            f"for {len(texts)} texts in batches of {_EMBEDDING_BATCH_SIZE}"
        )
        return result_embeddings
