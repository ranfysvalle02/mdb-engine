"""
Integration tests for index management with real MongoDB.

Tests index creation, verification, and idempotency.
"""

import pytest


@pytest.mark.integration
@pytest.mark.asyncio
class TestIndexManagement:
    """Integration tests for index management."""

    async def test_regular_index_creation(self, real_mongodb_engine):
        """Test creation of regular indexes."""
        engine = real_mongodb_engine

        manifest = {
            "schema_version": "2.0",
            "slug": "index_test",
            "name": "Index Test",
            "status": "active",
            "developer_id": "dev@example.com",
            "managed_indexes": {
                "test_collection": [
                    {
                        "name": "test_index",
                        "type": "regular",
                        "keys": [("field1", 1), ("field2", -1)],
                    }
                ]
            },
        }

        await engine.register_app(manifest, create_indexes=True)

        # Verify index was created
        scoped_db = engine.get_scoped_db("index_test")
        collection = scoped_db.test_collection

        # Get the real collection to check indexes
        real_collection = collection._collection

        # Wait a moment for indexes to be ready (MongoDB indexes are created asynchronously)
        import asyncio

        await asyncio.sleep(0.5)

        indexes = await real_collection.list_indexes().to_list(length=100)
        index_names = [idx["name"] for idx in indexes]

        # Debug: print all index names to see what's actually there
        print(f"DEBUG: Collection '{real_collection.name}' has indexes: {index_names}")

        # Index names are prefixed with slug: "index_test_test_index"
        prefixed_index_name = "index_test_test_index"
        assert (
            prefixed_index_name in index_names
        ), f"Index '{prefixed_index_name}' not found. Available indexes: {index_names}"

        # Verify index keys
        test_index = next(idx for idx in indexes if idx["name"] == prefixed_index_name)
        assert test_index is not None
        # Index key structure may vary, but should contain our fields
        key_list = list(test_index["key"].items())
        assert ("field1", 1) in key_list or ("field1", 1.0) in key_list

    async def test_index_idempotency(self, real_mongodb_engine):
        """Test that index creation is idempotent."""
        engine = real_mongodb_engine

        manifest = {
            "schema_version": "2.0",
            "slug": "idempotent_index_test",
            "name": "Idempotent Index Test",
            "status": "active",
            "developer_id": "dev@example.com",
            "managed_indexes": {
                "test_collection": [
                    {
                        "name": "idempotent_index",
                        "type": "regular",
                        "keys": [("field1", 1)],
                    }
                ]
            },
        }

        # First registration
        await engine.register_app(manifest, create_indexes=True)

        scoped_db = engine.get_scoped_db("idempotent_index_test")
        collection = scoped_db.test_collection
        real_collection = collection._collection

        indexes_after_first = await real_collection.list_indexes().to_list(length=100)
        index_names_first = [idx["name"] for idx in indexes_after_first]
        prefixed_index_name = "idempotent_index_test_idempotent_index"
        assert prefixed_index_name in index_names_first

        # Second registration - should not fail or create duplicate
        await engine.register_app(manifest, create_indexes=True)

        indexes_after_second = await real_collection.list_indexes().to_list(length=100)
        index_names_second = [idx["name"] for idx in indexes_after_second]

        # Should still have exactly one index with this name
        assert index_names_second.count(prefixed_index_name) == 1

    async def test_multiple_indexes_same_collection(self, real_mongodb_engine):
        """Test creating multiple indexes on the same collection."""
        engine = real_mongodb_engine

        manifest = {
            "schema_version": "2.0",
            "slug": "multi_index_test",
            "name": "Multi Index Test",
            "status": "active",
            "developer_id": "dev@example.com",
            "managed_indexes": {
                "test_collection": [
                    {"name": "index1", "type": "regular", "keys": [("field1", 1)]},
                    {"name": "index2", "type": "regular", "keys": [("field2", -1)]},
                    {
                        "name": "index3",
                        "type": "regular",
                        "keys": [("field3", 1), ("field4", -1)],
                    },
                ]
            },
        }

        await engine.register_app(manifest, create_indexes=True)

        scoped_db = engine.get_scoped_db("multi_index_test")
        collection = scoped_db.test_collection
        real_collection = collection._collection

        indexes = await real_collection.list_indexes().to_list(length=100)
        index_names = [idx["name"] for idx in indexes]

        assert "multi_index_test_index1" in index_names
        assert "multi_index_test_index2" in index_names
        assert "multi_index_test_index3" in index_names

    async def test_index_on_multiple_collections(self, real_mongodb_engine):
        """Test creating indexes on multiple collections."""
        engine = real_mongodb_engine

        manifest = {
            "schema_version": "2.0",
            "slug": "multi_collection_index_test",
            "name": "Multi Collection Index Test",
            "status": "active",
            "developer_id": "dev@example.com",
            "managed_indexes": {
                "collection1": [
                    {"name": "coll1_index", "type": "regular", "keys": [("field1", 1)]}
                ],
                "collection2": [
                    {"name": "coll2_index", "type": "regular", "keys": [("field2", 1)]}
                ],
            },
        }

        await engine.register_app(manifest, create_indexes=True)

        scoped_db = engine.get_scoped_db("multi_collection_index_test")

        # Check collection1 indexes
        coll1 = scoped_db.collection1
        real_coll1 = coll1._collection
        indexes1 = await real_coll1.list_indexes().to_list(length=100)
        index_names1 = [idx["name"] for idx in indexes1]
        assert "multi_collection_index_test_coll1_index" in index_names1

        # Check collection2 indexes
        coll2 = scoped_db.collection2
        real_coll2 = coll2._collection
        indexes2 = await real_coll2.list_indexes().to_list(length=100)
        index_names2 = [idx["name"] for idx in indexes2]
        assert "multi_collection_index_test_coll2_index" in index_names2

    async def test_index_creation_with_existing_data(self, real_mongodb_engine):
        """Test that indexes can be created on collections with existing data."""
        engine = real_mongodb_engine

        # First register app without indexes and insert data
        manifest_no_index = {
            "schema_version": "2.0",
            "slug": "existing_data_test",
            "name": "Existing Data Test",
            "status": "active",
            "developer_id": "dev@example.com",
        }

        await engine.register_app(manifest_no_index, create_indexes=False)

        scoped_db = engine.get_scoped_db("existing_data_test")
        collection = scoped_db.test_collection

        # Insert some data
        await collection.insert_one({"field1": "value1", "field2": "value2"})
        await collection.insert_one({"field1": "value3", "field2": "value4"})

        # Now register with indexes
        manifest_with_index = {
            "schema_version": "2.0",
            "slug": "existing_data_test",
            "name": "Existing Data Test",
            "status": "active",
            "developer_id": "dev@example.com",
            "managed_indexes": {
                "test_collection": [
                    {
                        "name": "existing_data_index",
                        "type": "regular",
                        "keys": [("field1", 1)],
                    }
                ]
            },
        }

        await engine.register_app(manifest_with_index, create_indexes=True)

        # Verify index was created despite existing data
        real_collection = collection._collection
        indexes = await real_collection.list_indexes().to_list(length=100)
        index_names = [idx["name"] for idx in indexes]
        assert "existing_data_test_existing_data_index" in index_names
