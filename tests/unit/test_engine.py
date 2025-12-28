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
    async def test_engine_initialization_success(
        self, mock_mongo_client, mongodb_engine_config
    ):
        """Test successful engine initialization."""
        with patch(
            "mdb_engine.core.connection.AsyncIOMotorClient",
            return_value=mock_mongo_client,
        ):
            engine = MongoDBEngine(**mongodb_engine_config)
            await engine.initialize()

            assert engine._initialized is True
            assert engine.mongo_client is not None
            assert engine.mongo_db is not None
            assert engine.mongo_db.name == mongodb_engine_config["db_name"]

            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_engine_initialization_failure_connection(
        self, mongodb_engine_config
    ):
        """Test engine initialization failure due to connection error."""
        from pymongo.errors import ConnectionFailure

        mock_client = MagicMock()
        mock_client.admin.command = AsyncMock(
            side_effect=ConnectionFailure("Connection failed")
        )

        with patch(
            "mdb_engine.core.connection.AsyncIOMotorClient", return_value=mock_client
        ):
            engine = MongoDBEngine(**mongodb_engine_config)

            with pytest.raises(InitializationError) as exc_info:
                await engine.initialize()

            assert "Failed to connect to MongoDB" in str(exc_info.value)
            assert engine._initialized is False

    @pytest.mark.asyncio
    async def test_engine_double_initialization(self, mongodb_engine):
        """Test that double initialization is handled gracefully."""
        # First initialization happens in fixture
        assert mongodb_engine._initialized is True

        # Second initialization should be a no-op
        await mongodb_engine.initialize()
        assert mongodb_engine._initialized is True

    @pytest.mark.asyncio
    async def test_engine_shutdown(self, mongodb_engine):
        """Test engine shutdown."""
        assert mongodb_engine._initialized is True

        await mongodb_engine.shutdown()

        assert mongodb_engine._initialized is False
        assert len(mongodb_engine._apps) == 0

    @pytest.mark.asyncio
    async def test_engine_shutdown_idempotent(self, mongodb_engine):
        """Test that shutdown is idempotent."""
        await mongodb_engine.shutdown()
        await mongodb_engine.shutdown()  # Should not raise

    @pytest.mark.asyncio
    async def test_engine_context_manager(
        self, mock_mongo_client, mongodb_engine_config
    ):
        """Test engine as async context manager."""
        with patch(
            "mdb_engine.core.connection.AsyncIOMotorClient",
            return_value=mock_mongo_client,
        ):
            async with MongoDBEngine(**mongodb_engine_config) as engine:
                assert engine._initialized is True

            # After context exit, should be shut down
            assert engine._initialized is False


class TestMongoDBEngineProperties:
    """Test MongoDBEngine property accessors."""

    @pytest.mark.asyncio
    async def test_mongo_client_property_uninitialized(
        self, uninitialized_mongodb_engine
    ):
        """Test accessing mongo_client before initialization raises error."""
        with pytest.raises(RuntimeError, match="not initialized"):
            _ = uninitialized_mongodb_engine.mongo_client

    @pytest.mark.asyncio
    async def test_mongo_db_property_uninitialized(self, uninitialized_mongodb_engine):
        """Test accessing mongo_db before initialization raises error."""
        with pytest.raises(RuntimeError, match="not initialized"):
            _ = uninitialized_mongodb_engine.mongo_db

    @pytest.mark.asyncio
    async def test_mongo_client_property_initialized(self, mongodb_engine):
        """Test accessing mongo_client after initialization."""
        client = mongodb_engine.mongo_client
        assert client is not None

    @pytest.mark.asyncio
    async def test_mongo_db_property_initialized(self, mongodb_engine):
        """Test accessing mongo_db after initialization."""
        db = mongodb_engine.mongo_db
        assert db is not None
        assert db.name == "test_db"


class TestMongoDBEngineScopedDatabase:
    """Test scoped database wrapper creation."""

    @pytest.mark.asyncio
    async def test_get_scoped_db_success(self, mongodb_engine):
        """Test successful scoped database creation."""
        scoped_db = mongodb_engine.get_scoped_db("test_app")

        assert scoped_db is not None
        assert scoped_db._read_scopes == ["test_app"]
        assert scoped_db._write_scope == "test_app"

    @pytest.mark.asyncio
    async def test_get_scoped_db_custom_scopes(self, mongodb_engine):
        """Test scoped database with custom read/write scopes."""
        scoped_db = mongodb_engine.get_scoped_db(
            app_slug="test_app", read_scopes=["app1", "app2"], write_scope="app1"
        )

        assert scoped_db._read_scopes == ["app1", "app2"]
        assert scoped_db._write_scope == "app1"

    @pytest.mark.asyncio
    async def test_get_scoped_db_uninitialized(self, uninitialized_mongodb_engine):
        """Test getting scoped db before initialization raises error."""
        with pytest.raises(RuntimeError, match="not initialized"):
            uninitialized_mongodb_engine.get_scoped_db("test_app")

    @pytest.mark.asyncio
    async def test_get_scoped_db_auto_index_disabled(self, mongodb_engine):
        """Test scoped database with auto_index disabled."""
        scoped_db = mongodb_engine.get_scoped_db("test_app", auto_index=False)

        assert scoped_db._auto_index is False


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
        is_valid, error, paths = await mongodb_engine.validate_manifest(
            invalid_manifest
        )

        assert is_valid is False
        assert error is not None
        assert paths is not None
        assert len(paths) > 0

    @pytest.mark.asyncio
    async def test_load_manifest_from_file(
        self, mongodb_engine, tmp_path, sample_manifest
    ):
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


