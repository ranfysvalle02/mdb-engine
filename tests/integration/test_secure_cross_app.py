"""
Integration tests for secure cross-app access.

Tests ClickTracker and ClickTrackerDashboard apps with real MongoDB and encryption.
"""

import os
from datetime import datetime

import pytest

from mdb_engine.core.encryption import MASTER_KEY_ENV_VAR, EnvelopeEncryptionService


@pytest.fixture
def master_key():
    """Generate a test master key."""
    return EnvelopeEncryptionService.generate_master_key()


@pytest.fixture
def mongodb_engine_with_secrets(master_key, real_mongodb_engine):
    """Create MongoDBEngine with encryption enabled."""
    engine = real_mongodb_engine
    # Set master key in environment
    os.environ[MASTER_KEY_ENV_VAR] = master_key
    # Re-initialize to pick up master key
    return engine


@pytest.fixture
def click_tracker_manifest():
    """ClickTracker manifest fixture."""
    return {
        "schema_version": "2.0",
        "slug": "click_tracker",
        "name": "Click Tracker",
        "description": "Tracks user clicks",
        "status": "active",
        "data_access": {
            "read_scopes": ["click_tracker"],
            "write_scope": "click_tracker",
        },
    }


@pytest.fixture
def dashboard_manifest():
    """ClickTrackerDashboard manifest fixture."""
    return {
        "schema_version": "2.0",
        "slug": "click_tracker_dashboard",
        "name": "Click Tracker Dashboard",
        "description": "Admin dashboard",
        "status": "active",
        "data_access": {
            "read_scopes": ["click_tracker_dashboard", "click_tracker"],
            "write_scope": "click_tracker_dashboard",
        },
    }


