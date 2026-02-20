"""
Unit tests for the Pluggable Strategy API.

Tests cover:
- Strategy protocol conformance (structural typing)
- Default implementations (PerfectRecallScoring, NoDecay, etc.)
- Built-in alternatives (RecencyDecayScoring, ExponentialDecay, etc.)
- Custom user-defined strategies (no inheritance needed)
- Manifest-based strategy resolution
- Builder integration with strategy injection
- Context dataclasses
"""

import math
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from mdb_engine.memory.strategies import (
    # Registry helpers
    DECAY_STRATEGIES,
    IMPORTANCE_STRATEGIES,
    PERSONA_STRATEGIES,
    SCORING_STRATEGIES,
    # Context dataclasses
    ConsolidationResult,
    # Built-in alternatives
    CustomWeightPersonaBlend,
    # Protocols
    DecayStrategy,
    ExponentialDecay,
    ExtractedFact,
    ExtractionContext,
    ExtractionStrategy,
    ImportanceContext,
    ImportanceStrategy,
    LinearDecay,
    # Default implementations
    LLMImportance,
    MemoryDocument,
    NoDecay,
    PerfectRecallScoring,
    PersonaStrategy,
    QueryContext,
    RecencyDecayScoring,
    ReflectionContext,
    ReflectionStats,
    ReflectionStrategy,
    RuleBasedImportance,
    ScoringStrategy,
    TimeCountReflection,
    WeightedPersonaBlend,
    resolve_strategy_from_config,
)

# ============================================================================
# Helpers
# ============================================================================


def _make_memory(
    *,
    similarity: float = 0.9,
    importance: float = 0.7,
    access_count: int = 3,
    emotion: float = 0.3,
    emotion_type: str = "neutral",
    created_at: datetime | None = None,
) -> MemoryDocument:
    return MemoryDocument(
        id="mem_1",
        text="User likes pizza",
        similarity=similarity,
        importance=importance,
        access_count=access_count,
        emotion=emotion,
        emotion_type=emotion_type,
        created_at=created_at or datetime.now(timezone.utc),
    )


def _make_query_ctx(
    scoring_weights: dict | None = None,
    now: datetime | None = None,
) -> QueryContext:
    return QueryContext(
        now=now or datetime.now(timezone.utc),
        user_id="user_1",
        scoring_weights=scoring_weights or {},
    )


# ============================================================================
# MemoryDocument Tests
# ============================================================================


class TestMemoryDocument:
    def test_from_doc_basic(self):
        doc = {
            "_id": "abc123",
            "text": "User loves Python",
            "similarity": 0.95,
            "importance": 0.8,
            "access_count": 5,
            "emotion": 0.6,
            "emotion_type": "resonance",
            "category": "preferences",
            "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "metadata": {"bucket_id": "b1"},
        }
        mem = MemoryDocument.from_doc(doc)
        assert mem.id == "abc123"
        assert mem.text == "User loves Python"
        assert mem.similarity == 0.95
        assert mem.importance == 0.8
        assert mem.access_count == 5
        assert mem.emotion == 0.6
        assert mem.emotion_type == "resonance"
        assert mem.category == "preferences"
        assert mem.metadata == {"bucket_id": "b1"}

    def test_from_doc_defaults(self):
        mem = MemoryDocument.from_doc({})
        assert mem.id == ""
        assert mem.text == ""
        assert mem.similarity == 0.0
        assert mem.importance == 0.5
        assert mem.access_count == 0
        assert mem.emotion == 0.3
        assert mem.emotion_type == "neutral"
        assert mem.category is None

    def test_from_doc_search_result_format(self):
        """Search results use 'id' and 'memory' keys instead of '_id' and 'text'."""
        doc = {"id": "xyz", "memory": "User hates bugs"}
        mem = MemoryDocument.from_doc(doc)
        assert mem.id == "xyz"
        assert mem.text == "User hates bugs"


# ============================================================================
# PerfectRecallScoring Tests
# ============================================================================


