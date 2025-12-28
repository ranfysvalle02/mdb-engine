"""
Session Management

Provides session tracking and management for user authentication sessions.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from bson.objectid import ObjectId

try:
    from pymongo.errors import (ConnectionFailure, OperationFailure,
                                ServerSelectionTimeoutError)
except ImportError:
    ConnectionFailure = Exception
    OperationFailure = Exception
    ServerSelectionTimeoutError = Exception

from ..config import MAX_SESSIONS_PER_USER as CONFIG_MAX_SESSIONS
from ..config import SESSION_INACTIVITY_TIMEOUT as CONFIG_INACTIVITY_TIMEOUT

logger = logging.getLogger(__name__)


class SessionManager:
    """
    Manages user sessions with device tracking and activity monitoring.

    Tracks active sessions per user, supports multiple concurrent sessions,
    and provides session management capabilities.
    """

    def __init__(self, db, collection_name: str = "user_sessions"):
        """
        Initialize session manager.

        Args:
            db: MongoDB database instance (Motor AsyncIOMotorDatabase)
            collection_name: Name of the sessions collection (default: "user_sessions")
        """
        self.db = db
        self.collection = db[collection_name]
        self._indexes_created = False
        self.max_sessions = CONFIG_MAX_SESSIONS
        self.inactivity_timeout = CONFIG_INACTIVITY_TIMEOUT
        self.fingerprinting_enabled = False
        self.fingerprinting_strict = False

    async def ensure_indexes(self):
        """
        Ensure required indexes exist for the sessions collection.

        Creates:
        - Index on 'user_id' + 'last_seen' for session cleanup queries
        - Index on 'device_id' for device lookup
        - Index on 'refresh_jti' for token-to-session mapping
        """
        if self._indexes_created:
            return

        try:
            # Index for user session queries
            await self.collection.create_index(
                [("user_id", 1), ("last_seen", -1)], name="user_id_last_seen_idx"
            )

            # Index for device lookup
            await self.collection.create_index("device_id", name="device_id_idx")

            # Index for refresh token lookup
            await self.collection.create_index(
                "refresh_jti", unique=True, name="refresh_jti_unique_idx"
            )

            self._indexes_created = True
            logger.info("Session manager indexes created successfully")
        except (
            OperationFailure,
            ConnectionFailure,
            ServerSelectionTimeoutError,
            AttributeError,
            TypeError,
        ) as e:
            logger.error(f"Error creating session manager indexes: {e}", exc_info=True)
            # Don't raise - indexes might already exist

    async def create_session(
        self,
        user_id: str,
        device_id: str,
        refresh_jti: str,
        device_info: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
        session_fingerprint: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Create a new user session.

        Args:
            user_id: User identifier (email or user_id)
            device_id: Unique device identifier
            refresh_jti: Refresh token JWT ID
            device_info: Optional device metadata (user_agent, browser, os, etc.)
            ip_address: Optional IP address
            session_fingerprint: Optional session fingerprint hash for security

        Returns:
            Created session document or None if creation failed
        """
        try:
            await self.ensure_indexes()

            # Check session limit
            active_sessions = await self.get_user_sessions(user_id, active_only=True)
            if len(active_sessions) >= self.max_sessions:
                # Remove oldest inactive session
                await self.cleanup_inactive_sessions(user_id)
                # Check again
                active_sessions = await self.get_user_sessions(
                    user_id, active_only=True
                )
                if len(active_sessions) >= self.max_sessions:
                    # Force remove oldest session
                    if active_sessions:
                        oldest = active_sessions[-1]  # Last in sorted list (oldest)
                        await self.revoke_session(oldest["_id"])

            now = datetime.utcnow()
            session_doc = {
                "user_id": user_id,
                "device_id": device_id,
                "refresh_jti": refresh_jti,
                "created_at": now,
                "last_seen": now,
                "ip_address": ip_address,
                "active": True,
            }

            if self.fingerprinting_enabled and session_fingerprint:
                session_doc["session_fingerprint"] = session_fingerprint

            if device_info:
                session_doc.update(
                    {
                        "user_agent": device_info.get("user_agent"),
                        "browser": device_info.get("browser"),
                        "os": device_info.get("os"),
                        "device_type": device_info.get("device_type"),
                        "location": device_info.get("location"),
                    }
                )

            result = await self.collection.insert_one(session_doc)
            session_doc["_id"] = result.inserted_id

            logger.debug(
                f"Created session {result.inserted_id} for user {user_id} on device {device_id}"
            )
            return session_doc
        except (
            OperationFailure,
            ConnectionFailure,
            ServerSelectionTimeoutError,
            ValueError,
            TypeError,
        ) as e:
            logger.error(
                f"Error creating session for user {user_id}: {e}", exc_info=True
            )
            return None

    async def update_session_activity(
        self, refresh_jti: str, ip_address: Optional[str] = None
    ) -> bool:
        """
        Update session last_seen timestamp (activity tracking).

        Args:
            refresh_jti: Refresh token JWT ID
            ip_address: Optional IP address update

        Returns:
            True if session was updated, False otherwise
        """
        try:
            update_data = {
                "last_seen": datetime.utcnow(),
            }
            if ip_address:
                update_data["ip_address"] = ip_address

            result = await self.collection.update_one(
                {"refresh_jti": refresh_jti, "active": True}, {"$set": update_data}
            )

            return result.modified_count > 0
        except (
            OperationFailure,
            ConnectionFailure,
            ServerSelectionTimeoutError,
            ValueError,
            TypeError,
        ) as e:
            logger.error(
                f"Error updating session activity for {refresh_jti}: {e}", exc_info=True
            )
            return False

    async def get_session_by_refresh_token(
        self, refresh_jti: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get session by refresh token JWT ID.

        Args:
            refresh_jti: Refresh token JWT ID

        Returns:
            Session document or None if not found
        """
        try:
            session = await self.collection.find_one(
                {"refresh_jti": refresh_jti, "active": True}
            )
            return session
        except (
            OperationFailure,
            ConnectionFailure,
            ServerSelectionTimeoutError,
            ValueError,
            TypeError,
        ) as e:
            logger.error(
                f"Error getting session for refresh token {refresh_jti}: {e}",
                exc_info=True,
            )
            return None

    async def validate_session_fingerprint(
        self, refresh_jti: str, current_fingerprint: str, strict: Optional[bool] = None
    ) -> bool:
        """
        Validate session fingerprint matches stored fingerprint.

        Args:
            refresh_jti: Refresh token JWT ID
            current_fingerprint: Current session fingerprint to validate
            strict: If True, reject if fingerprint doesn't match. If False,
                   allow if no fingerprint stored.
                   If None, uses self.fingerprinting_strict

        Returns:
            True if fingerprint is valid, False otherwise
        """
        if not self.fingerprinting_enabled:
            return True

        try:
            session = await self.get_session_by_refresh_token(refresh_jti)
            if not session:
                return False

            stored_fingerprint = session.get("session_fingerprint")

            if not stored_fingerprint:
                strict_mode = (
                    strict if strict is not None else self.fingerprinting_strict
                )
                return not strict_mode

            return stored_fingerprint == current_fingerprint
        except (
            OperationFailure,
            ConnectionFailure,
            ServerSelectionTimeoutError,
            ValueError,
            TypeError,
            AttributeError,
        ) as e:
            logger.error(f"Error validating session fingerprint: {e}", exc_info=True)
            return False

    def configure_fingerprinting(self, enabled: bool = True, strict: bool = False):
        """
        Configure session fingerprinting settings.

        Args:
            enabled: Enable/disable session fingerprinting
            strict: If True, reject requests when fingerprint doesn't match
        """
        self.fingerprinting_enabled = enabled
        self.fingerprinting_strict = strict

    async def get_user_sessions(
        self, user_id: str, active_only: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Get all sessions for a user.

        Args:
            user_id: User identifier
            active_only: If True, only return active sessions

        Returns:
            List of session documents, sorted by last_seen (newest first)
        """
        try:
            query = {"user_id": user_id}
            if active_only:
                query["active"] = True

            sessions = (
                await self.collection.find(query).sort("last_seen", -1).to_list(None)
            )
            return sessions
        except (
            OperationFailure,
            ConnectionFailure,
            ServerSelectionTimeoutError,
            ValueError,
            TypeError,
        ) as e:
            logger.error(
                f"Error getting sessions for user {user_id}: {e}", exc_info=True
            )
            return []

    async def revoke_session(self, session_id: Any) -> bool:
        """
        Revoke a specific session.

        Args:
            session_id: Session _id (ObjectId or string)

        Returns:
            True if session was revoked, False otherwise
        """
        try:
            # Convert to ObjectId if string
            if isinstance(session_id, str):
                try:
                    session_id = ObjectId(session_id)
                except (TypeError, ValueError):
                    # Type 2: Recoverable - invalid format, return False
                    logger.warning(f"Invalid session_id format: {session_id}")
                    return False

            result = await self.collection.update_one(
                {"_id": session_id},
                {"$set": {"active": False, "revoked_at": datetime.utcnow()}},
            )

            if result.modified_count > 0:
                logger.debug(f"Session {session_id} revoked")
            return result.modified_count > 0
        except (
            OperationFailure,
            ConnectionFailure,
            ServerSelectionTimeoutError,
            ValueError,
            TypeError,
        ) as e:
            logger.error(f"Error revoking session {session_id}: {e}", exc_info=True)
            return False

    async def revoke_user_sessions(
        self, user_id: str, exclude_device_id: Optional[str] = None
    ) -> int:
        """
        Revoke all sessions for a user.

        Args:
            user_id: User identifier
            exclude_device_id: Optional device_id to exclude from revocation

        Returns:
            Number of sessions revoked
        """
        try:
            query = {"user_id": user_id, "active": True}
            if exclude_device_id:
                query["device_id"] = {"$ne": exclude_device_id}

            result = await self.collection.update_many(
                query, {"$set": {"active": False, "revoked_at": datetime.utcnow()}}
            )

            revoked_count = result.modified_count
            if revoked_count > 0:
                logger.info(f"Revoked {revoked_count} sessions for user {user_id}")
            return revoked_count
        except (
            OperationFailure,
            ConnectionFailure,
            ServerSelectionTimeoutError,
            ValueError,
            TypeError,
        ) as e:
            logger.error(
                f"Error revoking sessions for user {user_id}: {e}", exc_info=True
            )
            return 0

    async def cleanup_inactive_sessions(self, user_id: Optional[str] = None) -> int:
        """
        Clean up inactive sessions (beyond inactivity timeout).

        Args:
            user_id: Optional user_id to limit cleanup to specific user

        Returns:
            Number of sessions cleaned up
        """
        try:
            cutoff_time = datetime.utcnow() - timedelta(seconds=self.inactivity_timeout)

            query = {"active": True, "last_seen": {"$lt": cutoff_time}}
            if user_id:
                query["user_id"] = user_id

            result = await self.collection.update_many(
                query,
                {
                    "$set": {
                        "active": False,
                        "revoked_at": datetime.utcnow(),
                        "reason": "inactivity",
                    }
                },
            )

            cleaned_count = result.modified_count
            if cleaned_count > 0:
                logger.debug(f"Cleaned up {cleaned_count} inactive sessions")
            return cleaned_count
        except (
            OperationFailure,
            ConnectionFailure,
            ServerSelectionTimeoutError,
            ValueError,
            TypeError,
        ) as e:
            logger.error(f"Error cleaning up inactive sessions: {e}", exc_info=True)
            return 0

    async def revoke_session_by_refresh_token(self, refresh_jti: str) -> bool:
        """
        Revoke session by refresh token JWT ID.

        Args:
            refresh_jti: Refresh token JWT ID

        Returns:
            True if session was revoked, False otherwise
        """
        try:
            result = await self.collection.update_one(
                {"refresh_jti": refresh_jti, "active": True},
                {"$set": {"active": False, "revoked_at": datetime.utcnow()}},
            )
            return result.modified_count > 0
        except (
            OperationFailure,
            ConnectionFailure,
            ServerSelectionTimeoutError,
            ValueError,
            TypeError,
        ) as e:
            logger.error(
                f"Error revoking session by refresh token {refresh_jti}: {e}",
                exc_info=True,
            )
            return False
