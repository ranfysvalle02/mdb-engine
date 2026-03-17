"""
Tests for Zero-Code Manifest Power Features.

Covers:
- Template resolver: {{doc.*}}, {{env.*}} placeholders
- owner_field: auto-generated defaults + write/delete policy + admin bypass
- immutable_fields: stripped on PATCH/PUT
- hooks: after_create / after_update / after_delete side effects
- relations / ?populate=: $lookup injection (config validation)
- computed / ?computed=: $addFields injection (config validation)
- unique constraints: DuplicateKeyError -> 409
- query parser: populate and computed params
"""

from __future__ import annotations

import os
from datetime import datetime

import pytest
from bson import ObjectId
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from mdb_engine.dependencies import get_scoped_db
from mdb_engine.routing.auto_crud import create_auto_crud_router
from mdb_engine.routing.query_parser import parse_query_params
from mdb_engine.routing.template_resolver import resolve_template
from mdb_engine.testing import _FakeCollection, _FakeCursor

# ═══════════════════════════════════════════════════════════════════════════
# Template Resolver — {{doc.*}}, {{env.*}} placeholders
# ═══════════════════════════════════════════════════════════════════════════


class TestTemplateResolverDoc:
    def test_doc_placeholder_resolves(self):
        result = resolve_template(
            {"entity_id": "{{doc._id}}", "title": "{{doc.title}}"},
            user=None,
            doc={"_id": "abc123", "title": "Hello"},
        )
        assert result == {"entity_id": "abc123", "title": "Hello"}

    def test_doc_placeholder_returns_raw_when_no_doc(self):
        result = resolve_template(
            {"entity_id": "{{doc._id}}"},
            user=None,
            doc=None,
        )
        assert result == {"entity_id": "{{doc._id}}"}

    def test_doc_nested_path(self):
        result = resolve_template(
            {"val": "{{doc.meta.key}}"},
            user=None,
            doc={"meta": {"key": "nested_value"}},
        )
        assert result == {"val": "nested_value"}


class TestTemplateResolverEnv:
    def test_env_placeholder_resolves(self, monkeypatch):
        monkeypatch.setenv("TEST_WEBHOOK_URL", "https://example.com/hook")
        result = resolve_template(
            {"url": "{{env.TEST_WEBHOOK_URL}}"},
            user=None,
        )
        assert result == {"url": "https://example.com/hook"}

    def test_env_placeholder_raises_on_missing(self):
        os.environ.pop("NONEXISTENT_VAR", None)
        with pytest.raises(HTTPException):
            resolve_template({"url": "{{env.NONEXISTENT_VAR}}"}, user=None)

    def test_user_placeholder_still_works(self):
        result = resolve_template(
            {"owner": "{{user._id}}"},
            user={"_id": "user123"},
        )
        assert result == {"owner": "user123"}

    def test_now_placeholder_still_works(self):
        result = resolve_template({"ts": "$$NOW"}, user=None)
        assert isinstance(result["ts"], datetime)

    def test_mixed_placeholders(self, monkeypatch):
        monkeypatch.setenv("APP_NAME", "blog")
        result = resolve_template(
            {
                "user_id": "{{user._id}}",
                "doc_id": "{{doc._id}}",
                "app": "{{env.APP_NAME}}",
                "ts": "$$NOW",
                "static": "hello",
            },
            user={"_id": "u1"},
            doc={"_id": "d1"},
        )
        assert result["user_id"] == "u1"
        assert result["doc_id"] == "d1"
        assert result["app"] == "blog"
        assert isinstance(result["ts"], datetime)
        assert result["static"] == "hello"


# ═══════════════════════════════════════════════════════════════════════════
# Query Parser — populate, computed params
# ═══════════════════════════════════════════════════════════════════════════


class TestQueryParserNewParams:
    def test_populate_single(self):
        result = parse_query_params({"populate": "post"})
        assert result.populate == ["post"]

    def test_populate_multiple(self):
        result = parse_query_params({"populate": "post,author"})
        assert result.populate == ["post", "author"]

    def test_computed_single(self):
        result = parse_query_params({"computed": "comment_count"})
        assert result.computed == ["comment_count"]

    def test_computed_multiple(self):
        result = parse_query_params({"computed": "comment_count,excerpt"})
        assert result.computed == ["comment_count", "excerpt"]

    def test_populate_not_treated_as_filter(self):
        result = parse_query_params({"populate": "post", "status": "active"})
        assert result.populate == ["post"]
        assert result.filter == {"status": "active"}
        assert "populate" not in result.filter

    def test_empty_populate(self):
        result = parse_query_params({"populate": ""})
        assert result.populate is None


# ═══════════════════════════════════════════════════════════════════════════
# Smart fake collection with $and / $ne support
# ═══════════════════════════════════════════════════════════════════════════