class TestPerfectRecallScoring:
    @pytest.mark.asyncio
    async def test_basic_scoring(self):
        strategy = PerfectRecallScoring()
        mem = _make_memory(similarity=0.9, importance=0.7, access_count=3)
        ctx = _make_query_ctx()

        score = await strategy.score(mem, ctx)
        expected = 0.9 * 0.7 * (1 + math.log(4))
        assert score == pytest.approx(expected, rel=1e-6)

    @pytest.mark.asyncio
    async def test_zero_access_count(self):
        strategy = PerfectRecallScoring()
        mem = _make_memory(access_count=0)
        ctx = _make_query_ctx()

        score = await strategy.score(mem, ctx)
        expected = 0.9 * 0.7 * (1 + math.log(1))
        assert score == pytest.approx(expected, rel=1e-6)

    @pytest.mark.asyncio
    async def test_emotion_novelty_boost(self):
        strategy = PerfectRecallScoring()
        mem = _make_memory(emotion=0.8, emotion_type="novelty")
        ctx = _make_query_ctx(scoring_weights={"novelty_boost": 0.2})

        score = await strategy.score(mem, ctx)
        base = 0.7 * (1 + math.log(4))
        type_boost = 1.0 + 0.2 * 0.8
        assert score == pytest.approx(0.9 * base * type_boost, rel=1e-6)

    @pytest.mark.asyncio
    async def test_emotion_stakes_boost(self):
        strategy = PerfectRecallScoring()
        mem = _make_memory(emotion=0.9, emotion_type="stakes")
        ctx = _make_query_ctx(scoring_weights={"stakes_boost": 0.3})

        score = await strategy.score(mem, ctx)
        base = 0.7 * (1 + math.log(4))
        type_boost = 1.0 + 0.3 * 0.9
        assert score == pytest.approx(0.9 * base * type_boost, rel=1e-6)

    @pytest.mark.asyncio
    async def test_emotion_weight(self):
        strategy = PerfectRecallScoring()
        mem = _make_memory(emotion=0.5)
        ctx = _make_query_ctx(scoring_weights={"emotion_weight": 0.4})

        score = await strategy.score(mem, ctx)
        base = 0.7 * (1 + math.log(4))
        emotion_factor = 1 + 0.4 * 0.5
        assert score == pytest.approx(0.9 * base * emotion_factor, rel=1e-6)

    @pytest.mark.asyncio
    async def test_protocol_conformance(self):
        assert isinstance(PerfectRecallScoring(), ScoringStrategy)


# ============================================================================
# RecencyDecayScoring Tests
# ============================================================================


class TestRecencyDecayScoring:
    @pytest.mark.asyncio
    async def test_recent_memory_no_decay(self):
        strategy = RecencyDecayScoring(half_life_hours=72)
        now = datetime.now(timezone.utc)
        mem = _make_memory(created_at=now)
        ctx = _make_query_ctx(now=now)

        score = await strategy.score(mem, ctx)
        base = 0.9 * 0.7 * (1 + math.log(4))
        assert score == pytest.approx(base, rel=1e-3)

    @pytest.mark.asyncio
    async def test_old_memory_decays(self):
        strategy = RecencyDecayScoring(half_life_hours=72)
        now = datetime.now(timezone.utc)
        old = now - timedelta(hours=72)
        mem = _make_memory(created_at=old)
        ctx = _make_query_ctx(now=now)

        score = await strategy.score(mem, ctx)
        base = 0.9 * 0.7 * (1 + math.log(4))
        # At exactly 1 half-life, decay = 0.5
        assert score == pytest.approx(base * 0.5, rel=1e-3)

    @pytest.mark.asyncio
    async def test_very_old_memory_nearly_zero(self):
        strategy = RecencyDecayScoring(half_life_hours=24)
        now = datetime.now(timezone.utc)
        old = now - timedelta(hours=240)  # 10 half-lives
        mem = _make_memory(created_at=old)
        ctx = _make_query_ctx(now=now)

        score = await strategy.score(mem, ctx)
        assert score < 0.01

    @pytest.mark.asyncio
    async def test_protocol_conformance(self):
        assert isinstance(RecencyDecayScoring(), ScoringStrategy)


# ============================================================================
# NoDecay Tests
# ============================================================================


