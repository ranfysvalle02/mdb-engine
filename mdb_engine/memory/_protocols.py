"""Protocol classes that formalize the implicit contracts between mixins.

Each mixin in the ``CognitiveMemoryService`` composite freely accesses
``self.*`` attributes defined elsewhere.  The protocols below make those
dependencies explicit so that a type-checker (MyPy) can catch breaks at
lint time rather than at runtime.

Usage — annotate the mixin docstring with the expected host protocol::

    class ReinforcementMixin:
        \"\"\"Requires host to implement :class:`ReinforcementHost`.\"\"\"

These protocols are **not** enforced at runtime; they exist purely for
documentation and optional static analysis.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    import asyncio

    from mdb_engine.database.scoped_wrapper import ScopedCollectionWrapper


# ---------------------------------------------------------------------------
# Embedding host
# ---------------------------------------------------------------------------


@runtime_checkable
class EmbeddingHost(Protocol):
    """Attributes required by :class:`EmbeddingMixin`."""

    embedding_provider: Any
    embed_model: str


# ---------------------------------------------------------------------------
# LLM host
# ---------------------------------------------------------------------------


@runtime_checkable
class LLMHost(Protocol):
    """Attributes required by any mixin that calls the LLM."""

    llm_available: bool
    memory_llm_model: str

    async def _llm_completion(self, prompt: str, **kwargs: Any) -> Any: ...


# ---------------------------------------------------------------------------
# Storage host
# ---------------------------------------------------------------------------


@runtime_checkable
class StorageHost(Protocol):
    """Attributes required by :class:`StorageMixin`."""

    collection: ScopedCollectionWrapper
    collection_name: str
    db_name: str
    index_name: str
    app_slug: str
    enable_cognitive: bool


# ---------------------------------------------------------------------------
# Scoring host
# ---------------------------------------------------------------------------


@runtime_checkable
class ScoringHost(Protocol):
    """Attributes required by :class:`ScoringMixin`."""

    collection: ScopedCollectionWrapper
    index_name: str
    app_slug: str
    llm_available: bool
    memory_llm_model: str
    _importance_strategy: Any
    _scoring_strategy: Any
    _neuroplasticity_engine: Any | None
    timeline_service: Any | None

    async def _llm_completion(self, prompt: str, **kwargs: Any) -> Any: ...

    def _get_user_scoring_weights(self, user_id: str) -> dict[str, float]: ...


# ---------------------------------------------------------------------------
# Reinforcement host
# ---------------------------------------------------------------------------


@runtime_checkable
class ReinforcementHost(Protocol):
    """Attributes required by :class:`ReinforcementMixin`."""

    collection: ScopedCollectionWrapper
    index_name: str
    duplicate_threshold: float
    reinforcement_factor: float
    similarity_threshold: float
    llm_available: bool
    memory_llm_model: str

    async def _llm_completion(self, prompt: str, **kwargs: Any) -> Any: ...

    async def _get_embedding(self, text: str) -> list[float]: ...

    def _detect_category_from_text(self, text: str) -> str: ...

    def _get_best_category(
        self,
        existing_category: str,
        new_text: str,
        new_category: str | None = None,
    ) -> str: ...

    def _has_new_information(self, existing_text: str, new_text: str) -> bool: ...


# ---------------------------------------------------------------------------
# Extraction host
# ---------------------------------------------------------------------------


@runtime_checkable
class ExtractionHost(Protocol):
    """Attributes required by :class:`ExtractionMixin`."""

    llm_available: bool
    memory_llm_model: str
    extraction_provider: str | None
    _injected_llm_service: Any | None
    categories_enabled: bool
    custom_categories: list[str]
    memory_types_enabled: bool
    auto_detect_memory_type: bool
    default_memory_type: str
    app_slug: str
    _extraction_strategy: Any | None

    async def _llm_completion(self, prompt: str, **kwargs: Any) -> Any: ...

    async def _get_embeddings_batch(self, texts: list[str]) -> dict[str, list[float]]: ...

    def _detect_category_from_text(self, text: str) -> str: ...


# ---------------------------------------------------------------------------
# Merging host
# ---------------------------------------------------------------------------


@runtime_checkable
class MergingHost(Protocol):
    """Attributes required by :class:`MergingMixin`."""

    collection: ScopedCollectionWrapper
    llm_available: bool
    memory_llm_model: str
    categories_enabled: bool
    memory_types_enabled: bool
    auto_detect_memory_type: bool
    default_memory_type: str
    episodic_retention_days: int
    enable_cognitive: bool
    _graph_service: Any | None

    async def _llm_completion(self, prompt: str, **kwargs: Any) -> Any: ...

    def _detect_category_from_text(self, text: str) -> str: ...

    def _get_best_category(
        self,
        existing_category: str,
        new_text: str,
        new_category: str | None = None,
    ) -> str: ...


# ---------------------------------------------------------------------------
# Verification host
# ---------------------------------------------------------------------------


@runtime_checkable
class VerificationHost(Protocol):
    """Attributes required by :class:`VerificationMixin`."""

    verification_enabled: bool
    verification_max_file_size_kb: int


# ---------------------------------------------------------------------------
# Cognitive service host (full composite)
# ---------------------------------------------------------------------------


@runtime_checkable
class CognitiveServiceHost(Protocol):
    """Union of all mixin host requirements.

    ``CognitiveMemoryService`` should satisfy this protocol after
    ``_apply_resolved()`` completes.
    """

    # Core
    collection: ScopedCollectionWrapper
    collection_name: str
    db_name: str
    index_name: str
    app_slug: str
    enable_cognitive: bool

    # LLM
    llm_available: bool
    memory_llm_model: str

    # Embedding
    embedding_provider: Any
    embed_model: str

    # Cognitive thresholds
    duplicate_threshold: float
    reinforcement_factor: float
    similarity_threshold: float

    # Categories
    categories_enabled: bool
    custom_categories: list[str]

    # Memory types
    memory_types_enabled: bool
    auto_detect_memory_type: bool
    default_memory_type: str
    episodic_retention_days: int

    # Sub-systems
    _graph_service: Any | None
    timeline_service: Any | None
    _neuroplasticity_engine: Any | None
    _importance_strategy: Any
    _scoring_strategy: Any
    _extraction_strategy: Any | None

    # Verification
    verification_enabled: bool
    verification_max_file_size_kb: int

    # Cognitive readiness
    _cognitive_fields_ensured: bool
    _cognitive_ready: asyncio.Event
