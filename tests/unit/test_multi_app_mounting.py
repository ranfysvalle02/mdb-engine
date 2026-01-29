"""
Tests for multi-app mounting functionality.

Tests the create_multi_app() method and related functionality:
- Programmatic multi-app configuration
- Manifest-based multi-app configuration
- Path prefix validation
- Shared auth in multi-app context
- Mounted app routing
- Health check endpoint
"""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Set test secret key before importing engine components
if "MDB_ENGINE_JWT_SECRET" not in os.environ:
    os.environ["MDB_ENGINE_JWT_SECRET"] = "test_jwt_secret_for_testing_only_" + "x" * 32

if "MDB_ENGINE_MASTER_KEY" not in os.environ:
    os.environ["MDB_ENGINE_MASTER_KEY"] = "test_master_key_for_testing_only_" + "x" * 32


class TestPathPrefixValidation:
    """Test path prefix validation logic."""

    def test_validate_path_prefixes_valid(self):
        """Test validation with valid path prefixes."""
        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")
        apps = [
            {"slug": "app1", "path_prefix": "/app1"},
            {"slug": "app2", "path_prefix": "/app2"},
            {"slug": "app3", "path_prefix": "/app3"},
        ]

        is_valid, errors = engine._validate_path_prefixes(apps)
        assert is_valid is True
        assert len(errors) == 0

    def test_validate_path_prefixes_missing_slash(self):
        """Test validation fails when path prefix doesn't start with '/'."""
        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")
        apps = [
            {"slug": "app1", "path_prefix": "app1"},  # Missing leading slash
            {"slug": "app2", "path_prefix": "/app2"},
        ]

        is_valid, errors = engine._validate_path_prefixes(apps)
        assert is_valid is False
        assert any("must start with '/'" in error for error in errors)

    def test_validate_path_prefixes_conflict(self):
        """Test validation fails when one prefix is prefix of another."""
        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")
        apps = [
            {"slug": "app1", "path_prefix": "/app"},
            {"slug": "app2", "path_prefix": "/app/v2"},  # Conflict: /app is prefix of /app/v2
        ]

        is_valid, errors = engine._validate_path_prefixes(apps)
        assert is_valid is False
        assert any("overlap" in error.lower() for error in errors)

    def test_validate_path_prefixes_reserved_path(self):
        """Test validation fails when path conflicts with reserved paths."""
        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")
        apps = [
            {"slug": "app1", "path_prefix": "/health"},  # Conflicts with reserved /health
            {"slug": "app2", "path_prefix": "/app2"},
        ]

        is_valid, errors = engine._validate_path_prefixes(apps)
        assert is_valid is False
        assert any("reserved path" in error.lower() for error in errors)

    def test_validate_path_prefixes_duplicate(self):
        """Test validation fails when path prefixes are duplicated."""
        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")
        apps = [
            {"slug": "app1", "path_prefix": "/app"},
            {"slug": "app2", "path_prefix": "/app"},  # Duplicate
        ]

        is_valid, errors = engine._validate_path_prefixes(apps)
        assert is_valid is False
        assert any("duplicate" in error.lower() for error in errors)


class TestCreateMultiAppProgrammatic:
    """Test create_multi_app() with programmatic configuration."""

    @pytest.fixture
    def temp_manifests(self, tmp_path):
        """Create temporary manifest files for testing."""
        manifests_dir = tmp_path / "manifests"
        manifests_dir.mkdir()

        # Create manifest 1
        manifest1 = {
            "schema_version": "2.0",
            "slug": "app1",
            "name": "App 1",
            "auth": {"mode": "app"},
        }
        manifest1_path = manifests_dir / "app1" / "manifest.json"
        manifest1_path.parent.mkdir()
        manifest1_path.write_text(json.dumps(manifest1))

        # Create manifest 2
        manifest2 = {
            "schema_version": "2.0",
            "slug": "app2",
            "name": "App 2",
            "auth": {"mode": "app"},
        }
        manifest2_path = manifests_dir / "app2" / "manifest.json"
        manifest2_path.parent.mkdir()
        manifest2_path.write_text(json.dumps(manifest2))

        return {
            "app1": manifest1_path,
            "app2": manifest2_path,
        }

    def test_create_multi_app_programmatic(self, temp_manifests):
        """Test creating multi-app with programmatic configuration."""
        from fastapi import FastAPI

        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        app = engine.create_multi_app(
            apps=[
                {
                    "slug": "app1",
                    "manifest": temp_manifests["app1"],
                    "path_prefix": "/app1",
                },
                {
                    "slug": "app2",
                    "manifest": temp_manifests["app2"],
                    "path_prefix": "/app2",
                },
            ],
            title="Test Multi-App",
        )

        assert app is not None
        assert isinstance(app, FastAPI)
        assert app.title == "Test Multi-App"

    @pytest.mark.asyncio
    async def test_create_multi_app_no_apps(self):
        """Test create_multi_app fails when no apps provided."""
        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        with pytest.raises(ValueError, match="Either 'apps' or 'multi_app_manifest' must be provided"):
            engine.create_multi_app()

    def test_create_multi_app_empty_apps(self):
        """Test create_multi_app fails when apps list is empty."""
        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        with pytest.raises(ValueError, match="At least one app must be configured"):
            engine.create_multi_app(apps=[])

    def test_create_multi_app_path_conflict(self, temp_manifests):
        """Test create_multi_app fails when path prefixes conflict."""
        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        with pytest.raises(ValueError, match="Path prefix validation failed"):
            engine.create_multi_app(
                apps=[
                    {
                        "slug": "app1",
                        "manifest": temp_manifests["app1"],
                        "path_prefix": "/app",
                    },
                    {
                        "slug": "app2",
                        "manifest": temp_manifests["app2"],
                        "path_prefix": "/app/v2",  # Conflict
                    },
                ]
            )


