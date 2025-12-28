"""
Pytest configuration and shared fixtures for MDB_ENGINE tests.

This module provides:
- Async test fixtures
- Mock MongoDB client fixtures
- Test data factories
- Common test utilities
"""

import asyncio
import os
from typing import Any, AsyncGenerator, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from motor.motor_asyncio import (AsyncIOMotorClient, AsyncIOMotorCollection,
                                 AsyncIOMotorDatabase)

# Set test secret key before importing engine components
if "FLASK_SECRET_KEY" not in os.environ:
    os.environ["FLASK_SECRET_KEY"] = "test_secret_key_for_testing_only_" + "x" * 32

# Import engine components for testing
from mdb_engine.core.engine import MongoDBEngine
from mdb_engine.database.scoped_wrapper import (ScopedCollectionWrapper,
                                                ScopedMongoWrapper)

# ============================================================================
# ASYNC FIXTURES
# ============================================================================


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def async_context():
    """Provide async context for tests."""
    yield


# ============================================================================
# MOCK MONGODB FIXTURES
# ============================================================================


@pytest.fixture
def mock_mongo_client() -> MagicMock:
    """Create a mock MongoDB client."""
    client = MagicMock(spec=AsyncIOMotorClient)
    client.admin = MagicMock()
    client.admin.command = AsyncMock(return_value={"ok": 1})

    # Mock database access via __getitem__
    # When assigned to __getitem__, Python calls it as type(client).__getitem__(client, db_name)
    # So we need to accept self as first arg and db_name as second
    def get_database(self, db_name):
        # self is the client instance (we can ignore it since we have closure over client)
        # db_name is the database name we want

        db = MagicMock(spec=AsyncIOMotorDatabase)
        db.client = client

        # Mock collection access with async methods
        def get_collection(name: str):
            collection = MagicMock(spec=AsyncIOMotorCollection)
            collection.name = name
            collection.database = db
            collection.replace_one = AsyncMock(
                return_value=MagicMock(modified_count=1, upserted_id="test_id")
            )
            collection.find_one = AsyncMock(return_value=None)
            collection.find = AsyncMock(
                return_value=AsyncMock(to_list=AsyncMock(return_value=[]))
            )
            collection.insert_one = AsyncMock(
                return_value=MagicMock(inserted_id="test_id")
            )
            collection.insert_many = AsyncMock(
                return_value=MagicMock(inserted_ids=["id1", "id2"])
            )
            collection.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
            collection.update_many = AsyncMock(return_value=MagicMock(modified_count=2))
            collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
            collection.delete_many = AsyncMock(return_value=MagicMock(deleted_count=2))
            collection.count_documents = AsyncMock(return_value=0)
            collection.aggregate = AsyncMock(
                return_value=AsyncMock(to_list=AsyncMock(return_value=[]))
            )
            collection.list_indexes = AsyncMock(
                return_value=AsyncMock(to_list=AsyncMock(return_value=[]))
            )
            collection.create_index = AsyncMock(return_value="test_index")
            collection.drop_index = AsyncMock()
            return collection

        # Use MockDatabaseWrapper pattern for dynamic collection access
        class MockDatabaseWrapper:
            def __init__(self, mock_db, get_collection_func, db_name: str):
                object.__setattr__(self, "_mock_db", mock_db)
                object.__setattr__(self, "_get_collection", get_collection_func)
                object.__setattr__(self, "client", mock_db.client)
                # Store name as a real attribute (not a mock)
                object.__setattr__(self, "name", db_name)

            def __getattr__(self, name):
                if name.startswith("_"):
                    raise AttributeError(
                        f"'{type(self).__name__}' object has no attribute '{name}'"
                    )
                return self._get_collection(name)

            def __getitem__(self, name):
                return self._get_collection(name)

        return MockDatabaseWrapper(db, get_collection, db_name)

    # Configure __getitem__ to use our function
    client.__getitem__ = get_database
    return client