class TestNoDecay:
    @pytest.mark.asyncio
    async def test_no_decay(self):
        strategy = NoDecay()
        mem = _make_memory(importance=0.8)
        result = await strategy.apply_decay(mem, datetime.now(timezone.utc))
        assert result == 0.8

    def test_never_archives(self):
        strategy = NoDecay()
        mem = _make_memory()
        assert strategy.should_archive(mem) is False

    @pytest.mark.asyncio
    async def test_protocol_conformance(self):
        assert isinstance(NoDecay(), DecayStrategy)


# ============================================================================
# ExponentialDecay Tests
# ============================================================================


class TestExponentialDecay:
    @pytest.mark.asyncio
    async def test_no_decay_at_creation(self):
        strategy = ExponentialDecay(half_life_hours=168)
        now = datetime.now(timezone.utc)
        mem = _make_memory(importance=0.8, created_at=now)
        result = await strategy.apply_decay(mem, now)
        assert result == pytest.approx(0.8, rel=1e-6)

    @pytest.mark.asyncio
    async def test_half_decay_at_half_life(self):
        strategy = ExponentialDecay(half_life_hours=168)
        now = datetime.now(timezone.utc)
        created = now - timedelta(hours=168)
        mem = _make_memory(importance=0.8, created_at=created)
        result = await strategy.apply_decay(mem, now)
        assert result == pytest.approx(0.4, rel=1e-3)

    def test_archive_below_threshold(self):
        strategy = ExponentialDecay(half_life_hours=24, archive_threshold=0.05)
        very_old = datetime.now(timezone.utc) - timedelta(hours=240)
        mem = _make_memory(importance=0.5, created_at=very_old)
        assert strategy.should_archive(mem) is True

    def test_no_archive_recent(self):
        strategy = ExponentialDecay(half_life_hours=168)
        recent = datetime.now(timezone.utc) - timedelta(hours=1)
        mem = _make_memory(importance=0.8, created_at=recent)
        assert strategy.should_archive(mem) is False

    @pytest.mark.asyncio
    async def test_none_created_at(self):
        strategy = ExponentialDecay(half_life_hours=168)
        mem = _make_memory(importance=0.7, created_at=None)
        result = await strategy.apply_decay(mem, datetime.now(timezone.utc))
        assert result == pytest.approx(0.7)

    @pytest.mark.asyncio
    async def test_protocol_conformance(self):
        assert isinstance(ExponentialDecay(), DecayStrategy)


# ============================================================================
# LinearDecay Tests
# ============================================================================


class TestLinearDecay:
    @pytest.mark.asyncio
    async def test_no_decay_at_creation(self):
        strategy = LinearDecay(lifetime_hours=720)
        now = datetime.now(timezone.utc)
        mem = _make_memory(importance=0.6, created_at=now)
        result = await strategy.apply_decay(mem, now)
        assert result == pytest.approx(0.6)

    @pytest.mark.asyncio
    async def test_half_decay_at_midpoint(self):
        strategy = LinearDecay(lifetime_hours=720)
        now = datetime.now(timezone.utc)
        created = now - timedelta(hours=360)
        mem = _make_memory(importance=0.6, created_at=created)
        result = await strategy.apply_decay(mem, now)
        assert result == pytest.approx(0.3, rel=1e-3)

    @pytest.mark.asyncio
    async def test_zero_at_end_of_life(self):
        strategy = LinearDecay(lifetime_hours=720)
        now = datetime.now(timezone.utc)
        created = now - timedelta(hours=720)
        mem = _make_memory(importance=0.6, created_at=created)
        result = await strategy.apply_decay(mem, now)
        assert result == pytest.approx(0.0, abs=1e-6)

    @pytest.mark.asyncio
    async def test_clamped_past_lifetime(self):
        strategy = LinearDecay(lifetime_hours=100)
        now = datetime.now(timezone.utc)
        created = now - timedelta(hours=200)
        mem = _make_memory(importance=0.5, created_at=created)
        result = await strategy.apply_decay(mem, now)
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_protocol_conformance(self):
        assert isinstance(LinearDecay(), DecayStrategy)


# ============================================================================
# LLMImportance Tests
# ============================================================================


