"""
Tests for automatic route import functionality in multi-app deployments.

Tests the _import_app_routes() method and its integration with create_multi_app():
- Route module discovery (web.py, routes.py)
- Route registration via decorators
- Error handling and edge cases
- Manifest-based route module specification
- Relative imports in route modules
"""

import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Set test secret key before importing engine components
if "MDB_ENGINE_JWT_SECRET" not in os.environ:
    os.environ["MDB_ENGINE_JWT_SECRET"] = "test_jwt_secret_for_testing_only_" + "x" * 32

if "MDB_ENGINE_MASTER_KEY" not in os.environ:
    os.environ["MDB_ENGINE_MASTER_KEY"] = "test_master_key_for_testing_only_" + "x" * 32


class TestRouteAutoImport:
    """Test automatic route import functionality."""

    @pytest.fixture
    def temp_app_dir(self, tmp_path):
        """Create a temporary app directory with manifest."""
        app_dir = tmp_path / "test_app"
        app_dir.mkdir()

        # Create manifest
        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test App",
            "auth": {"mode": "app"},
        }
        manifest_path = app_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        return {"app_dir": app_dir, "manifest_path": manifest_path}

    def test_import_routes_from_web_py(self, temp_app_dir, mongodb_connection_string):
        """Test that routes are automatically imported from web.py."""
        from mdb_engine.core.engine import MongoDBEngine

        app_dir = temp_app_dir["app_dir"]
        manifest_path = temp_app_dir["manifest_path"]

        # Create web.py with routes
        web_py = app_dir / "web.py"
        web_py.write_text(
            """
from fastapi import FastAPI

# Routes should be registered on the injected 'app' variable
@app.get("/")
async def root():
    return {"message": "Hello from test_app"}

@app.get("/api/data")
async def get_data():
    return {"data": [1, 2, 3]}

@app.post("/api/data")
async def post_data():
    return {"status": "created"}
"""
        )

        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name="test_db")
        child_app = engine.create_app(slug="test_app", manifest=manifest_path, is_sub_app=True)

        # Manually call _import_app_routes to test it
        engine._import_app_routes(child_app, manifest_path, "test_app")

        # Verify routes were registered
        assert len(child_app.routes) >= 3  # At least our 3 routes

        # Verify routes work
        client = TestClient(child_app)
        response = client.get("/")
        assert response.status_code == 200
        assert response.json() == {"message": "Hello from test_app"}

        response = client.get("/api/data")
        assert response.status_code == 200
        assert response.json() == {"data": [1, 2, 3]}

        response = client.post("/api/data")
        assert response.status_code == 200

    def test_import_routes_from_routes_py(self, temp_app_dir, mongodb_connection_string):
        """Test that routes.py is used as fallback when web.py doesn't exist."""
        from mdb_engine.core.engine import MongoDBEngine

        app_dir = temp_app_dir["app_dir"]
        manifest_path = temp_app_dir["manifest_path"]

        # Create routes.py (no web.py)
        routes_py = app_dir / "routes.py"
        routes_py.write_text(
            """
from fastapi import FastAPI

@app.get("/from-routes")
async def from_routes():
    return {"source": "routes.py"}
"""
        )

        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name="test_db")
        child_app = engine.create_app(slug="test_app", manifest=manifest_path, is_sub_app=True)

        engine._import_app_routes(child_app, manifest_path, "test_app")

        # Verify route was registered
        client = TestClient(child_app)
        response = client.get("/from-routes")
        assert response.status_code == 200
        assert response.json() == {"source": "routes.py"}

    def test_web_py_takes_precedence_over_routes_py(self, temp_app_dir, mongodb_connection_string):
        """Test that web.py takes precedence over routes.py."""
        from mdb_engine.core.engine import MongoDBEngine

        app_dir = temp_app_dir["app_dir"]
        manifest_path = temp_app_dir["manifest_path"]

        # Create both files
        web_py = app_dir / "web.py"
        web_py.write_text(
            """
from fastapi import FastAPI

@app.get("/")
async def root():
    return {"source": "web.py"}
"""
        )

        routes_py = app_dir / "routes.py"
        routes_py.write_text(
            """
from fastapi import FastAPI

@app.get("/")
async def root():
    return {"source": "routes.py"}
"""
        )

        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name="test_db")
        child_app = engine.create_app(slug="test_app", manifest=manifest_path, is_sub_app=True)

        engine._import_app_routes(child_app, manifest_path, "test_app")

        # Verify web.py was used (not routes.py)
        client = TestClient(child_app)
        response = client.get("/")
        assert response.status_code == 200
        assert response.json() == {"source": "web.py"}

    def test_manifest_routes_module_field(self, temp_app_dir, mongodb_connection_string):
        """Test that routes_module field in manifest is respected."""
        from mdb_engine.core.engine import MongoDBEngine

        app_dir = temp_app_dir["app_dir"]
        manifest_path = temp_app_dir["manifest_path"]

        # Update manifest with routes_module
        manifest = json.loads(manifest_path.read_text())
        manifest["routes_module"] = "custom_routes.py"
        manifest_path.write_text(json.dumps(manifest))

        # Create custom routes file
        custom_routes = app_dir / "custom_routes.py"
        custom_routes.write_text(
            """
from fastapi import FastAPI

@app.get("/custom")
async def custom():
    return {"source": "custom_routes.py"}
"""
        )

        # Also create web.py to ensure custom_routes.py takes precedence
        web_py = app_dir / "web.py"
        web_py.write_text(
            """
from fastapi import FastAPI

@app.get("/custom")
async def custom():
    return {"source": "web.py"}
"""
        )

        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name="test_db")
        child_app = engine.create_app(slug="test_app", manifest=manifest_path, is_sub_app=True)

        engine._import_app_routes(child_app, manifest_path, "test_app")

        # Verify custom_routes.py was used
        client = TestClient(child_app)
        response = client.get("/custom")
        assert response.status_code == 200
        assert response.json() == {"source": "custom_routes.py"}

    def test_no_route_modules_graceful_degradation(self, temp_app_dir, mongodb_connection_string):
        """Test that missing route modules don't cause errors."""
        from mdb_engine.core.engine import MongoDBEngine

        manifest_path = temp_app_dir["manifest_path"]

        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name="test_db")
        child_app = engine.create_app(slug="test_app", manifest=manifest_path, is_sub_app=True)

        # Should not raise an exception
        engine._import_app_routes(child_app, manifest_path, "test_app")

        # App should still work (just no custom routes)
        assert isinstance(child_app, FastAPI)

    def test_syntax_error_in_route_module(self, temp_app_dir, mongodb_connection_string):
        """Test that syntax errors in route modules are handled gracefully."""
        from mdb_engine.core.engine import MongoDBEngine

        app_dir = temp_app_dir["app_dir"]
        manifest_path = temp_app_dir["manifest_path"]

        # Create web.py with syntax error
        web_py = app_dir / "web.py"
        web_py.write_text(
            """
from fastapi import FastAPI

@app.get("/")
async def root():
    return {"message": "Hello"  # Missing closing brace
"""
        )

        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name="test_db")
        child_app = engine.create_app(slug="test_app", manifest=manifest_path, is_sub_app=True)

        # Should not raise exception, just log warning
        engine._import_app_routes(child_app, manifest_path, "test_app")

        # App should still be functional
        assert isinstance(child_app, FastAPI)

    def test_route_module_with_relative_imports(self, temp_app_dir, mongodb_connection_string):
        """Test that route modules can use relative imports."""
        from mdb_engine.core.engine import MongoDBEngine

        app_dir = temp_app_dir["app_dir"]
        manifest_path = temp_app_dir["manifest_path"]

        # Create a helper module
        helpers_dir = app_dir / "helpers"
        helpers_dir.mkdir()
        (helpers_dir / "__init__.py").write_text("")

        helper_py = helpers_dir / "utils.py"
        helper_py.write_text(
            """
def get_message():
    return "Hello from helper"
"""
        )

        # Create web.py that imports the helper
        web_py = app_dir / "web.py"
        web_py.write_text(
            """
from fastapi import FastAPI
from helpers.utils import get_message

@app.get("/")
async def root():
    return {"message": get_message()}
"""
        )

        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name="test_db")
        child_app = engine.create_app(slug="test_app", manifest=manifest_path, is_sub_app=True)

        engine._import_app_routes(child_app, manifest_path, "test_app")

        # Verify route works with relative import
        client = TestClient(child_app)
        response = client.get("/")
        assert response.status_code == 200
        assert response.json() == {"message": "Hello from helper"}

    def test_route_module_overwrites_app_warning(self, temp_app_dir, mongodb_connection_string):
        """Test that overwriting app variable generates a warning."""
        from mdb_engine.core.engine import MongoDBEngine

        app_dir = temp_app_dir["app_dir"]
        manifest_path = temp_app_dir["manifest_path"]

        # Create web.py that creates its own app
        web_py = app_dir / "web.py"
        web_py.write_text(
            """
from fastapi import FastAPI

# Route before app creation - should use injected app
@app.get("/before")
async def before():
    return {"order": "before"}

# Create new app (bad practice but should be handled)
app = FastAPI()

# Route after app creation - won't be registered on child_app
@app.get("/after")
async def after():
    return {"order": "after"}
"""
        )

        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name="test_db")
        child_app = engine.create_app(slug="test_app", manifest=manifest_path, is_sub_app=True)

        with pytest.warns() as record:
            engine._import_app_routes(child_app, manifest_path, "test_app")

        # Should have warning about app being overwritten
        assert len(record) > 0

        # Route defined before app creation should work
        client = TestClient(child_app)
        response = client.get("/before")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_integration_with_create_multi_app(self, tmp_path, mongodb_connection_string):
        """Test that route auto-import works in create_multi_app context."""
        from httpx import ASGITransport, AsyncClient

        from mdb_engine.core.engine import MongoDBEngine

        # Create app1 directory
        app1_dir = tmp_path / "app1"
        app1_dir.mkdir()

        manifest1 = {
            "schema_version": "2.0",
            "slug": "app1",
            "name": "App 1",
            "auth": {"mode": "app"},
        }
        manifest1_path = app1_dir / "manifest.json"
        manifest1_path.write_text(json.dumps(manifest1))

        # Create web.py for app1
        web1_py = app1_dir / "web.py"
        web1_py.write_text(
            """
from fastapi import FastAPI

@app.get("/")
async def app1_root():
    return {"app": "app1"}
"""
        )

        # Create app2 directory
        app2_dir = tmp_path / "app2"
        app2_dir.mkdir()

        manifest2 = {
            "schema_version": "2.0",
            "slug": "app2",
            "name": "App 2",
            "auth": {"mode": "app"},
        }
        manifest2_path = app2_dir / "manifest.json"
        manifest2_path.write_text(json.dumps(manifest2))

        # Create web.py for app2
        web2_py = app2_dir / "web.py"
        web2_py.write_text(
            """
from fastapi import FastAPI

@app.get("/")
async def app2_root():
    return {"app": "app2"}
"""
        )

        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name="test_db")

        # Create multi-app
        app = engine.create_multi_app(
            apps=[
                {"slug": "app1", "manifest": manifest1_path, "path_prefix": "/app1"},
                {"slug": "app2", "manifest": manifest2_path, "path_prefix": "/app2"},
            ]
        )

        # Test routes are accessible via path prefix
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                # Test app1 route
                response = await client.get("/app1/")
                assert response.status_code == 200
                assert response.json() == {"app": "app1"}

                # Test app2 route
                response = await client.get("/app2/")
                assert response.status_code == 200
                assert response.json() == {"app": "app2"}

    def test_route_module_with_engine_dependency(self, temp_app_dir, mongodb_connection_string):
        """Test that route modules can access engine via injected variable."""
        from mdb_engine.core.engine import MongoDBEngine

        app_dir = temp_app_dir["app_dir"]
        manifest_path = temp_app_dir["manifest_path"]

        # Create web.py that uses engine
        web_py = app_dir / "web.py"
        web_py.write_text(
            """
from fastapi import FastAPI

@app.get("/engine-check")
async def engine_check():
    # Verify engine is available
    has_engine = hasattr(engine, 'get_scoped_db')
    return {"engine_available": has_engine}
"""
        )

        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name="test_db")
        child_app = engine.create_app(slug="test_app", manifest=manifest_path, is_sub_app=True)

        engine._import_app_routes(child_app, manifest_path, "test_app")

        # Verify route can access engine
        client = TestClient(child_app)
        response = client.get("/engine-check")
        assert response.status_code == 200
        assert response.json() == {"engine_available": True}

    def test_non_python_files_ignored(self, temp_app_dir, mongodb_connection_string):
        """Test that non-Python files are ignored."""
        from mdb_engine.core.engine import MongoDBEngine

        app_dir = temp_app_dir["app_dir"]
        manifest_path = temp_app_dir["manifest_path"]

        # Create a .txt file that looks like it could be a route module
        fake_web = app_dir / "web.txt"
        fake_web.write_text("This is not Python code")

        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name="test_db")
        child_app = engine.create_app(slug="test_app", manifest=manifest_path, is_sub_app=True)

        # Should not raise exception
        engine._import_app_routes(child_app, manifest_path, "test_app")

        # No routes should be added
        routes_before = len(child_app.routes)
        # (routes_before should be minimal - just health check or similar)