@pytest.fixture
def mock_mongo_database(mock_mongo_client: MagicMock) -> MagicMock:
    """Create a mock MongoDB database."""
    db = MagicMock(spec=AsyncIOMotorDatabase)
    db.client = mock_mongo_client
    db.name = "test_db"

    # Mock collection access
    def get_collection(name: str):
        collection = MagicMock(spec=AsyncIOMotorCollection)
        collection.name = name
        collection.database = db
        collection.find = AsyncMock(
            return_value=AsyncMock(to_list=AsyncMock(return_value=[]))
        )
        collection.find_one = AsyncMock(return_value=None)
        collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id="test_id"))
        collection.insert_many = AsyncMock(
            return_value=MagicMock(inserted_ids=["id1", "id2"])
        )
        collection.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
        collection.update_many = AsyncMock(return_value=MagicMock(modified_count=2))
        collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
        collection.delete_many = AsyncMock(return_value=MagicMock(deleted_count=2))
        collection.count_documents = AsyncMock(return_value=0)
        collection.aggregate = AsyncMock(
            return_value=AsyncMock(to_list=AsyncMock(return_value=[]))
        )
        collection.list_indexes = AsyncMock(
            return_value=AsyncMock(to_list=AsyncMock(return_value=[]))
        )
        collection.create_index = AsyncMock(return_value="test_index")
        collection.drop_index = AsyncMock()
        collection.create_search_index = AsyncMock()
        collection.update_search_index = AsyncMock()
        collection.drop_search_index = AsyncMock()
        collection.list_search_indexes = AsyncMock(
            return_value=AsyncMock(to_list=AsyncMock(return_value=[]))
        )
        return collection

    # MagicMock doesn't allow setting __getattr__ directly on instances
    # Create a simple wrapper that properly handles __getattr__
    class MockDatabaseWrapper:
        """Wrapper that allows __getattr__ for dynamic collection access."""

        def __init__(self, mock_db, get_collection_func):
            # Use object.__setattr__ to bypass __setattr__ during init
            object.__setattr__(self, "_mock_db", mock_db)
            object.__setattr__(self, "_get_collection", get_collection_func)
            # Copy essential attributes
            object.__setattr__(self, "client", mock_db.client)
            object.__setattr__(self, "name", mock_db.name)

        def __getattr__(self, name):
            if name.startswith("_"):
                raise AttributeError(
                    f"'{type(self).__name__}' object has no attribute '{name}'"
                )
            # Return collection for any attribute access
            return self._get_collection(name)

        def __getitem__(self, name):
            return self._get_collection(name)

        # Proxy method calls and attribute access to underlying mock
        def __getattribute__(self, name):
            # Handle our internal attributes first
            if name in ("_mock_db", "_get_collection", "__class__", "__dict__"):
                return object.__getattribute__(self, name)
            # Try our own attributes
            try:
                return object.__getattribute__(self, name)
            except AttributeError:
                # Proxy everything else to mock_db
                mock_db = object.__getattribute__(self, "_mock_db")
                return getattr(mock_db, name)

    db.__getitem__ = lambda self, name: get_collection(name)
    # Return wrapped database that supports __getattr__
    wrapped = MockDatabaseWrapper(db, get_collection)
    return wrapped


@pytest.fixture
def mock_mongo_collection() -> MagicMock:
    """Create a mock MongoDB collection."""
    collection = MagicMock(spec=AsyncIOMotorCollection)
    collection.name = "test_collection"
    collection.find = AsyncMock(
        return_value=AsyncMock(to_list=AsyncMock(return_value=[]))
    )
    collection.find_one = AsyncMock(return_value=None)
    collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id="test_id"))
    collection.insert_many = AsyncMock(
        return_value=MagicMock(inserted_ids=["id1", "id2"])
    )
    collection.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    collection.update_many = AsyncMock(return_value=MagicMock(modified_count=2))
    collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
    collection.delete_many = AsyncMock(return_value=MagicMock(deleted_count=2))
    collection.count_documents = AsyncMock(return_value=0)
    collection.aggregate = AsyncMock(
        return_value=AsyncMock(to_list=AsyncMock(return_value=[]))
    )
    collection.list_indexes = AsyncMock(
        return_value=AsyncMock(to_list=AsyncMock(return_value=[]))
    )
    collection.create_index = AsyncMock(return_value="test_index")
    collection.drop_index = AsyncMock()
    return collection


