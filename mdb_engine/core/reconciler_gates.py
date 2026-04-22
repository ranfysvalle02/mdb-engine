"""
Safety gates for the manifest reconciler.

These are the "saves-you-at-3am" checks that prevent a destructive plan
from silently executing. They are evaluated **after** the plan has been
built but **before** any ops are applied.

Gates are driven by ``manifest_tracking.confirm_if`` and can be overridden
at apply-time by:

- Passing ``confirm=True`` to :func:`evaluate_gates`.
- Setting ``MDB_CONFIRM=1`` in the environment.
- Passing ``--yes`` to the CLI (translated into ``confirm=True``).

When a gate is tripped, :class:`GateResult.blocked` is True and
:meth:`Reconciler.apply` returns ``{"status": "confirmation_required",
"reasons": [...]}``. Interactive TTY consumers can ask the user to
confirm; CI consumers should treat ``confirmation_required`` as a
non-zero exit.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import ConnectionFailure, OperationFailure, ServerSelectionTimeoutError

if TYPE_CHECKING:
    from .reconciler import ReconcilePlan

logger = logging.getLogger(__name__)


# Per-collection doc-count budget so a giant cluster doesn't block apply.
_DOC_COUNT_TIMEOUT_S: float = 0.25
# Total wall-clock budget across all doc-count checks.
_DOC_COUNT_TOTAL_BUDGET_S: float = 5.0

_DESTRUCTIVE_OPS = {"drop_collection", "drop_index", "disable_service"}


@dataclass
class GateResult:
    """Outcome of evaluating the ``confirm_if`` safety gates."""

    blocked: bool = False
    reasons: list[str] = field(default_factory=list)
    destructive_ops: int = 0
    docs_at_risk: int = 0
    protected_matches: list[str] = field(default_factory=list)
    thresholds: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocked": self.blocked,
            "reasons": list(self.reasons),
            "destructive_ops": self.destructive_ops,
            "docs_at_risk": self.docs_at_risk,
            "protected_matches": list(self.protected_matches),
            "thresholds": dict(self.thresholds),
        }


def _confirm_if_config(manifest: dict[str, Any]) -> dict[str, Any] | None:
    tracking = manifest.get("manifest_tracking") or {}
    cfg = tracking.get("confirm_if")
    if not isinstance(cfg, dict):
        return None
    return cfg


def _count_destructive(plan: ReconcilePlan) -> int:
    return sum(1 for op in plan.ops if op.op in _DESTRUCTIVE_OPS and not op.skipped)


def _match_protected(
    plan: ReconcilePlan,
    patterns: list[str],
) -> list[str]:
    """Return names of destructive ops whose target matches any glob pattern."""
    if not patterns:
        return []
    import fnmatch

    matched: list[str] = []
    for op in plan.ops:
        if op.skipped or op.op not in _DESTRUCTIVE_OPS:
            continue
        target = op.collection or op.name
        for pattern in patterns:
            if fnmatch.fnmatchcase(target, pattern):
                matched.append(target)
                break
    return matched


async def _estimate_docs_at_risk(
    db: AsyncIOMotorDatabase,
    slug: str,
    plan: ReconcilePlan,
) -> tuple[int, bool]:
    """Sum document counts across destructive collection ops.

    Returns ``(total, timed_out)``. On any timeout, we treat the count
    as "unknown -> at risk" by adding a sentinel so the gate trips.
    """
    destructive = [op for op in plan.ops if op.op in {"drop_collection", "disable_service"} and not op.skipped]
    if not destructive:
        return 0, False

    loop = asyncio.get_event_loop()
    start = loop.time()
    total = 0
    timed_out = False

    for op in destructive:
        remaining = _DOC_COUNT_TOTAL_BUDGET_S - (loop.time() - start)
        if remaining <= 0:
            timed_out = True
            break
        # service ops carry a pre-prefixed collection; regular ops carry the base name.
        if op.op == "disable_service":
            physical = op.collection
        else:
            physical = op.collection if op.collection.startswith(f"{slug}_") else f"{slug}_{op.collection}"
        try:  # nosemgrep
            count = await asyncio.wait_for(
                db[physical].estimated_document_count(),
                timeout=min(_DOC_COUNT_TIMEOUT_S, remaining),
            )
            total += int(count or 0)
        except (asyncio.TimeoutError, OperationFailure, ConnectionFailure, ServerSelectionTimeoutError):
            timed_out = True
            continue
        except Exception as e:  # noqa: BLE001 - we always want to proceed
            logger.debug("[%s] doc-count estimate failed for %s: %s", slug, physical, e)
            continue
    return total, timed_out


async def evaluate_gates(
    *,
    db: AsyncIOMotorDatabase,
    slug: str,
    manifest: dict[str, Any],
    plan: ReconcilePlan,
    env_confirm: bool = False,
) -> GateResult:
    """Evaluate the ``confirm_if`` thresholds against a plan.

    Args:
        db: Motor db used for doc-count probes.
        slug: App slug.
        manifest: The desired manifest (source of `confirm_if` config).
        plan: The already-built reconcile plan.
        env_confirm: When True, act as if ``MDB_CONFIRM=1`` was set
            (bypass all gates). Caller is responsible for honoring
            ``--yes`` / env vars.

    Returns:
        A :class:`GateResult`; callers should check ``blocked``.
    """
    result = GateResult()
    cfg = _confirm_if_config(manifest)
    if not cfg:
        return result

    result.thresholds = {
        "destructive_ops": cfg.get("destructive_ops"),
        "docs_at_risk": cfg.get("docs_at_risk"),
        "protect_on_match": cfg.get("protect_on_match"),
    }

    if env_confirm:
        return result

    destructive = _count_destructive(plan)
    result.destructive_ops = destructive
    threshold = cfg.get("destructive_ops")
    if isinstance(threshold, int) and threshold >= 0 and destructive >= threshold and destructive > 0:
        result.blocked = True
        result.reasons.append(f"destructive_ops={destructive} >= confirm_if.destructive_ops={threshold}")

    docs_threshold = cfg.get("docs_at_risk")
    if isinstance(docs_threshold, int) and docs_threshold >= 0:
        at_risk, timed_out = await _estimate_docs_at_risk(db, slug, plan)
        result.docs_at_risk = at_risk
        if at_risk >= docs_threshold and at_risk > 0:
            result.blocked = True
            result.reasons.append(f"docs_at_risk={at_risk} >= confirm_if.docs_at_risk={docs_threshold}")
        elif timed_out:
            # Unknown counts: be safe and block.
            result.blocked = True
            result.reasons.append("docs_at_risk check timed out; refusing to proceed without --yes")

    patterns = cfg.get("protect_on_match") or []
    if isinstance(patterns, list) and patterns:
        matches = _match_protected(plan, patterns)
        result.protected_matches = matches
        if matches:
            result.blocked = True
            joined = ", ".join(sorted(set(matches)))
            result.reasons.append(f"confirm_if.protect_on_match tripped for: {joined}")

    return result


__all__ = [
    "GateResult",
    "evaluate_gates",
]
