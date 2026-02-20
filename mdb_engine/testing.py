"""
Testing utilities for mdb-engine app developers.

Provides a test client factory, dependency mocks, and fixture helpers
so apps can be tested without a running MongoDB instance.

Usage (pytest)::

    import pytest
    from mdb_engine.testing import create_test_client, mock_scoped_db, mock_user

    @pytest.fixture
    async def client():
        async with create_test_client("my_app", manifest="manifest.json") as c:
            yield c

    async def test_list_items(client):
        resp = await client.get("/items")
        assert resp.status_code == 200
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

logger = logging.getLogger(__name__)


# ── Fake scoped DB ─────────────────────────────────────────────────────


class _FakeCollection:
    """Minimal async MongoDB collection stub backed by an in-memory list."""

    def __init__(self, docs: list[dict[str, Any]] | None = None):
        self._docs = list(docs or [])

    async def find_one(self, filter_: dict | None = None, *args, **kw):
        for d in self._docs:
            if all(d.get(k) == v for k, v in (filter_ or {}).items()):
                return dict(d)
        return None

    def find(self, filter_: dict | None = None, *args, **kw):
        matched = [dict(d) for d in self._docs if all(d.get(k) == v for k, v in (filter_ or {}).items())]
        return _FakeCursor(matched)

    async def insert_one(self, doc: dict, **kw):
        self._docs.append(dict(doc))
        return MagicMock(inserted_id=doc.get("_id", "fake-id"))

    async def insert_many(self, docs: list[dict], **kw):
        self._docs.extend(dict(d) for d in docs)
        return MagicMock(inserted_ids=[d.get("_id", f"fake-{i}") for i, d in enumerate(docs)])

    async def update_one(self, filter_: dict, update: dict, **kw):
        for d in self._docs:
            if all(d.get(k) == v for k, v in filter_.items()):
                if "$set" in update:
                    d.update(update["$set"])
                return MagicMock(modified_count=1)
        return MagicMock(modified_count=0)

    async def update_many(self, filter_: dict, update: dict, **kw):
        count = 0
        for d in self._docs:
            if all(d.get(k) == v for k, v in filter_.items()):
                if "$set" in update:
                    d.update(update["$set"])
                count += 1
        return MagicMock(modified_count=count)

    async def delete_one(self, filter_: dict, **kw):
        for i, d in enumerate(self._docs):
            if all(d.get(k) == v for k, v in filter_.items()):
                self._docs.pop(i)
                return MagicMock(deleted_count=1)
        return MagicMock(deleted_count=0)

    async def delete_many(self, filter_: dict, **kw):
        before = len(self._docs)
        self._docs = [d for d in self._docs if not all(d.get(k) == v for k, v in filter_.items())]
        return MagicMock(deleted_count=before - len(self._docs))

    async def count_documents(self, filter_: dict | None = None, **kw):
        if not filter_:
            return len(self._docs)
        return sum(1 for d in self._docs if all(d.get(k) == v for k, v in filter_.items()))

    async def aggregate(self, pipeline: list, **kw):
        return _FakeCursor(list(self._docs))


class _FakeCursor:
    """Minimal async cursor that supports .to_list(), .sort(), .limit()."""

    def __init__(self, docs: list[dict]):
        self._docs = docs

    def sort(self, key_or_list, direction=None):
        return self

    def limit(self, n: int):
        self._docs = self._docs[:n]
        return self

    def skip(self, n: int):
        self._docs = self._docs[n:]
        return self

    async def to_list(self, length: int | None = None):
        if length is not None:
            return self._docs[:length]
        return list(self._docs)

    def __aiter__(self):
        return _FakeAsyncIter(self._docs)


class _FakeAsyncIter:
    def __init__(self, docs):
        self._it = iter(docs)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None


class _FakeScopedDB:
    """Dict-like object that returns _FakeCollection for any attribute/key."""

    def __init__(self, collections: dict[str, list[dict]] | None = None):
        self._cols: dict[str, _FakeCollection] = {}
        for name, docs in (collections or {}).items():
            self._cols[name] = _FakeCollection(docs)

    def __getattr__(self, name: str) -> _FakeCollection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._cols.setdefault(name, _FakeCollection())

    def __getitem__(self, name: str) -> _FakeCollection:
        return self._cols.setdefault(name, _FakeCollection())


# ── Dependency overrides ───────────────────────────────────────────────


def mock_scoped_db(collections: dict[str, list[dict]] | None = None) -> Callable:
    """Return a FastAPI dependency override for ``get_scoped_db``.

    Args:
        collections: Optional mapping of collection name to seed documents.

    Example::

        from mdb_engine.dependencies import get_scoped_db
        from mdb_engine.testing import mock_scoped_db

        app.dependency_overrides[get_scoped_db] = mock_scoped_db({"tasks": [{"title": "A"}]})
    """
    fake = _FakeScopedDB(collections)

    async def _override():
        return fake

    return _override


def mock_user(user_data: dict[str, Any] | None = None) -> Callable:
    """Return a dependency override for ``require_user()`` or ``get_current_user``.

    Example::

        from mdb_engine.dependencies import require_user
        from mdb_engine.testing import mock_user

        app.dependency_overrides[require_user()] = mock_user({"email": "test@test.com"})
    """
    data = user_data or {"email": "test@test.com", "_id": "test-user-id"}

    async def _override():
        return data

    return _override


# ── Test client factory ─────────────────────────────────────────────────


@asynccontextmanager
async def create_test_client(
    slug: str,
    manifest: str | Path | dict | None = None,
    collections: dict[str, list[dict]] | None = None,
    user: dict[str, Any] | None = None,
) -> AsyncIterator:
    """Create an async test client for an mdb-engine app.

    Uses httpx ``AsyncClient`` with an in-memory fake database.  No real
    MongoDB connection is required.

    Args:
        slug: App slug.
        manifest: Path to manifest.json, a dict, or None for minimal defaults.
        collections: Seed data for the fake scoped DB.
        user: If provided, every request will be authenticated as this user.

    Yields:
        ``httpx.AsyncClient`` bound to the test app.
    """
    from fastapi import FastAPI

    try:
        from httpx import ASGITransport, AsyncClient
    except ImportError as exc:
        raise ImportError("Install httpx to use create_test_client: pip install httpx") from exc

    # Build a minimal FastAPI app
    test_app = FastAPI(title=f"test-{slug}")
    test_app.state.app_slug = slug

    # Load manifest if path
    if isinstance(manifest, str | Path):
        with open(manifest) as f:
            manifest_data = json.load(f)
    elif isinstance(manifest, dict):
        manifest_data = manifest
    else:
        manifest_data = {"schema_version": "2.0", "slug": slug, "name": slug}

    test_app.state.manifest = manifest_data

    # Wire up fake scoped DB
    fake_db = _FakeScopedDB(collections)

    from .dependencies import get_scoped_db

    async def _fake_scoped_db(*_a, **_kw):
        return fake_db

    test_app.dependency_overrides[get_scoped_db] = _fake_scoped_db

    # Wire up fake engine
    fake_engine = MagicMock()
    fake_engine.initialized = True
    fake_engine.get_scoped_db = AsyncMock(return_value=fake_db)
    fake_engine.get_memory_service = MagicMock(return_value=None)
    fake_engine.get_graph_service = MagicMock(return_value=None)
    fake_engine.get_profile_service = MagicMock(return_value=None)
    test_app.state.engine = fake_engine

    # Optionally inject a test user on every request
    if user:
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.requests import Request

        class _InjectUser(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                request.state.user = user
                request.state.user_roles = user.get("roles", [])
                return await call_next(request)

        test_app.add_middleware(_InjectUser)

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.app = test_app  # expose for dependency_overrides
        yield client
