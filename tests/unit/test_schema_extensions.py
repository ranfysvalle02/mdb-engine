"""
Tests for engine-specific schema validation extensions.

Covers:
- x-values-from: validate field values against a lookup collection
- x-references: validate foreign key references (A.7)
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from mdb_engine.routing._validators import validate_schema_extensions

# ── Helpers ──────────────────────────────────────────────────────────────


class FakeCursor:
    def __init__(self, docs: list[dict]):
        self._docs = docs

    def __await__(self):
        async def _noop():
            return self

        return _noop().__await__()

    async def to_list(self, length=None):
        return self._docs


class FakeCollection:
    def __init__(self, docs: list[dict] | None = None):
        self._docs = docs or []

    def find(self, filter_=None, projection=None):
        return FakeCursor(self._docs)

    async def find_one(self, filter_=None):
        for doc in self._docs:
            for k, v in (filter_ or {}).items():
                if doc.get(k) != v:
                    break
            else:
                return doc
        return None


class FakeDB:
    def __init__(self, collections: dict[str, FakeCollection] | None = None):
        self._cols = collections or {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self._cols.get(name, FakeCollection())


# ═════════════════════════════════════════════════════════════════════════
# x-values-from
# ═════════════════════════════════════════════════════════════════════════


class TestValuesFrom:
    @pytest.fixture()
    def db(self) -> FakeDB:
        return FakeDB(
            {
                "categories": FakeCollection(
                    [
                        {"name": "python"},
                        {"name": "javascript"},
                        {"name": "rust"},
                    ]
                ),
            }
        )

    @pytest.fixture()
    def schema(self) -> dict:
        return {
            "properties": {
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "x-values-from": {"collection": "categories", "field": "name"},
                },
            },
        }

    @pytest.mark.asyncio
    async def test_valid_values_accepted(self, db, schema):
        body = {"tags": ["python", "rust"]}
        await validate_schema_extensions(body, schema, db)

    @pytest.mark.asyncio
    async def test_invalid_value_rejected(self, db, schema):
        body = {"tags": ["python", "cobol"]}
        with pytest.raises(HTTPException) as exc_info:
            await validate_schema_extensions(body, schema, db)
        assert exc_info.value.status_code == 422
        assert "cobol" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_scalar_value_checked(self, db):
        schema = {
            "properties": {
                "category": {
                    "type": "string",
                    "x-values-from": {"collection": "categories", "field": "name"},
                },
            },
        }
        body = {"category": "python"}
        await validate_schema_extensions(body, schema, db)

    @pytest.mark.asyncio
    async def test_field_not_in_body_skipped(self, db, schema):
        body = {"title": "No tags here"}
        await validate_schema_extensions(body, schema, db)

    @pytest.mark.asyncio
    async def test_no_schema_is_noop(self, db):
        await validate_schema_extensions({"tags": ["anything"]}, None, db)

    @pytest.mark.asyncio
    async def test_empty_properties_is_noop(self, db):
        await validate_schema_extensions({"tags": ["anything"]}, {"properties": {}}, db)


# ═════════════════════════════════════════════════════════════════════════
# x-references (A.7)
# ═════════════════════════════════════════════════════════════════════════


class TestReferences:
    @pytest.fixture()
    def db(self) -> FakeDB:
        from bson import ObjectId

        return FakeDB(
            {
                "posts": FakeCollection(
                    [
                        {"_id": ObjectId("507f1f77bcf86cd799439011"), "title": "Hello"},
                    ]
                ),
            }
        )

    @pytest.fixture()
    def schema(self) -> dict:
        return {
            "properties": {
                "post_id": {
                    "type": "string",
                    "x-references": {"collection": "posts", "field": "_id"},
                },
            },
        }

    @pytest.mark.asyncio
    async def test_valid_reference_accepted(self, db, schema):
        body = {"post_id": "507f1f77bcf86cd799439011"}
        await validate_schema_extensions(body, schema, db)

    @pytest.mark.asyncio
    async def test_invalid_reference_rejected(self, db, schema):
        body = {"post_id": "000000000000000000000000"}
        with pytest.raises(HTTPException) as exc_info:
            await validate_schema_extensions(body, schema, db)
        assert exc_info.value.status_code == 422
        assert "not found" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_reference_skipped_if_field_absent(self, db, schema):
        body = {"title": "No post_id here"}
        await validate_schema_extensions(body, schema, db)

    @pytest.mark.asyncio
    async def test_non_id_field_reference(self):
        db = FakeDB(
            {
                "users": FakeCollection(
                    [
                        {"email": "alice@test.com"},
                    ]
                ),
            }
        )
        schema = {
            "properties": {
                "author_email": {
                    "type": "string",
                    "x-references": {"collection": "users", "field": "email"},
                },
            },
        }
        body = {"author_email": "alice@test.com"}
        await validate_schema_extensions(body, schema, db)

    @pytest.mark.asyncio
    async def test_non_id_field_reference_invalid(self):
        db = FakeDB(
            {
                "users": FakeCollection(
                    [
                        {"email": "alice@test.com"},
                    ]
                ),
            }
        )
        schema = {
            "properties": {
                "author_email": {
                    "type": "string",
                    "x-references": {"collection": "users", "field": "email"},
                },
            },
        }
        body = {"author_email": "nobody@test.com"}
        with pytest.raises(HTTPException) as exc_info:
            await validate_schema_extensions(body, schema, db)
        assert exc_info.value.status_code == 422
