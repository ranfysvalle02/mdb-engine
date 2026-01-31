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
                assert "apps" in health_data  # Changed from "mounted_apps" to "apps"
                assert len(health_data["apps"]) == 2

                # Verify mounted apps are registered
                assert "app1" in health_data["apps"]
                assert "app2" in health_data["apps"]
                assert health_data["apps"]["app1"]["status"] == "healthy"
                assert health_data["apps"]["app2"]["status"] == "healthy"

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
                assert "apps" in health_data  # Changed from "mounted_apps" to "apps"
                assert len(health_data["apps"]) == 2

        # Cleanup
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_public_routes_with_path_prefix(self, mongodb_connection_string, tmp_path):
        """Test that public routes work correctly when apps are mounted with path prefixes."""
        import os

        from httpx import ASGITransport, AsyncClient

        from mdb_engine.core.engine import MongoDBEngine

        manifests_dir = tmp_path / "manifests"
        manifests_dir.mkdir()

        # Create test app manifest with public routes
        test_app_manifest = {
            "schema_version": "2.0",
            "slug": "test-app",
            "name": "Test App",
            "auth": {
                "mode": "shared",
                "roles": ["viewer", "admin"],
                "require_role": "viewer",
                "public_routes": ["/", "/login", "/register", "/health"],
            },
        }
        test_app_path = manifests_dir / "test-app" / "manifest.json"
        test_app_path.parent.mkdir()
        test_app_path.write_text(json.dumps(test_app_manifest))

        # Use unique database name per test
        db_name = f"test_public_routes_prefix_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)

        # Create multi-app with test app mounted at path prefix
        app = engine.create_multi_app(
            apps=[
                {
                    "slug": "test-app",
                    "manifest": test_app_path,
                    "path_prefix": "/test-app",
                },
            ]
        )

        # Add test routes to the mounted app
        # We need to access the mounted app and add routes to it
        async with app.router.lifespan_context(app):
            # Find the mounted app
            mounted_app = None
            for route in app.routes:
                if hasattr(route, "app") and hasattr(route.app, "state"):
                    if getattr(route.app.state, "app_slug", None) == "test-app":
                        mounted_app = route.app
                        break

            assert mounted_app is not None, "Mounted app not found"

            # Add test routes to the mounted app
            @mounted_app.get("/")
            async def root():
                return {"message": "root"}

            @mounted_app.get("/login")
            async def login():
                return {"message": "login"}

            @mounted_app.get("/register")
            async def register():
                return {"message": "register"}

            @mounted_app.get("/health")
            async def health():
                return {"status": "ok"}

            @mounted_app.get("/protected")
            async def protected():
                return {"message": "protected"}

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                # Test public routes with path prefix - should return 200 (not 401)
                response = await client.get("/test-app/")
                assert (
                    response.status_code == 200
                ), f"Root route failed: {response.status_code} - {response.text}"

                response = await client.get("/test-app/login")
                assert (
                    response.status_code == 200
                ), f"Login route failed: {response.status_code} - {response.text}"
                assert response.json() == {"message": "login"}

                response = await client.get("/test-app/register")
                assert (
                    response.status_code == 200
                ), f"Register route failed: {response.status_code} - {response.text}"
                assert response.json() == {"message": "register"}

                response = await client.get("/test-app/health")
                assert (
                    response.status_code == 200
                ), f"Health route failed: {response.status_code} - {response.text}"
                assert response.json() == {"status": "ok"}

                # Test protected route - should return 401 (no auth token)
                response = await client.get("/test-app/protected")
                assert (
                    response.status_code == 401
                ), f"Protected route should require auth: {response.status_code}"

        # Cleanup
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_public_routes_multiple_mounted_apps(self, mongodb_connection_string, tmp_path):
        """Test public routes work correctly with multiple apps mounted at different prefixes."""
        import os

        from httpx import ASGITransport, AsyncClient

        from mdb_engine.core.engine import MongoDBEngine

        manifests_dir = tmp_path / "manifests"
        manifests_dir.mkdir()

        # Create first app manifest
        app1_manifest = {
            "schema_version": "2.0",
            "slug": "app1",
            "name": "App 1",
            "auth": {
                "mode": "shared",
                "roles": ["viewer"],
                "require_role": "viewer",
                "public_routes": ["/login", "/register"],
            },
        }
        app1_path = manifests_dir / "app1" / "manifest.json"
        app1_path.parent.mkdir()
        app1_path.write_text(json.dumps(app1_manifest))

        # Create second app manifest with different public routes
        app2_manifest = {
            "schema_version": "2.0",
            "slug": "app2",
            "name": "App 2",
            "auth": {
                "mode": "shared",
                "roles": ["viewer"],
                "require_role": "viewer",
                "public_routes": ["/", "/health", "/api/public/*"],
            },
        }
        app2_path = manifests_dir / "app2" / "manifest.json"
        app2_path.parent.mkdir()
        app2_path.write_text(json.dumps(app2_manifest))

        # Use unique database name per test
        db_name = f"test_multi_apps_prefix_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)

        # Create multi-app with both apps
        app = engine.create_multi_app(
            apps=[
                {
                    "slug": "app1",
                    "manifest": app1_path,
                    "path_prefix": "/app1",
                },
                {
                    "slug": "app2",
                    "manifest": app2_path,
                    "path_prefix": "/app2",
                },
            ]
        )

        async with app.router.lifespan_context(app):
            # Find mounted apps
            app1_mounted = None
            app2_mounted = None
            for route in app.routes:
                if hasattr(route, "app") and hasattr(route.app, "state"):
                    slug = getattr(route.app.state, "app_slug", None)
                    if slug == "app1":
                        app1_mounted = route.app
                    elif slug == "app2":
                        app2_mounted = route.app

            assert app1_mounted is not None, "App1 not found"
            assert app2_mounted is not None, "App2 not found"

            # Add routes to app1
            @app1_mounted.get("/login")
            async def app1_login():
                return {"app": "app1", "route": "login"}

            @app1_mounted.get("/register")
            async def app1_register():
                return {"app": "app1", "route": "register"}

            @app1_mounted.get("/protected")
            async def app1_protected():
                return {"app": "app1", "route": "protected"}

            # Add routes to app2
            @app2_mounted.get("/")
            async def app2_root():
                return {"app": "app2", "route": "root"}

            @app2_mounted.get("/health")
            async def app2_health():
                return {"app": "app2", "route": "health"}

            @app2_mounted.get("/api/public/test")
            async def app2_public():
                return {"app": "app2", "route": "public"}

            @app2_mounted.get("/protected")
            async def app2_protected():
                return {"app": "app2", "route": "protected"}

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                # Test app1 public routes
                response = await client.get("/app1/login")
                assert response.status_code == 200, f"App1 login failed: {response.status_code}"
                assert response.json()["app"] == "app1"

                response = await client.get("/app1/register")
                assert response.status_code == 200, f"App1 register failed: {response.status_code}"
                assert response.json()["app"] == "app1"

                # Test app1 protected route
                response = await client.get("/app1/protected")
                assert (
                    response.status_code == 401
                ), f"App1 protected should require auth: {response.status_code}"

                # Test app2 public routes
                response = await client.get("/app2/")
                assert response.status_code == 200, f"App2 root failed: {response.status_code}"
                assert response.json()["app"] == "app2"

                response = await client.get("/app2/health")
                assert response.status_code == 200, f"App2 health failed: {response.status_code}"
                assert response.json()["app"] == "app2"

                response = await client.get("/app2/api/public/test")
                assert (
                    response.status_code == 200
                ), f"App2 public API failed: {response.status_code}"
                assert response.json()["app"] == "app2"

                # Test app2 protected route
                response = await client.get("/app2/protected")
                assert (
                    response.status_code == 401
                ), f"App2 protected should require auth: {response.status_code}"

        # Cleanup
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_public_routes_wildcard_with_path_prefix(
        self, mongodb_connection_string, tmp_path
    ):  # noqa: E501
        """Test wildcard public routes work correctly with path prefixes."""
        import os

        from httpx import ASGITransport, AsyncClient

        from mdb_engine.core.engine import MongoDBEngine

        manifests_dir = tmp_path / "manifests"
        manifests_dir.mkdir()

        # Create app manifest with wildcard public routes
        app_manifest = {
            "schema_version": "2.0",
            "slug": "wildcard-app",
            "name": "Wildcard App",
            "auth": {
                "mode": "shared",
                "roles": ["viewer"],
                "require_role": "viewer",
                "public_routes": ["/api/public/*", "/static/*"],
            },
        }
        app_path = manifests_dir / "wildcard-app" / "manifest.json"
        app_path.parent.mkdir()
        app_path.write_text(json.dumps(app_manifest))

        # Use unique database name per test
        db_name = f"test_wildcard_prefix_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)

        # Create multi-app
        app = engine.create_multi_app(
            apps=[
                {
                    "slug": "wildcard-app",
                    "manifest": app_path,
                    "path_prefix": "/wildcard-app",
                },
            ]
        )

        async with app.router.lifespan_context(app):
            # Find mounted app
            mounted_app = None
            for route in app.routes:
                if hasattr(route, "app") and hasattr(route.app, "state"):
                    if getattr(route.app.state, "app_slug", None) == "wildcard-app":
                        mounted_app = route.app
                        break

            assert mounted_app is not None, "Mounted app not found"

            # Add routes
            @mounted_app.get("/api/public/endpoint1")
            async def public1():
                return {"route": "public1"}

            @mounted_app.get("/api/public/nested/deep/endpoint")
            async def public_nested():
                return {"route": "public_nested"}

            @mounted_app.get("/static/css/style.css")
            async def static_css():
                return {"route": "static_css"}

            @mounted_app.get("/api/private/endpoint")
            async def private():
                return {"route": "private"}

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                # Test wildcard public routes
                response = await client.get("/wildcard-app/api/public/endpoint1")
                assert (
                    response.status_code == 200
                ), f"Wildcard public route failed: {response.status_code}"

                response = await client.get("/wildcard-app/api/public/nested/deep/endpoint")
                assert (
                    response.status_code == 200
                ), f"Nested wildcard route failed: {response.status_code}"

                response = await client.get("/wildcard-app/static/css/style.css")
                assert (
                    response.status_code == 200
                ), f"Static wildcard route failed: {response.status_code}"

                # Test non-public route (should require auth)
                response = await client.get("/wildcard-app/api/private/endpoint")
                assert (
                    response.status_code == 401
                ), f"Private route should require auth: {response.status_code}"

        # Cleanup
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_public_routes_edge_cases(self, mongodb_connection_string, tmp_path):
        """Test edge cases for public routes with path prefixes."""
        import os

        from httpx import ASGITransport, AsyncClient

        from mdb_engine.core.engine import MongoDBEngine

        manifests_dir = tmp_path / "manifests"
        manifests_dir.mkdir()

        # Create app manifest
        app_manifest = {
            "schema_version": "2.0",
            "slug": "edge-app",
            "name": "Edge Case App",
            "auth": {
                "mode": "shared",
                "roles": ["viewer"],
                "require_role": "viewer",
                "public_routes": ["/", "/health"],
            },
        }
        app_path = manifests_dir / "edge-app" / "manifest.json"
        app_path.parent.mkdir()
        app_path.write_text(json.dumps(app_manifest))

        # Use unique database name per test
        db_name = f"test_edge_cases_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)

        # Create multi-app
        app = engine.create_multi_app(
            apps=[
                {
                    "slug": "edge-app",
                    "manifest": app_path,
                    "path_prefix": "/edge-app",
                },
            ]
        )

        async with app.router.lifespan_context(app):
            # Find mounted app
            mounted_app = None
            for route in app.routes:
                if hasattr(route, "app") and hasattr(route.app, "state"):
                    if getattr(route.app.state, "app_slug", None) == "edge-app":
                        mounted_app = route.app
                        break

            assert mounted_app is not None, "Mounted app not found"

            # Add routes
            @mounted_app.get("/")
            async def root():
                return {"route": "root"}

            @mounted_app.get("/health")
            async def health():
                return {"route": "health"}

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test", follow_redirects=True
            ) as client:
                # Test root path (prefix matches entire path)
                # Note: Starlette redirects /edge-app to /edge-app/, so we follow redirects
                response = await client.get("/edge-app")
                assert response.status_code == 200, f"Root path failed: {response.status_code}"

                # Test root path with trailing slash
                response = await client.get("/edge-app/")
                assert (
                    response.status_code == 200
                ), f"Root with slash failed: {response.status_code}"

                # Test health route
                response = await client.get("/edge-app/health")
                assert response.status_code == 200, f"Health route failed: {response.status_code}"

        # Cleanup
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_regression_public_routes_path_prefix_bug(
        self, mongodb_connection_string, tmp_path
    ):  # noqa: E501
        """
        REGRESSION TEST: Ensure public routes work with path prefixes.

        This test specifically addresses the bug where public routes defined in manifests
        (like ["/", "/login", "/register"]) failed when apps were mounted with path prefixes
        (like "/auth-hub") because the middleware was checking the full path instead of
        stripping the prefix first.

        Bug scenario:
        - App mounted at /auth-hub
        - Manifest has public_routes: ["/", "/login", "/register"]
        - Request to /auth-hub/register should match /register in public_routes
        - Previously returned 401, should now return 200
        """
        import os

        from httpx import ASGITransport, AsyncClient

        from mdb_engine.core.engine import MongoDBEngine

        manifests_dir = tmp_path / "manifests"
        manifests_dir.mkdir()

        # Create manifest exactly as described in bug report
        auth_hub_manifest = {
            "schema_version": "2.0",
            "slug": "auth-hub",
            "name": "Auth Hub",
            "auth": {
                "mode": "shared",
                "roles": ["viewer", "admin"],
                "require_role": "base_user",
                "public_routes": ["/", "/login", "/register"],
            },
        }
        auth_hub_path = manifests_dir / "auth-hub" / "manifest.json"
        auth_hub_path.parent.mkdir()
        auth_hub_path.write_text(json.dumps(auth_hub_manifest))

        # Use unique database name per test
        db_name = f"test_regression_bug_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)

        # Create multi-app exactly as in bug report
        app = engine.create_multi_app(
            apps=[
                {
                    "slug": "auth-hub",
                    "manifest": auth_hub_path,
                    "path_prefix": "/auth-hub",
                }
            ]
        )

        async with app.router.lifespan_context(app):
            # Find mounted app
            mounted_app = None
            for route in app.routes:
                if hasattr(route, "app") and hasattr(route.app, "state"):
                    if getattr(route.app.state, "app_slug", None) == "auth-hub":
                        mounted_app = route.app
                        break

            assert mounted_app is not None, "Mounted app not found"

            # Add routes matching the bug scenario
            @mounted_app.get("/")
            async def root():
                return {"message": "root"}

            @mounted_app.get("/login")
            async def login():
                return {"message": "login"}

            @mounted_app.get("/register")
            async def register():
                return {"message": "register"}

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test", follow_redirects=True
            ) as client:
                # CRITICAL: These requests should return 200, NOT 401
                # This is the exact bug scenario - full path /auth-hub/register
                # should match relative path /register in public_routes

                response = await client.get("/auth-hub/register")
                assert response.status_code == 200, (
                    f"REGRESSION: /auth-hub/register returned "
                    f"{response.status_code} instead of 200. "
                    f"Response: {response.text}. "
                    f"This indicates the path prefix is not being stripped correctly."
                )
                assert response.json() == {"message": "register"}

                response = await client.get("/auth-hub/login")
                assert response.status_code == 200, (
                    f"REGRESSION: /auth-hub/login returned {response.status_code} instead of 200. "
                    f"This indicates the path prefix is not being stripped correctly."
                )
                assert response.json() == {"message": "login"}

                response = await client.get("/auth-hub/")
                assert response.status_code == 200, (
                    f"REGRESSION: /auth-hub/ returned {response.status_code} instead of 200. "
                    f"This indicates the path prefix is not being stripped correctly."
                )

                # Note: Starlette redirects /auth-hub to /auth-hub/, so we follow redirects
                response = await client.get("/auth-hub")
                assert response.status_code == 200, (
                    f"REGRESSION: /auth-hub returned {response.status_code} instead of 200. "
                    f"This indicates the root path handling is broken."
                )

        # Cleanup
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_get_scoped_db_dependency_in_multi_app(self, mongodb_connection_string, tmp_path):
        """
        Test that get_scoped_db dependency works correctly in multi-app setups.

        This test verifies that child apps have engine set in their state,
        allowing get_scoped_db and other dependencies to resolve correctly.
        Without this fix, the dependency would return 503 Service Unavailable.
        """
        import os

        from mdb_engine.core.engine import MongoDBEngine

        manifests_dir = tmp_path / "manifests"
        manifests_dir.mkdir()

        # Create test app manifest with public route for testing
        test_app_manifest = {
            "schema_version": "2.0",
            "slug": "test-db-app",
            "name": "Test DB App",
            "auth": {
                "mode": "app",
                "public_routes": ["/api/test-db"],
            },
            "data_access": {
                "read_scopes": ["test-db-app"],
                "write_scope": "test-db-app",
            },
        }
        test_app_path = manifests_dir / "test-db-app" / "manifest.json"
        test_app_path.parent.mkdir()
        test_app_path.write_text(json.dumps(test_app_manifest))

        # Use unique database name per test
        db_name = f"test_get_scoped_db_multi_app_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)

        # Create multi-app with test app mounted at path prefix
        app = engine.create_multi_app(
            apps=[
                {
                    "slug": "test-db-app",
                    "manifest": test_app_path,
                    "path_prefix": "/test-db-app",
                },
            ]
        )

        # Add test route to the mounted app that uses get_scoped_db dependency
        async with app.router.lifespan_context(app):
            # Find the mounted app
            mounted_app = None
            for route in app.routes:
                if hasattr(route, "app") and hasattr(route.app, "state"):
                    if getattr(route.app.state, "app_slug", None) == "test-db-app":
                        mounted_app = route.app
                        break

            assert mounted_app is not None, "Mounted app not found"

            # Verify that child app has engine in state (this is what we're testing)
            assert hasattr(mounted_app.state, "engine"), (
                "REGRESSION: Child app missing engine in state. "
                "get_scoped_db dependency will fail with 503."
            )
            assert mounted_app.state.engine is not None, (
                "REGRESSION: Child app engine is None. "
                "get_scoped_db dependency will fail with 503."
            )
            assert (
                mounted_app.state.engine == engine
            ), "Child app engine should be same instance as parent engine"
            assert hasattr(
                mounted_app.state, "app_slug"
            ), "REGRESSION: Child app missing app_slug in state."
            assert (
                mounted_app.state.app_slug == "test-db-app"
            ), "Child app slug should be set correctly."

            # Add test route that uses get_scoped_db dependency
            from fastapi import Depends
            from httpx import ASGITransport, AsyncClient

            from mdb_engine.dependencies import get_scoped_db

            @mounted_app.get("/api/test-db")
            async def test_db_route(db=Depends(get_scoped_db)):
                """
                Test route that uses get_scoped_db dependency.
                This would fail with 503 if child_app.state.engine is not set.
                """
                # Verify we got a database connection
                assert db is not None, "Database connection should not be None"

                # Try to access a collection to verify it's working
                # This would fail if the dependency didn't resolve correctly
                test_collection = db.test_collection
                assert test_collection is not None, "Should be able to access collection"

                return {
                    "status": "success",
                    "message": "get_scoped_db dependency resolved successfully",
                    "app_slug": mounted_app.state.app_slug,
                    "has_engine": hasattr(mounted_app.state, "engine"),
                }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                # Test the route with get_scoped_db dependency
                # This should return 200, not 503
                response = await client.get("/test-db-app/api/test-db")

                assert response.status_code == 200, (
                    f"REGRESSION: Route with get_scoped_db dependency returned "
                    f"{response.status_code} instead of 200. "
                    f"This indicates the dependency failed to resolve. "
                    f"Response: {response.text}"
                )

                response_data = response.json()
                assert response_data["status"] == "success"
                assert response_data["message"] == "get_scoped_db dependency resolved successfully"
                assert response_data["app_slug"] == "test-db-app"
                assert response_data["has_engine"] is True

        # Cleanup
        await engine.shutdown()


