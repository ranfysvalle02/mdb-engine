"""Integration tests for MongoDBEngine with real MongoDB.

These tests require a running MongoDB instance (via Docker/testcontainers).
"""

import pytest

from mdb_engine.exceptions import QueryValidationError, ResourceLimitExceeded


@pytest.mark.integration
@pytest.mark.asyncio
class TestMongoDBEngineIntegration:
    """Integration tests for MongoDBEngine with real MongoDB."""

    async def test_engine_initialization_real_mongodb(self, real_mongodb_engine):
        """Test engine initialization with real MongoDB."""
        engine = real_mongodb_engine

        assert engine._initialized is True
        assert engine.mongo_client is not None

    async def test_engine_shutdown(self, real_mongodb_engine):
        """Test engine shutdown with real MongoDB."""
        engine = real_mongodb_engine

        assert engine._initialized is True
        await engine.shutdown()
        assert engine._initialized is False

    async def test_register_app_with_real_db(self, real_mongodb_engine, sample_manifest):
        """Test app registration with real MongoDB."""
        engine = real_mongodb_engine

        result = await engine.register_app(sample_manifest, create_indexes=False)

        assert result is True
        assert sample_manifest["slug"] in engine._apps

        # Verify app was stored in MongoDB (using internal access for system collection)
        apps_collection = engine._connection_manager.mongo_db["apps_config"]
        stored_app = await apps_collection.find_one({"slug": sample_manifest["slug"]})
        assert stored_app is not None
        assert stored_app["slug"] == sample_manifest["slug"]

    async def test_get_app_after_registration(self, real_mongodb_engine, sample_manifest):
        """Test getting registered app from engine."""
        engine = real_mongodb_engine

        await engine.register_app(sample_manifest, create_indexes=False)

        app = engine.get_app(sample_manifest["slug"])
        assert app is not None
        assert app["slug"] == sample_manifest["slug"]
        assert app["name"] == sample_manifest["name"]

    async def test_list_apps(self, real_mongodb_engine, sample_manifest):
        """Test listing all registered apps."""
        engine = real_mongodb_engine

        # Initially empty
        apps = engine.list_apps()
        assert len(apps) == 0

        # Register app
        await engine.register_app(sample_manifest, create_indexes=False)

        # Should have one app
        apps = engine.list_apps()
        assert len(apps) == 1
        assert sample_manifest["slug"] in apps

    async def test_register_multiple_apps(self, real_mongodb_engine):
        """Test registering multiple apps."""
        engine = real_mongodb_engine

        app1_manifest = {
            "schema_version": "2.0",
            "slug": "app1",
            "name": "App 1",
            "status": "active",
            "developer_id": "dev@example.com",
        }

        app2_manifest = {
            "schema_version": "2.0",
            "slug": "app2",
            "name": "App 2",
            "status": "active",
            "developer_id": "dev@example.com",
        }

        result1 = await engine.register_app(app1_manifest, create_indexes=False)
        result2 = await engine.register_app(app2_manifest, create_indexes=False)

        assert result1 is True
        assert result2 is True

        apps = engine.list_apps()
        assert len(apps) == 2
        assert "app1" in apps
        assert "app2" in apps

    async def test_get_scoped_db_real_mongodb(self, real_mongodb_engine):
        """Test getting scoped database with real MongoDB."""
        engine = real_mongodb_engine

        scoped_db = engine.get_scoped_db("test_app")

        assert scoped_db is not None
        assert scoped_db._read_scopes == ["test_app"]
        assert scoped_db._write_scope == "test_app"

    async def test_engine_context_manager(self, mongodb_connection_string):
        """Test engine as async context manager with real MongoDB."""
        from mdb_engine.core.engine import MongoDBEngine

        mongo_uri = mongodb_connection_string

        async with MongoDBEngine(mongo_uri=mongo_uri, db_name="test_context_db") as engine:
            assert engine._initialized is True

        # After context exit, should be shut down
        assert engine._initialized is False

    async def test_engine_reinitialization(self, real_mongodb_engine):
        """Test that engine can be reinitialized after shutdown."""
        engine = real_mongodb_engine

        # Shutdown
        await engine.shutdown()
        assert engine._initialized is False

        # Reinitialize
        await engine.initialize()
        assert engine._initialized is True

    async def test_engine_security_features_integration(self, real_mongodb_engine):
        """Test that security features work end-to-end through engine."""
        engine = real_mongodb_engine

        db = engine.get_scoped_db("test_app")

        # Verify validators and limiters are present
        assert hasattr(db, "_query_validator")
        assert hasattr(db, "_resource_limiter")

        # Test that dangerous operator is blocked
        with pytest.raises(QueryValidationError, match="Dangerous operator"):
            db.test_collection.find({"$where": "true"})

        # Test that document size validation works
        large_doc = {"data": "x" * (20 * 1024 * 1024)}  # 20MB
        with pytest.raises(ResourceLimitExceeded, match="exceeds maximum"):
            await db.test_collection.insert_one(large_doc)
