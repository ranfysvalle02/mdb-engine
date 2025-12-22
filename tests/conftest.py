"""
Pytest configuration and shared fixtures for MDB_ENGINE tests.

This module provides:
- Async test fixtures
- Mock MongoDB client fixtures
- Test data factories
- Common test utilities
"""
import asyncio
import pytest
from typing import AsyncGenerator, Dict, Any
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

# Import engine components for testing
from mdb_engine.core.engine import MongoDBEngine
from mdb_engine.database.scoped_wrapper import ScopedMongoWrapper, ScopedCollectionWrapper
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase, AsyncIOMotorCollection


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
        collection.find = AsyncMock(return_value=AsyncMock(to_list=AsyncMock(return_value=[])))
        collection.find_one = AsyncMock(return_value=None)
        collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id="test_id"))
        collection.insert_many = AsyncMock(return_value=MagicMock(inserted_ids=["id1", "id2"]))
        collection.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
        collection.update_many = AsyncMock(return_value=MagicMock(modified_count=2))
        collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
        collection.delete_many = AsyncMock(return_value=MagicMock(deleted_count=2))
        collection.count_documents = AsyncMock(return_value=0)
        collection.aggregate = AsyncMock(return_value=AsyncMock(to_list=AsyncMock(return_value=[])))
        collection.list_indexes = AsyncMock(return_value=AsyncMock(to_list=AsyncMock(return_value=[])))
        collection.create_index = AsyncMock(return_value="test_index")
        collection.drop_index = AsyncMock()
        collection.create_search_index = AsyncMock()
        collection.update_search_index = AsyncMock()
        collection.drop_search_index = AsyncMock()
        collection.list_search_indexes = AsyncMock(return_value=AsyncMock(to_list=AsyncMock(return_value=[])))
        return collection
    
    db.__getattr__ = lambda self, name: get_collection(name)
    db.__getitem__ = lambda self, name: get_collection(name)
    return db


@pytest.fixture
def mock_mongo_collection() -> MagicMock:
    """Create a mock MongoDB collection."""
    collection = MagicMock(spec=AsyncIOMotorCollection)
    collection.name = "test_collection"
    collection.find = AsyncMock(return_value=AsyncMock(to_list=AsyncMock(return_value=[])))
    collection.find_one = AsyncMock(return_value=None)
    collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id="test_id"))
    collection.insert_many = AsyncMock(return_value=MagicMock(inserted_ids=["id1", "id2"]))
    collection.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    collection.update_many = AsyncMock(return_value=MagicMock(modified_count=2))
    collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
    collection.delete_many = AsyncMock(return_value=MagicMock(deleted_count=2))
    collection.count_documents = AsyncMock(return_value=0)
    collection.aggregate = AsyncMock(return_value=AsyncMock(to_list=AsyncMock(return_value=[])))
    collection.list_indexes = AsyncMock(return_value=AsyncMock(to_list=AsyncMock(return_value=[])))
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
async def mongodb_engine(mock_mongo_client: MagicMock, mongodb_engine_config: Dict[str, Any]) -> AsyncGenerator[MongoDBEngine, None]:
    """Create a MongoDBEngine instance with mocked MongoDB client."""
    with patch('mdb_engine.core.engine.AsyncIOMotorClient', return_value=mock_mongo_client):
        engine = MongoDBEngine(**mongodb_engine_config)
        await engine.initialize()
        yield engine
        await engine.shutdown()


@pytest.fixture
async def uninitialized_mongodb_engine(mongodb_engine_config: Dict[str, Any]) -> MongoDBEngine:
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
async def scoped_db(mock_mongo_database: MagicMock, scoped_db_config: Dict[str, Any]) -> ScopedMongoWrapper:
    """Create a ScopedMongoWrapper instance with mocked database."""
    return ScopedMongoWrapper(
        real_db=mock_mongo_database,
        **scoped_db_config
    )


@pytest.fixture
async def scoped_collection(mock_mongo_collection: MagicMock, scoped_db_config: Dict[str, Any]) -> ScopedCollectionWrapper:
    """Create a ScopedCollectionWrapper instance with mocked collection."""
    return ScopedCollectionWrapper(
        real_collection=mock_mongo_collection,
        **scoped_db_config
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


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def assert_experiment_id_in_document(doc: Dict[str, Any], expected_scope: str) -> None:
    """Assert that a document has the correct experiment_id."""
    assert "experiment_id" in doc, "Document missing experiment_id field"
    assert doc["experiment_id"] == expected_scope, f"Expected experiment_id={expected_scope}, got {doc.get('experiment_id')}"


def assert_filter_has_experiment_scope(filter_dict: Dict[str, Any], expected_scopes: list) -> None:
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
            assert set(scope_filter["$in"]) == set(expected_scopes), \
                f"Expected scopes {expected_scopes}, got {scope_filter['$in']}"
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