class TestLLMImportance:
    @pytest.mark.asyncio
    async def test_default_when_no_llm(self):
        strategy = LLMImportance(llm_completion_fn=None)
        ctx = ImportanceContext(app_slug="test")
        result = await strategy.assess("some text", ctx)
        assert result == 0.5

    @pytest.mark.asyncio
    async def test_llm_assessment(self):
        from types import SimpleNamespace

        mock_resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="8"))])
        mock_fn = AsyncMock(return_value=mock_resp)
        strategy = LLMImportance(llm_completion_fn=mock_fn, model="test-model")
        ctx = ImportanceContext(app_slug="test")

        result = await strategy.assess("User's name is John", ctx)
        assert result == pytest.approx(0.8)
        mock_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_failure_returns_default(self):
        mock_fn = AsyncMock(side_effect=RuntimeError("LLM failed"))
        strategy = LLMImportance(llm_completion_fn=mock_fn, model="test-model")
        ctx = ImportanceContext(app_slug="test")

        result = await strategy.assess("test", ctx)
        assert result == 0.5

    @pytest.mark.asyncio
    async def test_protocol_conformance(self):
        assert isinstance(LLMImportance(), ImportanceStrategy)


# ============================================================================
# RuleBasedImportance Tests
# ============================================================================


class TestRuleBasedImportance:
    @pytest.mark.asyncio
    async def test_high_importance_keyword(self):
        strategy = RuleBasedImportance()
        ctx = ImportanceContext(app_slug="test")
        result = await strategy.assess("User is allergic to peanuts", ctx)
        assert result == 0.9

    @pytest.mark.asyncio
    async def test_medium_importance_keyword(self):
        strategy = RuleBasedImportance()
        ctx = ImportanceContext(app_slug="test")
        result = await strategy.assess("User loves hiking", ctx)
        assert result == 0.7

    @pytest.mark.asyncio
    async def test_default_importance(self):
        strategy = RuleBasedImportance(default_importance=0.4)
        ctx = ImportanceContext(app_slug="test")
        result = await strategy.assess("something generic happened", ctx)
        assert result == 0.4

    @pytest.mark.asyncio
    async def test_empty_text(self):
        strategy = RuleBasedImportance()
        ctx = ImportanceContext(app_slug="test")
        result = await strategy.assess("", ctx)
        assert result == 0.5

    @pytest.mark.asyncio
    async def test_protocol_conformance(self):
        assert isinstance(RuleBasedImportance(), ImportanceStrategy)


# ============================================================================
# WeightedPersonaBlend Tests
# ============================================================================


class TestWeightedPersonaBlend:
    @pytest.mark.asyncio
    async def test_default_8020_blend(self):
        strategy = WeightedPersonaBlend()
        q = [1.0, 0.0, 0.0]
        p = [0.0, 1.0, 0.0]
        result = await strategy.blend(q, p, {})
        magnitude = math.sqrt(0.8**2 + 0.2**2)
        expected = [0.8 / magnitude, 0.2 / magnitude, 0.0]
        for r, e in zip(result, expected, strict=False):
            assert r == pytest.approx(e, rel=1e-6)

    @pytest.mark.asyncio
    async def test_config_override(self):
        strategy = WeightedPersonaBlend()
        q = [1.0, 0.0]
        p = [0.0, 1.0]
        result = await strategy.blend(q, p, {"query_weight": 0.5, "persona_weight": 0.5})
        # 50/50 blend, normalised
        magnitude = math.sqrt(0.5**2 + 0.5**2)
        assert result[0] == pytest.approx(0.5 / magnitude, rel=1e-6)
        assert result[1] == pytest.approx(0.5 / magnitude, rel=1e-6)

    @pytest.mark.asyncio
    async def test_protocol_conformance(self):
        assert isinstance(WeightedPersonaBlend(), PersonaStrategy)


# ============================================================================
# CustomWeightPersonaBlend Tests
# ============================================================================


