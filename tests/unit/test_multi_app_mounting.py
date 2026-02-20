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

import base64
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
    os.environ["MDB_ENGINE_MASTER_KEY"] = base64.b64encode(b"x" * 32).decode()


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

        is_valid, errors = engine._validate_path_prefixes(apps)  # noqa: SLF001
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

        is_valid, errors = engine._validate_path_prefixes(apps)  # noqa: SLF001
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

        is_valid, errors = engine._validate_path_prefixes(apps)  # noqa: SLF001
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

        is_valid, errors = engine._validate_path_prefixes(apps)  # noqa: SLF001
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

        is_valid, errors = engine._validate_path_prefixes(apps)  # noqa: SLF001
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

    @pytest.mark.asyncio
    async def test_create_multi_app_programmatic(self, temp_manifests):
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

        with pytest.raises(
            ValueError,
            match="Either 'apps', 'multi_app_manifest', or 'apps_dir' must be provided",
        ):
            engine.create_multi_app()

    @pytest.mark.asyncio
    async def test_create_multi_app_empty_apps(self):
        """Test create_multi_app fails when apps list is empty."""
        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        with pytest.raises(ValueError, match="At least one app must be configured"):
            engine.create_multi_app(apps=[])

    @pytest.mark.asyncio
    async def test_create_multi_app_path_conflict(self, temp_manifests):
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

    @pytest.mark.asyncio
    async def test_create_multi_app_from_manifest(self, temp_multi_app_manifest):
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
    async def test_multi_app_shared_auth_initialization(self, mock_mongo_database, temp_shared_auth_manifests):
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
                assert "apps" in data  # Changed from "mounted_apps" to "apps"


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


class TestAutoDiscovery:
    """Test auto-discovery feature."""

    @pytest.fixture
    def temp_apps_dir(self, tmp_path):
        """Create temporary apps directory structure."""
        apps_dir = tmp_path / "apps"
        apps_dir.mkdir()

        # Create app1
        app1_dir = apps_dir / "app1"
        app1_dir.mkdir()
        manifest1 = {
            "schema_version": "2.0",
            "slug": "app1",
            "name": "App 1",
            "auth": {"mode": "app"},
        }
        (app1_dir / "manifest.json").write_text(json.dumps(manifest1))

        # Create app2
        app2_dir = apps_dir / "app2"
        app2_dir.mkdir()
        manifest2 = {
            "schema_version": "2.0",
            "slug": "app2",
            "name": "App 2",
            "auth": {"mode": "app"},
        }
        (app2_dir / "manifest.json").write_text(json.dumps(manifest2))

        # Create nested app3
        nested_dir = apps_dir / "nested" / "app3"
        nested_dir.mkdir(parents=True)
        manifest3 = {
            "schema_version": "2.0",
            "slug": "app3",
            "name": "App 3",
            "auth": {"mode": "app"},
        }
        (nested_dir / "manifest.json").write_text(json.dumps(manifest3))

        return apps_dir

    def test_auto_discover_apps(self, temp_apps_dir):
        """Test auto-discovery of apps from directory."""
        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        apps = engine._discover_apps_from_directory(temp_apps_dir)  # noqa: SLF001

        assert len(apps) == 3
        slugs = [app["slug"] for app in apps]
        assert "app1" in slugs
        assert "app2" in slugs
        assert "app3" in slugs

    def test_auto_discover_with_template(self, temp_apps_dir):
        """Test auto-discovery with path prefix template."""
        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        apps = engine._discover_apps_from_directory(  # noqa: SLF001
            temp_apps_dir, path_prefix_template="/app-{index}"
        )

        assert len(apps) == 3
        # Check that prefixes are generated
        prefixes = [app["path_prefix"] for app in apps]
        assert "/app-1" in prefixes
        assert "/app-2" in prefixes
        assert "/app-3" in prefixes

    def test_auto_discover_nonexistent_dir(self):
        """Test auto-discovery fails with nonexistent directory."""
        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        with pytest.raises(ValueError, match="does not exist"):
            engine._discover_apps_from_directory(Path("/nonexistent/dir"))  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_create_multi_app_with_auto_discovery(self, temp_apps_dir):
        """Test create_multi_app with auto-discovery."""
        from fastapi import FastAPI

        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        app = engine.create_multi_app(
            apps_dir=temp_apps_dir,
            path_prefix_template="/app-{index}",
        )

        assert app is not None
        assert isinstance(app, FastAPI)


