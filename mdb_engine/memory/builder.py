"""
Builder for CognitiveMemoryService.

Breaks the ~275-line __init__ into discrete, testable steps.
Each ``_setup_*`` method resolves one concern and stores results
on the builder instance.  ``build()`` assembles the final service.

The public API is unchanged -- ``get_memory_service()`` in ``service.py``
and direct ``CognitiveMemoryService(...)`` both work as before.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pymongo.errors import OperationFailure, PyMongoError

if TYPE_CHECKING:
    from mdb_engine.database.scoped_wrapper import (
        ScopedCollectionWrapper,
        ScopedMongoWrapper,
    )
    from mdb_engine.memory.cognitive import CognitiveMemoryService

logger = logging.getLogger(__name__)


# -------------------------------------------------------------------------
# Resolved configuration dataclass
# -------------------------------------------------------------------------


@dataclass
class _ResolvedConfig:
    """All pre-resolved values that ``CognitiveMemoryService.__init__`` needs."""

    # Core
    app_slug: str
    config: dict[str, Any]
    collection_name: str
    index_name: str
    embedding_model: str
    chat_model: str
    memory_llm_model: str
    embedding_dims: int
    infer: bool
    extraction_provider: str | None
    temperature: float

    # Cognitive
    enable_cognitive: bool
    similarity_threshold: float
    reinforcement_factor: float
    merge_threshold_low: float
    merge_threshold_high: float
    duplicate_threshold: float

    # Categories
    categories_enabled: bool
    custom_categories: list[str]

    # Memory types
    memory_types_enabled: bool
    auto_detect_memory_type: bool
    default_memory_type: str
    episodic_retention_days: int
    working_ttl_hours: int

    # Database
    collection: ScopedCollectionWrapper
    db: Any  # ScopedMongoWrapper
    client: Any
    db_name: str

    # Services
    injected_llm_service: Any | None
    llm_available: bool
    embedding_provider: Any  # EmbeddingService or EmbeddingProvider

    # Sub-systems (may be None)
    graph_service: Any | None
    graph_auto_extract: bool
    timeline_service: Any | None
    persona_engine: Any | None

    # Verification
    verification_enabled: bool
    verification_max_file_size_kb: int

    # Neuroplasticity
    neuroplasticity_enabled: bool
    neuroplasticity_config: dict[str, Any]

    # Pluggable strategies (None = use default)
    scoring_strategy: Any | None = None
    decay_strategy: Any | None = None
    extraction_strategy: Any | None = None
    importance_strategy: Any | None = None
    persona_strategy: Any | None = None
    reflection_strategy: Any | None = None

    # Cognitive readiness
    cognitive_fields_ensured: bool = False
    cognitive_ready: asyncio.Event = field(default_factory=asyncio.Event)


# -------------------------------------------------------------------------
# Builder
# -------------------------------------------------------------------------


class CognitiveMemoryServiceBuilder:
    """
    Builds a ``CognitiveMemoryService`` in discrete, testable steps.

    Usage::

        builder = CognitiveMemoryServiceBuilder(
            app_slug="my_app",
            config=config,
            collection=collection,
            llm_service=llm,
            embedding_service=emb,
            graph_service=graph,
        )
        service = builder.build()
    """

    def __init__(
        self,
        app_slug: str,
        config: dict[str, Any] | None = None,
        collection: ScopedCollectionWrapper | None = None,
        *,
        graph_service: Any = None,
        embedding_service: Any = None,
        llm_service: Any = None,
        scoring_strategy: Any = None,
        decay_strategy: Any = None,
        extraction_strategy: Any = None,
        importance_strategy: Any = None,
        persona_strategy: Any = None,
        reflection_strategy: Any = None,
    ) -> None:
        from .cognitive import CognitiveMemoryServiceError

        if collection is None:
            raise CognitiveMemoryServiceError(
                "Collection is REQUIRED. CognitiveMemoryService must use "
                "MDB-Engine's connection pool. Pass a Motor AsyncIOMotorCollection "
                "obtained from MDB-Engine's connection manager."
            )

        # Reject synchronous PyMongo collections and plain strings (fail fast).
        if isinstance(collection, str):
            raise TypeError(
                f"collection must be an async Motor collection or ScopedCollectionWrapper, "
                f"not a string ('{collection}'). Did you mean to pass a collection object?"
            )
        try:
            from pymongo.collection import Collection as SyncCollection

            if isinstance(collection, SyncCollection):
                raise TypeError(
                    "collection must be an ASYNC Motor collection (AsyncIOMotorCollection) "
                    "or ScopedCollectionWrapper, not a synchronous pymongo.Collection. "
                    "Use engine.get_scoped_db(slug) to get async collections."
                )
        except ImportError:
            pass

        self._app_slug = app_slug
        self._config = config or {}
        self._collection = collection
        self._graph_service = graph_service
        self._embedding_service = embedding_service
        self._llm_service = llm_service

        # Pluggable strategies (None = resolve from config or use defaults)
        self._scoring_strategy = scoring_strategy
        self._decay_strategy = decay_strategy
        self._extraction_strategy = extraction_strategy
        self._importance_strategy = importance_strategy
        self._persona_strategy = persona_strategy
        self._reflection_strategy = reflection_strategy

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clamp(name: str, value: Any, lo: float, hi: float) -> float:
        """Clamp *value* to ``[lo, hi]``, warning if adjustment was needed."""
        try:
            fval = float(value)
        except (TypeError, ValueError):
            logger.warning(f"{name}: invalid value {value!r}, using midpoint {(lo + hi) / 2}")
            return (lo + hi) / 2
        if fval < lo or fval > hi:
            clamped = max(lo, min(hi, fval))
            logger.warning(f"{name}: {fval} out of range [{lo}, {hi}], clamped to {clamped}")
            return clamped
        return fval

    # ------------------------------------------------------------------
    # Individual setup steps
    # ------------------------------------------------------------------

    def _resolve_model(self) -> str:
        """Resolve ``memory_llm_model`` to LiteLLM ``provider/model`` format."""
        chat_model_raw = self._config.get("chat_model", "gpt-4o")
        memory_llm_model_raw = self._config.get("memory_llm_model", chat_model_raw)

        logger.debug(f"[Memory Config] chat_model={chat_model_raw}, " f"memory_llm_model_raw={memory_llm_model_raw}")

        if "/" not in memory_llm_model_raw:
            model_lower = memory_llm_model_raw.lower()
            if "gemini" in model_lower:
                resolved = f"gemini/{memory_llm_model_raw}"
            elif "claude" in model_lower:
                resolved = f"anthropic/{memory_llm_model_raw}"
            elif os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("AZURE_OPENAI_ENDPOINT"):
                deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", memory_llm_model_raw)
                resolved = f"azure/{deployment}"
            elif os.getenv("GEMINI_API_KEY"):
                resolved = f"gemini/{memory_llm_model_raw}"
            else:
                resolved = f"openai/{memory_llm_model_raw}"
            logger.info(f"[Memory Config] Resolved model: {memory_llm_model_raw} → {resolved}")
        else:
            resolved = memory_llm_model_raw
            logger.info(f"[Memory Config] Using LiteLLM format model as-is: {resolved}")

        return resolved

    def _setup_llm(self) -> tuple[Any | None, bool]:
        """Resolve LLM service.  Returns ``(injected_service, llm_available)``."""
        if self._llm_service is not None:
            logger.info("Using injected LLM service")
            return self._llm_service, True

        # Fall back to internal LiteLLM probe (kept for backward compat)
        from .cognitive import LITELLM_AVAILABLE

        if not LITELLM_AVAILABLE:
            infer = self._config.get("infer", True)
            if infer:
                logger.warning("LiteLLM not available. Memory extraction will fail when infer=True.")
            return None, False

        has_openai = bool(os.getenv("OPENAI_API_KEY"))
        has_azure = bool(os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("AZURE_OPENAI_ENDPOINT"))
        has_gemini = bool(os.getenv("GEMINI_API_KEY"))
        has_anthropic = bool(os.getenv("ANTHROPIC_API_KEY"))

        if not (has_openai or has_azure or has_gemini or has_anthropic):
            infer = self._config.get("infer", True)
            if infer:
                logger.warning("No LLM API keys found. Memory extraction will fail.")
            return None, False

        logger.info("LLM credentials detected (direct LiteLLM path)")
        return None, True

    def _setup_embedding(self) -> Any:
        """Resolve embedding provider.  Returns the provider instance."""
        if self._embedding_service is not None:
            logger.info("Using injected embedding service")
            return self._embedding_service

        # Fall back to internal EmbeddingProvider
        from ..embeddings import EmbeddingProvider

        embedding_model = self._config.get("embedding_model", "text-embedding-3-small")
        embedding_config = {"default_embedding_model": embedding_model}
        provider = EmbeddingProvider(config=embedding_config)
        logger.info(f"Embedding service initialized (model: {embedding_model})")
        return provider

    def _setup_cognitive_event(self, enable_cognitive: bool) -> tuple[bool, asyncio.Event]:
        """Set up the ``_cognitive_ready`` event and schedule background task if needed."""
        cognitive_ready = asyncio.Event()
        ensured = False

        if not enable_cognitive:
            cognitive_ready.set()
            return ensured, cognitive_ready

        # Eager scheduling deferred to post-build in __init__
        return ensured, cognitive_ready

    @staticmethod
    async def create_auxiliary_indexes(collection: ScopedCollectionWrapper) -> None:
        """Create MongoDB indexes for graph_links and timeline_id.

        Must be called from an async context (Motor requires ``await``).
        The ``CognitiveMemoryService`` invokes this lazily on first use.
        """
        try:
            await collection.create_index("graph_links.derived_from", background=True)
            await collection.create_index("graph_links.contradicts", background=True)
            await collection.create_index("metadata.timeline_id", background=True)
            await collection.create_index("metadata.confidence", background=True)
            logger.info("Created indexes for graph_links and timeline_id")
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to create indexes (may already exist): {e}")

    def _setup_graph(self) -> tuple[Any | None, bool]:
        """Resolve graph service.  Returns ``(service, auto_extract)``."""
        if self._graph_service is None:
            return None, False

        # Check memory_config's nested "graph" key first (legacy path),
        # then fall back to sensible defaults when a graph service is
        # explicitly injected — the caller clearly intends extraction.
        graph_cfg = self._config.get("graph", {})
        if graph_cfg:
            # Explicit "graph" sub-config inside memory_config
            auto_extract = graph_cfg.get("enabled", True) and graph_cfg.get("auto_extract", True)
        else:
            # No nested graph config — graph service was injected externally
            # (e.g. by ServiceInitializer which reads manifest's top-level
            # graph_config).  Default to auto_extract=True.
            auto_extract = True

        logger.info(f"Graph service injected (auto_extract={auto_extract})")
        return self._graph_service, auto_extract

    def _setup_timeline(self, db: ScopedMongoWrapper) -> Any | None:
        """Initialize timeline service."""
        try:
            from .timeline import TimelineService

            timelines_collection = db.get_collection("__timelines")
            svc = TimelineService(timelines_collection)
            logger.info("Timeline service initialized")
            return svc
        except (ImportError, PyMongoError) as e:
            logger.warning(f"Failed to initialize timeline service: {e}")
            return None

    def _setup_persona(self, app_slug: str, collection: ScopedCollectionWrapper, embedding_provider: Any) -> Any | None:
        """Initialize persona engine (if enabled)."""
        from .persona import PersonaEngine

        persona_config = self._config.get("persona", {})
        if not persona_config.get("enabled", True):
            logger.info("Persona Engine disabled")
            return None

        try:
            engine = PersonaEngine(
                app_slug=app_slug,
                collection=collection,
                embedding_service=embedding_provider,
                config=persona_config,
            )
            logger.info("Persona Engine initialized")
            return engine
        except (AttributeError, TypeError, ValueError, RuntimeError) as e:
            logger.warning(f"Failed to initialize Persona Engine: {e}")
            return None

    # ------------------------------------------------------------------
    # Strategy resolution
    # ------------------------------------------------------------------

    def _resolve_strategies(
        self,
        *,
        injected_llm_service: Any | None,
        memory_llm_model: str,
    ) -> dict[str, Any]:
        """Resolve all strategy slots.

        For each slot the priority is:
        1. Explicitly injected strategy instance (constructor arg)
        2. Manifest config block (e.g. ``memory_config.scoring.strategy``)
        3. Built-in default
        """
        from .strategies import (
            DECAY_STRATEGIES,
            IMPORTANCE_STRATEGIES,
            PERSONA_STRATEGIES,
            REFLECTION_STRATEGIES,
            SCORING_STRATEGIES,
            LLMImportance,
            NoDecay,
            PerfectRecallScoring,
            TimeCountReflection,
            WeightedPersonaBlend,
            resolve_strategy_from_config,
        )

        cfg = self._config

        # --- Scoring ---
        if self._scoring_strategy is not None:
            scoring = self._scoring_strategy
        elif "scoring" in cfg and isinstance(cfg["scoring"], dict):
            scoring = resolve_strategy_from_config(SCORING_STRATEGIES, cfg["scoring"], "perfect_recall")
        else:
            scoring = PerfectRecallScoring()

        # --- Decay ---
        if self._decay_strategy is not None:
            decay = self._decay_strategy
        elif "decay" in cfg and isinstance(cfg["decay"], dict):
            decay = resolve_strategy_from_config(DECAY_STRATEGIES, cfg["decay"], "none")
        else:
            decay = NoDecay()

        # --- Importance ---
        if self._importance_strategy is not None:
            importance = self._importance_strategy
        elif "importance" in cfg and isinstance(cfg["importance"], dict):
            importance = resolve_strategy_from_config(IMPORTANCE_STRATEGIES, cfg["importance"], "llm")
        else:
            importance = LLMImportance(
                llm_completion_fn=None,  # Wired later in _apply_resolved
                model=memory_llm_model,
            )

        # --- Persona ---
        if self._persona_strategy is not None:
            persona = self._persona_strategy
        elif "persona_blending" in cfg and isinstance(cfg["persona_blending"], dict):
            persona = resolve_strategy_from_config(PERSONA_STRATEGIES, cfg["persona_blending"], "weighted")
        else:
            persona = WeightedPersonaBlend()

        # --- Reflection ---
        if self._reflection_strategy is not None:
            reflection = self._reflection_strategy
        elif "reflection" in cfg and isinstance(cfg["reflection"], dict):
            refl_cfg = cfg["reflection"]
            if "strategy" in refl_cfg:
                reflection = resolve_strategy_from_config(REFLECTION_STRATEGIES, refl_cfg, "time_count")
            else:
                reflection = TimeCountReflection(
                    interval_hours=refl_cfg.get("interval_hours", 24.0),
                    message_threshold=refl_cfg.get("message_threshold", 50),
                )
        else:
            reflection = TimeCountReflection()

        # Extraction strategy is None by default -- the ExtractionMixin
        # checks for self._extraction_strategy and falls back to its
        # internal logic when None.
        extraction = self._extraction_strategy

        return {
            "scoring_strategy": scoring,
            "decay_strategy": decay,
            "extraction_strategy": extraction,
            "importance_strategy": importance,
            "persona_strategy": persona,
            "reflection_strategy": reflection,
        }

    # ------------------------------------------------------------------
    # build()
    # ------------------------------------------------------------------

    def build(self) -> CognitiveMemoryService:
        """
        Run all setup steps and construct a ``CognitiveMemoryService``.

        Returns:
            Fully initialized ``CognitiveMemoryService`` instance.
        """
        from .cognitive import CognitiveMemoryService

        cfg = self._config
        app_slug = self._app_slug
        collection = self._collection

        # 1. Config resolution
        memory_llm_model = self._resolve_model()
        chat_model_raw = cfg.get("chat_model", "gpt-4o")
        temperature = float(cfg.get("temperature", os.getenv("MEMORY_LLM_TEMPERATURE", "0")))
        logger.info(f"[Memory Config] LLM temperature: {temperature}")

        # 2. Database
        db = collection.database  # ScopedMongoWrapper
        client = db.client
        db_name = db.name
        logger.info(
            f"Memory Service using MDB-Engine collection: "
            f"db={db_name}, collection={cfg.get('collection_name', f'{app_slug}_memories')}, "
            f"app_slug={app_slug}"
        )

        # 3. Services
        injected_llm, llm_available = self._setup_llm()
        embedding_provider = self._setup_embedding()

        # 4. Cognitive readiness
        enable_cognitive = cfg.get("enable_cognitive", True)
        ensured, cognitive_ready = self._setup_cognitive_event(enable_cognitive)

        # 5. Indexes — deferred to CognitiveMemoryService._ensure_ready()
        # Motor's create_index() is a coroutine; it cannot be awaited from
        # a sync builder.  The service creates them lazily on first async call.

        # 6. Graph
        graph_service, graph_auto_extract = self._setup_graph()

        # 7. Timeline
        timeline_service = self._setup_timeline(db)

        # 8. Persona
        persona_engine = self._setup_persona(app_slug, collection, embedding_provider)

        # Categories
        categories_config = cfg.get("categories", {})
        # Memory types
        memory_types_config = cfg.get("memory_types", {})

        # Verification
        verification_config = cfg.get("verification", {})
        verification_enabled = verification_config.get("enabled", False)
        verification_max_file_size_kb = verification_config.get("max_file_size_kb", 100)

        # Neuroplasticity
        neuroplasticity_config = cfg.get("neuroplasticity", {})
        neuroplasticity_enabled = neuroplasticity_config.get("enabled", False)

        # 9. Strategies
        strategies = self._resolve_strategies(
            injected_llm_service=injected_llm,
            memory_llm_model=memory_llm_model,
        )

        # Validate cognitive thresholds before constructing resolved config
        similarity_threshold = self._clamp("similarity_threshold", cfg.get("similarity_threshold", 0.7), 0.0, 1.0)
        reinforcement_factor = self._clamp("reinforcement_factor", cfg.get("reinforcement_factor", 1.1), 0.01, 10.0)
        merge_threshold_low = self._clamp("merge_threshold_low", cfg.get("merge_threshold_low", 0.7), 0.0, 1.0)
        merge_threshold_high = self._clamp("merge_threshold_high", cfg.get("merge_threshold_high", 0.85), 0.0, 1.0)
        duplicate_threshold = self._clamp("duplicate_threshold", cfg.get("duplicate_threshold", 0.90), 0.0, 1.0)

        # Ensure ordering: merge_low <= merge_high <= duplicate
        if merge_threshold_low > merge_threshold_high:
            logger.warning(
                f"merge_threshold_low ({merge_threshold_low}) > merge_threshold_high ({merge_threshold_high}); swapping"
            )
            merge_threshold_low, merge_threshold_high = merge_threshold_high, merge_threshold_low
        if merge_threshold_high > duplicate_threshold:
            logger.warning(
                f"merge_threshold_high ({merge_threshold_high}) > duplicate_threshold ({duplicate_threshold}); clamping"
            )
            merge_threshold_high = duplicate_threshold

        resolved = _ResolvedConfig(
            app_slug=app_slug,
            config=cfg,
            collection_name=cfg.get("collection_name", f"{app_slug}_memories"),
            index_name=cfg.get("index_name", "vector_index"),
            embedding_model=cfg.get("embedding_model", "text-embedding-3-small"),
            chat_model=chat_model_raw,
            memory_llm_model=memory_llm_model,
            embedding_dims=cfg.get("embedding_dims", 1536),
            infer=cfg.get("infer", True),
            extraction_provider=cfg.get("extraction_provider"),
            temperature=temperature,
            # Cognitive (validated above)
            enable_cognitive=enable_cognitive,
            similarity_threshold=similarity_threshold,
            reinforcement_factor=reinforcement_factor,
            merge_threshold_low=merge_threshold_low,
            merge_threshold_high=merge_threshold_high,
            duplicate_threshold=duplicate_threshold,
            # Categories
            categories_enabled=categories_config.get("enabled", True),
            custom_categories=categories_config.get("custom_categories", []),
            # Memory types
            memory_types_enabled=memory_types_config.get("enabled", True),
            auto_detect_memory_type=memory_types_config.get("auto_detect", True),
            default_memory_type=memory_types_config.get("default_type", "semantic"),
            episodic_retention_days=memory_types_config.get("episodic_retention_days", 730),
            working_ttl_hours=memory_types_config.get("working_ttl_hours", 24),
            # Database
            collection=collection,
            db=db,
            client=client,
            db_name=db_name,
            # Services
            injected_llm_service=injected_llm,
            llm_available=llm_available,
            embedding_provider=embedding_provider,
            # Sub-systems
            graph_service=graph_service,
            graph_auto_extract=graph_auto_extract,
            timeline_service=timeline_service,
            persona_engine=persona_engine,
            # Verification
            verification_enabled=verification_enabled,
            verification_max_file_size_kb=verification_max_file_size_kb,
            # Neuroplasticity
            neuroplasticity_enabled=neuroplasticity_enabled,
            neuroplasticity_config=neuroplasticity_config,
            # Pluggable strategies
            scoring_strategy=strategies["scoring_strategy"],
            decay_strategy=strategies["decay_strategy"],
            extraction_strategy=strategies["extraction_strategy"],
            importance_strategy=strategies["importance_strategy"],
            persona_strategy=strategies["persona_strategy"],
            reflection_strategy=strategies["reflection_strategy"],
            # Cognitive readiness
            cognitive_fields_ensured=ensured,
            cognitive_ready=cognitive_ready,
        )

        service = CognitiveMemoryService(_resolved=resolved)

        logger.info(
            f"Cognitive Memory Service initialized: "
            f"cognitive_features={enable_cognitive}, "
            f"perfect_recall=True (unlimited memory), "
            f"graph_enabled={graph_service is not None}, "
            f"persona_enabled={persona_engine is not None}, "
            f"verification_enabled={verification_enabled}, "
            f"neuroplasticity_enabled={neuroplasticity_enabled}"
        )

        return service