class TestCustomWeightPersonaBlend:
    @pytest.mark.asyncio
    async def test_custom_defaults(self):
        strategy = CustomWeightPersonaBlend(
            default_query_weight=0.6,
            default_persona_weight=0.4,
        )
        q = [1.0, 0.0]
        p = [0.0, 1.0]
        result = await strategy.blend(q, p, {})
        magnitude = math.sqrt(0.6**2 + 0.4**2)
        assert result[0] == pytest.approx(0.6 / magnitude, rel=1e-6)
        assert result[1] == pytest.approx(0.4 / magnitude, rel=1e-6)

    @pytest.mark.asyncio
    async def test_protocol_conformance(self):
        assert isinstance(CustomWeightPersonaBlend(), PersonaStrategy)


# ============================================================================
# TimeCountReflection Tests
# ============================================================================


class TestTimeCountReflection:
    @pytest.mark.asyncio
    async def test_time_trigger(self):
        strategy = TimeCountReflection(interval_hours=24)
        stats = ReflectionStats(
            last_reflection_at=datetime.now(timezone.utc) - timedelta(hours=25),
            recent_memory_count=5,
        )
        should, reason = await strategy.should_trigger("user1", stats)
        assert should is True
        assert "Time-based trigger" in reason

    @pytest.mark.asyncio
    async def test_too_recent_no_trigger(self):
        strategy = TimeCountReflection(interval_hours=24)
        stats = ReflectionStats(
            last_reflection_at=datetime.now(timezone.utc) - timedelta(hours=2),
            recent_memory_count=5,
        )
        should, reason = await strategy.should_trigger("user1", stats)
        assert should is False

    @pytest.mark.asyncio
    async def test_count_trigger(self):
        strategy = TimeCountReflection(message_threshold=10)
        stats = ReflectionStats(
            last_reflection_at=None,
            recent_memory_count=15,
        )
        should, reason = await strategy.should_trigger("user1", stats)
        assert should is True
        assert "exceeds threshold" in reason

    @pytest.mark.asyncio
    async def test_no_trigger_conditions(self):
        strategy = TimeCountReflection(interval_hours=24, message_threshold=50)
        stats = ReflectionStats(
            last_reflection_at=None,
            recent_memory_count=5,
            total_memory_count=5,
        )
        should, reason = await strategy.should_trigger("user1", stats)
        assert should is False

    @pytest.mark.asyncio
    async def test_first_reflection_total_count(self):
        strategy = TimeCountReflection(message_threshold=10)
        stats = ReflectionStats(
            last_reflection_at=None,
            recent_memory_count=5,
            total_memory_count=15,
        )
        should, reason = await strategy.should_trigger("user1", stats)
        assert should is True
        assert "No previous reflection" in reason

    @pytest.mark.asyncio
    async def test_consolidate_returns_result(self):
        strategy = TimeCountReflection()
        memories = [_make_memory(), _make_memory()]
        ctx = ReflectionContext(app_slug="test", user_id="user1")
        result = await strategy.consolidate(memories, ctx)
        assert isinstance(result, ConsolidationResult)
        assert result.memories_processed == 2

    @pytest.mark.asyncio
    async def test_protocol_conformance(self):
        assert isinstance(TimeCountReflection(), ReflectionStrategy)


# ============================================================================
# Strategy Registry and Resolution Tests
# ============================================================================


class TestStrategyResolution:
    def test_scoring_registry_keys(self):
        assert "perfect_recall" in SCORING_STRATEGIES
        assert "recency_decay" in SCORING_STRATEGIES

    def test_decay_registry_keys(self):
        assert "none" in DECAY_STRATEGIES
        assert "exponential" in DECAY_STRATEGIES
        assert "linear" in DECAY_STRATEGIES

    def test_importance_registry_keys(self):
        assert "llm" in IMPORTANCE_STRATEGIES
        assert "rule_based" in IMPORTANCE_STRATEGIES

    def test_persona_registry_keys(self):
        assert "weighted" in PERSONA_STRATEGIES
        assert "custom_weight" in PERSONA_STRATEGIES

    def test_resolve_default(self):
        result = resolve_strategy_from_config(SCORING_STRATEGIES, {}, "perfect_recall")
        assert isinstance(result, PerfectRecallScoring)

    def test_resolve_explicit(self):
        result = resolve_strategy_from_config(SCORING_STRATEGIES, {"strategy": "recency_decay"}, "perfect_recall")
        assert isinstance(result, RecencyDecayScoring)

    def test_resolve_with_kwargs(self):
        result = resolve_strategy_from_config(
            DECAY_STRATEGIES,
            {"strategy": "exponential", "half_life_hours": 48},
            "none",
        )
        assert isinstance(result, ExponentialDecay)
        assert result.half_life_hours == 48

    def test_resolve_unknown_strategy(self):
        with pytest.raises(ValueError, match="Unknown strategy"):
            resolve_strategy_from_config(SCORING_STRATEGIES, {"strategy": "nonexistent"}, "perfect_recall")

    def test_resolve_bad_kwargs_fallback(self):
        result = resolve_strategy_from_config(
            SCORING_STRATEGIES,
            {"strategy": "perfect_recall", "bogus_param": 42},
            "perfect_recall",
        )
        assert isinstance(result, PerfectRecallScoring)


