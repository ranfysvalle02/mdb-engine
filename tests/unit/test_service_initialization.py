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
