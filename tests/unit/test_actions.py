"""Tests for mdb_engine.actions — ActionContext."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


class TestActionContextAuth:
    """Auth helpers on ActionContext."""

    def _make_ctx(self, user=None, request=None):
        from mdb_engine.actions import ActionContext

        engine = MagicMock()
        engine.get_memory_service.return_value = None
        engine.get_llm_service.return_value = None
        engine.get_embedding_service.return_value = None

        if request is None and user is not None:
            request = MagicMock()
            request.state = MagicMock()
            request.state.user = user

        return ActionContext(engine=engine, slug="test", request=request)

    def test_user_from_request_state(self):
        user = {"_id": "u1", "email": "a@b.com", "role": "editor"}
        ctx = self._make_ctx(user=user)
        assert ctx.user == user

    def test_user_none_when_no_request(self):
        from mdb_engine.actions import ActionContext

        ctx = ActionContext(engine=MagicMock(), slug="test")
        assert ctx.user is None

    def test_require_user_raises_401(self):
        ctx = self._make_ctx(user=None, request=None)
        with pytest.raises(HTTPException) as exc_info:
            ctx.require_user()
        assert exc_info.value.status_code == 401

    def test_require_user_returns_user(self):
        user = {"_id": "u1", "role": "viewer"}
        ctx = self._make_ctx(user=user)
        assert ctx.require_user() == user

    def test_require_role_passes(self):
        user = {"_id": "u1", "role": "admin", "roles": ["admin", "editor"]}
        ctx = self._make_ctx(user=user)
        assert ctx.require_role("admin") == user

    def test_require_role_raises_403(self):
        user = {"_id": "u1", "role": "viewer", "roles": ["viewer"]}
        ctx = self._make_ctx(user=user)
        with pytest.raises(HTTPException) as exc_info:
            ctx.require_role("admin")
        assert exc_info.value.status_code == 403


class TestActionContextFromEvent:
    """ActionContext.from_event class method."""

    def test_from_event_sets_fields(self):
        from mdb_engine.actions import ActionContext

        engine = MagicMock()
        doc = {"_id": "doc1", "title": "Test"}
        user = {"_id": "u1"}

        ctx = ActionContext.from_event(
            engine=engine,
            slug="app1",
            doc=doc,
            user=user,
            event="after_create",
        )

        assert ctx.event_doc == doc
        assert ctx.event_name == "after_create"
        assert ctx.user == user
        assert ctx.request is None
        assert ctx.slug == "app1"

    def test_from_event_user_from_event_context(self):
        from mdb_engine.actions import ActionContext

        user = {"_id": "u2", "role": "editor"}
        ctx = ActionContext.from_event(
            engine=MagicMock(),
            slug="app1",
            doc={},
            user=user,
        )
        assert ctx.user == user
        assert ctx.require_user() == user


class TestActionContextDatabase:
    """Database access via ActionContext."""

    @pytest.mark.asyncio
    async def test_get_db_calls_engine(self):
        from mdb_engine.actions import ActionContext

        mock_db = MagicMock()
        engine = MagicMock()
        engine.get_scoped_db = AsyncMock(return_value=mock_db)

        ctx = ActionContext(engine=engine, slug="app1")
        db = await ctx.get_db()

        assert db is mock_db
        engine.get_scoped_db.assert_awaited_once_with("app1")

    @pytest.mark.asyncio
    async def test_get_db_caches_result(self):
        from mdb_engine.actions import ActionContext

        mock_db = MagicMock()
        engine = MagicMock()
        engine.get_scoped_db = AsyncMock(return_value=mock_db)

        ctx = ActionContext(engine=engine, slug="app1")
        db1 = await ctx.get_db()
        db2 = await ctx.get_db()

        assert db1 is db2
        assert engine.get_scoped_db.await_count == 1

    @pytest.mark.asyncio
    async def test_get_uow_returns_unit_of_work(self):
        from mdb_engine.actions import ActionContext

        mock_db = MagicMock()
        engine = MagicMock()
        engine.get_scoped_db = AsyncMock(return_value=mock_db)

        ctx = ActionContext(engine=engine, slug="app1")
        uow = await ctx.get_uow()

        from mdb_engine.repositories import UnitOfWork

        assert isinstance(uow, UnitOfWork)


class TestActionContextRequestHelpers:
    """Request helpers for HTTP triggers."""

    def test_method_returns_request_method(self):
        from mdb_engine.actions import ActionContext

        request = MagicMock()
        request.method = "POST"
        ctx = ActionContext(engine=MagicMock(), slug="test", request=request)
        assert ctx.method == "POST"

    def test_method_returns_empty_without_request(self):
        from mdb_engine.actions import ActionContext

        ctx = ActionContext(engine=MagicMock(), slug="test")
        assert ctx.method == ""

    def test_headers_returns_dict(self):
        from mdb_engine.actions import ActionContext

        request = MagicMock()
        request.headers = {"content-type": "application/json"}
        ctx = ActionContext(engine=MagicMock(), slug="test", request=request)
        assert ctx.headers == {"content-type": "application/json"}

    def test_headers_returns_empty_without_request(self):
        from mdb_engine.actions import ActionContext

        ctx = ActionContext(engine=MagicMock(), slug="test")
        assert ctx.headers == {}

    @pytest.mark.asyncio
    async def test_json_raises_without_request(self):
        from mdb_engine.actions import ActionContext

        ctx = ActionContext(engine=MagicMock(), slug="test")
        with pytest.raises(RuntimeError, match="HTTP-triggered"):
            await ctx.json()

    @pytest.mark.asyncio
    async def test_text_raises_without_request(self):
        from mdb_engine.actions import ActionContext

        ctx = ActionContext(engine=MagicMock(), slug="test")
        with pytest.raises(RuntimeError, match="HTTP-triggered"):
            await ctx.text()


class TestActionContextResponseHelpers:
    """Response helpers."""

    def test_json_response(self):
        from mdb_engine.actions import ActionContext

        resp = ActionContext.json_response({"ok": True}, status=201)
        assert resp.status_code == 201

    def test_text_response(self):
        from mdb_engine.actions import ActionContext

        resp = ActionContext.text_response("hello", status=200)
        assert resp.status_code == 200
        assert resp.media_type == "text/plain"

    def test_error_creates_exception(self):
        from mdb_engine.actions import ActionContext

        exc = ActionContext.error(404, "not found")
        assert isinstance(exc, HTTPException)
        assert exc.status_code == 404
        assert exc.detail == "not found"


class TestActionContextAIServices:
    """AI service properties."""

    def test_memory_delegates_to_engine(self):
        from mdb_engine.actions import ActionContext

        engine = MagicMock()
        engine.get_memory_service.return_value = "mem_svc"

        ctx = ActionContext(engine=engine, slug="app1")
        assert ctx.memory == "mem_svc"
        engine.get_memory_service.assert_called_with("app1")

    def test_llm_delegates_to_engine(self):
        from mdb_engine.actions import ActionContext

        engine = MagicMock()
        engine.get_llm_service.return_value = "llm_svc"

        ctx = ActionContext(engine=engine, slug="app1")
        assert ctx.llm == "llm_svc"

    def test_embedding_delegates_to_engine(self):
        from mdb_engine.actions import ActionContext

        engine = MagicMock()
        engine.get_embedding_service.return_value = "emb_svc"

        ctx = ActionContext(engine=engine, slug="app1")
        assert ctx.embedding == "emb_svc"