# ============================================================================
# MONGODB ENGINE FIXTURES
# ============================================================================


@pytest.fixture
def mongodb_engine_config() -> Dict[str, Any]:
    """Provide default configuration for MongoDBEngine."""
    return {
        "mongo_uri": "mongodb://localhost:27017",
        "db_name": "test_db",
        "max_pool_size": 10,
        "min_pool_size": 1,
    }


@pytest.fixture
async def mongodb_engine(
    mock_mongo_client: MagicMock, mongodb_engine_config: Dict[str, Any]
) -> AsyncGenerator[MongoDBEngine, None]:
    """Create a MongoDBEngine instance with mocked MongoDB client."""
    # Patch AsyncIOMotorClient in connection module where it's actually used
    with patch(
        "mdb_engine.core.connection.AsyncIOMotorClient", return_value=mock_mongo_client
    ):
        engine = MongoDBEngine(**mongodb_engine_config)
        await engine.initialize()
        yield engine
        await engine.shutdown()


@pytest.fixture
async def uninitialized_mongodb_engine(
    mongodb_engine_config: Dict[str, Any]
) -> MongoDBEngine:
    """Create an uninitialized MongoDBEngine instance."""
    return MongoDBEngine(**mongodb_engine_config)


# ============================================================================
# SCOPED WRAPPER FIXTURES
# ============================================================================


@pytest.fixture
def scoped_db_config() -> Dict[str, Any]:
    """Provide default configuration for ScopedMongoWrapper."""
    return {
        "read_scopes": ["test_experiment"],
        "write_scope": "test_experiment",
        "auto_index": True,
    }


@pytest.fixture
async def scoped_db(
    mock_mongo_database: MagicMock, scoped_db_config: Dict[str, Any]
) -> ScopedMongoWrapper:
    """Create a ScopedMongoWrapper instance with mocked database."""
    return ScopedMongoWrapper(real_db=mock_mongo_database, **scoped_db_config)


@pytest.fixture
async def scoped_collection(
    mock_mongo_collection: MagicMock, scoped_db_config: Dict[str, Any]
) -> ScopedCollectionWrapper:
    """Create a ScopedCollectionWrapper instance with mocked collection."""
    return ScopedCollectionWrapper(
        real_collection=mock_mongo_collection, **scoped_db_config
    )


# ============================================================================
# TEST DATA FACTORIES
# ============================================================================


@pytest.fixture
def sample_manifest() -> Dict[str, Any]:
    """Provide a sample valid manifest for testing."""
    return {
        "schema_version": "2.0",
        "slug": "test_experiment",
        "name": "Test Experiment",
        "description": "A test experiment for unit testing",
        "status": "active",
        "auth_required": False,
        "data_scope": ["self"],
        "developer_id": "test@example.com",
    }


@pytest.fixture
def sample_manifest_v1() -> Dict[str, Any]:
    """Provide a sample v1.0 manifest for testing."""
    return {
        "schema_version": "1.0",
        "slug": "test_experiment_v1",
        "name": "Test Experiment V1",
        "description": "A test experiment with v1 schema",
        "status": "active",
        "auth_required": False,
        "data_scope": ["self"],
    }


