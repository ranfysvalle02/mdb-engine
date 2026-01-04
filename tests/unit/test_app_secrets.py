"""
Unit tests for AppSecretsManager.

Tests secret storage, verification, rotation, and MongoDB operations.
"""

import base64
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from mdb_engine.core.app_secrets import SECRETS_COLLECTION_NAME, AppSecretsManager
from mdb_engine.core.encryption import EnvelopeEncryptionService


@pytest.fixture
def master_key():
    """Generate a test master key."""
    return EnvelopeEncryptionService.generate_master_key()


@pytest.fixture
def encryption_service(master_key):
    """Create encryption service with test master key."""
    import base64

    key_bytes = base64.b64decode(master_key.encode())
    return EnvelopeEncryptionService(key_bytes)


@pytest.fixture
def mock_mongo_db():
    """Create mock MongoDB database."""
    mock_db = MagicMock()
    mock_collection = AsyncMock()
    mock_db.__getitem__ = MagicMock(return_value=mock_collection)
    mock_db[SECRETS_COLLECTION_NAME] = mock_collection
    return mock_db, mock_collection


@pytest.fixture
def app_secrets_manager(mock_mongo_db, encryption_service):
    """Create AppSecretsManager instance."""
    mock_db, _ = mock_mongo_db
    return AppSecretsManager(mock_db, encryption_service)


