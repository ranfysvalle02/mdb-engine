"""
Memory Analytics

Standalone function for computing comprehensive memory analytics.
Extracted from CognitiveMemoryService.get_memory_analytics for modularity.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from pymongo.errors import OperationFailure, PyMongoError

if TYPE_CHECKING:
    from mdb_engine.database.scoped_wrapper import ScopedCollectionWrapper

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Empty-state default (returned when a user has zero memories)
# ---------------------------------------------------------------------------

_EMPTY_ANALYTICS: dict[str, Any] = {
    "total_memories": 0,
    "memory_growth": [],
    "access_patterns": {
        "most_accessed": [],
        "avg_access_count": 0.0,
        "access_distribution": {"high": 0, "medium": 0, "low": 0},
    },
    "quality_metrics": {
        "avg_importance": 0.0,
        "avg_confidence": 0.0,
        "importance_distribution": {"high": 0, "medium": 0, "low": 0},
        "confidence_distribution": {"high": 0, "medium": 0, "low": 0},
        "health_score": 0.0,
    },
    "graph_connectivity": {
        "memories_with_links": 0,
        "total_graph_links": 0,
        "avg_links_per_memory": 0.0,
    },
    "timeline_organization": {
        "timeline_distribution": {},
        "memories_per_timeline": 0.0,
    },
    "category_trends": {"current": {}, "growth": {}},
    "top_memories": {
        "most_important": [],
        "most_accessed": [],
        "recently_created": [],
    },
    "merge_statistics": {"merged_count": 0, "merge_rate": 0.0},
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize_created_at(raw: Any) -> datetime | None:
    """Normalise a ``created_at`` value into a timezone-aware datetime."""
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    # Handle bson datetime-like objects
    try:
        if hasattr(raw, "replace"):
            return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    except (AttributeError, TypeError):
        pass
    return None


def _collect_doc_metrics(
    docs: list[dict[str, Any]],
    detect_category_fn: Callable[[str], str] | None,
    now: datetime,
) -> dict[str, Any]:
    """Iterate all documents once and collect per-document metrics."""
    thirty_days_ago = now - timedelta(days=30)
    seven_days_ago = now - timedelta(days=7)

    importances: list[float] = []
    access_counts: list[int] = []
    emotions: list[float] = []
    confidences: list[float] = []
    categories: dict[str, int] = {}
    growth_by_date: dict[str, int] = {}
    cat_growth_by_date: dict[str, dict[str, int]] = {}
    graph_links_count = 0
    with_graph_links = 0
    timeline_dist: dict[str, int] = {}
    merged_count = 0
    top_by_importance: list[dict[str, Any]] = []
    top_by_access: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []

    for doc in docs:
        importance = doc.get("importance", 0.5)
        access_count = doc.get("access_count", 0)
        confidence = doc.get("confidence", 0.8)
        emotion = doc.get("emotion", 0.3)

        importances.append(importance)
        access_counts.append(access_count)
        emotions.append(emotion)
        confidences.append(confidence)

        # Category
        cat = doc.get("category")
        if not cat:
            cat = detect_category_fn(doc.get("text", "")) if detect_category_fn and doc.get("text") else "biographical"
        categories[cat] = categories.get(cat, 0) + 1

        # Created-at normalisation
        created_at = _normalize_created_at(doc.get("created_at"))
        if created_at and created_at >= thirty_days_ago:
            dk = created_at.date().isoformat()
            growth_by_date[dk] = growth_by_date.get(dk, 0) + 1
            if created_at >= seven_days_ago:
                cat_growth_by_date.setdefault(cat, {})[dk] = cat_growth_by_date.get(cat, {}).get(dk, 0) + 1

        # Graph links
        gl = doc.get("graph_links", {})
        if gl:
            with_graph_links += 1
            for links in gl.values():
                graph_links_count += len(links) if isinstance(links, list) else (1 if links else 0)

        # Timeline
        tid = doc.get("metadata", {}).get("timeline_id", "root")
        timeline_dist[tid] = timeline_dist.get(tid, 0) + 1

        # Merges
        if doc.get("metadata", {}).get("merged", False):
            merged_count += 1

        # Top-memory candidates
        text = doc.get("text", "")[:100]
        mid = str(doc.get("_id", ""))
        top_by_importance.append({"id": mid, "text": text, "importance": importance, "access_count": access_count})
        top_by_access.append({"id": mid, "text": text, "access_count": access_count, "importance": importance})
        if created_at:
            recent.append({"id": mid, "text": text, "created_at": created_at.isoformat(), "importance": importance})

    return {
        "importances": importances,
        "access_counts": access_counts,
        "emotions": emotions,
        "confidences": confidences,
        "categories": categories,
        "growth_by_date": growth_by_date,
        "cat_growth_by_date": cat_growth_by_date,
        "graph_links_count": graph_links_count,
        "with_graph_links": with_graph_links,
        "timeline_dist": timeline_dist,
        "merged_count": merged_count,
        "top_by_importance": top_by_importance,
        "top_by_access": top_by_access,
        "recent": recent,
    }


def _compute_distributions(m: dict[str, Any]) -> dict[str, Any]:
    """Compute averages, distributions, and health score from raw metrics."""
    importances = m["importances"]
    access_counts = m["access_counts"]
    confidences = m["confidences"]
    emotions = m["emotions"]
    total = len(importances)

    avg_imp = sum(importances) / total if total else 0
    avg_acc = sum(access_counts) / total if total else 0
    avg_emo = sum(emotions) / total if total else 0
    avg_conf = sum(confidences) / total if total else 0.8

    imp_dist = {
        "high": sum(1 for v in importances if v > 0.7),
        "medium": sum(1 for v in importances if 0.3 <= v <= 0.7),
        "low": sum(1 for v in importances if v < 0.3),
    }
    conf_dist = {
        "high": sum(1 for v in confidences if v > 0.7),
        "medium": sum(1 for v in confidences if 0.5 <= v <= 0.7),
        "low": sum(1 for v in confidences if v < 0.5),
    }
    acc_dist = {
        "high": sum(1 for v in access_counts if v > 5),
        "medium": sum(1 for v in access_counts if 1 <= v <= 5),
        "low": sum(1 for v in access_counts if v == 0),
    }

    # Health score (0-100): importance 40% + confidence 30% + access 20% + graph 10%
    imp_score = avg_imp * 40
    conf_score = avg_conf * 30
    acc_score = min((avg_acc / 10.0) * 20, 20)
    graph_score = min((m["with_graph_links"] / max(total, 1)) * 10, 10)
    health = round(imp_score + conf_score + acc_score + graph_score, 1)

    return {
        "avg_importance": avg_imp,
        "avg_access_count": avg_acc,
        "avg_emotion": avg_emo,
        "avg_confidence": avg_conf,
        "imp_dist": imp_dist,
        "conf_dist": conf_dist,
        "acc_dist": acc_dist,
        "health_score": health,
    }


def _build_growth_trends(
    now: datetime,
    growth_by_date: dict[str, int],
    cat_growth_by_date: dict[str, dict[str, int]],
    categories: dict[str, int],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Construct 30-day growth and 7-day category growth timelines."""
    growth = []
    for i in range(30):
        dk = (now - timedelta(days=i)).date().isoformat()
        growth.append({"date": dk, "count": growth_by_date.get(dk, 0)})
    growth.reverse()

    cat_growth: dict[str, list[dict[str, Any]]] = {}
    for cat in categories:
        cg = []
        for i in range(7):
            dk = (now - timedelta(days=i)).date().isoformat()
            cg.append({"date": dk, "count": cat_growth_by_date.get(cat, {}).get(dk, 0)})
        cg.reverse()
        cat_growth[cat] = cg

    return growth, cat_growth


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_memory_analytics(
    collection: ScopedCollectionWrapper,
    user_id: str,
    enable_cognitive: bool = True,
    detect_category_fn: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Get comprehensive analytics about a user's memory health.

    Perfect Recall: all memories are accessible forever.
    """
    try:
        total_count = await collection.count_documents({"user_id": str(user_id)})
        cursor = collection.find({"user_id": str(user_id)})
        docs = await cursor.to_list(length=None)

        if not docs:
            return {"user_id": str(user_id), **_EMPTY_ANALYTICS}

        now = datetime.now(timezone.utc)
        m = _collect_doc_metrics(docs, detect_category_fn, now)
        stats = _compute_distributions(m)
        growth, cat_growth = _build_growth_trends(now, m["growth_by_date"], m["cat_growth_by_date"], m["categories"])

        # Sort top-memory lists
        m["top_by_importance"].sort(key=lambda x: x["importance"], reverse=True)
        m["top_by_access"].sort(key=lambda x: x["access_count"], reverse=True)
        m["recent"].sort(key=lambda x: x.get("created_at", ""), reverse=True)

        merge_rate = (m["merged_count"] / total_count * 100) if total_count > 0 else 0.0
        unique_tl = len(m["timeline_dist"])
        mem_per_tl = total_count / unique_tl if unique_tl > 0 else 0.0
        avg_links = m["graph_links_count"] / m["with_graph_links"] if m["with_graph_links"] > 0 else 0.0

        return {
            "user_id": str(user_id),
            "total_memories": total_count,
            "average_importance": round(stats["avg_importance"], 4),
            "average_access_count": round(stats["avg_access_count"], 2),
            "average_emotion": round(stats["avg_emotion"], 4),
            "average_confidence": round(stats["avg_confidence"], 4),
            "low_importance_memories": stats["imp_dist"]["low"],
            "high_importance_memories": stats["imp_dist"]["high"],
            "categories": m["categories"],
            "memory_growth": growth,
            "access_patterns": {
                "most_accessed": m["top_by_access"][:5],
                "avg_access_count": round(stats["avg_access_count"], 2),
                "access_distribution": stats["acc_dist"],
            },
            "quality_metrics": {
                "avg_importance": round(stats["avg_importance"], 4),
                "avg_confidence": round(stats["avg_confidence"], 4),
                "importance_distribution": stats["imp_dist"],
                "confidence_distribution": stats["conf_dist"],
                "health_score": stats["health_score"],
            },
            "graph_connectivity": {
                "memories_with_links": m["with_graph_links"],
                "total_graph_links": m["graph_links_count"],
                "avg_links_per_memory": round(avg_links, 2),
            },
            "timeline_organization": {
                "timeline_distribution": m["timeline_dist"],
                "memories_per_timeline": round(mem_per_tl, 2),
            },
            "category_trends": {"current": m["categories"], "growth": cat_growth},
            "top_memories": {
                "most_important": m["top_by_importance"][:3],
                "most_accessed": m["top_by_access"][:3],
                "recently_created": m["recent"][:3],
            },
            "merge_statistics": {
                "merged_count": m["merged_count"],
                "merge_rate": round(merge_rate, 2),
            },
        }

    except (PyMongoError, OperationFailure) as e:
        logger.exception("Failed to get analytics for user %s: %s", user_id, e)
        return {"user_id": str(user_id), "error": str(e)}
