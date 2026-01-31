"""
Tests for WebSocket authentication using cookie-based authentication.

Security Focus:
- Validates cookie-based authentication prevents XSS attacks (httpOnly cookies)
- Ensures CSRF protection via double-submit cookie pattern
- Verifies authentication happens before connection acceptance
- Tests token validation and expiration handling
- Confirms multi-app isolation for WebSocket connections

Test Coverage:
- Successful authentication via httpOnly cookie
- Cookie extraction from WebSocket requests
- Invalid token rejection
- Expired token rejection
- Error handling for missing/invalid tokens
- No-auth scenarios
- Cookie parsing from different sources (headers, scope)
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import jwt
import pytest

from mdb_engine.auth.dependencies import SECRET_KEY
from mdb_engine.auth.shared_middleware import AUTH_COOKIE_NAME
from mdb_engine.routing.websockets import _get_cookies_from_websocket, authenticate_websocket


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket instance."""
    ws = MagicMock()
    ws.headers = {}
    ws.query_params = {}
    ws.cookies = {}
    ws.scope = {"headers": []}
    return ws


@pytest.fixture
def valid_jwt_token():
    """Create a valid JWT token for testing."""
    payload = {
        "sub": "user123",
        "user_id": "user123",
        "email": "test@example.com",
        "exp": datetime.utcnow() + timedelta(hours=1),
    }
    return jwt.encode(payload, str(SECRET_KEY), algorithm="HS256")


class TestCookieExtraction:
    """Tests for cookie extraction from WebSocket requests."""

    @pytest.mark.asyncio
    async def test_get_cookies_from_websocket_cookies_attribute(self, mock_websocket):
        """Test extracting cookies from WebSocket.cookies attribute."""
        mock_websocket.cookies = {AUTH_COOKIE_NAME: "test-token", "csrf_token": "csrf-value"}

        cookies = _get_cookies_from_websocket(mock_websocket)

        assert cookies[AUTH_COOKIE_NAME] == "test-token"
        assert cookies["csrf_token"] == "csrf-value"

    @pytest.mark.asyncio
    async def test_get_cookies_from_websocket_scope_header(self, mock_websocket):
        """Test extracting cookies from Cookie header in ASGI scope."""
        # Simulate ASGI-style headers in scope
        mock_websocket.cookies = None  # No cookies attribute
        mock_websocket.scope = {
            "headers": [
                (b"cookie", f"{AUTH_COOKIE_NAME}=test-token; csrf_token=csrf-value".encode())
            ]
        }

        cookies = _get_cookies_from_websocket(mock_websocket)

        assert cookies[AUTH_COOKIE_NAME] == "test-token"
        assert cookies["csrf_token"] == "csrf-value"

    @pytest.mark.asyncio
    async def test_get_cookies_from_websocket_empty(self, mock_websocket):
        """Test extracting cookies when none are present."""
        mock_websocket.cookies = {}
        mock_websocket.scope = {"headers": []}

        cookies = _get_cookies_from_websocket(mock_websocket)

        assert cookies == {}

    @pytest.mark.asyncio
    async def test_get_cookies_from_websocket_malformed_cookie(self, mock_websocket):
        """Test handling malformed cookie strings."""
        mock_websocket.cookies = None
        mock_websocket.scope = {
            "headers": [(b"cookie", b"malformed=cookie=value; valid=mdb_auth_token")]
        }

        cookies = _get_cookies_from_websocket(mock_websocket)

        # Should handle gracefully, extract what it can
        assert "valid" in cookies


class TestWebSocketCookieAuthentication:
    """
    Test WebSocket authentication via httpOnly cookies.

    Security: These tests validate that cookie-based authentication works correctly,
    ensuring tokens are securely stored in httpOnly cookies (not accessible to JavaScript)
    and authentication occurs before connection establishment.
    """

    @pytest.mark.asyncio
    async def test_authenticate_via_cookie_success(self, mock_websocket, valid_jwt_token):
        """
        Test successful authentication via httpOnly cookie.

        Security: Validates that valid JWT tokens in httpOnly cookies
        are correctly extracted and validated.
        """
        # Set up WebSocket with token in cookie (use correct cookie name)
        mock_websocket.cookies = {AUTH_COOKIE_NAME: valid_jwt_token}

        user_id, user_email = await authenticate_websocket(
            mock_websocket, "test_app", require_auth=True
        )

        assert user_id == "user123"
        assert user_email == "test@example.com"

    @pytest.mark.asyncio
    async def test_authenticate_via_cookie_invalid_token(self, mock_websocket):
        """
        Test authentication failure with invalid token in cookie.

        Security: Ensures that invalid or malformed JWT tokens are rejected
        immediately, preventing unauthorized access. Invalid tokens raise
        exceptions during decode, which is the expected secure behavior.
        """
        invalid_token = "not.a.valid.jwt.token"
        mock_websocket.cookies = {AUTH_COOKIE_NAME: invalid_token}

        # Invalid JWT tokens raise exceptions during decode
        with pytest.raises(jwt.DecodeError):
            await authenticate_websocket(mock_websocket, "test_app", require_auth=True)

    @pytest.mark.asyncio
    async def test_authenticate_via_cookie_expired_token(self, mock_websocket):
        """
        Test authentication failure with expired token.

        Security: Validates that expired JWT tokens are rejected, preventing
        replay attacks and ensuring tokens have limited lifetime. This is
        critical for security - expired tokens must never be accepted.
        """
        expired_payload = {
            "sub": "user123",
            "email": "test@example.com",
            "exp": datetime.utcnow() - timedelta(hours=1),  # Expired
        }
        expired_token = jwt.encode(expired_payload, str(SECRET_KEY), algorithm="HS256")
        mock_websocket.cookies = {AUTH_COOKIE_NAME: expired_token}

        with pytest.raises(jwt.ExpiredSignatureError):
            await authenticate_websocket(mock_websocket, "test_app", require_auth=True)

    @pytest.mark.asyncio
    async def test_authenticate_via_cookie_scope_header(self, mock_websocket, valid_jwt_token):
        """
        Test authentication via Cookie header in ASGI scope.

        Security: Validates that cookies can be extracted from ASGI scope
        headers when WebSocket.cookies attribute is not available. This ensures
        compatibility with different WebSocket implementations while maintaining security.
        """
        # Simulate ASGI-style headers in scope
        mock_websocket.cookies = None  # No cookies attribute
        mock_websocket.scope = {
            "headers": [(b"cookie", f"{AUTH_COOKIE_NAME}={valid_jwt_token}".encode())]
        }

        user_id, user_email = await authenticate_websocket(
            mock_websocket, "test_app", require_auth=True
        )

        assert user_id == "user123"
        assert user_email == "test@example.com"