def _match_filter(doc: dict, filter_: dict | None) -> bool:
    if not filter_:
        return True
    for key, value in filter_.items():
        if key == "$and":
            if not all(_match_filter(doc, sub) for sub in value):
                return False
        elif isinstance(value, dict):
            if "$ne" in value:
                if doc.get(key) == value["$ne"]:
                    return False
        else:
            if doc.get(key) != value:
                return False
    return True


class _SmartFakeCollection(_FakeCollection):
    async def find_one(self, filter_: dict | None = None, *args, **kw):
        for d in self._docs:
            if _match_filter(d, filter_):
                return dict(d)
        return None

    def find(self, filter_: dict | None = None, *args, **kw):
        matched = [dict(d) for d in self._docs if _match_filter(d, filter_)]
        return _FakeCursor(matched)

    async def count_documents(self, filter_: dict | None = None, **kw):
        if not filter_:
            return len(self._docs)
        return sum(1 for d in self._docs if _match_filter(d, filter_))


class _SmartFakeScopedDB:
    def __init__(self, collections=None):
        self._cols = {}
        for name, docs in (collections or {}).items():
            self._cols[name] = _SmartFakeCollection(docs)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self._cols.setdefault(name, _SmartFakeCollection())

    def __getitem__(self, name):
        return self._cols.setdefault(name, _SmartFakeCollection())


# ═══════════════════════════════════════════════════════════════════════════
# Helper to build a test app
# ═══════════════════════════════════════════════════════════════════════════


