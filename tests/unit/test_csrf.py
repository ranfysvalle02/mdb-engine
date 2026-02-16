"""
Unit tests for CSRF protection middleware.

Tests cover:
- Token generation and validation
- Middleware behavior for safe/unsafe methods
- Public route exemption
- Cookie and header handling
- WebSocket Origin validation (CSWSH protection)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from starlette.requests import Request
from starlette.testclient import TestClient

from mdb_engine.auth.csrf import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    CSRFMiddleware,
    create_csrf_middleware,
    generate_csrf_token,
    get_csrf_token,
    validate_csrf_token,
)
from mdb_engine.auth.shared_middleware import AUTH_COOKIE_NAME
from mdb_engine.auth.websocket_sessions import WebSocketSessionManager


class TestTokenGeneration:
    """Tests for CSRF token generation."""

    def test_generate_token_without_secret(self):
        """Test generating token without HMAC secret."""
        token = generate_csrf_token()
        assert token is not None
        assert len(token) >= 32
        # Should not contain : (no signature)
        assert ":" not in token

    def test_generate_token_with_secret(self):
        """Test generating token with HMAC secret."""
        token = generate_csrf_token(secret="test-secret")
        assert token is not None
        # Should contain 3 parts: token:timestamp:signature
        parts = token.split(":")
        assert len(parts) == 3

    def test_generated_tokens_are_unique(self):
        """Test that each generated token is unique."""
        tokens = [generate_csrf_token() for _ in range(10)]
        assert len(set(tokens)) == 10


class TestTokenValidation:
    """Tests for CSRF token validation."""

    def test_validate_simple_token(self):
        """Test validating a simple token without signature."""
        token = generate_csrf_token()
        assert validate_csrf_token(token) is True

    def test_validate_signed_token(self):
        """Test validating a signed token."""
        secret = "test-secret"
        token = generate_csrf_token(secret=secret)
        assert validate_csrf_token(token, secret=secret) is True

    def test_reject_empty_token(self):
        """Test that empty token is rejected."""
        assert validate_csrf_token("") is False
        assert validate_csrf_token(None) is False

    def test_reject_short_token(self):
        """Test that short tokens are rejected."""
        assert validate_csrf_token("short") is False

    def test_reject_tampered_signature(self):
        """Test that tampered signature is rejected."""
        secret = "test-secret"
        token = generate_csrf_token(secret=secret)
        # Tamper with signature
        parts = token.split(":")
        parts[2] = "tampered123456"
        tampered_token = ":".join(parts)
        assert validate_csrf_token(tampered_token, secret=secret) is False

    def test_reject_expired_token(self):
        """Test that expired tokens are rejected."""
        import time

        secret = "test-secret"
        token = generate_csrf_token(secret=secret)

        # Validate with very short max_age
        with patch("mdb_engine.auth.csrf.time") as mock_time:
            # Simulate time passing
            mock_time.time.return_value = time.time() + 10000
            assert validate_csrf_token(token, secret=secret, max_age=1) is False


class TestWebSocketSessionKeyValidation:
    """Tests for WebSocket session key validation in CSRF middleware."""

    @pytest.fixture
    def app_with_session_manager(self):
        """Create FastAPI app with WebSocket session manager."""
        import base64
        import os

        from mdb_engine.auth.websocket_sessions import WebSocketSessionManager
        from mdb_engine.core.encryption import EnvelopeEncryptionService

        app = FastAPI()

        # Set up encryption service - always set a valid master key for this test
        # (override any existing value that might be incorrectly formatted)
        test_master_key = base64.b64encode(b"x" * 32).decode()
        os.environ["MDB_ENGINE_MASTER_KEY"] = test_master_key

        encryption_service = EnvelopeEncryptionService()

        # Mock MongoDB database
        mock_db = MagicMock()
        mock_collection = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_collection)  # noqa: SLF001

        session_manager = WebSocketSessionManager(mock_db, encryption_service)
        app.state.websocket_session_manager = session_manager
        app.state.cors_config = {"allow_origins": ["https://example.com"]}
        app.state.websocket_configs = {
            "test_app": {
                "realtime": {
                    "path": "/ws",
                    "auth": {"required": True, "csrf_required": True},
                }
            }
        }

        app.add_middleware(CSRFMiddleware)

        return app, session_manager, mock_collection

    @pytest.mark.asyncio
    async def test_websocket_with_valid_session_key_accepted(self, app_with_session_manager):
        """Test that WebSocket upgrade with valid session key is accepted."""
        app, session_manager, mock_collection = app_with_session_manager

        # Create a valid session
        session_key = WebSocketSessionManager.generate_session_key()
        from datetime import datetime, timedelta

        expires_at = datetime.utcnow() + timedelta(hours=24)

        # Mock session validation
        async def mock_find_one(query):
            if query.get("_id") == session_key:
                # Return valid session
                encrypted_key, encrypted_dek = session_manager._encryption_service.encrypt_secret(  # noqa: SLF001
                    session_key
                )
                import base64

                return {
                    "_id": session_key,
                    "user_id": "user123",
                    "user_email": "test@example.com",
                    "encrypted_key": base64.b64encode(encrypted_key).decode(),
                    "encrypted_dek": base64.b64encode(encrypted_dek).decode(),
                    "expires_at": expires_at,
                }
            return None

        mock_collection.find_one = AsyncMock(side_effect=mock_find_one)

        # Create mock WebSocket upgrade request
        from unittest.mock import MagicMock

        request = MagicMock(spec=Request)
        request.app = app
        request.url.path = "/test_app/ws"
        request.method = "GET"
        # Create a proper headers mock that supports .get() method
        headers_dict = {
            "upgrade": "websocket",
            "connection": "upgrade",
            "origin": "https://example.com",
        }
        headers_mock = MagicMock()
        headers_mock.get = lambda key, default=None: headers_dict.get(key.lower(), default)
        headers_mock.__getitem__ = lambda key: headers_dict[key.lower()]  # noqa: SLF001
        headers_mock.__contains__ = lambda key: key.lower() in headers_dict  # noqa: SLF001
        request.headers = headers_mock
        request.cookies = {AUTH_COOKIE_NAME: "valid_token"}
        request.query_params = MagicMock()
        request.query_params.get = MagicMock(return_value=session_key)

        # Mock call_next
        async def mock_call_next(req):
            from fastapi.responses import JSONResponse

            return JSONResponse({"status": "ok"})

        middleware = CSRFMiddleware(app)

        # Process request
        response = await middleware.dispatch(request, mock_call_next)

        # Should be accepted (not 403)
        assert response.status_code != 403

    @pytest.mark.asyncio
    async def test_websocket_with_invalid_session_key_rejected(self, app_with_session_manager):
        """Test that WebSocket upgrade with invalid session key passes through middleware.

        Note: The middleware now lets session keys pass through to the WebSocket handler,
        which validates them and raises WebSocketDisconnect if invalid. This allows TestClient
        to properly catch WebSocketDisconnect exceptions in integration tests.
        """
        app, session_manager, mock_collection = app_with_session_manager

        invalid_session_key = "invalid_key_12345"

        # Mock session validation returning None
        mock_collection.find_one = AsyncMock(return_value=None)

        # Create mock WebSocket upgrade request
        from unittest.mock import MagicMock

        request = MagicMock(spec=Request)
        request.app = app
        request.url.path = "/test_app/ws"
        request.method = "GET"
        # Create a proper headers mock that supports .get() method
        headers_dict = {
            "upgrade": "websocket",
            "connection": "upgrade",
            "origin": "https://example.com",
        }
        headers_mock = MagicMock()
        headers_mock.get = lambda key, default=None: headers_dict.get(key.lower(), default)
        headers_mock.__getitem__ = lambda key: headers_dict[key.lower()]  # noqa: SLF001
        headers_mock.__contains__ = lambda key: key.lower() in headers_dict  # noqa: SLF001
        request.headers = headers_mock
        request.cookies = {AUTH_COOKIE_NAME: "valid_token"}
        request.query_params = MagicMock()
        request.query_params.get = MagicMock(return_value=invalid_session_key)

        # Mock call_next
        async def mock_call_next(req):
            from fastapi.responses import JSONResponse

            return JSONResponse({"status": "ok"})

        middleware = CSRFMiddleware(app)

        # Process request
        response = await middleware.dispatch(request, mock_call_next)

        # Middleware now lets session keys pass through to handler for validation
        # The handler will validate and raise WebSocketDisconnect if invalid
        # This allows TestClient to properly catch WebSocketDisconnect in integration tests
        assert response.status_code == 200

    def test_websocket_csrf_required_defaults_to_true(self):
        """Test that csrf_required defaults to True (security by default)."""
        app = FastAPI()
        app.state.websocket_configs = {
            "test_app": {
                "realtime": {
                    "path": "/ws",
                    "auth": {"required": True},  # csrf_required not specified
                }
            }
        }

        middleware = CSRFMiddleware(app)

        # Create mock request
        request = MagicMock(spec=Request)
        request.app = app
        request.url.path = "/test_app/ws"

        # Check csrf_required (should default to True)
        csrf_required = middleware._websocket_requires_csrf(request, "/test_app/ws")  # noqa: SLF001
        assert csrf_required is True

    def test_websocket_csrf_required_can_be_disabled(self):
        """Test that csrf_required can be disabled per endpoint."""
        app = FastAPI()
        app.state.websocket_configs = {
            "test_app": {
                "realtime": {
                    "path": "/ws",
                    "auth": {"required": True, "csrf_required": False},
                }
            }
        }

        middleware = CSRFMiddleware(app)

        # Create mock request
        request = MagicMock(spec=Request)
        request.app = app
        request.url.path = "/test_app/ws"

        # Check csrf_required (should be False)
        csrf_required = middleware._websocket_requires_csrf(request, "/test_app/ws")  # noqa: SLF001
        assert csrf_required is False


class TestCSRFMiddleware:
    """Tests for CSRF middleware."""

    @pytest.fixture
    def app(self):
        """Create FastAPI app with CSRF middleware."""
        app = FastAPI()

        @app.get("/")
        def get_root():
            return {"message": "ok"}

        @app.post("/submit")
        def post_submit():
            return {"message": "submitted"}

        @app.get("/exempt")
        def get_exempt():
            return {"message": "exempt"}

        @app.post("/exempt/action")
        def post_exempt_action():
            return {"message": "exempt action"}

        app.add_middleware(
            CSRFMiddleware,
            exempt_routes=["/exempt/*", "/health"],
        )

        return app

    def test_get_request_sets_cookie(self, app):
        """Test that GET requests set CSRF cookie."""
        client = TestClient(app)
        response = client.get("/")

        assert response.status_code == 200
        assert CSRF_COOKIE_NAME in response.cookies

    def test_post_without_token_rejected(self, app):
        """Test that POST without CSRF token is rejected."""
        client = TestClient(app, raise_server_exceptions=False)

        # First get a cookie
        client.get("/")

        # POST without CSRF header should fail
        response = client.post("/submit")
        assert response.status_code == 403
        assert "CSRF" in response.json()["detail"]

    def test_post_with_valid_token_accepted(self, app):
        """Test that POST with valid CSRF token is accepted."""
        client = TestClient(app)

        # Get CSRF cookie
        get_response = client.get("/")
        csrf_token = get_response.cookies.get(CSRF_COOKIE_NAME)

        # POST with matching header should succeed
        response = client.post(
            "/submit",
            headers={CSRF_HEADER_NAME: csrf_token},
            cookies={CSRF_COOKIE_NAME: csrf_token},
        )
        assert response.status_code == 200

    def test_post_with_mismatched_token_rejected(self, app):
        """Test that POST with mismatched CSRF token is rejected."""
        client = TestClient(app, raise_server_exceptions=False)

        # Get CSRF cookie
        get_response = client.get("/")
        csrf_token = get_response.cookies.get(CSRF_COOKIE_NAME)

        # POST with different header should fail
        response = client.post(
            "/submit",
            headers={CSRF_HEADER_NAME: "different-token"},
            cookies={CSRF_COOKIE_NAME: csrf_token},
        )
        assert response.status_code == 403
        assert "CSRF" in response.json()["detail"]

    def test_exempt_route_skipped(self, app):
        """Test that exempt routes skip CSRF validation."""
        client = TestClient(app)

        # POST to exempt route should work without token
        response = client.post("/exempt/action")
        assert response.status_code == 200


class TestCSRFMiddlewareFactory:
    """Tests for create_csrf_middleware factory."""

    def test_create_with_boolean_true(self):
        """Test creating middleware with boolean True config."""
        middleware_class = create_csrf_middleware(manifest_auth={"csrf_protection": True, "public_routes": ["/health"]})
        assert middleware_class is not None

    def test_create_with_boolean_false(self):
        """Test creating middleware with boolean False config."""
        middleware_class = create_csrf_middleware(manifest_auth={"csrf_protection": False})
        # Should return no-op middleware
        assert middleware_class is not None

    def test_create_with_object_config(self):
        """Test creating middleware with object config."""
        middleware_class = create_csrf_middleware(
            manifest_auth={
                "csrf_protection": {
                    "exempt_routes": ["/api/*"],
                    "rotate_tokens": True,
                    "token_ttl": 7200,
                }
            }
        )
        assert middleware_class is not None

    def test_auth_ticket_exempted_with_boolean_config(self):
        """Test that /auth/ticket is exempted when csrf_protection is boolean True."""
        # Simulate what engine.py does: adds /auth/ticket to public_routes
        manifest_auth = {
            "csrf_protection": True,
            "public_routes": ["/health", "/auth/callback", "/auth/ticket"],
        }
        middleware_class = create_csrf_middleware(manifest_auth=manifest_auth)

        # Create app and test that /auth/ticket is exempt
        app = FastAPI()

        @app.post("/auth/ticket")
        def ticket_endpoint():
            return {"ticket": "test"}

        @app.post("/submit")
        def submit():
            return {"status": "ok"}

        app.add_middleware(middleware_class)
        client = TestClient(app, raise_server_exceptions=False)

        # /auth/ticket should work without CSRF token
        response = client.post("/auth/ticket")
        assert response.status_code == 200

        # /submit should require CSRF token
        client.get("/")  # Get CSRF cookie
        response = client.post("/submit")
        assert response.status_code == 403

    def test_auth_ticket_exempted_with_object_config_explicit_exempt_routes(self):
        """Test that /auth/ticket is exempted when exempt_routes is explicitly set."""
        # Simulate what engine.py does: merges /auth/ticket into exempt_routes
        manifest_auth = {
            "csrf_protection": {
                "exempt_routes": ["/health", "/auth/callback", "/auth/ticket"],
                "token_ttl": 3600,
            },
            "public_routes": ["/health", "/auth/callback"],
        }
        middleware_class = create_csrf_middleware(manifest_auth=manifest_auth)

        app = FastAPI()

        @app.post("/auth/ticket")
        def ticket_endpoint():
            return {"ticket": "test"}

        @app.post("/submit")
        def submit():
            return {"status": "ok"}

        app.add_middleware(middleware_class)
        client = TestClient(app, raise_server_exceptions=False)

        # /auth/ticket should work without CSRF token
        response = client.post("/auth/ticket")
        assert response.status_code == 200

        # /submit should require CSRF token
        client.get("/")  # Get CSRF cookie
        response = client.post("/submit")
        assert response.status_code == 403

    def test_auth_ticket_exempted_with_object_config_no_exempt_routes(self):
        """Test that /auth/ticket is exempted when exempt_routes is not set (uses public_routes)."""
        # Simulate what engine.py does: adds /auth/ticket to public_routes
        manifest_auth = {
            "csrf_protection": {
                "enabled": True,
                "token_ttl": 3600,
                # exempt_routes not set, should fall back to public_routes
            },
            "public_routes": ["/health", "/auth/callback", "/auth/ticket"],
        }
        middleware_class = create_csrf_middleware(manifest_auth=manifest_auth)

        app = FastAPI()

        @app.post("/auth/ticket")
        def ticket_endpoint():
            return {"ticket": "test"}

        app.add_middleware(middleware_class)
        client = TestClient(app)

        # /auth/ticket should work without CSRF token
        response = client.post("/auth/ticket")
        assert response.status_code == 200

    def test_auth_ticket_exempted_with_object_config_empty_exempt_routes(self):
        """Test that /auth/ticket is exempted when exempt_routes is empty list.

        Note: When exempt_routes is explicitly set to [], create_csrf_middleware uses it
        instead of falling back to public_routes. However, engine.py ensures /auth/ticket
        is added to exempt_routes even when it's empty, so this test simulates that behavior.
        """
        # Simulate what engine.py does: adds /auth/ticket to empty exempt_routes
        # In engine.py, if exempt_routes is [], it will add /auth/ticket to it
        manifest_auth = {
            "csrf_protection": {
                "exempt_routes": ["/auth/ticket"],  # engine.py adds this even if original was []
                "token_ttl": 3600,
            },
            "public_routes": ["/health", "/auth/callback"],
        }
        middleware_class = create_csrf_middleware(manifest_auth=manifest_auth)

        app = FastAPI()

        @app.post("/auth/ticket")
        def ticket_endpoint():
            return {"ticket": "test"}

        app.add_middleware(middleware_class)
        client = TestClient(app, raise_server_exceptions=False)

        # /auth/ticket should work without CSRF token (engine.py ensures it's in exempt_routes)
        response = client.post("/auth/ticket")
        assert response.status_code == 200

    def test_auth_ticket_exempted_when_already_in_exempt_routes(self):
        """Test that /auth/ticket is not duplicated when already in exempt_routes."""
        manifest_auth = {
            "csrf_protection": {
                "exempt_routes": ["/health", "/auth/ticket"],  # Already includes /auth/ticket
                "token_ttl": 3600,
            },
            "public_routes": ["/health"],
        }
        middleware_class = create_csrf_middleware(manifest_auth=manifest_auth)

        app = FastAPI()

        @app.post("/auth/ticket")
        def ticket_endpoint():
            return {"ticket": "test"}

        app.add_middleware(middleware_class)
        client = TestClient(app)

        # /auth/ticket should work without CSRF token
        response = client.post("/auth/ticket")
        assert response.status_code == 200

    def test_middleware_exempt_routes_contains_auth_ticket(self):
        """Test that middleware instance has /auth/ticket in exempt_routes when configured."""
        # Simulate engine.py behavior: object config with exempt_routes that includes /auth/ticket
        manifest_auth = {
            "csrf_protection": {
                "exempt_routes": ["/health", "/auth/callback", "/auth/ticket"],
                "token_ttl": 3600,
            },
            "public_routes": ["/health", "/auth/callback"],
        }
        middleware_class = create_csrf_middleware(manifest_auth=manifest_auth)

        app = FastAPI()
        app.add_middleware(middleware_class)

        # Get the middleware instance to verify its exempt_routes
        # The middleware is added to the app, so we need to access it via the app's middleware stack
        # For testing purposes, create a middleware instance directly
        middleware_instance = middleware_class(app)

        # Verify /auth/ticket is in exempt_routes
        assert "/auth/ticket" in middleware_instance.exempt_routes
        assert middleware_instance._is_exempt("/auth/ticket") is True  # noqa: SLF001

    def test_middleware_exempt_routes_fallback_to_public_routes(self):
        """Test that middleware falls back to public_routes when exempt_routes not set."""
        # Simulate engine.py behavior: object config without exempt_routes, uses public_routes
        manifest_auth = {
            "csrf_protection": {
                "enabled": True,
                "token_ttl": 3600,
                # exempt_routes not set
            },
            "public_routes": ["/health", "/auth/callback", "/auth/ticket"],
        }
        middleware_class = create_csrf_middleware(manifest_auth=manifest_auth)

        app = FastAPI()
        middleware_instance = middleware_class(app)

        # Verify /auth/ticket is exempt (via public_routes fallback)
        assert middleware_instance._is_exempt("/auth/ticket") is True  # noqa: SLF001


class TestGetCSRFToken:
    """Tests for get_csrf_token dependency."""

    def test_get_from_request_state(self):
        """Test getting token from request state."""
        request = MagicMock(spec=Request)
        request.state.csrf_token = "state-token"
        request.cookies = {}

        token = get_csrf_token(request)
        assert token == "state-token"

    def test_get_from_cookie(self):
        """Test getting token from cookie."""
        request = MagicMock(spec=Request)
        request.state = MagicMock()
        del request.state.csrf_token  # Remove the attribute
        request.cookies = {CSRF_COOKIE_NAME: "cookie-token"}

        # Mock hasattr to return False for csrf_token
        with patch(
            "mdb_engine.auth.csrf.hasattr",
            side_effect=lambda obj, attr: attr != "csrf_token",
        ):
            token = get_csrf_token(request)
        assert token is not None

    def test_generate_new_token(self):
        """Test generating new token when none exists."""
        request = MagicMock(spec=Request)
        request.state = MagicMock(spec=[])  # No csrf_token attribute
        request.cookies = {}

        token = get_csrf_token(request)
        assert token is not None
        assert len(token) >= 32


class TestWebSocketOriginValidation:
    """Tests for WebSocket Origin validation (CSWSH protection)."""

    @pytest.fixture
    def app(self):
        """Create FastAPI app with CSRF middleware."""
        app = FastAPI()

        @app.get("/")
        def get_root():
            return {"message": "ok"}

        @app.post("/submit")
        def post_submit():
            return {"message": "submitted"}

        app.add_middleware(CSRFMiddleware)

        return app

    def test_websocket_upgrade_detection(self):
        """Test that WebSocket upgrade requests are detected."""
        middleware = CSRFMiddleware(MagicMock())
        request = MagicMock(spec=Request)
        headers_dict = {
            "upgrade": "websocket",
            "connection": "upgrade",
        }
        headers_mock = MagicMock()
        headers_mock.get = lambda key, default="": headers_dict.get(key.lower(), default)
        request.headers = headers_mock

        assert middleware._is_websocket_upgrade(request) is True  # noqa: SLF001

    def test_non_websocket_request_not_detected(self):
        """Test that non-WebSocket requests are not detected."""
        middleware = CSRFMiddleware(MagicMock())
        request = MagicMock(spec=Request)
        request.headers.get = lambda key, default="": {"upgrade": "http/1.1"}.get(key.lower(), default)

        assert middleware._is_websocket_upgrade(request) is False  # noqa: SLF001

    def test_websocket_with_valid_origin_accepted(self, app):
        """Test that WebSocket with valid Origin is accepted."""
        client = TestClient(app, raise_server_exceptions=False)

        app.state.cors_config = {"allow_origins": ["https://example.com"]}
        # Configure WebSocket route to disable CSRF requirement for this test
        app.state.websocket_configs = {
            "test_app": {
                "realtime": {
                    "path": "/",
                    "auth": {"required": False, "csrf_required": False},  # No auth, no CSRF
                }
            }
        }

        response = client.get(
            "/",
            headers={
                "upgrade": "websocket",
                "connection": "upgrade",
                "origin": "https://example.com",
            },
        )
        assert response.status_code == 200

    def test_websocket_with_invalid_origin_rejected(self, app):
        """Test that WebSocket with invalid Origin is rejected."""
        client = TestClient(app, raise_server_exceptions=False)

        app.state.cors_config = {"allow_origins": ["https://example.com"]}
        # Configure WebSocket route
        app.state.websocket_configs = {
            "test_app": {
                "realtime": {
                    "path": "/",
                    "auth": {"required": False, "csrf_required": False},
                }
            }
        }

        response = client.get(
            "/",
            headers={
                "upgrade": "websocket",
                "connection": "upgrade",
                "origin": "https://evil.com",
            },
        )
        assert response.status_code == 403
        assert "origin" in response.json()["detail"].lower()

    def test_websocket_with_missing_origin_rejected(self, app):
        """Test that WebSocket with missing Origin is rejected."""
        client = TestClient(app, raise_server_exceptions=False)

        app.state.cors_config = {"allow_origins": ["https://example.com"]}
        # Configure WebSocket route
        app.state.websocket_configs = {
            "test_app": {
                "realtime": {
                    "path": "/",
                    "auth": {"required": False, "csrf_required": False},
                }
            }
        }

        response = client.get(
            "/",
            headers={
                "upgrade": "websocket",
                "connection": "upgrade",
            },
        )
        assert response.status_code == 403
        assert "origin" in response.json()["detail"].lower()

    def test_websocket_with_wildcard_origin_accepted(self, app):
        """Test that WebSocket with wildcard Origin is accepted (with warning)."""
        client = TestClient(app, raise_server_exceptions=False)

        app.state.cors_config = {"allow_origins": ["*"]}
        # Configure WebSocket route
        app.state.websocket_configs = {
            "test_app": {
                "realtime": {
                    "path": "/",
                    "auth": {"required": False, "csrf_required": False},
                }
            }
        }

        with patch("mdb_engine.auth.csrf.logger") as mock_logger:
            response = client.get(
                "/",
                headers={
                    "upgrade": "websocket",
                    "connection": "upgrade",
                    "origin": "https://any-origin.com",
                },
            )
            assert response.status_code == 200
            mock_logger.warning.assert_called()
            assert "wildcard" in str(mock_logger.warning.call_args).lower()

    def test_websocket_uses_cors_config_origins(self, app):
        """Test that WebSocket validation uses CORS config origins."""
        client = TestClient(app, raise_server_exceptions=False)

        app.state.cors_config = {"allow_origins": ["https://app1.com", "https://app2.com"]}
        # Configure WebSocket route
        app.state.websocket_configs = {
            "test_app": {
                "realtime": {
                    "path": "/",
                    "auth": {"required": False, "csrf_required": False},
                }
            }
        }

        response = client.get(
            "/",
            headers={
                "upgrade": "websocket",
                "connection": "upgrade",
                "origin": "https://app1.com",
            },
        )
        assert response.status_code == 200

        response = client.get(
            "/",
            headers={
                "upgrade": "websocket",
                "connection": "upgrade",
                "origin": "https://app2.com",
            },
        )
        assert response.status_code == 200

        response = client.get(
            "/",
            headers={
                "upgrade": "websocket",
                "connection": "upgrade",
                "origin": "https://app3.com",
            },
        )
        assert response.status_code == 403

    def test_websocket_fallback_to_request_host(self, app):
        """Test that WebSocket validation falls back to request host when no CORS config."""
        client = TestClient(app, raise_server_exceptions=False)

        if hasattr(app.state, "cors_config"):
            delattr(app.state, "cors_config")

        request_url = client.base_url
        host = request_url.host if hasattr(request_url, "host") else "testserver"
        scheme = request_url.scheme if hasattr(request_url, "scheme") else "http"
        port = request_url.port if hasattr(request_url, "port") else None

        expected_origin = f"{scheme}://{host}"
        if port and port not in [80, 443]:
            expected_origin = f"{expected_origin}:{port}"

        response = client.get(
            "/",
            headers={
                "upgrade": "websocket",
                "origin": expected_origin,
            },
        )
        assert response.status_code == 200

    def test_websocket_origin_normalization(self, app):
        """Test that Origin validation handles trailing slashes."""
        client = TestClient(app, raise_server_exceptions=False)

        app.state.cors_config = {"allow_origins": ["https://example.com"]}

        response = client.get(
            "/",
            headers={
                "upgrade": "websocket",
                "origin": "https://example.com/",
            },
        )
        assert response.status_code == 200

    def test_http_requests_unaffected_by_websocket_validation(self, app):
        """Test that HTTP requests are not affected by WebSocket Origin validation."""
        client = TestClient(app)

        app.state.cors_config = {"allow_origins": ["https://example.com"]}

        # Get CSRF cookie from GET request
        get_response = client.get("/")
        assert get_response.status_code == 200
        csrf_token = get_response.cookies.get(CSRF_COOKIE_NAME)

        # POST with valid CSRF token should succeed
        response = client.post(
            "/submit",
            headers={CSRF_HEADER_NAME: csrf_token},
            cookies={CSRF_COOKIE_NAME: csrf_token},
        )
        assert response.status_code == 200

    def test_websocket_validation_before_csrf_token_check(self, app):
        """Test that WebSocket Origin validation happens before CSRF token validation."""
        client = TestClient(app, raise_server_exceptions=False)

        app.state.cors_config = {"allow_origins": ["https://example.com"]}

        response = client.get(
            "/",
            headers={
                "upgrade": "websocket",
                "origin": "https://example.com",
            },
        )
        assert response.status_code == 200

    def test_get_allowed_origins_from_cors_config(self):
        """Test getting allowed origins from CORS config."""
        middleware = CSRFMiddleware(MagicMock())
        request = MagicMock(spec=Request)
        request.app.state.cors_config = {"allow_origins": ["https://example.com"]}

        origins = middleware._get_allowed_origins(request)  # noqa: SLF001
        assert origins == ["https://example.com"]

    def test_get_allowed_origins_fallback_to_request_host(self):
        """Test fallback to request host when no CORS config."""
        middleware = CSRFMiddleware(MagicMock())
        request = MagicMock(spec=Request)
        request.app.state = MagicMock()
        delattr(request.app.state, "cors_config")
        request.url.hostname = "example.com"
        request.url.scheme = "https"
        request.url.port = None

        origins = middleware._get_allowed_origins(request)  # noqa: SLF001
        assert len(origins) == 1
        assert origins[0] == "https://example.com"

    def test_get_allowed_origins_with_port(self):
        """Test getting allowed origins with non-standard port."""
        middleware = CSRFMiddleware(MagicMock())
        request = MagicMock(spec=Request)
        request.app.state = MagicMock()
        delattr(request.app.state, "cors_config")
        request.url.hostname = "example.com"
        request.url.scheme = "https"
        request.url.port = 8080

        origins = middleware._get_allowed_origins(request)  # noqa: SLF001
        assert origins == ["https://example.com:8080"]

    def test_validate_websocket_origin_exact_match(self):
        """Test Origin validation with exact match."""
        middleware = CSRFMiddleware(MagicMock())
        request = MagicMock(spec=Request)
        request.headers.get = lambda key, default=None: {"origin": "https://example.com"}.get(key.lower(), default)
        request.url.path = "/ws"
        request.app = MagicMock()
        request.app.state.cors_config = {"allow_origins": ["https://example.com"]}

        assert middleware._validate_websocket_origin(request) is True  # noqa: SLF001

    def test_validate_websocket_origin_no_match(self):
        """Test Origin validation with no match."""
        middleware = CSRFMiddleware(MagicMock())
        request = MagicMock(spec=Request)
        request.headers.get = lambda key, default=None: {"origin": "https://evil.com"}.get(key.lower(), default)
        request.url.path = "/ws"
        request.app = MagicMock()
        request.app.state.cors_config = {"allow_origins": ["https://example.com"]}

        assert middleware._validate_websocket_origin(request) is False  # noqa: SLF001

    def test_validate_websocket_origin_missing_header(self):
        """Test Origin validation with missing Origin header."""
        middleware = CSRFMiddleware(MagicMock())
        request = MagicMock(spec=Request)
        request.headers.get = lambda key, default=None: {}.get(key.lower(), default)
        request.url.path = "/ws"
        request.app = MagicMock()
        request.app.state.cors_config = {"allow_origins": ["https://example.com"]}

        assert middleware._validate_websocket_origin(request) is False  # noqa: SLF001

    def test_websocket_error_message_includes_path_and_cors_status(self, app):
        """Test that WebSocket rejection error messages include path and CORS status."""
        import logging

        client = TestClient(app, raise_server_exceptions=False)

        app.state.cors_config = {
            "enabled": True,
            "allow_origins": ["https://example.com"],
        }
        # Configure WebSocket route at /test/ws
        app.state.websocket_configs = {
            "test_app": {
                "realtime": {
                    "path": "/test/ws",
                    "auth": {"required": False, "csrf_required": False},
                }
            }
        }

        # Capture warning logs
        log_capture = []
        handler = logging.Handler()
        handler.emit = lambda record: log_capture.append(record.getMessage())

        logger = logging.getLogger("mdb_engine.auth.csrf")
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)

        response = client.get(
            "/test/ws",
            headers={
                "upgrade": "websocket",
                "connection": "upgrade",
                "origin": "https://evil.com",
            },
        )

        assert response.status_code == 403

        # Check that error message includes path and CORS status
        error_logs = [log for log in log_capture if "WebSocket upgrade rejected" in log]
        assert len(error_logs) > 0, "Error should be logged"
        error_msg = error_logs[0]
        assert "path:" in error_msg.lower() or "/test/ws" in error_msg
        assert "cors enabled:" in error_msg.lower()

        logger.removeHandler(handler)

    def test_get_allowed_origins_reads_from_parent_app_state(self, app):
        """Test that _get_allowed_origins reads from parent app's merged CORS config."""
        middleware = CSRFMiddleware(app)

        # Set CORS config on app state (simulating merged config from child apps)
        app.state.cors_config = {
            "allow_origins": ["https://app1.com", "https://app2.com"],
        }

        # Create a mock request
        request = MagicMock(spec=Request)
        request.app = app
        request.url.path = "/app-3/ws"

        allowed_origins = middleware._get_allowed_origins(request)  # noqa: SLF001

        assert "https://app1.com" in allowed_origins
        assert "https://app2.com" in allowed_origins

    def test_get_allowed_origins_handles_missing_cors_config(self, app):
        """Test that _get_allowed_origins handles missing CORS config gracefully."""
        middleware = CSRFMiddleware(app)

        # Don't set CORS config
        if hasattr(app.state, "cors_config"):
            delattr(app.state, "cors_config")

        # Create a mock request
        request = MagicMock(spec=Request)
        request.app = app
        request.url.path = "/test/ws"
        request.url.hostname = "testserver"
        request.url.scheme = "http"
        request.url.port = None

        allowed_origins = middleware._get_allowed_origins(request)  # noqa: SLF001

        # Should fall back to request host or return empty list
        assert isinstance(allowed_origins, list)