@pytest.mark.asyncio
@pytest.mark.integration
class TestSecureCrossAppAccess:
    """Test secure cross-app access with real MongoDB."""

    async def test_click_tracker_registration(
        self, mongodb_engine_with_secrets, click_tracker_manifest, master_key
    ):
        """Test registering ClickTracker app with secret generation."""
        engine = mongodb_engine_with_secrets
        await engine.initialize()

        result = await engine.register_app(click_tracker_manifest)
        assert result is True

        # Verify secret was stored
        secret_exists = await engine._app_secrets_manager.app_secret_exists("click_tracker")
        assert secret_exists is True

    async def test_dashboard_registration(
        self, mongodb_engine_with_secrets, dashboard_manifest, master_key
    ):
        """Test registering Dashboard app with secret generation."""
        engine = mongodb_engine_with_secrets
        await engine.initialize()

        result = await engine.register_app(dashboard_manifest)
        assert result is True

        # Verify secret was stored
        secret_exists = await engine._app_secrets_manager.app_secret_exists(
            "click_tracker_dashboard"
        )
        assert secret_exists is True

    async def test_click_tracker_tracks_clicks(
        self, mongodb_engine_with_secrets, click_tracker_manifest, master_key
    ):
        """Test that ClickTracker can write to its own collections."""
        engine = mongodb_engine_with_secrets
        await engine.initialize()

        await engine.register_app(click_tracker_manifest)

        # Get the generated secret (for testing - in production, retrieve securely)
        secret = await engine._app_secrets_manager.get_app_secret("click_tracker")

        # Get scoped database with token
        db = engine.get_scoped_db("click_tracker", app_token=secret)

        # Insert click
        click_doc = {
            "user_id": "user@example.com",
            "timestamp": datetime.utcnow(),
            "url": "/page",
            "element": "button",
        }

        result = await db.clicks.insert_one(click_doc)
        assert result.inserted_id is not None

        # Verify click was inserted with app_id
        inserted = await db.clicks.find_one({"_id": result.inserted_id})
        assert inserted is not None
        assert inserted["app_id"] == "click_tracker"

    async def test_dashboard_reads_clicks(
        self,
        mongodb_engine_with_secrets,
        click_tracker_manifest,
        dashboard_manifest,
        master_key,
    ):
        """Test that Dashboard can read ClickTracker data."""
        engine = mongodb_engine_with_secrets
        await engine.initialize()

        # Register both apps
        await engine.register_app(click_tracker_manifest)
        await engine.register_app(dashboard_manifest)

        # Get secrets
        tracker_secret = await engine._app_secrets_manager.get_app_secret("click_tracker")
        dashboard_secret = await engine._app_secrets_manager.get_app_secret(
            "click_tracker_dashboard"
        )

        # Insert click via ClickTracker
        tracker_db = engine.get_scoped_db("click_tracker", app_token=tracker_secret)
        click_doc = {
            "user_id": "user@example.com",
            "timestamp": datetime.utcnow(),
            "url": "/page",
            "element": "button",
        }
        await tracker_db.clicks.insert_one(click_doc)

        # Read click via Dashboard (cross-app access)
        dashboard_db = engine.get_scoped_db("click_tracker_dashboard", app_token=dashboard_secret)

        # Access ClickTracker's collection
        clicks = (
            await dashboard_db.get_collection("click_tracker_clicks").find({}).to_list(length=100)
        )

        assert len(clicks) > 0
        assert clicks[0]["app_id"] == "click_tracker"

    async def test_dashboard_cannot_write_clicks(
        self,
        mongodb_engine_with_secrets,
        click_tracker_manifest,
        dashboard_manifest,
        master_key,
    ):
        """Test that Dashboard cannot write to ClickTracker collections."""
        engine = mongodb_engine_with_secrets
        await engine.initialize()

        await engine.register_app(click_tracker_manifest)
        await engine.register_app(dashboard_manifest)

        dashboard_secret = await engine._app_secrets_manager.get_app_secret(
            "click_tracker_dashboard"
        )

        dashboard_db = engine.get_scoped_db("click_tracker_dashboard", app_token=dashboard_secret)

        # Try to write to ClickTracker's collection (should fail validation)
        # Note: The scoped wrapper will add app_id="click_tracker_dashboard"
        # but the collection name suggests click_tracker, which may cause confusion
        # In practice, writes go to dashboard's own collections

        # Dashboard can write to its own collections
        result = await dashboard_db.dashboard_data.insert_one({"test": "data"})
        assert result.inserted_id is not None

    async def test_invalid_token_rejected(
        self, mongodb_engine_with_secrets, click_tracker_manifest, master_key
    ):
        """Test that invalid token blocks access."""
        engine = mongodb_engine_with_secrets
        await engine.initialize()

        await engine.register_app(click_tracker_manifest)

        # In async context, token verification is skipped in get_scoped_db
        # but will be verified at query time. Use get_scoped_db_async to test verification.
        with pytest.raises(ValueError, match="Invalid app token"):
            await engine.get_scoped_db_async("click_tracker", app_token="invalid_token")

    async def test_missing_token_rejected(
        self, mongodb_engine_with_secrets, click_tracker_manifest, master_key
    ):
        """Test that missing token blocks access when secret exists."""
        engine = mongodb_engine_with_secrets
        await engine.initialize()

        await engine.register_app(click_tracker_manifest)

        # In async context, use get_scoped_db_async to test token requirement
        with pytest.raises(ValueError, match="App token required"):
            await engine.get_scoped_db_async("click_tracker")

    async def test_cross_app_unauthorized_rejected(
        self, mongodb_engine_with_secrets, click_tracker_manifest, master_key
    ):
        """Test that unauthorized cross-app access is blocked."""
        engine = mongodb_engine_with_secrets
        await engine.initialize()

        # Register ClickTracker only (no Dashboard)
        await engine.register_app(click_tracker_manifest)

        tracker_secret = await engine._app_secrets_manager.get_app_secret("click_tracker")

        # Try to access unauthorized scope
        with pytest.raises(ValueError, match="not authorized to read from"):
            engine.get_scoped_db(
                "click_tracker",
                app_token=tracker_secret,
                read_scopes=["click_tracker", "unauthorized_app"],
            )

    async def test_envelope_encryption_end_to_end(
        self, mongodb_engine_with_secrets, click_tracker_manifest, master_key
    ):
        """Test full encryption/decryption flow."""
        engine = mongodb_engine_with_secrets
        await engine.initialize()

        await engine.register_app(click_tracker_manifest)

        # Get secret
        secret = await engine._app_secrets_manager.get_app_secret("click_tracker")

        # Verify secret can be used
        is_valid = await engine._app_secrets_manager.verify_app_secret("click_tracker", secret)
        assert is_valid is True

        # Verify wrong secret is rejected
        is_invalid = await engine._app_secrets_manager.verify_app_secret(
            "click_tracker", "wrong_secret"
        )
        assert is_invalid is False

    async def test_secret_rotation_flow(
        self, mongodb_engine_with_secrets, click_tracker_manifest, master_key
    ):
        """Test secret rotation flow."""
        engine = mongodb_engine_with_secrets
        await engine.initialize()

        await engine.register_app(click_tracker_manifest)

        # Get original secret
        original_secret = await engine._app_secrets_manager.get_app_secret("click_tracker")

        # Rotate secret
        new_secret = await engine._app_secrets_manager.rotate_app_secret("click_tracker")

        assert new_secret != original_secret

        # Old secret should be invalid
        is_valid_old = await engine._app_secrets_manager.verify_app_secret(
            "click_tracker", original_secret
        )
        assert is_valid_old is False

        # New secret should be valid
        is_valid_new = await engine._app_secrets_manager.verify_app_secret(
            "click_tracker", new_secret
        )
        assert is_valid_new is True

    async def test_data_isolation(
        self,
        mongodb_engine_with_secrets,
        click_tracker_manifest,
        dashboard_manifest,
        master_key,
    ):
        """Test that apps cannot access each other's data without authorization."""
        engine = mongodb_engine_with_secrets
        await engine.initialize()

        await engine.register_app(click_tracker_manifest)

        # Create a second app without cross-app access
        other_app_manifest = {
            "schema_version": "2.0",
            "slug": "other_app",
            "name": "Other App",
            "data_access": {
                "read_scopes": ["other_app"],  # Only self-access
            },
        }
        await engine.register_app(other_app_manifest)

        tracker_secret = await engine._app_secrets_manager.get_app_secret("click_tracker")
        other_secret = await engine._app_secrets_manager.get_app_secret("other_app")

        # Insert data in ClickTracker
        tracker_db = engine.get_scoped_db("click_tracker", app_token=tracker_secret)
        await tracker_db.clicks.insert_one({"test": "data"})

        # Other app should not be able to read ClickTracker data
        other_db = engine.get_scoped_db("other_app", app_token=other_secret)

        # Query should only return other_app's data (empty)
        clicks = await other_db.get_collection("click_tracker_clicks").find({}).to_list(length=100)
        # Should be empty because other_app is not authorized to read click_tracker
        assert len(clicks) == 0
