"""
Unit tests for CSRF protection middleware.

Tests cover:
- Token generation and validation
- Middleware behavior for safe/unsafe methods
- Public route exemption
- Cookie and header handling
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
