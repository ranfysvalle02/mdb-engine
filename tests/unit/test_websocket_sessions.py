"""
Unit tests for WebSocket Session Manager.

Tests envelope encryption integration, session lifecycle, and validation.
"""

import base64
import os
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

# Set test master key before importing
# Always set a valid base64-encoded master key (other test files may have set it incorrectly)
os.environ["MDB_ENGINE_MASTER_KEY"] = base64.b64encode(b"x" * 32).decode()

from mdb_engine.auth.websocket_sessions import (
    SESSION_TTL_HOURS,
    WebSocketSessionManager,
    create_websocket_session_endpoint,
)
from mdb_engine.core.encryption import EnvelopeEncryptionService


@pytest.fixture
def mock_mongo_db():
    """Create a mock MongoDB database."""
    db = AsyncMock()
    collection = AsyncMock()
    db.__getitem__ = MagicMock(return_value=collection)
    return db, collection


@pytest.fixture
def encryption_service():
    """Create an encryption service instance."""
    return EnvelopeEncryptionService()


@pytest.fixture
def session_manager(mock_mongo_db, encryption_service):
    """Create a WebSocketSessionManager instance."""
    db, _ = mock_mongo_db
    return WebSocketSessionManager(db, encryption_service)


@pytest.mark.asyncio
class TestWebSocketSessionManager:
    """Unit tests for WebSocketSessionManager."""

    async def test_generate_session_key(self):
        """Test session key generation."""
        key1 = WebSocketSessionManager.generate_session_key()
        key2 = WebSocketSessionManager.generate_session_key()

        # Keys should be different
        assert key1 != key2

        # Keys should be base64 URL-safe strings
        assert isinstance(key1, str)
        assert len(key1) > 0

        # Decode to verify it's valid base64
        try:
            decoded = base64.urlsafe_b64decode(key1 + "==")
            assert len(decoded) == 32  # 256 bits
        except Exception:
            pytest.fail("Session key is not valid base64")

    async def test_create_session(self, session_manager, mock_mongo_db):
        """Test session creation."""
        db, collection = mock_mongo_db

        # Mock successful insert
        collection.insert_one = AsyncMock()

        session_key = await session_manager.create_session(
            user_id="user123",
            user_email="test@example.com",
            app_slug="test_app",
        )

        # Verify session key was generated
        assert session_key is not None
        assert isinstance(session_key, str)
        assert len(session_key) > 0

        # Verify insert was called
        assert collection.insert_one.called
        call_args = collection.insert_one.call_args[0][0]

        # Verify document structure
        assert call_args["_id"] == session_key
        assert call_args["user_id"] == "user123"
        assert call_args["user_email"] == "test@example.com"
        assert call_args["app_slug"] == "test_app"
        assert "encrypted_key" in call_args
        assert "encrypted_dek" in call_args
        assert "expires_at" in call_args
        assert call_args["algorithm"] == "AES-256-GCM"

    async def test_validate_session_success(
        self, session_manager, mock_mongo_db, encryption_service
    ):
        """Test successful session validation."""
        db, collection = mock_mongo_db

        # Create a real session key and encrypt it
        session_key = WebSocketSessionManager.generate_session_key()
        encrypted_key, encrypted_dek = encryption_service.encrypt_secret(session_key)

        expires_at = datetime.utcnow() + timedelta(hours=SESSION_TTL_HOURS)

        # Mock find_one to return session document
        collection.find_one = AsyncMock(
            return_value={
                "_id": session_key,
                "user_id": "user123",
                "user_email": "test@example.com",
                "app_slug": "test_app",
                "encrypted_key": base64.b64encode(encrypted_key).decode(),
                "encrypted_dek": base64.b64encode(encrypted_dek).decode(),
                "algorithm": "AES-256-GCM",
                "created_at": datetime.utcnow(),
                "expires_at": expires_at,
            }
        )

        # Validate session
        session_data = await session_manager.validate_session(session_key)

        # Verify validation succeeded
        assert session_data is not None
        assert session_data["user_id"] == "user123"
        assert session_data["user_email"] == "test@example.com"
        assert session_data["app_slug"] == "test_app"

    async def test_validate_session_not_found(self, session_manager, mock_mongo_db):
        """Test session validation when session not found."""
        db, collection = mock_mongo_db

        # Mock find_one to return None
        collection.find_one = AsyncMock(return_value=None)

        session_key = "nonexistent_key"
        session_data = await session_manager.validate_session(session_key)

        # Verify validation failed
        assert session_data is None

    async def test_validate_session_expired(
        self, session_manager, mock_mongo_db, encryption_service
    ):
        """Test session validation when session expired."""
        db, collection = mock_mongo_db

        # Create a real session key and encrypt it
        session_key = WebSocketSessionManager.generate_session_key()
        encrypted_key, encrypted_dek = encryption_service.encrypt_secret(session_key)

        # Mock expired session
        expires_at = datetime.utcnow() - timedelta(hours=1)  # Expired

        collection.find_one = AsyncMock(
            return_value={
                "_id": session_key,
                "user_id": "user123",
                "encrypted_key": base64.b64encode(encrypted_key).decode(),
                "encrypted_dek": base64.b64encode(encrypted_dek).decode(),
                "expires_at": expires_at,
            }
        )
        collection.delete_one = AsyncMock()

        # Validate session
        session_data = await session_manager.validate_session(session_key)

        # Verify validation failed
        assert session_data is None

        # Verify expired session was deleted
        assert collection.delete_one.called

    async def test_validate_session_user_mismatch(
        self, session_manager, mock_mongo_db, encryption_service
    ):
        """Test session validation when user_id doesn't match."""
        db, collection = mock_mongo_db

        # Create a real session key and encrypt it
        session_key = WebSocketSessionManager.generate_session_key()
        encrypted_key, encrypted_dek = encryption_service.encrypt_secret(session_key)

        expires_at = datetime.utcnow() + timedelta(hours=SESSION_TTL_HOURS)

        collection.find_one = AsyncMock(
            return_value={
                "_id": session_key,
                "user_id": "user123",
                "encrypted_key": base64.b64encode(encrypted_key).decode(),
                "encrypted_dek": base64.b64encode(encrypted_dek).decode(),
                "expires_at": expires_at,
            }
        )

        # Validate with different user_id
        session_data = await session_manager.validate_session(session_key, user_id="different_user")

        # Verify validation failed
        assert session_data is None

    async def test_revoke_session(self, session_manager, mock_mongo_db):
        """Test session revocation."""
        db, collection = mock_mongo_db

        # Mock successful delete
        collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))

        session_key = "test_session_key"
        result = await session_manager.revoke_session(session_key)

        # Verify revocation succeeded
        assert result is True
        assert collection.delete_one.called
        call_args = collection.delete_one.call_args[0][0]
        assert call_args == {"_id": session_key}

    async def test_revoke_session_not_found(self, session_manager, mock_mongo_db):
        """Test session revocation when session not found."""
        db, collection = mock_mongo_db

        # Mock delete with no matches
        collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=0))

        session_key = "nonexistent_key"
        result = await session_manager.revoke_session(session_key)

        # Verify revocation returned False
        assert result is False

    async def test_revoke_user_sessions(self, session_manager, mock_mongo_db):
        """Test revoking all sessions for a user."""
        db, collection = mock_mongo_db

        # Mock successful delete
        collection.delete_many = AsyncMock(return_value=MagicMock(deleted_count=3))

        result = await session_manager.revoke_user_sessions("user123", app_slug="test_app")

        # Verify revocation succeeded
        assert result == 3
        assert collection.delete_many.called
        call_args = collection.delete_many.call_args[0][0]
        assert call_args == {"user_id": "user123", "app_slug": "test_app"}

    async def test_revoke_user_sessions_all_apps(self, session_manager, mock_mongo_db):
        """Test revoking all sessions for a user across all apps."""
        db, collection = mock_mongo_db

        # Mock successful delete
        collection.delete_many = AsyncMock(return_value=MagicMock(deleted_count=5))

        result = await session_manager.revoke_user_sessions("user123")

        # Verify revocation succeeded
        assert result == 5
        call_args = collection.delete_many.call_args[0][0]
        assert call_args == {"user_id": "user123"}

    async def test_cleanup_expired_sessions(self, session_manager, mock_mongo_db):
        """Test cleanup of expired sessions."""
        db, collection = mock_mongo_db

        # Mock successful delete
        collection.delete_many = AsyncMock(return_value=MagicMock(deleted_count=2))

        result = await session_manager.cleanup_expired_sessions()

        # Verify cleanup succeeded
        assert result == 2
        assert collection.delete_many.called
        call_args = collection.delete_many.call_args[0][0]
        assert "expires_at" in call_args
        assert "$lt" in call_args["expires_at"]