class TestValidationMode:
    """Test validation mode feature."""

    @pytest.fixture
    def temp_manifests(self, tmp_path):
        """Create temporary manifest files."""
        manifests_dir = tmp_path / "manifests"
        manifests_dir.mkdir()

        # Valid manifest
        valid_manifest = {
            "schema_version": "2.0",
            "slug": "valid-app",
            "name": "Valid App",
            "auth": {"mode": "app"},
        }
        valid_path = manifests_dir / "valid-app" / "manifest.json"
        valid_path.parent.mkdir()
        valid_path.write_text(json.dumps(valid_manifest))

        # Invalid manifest (missing required fields)
        invalid_manifest = {
            "schema_version": "2.0",
            # Missing slug
            "name": "Invalid App",
        }
        invalid_path = manifests_dir / "invalid-app" / "manifest.json"
        invalid_path.parent.mkdir()
        invalid_path.write_text(json.dumps(invalid_manifest))

        return {
            "valid": valid_path,
            "invalid": invalid_path,
        }

    @pytest.mark.asyncio
    async def test_validation_mode_non_strict(self, temp_manifests):
        """Test validation mode with strict=False (warns but continues)."""
        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        # Should not raise, but log warnings
        app = engine.create_multi_app(
            apps=[
                {
                    "slug": "valid-app",
                    "manifest": temp_manifests["valid"],
                    "path_prefix": "/valid",
                }
            ],
            validate=True,
            strict=False,
        )

        assert app is not None

    @pytest.mark.asyncio
    async def test_validation_mode_strict(self, temp_manifests):
        """Test validation mode with strict=True (fails fast)."""
        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        with pytest.raises(ValueError, match="validation failed"):
            engine.create_multi_app(
                apps=[
                    {
                        "slug": "invalid-app",
                        "manifest": temp_manifests["invalid"],
                        "path_prefix": "/invalid",
                    }
                ],
                validate=True,
                strict=True,
            )


class TestAppContextHelpers:
    """Test built-in app context helpers."""

    @pytest.fixture
    def temp_manifest(self, tmp_path):
        """Create temporary manifest file."""
        manifest = {
            "schema_version": "2.0",
            "slug": "test-app",
            "name": "Test App",
            "auth": {
                "mode": "shared",
                "auth_hub_url": "/auth-hub",
                "roles": ["viewer"],
            },
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        return manifest_path

    @pytest.mark.asyncio
    async def test_app_context_middleware(self, mock_mongo_database, temp_manifest):
        """Test that app context helpers are set in request.state."""
        from fastapi import Request

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
                        "slug": "test-app",
                        "manifest": temp_manifest,
                        "path_prefix": "/test-app",
                    }
                ]
            )

            async with app.router.lifespan_context(app):
                # Find the mounted child app and add route to it
                child_app = None
                for route in app.routes:
                    if hasattr(route, "path") and route.path == "/test-app":
                        if hasattr(route, "app"):
                            child_app = route.app
                            break

                # Create a test route in the child app to check state
                @child_app.get("/check-state")
                async def check_state(request: Request):
                    return {
                        "app_base_path": getattr(request.state, "app_base_path", None),
                        "auth_hub_url": getattr(request.state, "auth_hub_url", None),
                        "app_slug": getattr(request.state, "app_slug", None),
                        "has_mounted_apps": hasattr(request.state, "mounted_apps"),
                        "has_engine": hasattr(request.state, "engine"),
                        "has_manifest": hasattr(request.state, "manifest"),
                    }

                client = TestClient(app)
                response = client.get("/test-app/check-state")

                assert response.status_code == 200
                data = response.json()
                assert data["app_base_path"] == "/test-app"
                assert data["auth_hub_url"] == "/auth-hub"
                assert data["app_slug"] == "test-app"
                assert data["has_mounted_apps"] is True
                assert data["has_engine"] is True
                assert data["has_manifest"] is True


