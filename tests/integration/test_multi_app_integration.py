"""
Integration tests for multi-app mounting.

Tests the full multi-app mounting flow with real MongoDB connection.
"""

import json
import os

import pytest

# Set test secret key before importing engine components
if "MDB_ENGINE_JWT_SECRET" not in os.environ:
    os.environ["MDB_ENGINE_JWT_SECRET"] = "test_jwt_secret_for_testing_only_" + "x" * 32

if "MDB_ENGINE_MASTER_KEY" not in os.environ:
    os.environ["MDB_ENGINE_MASTER_KEY"] = "test_master_key_for_testing_only_" + "x" * 32


@pytest.mark.integration
class TestMultiAppIntegration:
    """Integration tests for multi-app mounting."""

    @pytest.fixture
    def temp_manifests(self, tmp_path):
        """Create temporary manifest files for integration testing."""
        manifests_dir = tmp_path / "manifests"
        manifests_dir.mkdir()

        # Create app1 manifest
        app1_manifest = {
            "schema_version": "2.0",
            "slug": "app1",
            "name": "App 1",
            "auth": {"mode": "app"},
            "data_access": {"read_scopes": ["app1"], "write_scope": "app1"},
        }
        app1_path = manifests_dir / "app1" / "manifest.json"
        app1_path.parent.mkdir()
        app1_path.write_text(json.dumps(app1_manifest))

        # Create app2 manifest
        app2_manifest = {
            "schema_version": "2.0",
            "slug": "app2",
            "name": "App 2",
            "auth": {"mode": "app"},
            "data_access": {"read_scopes": ["app2"], "write_scope": "app2"},
        }
        app2_path = manifests_dir / "app2" / "manifest.json"
        app2_path.parent.mkdir()
        app2_path.write_text(json.dumps(app2_manifest))

        return {
            "app1": app1_path,
            "app2": app2_path,
            "manifests_dir": manifests_dir,
        }

    @pytest.mark.asyncio
    async def test_multi_app_mounting_integration(self, mongodb_connection_string, temp_manifests):
        """Test full multi-app mounting integration."""
        import os

        from httpx import ASGITransport, AsyncClient

        from mdb_engine.core.engine import MongoDBEngine

        # Use unique database name per test
        db_name = f"test_multi_app_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)

        # Create multi-app
        app = engine.create_multi_app(
            apps=[
                {
                    "slug": "app1",
                    "manifest": temp_manifests["app1"],
                    "path_prefix": "/app1",
                },
                {
                    "slug": "app2",
                    "manifest": temp_manifests["app2"],
                    "path_prefix": "/app2",
                },
            ],
            title="Test Multi-App",
        )

        # Test that app was created
        assert app is not None
        assert app.title == "Test Multi-App"

        # Start lifespan and test endpoints
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                # Test health check endpoint
                response = await client.get("/health")
                assert response.status_code == 200
                health_data = response.json()
                assert "status" in health_data
                assert "mounted_apps" in health_data
                assert len(health_data["mounted_apps"]) == 2

                # Verify mounted apps are registered
                assert "app1" in health_data["mounted_apps"]
                assert "app2" in health_data["mounted_apps"]
                assert health_data["mounted_apps"]["app1"]["status"] == "mounted"
                assert health_data["mounted_apps"]["app2"]["status"] == "mounted"

        # Cleanup
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_multi_app_shared_auth_integration(self, mongodb_connection_string, tmp_path):
        """Test multi-app with shared auth integration."""
        import os

        from httpx import ASGITransport, AsyncClient

        from mdb_engine.core.engine import MongoDBEngine

        manifests_dir = tmp_path / "manifests"
        manifests_dir.mkdir()

        # Create auth hub manifest (shared auth)
        auth_hub_manifest = {
            "schema_version": "2.0",
            "slug": "auth-hub",
            "name": "Auth Hub",
            "auth": {
                "mode": "shared",
                "roles": ["viewer", "admin"],
                "require_role": "viewer",
                "public_routes": ["/health"],
            },
        }
        auth_hub_path = manifests_dir / "auth-hub" / "manifest.json"
        auth_hub_path.parent.mkdir()
        auth_hub_path.write_text(json.dumps(auth_hub_manifest))

        # Create SSO app manifest (shared auth)
        sso_app_manifest = {
            "schema_version": "2.0",
            "slug": "sso-app",
            "name": "SSO App",
            "auth": {
                "mode": "shared",
                "roles": ["viewer", "admin"],
                "require_role": "viewer",
                "public_routes": ["/health"],
            },
        }
        sso_app_path = manifests_dir / "sso-app" / "manifest.json"
        sso_app_path.parent.mkdir()
        sso_app_path.write_text(json.dumps(sso_app_manifest))

        # Use unique database name per test
        db_name = f"test_multi_app_shared_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)

        # Create multi-app with shared auth
        app = engine.create_multi_app(
            apps=[
                {
                    "slug": "auth-hub",
                    "manifest": auth_hub_path,
                    "path_prefix": "/auth-hub",
                },
                {
                    "slug": "sso-app",
                    "manifest": sso_app_path,
                    "path_prefix": "/sso-app",
                },
            ]
        )

        # Start lifespan
        async with app.router.lifespan_context(app):
            # Verify shared user pool was initialized
            assert hasattr(app.state, "user_pool")
            assert app.state.user_pool is not None

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                # Test health check
                response = await client.get("/health")
                assert response.status_code == 200

        # Cleanup
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_multi_app_manifest_based_integration(
        self, mongodb_connection_string, temp_manifests
    ):
        """Test multi-app from manifest file integration."""
        import os

        from httpx import ASGITransport, AsyncClient

        from mdb_engine.core.engine import MongoDBEngine

        # Create multi-app manifest
        multi_app_manifest = {
            "schema_version": "2.0",
            "multi_app": {
                "enabled": True,
                "apps": [
                    {
                        "slug": "app1",
                        "manifest": "./app1/manifest.json",
                        "path_prefix": "/app1",
                    },
                    {
                        "slug": "app2",
                        "manifest": "./app2/manifest.json",
                        "path_prefix": "/app2",
                    },
                ],
            },
        }
        multi_app_manifest_path = temp_manifests["manifests_dir"] / "multi_app_manifest.json"
        multi_app_manifest_path.write_text(json.dumps(multi_app_manifest))

        # Use unique database name per test
        db_name = f"test_multi_app_manifest_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)

        # Create multi-app from manifest
        app = engine.create_multi_app(multi_app_manifest=multi_app_manifest_path)

        assert app is not None

        # Start lifespan and test
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                # Test health check
                response = await client.get("/health")
                assert response.status_code == 200

                health_data = response.json()
                assert len(health_data["mounted_apps"]) == 2

        # Cleanup
        await engine.shutdown()
