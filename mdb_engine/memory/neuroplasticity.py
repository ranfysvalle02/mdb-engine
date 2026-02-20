"""
Neuroplasticity Engine — Reflective-to-Operative Feedback Loop.

Reads reflective insights from ``ReflectiveMemory`` and converts them into
concrete, bounded, auditable changes to the brain's operating parameters:

- **Trait nudges** — per-user persona trait adjustments
- **Scoring weight overrides** — per-user memory ranking tuning
- **Auto-triggers** — ProspectiveMemory triggers from detected patterns
- **Context budget rebalances** — token allocation adjustments

Safety model ("eustress zone"):
    - Max drift per cycle is bounded (traits: 0.05, weights: 0.1)
    - Hard bounds prevent runaway values (traits: [0.1, 0.9])
    - Cooldown period between cycles (default 24h)
    - Full audit trail with rollback support
    - Perfect Recall: no memories are ever pruned or decayed

This module closes the loop between observation and action, enabling the
cognitive system to correct its own behavior over time.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mdb_engine.database.scoped_wrapper import ScopedCollectionWrapper

try:
    from bson import ObjectId
    from bson.errors import InvalidId
    from pymongo.errors import OperationFailure, PyMongoError
except ImportError:
    raise ImportError("pip install pymongo") from None

from .base import MemoryServiceError

logger = logging.getLogger(__name__)

# Valid adaptation types
ADAPTATION_TYPES = frozenset({"trait_nudge", "scoring_weight", "auto_trigger", "budget_rebalance"})

# Valid emotion types (must match extraction.py)
VALID_EMOTION_TYPES = frozenset({"novelty", "stakes", "resonance", "neutral"})

# Default scoring weight keys and their base values (when no adaptations exist)
DEFAULT_SCORING_WEIGHTS: dict[str, float] = {
    "emotion_weight": 0.0,
    "novelty_boost": 0.1,
    "stakes_boost": 0.15,
    "resonance_boost": 0.1,
    "recency_weight": 0.0,
}

# Default persona trait keys (app-level defaults come from PersonaEngine)
ADAPTABLE_TRAIT_KEYS = frozenset(
    {
        "technical_focus",
        "humor",
        "formality",
        "empathy",
        "creativity",
        "directness",
        "patience",
        "verbosity",
    }
)


class NeuroplasticityError(MemoryServiceError):
    """Base exception for Neuroplasticity Engine failures."""

    pass


class NeuroplasticityEngine:
    """
    Closes the reflective-to-operative feedback loop.

    Reads reflective insights, analyzes memory analytics, and produces
    bounded adaptations that modify per-user scoring weights and persona
    traits.

    All adaptations are stored in ``{app_slug}_adaptations`` with full
    audit trail for observability and rollback.
    """

    def __init__(
        self,
        app_slug: str,
        adaptations_collection: ScopedCollectionWrapper,
        reflective_collection: ScopedCollectionWrapper | None = None,
        memories_collection: ScopedCollectionWrapper | None = None,
        prospective_memory: Any | None = None,
        llm_service: Any | None = None,
        config: dict[str, Any] | None = None,
    ):
        """
        Initialize NeuroplasticityEngine.

        Args:
            app_slug: Application slug.
            adaptations_collection: MongoDB collection for adaptation documents.
            reflective_collection: MongoDB collection for reflective memory
                (source of insights). Can be None if unavailable.
            memories_collection: MongoDB collection for memories
                (used for analytics). Can be None if unavailable.
            prospective_memory: Optional ProspectiveMemory instance for
                creating auto-triggers.
            llm_service: Optional LLM service for classifying reflections
                into adaptations.
            config: Neuroplasticity configuration dict.
        """
        self.app_slug = app_slug
        self._adaptations = adaptations_collection
        self._reflective = reflective_collection
        self._memories = memories_collection
        self._prospective_memory = prospective_memory
        self._llm_service = llm_service
        self._config = config or {}

        # Safety bounds
        self.enabled = self._config.get("enabled", False)
        self.max_trait_drift = self._config.get("max_trait_drift", 0.05)
        self.max_weight_drift = self._config.get("max_weight_drift", 0.1)
        self.trait_bounds = tuple(self._config.get("trait_bounds", (0.1, 0.9)))
        self.weight_bounds = tuple(self._config.get("weight_bounds", (0.0, 0.5)))
        self.confidence_threshold = self._config.get("adaptation_confidence_threshold", 0.6)
        self.cooldown_hours = self._config.get("cooldown_hours", 24)

        self._indexes_ensured = False

        if self.enabled:
            logger.info(
                f"NeuroplasticityEngine initialized: "
                f"max_trait_drift={self.max_trait_drift}, "
                f"max_weight_drift={self.max_weight_drift}, "
                f"cooldown={self.cooldown_hours}h"
            )
        else:
            logger.info("NeuroplasticityEngine initialized but DISABLED")

    async def _ensure_indexes(self) -> None:
        """Create indexes for the adaptations collection (idempotent)."""
        if self._indexes_ensured:
            return
        try:
            await self._adaptations.create_index([("user_id", 1), ("type", 1), ("is_active", 1)])
            await self._adaptations.create_index([("user_id", 1), ("key", 1), ("is_active", 1)])
            await self._adaptations.create_index("created_at")
            self._indexes_ensured = True
            logger.info("Neuroplasticity indexes ensured")
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Index creation warning (may already exist): {e}")

    # ------------------------------------------------------------------
    # Cooldown
    # ------------------------------------------------------------------

    async def _check_cooldown(self, user_id: str) -> tuple[bool, str]:
        """Check if the cooldown period has elapsed for this user.

        Returns:
            Tuple of (can_run: bool, reason: str).
        """
        try:
            last_adaptation = await self._adaptations.find_one(
                {"user_id": str(user_id), "is_active": True},
                sort=[("created_at", -1)],
            )
            if last_adaptation:
                last_time = last_adaptation.get("created_at")
                if last_time:
                    hours_since = (datetime.now(timezone.utc) - last_time).total_seconds() / 3600
                    if hours_since < self.cooldown_hours:
                        return (
                            False,
                            f"Cooldown active: {hours_since:.1f}h since last cycle (need {self.cooldown_hours}h)",
                        )
            return True, "Cooldown elapsed or no previous adaptations"
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Cooldown check failed: {e}")
            return False, f"Cooldown check error: {e}"

    # ------------------------------------------------------------------
    # Core adaptation cycle
    # ------------------------------------------------------------------

    async def run_adaptation_cycle(self, user_id: str) -> dict[str, Any]:
        """
        Run a full adaptation cycle for a user.

        1. Check cooldown
        2. Fetch recent reflective insights (above confidence threshold)
        3. Fetch memory analytics (emotion_type distribution)
        4. Use LLM to classify insights into adaptation types
        5. Apply bounded changes
        6. Store adaptation documents

        Args:
            user_id: User identifier.

        Returns:
            Dict with cycle results including adaptations_applied count.
        """
        if not self.enabled:
            return {"skipped": True, "reason": "neuroplasticity disabled", "adaptations_applied": 0}

        await self._ensure_indexes()

        # 1. Cooldown check
        can_run, reason = await self._check_cooldown(user_id)
        if not can_run:
            logger.info(f"[Neuroplasticity] Skipping cycle for {user_id}: {reason}")
            return {"skipped": True, "reason": reason, "adaptations_applied": 0}

        # 2. Fetch reflective insights
        insights = await self._fetch_insights(user_id)
        if not insights:
            return {"skipped": True, "reason": "no reflective insights above threshold", "adaptations_applied": 0}

        # 3. Fetch memory analytics
        analytics = await self._fetch_memory_analytics(user_id)

        # 4. Classify insights into adaptations
        proposed = await self._classify_insights(user_id, insights, analytics)
        if not proposed:
            return {"skipped": True, "reason": "no actionable adaptations proposed", "adaptations_applied": 0}

        # 5. Apply bounded changes
        applied = []
        cycle_number = await self._get_next_cycle_number(user_id)

        for adaptation in proposed:
            try:
                result = await self._apply_adaptation(user_id, adaptation, cycle_number)
                if result:
                    applied.append(result)
            except (NeuroplasticityError, PyMongoError) as e:
                logger.warning(f"[Neuroplasticity] Failed to apply adaptation: {e}")

        logger.info(
            f"[Neuroplasticity] Cycle complete for {user_id}: "
            f"{len(applied)} adaptations applied from {len(insights)} insights"
        )

        return {
            "skipped": False,
            "adaptations_applied": len(applied),
            "adaptations": applied,
            "insights_processed": len(insights),
            "cycle_number": cycle_number,
        }

    # ------------------------------------------------------------------
    # Fetch helpers
    # ------------------------------------------------------------------

    async def _fetch_insights(self, user_id: str) -> list[dict[str, Any]]:
        """Fetch recent reflective insights above the confidence threshold."""
        if not self._reflective:
            return []

        try:
            cursor = (
                self._reflective.find(
                    {
                        "user_id": str(user_id),
                        "confidence": {"$gte": self.confidence_threshold},
                        "consumed_by_neuroplasticity": {"$ne": True},
                    }
                )
                .sort("created_at", -1)
                .limit(20)
            )
            insights = await cursor.to_list(length=20)
            return insights
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to fetch reflective insights: {e}")
            return []

    async def _fetch_memory_analytics(self, user_id: str) -> dict[str, Any]:
        """Fetch memory analytics for context: emotion_type distribution, category counts."""
        if not self._memories:
            return {}

        try:
            pipeline = [
                {"$match": {"user_id": str(user_id)}},
                {
                    "$group": {
                        "_id": None,
                        "total": {"$sum": 1},
                        "avg_emotion": {"$avg": {"$ifNull": ["$emotion", 0.3]}},
                        "novelty_count": {"$sum": {"$cond": [{"$eq": ["$emotion_type", "novelty"]}, 1, 0]}},
                        "stakes_count": {"$sum": {"$cond": [{"$eq": ["$emotion_type", "stakes"]}, 1, 0]}},
                        "resonance_count": {"$sum": {"$cond": [{"$eq": ["$emotion_type", "resonance"]}, 1, 0]}},
                        "neutral_count": {"$sum": {"$cond": [{"$eq": ["$emotion_type", "neutral"]}, 1, 0]}},
                        "biographical_count": {"$sum": {"$cond": [{"$eq": ["$category", "biographical"]}, 1, 0]}},
                        "relational_count": {"$sum": {"$cond": [{"$eq": ["$category", "relational"]}, 1, 0]}},
                        "preferences_count": {"$sum": {"$cond": [{"$eq": ["$category", "preferences"]}, 1, 0]}},
                        "temporal_count": {"$sum": {"$cond": [{"$eq": ["$category", "temporal"]}, 1, 0]}},
                    }
                },
            ]
            cursor = self._memories.aggregate(pipeline)
            results = await cursor.to_list(length=1)
            return results[0] if results else {}
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to fetch memory analytics: {e}")
            return {}

    async def _get_next_cycle_number(self, user_id: str) -> int:
        """Get the next cycle number for this user."""
        try:
            last = await self._adaptations.find_one(
                {"user_id": str(user_id)},
                sort=[("cycle_number", -1)],
                projection={"cycle_number": 1},
            )
            return (last.get("cycle_number", 0) + 1) if last else 1
        except (PyMongoError, OperationFailure):
            return 1

    # ------------------------------------------------------------------
    # LLM classification
    # ------------------------------------------------------------------

    async def _classify_insights(
        self,
        user_id: str,
        insights: list[dict[str, Any]],
        analytics: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Use LLM to classify reflective insights into adaptation proposals.

        Falls back to rule-based classification if LLM is unavailable.
        """
        if self._llm_service:
            return await self._classify_insights_llm(user_id, insights, analytics)
        return self._classify_insights_rules(insights, analytics)

    async def _classify_insights_llm(
        self,
        user_id: str,
        insights: list[dict[str, Any]],
        analytics: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """LLM-powered insight classification."""
        insights_text = "\n".join(
            f"- [{ins.get('trigger', 'unknown')}] (confidence: {ins.get('confidence', 0):.2f}) "
            f"{ins.get('reflection', '')}"
            for ins in insights[:10]
        )

        analytics_text = (
            json.dumps(
                {k: v for k, v in analytics.items() if k != "_id"},
                default=str,
            )
            if analytics
            else "No analytics available"
        )

        # Fetch current adaptations for context
        current_adaptations = await self.get_active_adaptations(user_id)
        current_text = ""
        if current_adaptations:
            current_text = "\nCURRENT ACTIVE ADAPTATIONS:\n" + "\n".join(
                f"- {a['type']}: {a['key']} = {a['value']}" for a in current_adaptations[:10]
            )

        prompt = f"""You are a neuroplasticity engine that analyzes meta-cognitive reflections
and proposes concrete parameter adjustments for a cognitive memory system.

REFLECTIVE INSIGHTS:
{insights_text}

MEMORY ANALYTICS:
{analytics_text}
{current_text}

Based on these insights and analytics, propose parameter adjustments.

AVAILABLE ADJUSTMENT TYPES:
1. trait_nudge: Adjust a persona trait (empathy, humor, formality, technical_focus,
   creativity, directness, patience, verbosity). Delta must be between -0.05 and +0.05.
2. scoring_weight: Adjust a scoring weight (emotion_weight, novelty_boost, stakes_boost,
   resonance_boost, recency_weight). Delta must be between -0.1 and +0.1.

RULES:
- Only propose adjustments supported by the insights. Do NOT invent adjustments.
- Each adjustment must have a clear reason traced to a specific insight.
- Propose at most 3 adjustments per cycle (conservative adaptation).
- If no adjustments are warranted, return an empty array.

Return ONLY valid JSON:
{{"adaptations": [
    {{
        "type": "trait_nudge",
        "key": "empathy",
        "delta": 0.03,
        "reason": "User frequently shares personal stories (high relational memory count)"
    }}
]}}"""

        try:
            response = await self._llm_service.chat_completion(
                provider_name="chat",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.1,
            )

            # Parse response
            raw = response.strip()
            if raw.startswith("```"):
                import re

                raw = re.sub(r"^```\w*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw)

            data = json.loads(raw)
            proposals = data.get("adaptations", [])

            # Validate proposals
            validated = []
            for p in proposals[:3]:
                p_type = p.get("type", "")
                p_key = p.get("key", "")
                p_delta = p.get("delta", 0)
                p_reason = p.get("reason", "")

                if p_type not in ADAPTATION_TYPES:
                    continue
                if not p_key or not p_reason:
                    continue
                if not isinstance(p_delta, int | float):
                    continue

                validated.append(
                    {
                        "type": p_type,
                        "key": p_key,
                        "delta": float(p_delta),
                        "reason": p_reason,
                    }
                )

            return validated

        except (json.JSONDecodeError, ValueError, TypeError, RuntimeError, AttributeError) as e:
            logger.warning(f"[Neuroplasticity] LLM classification failed: {e}, falling back to rules")
            return self._classify_insights_rules(insights, analytics)

    def _classify_insights_rules(
        self,
        insights: list[dict[str, Any]],
        analytics: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Rule-based fallback classification when LLM is unavailable."""
        proposals: list[dict[str, Any]] = []
        total = analytics.get("total", 0)
        if total == 0:
            return proposals

        # Rule 1: High relational memory ratio -> empathy nudge
        relational_ratio = analytics.get("relational_count", 0) / max(total, 1)
        if relational_ratio > 0.3:
            proposals.append(
                {
                    "type": "trait_nudge",
                    "key": "empathy",
                    "delta": 0.03,
                    "reason": (
                        f"High relational memory ratio ({relational_ratio:.0%})"
                        " suggests empathetic communication style"
                    ),
                }
            )

        # Rule 2: High stakes memory ratio -> stakes_boost
        stakes_ratio = analytics.get("stakes_count", 0) / max(total, 1)
        if stakes_ratio > 0.2:
            proposals.append(
                {
                    "type": "scoring_weight",
                    "key": "stakes_boost",
                    "delta": 0.05,
                    "reason": (
                        f"High stakes-type memory ratio ({stakes_ratio:.0%})"
                        " suggests user benefits from urgency-sensitive recall"
                    ),
                }
            )

        # Rule 3: High novelty memory ratio -> novelty_boost
        novelty_ratio = analytics.get("novelty_count", 0) / max(total, 1)
        if novelty_ratio > 0.15:
            proposals.append(
                {
                    "type": "scoring_weight",
                    "key": "novelty_boost",
                    "delta": 0.05,
                    "reason": (
                        f"High novelty-type memory ratio ({novelty_ratio:.0%})"
                        " suggests user encounters frequent pattern-breaking information"
                    ),
                }
            )

        return proposals[:3]

    # ------------------------------------------------------------------
    # Apply adaptations
    # ------------------------------------------------------------------

    async def _apply_adaptation(
        self,
        user_id: str,
        proposal: dict[str, Any],
        cycle_number: int,
    ) -> dict[str, Any] | None:
        """Apply a single bounded adaptation and store the document.

        Returns the stored adaptation document, or None if bounds prevented it.
        """
        a_type = proposal["type"]
        a_key = proposal["key"]
        a_delta = proposal["delta"]
        a_reason = proposal["reason"]

        # Get current value
        current = await self._get_current_value(user_id, a_type, a_key)

        # Clamp delta to max drift
        if a_type == "trait_nudge":
            max_drift = self.max_trait_drift
            bounds = self.trait_bounds
        elif a_type == "scoring_weight":
            max_drift = self.max_weight_drift
            bounds = self.weight_bounds
        else:
            max_drift = self.max_weight_drift
            bounds = (0.0, 1.0)

        clamped_delta = max(-max_drift, min(max_drift, a_delta))
        new_value = current + clamped_delta

        # Clamp to bounds
        new_value = max(bounds[0], min(bounds[1], new_value))

        # Skip if no effective change
        if abs(new_value - current) < 0.001:
            logger.debug(f"[Neuroplasticity] Skipping {a_type}:{a_key} — " f"already at bound ({current:.3f})")
            return None

        actual_delta = new_value - current

        # Store adaptation document
        adaptation_doc = {
            "user_id": str(user_id),
            "app_slug": self.app_slug,
            "type": a_type,
            "key": a_key,
            "value": round(new_value, 4),
            "previous_value": round(current, 4),
            "delta": round(actual_delta, 4),
            "reason": a_reason,
            "source_reflection_id": proposal.get("source_reflection_id"),
            "confidence": proposal.get("confidence", 0.7),
            "cycle_number": cycle_number,
            "created_at": datetime.now(timezone.utc),
            "is_active": True,
        }

        try:
            # Deactivate previous adaptation for same key (if any)
            await self._adaptations.update_many(
                {
                    "user_id": str(user_id),
                    "type": a_type,
                    "key": a_key,
                    "is_active": True,
                },
                {"$set": {"is_active": False, "superseded_at": datetime.now(timezone.utc)}},
            )

            # Insert new adaptation
            result = await self._adaptations.insert_one(adaptation_doc)
            adaptation_doc["_id"] = str(result.inserted_id)

            logger.info(
                f"[Neuroplasticity] Applied {a_type}:{a_key} "
                f"{current:.3f} -> {new_value:.3f} (delta={actual_delta:+.3f}): {a_reason[:80]}"
            )

            return adaptation_doc

        except (PyMongoError, OperationFailure) as e:
            logger.exception(f"[Neuroplasticity] Failed to store adaptation: {e}")
            return None

    async def _get_current_value(self, user_id: str, a_type: str, a_key: str) -> float:
        """Get the current effective value for an adaptation key.

        Checks active adaptations first, falls back to defaults.
        """
        try:
            existing = await self._adaptations.find_one(
                {
                    "user_id": str(user_id),
                    "type": a_type,
                    "key": a_key,
                    "is_active": True,
                },
                sort=[("created_at", -1)],
            )
            if existing:
                return existing.get("value", 0.5)
        except (PyMongoError, OperationFailure):
            pass

        # Return defaults
        if a_type == "scoring_weight":
            return DEFAULT_SCORING_WEIGHTS.get(a_key, 0.0)
        if a_type == "trait_nudge":
            return 0.5  # Neutral default; will be merged with PersonaEngine defaults
        return 0.0

    # ------------------------------------------------------------------
    # Read adaptations (consumed by scoring and context engineering)
    # ------------------------------------------------------------------

    async def get_active_adaptations(self, user_id: str) -> list[dict[str, Any]]:
        """Get all active adaptations for a user.

        Returns:
            List of adaptation documents sorted by type then key.
        """
        await self._ensure_indexes()
        try:
            cursor = self._adaptations.find(
                {"user_id": str(user_id), "is_active": True},
            ).sort([("type", 1), ("key", 1)])
            adaptations = await cursor.to_list(length=100)
            for a in adaptations:
                a["_id"] = str(a["_id"])
            return adaptations
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to get active adaptations: {e}")
            return []

    async def get_scoring_weights(self, user_id: str) -> dict[str, float]:
        """Get per-user scoring weight overrides.

        Returns a dict of weight_key -> value. Missing keys should use
        the defaults from ``DEFAULT_SCORING_WEIGHTS``.
        """
        try:
            cursor = self._adaptations.find(
                {
                    "user_id": str(user_id),
                    "type": "scoring_weight",
                    "is_active": True,
                },
            )
            adaptations = await cursor.to_list(length=20)
            return {a["key"]: a["value"] for a in adaptations if "key" in a and "value" in a}
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to get scoring weights: {e}")
            return {}

    async def get_trait_overrides(self, user_id: str) -> dict[str, float]:
        """Get per-user persona trait overrides.

        Returns a dict of trait_key -> value to merge with app-level traits.
        """
        try:
            cursor = self._adaptations.find(
                {
                    "user_id": str(user_id),
                    "type": "trait_nudge",
                    "is_active": True,
                },
            )
            adaptations = await cursor.to_list(length=20)
            return {a["key"]: a["value"] for a in adaptations if "key" in a and "value" in a}
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to get trait overrides: {e}")
            return {}

    # ------------------------------------------------------------------
    # Revert / manage adaptations
    # ------------------------------------------------------------------

    async def revert_adaptation(self, adaptation_id: str) -> bool:
        """Revert (deactivate) a specific adaptation.

        Args:
            adaptation_id: The adaptation document ID.

        Returns:
            True if successfully deactivated.
        """
        try:
            result = await self._adaptations.update_one(
                {"_id": ObjectId(adaptation_id)},
                {
                    "$set": {
                        "is_active": False,
                        "reverted_at": datetime.now(timezone.utc),
                    }
                },
            )
            if result.modified_count > 0:
                logger.info(f"[Neuroplasticity] Reverted adaptation {adaptation_id}")
                return True
            return False
        except (PyMongoError, OperationFailure, InvalidId) as e:
            logger.warning(f"Failed to revert adaptation: {e}")
            return False

    async def get_adaptation_history(
        self,
        user_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get full adaptation history for a user (active and reverted).

        Args:
            user_id: User identifier.
            limit: Maximum number of records.

        Returns:
            List of adaptation documents sorted by creation time (newest first).
        """
        try:
            cursor = self._adaptations.find({"user_id": str(user_id)}).sort("created_at", -1).limit(limit)
            history = await cursor.to_list(length=limit)
            for h in history:
                h["_id"] = str(h["_id"])
            return history
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to get adaptation history: {e}")
            return []

    async def get_cooldown_status(self, user_id: str) -> dict[str, Any]:
        """Get cooldown status for a user.

        Returns:
            Dict with cooldown_active, hours_remaining, next_eligible_at.
        """
        can_run, reason = await self._check_cooldown(user_id)
        result: dict[str, Any] = {
            "cooldown_active": not can_run,
            "reason": reason,
            "cooldown_hours": self.cooldown_hours,
        }
        if not can_run:
            try:
                last = await self._adaptations.find_one(
                    {"user_id": str(user_id), "is_active": True},
                    sort=[("created_at", -1)],
                )
                if last and last.get("created_at"):
                    next_eligible = last["created_at"] + timedelta(hours=self.cooldown_hours)
                    hours_remaining = (next_eligible - datetime.now(timezone.utc)).total_seconds() / 3600
                    result["hours_remaining"] = max(0, round(hours_remaining, 1))
                    result["next_eligible_at"] = next_eligible.isoformat()
            except (PyMongoError, OperationFailure):
                pass
        return result

    async def mark_insights_consumed(self, insight_ids: list[str]) -> int:
        """Mark reflective insights as consumed by the neuroplasticity engine.

        Args:
            insight_ids: List of reflective memory document IDs.

        Returns:
            Number of documents updated.
        """
        if not self._reflective or not insight_ids:
            return 0
        try:
            oids = [ObjectId(i) for i in insight_ids if i]
            result = await self._reflective.update_many(
                {"_id": {"$in": oids}},
                {"$set": {"consumed_by_neuroplasticity": True, "consumed_at": datetime.now(timezone.utc)}},
            )
            return result.modified_count if hasattr(result, "modified_count") else 0
        except (PyMongoError, OperationFailure, InvalidId) as e:
            logger.warning(f"Failed to mark insights consumed: {e}")
            return 0