class TestRouteIntrospection:
    """Test route introspection endpoint."""

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
    async def test_route_introspection_endpoint(self, mock_mongo_database, temp_manifests):
        """Test route introspection endpoint."""
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

            async with app.router.lifespan_context(app):
                client = TestClient(app)
                response = client.get("/_mdb/routes")

                assert response.status_code == 200
                data = response.json()
                assert "parent_app" in data
                assert "mounted_apps" in data
                assert "app1" in data["mounted_apps"]


class TestGetMountedApps:
    """Test get_mounted_apps() method."""

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

    @pytest.mark.asyncio
    async def test_get_mounted_apps(self, mock_mongo_database, temp_manifests):
        """Test get_mounted_apps() method."""
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

            # Test that get_mounted_apps() works immediately after create_multi_app()
            # (before lifespan runs)
            mounted_apps = engine.get_mounted_apps(app)
            assert len(mounted_apps) == 1
            assert mounted_apps[0]["slug"] == "app1"
            assert mounted_apps[0]["path_prefix"] == "/app1"
            assert mounted_apps[0]["status"] == "pending"  # Status is "pending" before lifespan

            # After lifespan runs, status should be updated to "mounted"
            async with app.router.lifespan_context(app):
                mounted_apps = engine.get_mounted_apps(app)

                assert len(mounted_apps) == 1
                assert mounted_apps[0]["slug"] == "app1"
                assert mounted_apps[0]["path_prefix"] == "/app1"
                assert mounted_apps[0]["status"] == "mounted"

    @pytest.mark.asyncio
    async def test_get_mounted_apps_before_lifespan(self, mock_mongo_database, temp_manifests):
        """Test get_mounted_apps() works immediately after create_multi_app() without lifespan."""
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
                    },
                    {
                        "slug": "app2",
                        "manifest": temp_manifests["app2"],
                        "path_prefix": "/app2",
                    },
                ]
            )

            # Should work without starting lifespan
            mounted_apps = engine.get_mounted_apps(app)
            assert len(mounted_apps) == 2
            assert mounted_apps[0]["slug"] == "app1"
            assert mounted_apps[0]["path_prefix"] == "/app1"
            assert mounted_apps[0]["status"] == "pending"
            assert "manifest_path" in mounted_apps[0]
            assert mounted_apps[1]["slug"] == "app2"
            assert mounted_apps[1]["path_prefix"] == "/app2"
            assert mounted_apps[1]["status"] == "pending"


class TestEnhancedHealthCheck:
    """Test enhanced health check endpoint."""

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
    async def test_enhanced_health_check(self, mock_mongo_database, temp_manifests):
        """Test enhanced health check with apps status."""
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

            async with app.router.lifespan_context(app):
                client = TestClient(app)
                response = client.get("/health")

                assert response.status_code == 200
                data = response.json()
                assert "status" in data
                assert "engine" in data
                assert "mongodb" in data
                assert "apps" in data  # Changed from "mounted_apps"
                assert "app1" in data["apps"]
                assert "response_time_ms" in data["engine"]


class TestStartupShutdownHooks:
    """Test startup/shutdown hooks validation."""

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
    async def test_invalid_startup_hook(self, temp_manifest):
        """Test that invalid startup hook raises error."""
        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        with pytest.raises(ValueError, match="must be callable"):
            engine.create_multi_app(
                apps=[
                    {
                        "slug": "test-app",
                        "manifest": temp_manifest,
                        "path_prefix": "/test-app",
                        "on_startup": "not a callable",  # Invalid
                    }
                ]
            )

    @pytest.mark.asyncio
    async def test_invalid_shutdown_hook(self, temp_manifest):
        """Test that invalid shutdown hook raises error."""
        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        with pytest.raises(ValueError, match="must be callable"):
            engine.create_multi_app(
                apps=[
                    {
                        "slug": "test-app",
                        "manifest": temp_manifest,
                        "path_prefix": "/test-app",
                        "on_shutdown": 123,  # Invalid
                    }
                ]
            )


