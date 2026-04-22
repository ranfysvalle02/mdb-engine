"""
Pluggable rate-limit stores for the admin plane.

Two stores ship out of the box:

- :class:`InMemoryRateLimitStore` — fast, sliding window, bounded LRU.
  Zero dependencies. Correct for **single-process** deployments only.
  Use this in dev, in tests, or when you know you'll always run one
  uvicorn worker behind one pod.

- :class:`MongoRateLimitStore` — fixed-window counter persisted in
  ``_mdb_admin_rate_limits`` with a TTL index. Correct across workers
  and pods. Slightly more latency per request (one ``findAndModify``
  round-trip) and a small 2× burst at window boundaries, which is
  fine for admin-plane traffic.

Select at mount time via manifest::

    {
        "admin_api": {
            "rate_limits": {
                "backend": "mongo",      // or "memory" (default)
                "read":  {"max": 120, "window_seconds": 60},
                "write": {"max": 15,  "window_seconds": 60}
            }
        }
    }

Both stores implement :class:`RateLimitStore`:

    async def hit(key, max_attempts, window_seconds) -> (allowed, retry_after)

A ``True`` means "the request may proceed and has been counted."
``retry_after`` is only meaningful when ``allowed`` is False.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict, deque
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)


RATE_LIMIT_COLLECTION = "_mdb_admin_rate_limits"
"""Where :class:`MongoRateLimitStore` persists counters."""

_DEFAULT_MEMORY_MAX_KEYS = 10_000
"""Upper bound on distinct principal×module×bucket entries retained
in :class:`InMemoryRateLimitStore`. Keeps a long-running process with
heavy failed-auth traffic from growing RSS without bound."""


class RateLimitStore(Protocol):
    """Interface shared by every rate-limit backend."""

    async def hit(
        self,
        key: str,
        *,
        max_attempts: int,
        window_seconds: int,
    ) -> tuple[bool, int]:
        """Record one request against ``key`` and decide whether to allow it.

        Returns ``(allowed, retry_after_seconds)``. When ``allowed`` is
        True the store MUST have incremented the counter — the return
        value is the full side-effect. When False, ``retry_after`` is
        a best-effort seconds-until-reset estimate (lower-bounded at
        1s so clients never spin).
        """
        ...


# ---------------------------------------------------------------------------
# In-memory
# ---------------------------------------------------------------------------


class InMemoryRateLimitStore:
    """Sliding-window counter backed by a bounded LRU of deques.

    Each ``key`` maps to a deque of recent request timestamps. On
    every hit we drop expired entries, then admit iff ``len(q) <
    max_attempts``. Classic sliding window; accurate to the
    millisecond within a single process.

    **Bounded memory**: the LRU cap means a long-running process with
    lots of distinct failed-auth IPs cannot leak. When the cap is
    hit, the least-recently-used key is evicted — effectively "your
    bucket resets if you haven't tried in a while AND we're under
    pressure". Acceptable trade-off for the admin plane; if someone
    is worried about it they should use the Mongo store instead.
    """

    def __init__(self, *, max_keys: int = _DEFAULT_MEMORY_MAX_KEYS):
        self._max_keys = max_keys
        self._buckets: OrderedDict[str, deque[float]] = OrderedDict()

    async def hit(
        self,
        key: str,
        *,
        max_attempts: int,
        window_seconds: int,
    ) -> tuple[bool, int]:
        now = time.time()
        cutoff = now - window_seconds
        q = self._buckets.get(key)
        if q is None:
            q = deque()
            self._buckets[key] = q
        else:
            self._buckets.move_to_end(key)
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= max_attempts:
            retry_after = int(max(1, window_seconds - (now - q[0])))
            return False, retry_after
        q.append(now)
        # Evict LRU if we've crossed the cap. Do it after appending so
        # the caller's bucket is never the one we drop.
        if len(self._buckets) > self._max_keys:
            evicted_key, _ = self._buckets.popitem(last=False)
            logger.debug("rate-limit LRU evicted key=%s (cap=%d)", evicted_key, self._max_keys)
        return True, 0

    def __len__(self) -> int:
        return len(self._buckets)


# ---------------------------------------------------------------------------
# Mongo
# ---------------------------------------------------------------------------


async def bootstrap_rate_limit_collection(mongo_db: AsyncIOMotorDatabase) -> None:
    """Create ``_mdb_admin_rate_limits`` + TTL index if missing.

    Idempotent. Safe to call on every engine boot.
    """
    from pymongo.errors import OperationFailure

    coll = mongo_db[RATE_LIMIT_COLLECTION]
    try:
        await coll.create_index("expires_at", expireAfterSeconds=0, name="ttl_expires_at")
    except OperationFailure as e:
        # Index already exists with a different spec — log and carry on.
        logger.debug("rate-limit TTL index already present: %s", e)


class MongoRateLimitStore:
    """Fixed-window counter persisted in Mongo.

    One document per ``(key, window-aligned-epoch)`` with an ``$inc``
    counter and a TTL index for automatic cleanup. The atomic
    ``findOneAndUpdate(upsert=true, returnDocument=after)`` makes this
    **correct across any number of workers or pods**.

    Trade-offs vs. in-memory:

    - One Mongo round-trip per admin request (~1–3ms typical). The
      liveness probe bypasses rate limiting entirely so this does not
      affect k8s health checks.
    - Fixed-window (not sliding) → up to 2× burst at the window edge.
      Acceptable for admin-plane traffic; this is not a DDoS defense.
    - TTL index cleans up cold windows; no manual GC required.

    Failure mode: if Mongo is down, the store logs once and returns
    ``(True, 0)`` — fail-open. The audit plane stays reachable even
    when the DB is having a bad day, which is more important than
    enforcing a rate limit during an outage.
    """

    def __init__(self, mongo_db: AsyncIOMotorDatabase):
        self._mongo_db = mongo_db
        self._coll = mongo_db[RATE_LIMIT_COLLECTION]
        self._fail_open_logged = False

    async def hit(
        self,
        key: str,
        *,
        max_attempts: int,
        window_seconds: int,
    ) -> tuple[bool, int]:
        from datetime import datetime, timedelta, timezone

        from pymongo import ReturnDocument

        now = time.time()
        window_start_epoch = int(now // window_seconds) * window_seconds
        doc_id = f"{key}|{window_start_epoch}"
        window_end = datetime.fromtimestamp(window_start_epoch + window_seconds, tz=timezone.utc)
        # Give the TTL a small grace buffer so we never race cleanup
        # against a request landing in the final millisecond of a window.
        expires_at = window_end + timedelta(seconds=5)

        try:  # nosemgrep
            doc = await self._coll.find_one_and_update(
                {"_id": doc_id},
                {
                    "$inc": {"count": 1},
                    "$setOnInsert": {"expires_at": expires_at},
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except Exception as e:  # noqa: BLE001 - fail-open on DB trouble
            if not self._fail_open_logged:
                logger.warning("MongoRateLimitStore failing open (Mongo unavailable): %s", e)
                self._fail_open_logged = True
            return True, 0
        count = int(doc.get("count", 1))
        if count > max_attempts:
            retry_after = int(max(1, window_end.timestamp() - now))
            return False, retry_after
        return True, 0


__all__ = [
    "RATE_LIMIT_COLLECTION",
    "InMemoryRateLimitStore",
    "MongoRateLimitStore",
    "RateLimitStore",
    "bootstrap_rate_limit_collection",
]