@pytest.mark.asyncio
class TestAppSecretsManager:
    """Test AppSecretsManager functionality."""

    async def test_store_app_secret(self, app_secrets_manager, mock_mongo_db):
        """Test storing an encrypted app secret."""
        mock_db, mock_collection = mock_mongo_db
        mock_collection.find_one.return_value = None  # New secret
        mock_collection.insert_one = AsyncMock()

        await app_secrets_manager.store_app_secret("test_app", "my_secret")

        # Verify insert_one was called
        assert mock_collection.insert_one.called
        call_args = mock_collection.insert_one.call_args[0][0]
        assert call_args["_id"] == "test_app"
        assert "encrypted_secret" in call_args
        assert "encrypted_dek" in call_args
        assert call_args["algorithm"] == "AES-256-GCM"
        assert "created_at" in call_args
        assert call_args["rotation_count"] == 0

    async def test_store_app_secret_duplicate(self, app_secrets_manager, mock_mongo_db):
        """Test updating existing secret."""
        mock_db, mock_collection = mock_mongo_db
        existing_doc = {
            "_id": "test_app",
            "created_at": datetime.utcnow(),
            "rotation_count": 2,
        }
        mock_collection.find_one.return_value = existing_doc
        mock_collection.replace_one = AsyncMock()

        await app_secrets_manager.store_app_secret("test_app", "new_secret")

        # Verify replace_one was called
        assert mock_collection.replace_one.called
        # replace_one takes (filter, replacement) - check the replacement document
        replacement_doc = mock_collection.replace_one.call_args[0][1]
        assert replacement_doc["rotation_count"] == 3  # Incremented

    async def test_verify_app_secret_valid(
        self, app_secrets_manager, mock_mongo_db, encryption_service
    ):
        """Test verifying correct secret."""
        mock_db, mock_collection = mock_mongo_db

        # Create encrypted secret
        secret = "my_secret"
        encrypted_secret, encrypted_dek = encryption_service.encrypt_secret(secret)
        encrypted_secret_b64 = base64.b64encode(encrypted_secret).decode()
        encrypted_dek_b64 = base64.b64encode(encrypted_dek).decode()

        mock_collection.find_one.return_value = {
            "_id": "test_app",
            "encrypted_secret": encrypted_secret_b64,
            "encrypted_dek": encrypted_dek_b64,
        }

        result = await app_secrets_manager.verify_app_secret("test_app", secret)
        assert result is True

    async def test_verify_app_secret_invalid(
        self, app_secrets_manager, mock_mongo_db, encryption_service
    ):
        """Test verifying wrong secret."""
        mock_db, mock_collection = mock_mongo_db

        # Create encrypted secret
        secret = "my_secret"
        encrypted_secret, encrypted_dek = encryption_service.encrypt_secret(secret)
        encrypted_secret_b64 = base64.b64encode(encrypted_secret).decode()
        encrypted_dek_b64 = base64.b64encode(encrypted_dek).decode()

        mock_collection.find_one.return_value = {
            "_id": "test_app",
            "encrypted_secret": encrypted_secret_b64,
            "encrypted_dek": encrypted_dek_b64,
        }

        result = await app_secrets_manager.verify_app_secret("test_app", "wrong_secret")
        assert result is False

    async def test_verify_app_secret_not_found(self, app_secrets_manager, mock_mongo_db):
        """Test verifying secret for non-existent app."""
        mock_db, mock_collection = mock_mongo_db
        mock_collection.find_one.return_value = None

        result = await app_secrets_manager.verify_app_secret("nonexistent_app", "secret")
        assert result is False

    async def test_rotate_app_secret(self, app_secrets_manager, mock_mongo_db):
        """Test rotating an app secret."""
        mock_db, mock_collection = mock_mongo_db
        mock_collection.find_one.return_value = None  # New secret
        mock_collection.insert_one = AsyncMock()

        new_secret = await app_secrets_manager.rotate_app_secret("test_app")

        assert isinstance(new_secret, str)
        assert len(new_secret) > 0
        assert mock_collection.insert_one.called

    async def test_get_app_secret(self, app_secrets_manager, mock_mongo_db, encryption_service):
        """Test getting decrypted app secret."""
        mock_db, mock_collection = mock_mongo_db

        secret = "my_secret"
        encrypted_secret, encrypted_dek = encryption_service.encrypt_secret(secret)
        encrypted_secret_b64 = base64.b64encode(encrypted_secret).decode()
        encrypted_dek_b64 = base64.b64encode(encrypted_dek).decode()

        mock_collection.find_one.return_value = {
            "_id": "test_app",
            "encrypted_secret": encrypted_secret_b64,
            "encrypted_dek": encrypted_dek_b64,
        }

        retrieved_secret = await app_secrets_manager.get_app_secret("test_app")
        assert retrieved_secret == secret

    async def test_get_app_secret_not_found(self, app_secrets_manager, mock_mongo_db):
        """Test getting secret for non-existent app."""
        mock_db, mock_collection = mock_mongo_db
        mock_collection.find_one.return_value = None

        result = await app_secrets_manager.get_app_secret("nonexistent_app")
        assert result is None

    async def test_app_secret_exists(self, app_secrets_manager, mock_mongo_db):
        """Test checking if app secret exists."""
        mock_db, mock_collection = mock_mongo_db
        mock_collection.find_one.return_value = {"_id": "test_app"}

        result = await app_secrets_manager.app_secret_exists("test_app")
        assert result is True

    async def test_app_secret_not_exists(self, app_secrets_manager, mock_mongo_db):
        """Test checking if app secret doesn't exist."""
        mock_db, mock_collection = mock_mongo_db
        mock_collection.find_one.return_value = None

        result = await app_secrets_manager.app_secret_exists("test_app")
        assert result is False

    async def test_secret_storage_schema(self, app_secrets_manager, mock_mongo_db):
        """Test that stored secret has correct schema."""
        mock_db, mock_collection = mock_mongo_db
        mock_collection.find_one.return_value = None
        mock_collection.insert_one = AsyncMock()

        await app_secrets_manager.store_app_secret("test_app", "secret")

        call_args = mock_collection.insert_one.call_args[0][0]
        assert "_id" in call_args
        assert "encrypted_secret" in call_args
        assert "encrypted_dek" in call_args
        assert "algorithm" in call_args
        assert "created_at" in call_args
        assert "updated_at" in call_args
        assert "rotation_count" in call_args

    def test_verify_app_secret_sync(self, app_secrets_manager, mock_mongo_db, encryption_service):
        """Test synchronous secret verification."""
        mock_db, mock_collection = mock_mongo_db

        secret = "my_secret"
        encrypted_secret, encrypted_dek = encryption_service.encrypt_secret(secret)
        encrypted_secret_b64 = base64.b64encode(encrypted_secret).decode()
        encrypted_dek_b64 = base64.b64encode(encrypted_dek).decode()

        mock_collection.find_one = AsyncMock(
            return_value={
                "_id": "test_app",
                "encrypted_secret": encrypted_secret_b64,
                "encrypted_dek": encrypted_dek_b64,
            }
        )

        # Should work when no async context
        result = app_secrets_manager.verify_app_secret_sync("test_app", secret)
        assert result is True

    def test_app_secret_exists_sync(self, app_secrets_manager, mock_mongo_db):
        """Test synchronous secret existence check."""
        mock_db, mock_collection = mock_mongo_db
        mock_collection.find_one = AsyncMock(return_value={"_id": "test_app"})

        # Should work when no async context
        result = app_secrets_manager.app_secret_exists_sync("test_app")
        assert result is True
