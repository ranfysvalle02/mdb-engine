"""
ChangeStreamWatcher — background task that bridges MongoDB Change Streams
to the :class:`RealtimeManager` event dispatcher.

Opens a single DB-level ``watch()`` cursor filtered to the physical
collection names that have ``realtime: true``, then forwards each event
to the manager for fan-out to WebSocket subscribers.

Requires a MongoDB **replica set** (or Atlas).  On a standalone instance
the watcher logs a clear warning and does not start.
"""

from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import TYPE_CHECKING, Any

from pymongo.errors import PyMongoError

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorDatabase

    from .manager import RealtimeManager

_BACKOFF_BASE = 1.0
_BACKOFF_CAP = 30.0


class WatcherState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    STOPPED = "stopped"
    FAILED = "failed"


class ChangeStreamWatcher:
    """Watch a MongoDB database for changes and dispatch to subscribers."""

    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        manager: RealtimeManager,
        watched_collections: dict[str, set[str]],
    ) -> None:
        """
        Args:
            db: Raw Motor database (``engine.connection_manager.mongo_db``).
            manager: :class:`RealtimeManager` that dispatches events.
            watched_collections: ``{app_slug: {physical_collection_name, ...}}``.
        """
        self._db = db
        self._manager = manager

        self._all_physical: set[str] = set()
        for names in watched_collections.values():
            self._all_physical |= names

        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._state = WatcherState.IDLE
        self._resume_token: dict[str, Any] | None = None

    # ── Public API ───────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._state in (WatcherState.RUNNING, WatcherState.RECONNECTING)

    @property
    def state(self) -> WatcherState:
        return self._state

    async def start(self) -> None:
        """Start the background watcher task."""
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="realtime-watcher")
        logger.info(
            "Change Stream watcher started for %d collection(s)",
            len(self._all_physical),
        )

    async def stop(self) -> None:
        """Signal the watcher to stop and wait for it to finish."""
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._state = WatcherState.STOPPED
        logger.info("Change Stream watcher stopped")

    # ── Internal loop ────────────────────────────────────────────────

    async def _run(self) -> None:
        backoff = _BACKOFF_BASE
        while not self._stop_event.is_set():
            try:
                await self._watch_loop()
                break  # clean exit
            except asyncio.CancelledError:
                break
            except (PyMongoError, OSError, RuntimeError) as exc:
                if self._is_not_replica_set(exc):
                    logger.warning(
                        "Change Streams require a MongoDB replica set or Atlas. "
                        "Realtime subscriptions are disabled for this deployment."
                    )
                    self._state = WatcherState.FAILED
                    return

                self._state = WatcherState.RECONNECTING
                logger.warning(
                    "Change Stream lost (%s). Reconnecting in %.1fs …",
                    exc,
                    backoff,
                )
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                    break  # stop was requested during wait
                except asyncio.TimeoutError:
                    pass

                backoff = min(backoff * 2, _BACKOFF_CAP)

        self._state = WatcherState.STOPPED

    async def _watch_loop(self) -> None:
        """Open the Change Stream and iterate until stopped or error."""
        pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    "ns.coll": {"$in": sorted(self._all_physical)},
                    "operationType": {"$in": ["insert", "update", "replace", "delete"]},
                }
            }
        ]

        kwargs: dict[str, Any] = {"full_document": "updateLookup"}
        if self._resume_token is not None:
            kwargs["resume_after"] = self._resume_token

        self._state = WatcherState.RUNNING
        async with self._db.watch(pipeline, **kwargs) as stream:
            async for event in stream:
                if self._stop_event.is_set():
                    break
                self._resume_token = stream.resume_token
                try:
                    await self._manager.dispatch(event)
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    logger.exception("Error dispatching change event: %s", exc)

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _is_not_replica_set(exc: BaseException) -> bool:
        msg = str(exc).lower()
        return (
            "replica set" in msg
            or "the $changestream stage is only supported on replica sets" in msg
            or "not allowed" in msg
            and "changestream" in msg
        )
