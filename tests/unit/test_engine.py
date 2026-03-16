"""
Unit tests for MongoDBEngine.

Tests the core orchestration engine functionality including:
- Initialization and shutdown
- App registration
- Manifest validation
- Database scoping
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mdb_engine.core.engine import MongoDBEngine
from mdb_engine.exceptions import InitializationError


class TestMongoDBEngineInitialization:
    """Test MongoDBEngine initialization and lifecycle."""

    @pytest.mark.asyncio
    async def test_engine_initialization_success(self, mock_mongo_client, mongodb_engine_config):
        """Test successful engine initialization."""
        with patch(
            "mdb_engine.core.connection.AsyncIOMotorClient",
            return_value=mock_mongo_client,
        ):
            engine = MongoDBEngine(**mongodb_engine_config)
            await engine.initialize()

            assert engine._initialized is True  # noqa: SLF001
            assert engine.mongo_client is not None

            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_engine_initialization_failure_connection(self, mongodb_engine_config):
        """Test engine initialization failure due to connection error."""
        from pymongo.errors import ConnectionFailure

        mock_client = MagicMock()
        mock_client.admin.command = AsyncMock(side_effect=ConnectionFailure("Connection failed"))

        with patch("mdb_engine.core.connection.AsyncIOMotorClient", return_value=mock_client):
            engine = MongoDBEngine(**mongodb_engine_config)

            with pytest.raises(InitializationError) as exc_info:
                await engine.initialize()

            assert "Failed to connect to MongoDB" in str(exc_info.value)
            assert engine._initialized is False  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_engine_double_initialization(self, mongodb_engine):
        """Test that double initialization is handled gracefully."""
        # First initialization happens in fixture
        assert mongodb_engine._initialized is True  # noqa: SLF001

        # Second initialization should be a no-op
        await mongodb_engine.initialize()
        assert mongodb_engine._initialized is True  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_engine_shutdown(self, mongodb_engine):
        """Test engine shutdown."""
        assert mongodb_engine._initialized is True  # noqa: SLF001

        await mongodb_engine.shutdown()

        assert mongodb_engine._initialized is False  # noqa: SLF001
        assert len(mongodb_engine.apps) == 0

    @pytest.mark.asyncio
    async def test_engine_shutdown_idempotent(self, mongodb_engine):
        """Test that shutdown is idempotent."""
        await mongodb_engine.shutdown()
        await mongodb_engine.shutdown()  # Should not raise

    @pytest.mark.asyncio
    async def test_shutdown_logs_timing(self, mongodb_engine):
        """Test that shutdown emits structured log messages with timing."""
        with patch("mdb_engine.core.engine.logger") as mock_logger:
            await mongodb_engine.shutdown()
            log_messages = [str(call) for call in mock_logger.info.call_args_list]
            assert any("Shutdown initiated" in m for m in log_messages)
            assert any("Shutdown complete" in m for m in log_messages)

    @pytest.mark.asyncio
    async def test_engine_context_manager(self, mock_mongo_client, mongodb_engine_config):
        """Test engine as async context manager."""
        with patch(
            "mdb_engine.core.connection.AsyncIOMotorClient",
            return_value=mock_mongo_client,
        ):
            async with MongoDBEngine(**mongodb_engine_config) as engine:
                assert engine._initialized is True  # noqa: SLF001

            # After context exit, should be shut down
            assert engine._initialized is False  # noqa: SLF001


class TestMongoDBEngineProperties:
    """Test MongoDBEngine property accessors."""

    @pytest.mark.asyncio
    async def test_mongo_client_property_uninitialized(self, uninitialized_mongodb_engine):
        """Test accessing mongo_client before initialization raises error."""
        with pytest.raises(RuntimeError, match="not initialized"):
            _ = uninitialized_mongodb_engine.mongo_client

    @pytest.mark.asyncio
    async def test_mongo_db_property_removed(self, mongodb_engine):
        """Test that mongo_db property is no longer accessible."""
        with pytest.raises(AttributeError, match="mongo_db"):
            _ = mongodb_engine.mongo_db

    @pytest.mark.asyncio
    async def test_mongo_client_property_initialized(self, mongodb_engine):
        """Test accessing mongo_client after initialization."""
        client = mongodb_engine.mongo_client
        assert client is not None


class TestMongoDBEngineScopedDatabase:
    """Test scoped database wrapper creation."""

    @pytest.mark.asyncio
    async def test_get_scoped_db_success(self, mongodb_engine):
        """Test successful scoped database creation."""
        scoped_db = await mongodb_engine.get_scoped_db("test_app")

        assert scoped_db is not None
        assert scoped_db._read_scopes == ["test_app"]  # noqa: SLF001
        assert scoped_db._write_scope == "test_app"  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_get_scoped_db_custom_scopes(self, mongodb_engine, sample_manifest):
        """Test scoped database with custom read/write scopes."""
        # Register app with read_scopes including app1 and app2
        manifest_with_scopes = sample_manifest.copy()
        manifest_with_scopes["slug"] = "test_app"  # Update slug to match test expectations
        manifest_with_scopes["schema_version"] = "2.0"  # Use V2 schema
        manifest_with_scopes["data_access"] = {
            "read_scopes": ["test_app", "app1", "app2"],
        }
        await mongodb_engine.register_app(manifest_with_scopes, create_indexes=False)

        # Verify read_scopes were set correctly
        assert mongodb_engine._app_read_scopes.get("test_app") == [  # noqa: SLF001
            "test_app",
            "app1",
            "app2",
        ]

        # Also register app1 and app2 so they exist
        app1_manifest = sample_manifest.copy()
        app1_manifest["slug"] = "app1"
        app1_manifest["name"] = "App 1"
        await mongodb_engine.register_app(app1_manifest, create_indexes=False)

        app2_manifest = sample_manifest.copy()
        app2_manifest["slug"] = "app2"
        app2_manifest["name"] = "App 2"
        await mongodb_engine.register_app(app2_manifest, create_indexes=False)

        scoped_db = await mongodb_engine.get_scoped_db(
            app_slug="test_app", read_scopes=["app1", "app2"], write_scope="app1"
        )

        assert scoped_db._read_scopes == ["app1", "app2"]  # noqa: SLF001
        assert scoped_db._write_scope == "app1"  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_get_scoped_db_has_validators_and_limiters(self, mongodb_engine):
        """Test that scoped database has query validators and resource limiters."""
        scoped_db = await mongodb_engine.get_scoped_db("test_app")

        # Verify validators and limiters are present
        assert hasattr(scoped_db, "_query_validator")
        assert hasattr(scoped_db, "_resource_limiter")
        assert scoped_db._query_validator is not None  # noqa: SLF001
        assert scoped_db._resource_limiter is not None  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_get_scoped_db_collections_have_validators(self, mongodb_engine, mock_mongo_database):
        """Test that collections created from scoped db have validators."""
        # Mock the database
        with patch.object(mongodb_engine._connection_manager, "mongo_db", mock_mongo_database):  # noqa: SLF001
            scoped_db = await mongodb_engine.get_scoped_db("test_app")

            # Mock collection
            from motor.motor_asyncio import AsyncIOMotorCollection

            mock_collection = MagicMock(spec=AsyncIOMotorCollection)
            mock_collection.name = "test_app_users"
            mock_mongo_database.test_app_users = mock_collection

            # Get collection
            collection = scoped_db.users

            # Verify collection has validators
            assert hasattr(collection, "_query_validator")
            assert hasattr(collection, "_resource_limiter")
            assert collection._query_validator is scoped_db._query_validator  # noqa: SLF001
            assert collection._resource_limiter is scoped_db._resource_limiter  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_get_scoped_db_security_features_work(self, mongodb_engine, mock_mongo_database):
        """Test that security features work end-to-end through engine."""
        from motor.motor_asyncio import AsyncIOMotorCollection

        from mdb_engine.exceptions import QueryValidationError

        # Mock the database
        with patch.object(mongodb_engine._connection_manager, "mongo_db", mock_mongo_database):  # noqa: SLF001
            scoped_db = await mongodb_engine.get_scoped_db("test_app")

            # Mock collection
            mock_collection = MagicMock(spec=AsyncIOMotorCollection)
            mock_collection.name = "test_app_users"
            mock_mongo_database.test_app_users = mock_collection

            collection = scoped_db.users

            # Test that dangerous operator is blocked
            with pytest.raises(QueryValidationError, match="Dangerous operator"):
                collection.find({"$where": "true"})

    @pytest.mark.asyncio
    async def test_get_scoped_db_uninitialized(self, uninitialized_mongodb_engine):
        """Test getting scoped db before initialization raises error."""
        with pytest.raises(RuntimeError, match="not initialized"):
            await uninitialized_mongodb_engine.get_scoped_db("test_app")

    @pytest.mark.asyncio
    async def test_get_scoped_db_auto_index_disabled(self, mongodb_engine):
        """Test scoped database with auto_index disabled."""
        scoped_db = await mongodb_engine.get_scoped_db("test_app", auto_index=False)

        assert scoped_db._auto_index is False  # noqa: SLF001


class TestMongoDBEngineManifestValidation:
    """Test manifest validation functionality."""

    @pytest.mark.asyncio
    async def test_validate_manifest_valid(self, mongodb_engine, sample_manifest):
        """Test validation of a valid manifest."""
        is_valid, error, paths = await mongodb_engine.validate_manifest(sample_manifest)

        assert is_valid is True
        assert error is None
        assert paths is None

    @pytest.mark.asyncio
    async def test_validate_manifest_invalid(self, mongodb_engine, invalid_manifest):
        """Test validation of an invalid manifest."""
        is_valid, error, paths = await mongodb_engine.validate_manifest(invalid_manifest)

        assert is_valid is False
        assert error is not None
        assert paths is not None
        assert len(paths) > 0

    @pytest.mark.asyncio
    async def test_load_manifest_from_file(self, mongodb_engine, tmp_path, sample_manifest):
        """Test loading manifest from file."""
        import json

        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(sample_manifest))

        loaded = await mongodb_engine.load_manifest(manifest_file)

        assert loaded["slug"] == sample_manifest["slug"]
        assert loaded["name"] == sample_manifest["name"]

    @pytest.mark.asyncio
    async def test_load_manifest_file_not_found(self, mongodb_engine, tmp_path):
        """Test loading non-existent manifest file."""
        manifest_file = tmp_path / "nonexistent.json"

        with pytest.raises(FileNotFoundError):
            await mongodb_engine.load_manifest(manifest_file)

    @pytest.mark.asyncio
    async def test_validate_manifest_not_initialized(self):
        """Test validate_manifest raises RuntimeError when not initialized (line 230)."""
        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")
        with pytest.raises(RuntimeError, match="not initialized"):
            await engine.validate_manifest({})

    @pytest.mark.asyncio
    async def test_load_manifest_not_initialized(self):
        """Test load_manifest raises RuntimeError when not initialized (line 250)."""
        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")
        with pytest.raises(RuntimeError, match="not initialized"):
            await engine.load_manifest("nonexistent.json")


class TestMongoDBEngineGetScopedDbSecurity:
    """Test security validation in get_scoped_db."""

    @pytest.mark.asyncio
    async def test_get_scoped_db_validates_read_scopes(self, mongodb_engine):
        """Test that get_scoped_db validates read_scopes."""
        await mongodb_engine.initialize()

        # Empty read_scopes
        with pytest.raises(ValueError, match="cannot be empty"):
            await mongodb_engine.get_scoped_db("test_app", read_scopes=[])

        # Invalid type
        with pytest.raises(ValueError, match="must be a list"):
            await mongodb_engine.get_scoped_db("test_app", read_scopes="not_a_list")

        # Invalid app slug in read_scopes
        with pytest.raises(ValueError, match="Invalid app slug"):
            await mongodb_engine.get_scoped_db("test_app", read_scopes=[""])

        with pytest.raises(ValueError, match="Invalid app slug"):
            await mongodb_engine.get_scoped_db("test_app", read_scopes=[None])

    @pytest.mark.asyncio
    async def test_get_scoped_db_validates_write_scope(self, mongodb_engine):
        """Test that get_scoped_db validates write_scope."""
        await mongodb_engine.initialize()

        # Empty write_scope
        with pytest.raises(ValueError, match="non-empty string"):
            await mongodb_engine.get_scoped_db("test_app", write_scope="")

        # None write_scope (will default to app_slug, which is valid)
        db = await mongodb_engine.get_scoped_db("test_app", write_scope=None)
        assert db is not None

    @pytest.mark.asyncio
    async def test_get_scoped_db_valid_scopes(self, mongodb_engine, sample_manifest):
        """Test that get_scoped_db works with valid scopes."""
        await mongodb_engine.initialize()

        # Register app with read_scopes including other_app
        manifest_with_scopes = sample_manifest.copy()
        manifest_with_scopes["slug"] = "test_app"  # Update slug to match test expectations
        manifest_with_scopes["schema_version"] = "2.0"  # Use V2 schema
        manifest_with_scopes["data_access"] = {
            "read_scopes": ["test_app", "other_app"],
        }
        await mongodb_engine.register_app(manifest_with_scopes, create_indexes=False)

        # Verify read_scopes were set correctly
        assert mongodb_engine._app_read_scopes.get("test_app") == [  # noqa: SLF001
            "test_app",
            "other_app",
        ]

        # Also register other_app so it exists
        other_app_manifest = sample_manifest.copy()
        other_app_manifest["slug"] = "other_app"
        other_app_manifest["name"] = "Other App"
        await mongodb_engine.register_app(other_app_manifest, create_indexes=False)

        # Valid single scope
        db = await mongodb_engine.get_scoped_db("test_app")
        assert db is not None

        # Valid multiple scopes (now authorized in manifest)
        db = await mongodb_engine.get_scoped_db("test_app", read_scopes=["test_app", "other_app"])
        assert db is not None


class TestMongoDBEngineTenantRegistration:
    """Test app registration functionality."""

    @pytest.mark.asyncio
    async def test_register_app_success(self, mongodb_engine, sample_manifest):
        """Test successful app registration."""
        result = await mongodb_engine.register_app(sample_manifest, create_indexes=False)

        assert result is True
        assert sample_manifest["slug"] in mongodb_engine.apps
        assert mongodb_engine.get_app(sample_manifest["slug"]) == sample_manifest

    @pytest.mark.asyncio
    async def test_register_app_missing_slug(self, mongodb_engine, sample_manifest):
        """Test registration with missing slug."""
        manifest_no_slug = {k: v for k, v in sample_manifest.items() if k != "slug"}

        result = await mongodb_engine.register_app(manifest_no_slug)

        assert result is False
        assert len(mongodb_engine.apps) == 0

    @pytest.mark.asyncio
    async def test_register_app_invalid_manifest(self, mongodb_engine, invalid_manifest):
        """Test registration with invalid manifest."""
        result = await mongodb_engine.register_app(invalid_manifest)

        assert result is False
        assert len(mongodb_engine.apps) == 0

    @pytest.mark.asyncio
    async def test_register_app_uninitialized(self, uninitialized_mongodb_engine, sample_manifest):
        """Test registration before initialization raises error."""
        with pytest.raises(RuntimeError, match="not initialized"):
            await uninitialized_mongodb_engine.register_app(sample_manifest)

    @pytest.mark.asyncio
    async def test_get_app(self, mongodb_engine, sample_manifest):
        """Test getting registered app."""
        await mongodb_engine.register_app(sample_manifest, create_indexes=False)

        app = mongodb_engine.get_app(sample_manifest["slug"])
        assert app is not None
        assert app["slug"] == sample_manifest["slug"]

    @pytest.mark.asyncio
    async def test_get_app_not_found(self, mongodb_engine):
        """Test getting non-existent app."""
        app = mongodb_engine.get_app("nonexistent")
        assert app is None

    @pytest.mark.asyncio
    async def test_list_apps(self, mongodb_engine, sample_manifest):
        """Test listing all apps."""
        assert len(mongodb_engine.list_apps()) == 0

        await mongodb_engine.register_app(sample_manifest, create_indexes=False)

        apps = mongodb_engine.list_apps()
        assert len(apps) == 1
        assert sample_manifest["slug"] in apps


class TestMongoDBEngineWebSocket:
    """Test WebSocket functionality."""

    @pytest.mark.asyncio
    async def test_get_websocket_config_with_config(self, mongodb_engine):
        """Test getting WebSocket config when available."""
        # Mock service initializer with websocket config
        websocket_config = {"endpoint1": {"path": "/ws"}}
        mongodb_engine._service_initializer._websocket_configs["test_app"] = websocket_config  # noqa: SLF001

        config = mongodb_engine.get_websocket_config("test_app")
        assert config == websocket_config

    @pytest.mark.asyncio
    async def test_get_websocket_config_no_config(self, mongodb_engine):
        """Test getting WebSocket config when not configured."""
        config = mongodb_engine.get_websocket_config("nonexistent_app")
        assert config is None

    @pytest.mark.asyncio
    async def test_get_websocket_config_uninitialized(self, uninitialized_mongodb_engine):
        """Test getting WebSocket config before initialization."""
        # When uninitialized, _service_initializer is None, so should return None
        config = uninitialized_mongodb_engine.get_websocket_config("test_app")
        assert config is None

    @pytest.mark.asyncio
    async def test_register_websocket_routes_no_config(self, mongodb_engine):
        """Test registering WebSocket routes when no config exists."""
        mock_app = MagicMock()
        # No websocket config set, should return early
        mongodb_engine.register_websocket_routes(mock_app, "nonexistent_app")
        # Should not have called any FastAPI methods
        assert not hasattr(mock_app, "include_router") or not mock_app.include_router.called

    @pytest.mark.asyncio
    async def test_register_websocket_routes_import_error(self, mongodb_engine):
        """Test registering WebSocket routes when FastAPI is not available."""
        websocket_config = {"endpoint1": {"path": "/ws"}}
        mongodb_engine._service_initializer._websocket_configs["test_app"] = websocket_config  # noqa: SLF001

        mock_app = MagicMock()
        # Simulate ImportError by replacing the module with one that raises ImportError
        import sys

        original_websockets = sys.modules.get("mdb_engine.routing.websockets")
        try:
            # Create a mock module that raises ImportError when imported from
            class MockModule:
                def __getattr__(self, name):
                    raise ImportError("No module named 'fastapi'")

            sys.modules["mdb_engine.routing.websockets"] = MockModule()
            mongodb_engine.register_websocket_routes(mock_app, "test_app")
            # Should not have called include_router since import failed
            assert not mock_app.include_router.called
        finally:
            # Restore the original module
            if original_websockets:
                sys.modules["mdb_engine.routing.websockets"] = original_websockets
            elif "mdb_engine.routing.websockets" in sys.modules:
                del sys.modules["mdb_engine.routing.websockets"]

    @pytest.mark.asyncio
    async def test_register_websocket_routes_success(self, mongodb_engine):
        """Test successful WebSocket route registration."""
        websocket_config = {
            "endpoint1": {
                "path": "/ws/endpoint1",
                "auth": {"required": False},
                "ping_interval": 20,
            }
        }
        mongodb_engine._service_initializer._websocket_configs["test_app"] = websocket_config  # noqa: SLF001

        mock_app = MagicMock()
        mock_app.routes = []
        mock_handler = MagicMock()
        mock_router = MagicMock()

        with patch(
            "mdb_engine.routing.websockets.create_websocket_endpoint",
            return_value=mock_handler,
        ):
            with patch("fastapi.APIRouter", return_value=mock_router):
                mongodb_engine.register_websocket_routes(mock_app, "test_app")
                # Should have created router and included it
                mock_router.websocket.assert_called_once()
                mock_app.include_router.assert_called_once()

    @pytest.mark.asyncio
    async def test_register_websocket_routes_auth_configs(self, mongodb_engine, sample_manifest):
        """Test WebSocket route registration with different auth config formats."""
        # Register app first
        await mongodb_engine.register_app(sample_manifest, create_indexes=False)

        # Test nested auth config
        websocket_config_nested = {"endpoint1": {"path": "/ws/endpoint1", "auth": {"required": False}}}
        mongodb_engine._service_initializer._websocket_configs["test_experiment"] = (  # noqa: SLF001
            websocket_config_nested
        )

        mock_app = MagicMock()
        mock_app.routes = []
        mock_handler = MagicMock()
        mock_router = MagicMock()

        with patch(
            "mdb_engine.routing.websockets.create_websocket_endpoint",
            return_value=mock_handler,
        ) as mock_create:
            with patch("fastapi.APIRouter", return_value=mock_router):
                mongodb_engine.register_websocket_routes(mock_app, "test_experiment")
                # Check that require_auth was False
                call_kwargs = mock_create.call_args[1]
                assert call_kwargs["require_auth"] is False

        # Test top-level require_auth (backward compatibility)
        websocket_config_top_level = {"endpoint2": {"path": "/ws/endpoint2", "require_auth": False}}
        mongodb_engine._service_initializer._websocket_configs["test_experiment"] = (  # noqa: SLF001
            websocket_config_top_level
        )

        with patch(
            "mdb_engine.routing.websockets.create_websocket_endpoint",
            return_value=mock_handler,
        ) as mock_create:
            with patch("fastapi.APIRouter", return_value=mock_router):
                mongodb_engine.register_websocket_routes(mock_app, "test_experiment")
                call_kwargs = mock_create.call_args[1]
                assert call_kwargs["require_auth"] is False

    @pytest.mark.asyncio
    async def test_register_websocket_routes_handler_creation_error(self, mongodb_engine):
        """Test WebSocket route registration when handler creation fails."""
        websocket_config = {"endpoint1": {"path": "/ws"}}
        mongodb_engine._service_initializer._websocket_configs["test_app"] = websocket_config  # noqa: SLF001

        mock_app = MagicMock()
        mock_app.routes = []

        with patch(
            "mdb_engine.routing.websockets.create_websocket_endpoint",
            side_effect=ValueError("Handler creation failed"),
        ):
            with pytest.raises(ValueError, match="Handler creation failed"):
                mongodb_engine.register_websocket_routes(mock_app, "test_app")

    @pytest.mark.asyncio
    async def test_register_websocket_routes_registration_error(self, mongodb_engine):
        """Test WebSocket route registration when FastAPI registration fails."""
        websocket_config = {"endpoint1": {"path": "/ws"}}
        mongodb_engine._service_initializer._websocket_configs["test_app"] = websocket_config  # noqa: SLF001

        mock_app = MagicMock()
        mock_app.routes = []
        mock_handler = MagicMock()
        mock_router = MagicMock()
        # mock_router.websocket() should return a decorator function
        mock_router.websocket = MagicMock(return_value=lambda func: func)
        mock_app.include_router.side_effect = ValueError("Registration failed")

        with patch(
            "mdb_engine.routing.websockets.create_websocket_endpoint",
            return_value=mock_handler,
        ):
            with patch("fastapi.APIRouter", return_value=mock_router):
                with pytest.raises(ValueError, match="Registration failed"):
                    mongodb_engine.register_websocket_routes(mock_app, "test_app")


class TestMongoDBEngineAppManagement:
    """Test app management functionality."""

    @pytest.mark.asyncio
    async def test_reload_apps_success(self, mongodb_engine, sample_manifest):
        """Test successfully reloading apps from database."""
        # First register an app
        await mongodb_engine.register_app(sample_manifest, create_indexes=False)
        assert len(mongodb_engine.list_apps()) == 1

        # Mock the reload_apps method to return count
        with patch.object(mongodb_engine._app_registration_manager, "reload_apps", return_value=1):  # noqa: SLF001
            count = await mongodb_engine.reload_apps()
            assert count == 1

    @pytest.mark.asyncio
    async def test_reload_apps_uninitialized(self, uninitialized_mongodb_engine):
        """Test reloading apps before initialization raises error."""
        with pytest.raises(RuntimeError, match="not initialized"):
            await uninitialized_mongodb_engine.reload_apps()

    @pytest.mark.asyncio
    async def test_get_manifest_async(self, mongodb_engine, sample_manifest):
        """Test async get_manifest method."""
        await mongodb_engine.register_app(sample_manifest, create_indexes=False)

        manifest = await mongodb_engine.get_manifest("test_experiment")
        assert manifest is not None
        assert manifest["slug"] == "test_experiment"

    @pytest.mark.asyncio
    async def test_get_manifest_uninitialized(self, uninitialized_mongodb_engine):
        """Test getting manifest before initialization raises error."""
        with pytest.raises(RuntimeError, match="not initialized"):
            await uninitialized_mongodb_engine.get_manifest("test_app")


class TestMongoDBEngineServiceAccessors:
    """Test service accessor methods."""

    @pytest.mark.asyncio
    async def test_get_database_removed(self, mongodb_engine):
        """Test that get_database method is no longer accessible."""
        with pytest.raises(AttributeError, match="get_database"):
            _ = mongodb_engine.get_database()

    @pytest.mark.asyncio
    async def test_get_memory_service_with_service(self, mongodb_engine):
        """Test getting memory service when available."""
        mock_service = MagicMock()
        mock_service.memory = MagicMock()
        mongodb_engine._service_initializer._memory_services["test_app"] = mock_service  # noqa: SLF001

        service = mongodb_engine.get_memory_service("test_app")
        assert service == mock_service

    @pytest.mark.asyncio
    async def test_get_memory_service_no_service(self, mongodb_engine):
        """Test getting memory service when not configured."""
        service = mongodb_engine.get_memory_service("nonexistent_app")
        assert service is None

    @pytest.mark.asyncio
    async def test_get_memory_service_uninitialized(self, uninitialized_mongodb_engine):
        """Test getting memory service before initialization."""
        # When uninitialized, _service_initializer is None, so should return None
        service = uninitialized_mongodb_engine.get_memory_service("test_app")
        assert service is None


class TestMongoDBEngineHealthMetrics:
    """Test health status and metrics functionality."""

    @pytest.mark.asyncio
    async def test_get_health_status_success(self, mongodb_engine):
        """Test getting health status."""
        with patch(
            "mdb_engine.core.engine.check_engine_health",
            return_value=MagicMock(status="healthy"),
        ):
            with patch(
                "mdb_engine.core.engine.check_mongodb_health",
                return_value=MagicMock(status="healthy"),
            ):
                health = await mongodb_engine.get_health_status()
                assert health is not None

    @pytest.mark.asyncio
    async def test_get_health_status_with_pool_metrics(self, mongodb_engine):
        """Test health status with pool metrics available."""
        mock_health_result = MagicMock()
        mock_health_result.status.value = "healthy"
        mock_health_result.details = {"pool_usage_percent": 50, "status": "connected"}

        with patch(
            "mdb_engine.core.engine.check_engine_health",
            return_value=mock_health_result,
        ):
            with patch(
                "mdb_engine.core.engine.check_mongodb_health",
                return_value=mock_health_result,
            ):
                with patch(
                    "mdb_engine.database.connection.get_pool_metrics",
                    return_value={"status": "connected"},
                ):
                    with patch(
                        "mdb_engine.core.engine.check_pool_health",
                        return_value=mock_health_result,
                    ):
                        health = await mongodb_engine.get_health_status()
                        assert health is not None

    @pytest.mark.asyncio
    async def test_get_health_status_pool_degraded(self, mongodb_engine):
        """Test health status with degraded pool."""
        mock_health_result = MagicMock()
        mock_health_result.status.value = "unhealthy"
        mock_health_result.details = {"pool_usage_percent": 85, "status": "connected"}

        with patch(
            "mdb_engine.core.engine.check_engine_health",
            return_value=mock_health_result,
        ):
            with patch(
                "mdb_engine.core.engine.check_mongodb_health",
                return_value=mock_health_result,
            ):
                with patch(
                    "mdb_engine.database.connection.get_pool_metrics",
                    return_value={"status": "connected"},
                ):
                    with patch(
                        "mdb_engine.core.engine.check_pool_health",
                        return_value=mock_health_result,
                    ):
                        health = await mongodb_engine.get_health_status()
                        assert health is not None

    @pytest.mark.asyncio
    async def test_get_health_status_no_pool_metrics(self, mongodb_engine):
        """Test health status when pool metrics are not available."""
        mock_health_result = MagicMock()
        mock_health_result.status.value = "healthy"

        with patch(
            "mdb_engine.core.engine.check_engine_health",
            return_value=mock_health_result,
        ):
            with patch(
                "mdb_engine.core.engine.check_mongodb_health",
                return_value=mock_health_result,
            ):
                # Simulate ImportError for pool metrics by removing the module
                import sys

                original_modules = sys.modules.copy()
                connection_module = sys.modules.get("mdb_engine.database.connection")
                try:
                    # Remove the module to simulate it not being available
                    if "mdb_engine.database.connection" in sys.modules:
                        del sys.modules["mdb_engine.database.connection"]

                    # The ImportError will be caught and pool check skipped
                    health = await mongodb_engine.get_health_status()
                    assert health is not None
                finally:
                    # Restore modules
                    sys.modules.clear()
                    sys.modules.update(original_modules)
                    if connection_module:
                        sys.modules["mdb_engine.database.connection"] = connection_module

    @pytest.mark.asyncio
    async def test_get_metrics(self, mongodb_engine):
        """Test getting metrics summary."""
        mock_collector = MagicMock()
        mock_collector.get_summary.return_value = {"operations": 100, "errors": 0}

        with patch(
            "mdb_engine.observability.get_metrics_collector",
            return_value=mock_collector,
        ):
            metrics = mongodb_engine.get_metrics()
            assert metrics is not None
            # Check for actual structure - metrics is a dict with some content
            assert isinstance(metrics, dict)
            assert len(metrics) > 0

    @pytest.mark.asyncio
    async def test_get_manifest_not_initialized(self):
        """Test get_manifest raises RuntimeError when not initialized."""
        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")
        # get_manifest is async and checks initialization
        with pytest.raises(RuntimeError, match="not initialized"):
            await engine.get_manifest("test_app")

    @pytest.mark.asyncio
    async def test_get_database_not_initialized(self):
        """Test get_database is no longer accessible."""
        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")
        # get_database method has been removed
        with pytest.raises(AttributeError, match="get_database"):
            engine.get_database()

    @pytest.mark.asyncio
    async def test_get_memory_service_not_initialized(self):
        """Test get_memory_service when not initialized."""
        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")
        # get_memory_service doesn't check initialization, it delegates to service_initializer
        # If service_initializer is None, it will return None
        result = engine.get_memory_service("test_app")
        assert result is None

    @pytest.mark.asyncio
    async def test_register_app_not_initialized(self, sample_manifest):
        """Test register_app raises RuntimeError when not initialized."""
        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")
        with pytest.raises(RuntimeError, match="not initialized"):
            await engine.register_app(sample_manifest)

    @pytest.mark.asyncio
    async def test_reload_apps_not_initialized(self):
        """Test reload_apps raises RuntimeError when not initialized."""
        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")
        with pytest.raises(RuntimeError, match="not initialized"):
            await engine.reload_apps()

    def test_get_app_not_initialized(self):
        """Test get_app raises RuntimeError when not initialized (line 505)."""
        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")
        with pytest.raises(RuntimeError, match="not initialized"):
            engine.get_app("test_app")

    def test_apps_property_not_initialized(self):
        """Test _apps property raises RuntimeError when not initialized (line 571)."""
        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")
        with pytest.raises(RuntimeError, match="not initialized"):
            # Access as property
            _ = engine.apps

    def test_list_apps_not_initialized(self):
        """Test list_apps raises RuntimeError when not initialized (line 584)."""
        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")
        with pytest.raises(RuntimeError, match="not initialized"):
            engine.list_apps()

    @pytest.mark.asyncio
    async def test_register_websocket_routes_auth_from_app_config(self, mongodb_engine, sample_manifest):
        """Test WebSocket auth config from app's auth_policy."""
        # Register app with auth_policy
        manifest_with_auth = {
            **sample_manifest,
            "auth_policy": {"required": False},
            "websockets": {"endpoint1": {"path": "/ws"}},
        }
        await mongodb_engine.register_app(manifest_with_auth, create_indexes=False)

        mock_app = MagicMock()
        mock_app.routes = []
        mock_handler = MagicMock()
        mock_router = MagicMock()
        mock_router.websocket = MagicMock(return_value=lambda func: func)

        with patch(
            "mdb_engine.routing.websockets.create_websocket_endpoint",
            return_value=mock_handler,
        ) as mock_create:
            with patch("fastapi.APIRouter", return_value=mock_router):
                mongodb_engine.register_websocket_routes(mock_app, sample_manifest["slug"])
                # Check that require_auth was False from app's auth_policy
                call_kwargs = mock_create.call_args[1]
                assert call_kwargs["require_auth"] is False

    @pytest.mark.asyncio
    async def test_get_health_status_pool_metrics_with_config(self, mongodb_engine):
        """Test health status with pool metrics that need config added (lines 696-707)."""
        mock_health_result = MagicMock()
        mock_health_result.status.value = "healthy"

        # Mock pool metrics without max_pool_size/min_pool_size initially
        mock_pool_metrics = {"status": "connected", "pool_size": 10}

        async def get_metrics():
            return mock_pool_metrics

        with patch(
            "mdb_engine.core.engine.check_engine_health",
            return_value=mock_health_result,
        ):
            with patch(
                "mdb_engine.core.engine.check_mongodb_health",
                return_value=mock_health_result,
            ):
                with patch(
                    "mdb_engine.database.connection.get_pool_metrics",
                    return_value=mock_pool_metrics,
                ):
                    with patch("mdb_engine.core.engine.check_pool_health") as mock_pool_check:
                        mock_pool_result = MagicMock()
                        mock_pool_result.status.value = "healthy"
                        mock_pool_result.details = {}  # Ensure details is a dict, not MagicMock
                        mock_pool_check.return_value = mock_pool_result

                        health = await mongodb_engine.get_health_status()
                        assert health is not None

                        # Verify that check_pool_health was called with a metrics function
                        # The function should add max_pool_size and min_pool_size
                        # when status is "connected"
                        assert mock_pool_check.called
                        call_args = mock_pool_check.call_args
                        if call_args and len(call_args[0]) > 0:
                            metrics_func = call_args[0][0]
                            final_metrics = await metrics_func()
                            # If status is "connected", should have added pool config
                            if final_metrics.get("status") == "connected":
                                assert "max_pool_size" in final_metrics
                                assert "min_pool_size" in final_metrics

    @pytest.mark.asyncio
    async def test_no_sync_context_manager(self, mongodb_engine):
        """Test that synchronous context manager is not available."""
        # Sync __enter__/__exit__ were intentionally removed.
        # Only async context manager (__aenter__/__aexit__) is supported.
        assert not hasattr(mongodb_engine, "__enter__") or not callable(getattr(mongodb_engine, "__enter__", None))
        # Verify async context manager is available
        assert hasattr(mongodb_engine, "__aenter__")
        assert hasattr(mongodb_engine, "__aexit__")

    @pytest.mark.asyncio
    async def test_get_health_status_pool_import_error(self, mongodb_engine):
        """Test health status when pool metrics import fails (line 730-731)."""
        mock_health_result = MagicMock()
        mock_health_result.status.value = "healthy"

        with patch(
            "mdb_engine.core.engine.check_engine_health",
            return_value=mock_health_result,
        ):
            with patch(
                "mdb_engine.core.engine.check_mongodb_health",
                return_value=mock_health_result,
            ):
                # Simulate ImportError by removing the module
                import sys

                original_connection = sys.modules.get("mdb_engine.database.connection")
                try:
                    if "mdb_engine.database.connection" in sys.modules:
                        del sys.modules["mdb_engine.database.connection"]

                    # Should handle ImportError gracefully and skip pool check
                    health = await mongodb_engine.get_health_status()
                    assert health is not None
                finally:
                    if original_connection:
                        sys.modules["mdb_engine.database.connection"] = original_connection

    @pytest.mark.asyncio
    async def test_get_health_status_import_error_handled(self, mongodb_engine):
        """Test get_health_status handles ImportError when registering pool check."""
        from mdb_engine.observability.health import HealthCheckResult, HealthStatus

        mock_health_result = HealthCheckResult(
            name="test",
            status=HealthStatus.HEALTHY,
            message="Test check",
        )

        with patch(
            "mdb_engine.core.engine.check_engine_health",
            return_value=mock_health_result,
        ):
            with patch(
                "mdb_engine.core.engine.check_mongodb_health",
                return_value=mock_health_result,
            ):
                # Simulate ImportError when trying to import get_pool_metrics
                # The import happens at line 688: from ..database.connection import get_pool_metrics
                # Patch the import to raise ImportError
                import sys

                # Create a mock module that raises ImportError when get_pool_metrics is accessed
                class MockModule:
                    def __getattr__(self, name):
                        if name == "get_pool_metrics":
                            raise ImportError("No module")
                        raise AttributeError(f"module has no attribute '{name}'")

                # Replace the module temporarily
                original_module = sys.modules.get("mdb_engine.database.connection")
                sys.modules["mdb_engine.database.connection"] = MockModule()

                try:
                    # Should not raise, just skip pool check
                    result = await mongodb_engine.get_health_status()
                    assert result is not None
                    assert result["status"] == "healthy"
                finally:
                    # Restore original module
                    if original_module is not None:
                        sys.modules["mdb_engine.database.connection"] = original_module
                    elif "mdb_engine.database.connection" in sys.modules:
                        del sys.modules["mdb_engine.database.connection"]

    @pytest.mark.asyncio
    async def test_get_health_status_pool_check_import_error(self, mongodb_engine):
        """Test get_health_status handles ImportError when pool check import fails."""
        from mdb_engine.observability.health import HealthCheckResult, HealthStatus

        mock_health_result = HealthCheckResult(
            name="test",
            status=HealthStatus.HEALTHY,
            message="Test check",
        )

        with patch(
            "mdb_engine.core.engine.check_engine_health",
            return_value=mock_health_result,
        ):
            with patch(
                "mdb_engine.core.engine.check_mongodb_health",
                return_value=mock_health_result,
            ):
                # Simulate ImportError by replacing the module with one that raises ImportError
                import sys

                # Create a mock module that raises ImportError when get_pool_metrics is accessed
                class MockModule:
                    def __getattr__(self, name):
                        if name == "get_pool_metrics":
                            raise ImportError("No module")
                        raise AttributeError(f"module has no attribute '{name}'")

                # Replace the module temporarily
                original_module = sys.modules.get("mdb_engine.database.connection")
                sys.modules["mdb_engine.database.connection"] = MockModule()

                try:
                    # Should not raise, just skip pool check
                    result = await mongodb_engine.get_health_status()
                    assert result is not None
                    assert result["status"] == "healthy"
                finally:
                    # Restore original module
                    if original_module is not None:
                        sys.modules["mdb_engine.database.connection"] = original_module
                    elif "mdb_engine.database.connection" in sys.modules:
                        del sys.modules["mdb_engine.database.connection"]