def _build_app(config, *, collections=None, user=None):
    app = FastAPI()
    fake_db = _SmartFakeScopedDB(collections or {})

    async def _override_db():
        return fake_db

    app.dependency_overrides[get_scoped_db] = _override_db
    router = create_auto_crud_router("items", config)
    app.include_router(router)

    if user:
        from starlette.middleware.base import BaseHTTPMiddleware

        class _InjectUser(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                request.state.user = user
                request.state.user_roles = [user.get("role", "")]
                return await call_next(request)

        app.add_middleware(_InjectUser)

    return TestClient(app), fake_db


# ═══════════════════════════════════════════════════════════════════════════
# owner_field
# ═══════════════════════════════════════════════════════════════════════════


class TestOwnerField:
    def test_owner_field_injects_default_on_create(self):
        user = {"_id": "u1", "email": "a@b.com", "role": "user"}
        client, db = _build_app(
            {"owner_field": "creator_id"},
            user=user,
        )
        resp = client.post("/api/items", json={"title": "test"})
        assert resp.status_code == 201
        doc = db["items"]._docs[0]
        assert doc["creator_id"] == "u1"

    def test_owner_field_blocks_non_owner_update(self):
        oid = ObjectId()
        user = {"_id": "u2", "email": "b@b.com", "role": "user"}
        client, db = _build_app(
            {"owner_field": "creator_id"},
            collections={"items": [{"_id": oid, "creator_id": "u1", "title": "owned"}]},
            user=user,
        )
        resp = client.patch(f"/api/items/{oid}", json={"title": "hacked"})
        assert resp.status_code == 404

    def test_owner_field_admin_bypass(self):
        oid = ObjectId()
        user = {"_id": "admin1", "email": "admin@b.com", "role": "admin"}
        client, db = _build_app(
            {"owner_field": "creator_id"},
            collections={"items": [{"_id": oid, "creator_id": "u1", "title": "owned"}]},
            user=user,
        )
        resp = client.patch(f"/api/items/{oid}", json={"title": "admin edit"})
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# immutable_fields
# ═══════════════════════════════════════════════════════════════════════════


class TestImmutableFields:
    def test_immutable_stripped_on_patch(self):
        oid = ObjectId()
        client, db = _build_app(
            {"immutable_fields": ["creator_id", "post_id"]},
            collections={"items": [{"_id": oid, "creator_id": "u1", "post_id": "p1", "body": "hi"}]},
        )
        resp = client.patch(
            f"/api/items/{oid}",
            json={"creator_id": "hacked", "post_id": "hacked", "body": "updated"},
        )
        assert resp.status_code == 200
        doc = db["items"]._docs[0]
        assert doc["creator_id"] == "u1"
        assert doc["post_id"] == "p1"
        assert doc["body"] == "updated"

    def test_immutable_stripped_on_put(self):
        oid = ObjectId()
        client, db = _build_app(
            {"immutable_fields": ["creator_id"]},
            collections={"items": [{"_id": oid, "creator_id": "u1", "title": "old"}]},
        )
        resp = client.put(
            f"/api/items/{oid}",
            json={"creator_id": "hacked", "title": "new"},
        )
        assert resp.status_code == 200
        doc = db["items"]._docs[0]
        assert doc["creator_id"] == "u1"
        assert doc["title"] == "new"


# ═══════════════════════════════════════════════════════════════════════════
# hooks
# ═══════════════════════════════════════════════════════════════════════════


class TestHooks:
    def test_after_create_hook_inserts_audit(self):
        user = {"_id": "u1", "email": "a@b.com", "role": "admin"}
        client, db = _build_app(
            {
                "hooks": {
                    "after_create": [
                        {
                            "action": "insert",
                            "collection": "audit_log",
                            "document": {
                                "event": "item_created",
                                "entity_id": "{{doc._id}}",
                                "actor": "{{user.email}}",
                                "timestamp": "$$NOW",
                            },
                        }
                    ]
                }
            },
            user=user,
        )
        resp = client.post("/api/items", json={"title": "test"})
        assert resp.status_code == 201
        audit_docs = db["audit_log"]._docs
        assert len(audit_docs) == 1
        assert audit_docs[0]["event"] == "item_created"
        assert audit_docs[0]["actor"] == "a@b.com"
        assert isinstance(audit_docs[0]["timestamp"], datetime)

    def test_after_delete_hook_fires(self):
        oid = ObjectId()
        user = {"_id": "u1", "email": "a@b.com", "role": "admin"}
        client, db = _build_app(
            {
                "hooks": {
                    "after_delete": [
                        {
                            "action": "insert",
                            "collection": "audit_log",
                            "document": {
                                "event": "item_deleted",
                                "entity_id": "{{doc._id}}",
                            },
                        }
                    ]
                }
            },
            collections={"items": [{"_id": oid, "title": "doomed"}]},
            user=user,
        )
        resp = client.delete(f"/api/items/{oid}")
        assert resp.status_code == 200
        audit_docs = db["audit_log"]._docs
        assert len(audit_docs) == 1
        assert audit_docs[0]["event"] == "item_deleted"


# ═══════════════════════════════════════════════════════════════════════════
# relations config validation
# ═══════════════════════════════════════════════════════════════════════════


class TestRelationsConfig:
    def test_unknown_relation_returns_400(self):
        client, _ = _build_app(
            {"relations": {"post": {"from": "posts", "local_field": "post_id"}}},
        )
        resp = client.get("/api/items?populate=nonexistent")
        assert resp.status_code == 400
        assert "Unknown relation" in resp.json()["detail"]

    def test_valid_relation_accepted(self):
        client, db = _build_app(
            {
                "relations": {
                    "post": {"from": "posts", "local_field": "post_id", "foreign_field": "_id", "single": True}
                }
            },
            collections={"items": [{"_id": ObjectId(), "post_id": "p1", "body": "hi"}]},
        )
        resp = client.get("/api/items?populate=post")
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# computed config validation
# ═══════════════════════════════════════════════════════════════════════════


class TestComputedConfig:
    def test_unknown_computed_returns_400(self):
        client, _ = _build_app(
            {"computed": {"excerpt": {"$substr": ["$body", 0, 100]}}},
        )
        resp = client.get("/api/items?computed=nonexistent")
        assert resp.status_code == 400
        assert "Unknown computed" in resp.json()["detail"]

    def test_valid_computed_accepted(self):
        client, db = _build_app(
            {"computed": {"excerpt": {"$substr": ["$body", 0, 100]}}},
            collections={"items": [{"_id": ObjectId(), "body": "hello world"}]},
        )
        resp = client.get("/api/items?computed=excerpt")
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# unique constraints -> 409
# ═══════════════════════════════════════════════════════════════════════════


class TestUniqueConstraints:
    def test_duplicate_key_returns_409(self):
        """Simulate DuplicateKeyError from MongoDB and verify 409 response."""
        from pymongo.errors import DuplicateKeyError

        app = FastAPI()

        class _DupeCollection(_SmartFakeCollection):
            async def insert_one(self, doc, **kw):
                raise DuplicateKeyError("E11000", code=11000, details={"keyPattern": {"email": 1}})

        class _DupeDB:
            def __getitem__(self, name):
                return _DupeCollection()

            def __getattr__(self, name):
                if name.startswith("_"):
                    raise AttributeError(name)
                return _DupeCollection()

        async def _override():
            return _DupeDB()

        app.dependency_overrides[get_scoped_db] = _override
        router = create_auto_crud_router(
            "items",
            {
                "schema": {
                    "type": "object",
                    "properties": {"email": {"type": "string", "x-unique": True}},
                }
            },
        )
        app.include_router(router)
        client = TestClient(app)
        resp = client.post("/api/items", json={"email": "dupe@test.com"})
        assert resp.status_code == 409
        assert "Duplicate" in resp.json()["detail"]
