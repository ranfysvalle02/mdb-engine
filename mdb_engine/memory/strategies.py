"""
Pluggable Strategy Protocols and Default Implementations for Memory Mechanics.

This module defines the Strategy API surface that lets users customize how
the CognitiveMemoryService handles scoring, decay, extraction, importance
assessment, persona blending, and reflection triggers -- without touching
core internals.

Each strategy is defined as a ``typing.Protocol`` (structural typing).
Any object with matching method signatures satisfies the protocol -- no
inheritance required.

Default implementations reproduce the existing behaviour so nothing breaks
if strategies are not explicitly provided.  Built-in alternatives ship
alongside defaults for common use-cases (e.g. recency-decay scoring,
exponential memory decay, rule-based importance).

Usage (code-based)::

    from mdb_engine.memory.strategies import RecencyDecayScoring, ExponentialDecay

    service = get_memory_service(
        app_slug="my_app",
        collection=collection,
        config=config,
        scoring_strategy=RecencyDecayScoring(half_life_hours=72),
        decay_strategy=ExponentialDecay(half_life_hours=168),
    )

Usage (custom strategy -- no inheritance needed)::

    class MyScoring:
        async def score(self, memory, query_context):
            age_h = (query_context.now - memory.created_at).total_seconds() / 3600
            return memory.similarity * memory.importance * (0.5 ** (age_h / 48))

    service = get_memory_service(..., scoring_strategy=MyScoring())

Usage (manifest-based -- no code)::

    {
      "memory_config": {
        "scoring": {"strategy": "recency_decay", "half_life_hours": 72},
        "decay": {"strategy": "exponential", "half_life_hours": 168}
      }
    }
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# ============================================================================
# Context Dataclasses
# ============================================================================


@dataclass(frozen=True)
class MemoryDocument:
    """Lightweight view of a memory document passed to strategies.

    Strategies receive this instead of raw dicts so the API surface is
    explicit and documented.
    """

    id: str
    text: str
    similarity: float = 0.0
    importance: float = 0.5
    access_count: int = 0
    confidence: float = 0.8
    emotion: float = 0.3
    emotion_type: str = "neutral"
    category: str | None = None
    created_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> MemoryDocument:
        """Build from a raw MongoDB document / search result dict."""
        return cls(
            id=str(doc.get("_id", doc.get("id", ""))),
            text=doc.get("text", doc.get("memory", "")),
            similarity=doc.get("similarity", 0.0),
            importance=doc.get("importance", 0.5),
            access_count=doc.get("access_count", 0),
            confidence=doc.get("confidence", 0.8),
            emotion=doc.get("emotion", 0.3),
            emotion_type=doc.get("emotion_type", "neutral"),
            category=doc.get("category"),
            created_at=doc.get("created_at"),
            metadata=doc.get("metadata", {}),
        )


@dataclass(frozen=True)
class QueryContext:
    """Context provided to scoring strategies during retrieval."""

    now: datetime
    user_id: str | None = None
    scoring_weights: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractionContext:
    """Context provided to extraction strategies."""

    app_slug: str
    user_id: str | None = None
    categories_enabled: bool = True
    custom_categories: list[str] = field(default_factory=list)
    memory_types_enabled: bool = True
    auto_detect_memory_type: bool = True


@dataclass(frozen=True)
class ExtractedFact:
    """A single fact returned by an extraction strategy."""

    text: str
    category: str = "biographical"
    emotion: float = 0.3
    emotion_type: str = "neutral"
    memory_type: str | None = None


@dataclass(frozen=True)
class ImportanceContext:
    """Context provided to importance assessment strategies."""

    app_slug: str
    user_id: str | None = None
    category: str | None = None


@dataclass(frozen=True)
class ReflectionStats:
    """Stats provided to reflection strategy trigger checks."""

    last_reflection_at: datetime | None = None
    recent_memory_count: int = 0
    total_memory_count: int = 0


@dataclass(frozen=True)
class ReflectionContext:
    """Context provided to reflection consolidation."""

    app_slug: str
    user_id: str
    interval_hours: float = 24.0


@dataclass
class ConsolidationResult:
    """Result of a reflection consolidation."""

    summary: str | None = None
    entities_extracted: int = 0
    memories_processed: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


# ============================================================================
# Strategy Protocols
# ============================================================================


@runtime_checkable
class ScoringStrategy(Protocol):
    """Determines how memories are ranked during retrieval.

    The default implementation uses:
        ``similarity * importance * (1 + ln(access_count + 1)) * emotion_factor``

    Implement this protocol to change the ranking formula -- for example,
    adding recency decay or custom weighting.
    """

    async def score(self, memory: MemoryDocument, query_context: QueryContext) -> float:
        """Return a combined relevance score for *memory* given *query_context*.

        Higher values = more relevant.
        """
        ...


@runtime_checkable
class DecayStrategy(Protocol):
    """Controls memory lifecycle / salience over time.

    The default implementation is *Perfect Recall* -- memories never decay
    and are never archived.

    Implement this to add temporal decay, archival policies, or pruning.
    """

    async def apply_decay(self, memory: MemoryDocument, now: datetime) -> float:
        """Return the decayed importance for *memory* at time *now*.

        A return value <= 0 indicates the memory should be considered
        fully decayed (but not necessarily deleted).
        """
        ...

    def should_archive(self, memory: MemoryDocument) -> bool:
        """Return ``True`` if *memory* should be moved to cold storage."""
        ...


@runtime_checkable
class ExtractionStrategy(Protocol):
    """Controls how facts are extracted from raw text.

    The default implementation uses LLM-based cognitive extraction with
    emotion tagging and category classification.

    Implement this to use a different extraction approach -- for example,
    rule-based extraction, a different LLM prompt, or an external NLP
    pipeline.
    """

    async def extract(self, text: str, context: ExtractionContext) -> list[ExtractedFact]:
        """Extract atomic facts from *text*.

        Returns a list of ``ExtractedFact`` instances.  An empty list means
        no extractable facts were found (not an error).
        """
        ...


@runtime_checkable
class ImportanceStrategy(Protocol):
    """Controls how importance is assessed for new memories.

    The default implementation uses an LLM to rate importance on a 1-10
    scale, normalised to 0.1--1.0.

    Implement this for rule-based scoring, cost-sensitive setups, or
    custom assessment logic.
    """

    async def assess(self, text: str, context: ImportanceContext) -> float:
        """Return an importance score in ``[0.1, 1.0]`` for *text*."""
        ...


@runtime_checkable
class PersonaStrategy(Protocol):
    """Controls how persona influences memory retrieval.

    The default implementation blends query and persona vectors with a
    fixed 80/20 weighting and re-normalises.

    Implement this to change the blending ratio, use a different
    combination method, or apply persona influence in other ways.
    """

    async def blend(
        self,
        query_vector: list[float],
        persona_vector: list[float],
        config: dict[str, Any],
    ) -> list[float]:
        """Return a blended vector combining *query_vector* and *persona_vector*."""
        ...


@runtime_checkable
class ReflectionStrategy(Protocol):
    """Controls when and how memory consolidation triggers.

    The default implementation triggers on time-based or count-based
    thresholds.

    Implement this to add custom triggers (e.g. importance-based, topic
    shift detection) or different consolidation approaches.
    """

    async def should_trigger(self, user_id: str, stats: ReflectionStats) -> tuple[bool, str]:
        """Return ``(True, reason)`` if reflection should fire, else ``(False, reason)``."""
        ...

    async def consolidate(self, memories: list[MemoryDocument], context: ReflectionContext) -> ConsolidationResult:
        """Consolidate *memories* into a summary / reflection."""
        ...


# ============================================================================
# Default Implementations (match current behaviour)
# ============================================================================


class PerfectRecallScoring:
    """Default scoring: ``similarity * effective_importance * emotion_factor``.

    ``effective_importance = importance * (1 + ln(access_count + 1))``

    This reproduces the existing ranking formula from ``ScoringMixin._search()``.
    """

    async def score(self, memory: MemoryDocument, query_context: QueryContext) -> float:
        weights = query_context.scoring_weights

        base_importance = memory.importance * (1 + math.log(memory.access_count + 1))

        # Emotion-type-aware boost (three biochemical pathways)
        type_boost = 1.0
        if memory.emotion_type == "novelty":
            type_boost += weights.get("novelty_boost", 0.1) * memory.emotion
        elif memory.emotion_type == "stakes":
            type_boost += weights.get("stakes_boost", 0.15) * memory.emotion
        elif memory.emotion_type == "resonance":
            type_boost += weights.get("resonance_boost", 0.1) * memory.emotion

        emotion_factor = 1 + weights.get("emotion_weight", 0.0) * memory.emotion
        effective_importance = base_importance * emotion_factor * type_boost

        return memory.similarity * effective_importance


class NoDecay:
    """Default decay: Perfect Recall -- nothing decays or archives."""

    async def apply_decay(self, memory: MemoryDocument, now: datetime) -> float:
        return memory.importance

    def should_archive(self, memory: MemoryDocument) -> bool:
        return False


class LLMImportance:
    """Default importance: delegates to the service's ``_llm_completion``.

    This is a thin wrapper that preserves the existing LLM-based importance
    assessment.  It requires a reference to the memory service for LLM access.
    """

    def __init__(self, *, llm_completion_fn: Any = None, model: str | None = None) -> None:
        self._llm_fn = llm_completion_fn
        self._model = model

    async def assess(self, text: str, context: ImportanceContext) -> float:
        if self._llm_fn is None:
            return 0.5

        prompt = (
            "On a scale of 1-10, rate the importance of remembering this information "
            "long-term. Consider factors like: uniqueness of information, "
            "actionability, personal significance, and whether it contains key facts "
            "or decisions. Respond with just a number.\n\n"
            f"Text to evaluate: {text}"
        )
        try:
            response = await self._llm_fn(
                messages=[{"role": "user", "content": prompt}],
                model=self._model,
            )
            rating_text = response.choices[0].message.content
            rating = float("".join(c for c in rating_text if c.isdigit() or c == "."))
            return min(max(rating / 10.0, 0.1), 1.0)
        except (RuntimeError, ValueError, TypeError, AttributeError, OSError, ConnectionError):
            logger.warning("LLM importance assessment failed, using default 0.5")
            return 0.5


class WeightedPersonaBlend:
    """Default persona blending: 80% query / 20% persona, normalised.

    Args:
        query_weight: Weight for the query vector (default 0.8).
        persona_weight: Weight for the persona vector (default 0.2).
    """

    def __init__(
        self,
        query_weight: float = 0.8,
        persona_weight: float = 0.2,
    ) -> None:
        self.query_weight = query_weight
        self.persona_weight = persona_weight

    async def blend(
        self,
        query_vector: list[float],
        persona_vector: list[float],
        config: dict[str, Any],
    ) -> list[float]:
        qw = config.get("query_weight", self.query_weight)
        pw = config.get("persona_weight", self.persona_weight)

        blended = [qw * q + pw * p for q, p in zip(query_vector, persona_vector, strict=False)]
        magnitude = math.sqrt(sum(x * x for x in blended))
        if magnitude > 0:
            blended = [x / magnitude for x in blended]
        return blended


class TimeCountReflection:
    """Default reflection: time-based and count-based triggers.

    Args:
        interval_hours: Hours between reflections (default 24).
        message_threshold: Memory count to trigger reflection (default 50).
    """

    def __init__(
        self,
        interval_hours: float = 24.0,
        message_threshold: int = 50,
    ) -> None:
        self.interval_hours = interval_hours
        self.message_threshold = message_threshold

    async def should_trigger(self, user_id: str, stats: ReflectionStats) -> tuple[bool, str]:
        if stats.last_reflection_at is not None:
            hours_since = (datetime.now(timezone.utc) - stats.last_reflection_at).total_seconds() / 3600
            if hours_since < self.interval_hours:
                return False, f"Last reflection was {hours_since:.1f}h ago"
            return True, f"Time-based trigger: {hours_since:.1f}h since last reflection"

        if stats.recent_memory_count >= self.message_threshold:
            return (
                True,
                f"Memory count ({stats.recent_memory_count}) exceeds threshold ({self.message_threshold})",
            )

        if stats.total_memory_count >= self.message_threshold:
            return True, "No previous reflection and enough memories accumulated"

        return False, "No reflection trigger conditions met"

    async def consolidate(self, memories: list[MemoryDocument], context: ReflectionContext) -> ConsolidationResult:
        return ConsolidationResult(
            summary=None,
            memories_processed=len(memories),
        )


# ============================================================================
# Built-in Alternative Strategies
# ============================================================================


class RecencyDecayScoring:
    """Scoring with exponential recency decay.

    ``score = similarity * importance * (1 + ln(access_count + 1))
              * emotion_factor * decay``

    where ``decay = 0.5 ** (age_hours / half_life_hours)``.

    Args:
        half_life_hours: Number of hours for importance to halve (default 72).
    """

    def __init__(self, half_life_hours: float = 72.0) -> None:
        self.half_life_hours = half_life_hours

    async def score(self, memory: MemoryDocument, query_context: QueryContext) -> float:
        weights = query_context.scoring_weights

        base_importance = memory.importance * (1 + math.log(memory.access_count + 1))

        type_boost = 1.0
        if memory.emotion_type == "novelty":
            type_boost += weights.get("novelty_boost", 0.1) * memory.emotion
        elif memory.emotion_type == "stakes":
            type_boost += weights.get("stakes_boost", 0.15) * memory.emotion
        elif memory.emotion_type == "resonance":
            type_boost += weights.get("resonance_boost", 0.1) * memory.emotion

        emotion_factor = 1 + weights.get("emotion_weight", 0.0) * memory.emotion
        effective_importance = base_importance * emotion_factor * type_boost

        # Apply recency decay
        decay = 1.0
        if memory.created_at is not None:
            age_hours = (query_context.now - memory.created_at).total_seconds() / 3600
            if age_hours > 0 and self.half_life_hours > 0:
                decay = 0.5 ** (age_hours / self.half_life_hours)

        return memory.similarity * effective_importance * decay


class ExponentialDecay:
    """Exponential importance decay with configurable half-life.

    Memories lose half their importance every ``half_life_hours``.
    Memories below ``archive_threshold`` are eligible for archival.

    Args:
        half_life_hours: Hours for importance to halve (default 168 = 1 week).
        archive_threshold: Importance below which archival is suggested (default 0.05).
    """

    def __init__(
        self,
        half_life_hours: float = 168.0,
        archive_threshold: float = 0.05,
    ) -> None:
        self.half_life_hours = half_life_hours
        self.archive_threshold = archive_threshold

    async def apply_decay(self, memory: MemoryDocument, now: datetime) -> float:
        if memory.created_at is None:
            return memory.importance

        age_hours = (now - memory.created_at).total_seconds() / 3600
        if age_hours <= 0 or self.half_life_hours <= 0:
            return memory.importance

        decay_factor = 0.5 ** (age_hours / self.half_life_hours)
        return memory.importance * decay_factor

    def should_archive(self, memory: MemoryDocument) -> bool:
        if memory.created_at is None:
            return False
        now = datetime.now(timezone.utc)
        age_hours = (now - memory.created_at).total_seconds() / 3600
        if age_hours <= 0 or self.half_life_hours <= 0:
            return False
        decayed = memory.importance * (0.5 ** (age_hours / self.half_life_hours))
        return decayed < self.archive_threshold


class LinearDecay:
    """Linear importance decay.

    Importance decreases linearly from its original value to zero over
    ``lifetime_hours``.

    Args:
        lifetime_hours: Hours until importance reaches zero (default 720 = 30 days).
        archive_threshold: Importance below which archival is suggested (default 0.05).
    """

    def __init__(
        self,
        lifetime_hours: float = 720.0,
        archive_threshold: float = 0.05,
    ) -> None:
        self.lifetime_hours = lifetime_hours
        self.archive_threshold = archive_threshold

    async def apply_decay(self, memory: MemoryDocument, now: datetime) -> float:
        if memory.created_at is None:
            return memory.importance

        age_hours = (now - memory.created_at).total_seconds() / 3600
        if age_hours <= 0 or self.lifetime_hours <= 0:
            return memory.importance

        fraction_remaining = max(0.0, 1.0 - age_hours / self.lifetime_hours)
        return memory.importance * fraction_remaining

    def should_archive(self, memory: MemoryDocument) -> bool:
        if memory.created_at is None:
            return False
        now = datetime.now(timezone.utc)
        age_hours = (now - memory.created_at).total_seconds() / 3600
        if age_hours <= 0 or self.lifetime_hours <= 0:
            return False
        fraction_remaining = max(0.0, 1.0 - age_hours / self.lifetime_hours)
        return memory.importance * fraction_remaining < self.archive_threshold


class RuleBasedImportance:
    """Keyword/heuristic importance assessment (no LLM calls).

    Uses keyword matching to estimate importance -- useful for
    cost-sensitive deployments or when LLM is unavailable.

    Args:
        default_importance: Base importance when no keywords match (default 0.5).
    """

    _HIGH_IMPORTANCE_KEYWORDS = frozenset(
        [
            "name",
            "born",
            "birthday",
            "age",
            "married",
            "divorced",
            "children",
            "died",
            "allergic",
            "allergy",
            "medication",
            "password",
            "secret",
            "important",
            "critical",
            "urgent",
            "deadline",
            "emergency",
            "surgery",
            "diagnosis",
        ]
    )
    _MEDIUM_IMPORTANCE_KEYWORDS = frozenset(
        [
            "likes",
            "loves",
            "prefers",
            "favorite",
            "favourite",
            "hates",
            "dislikes",
            "hobby",
            "job",
            "works",
            "lives",
            "moved",
            "started",
            "graduated",
            "studied",
            "majored",
            "friend",
            "sister",
            "brother",
            "mother",
            "father",
        ]
    )

    def __init__(self, default_importance: float = 0.5) -> None:
        self.default_importance = default_importance

    async def assess(self, text: str, context: ImportanceContext) -> float:
        if not text:
            return self.default_importance

        text_lower = text.lower()
        words = set(text_lower.split())

        if words & self._HIGH_IMPORTANCE_KEYWORDS:
            return 0.9
        if words & self._MEDIUM_IMPORTANCE_KEYWORDS:
            return 0.7
        return self.default_importance


class CustomWeightPersonaBlend:
    """Persona blending with user-configurable weights.

    Unlike ``WeightedPersonaBlend``, this reads weights from the config
    dict passed to ``blend()`` at call time, allowing per-request tuning.

    Falls back to the provided constructor defaults.
    """

    def __init__(
        self,
        default_query_weight: float = 0.7,
        default_persona_weight: float = 0.3,
    ) -> None:
        self.default_query_weight = default_query_weight
        self.default_persona_weight = default_persona_weight

    async def blend(
        self,
        query_vector: list[float],
        persona_vector: list[float],
        config: dict[str, Any],
    ) -> list[float]:
        qw = config.get("query_weight", self.default_query_weight)
        pw = config.get("persona_weight", self.default_persona_weight)

        blended = [qw * q + pw * p for q, p in zip(query_vector, persona_vector, strict=False)]
        magnitude = math.sqrt(sum(x * x for x in blended))
        if magnitude > 0:
            blended = [x / magnitude for x in blended]
        return blended


# ============================================================================
# Strategy Registry (for manifest-based resolution)
# ============================================================================

SCORING_STRATEGIES: dict[str, type] = {
    "perfect_recall": PerfectRecallScoring,
    "recency_decay": RecencyDecayScoring,
}

DECAY_STRATEGIES: dict[str, type] = {
    "none": NoDecay,
    "exponential": ExponentialDecay,
    "linear": LinearDecay,
}

IMPORTANCE_STRATEGIES: dict[str, type] = {
    "llm": LLMImportance,
    "rule_based": RuleBasedImportance,
}

PERSONA_STRATEGIES: dict[str, type] = {
    "weighted": WeightedPersonaBlend,
    "custom_weight": CustomWeightPersonaBlend,
}

REFLECTION_STRATEGIES: dict[str, type] = {
    "time_count": TimeCountReflection,
}


def resolve_strategy_from_config(
    registry: dict[str, type],
    config: dict[str, Any],
    default_key: str,
) -> Any:
    """Resolve a strategy instance from a manifest config block.

    Args:
        registry: Mapping of strategy name -> class.
        config: Config dict, expected to have a ``strategy`` key.
        default_key: Default strategy name if not specified.

    Returns:
        Instantiated strategy.

    Raises:
        ValueError: If the requested strategy is not in the registry.
    """
    strategy_name = config.get("strategy", default_key)
    cls = registry.get(strategy_name)
    if cls is None:
        available = ", ".join(sorted(registry.keys()))
        raise ValueError(f"Unknown strategy '{strategy_name}'. Available: {available}")

    # Pass remaining config keys (excluding 'strategy') as constructor kwargs
    kwargs = {k: v for k, v in config.items() if k != "strategy"}
    try:
        return cls(**kwargs)
    except TypeError as e:
        logger.warning(f"Could not pass config kwargs to {cls.__name__}: {e}. " f"Falling back to defaults.")
        return cls()
