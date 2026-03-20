"""Shared fixtures for security tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase

from mdb_engine.database.scoped_wrapper import (
    ScopedCollectionWrapper,
    ScopedMongoWrapper,
)


@pytest.fixture
def attacker_user() -> dict[str, Any]:
    """A user dict with adversarial field values."""
    return {
        "_id": "attacker_123",
        "email": "evil@example.com",
        "role": "user",
        "roles": ["user"],
        "team_id": "team_evil",
        "profile": {
            "org": "evil_corp",
            "nested_template": "{{user._id}}",
            "nested_env": "{{env.SECRET_KEY}}",
        },
        "operator_field": {"$ne": None},
    }


@pytest.fixture
def normal_user() -> dict[str, Any]:
    """A standard authenticated user."""
    return {
        "_id": "user_456",
        "email": "user@example.com",
        "role": "editor",
        "roles": ["editor"],
        "team_id": "team_good",
        "profile": {"org": "good_corp"},
    }


@pytest.fixture
def admin_user() -> dict[str, Any]:
    """An admin user."""
    return {
        "_id": "admin_789",
        "email": "admin@example.com",
        "role": "admin",
        "roles": ["admin"],
        "team_id": "team_good",
    }


@pytest.fixture
def mock_raw_collection() -> MagicMock:
    """A raw Motor collection mock for scoped wrapper tests."""
    collection = MagicMock(spec=AsyncIOMotorCollection)
    collection.name = "tenant_a_items"
    collection.find = MagicMock(return_value=MagicMock(to_list=AsyncMock(return_value=[])))
    collection.find_one = AsyncMock(return_value=None)
    collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id="new_id"))
    collection.insert_many = AsyncMock(return_value=MagicMock(inserted_ids=["id1", "id2"]))
    collection.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    collection.update_many = AsyncMock(return_value=MagicMock(modified_count=2))
    collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
    collection.delete_many = AsyncMock(return_value=MagicMock(deleted_count=2))
    collection.count_documents = AsyncMock(return_value=5)
    collection.aggregate = MagicMock(return_value=MagicMock(to_list=AsyncMock(return_value=[])))
    collection.bulk_write = AsyncMock()
    collection.replace_one = AsyncMock()
    collection.find_one_and_replace = AsyncMock()
    collection.find_one_and_update = AsyncMock()
    collection.find_one_and_delete = AsyncMock()
    collection.rename = AsyncMock()
    collection.drop = AsyncMock()
    collection.list_indexes = AsyncMock(return_value=AsyncMock(to_list=AsyncMock(return_value=[])))
    collection.create_index = AsyncMock(return_value="idx")
    return collection


@pytest.fixture
def scoped_wrapper(mock_raw_collection: MagicMock) -> ScopedCollectionWrapper:
    """A ScopedCollectionWrapper configured for tenant_a."""
    return ScopedCollectionWrapper(
        real_collection=mock_raw_collection,
        read_scopes=["tenant_a"],
        write_scope="tenant_a",
    )


@pytest.fixture
def mock_raw_database() -> MagicMock:
    """A raw Motor database mock."""
    db = MagicMock(spec=AsyncIOMotorDatabase)
    db.name = "test_db"

    def get_collection(name: str) -> MagicMock:
        col = MagicMock(spec=AsyncIOMotorCollection)
        col.name = name
        col.database = db
        col.find = MagicMock(return_value=MagicMock(to_list=AsyncMock(return_value=[])))
        col.find_one = AsyncMock(return_value=None)
        col.insert_one = AsyncMock(return_value=MagicMock(inserted_id="id"))
        col.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
        col.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
        col.count_documents = AsyncMock(return_value=0)
        col.aggregate = MagicMock(return_value=MagicMock(to_list=AsyncMock(return_value=[])))
        return col

    db.__getitem__ = lambda self, name: get_collection(name)
    db.__getattr__ = lambda self, name: get_collection(name)
    return db


@pytest.fixture
def scoped_db(mock_raw_database: MagicMock) -> ScopedMongoWrapper:
    """A ScopedMongoWrapper configured for tenant_a."""
    return ScopedMongoWrapper(
        real_db=mock_raw_database,
        read_scopes=["tenant_a"],
        write_scope="tenant_a",
    )
