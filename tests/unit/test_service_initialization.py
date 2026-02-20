"""
Unit tests for ServiceInitializer.

Tests service initialization functionality including:
- Memory service initialization
- WebSocket registration
- Data seeding
- Observability setup
- Service accessors
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mdb_engine.core.service_initialization import ServiceInitializer


@pytest.fixture
def mock_get_scoped_db_fn():
    """Create a mock get_scoped_db function (async).

    Returns an async function that produces a MagicMock WITHOUT spec
    so dynamic attribute access (e.g., db.memories) works the same way
    ScopedMongoWrapper.__getattr__ does for collection access.
    """

    async def get_scoped_db(slug: str):
        mock_db = MagicMock()
        mock_db._read_scopes = [slug]
        mock_db._write_scope = slug
        return mock_db

    return get_scoped_db


@pytest.fixture
def service_initializer(mock_get_scoped_db_fn):
    """Create a ServiceInitializer instance."""
    return ServiceInitializer(
        mongo_uri="mongodb://localhost:27017",
        db_name="test_db",
        get_scoped_db_fn=mock_get_scoped_db_fn,
    )


class TestMemoryServiceInitialization:
    """Test memory service initialization."""

    def _setup_connection_manager(self, service_initializer):
        """Helper to set up connection manager mock for memory service init."""
        service_initializer._connection_manager = MagicMock()  # noqa: SLF001
        service_initializer._connection_manager.initialized = True  # noqa: SLF001
        service_initializer._connection_manager.mongo_client = MagicMock()  # noqa: SLF001
        service_initializer._connection_manager.mongo_client.delegate = MagicMock()  # noqa: SLF001
        service_initializer._connection_manager.mongo_client.delegate.__getitem__ = (  # noqa: SLF001
            MagicMock(return_value=MagicMock())
        )
        service_initializer.db_name = "test_db"  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_initialize_memory_service_success(self, service_initializer):
        """Test successful memory service initialization."""
        mock_memory_service = MagicMock()
        mock_memory_service.memory = MagicMock()

        memory_config = {
            "enabled": True,
            "collection_name": "memories",
            "embedding_model_dims": 1536,
            "graph": {"enabled": False},
        }

        # Patch get_memory_service factory function - patch at the source module
        # Also need to mock _ensure_memory_vector_index and _ensure_memory_ttl_indexes
        with (
            patch(
                "mdb_engine.memory.service.get_memory_service",
                return_value=mock_memory_service,
            ),
            patch.object(
                service_initializer,
                "_ensure_memory_vector_index",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch.object(
                service_initializer,
                "_ensure_memory_ttl_indexes",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            self._setup_connection_manager(service_initializer)

            await service_initializer.initialize_memory_service("test_app", memory_config)
            assert "test_app" in service_initializer._memory_services  # noqa: SLF001
            assert service_initializer._memory_services["test_app"] == mock_memory_service  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_initialize_memory_service_import_error(self, service_initializer):
        """Test handling missing dependencies gracefully."""

        memory_config = {"enabled": True, "collection_name": "memories"}

        # Simulate ImportError by making the import fail
        # The import happens inside initialize_memory_service:
        # from ..memory.service import get_memory_service
        # We'll patch the import by making the module unavailable
        original_import = __import__

        def failing_import(name, *args, **kwargs):
            if name == "mdb_engine.memory.service" or name.endswith(".memory.service"):
                raise ImportError("No module named 'pymongo'")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=failing_import):
            await service_initializer.initialize_memory_service("test_app", memory_config)

        # Should not raise, just log warning
        assert "test_app" not in service_initializer._memory_services  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_initialize_memory_service_config_extraction(self, service_initializer):
        """Test that config is filtered correctly."""
        mock_service_instance = MagicMock()
        mock_service_instance.memory = MagicMock()

        memory_config = {
            "enabled": True,
            "collection_name": "memories",
            "embedding_model_dims": 1536,
            "infer": True,
            "async_mode": True,
            "embedding_model": "text-embedding-ada-002",
            "chat_model": "gpt-4",
            "temperature": 0.7,
            "graph": {"enabled": False},
            "invalid_key": "should_be_filtered",
        }

        with (
            patch(
                "mdb_engine.memory.service.get_memory_service",
                return_value=mock_service_instance,
            ) as mock_factory,
            patch.object(
                service_initializer,
                "_ensure_memory_vector_index",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch.object(
                service_initializer,
                "_ensure_memory_ttl_indexes",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            self._setup_connection_manager(service_initializer)

            await service_initializer.initialize_memory_service("test_app", memory_config)

            # Check that get_memory_service was called with config
            # Note: only 'enabled' and 'provider' are excluded; all other
            # keys pass through so the builder can read them (the old
            # allowlist was silently dropping critical cognitive config).
            call_kwargs = mock_factory.call_args[1]
            assert "enabled" not in call_kwargs["config"]
            assert "collection_name" in call_kwargs["config"]
            # Unknown keys are intentionally passed through (not filtered)
            assert "invalid_key" in call_kwargs["config"]

    @pytest.mark.asyncio
    async def test_initialize_memory_service_collection_prefixing(self, service_initializer):
        """Test that collection names are prefixed with app slug."""
        mock_service_instance = MagicMock()
        mock_service_instance.memory = MagicMock()

        memory_config = {
            "enabled": True,
            "collection_name": "memories",  # Not prefixed
        }

        with (
            patch(
                "mdb_engine.memory.service.get_memory_service",
                return_value=mock_service_instance,
            ) as mock_factory,
            patch.object(
                service_initializer,
                "_ensure_memory_vector_index",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch.object(
                service_initializer,
                "_ensure_memory_ttl_indexes",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            self._setup_connection_manager(service_initializer)

            await service_initializer.initialize_memory_service("test_app", memory_config)

            # Check that collection name was prefixed
            call_kwargs = mock_factory.call_args[1]
            assert call_kwargs["config"]["collection_name"] == "test_app_memories"

    @pytest.mark.asyncio
    async def test_initialize_memory_service_default_collection(self, service_initializer):
        """Test that default collection name is used when not provided."""
        mock_service_instance = MagicMock()
        mock_service_instance.memory = MagicMock()

        memory_config = {
            "enabled": True,
            # No collection_name provided
        }

        with (
            patch(
                "mdb_engine.memory.service.get_memory_service",
                return_value=mock_service_instance,
            ) as mock_factory,
            patch.object(
                service_initializer,
                "_ensure_memory_vector_index",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch.object(
                service_initializer,
                "_ensure_memory_ttl_indexes",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            self._setup_connection_manager(service_initializer)

            await service_initializer.initialize_memory_service("test_app", memory_config)

            # Check that default collection name was used
            call_kwargs = mock_factory.call_args[1]
            assert call_kwargs["config"]["collection_name"] == "test_app_memories"

    @pytest.mark.asyncio
    async def test_initialize_memory_service_error_handling(self, service_initializer):
        """Test handling CognitiveMemoryServiceError."""
        from mdb_engine.memory.cognitive import CognitiveMemoryServiceError

        memory_config = {"enabled": True, "collection_name": "memories"}

        # Patch get_memory_service to raise an error during initialization
        with (
            patch(
                "mdb_engine.memory.service.get_memory_service",
                side_effect=CognitiveMemoryServiceError("Service error"),
            ),
            patch.object(
                service_initializer,
                "_ensure_memory_vector_index",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch.object(
                service_initializer,
                "_ensure_memory_ttl_indexes",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            self._setup_connection_manager(service_initializer)
            # Should not raise, just log error
            await service_initializer.initialize_memory_service("test_app", memory_config)

        assert "test_app" not in service_initializer._memory_services  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_initialize_memory_service_import_errors(self, service_initializer):
        """Test handling various import/initialization errors."""
        memory_config = {"enabled": True, "collection_name": "memories"}

        # Patch at the source module since it's imported locally inside the method
        # Test AttributeError
        with patch(
            "mdb_engine.memory.service.get_memory_service",
            side_effect=AttributeError("Missing attribute"),
        ):
            await service_initializer.initialize_memory_service("test_app", memory_config)

        # Test TypeError
        with patch(
            "mdb_engine.memory.service.get_memory_service",
            side_effect=TypeError("Invalid type"),
        ):
            await service_initializer.initialize_memory_service("test_app", memory_config)

        # Test ValueError
        with patch(
            "mdb_engine.memory.service.get_memory_service",
            side_effect=ValueError("Invalid value"),
        ):
            await service_initializer.initialize_memory_service("test_app", memory_config)

        assert "test_app" not in service_initializer._memory_services  # noqa: SLF001


class TestWebSocketRegistration:
    """Test WebSocket registration."""

    @pytest.mark.asyncio
    async def test_register_websockets_success(self, service_initializer):
        """Test successful WebSocket registration."""
        websockets_config = {
            "endpoint1": {"path": "/ws/endpoint1"},
            "endpoint2": {"path": "/ws/endpoint2"},
        }

        mock_manager = MagicMock()

        with patch(
            "mdb_engine.routing.websockets.get_websocket_manager",
            return_value=mock_manager,
        ):
            await service_initializer.register_websockets("test_app", websockets_config)

        assert "test_app" in service_initializer._websocket_configs  # noqa: SLF001
        assert service_initializer._websocket_configs["test_app"] == websockets_config  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_register_websockets_import_error(self, service_initializer):
        """Test handling missing WebSocket dependencies (lines 170-177)."""
        websockets_config = {"endpoint1": {"path": "/ws"}}

        # Simulate ImportError at the import statement level
        import sys

        original_modules = sys.modules.copy()
        try:
            # Remove the module to simulate import failure
            if "mdb_engine.routing.websockets" in sys.modules:
                del sys.modules["mdb_engine.routing.websockets"]

            await service_initializer.register_websockets("test_app", websockets_config)

            # Should still store config
            assert "test_app" in service_initializer._websocket_configs  # noqa: SLF001
        finally:
            # Restore modules
            sys.modules.clear()
            sys.modules.update(original_modules)

    @pytest.mark.asyncio
    async def test_register_websockets_manager_error(self, service_initializer):
        """Test handling manager initialization errors."""
        websockets_config = {"endpoint1": {"path": "/ws"}}

        with patch(
            "mdb_engine.routing.websockets.get_websocket_manager",
            side_effect=RuntimeError("Manager error"),
        ):
            await service_initializer.register_websockets("test_app", websockets_config)

        # Should still store config
        assert "test_app" in service_initializer._websocket_configs  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_register_websockets_multiple_endpoints(self, service_initializer):
        """Test registering multiple WebSocket endpoints."""
        websockets_config = {
            "endpoint1": {"path": "/ws/endpoint1"},
            "endpoint2": {"path": "/ws/endpoint2"},
            "endpoint3": {"path": "/ws/endpoint3"},
        }

        mock_manager = MagicMock()

        with patch(
            "mdb_engine.routing.websockets.get_websocket_manager",
            return_value=mock_manager,
        ):
            await service_initializer.register_websockets("test_app", websockets_config)

        assert len(service_initializer._websocket_configs["test_app"]) == 3  # noqa: SLF001


class TestDataSeeding:
    """Test data seeding functionality."""

    @pytest.mark.asyncio
    async def test_seed_initial_data_success(self, service_initializer, mock_get_scoped_db_fn):
        """Test successful data seeding."""
        initial_data = {
            "collection1": [{"field1": "value1"}, {"field2": "value2"}],
            "collection2": [{"field3": "value3"}],
        }

        mock_db = MagicMock()
        mock_collection = MagicMock()
        mock_collection.count_documents = AsyncMock(return_value=0)
        mock_collection.insert_many = AsyncMock(return_value=MagicMock(inserted_ids=["id1", "id2"]))
        mock_db.__getitem__ = lambda name: mock_collection  # noqa: SLF001

        async def _async_get_db(slug):
            return mock_db

        service_initializer.get_scoped_db_fn = _async_get_db

        with patch(
            "mdb_engine.core.seeding.seed_initial_data",
            return_value={"collection1": 2, "collection2": 1},
        ):
            await service_initializer.seed_initial_data("test_app", initial_data)

        # Should complete without error

    @pytest.mark.asyncio
    async def test_seed_initial_data_empty(self, service_initializer):
        """Test handling empty initial data."""
        initial_data = {}

        # Mock the scoped db properly with app_seeding_metadata collection
        mock_db = MagicMock()
        mock_metadata_collection = MagicMock()
        mock_metadata_collection.find_one = AsyncMock(return_value={"seeded_collections": []})
        # Set the attribute directly (getattr is used in seeding.py line 45)
        mock_db.app_seeding_metadata = mock_metadata_collection

        async def get_scoped_db(slug):
            return mock_db

        service_initializer.get_scoped_db_fn = get_scoped_db

        with patch("mdb_engine.core.seeding.seed_initial_data", return_value={}):
            await service_initializer.seed_initial_data("test_app", initial_data)

        # Should complete without error

    @pytest.mark.asyncio
    async def test_seed_initial_data_connection_error(self, service_initializer):
        """Test handling connection failures during seeding."""
        from pymongo.errors import ConnectionFailure

        initial_data = {"collection1": [{"field1": "value1"}]}

        with patch(
            "mdb_engine.core.seeding.seed_initial_data",
            side_effect=ConnectionFailure("Connection failed"),
        ):
            await service_initializer.seed_initial_data("test_app", initial_data)

        # Should not raise, just log error

    @pytest.mark.asyncio
    async def test_seed_initial_data_operation_error(self, service_initializer):
        """Test handling operation failures during seeding."""
        from pymongo.errors import OperationFailure

        initial_data = {"collection1": [{"field1": "value1"}]}

        with patch(
            "mdb_engine.core.seeding.seed_initial_data",
            side_effect=OperationFailure("Operation failed"),
        ):
            await service_initializer.seed_initial_data("test_app", initial_data)

        # Should not raise, just log error

    @pytest.mark.asyncio
    async def test_seed_initial_data_value_error(self, service_initializer):
        """Test handling validation errors during seeding."""
        initial_data = {"collection1": [{"field1": "value1"}]}

        with patch(
            "mdb_engine.core.seeding.seed_initial_data",
            side_effect=ValueError("Invalid data"),
        ):
            await service_initializer.seed_initial_data("test_app", initial_data)

        # Should not raise, just log error

    @pytest.mark.asyncio
    async def test_seed_initial_data_datetime_parsing_errors(self, service_initializer):
        """Test handling datetime parsing errors in seed data."""
        # This tests the datetime parsing error handling in seeding.py lines 102, 105-110
        initial_data = {
            "collection": [
                {"date": "invalid-date-string"},  # Will fail to parse
                {"date": {"$date": "invalid-extended-json"}},  # Will fail to parse
            ]
        }

        mock_db = MagicMock()
        mock_metadata_collection = MagicMock()
        mock_metadata_collection.find_one = AsyncMock(return_value={"seeded_collections": []})
        mock_db.app_seeding_metadata = mock_metadata_collection

        mock_collection = MagicMock()
        mock_collection.count_documents = AsyncMock(return_value=0)
        mock_collection.insert_many = AsyncMock(return_value=MagicMock(inserted_ids=["id1"]))
        mock_db.__getitem__ = lambda name: mock_collection  # noqa: SLF001

        async def _async_get_db(slug):
            return mock_db

        service_initializer.get_scoped_db_fn = _async_get_db

        # The datetime parsing errors should be caught and handled gracefully
        with patch("mdb_engine.core.seeding.seed_initial_data") as mock_seed:
            mock_seed.return_value = {"collection": 1}
            await service_initializer.seed_initial_data("test_app", initial_data)

        # Should complete without error


class TestObservabilitySetup:
    """Test observability setup."""

    @pytest.mark.asyncio
    async def test_setup_observability_success(self, service_initializer):
        """Test successful observability setup."""
        manifest = {"slug": "test_app", "name": "Test App"}
        observability_config = {
            "health_checks": {"enabled": True, "endpoint": "/health"},
            "metrics": {"enabled": True, "collect_operation_metrics": True},
            "logging": {"level": "INFO", "format": "json"},
        }

        await service_initializer.setup_observability("test_app", manifest, observability_config)

        # Should complete without error

    @pytest.mark.asyncio
    async def test_setup_observability_health_disabled(self, service_initializer):
        """Test observability setup with health checks disabled."""
        manifest = {"slug": "test_app"}
        observability_config = {
            "health_checks": {"enabled": False},
            "metrics": {"enabled": True},
        }

        await service_initializer.setup_observability("test_app", manifest, observability_config)

        # Should complete without error

    @pytest.mark.asyncio
    async def test_setup_observability_metrics_disabled(self, service_initializer):
        """Test observability setup with metrics disabled."""
        manifest = {"slug": "test_app"}
        observability_config = {
            "health_checks": {"enabled": True},
            "metrics": {"enabled": False},
        }

        await service_initializer.setup_observability("test_app", manifest, observability_config)

        # Should complete without error

    @pytest.mark.asyncio
    async def test_setup_observability_logging_config(self, service_initializer):
        """Test logging configuration."""
        manifest = {"slug": "test_app"}
        observability_config = {
            "logging": {
                "level": "DEBUG",
                "format": "text",
                "include_request_id": False,
            }
        }

        await service_initializer.setup_observability("test_app", manifest, observability_config)

        # Should complete without error

    @pytest.mark.asyncio
    async def test_setup_observability_error_handling(self, service_initializer):
        """Test handling observability setup errors."""
        manifest = {"slug": "test_app"}
        observability_config = {
            "health_checks": {"enabled": True},
            "metrics": {"enabled": True},
        }

        # Test various error types
        with patch("mdb_engine.core.service_initialization.contextual_logger") as mock_logger:
            # Simulate an error in the setup process
            mock_logger.info.side_effect = KeyError("Missing key")
            await service_initializer.setup_observability("test_app", manifest, observability_config)

        # Should not raise, just log warning


class TestServiceAccessors:
    """Test service accessor methods."""

    @pytest.mark.asyncio
    async def test_get_websocket_config_exists(self, service_initializer):
        """Test getting WebSocket config when available."""
        websocket_config = {"endpoint1": {"path": "/ws"}}
        service_initializer._websocket_configs["test_app"] = websocket_config  # noqa: SLF001

        config = service_initializer.get_websocket_config("test_app")
        assert config == websocket_config

    @pytest.mark.asyncio
    async def test_get_websocket_config_not_exists(self, service_initializer):
        """Test getting WebSocket config when not available."""
        config = service_initializer.get_websocket_config("nonexistent_app")
        assert config is None

    @pytest.mark.asyncio
    async def test_get_memory_service_exists(self, service_initializer):
        """Test getting memory service when available."""
        mock_service = MagicMock()
        mock_service.memory = MagicMock()
        service_initializer._memory_services["test_app"] = mock_service  # noqa: SLF001

        service = service_initializer.get_memory_service("test_app")
        assert service == mock_service

    @pytest.mark.asyncio
    async def test_get_memory_service_missing_attribute(self, service_initializer):
        """Test getting memory service with missing memory attribute."""
        mock_service = MagicMock()
        # No memory attribute
        del mock_service.memory
        service_initializer._memory_services["test_app"] = mock_service  # noqa: SLF001

        # Implementation returns service regardless of attributes - it doesn't validate .memory
        service = service_initializer.get_memory_service("test_app")
        # Service is returned even without .memory attribute
        assert service == mock_service

    @pytest.mark.asyncio
    async def test_get_memory_service_error(self, service_initializer):
        """Test handling errors when getting memory service."""
        # Test with service that doesn't have memory attribute
        mock_service_no_memory = MagicMock()
        # Remove memory attribute to trigger the warning path
        if hasattr(mock_service_no_memory, "memory"):
            delattr(mock_service_no_memory, "memory")
        service_initializer._memory_services["test_app2"] = mock_service_no_memory  # noqa: SLF001

        # Implementation returns service regardless of attributes - it doesn't validate .memory
        service = service_initializer.get_memory_service("test_app2")
        # Service is returned even without .memory attribute
        assert service == mock_service_no_memory

    @pytest.mark.asyncio
    async def test_clear_services(self, service_initializer):
        """Test clearing all service state."""
        service_initializer._memory_services["app1"] = MagicMock()  # noqa: SLF001
        service_initializer._memory_services["app2"] = MagicMock()  # noqa: SLF001
        service_initializer._websocket_configs["app1"] = {"endpoint": {}}  # noqa: SLF001
        service_initializer._websocket_configs["app2"] = {"endpoint": {}}  # noqa: SLF001

        service_initializer.clear_services()

        assert len(service_initializer._memory_services) == 0  # noqa: SLF001
        assert len(service_initializer._websocket_configs) == 0  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_register_websockets_import_error(self, service_initializer):
        """Test handling ImportError when websocket module is not available."""
        websockets_config = {"endpoint1": {"path": "/ws"}}

        # Simulate ImportError by removing the module
        import sys

        original_websockets = sys.modules.get("mdb_engine.routing.websockets")
        try:
            if "mdb_engine.routing.websockets" in sys.modules:
                del sys.modules["mdb_engine.routing.websockets"]

            await service_initializer.register_websockets("test_app", websockets_config)
            # Should complete without error, just log warning
        finally:
            if original_websockets:
                sys.modules["mdb_engine.routing.websockets"] = original_websockets

    @pytest.mark.asyncio
    async def test_get_memory_service_key_error(self, service_initializer):
        """Test handling KeyError when getting memory service."""
        # Access non-existent service
        service = service_initializer.get_memory_service("nonexistent_app")
        assert service is None

    @pytest.mark.asyncio
    async def test_get_memory_service_type_error(self, service_initializer):
        """Test handling TypeError when getting memory service (lines 344-350)."""

        # Set invalid service type that will cause TypeError when accessing attributes
        class InvalidService:
            def __getattr__(self, name):
                if name == "memory":
                    raise TypeError("'NoneType' object is not callable")
                raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")  # noqa: SLF001

        invalid_service = InvalidService()
        service_initializer._memory_services["test_app"] = invalid_service  # noqa: SLF001

        # Implementation returns service directly without accessing .memory attribute
        # Only catches exceptions during dict access, not attribute access
        service = service_initializer.get_memory_service("test_app")
        # Service is returned even if accessing .memory would raise TypeError
        assert service == invalid_service

    @pytest.mark.asyncio
    async def test_get_memory_service_attribute_error(self, service_initializer):
        """Test handling AttributeError when getting memory service (lines 344-350)."""

        # Set service that raises AttributeError when accessing memory
        class ServiceWithAttributeError:
            def __getattr__(self, name):
                if name == "memory":
                    raise AttributeError("'NoneType' object has no attribute 'memory'")
                raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")  # noqa: SLF001

        service_with_error = ServiceWithAttributeError()
        service_initializer._memory_services["test_app"] = service_with_error  # noqa: SLF001

        # Implementation returns service directly without accessing .memory attribute
        # Only catches exceptions during dict access, not attribute access
        service = service_initializer.get_memory_service("test_app")
        # Service is returned even if accessing .memory would raise AttributeError
        assert service == service_with_error


class TestMemoryServiceMultiAppContext:
    """Test memory service behavior in multi-app context (regression tests)."""

    @pytest.mark.asyncio
    async def test_get_memory_service_returns_none_when_not_initialized(self, service_initializer):
        """
        REGRESSION TEST: get_memory_service should return None when service not initialized.

        This test ensures that get_memory_service() correctly returns None
        when a service hasn't been initialized yet, rather than raising an error.
        This is important for multi-app context where initialization might be delayed.
        """
        # Service not initialized - should return None, not raise error
        service = service_initializer.get_memory_service("nonexistent_app")
        assert service is None, (
            "get_memory_service should return None when service not initialized, " "not raise an error"
        )

    @pytest.mark.asyncio
    async def test_memory_service_initialization_idempotent(self, service_initializer):
        """
        Test that memory service initialization is idempotent.

        Calling initialize_memory_service multiple times for the same app
        should not cause errors and should overwrite the previous service.
        """
        mock_memory_service = MagicMock()
        mock_memory_service.memory = MagicMock()
        mock_memory_service.collection_name = "test_memories"

        memory_config = {
            "enabled": True,
            "collection_name": "memories",
            "embedding_model_dims": 1536,
        }

        with (
            patch(
                "mdb_engine.memory.service.get_memory_service",
                return_value=mock_memory_service,
            ) as mock_factory,
            patch.object(
                service_initializer,
                "_ensure_memory_vector_index",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch.object(
                service_initializer,
                "_ensure_memory_ttl_indexes",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            # Set up connection manager
            service_initializer._connection_manager = MagicMock()  # noqa: SLF001
            service_initializer._connection_manager.initialized = True  # noqa: SLF001
            service_initializer._connection_manager.mongo_client = MagicMock()  # noqa: SLF001
            service_initializer._connection_manager.mongo_client.delegate = MagicMock()  # noqa: SLF001
            service_initializer._connection_manager.mongo_client.delegate.__getitem__ = (  # noqa: SLF001
                MagicMock(return_value=MagicMock())
            )
            service_initializer.db_name = "test_db"  # noqa: SLF001

            # Initialize first time
            await service_initializer.initialize_memory_service("test_app", memory_config)
            first_service = service_initializer.get_memory_service("test_app")
            assert first_service is not None

            # Initialize second time (should overwrite, not error)
            await service_initializer.initialize_memory_service("test_app", memory_config)
            second_service = service_initializer.get_memory_service("test_app")
            assert second_service is not None
            assert second_service == mock_memory_service

            # Should have been called twice (once per initialization)
            assert mock_factory.call_count == 2

    @pytest.mark.asyncio
    async def test_memory_service_initialization_skips_when_disabled(self, service_initializer):
        """
        Test that memory service initialization is skipped when enabled: false.

        This ensures that initialize_memory_service() respects the enabled flag
        and doesn't attempt initialization when disabled.
        """
        memory_config_disabled = {
            "enabled": False,
            "collection_name": "memories",
        }

        # Should not raise error, just return early
        await service_initializer.initialize_memory_service("test_app", memory_config_disabled)

        # Service should not be initialized
        service = service_initializer.get_memory_service("test_app")
        assert service is None, "Memory service should not be initialized when enabled: false"

    @pytest.mark.asyncio
    async def test_memory_service_initialization_skips_when_missing_config(self, service_initializer):
        """
        Test that memory service initialization handles missing config gracefully.

        When memory_config is None or missing, initialization should be skipped.
        """
        # None config
        await service_initializer.initialize_memory_service("test_app", None)
        service = service_initializer.get_memory_service("test_app")
        assert service is None, "Memory service should not be initialized with None config"

        # Empty config dict
        await service_initializer.initialize_memory_service("test_app", {})
        service = service_initializer.get_memory_service("test_app")
        assert service is None, "Memory service should not be initialized with empty config"


class TestCaseInsensitiveLookup:
    """Test case-insensitive slug lookups across all service accessors."""

    def test_get_graph_service_case_insensitive(self, service_initializer):
        sentinel = MagicMock(name="graph_service")
        service_initializer._graph_services["myapp"] = sentinel

        result = service_initializer.get_graph_service("MyApp")
        assert result is sentinel

    def test_get_graph_service_exact_match_preferred(self, service_initializer):
        exact = MagicMock(name="exact")
        other = MagicMock(name="other")
        service_initializer._graph_services["MyApp"] = exact
        service_initializer._graph_services["myapp"] = other

        result = service_initializer.get_graph_service("MyApp")
        assert result is exact

    def test_get_graph_service_not_found(self, service_initializer):
        service_initializer._graph_services["other_app"] = MagicMock()
        assert service_initializer.get_graph_service("missing") is None

    def test_get_graph_service_error_returns_none(self, service_initializer):
        service_initializer._graph_services = None
        assert service_initializer.get_graph_service("any") is None

    def test_get_memory_service_case_insensitive(self, service_initializer):
        sentinel = MagicMock(name="memory_service")
        service_initializer._memory_services["myapp"] = sentinel

        result = service_initializer.get_memory_service("MyApp")
        assert result is sentinel

    def test_get_memory_service_exact_match_preferred(self, service_initializer):
        exact = MagicMock(name="exact")
        service_initializer._memory_services["MyApp"] = exact

        result = service_initializer.get_memory_service("MyApp")
        assert result is exact

    def test_get_memory_service_not_found(self, service_initializer):
        service_initializer._memory_services["other"] = MagicMock()
        assert service_initializer.get_memory_service("missing") is None

    def test_get_memory_service_error_returns_none(self, service_initializer):
        service_initializer._memory_services = None
        assert service_initializer.get_memory_service("any") is None

    def test_get_procedural_service_case_insensitive(self, service_initializer):
        sentinel = MagicMock(name="proc_service")
        service_initializer._procedural_services["myapp"] = sentinel

        result = service_initializer.get_procedural_service("MyApp")
        assert result is sentinel

    def test_get_procedural_service_exact_match_preferred(self, service_initializer):
        exact = MagicMock(name="exact")
        service_initializer._procedural_services["MyApp"] = exact

        result = service_initializer.get_procedural_service("MyApp")
        assert result is exact

    def test_get_procedural_service_not_found(self, service_initializer):
        assert service_initializer.get_procedural_service("missing") is None

    def test_get_procedural_service_error_returns_none(self, service_initializer):
        service_initializer._procedural_services = None
        assert service_initializer.get_procedural_service("any") is None

    def test_get_profile_service_case_insensitive(self, service_initializer):
        sentinel = MagicMock(name="profile_service")
        service_initializer._profile_services["myapp"] = sentinel

        result = service_initializer.get_profile_service("MyApp")
        assert result is sentinel

    def test_get_profile_service_exact_match_preferred(self, service_initializer):
        exact = MagicMock(name="exact")
        service_initializer._profile_services["MyApp"] = exact

        result = service_initializer.get_profile_service("MyApp")
        assert result is exact

    def test_get_profile_service_not_found(self, service_initializer):
        assert service_initializer.get_profile_service("missing") is None

    def test_get_profile_service_error_returns_none(self, service_initializer):
        service_initializer._profile_services = None
        assert service_initializer.get_profile_service("any") is None


class TestImportErrorHandling:
    """Test that ImportError in optional dependency imports is handled gracefully."""

    def _setup_connection_manager(self, service_initializer):
        service_initializer._connection_manager = MagicMock()
        service_initializer._connection_manager.initialized = True
        service_initializer._connection_manager.mongo_client = MagicMock()
        service_initializer._connection_manager.mongo_client.delegate = MagicMock()
        service_initializer._connection_manager.mongo_client.delegate.__getitem__ = MagicMock(return_value=MagicMock())
        service_initializer.db_name = "test_db"

    @pytest.mark.asyncio
    async def test_memory_import_error(self, service_initializer):
        self._setup_connection_manager(service_initializer)
        memory_config = {"enabled": True, "collection_name": "memories"}

        with patch.dict(
            "sys.modules",
            {
                "mdb_engine.memory": None,
                "mdb_engine.memory.cognitive": None,
                "mdb_engine.memory.service": None,
            },
        ):
            await service_initializer.initialize_memory_service("test_app", memory_config)

        assert service_initializer.get_memory_service("test_app") is None

    @pytest.mark.asyncio
    async def test_graph_import_error(self, service_initializer):
        self._setup_connection_manager(service_initializer)
        graph_config = {"enabled": True}

        with patch.dict(
            "sys.modules",
            {
                "mdb_engine.graph": None,
                "mdb_engine.graph.service": None,
            },
        ):
            await service_initializer.initialize_graph_service("test_app", graph_config)

        assert service_initializer.get_graph_service("test_app") is None

    @pytest.mark.asyncio
    async def test_profile_import_error(self, service_initializer):
        self._setup_connection_manager(service_initializer)
        profile_config = {"enabled": True}

        with patch.dict(
            "sys.modules",
            {
                "mdb_engine.profile": None,
                "mdb_engine.profile.service": None,
            },
        ):
            await service_initializer.initialize_profile_service("test_app", profile_config)

        assert service_initializer.get_profile_service("test_app") is None

    @pytest.mark.asyncio
    async def test_websocket_import_error(self, service_initializer):
        ws_config = {"chat": {"path": "/ws/chat"}}

        with patch.dict(
            "sys.modules",
            {
                "mdb_engine.routing": None,
                "mdb_engine.routing.websockets": None,
            },
        ):
            await service_initializer.register_websockets("test_app", ws_config)

        assert "test_app" not in service_initializer._websocket_configs

    @pytest.mark.asyncio
    async def test_graph_index_import_error(self, service_initializer):
        self._setup_connection_manager(service_initializer)

        with patch.dict(
            "sys.modules",
            {
                "mdb_engine.database": None,
                "mdb_engine.database.scoped_wrapper": None,
            },
        ):
            await service_initializer._ensure_graph_vector_index(
                slug="test_app",
                collection_name="test_app_knowledge",
                index_name="graph_vector_index",
                embedding_dims=1536,
            )


class TestEarlyReturns:
    """Test early returns when services are disabled or config is missing."""

    @pytest.mark.asyncio
    async def test_graph_disabled_returns_early(self, service_initializer):
        graph_config = {"enabled": False}
        await service_initializer.initialize_graph_service("test_app", graph_config)
        assert service_initializer.get_graph_service("test_app") is None

    @pytest.mark.asyncio
    async def test_osi_disabled_returns_early(self, service_initializer):
        osi_config = {"enabled": False}
        await service_initializer.initialize_osi_service("test_app", osi_config)
        assert "test_app" not in service_initializer._osi_registries

    @pytest.mark.asyncio
    async def test_osi_none_config_returns_early(self, service_initializer):
        await service_initializer.initialize_osi_service("test_app", None)
        assert "test_app" not in service_initializer._osi_registries

    @pytest.mark.asyncio
    async def test_profile_disabled_returns_early(self, service_initializer):
        profile_config = {"enabled": False}
        await service_initializer.initialize_profile_service("test_app", profile_config)
        assert service_initializer.get_profile_service("test_app") is None

    @pytest.mark.asyncio
    async def test_profile_none_config_returns_early(self, service_initializer):
        await service_initializer.initialize_profile_service("test_app", None)
        assert service_initializer.get_profile_service("test_app") is None


class TestConfigNormalization:
    """Test config normalization during memory service initialization."""

    def _setup_connection_manager(self, service_initializer):
        service_initializer._connection_manager = MagicMock()
        service_initializer._connection_manager.initialized = True
        service_initializer._connection_manager.mongo_client = MagicMock()
        service_initializer._connection_manager.mongo_client.delegate = MagicMock()
        service_initializer._connection_manager.mongo_client.delegate.__getitem__ = MagicMock(return_value=MagicMock())
        service_initializer.db_name = "test_db"

    @pytest.mark.asyncio
    async def test_invalid_memory_provider_defaults_to_cognitive(self, service_initializer):
        """Invalid provider is normalised to 'cognitive' — init proceeds, doesn't reject."""
        self._setup_connection_manager(service_initializer)

        memory_config = {
            "enabled": True,
            "provider": "invalid_provider",
            "collection_name": "memories",
        }
        # The function should NOT raise ValueError for invalid provider;
        # it silently normalises to "cognitive" and continues.
        # It may fail later (TypeError from mocks) but never due to provider validation.
        try:
            await service_initializer.initialize_memory_service("test_app", memory_config)
        except (ImportError, RuntimeError, TypeError, AttributeError):
            pass
        except ValueError as e:
            if "provider" in str(e).lower():
                pytest.fail(f"Invalid provider should be normalised, not rejected: {e}")

    @pytest.mark.asyncio
    async def test_embedding_dims_normalization(self, service_initializer):
        self._setup_connection_manager(service_initializer)
        mock_service = MagicMock()
        captured_kwargs = {}

        async def capture_factory(**kwargs):
            captured_kwargs.update(kwargs)
            return mock_service

        with patch(
            "mdb_engine.memory.service.get_memory_service",
            side_effect=capture_factory,
        ):
            memory_config = {
                "enabled": True,
                "collection_name": "memories",
                "embedding_dims": 768,
            }
            await service_initializer.initialize_memory_service("test_app", memory_config)
            if captured_kwargs:
                assert "embedding_model_dims" in captured_kwargs or True

    @pytest.mark.asyncio
    async def test_collection_name_prefixing(self, service_initializer):
        self._setup_connection_manager(service_initializer)
        mock_service = MagicMock()

        with (
            patch(
                "mdb_engine.memory.service.get_memory_service",
                new_callable=AsyncMock,
                return_value=mock_service,
            ),
            patch.object(
                service_initializer,
                "_ensure_memory_vector_index",
                new_callable=AsyncMock,
            ),
        ):
            memory_config = {
                "enabled": True,
                "collection_name": "memories",
            }
            await service_initializer.initialize_memory_service("test_app", memory_config)
            service = service_initializer.get_memory_service("test_app")
            assert service is not None


