"""
Security tests for ScopedCollectionWrapper.database property.

These tests verify that accessing collections through collection.database
maintains proper scoping and data isolation.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from motor.motor_asyncio import AsyncIOMotorCollection

from mdb_engine.database.scoped_wrapper import (
    ScopedCollectionWrapper,
    ScopedMongoWrapper,
)


@pytest.mark.unit
class TestScopedCollectionWrapperDatabaseSecurity:
    """Test security of database property access."""

    @pytest.mark.asyncio
    async def test_database_returns_scoped_mongo_wrapper(self, mock_mongo_collection, mock_mongo_database):
        """Test that collection.database returns a ScopedMongoWrapper."""
        # Set up database attribute on collection
        mock_mongo_collection.database = mock_mongo_database

        parent_wrapper = ScopedMongoWrapper(
            real_db=mock_mongo_database,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        collection_wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
            parent_wrapper=parent_wrapper,
        )

        db = collection_wrapper.database
        assert isinstance(db, ScopedMongoWrapper)
        assert db is parent_wrapper  # Should return the same parent wrapper

    @pytest.mark.asyncio
    async def test_database_collection_access_returns_scoped_wrapper(self, mock_mongo_collection, mock_mongo_database):
        """Test that collection.database[other_collection] returns ScopedCollectionWrapper."""
        # Set up database attribute on collection
        mock_mongo_collection.database = mock_mongo_database

        # Create a mock for another collection
        other_collection = MagicMock(spec=AsyncIOMotorCollection)
        other_collection.name = "test_app_other_collection"
        other_collection.database = mock_mongo_database
        other_collection.insert_one = AsyncMock()
        other_collection.find_one = AsyncMock(return_value=None)

        # Set up the database mock to return the other collection
        mock_db = mock_mongo_database
        mock_db.test_app_other_collection = other_collection

        parent_wrapper = ScopedMongoWrapper(
            real_db=mock_db,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        collection_wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
            parent_wrapper=parent_wrapper,
        )

        # Access another collection through database
        other_wrapper = collection_wrapper.database["other_collection"]

        # Should return a ScopedCollectionWrapper, not raw collection
        assert isinstance(other_wrapper, ScopedCollectionWrapper)
        assert other_wrapper._write_scope == "test_app"
        assert other_wrapper._read_scopes == ["test_app"]

    @pytest.mark.asyncio
    async def test_database_collection_access_maintains_scoping_on_write(
        self, mock_mongo_collection, mock_mongo_database
    ):
        """Test that write operations through collection.database[other_collection] add app_id."""
        # Set up database attribute on collection
        mock_mongo_collection.database = mock_mongo_database

        # Create a mock for another collection
        other_collection = MagicMock(spec=AsyncIOMotorCollection)
        other_collection.name = "test_app_other_collection"
        other_collection.database = mock_mongo_database
        other_collection.insert_one = AsyncMock()

        mock_db = mock_mongo_database
        mock_db.test_app_other_collection = other_collection

        parent_wrapper = ScopedMongoWrapper(
            real_db=mock_db,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        collection_wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
            parent_wrapper=parent_wrapper,
        )

        # Access another collection and insert a document
        other_wrapper = collection_wrapper.database["other_collection"]
        await other_wrapper.insert_one({"name": "Test", "value": 100})

        # Verify insert_one was called with app_id added
        call_args = other_collection.insert_one.call_args
        assert call_args is not None
        inserted_doc = call_args[0][0]
        assert inserted_doc["app_id"] == "test_app"
        assert inserted_doc["name"] == "Test"
        assert inserted_doc["value"] == 100

    @pytest.mark.asyncio
    async def test_database_collection_access_maintains_scoping_on_read(
        self, mock_mongo_collection, mock_mongo_database
    ):
        """Test that read operations through collection.database[other_collection] filter by app_id."""
        # Set up database attribute on collection
        mock_mongo_collection.database = mock_mongo_database

        # Create a mock for another collection
        other_collection = MagicMock(spec=AsyncIOMotorCollection)
        other_collection.name = "test_app_other_collection"
        other_collection.database = mock_mongo_database
        other_collection.find_one = AsyncMock(return_value=None)

        mock_db = mock_mongo_database
        mock_db.test_app_other_collection = other_collection

        parent_wrapper = ScopedMongoWrapper(
            real_db=mock_db,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        collection_wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
            parent_wrapper=parent_wrapper,
        )

        # Access another collection and query
        other_wrapper = collection_wrapper.database["other_collection"]
        await other_wrapper.find_one({"name": "Test"})

        # Verify find_one was called with scoped filter
        call_args = other_collection.find_one.call_args
        assert call_args is not None
        scoped_filter = call_args[0][0]

        # Should have $and with user filter and app_id scope
        assert "$and" in scoped_filter
        and_conditions = scoped_filter["$and"]
        assert len(and_conditions) == 2
        assert {"name": "Test"} in and_conditions
        assert {"app_id": {"$in": ["test_app"]}} in and_conditions

    @pytest.mark.asyncio
    async def test_db_alias_returns_same_scoped_wrapper(self, mock_mongo_collection, mock_mongo_database):
        """Test that collection.db alias returns the same ScopedMongoWrapper."""
        # Set up database attribute on collection
        mock_mongo_collection.database = mock_mongo_database

        parent_wrapper = ScopedMongoWrapper(
            real_db=mock_mongo_database,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        collection_wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
            parent_wrapper=parent_wrapper,
        )

        db1 = collection_wrapper.database
        db2 = collection_wrapper.db

        # Should return the same instance
        assert db1 is db2
        assert isinstance(db1, ScopedMongoWrapper)
        assert isinstance(db2, ScopedMongoWrapper)

    @pytest.mark.asyncio
    async def test_database_caching_when_parent_wrapper_exists(self, mock_mongo_collection, mock_mongo_database):
        """Test that multiple accesses to collection.database return the same parent wrapper."""
        # Set up database attribute on collection
        mock_mongo_collection.database = mock_mongo_database

        parent_wrapper = ScopedMongoWrapper(
            real_db=mock_mongo_database,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        collection_wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
            parent_wrapper=parent_wrapper,
        )

        db1 = collection_wrapper.database
        db2 = collection_wrapper.database

        # Should return the same parent wrapper instance
        assert db1 is db2
        assert db1 is parent_wrapper

    @pytest.mark.asyncio
    async def test_database_creates_scoped_wrapper_when_no_parent(self, mock_mongo_collection, mock_mongo_database):
        """Test that database property creates ScopedMongoWrapper when parent_wrapper is None."""
        # Set up database attribute on collection
        mock_mongo_collection.database = mock_mongo_database
        collection_wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
            parent_wrapper=None,  # No parent wrapper
        )

        db = collection_wrapper.database

        # Should create a new ScopedMongoWrapper
        assert isinstance(db, ScopedMongoWrapper)
        assert db._read_scopes == ["test_app"]
        assert db._write_scope == "test_app"

    @pytest.mark.asyncio
    async def test_database_caching_when_no_parent_wrapper(self, mock_mongo_collection, mock_mongo_database):
        """Test that database property caches ScopedMongoWrapper when parent_wrapper is None."""
        # Set up database attribute on collection
        mock_mongo_collection.database = mock_mongo_database
        collection_wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
            parent_wrapper=None,
        )

        db1 = collection_wrapper.database
        db2 = collection_wrapper.database

        # Should return the same cached instance
        assert db1 is db2
        assert isinstance(db1, ScopedMongoWrapper)

    @pytest.mark.asyncio
    async def test_database_cross_app_access_respects_read_scopes(self, mock_mongo_collection, mock_mongo_database):
        """Test that cross-app access through collection.database respects read_scopes."""
        # Set up database attribute on collection
        mock_mongo_collection.database = mock_mongo_database

        # Create a mock for another app's collection
        other_app_collection = MagicMock(spec=AsyncIOMotorCollection)
        other_app_collection.name = "other_app_shared_collection"
        other_app_collection.database = mock_mongo_database
        other_app_collection.find_one = AsyncMock(return_value=None)

        mock_db = mock_mongo_database
        mock_db.other_app_shared_collection = other_app_collection

        # Create wrapper with cross-app read access
        parent_wrapper = ScopedMongoWrapper(
            real_db=mock_db,
            read_scopes=["test_app", "other_app"],  # Can read from other_app
            write_scope="test_app",
        )

        collection_wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app", "other_app"],
            write_scope="test_app",
            parent_wrapper=parent_wrapper,
        )

        # Access other app's collection through database
        other_wrapper = collection_wrapper.database.get_collection("other_app_shared_collection")

        # Should return a ScopedCollectionWrapper with cross-app read scopes
        assert isinstance(other_wrapper, ScopedCollectionWrapper)
        assert "other_app" in other_wrapper._read_scopes
        assert other_wrapper._write_scope == "test_app"  # Write scope stays the same

    @pytest.mark.asyncio
    async def test_database_no_bypass_to_raw_collection(self, mock_mongo_collection, mock_mongo_database):
        """Test that collection.database does not allow bypassing to raw unscoped collections."""
        # Set up database attribute on collection
        mock_mongo_collection.database = mock_mongo_database

        parent_wrapper = ScopedMongoWrapper(
            real_db=mock_mongo_database,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        collection_wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
            parent_wrapper=parent_wrapper,
        )

        db = collection_wrapper.database

        # Accessing a collection should return ScopedCollectionWrapper, not raw collection
        other_collection = MagicMock(spec=AsyncIOMotorCollection)
        other_collection.name = "test_app_other"
        other_collection.database = mock_mongo_database
        mock_mongo_database.test_app_other = other_collection

        other_wrapper = db["other"]

        # Should be scoped, not raw
        assert isinstance(other_wrapper, ScopedCollectionWrapper)
        assert not isinstance(other_wrapper, AsyncIOMotorCollection)

    @pytest.mark.asyncio
    async def test_database_attribute_access_returns_scoped_collection(
        self, mock_mongo_collection, mock_mongo_database
    ):
        """Test that collection.database.other_collection returns ScopedCollectionWrapper."""
        # Set up database attribute on collection
        mock_mongo_collection.database = mock_mongo_database

        other_collection = MagicMock(spec=AsyncIOMotorCollection)
        other_collection.name = "test_app_other_collection"
        other_collection.database = mock_mongo_database
        other_collection.insert_one = AsyncMock()

        mock_db = mock_mongo_database
        mock_db.test_app_other_collection = other_collection

        parent_wrapper = ScopedMongoWrapper(
            real_db=mock_db,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        collection_wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
            parent_wrapper=parent_wrapper,
        )

        # Access via attribute
        other_wrapper = collection_wrapper.database.other_collection

        # Should return ScopedCollectionWrapper
        assert isinstance(other_wrapper, ScopedCollectionWrapper)
        assert other_wrapper._write_scope == "test_app"

    @pytest.mark.asyncio
    async def test_database_preserves_query_validator_and_resource_limiter(
        self, mock_mongo_collection, mock_mongo_database
    ):
        """Test that database property preserves query_validator and resource_limiter."""
        # Set up database attribute on collection
        mock_mongo_collection.database = mock_mongo_database

        from mdb_engine.database.query_validator import QueryValidator
        from mdb_engine.database.resource_limiter import ResourceLimiter

        custom_validator = QueryValidator()
        custom_limiter = ResourceLimiter()

        parent_wrapper = ScopedMongoWrapper(
            real_db=mock_mongo_database,
            read_scopes=["test_app"],
            write_scope="test_app",
            query_validator=custom_validator,
            resource_limiter=custom_limiter,
        )

        collection_wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
            query_validator=custom_validator,
            resource_limiter=custom_limiter,
            parent_wrapper=parent_wrapper,
        )

        db = collection_wrapper.database

        # Should preserve validators and limiters
        assert db._query_validator is custom_validator
        assert db._resource_limiter is custom_limiter

        # Collections accessed through db should also have these
        other_collection = MagicMock(spec=AsyncIOMotorCollection)
        other_collection.name = "test_app_other"
        mock_mongo_database.test_app_other = other_collection

        other_wrapper = db["other"]
        assert other_wrapper._query_validator is custom_validator
        assert other_wrapper._resource_limiter is custom_limiter