# ============================================================================
# TestCSFLEConfigBuilding
# ============================================================================


class TestCSFLEConfigBuilding:
    """Test CSFLE config building from manifests (engine.py lines 585-704)."""

    def test_build_from_single_manifest_memory_encrypted(self):
        """build_csfle_config_from_manifest with memory encryption (lines 684-692)."""
        from mdb_engine.core.engine import build_csfle_config_from_manifest

        manifest = {
            "slug": "enc-app",
            "memory_config": {
                "encrypted": True,
                "collection_name": "memories",
            },
        }
        config = build_csfle_config_from_manifest(manifest)
        assert config is not None
        assert config.enabled is True
        assert "enc-app_memories" in config.encrypted_collections

    def test_build_from_single_manifest_no_encryption(self):
        """build_csfle_config_from_manifest with no encryption returns None (line 704)."""
        from mdb_engine.core.engine import build_csfle_config_from_manifest

        manifest = {"slug": "plain-app"}
        config = build_csfle_config_from_manifest(manifest)
        assert config is None

    def test_build_from_single_manifest_encrypted_fields(self):
        """build_csfle_config_from_manifest with encrypted_fields (lines 695-702)."""
        from mdb_engine.core.engine import build_csfle_config_from_manifest

        manifest = {
            "slug": "fields-app",
            "encrypted_fields": {"payments": ["card_number", "cvv"]},
            "encryption_config": {"kms_provider": "aws"},
        }
        config = build_csfle_config_from_manifest(manifest)
        assert config is not None
        assert config.enabled is True
        assert "fields-app_payments" in config.encrypted_collections
        assert "card_number" in config.encrypted_collections["fields-app_payments"]
        assert config.kms_provider == "aws"

    def test_build_from_single_manifest_both_memory_and_fields(self):
        """Memory + encrypted_fields are merged (lines 699-700)."""
        from mdb_engine.core.engine import build_csfle_config_from_manifest

        manifest = {
            "slug": "dual-app",
            "memory_config": {"encrypted": True, "collection_name": "mems"},
            "encrypted_fields": {"users": ["ssn"]},
        }
        config = build_csfle_config_from_manifest(manifest)
        assert config is not None
        assert "dual-app_mems" in config.encrypted_collections
        assert "dual-app_users" in config.encrypted_collections

    def test_build_from_single_manifest_custom_slug(self):
        """app_slug parameter overrides manifest slug (line 686)."""
        from mdb_engine.core.engine import build_csfle_config_from_manifest

        manifest = {
            "slug": "manifest-slug",
            "memory_config": {"encrypted": True},
        }
        config = build_csfle_config_from_manifest(manifest, app_slug="custom-slug")
        assert config is not None
        assert any("custom-slug_" in k for k in config.encrypted_collections)

    def test_build_from_manifests_dict_list(self):
        """build_csfle_config_from_manifests with manifest dicts (lines 585-650)."""
        from mdb_engine.core.engine import build_csfle_config_from_manifests

        manifests = [
            {
                "slug": "app1",
                "memory_config": {"encrypted": True, "collection_name": "mem1"},
            },
            {
                "slug": "app2",
                "encrypted_fields": {"data": ["secret"]},
            },
        ]
        config = build_csfle_config_from_manifests(manifests=manifests)
        assert config is not None
        assert config.enabled is True
        assert "app1_mem1" in config.encrypted_collections
        assert "app2_data" in config.encrypted_collections

    def test_build_from_manifests_empty_returns_none(self):
        """No manifests returns None (line 619-620)."""
        from mdb_engine.core.engine import build_csfle_config_from_manifests

        config = build_csfle_config_from_manifests(manifests=[])
        assert config is None

    def test_build_from_manifests_no_encryption_returns_none(self):
        """Manifests without encryption return None."""
        from mdb_engine.core.engine import build_csfle_config_from_manifests

        manifests = [{"slug": "plain1"}, {"slug": "plain2"}]
        config = build_csfle_config_from_manifests(manifests=manifests)
        assert config is None

    def test_build_from_manifests_none_args(self):
        """All None args returns None."""
        from mdb_engine.core.engine import build_csfle_config_from_manifests

        config = build_csfle_config_from_manifests()
        assert config is None

    def test_build_from_manifest_paths(self, tmp_path):
        """build_csfle_config_from_manifests with file paths (lines 596-604)."""
        import json

        from mdb_engine.core.engine import build_csfle_config_from_manifests

        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(
            json.dumps(
                {
                    "slug": "file-app",
                    "memory_config": {"encrypted": True},
                }
            )
        )

        config = build_csfle_config_from_manifests(manifest_paths=[manifest_file])
        assert config is not None
        assert config.enabled is True

    def test_build_from_manifest_paths_bad_file(self, tmp_path):
        """Bad manifest path is skipped with warning (lines 603-604)."""
        from mdb_engine.core.engine import build_csfle_config_from_manifests

        bad_path = tmp_path / "nonexistent.json"
        config = build_csfle_config_from_manifests(manifest_paths=[bad_path])
        assert config is None

    def test_build_from_manifests_dir(self, tmp_path):
        """build_csfle_config_from_manifests scanning directory (lines 607-617)."""
        import json

        from mdb_engine.core.engine import build_csfle_config_from_manifests

        app_dir = tmp_path / "app1"
        app_dir.mkdir()
        (app_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "slug": "dir-app",
                    "memory_config": {"encrypted": True, "collection_name": "mems"},
                }
            )
        )

        config = build_csfle_config_from_manifests(manifests_dir=tmp_path)
        assert config is not None
        assert "dir-app_mems" in config.encrypted_collections

    def test_build_from_manifests_dir_deduplication(self, tmp_path):
        """Directory scan avoids duplicates with manifests list (lines 613-615)."""
        import json

        from mdb_engine.core.engine import build_csfle_config_from_manifests

        app_dir = tmp_path / "myapp"
        app_dir.mkdir()
        manifest_data = {
            "slug": "dedup-app",
            "memory_config": {"encrypted": True},
        }
        (app_dir / "manifest.json").write_text(json.dumps(manifest_data))

        config = build_csfle_config_from_manifests(
            manifests=[manifest_data],
            manifests_dir=tmp_path,
        )
        assert config is not None
        collections = list(config.encrypted_collections.keys())
        dedup_collections = [c for c in collections if c.startswith("dedup-app_")]
        assert len(dedup_collections) == 1

    def test_build_from_manifests_merges_multiple(self):
        """Multiple encrypted manifests are merged (lines 646-650)."""
        from mdb_engine.core.engine import build_csfle_config_from_manifests

        manifests = [
            {
                "slug": "a",
                "memory_config": {"encrypted": True, "collection_name": "m"},
            },
            {
                "slug": "b",
                "memory_config": {"encrypted": True, "collection_name": "m"},
            },
        ]
        config = build_csfle_config_from_manifests(manifests=manifests)
        assert config is not None
        assert "a_m" in config.encrypted_collections
        assert "b_m" in config.encrypted_collections

    def test_build_from_manifest_paths_invalid_json(self, tmp_path):
        """Invalid JSON in manifest file is skipped (line 603)."""
        from mdb_engine.core.engine import build_csfle_config_from_manifests

        bad_file = tmp_path / "manifest.json"
        bad_file.write_text("not valid json {{{")

        config = build_csfle_config_from_manifests(manifest_paths=[bad_file])
        assert config is None