class TestCreateMultiAppManifest:
    """Test create_multi_app() with manifest-based configuration."""

    @pytest.fixture
    def temp_multi_app_manifest(self, tmp_path):
        """Create temporary multi-app manifest file."""
        manifests_dir = tmp_path / "manifests"
        manifests_dir.mkdir()

        # Create child manifests
        manifest1 = {
            "schema_version": "2.0",
            "slug": "app1",
            "name": "App 1",
            "auth": {"mode": "app"},
        }
        manifest1_path = manifests_dir / "app1" / "manifest.json"
        manifest1_path.parent.mkdir()
        manifest1_path.write_text(json.dumps(manifest1))

        manifest2 = {
            "schema_version": "2.0",
            "slug": "app2",
            "name": "App 2",
            "auth": {"mode": "app"},
        }
        manifest2_path = manifests_dir / "app2" / "manifest.json"
        manifest2_path.parent.mkdir()
        manifest2_path.write_text(json.dumps(manifest2))

        # Create multi-app manifest
        multi_app_manifest = {
            "schema_version": "2.0",
            "multi_app": {
                "enabled": True,
                "apps": [
                    {
                        "slug": "app1",
                        "manifest": "./app1/manifest.json",
                        "path_prefix": "/app1",
                    },
                    {
                        "slug": "app2",
                        "manifest": "./app2/manifest.json",
                        "path_prefix": "/app2",
                    },
                ],
            },
        }
        multi_app_manifest_path = manifests_dir / "multi_app_manifest.json"
        multi_app_manifest_path.write_text(json.dumps(multi_app_manifest))

        return multi_app_manifest_path

    def test_create_multi_app_from_manifest(self, temp_multi_app_manifest):
        """Test creating multi-app from manifest file."""
        from fastapi import FastAPI

        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        app = engine.create_multi_app(multi_app_manifest=temp_multi_app_manifest)

        assert app is not None
        assert isinstance(app, FastAPI)

    @pytest.mark.asyncio
    async def test_create_multi_app_manifest_not_enabled(self, tmp_path):
        """Test create_multi_app fails when multi_app.enabled is False."""
        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        # Create manifest with multi_app.enabled = False
        manifest_path = tmp_path / "multi_app_manifest.json"
        manifest = {
            "schema_version": "2.0",
            "multi_app": {
                "enabled": False,  # Not enabled
                "apps": [],
            },
        }
        manifest_path.write_text(json.dumps(manifest))

        with pytest.raises(ValueError, match="multi_app.enabled must be True"):
            engine.create_multi_app(multi_app_manifest=manifest_path)


