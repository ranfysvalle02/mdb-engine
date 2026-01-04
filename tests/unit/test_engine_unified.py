"""
Tests for unified MongoDBEngine with FastAPI integration and Ray support.

Tests the functionality merged from AppFramework:
- FastAPI integration via create_app()
- Lifespan management
- Auto app token retrieval
- Optional Ray support
"""

import os

import pytest


class TestMongoDBEngineImports:
    """Test that MongoDBEngine can be imported correctly."""

    def test_import_from_core(self):
        """Test importing MongoDBEngine from core module."""
        from mdb_engine.core import MongoDBEngine

        assert MongoDBEngine is not None

    def test_import_from_main(self):
        """Test importing MongoDBEngine from main module."""
        from mdb_engine import MongoDBEngine

        assert MongoDBEngine is not None

    def test_ray_available_import(self):
        """Test RAY_AVAILABLE can be imported."""
        from mdb_engine import RAY_AVAILABLE

        # RAY_AVAILABLE is a boolean
        assert isinstance(RAY_AVAILABLE, bool)


class TestMongoDBEngineInstantiation:
    """Test MongoDBEngine instantiation with various configurations."""

    def test_instantiation_basic(self):
        """Test basic instantiation."""
        from mdb_engine import MongoDBEngine

        engine = MongoDBEngine(
            mongo_uri="mongodb://localhost:27017",
            db_name="test_db",
        )

        assert engine.mongo_uri == "mongodb://localhost:27017"
        assert engine.db_name == "test_db"
        assert engine.enable_ray is False
        assert engine._initialized is False

    def test_instantiation_with_ray(self):
        """Test instantiation with Ray enabled."""
        from mdb_engine import MongoDBEngine

        engine = MongoDBEngine(
            mongo_uri="mongodb://localhost:27017",
            db_name="test_db",
            enable_ray=True,
            ray_namespace="test_namespace",
        )

        assert engine.enable_ray is True
        assert engine.ray_namespace == "test_namespace"

    def test_instantiation_with_pool_config(self):
        """Test instantiation with pool configuration."""
        from mdb_engine import MongoDBEngine

        engine = MongoDBEngine(
            mongo_uri="mongodb://localhost:27017",
            db_name="test_db",
            max_pool_size=200,
            min_pool_size=20,
        )

        assert engine.max_pool_size == 200
        assert engine.min_pool_size == 20


class TestMongoDBEngineProperties:
    """Test MongoDBEngine properties and state."""

    def test_has_ray_false_before_init(self):
        """Test has_ray is False before initialization."""
        from mdb_engine import MongoDBEngine

        engine = MongoDBEngine(
            mongo_uri="mongodb://localhost:27017",
            db_name="test_db",
            enable_ray=True,
        )

        assert engine.has_ray is False

    def test_initialized_false_before_init(self):
        """Test _initialized is False before initialization."""
        from mdb_engine import MongoDBEngine

        engine = MongoDBEngine(
            mongo_uri="mongodb://localhost:27017",
            db_name="test_db",
        )

        assert engine._initialized is False


class TestMongoDBEngineCreateApp:
    """Test create_app method for FastAPI integration."""

    def test_create_app_returns_fastapi(self, tmp_path):
        """Test create_app returns a FastAPI instance."""
        from fastapi import FastAPI

        from mdb_engine import MongoDBEngine

        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(
            '{"slug": "test_app", "name": "Test App", "schema_version": "2.0"}'
        )

        engine = MongoDBEngine(
            mongo_uri="mongodb://localhost:27017",
            db_name="test_db",
        )

        app = engine.create_app(
            slug="test_app",
            manifest=manifest_path,
        )

        assert isinstance(app, FastAPI)

    def test_create_app_with_custom_title(self, tmp_path):
        """Test create_app with custom title."""
        from fastapi import FastAPI

        from mdb_engine import MongoDBEngine

        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(
            '{"slug": "test_app", "name": "Test App", "schema_version": "2.0"}'
        )

        engine = MongoDBEngine(
            mongo_uri="mongodb://localhost:27017",
            db_name="test_db",
        )

        app = engine.create_app(
            slug="test_app",
            manifest=manifest_path,
            title="Custom Title",
        )

        assert isinstance(app, FastAPI)
        assert app.title == "Custom Title"


class TestMongoDBEngineLifespan:
    """Test lifespan method for FastAPI integration."""

    def test_lifespan_returns_callable(self, tmp_path):
        """Test lifespan returns a callable."""
        from mdb_engine import MongoDBEngine

        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(
            '{"slug": "test_app", "name": "Test App", "schema_version": "2.0"}'
        )

        engine = MongoDBEngine(
            mongo_uri="mongodb://localhost:27017",
            db_name="test_db",
        )

        lifespan_fn = engine.lifespan(
            slug="test_app",
            manifest=manifest_path,
        )

        assert callable(lifespan_fn)


class TestMongoDBEngineAppToken:
    """Test app token retrieval functionality."""

    def test_get_app_token_empty_cache(self):
        """Test get_app_token returns None when cache is empty."""
        from mdb_engine import MongoDBEngine

        engine = MongoDBEngine(
            mongo_uri="mongodb://localhost:27017",
            db_name="test_db",
        )

        assert engine.get_app_token("nonexistent") is None

    def test_get_app_token_from_cache(self):
        """Test get_app_token returns value from cache."""
        from mdb_engine import MongoDBEngine

        engine = MongoDBEngine(
            mongo_uri="mongodb://localhost:27017",
            db_name="test_db",
        )

        # Manually populate cache
        engine._app_token_cache["test_app"] = "test_token"

        assert engine.get_app_token("test_app") == "test_token"

    @pytest.mark.asyncio
    async def test_auto_retrieve_from_env(self):
        """Test auto_retrieve_app_token from environment variable."""
        from mdb_engine import MongoDBEngine

        engine = MongoDBEngine(
            mongo_uri="mongodb://localhost:27017",
            db_name="test_db",
        )

        # Set environment variable
        os.environ["TEST_APP_SECRET"] = "env_token"

        try:
            token = await engine.auto_retrieve_app_token("test_app")
            assert token == "env_token"
            assert engine._app_token_cache["test_app"] == "env_token"
        finally:
            del os.environ["TEST_APP_SECRET"]


class TestRayIntegrationSmoke:
    """Smoke tests for Ray integration."""

    def test_ray_available_constant(self):
        """Test RAY_AVAILABLE is accessible."""
        from mdb_engine.core.ray_integration import RAY_AVAILABLE

        assert isinstance(RAY_AVAILABLE, bool)

    def test_ray_actor_import(self):
        """Test AppRayActor can be imported."""
        from mdb_engine.core.ray_integration import AppRayActor

        assert AppRayActor is not None

    def test_ray_decorator_import(self):
        """Test ray_actor_decorator can be imported."""
        from mdb_engine.core.ray_integration import ray_actor_decorator

        assert ray_actor_decorator is not None
        assert callable(ray_actor_decorator)