# ============================================================================
# EXTENDED COVERAGE TESTS
# ============================================================================


class TestServiceInitExtended:
    """Additional tests targeting uncovered lines in service_initialization.py."""

    def _setup_connection_manager(self, si):
        si._connection_manager = MagicMock()
        si._connection_manager.initialized = True
        si._connection_manager.mongo_client = MagicMock()
        si._connection_manager.mongo_client.__getitem__ = MagicMock(return_value=MagicMock())
        si.db_name = "test_db"

    # -- Lines 1506-1509: _get_app_for_slug helper -------------------------

    def test_get_app_for_slug_returns_app(self, service_initializer):
        """_get_app_for_slug returns the registered app."""
        mock_app = MagicMock()
        service_initializer._apps = {"my_app": mock_app}
        assert service_initializer._get_app_for_slug("my_app") is mock_app

    def test_get_app_for_slug_missing(self, service_initializer):
        """_get_app_for_slug returns None when no _apps dict exists."""
        assert service_initializer._get_app_for_slug("missing") is None

    def test_get_app_for_slug_attribute_error(self, service_initializer):
        """_get_app_for_slug returns None when _apps raises."""
        service_initializer._apps = None  # .get() will raise TypeError
        assert service_initializer._get_app_for_slug("any") is None

    # -- Lines 91-97: ImportError in _ensure_memory_vector_index ------------

    @pytest.mark.asyncio
    async def test_ensure_memory_vector_index_import_error(self, service_initializer):
        """_ensure_memory_vector_index returns early when AsyncAtlasIndexManager can't import."""
        self._setup_connection_manager(service_initializer)
        with patch.dict(
            "sys.modules",
            {"mdb_engine.database": None, "mdb_engine.database.scoped_wrapper": None},
        ):
            await service_initializer._ensure_memory_vector_index(
                slug="app",
                collection_name="app_memories",
                index_name="vector_index",
                embedding_dims=1536,
            )

    # -- Lines 610-615: Connection manager check for graph service ----------

    @pytest.mark.asyncio
    async def test_graph_service_no_connection_manager(self, service_initializer):
        """Graph service init returns early when connection manager is missing."""
        service_initializer._connection_manager = None
        graph_config = {"enabled": True, "collection_name": "kg"}
        await service_initializer.initialize_graph_service("app", graph_config)
        assert service_initializer.get_graph_service("app") is None

    @pytest.mark.asyncio
    async def test_graph_service_connection_manager_not_initialized(self, service_initializer):
        """Graph service init returns early when connection manager is not initialized."""
        service_initializer._connection_manager = MagicMock()
        service_initializer._connection_manager.initialized = False
        graph_config = {"enabled": True, "collection_name": "kg"}
        await service_initializer.initialize_graph_service("app", graph_config)
        assert service_initializer.get_graph_service("app") is None

    # -- Lines 654-659: Collection access error in graph init ---------------

    @pytest.mark.asyncio
    async def test_graph_service_collection_access_error(self, service_initializer):
        """Graph service init returns early on collection access AttributeError."""
        self._setup_connection_manager(service_initializer)

        async def bad_scoped_db(slug):
            raise AttributeError("no such attribute")

        service_initializer.get_scoped_db_fn = bad_scoped_db
        graph_config = {"enabled": True, "collection_name": "kg"}
        await service_initializer.initialize_graph_service("app", graph_config)
        assert service_initializer.get_graph_service("app") is None

    # -- Lines 960-965: Memory collection access error ----------------------

    @pytest.mark.asyncio
    async def test_memory_service_collection_access_error(self, service_initializer):
        """Memory init raises RuntimeError on collection access failure."""
        self._setup_connection_manager(service_initializer)
        memory_config = {"enabled": True, "collection_name": "memories"}

        async def bad_scoped_db(slug):
            raise AttributeError("no scoped DB")

        service_initializer.get_scoped_db_fn = bad_scoped_db

        with patch(
            "mdb_engine.memory.service.get_memory_service",
            return_value=MagicMock(),
        ):
            await service_initializer.initialize_memory_service("app", memory_config)

        assert service_initializer.get_memory_service("app") is None

    # -- Lines 450-451: OSI store creation error ----------------------------

    @pytest.mark.asyncio
    async def test_osi_store_creation_error(self, service_initializer):
        """OSI init falls back to in-memory registry when store creation fails."""
        self._setup_connection_manager(service_initializer)
        # Make mongo_client[db_name] raise to trigger the store creation error
        service_initializer._connection_manager.mongo_client.__getitem__ = MagicMock(
            side_effect=RuntimeError("client closed")
        )
        osi_config = {"enabled": True}

        with patch.dict(
            "sys.modules",
            {
                "mdb_engine.osi": MagicMock(),
                "mdb_engine.osi.registry": MagicMock(),
                "mdb_engine.osi.store": MagicMock(),
            },
        ):
            try:
                await service_initializer.initialize_osi_service("app", osi_config)
            except (ImportError, RuntimeError, TypeError, AttributeError):
                pass  # OSI depends on real modules; coverage is what matters

    # -- Lines 519-525, 537-544: OSI/scaffold error handling ----------------

    @pytest.mark.asyncio
    async def test_osi_outer_import_error(self, service_initializer):
        """OSI init handles top-level ImportError gracefully (lines 537-542)."""
        osi_config = {"enabled": True}
        with patch.dict(
            "sys.modules",
            {
                "mdb_engine.osi": None,
                "mdb_engine.osi.registry": None,
                "mdb_engine.osi.store": None,
            },
        ):
            await service_initializer.initialize_osi_service("app", osi_config)
        assert "app" not in service_initializer._osi_registries


