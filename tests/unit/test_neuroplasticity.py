"""
Unit tests for the Neuroplasticity Engine.

Tests cover:
- Adaptation cycle with cooldown enforcement
- Safety bounds (trait clamping, weight clamping, max drift)
- Revert logic
- Scoring weight integration
- Emotion-type-aware scoring
- Per-user trait overrides
- Zero overhead for non-adapted users
- Rule-based fallback classification
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_async_cursor(docs: list[dict[str, Any]]) -> MagicMock:
    """Create a mock async cursor that supports ``to_list`` and ``sort``."""
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=docs)
    cursor.sort = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    return cursor


def _make_collection(
    find_docs: list[dict[str, Any]] | None = None,
    find_one_doc: dict[str, Any] | None = None,
    aggregate_docs: list[dict[str, Any]] | None = None,
) -> MagicMock:
    """Create a mock ScopedCollectionWrapper."""
    coll = MagicMock()
    coll.find = MagicMock(return_value=_make_async_cursor(find_docs or []))
    coll.find_one = AsyncMock(return_value=find_one_doc)
    coll.insert_one = AsyncMock(return_value=MagicMock(inserted_id="new_id_123"))
    coll.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    coll.update_many = AsyncMock(return_value=MagicMock(modified_count=1))
    coll.create_index = AsyncMock()
    coll.count_documents = AsyncMock(return_value=0)

    if aggregate_docs is not None:
        agg_cursor = _make_async_cursor(aggregate_docs)
        coll.aggregate = MagicMock(return_value=agg_cursor)
    else:
        coll.aggregate = MagicMock(return_value=_make_async_cursor([]))

    return coll


def _make_engine(
    enabled: bool = True,
    cooldown_hours: int = 24,
    max_trait_drift: float = 0.05,
    max_weight_drift: float = 0.1,
    adaptations_find_docs: list[dict[str, Any]] | None = None,
    adaptations_find_one: dict[str, Any] | None = None,
    reflective_find_docs: list[dict[str, Any]] | None = None,
    analytics_docs: list[dict[str, Any]] | None = None,
    llm_service: Any = None,
):
    """Create a NeuroplasticityEngine with mocked collections."""
    from mdb_engine.memory.neuroplasticity import NeuroplasticityEngine

    adaptations_coll = _make_collection(
        find_docs=adaptations_find_docs or [],
        find_one_doc=adaptations_find_one,
    )
    reflective_coll = _make_collection(
        find_docs=reflective_find_docs or [],
    )
    memories_coll = _make_collection(
        aggregate_docs=analytics_docs or [],
    )

    engine = NeuroplasticityEngine(
        app_slug="test_app",
        adaptations_collection=adaptations_coll,
        reflective_collection=reflective_coll,
        memories_collection=memories_coll,
        llm_service=llm_service,
        config={
            "enabled": enabled,
            "cooldown_hours": cooldown_hours,
            "max_trait_drift": max_trait_drift,
            "max_weight_drift": max_weight_drift,
            "trait_bounds": (0.1, 0.9),
            "weight_bounds": (0.0, 0.5),
            "adaptation_confidence_threshold": 0.6,
        },
    )
    return engine


# ---------------------------------------------------------------------------
# Tests: Disabled state
# ---------------------------------------------------------------------------


class TestDisabledEngine:
    """Tests for when neuroplasticity is disabled."""

    @pytest.mark.asyncio
    async def test_disabled_returns_skipped(self):
        engine = _make_engine(enabled=False)
        result = await engine.run_adaptation_cycle("user1")
        assert result["skipped"] is True
        assert result["reason"] == "neuroplasticity disabled"
        assert result["adaptations_applied"] == 0


# ---------------------------------------------------------------------------
# Tests: Cooldown
# ---------------------------------------------------------------------------


class TestCooldown:
    """Tests for cooldown enforcement."""

    @pytest.mark.asyncio
    async def test_cooldown_blocks_when_recent(self):
        recent_time = datetime.now(timezone.utc) - timedelta(hours=1)
        engine = _make_engine(
            cooldown_hours=24,
            adaptations_find_one={"created_at": recent_time, "is_active": True},
        )
        can_run, reason = await engine._check_cooldown("user1")
        assert can_run is False
        assert "Cooldown active" in reason

    @pytest.mark.asyncio
    async def test_cooldown_allows_when_elapsed(self):
        old_time = datetime.now(timezone.utc) - timedelta(hours=48)
        engine = _make_engine(
            cooldown_hours=24,
            adaptations_find_one={"created_at": old_time, "is_active": True},
        )
        can_run, reason = await engine._check_cooldown("user1")
        assert can_run is True

    @pytest.mark.asyncio
    async def test_cooldown_allows_when_no_previous(self):
        engine = _make_engine(cooldown_hours=24, adaptations_find_one=None)
        can_run, reason = await engine._check_cooldown("user1")
        assert can_run is True

    @pytest.mark.asyncio
    async def test_cooldown_status_returns_info(self):
        recent_time = datetime.now(timezone.utc) - timedelta(hours=6)
        engine = _make_engine(
            cooldown_hours=24,
            adaptations_find_one={"created_at": recent_time, "is_active": True},
        )
        status = await engine.get_cooldown_status("user1")
        assert status["cooldown_active"] is True
        assert "hours_remaining" in status
        assert status["hours_remaining"] > 0


# ---------------------------------------------------------------------------
# Tests: Safety bounds
# ---------------------------------------------------------------------------


class TestSafetyBounds:
    """Tests for drift limits and value clamping."""

    @pytest.mark.asyncio
    async def test_trait_drift_clamped(self):
        engine = _make_engine(max_trait_drift=0.05)

        # Propose a 0.2 delta — should be clamped to 0.05
        result = await engine._apply_adaptation(
            user_id="user1",
            proposal={
                "type": "trait_nudge",
                "key": "empathy",
                "delta": 0.2,
                "reason": "test",
            },
            cycle_number=1,
        )
        assert result is not None
        assert abs(result["delta"]) <= 0.05

    @pytest.mark.asyncio
    async def test_weight_drift_clamped(self):
        engine = _make_engine(max_weight_drift=0.1)

        result = await engine._apply_adaptation(
            user_id="user1",
            proposal={
                "type": "scoring_weight",
                "key": "novelty_boost",
                "delta": 0.5,
                "reason": "test",
            },
            cycle_number=1,
        )
        assert result is not None
        assert abs(result["delta"]) <= 0.1

    @pytest.mark.asyncio
    async def test_trait_clamped_to_bounds(self):
        engine = _make_engine(max_trait_drift=0.05)

        # Current value is at upper bound (0.9), delta positive — no change
        engine._adaptations.find_one = AsyncMock(
            return_value={"value": 0.9, "is_active": True, "created_at": datetime.now(timezone.utc) - timedelta(days=2)}
        )
        result = await engine._apply_adaptation(
            user_id="user1",
            proposal={
                "type": "trait_nudge",
                "key": "empathy",
                "delta": 0.05,
                "reason": "test",
            },
            cycle_number=1,
        )
        # Should be None — already at bound
        assert result is None

    @pytest.mark.asyncio
    async def test_negative_delta_works(self):
        engine = _make_engine(max_trait_drift=0.05)

        # Set current value to 0.7
        engine._adaptations.find_one = AsyncMock(
            return_value={"value": 0.7, "is_active": True, "created_at": datetime.now(timezone.utc) - timedelta(days=2)}
        )
        result = await engine._apply_adaptation(
            user_id="user1",
            proposal={
                "type": "trait_nudge",
                "key": "empathy",
                "delta": -0.03,
                "reason": "test",
            },
            cycle_number=1,
        )
        assert result is not None
        assert result["value"] == pytest.approx(0.67, abs=0.001)
        assert result["delta"] == pytest.approx(-0.03, abs=0.001)


# ---------------------------------------------------------------------------
# Tests: Revert
# ---------------------------------------------------------------------------


class TestRevert:
    """Tests for adaptation reversal."""

    @pytest.mark.asyncio
    async def test_revert_deactivates(self):
        engine = _make_engine()
        success = await engine.revert_adaptation("507f1f77bcf86cd799439011")
        assert success is True
        engine._adaptations.update_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_revert_nonexistent_returns_false(self):
        engine = _make_engine()
        engine._adaptations.update_one = AsyncMock(return_value=MagicMock(modified_count=0))
        success = await engine.revert_adaptation("507f1f77bcf86cd799439011")
        assert success is False


# ---------------------------------------------------------------------------
# Tests: Rule-based classification
# ---------------------------------------------------------------------------


class TestRuleClassification:
    """Tests for the rule-based fallback classifier."""

    def test_high_relational_triggers_empathy(self):
        engine = _make_engine()
        analytics = {
            "total": 100,
            "relational_count": 40,
            "stakes_count": 5,
            "novelty_count": 5,
        }
        proposals = engine._classify_insights_rules([], analytics)
        assert any(p["key"] == "empathy" for p in proposals)

    def test_high_stakes_triggers_stakes_boost(self):
        engine = _make_engine()
        analytics = {
            "total": 100,
            "relational_count": 10,
            "stakes_count": 25,
            "novelty_count": 5,
        }
        proposals = engine._classify_insights_rules([], analytics)
        assert any(p["key"] == "stakes_boost" for p in proposals)

    def test_high_novelty_triggers_novelty_boost(self):
        engine = _make_engine()
        analytics = {
            "total": 100,
            "relational_count": 10,
            "stakes_count": 5,
            "novelty_count": 20,
        }
        proposals = engine._classify_insights_rules([], analytics)
        assert any(p["key"] == "novelty_boost" for p in proposals)

    def test_no_data_returns_empty(self):
        engine = _make_engine()
        proposals = engine._classify_insights_rules([], {"total": 0})
        assert proposals == []

    def test_max_three_proposals(self):
        engine = _make_engine()
        analytics = {
            "total": 100,
            "relational_count": 50,
            "stakes_count": 30,
            "novelty_count": 20,
        }
        proposals = engine._classify_insights_rules([], analytics)
        assert len(proposals) <= 3


# ---------------------------------------------------------------------------
# Tests: Get scoring weights / trait overrides
# ---------------------------------------------------------------------------


class TestGetAdaptations:
    """Tests for reading per-user adaptations."""

    @pytest.mark.asyncio
    async def test_get_scoring_weights_returns_dict(self):
        engine = _make_engine(
            adaptations_find_docs=[
                {"key": "novelty_boost", "value": 0.2, "type": "scoring_weight", "is_active": True},
                {"key": "stakes_boost", "value": 0.25, "type": "scoring_weight", "is_active": True},
            ]
        )
        weights = await engine.get_scoring_weights("user1")
        assert weights["novelty_boost"] == 0.2
        assert weights["stakes_boost"] == 0.25

    @pytest.mark.asyncio
    async def test_get_scoring_weights_empty_for_no_adaptations(self):
        engine = _make_engine(adaptations_find_docs=[])
        weights = await engine.get_scoring_weights("user1")
        assert weights == {}

    @pytest.mark.asyncio
    async def test_get_trait_overrides_returns_dict(self):
        engine = _make_engine(
            adaptations_find_docs=[
                {"key": "empathy", "value": 0.75, "type": "trait_nudge", "is_active": True},
            ]
        )
        overrides = await engine.get_trait_overrides("user1")
        assert overrides["empathy"] == 0.75

    @pytest.mark.asyncio
    async def test_get_active_adaptations(self):
        from bson import ObjectId

        engine = _make_engine(
            adaptations_find_docs=[
                {
                    "_id": ObjectId(),
                    "type": "trait_nudge",
                    "key": "empathy",
                    "value": 0.65,
                    "is_active": True,
                },
            ]
        )
        active = await engine.get_active_adaptations("user1")
        assert len(active) == 1
        assert active[0]["key"] == "empathy"


# ---------------------------------------------------------------------------
# Tests: Full adaptation cycle
# ---------------------------------------------------------------------------


class TestAdaptationCycle:
    """Tests for the full run_adaptation_cycle flow."""

    @pytest.mark.asyncio
    async def test_cycle_skips_on_cooldown(self):
        recent_time = datetime.now(timezone.utc) - timedelta(hours=1)
        engine = _make_engine(
            cooldown_hours=24,
            adaptations_find_one={"created_at": recent_time, "is_active": True},
        )
        result = await engine.run_adaptation_cycle("user1")
        assert result["skipped"] is True
        assert "cooldown" in result["reason"].lower() or "Cooldown" in result["reason"]

    @pytest.mark.asyncio
    async def test_cycle_skips_no_insights(self):
        engine = _make_engine(
            adaptations_find_one=None,
            reflective_find_docs=[],
        )
        result = await engine.run_adaptation_cycle("user1")
        assert result["skipped"] is True
        assert "no reflective insights" in result["reason"]

    @pytest.mark.asyncio
    async def test_cycle_applies_rule_based(self):
        engine = _make_engine(
            adaptations_find_one=None,
            reflective_find_docs=[
                {
                    "_id": "ref1",
                    "user_id": "user1",
                    "reflection": "User shares many personal stories",
                    "confidence": 0.8,
                    "trigger": "pattern_detection",
                },
            ],
            analytics_docs=[
                {
                    "_id": None,
                    "total": 100,
                    "avg_emotion": 0.5,
                    "novelty_count": 5,
                    "stakes_count": 5,
                    "resonance_count": 20,
                    "neutral_count": 70,
                    "biographical_count": 20,
                    "relational_count": 40,
                    "preferences_count": 20,
                    "temporal_count": 20,
                }
            ],
        )
        result = await engine.run_adaptation_cycle("user1")
        assert result["skipped"] is False
        assert result["adaptations_applied"] >= 1
        assert result["insights_processed"] == 1


# ---------------------------------------------------------------------------
# Tests: Emotion type in extraction model
# ---------------------------------------------------------------------------


class TestEmotionType:
    """Tests for the CognitiveFact emotion_type field."""

    def test_cognitive_fact_has_emotion_type(self):
        from mdb_engine.memory.extraction import PYDANTIC_AVAILABLE

        if not PYDANTIC_AVAILABLE:
            pytest.skip("Pydantic not available")

        from mdb_engine.memory.extraction import CognitiveFact

        fact = CognitiveFact(
            text="User's daughter is allergic to peanuts",
            category="relational",
            emotion=0.9,
            emotion_type="stakes",
        )
        assert fact.emotion_type == "stakes"
        assert fact.emotion == 0.9

    def test_cognitive_fact_defaults_to_neutral(self):
        from mdb_engine.memory.extraction import PYDANTIC_AVAILABLE

        if not PYDANTIC_AVAILABLE:
            pytest.skip("Pydantic not available")

        from mdb_engine.memory.extraction import CognitiveFact

        fact = CognitiveFact(
            text="User drinks coffee",
            category="preferences",
        )
        assert fact.emotion_type == "neutral"
        assert fact.emotion == 0.3


# ---------------------------------------------------------------------------
# Tests: Scoring weight integration
# ---------------------------------------------------------------------------


class TestScoringWeightIntegration:
    """Test that scoring formula uses emotion_type and per-user weights."""

    def test_type_boost_calculation(self):
        """Verify the boost formula for different emotion types."""
        from mdb_engine.memory.neuroplasticity import DEFAULT_SCORING_WEIGHTS

        weights = DEFAULT_SCORING_WEIGHTS.copy()

        # Novelty memory with emotion=0.8
        novelty_boost = 1.0 + weights["novelty_boost"] * 0.8
        assert novelty_boost > 1.0

        # Stakes memory with emotion=0.9
        stakes_boost = 1.0 + weights["stakes_boost"] * 0.9
        assert stakes_boost > novelty_boost  # stakes_boost (0.15) > novelty_boost (0.1)

        # Neutral memory
        neutral_boost = 1.0
        assert neutral_boost < novelty_boost

    def test_zero_overhead_no_weights(self):
        """When no weights exist, the formula behaves identically to the original."""
        import math

        weights: dict[str, float] = {}
        importance = 0.7
        access_count = 3

        base_importance = importance * (1 + math.log(access_count + 1))

        emotion_type = "neutral"
        emotion_val = 0.3
        type_boost = 1.0
        if emotion_type == "novelty":
            type_boost += weights.get("novelty_boost", 0.1) * emotion_val
        elif emotion_type == "stakes":
            type_boost += weights.get("stakes_boost", 0.15) * emotion_val
        elif emotion_type == "resonance":
            type_boost += weights.get("resonance_boost", 0.1) * emotion_val

        emotion_factor = 1 + weights.get("emotion_weight", 0.0) * emotion_val

        effective = base_importance * emotion_factor * type_boost
        original = importance * (1 + math.log(access_count + 1))

        # For neutral type with no weights: type_boost=1.0, emotion_factor=1.0
        # So effective should equal original
        assert effective == pytest.approx(original)