@pytest.fixture
def sample_manifest_with_indexes() -> Dict[str, Any]:
    """Provide a sample manifest with managed indexes."""
    return {
        "schema_version": "2.0",
        "slug": "test_experiment",
        "name": "Test Experiment",
        "description": "A test experiment with indexes",
        "status": "active",
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


@pytest.fixture
def invalid_manifest() -> Dict[str, Any]:
    """Provide an invalid manifest for testing validation."""
    return {
        "slug": "invalid-experiment!@#",  # Invalid characters
        "name": "",  # Empty name
        "status": "invalid_status",  # Invalid enum value
    }


@pytest.fixture
def sample_websocket_config() -> Dict[str, Any]:
    """Provide a sample WebSocket configuration for testing."""
    return {
        "endpoint1": {
            "path": "/ws/endpoint1",
            "auth": {"required": True},
            "ping_interval": 30,
        },
        "endpoint2": {
            "path": "/ws/endpoint2",
            "require_auth": False,  # Backward compatibility format
            "ping_interval": 20,
        },
    }


@pytest.fixture
def sample_observability_config() -> Dict[str, Any]:
    """Provide a sample observability configuration for testing."""
    return {
        "health_checks": {
            "enabled": True,
            "endpoint": "/health",
            "interval_seconds": 30,
        },
        "metrics": {
            "enabled": True,
            "collect_operation_metrics": True,
            "collect_performance_metrics": True,
            "custom_metrics": ["custom_metric1", "custom_metric2"],
        },
        "logging": {
            "level": "INFO",
            "format": "json",
            "include_request_id": True,
        },
    }


@pytest.fixture
def mock_fastapi_app() -> MagicMock:
    """Create a mock FastAPI application for testing."""
    app = MagicMock()
    app.routes = []
    app.include_router = MagicMock()
    return app


@pytest.fixture
def mock_memory_service() -> MagicMock:
    """Create a mock memory service for testing."""
    service = MagicMock()
    service.memory = MagicMock()
    service.add = AsyncMock(return_value=[])
    service.search = AsyncMock(return_value=[])
    return service


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================


def assert_experiment_id_in_document(doc: Dict[str, Any], expected_scope: str) -> None:
    """Assert that a document has the correct experiment_id."""
    assert "experiment_id" in doc, "Document missing experiment_id field"
    assert (
        doc["experiment_id"] == expected_scope
    ), f"Expected experiment_id={expected_scope}, got {doc.get('experiment_id')}"


def assert_filter_has_experiment_scope(
    filter_dict: Dict[str, Any], expected_scopes: list
) -> None:
    """Assert that a filter includes experiment_id scoping."""
    if "$and" in filter_dict:
        # Check if any $and condition has experiment_id
        and_conditions = filter_dict["$and"]
        has_scope = any(
            isinstance(cond, dict) and "experiment_id" in cond
            for cond in and_conditions
        )
        assert has_scope, "Filter missing experiment_id scope in $and conditions"
    elif "experiment_id" in filter_dict:
        # Direct experiment_id filter
        scope_filter = filter_dict["experiment_id"]
        if "$in" in scope_filter:
            assert set(scope_filter["$in"]) == set(
                expected_scopes
            ), f"Expected scopes {expected_scopes}, got {scope_filter['$in']}"
    else:
        pytest.fail("Filter missing experiment_id scope")


# ============================================================================
# PATCHES AND MOCKS
# ============================================================================


@pytest.fixture
def mock_authorization_provider():
    """Create a mock authorization provider."""
    provider = AsyncMock()
    provider.check = AsyncMock(return_value=True)
    return provider


@pytest.fixture
def mock_task_manager():
    """Create a mock task manager."""
    manager = AsyncMock()
    manager.create_task = AsyncMock()
    return manager


# ============================================================================
# ENVIRONMENT VARIABLES
# ============================================================================


@pytest.fixture(autouse=True)
def reset_environment(monkeypatch):
    """Reset environment variables before each test."""
    # Clear any test-specific env vars
    env_vars_to_clear = [
        "MONGO_URI",
        "DB_NAME",
        "MONGO_MAX_POOL_SIZE",
        "MONGO_MIN_POOL_SIZE",
    ]
    for var in env_vars_to_clear:
        monkeypatch.delenv(var, raising=False)
    yield
    # Cleanup happens automatically


# ============================================================================
# TESTCONTAINERS FIXTURES (Real MongoDB for Integration Tests)
# ============================================================================


@pytest.fixture(scope="session")
def mongodb_container():
    """
    Start MongoDB Atlas Local container for integration tests.

    Uses mongodb/mongodb-atlas-local:latest image to match examples.
    Session-scoped: container starts once and is reused for all integration tests.
    """
    try:
        from testcontainers.mongodb import MongoDbContainer
    except ImportError:
        pytest.skip(
            "testcontainers not installed. Install with: pip install -e '.[test]'"
        )

    # Use MongoDB Atlas Local image (matches examples)
    with MongoDbContainer(image="mongodb/mongodb-atlas-local:latest") as container:
        yield container


@pytest.fixture
def mongodb_connection_string(mongodb_container):
    """
    Get the correct MongoDB connection string for the test container.

    Returns a connection string using localhost with the exposed port,
    which is required for MongoDB Atlas Local containers.
    """
    exposed_port = mongodb_container.get_exposed_port(27017)
    return f"mongodb://localhost:{exposed_port}/?directConnection=true"


@pytest.fixture
async def real_mongo_client(mongodb_container):
    """
    Create a real MongoDB client connected to testcontainer.

    Returns an AsyncIOMotorClient connected to the MongoDB container.
    Automatically closes the client after the test.
    """
    from motor.motor_asyncio import AsyncIOMotorClient

    # MongoDB Atlas Local containers need to use localhost with exposed port
    # instead of the container's internal hostname
    exposed_port = mongodb_container.get_exposed_port(27017)
    connection_string = f"mongodb://localhost:{exposed_port}/?directConnection=true"
    client = AsyncIOMotorClient(connection_string)

    # Verify connection
    try:
        await client.admin.command("ping")
    except (ConnectionError, RuntimeError, OSError) as e:
        pytest.fail(f"Failed to connect to MongoDB container: {e}")

    yield client

    # Cleanup
    client.close()


@pytest.fixture
async def real_mongo_db(real_mongo_client):
    """
    Create a real MongoDB database for testing.

    Uses a unique database name per test run to avoid conflicts.
    Automatically drops the database after the test.
    """
    import os

    # Unique database name per test process
    db_name = f"test_db_{os.getpid()}_{id(real_mongo_client)}"
    db = real_mongo_client[db_name]

    yield db

    # Cleanup: drop test database
    try:
        await real_mongo_client.drop_database(db_name)
    except (ConnectionError, RuntimeError, OSError):
        pass  # Ignore cleanup errors


@pytest.fixture
async def real_mongodb_engine(mongodb_container, real_mongo_db):
    """
    Create a fully initialized MongoDBEngine instance with real MongoDB.

    Uses the mongodb_container to get connection string and real_mongo_db for database name.
    Automatically shuts down the engine after the test.
    """
    # MongoDB Atlas Local containers need to use localhost with exposed port
    # instead of the container's internal hostname
    exposed_port = mongodb_container.get_exposed_port(27017)
    mongo_uri = f"mongodb://localhost:{exposed_port}/?directConnection=true"

    engine = MongoDBEngine(
        mongo_uri=mongo_uri,
        db_name=real_mongo_db.name,
        max_pool_size=5,
        min_pool_size=1,
    )

    await engine.initialize()
    yield engine
    await engine.shutdown()


@pytest.fixture
async def clean_test_db(real_mongo_db):
    """
    Clean test database fixture that drops all collections between tests.

    This ensures test isolation by cleaning up data after each test.
    """
    # Get all collection names
    collection_names = await real_mongo_db.list_collection_names()

    yield real_mongo_db

    # Cleanup: drop all collections
    for collection_name in collection_names:
        try:
            await real_mongo_db.drop_collection(collection_name)
        except (ConnectionError, RuntimeError, OSError):
            pass  # Ignore cleanup errors