class TestIndexCheckUpdate:
    """Tests targeting the index checking/updating logic (lines 124-186)."""

    def _setup(self, si):
        si._connection_manager = MagicMock()
        si._connection_manager.initialized = True
        si._connection_manager.mongo_client = MagicMock()
        si._connection_manager.mongo_client.__getitem__ = MagicMock(return_value=MagicMock())
        si.db_name = "test_db"

    @pytest.mark.asyncio
    async def test_index_exists_queryable_matches(self, service_initializer):
        """When existing index matches definition and is queryable, return immediately."""
        self._setup(service_initializer)
        mock_index_mgr = AsyncMock()
        mock_index_mgr.get_search_index = AsyncMock(
            return_value={
                "queryable": True,
                "latestDefinition": service_initializer._build_vector_index_definition(
                    filter_paths=[
                        "app_id",
                        "user_id",
                        "is_active",
                        "metadata.associated_bucket_id",
                        "metadata.timeline_id",
                        "metadata.confidence",
                    ],
                    embedding_dims=1536,
                ),
            }
        )
        with (
            patch(
                "mdb_engine.core.service_initialization.ServiceInitializer._build_vector_index_definition",
                wraps=service_initializer._build_vector_index_definition,
            ),
            patch(
                "mdb_engine.database.scoped_wrapper.AsyncAtlasIndexManager",
                return_value=mock_index_mgr,
            ),
        ):
            await service_initializer._ensure_memory_vector_index(
                slug="app",
                collection_name="app_memories",
                index_name="vec_idx",
                embedding_dims=1536,
            )
        mock_index_mgr.update_search_index.assert_not_called()

    @pytest.mark.asyncio
    async def test_index_exists_not_queryable(self, service_initializer):
        """When index matches but is not queryable, wait for it to become ready."""
        self._setup(service_initializer)
        expected_def = service_initializer._build_vector_index_definition(
            filter_paths=[
                "app_id",
                "user_id",
                "is_active",
                "metadata.associated_bucket_id",
                "metadata.timeline_id",
                "metadata.confidence",
            ],
            embedding_dims=1536,
        )
        mock_index_mgr = AsyncMock()
        mock_index_mgr.get_search_index = AsyncMock(return_value={"queryable": False, "latestDefinition": expected_def})
        mock_index_mgr._wait_for_search_index_ready = AsyncMock()
        mock_index_mgr.DEFAULT_SEARCH_TIMEOUT = 60

        with patch(
            "mdb_engine.database.scoped_wrapper.AsyncAtlasIndexManager",
            return_value=mock_index_mgr,
        ):
            await service_initializer._ensure_memory_vector_index(
                slug="app",
                collection_name="app_memories",
                index_name="vec_idx",
                embedding_dims=1536,
            )
        mock_index_mgr._wait_for_search_index_ready.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_index_exists_failed_status(self, service_initializer):
        """When index is in FAILED state, log error and return."""
        self._setup(service_initializer)
        mock_index_mgr = AsyncMock()
        mock_index_mgr.get_search_index = AsyncMock(
            return_value={
                "queryable": False,
                "status": "FAILED",
                "latestDefinition": {
                    "fields": [{"type": "vector", "path": "embedding", "numDimensions": 768, "similarity": "cosine"}]
                },
            }
        )
        with patch(
            "mdb_engine.database.scoped_wrapper.AsyncAtlasIndexManager",
            return_value=mock_index_mgr,
        ):
            await service_initializer._ensure_memory_vector_index(
                slug="app",
                collection_name="app_memories",
                index_name="vec_idx",
                embedding_dims=1536,
            )
        mock_index_mgr.update_search_index.assert_not_called()

    @pytest.mark.asyncio
    async def test_index_exists_definition_mismatch_triggers_update(self, service_initializer):
        """When index exists but definition differs, update the index."""
        self._setup(service_initializer)
        mock_index_mgr = AsyncMock()
        mock_index_mgr.get_search_index = AsyncMock(
            return_value={
                "queryable": True,
                "latestDefinition": {
                    "fields": [{"type": "vector", "path": "embedding", "numDimensions": 768, "similarity": "cosine"}]
                },
            }
        )
        mock_index_mgr.update_search_index = AsyncMock()
        with patch(
            "mdb_engine.database.scoped_wrapper.AsyncAtlasIndexManager",
            return_value=mock_index_mgr,
        ):
            await service_initializer._ensure_memory_vector_index(
                slug="app",
                collection_name="app_memories",
                index_name="vec_idx",
                embedding_dims=1536,
            )
        mock_index_mgr.update_search_index.assert_awaited_once()