@pytest.mark.asyncio
class TestWebSocketSessionEndpoint:
    """Unit tests for WebSocket session endpoint."""

    async def test_create_endpoint_requires_auth(self, session_manager, mock_mongo_db):
        """Test that endpoint requires authentication."""
        from fastapi import Request
        from fastapi.responses import JSONResponse

        endpoint = create_websocket_session_endpoint(session_manager)

        # Create mock request without user
        request = MagicMock(spec=Request)
        request.state = MagicMock()
        delattr(request.state, "user")  # Ensure no user attribute

        # Ensure app.state.user_pool is None (not a MagicMock)
        request.app = MagicMock()
        request.app.state = MagicMock()
        request.app.state.user_pool = None
        request.cookies = {}  # No auth cookie

        # Call endpoint
        response = await endpoint(request)

        # Verify 401 response
        assert isinstance(response, JSONResponse)
        assert response.status_code == 401

    async def test_create_endpoint_success(self, session_manager, mock_mongo_db):
        """Test successful session key generation."""
        from fastapi.responses import JSONResponse

        db, collection = mock_mongo_db
        collection.insert_one = AsyncMock()

        endpoint = create_websocket_session_endpoint(session_manager)

        # Create mock request with user
        request = MagicMock()
        request.state = MagicMock()
        request.state.user = {
            "user_id": "user123",
            "email": "test@example.com",
        }
        request.state.app_slug = "test_app"

        # Call endpoint
        response = await endpoint(request)

        # Verify success response
        assert isinstance(response, JSONResponse)
        assert response.status_code == 200

        # Parse response body
        import json

        body = json.loads(response.body.decode())
        assert "session_key" in body
        assert "expires_at" in body
        assert "ttl_hours" in body
        assert body["ttl_hours"] == SESSION_TTL_HOURS

    async def test_create_endpoint_missing_user_id(self, session_manager, mock_mongo_db):
        """Test endpoint with missing user_id."""
        from types import SimpleNamespace

        from fastapi.responses import JSONResponse

        endpoint = create_websocket_session_endpoint(session_manager)

        # Create mock request with user but no user_id
        # Use SimpleNamespace to properly simulate request.state
        user_dict = {"email": "test@example.com"}  # No user_id or sub
        request = MagicMock()
        state = SimpleNamespace()
        state.user = user_dict
        request.state = state

        # Call endpoint
        response = await endpoint(request)

        # Verify 400 response
        assert isinstance(response, JSONResponse)
        assert response.status_code == 400
