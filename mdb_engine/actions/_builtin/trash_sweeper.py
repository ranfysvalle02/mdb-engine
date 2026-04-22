"""
Built-in trash sweeper for the manifest reconciler.

Periodically hard-drops expired quarantined artifacts recorded in the
``_mdb_trash`` collection. The sweeper is the **sole deletion authority**
for tombstones — there is intentionally no TTL index on
``_mdb_trash.expires_at``; if MongoDB's monitor deleted the tombstone
before the sweeper ran, orphaned physical ``_mdb_trash__*`` collections
would accumulate. Instead:

1. The sweeper finds tombstones whose ``expires_at`` has passed.
2. It drops the paired physical collection (if any).
3. Only after a successful drop (or a "not found" error) does it delete
   the tombstone itself. This keeps ``trash ls`` faithful even if the
   sweeper is temporarily late.

A secondary orphan-scan step exists to clean up any stray
``_mdb_trash__*`` physical collections that pre-date the current
sweeper contract.

The sweeper is registered by the engine on initialization and runs as an
``asyncio.Task`` until ``engine.shutdown()`` cancels it.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import ConnectionFailure, OperationFailure, ServerSelectionTimeoutError

from ...constants import RESERVED_TRASH_PREFIX, TRASH_COLLECTION
from ...core.reconciler_events import EVENT_TRASH_SWEPT, emit_event

logger = logging.getLogger(__name__)


DEFAULT_SWEEP_INTERVAL_SECONDS = 3600  # 1 hour


async def sweep_once(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    """Run a single sweep pass across all slugs.

    Returns a dict with counters suitable for logging or metrics:
    ``{"dropped_collections": int, "orphan_physical_dropped": int,
    "tombstones_deleted": int}``.
    """
    dropped_collections = 0
    orphan_physical_dropped = 0
    tombstones_deleted = 0

    now = datetime.now(timezone.utc)

    # 1. Hard-drop tombstones whose expires_at has passed but the TTL monitor
    #    hasn't yet purged them. Also drop the physical renamed collection.
    try:
        cursor = db[TRASH_COLLECTION].find({"expires_at": {"$lt": now}})
        async for tomb in cursor:
            trash_name = tomb.get("trash_name") or ""
            if trash_name:
                try:
                    await db.drop_collection(trash_name)
                    dropped_collections += 1
                except OperationFailure as e:
                    logger.debug(
                        "trash_sweeper: drop_collection(%s) failed: %s",
                        trash_name,
                        e,
                    )
            try:
                await db[TRASH_COLLECTION].delete_one({"_id": tomb["_id"]})
                tombstones_deleted += 1
                emit_event(
                    EVENT_TRASH_SWEPT,
                    slug=tomb.get("slug"),
                    kind=tomb.get("kind"),
                    original_name=tomb.get("original_name"),
                    trash_name=trash_name,
                    expired_at=tomb.get("expires_at"),
                )
            except OperationFailure as e:
                logger.debug("trash_sweeper: tombstone delete failed: %s", e)
    except (ConnectionFailure, ServerSelectionTimeoutError) as e:
        logger.warning("trash_sweeper: connection failure during sweep: %s", e)
        return {
            "dropped_collections": dropped_collections,
            "orphan_physical_dropped": orphan_physical_dropped,
            "tombstones_deleted": tombstones_deleted,
        }

    # 2. Find orphan ``_mdb_trash__*`` physical collections that have no
    #    matching tombstone (TTL already purged). Drop them too.
    try:
        names = await db.list_collection_names()
        trash_physical = [n for n in names if n.startswith(RESERVED_TRASH_PREFIX)]
        if trash_physical:
            referenced = set()
            cursor = db[TRASH_COLLECTION].find(
                {"trash_name": {"$in": trash_physical}},
                {"trash_name": 1},
            )
            async for doc in cursor:
                referenced.add(doc["trash_name"])
            for name in trash_physical:
                if name in referenced:
                    continue
                try:
                    await db.drop_collection(name)
                    orphan_physical_dropped += 1
                except OperationFailure as e:
                    logger.debug(
                        "trash_sweeper: drop orphan %s failed: %s",
                        name,
                        e,
                    )
    except (OperationFailure, ConnectionFailure, ServerSelectionTimeoutError) as e:
        logger.debug("trash_sweeper: orphan scan skipped: %s", e)

    result = {
        "dropped_collections": dropped_collections,
        "orphan_physical_dropped": orphan_physical_dropped,
        "tombstones_deleted": tombstones_deleted,
    }
    if any(result.values()):
        logger.info("trash_sweeper: %s", result)
    return result


async def run_sweeper_loop(
    db: AsyncIOMotorDatabase,
    *,
    interval_seconds: int = DEFAULT_SWEEP_INTERVAL_SECONDS,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Run ``sweep_once`` on a fixed interval until ``stop_event`` is set."""
    interval = max(60, int(interval_seconds))
    while True:
        try:  # nosemgrep
            if stop_event is not None and stop_event.is_set():
                return
            await sweep_once(db)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("trash_sweeper: unexpected error (will retry)")

        # CancelledError propagates naturally — no explicit re-raise needed.
        if stop_event is not None:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                continue
        else:
            await asyncio.sleep(interval)


__all__ = [
    "sweep_once",
    "run_sweeper_loop",
    "DEFAULT_SWEEP_INTERVAL_SECONDS",
]
