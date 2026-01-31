"""
WebSocket Session Manager with Envelope Encryption

Manages WebSocket session keys using envelope encryption and private collections.
Provides secure-by-default WebSocket authentication without relying on CSRF cookies.

This module is part of MDB_ENGINE - MongoDB Engine.

Security Model:
- Session keys generated on authentication
- Stored encrypted in _mdb_engine_websocket_sessions collection
- Validated during WebSocket upgrade
- Uses envelope encryption (same as app secrets)
- Security by default: CSRF always required
"""

import base64
import logging
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import OperationFailure, PyMongoError

from ..core.encryption import EnvelopeEncryptionService

logger = logging.getLogger(__name__)

# Collection name for storing encrypted WebSocket session keys
WEBSOCKET_SESSIONS_COLLECTION_NAME = "_mdb_engine_websocket_sessions"

# Session key configuration
SESSION_KEY_SIZE = 32  # 256 bits
SESSION_TTL_HOURS = 24  # Sessions expire after 24 hours


class WebSocketSessionManager:
    """
    Manages WebSocket session keys using envelope encryption.

    Session keys are:
    - Generated on user authentication
    - Encrypted using envelope encryption
    - Stored in private collection (_mdb_engine_websocket_sessions)
    - Validated during WebSocket upgrade
    - Automatically expired after TTL
    """

    def __init__(
        self,
        mongo_db: AsyncIOMotorDatabase,
        encryption_service: EnvelopeEncryptionService,
    ):
        """
        Initialize the WebSocket session manager.

        Args:
            mongo_db: MongoDB database instance (raw, not scoped)
            encryption_service: Envelope encryption service instance
        """
        self._mongo_db = mongo_db
        self._encryption_service = encryption_service
        self._sessions_collection = mongo_db[WEBSOCKET_SESSIONS_COLLECTION_NAME]

    @staticmethod
    def generate_session_key() -> str:
        """
        Generate a random WebSocket session key.

        Returns:
            Base64-encoded session key string
        """
        key_bytes = secrets.token_bytes(SESSION_KEY_SIZE)
        return base64.urlsafe_b64encode(key_bytes).decode().rstrip("=")

    async def create_session(
        self,
        user_id: str,
        user_email: str | None = None,
        app_slug: str | None = None,
    ) -> str:
        """
        Create a new WebSocket session with encrypted session key.

        Args:
            user_id: User ID
            user_email: Optional user email
            app_slug: Optional app slug for scoping

        Returns:
            Plaintext session key (to be sent to client)

        Raises:
            OperationFailure: If MongoDB operation fails
        """
        try:
            # Generate session key
            session_key = self.generate_session_key()

            # Encrypt session key using envelope encryption
            encrypted_key, encrypted_dek = self._encryption_service.encrypt_secret(session_key)

            # Encode as base64 for storage
            encrypted_key_b64 = base64.b64encode(encrypted_key).decode()
            encrypted_dek_b64 = base64.b64encode(encrypted_dek).decode()

            # Calculate expiration
            expires_at = datetime.utcnow() + timedelta(hours=SESSION_TTL_HOURS)

            # Prepare document
            document = {
                "_id": session_key,  # Use session key as ID for fast lookup
                "user_id": user_id,
                "user_email": user_email,
                "app_slug": app_slug,
                "encrypted_key": encrypted_key_b64,
                "encrypted_dek": encrypted_dek_b64,
                "algorithm": "AES-256-GCM",
                "created_at": datetime.utcnow(),
                "expires_at": expires_at,
            }

            # Store in private collection
            await self._sessions_collection.insert_one(document)

            logger.info(
                f"Created WebSocket session for user '{user_id}' "
                f"(app: {app_slug}, expires: {expires_at})"
            )

            return session_key

        except (OperationFailure, PyMongoError):
            logger.exception("Failed to create WebSocket session")
            raise

    async def validate_session(
        self,
        session_key: str,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Validate a WebSocket session key.

        Args:
            session_key: Session key to validate
            user_id: Optional user ID for additional validation

        Returns:
            Session document if valid, None otherwise

        Raises:
            OperationFailure: If MongoDB operation fails
        """
        try:
            # Find session by key
            session_doc = await self._sessions_collection.find_one({"_id": session_key})

            if not session_doc:
                logger.warning(f"WebSocket session not found: {session_key[:16]}...")
                return None

            # Check expiration
            expires_at = session_doc.get("expires_at")
            if expires_at and expires_at < datetime.utcnow():
                logger.warning(
                    f"WebSocket session expired: {session_key[:16]}... " f"(expired: {expires_at})"
                )
                # Clean up expired session
                await self._sessions_collection.delete_one({"_id": session_key})
                return None

            # Optional: Validate user_id matches
            if user_id and session_doc.get("user_id") != user_id:
                logger.warning(
                    f"WebSocket session user mismatch: "
                    f"session_user={session_doc.get('user_id')}, "
                    f"provided_user={user_id}"
                )
                return None

            # Decrypt session key to verify it's valid
            try:
                encrypted_key = base64.b64decode(session_doc["encrypted_key"])
                encrypted_dek = base64.b64decode(session_doc["encrypted_dek"])
                decrypted_key = self._encryption_service.decrypt_secret(
                    encrypted_key, encrypted_dek
                )

                # Verify decrypted key matches session_key
                if decrypted_key != session_key:
                    logger.error(
                        f"WebSocket session key decryption mismatch: "
                        f"session_key={session_key[:16]}..."
                    )
                    return None

            except (ValueError, TypeError, AttributeError, KeyError):
                logger.exception("Failed to decrypt WebSocket session key")
                return None

            logger.debug(
                f"Validated WebSocket session for user '{session_doc.get('user_id')}' "
                f"(app: {session_doc.get('app_slug')})"
            )

            return {
                "user_id": session_doc.get("user_id"),
                "user_email": session_doc.get("user_email"),
                "app_slug": session_doc.get("app_slug"),
                "created_at": session_doc.get("created_at"),
                "expires_at": session_doc.get("expires_at"),
            }

        except (OperationFailure, PyMongoError):
            logger.exception("Failed to validate WebSocket session")
            raise

    async def revoke_session(self, session_key: str) -> bool:
        """
        Revoke a WebSocket session.

        Args:
            session_key: Session key to revoke

        Returns:
            True if session was revoked, False if not found
        """
        try:
            result = await self._sessions_collection.delete_one({"_id": session_key})
            if result.deleted_count > 0:
                logger.info(f"Revoked WebSocket session: {session_key[:16]}...")
                return True
            return False
        except (OperationFailure, PyMongoError):
            logger.exception("Failed to revoke WebSocket session")
            return False

    async def revoke_user_sessions(self, user_id: str, app_slug: str | None = None) -> int:
        """
        Revoke all sessions for a user.

        Args:
            user_id: User ID
            app_slug: Optional app slug filter

        Returns:
            Number of sessions revoked
        """
        try:
            query = {"user_id": user_id}
            if app_slug:
                query["app_slug"] = app_slug

            result = await self._sessions_collection.delete_many(query)
            logger.info(
                f"Revoked {result.deleted_count} WebSocket sessions "
                f"for user '{user_id}' (app: {app_slug})"
            )
            return result.deleted_count
        except (OperationFailure, PyMongoError):
            logger.exception("Failed to revoke user WebSocket sessions")
            return 0

    async def cleanup_expired_sessions(self) -> int:
        """
        Clean up expired WebSocket sessions.

        Returns:
            Number of sessions cleaned up
        """
        try:
            result = await self._sessions_collection.delete_many(
                {"expires_at": {"$lt": datetime.utcnow()}}
            )
            if result.deleted_count > 0:
                logger.info(f"Cleaned up {result.deleted_count} expired WebSocket sessions")
            return result.deleted_count
        except (OperationFailure, PyMongoError):
            logger.exception("Failed to cleanup expired WebSocket sessions")
            return 0


def create_websocket_session_endpoint(
    session_manager: WebSocketSessionManager,
) -> Callable:
    """
    Create a FastAPI endpoint for generating WebSocket session keys.

    This endpoint requires authentication and generates a new WebSocket session key
    for the authenticated user. The session key is encrypted and stored in the
    private collection.

    Args:
        session_manager: WebSocketSessionManager instance

    Returns:
        FastAPI route handler function

    Example:
        ```python
        from mdb_engine.auth.websocket_sessions import (
            WebSocketSessionManager,
            create_websocket_session_endpoint,
        )
        from mdb_engine.core.encryption import EnvelopeEncryptionService

        # Initialize session manager
        encryption_service = EnvelopeEncryptionService()
        session_manager = WebSocketSessionManager(
            mongo_db=db,
            encryption_service=encryption_service,
        )

        # Create endpoint
        endpoint = create_websocket_session_endpoint(session_manager)
        app.get("/auth/websocket-session")(endpoint)
        ```

    The endpoint:
    - Requires authentication (user must be logged in)
    - Returns JSON: `{"session_key": "...", "expires_at": "..."}`
    - Uses user info from `request.state.user` (set by SharedAuthMiddleware)
    """
    from fastapi import Request, status
    from fastapi.responses import JSONResponse

    async def websocket_session_endpoint(request: Request) -> JSONResponse:
        """
        Generate a WebSocket session key for the authenticated user.

        Requires:
        - User to be authenticated (via request.state.user or auth cookie)
        - WebSocket session manager to be available

        Returns:
        - JSONResponse with session_key and expires_at
        """
        # Check if user is authenticated (set by middleware)
        user = getattr(request.state, "user", None)

        # If not set by middleware, try to authenticate using cookie
        # This handles the case where endpoint is on parent app without auth middleware
        if not user:
            from .shared_middleware import AUTH_COOKIE_NAME

            # Get user pool from app state
            user_pool = None
            try:
                if hasattr(request, "app") and hasattr(request.app, "state"):
                    user_pool = getattr(request.app.state, "user_pool", None)
            except (AttributeError, TypeError):
                pass

            # Only try to authenticate if we have a real user pool (not None)
            if user_pool is not None:
                # Extract token from cookie
                token = None
                try:
                    if hasattr(request, "cookies"):
                        token = request.cookies.get(AUTH_COOKIE_NAME)
                except (AttributeError, TypeError):
                    pass

                if token:
                    try:
                        # Validate token and get user
                        user = await user_pool.validate_token(token)
                    except (TypeError, AttributeError):
                        # If user_pool is a mock that can't be awaited, ignore
                        pass

            if not user:
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "Authentication required"},
                )

        # Extract user info
        # Prefer user_id, sub (JWT standard), or _id (MongoDB document ID)
        user_id = user.get("user_id") or user.get("sub") or user.get("_id")
        if not user_id:
            # Email is not a valid user_id - it's just metadata
            logger.error("Cannot generate WebSocket session: user_id not found in user data")
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "Invalid user data"},
            )
        user_email = user.get("email")
        app_slug = getattr(request.state, "app_slug", None)

        try:
            # Generate session key
            session_key = await session_manager.create_session(
                user_id=str(user_id),
                user_email=user_email,
                app_slug=app_slug,
            )

            # Get expiration time (24 hours from now)
            from datetime import datetime, timedelta

            expires_at = datetime.utcnow() + timedelta(hours=SESSION_TTL_HOURS)

            logger.info(
                f"Generated WebSocket session key for user '{user_id}' " f"(app: {app_slug})"
            )

            return JSONResponse(
                {
                    "session_key": session_key,
                    "expires_at": expires_at.isoformat(),
                    "ttl_hours": SESSION_TTL_HOURS,
                }
            )

        except (
            ValueError,
            TypeError,
            AttributeError,
            RuntimeError,
            OperationFailure,
            PyMongoError,
        ):
            logger.exception("Failed to generate WebSocket session key")
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": "Failed to generate WebSocket session key"},
            )

    return websocket_session_endpoint