# ============================================================================
# Custom Strategy (Protocol Structural Typing) Tests
# ============================================================================


class TestCustomStrategy:
    """Users can implement strategies without inheriting from anything."""

    @pytest.mark.asyncio
    async def test_custom_scoring(self):
        class MyScoring:
            async def score(self, memory, query_context):
                return memory.similarity * 42

        strategy = MyScoring()
        assert isinstance(strategy, ScoringStrategy)

        mem = _make_memory(similarity=0.5)
        ctx = _make_query_ctx()
        result = await strategy.score(mem, ctx)
        assert result == pytest.approx(21.0)

    @pytest.mark.asyncio
    async def test_custom_decay(self):
        class ConstantDecay:
            async def apply_decay(self, memory, now):
                return 0.1

            def should_archive(self, memory):
                return True

        strategy = ConstantDecay()
        assert isinstance(strategy, DecayStrategy)
        mem = _make_memory()
        assert await strategy.apply_decay(mem, datetime.now(timezone.utc)) == 0.1
        assert strategy.should_archive(mem) is True

    @pytest.mark.asyncio
    async def test_custom_importance(self):
        class AlwaysHigh:
            async def assess(self, text, context):
                return 0.95

        strategy = AlwaysHigh()
        assert isinstance(strategy, ImportanceStrategy)
        ctx = ImportanceContext(app_slug="test")
        assert await strategy.assess("anything", ctx) == 0.95

    @pytest.mark.asyncio
    async def test_custom_persona(self):
        class NoPersona:
            async def blend(self, query_vector, persona_vector, config):
                return query_vector

        strategy = NoPersona()
        assert isinstance(strategy, PersonaStrategy)
        result = await strategy.blend([1, 2, 3], [4, 5, 6], {})
        assert result == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_custom_extraction(self):
        class SimpleExtractor:
            async def extract(self, text, context):
                return [ExtractedFact(text=text, category="biographical")]

        strategy = SimpleExtractor()
        assert isinstance(strategy, ExtractionStrategy)
        ctx = ExtractionContext(app_slug="test")
        facts = await strategy.extract("User is 30", ctx)
        assert len(facts) == 1
        assert facts[0].text == "User is 30"

    @pytest.mark.asyncio
    async def test_custom_reflection(self):
        class AlwaysReflect:
            async def should_trigger(self, user_id, stats):
                return True, "Always reflect"

            async def consolidate(self, memories, context):
                return ConsolidationResult(
                    summary="Consolidated",
                    memories_processed=len(memories),
                )

        strategy = AlwaysReflect()
        assert isinstance(strategy, ReflectionStrategy)
        should, reason = await strategy.should_trigger("u1", ReflectionStats())
        assert should is True
        result = await strategy.consolidate([], ReflectionContext(app_slug="t", user_id="u1"))
        assert result.summary == "Consolidated"


# ============================================================================
# Context Dataclass Tests
# ============================================================================


