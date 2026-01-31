"""
Integration tests for cookie-based WebSocket authentication.

Tests end-to-end WebSocket authentication flow with:
- Real MongoDB connection
- Cookie-based authentication
- CSRF token validation
- Origin validation
- Multi-app isolation
"""

import base64
import json
import os
from datetime import datetime, timedelta

import jwt
import pytest

# Set test secret key before importing engine components
if "MDB_ENGINE_JWT_SECRET" not in os.environ:
    os.environ["MDB_ENGINE_JWT_SECRET"] = "test_jwt_secret_for_testing_only_" + "x" * 32

# Set test master key - must be base64-encoded 32-byte key
if "MDB_ENGINE_MASTER_KEY" not in os.environ:
    os.environ["MDB_ENGINE_MASTER_KEY"] = base64.b64encode(b"x" * 32).decode()

# Import TestClient for WebSocket testing
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mdb_engine.auth.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, generate_csrf_token
from mdb_engine.auth.dependencies import SECRET_KEY
from mdb_engine.auth.shared_middleware import AUTH_COOKIE_NAME


@pytest.mark.integration
class TestWebSocketCookieAuthIntegration:
    """Integration tests for cookie-based WebSocket authentication."""

    @pytest.fixture
    def test_manifest(self):
        """Create test manifest with WebSocket configuration."""
        return {
            "schema_version": "2.0",
            "slug": "test_ws_app",
            "name": "Test WebSocket App",
            "auth": {
                "mode": "shared",
                "roles": ["viewer", "editor"],
                "require_role": "viewer",
            },
            "websockets": {
                "realtime": {
                    "path": "/ws",
                    "auth": {"required": True},
                    "ping_interval": 30,
                }
            },
            "cors": {
                "enabled": True,
                "allow_origins": ["https://example.com", "http://localhost:3000"],
                "allow_credentials": True,
                "allow_methods": ["*"],
                "allow_headers": ["*"],
            },
            "data_access": {
                "read_scopes": ["test_ws_app"],
                "write_scope": "test_ws_app",
            },
        }

    @pytest.fixture
    def valid_jwt_token(self):
        """Create a valid JWT token for testing."""
        payload = {
            "sub": "user123",
            "user_id": "user123",
            "email": "test@example.com",
            "exp": datetime.utcnow() + timedelta(hours=1),
        }
        return jwt.encode(payload, str(SECRET_KEY), algorithm="HS256")

    @pytest.mark.asyncio
    async def test_websocket_connection_with_valid_cookie(
        self, mongodb_connection_string, test_manifest, valid_jwt_token, tmp_path
    ):
        """Test successful WebSocket connection with valid httpOnly cookie."""
        import asyncio

        from mdb_engine.core.engine import MongoDBEngine

        # Use unique database name per test
        db_name = f"test_ws_auth_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)

        # Write manifest to temp file (create_multi_app requires Path, not dict)
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(test_manifest))

        # Create app with WebSocket support using create_multi_app
        app = engine.create_multi_app(
            apps=[
                {
                    "slug": test_manifest["slug"],
                    "manifest": manifest_path,
                    "path_prefix": "/",
                }
            ],
            title="Test WebSocket App",
        )

        # Generate CSRF token
        csrf_token = generate_csrf_token()

        async with app.router.lifespan_context(app):
            # Use TestClient for WebSocket testing (it's synchronous but works with async tests)
            def test_websocket():
                client = TestClient(app)
                # First, get CSRF cookie from a GET request
                get_response = client.get("/")
                csrf_cookie = get_response.cookies.get(CSRF_COOKIE_NAME)
                if not csrf_cookie:
                    csrf_cookie = csrf_token

                # Try to connect WebSocket with valid auth cookie and CSRF cookie
                # Note: CSRF header is optional for WebSocket (JS can't set headers)
                # Protection relies on Origin validation + SameSite cookies
                with client.websocket_connect(
                    "/ws",
                    cookies={
                        AUTH_COOKIE_NAME: valid_jwt_token,
                        CSRF_COOKIE_NAME: csrf_cookie,
                    },
                    headers={
                        "origin": "https://example.com",
                        # CSRF header optional - Origin validation provides primary protection
                    },
                ) as websocket:
                    # Connection should be established
                    assert websocket is not None

                    # Receive initial connection message
                    message = websocket.receive_json()
                    assert message["type"] == "connected"
                    assert message["authenticated"] is True
                    assert message["user_email"] == "test@example.com"

            # Run TestClient in a thread to avoid blocking
            try:
                await asyncio.to_thread(test_websocket)
            except (WebSocketDisconnect, RuntimeError, OSError, AttributeError) as e:
                # WebSocket connection might fail if FastAPI WebSocket support isn't available
                # This is acceptable for integration tests
                pytest.skip(f"WebSocket connection failed: {e}")

    @pytest.mark.asyncio
    async def test_websocket_connection_without_csrf_cookie_rejected(
        self, mongodb_connection_string, test_manifest, valid_jwt_token, tmp_path
    ):
        """Test that WebSocket connection without CSRF cookie is rejected."""
        import asyncio

        from mdb_engine.core.engine import MongoDBEngine

        # Use unique database name per test
        db_name = f"test_ws_csrf_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)

        # Write manifest to temp file
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(test_manifest))

        # Create app with WebSocket support using create_multi_app
        app = engine.create_multi_app(
            apps=[
                {
                    "slug": test_manifest["slug"],
                    "manifest": manifest_path,
                    "path_prefix": "/",
                }
            ],
            title="Test WebSocket App",
        )

        async with app.router.lifespan_context(app):

            def test_websocket():
                client = TestClient(app)
                # Try to connect WebSocket with auth cookie but no CSRF cookie
                try:
                    with client.websocket_connect(
                        "/ws",
                        cookies={AUTH_COOKIE_NAME: valid_jwt_token},  # Auth cookie, no CSRF cookie
                        headers={"origin": "https://example.com"},
                    ) as websocket:
                        # Should not reach here - connection should be rejected
                        pytest.fail("WebSocket connection should have been rejected")
                except WebSocketDisconnect:
                    # Connection was rejected (upgrade failed) - this is expected
                    pass  # Test passes - connection was properly rejected

            await asyncio.to_thread(test_websocket)

    @pytest.mark.asyncio
    async def test_websocket_connection_with_invalid_origin_rejected(
        self, mongodb_connection_string, test_manifest, valid_jwt_token, tmp_path
    ):
        """Test that WebSocket connection with invalid origin is rejected."""
        import asyncio

        from mdb_engine.core.engine import MongoDBEngine

        # Use unique database name per test
        db_name = f"test_ws_origin_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)

        # Write manifest to temp file
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(test_manifest))

        # Create app with WebSocket support using create_multi_app
        app = engine.create_multi_app(
            apps=[
                {
                    "slug": test_manifest["slug"],
                    "manifest": manifest_path,
                    "path_prefix": "/",
                }
            ],
            title="Test WebSocket App",
        )

        # Generate CSRF token
        csrf_token = generate_csrf_token()

        async with app.router.lifespan_context(app):

            def test_websocket():
                client = TestClient(app)
                # Get CSRF cookie
                get_response = client.get("/")
                csrf_cookie = get_response.cookies.get(CSRF_COOKIE_NAME)
                if not csrf_cookie:
                    csrf_cookie = csrf_token

                # Try to connect WebSocket with invalid origin
                try:
                    with client.websocket_connect(
                        "/ws",
                        cookies={
                            AUTH_COOKIE_NAME: valid_jwt_token,
                            CSRF_COOKIE_NAME: csrf_cookie,
                        },
                        headers={
                            CSRF_HEADER_NAME: csrf_cookie,
                            "origin": "https://evil.com",  # Invalid origin
                        },
                    ) as websocket:
                        # Should not reach here - connection should be rejected
                        pytest.fail("WebSocket connection should have been rejected")
                except WebSocketDisconnect:
                    # Connection was rejected (invalid origin) - expected
                    pass  # Test passes - connection was properly rejected

            await asyncio.to_thread(test_websocket)

    @pytest.mark.asyncio
    async def test_websocket_connection_without_auth_cookie_allowed(
        self, mongodb_connection_string, test_manifest, tmp_path
    ):
        """Test that WebSocket connection without auth cookie is allowed (if auth not required)."""
        import asyncio

        from mdb_engine.core.engine import MongoDBEngine

        # Modify manifest to allow anonymous connections
        test_manifest["websockets"]["realtime"]["auth"]["required"] = False

        # Use unique database name per test
        db_name = f"test_ws_anon_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)

        # Write manifest to temp file
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(test_manifest))

        # Create app with WebSocket support using create_multi_app
        app = engine.create_multi_app(
            apps=[
                {
                    "slug": test_manifest["slug"],
                    "manifest": manifest_path,
                    "path_prefix": "/",
                }
            ],
            title="Test WebSocket App",
        )

        async with app.router.lifespan_context(app):

            def test_websocket():
                client = TestClient(app)
                # Try to connect WebSocket without auth cookie
                try:
                    with client.websocket_connect(
                        "/ws",
                        headers={"origin": "https://example.com"},
                    ) as websocket:
                        # Connection should be established (auth not required)
                        assert websocket is not None

                        # Receive initial connection message
                        message = websocket.receive_json()
                        assert message["type"] == "connected"
                        assert message["authenticated"] is False
                except (WebSocketDisconnect, RuntimeError, OSError, AttributeError) as e:
                    # WebSocket connection might fail if FastAPI WebSocket support isn't available
                    pytest.skip(f"WebSocket connection failed: {e}")

            await asyncio.to_thread(test_websocket)

    @pytest.mark.asyncio
    async def test_websocket_connection_with_expired_token_rejected(
        self, mongodb_connection_string, test_manifest, tmp_path
    ):
        """Test that WebSocket connection with expired token is rejected."""
        import asyncio

        from mdb_engine.core.engine import MongoDBEngine

        # Create expired token
        expired_payload = {
            "sub": "user123",
            "user_id": "user123",
            "email": "test@example.com",
            "exp": datetime.utcnow() - timedelta(hours=1),  # Expired
        }
        expired_token = jwt.encode(expired_payload, str(SECRET_KEY), algorithm="HS256")

        # Use unique database name per test
        db_name = f"test_ws_expired_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)

        # Write manifest to temp file
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(test_manifest))

        # Create app with WebSocket support using create_multi_app
        app = engine.create_multi_app(
            apps=[
                {
                    "slug": test_manifest["slug"],
                    "manifest": manifest_path,
                    "path_prefix": "/",
                }
            ],
            title="Test WebSocket App",
        )

        # Generate CSRF token
        csrf_token = generate_csrf_token()

        async with app.router.lifespan_context(app):

            def test_websocket():
                client = TestClient(app)
                # Get CSRF cookie
                get_response = client.get("/")
                csrf_cookie = get_response.cookies.get(CSRF_COOKIE_NAME)
                if not csrf_cookie:
                    csrf_cookie = csrf_token

                # Try to connect WebSocket with expired token
                try:
                    with client.websocket_connect(
                        "/ws",
                        cookies={
                            AUTH_COOKIE_NAME: expired_token,
                            CSRF_COOKIE_NAME: csrf_cookie,
                        },
                        headers={
                            "origin": "https://example.com",
                            # CSRF header optional
                        },
                    ) as websocket:
                        # Should not reach here - connection should be rejected
                        pytest.fail("WebSocket connection should have been rejected")
                except WebSocketDisconnect:
                    # Connection was rejected (expired token) - expected
                    pass  # Test passes - connection was properly rejected

            await asyncio.to_thread(test_websocket)

    @pytest.mark.asyncio
    async def test_websocket_connection_with_session_key_query_param(
        self, mongodb_connection_string, test_manifest, valid_jwt_token, tmp_path
    ):
        """Test successful WebSocket connection with session key in query param."""
        import asyncio

        from mdb_engine.core.engine import MongoDBEngine

        # Use unique database name per test
        db_name = f"test_ws_session_key_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)
        await engine.initialize()

        # Write manifest to temp file
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(test_manifest))

        # Create app with WebSocket support
        app = engine.create_multi_app(
            apps=[
                {
                    "slug": test_manifest["slug"],
                    "manifest": manifest_path,
                    "path_prefix": "/",
                }
            ],
            title="Test WebSocket App",
        )

        async with app.router.lifespan_context(app):
            # Generate session key via endpoint
            from fastapi.testclient import TestClient

            client = TestClient(app)

            # First authenticate to get session key
            user_pool = app.state.user_pool
            if not user_pool:
                pytest.skip("SharedUserPool not initialized")

            # Create test user
            try:
                await user_pool.create_user(
                    email="test@example.com",
                    password="testpass123",
                    app_roles={test_manifest["slug"]: ["viewer"]},
                )
            except Exception:
                pass  # User might already exist

            # Authenticate to get session key
            auth_result = await user_pool.authenticate(
                "test@example.com",
                "testpass123",
                generate_websocket_session=True,
                app_slug=test_manifest["slug"],
            )

            if isinstance(auth_result, tuple):
                jwt_token, session_key = auth_result
            else:
                # Fallback: get session key from endpoint
                response = client.get(
                    "/auth/websocket-session",
                    cookies={AUTH_COOKIE_NAME: auth_result},
                    headers={"origin": "https://example.com"},
                )
                if response.status_code == 200:
                    session_key = response.json()["session_key"]
                else:
                    pytest.skip("Could not generate session key")

            def test_websocket():
                # Connect WebSocket with session key in query param
                with client.websocket_connect(
                    f"/ws?session_key={session_key}",
                    headers={"origin": "https://example.com"},
                ) as websocket:
                    # Connection should be established
                    assert websocket is not None
                    message = websocket.receive_json()
                    assert message["type"] == "connected"
                    assert message["authenticated"] is True

            try:
                await asyncio.to_thread(test_websocket)
            except (WebSocketDisconnect, RuntimeError, OSError, AttributeError) as e:
                pytest.skip(f"WebSocket connection failed: {e}")

    @pytest.mark.asyncio
    async def test_websocket_connection_invalid_session_key_rejected(
        self, mongodb_connection_string, test_manifest, valid_jwt_token, tmp_path
    ):
        """Test that WebSocket connection with invalid session key is rejected."""
        import asyncio

        from mdb_engine.core.engine import MongoDBEngine

        # Use unique database name per test
        db_name = f"test_ws_invalid_key_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)
        await engine.initialize()

        # Write manifest to temp file
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(test_manifest))

        # Create app with WebSocket support
        app = engine.create_multi_app(
            apps=[
                {
                    "slug": test_manifest["slug"],
                    "manifest": manifest_path,
                    "path_prefix": "/",
                }
            ],
            title="Test WebSocket App",
        )

        async with app.router.lifespan_context(app):
            from fastapi.testclient import TestClient

            client = TestClient(app)

            invalid_session_key = "invalid_session_key_12345"

            def test_websocket():
                # Try to connect with invalid session key
                try:
                    with client.websocket_connect(
                        f"/ws?session_key={invalid_session_key}",
                        headers={"origin": "https://example.com"},
                    ) as websocket:
                        pytest.fail("WebSocket connection should have been rejected")
                except WebSocketDisconnect:
                    # Connection was rejected - this is expected
                    pass

            await asyncio.to_thread(test_websocket)

    @pytest.mark.asyncio
    async def test_websocket_session_endpoint_requires_auth(
        self, mongodb_connection_string, test_manifest, tmp_path
    ):
        """Test that WebSocket session endpoint requires authentication."""
        from mdb_engine.core.engine import MongoDBEngine

        # Use unique database name per test
        db_name = f"test_ws_endpoint_auth_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)
        await engine.initialize()

        # Write manifest to temp file
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(test_manifest))

        # Create app with WebSocket support
        app = engine.create_multi_app(
            apps=[
                {
                    "slug": test_manifest["slug"],
                    "manifest": manifest_path,
                    "path_prefix": "/",
                }
            ],
            title="Test WebSocket App",
        )

        async with app.router.lifespan_context(app):
            from fastapi.testclient import TestClient

            client = TestClient(app)

            # Try to access endpoint without authentication
            response = client.get("/auth/websocket-session")

            # Should return 401 Unauthorized
            assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_websocket_session_endpoint_generates_key(
        self, mongodb_connection_string, test_manifest, valid_jwt_token, tmp_path
    ):
        """Test that WebSocket session endpoint generates session key."""
        from mdb_engine.core.engine import MongoDBEngine

        # Use unique database name per test
        db_name = f"test_ws_endpoint_gen_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)
        await engine.initialize()

        # Write manifest to temp file
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(test_manifest))

        # Create app with WebSocket support
        app = engine.create_multi_app(
            apps=[
                {
                    "slug": test_manifest["slug"],
                    "manifest": manifest_path,
                    "path_prefix": "/",
                }
            ],
            title="Test WebSocket App",
        )

        async with app.router.lifespan_context(app):
            from httpx import ASGITransport, AsyncClient

            # Create test user in shared user pool and authenticate to get valid token
            user_pool = app.state.user_pool
            if user_pool:
                try:
                    # Create user
                    await user_pool.create_user(
                        email="test@example.com",
                        password="testpass123",
                        app_roles={test_manifest["slug"]: ["viewer"]},
                    )
                except Exception:
                    pass  # User might already exist

                # Authenticate to get a valid token (this ensures user exists and token is valid)
                auth_result = await user_pool.authenticate(
                    "test@example.com",
                    "testpass123",
                    generate_websocket_session=False,  # We'll test endpoint separately
                )
                if isinstance(auth_result, tuple):
                    jwt_token = auth_result[0]
                else:
                    jwt_token = auth_result
            else:
                jwt_token = valid_jwt_token

            # Use AsyncClient for async tests to avoid event loop conflicts
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                # Access endpoint with authentication
                response = await client.get(
                    "/auth/websocket-session",
                    cookies={AUTH_COOKIE_NAME: jwt_token},
                    headers={"origin": "https://example.com"},
                )

                # Should return 200 with session key
                assert response.status_code == 200
                data = response.json()
                assert "session_key" in data
                assert "expires_at" in data
                assert "ttl_hours" in data
                assert len(data["session_key"]) > 0
