"""
Tests for the HookExecutor and related hook infrastructure.

Covers:
- A.2 Atomic update operators ($inc, $push, $set passthrough vs auto-wrap)
- A.1 Conditional hooks (if expressions with doc/prev)
- A.3 Delete and HTTP hook actions
- Template resolution inside hook actions
- Fire-and-forget: hook failures don't propagate
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mdb_engine.routing._hooks import (
    HookExecutor,
    _evaluate_condition,
    _has_update_operators,
)

# ── Helpers ──────────────────────────────────────────────────────────────


class FakeCollection:
    """Minimal in-memory collection mock for hook tests."""

    def __init__(self) -> None:
        self.inserted: list[dict] = []
        self.updated: list[tuple[dict, dict]] = []
        self.deleted: list[dict] = []

    async def insert_one(self, doc: dict) -> MagicMock:
        self.inserted.append(doc)
        result = MagicMock()
        result.inserted_id = "fake_id"
        return result

    async def update_many(self, filter_: dict, update: dict) -> MagicMock:
        self.updated.append((filter_, update))
        result = MagicMock()
        result.modified_count = 1
        return result

    async def delete_many(self, filter_: dict) -> MagicMock:
        self.deleted.append(filter_)
        result = MagicMock()
        result.deleted_count = 1
        return result


class FakeDB:
    """Dict-like DB wrapper that returns FakeCollections."""

    def __init__(self) -> None:
        self._collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]


# ═════════════════════════════════════════════════════════════════════════
# A.2 — Atomic Update Operators
# ═════════════════════════════════════════════════════════════════════════


class TestHasUpdateOperators:
    def test_detects_dollar_inc(self):
        assert _has_update_operators({"$inc": {"count": 1}}) is True

    def test_detects_dollar_set(self):
        assert _has_update_operators({"$set": {"name": "x"}}) is True

    def test_detects_mixed(self):
        assert _has_update_operators({"$inc": {"a": 1}, "$set": {"b": 2}}) is True

    def test_no_operators(self):
        assert _has_update_operators({"name": "val", "count": 1}) is False

    def test_empty_dict(self):
        assert _has_update_operators({}) is False


class TestUpdateHookAtomicOperators:
    @pytest.fixture()
    def db(self) -> FakeDB:
        return FakeDB()

    @pytest.mark.asyncio
    async def test_dollar_inc_passes_through(self, db: FakeDB):
        """$inc in hook update should NOT be wrapped in $set."""
        hooks = {
            "after_create": [
                {
                    "action": "update",
                    "collection": "posts",
                    "filter": {"_id": "{{doc.post_id}}"},
                    "update": {"$inc": {"comment_count": 1}},
                }
            ]
        }
        executor = HookExecutor(hooks)
        doc = {"_id": "c1", "post_id": "p1"}
        await executor.run("after_create", doc, None, db)

        col = db["posts"]
        assert len(col.updated) == 1
        filt, update = col.updated[0]
        assert filt == {"_id": "p1"}
        assert update == {"$inc": {"comment_count": 1}}

    @pytest.mark.asyncio
    async def test_plain_fields_wrapped_in_set(self, db: FakeDB):
        """Fields without $ operators should be auto-wrapped in $set."""
        hooks = {
            "after_create": [
                {
                    "action": "update",
                    "collection": "audit",
                    "filter": {"entity_id": "{{doc._id}}"},
                    "update": {"status": "processed"},
                }
            ]
        }
        executor = HookExecutor(hooks)
        await executor.run("after_create", {"_id": "d1"}, None, db)

        filt, update = db["audit"].updated[0]
        assert update == {"$set": {"status": "processed"}}

    @pytest.mark.asyncio
    async def test_mixed_operators_pass_through(self, db: FakeDB):
        """$inc + $set together should pass through directly."""
        hooks = {
            "after_create": [
                {
                    "action": "update",
                    "collection": "stats",
                    "filter": {"_id": "global"},
                    "update": {"$inc": {"total": 1}, "$set": {"last_id": "{{doc._id}}"}},
                }
            ]
        }
        executor = HookExecutor(hooks)
        await executor.run("after_create", {"_id": "x1"}, None, db)

        filt, update = db["stats"].updated[0]
        assert "$inc" in update
        assert "$set" in update
        assert update["$set"]["last_id"] == "x1"

    @pytest.mark.asyncio
    async def test_template_in_operator_value(self, db: FakeDB):
        """{{doc.*}} placeholders inside $push should resolve."""
        hooks = {
            "after_create": [
                {
                    "action": "update",
                    "collection": "posts",
                    "filter": {"_id": "{{doc.post_id}}"},
                    "update": {"$push": {"comment_ids": "{{doc._id}}"}},
                }
            ]
        }
        executor = HookExecutor(hooks)
        await executor.run("after_create", {"_id": "c99", "post_id": "p5"}, None, db)

        filt, update = db["posts"].updated[0]
        assert update == {"$push": {"comment_ids": "c99"}}


# ═════════════════════════════════════════════════════════════════════════
# A.1 — Conditional Hooks
# ═════════════════════════════════════════════════════════════════════════


class TestEvaluateCondition:
    def test_simple_equality_match(self):
        assert _evaluate_condition({"status": "published"}, {"status": "published"}, None)

    def test_simple_equality_no_match(self):
        assert not _evaluate_condition({"status": "published"}, {"status": "draft"}, None)

    def test_doc_prefix(self):
        assert _evaluate_condition({"doc.status": "published"}, {"status": "published"}, None)

    def test_prev_prefix(self):
        prev = {"status": "draft"}
        assert _evaluate_condition({"prev.status": "draft"}, {"status": "published"}, prev)

    def test_prev_prefix_no_match(self):
        prev = {"status": "published"}
        assert not _evaluate_condition({"prev.status": "draft"}, {}, prev)

    def test_dollar_ne(self):
        assert _evaluate_condition({"status": {"$ne": "draft"}}, {"status": "published"}, None)
        assert not _evaluate_condition({"status": {"$ne": "draft"}}, {"status": "draft"}, None)

    def test_dollar_in(self):
        assert _evaluate_condition(
            {"status": {"$in": ["published", "archived"]}},
            {"status": "published"},
            None,
        )
        assert not _evaluate_condition(
            {"status": {"$in": ["published", "archived"]}},
            {"status": "draft"},
            None,
        )

    def test_dollar_nin(self):
        assert _evaluate_condition(
            {"status": {"$nin": ["draft", "deleted"]}},
            {"status": "published"},
            None,
        )

    def test_dollar_exists(self):
        assert _evaluate_condition({"title": {"$exists": True}}, {"title": "Hi"}, None)
        assert not _evaluate_condition({"title": {"$exists": True}}, {"body": "text"}, None)

    def test_dollar_gt_lt(self):
        assert _evaluate_condition({"count": {"$gt": 5}}, {"count": 10}, None)
        assert not _evaluate_condition({"count": {"$gt": 5}}, {"count": 3}, None)
        assert _evaluate_condition({"count": {"$lt": 5}}, {"count": 3}, None)

    def test_dollar_gte_lte(self):
        assert _evaluate_condition({"count": {"$gte": 5}}, {"count": 5}, None)
        assert _evaluate_condition({"count": {"$lte": 5}}, {"count": 5}, None)

    def test_transition_detection(self):
        """Detect status changing from draft to published."""
        condition = {
            "doc.status": "published",
            "prev.status": {"$ne": "published"},
        }
        doc = {"status": "published"}
        prev = {"status": "draft"}
        assert _evaluate_condition(condition, doc, prev)

    def test_transition_no_change(self):
        """Already published — should not fire."""
        condition = {
            "doc.status": "published",
            "prev.status": {"$ne": "published"},
        }
        doc = {"status": "published"}
        prev = {"status": "published"}
        assert not _evaluate_condition(condition, doc, prev)

    def test_prev_none_returns_none_for_field(self):
        """When prev is None, prev.* fields resolve to None."""
        assert not _evaluate_condition({"prev.status": "draft"}, {"status": "x"}, None)


class TestConditionalHookExecution:
    @pytest.fixture()
    def db(self) -> FakeDB:
        return FakeDB()

    @pytest.mark.asyncio
    async def test_hook_fires_when_condition_matches(self, db: FakeDB):
        hooks = {
            "after_update": [
                {
                    "action": "insert",
                    "collection": "notifications",
                    "document": {"type": "published", "post_id": "{{doc._id}}"},
                    "if": {"doc.status": "published", "prev.status": {"$ne": "published"}},
                }
            ]
        }
        executor = HookExecutor(hooks)
        doc = {"_id": "p1", "status": "published"}
        prev = {"_id": "p1", "status": "draft"}
        await executor.run("after_update", doc, None, db, prev=prev)

        assert len(db["notifications"].inserted) == 1
        assert db["notifications"].inserted[0]["post_id"] == "p1"

    @pytest.mark.asyncio
    async def test_hook_skips_when_condition_fails(self, db: FakeDB):
        hooks = {
            "after_update": [
                {
                    "action": "insert",
                    "collection": "notifications",
                    "document": {"type": "published"},
                    "if": {"doc.status": "published"},
                }
            ]
        }
        executor = HookExecutor(hooks)
        await executor.run("after_update", {"_id": "p1", "status": "draft"}, None, db)

        assert len(db["notifications"].inserted) == 0


# ═════════════════════════════════════════════════════════════════════════
# A.3 — Delete and HTTP Hook Actions
# ═════════════════════════════════════════════════════════════════════════


class TestDeleteHookAction:
    @pytest.fixture()
    def db(self) -> FakeDB:
        return FakeDB()

    @pytest.mark.asyncio
    async def test_delete_action(self, db: FakeDB):
        hooks = {
            "after_delete": [
                {
                    "action": "delete",
                    "collection": "comments",
                    "filter": {"post_id": "{{doc._id}}"},
                }
            ]
        }
        executor = HookExecutor(hooks)
        await executor.run("after_delete", {"_id": "p1"}, None, db)

        assert len(db["comments"].deleted) == 1
        assert db["comments"].deleted[0] == {"post_id": "p1"}


class TestHttpHookAction:
    @pytest.mark.asyncio
    async def test_http_post_webhook(self):
        hooks = {
            "after_create": [
                {
                    "action": "http",
                    "collection": "ignored",
                    "url": "https://hooks.example.com/notify",
                    "method": "POST",
                    "body": {"title": "{{doc.title}}", "post_id": "{{doc._id}}"},
                    "headers": {"X-Api-Key": "secret123"},
                    "timeout": 5,
                }
            ]
        }
        executor = HookExecutor(hooks)

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=MagicMock())
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_httpx = MagicMock()
        mock_httpx.AsyncClient.return_value = mock_client

        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            await executor.run("after_create", {"_id": "p1", "title": "Hello"}, None, FakeDB())

        mock_client.request.assert_called_once_with(
            "POST",
            "https://hooks.example.com/notify",
            json={"title": "Hello", "post_id": "p1"},
            headers={"X-Api-Key": "secret123"},
        )

    @pytest.mark.asyncio
    async def test_http_hook_failure_does_not_propagate(self):
        """HTTP hook failure should be caught — fire-and-forget."""
        hooks = {
            "after_create": [
                {
                    "action": "http",
                    "collection": "x",
                    "url": "https://hooks.example.com/fail",
                    "body": {},
                }
            ]
        }
        executor = HookExecutor(hooks)

        mock_httpx = MagicMock()
        mock_httpx.AsyncClient.side_effect = OSError("Connection refused")

        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            await executor.run("after_create", {"_id": "p1"}, None, FakeDB())


# ═════════════════════════════════════════════════════════════════════════
# General Hook Tests
# ═════════════════════════════════════════════════════════════════════════


class TestInsertHookAction:
    @pytest.fixture()
    def db(self) -> FakeDB:
        return FakeDB()

    @pytest.mark.asyncio
    async def test_insert_action(self, db: FakeDB):
        hooks = {
            "after_create": [
                {
                    "action": "insert",
                    "collection": "audit_log",
                    "document": {"event": "created", "entity_id": "{{doc._id}}"},
                }
            ]
        }
        executor = HookExecutor(hooks)
        await executor.run("after_create", {"_id": "d1"}, None, db)

        assert len(db["audit_log"].inserted) == 1
        assert db["audit_log"].inserted[0]["entity_id"] == "d1"


class TestHookFireAndForget:
    @pytest.fixture()
    def db(self) -> FakeDB:
        return FakeDB()

    @pytest.mark.asyncio
    async def test_insert_failure_does_not_propagate(self, db: FakeDB):
        """Hook failure should be logged, not raised."""
        col = db["audit_log"]
        original_insert = col.insert_one

        async def failing_insert(doc):
            raise RuntimeError("DB write failed")

        col.insert_one = failing_insert

        hooks = {
            "after_create": [
                {
                    "action": "insert",
                    "collection": "audit_log",
                    "document": {"event": "created"},
                }
            ]
        }
        executor = HookExecutor(hooks)
        await executor.run("after_create", {"_id": "d1"}, None, db)

    @pytest.mark.asyncio
    async def test_no_actions_is_noop(self, db: FakeDB):
        executor = HookExecutor({})
        await executor.run("after_create", {"_id": "d1"}, None, db)

    @pytest.mark.asyncio
    async def test_missing_action_key_skips(self, db: FakeDB):
        hooks = {"after_create": [{"collection": "audit"}]}
        executor = HookExecutor(hooks)
        await executor.run("after_create", {"_id": "d1"}, None, db)

    @pytest.mark.asyncio
    async def test_missing_collection_skips(self, db: FakeDB):
        hooks = {"after_create": [{"action": "insert"}]}
        executor = HookExecutor(hooks)
        await executor.run("after_create", {"_id": "d1"}, None, db)