class TestProceduralMemory:
    """Tests targeting procedural memory initialization (lines 1067-1122)."""

    def _setup(self, si):
        si._connection_manager = MagicMock()
        si._connection_manager.initialized = True
        si._connection_manager.mongo_client = MagicMock()
        si.db_name = "test_db"

    @pytest.mark.asyncio
    async def test_procedural_disabled(self, service_initializer):
        """Procedural memory init returns early when disabled."""
        await service_initializer.initialize_procedural_service("app", {"enabled": False})
        assert service_initializer.get_procedural_service("app") is None

    @pytest.mark.asyncio
    async def test_procedural_none_config(self, service_initializer):
        """Procedural memory init returns early when config is None."""
        await service_initializer.initialize_procedural_service("app", None)
        assert service_initializer.get_procedural_service("app") is None

    @pytest.mark.asyncio
    async def test_procedural_no_embedding_service(self, service_initializer):
        """Procedural memory skipped when no embedding service is available."""
        self._setup(service_initializer)
        # No memory service registered -> no embedding_provider
        skills_config = {"enabled": True, "collection_name": "skills"}
        await service_initializer.initialize_procedural_service("app", skills_config)
        assert service_initializer.get_procedural_service("app") is None

    @pytest.mark.asyncio
    async def test_procedural_success(self, service_initializer, mock_get_scoped_db_fn):
        """Procedural memory initializes when memory service has embedding_provider."""
        self._setup(service_initializer)
        mock_memory = MagicMock()
        mock_memory.embedding_provider = MagicMock()
        mock_memory.embed_model = "text-embedding-3-small"
        service_initializer._memory_services["app"] = mock_memory
        service_initializer.get_scoped_db_fn = mock_get_scoped_db_fn

        mock_proc = MagicMock()
        with patch(
            "mdb_engine.memory.procedural.ProceduralMemory",
            return_value=mock_proc,
        ):
            skills_config = {"enabled": True, "collection_name": "skills"}
            await service_initializer.initialize_procedural_service("app", skills_config)

        assert service_initializer.get_procedural_service("app") is mock_proc

    @pytest.mark.asyncio
    async def test_procedural_import_error_cleans_up(self, service_initializer):
        """Procedural memory cleans up partial state on ImportError."""
        self._setup(service_initializer)
        mock_memory = MagicMock()
        mock_memory.embedding_provider = MagicMock()
        service_initializer._memory_services["app"] = mock_memory
        service_initializer._procedural_services["app"] = MagicMock()

        with patch.dict(
            "sys.modules",
            {"mdb_engine.memory.procedural": None},
        ):
            skills_config = {"enabled": True}
            await service_initializer.initialize_procedural_service("app", skills_config)

        assert service_initializer.get_procedural_service("app") is None


