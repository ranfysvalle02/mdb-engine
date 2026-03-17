"""Tests for cascade delete policies."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from bson import ObjectId

from mdb_engine.routing.auto_crud import _execute_cascade


class FakeCollection:
    def __init__(self):
        self.deleted: list[dict] = []
        self.updated: list[tuple[dict, dict]] = []

    async def delete_many(self, filter_):
        self.deleted.append(filter_)
        result = MagicMock()
        result.deleted_count = 1
        return result

    async def update_many(self, filter_, update):
        self.updated.append((filter_, update))
        result = MagicMock()
        result.modified_count = 1
        return result


class FakeDB:
    def __init__(self):
        self._cols: dict[str, FakeCollection] = {}

    def __getitem__(self, name):
        if name not in self._cols:
            self._cols[name] = FakeCollection()
        return self._cols[name]


class TestCascadeDelete:
    @pytest.mark.asyncio
    async def test_cascade_hard_delete(self):
        db = FakeDB()
        rules = [
            {"collection": "comments", "match_field": "post_id", "action": "delete"},
            {"collection": "reactions", "match_field": "post_id", "action": "delete"},
        ]
        doc = {"_id": ObjectId(), "title": "Test"}
        await _execute_cascade(rules, doc, db)

        assert len(db["comments"].deleted) == 1
        assert db["comments"].deleted[0]["post_id"] == str(doc["_id"])
        assert len(db["reactions"].deleted) == 1

    @pytest.mark.asyncio
    async def test_cascade_soft_delete(self):
        db = FakeDB()
        rules = [
            {"collection": "comments", "match_field": "post_id", "action": "soft_delete"},
        ]
        doc = {"_id": ObjectId(), "title": "Test"}
        await _execute_cascade(rules, doc, db, soft=True)

        col = db["comments"]
        assert len(col.updated) == 1
        filt, update = col.updated[0]
        assert filt["post_id"] == str(doc["_id"])
        assert "$set" in update
        assert "deleted_at" in update["$set"]

    @pytest.mark.asyncio
    async def test_no_cascade_when_empty(self):
        db = FakeDB()
        doc = {"_id": ObjectId()}
        await _execute_cascade([], doc, db)

    @pytest.mark.asyncio
    async def test_cascade_skips_invalid_rule(self):
        db = FakeDB()
        rules = [{"collection": "", "match_field": ""}]
        doc = {"_id": ObjectId()}
        await _execute_cascade(rules, doc, db)

    @pytest.mark.asyncio
    async def test_cascade_failure_does_not_propagate(self):
        """Cascade failure should be logged, not raised."""
        db = FakeDB()
        col = db["comments"]

        async def failing_delete(filter_):
            raise RuntimeError("DB error")

        col.delete_many = failing_delete
        rules = [{"collection": "comments", "match_field": "post_id", "action": "delete"}]
        doc = {"_id": ObjectId()}
        await _execute_cascade(rules, doc, db)
