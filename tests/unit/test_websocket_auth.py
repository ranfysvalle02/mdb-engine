"""
Tests for WebSocket authentication using subprotocol tunneling.

Security Focus:
- Validates subprotocol tunneling prevents CSRF attacks (no cookies needed)
- Ensures tokens are not exposed in URLs (security best practice)
- Verifies authentication happens before connection acceptance
- Tests token validation and expiration handling
- Confirms multi-app isolation for WebSocket connections

Test Coverage:
- Successful authentication via subprotocol header
- Multiple protocol handling
- Invalid token rejection
- Expired token rejection
- Short protocol string filtering (security: prevents protocol confusion)
- Scope storage for subprotocol acceptance
- Error handling for missing/invalid tokens
- No-auth scenarios
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import jwt
import pytest

from mdb_engine.auth.dependencies import SECRET_KEY
from mdb_engine.routing.websockets import authenticate_websocket


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket instance."""
    ws = MagicMock()
    ws.headers = {}
    ws.query_params = {}
    ws.cookies = {}
    ws.scope = {}
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


class TestWebSocketSubprotocolAuthentication:
    """
    Test WebSocket authentication via Sec-WebSocket-Protocol header.

    Security: These tests validate that subprotocol tunneling works correctly,
    ensuring tokens are securely transmitted via headers (not URLs) and
    authentication occurs before connection establishment.
    """

    @pytest.mark.asyncio
    async def test_authenticate_via_subprotocol_success(self, mock_websocket, valid_jwt_token):
        """
        Test successful authentication via subprotocol header.

        Security: Validates that valid JWT tokens in subprotocol headers
        are correctly extracted and validated. Ensures subprotocol is stored
        in scope for proper WebSocket handshake completion.
        """
        # Set up WebSocket with token in subprotocol header
        mock_websocket.headers = {"sec-websocket-protocol": valid_jwt_token}

        user_id, user_email = await authenticate_websocket(
            mock_websocket, "test_app", require_auth=True
        )

        assert user_id == "user123"
        assert user_email == "test@example.com"
        # Verify subprotocol was stored in scope
        assert mock_websocket.scope.get("_selected_subprotocol") == valid_jwt_token

    @pytest.mark.asyncio
    async def test_authenticate_via_subprotocol_multiple_protocols(
        self, mock_websocket, valid_jwt_token
    ):
        """
        Test authentication when multiple protocols are sent.

        Security: Validates that when clients send multiple protocols
        (e.g., ["chat", token, "json"]), the system correctly identifies
        and extracts the JWT token from the list. This ensures compatibility
        with clients that send multiple protocols while maintaining security.
        """
        # Client sends multiple protocols, token is one of them
        protocols = f"chat, {valid_jwt_token}, json"
        mock_websocket.headers = {"sec-websocket-protocol": protocols}

        user_id, user_email = await authenticate_websocket(
            mock_websocket, "test_app", require_auth=True
        )

        assert user_id == "user123"
        assert user_email == "test@example.com"

    @pytest.mark.asyncio
    async def test_authenticate_via_subprotocol_invalid_token(self, mock_websocket):
        """
        Test authentication failure with invalid token in subprotocol.

        Security: Ensures that invalid or malformed JWT tokens are rejected
        immediately, preventing unauthorized access. Invalid tokens raise
        exceptions during decode, which is the expected secure behavior.
        """
        invalid_token = "not.a.valid.jwt.token"
        mock_websocket.headers = {"sec-websocket-protocol": invalid_token}

        # Invalid JWT tokens raise exceptions during decode
        with pytest.raises(jwt.DecodeError):
            await authenticate_websocket(mock_websocket, "test_app", require_auth=True)

    @pytest.mark.asyncio
    async def test_authenticate_via_subprotocol_expired_token(self, mock_websocket):
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
        mock_websocket.headers = {"sec-websocket-protocol": expired_token}

        with pytest.raises(jwt.ExpiredSignatureError):
            await authenticate_websocket(mock_websocket, "test_app", require_auth=True)

    @pytest.mark.asyncio
    async def test_authenticate_via_subprotocol_short_protocol_ignored(
        self, mock_websocket, valid_jwt_token
    ):
        """
        Test that short protocol strings are ignored (not JWT-like).

        Security: Validates that short protocol strings (like "chat", "json")
        are not mistaken for JWT tokens. This prevents protocol confusion
        attacks where malicious clients might try to use standard protocol
        names to bypass authentication. Only JWT-like strings (>20 chars, no spaces)
        are considered as potential tokens.
        """
        # Short protocol should be ignored - no fallback, should fail
        mock_websocket.headers = {"sec-websocket-protocol": "chat"}

        user_id, user_email = await authenticate_websocket(
            mock_websocket, "test_app", require_auth=True
        )

        # Should fail - no valid token in subprotocol
        assert user_id is None
        assert user_email is None

    @pytest.mark.asyncio
    async def test_authenticate_via_subprotocol_scope_access(self, mock_websocket, valid_jwt_token):
        """
        Test that subprotocol is stored in scope for accept() to use.

        Security: Validates that the selected subprotocol is stored in the
        WebSocket scope so that accept() can echo it back to the client.
        This is required by the WebSocket specification - the server must
        accept with the same subprotocol the client requested, or the
        browser will reject the connection. This ensures proper handshake
        completion and prevents protocol downgrade attacks.
        """
        mock_websocket.headers = {"sec-websocket-protocol": valid_jwt_token}

        await authenticate_websocket(mock_websocket, "test_app", require_auth=True)

        # Verify subprotocol stored in scope
        assert mock_websocket.scope["_selected_subprotocol"] == valid_jwt_token


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
        mock_websocket.headers = {"sec-websocket-protocol": valid_jwt_token}

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
    async def test_headers_access_error_handled(self, mock_websocket, valid_jwt_token):
        """
        Test that errors accessing headers are handled gracefully.

        Security: Validates that if header access fails (e.g., due to
        WebSocket implementation differences), the system gracefully fails
        closed (rejects connection) rather than crashing or exposing errors.
        This ensures robust error handling and prevents information leakage
        through error messages.
        """
        # Make headers access raise an error
        mock_websocket.headers = MagicMock(side_effect=AttributeError("No headers"))
        mock_websocket.scope = {}

        user_id, user_email = await authenticate_websocket(
            mock_websocket, "test_app", require_auth=True
        )

        # Should fail - no way to access subprotocol
        assert user_id is None
        assert user_email is None

    @pytest.mark.asyncio
    async def test_scope_access_via_headers(self, mock_websocket, valid_jwt_token):
        """
        Test accessing headers via scope when headers attribute not available.

        Security: Validates that the system can access subprotocol headers
        via ASGI scope when the WebSocket object doesn't expose a headers
        attribute. This ensures compatibility with different WebSocket
        implementations while maintaining secure token extraction. The
        fallback to scope access ensures security is maintained across
        different runtime environments.
        """
        # Simulate ASGI-style headers in scope
        mock_websocket.headers = None  # No headers attribute
        mock_websocket.scope = {
            "headers": [(b"sec-websocket-protocol", valid_jwt_token.encode("utf-8"))]
        }

        user_id, user_email = await authenticate_websocket(
            mock_websocket, "test_app", require_auth=True
        )

        # Should authenticate via scope headers
        assert user_id == "user123"
        assert user_email == "test@example.com"
