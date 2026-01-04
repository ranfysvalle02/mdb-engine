"""
Unit tests for engine app authentication integration.

Tests register_app secret generation and get_scoped_db token verification.
"""

import os
from unittest.mock import AsyncMock, patch

import pytest

from mdb_engine.core.encryption import MASTER_KEY_ENV_VAR, EnvelopeEncryptionService
from mdb_engine.core.engine import MongoDBEngine


@pytest.fixture
def master_key():
    """Generate a test master key."""
    return EnvelopeEncryptionService.generate_master_key()


@pytest.fixture
async def mongodb_engine_with_secrets(master_key, mock_mongo_client):
    """Create MongoDBEngine with encryption enabled."""
    # Set master key in environment
    os.environ[MASTER_KEY_ENV_VAR] = master_key
    # Patch AsyncIOMotorClient to use mock client (same as mongodb_engine fixture)
    with patch("mdb_engine.core.connection.AsyncIOMotorClient", return_value=mock_mongo_client):
        engine = MongoDBEngine(
            mongo_uri="mongodb://localhost:27017",
            db_name="test_db",
        )
        await engine.initialize()
        yield engine
        await engine.shutdown()


@pytest.mark.asyncio
class TestEngineAppAuthentication:
    """Test engine app authentication integration."""

    async def test_register_app_generates_secret(
        self, mongodb_engine_with_secrets, sample_manifest, master_key
    ):
        """Test that register_app generates and stores secret."""
        engine = mongodb_engine_with_secrets
        await engine.initialize()

        # Mock the secrets collection BEFORE register_app
        secrets_collection = engine._app_secrets_manager._secrets_collection
        secrets_collection.find_one = AsyncMock(return_value=None)
        secrets_collection.insert_one = AsyncMock()

        result = await engine.register_app(sample_manifest)

        assert result is True
        # Verify secret was stored
        assert secrets_collection.insert_one.called

    async def test_register_app_stores_secret(
        self, mongodb_engine_with_secrets, sample_manifest, master_key
    ):
        """Test that secret is stored encrypted."""
        engine = mongodb_engine_with_secrets
        await engine.initialize()

        # Mock the secrets collection BEFORE register_app
        secrets_collection = engine._app_secrets_manager._secrets_collection
        secrets_collection.find_one = AsyncMock(return_value=None)
        secrets_collection.insert_one = AsyncMock()

        await engine.register_app(sample_manifest)

        # Verify insert was called with encrypted data
        assert secrets_collection.insert_one.called
        call_args = secrets_collection.insert_one.call_args[0][0]
        assert "encrypted_secret" in call_args
        assert "encrypted_dek" in call_args

    async def test_get_scoped_db_requires_token_when_secret_exists(
        self, mongodb_engine_with_secrets, sample_manifest, master_key
    ):
        """Test that get_scoped_db requires token when app has stored secret."""
        engine = mongodb_engine_with_secrets
        await engine.initialize()

        # Register app (creates secret) - use test_app slug to match test expectations
        manifest = sample_manifest.copy()
        manifest["slug"] = "test_app"

        # Mock the secrets collection to simulate secret storage
        secrets_collection = engine._app_secrets_manager._secrets_collection
        # Sequence: 1) app_secret_exists check (None),
        #          2) store_app_secret check (None),
        #          3) get_scoped_db_async check (exists)
        secrets_collection.find_one = AsyncMock(
            side_effect=[
                None,  # First call: register_app -> app_secret_exists
                # (secret doesn't exist yet)
                None,  # Second call: register_app -> store_app_secret
                # -> find_one (check before insert)
                {
                    "_id": "test_app"
                },  # Third call: get_scoped_db_async -> app_secret_exists (secret exists)
            ]
        )
        secrets_collection.insert_one = AsyncMock()

        await engine.register_app(manifest)

        # In async context, use get_scoped_db_async to test token requirement
        with pytest.raises(ValueError, match="App token required"):
            await engine.get_scoped_db_async("test_app")

    async def test_get_scoped_db_valid_token(
        self, mongodb_engine_with_secrets, sample_manifest, master_key
    ):
        """Test that get_scoped_db works with valid token."""
        engine = mongodb_engine_with_secrets
        await engine.initialize()

        # Register app and get the generated secret
        await engine.register_app(sample_manifest)

        # Get the actual secret that was generated
        secret = await engine._app_secrets_manager.get_app_secret("test_app")

        # In async context, verification is skipped, so this should work
        # The token will be verified at query time
        db = engine.get_scoped_db("test_app", app_token=secret)
        assert db is not None

    async def test_get_scoped_db_invalid_token(
        self, mongodb_engine_with_secrets, sample_manifest, master_key
    ):
        """Test that get_scoped_db rejects invalid token."""
        engine = mongodb_engine_with_secrets
        await engine.initialize()

        await engine.register_app(sample_manifest)

        # In async context, use get_scoped_db_async to test token verification
        with pytest.raises(ValueError, match="Invalid app token"):
            await engine.get_scoped_db_async("test_app", app_token="invalid_token")

    async def test_get_scoped_db_uses_manifest_read_scopes(
        self, mongodb_engine_with_secrets, master_key
    ):
        """Test that get_scoped_db uses manifest read_scopes if not provided."""
        engine = mongodb_engine_with_secrets
        await engine.initialize()

        manifest = {
            "slug": "test_app",
            "name": "Test App",
            "data_access": {
                "read_scopes": ["test_app", "other_app"],
            },
        }

        await engine.register_app(manifest)

        # Get the actual secret
        secret = await engine._app_secrets_manager.get_app_secret("test_app")

        # In async context, verification is skipped but read_scopes are still used
        engine.get_scoped_db("test_app", app_token=secret)
        # Verify read_scopes were set from manifest
        assert "test_app" in engine._app_read_scopes["test_app"]
        assert "other_app" in engine._app_read_scopes["test_app"]

    async def test_get_scoped_db_validates_read_scopes(
        self, mongodb_engine_with_secrets, master_key
    ):
        """Test that get_scoped_db validates requested read_scopes."""
        engine = mongodb_engine_with_secrets
        await engine.initialize()

        manifest = {
            "slug": "test_app",
            "name": "Test App",
            "data_access": {
                "read_scopes": ["test_app"],
            },
        }

        await engine.register_app(manifest)

        # Get the actual secret
        secret = await engine._app_secrets_manager.get_app_secret("test_app")

        # Try to access unauthorized scope
        # In async context, token verification is skipped, but authorization is still checked
        with pytest.raises(ValueError, match="not authorized to read from"):
            engine.get_scoped_db(
                "test_app",
                app_token=secret,
                read_scopes=["test_app", "unauthorized_app"],
            )

    async def test_register_app_extracts_data_access(self, mongodb_engine_with_secrets, master_key):
        """Test that register_app extracts data_access from manifest."""
        engine = mongodb_engine_with_secrets
        await engine.initialize()

        manifest = {
            "slug": "test_app",
            "name": "Test App",
            "data_access": {
                "read_scopes": ["test_app", "shared_app"],
                "write_scope": "test_app",
            },
        }

        secrets_collection = engine._connection_manager.mongo_db["_mdb_engine_app_secrets"]
        secrets_collection.find_one = AsyncMock(return_value=None)
        secrets_collection.insert_one = AsyncMock()

        await engine.register_app(manifest)

        # Verify read_scopes were stored
        assert "test_app" in engine._app_read_scopes
        assert "test_app" in engine._app_read_scopes["test_app"]
        assert "shared_app" in engine._app_read_scopes["test_app"]

    async def test_register_app_warns_missing_apps(self, mongodb_engine_with_secrets, master_key):
        """Test that register_app warns if referenced apps don't exist."""
        engine = mongodb_engine_with_secrets
        await engine.initialize()

        manifest = {
            "slug": "test_app",
            "name": "Test App",
            "data_access": {
                "read_scopes": ["test_app", "nonexistent_app"],
            },
        }

        secrets_collection = engine._connection_manager.mongo_db["_mdb_engine_app_secrets"]
        secrets_collection.find_one = AsyncMock(return_value=None)
        secrets_collection.insert_one = AsyncMock()

        # Should still register (just warn)
        result = await engine.register_app(manifest)
        assert result is True