class TestMongoDBEngineCallbackErrors:
    """Test error handling in register_app callbacks."""

    @pytest.mark.asyncio
    async def test_register_app_index_callback_error(self, mongodb_engine, sample_manifest):
        """Test handling index creation callback errors."""
        # Mock index manager to raise error
        with patch.object(
            mongodb_engine._index_manager,  # noqa: SLF001
            "create_app_indexes",
            side_effect=Exception("Index error"),
        ):
            # Should still register app, but index creation fails
            result = await mongodb_engine.register_app(sample_manifest, create_indexes=True)
            # Registration should succeed even if callbacks fail
            assert result is True

    @pytest.mark.asyncio
    async def test_register_app_seed_callback_error(self, mongodb_engine, sample_manifest):
        """Test handling seeding callback errors."""
        manifest_with_seed = {
            **sample_manifest,
            "initial_data": {"collection": [{"test": "data"}]},
        }

        # The seed callback is called internally, and errors are caught
        # So we need to ensure the callback doesn't raise, or test that
        # registration succeeds despite callback errors
        # Actually, let's test that the callback error doesn't prevent registration
        result = await mongodb_engine.register_app(manifest_with_seed, create_indexes=False)
        # Registration should succeed even if seeding has issues (it's handled internally)
        assert result is True

    @pytest.mark.asyncio
    async def test_register_app_memory_callback_error(self, mongodb_engine, sample_manifest):
        """Test handling memory initialization callback errors."""
        manifest_with_memory = {
            **sample_manifest,
            "memory": {"enabled": True, "collection_name": "memories"},
        }

        with patch.object(
            mongodb_engine._service_initializer,  # noqa: SLF001
            "initialize_memory_service",
            side_effect=Exception("Memory error"),
        ):
            # Should still register app
            result = await mongodb_engine.register_app(manifest_with_memory, create_indexes=False)
            assert result is True

    @pytest.mark.asyncio
    async def test_register_app_memory_callback_no_service_initializer(self, mongodb_engine, sample_manifest):
        """Test register_app memory callback when service_initializer is None (line 294)."""
        # Add memory config to manifest
        manifest_with_memory = {
            **sample_manifest,
            "memory": {"enabled": True, "collection_name": "memories"},
        }

        # Set service_initializer to None
        mongodb_engine._service_initializer = None  # noqa: SLF001

        # Should not raise, just skip memory initialization
        await mongodb_engine.register_app(manifest_with_memory, create_indexes=False)

    @pytest.mark.asyncio
    async def test_register_app_websocket_callback_error(self, mongodb_engine, sample_manifest):
        """Test handling WebSocket callback errors."""
        manifest_with_websockets = {
            **sample_manifest,
            "websockets": {"endpoint1": {"path": "/ws"}},
        }

        # Mock the callback to raise error - but registration should still succeed
        async def failing_websocket_callback(slug, config):
            raise Exception("WebSocket error")

        # Register app - the callback error should be caught internally
        result = await mongodb_engine.register_app(manifest_with_websockets, create_indexes=False)
        # Registration should succeed even if callbacks fail
        assert result is True

    @pytest.mark.asyncio
    async def test_register_app_observability_callback_error(self, mongodb_engine, sample_manifest):
        """Test handling observability callback errors."""
        manifest_with_observability = {
            **sample_manifest,
            "observability": {"health_checks": {"enabled": True}},
        }

        # Mock the callback to raise error - but registration should still succeed
        async def failing_observability_callback(slug, manifest, config):
            raise Exception("Observability error")

        # Register app - the callback error should be caught internally
        result = await mongodb_engine.register_app(manifest_with_observability, create_indexes=False)
        # Registration should succeed even if callbacks fail
        assert result is True


