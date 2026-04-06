"""
Pluggable broadcast backends for cross-process WebSocket fan-out.

The default ``InProcessBackend`` delivers messages only within the current
process (same as pre-0.12 behaviour).  ``MongoDBChangeStreamBackend`` uses
a MongoDB capped collection as a pub/sub channel so that every process
subscribed to the change stream receives broadcast messages — no Redis or
external broker required.

Usage::

    from mdb_engine.routing._broadcast import (
        MongoDBChangeStreamBackend,
        set_broadcast_backend,
    )

    backend = MongoDBChangeStreamBackend(db=engine.connection_manager.mongo_db)
    await backend.initialize()
    set_broadcast_backend(backend)
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class BroadcastBackend(Protocol):
    """Minimal interface that broadcast backends must implement."""

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        """Publish *message* to *channel*."""
        ...

    async def subscribe(self, channel: str) -> AsyncIterator[dict[str, Any]]:
        """Yield messages from *channel* as they arrive."""
        ...

    async def shutdown(self) -> None:
        """Release resources."""
        ...


# ---------------------------------------------------------------------------
# In-process backend (default — single-worker)
# ---------------------------------------------------------------------------


class InProcessBackend:
    """Delivers messages only within the current process.

    This is the default backend and matches the pre-0.12 behaviour where
    ``broadcast_to_app`` only reaches sockets connected to the same worker.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        for q in self._subscribers.get(channel, []):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                pass

    async def subscribe(self, channel: str) -> AsyncIterator[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subscribers.setdefault(channel, []).append(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subscribers[channel].remove(q)

    async def shutdown(self) -> None:
        self._subscribers.clear()


# ---------------------------------------------------------------------------
# MongoDB capped-collection backend (multi-worker / multi-container)
# ---------------------------------------------------------------------------

_CAPPED_COLLECTION = "_mdb_engine_ws_broadcast"
_CAPPED_SIZE_BYTES = 4 * 1024 * 1024  # 4 MB — holds ~4 000 messages
_CAPPED_MAX_DOCS = 10_000


class MongoDBChangeStreamBackend:
    """Fan-out broadcast via a MongoDB capped collection + change stream.

    How it works:

    1. ``publish()`` inserts a document into the capped collection.
    2. Each process calls ``subscribe()`` which opens a change stream on
       the collection and yields every new insert.
    3. The local ``WebSocketConnectionManager`` delivers the message to
       its in-process connections.

    The capped collection acts as a lightweight pub/sub ring buffer.
    MongoDB's change stream infrastructure handles the fan-out.
    """

    def __init__(self, db: Any) -> None:
        self._db = db
        self._collection: Any | None = None
        self._watchers: list[asyncio.Task] = []

    async def initialize(self) -> None:
        """Create the capped collection if it does not exist."""
        existing = await self._db.list_collection_names()
        if _CAPPED_COLLECTION not in existing:
            try:
                await self._db.create_collection(
                    _CAPPED_COLLECTION,
                    capped=True,
                    size=_CAPPED_SIZE_BYTES,
                    max=_CAPPED_MAX_DOCS,
                )
                logger.info(f"Created capped collection '{_CAPPED_COLLECTION}' for WS broadcast")
            except (KeyError, TypeError, RuntimeError, OSError) as exc:
                logger.debug(f"Capped collection '{_CAPPED_COLLECTION}' may already exist: {exc}")
        self._collection = self._db[_CAPPED_COLLECTION]

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        if self._collection is None:
            await self.initialize()
        doc = {
            "channel": channel,
            "payload": json.dumps(message),
            "ts": datetime.now(timezone.utc),
        }
        try:
            await self._collection.insert_one(doc)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("Failed to publish broadcast message to MongoDB: %s", exc)

    async def subscribe(self, channel: str) -> AsyncIterator[dict[str, Any]]:
        """Open a change stream and yield inserts matching *channel*."""
        if self._collection is None:
            await self.initialize()

        pipeline = [
            {"$match": {"operationType": "insert", "fullDocument.channel": channel}},
        ]

        try:
            async with self._collection.watch(pipeline, full_document="updateLookup") as stream:
                async for change in stream:
                    doc = change.get("fullDocument", {})
                    payload_str = doc.get("payload", "{}")
                    try:
                        yield json.loads(payload_str)
                    except (json.JSONDecodeError, TypeError):
                        continue
        except (OSError, RuntimeError, StopAsyncIteration) as exc:
            logger.warning("Change stream error in MongoDBChangeStreamBackend: %s", exc)

    async def shutdown(self) -> None:
        for task in self._watchers:
            task.cancel()
        self._watchers.clear()


# ---------------------------------------------------------------------------
# Module-level singleton + setter
# ---------------------------------------------------------------------------

_backend: BroadcastBackend = InProcessBackend()


def get_broadcast_backend() -> BroadcastBackend:
    """Return the currently active broadcast backend."""
    return _backend


def set_broadcast_backend(backend: BroadcastBackend) -> None:
    """Replace the module-level broadcast backend.

    Call this at app startup before any WebSocket connections are accepted.
    """
    global _backend
    _backend = backend
    logger.info(f"Broadcast backend set to {type(backend).__name__}")


async def start_subscriber(
    channel: str,
    on_message: Callable[[dict[str, Any]], Any],
) -> asyncio.Task:
    """Start a background task that subscribes to *channel* and calls
    *on_message* for every incoming broadcast.

    Returns the ``asyncio.Task`` so the caller can cancel it on shutdown.
    """

    async def _loop() -> None:
        async for msg in _backend.subscribe(channel):
            try:
                result = on_message(msg)
                if asyncio.iscoroutine(result):
                    await result
            except (OSError, RuntimeError, TypeError, ValueError):
                logger.exception("Error in broadcast subscriber callback")

    task = asyncio.create_task(_loop())
    return task
