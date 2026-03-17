"""Tests for background hooks with retry."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from mdb_engine.routing._hooks import BackgroundHookExecutor


class FakeCollection:
    def __init__(self):
        self.inserted: list[dict] = []

    async def insert_one(self, doc):
        self.inserted.append(doc)
        return MagicMock(inserted_id="fake_id")


class FakeDB:
    def __init__(self):
        self._cols: dict[str, FakeCollection] = {}

    def __getitem__(self, name):
        if name not in self._cols:
            self._cols[name] = FakeCollection()
        return self._cols[name]


class TestBackgroundHookExecutor:
    @pytest.mark.asyncio
    async def test_non_background_runs_inline(self):
        hooks = {
            "after_create": [
                {"action": "insert", "collection": "audit", "document": {"event": "created"}},
            ]
        }
        executor = BackgroundHookExecutor(hooks)
        db = FakeDB()
        await executor.run("after_create", {"_id": "d1"}, None, db)
        assert len(db["audit"].inserted) == 1

    @pytest.mark.asyncio
    async def test_background_runs_in_task(self):
        hooks = {
            "after_create": [
                {
                    "action": "insert",
                    "collection": "audit",
                    "document": {"event": "created"},
                    "background": True,
                    "retry": {"attempts": 1},
                },
            ]
        }
        executor = BackgroundHookExecutor(hooks)
        db = FakeDB()
        await executor.run("after_create", {"_id": "d1"}, None, db)
        await asyncio.sleep(0.1)
        assert len(db["audit"].inserted) == 1

    @pytest.mark.asyncio
    async def test_background_retry_on_failure(self):
        call_count = 0

        class FailOnceCollection:
            def __init__(self):
                self.inserted = []

            async def insert_one(self, doc):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise RuntimeError("Transient failure")
                self.inserted.append(doc)
                return MagicMock(inserted_id="id")

        hooks = {
            "after_create": [
                {
                    "action": "insert",
                    "collection": "audit",
                    "document": {"event": "created"},
                    "background": True,
                    "retry": {"attempts": 3, "backoff": "fixed"},
                },
            ]
        }
        executor = BackgroundHookExecutor(hooks)
        db = FakeDB()
        db._cols["audit"] = FailOnceCollection()

        await executor.run("after_create", {"_id": "d1"}, None, db)
        await asyncio.sleep(2.5)

        assert call_count >= 2
        assert len(db["audit"].inserted) >= 1

    @pytest.mark.asyncio
    async def test_background_logs_failure_after_max_retries(self):
        hooks = {
            "after_create": [
                {
                    "action": "insert",
                    "collection": "audit",
                    "document": {"event": "created"},
                    "background": True,
                    "retry": {"attempts": 1, "backoff": "fixed"},
                },
            ]
        }
        executor = BackgroundHookExecutor(hooks)
        db = FakeDB()

        async def always_fail(doc):
            raise RuntimeError("Permanent failure")

        db["audit"].insert_one = always_fail
        await executor.run("after_create", {"_id": "d1"}, None, db)
        await asyncio.sleep(0.5)
        assert len(db["_hook_failures"].inserted) == 1