class TestMultiAppSharedAuth:
    """Test shared auth in multi-app context."""

    @pytest.fixture
    def temp_shared_auth_manifests(self, tmp_path):
        """Create temporary manifests with shared auth."""
        manifests_dir = tmp_path / "manifests"
        manifests_dir.mkdir()

        # Create auth hub manifest (shared auth)
        auth_hub_manifest = {
            "schema_version": "2.0",
            "slug": "auth-hub",
            "name": "Auth Hub",
            "auth": {
                "mode": "shared",
                "roles": ["viewer", "admin"],
                "require_role": "viewer",
            },
        }
        auth_hub_path = manifests_dir / "auth-hub" / "manifest.json"
        auth_hub_path.parent.mkdir()
        auth_hub_path.write_text(json.dumps(auth_hub_manifest))

        # Create SSO app manifest (shared auth)
        sso_app_manifest = {
            "schema_version": "2.0",
            "slug": "sso-app",
            "name": "SSO App",
            "auth": {
                "mode": "shared",
                "roles": ["viewer", "admin"],
                "require_role": "viewer",
            },
        }
        sso_app_path = manifests_dir / "sso-app" / "manifest.json"
        sso_app_path.parent.mkdir()
        sso_app_path.write_text(json.dumps(sso_app_manifest))

        return {
            "auth-hub": auth_hub_path,
            "sso-app": sso_app_path,
        }

    @pytest.mark.asyncio
    async def test_multi_app_shared_auth_initialization(
        self, mock_mongo_database, temp_shared_auth_manifests
    ):
        """Test that shared auth is initialized once for multi-app."""
        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        with patch.object(engine, "_connection_manager") as mock_conn:
            mock_conn.mongo_db = mock_mongo_database
            mock_conn.mongo_client = MagicMock()
            mock_conn.initialized = True
            mock_conn.initialize = AsyncMock()
            mock_conn.shutdown = AsyncMock()

            # Mock _initialize_shared_user_pool
            with patch.object(engine, "_initialize_shared_user_pool") as mock_init_pool:
                mock_init_pool.return_value = AsyncMock()

                app = engine.create_multi_app(
                    apps=[
                        {
                            "slug": "auth-hub",
                            "manifest": temp_shared_auth_manifests["auth-hub"],
                            "path_prefix": "/auth-hub",
                        },
                        {
                            "slug": "sso-app",
                            "manifest": temp_shared_auth_manifests["sso-app"],
                            "path_prefix": "/sso-app",
                        },
                    ]
                )

                # Start the app to trigger lifespan
                async with app.router.lifespan_context(app):
                    # _initialize_shared_user_pool should be called once
                    assert mock_init_pool.call_count == 1


class TestMultiAppHealthCheck:
    """Test health check endpoint in multi-app."""

    @pytest.fixture
    def temp_manifests(self, tmp_path):
        """Create temporary manifest files."""
        manifests_dir = tmp_path / "manifests"
        manifests_dir.mkdir()

        manifest1 = {
            "schema_version": "2.0",
            "slug": "app1",
            "name": "App 1",
            "auth": {"mode": "app"},
        }
        manifest1_path = manifests_dir / "app1" / "manifest.json"
        manifest1_path.parent.mkdir()
        manifest1_path.write_text(json.dumps(manifest1))

        return {"app1": manifest1_path}

    @pytest.mark.asyncio
    async def test_health_check_endpoint(self, mock_mongo_database, temp_manifests):
        """Test unified health check endpoint."""
        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        with patch.object(engine, "_connection_manager") as mock_conn:
            mock_conn.mongo_db = mock_mongo_database
            mock_conn.mongo_client = MagicMock()
            mock_conn.initialized = True
            mock_conn.initialize = AsyncMock()
            mock_conn.shutdown = AsyncMock()

            app = engine.create_multi_app(
                apps=[
                    {
                        "slug": "app1",
                        "manifest": temp_manifests["app1"],
                        "path_prefix": "/app1",
                    }
                ]
            )

            # Start lifespan
            async with app.router.lifespan_context(app):
                client = TestClient(app)
                response = client.get("/health")

                assert response.status_code == 200
                data = response.json()
                assert "status" in data
                assert "engine" in data
                assert "mongodb" in data
                assert "mounted_apps" in data


class TestSubAppMode:
    """Test is_sub_app parameter in create_app()."""

    @pytest.fixture
    def temp_manifest(self, tmp_path):
        """Create temporary manifest file."""
        manifest = {
            "schema_version": "2.0",
            "slug": "test-app",
            "name": "Test App",
            "auth": {"mode": "app"},
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        return manifest_path

    @pytest.mark.asyncio
    async def test_create_app_sub_app_mode(self, mock_mongo_database, temp_manifest):
        """Test create_app with is_sub_app=True skips engine initialization."""
        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        with patch.object(engine, "_connection_manager") as mock_conn:
            mock_conn.mongo_db = mock_mongo_database
            mock_conn.mongo_client = MagicMock()
            mock_conn.initialized = True
            mock_conn.initialize = AsyncMock()
            mock_conn.shutdown = AsyncMock()

            # Mock the apps_config collection replace_one method
            mock_apps_config = MagicMock()
            mock_apps_config.replace_one = AsyncMock(return_value=MagicMock(modified_count=1))
            mock_mongo_database.apps_config = mock_apps_config

            # Initialize engine first (as parent would)
            await engine.initialize()

            # Create sub-app (should not initialize engine again)
            app = engine.create_app(
                slug="test-app",
                manifest=temp_manifest,
                is_sub_app=True,
            )

            # Start lifespan - should not call initialize again
            init_call_count_before = mock_conn.initialize.call_count

            async with app.router.lifespan_context(app):
                # Initialize should not be called again (is_sub_app=True skips it)
                assert mock_conn.initialize.call_count == init_call_count_before