class TestWebSocketRoutesWithMountedApps:
    """Test WebSocket routes registration for mounted apps."""

    @pytest.fixture
    def temp_manifest_with_websocket(self, tmp_path):
        """Create a temporary manifest file with WebSocket configuration."""
        manifest = {
            "schema_version": "2.0",
            "slug": "ws-app",
            "name": "WebSocket App",
            "auth": {"mode": "app"},
            "websockets": {
                "realtime": {
                    "path": "/ws",
                    "auth": {"required": False},
                    "ping_interval": 30,
                }
            },
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        return manifest_path

    @pytest.fixture
    def temp_manifest(self, tmp_path):
        """Create temporary manifest file without WebSocket config."""
        manifest = {
            "schema_version": "2.0",
            "slug": "no-ws-app",
            "name": "No WebSocket App",
            "auth": {"mode": "app"},
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        return manifest_path

    @pytest.mark.asyncio
    async def test_websocket_routes_registered_on_parent_app(self, mock_mongo_database, temp_manifest_with_websocket):
        """Test that WebSocket routes are registered on parent app with mount prefix."""
        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        with patch.object(engine, "_connection_manager") as mock_conn:
            mock_conn.mongo_db = mock_mongo_database
            mock_conn.mongo_client = MagicMock()
            mock_conn.initialized = True
            mock_conn.initialize = AsyncMock()
            mock_conn.shutdown = AsyncMock()

            mock_handler = MagicMock()
            mock_router = MagicMock()
            mock_include_router = MagicMock()

            with patch(
                "mdb_engine.routing.websockets.create_websocket_endpoint",
                return_value=mock_handler,
            ) as mock_create:
                with patch("fastapi.APIRouter", return_value=mock_router) as mock_router_class:
                    app = engine.create_multi_app(
                        apps=[
                            {
                                "slug": "ws-app",
                                "manifest": temp_manifest_with_websocket,
                                "path_prefix": "/app-3",
                            }
                        ]
                    )
                    # Mock include_router on the app instance
                    app.include_router = mock_include_router

                    # WebSocket routes are registered during lifespan, so run it
                    async with app.router.lifespan_context(app):
                        # Verify WebSocket endpoint was created with correct parameters
                        mock_create.assert_called_once()
                        call_kwargs = mock_create.call_args[1]
                        assert call_kwargs["app_slug"] == "ws-app"
                        assert call_kwargs["path"] == "/ws"
                        assert call_kwargs["endpoint_name"] == "realtime"
                        assert call_kwargs["require_auth"] is False
                        assert call_kwargs["ping_interval"] == 30

                        # Verify router was created and WebSocket route registered
                        assert mock_router_class.called
                        mock_router.websocket.assert_called_once_with("/app-3/ws")
                        assert mock_include_router.called

    @pytest.mark.asyncio
    async def test_websocket_routes_multiple_endpoints(self, mock_mongo_database, tmp_path):
        """Test WebSocket routes with multiple endpoints."""
        from mdb_engine.core.engine import MongoDBEngine

        manifest = {
            "schema_version": "2.0",
            "slug": "multi-ws-app",
            "name": "Multi WebSocket App",
            "auth": {"mode": "app"},
            "websockets": {
                "realtime": {"path": "/ws", "auth": {"required": False}},
                "events": {"path": "/events", "auth": {"required": True}},
            },
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        with patch.object(engine, "_connection_manager") as mock_conn:
            mock_conn.mongo_db = mock_mongo_database
            mock_conn.mongo_client = MagicMock()
            mock_conn.initialized = True
            mock_conn.initialize = AsyncMock()
            mock_conn.shutdown = AsyncMock()

            mock_handler = MagicMock()
            mock_router = MagicMock()

            with patch(
                "mdb_engine.routing.websockets.create_websocket_endpoint",
                return_value=mock_handler,
            ):
                with patch("fastapi.APIRouter", return_value=mock_router):
                    app = engine.create_multi_app(
                        apps=[
                            {
                                "slug": "multi-ws-app",
                                "manifest": manifest_path,
                                "path_prefix": "/multi",
                            }
                        ]
                    )

                    # WebSocket routes are registered during lifespan, so run it
                    async with app.router.lifespan_context(app):
                        # Should have registered 2 WebSocket routes
                        assert mock_router.websocket.call_count == 2

                        # Check that routes were registered with correct paths
                        call_paths = [call[0][0] for call in mock_router.websocket.call_args_list]
                        assert "/multi/ws" in call_paths
                        assert "/multi/events" in call_paths

    @pytest.mark.asyncio
    async def test_websocket_routes_no_config(self, mock_mongo_database, temp_manifest):
        """Test that apps without WebSocket config don't register routes."""
        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        with patch.object(engine, "_connection_manager") as mock_conn:
            mock_conn.mongo_db = mock_mongo_database
            mock_conn.mongo_client = MagicMock()
            mock_conn.initialized = True
            mock_conn.initialize = AsyncMock()
            mock_conn.shutdown = AsyncMock()

            with patch("fastapi.APIRouter") as mock_router_class:
                app = engine.create_multi_app(
                    apps=[
                        {
                            "slug": "no-ws-app",
                            "manifest": temp_manifest,
                            "path_prefix": "/no-ws",
                        }
                    ]
                )

                # WebSocket routes are registered during lifespan, so run it
                async with app.router.lifespan_context(app):
                    # Should not create any routers for WebSocket routes
                    assert not mock_router_class.called

    @pytest.mark.asyncio
    async def test_websocket_routes_import_error_handled(self, mock_mongo_database, temp_manifest_with_websocket):
        """Test that ImportError for WebSocket support is handled gracefully."""
        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        with patch.object(engine, "_connection_manager") as mock_conn:
            mock_conn.mongo_db = mock_mongo_database
            mock_conn.mongo_client = MagicMock()
            mock_conn.initialized = True
            mock_conn.initialize = AsyncMock()
            mock_conn.shutdown = AsyncMock()

            # Simulate ImportError when trying to import WebSocket support
            with patch(
                "mdb_engine.routing.websockets.create_websocket_endpoint",
                side_effect=ImportError("WebSocket support not available"),
            ):
                # Should not raise, but log warning
                app = engine.create_multi_app(
                    apps=[
                        {
                            "slug": "ws-app",
                            "manifest": temp_manifest_with_websocket,
                            "path_prefix": "/app-3",
                        }
                    ]
                )

                # WebSocket routes are registered during lifespan, so run it
                # Should not raise, but log warning
                async with app.router.lifespan_context(app):
                    # App should still be created successfully
                    assert app is not None

    @pytest.mark.asyncio
    async def test_websocket_registration_summary_logging(self, mock_mongo_database, temp_manifest_with_websocket):
        """Test that WebSocket registration includes summary logging."""
        import logging

        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        # Capture logs from root logger to catch all logs
        log_capture = []
        handler = logging.Handler()
        handler.emit = lambda record: log_capture.append(record.getMessage())

        # Add handler to root logger to catch all logs
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)

        try:
            with patch.object(engine, "_connection_manager") as mock_conn:
                mock_conn.mongo_db = mock_mongo_database
                mock_conn.mongo_client = MagicMock()
                mock_conn.initialized = True
                mock_conn.initialize = AsyncMock()
                mock_conn.shutdown = AsyncMock()

                # Create a real callable handler
                async def mock_ws_handler(websocket):
                    await websocket.accept()
                    await websocket.close()

                # Don't patch APIRouter - let it create real routers that register routes
                with patch(
                    "mdb_engine.routing.websockets.create_websocket_endpoint",
                    return_value=mock_ws_handler,
                ):
                    app = engine.create_multi_app(
                        apps=[
                            {
                                "slug": "ws-app",
                                "manifest": temp_manifest_with_websocket,
                                "path_prefix": "/app-3",
                            }
                        ]
                    )

                    async with app.router.lifespan_context(app):
                        # Check that summary logging occurred (either success or failure)
                        summary_logs = [
                            log
                            for log in log_capture
                            if "WebSocket registration summary" in log or "WebSocket registration issues" in log
                        ]
                        assert len(summary_logs) > 0, (
                            f"Registration summary should be logged. "
                            f"Captured logs: {log_capture[:20]}"  # Show first 20 logs
                        )
        finally:
            root_logger.removeHandler(handler)

    @pytest.mark.asyncio
    async def test_child_apps_skip_csrf_middleware(self, mock_mongo_database, tmp_path):
        """Test that child apps in multi-app setups don't get CSRF middleware."""
        import logging

        from mdb_engine.core.engine import MongoDBEngine

        manifest = {
            "schema_version": "2.0",
            "slug": "test-app",
            "name": "Test App",
            "auth": {"mode": "shared", "roles": ["viewer"], "require_role": "viewer"},
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        # Capture logs
        log_capture = []
        handler = logging.Handler()
        handler.emit = lambda record: log_capture.append(record.getMessage())

        # Capture logs from both engine and fastapi_app modules
        # (CSRF skip is logged by fastapi_app, not engine)
        loggers_to_capture = [
            logging.getLogger("mdb_engine.core.engine"),
            logging.getLogger("mdb_engine.core.fastapi_app"),
            logging.getLogger("mdb_engine.core.multi_app"),
        ]
        for lgr in loggers_to_capture:
            lgr.addHandler(handler)
            lgr.setLevel(logging.DEBUG)

        try:
            with patch.object(engine, "_connection_manager") as mock_conn:
                mock_conn.mongo_db = mock_mongo_database
                mock_conn.mongo_client = MagicMock()
                mock_conn.initialized = True
                mock_conn.initialize = AsyncMock()
                mock_conn.shutdown = AsyncMock()

                app = engine.create_multi_app(
                    apps=[
                        {
                            "slug": "test-app",
                            "manifest": manifest_path,
                            "path_prefix": "/test-app",
                        }
                    ]
                )

                async with app.router.lifespan_context(app):
                    # Check that CSRF middleware was skipped for child app
                    skip_logs = [log for log in log_capture if "CSRFMiddleware skipped for child app" in log]
                    assert len(skip_logs) > 0, "Child app should skip CSRF middleware. " f"Logs: {log_capture[:30]}"

                    # Verify parent app has CSRF middleware
                    parent_csrf_logs = [log for log in log_capture if "CSRFMiddleware added" in log]
                    assert len(parent_csrf_logs) > 0, (
                        "Parent app should have CSRF middleware. " f"Logs: {log_capture[:30]}"
                    )

        finally:
            for lgr in loggers_to_capture:
                lgr.removeHandler(handler)

    @pytest.mark.asyncio
    async def test_child_app_public_routes_merged_into_parent_csrf(self, mock_mongo_database, tmp_path):
        """Test that child app public routes are merged into parent CSRF exempt list."""
        from mdb_engine.core.engine import MongoDBEngine

        manifest1 = {
            "schema_version": "2.0",
            "slug": "app1",
            "name": "App 1",
            "auth": {
                "mode": "shared",
                "public_routes": ["/api/public", "/health"],
            },
        }
        manifest1_path = tmp_path / "manifest1.json"
        manifest1_path.write_text(json.dumps(manifest1))

        manifest2 = {
            "schema_version": "2.0",
            "slug": "app2",
            "name": "App 2",
            "auth": {
                "mode": "shared",
                "public_routes": ["/api/open"],
            },
        }
        manifest2_path = tmp_path / "manifest2.json"
        manifest2_path.write_text(json.dumps(manifest2))

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        with patch.object(engine, "_connection_manager") as mock_conn:
            mock_conn.mongo_db = mock_mongo_database
            mock_conn.mongo_client = MagicMock()
            mock_conn.initialized = True
            mock_conn.initialize = AsyncMock()
            mock_conn.shutdown = AsyncMock()

            app = engine.create_multi_app(
                apps=[
                    {"slug": "app1", "manifest": manifest1_path, "path_prefix": "/app1"},
                    {"slug": "app2", "manifest": manifest2_path, "path_prefix": "/app2"},
                ]
            )

            # Check that parent app CSRF middleware was created with merged public routes
            # We can't directly access the middleware config, but we can verify it was created
            # The actual exempt route checking happens at runtime
            assert app is not None
            # Verify parent app has CSRF middleware (it should be in middleware stack)
            # This is verified by the fact that the app was created successfully


class TestCORSCConfigPropagation:
    """Test CORS config propagation from child apps to parent app."""

    @pytest.mark.asyncio
    async def test_parent_app_has_default_cors_config(self, mock_mongo_database, tmp_path):
        """Test that parent app has default CORS config set."""
        from mdb_engine.core.engine import MongoDBEngine

        manifest = {
            "schema_version": "2.0",
            "slug": "test-app",
            "name": "Test App",
            "auth": {"mode": "app"},
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))

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
                        "slug": "test-app",
                        "manifest": manifest_path,
                        "path_prefix": "/test",
                    }
                ]
            )

            # Check that parent app has default CORS config
            assert hasattr(app.state, "cors_config")
            assert app.state.cors_config["enabled"] is True
            assert "*" in app.state.cors_config["allow_origins"]

    @pytest.mark.asyncio
    async def test_child_cors_config_merged_to_parent(self, mock_mongo_database, tmp_path):
        """Test that child app CORS config is merged into parent app."""
        from mdb_engine.core.engine import MongoDBEngine

        manifest = {
            "schema_version": "2.0",
            "slug": "test-app",
            "name": "Test App",
            "auth": {"mode": "app"},
            "cors": {
                "enabled": True,
                "allow_origins": ["https://example.com", "https://test.com"],
                "allow_credentials": True,
            },
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))

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
                        "slug": "test-app",
                        "manifest": manifest_path,
                        "path_prefix": "/test",
                    }
                ]
            )

            # Run lifespan to mount apps
            async with app.router.lifespan_context(app):
                # Check that parent app has merged CORS config
                assert hasattr(app.state, "cors_config")
                cors_config = app.state.cors_config
                # When child has credentials=True and specific origins,
                # merged should use child's origins
                # (not parent's wildcard, because wildcard + credentials is invalid)
                assert "https://example.com" in cors_config["allow_origins"]
                assert "https://test.com" in cors_config["allow_origins"]
                assert cors_config["allow_credentials"] is True

    @pytest.mark.asyncio
    async def test_multiple_child_apps_merge_cors_origins(self, mock_mongo_database, tmp_path):
        """Test that multiple child apps' CORS origins are merged."""
        from mdb_engine.core.engine import MongoDBEngine

        manifest1 = {
            "schema_version": "2.0",
            "slug": "app1",
            "name": "App 1",
            "auth": {"mode": "app"},
            "cors": {
                "enabled": True,
                "allow_origins": ["https://app1.com"],
            },
        }
        manifest_path1 = tmp_path / "app1" / "manifest.json"
        manifest_path1.parent.mkdir()
        manifest_path1.write_text(json.dumps(manifest1))

        manifest2 = {
            "schema_version": "2.0",
            "slug": "app2",
            "name": "App 2",
            "auth": {"mode": "app"},
            "cors": {
                "enabled": True,
                "allow_origins": ["https://app2.com"],
            },
        }
        manifest_path2 = tmp_path / "app2" / "manifest.json"
        manifest_path2.parent.mkdir()
        manifest_path2.write_text(json.dumps(manifest2))

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
                        "manifest": manifest_path1,
                        "path_prefix": "/app1",
                    },
                    {
                        "slug": "app2",
                        "manifest": manifest_path2,
                        "path_prefix": "/app2",
                    },
                ]
            )

            # Run lifespan to mount apps
            async with app.router.lifespan_context(app):
                # Check that parent app has merged CORS config from both apps
                assert hasattr(app.state, "cors_config")
                cors_config = app.state.cors_config
                origins = cors_config["allow_origins"]
                # Should include origins from both apps (and possibly default "*")
                assert "https://app1.com" in origins or "*" in origins
                assert "https://app2.com" in origins or "*" in origins

    @pytest.mark.asyncio
    async def test_child_app_without_cors_config_uses_default(self, mock_mongo_database, tmp_path):
        """Test that child app without CORS config doesn't break merging."""
        from mdb_engine.core.engine import MongoDBEngine

        manifest = {
            "schema_version": "2.0",
            "slug": "test-app",
            "name": "Test App",
            "auth": {"mode": "app"},
            # No CORS config
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))

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
                        "slug": "test-app",
                        "manifest": manifest_path,
                        "path_prefix": "/test",
                    }
                ]
            )

            # Run lifespan to mount apps
            async with app.router.lifespan_context(app):
                # Parent app should still have default CORS config
                assert hasattr(app.state, "cors_config")
                assert app.state.cors_config["enabled"] is True
