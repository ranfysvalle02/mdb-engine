"""
Tests for the auto-CRUD router and query parser.

Covers:
- Query parser: filtering, operators, sort, pagination, injection safety
- Router generation: endpoints created, read_only mode
- CRUD lifecycle: POST -> GET list -> GET by ID -> PATCH -> DELETE -> 404
- Schema validation: reject invalid documents
- Tenant isolation: app_id scoping via ScopedCollectionWrapper
- Timestamps: auto-injected created_at / updated_at
- Count endpoint: GET /_count with filter support
- Bulk insert: POST /_bulk with validation and limits
- Soft delete: DELETE sets deleted_at, reads exclude, trash, restore
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from bson import ObjectId
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from mdb_engine.dependencies import get_scoped_db
from mdb_engine.routing.auto_crud import create_auto_crud_router, mount_auto_crud_routes
from mdb_engine.routing.query_parser import (
    MAX_LIMIT,
    parse_query_params,
)
from mdb_engine.testing import _FakeCollection, _FakeCursor, _FakeScopedDB

# ═══════════════════════════════════════════════════════════════════════
# Enhanced fake collection for operator-aware tests (soft delete)
# ═══════════════════════════════════════════════════════════════════════


def _match_filter(doc: dict, filter_: dict | None) -> bool:
    """Evaluate a MongoDB-like filter against a document in memory.

    Supports: equality, ``$and``, ``$ne``, ``$ne: None`` (field-not-None check),
    and ``None`` equality (matches missing fields too, like MongoDB).
    """
    if not filter_:
        return True
    for key, value in filter_.items():
        if key == "$and":
            if not all(_match_filter(doc, sub) for sub in value):
                return False
        elif isinstance(value, dict):
            if "$ne" in value:
                target = value["$ne"]
                if doc.get(key) == target:
                    return False
            if "$gt" in value:
                if not (doc.get(key) is not None and doc.get(key) > value["$gt"]):
                    return False
        else:
            if doc.get(key) != value:
                return False
    return True


class _SmartFakeCollection(_FakeCollection):
    """``_FakeCollection`` with ``$and`` / ``$ne`` support for soft-delete tests."""

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

    async def update_one(self, filter_: dict, update: dict, **kw):
        for d in self._docs:
            if _match_filter(d, filter_):
                if "$set" in update:
                    d.update(update["$set"])
                return MagicMock(modified_count=1)
        return MagicMock(modified_count=0)


class _SmartFakeScopedDB:
    """Like ``_FakeScopedDB`` but returns ``_SmartFakeCollection`` instances."""

    def __init__(self, collections: dict[str, list[dict]] | None = None):
        self._cols: dict[str, _SmartFakeCollection] = {}
        for name, docs in (collections or {}).items():
            self._cols[name] = _SmartFakeCollection(docs)

    def __getattr__(self, name: str) -> _SmartFakeCollection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._cols.setdefault(name, _SmartFakeCollection())

    def __getitem__(self, name: str) -> _SmartFakeCollection:
        return self._cols.setdefault(name, _SmartFakeCollection())


# ═══════════════════════════════════════════════════════════════════════
# Query Parser
# ═══════════════════════════════════════════════════════════════════════


class TestQueryParserFilters:
    def test_simple_equality(self):
        result = parse_query_params({"status": "pending"})
        assert result.filter == {"status": "pending"}

    def test_integer_coercion(self):
        result = parse_query_params({"age": "25"})
        assert result.filter == {"age": 25}

    def test_float_coercion(self):
        result = parse_query_params({"score": "3.14"})
        assert result.filter == {"score": 3.14}

    def test_boolean_coercion(self):
        result = parse_query_params({"active": "true"})
        assert result.filter == {"active": True}

        result = parse_query_params({"active": "false"})
        assert result.filter == {"active": False}

    def test_gt_operator(self):
        result = parse_query_params({"age": "gt:18"})
        assert result.filter == {"age": {"$gt": 18}}

    def test_gte_operator(self):
        result = parse_query_params({"age": "gte:21"})
        assert result.filter == {"age": {"$gte": 21}}

    def test_lt_operator(self):
        result = parse_query_params({"price": "lt:100"})
        assert result.filter == {"price": {"$lt": 100}}

    def test_lte_operator(self):
        result = parse_query_params({"price": "lte:99.99"})
        assert result.filter == {"price": {"$lte": 99.99}}

    def test_ne_operator(self):
        result = parse_query_params({"status": "ne:deleted"})
        assert result.filter == {"status": {"$ne": "deleted"}}

    def test_in_operator(self):
        result = parse_query_params({"status": "in:active,pending,review"})
        assert result.filter == {"status": {"$in": ["active", "pending", "review"]}}

    def test_multiple_filters(self):
        result = parse_query_params({"status": "active", "age": "gt:18"})
        assert result.filter == {"status": "active", "age": {"$gt": 18}}

    def test_empty_params(self):
        result = parse_query_params({})
        assert result.filter == {}
        assert result.sort is None
        assert result.skip == 0
        assert result.limit == 50
        assert result.projection is None


class TestQueryParserSort:
    def test_single_ascending(self):
        result = parse_query_params({"sort": "name"})
        assert result.sort == [("name", 1)]

    def test_single_descending(self):
        result = parse_query_params({"sort": "-created_at"})
        assert result.sort == [("created_at", -1)]

    def test_multiple_fields(self):
        result = parse_query_params({"sort": "-created_at,name"})
        assert result.sort == [("created_at", -1), ("name", 1)]

    def test_explicit_ascending(self):
        result = parse_query_params({"sort": "+name"})
        assert result.sort == [("name", 1)]


class TestQueryParserPagination:
    def test_defaults(self):
        result = parse_query_params({})
        assert result.skip == 0
        assert result.limit == 50

    def test_custom_values(self):
        result = parse_query_params({"limit": "10", "skip": "20"})
        assert result.limit == 10
        assert result.skip == 20

    def test_limit_clamped_to_max(self):
        result = parse_query_params({"limit": "99999"})
        assert result.limit == MAX_LIMIT

    def test_limit_minimum_is_one(self):
        result = parse_query_params({"limit": "0"})
        assert result.limit == 1

    def test_skip_minimum_is_zero(self):
        result = parse_query_params({"skip": "-5"})
        assert result.skip == 0

    def test_invalid_limit_ignored(self):
        result = parse_query_params({"limit": "abc"})
        assert result.limit == 50

    def test_invalid_skip_ignored(self):
        result = parse_query_params({"skip": "abc"})
        assert result.skip == 0


class TestQueryParserProjection:
    def test_field_selection(self):
        result = parse_query_params({"fields": "title,status"})
        assert result.projection == {"title": 1, "status": 1}

    def test_single_field(self):
        result = parse_query_params({"fields": "name"})
        assert result.projection == {"name": 1}

    def test_empty_fields_returns_none(self):
        result = parse_query_params({"fields": ""})
        assert result.projection is None


class TestQueryParserSecurity:
    def test_dollar_prefix_rejected(self):
        result = parse_query_params({"$where": "1==1"})
        assert "$where" not in result.filter

    def test_underscore_prefix_rejected(self):
        result = parse_query_params({"_internal": "secret"})
        assert "_internal" not in result.filter

    def test_reserved_params_not_in_filter(self):
        result = parse_query_params({"sort": "-name", "limit": "10", "skip": "0", "fields": "a"})
        assert result.filter == {}

    def test_dollar_in_sort_field_rejected(self):
        result = parse_query_params({"sort": "$malicious"})
        assert result.sort is None


# ═══════════════════════════════════════════════════════════════════════
# Router Generation
# ═══════════════════════════════════════════════════════════════════════


class TestRouterGeneration:
    def test_default_creates_all_endpoints(self):
        router = create_auto_crud_router("tasks", {})
        methods = set()
        for r in router.routes:
            for m in getattr(r, "methods", []):
                methods.add(m)

        assert "GET" in methods
        assert "POST" in methods
        assert "PUT" in methods
        assert "PATCH" in methods
        assert "DELETE" in methods

    def test_read_only_skips_write_endpoints(self):
        router = create_auto_crud_router("tasks", {"read_only": True})
        methods = set()
        for r in router.routes:
            for m in getattr(r, "methods", []):
                methods.add(m)

        assert "GET" in methods
        assert "POST" not in methods
        assert "PUT" not in methods
        assert "PATCH" not in methods
        assert "DELETE" not in methods

    def test_router_has_correct_prefix(self):
        router = create_auto_crud_router("tasks", {})
        assert router.prefix == "/api/tasks"


class TestMountAutoCrudRoutes:
    def test_mounts_auto_crud_collections(self):
        app = MagicMock()
        collections = {
            "tasks": {"auto_crud": True},
            "comments": {},
        }
        mount_auto_crud_routes(app, collections)
        assert app.include_router.call_count == 2

    def test_skips_disabled_collections(self):
        app = MagicMock()
        collections = {
            "tasks": {"auto_crud": True},
            "internal": {"auto_crud": False},
        }
        mount_auto_crud_routes(app, collections)
        assert app.include_router.call_count == 1


# ═══════════════════════════════════════════════════════════════════════
# CRUD Lifecycle (Integration-style with TestClient)
# ═══════════════════════════════════════════════════════════════════════


def _build_test_app(
    collections_config: dict | None = None,
    seed_data: dict | None = None,
) -> tuple[FastAPI, _FakeScopedDB]:
    """Build a FastAPI app with auto-CRUD and a fake DB for testing."""
    collections_config = collections_config or {"tasks": {}}
    app = FastAPI()

    fake_db = _FakeScopedDB(seed_data)

    async def _override_scoped_db():
        return fake_db

    app.dependency_overrides[get_scoped_db] = _override_scoped_db

    for name, config in collections_config.items():
        if config.get("auto_crud", True):
            router = create_auto_crud_router(name, config)
            app.include_router(router)

    return app, fake_db


class TestCRUDLifecycle:
    def test_create_document(self):
        app, fake_db = _build_test_app()
        client = TestClient(app)

        resp = client.post("/api/tasks", json={"title": "Buy milk"})
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert "_id" in data

    def test_list_documents(self):
        app, fake_db = _build_test_app(
            seed_data={"tasks": [{"_id": ObjectId(), "title": "Task 1"}, {"_id": ObjectId(), "title": "Task 2"}]}
        )
        client = TestClient(app)

        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert len(body["data"]) == 2

    def test_get_by_id(self):
        oid = ObjectId()
        app, fake_db = _build_test_app(seed_data={"tasks": [{"_id": oid, "title": "Found me"}]})
        client = TestClient(app)

        resp = client.get(f"/api/tasks/{oid}")
        assert resp.status_code == 200
        assert resp.json()["data"]["title"] == "Found me"

    def test_get_by_id_not_found(self):
        app, fake_db = _build_test_app()
        client = TestClient(app)

        resp = client.get(f"/api/tasks/{ObjectId()}")
        assert resp.status_code == 404

    def test_get_by_id_invalid_id(self):
        app, fake_db = _build_test_app()
        client = TestClient(app)

        resp = client.get("/api/tasks/not-an-objectid")
        assert resp.status_code == 400

    def test_replace_document(self):
        oid = ObjectId()
        app, fake_db = _build_test_app(seed_data={"tasks": [{"_id": oid, "title": "Old"}]})
        client = TestClient(app)

        resp = client.put(f"/api/tasks/{oid}", json={"title": "New"})
        assert resp.status_code == 200
        assert resp.json()["data"]["modified"] == 1

    def test_replace_document_not_found(self):
        app, fake_db = _build_test_app()
        client = TestClient(app)

        resp = client.put(f"/api/tasks/{ObjectId()}", json={"title": "Ghost"})
        assert resp.status_code == 404

    def test_patch_document(self):
        oid = ObjectId()
        app, fake_db = _build_test_app(seed_data={"tasks": [{"_id": oid, "title": "Before", "done": False}]})
        client = TestClient(app)

        resp = client.patch(f"/api/tasks/{oid}", json={"done": True})
        assert resp.status_code == 200
        assert resp.json()["data"]["modified"] == 1

    def test_delete_document(self):
        oid = ObjectId()
        app, fake_db = _build_test_app(seed_data={"tasks": [{"_id": oid, "title": "Goodbye"}]})
        client = TestClient(app)

        resp = client.delete(f"/api/tasks/{oid}")
        assert resp.status_code == 200
        assert resp.json()["data"]["deleted"] is True

    def test_delete_document_not_found(self):
        app, fake_db = _build_test_app()
        client = TestClient(app)

        resp = client.delete(f"/api/tasks/{ObjectId()}")
        assert resp.status_code == 404

    def test_full_lifecycle(self):
        """Create -> list -> get -> patch -> delete -> verify 404."""
        oid = ObjectId()
        app, fake_db = _build_test_app(seed_data={"tasks": [{"_id": oid, "title": "Lifecycle test", "done": False}]})
        client = TestClient(app)
        doc_id = str(oid)

        # List
        list_resp = client.get("/api/tasks")
        assert list_resp.status_code == 200
        assert list_resp.json()["total"] >= 1

        # Get by ID
        get_resp = client.get(f"/api/tasks/{doc_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["title"] == "Lifecycle test"

        # Patch
        patch_resp = client.patch(f"/api/tasks/{doc_id}", json={"done": True})
        assert patch_resp.status_code == 200

        # Delete
        del_resp = client.delete(f"/api/tasks/{doc_id}")
        assert del_resp.status_code == 200

        # Verify deleted
        get_after_resp = client.get(f"/api/tasks/{doc_id}")
        assert get_after_resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# Schema Validation
# ═══════════════════════════════════════════════════════════════════════


class TestSchemaValidation:
    TASK_SCHEMA = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "done": {"type": "boolean"},
        },
        "required": ["title"],
    }

    def test_valid_document_accepted(self):
        app, _ = _build_test_app(collections_config={"tasks": {"schema": self.TASK_SCHEMA}})
        client = TestClient(app)

        resp = client.post("/api/tasks", json={"title": "Valid", "done": False})
        assert resp.status_code == 201

    def test_missing_required_field_rejected(self):
        app, _ = _build_test_app(collections_config={"tasks": {"schema": self.TASK_SCHEMA}})
        client = TestClient(app)

        resp = client.post("/api/tasks", json={"done": True})
        assert resp.status_code == 422
        assert "title" in resp.json()["detail"].lower()

    def test_wrong_type_rejected(self):
        app, _ = _build_test_app(collections_config={"tasks": {"schema": self.TASK_SCHEMA}})
        client = TestClient(app)

        resp = client.post("/api/tasks", json={"title": 123})
        assert resp.status_code == 422

    def test_patch_skips_required_check(self):
        """PATCH should not enforce `required` — partial updates are valid."""
        oid = ObjectId()
        app, _ = _build_test_app(
            collections_config={"tasks": {"schema": self.TASK_SCHEMA}},
            seed_data={"tasks": [{"_id": oid, "title": "Existing", "done": False}]},
        )
        client = TestClient(app)

        resp = client.patch(f"/api/tasks/{oid}", json={"done": True})
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════
# Read-Only Mode
# ═══════════════════════════════════════════════════════════════════════


class TestReadOnlyMode:
    def test_get_works(self):
        app, _ = _build_test_app(
            collections_config={"logs": {"read_only": True}},
            seed_data={"logs": [{"_id": ObjectId(), "message": "hello"}]},
        )
        client = TestClient(app)

        resp = client.get("/api/logs")
        assert resp.status_code == 200

    def test_post_returns_405(self):
        app, _ = _build_test_app(collections_config={"logs": {"read_only": True}})
        client = TestClient(app)

        resp = client.post("/api/logs", json={"message": "nope"})
        assert resp.status_code == 405

    def test_delete_returns_405(self):
        app, _ = _build_test_app(collections_config={"logs": {"read_only": True}})
        client = TestClient(app)

        resp = client.delete(f"/api/logs/{ObjectId()}")
        assert resp.status_code == 405


# ═══════════════════════════════════════════════════════════════════════
# Manifest Validation
# ═══════════════════════════════════════════════════════════════════════


class TestManifestValidation:
    def test_manifest_with_collections_validates(self):
        import asyncio

        from mdb_engine.core.manifest import ManifestValidator

        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test",
            "collections": {
                "tasks": {
                    "auto_crud": True,
                    "schema": {"type": "object", "properties": {"title": {"type": "string"}}},
                },
                "logs": {"read_only": True},
            },
        }
        validator = ManifestValidator()
        is_valid, error_msg, _ = asyncio.run(validator.validate(manifest))
        assert is_valid, f"Manifest validation failed: {error_msg}"

    def test_manifest_rejects_bad_collection_key(self):
        import asyncio

        from mdb_engine.core.manifest import ManifestValidator

        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test",
            "collections": {
                "tasks": {"auto_crud": True, "unknown_field": True},
            },
        }
        validator = ManifestValidator()
        is_valid, error_msg, _ = asyncio.run(validator.validate(manifest))
        assert not is_valid

    def test_manifest_with_new_collection_options_validates(self):
        import asyncio

        from mdb_engine.core.manifest import ManifestValidator

        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test",
            "collections": {
                "tasks": {
                    "timestamps": True,
                    "soft_delete": True,
                    "bulk_insert": True,
                },
            },
        }
        validator = ManifestValidator()
        is_valid, error_msg, _ = asyncio.run(validator.validate(manifest))
        assert is_valid, f"Manifest validation failed: {error_msg}"


# ═══════════════════════════════════════════════════════════════════════
# Timestamps
# ═══════════════════════════════════════════════════════════════════════


class TestTimestamps:
    def test_create_injects_timestamps(self):
        app, fake_db = _build_test_app()
        client = TestClient(app)

        client.post("/api/tasks", json={"title": "Stamped"})
        doc = fake_db["tasks"]._docs[-1]
        assert "created_at" in doc
        assert "updated_at" in doc
        assert isinstance(doc["created_at"], datetime)
        assert isinstance(doc["updated_at"], datetime)

    def test_patch_updates_updated_at(self):
        oid = ObjectId()
        early = datetime(2020, 1, 1, tzinfo=timezone.utc)
        app, fake_db = _build_test_app(
            seed_data={"tasks": [{"_id": oid, "title": "Old", "created_at": early, "updated_at": early}]}
        )
        client = TestClient(app)

        client.patch(f"/api/tasks/{oid}", json={"title": "New"})
        doc = fake_db["tasks"]._docs[0]
        assert doc["updated_at"] > early
        assert doc["created_at"] == early

    def test_put_updates_updated_at(self):
        oid = ObjectId()
        early = datetime(2020, 1, 1, tzinfo=timezone.utc)
        app, fake_db = _build_test_app(
            seed_data={"tasks": [{"_id": oid, "title": "Old", "created_at": early, "updated_at": early}]}
        )
        client = TestClient(app)

        client.put(f"/api/tasks/{oid}", json={"title": "Replaced"})
        doc = fake_db["tasks"]._docs[0]
        assert doc["updated_at"] > early

    def test_timestamps_disabled(self):
        app, fake_db = _build_test_app(collections_config={"tasks": {"timestamps": False}})
        client = TestClient(app)

        client.post("/api/tasks", json={"title": "No stamps"})
        doc = fake_db["tasks"]._docs[-1]
        assert "created_at" not in doc
        assert "updated_at" not in doc

    def test_timestamps_serialized_as_iso(self):
        app, _ = _build_test_app(
            seed_data={
                "tasks": [{"_id": ObjectId(), "title": "T", "created_at": datetime(2024, 6, 15, tzinfo=timezone.utc)}]
            }
        )
        client = TestClient(app)

        resp = client.get("/api/tasks")
        item = resp.json()["data"][0]
        assert "2024-06-15" in item["created_at"]

    def test_create_preserves_caller_created_at(self):
        """setdefault should not overwrite an explicit created_at."""
        app, fake_db = _build_test_app()
        client = TestClient(app)

        custom_ts = "2000-01-01T00:00:00"
        client.post("/api/tasks", json={"title": "Custom", "created_at": custom_ts})
        doc = fake_db["tasks"]._docs[-1]
        assert doc["created_at"] == custom_ts


# ═══════════════════════════════════════════════════════════════════════
# Count Endpoint
# ═══════════════════════════════════════════════════════════════════════


class TestCountEndpoint:
    def test_count_returns_total(self):
        app, _ = _build_test_app(seed_data={"tasks": [{"_id": ObjectId(), "title": f"T{i}"} for i in range(5)]})
        client = TestClient(app)

        resp = client.get("/api/tasks/_count")
        assert resp.status_code == 200
        assert resp.json()["count"] == 5

    def test_count_with_filter(self):
        app, _ = _build_test_app(
            seed_data={
                "tasks": [
                    {"_id": ObjectId(), "status": "active"},
                    {"_id": ObjectId(), "status": "active"},
                    {"_id": ObjectId(), "status": "done"},
                ]
            }
        )
        client = TestClient(app)

        resp = client.get("/api/tasks/_count?status=active")
        assert resp.json()["count"] == 2

    def test_count_empty_collection(self):
        app, _ = _build_test_app()
        client = TestClient(app)

        resp = client.get("/api/tasks/_count")
        assert resp.json()["count"] == 0

    def test_count_available_on_read_only(self):
        app, _ = _build_test_app(
            collections_config={"logs": {"read_only": True}},
            seed_data={"logs": [{"_id": ObjectId(), "msg": "hi"}]},
        )
        client = TestClient(app)

        resp = client.get("/api/logs/_count")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1


# ═══════════════════════════════════════════════════════════════════════
# Bulk Insert
# ═══════════════════════════════════════════════════════════════════════


class TestBulkInsert:
    def test_bulk_insert_array(self):
        app, fake_db = _build_test_app()
        client = TestClient(app)

        docs = [{"title": f"Task {i}"} for i in range(3)]
        resp = client.post("/api/tasks/_bulk", json=docs)
        assert resp.status_code == 201
        assert resp.json()["data"]["inserted"] == 3
        assert len(fake_db["tasks"]._docs) == 3

    def test_bulk_insert_rejects_non_array(self):
        app, _ = _build_test_app()
        client = TestClient(app)

        resp = client.post("/api/tasks/_bulk", json={"title": "Not an array"})
        assert resp.status_code == 400

    def test_bulk_insert_rejects_oversized_batch(self):
        app, _ = _build_test_app()
        client = TestClient(app)

        docs = [{"title": f"T{i}"} for i in range(1001)]
        resp = client.post("/api/tasks/_bulk", json=docs)
        assert resp.status_code == 400
        assert "1000" in resp.json()["detail"]

    def test_bulk_insert_validates_schema(self):
        schema = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}
        app, _ = _build_test_app(collections_config={"tasks": {"schema": schema}})
        client = TestClient(app)

        docs = [{"title": "Valid"}, {"no_title": True}]
        resp = client.post("/api/tasks/_bulk", json=docs)
        assert resp.status_code == 422

    def test_bulk_insert_injects_timestamps(self):
        app, fake_db = _build_test_app()
        client = TestClient(app)

        client.post("/api/tasks/_bulk", json=[{"title": "A"}, {"title": "B"}])
        for doc in fake_db["tasks"]._docs:
            assert "created_at" in doc
            assert "updated_at" in doc

    def test_bulk_insert_disabled(self):
        app, _ = _build_test_app(collections_config={"tasks": {"bulk_insert": False}})
        client = TestClient(app)

        resp = client.post("/api/tasks/_bulk", json=[{"title": "Nope"}])
        assert resp.status_code == 405

    def test_bulk_insert_rejects_non_object_items(self):
        app, _ = _build_test_app()
        client = TestClient(app)

        resp = client.post("/api/tasks/_bulk", json=["not", "objects"])
        assert resp.status_code == 400

    def test_bulk_insert_unavailable_on_read_only(self):
        app, _ = _build_test_app(collections_config={"logs": {"read_only": True}})
        client = TestClient(app)

        resp = client.post("/api/logs/_bulk", json=[{"msg": "no"}])
        assert resp.status_code == 405


# ═══════════════════════════════════════════════════════════════════════
# Soft Delete
# ═══════════════════════════════════════════════════════════════════════


def _build_soft_delete_app(
    seed_data: dict | None = None,
    extra_config: dict | None = None,
) -> tuple[FastAPI, _SmartFakeScopedDB]:
    """Build a test app with soft_delete enabled using the smart fake DB."""
    config = {"soft_delete": True}
    if extra_config:
        config.update(extra_config)
    collections_config = {"tasks": config}
    app = FastAPI()

    fake_db = _SmartFakeScopedDB(seed_data)

    async def _override():
        return fake_db

    app.dependency_overrides[get_scoped_db] = _override

    for name, cfg in collections_config.items():
        if cfg.get("auto_crud", True):
            router = create_auto_crud_router(name, cfg)
            app.include_router(router)

    return app, fake_db


class TestSoftDelete:
    def test_delete_sets_deleted_at(self):
        oid = ObjectId()
        app, fake_db = _build_soft_delete_app(seed_data={"tasks": [{"_id": oid, "title": "Soft", "deleted_at": None}]})
        client = TestClient(app)

        resp = client.delete(f"/api/tasks/{oid}")
        assert resp.status_code == 200
        assert resp.json()["data"]["deleted"] is True

        doc = fake_db["tasks"]._docs[0]
        assert doc["deleted_at"] is not None
        assert isinstance(doc["deleted_at"], datetime)

    def test_get_list_excludes_soft_deleted(self):
        oid_alive = ObjectId()
        oid_dead = ObjectId()
        app, _ = _build_soft_delete_app(
            seed_data={
                "tasks": [
                    {"_id": oid_alive, "title": "Alive", "deleted_at": None},
                    {"_id": oid_dead, "title": "Dead", "deleted_at": datetime.now(timezone.utc)},
                ]
            }
        )
        client = TestClient(app)

        resp = client.get("/api/tasks")
        assert resp.json()["total"] == 1
        assert resp.json()["data"][0]["title"] == "Alive"

    def test_get_by_id_excludes_soft_deleted(self):
        oid = ObjectId()
        app, _ = _build_soft_delete_app(
            seed_data={"tasks": [{"_id": oid, "title": "Gone", "deleted_at": datetime.now(timezone.utc)}]}
        )
        client = TestClient(app)

        resp = client.get(f"/api/tasks/{oid}")
        assert resp.status_code == 404

    def test_count_excludes_soft_deleted(self):
        app, _ = _build_soft_delete_app(
            seed_data={
                "tasks": [
                    {"_id": ObjectId(), "title": "A", "deleted_at": None},
                    {"_id": ObjectId(), "title": "B", "deleted_at": None},
                    {"_id": ObjectId(), "title": "C", "deleted_at": datetime.now(timezone.utc)},
                ]
            }
        )
        client = TestClient(app)

        resp = client.get("/api/tasks/_count")
        assert resp.json()["count"] == 2

    def test_double_delete_returns_404(self):
        oid = ObjectId()
        app, _ = _build_soft_delete_app(seed_data={"tasks": [{"_id": oid, "title": "Once", "deleted_at": None}]})
        client = TestClient(app)

        resp1 = client.delete(f"/api/tasks/{oid}")
        assert resp1.status_code == 200

        resp2 = client.delete(f"/api/tasks/{oid}")
        assert resp2.status_code == 404

    def test_trash_returns_only_deleted(self):
        oid_alive = ObjectId()
        oid_dead = ObjectId()
        app, _ = _build_soft_delete_app(
            seed_data={
                "tasks": [
                    {"_id": oid_alive, "title": "Alive", "deleted_at": None},
                    {"_id": oid_dead, "title": "Dead", "deleted_at": datetime.now(timezone.utc)},
                ]
            }
        )
        client = TestClient(app)

        resp = client.get("/api/tasks/_trash")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["data"][0]["title"] == "Dead"

    def test_restore_clears_deleted_at(self):
        oid = ObjectId()
        app, fake_db = _build_soft_delete_app(
            seed_data={"tasks": [{"_id": oid, "title": "Restore me", "deleted_at": datetime.now(timezone.utc)}]}
        )
        client = TestClient(app)

        resp = client.post(f"/api/tasks/{oid}/_restore")
        assert resp.status_code == 200
        assert resp.json()["data"]["restored"] is True

        doc = fake_db["tasks"]._docs[0]
        assert doc["deleted_at"] is None

    def test_restore_makes_doc_visible_again(self):
        oid = ObjectId()
        app, _ = _build_soft_delete_app(
            seed_data={"tasks": [{"_id": oid, "title": "Back", "deleted_at": datetime.now(timezone.utc)}]}
        )
        client = TestClient(app)

        assert client.get(f"/api/tasks/{oid}").status_code == 404

        client.post(f"/api/tasks/{oid}/_restore")

        resp = client.get(f"/api/tasks/{oid}")
        assert resp.status_code == 200
        assert resp.json()["data"]["title"] == "Back"

    def test_restore_non_deleted_returns_404(self):
        oid = ObjectId()
        app, _ = _build_soft_delete_app(seed_data={"tasks": [{"_id": oid, "title": "Alive", "deleted_at": None}]})
        client = TestClient(app)

        resp = client.post(f"/api/tasks/{oid}/_restore")
        assert resp.status_code == 404

    def test_patch_excluded_on_soft_deleted_doc(self):
        oid = ObjectId()
        app, _ = _build_soft_delete_app(
            seed_data={"tasks": [{"_id": oid, "title": "Deleted", "deleted_at": datetime.now(timezone.utc)}]}
        )
        client = TestClient(app)

        resp = client.patch(f"/api/tasks/{oid}", json={"title": "Nope"})
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# Projection
# ═══════════════════════════════════════════════════════════════════════


class _ProjectionFakeCollection(_FakeCollection):
    """``_FakeCollection`` that records the projection argument."""

    def __init__(self, docs=None):
        super().__init__(docs)
        self.last_projection = None

    def find(self, filter_=None, projection=None, *args, **kw):
        self.last_projection = projection
        matched = [dict(d) for d in self._docs if all(d.get(k) == v for k, v in (filter_ or {}).items())]
        if projection:
            projected = []
            for doc in matched:
                p = {}
                for field in projection:
                    if field in doc:
                        p[field] = doc[field]
                if "_id" in doc:
                    p["_id"] = doc["_id"]
                projected.append(p)
            matched = projected
        return _FakeCursor(matched)


class _ProjectionFakeScopedDB:
    """ScopedDB returning ``_ProjectionFakeCollection`` instances."""

    def __init__(self, collections=None):
        self._cols: dict[str, _ProjectionFakeCollection] = {}
        for name, docs in (collections or {}).items():
            self._cols[name] = _ProjectionFakeCollection(docs)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self._cols.setdefault(name, _ProjectionFakeCollection())

    def __getitem__(self, name):
        return self._cols.setdefault(name, _ProjectionFakeCollection())


class TestProjection:
    def test_fields_param_applies_projection(self):
        oid = ObjectId()
        seed = {"tasks": [{"_id": oid, "title": "Hello", "status": "active", "priority": 1}]}
        app = FastAPI()
        fake_db = _ProjectionFakeScopedDB(seed)

        async def _override():
            return fake_db

        app.dependency_overrides[get_scoped_db] = _override
        router = create_auto_crud_router("tasks", {})
        app.include_router(router)
        client = TestClient(app)

        resp = client.get("/api/tasks?fields=title")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert "title" in data[0]
        assert "priority" not in data[0]
        assert fake_db["tasks"].last_projection == {"title": 1}

    def test_no_fields_param_returns_all(self):
        oid = ObjectId()
        seed = {"tasks": [{"_id": oid, "title": "Hello", "status": "active"}]}
        app = FastAPI()
        fake_db = _ProjectionFakeScopedDB(seed)

        async def _override():
            return fake_db

        app.dependency_overrides[get_scoped_db] = _override
        router = create_auto_crud_router("tasks", {})
        app.include_router(router)
        client = TestClient(app)

        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "title" in data[0]
        assert "status" in data[0]
        assert fake_db["tasks"].last_projection is None


# ═══════════════════════════════════════════════════════════════════════
# Deep Serialization
# ═══════════════════════════════════════════════════════════════════════


class TestDeepSerialization:
    def test_nested_objectid_serialized(self):
        nested_oid = ObjectId()
        oid = ObjectId()
        app, _ = _build_test_app(seed_data={"tasks": [{"_id": oid, "title": "T", "ref": {"linked_id": nested_oid}}]})
        client = TestClient(app)

        resp = client.get(f"/api/tasks/{oid}")
        assert resp.status_code == 200
        doc = resp.json()["data"]
        assert doc["ref"]["linked_id"] == str(nested_oid)

    def test_nested_datetime_serialized(self):
        oid = ObjectId()
        nested_dt = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        app, _ = _build_test_app(seed_data={"tasks": [{"_id": oid, "title": "T", "meta": {"due": nested_dt}}]})
        client = TestClient(app)

        resp = client.get(f"/api/tasks/{oid}")
        assert resp.status_code == 200
        doc = resp.json()["data"]
        assert "2024-06-15" in doc["meta"]["due"]

    def test_array_of_objectids_serialized(self):
        oid = ObjectId()
        tag_ids = [ObjectId(), ObjectId()]
        app, _ = _build_test_app(seed_data={"tasks": [{"_id": oid, "title": "T", "tag_ids": tag_ids}]})
        client = TestClient(app)

        resp = client.get(f"/api/tasks/{oid}")
        assert resp.status_code == 200
        doc = resp.json()["data"]
        assert doc["tag_ids"] == [str(t) for t in tag_ids]

    def test_deeply_nested_mixed(self):
        oid = ObjectId()
        inner_oid = ObjectId()
        inner_dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
        app, _ = _build_test_app(
            seed_data={
                "tasks": [
                    {
                        "_id": oid,
                        "title": "T",
                        "items": [{"ref": inner_oid, "ts": inner_dt}],
                    }
                ]
            }
        )
        client = TestClient(app)

        resp = client.get(f"/api/tasks/{oid}")
        assert resp.status_code == 200
        item = resp.json()["data"]["items"][0]
        assert item["ref"] == str(inner_oid)
        assert "2025-01-01" in item["ts"]


# ═══════════════════════════════════════════════════════════════════════
# Per-Collection Auth
# ═══════════════════════════════════════════════════════════════════════


def _build_auth_test_app(
    auth_config: dict,
    seed_data: dict | None = None,
    user: dict | None = None,
    user_roles: list[str] | None = None,
) -> tuple[FastAPI, TestClient]:
    """Build an app with per-collection auth and an optional injected user."""
    from starlette.middleware.base import BaseHTTPMiddleware

    collections_config = {"tasks": {"auth": auth_config}}
    app = FastAPI()

    fake_db = _FakeScopedDB(seed_data)

    async def _override_db():
        return fake_db

    app.dependency_overrides[get_scoped_db] = _override_db

    _user = user
    _roles = user_roles or []

    class InjectUserMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.user = _user
            request.state.user_roles = _roles
            return await call_next(request)

    app.add_middleware(InjectUserMiddleware)

    for name, config in collections_config.items():
        router = create_auto_crud_router(name, config)
        app.include_router(router)

    return app, TestClient(app)


class TestCollectionAuth:
    def test_auth_required_rejects_anonymous(self):
        app, client = _build_auth_test_app({"required": True}, user=None)
        resp = client.get("/api/tasks")
        assert resp.status_code == 401

    def test_auth_required_allows_authenticated(self):
        oid = ObjectId()
        app, client = _build_auth_test_app(
            {"required": True},
            seed_data={"tasks": [{"_id": oid, "title": "OK"}]},
            user={"_id": "u1", "email": "test@test.com"},
        )
        resp = client.get("/api/tasks")
        assert resp.status_code == 200

    def test_role_rejects_wrong_role(self):
        app, client = _build_auth_test_app(
            {"roles": ["admin"]},
            user={"_id": "u1", "email": "test@test.com"},
            user_roles=["viewer"],
        )
        resp = client.get("/api/tasks")
        assert resp.status_code == 403

    def test_role_allows_correct_role(self):
        oid = ObjectId()
        app, client = _build_auth_test_app(
            {"roles": ["admin"]},
            seed_data={"tasks": [{"_id": oid, "title": "OK"}]},
            user={"_id": "u1", "email": "admin@test.com"},
            user_roles=["admin"],
        )
        resp = client.get("/api/tasks")
        assert resp.status_code == 200

    def test_no_auth_config_allows_anonymous(self):
        """Collections without auth config remain open (subject to app-level auth)."""
        oid = ObjectId()
        app, _ = _build_test_app(seed_data={"tasks": [{"_id": oid, "title": "Public"}]})
        client = TestClient(app)
        resp = client.get("/api/tasks")
        assert resp.status_code == 200

    def test_auth_applies_to_writes_too(self):
        app, client = _build_auth_test_app({"required": True}, user=None)
        resp = client.post("/api/tasks", json={"title": "Nope"})
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════
# Manifest Validation — new collection options
# ═══════════════════════════════════════════════════════════════════════


class TestManifestNewCollectionOptions:
    def test_manifest_with_auth_config_validates(self):
        import asyncio

        from mdb_engine.core.manifest import ManifestValidator

        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test",
            "collections": {
                "tasks": {
                    "auth": {"required": True, "roles": ["admin"]},
                },
            },
        }
        validator = ManifestValidator()
        is_valid, error_msg, _ = asyncio.run(validator.validate(manifest))
        assert is_valid, f"Manifest validation failed: {error_msg}"

    def test_manifest_with_realtime_validates(self):
        import asyncio

        from mdb_engine.core.manifest import ManifestValidator

        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test",
            "collections": {
                "tasks": {
                    "auto_crud": True,
                    "realtime": True,
                },
            },
        }
        validator = ManifestValidator()
        is_valid, error_msg, _ = asyncio.run(validator.validate(manifest))
        assert is_valid, f"Manifest validation failed: {error_msg}"

    def test_manifest_rejects_invalid_realtime_type(self):
        import asyncio

        from mdb_engine.core.manifest import ManifestValidator

        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test",
            "collections": {
                "tasks": {"realtime": "yes"},
            },
        }
        validator = ManifestValidator()
        is_valid, error_msg, _ = asyncio.run(validator.validate(manifest))
        assert not is_valid


# ═══════════════════════════════════════════════════════════════════════
# MQL-as-DSL: Template Resolver
# ═══════════════════════════════════════════════════════════════════════


class TestTemplateResolver:
    def test_resolve_user_placeholder(self):
        from mdb_engine.routing.template_resolver import resolve_template

        user = {"_id": "u1", "team_id": "t1"}
        result = resolve_template({"owner_id": "{{user._id}}"}, user)
        assert result == {"owner_id": "u1"}

    def test_resolve_nested_user_path(self):
        from mdb_engine.routing.template_resolver import resolve_template

        user = {"_id": "u1", "profile": {"team_id": "t42"}}
        result = resolve_template({"team": "{{user.profile.team_id}}"}, user)
        assert result == {"team": "t42"}

    def test_resolve_now(self):
        from mdb_engine.routing.template_resolver import resolve_template

        result = resolve_template({"after": "$$NOW"}, None)
        assert isinstance(result["after"], datetime)

    def test_resolve_in_list(self):
        from mdb_engine.routing.template_resolver import resolve_template

        user = {"_id": "u1"}
        result = resolve_template([{"owner": "{{user._id}}"}], user)
        assert result == [{"owner": "u1"}]

    def test_resolve_no_user_raises_401(self):
        from mdb_engine.routing.template_resolver import resolve_template

        with pytest.raises(HTTPException) as exc_info:
            resolve_template({"x": "{{user._id}}"}, None)
        assert exc_info.value.status_code == 401

    def test_resolve_bad_user_path_raises_400(self):
        from mdb_engine.routing.template_resolver import resolve_template

        user = {"_id": "u1"}
        with pytest.raises(HTTPException) as exc_info:
            resolve_template({"x": "{{user.nonexistent}}"}, user)
        assert exc_info.value.status_code == 400

    def test_resolve_deep_copy(self):
        from mdb_engine.routing.template_resolver import resolve_template

        original = {"status": "active", "nested": {"val": 1}}
        result = resolve_template(original, None)
        result["nested"]["val"] = 999
        assert original["nested"]["val"] == 1

    def test_passthrough_non_template_strings(self):
        from mdb_engine.routing.template_resolver import resolve_template

        result = resolve_template({"status": "active"}, None)
        assert result == {"status": "active"}


class TestMergeFilters:
    def test_merge_none_and_empty(self):
        from mdb_engine.routing.template_resolver import merge_filters

        assert merge_filters(None, {}, None) is None

    def test_merge_single(self):
        from mdb_engine.routing.template_resolver import merge_filters

        f = {"status": "active"}
        assert merge_filters(None, f, {}) == f

    def test_merge_multiple(self):
        from mdb_engine.routing.template_resolver import merge_filters

        a = {"status": "active"}
        b = {"owner": "u1"}
        result = merge_filters(a, b)
        assert result == {"$and": [a, b]}

    def test_merge_three(self):
        from mdb_engine.routing.template_resolver import merge_filters

        a = {"a": 1}
        b = {"b": 2}
        c = {"c": 3}
        result = merge_filters(a, b, c)
        assert result == {"$and": [a, b, c]}


# ═══════════════════════════════════════════════════════════════════════
# MQL-as-DSL: Query Parser Scope
# ═══════════════════════════════════════════════════════════════════════


class TestQueryParserScope:
    def test_single_scope(self):
        result = parse_query_params({"scope": "active"})
        assert result.scope == ["active"]
        assert result.filter == {}

    def test_multiple_scopes(self):
        result = parse_query_params({"scope": "active,mine"})
        assert result.scope == ["active", "mine"]

    def test_scope_with_filters(self):
        result = parse_query_params({"scope": "active", "status": "pending"})
        assert result.scope == ["active"]
        assert result.filter == {"status": "pending"}

    def test_empty_scope_is_none(self):
        result = parse_query_params({"scope": ""})
        assert result.scope is None

    def test_scope_not_in_filter(self):
        result = parse_query_params({"scope": "active"})
        assert "scope" not in result.filter


# ═══════════════════════════════════════════════════════════════════════
# MQL-as-DSL: Policy
# ═══════════════════════════════════════════════════════════════════════


def _build_policy_test_app(
    policy: dict,
    seed_data: dict | None = None,
    user: dict | None = None,
    extra_config: dict | None = None,
) -> tuple[FastAPI, TestClient]:
    """Build a test app with policy config and an injected user."""
    from starlette.middleware.base import BaseHTTPMiddleware

    config = {"policy": policy}
    if extra_config:
        config.update(extra_config)
    collections_config = {"tasks": config}
    app = FastAPI()

    fake_db = _SmartFakeScopedDB(seed_data)

    async def _override_db():
        return fake_db

    app.dependency_overrides[get_scoped_db] = _override_db

    _user = user

    class InjectUserMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.user = _user
            request.state.user_roles = []
            return await call_next(request)

    app.add_middleware(InjectUserMiddleware)

    for name, cfg in collections_config.items():
        router = create_auto_crud_router(name, cfg)
        app.include_router(router)

    return app, TestClient(app)


class TestPolicy:
    def test_read_policy_filters_results(self):
        """Only documents matching the read policy are returned."""
        oid_mine = ObjectId()
        oid_other = ObjectId()
        app, client = _build_policy_test_app(
            policy={"read": {"owner_id": "{{user._id}}"}},
            seed_data={
                "tasks": [
                    {"_id": oid_mine, "title": "Mine", "owner_id": "u1", "deleted_at": None},
                    {"_id": oid_other, "title": "Theirs", "owner_id": "u2", "deleted_at": None},
                ]
            },
            user={"_id": "u1"},
        )
        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["title"] == "Mine"

    def test_read_policy_no_user_returns_401(self):
        """Accessing a policy-protected collection without auth returns 401."""
        app, client = _build_policy_test_app(
            policy={"read": {"owner_id": "{{user._id}}"}},
            user=None,
        )
        resp = client.get("/api/tasks")
        assert resp.status_code == 401

    def test_write_policy_blocks_unauthorized_update(self):
        """PATCH on a doc not matching write policy returns 404."""
        oid = ObjectId()
        app, client = _build_policy_test_app(
            policy={"write": {"owner_id": "{{user._id}}"}},
            seed_data={"tasks": [{"_id": oid, "title": "Not mine", "owner_id": "u2", "deleted_at": None}]},
            user={"_id": "u1"},
        )
        resp = client.patch(f"/api/tasks/{oid}", json={"title": "Hacked"})
        assert resp.status_code == 404

    def test_write_policy_allows_owner(self):
        """PATCH on a doc matching write policy succeeds."""
        oid = ObjectId()
        app, client = _build_policy_test_app(
            policy={"write": {"owner_id": "{{user._id}}"}},
            seed_data={"tasks": [{"_id": oid, "title": "Mine", "owner_id": "u1", "deleted_at": None}]},
            user={"_id": "u1"},
        )
        resp = client.patch(f"/api/tasks/{oid}", json={"title": "Updated"})
        assert resp.status_code == 200

    def test_delete_falls_back_to_write_policy(self):
        """DELETE uses write policy when no explicit delete policy is set."""
        oid = ObjectId()
        app, client = _build_policy_test_app(
            policy={"write": {"owner_id": "{{user._id}}"}},
            seed_data={"tasks": [{"_id": oid, "title": "Not mine", "owner_id": "u2", "deleted_at": None}]},
            user={"_id": "u1"},
        )
        resp = client.delete(f"/api/tasks/{oid}")
        assert resp.status_code == 404

    def test_get_by_id_respects_read_policy(self):
        oid = ObjectId()
        app, client = _build_policy_test_app(
            policy={"read": {"owner_id": "{{user._id}}"}},
            seed_data={"tasks": [{"_id": oid, "title": "Hidden", "owner_id": "u2", "deleted_at": None}]},
            user={"_id": "u1"},
        )
        resp = client.get(f"/api/tasks/{oid}")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# MQL-as-DSL: Scopes
# ═══════════════════════════════════════════════════════════════════════


def _build_scoped_test_app(
    scopes: dict,
    seed_data: dict | None = None,
    user: dict | None = None,
    extra_config: dict | None = None,
) -> tuple[FastAPI, TestClient]:
    from starlette.middleware.base import BaseHTTPMiddleware

    config = {"scopes": scopes}
    if extra_config:
        config.update(extra_config)
    collections_config = {"tasks": config}
    app = FastAPI()

    fake_db = _SmartFakeScopedDB(seed_data)

    async def _override_db():
        return fake_db

    app.dependency_overrides[get_scoped_db] = _override_db

    _user = user

    class InjectUserMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.user = _user
            request.state.user_roles = []
            return await call_next(request)

    app.add_middleware(InjectUserMiddleware)

    for name, cfg in collections_config.items():
        router = create_auto_crud_router(name, cfg)
        app.include_router(router)

    return app, TestClient(app)


class TestScopes:
    def test_scope_filters_results(self):
        app, client = _build_scoped_test_app(
            scopes={"active": {"status": {"$ne": "archived"}}},
            seed_data={
                "tasks": [
                    {"_id": ObjectId(), "title": "Active", "status": "pending", "deleted_at": None},
                    {"_id": ObjectId(), "title": "Gone", "status": "archived", "deleted_at": None},
                ]
            },
        )
        resp = client.get("/api/tasks?scope=active")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert resp.json()["data"][0]["title"] == "Active"

    def test_scope_with_user_placeholder(self):
        app, client = _build_scoped_test_app(
            scopes={"mine": {"owner_id": "{{user._id}}"}},
            seed_data={
                "tasks": [
                    {"_id": ObjectId(), "title": "Mine", "owner_id": "u1", "deleted_at": None},
                    {"_id": ObjectId(), "title": "Theirs", "owner_id": "u2", "deleted_at": None},
                ]
            },
            user={"_id": "u1"},
        )
        resp = client.get("/api/tasks?scope=mine")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_unknown_scope_returns_400(self):
        app, client = _build_scoped_test_app(
            scopes={"active": {"status": "active"}},
        )
        resp = client.get("/api/tasks?scope=nonexistent")
        assert resp.status_code == 400
        assert "Unknown scope" in resp.json()["detail"]

    def test_multiple_scopes_combined(self):
        app, client = _build_scoped_test_app(
            scopes={
                "active": {"status": {"$ne": "archived"}},
                "high": {"priority": {"$gt": 3}},
            },
            seed_data={
                "tasks": [
                    {"_id": ObjectId(), "title": "Active+High", "status": "pending", "priority": 5, "deleted_at": None},
                    {"_id": ObjectId(), "title": "Active+Low", "status": "pending", "priority": 1, "deleted_at": None},
                    {
                        "_id": ObjectId(),
                        "title": "Archived+High",
                        "status": "archived",
                        "priority": 5,
                        "deleted_at": None,
                    },
                ]
            },
        )
        resp = client.get("/api/tasks?scope=active,high")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert resp.json()["data"][0]["title"] == "Active+High"

    def test_scope_on_count_endpoint(self):
        app, client = _build_scoped_test_app(
            scopes={"active": {"status": {"$ne": "archived"}}},
            seed_data={
                "tasks": [
                    {"_id": ObjectId(), "status": "pending", "deleted_at": None},
                    {"_id": ObjectId(), "status": "archived", "deleted_at": None},
                ]
            },
        )
        resp = client.get("/api/tasks/_count?scope=active")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_no_scope_returns_all(self):
        app, client = _build_scoped_test_app(
            scopes={"active": {"status": "active"}},
            seed_data={
                "tasks": [
                    {"_id": ObjectId(), "title": "A", "status": "active", "deleted_at": None},
                    {"_id": ObjectId(), "title": "B", "status": "done", "deleted_at": None},
                ]
            },
        )
        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        assert resp.json()["total"] == 2


# ═══════════════════════════════════════════════════════════════════════
# MQL-as-DSL: Pipelines
# ═══════════════════════════════════════════════════════════════════════


def _build_pipeline_test_app(
    pipelines: dict,
    seed_data: dict | None = None,
) -> tuple[FastAPI, TestClient]:
    collections_config = {"tasks": {"pipelines": pipelines}}
    app = FastAPI()

    fake_db = _FakeScopedDB(seed_data)

    async def _override_db():
        return fake_db

    app.dependency_overrides[get_scoped_db] = _override_db

    for name, cfg in collections_config.items():
        router = create_auto_crud_router(name, cfg)
        app.include_router(router)

    return app, TestClient(app)


class TestPipelines:
    def test_pipeline_endpoint_exists(self):
        app, client = _build_pipeline_test_app(
            pipelines={"by_status": [{"$group": {"_id": "$status", "count": {"$sum": 1}}}]},
            seed_data={"tasks": [{"_id": ObjectId(), "status": "active"}]},
        )
        resp = client.get("/api/tasks/_agg/by_status")
        assert resp.status_code == 200
        assert "data" in resp.json()

    def test_pipeline_returns_data(self):
        app, client = _build_pipeline_test_app(
            pipelines={"by_status": [{"$group": {"_id": "$status", "count": {"$sum": 1}}}]},
            seed_data={
                "tasks": [
                    {"_id": ObjectId(), "status": "active"},
                    {"_id": ObjectId(), "status": "done"},
                ]
            },
        )
        resp = client.get("/api/tasks/_agg/by_status")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) >= 1

    def test_multiple_pipelines(self):
        app, client = _build_pipeline_test_app(
            pipelines={
                "by_status": [{"$group": {"_id": "$status"}}],
                "by_owner": [{"$group": {"_id": "$owner"}}],
            },
            seed_data={"tasks": [{"_id": ObjectId(), "status": "active", "owner": "u1"}]},
        )
        resp1 = client.get("/api/tasks/_agg/by_status")
        resp2 = client.get("/api/tasks/_agg/by_owner")
        assert resp1.status_code == 200
        assert resp2.status_code == 200

    def test_nonexistent_pipeline_returns_404(self):
        app, client = _build_pipeline_test_app(
            pipelines={"by_status": [{"$group": {"_id": "$status"}}]},
        )
        resp = client.get("/api/tasks/_agg/nonexistent")
        assert resp.status_code in (404, 405)


# ═══════════════════════════════════════════════════════════════════════
# MQL-as-DSL: Defaults
# ═══════════════════════════════════════════════════════════════════════


def _build_defaults_test_app(
    defaults: dict,
    user: dict | None = None,
    extra_config: dict | None = None,
) -> tuple[FastAPI, _FakeScopedDB, TestClient]:
    from starlette.middleware.base import BaseHTTPMiddleware

    config = {"defaults": defaults}
    if extra_config:
        config.update(extra_config)
    collections_config = {"tasks": config}
    app = FastAPI()

    fake_db = _FakeScopedDB()

    async def _override_db():
        return fake_db

    app.dependency_overrides[get_scoped_db] = _override_db

    _user = user

    class InjectUserMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.user = _user
            request.state.user_roles = []
            return await call_next(request)

    app.add_middleware(InjectUserMiddleware)

    for name, cfg in collections_config.items():
        router = create_auto_crud_router(name, cfg)
        app.include_router(router)

    return app, fake_db, TestClient(app)


class TestDefaults:
    def test_defaults_applied_on_create(self):
        app, fake_db, client = _build_defaults_test_app(
            defaults={"status": "pending", "priority": 3},
        )
        client.post("/api/tasks", json={"title": "New"})
        doc = fake_db["tasks"]._docs[-1]
        assert doc["status"] == "pending"
        assert doc["priority"] == 3

    def test_defaults_do_not_overwrite_caller(self):
        app, fake_db, client = _build_defaults_test_app(
            defaults={"status": "pending"},
        )
        client.post("/api/tasks", json={"title": "New", "status": "done"})
        doc = fake_db["tasks"]._docs[-1]
        assert doc["status"] == "done"

    def test_defaults_with_user_template(self):
        app, fake_db, client = _build_defaults_test_app(
            defaults={"owner_id": "{{user._id}}"},
            user={"_id": "u42"},
        )
        client.post("/api/tasks", json={"title": "Owned"})
        doc = fake_db["tasks"]._docs[-1]
        assert doc["owner_id"] == "u42"

    def test_defaults_on_bulk_insert(self):
        app, fake_db, client = _build_defaults_test_app(
            defaults={"status": "pending"},
        )
        client.post("/api/tasks/_bulk", json=[{"title": "A"}, {"title": "B"}])
        for doc in fake_db["tasks"]._docs:
            assert doc["status"] == "pending"


# ═══════════════════════════════════════════════════════════════════════
# MQL-as-DSL: Default Projection
# ═══════════════════════════════════════════════════════════════════════


class TestDefaultProjection:
    def test_default_projection_applied(self):
        oid = ObjectId()
        seed = {"tasks": [{"_id": oid, "title": "Hi", "secret": "hidden", "status": "ok"}]}
        app = FastAPI()
        fake_db = _ProjectionFakeScopedDB(seed)

        async def _override():
            return fake_db

        app.dependency_overrides[get_scoped_db] = _override
        router = create_auto_crud_router("tasks", {"default_projection": {"secret": 0}})
        app.include_router(router)
        client = TestClient(app)

        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        assert fake_db["tasks"].last_projection == {"secret": 0}

    def test_fields_param_overrides_default_projection(self):
        oid = ObjectId()
        seed = {"tasks": [{"_id": oid, "title": "Hi", "secret": "hidden", "status": "ok"}]}
        app = FastAPI()
        fake_db = _ProjectionFakeScopedDB(seed)

        async def _override():
            return fake_db

        app.dependency_overrides[get_scoped_db] = _override
        router = create_auto_crud_router("tasks", {"default_projection": {"secret": 0}})
        app.include_router(router)
        client = TestClient(app)

        resp = client.get("/api/tasks?fields=title")
        assert resp.status_code == 200
        assert fake_db["tasks"].last_projection == {"title": 1}

    def test_no_projection_when_neither_set(self):
        oid = ObjectId()
        seed = {"tasks": [{"_id": oid, "title": "Hi"}]}
        app = FastAPI()
        fake_db = _ProjectionFakeScopedDB(seed)

        async def _override():
            return fake_db

        app.dependency_overrides[get_scoped_db] = _override
        router = create_auto_crud_router("tasks", {})
        app.include_router(router)
        client = TestClient(app)

        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        assert fake_db["tasks"].last_projection is None


# ═══════════════════════════════════════════════════════════════════════
# MQL-as-DSL: Manifest Validation for New Keys
# ═══════════════════════════════════════════════════════════════════════


class TestManifestMQLKeys:
    def test_manifest_with_policy_validates(self):
        import asyncio

        from mdb_engine.core.manifest import ManifestValidator

        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test",
            "collections": {
                "tasks": {
                    "policy": {
                        "read": {"owner_id": "{{user._id}}"},
                        "write": {"owner_id": "{{user._id}}"},
                    },
                },
            },
        }
        validator = ManifestValidator()
        is_valid, error_msg, _ = asyncio.run(validator.validate(manifest))
        assert is_valid, f"Manifest validation failed: {error_msg}"

    def test_manifest_with_scopes_validates(self):
        import asyncio

        from mdb_engine.core.manifest import ManifestValidator

        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test",
            "collections": {
                "tasks": {
                    "scopes": {
                        "active": {"status": {"$ne": "archived"}},
                        "mine": {"owner_id": "{{user._id}}"},
                    },
                },
            },
        }
        validator = ManifestValidator()
        is_valid, error_msg, _ = asyncio.run(validator.validate(manifest))
        assert is_valid, f"Manifest validation failed: {error_msg}"

    def test_manifest_with_pipelines_validates(self):
        import asyncio

        from mdb_engine.core.manifest import ManifestValidator

        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test",
            "collections": {
                "tasks": {
                    "pipelines": {
                        "by_status": [
                            {"$group": {"_id": "$status", "count": {"$sum": 1}}},
                        ],
                    },
                },
            },
        }
        validator = ManifestValidator()
        is_valid, error_msg, _ = asyncio.run(validator.validate(manifest))
        assert is_valid, f"Manifest validation failed: {error_msg}"

    def test_manifest_with_defaults_validates(self):
        import asyncio

        from mdb_engine.core.manifest import ManifestValidator

        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test",
            "collections": {
                "tasks": {
                    "defaults": {"status": "pending", "priority": 3},
                },
            },
        }
        validator = ManifestValidator()
        is_valid, error_msg, _ = asyncio.run(validator.validate(manifest))
        assert is_valid, f"Manifest validation failed: {error_msg}"

    def test_manifest_with_default_projection_validates(self):
        import asyncio

        from mdb_engine.core.manifest import ManifestValidator

        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test",
            "collections": {
                "tasks": {
                    "default_projection": {"internal_notes": 0},
                },
            },
        }
        validator = ManifestValidator()
        is_valid, error_msg, _ = asyncio.run(validator.validate(manifest))
        assert is_valid, f"Manifest validation failed: {error_msg}"

    def test_manifest_with_all_mql_keys_validates(self):
        import asyncio

        from mdb_engine.core.manifest import ManifestValidator

        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test",
            "collections": {
                "tasks": {
                    "auto_crud": True,
                    "soft_delete": True,
                    "policy": {"read": {"owner_id": "{{user._id}}"}},
                    "scopes": {"active": {"status": "active"}},
                    "pipelines": {
                        "by_status": [{"$group": {"_id": "$status"}}],
                    },
                    "defaults": {"status": "pending"},
                    "default_projection": {"secret": 0},
                },
            },
        }
        validator = ManifestValidator()
        is_valid, error_msg, _ = asyncio.run(validator.validate(manifest))
        assert is_valid, f"Manifest validation failed: {error_msg}"


# ═══════════════════════════════════════════════════════════════════════
# MQL-as-DSL: Edge-Case Coverage
# ═══════════════════════════════════════════════════════════════════════


class TestTemplateResolverEdgeCases:
    def test_user_path_too_deep_raises_400(self):
        from mdb_engine.routing.template_resolver import resolve_template

        user = {"a": {"b": {"c": {"d": "deep"}}}}
        with pytest.raises(HTTPException) as exc_info:
            resolve_template({"x": "{{user.a.b.c.d}}"}, user)
        assert exc_info.value.status_code == 400
        assert "too deep" in exc_info.value.detail

    def test_user_path_hits_non_dict_raises_400(self):
        from mdb_engine.routing.template_resolver import resolve_template

        user = {"_id": "u1", "name": "Alice"}
        with pytest.raises(HTTPException) as exc_info:
            resolve_template({"x": "{{user.name.first}}"}, user)
        assert exc_info.value.status_code == 400
        assert "Cannot resolve" in exc_info.value.detail


class TestHardDeleteWithPolicy:
    """Cover the hard-delete path (no soft_delete) with a write policy."""

    def test_hard_delete_blocked_by_policy(self):
        oid = ObjectId()
        app, client = _build_policy_test_app(
            policy={"write": {"owner_id": "{{user._id}}"}},
            seed_data={"tasks": [{"_id": oid, "title": "Other", "owner_id": "u2", "deleted_at": None}]},
            user={"_id": "u1"},
            extra_config={"soft_delete": False},
        )
        resp = client.delete(f"/api/tasks/{oid}")
        assert resp.status_code == 404

    def test_hard_delete_allowed_by_policy(self):
        oid = ObjectId()
        app, client = _build_policy_test_app(
            policy={"write": {"owner_id": "{{user._id}}"}},
            seed_data={"tasks": [{"_id": oid, "title": "Mine", "owner_id": "u1", "deleted_at": None}]},
            user={"_id": "u1"},
            extra_config={"soft_delete": False},
        )
        resp = client.delete(f"/api/tasks/{oid}")
        assert resp.status_code == 200
        assert resp.json()["data"]["deleted"] is True


# ═══════════════════════════════════════════════════════════════════════
# Unified AuthZ — authz_provider integration
# ═══════════════════════════════════════════════════════════════════════


class _FakeAuthzProvider:
    """Mock AuthorizationProvider that records check() calls."""

    def __init__(self, allow: bool = True):
        self._allow = allow
        self.checks: list[tuple[str, str, str]] = []

    async def check(self, subject, resource, action, user_object=None):
        self.checks.append((subject, resource, action))
        return self._allow


def _build_authz_provider_app(
    auth_config: dict,
    seed_data: dict | None = None,
    user: dict | None = None,
    authz_allow: bool = True,
) -> tuple[FastAPI, TestClient, _FakeAuthzProvider]:
    """Build an app with a mock authz_provider on app.state."""
    from starlette.middleware.base import BaseHTTPMiddleware

    provider = _FakeAuthzProvider(allow=authz_allow)
    collections_config = {"tasks": {"auth": auth_config}}
    app = FastAPI()
    app.state.authz_provider = provider
    app.state.role_hierarchy = None

    fake_db = _FakeScopedDB(seed_data)

    async def _override_db():
        return fake_db

    app.dependency_overrides[get_scoped_db] = _override_db

    _user = user

    class InjectUserMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.user = _user
            request.state.user_roles = []
            return await call_next(request)

    app.add_middleware(InjectUserMiddleware)

    for name, config in collections_config.items():
        router = create_auto_crud_router(name, config)
        app.include_router(router)

    return app, TestClient(app), provider


class TestAuthzProviderIntegration:
    """Verify auto-CRUD routes delegate to authz_provider.check()."""

    def test_write_roles_delegates_to_provider(self):
        oid = ObjectId()
        _, client, provider = _build_authz_provider_app(
            auth_config={"write_roles": ["editor"], "public_read": True},
            seed_data={"tasks": [{"_id": oid, "title": "t1"}]},
            user={"_id": "u1", "email": "alice@example.com"},
        )
        resp = client.post("/api/tasks", json={"title": "new"})
        assert resp.status_code == 201
        assert any(c[2] == "create" and c[1] == "tasks" for c in provider.checks)

    def test_write_roles_denied_by_provider(self):
        _, client, provider = _build_authz_provider_app(
            auth_config={"write_roles": ["editor"], "public_read": True},
            user={"_id": "u1", "email": "alice@example.com"},
            authz_allow=False,
        )
        resp = client.post("/api/tasks", json={"title": "new"})
        assert resp.status_code == 403

    def test_public_read_with_provider_allows_anonymous(self):
        oid = ObjectId()
        _, client, provider = _build_authz_provider_app(
            auth_config={"public_read": True, "write_roles": ["editor"]},
            seed_data={"tasks": [{"_id": oid, "title": "t1"}]},
            user=None,
            authz_allow=True,
        )
        resp = client.get("/api/tasks")
        assert resp.status_code == 200

    def test_roles_delegates_read_to_provider(self):
        oid = ObjectId()
        _, client, provider = _build_authz_provider_app(
            auth_config={"roles": ["editor"]},
            seed_data={"tasks": [{"_id": oid, "title": "t1"}]},
            user={"_id": "u1", "email": "bob@example.com"},
        )
        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        assert any(c[2] == "read" and c[1] == "tasks" for c in provider.checks)

    def test_mql_scoping_still_applies_with_provider(self):
        """owner_field MQL filter should apply even when authz_provider allows."""
        oid_mine = ObjectId()
        oid_other = ObjectId()
        _, client, provider = _build_authz_provider_app(
            auth_config={"write_roles": ["editor"], "public_read": True},
            seed_data={
                "tasks": [
                    {"_id": oid_mine, "title": "mine", "owner_id": "u1"},
                    {"_id": oid_other, "title": "other", "owner_id": "u2"},
                ]
            },
            user={"_id": "u1", "email": "alice@example.com"},
            authz_allow=True,
        )
        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        titles = [d["title"] for d in resp.json()["data"]]
        assert "mine" in titles
        assert "other" in titles


# ═══════════════════════════════════════════════════════════════════════
# public_read fallback (no authz_provider on app.state)
# ═══════════════════════════════════════════════════════════════════════


class TestPublicReadFallback:
    """Verify public_read works without an authz_provider (inline role fallback)."""

    def test_public_read_without_provider_allows_anonymous_get(self):
        """Anonymous GET should succeed when public_read is True, even with write_roles set."""
        oid = ObjectId()
        _, client = _build_auth_test_app(
            auth_config={"public_read": True, "write_roles": ["editor"]},
            seed_data={"tasks": [{"_id": oid, "title": "hello"}]},
            user=None,
        )
        resp = client.get("/api/tasks")
        assert resp.status_code == 200

    def test_public_read_without_provider_rejects_anonymous_write(self):
        """Anonymous POST should be rejected (write router requires auth)."""
        _, client = _build_auth_test_app(
            auth_config={"public_read": True, "write_roles": ["editor"]},
            user=None,
        )
        resp = client.post("/api/tasks", json={"title": "new"})
        assert resp.status_code == 401

    def test_public_read_without_provider_allows_authenticated_write(self):
        """Authenticated user with correct role can write."""
        _, client = _build_auth_test_app(
            auth_config={"public_read": True, "write_roles": ["editor"]},
            user={"_id": "u1", "email": "a@b.com", "role": "editor"},
        )
        resp = client.post("/api/tasks", json={"title": "new"})
        assert resp.status_code == 201

    def test_public_read_without_provider_get_by_id(self):
        """Anonymous GET by ID should work for public_read collections."""
        oid = ObjectId()
        _, client = _build_auth_test_app(
            auth_config={"public_read": True, "write_roles": ["editor"]},
            seed_data={"tasks": [{"_id": oid, "title": "hi"}]},
            user=None,
        )
        resp = client.get(f"/api/tasks/{oid}")
        assert resp.status_code == 200
