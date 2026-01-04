"""
Integration tests for example apps (ClickTracker and ClickTrackerDashboard).

Tests end-to-end workflows with real apps.
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
    os.environ[MASTER_KEY_ENV_VAR] = master_key
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
class TestExampleApps:
    """Test example apps end-to-end."""

    async def test_click_tracker_app_startup(
        self, mongodb_engine_with_secrets, click_tracker_manifest, master_key
    ):
        """Test that ClickTracker app can start successfully."""
        engine = mongodb_engine_with_secrets
        await engine.initialize()

        result = await engine.register_app(click_tracker_manifest)
        assert result is True

        # Verify app is registered (check read_scopes mapping)
        assert "click_tracker" in engine._app_read_scopes
        assert "click_tracker" in engine._app_read_scopes["click_tracker"]

    async def test_click_tracker_track_endpoint_flow(
        self, mongodb_engine_with_secrets, click_tracker_manifest, master_key
    ):
        """Test ClickTracker track endpoint flow."""
        engine = mongodb_engine_with_secrets
        await engine.initialize()

        await engine.register_app(click_tracker_manifest)

        # Get secret
        secret = await engine._app_secrets_manager.get_app_secret("click_tracker")

        # Simulate track endpoint
        db = engine.get_scoped_db("click_tracker", app_token=secret)

        click_doc = {
            "user_id": "user@example.com",
            "timestamp": datetime.utcnow(),
            "url": "/page",
            "element": "button",
            "session_id": "session123",
        }

        result = await db.clicks.insert_one(click_doc)
        assert result.inserted_id is not None

        # Verify click was stored
        inserted = await db.clicks.find_one({"_id": result.inserted_id})
        assert inserted is not None
        assert inserted["user_id"] == "user@example.com"

    async def test_dashboard_analytics_endpoint_flow(
        self,
        mongodb_engine_with_secrets,
        click_tracker_manifest,
        dashboard_manifest,
        master_key,
    ):
        """Test Dashboard analytics endpoint flow."""
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

        # Insert clicks via ClickTracker
        tracker_db = engine.get_scoped_db("click_tracker", app_token=tracker_secret)
        for i in range(5):
            await tracker_db.clicks.insert_one(
                {
                    "user_id": f"user{i}@example.com",
                    "timestamp": datetime.utcnow(),
                    "url": f"/page{i}",
                    "element": "button",
                }
            )

        # Read analytics via Dashboard (cross-app access)
        dashboard_db = engine.get_scoped_db("click_tracker_dashboard", app_token=dashboard_secret)

        # Access ClickTracker's collection
        clicks = (
            await dashboard_db.get_collection("click_tracker_clicks").find({}).to_list(length=100)
        )

        assert len(clicks) == 5
        assert all(c["app_id"] == "click_tracker" for c in clicks)

    async def test_end_to_end_workflow(
        self,
        mongodb_engine_with_secrets,
        click_tracker_manifest,
        dashboard_manifest,
        master_key,
    ):
        """Test full workflow: track clicks, view dashboard."""
        engine = mongodb_engine_with_secrets
        await engine.initialize()

        # Register apps
        await engine.register_app(click_tracker_manifest)
        await engine.register_app(dashboard_manifest)

        # Get secrets
        tracker_secret = await engine._app_secrets_manager.get_app_secret("click_tracker")
        dashboard_secret = await engine._app_secrets_manager.get_app_secret(
            "click_tracker_dashboard"
        )

        # Step 1: Track clicks
        tracker_db = engine.get_scoped_db("click_tracker", app_token=tracker_secret)
        click_ids = []
        for i in range(10):
            result = await tracker_db.clicks.insert_one(
                {
                    "user_id": "user@example.com",
                    "timestamp": datetime.utcnow(),
                    "url": f"/page{i}",
                    "element": "button",
                }
            )
            click_ids.append(result.inserted_id)

        # Step 2: View analytics via dashboard
        dashboard_db = engine.get_scoped_db("click_tracker_dashboard", app_token=dashboard_secret)

        # Get all clicks
        all_clicks = (
            await dashboard_db.get_collection("click_tracker_clicks").find({}).to_list(length=100)
        )

        assert len(all_clicks) == 10

        # Step 3: Verify data isolation
        # Dashboard's own collections should be separate
        dashboard_data = await dashboard_db.dashboard_data.find({}).to_list(length=100)
        assert len(dashboard_data) == 0  # No dashboard data yet