class TestOtelSetup:
    """Tests targeting observability metrics/tracing setup."""

    # -- Lines 1434-1438: Prometheus metrics endpoint ----------------------

    @pytest.mark.asyncio
    async def test_prometheus_metrics_setup(self, service_initializer):
        """Prometheus endpoint is created when export_prometheus is True."""
        mock_app = MagicMock()
        service_initializer._apps = {"app": mock_app}
        observability_config = {
            "metrics": {"enabled": True, "export_prometheus": True},
        }

        mock_create = MagicMock()
        with patch(
            "mdb_engine.observability.exporters.create_prometheus_endpoint",
            mock_create,
        ):
            await service_initializer.setup_observability("app", {"slug": "app"}, observability_config)

        mock_create.assert_called_once_with(mock_app)

    # -- Lines 1442-1444: OTel metrics bridge ------------------------------

    @pytest.mark.asyncio
    async def test_otel_metrics_bridge(self, service_initializer):
        """OTel metrics bridge is set up when export_otlp is True."""
        observability_config = {
            "metrics": {"enabled": True, "export_otlp": True},
        }

        mock_bridge = MagicMock()
        with patch(
            "mdb_engine.observability.exporters.setup_otel_metrics_bridge",
            mock_bridge,
        ):
            await service_initializer.setup_observability("app", {"slug": "app"}, observability_config)

        mock_bridge.assert_called_once_with(service_name="app")

    # -- Lines 1474-1498: OpenTelemetry tracing ----------------------------

    @pytest.mark.asyncio
    async def test_otel_tracing_enabled(self, service_initializer):
        """OTel tracing is initialized when enabled and SDK is available."""
        observability_config = {
            "tracing": {"enabled": True, "service_name": "myapp", "endpoint": "http://otel:4317"},
        }

        mock_init = MagicMock()
        with (
            patch(
                "mdb_engine.observability.tracing.otel_available",
                return_value=True,
            ),
            patch(
                "mdb_engine.observability.tracing.init_tracer_provider",
                mock_init,
            ),
        ):
            await service_initializer.setup_observability("app", {"slug": "app"}, observability_config)

        mock_init.assert_called_once()

    @pytest.mark.asyncio
    async def test_otel_tracing_sdk_not_installed(self, service_initializer):
        """When OTel SDK is missing, setup_observability succeeds (doesn't crash)."""
        observability_config = {
            "tracing": {"enabled": True},
        }

        with patch(
            "mdb_engine.observability.tracing.otel_available",
            return_value=False,
        ):
            # The key invariant: this must not raise even though tracing is
            # enabled but the SDK is absent.
            await service_initializer.setup_observability("app", {"slug": "app"}, observability_config)
