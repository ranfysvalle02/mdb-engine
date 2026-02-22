"""
Perfect Brain — unified container for advanced memory components.

Initialized from manifest ``perfect_brain`` config and exposed via
``engine.get_perfect_brain(slug)`` or ``Depends(get_perfect_brain)``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..database.scoped_wrapper import ScopedMongoWrapper

logger = logging.getLogger(__name__)


class PerfectBrain:
    """Unified container for all Perfect Brain components.

    Each component is lazily initialized based on the ``config`` dict
    (typically from ``manifest["perfect_brain"]``).  Components that
    are not enabled remain ``None``.

    Args:
        slug: Application slug.
        scoped_db: ``ScopedMongoWrapper`` for collection access.
        embedding_service: Shared ``EmbeddingService`` (or provider).
        llm_service: Shared ``LLMService``.
        config: ``perfect_brain`` manifest section.
    """

    def __init__(
        self,
        slug: str,
        scoped_db: ScopedMongoWrapper,
        embedding_service: Any | None = None,
        llm_service: Any | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.slug = slug
        self._db = scoped_db
        self._embedding_service = embedding_service
        self._llm_service = llm_service
        self._config = config or {}

        self._shared_memory: Any | None = None
        self._reflective_memory: Any | None = None
        self._predictive_memory: Any | None = None
        self._memory_veto: Any | None = None
        self._prospective_memory: Any | None = None
        self._cognitive_memory: Any | None = None
        self._timeline_service: Any | None = None
        self._memory_versioning: Any | None = None
        self._consolidator: Any | None = None
        self._reflection_service: Any | None = None

        self._initialize_components()

    def _initialize_components(self) -> None:  # noqa: C901
        cfg = self._config
        if not cfg.get("enabled", False):
            return

        if cfg.get("memory_veto"):
            self._init_memory_veto()

        if cfg.get("shared_memory"):
            self._init_shared_memory()

        if cfg.get("timeline_service"):
            self._init_timeline_service()

        if cfg.get("memory_versioning"):
            self._init_memory_versioning()

        if cfg.get("prospective_memory"):
            self._init_prospective_memory()

        if cfg.get("cognitive_memory"):
            self._init_cognitive_memory()

        consolidator_cfg = cfg.get("consolidator", {})
        if consolidator_cfg.get("enabled"):
            self._init_consolidator(consolidator_cfg)

        reflection_cfg = cfg.get("reflection", {})
        if reflection_cfg.get("enabled"):
            self._init_reflection_service(reflection_cfg)

        logger.info(f"PerfectBrain initialized for '{self.slug}' with components: {self.active_components}")

    # ------------------------------------------------------------------
    # Component initializers
    # ------------------------------------------------------------------

    def _init_memory_veto(self) -> None:
        try:
            from .veto import MemoryVeto

            col = self._db.get_collection(f"{self.slug}_memory_vetoes")
            self._memory_veto = MemoryVeto(collection=col)
        except (ImportError, AttributeError, TypeError, ValueError, RuntimeError) as e:
            logger.warning(f"PerfectBrain: failed to init MemoryVeto: {e}")

    def _init_shared_memory(self) -> None:
        try:
            from .shared import SharedMemory

            sem_col = self._db.get_collection(f"{self.slug}_memories")
            shared_col = self._db.get_collection(f"{self.slug}_shared_memories")
            self._shared_memory = SharedMemory(
                semantic_collection=sem_col,
                shared_collection=shared_col,
                embedding_service=self._embedding_service,
            )
        except (ImportError, AttributeError, TypeError, ValueError, RuntimeError) as e:
            logger.warning(f"PerfectBrain: failed to init SharedMemory: {e}")

    def _init_timeline_service(self) -> None:
        try:
            from .timeline import TimelineService

            col = self._db.get_collection(f"{self.slug}_timelines")
            self._timeline_service = TimelineService(collection=col)
        except (ImportError, AttributeError, TypeError, ValueError, RuntimeError) as e:
            logger.warning(f"PerfectBrain: failed to init TimelineService: {e}")

    def _init_memory_versioning(self) -> None:
        try:
            from .versioning import MemoryVersioning

            col = self._db.get_collection(f"{self.slug}_memory_versions")
            self._memory_versioning = MemoryVersioning(collection=col)
        except (ImportError, AttributeError, TypeError, ValueError, RuntimeError) as e:
            logger.warning(f"PerfectBrain: failed to init MemoryVersioning: {e}")

    def _init_prospective_memory(self) -> None:
        try:
            from .prospective import ProspectiveMemory

            col = self._db.get_collection(f"{self.slug}_prospective")
            self._prospective_memory = ProspectiveMemory(
                collection=col,
                embedding_service=self._embedding_service,
            )
        except (ImportError, AttributeError, TypeError, ValueError, RuntimeError) as e:
            logger.warning(f"PerfectBrain: failed to init ProspectiveMemory: {e}")

    def _init_cognitive_memory(self) -> None:
        try:
            from .system import CognitiveMemory

            col = self._db.get_collection(f"{self.slug}_memories")
            self._cognitive_memory = CognitiveMemory(
                collection=col,
                embedding_service=self._embedding_service,
            )
        except (ImportError, AttributeError, TypeError, ValueError, RuntimeError) as e:
            logger.warning(f"PerfectBrain: failed to init CognitiveMemory: {e}")

    def _init_consolidator(self, cfg: dict[str, Any]) -> None:
        try:
            from .consolidator import MemoryConsolidator

            if self._llm_service is None or self._embedding_service is None:
                logger.warning(
                    "PerfectBrain: MemoryConsolidator requires llm_service and " "embedding_service — skipping."
                )
                return
            self._consolidator = MemoryConsolidator(
                db_client=self._db,
                llm_service=self._llm_service,
                embedding_service=self._embedding_service,
                memory_veto=self._memory_veto,
            )
        except (ImportError, AttributeError, TypeError, ValueError, RuntimeError) as e:
            logger.warning(f"PerfectBrain: failed to init MemoryConsolidator: {e}")

    def _init_reflection_service(self, cfg: dict[str, Any]) -> None:
        try:
            from .reflection import ReflectionService

            mem_col = self._db.get_collection(f"{self.slug}_memories")
            self._reflection_service = ReflectionService(
                app_slug=self.slug,
                memories_collection=mem_col,
                config=cfg,
                llm_service=self._llm_service,
            )
        except (ImportError, AttributeError, TypeError, ValueError, RuntimeError) as e:
            logger.warning(f"PerfectBrain: failed to init ReflectionService: {e}")

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    @property
    def shared_memory(self) -> Any | None:
        return self._shared_memory

    @property
    def reflective_memory(self) -> Any | None:
        return self._reflective_memory

    @property
    def predictive_memory(self) -> Any | None:
        return self._predictive_memory

    @property
    def memory_veto(self) -> Any | None:
        return self._memory_veto

    @property
    def prospective_memory(self) -> Any | None:
        return self._prospective_memory

    @property
    def cognitive_memory(self) -> Any | None:
        return self._cognitive_memory

    @property
    def timeline_service(self) -> Any | None:
        return self._timeline_service

    @property
    def memory_versioning(self) -> Any | None:
        return self._memory_versioning

    @property
    def consolidator(self) -> Any | None:
        return self._consolidator

    @property
    def reflection_service(self) -> Any | None:
        return self._reflection_service

    @property
    def active_components(self) -> list[str]:
        """Return names of components that were successfully initialized."""
        names = []
        for attr in (
            "shared_memory",
            "reflective_memory",
            "predictive_memory",
            "memory_veto",
            "prospective_memory",
            "cognitive_memory",
            "timeline_service",
            "memory_versioning",
            "consolidator",
            "reflection_service",
        ):
            if getattr(self, attr) is not None:
                names.append(attr)
        return names