@pytest.mark.integration
class TestWebSocketWithCORSCConfig:
    """Integration tests for WebSocket connections with CORS config in multi-app setup."""

    @pytest.mark.asyncio
    async def test_websocket_connection_with_cors_config(self, mongodb_connection_string, tmp_path):
        """Test that WebSocket connections work with proper CORS config propagation."""
        import os

        from mdb_engine.core.engine import MongoDBEngine

        # Create manifest with WebSocket and CORS config
        manifest = {
            "schema_version": "2.0",
            "slug": "ws-test-app",
            "name": "WebSocket Test App",
            "auth": {"mode": "shared", "roles": ["viewer"], "require_role": "viewer"},
            "websockets": {
                "realtime": {
                    "path": "/ws",
                    "auth": {"required": False, "allow_anonymous": True},
                    "ping_interval": 30,
                }
            },
            "cors": {
                "enabled": True,
                "allow_origins": ["*"],
                "allow_credentials": True,
                "allow_methods": ["*"],
                "allow_headers": ["*"],
            },
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        # Use unique database name per test
        db_name = f"test_websocket_cors_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)

        # Create multi-app
        app = engine.create_multi_app(
            apps=[
                {
                    "slug": "ws-test-app",
                    "manifest": manifest_path,
                    "path_prefix": "/ws-app",
                }
            ],
            title="WebSocket CORS Test",
        )

        # Verify parent app has CORS config
        assert hasattr(app.state, "cors_config"), "Parent app should have CORS config"
        assert app.state.cors_config["enabled"] is True, "Parent app CORS should be enabled"

        # Start lifespan to mount apps
        async with app.router.lifespan_context(app):
            # Verify CORS config was merged from child app
            cors_config = app.state.cors_config
            assert "*" in cors_config["allow_origins"], "Parent app should have wildcard origin"
            assert cors_config["allow_credentials"] is True, "Parent app should allow credentials"

            # Verify WebSocket route exists on parent app
            ws_routes = [
                route
                for route in app.routes
                if hasattr(route, "path") and "/ws-app/ws" in route.path
            ]
            assert len(ws_routes) > 0, "WebSocket route should be registered on parent app"

            # Note: Actual WebSocket connection testing would require a WebSocket client
            # This test verifies the configuration is correct, which is the main fix

        # Cleanup
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_allow_credentials_preserved_in_merged_cors(
        self, mongodb_connection_string, tmp_path
    ):
        """Test that allow_credentials: True from child apps is preserved in parent CORS config."""
        import os

        from httpx import ASGITransport, AsyncClient

        from mdb_engine.core.engine import MongoDBEngine

        # Create manifest with allow_credentials: True
        manifest = {
            "schema_version": "2.0",
            "slug": "credentials-app",
            "name": "Credentials Test App",
            "auth": {"mode": "shared", "roles": ["viewer"]},
            "cors": {
                "enabled": True,
                "allow_origins": ["http://localhost:3000"],
                "allow_credentials": True,  # CRITICAL: This must be preserved
                "allow_methods": ["*"],
                "allow_headers": ["*"],
            },
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        db_name = f"test_credentials_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)

        app = engine.create_multi_app(
            apps=[
                {
                    "slug": "credentials-app",
                    "manifest": manifest_path,
                    "path_prefix": "/credentials-app",
                }
            ],
            title="Credentials Test",
        )

        async with app.router.lifespan_context(app):
            # Verify parent app's CORS config has allow_credentials: True
            cors_config = app.state.cors_config
            assert cors_config.get("allow_credentials") is True, (
                f"allow_credentials should be True after merge, "
                f"but got {cors_config.get('allow_credentials')}. "
                f"Full config: {cors_config}"
            )

            # Verify CORS middleware reads from app.state dynamically
            # Make a request and check CORS headers
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.options(
                    "/credentials-app/api/test",
                    headers={"Origin": "http://localhost:3000"},
                )
                # Should include Access-Control-Allow-Credentials header
                assert (
                    "access-control-allow-credentials" in response.headers
                ), "CORS response should include allow-credentials header"
                assert (
                    response.headers["access-control-allow-credentials"].lower() == "true"
                ), "allow-credentials should be 'true'"

        # Cleanup
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_multiple_apps_credentials_merge_correctly(
        self, mongodb_connection_string, tmp_path
    ):
        """Test that if ANY child app has allow_credentials: True, parent gets True."""
        import os

        from mdb_engine.core.engine import MongoDBEngine

        # Create two manifests - one with credentials, one without
        manifest1 = {
            "schema_version": "2.0",
            "slug": "app-with-creds",
            "name": "App With Credentials",
            "auth": {"mode": "shared"},
            "cors": {
                "enabled": True,
                "allow_origins": ["http://localhost:3000"],
                "allow_credentials": True,  # This should make parent True
            },
        }
        manifest1_path = tmp_path / "manifest1.json"
        manifest1_path.write_text(json.dumps(manifest1))

        manifest2 = {
            "schema_version": "2.0",
            "slug": "app-without-creds",
            "name": "App Without Credentials",
            "auth": {"mode": "shared"},
            "cors": {
                "enabled": True,
                "allow_origins": ["http://localhost:3000"],
                "allow_credentials": False,  # This should not override
            },
        }
        manifest2_path = tmp_path / "manifest2.json"
        manifest2_path.write_text(json.dumps(manifest2))

        db_name = f"test_multi_creds_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)

        app = engine.create_multi_app(
            apps=[
                {"slug": "app-with-creds", "manifest": manifest1_path, "path_prefix": "/app1"},
                {"slug": "app-without-creds", "manifest": manifest2_path, "path_prefix": "/app2"},
            ],
            title="Multi Credentials Test",
        )

        async with app.router.lifespan_context(app):
            # Verify parent app's CORS config has allow_credentials: True
            # (because at least one child app requires it)
            cors_config = app.state.cors_config
            assert cors_config.get("allow_credentials") is True, (
                f"allow_credentials should be True (merged from app-with-creds), "
                f"but got {cors_config.get('allow_credentials')}"
            )

        # Cleanup
        await engine.shutdown()