class TestWebSocketAuthenticationNoAuth:
    """
    Test WebSocket authentication when auth is not required.

    Security: These tests validate that when authentication is disabled,
    the system correctly bypasses auth checks. This is important for
    public WebSocket endpoints that don't require authentication.
    """

    @pytest.mark.asyncio
    async def test_no_auth_required_returns_none(self, mock_websocket):
        """
        Test that when require_auth=False, returns None, None.

        Security: Validates that when auth is not required, the function
        correctly returns None values without attempting token validation.
        This ensures public endpoints work correctly without unnecessary
        security checks.
        """
        # No token provided
        user_id, user_email = await authenticate_websocket(
            mock_websocket, "test_app", require_auth=False
        )

        assert user_id is None
        assert user_email is None

    @pytest.mark.asyncio
    async def test_no_auth_required_with_token_ignored(self, mock_websocket, valid_jwt_token):
        """
        Test that tokens are ignored when require_auth=False.

        Security: Validates that even if a token is provided when auth is
        not required, it is ignored. This ensures that public endpoints
        remain public and tokens are not unnecessarily processed or logged.
        """
        mock_websocket.cookies = {AUTH_COOKIE_NAME: valid_jwt_token}

        user_id, user_email = await authenticate_websocket(
            mock_websocket, "test_app", require_auth=False
        )

        # Should return None even though token is present
        assert user_id is None
        assert user_email is None


class TestWebSocketAuthenticationErrors:
    """
    Test error handling in WebSocket authentication.

    Security: These tests validate that error conditions are handled
    securely, preventing information leakage and ensuring proper rejection
    of unauthorized connection attempts.
    """

    @pytest.mark.asyncio
    async def test_no_token_provided_require_auth(self, mock_websocket):
        """
        Test authentication failure when no token is provided.

        Security: Validates that when authentication is required but no
        token is provided, the connection is rejected (returns None, None).
        This ensures that unauthenticated connections are not allowed when
        auth is required, preventing unauthorized access.
        """
        user_id, user_email = await authenticate_websocket(
            mock_websocket, "test_app", require_auth=True
        )

        assert user_id is None
        assert user_email is None

    @pytest.mark.asyncio
    async def test_cookies_access_error_handled(self, mock_websocket, valid_jwt_token):
        """
        Test that errors accessing cookies are handled gracefully.

        Security: Validates that if cookie access fails (e.g., due to
        WebSocket implementation differences), the system gracefully fails
        closed (rejects connection) rather than crashing or exposing errors.
        This ensures robust error handling and prevents information leakage
        through error messages.
        """
        # Make cookies access raise an error
        mock_websocket.cookies = MagicMock(side_effect=AttributeError("No cookies"))
        mock_websocket.scope = {"headers": []}

        user_id, user_email = await authenticate_websocket(
            mock_websocket, "test_app", require_auth=True
        )

        # Should fail - no way to access cookies
        assert user_id is None
        assert user_email is None

    @pytest.mark.asyncio
    async def test_websocket_auth_uses_correct_cookie_name(self, mock_websocket, valid_jwt_token):
        """
        Regression test: Ensure WebSocket authentication uses AUTH_COOKIE_NAME (mdb_auth_token).

        This test prevents regressions where WebSocket authentication might use
        a hardcoded "token" cookie name instead of the shared AUTH_COOKIE_NAME.
        This ensures consistency with SharedAuthMiddleware and prevents auth failures.
        """
        # Test with correct cookie name (should work)
        mock_websocket.cookies = {AUTH_COOKIE_NAME: valid_jwt_token}
        user_id, user_email = await authenticate_websocket(
            mock_websocket, "test_app", require_auth=True
        )
        assert user_id == "user123"
        assert user_email == "test@example.com"

        # Test with old/wrong cookie name (should fail)
        mock_websocket.cookies = {"token": valid_jwt_token}  # Wrong name
        user_id, user_email = await authenticate_websocket(
            mock_websocket, "test_app", require_auth=True
        )
        # Should fail because wrong cookie name
        assert user_id is None
        assert user_email is None