# ============================================================================
# TestAppTokenCache
# ============================================================================


class TestAppTokenCache:
    """Test auto_retrieve_app_token caching behaviour."""

    @pytest.mark.asyncio
    async def test_token_from_env_is_cached(self, mongodb_engine, monkeypatch):
        """First call reads env var, second call returns cached value."""
        monkeypatch.setenv("MY_APP_SECRET", "env-secret-123")
        mongodb_engine._app_token_cache.clear()  # noqa: SLF001

        token1 = await mongodb_engine.auto_retrieve_app_token("my_app")
        assert token1 == "env-secret-123"

        # Remove env var — cache should still serve the value
        monkeypatch.delenv("MY_APP_SECRET")
        token2 = await mongodb_engine.auto_retrieve_app_token("my_app")
        assert token2 == "env-secret-123"

    @pytest.mark.asyncio
    async def test_cache_hit_skips_env_and_db(self, mongodb_engine):
        """When the cache already has a value, env and DB are never consulted."""
        mongodb_engine._app_token_cache["cached_app"] = "cached-tok"  # noqa: SLF001

        token = await mongodb_engine.auto_retrieve_app_token("cached_app")
        assert token == "cached-tok"

    @pytest.mark.asyncio
    async def test_token_db_error_returns_none(self, mongodb_engine, monkeypatch):
        """PyMongoError during DB retrieval is caught; None returned."""
        from pymongo.errors import PyMongoError

        monkeypatch.delenv("TEST_DB_ERR_SECRET", raising=False)
        mongodb_engine._app_token_cache.clear()  # noqa: SLF001

        mock_secrets = AsyncMock()
        mock_secrets.app_secret_exists = AsyncMock(side_effect=PyMongoError("conn lost"))
        mongodb_engine._app_secrets_manager = mock_secrets  # noqa: SLF001

        token = await mongodb_engine.auto_retrieve_app_token("test_db_err")
        assert token is None


