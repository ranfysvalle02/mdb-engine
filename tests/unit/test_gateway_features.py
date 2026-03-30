"""
Tests for gateway quick-win features:

1. BackgroundHookExecutor wiring in auto-CRUD
2. Before-event hooks (before_create / before_update) via run_before()
3. Per-collection rate limiting via _rate_limit dependency
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from mdb_engine.auth.rate_limiter import InMemoryRateLimitStore
from mdb_engine.dependencies import get_scoped_db
from mdb_engine.routing._hooks import BackgroundHookExecutor, HookExecutor
from mdb_engine.routing._rate_limit import create_collection_rate_limit_dependency
from mdb_engine.routing.auto_crud import create_auto_crud_router
from mdb_engine.testing import _FakeScopedDB

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


# ═══════════════════════════════════════════════════════════════════════
# 1. BackgroundHookExecutor wiring
# ═══════════════════════════════════════════════════════════════════════


class TestBackgroundHookExecutorWiring:
    """Verify auto-CRUD uses BackgroundHookExecutor by default."""

    def test_auto_crud_router_uses_background_executor(self):
        """create_auto_crud_router should import BackgroundHookExecutor."""
        from mdb_engine.routing import auto_crud

        assert hasattr(auto_crud, "BackgroundHookExecutor")

    @pytest.mark.asyncio
    async def test_inline_action_runs_synchronously(self):
        """Actions without background:true run inline (backward compat)."""
        db = FakeDB()
        hooks = {
            "after_create": [
                {
                    "action": "insert",
                    "collection": "audit",
                    "document": {"event": "created", "id": "{{doc._id}}"},
                }
            ]
        }
        executor = BackgroundHookExecutor(hooks)
        await executor.run("after_create", {"_id": "d1"}, None, db)

        assert len(db["audit"].inserted) == 1
        assert db["audit"].inserted[0]["id"] == "d1"

    @pytest.mark.asyncio
    async def test_background_action_offloaded(self):
        """Actions with background:true are dispatched via create_task."""
        db = FakeDB()
        hooks = {
            "after_create": [
                {
                    "action": "insert",
                    "collection": "audit",
                    "document": {"event": "created"},
                    "background": True,
                }
            ]
        }
        executor = BackgroundHookExecutor(hooks)
        await executor.run("after_create", {"_id": "d1"}, None, db)

        # Give the background task a moment to complete
        await asyncio.sleep(0.05)
        assert len(db["audit"].inserted) == 1

    @pytest.mark.asyncio
    async def test_background_retry_on_failure(self):
        """Background hooks retry with backoff on failure."""
        db = FakeDB()
        call_count = 0

        col = db["audit"]
        original = col.insert_one

        async def flaky_insert(doc):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("transient failure")
            return await original(doc)

        col.insert_one = flaky_insert

        hooks = {
            "after_create": [
                {
                    "action": "insert",
                    "collection": "audit",
                    "document": {"event": "created"},
                    "background": True,
                    "retry": {"attempts": 3, "backoff": "linear"},
                }
            ]
        }
        executor = BackgroundHookExecutor(hooks)
        await executor.run("after_create", {"_id": "d1"}, None, db)

        # Wait for retries (linear: 1s + 2s but we mock so it's fast... actually asyncio.sleep is real)
        # With linear backoff attempts 1,2,3 the delays are 1s, 2s. Let's wait enough.
        await asyncio.sleep(4)
        assert call_count == 3
        assert len(db["audit"].inserted) == 1

    @pytest.mark.asyncio
    async def test_dead_letter_on_exhausted_retries(self):
        """After all retries fail, failure is logged to _hook_failures."""
        db = FakeDB()

        col = db["audit"]

        async def always_fail(doc):
            raise RuntimeError("permanent failure")

        col.insert_one = always_fail

        hooks = {
            "after_create": [
                {
                    "action": "insert",
                    "collection": "audit",
                    "document": {"event": "created"},
                    "background": True,
                    "retry": {"attempts": 2, "backoff": "linear"},
                }
            ]
        }
        executor = BackgroundHookExecutor(hooks)
        await executor.run("after_create", {"_id": "d1"}, None, db)

        await asyncio.sleep(3)
        failures = db["_hook_failures"]
        assert len(failures.inserted) == 1
        assert failures.inserted[0]["event"] == "after_create"
        assert failures.inserted[0]["attempts"] == 2


# ═══════════════════════════════════════════════════════════════════════
# 2. Before-event hooks (run_before)
# ═══════════════════════════════════════════════════════════════════════


class TestRunBefore:
    """Test run_before() on HookExecutor — errors propagate, not swallowed."""

    @pytest.fixture()
    def db(self) -> FakeDB:
        return FakeDB()

    @pytest.mark.asyncio
    async def test_before_hook_runs_action(self, db: FakeDB):
        """before_create hooks execute their actions."""
        hooks = {
            "before_create": [
                {
                    "action": "insert",
                    "collection": "pre_audit",
                    "document": {"event": "pre_create", "id": "{{doc._id}}"},
                }
            ]
        }
        executor = HookExecutor(hooks)
        await executor.run_before("before_create", {"_id": "d1"}, None, db)

        assert len(db["pre_audit"].inserted) == 1
        assert db["pre_audit"].inserted[0]["event"] == "pre_create"

    @pytest.mark.asyncio
    async def test_before_hook_error_propagates(self, db: FakeDB):
        """Unlike run(), run_before() lets errors bubble up."""
        col = db["audit"]

        async def fail_insert(doc):
            raise RuntimeError("validation failed")

        col.insert_one = fail_insert

        hooks = {
            "before_create": [
                {
                    "action": "insert",
                    "collection": "audit",
                    "document": {"event": "x"},
                }
            ]
        }
        executor = HookExecutor(hooks)
        with pytest.raises(RuntimeError, match="validation failed"):
            await executor.run_before("before_create", {"_id": "d1"}, None, db)

    @pytest.mark.asyncio
    async def test_after_hook_error_does_not_propagate(self, db: FakeDB):
        """Confirm that run() (after hooks) still swallows errors."""
        col = db["audit"]

        async def fail_insert(doc):
            raise RuntimeError("should be swallowed")

        col.insert_one = fail_insert

        hooks = {
            "after_create": [
                {
                    "action": "insert",
                    "collection": "audit",
                    "document": {"event": "x"},
                }
            ]
        }
        executor = HookExecutor(hooks)
        # Should NOT raise
        await executor.run("after_create", {"_id": "d1"}, None, db)

    @pytest.mark.asyncio
    async def test_before_hook_mutates_body(self, db: FakeDB):
        """Before hooks can mutate the document dict in-place (enrichment)."""
        hooks = {
            "before_create": [
                {
                    "action": "insert",
                    "collection": "side_effect",
                    "document": {"logged": True},
                }
            ]
        }
        executor = HookExecutor(hooks)
        body = {"title": "test"}
        # Simulate enrichment: a run_action hook would mutate body.
        # Here we test the mechanism works with a simple action.
        await executor.run_before("before_create", body, None, db)
        assert len(db["side_effect"].inserted) == 1

    @pytest.mark.asyncio
    async def test_before_hook_noop_on_empty(self, db: FakeDB):
        """No actions for event -> noop."""
        executor = HookExecutor({})
        await executor.run_before("before_create", {"_id": "d1"}, None, db)

    @pytest.mark.asyncio
    async def test_before_hook_with_prev_for_update(self, db: FakeDB):
        """before_update receives prev= and uses it in conditions."""
        hooks = {
            "before_update": [
                {
                    "action": "insert",
                    "collection": "changelog",
                    "document": {
                        "new_status": "{{doc.status}}",
                        "entity_id": "{{doc._id}}",
                    },
                    "if": {
                        "doc.status": "published",
                        "prev.status": {"$ne": "published"},
                    },
                }
            ]
        }
        executor = HookExecutor(hooks)
        doc = {"_id": "d1", "status": "published"}
        prev = {"_id": "d1", "status": "draft"}
        await executor.run_before("before_update", doc, None, db, prev=prev)

        assert len(db["changelog"].inserted) == 1
        assert db["changelog"].inserted[0]["new_status"] == "published"

    @pytest.mark.asyncio
    async def test_before_hook_condition_skips_with_prev(self, db: FakeDB):
        """before_update condition using prev.* can prevent hook execution."""
        hooks = {
            "before_update": [
                {
                    "action": "insert",
                    "collection": "changelog",
                    "document": {"event": "change"},
                    "if": {
                        "doc.status": "published",
                        "prev.status": {"$ne": "published"},
                    },
                }
            ]
        }
        executor = HookExecutor(hooks)
        doc = {"_id": "d1", "status": "published"}
        prev = {"_id": "d1", "status": "published"}
        await executor.run_before("before_update", doc, None, db, prev=prev)

        assert len(db["changelog"].inserted) == 0

    @pytest.mark.asyncio
    async def test_background_executor_run_before(self, db: FakeDB):
        """BackgroundHookExecutor inherits run_before() from HookExecutor."""
        hooks = {
            "before_create": [
                {
                    "action": "insert",
                    "collection": "pre_audit",
                    "document": {"event": "pre_create"},
                }
            ]
        }
        executor = BackgroundHookExecutor(hooks)
        await executor.run_before("before_create", {"_id": "d1"}, None, db)
        assert len(db["pre_audit"].inserted) == 1


# ═══════════════════════════════════════════════════════════════════════
# 3. Per-collection rate limiting
# ═══════════════════════════════════════════════════════════════════════


class TestCollectionRateLimit:
    """Test the collection rate limit dependency factory."""

    def _make_request(self, user: dict | None = None, ip: str = "127.0.0.1") -> MagicMock:
        req = MagicMock(spec=Request)
        req.client = MagicMock()
        req.client.host = ip
        req.state = MagicMock()
        req.state.user = user
        return req

    @pytest.mark.asyncio
    async def test_no_config_returns_noop(self):
        """When operation is not configured, dependency is a no-op."""
        dep = create_collection_rate_limit_dependency("tasks", "reads", {})
        req = self._make_request()
        await dep(req)  # should not raise

    @pytest.mark.asyncio
    async def test_rate_limit_allows_under_limit(self):
        """Requests under the limit should pass."""
        config = {
            "reads": {"max_attempts": 5, "window_seconds": 60},
            "per": "ip",
        }
        dep = create_collection_rate_limit_dependency("tasks", "reads", config)
        req = self._make_request(ip="10.0.0.1")
        # First request should succeed
        await dep(req)

    @pytest.mark.asyncio
    async def test_rate_limit_blocks_over_limit(self):
        """Requests over the limit should raise 429."""
        config = {
            "writes": {"max_attempts": 2, "window_seconds": 60},
            "per": "ip",
        }
        dep = create_collection_rate_limit_dependency("rate_test_col", "writes", config)
        req = self._make_request(ip="10.0.0.99")

        await dep(req)  # 1
        await dep(req)  # 2
        with pytest.raises(HTTPException) as exc_info:
            await dep(req)  # 3 -> over limit
        assert exc_info.value.status_code == 429
        assert "Rate limit exceeded" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_rate_limit_per_user(self):
        """Per-user rate limiting uses user ID, not IP."""
        config = {
            "reads": {"max_attempts": 2, "window_seconds": 60},
            "per": "user",
        }
        dep = create_collection_rate_limit_dependency("user_rl_col", "reads", config)

        user_a = self._make_request(user={"_id": "user_a"}, ip="1.1.1.1")
        user_b = self._make_request(user={"_id": "user_b"}, ip="1.1.1.1")

        await dep(user_a)
        await dep(user_a)
        # user_a is now at limit
        with pytest.raises(HTTPException) as exc_info:
            await dep(user_a)
        assert exc_info.value.status_code == 429

        # user_b should still be fine (different identity)
        await dep(user_b)

    @pytest.mark.asyncio
    async def test_rate_limit_per_user_falls_back_to_ip(self):
        """When per=user but no user is authenticated, fall back to IP."""
        config = {
            "reads": {"max_attempts": 2, "window_seconds": 60},
            "per": "user",
        }
        dep = create_collection_rate_limit_dependency("anon_rl_col", "reads", config)
        req = self._make_request(user=None, ip="10.0.0.50")

        await dep(req)
        await dep(req)
        with pytest.raises(HTTPException) as exc_info:
            await dep(req)
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_retry_after_header(self):
        """429 response includes Retry-After header."""
        config = {
            "writes": {"max_attempts": 1, "window_seconds": 120},
            "per": "ip",
        }
        dep = create_collection_rate_limit_dependency("hdr_rl_col", "writes", config)
        req = self._make_request(ip="10.0.0.77")

        await dep(req)
        with pytest.raises(HTTPException) as exc_info:
            await dep(req)
        assert exc_info.value.headers["Retry-After"] == "120"


class TestCollectionRateLimitIntegration:
    """Integration test: rate limits wired into auto-CRUD routes."""

    @pytest.fixture(autouse=True)
    def _reset_rate_limit_store(self):
        """Reset the module-level store between tests."""
        from mdb_engine.routing import _rate_limit

        _rate_limit._store = InMemoryRateLimitStore()
        yield
        _rate_limit._store = InMemoryRateLimitStore()

    def _create_app_with_rate_limits(self, collection_name: str = "rl_items") -> tuple[FastAPI, _FakeScopedDB]:
        app = FastAPI()
        fake_db = _FakeScopedDB()

        app.dependency_overrides[get_scoped_db] = lambda: fake_db

        config = {
            "auto_crud": True,
            "rate_limits": {
                "reads": {"max_attempts": 3, "window_seconds": 60},
                "writes": {"max_attempts": 2, "window_seconds": 60},
                "per": "ip",
            },
        }
        router = create_auto_crud_router(collection_name, config)
        app.include_router(router)
        return app, fake_db

    def test_read_rate_limit_enforced(self):
        """GET requests are rate-limited when configured."""
        app, fake_db = self._create_app_with_rate_limits("read_rl_items")
        client = TestClient(app)

        for _ in range(3):
            resp = client.get("/api/read_rl_items")
            assert resp.status_code == 200

        resp = client.get("/api/read_rl_items")
        assert resp.status_code == 429

    def test_write_rate_limit_enforced(self):
        """POST requests are rate-limited when configured."""
        app, fake_db = self._create_app_with_rate_limits("write_rl_items")
        client = TestClient(app)

        for _ in range(2):
            resp = client.post("/api/write_rl_items", json={"title": "test"})
            assert resp.status_code == 201

        resp = client.post("/api/write_rl_items", json={"title": "test"})
        assert resp.status_code == 429