class TestContextDataclasses:
    def test_extraction_context_defaults(self):
        ctx = ExtractionContext(app_slug="my_app")
        assert ctx.categories_enabled is True
        assert ctx.custom_categories == []
        assert ctx.memory_types_enabled is True
        assert ctx.auto_detect_memory_type is True

    def test_extracted_fact_defaults(self):
        fact = ExtractedFact(text="User likes cats")
        assert fact.category == "biographical"
        assert fact.emotion == 0.3
        assert fact.emotion_type == "neutral"

    def test_query_context_defaults(self):
        ctx = QueryContext(now=datetime.now(timezone.utc))
        assert ctx.user_id is None
        assert ctx.scoring_weights == {}

    def test_reflection_stats_defaults(self):
        stats = ReflectionStats()
        assert stats.last_reflection_at is None
        assert stats.recent_memory_count == 0
        assert stats.total_memory_count == 0

    def test_consolidation_result_defaults(self):
        result = ConsolidationResult()
        assert result.summary is None
        assert result.entities_extracted == 0
        assert result.memories_processed == 0
        assert result.metadata == {}


# ============================================================================
# Builder Integration Tests (using mocks)
# ============================================================================


class TestBuilderStrategyIntegration:
    """Test that the builder correctly resolves and passes strategies."""

    def test_resolve_strategies_defaults(self):
        """Builder should resolve defaults when no strategies are provided."""
        from mdb_engine.memory.builder import CognitiveMemoryServiceBuilder

        # Create a minimal mock collection
        mock_collection = MagicMock()
        mock_collection.database = MagicMock()
        mock_collection.database.client = MagicMock()
        mock_collection.database.name = "test_db"

        builder = CognitiveMemoryServiceBuilder(
            app_slug="test",
            config={},
            collection=mock_collection,
        )

        strategies = builder._resolve_strategies(
            injected_llm_service=None,
            memory_llm_model="openai/gpt-4o",
        )

        assert isinstance(strategies["scoring_strategy"], PerfectRecallScoring)
        assert isinstance(strategies["decay_strategy"], NoDecay)
        assert isinstance(strategies["importance_strategy"], LLMImportance)
        assert isinstance(strategies["persona_strategy"], WeightedPersonaBlend)
        assert isinstance(strategies["reflection_strategy"], TimeCountReflection)
        assert strategies["extraction_strategy"] is None

    def test_resolve_strategies_injected(self):
        """Builder should prefer injected strategies over defaults."""
        from mdb_engine.memory.builder import CognitiveMemoryServiceBuilder

        mock_collection = MagicMock()
        mock_collection.database = MagicMock()
        mock_collection.database.client = MagicMock()
        mock_collection.database.name = "test_db"

        custom_scoring = RecencyDecayScoring(half_life_hours=48)
        custom_decay = ExponentialDecay(half_life_hours=100)

        builder = CognitiveMemoryServiceBuilder(
            app_slug="test",
            config={},
            collection=mock_collection,
            scoring_strategy=custom_scoring,
            decay_strategy=custom_decay,
        )

        strategies = builder._resolve_strategies(
            injected_llm_service=None,
            memory_llm_model="openai/gpt-4o",
        )

        assert strategies["scoring_strategy"] is custom_scoring
        assert strategies["decay_strategy"] is custom_decay

    def test_resolve_strategies_from_config(self):
        """Builder should resolve strategies from manifest config."""
        from mdb_engine.memory.builder import CognitiveMemoryServiceBuilder

        mock_collection = MagicMock()
        mock_collection.database = MagicMock()
        mock_collection.database.client = MagicMock()
        mock_collection.database.name = "test_db"

        builder = CognitiveMemoryServiceBuilder(
            app_slug="test",
            config={
                "scoring": {"strategy": "recency_decay", "half_life_hours": 72},
                "decay": {"strategy": "exponential", "half_life_hours": 168},
                "importance": {"strategy": "rule_based"},
            },
            collection=mock_collection,
        )

        strategies = builder._resolve_strategies(
            injected_llm_service=None,
            memory_llm_model="openai/gpt-4o",
        )

        assert isinstance(strategies["scoring_strategy"], RecencyDecayScoring)
        assert strategies["scoring_strategy"].half_life_hours == 72
        assert isinstance(strategies["decay_strategy"], ExponentialDecay)
        assert strategies["decay_strategy"].half_life_hours == 168
        assert isinstance(strategies["importance_strategy"], RuleBasedImportance)
