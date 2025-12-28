"""
Unit tests for IndexManager.

Tests index creation, validation, and error handling.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import (ConnectionFailure, InvalidOperation,
                            OperationFailure, ServerSelectionTimeoutError)

from mdb_engine.core.index_management import IndexManager


@pytest.fixture
def mock_mongo_database():
    """Create a mock MongoDB database."""
    db = MagicMock(spec=AsyncIOMotorDatabase)
    return db


@pytest.fixture
def sample_manifest():
    """Sample manifest with managed indexes."""
    return {
        "slug": "test_app",
        "managed_indexes": {
            "users": [
                {"name": "email_idx", "type": "regular", "keys": [("email", 1)]},
                {"name": "name_idx", "type": "regular", "keys": [("name", 1)]},
            ]
        },
    }


class TestIndexManager:
    """Test IndexManager functionality."""

    @pytest.mark.asyncio
    async def test_create_app_indexes_no_managed_indexes(self, mock_mongo_database):
        """Test create_app_indexes with no managed_indexes (lines 57-62)."""
        manager = IndexManager(mock_mongo_database)
        manifest = {"slug": "test_app"}

        await manager.create_app_indexes("test_app", manifest)

        # Should complete without error

    @pytest.mark.asyncio
    async def test_create_app_indexes_invalid_validation(self, mock_mongo_database):
        """Test create_app_indexes with invalid index validation (lines 74-79)."""
        manager = IndexManager(mock_mongo_database)
        manifest = {
            "slug": "test_app",
            "managed_indexes": {"users": "invalid"},  # Invalid format
        }

        with patch(
            "mdb_engine.core.manifest.validate_managed_indexes",
            return_value=(False, "Invalid format"),
        ):
            await manager.create_app_indexes("test_app", manifest)

        # Should complete without error (validation failed)

    @pytest.mark.asyncio
    async def test_create_app_indexes_invalid_collection_name(
        self, mock_mongo_database
    ):
        """Test create_app_indexes with invalid collection name (lines 91-98)."""
        manager = IndexManager(mock_mongo_database)
        manifest = {
            "slug": "test_app",
            "managed_indexes": {
                "": [{"name": "idx1", "type": "regular"}],  # Empty collection name
                None: [{"name": "idx2", "type": "regular"}],  # None collection name
            },
        }

        with patch(
            "mdb_engine.core.manifest.validate_managed_indexes",
            return_value=(True, None),
        ):
            await manager.create_app_indexes("test_app", manifest)

        # Should skip invalid collections

    @pytest.mark.asyncio
    async def test_create_app_indexes_invalid_index_def(self, mock_mongo_database):
        """Test create_app_indexes with invalid index definition (lines 113-119)."""
        manager = IndexManager(mock_mongo_database)
        manifest = {
            "slug": "test_app",
            "managed_indexes": {
                "users": [
                    {"name": "", "type": "regular"},  # Missing name
                    {"name": "idx2"},  # Missing type
                    {"name": "idx3", "type": None},  # None type
                ]
            },
        }

        with patch(
            "mdb_engine.core.manifest.validate_managed_indexes",
            return_value=(True, None),
        ):
            await manager.create_app_indexes("test_app", manifest)

        # Should skip invalid index definitions

    @pytest.mark.asyncio
    async def test_create_app_indexes_no_valid_defs(self, mock_mongo_database):
        """Test create_app_indexes with no valid index definitions (lines 126-131)."""
        manager = IndexManager(mock_mongo_database)
        manifest = {
            "slug": "test_app",
            "managed_indexes": {
                "users": [
                    {"name": "", "type": "regular"},  # All invalid
                ]
            },
        }

        with patch(
            "mdb_engine.core.manifest.validate_managed_indexes",
            return_value=(True, None),
        ):
            await manager.create_app_indexes("test_app", manifest)

        # Should skip collection with no valid definitions

    @pytest.mark.asyncio
    async def test_create_app_indexes_success(
        self, mock_mongo_database, sample_manifest
    ):
        """Test successful index creation (lines 137-149)."""
        manager = IndexManager(mock_mongo_database)

        with patch(
            "mdb_engine.core.manifest.validate_managed_indexes",
            return_value=(True, None),
        ):
            with patch(
                "mdb_engine.core.index_management.run_index_creation_for_collection"
            ) as mock_create:
                mock_create.return_value = AsyncMock()
                await manager.create_app_indexes("test_app", sample_manifest)

                # Should have called run_index_creation_for_collection
                assert mock_create.called

    @pytest.mark.asyncio
    async def test_create_app_indexes_operation_failure(
        self, mock_mongo_database, sample_manifest
    ):
        """Test handling OperationFailure during index creation (lines 150-163)."""
        manager = IndexManager(mock_mongo_database)

        with patch(
            "mdb_engine.core.manifest.validate_managed_indexes",
            return_value=(True, None),
        ):
            with patch(
                "mdb_engine.core.index_management.run_index_creation_for_collection"
            ) as mock_create:
                mock_create.side_effect = OperationFailure("Index creation failed")

                with pytest.raises(OperationFailure):
                    await manager.create_app_indexes("test_app", sample_manifest)

    @pytest.mark.asyncio
    async def test_create_app_indexes_connection_failure(
        self, mock_mongo_database, sample_manifest
    ):
        """Test handling ConnectionFailure during index creation."""
        manager = IndexManager(mock_mongo_database)

        with patch(
            "mdb_engine.core.manifest.validate_managed_indexes",
            return_value=(True, None),
        ):
            with patch(
                "mdb_engine.core.index_management.run_index_creation_for_collection"
            ) as mock_create:
                mock_create.side_effect = ConnectionFailure("Connection failed")

                with pytest.raises(ConnectionFailure):
                    await manager.create_app_indexes("test_app", sample_manifest)

    @pytest.mark.asyncio
    async def test_create_app_indexes_timeout_error(
        self, mock_mongo_database, sample_manifest
    ):
        """Test handling ServerSelectionTimeoutError during index creation."""
        manager = IndexManager(mock_mongo_database)

        with patch(
            "mdb_engine.core.manifest.validate_managed_indexes",
            return_value=(True, None),
        ):
            with patch(
                "mdb_engine.core.index_management.run_index_creation_for_collection"
            ) as mock_create:
                mock_create.side_effect = ServerSelectionTimeoutError("Timeout")

                with pytest.raises(ServerSelectionTimeoutError):
                    await manager.create_app_indexes("test_app", sample_manifest)

    @pytest.mark.asyncio
    async def test_create_app_indexes_invalid_operation(
        self, mock_mongo_database, sample_manifest
    ):
        """Test handling InvalidOperation during index creation."""
        manager = IndexManager(mock_mongo_database)

        with patch(
            "mdb_engine.core.manifest.validate_managed_indexes",
            return_value=(True, None),
        ):
            with patch(
                "mdb_engine.core.index_management.run_index_creation_for_collection"
            ) as mock_create:
                mock_create.side_effect = InvalidOperation("Invalid operation")

                with pytest.raises(InvalidOperation):
                    await manager.create_app_indexes("test_app", sample_manifest)

    @pytest.mark.asyncio
    async def test_create_app_indexes_value_error(
        self, mock_mongo_database, sample_manifest
    ):
        """Test handling ValueError during index creation."""
        manager = IndexManager(mock_mongo_database)

        with patch(
            "mdb_engine.core.manifest.validate_managed_indexes",
            return_value=(True, None),
        ):
            with patch(
                "mdb_engine.core.index_management.run_index_creation_for_collection"
            ) as mock_create:
                mock_create.side_effect = ValueError("Invalid value")

                with pytest.raises(ValueError):
                    await manager.create_app_indexes("test_app", sample_manifest)

    @pytest.mark.asyncio
    async def test_create_app_indexes_type_error(
        self, mock_mongo_database, sample_manifest
    ):
        """Test handling TypeError during index creation."""
        manager = IndexManager(mock_mongo_database)

        with patch(
            "mdb_engine.core.manifest.validate_managed_indexes",
            return_value=(True, None),
        ):
            with patch(
                "mdb_engine.core.index_management.run_index_creation_for_collection"
            ) as mock_create:
                mock_create.side_effect = TypeError("Invalid type")

                with pytest.raises(TypeError):
                    await manager.create_app_indexes("test_app", sample_manifest)
