"""
Unit tests for CSRF protection middleware.

Tests cover:
- Token generation and validation
- Middleware behavior for safe/unsafe methods
- Public route exemption
- Cookie and header handling
- WebSocket Origin validation (CSWSH protection)
"""

from unittest.mock import MagicMock, patch

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
        middleware_class = create_csrf_middleware(
            manifest_auth={"csrf_protection": True, "public_routes": ["/health"]}
        )
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
        request.headers.get = lambda key, default="": {"upgrade": "websocket"}.get(
            key.lower(), default
        )

        assert middleware._is_websocket_upgrade(request) is True

    def test_non_websocket_request_not_detected(self):
        """Test that non-WebSocket requests are not detected."""
        middleware = CSRFMiddleware(MagicMock())
        request = MagicMock(spec=Request)
        request.headers.get = lambda key, default="": {"upgrade": "http/1.1"}.get(
            key.lower(), default
        )

        assert middleware._is_websocket_upgrade(request) is False

    def test_websocket_with_valid_origin_accepted(self, app):
        """Test that WebSocket with valid Origin is accepted."""
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

    def test_websocket_with_invalid_origin_rejected(self, app):
        """Test that WebSocket with invalid Origin is rejected."""
        client = TestClient(app, raise_server_exceptions=False)

        app.state.cors_config = {"allow_origins": ["https://example.com"]}

        response = client.get(
            "/",
            headers={
                "upgrade": "websocket",
                "origin": "https://evil.com",
            },
        )
        assert response.status_code == 403
        assert "origin" in response.json()["detail"].lower()

    def test_websocket_with_missing_origin_rejected(self, app):
        """Test that WebSocket with missing Origin is rejected."""
        client = TestClient(app, raise_server_exceptions=False)

        app.state.cors_config = {"allow_origins": ["https://example.com"]}

        response = client.get(
            "/",
            headers={
                "upgrade": "websocket",
            },
        )
        assert response.status_code == 403
        assert "origin" in response.json()["detail"].lower()

    def test_websocket_with_wildcard_origin_accepted(self, app):
        """Test that WebSocket with wildcard Origin is accepted (with warning)."""
        client = TestClient(app, raise_server_exceptions=False)

        app.state.cors_config = {"allow_origins": ["*"]}

        with patch("mdb_engine.auth.csrf.logger") as mock_logger:
            response = client.get(
                "/",
                headers={
                    "upgrade": "websocket",
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

        response = client.get(
            "/",
            headers={
                "upgrade": "websocket",
                "origin": "https://app1.com",
            },
        )
        assert response.status_code == 200

        response = client.get(
            "/",
            headers={
                "upgrade": "websocket",
                "origin": "https://app2.com",
            },
        )
        assert response.status_code == 200

        response = client.get(
            "/",
            headers={
                "upgrade": "websocket",
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

        origins = middleware._get_allowed_origins(request)
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

        origins = middleware._get_allowed_origins(request)
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

        origins = middleware._get_allowed_origins(request)
        assert origins == ["https://example.com:8080"]

    def test_validate_websocket_origin_exact_match(self):
        """Test Origin validation with exact match."""
        middleware = CSRFMiddleware(MagicMock())
        request = MagicMock(spec=Request)
        request.headers.get = lambda key, default=None: {"origin": "https://example.com"}.get(
            key.lower(), default
        )
        request.url.path = "/ws"
        request.app = MagicMock()
        request.app.state.cors_config = {"allow_origins": ["https://example.com"]}

        assert middleware._validate_websocket_origin(request) is True

    def test_validate_websocket_origin_no_match(self):
        """Test Origin validation with no match."""
        middleware = CSRFMiddleware(MagicMock())
        request = MagicMock(spec=Request)
        request.headers.get = lambda key, default=None: {"origin": "https://evil.com"}.get(
            key.lower(), default
        )
        request.url.path = "/ws"
        request.app = MagicMock()
        request.app.state.cors_config = {"allow_origins": ["https://example.com"]}

        assert middleware._validate_websocket_origin(request) is False

    def test_validate_websocket_origin_missing_header(self):
        """Test Origin validation with missing Origin header."""
        middleware = CSRFMiddleware(MagicMock())
        request = MagicMock(spec=Request)
        request.headers.get = lambda key, default=None: {}.get(key.lower(), default)
        request.url.path = "/ws"
        request.app = MagicMock()
        request.app.state.cors_config = {"allow_origins": ["https://example.com"]}

        assert middleware._validate_websocket_origin(request) is False