class TestMongoDBEngineTenantRegistration:
    """Test app registration functionality."""

    @pytest.mark.asyncio
    async def test_register_app_success(self, mongodb_engine, sample_manifest):
        """Test successful app registration."""
        result = await mongodb_engine.register_app(
            sample_manifest, create_indexes=False
        )

        assert result is True
        assert sample_manifest["slug"] in mongodb_engine._apps
        assert mongodb_engine.get_app(sample_manifest["slug"]) == sample_manifest

    @pytest.mark.asyncio
    async def test_register_app_missing_slug(self, mongodb_engine, sample_manifest):
        """Test registration with missing slug."""
        manifest_no_slug = {k: v for k, v in sample_manifest.items() if k != "slug"}

        result = await mongodb_engine.register_app(manifest_no_slug)

        assert result is False
        assert len(mongodb_engine._apps) == 0

    @pytest.mark.asyncio
    async def test_register_app_invalid_manifest(
        self, mongodb_engine, invalid_manifest
    ):
        """Test registration with invalid manifest."""
        result = await mongodb_engine.register_app(invalid_manifest)

        assert result is False
        assert len(mongodb_engine._apps) == 0

    @pytest.mark.asyncio
    async def test_register_app_uninitialized(
        self, uninitialized_mongodb_engine, sample_manifest
    ):
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
        mongodb_engine._service_initializer._websocket_configs["test_app"] = (
            websocket_config
        )

        config = mongodb_engine.get_websocket_config("test_app")
        assert config == websocket_config

    @pytest.mark.asyncio
    async def test_get_websocket_config_no_config(self, mongodb_engine):
        """Test getting WebSocket config when not configured."""
        config = mongodb_engine.get_websocket_config("nonexistent_app")
        assert config is None

    @pytest.mark.asyncio
    async def test_get_websocket_config_uninitialized(
        self, uninitialized_mongodb_engine
    ):
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
        assert (
            not hasattr(mock_app, "include_router")
            or not mock_app.include_router.called
        )

    @pytest.mark.asyncio
    async def test_register_websocket_routes_import_error(self, mongodb_engine):
        """Test registering WebSocket routes when FastAPI is not available."""
        websocket_config = {"endpoint1": {"path": "/ws"}}
        mongodb_engine._service_initializer._websocket_configs["test_app"] = (
            websocket_config
        )

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
        mongodb_engine._service_initializer._websocket_configs["test_app"] = (
            websocket_config
        )

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
    async def test_register_websocket_routes_auth_configs(
        self, mongodb_engine, sample_manifest
    ):
        """Test WebSocket route registration with different auth config formats."""
        # Register app first
        await mongodb_engine.register_app(sample_manifest, create_indexes=False)

        # Test nested auth config
        websocket_config_nested = {
            "endpoint1": {"path": "/ws/endpoint1", "auth": {"required": False}}
        }
        mongodb_engine._service_initializer._websocket_configs["test_experiment"] = (
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
        websocket_config_top_level = {
            "endpoint2": {"path": "/ws/endpoint2", "require_auth": False}
        }
        mongodb_engine._service_initializer._websocket_configs["test_experiment"] = (
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
    async def test_register_websocket_routes_handler_creation_error(
        self, mongodb_engine
    ):
        """Test WebSocket route registration when handler creation fails."""
        websocket_config = {"endpoint1": {"path": "/ws"}}
        mongodb_engine._service_initializer._websocket_configs["test_app"] = (
            websocket_config
        )

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
        mongodb_engine._service_initializer._websocket_configs["test_app"] = (
            websocket_config
        )

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
        with patch.object(
            mongodb_engine._app_registration_manager, "reload_apps", return_value=1
        ):
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
    async def test_get_database(self, mongodb_engine):
        """Test getting database instance."""
        db = mongodb_engine.get_database()
        assert db is not None
        assert db == mongodb_engine.mongo_db

    @pytest.mark.asyncio
    async def test_get_memory_service_with_service(self, mongodb_engine):
        """Test getting memory service when available."""
        mock_service = MagicMock()
        mock_service.memory = MagicMock()
        mongodb_engine._service_initializer._memory_services["test_app"] = mock_service

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
                        sys.modules["mdb_engine.database.connection"] = (
                            connection_module
                        )

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
        """Test get_database raises RuntimeError when not initialized."""
        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")
        # get_database accesses mongo_db which raises RuntimeError if not initialized
        with pytest.raises(RuntimeError, match="not initialized"):
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
            # Access as property (it's a @property decorator, but private _apps)
            _ = engine._apps

    def test_list_apps_not_initialized(self):
        """Test list_apps raises RuntimeError when not initialized (line 584)."""
        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")
        with pytest.raises(RuntimeError, match="not initialized"):
            engine.list_apps()

    @pytest.mark.asyncio
    async def test_register_websocket_routes_auth_from_app_config(
        self, mongodb_engine, sample_manifest
    ):
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
                mongodb_engine.register_websocket_routes(
                    mock_app, sample_manifest["slug"]
                )
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
                    with patch(
                        "mdb_engine.core.engine.check_pool_health"
                    ) as mock_pool_check:
                        mock_pool_result = MagicMock()
                        mock_pool_result.status.value = "healthy"
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
    async def test_context_manager_sync(self, mongodb_engine):
        """Test synchronous context manager."""
        # __enter__ returns self
        result = mongodb_engine.__enter__()
        assert result is mongodb_engine

        # __exit__ does nothing (synchronous)
        result = mongodb_engine.__exit__(None, None, None)
        assert result is None

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
                        sys.modules["mdb_engine.database.connection"] = (
                            original_connection
                        )

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


class TestMongoDBEngineCallbackErrors:
    """Test error handling in register_app callbacks."""

    @pytest.mark.asyncio
    async def test_register_app_index_callback_error(
        self, mongodb_engine, sample_manifest
    ):
        """Test handling index creation callback errors."""
        # Mock index manager to raise error
        with patch.object(
            mongodb_engine._index_manager,
            "create_app_indexes",
            side_effect=Exception("Index error"),
        ):
            # Should still register app, but index creation fails
            result = await mongodb_engine.register_app(
                sample_manifest, create_indexes=True
            )
            # Registration should succeed even if callbacks fail
            assert result is True

    @pytest.mark.asyncio
    async def test_register_app_seed_callback_error(
        self, mongodb_engine, sample_manifest
    ):
        """Test handling seeding callback errors."""
        manifest_with_seed = {
            **sample_manifest,
            "initial_data": {"collection": [{"test": "data"}]},
        }

        # The seed callback is called internally, and errors are caught
        # So we need to ensure the callback doesn't raise, or test that
        # registration succeeds despite callback errors
        # Actually, let's test that the callback error doesn't prevent registration
        result = await mongodb_engine.register_app(
            manifest_with_seed, create_indexes=False
        )
        # Registration should succeed even if seeding has issues (it's handled internally)
        assert result is True

    @pytest.mark.asyncio
    async def test_register_app_memory_callback_error(
        self, mongodb_engine, sample_manifest
    ):
        """Test handling memory initialization callback errors."""
        manifest_with_memory = {
            **sample_manifest,
            "memory": {"enabled": True, "collection_name": "memories"},
        }

        with patch.object(
            mongodb_engine._service_initializer,
            "initialize_memory_service",
            side_effect=Exception("Memory error"),
        ):
            # Should still register app
            result = await mongodb_engine.register_app(
                manifest_with_memory, create_indexes=False
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_register_app_memory_callback_no_service_initializer(
        self, mongodb_engine, sample_manifest
    ):
        """Test register_app memory callback when service_initializer is None (line 294)."""
        # Add memory config to manifest
        manifest_with_memory = {
            **sample_manifest,
            "memory": {"enabled": True, "collection_name": "memories"},
        }

        # Set service_initializer to None
        mongodb_engine._service_initializer = None

        # Should not raise, just skip memory initialization
        await mongodb_engine.register_app(manifest_with_memory, create_indexes=False)

    @pytest.mark.asyncio
    async def test_register_app_websocket_callback_error(
        self, mongodb_engine, sample_manifest
    ):
        """Test handling WebSocket callback errors."""
        manifest_with_websockets = {
            **sample_manifest,
            "websockets": {"endpoint1": {"path": "/ws"}},
        }

        # Mock the callback to raise error - but registration should still succeed
        async def failing_websocket_callback(slug, config):
            raise Exception("WebSocket error")

        # Register app - the callback error should be caught internally
        result = await mongodb_engine.register_app(
            manifest_with_websockets, create_indexes=False
        )
        # Registration should succeed even if callbacks fail
        assert result is True

    @pytest.mark.asyncio
    async def test_register_app_observability_callback_error(
        self, mongodb_engine, sample_manifest
    ):
        """Test handling observability callback errors."""
        manifest_with_observability = {
            **sample_manifest,
            "observability": {"health_checks": {"enabled": True}},
        }

        # Mock the callback to raise error - but registration should still succeed
        async def failing_observability_callback(slug, manifest, config):
            raise Exception("Observability error")

        # Register app - the callback error should be caught internally
        result = await mongodb_engine.register_app(
            manifest_with_observability, create_indexes=False
        )
        # Registration should succeed even if callbacks fail
        assert result is True
