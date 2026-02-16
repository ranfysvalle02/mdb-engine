"""Community profile builder — MongoDB aggregation pipelines.

Aggregates anonymous statistics across all users of an app.
No LLM calls — pure MongoDB aggregation for privacy and speed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


async def build_community_profile(
    memory_collection: Any,
    graph_collection: Any | None,
    app_slug: str,
    min_users: int = 3,
) -> dict[str, Any]:
    """Build an anonymous community profile via MongoDB aggregation.

    Args:
        memory_collection: The app's memory collection (scoped).
        graph_collection: The app's graph/KG collection (scoped).
            Can be None if graph service is unavailable.
        app_slug: App slug for scoping.
        min_users: Minimum users required before aggregation.

    Returns:
        Community profile dict matching ``CommunityProfileDict``.
    """
    now = datetime.now(timezone.utc)
    thirty_days_ago = now - timedelta(days=30)

    profile: dict[str, Any] = {
        "app_slug": app_slug,
        "version": 1,
        "last_rebuild": now,
        "created_at": now,
        "updated_at": now,
    }

    # ---------------------------------------------------------------
    # 1. Population statistics
    # ---------------------------------------------------------------
    try:
        total_users = await _count_distinct_users(memory_collection)
        active_users = await _count_distinct_users(
            memory_collection,
            date_filter={"created_at": {"$gte": thirty_days_ago}},
        )
        profile["population"] = {
            "total_users": total_users,
            "active_users_30d": active_users,
        }
        profile["user_count_at_build"] = total_users

        if total_users < min_users:
            logger.info(f"[Community] Skipping aggregation: {total_users} users " f"< min_users={min_users}")
            return profile

    except (TypeError, ValueError, RuntimeError) as e:
        logger.warning(f"[Community] Population count failed: {e}")
        profile["population"] = {"total_users": 0, "active_users_30d": 0}
        return profile

    # ---------------------------------------------------------------
    # 2. Common preferences (from preference memories)
    # ---------------------------------------------------------------
    try:
        top_interests = await _aggregate_preference_interests(memory_collection, total_users)
        profile["common_preferences"] = {
            "top_interests": top_interests[:20],
            "trending": [],  # Could compare against 30-day-ago snapshot
        }
    except (TypeError, ValueError, RuntimeError) as e:
        logger.warning(f"[Community] Preference aggregation failed: {e}")
        profile["common_preferences"] = {"top_interests": [], "trending": []}

    # ---------------------------------------------------------------
    # 3. Shared knowledge (from graph)
    # ---------------------------------------------------------------
    if graph_collection is not None:
        try:
            popular_entities = await _aggregate_popular_entities(graph_collection, total_users)
            common_relations = await _aggregate_common_relations(graph_collection)
            profile["shared_knowledge"] = {
                "popular_entities": popular_entities[:20],
                "common_relations": common_relations[:20],
            }
        except (TypeError, ValueError, RuntimeError) as e:
            logger.warning(f"[Community] Graph aggregation failed: {e}")
            profile["shared_knowledge"] = {
                "popular_entities": [],
                "common_relations": [],
            }
    else:
        profile["shared_knowledge"] = {"popular_entities": [], "common_relations": []}

    # ---------------------------------------------------------------
    # 4. Memory landscape
    # ---------------------------------------------------------------
    try:
        landscape = await _aggregate_memory_landscape(memory_collection, total_users)
        profile["memory_landscape"] = landscape
    except (TypeError, ValueError, RuntimeError) as e:
        logger.warning(f"[Community] Landscape aggregation failed: {e}")
        profile["memory_landscape"] = {
            "avg_memories_per_user": 0.0,
            "category_distribution": {},
        }

    logger.info(
        f"[Community] Profile built for '{app_slug}': "
        f"{total_users} users, "
        f"{len(profile.get('common_preferences', {}).get('top_interests', []))} interests"
    )
    return profile


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------


async def _count_distinct_users(
    collection: Any,
    date_filter: dict[str, Any] | None = None,
) -> int:
    """Count distinct user_ids in a collection."""
    match_stage: dict[str, Any] = {}
    if date_filter:
        match_stage.update(date_filter)

    pipeline: list[dict[str, Any]] = []
    if match_stage:
        pipeline.append({"$match": match_stage})
    pipeline.append({"$group": {"_id": "$user_id"}})
    pipeline.append({"$count": "total"})

    results = []
    async for doc in collection.aggregate(pipeline):
        results.append(doc)

    return results[0]["total"] if results else 0


async def _aggregate_preference_interests(
    collection: Any,
    total_users: int,
) -> list[dict[str, Any]]:
    """Aggregate the most common interests from preference memories.

    Extracts key phrases from memories categorized as "preferences"
    and counts how many distinct users mention each.
    """
    pipeline = [
        # Only preference memories
        {
            "$match": {
                "$or": [
                    {"category": "preferences"},
                    {"metadata.category": "preferences"},
                ],
            }
        },
        # Group by user_id + memory text to deduplicate per user
        {
            "$group": {
                "_id": {
                    "user_id": "$user_id",
                    "text": {"$toLower": {"$ifNull": ["$text", "$memory"]}},
                },
            }
        },
        # Group by text across users, count distinct users
        {
            "$group": {
                "_id": "$_id.text",
                "user_count": {"$sum": 1},
            }
        },
        {"$sort": {"user_count": -1}},
        {"$limit": 20},
    ]

    results = []
    async for doc in collection.aggregate(pipeline):
        text = doc["_id"] or ""
        if text and len(text) > 2:
            results.append(
                {
                    "item": text,
                    "user_count": doc["user_count"],
                    "pct": round(doc["user_count"] / max(total_users, 1), 3),
                }
            )

    return results


async def _aggregate_popular_entities(
    graph_collection: Any,
    total_users: int,
) -> list[dict[str, Any]]:
    """Aggregate the most popular graph entities across users."""
    pipeline = [
        # Exclude user-specific nodes (type "person" with ObjectId-like names)
        {"$match": {"type": {"$nin": ["person"]}}},
        # Group by node type + name, count distinct user_ids
        {
            "$group": {
                "_id": {"type": "$type", "name": "$name"},
                "user_ids": {"$addToSet": "$user_id"},
            }
        },
        {"$addFields": {"user_count": {"$size": "$user_ids"}}},
        {"$sort": {"user_count": -1}},
        {"$limit": 20},
        {"$project": {"user_ids": 0}},  # Don't leak user IDs
    ]

    results = []
    async for doc in graph_collection.aggregate(pipeline):
        results.append(
            {
                "name": doc["_id"].get("name", "unknown"),
                "type": doc["_id"].get("type", "unknown"),
                "user_count": doc["user_count"],
            }
        )

    return results


async def _aggregate_common_relations(
    graph_collection: Any,
) -> list[dict[str, Any]]:
    """Aggregate common relationship patterns from graph edges."""
    pipeline = [
        {"$match": {"edges": {"$exists": True, "$ne": []}}},
        {"$unwind": "$edges"},
        {"$match": {"edges.active": True}},
        {
            "$group": {
                "_id": {
                    "source_type": "$type",
                    "relation": "$edges.relation",
                    # Extract target type from target ID (format: "type:name")
                    "target_type": {
                        "$arrayElemAt": [
                            {"$split": ["$edges.target", ":"]},
                            0,
                        ]
                    },
                },
                "count": {"$sum": 1},
            }
        },
        {"$sort": {"count": -1}},
        {"$limit": 20},
    ]

    results = []
    async for doc in graph_collection.aggregate(pipeline):
        results.append(
            {
                "source_type": doc["_id"].get("source_type", "?"),
                "relation": doc["_id"].get("relation", "?"),
                "target_type": doc["_id"].get("target_type", "?"),
                "count": doc["count"],
            }
        )

    return results


async def _aggregate_memory_landscape(
    collection: Any,
    total_users: int,
) -> dict[str, Any]:
    """Aggregate memory category distribution across users."""
    # Total memory count
    pipeline_count = [{"$count": "total"}]
    total_memories = 0
    async for doc in collection.aggregate(pipeline_count):
        total_memories = doc["total"]

    # Category distribution
    pipeline_cats = [
        {
            "$group": {
                "_id": {
                    "$ifNull": [
                        "$category",
                        {"$ifNull": ["$metadata.category", "unknown"]},
                    ]
                },
                "count": {"$sum": 1},
            }
        },
        {"$sort": {"count": -1}},
    ]

    category_dist: dict[str, float] = {}
    async for doc in collection.aggregate(pipeline_cats):
        cat = doc["_id"] or "unknown"
        if total_memories > 0:
            category_dist[cat] = round(doc["count"] / total_memories, 3)

    return {
        "avg_memories_per_user": round(total_memories / max(total_users, 1), 1),
        "category_distribution": category_dist,
    }