class TestWebSocketCSRFValidation:
    """Tests for CSRF validation on WebSocket upgrade requests."""

    @pytest.fixture
    def app(self):
        """Create FastAPI app with CSRF middleware."""
        app = FastAPI()

        @app.get("/")
        def get_root():
            return {"message": "ok"}

        # Configure WebSocket to use cookie-based CSRF (not session keys)
        # This allows tests to verify cookie-based CSRF behavior
        # Note: No websocket_session_manager is set, so it will fall back to cookie-based CSRF
        app.state.websocket_configs = {
            "test_app": {
                "realtime": {
                    "path": "/",
                    "auth": {
                        "required": True,
                        "csrf_required": True,
                    },  # CSRF required, will use cookie-based fallback
                }
            }
        }

        app.add_middleware(CSRFMiddleware)

        return app

    def test_websocket_with_auth_cookie_requires_csrf_cookie(self, app):
        """Test that WebSocket upgrade with auth cookie requires CSRF cookie."""
        client = TestClient(app, raise_server_exceptions=False)

        app.state.cors_config = {"allow_origins": ["https://example.com"]}

        # WebSocket upgrade with auth cookie but no CSRF cookie should fail
        response = client.get(
            "/",
            headers={
                "upgrade": "websocket",
                "connection": "upgrade",
                "origin": "https://example.com",
            },
            cookies={"mdb_auth_token": "auth-token"},  # Auth cookie present, no CSRF cookie
        )
        assert response.status_code == 403
        assert "CSRF" in response.json()["detail"]

    def test_websocket_with_auth_cookie_and_csrf_cookie_accepted(self, app):
        """Test that WebSocket upgrade with auth cookie and CSRF cookie is accepted."""
        client = TestClient(app, raise_server_exceptions=False)

        app.state.cors_config = {"allow_origins": ["https://example.com"]}

        # Get CSRF cookie first
        get_response = client.get("/")
        csrf_token = get_response.cookies.get(CSRF_COOKIE_NAME)

        # WebSocket upgrade with auth cookie and CSRF cookie should succeed
        # (CSRF header is optional for WebSocket upgrades since JS can't set headers)
        response = client.get(
            "/",
            headers={
                "upgrade": "websocket",
                "origin": "https://example.com",
                # CSRF header not required - Origin validation + SameSite cookies provide protection
            },
            cookies={
                "mdb_auth_token": "auth-token",  # Auth cookie
                CSRF_COOKIE_NAME: csrf_token,  # CSRF cookie
            },
        )
        assert response.status_code == 200

    def test_websocket_with_csrf_header_optional_but_validated_if_present(self, app):
        """Test that CSRF header is validated if provided, but not required."""
        client = TestClient(app, raise_server_exceptions=False)

        app.state.cors_config = {"allow_origins": ["https://example.com"]}

        # Get CSRF cookie first
        get_response = client.get("/")
        csrf_token = get_response.cookies.get(CSRF_COOKIE_NAME)

        # WebSocket upgrade with matching CSRF header should succeed
        response = client.get(
            "/",
            headers={
                "upgrade": "websocket",
                "connection": "upgrade",
                "origin": "https://example.com",
                CSRF_HEADER_NAME: csrf_token,  # Optional header, but validated if present
            },
            cookies={
                AUTH_COOKIE_NAME: "auth-token",
                CSRF_COOKIE_NAME: csrf_token,
            },
        )
        assert response.status_code == 200

        # WebSocket upgrade with mismatched CSRF header should fail
        response = client.get(
            "/",
            headers={
                "upgrade": "websocket",
                "connection": "upgrade",
                "origin": "https://example.com",
                CSRF_HEADER_NAME: "different-token",  # Mismatched header
            },
            cookies={
                AUTH_COOKIE_NAME: "auth-token",
                CSRF_COOKIE_NAME: csrf_token,
            },
        )
        assert response.status_code == 403
        assert "CSRF" in response.json()["detail"]

    def test_websocket_with_auth_cookie_and_mismatched_csrf_rejected(self, app):
        """Test that WebSocket upgrade with mismatched CSRF token is rejected."""
        client = TestClient(app, raise_server_exceptions=False)

        app.state.cors_config = {"allow_origins": ["https://example.com"]}

        # Get CSRF cookie
        get_response = client.get("/")
        csrf_token = get_response.cookies.get(CSRF_COOKIE_NAME)

        # WebSocket upgrade with mismatched CSRF token should fail
        response = client.get(
            "/",
            headers={
                "upgrade": "websocket",
                "connection": "upgrade",
                "origin": "https://example.com",
                CSRF_HEADER_NAME: "different-token",
            },
            cookies={
                AUTH_COOKIE_NAME: "auth-token",
                CSRF_COOKIE_NAME: csrf_token,
            },
        )
        assert response.status_code == 403
        assert "CSRF" in response.json()["detail"]

    def test_websocket_without_auth_cookie_no_csrf_required(self, app):
        """Test that WebSocket upgrade without auth cookie doesn't require CSRF."""
        client = TestClient(app, raise_server_exceptions=False)

        app.state.cors_config = {"allow_origins": ["https://example.com"]}

        # WebSocket upgrade without auth cookie should only check origin
        response = client.get(
            "/",
            headers={
                "upgrade": "websocket",
                "origin": "https://example.com",
            },
            # No auth cookie, no CSRF token
        )
        assert response.status_code == 200

    def test_websocket_csrf_validation_before_origin_validation(self, app):
        """Test that CSRF validation happens after origin validation."""
        client = TestClient(app, raise_server_exceptions=False)

        app.state.cors_config = {"allow_origins": ["https://example.com"]}

        # Invalid origin should be rejected before CSRF check
        response = client.get(
            "/",
            headers={
                "upgrade": "websocket",
                "connection": "upgrade",
                "origin": "https://evil.com",  # Invalid origin
            },
            cookies={AUTH_COOKIE_NAME: "auth-token"},
        )
        assert response.status_code == 403
        assert "origin" in response.json()["detail"].lower()

    def test_websocket_csrf_token_expiration_validation(self):
        """Test that expired CSRF tokens are rejected for WebSocket upgrades."""
        import time

        from mdb_engine.auth.csrf import DEFAULT_TOKEN_TTL, generate_csrf_token

        # Create app with CSRF middleware configured with secret
        # (needed for token expiration validation)
        secret = "test-secret"
        app = FastAPI()

        @app.get("/")
        def get_root():
            return {"message": "ok"}

        app.add_middleware(CSRFMiddleware, secret=secret, token_ttl=DEFAULT_TOKEN_TTL)
        app.state.cors_config = {"allow_origins": ["https://example.com"]}

        client = TestClient(app, raise_server_exceptions=False)

        # Generate CSRF token with the same secret
        csrf_token = generate_csrf_token(secret=secret)

        # Simulate time passing beyond token TTL to make it expired
        # DEFAULT_TOKEN_TTL is 3600 seconds (1 hour), so we need to advance time by more than that
        with patch("mdb_engine.auth.csrf.time") as mock_time:
            # Advance time beyond token TTL to make token expired
            mock_time.time.return_value = time.time() + DEFAULT_TOKEN_TTL + 100

            response = client.get(
                "/",
                headers={
                    "upgrade": "websocket",
                    "connection": "upgrade",  # Add Connection header for proper WebSocket upgrade
                    "origin": "https://example.com",
                    CSRF_HEADER_NAME: csrf_token,
                },
                cookies={
                    AUTH_COOKIE_NAME: "auth-token",
                    CSRF_COOKIE_NAME: csrf_token,
                },
            )
            # Should fail CSRF validation due to expiration
            assert response.status_code == 403
            assert "CSRF" in response.json()["detail"]
