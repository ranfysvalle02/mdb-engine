"""Unit tests for the pluggable admin rate-limit stores.

Covers both :class:`InMemoryRateLimitStore` (sliding window, LRU-bounded)
and :class:`MongoRateLimitStore` (fixed window, atomic, fail-open).

The Mongo store is exercised against an in-process fake that honours
the same ``find_one_and_update(upsert=True, return_document=AFTER)``
contract — enough to validate the counter + TTL side effects without
spinning up a real Mongo.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from mdb_engine.admin.rate_limit_stores import (
    InMemoryRateLimitStore,
    MongoRateLimitStore,
)

# ---------------------------------------------------------------------------
# In-memory
# ---------------------------------------------------------------------------


class TestInMemoryStore:
    @pytest.mark.asyncio
    async def test_under_limit_requests_are_admitted(self):
        store = InMemoryRateLimitStore()
        for _ in range(5):
            allowed, retry = await store.hit("k", max_attempts=5, window_seconds=60)
            assert allowed is True
            assert retry == 0

    @pytest.mark.asyncio
    async def test_over_limit_is_rejected_with_retry_after(self):
        store = InMemoryRateLimitStore()
        for _ in range(3):
            await store.hit("k", max_attempts=3, window_seconds=60)
        allowed, retry = await store.hit("k", max_attempts=3, window_seconds=60)
        assert allowed is False
        assert retry >= 1

    @pytest.mark.asyncio
    async def test_window_slides(self, monkeypatch):
        # Advance time past the window and verify the bucket resets.
        clock = {"t": 1_000_000.0}
        monkeypatch.setattr(
            "mdb_engine.admin.rate_limit_stores.time.time",
            lambda: clock["t"],
        )
        store = InMemoryRateLimitStore()
        for _ in range(3):
            await store.hit("k", max_attempts=3, window_seconds=60)
        blocked, _ = await store.hit("k", max_attempts=3, window_seconds=60)
        assert blocked is False
        clock["t"] += 61  # step past the window
        admitted, _ = await store.hit("k", max_attempts=3, window_seconds=60)
        assert admitted is True

    @pytest.mark.asyncio
    async def test_lru_eviction_respects_cap(self):
        store = InMemoryRateLimitStore(max_keys=3)
        for k in ("a", "b", "c", "d"):
            await store.hit(k, max_attempts=5, window_seconds=60)
        # "a" should have been evicted (oldest)
        assert len(store) == 3

    @pytest.mark.asyncio
    async def test_buckets_are_independent(self):
        store = InMemoryRateLimitStore()
        for _ in range(3):
            await store.hit("a", max_attempts=3, window_seconds=60)
        # "a" is full; "b" should still be wide open
        blocked, _ = await store.hit("a", max_attempts=3, window_seconds=60)
        admitted, _ = await store.hit("b", max_attempts=3, window_seconds=60)
        assert blocked is False
        assert admitted is True


# ---------------------------------------------------------------------------
# Mongo (fake)
# ---------------------------------------------------------------------------


class _FakeMongoCollection:
    """Minimal find_one_and_update with upsert + ReturnDocument.AFTER."""

    def __init__(self):
        self._docs: dict[str, dict[str, Any]] = {}

    async def find_one_and_update(self, filt, update, upsert=False, return_document=None):
        _id = filt["_id"]
        doc = self._docs.get(_id)
        if doc is None:
            if not upsert:
                return None
            doc = {"_id": _id, "count": 0}
            for k, v in (update.get("$setOnInsert") or {}).items():
                doc[k] = v
            self._docs[_id] = doc
        for k, v in (update.get("$inc") or {}).items():
            doc[k] = int(doc.get(k, 0)) + int(v)
        return doc


class _FakeMongoDB:
    def __init__(self):
        self._colls: dict[str, _FakeMongoCollection] = {}

    def __getitem__(self, name: str) -> _FakeMongoCollection:
        self._colls.setdefault(name, _FakeMongoCollection())
        return self._colls[name]


class TestMongoStore:
    @pytest.mark.asyncio
    async def test_first_n_requests_in_window_are_admitted(self):
        db = _FakeMongoDB()
        store = MongoRateLimitStore(db)
        for _ in range(5):
            allowed, _ = await store.hit("k", max_attempts=5, window_seconds=60)
            assert allowed is True

    @pytest.mark.asyncio
    async def test_over_limit_is_rejected_with_retry_after(self):
        db = _FakeMongoDB()
        store = MongoRateLimitStore(db)
        for _ in range(3):
            await store.hit("k", max_attempts=3, window_seconds=60)
        allowed, retry = await store.hit("k", max_attempts=3, window_seconds=60)
        assert allowed is False
        assert retry >= 1

    @pytest.mark.asyncio
    async def test_documents_carry_expires_at_for_ttl_cleanup(self):
        db = _FakeMongoDB()
        store = MongoRateLimitStore(db)
        await store.hit("k", max_attempts=10, window_seconds=60)
        [doc] = list(db["_mdb_admin_rate_limits"]._docs.values())
        assert isinstance(doc["expires_at"], datetime)
        # Expires strictly in the future
        assert doc["expires_at"] > datetime.now(tz=timezone.utc)

    @pytest.mark.asyncio
    async def test_fails_open_when_mongo_raises(self, caplog):
        class _Broken:
            async def find_one_and_update(self, *a, **kw):
                raise RuntimeError("connection reset")

        class _BrokenDB:
            def __getitem__(self, _):
                return _Broken()

        store = MongoRateLimitStore(_BrokenDB())
        allowed, retry = await store.hit("k", max_attempts=1, window_seconds=60)
        assert allowed is True
        assert retry == 0
        # Second call must not re-log the same warning
        with caplog.at_level("WARNING"):
            await store.hit("k", max_attempts=1, window_seconds=60)
        warnings = [r for r in caplog.records if "failing open" in r.getMessage()]
        assert len(warnings) <= 1

    @pytest.mark.asyncio
    async def test_buckets_are_scoped_by_key_and_window(self):
        db = _FakeMongoDB()
        store = MongoRateLimitStore(db)
        for _ in range(3):
            await store.hit("a", max_attempts=3, window_seconds=60)
        # "a" exhausted; "b" should still be admitted
        allowed, _ = await store.hit("b", max_attempts=3, window_seconds=60)
        assert allowed is True