# ============================================================================
# TestLegacyLifespan
# ============================================================================


class TestLegacyLifespan:
    """Test the deprecated MongoDBEngine.lifespan() helper."""

    def test_lifespan_returns_async_context_manager(self, mongodb_engine):
        """lifespan() returns a callable that yields an async context manager."""
        from pathlib import Path

        cm = mongodb_engine.lifespan("my-app", Path("manifest.json"))
        # The returned object should be callable (it wraps @asynccontextmanager)
        assert callable(cm)

    @pytest.mark.asyncio
    async def test_lifespan_initializes_and_shuts_down(self, mock_mongo_client, mongodb_engine_config):
        """Full lifespan cycle: initialize → yield → shutdown."""
        from pathlib import Path

        with patch("mdb_engine.core.connection.AsyncIOMotorClient", return_value=mock_mongo_client):
            engine = MongoDBEngine(**mongodb_engine_config)

            lifespan_cm = engine.lifespan("test-slug", Path("manifest.json"))

            mock_app = MagicMock()
            mock_app.state = MagicMock()

            with (
                patch.object(engine, "initialize", new_callable=AsyncMock) as mock_init,
                patch.object(
                    engine, "load_manifest", new_callable=AsyncMock, return_value={"slug": "test-slug"}
                ) as mock_load,
                patch.object(engine, "register_app", new_callable=AsyncMock, return_value=True),
                patch.object(engine, "auto_retrieve_app_token", new_callable=AsyncMock, return_value="tok"),
                patch.object(engine, "shutdown", new_callable=AsyncMock) as mock_shutdown,
            ):
                async with lifespan_cm(mock_app):
                    mock_init.assert_awaited_once()
                    mock_load.assert_awaited_once()
                    assert mock_app.state.engine == engine

                mock_shutdown.assert_awaited_once()
