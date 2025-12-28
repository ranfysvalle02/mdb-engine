"""
Token Blacklist Management

Provides token blacklist functionality for revoking tokens before expiration.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

try:
    from pymongo.errors import (ConnectionFailure, OperationFailure,
                                ServerSelectionTimeoutError)
except ImportError:
    ConnectionFailure = Exception
    OperationFailure = Exception
    ServerSelectionTimeoutError = Exception

logger = logging.getLogger(__name__)


class TokenBlacklist:
    """
    Manages token blacklist for revoked tokens.

    Stores revoked tokens in MongoDB with TTL index for automatic cleanup.
    Supports immediate revocation and scheduled revocation.
    """

    def __init__(self, db, collection_name: str = "token_blacklist"):
        """
        Initialize token blacklist manager.

        Args:
            db: MongoDB database instance (Motor AsyncIOMotorDatabase)
            collection_name: Name of the blacklist collection (default: "token_blacklist")
        """
        self.db = db
        self.collection = db[collection_name]
        self._indexes_created = False

    async def ensure_indexes(self):
        """
        Ensure required indexes exist for the blacklist collection.

        Creates:
        - Unique index on 'jti' (JWT ID) for fast lookups
        - TTL index on 'expires_at' for automatic cleanup
        """
        if self._indexes_created:
            return

        try:
            # Create unique index on jti
            await self.collection.create_index(
                "jti", unique=True, name="jti_unique_idx"
            )

            # Create TTL index on expires_at (auto-delete expired entries)
            await self.collection.create_index(
                "expires_at", expireAfterSeconds=0, name="expires_at_ttl_idx"
            )

            self._indexes_created = True
            logger.info("Token blacklist indexes created successfully")
        except (
            OperationFailure,
            ConnectionFailure,
            ServerSelectionTimeoutError,
            AttributeError,
            TypeError,
        ) as e:
            logger.error(f"Error creating token blacklist indexes: {e}", exc_info=True)
            # Don't raise - indexes might already exist

    async def revoke_token(
        self,
        jti: str,
        user_id: Optional[str] = None,
        expires_at: Optional[datetime] = None,
        reason: Optional[str] = None,
    ) -> bool:
        """
        Revoke a token by adding it to the blacklist.

        Args:
            jti: JWT ID (unique token identifier)
            user_id: Optional user ID for tracking
            expires_at: Optional expiration time (defaults to token's exp claim if available)
            reason: Optional reason for revocation (e.g., "logout", "password_change")

        Returns:
            True if token was successfully blacklisted, False otherwise
        """
        try:
            await self.ensure_indexes()

            # If expires_at not provided, use a default (e.g., 7 days from now)
            if expires_at is None:
                expires_at = datetime.utcnow() + timedelta(days=7)

            blacklist_entry = {
                "jti": jti,
                "user_id": user_id,
                "revoked_at": datetime.utcnow(),
                "expires_at": expires_at,
                "reason": reason or "manual_revocation",
            }

            # Use upsert to handle duplicate jti gracefully
            await self.collection.update_one(
                {"jti": jti}, {"$set": blacklist_entry}, upsert=True
            )

            logger.debug(f"Token {jti} added to blacklist (reason: {reason})")
            return True
        except (
            OperationFailure,
            ConnectionFailure,
            ServerSelectionTimeoutError,
            ValueError,
            TypeError,
        ) as e:
            logger.error(f"Error revoking token {jti}: {e}", exc_info=True)
            return False

    async def is_revoked(self, jti: str) -> bool:
        """
        Check if a token is revoked (blacklisted).

        Args:
            jti: JWT ID to check

        Returns:
            True if token is blacklisted, False otherwise
        """
        try:
            await self.ensure_indexes()

            entry = await self.collection.find_one({"jti": jti})
            if entry:
                # Check if entry hasn't expired (TTL index should handle this, but double-check)
                expires_at = entry.get("expires_at")
                if expires_at and isinstance(expires_at, datetime):
                    if datetime.utcnow() < expires_at:
                        return True
                elif expires_at is None:
                    # No expiration means it's permanently blacklisted
                    return True

            return False
        except (
            OperationFailure,
            ConnectionFailure,
            ServerSelectionTimeoutError,
            ValueError,
            TypeError,
        ) as e:
            logger.error(
                f"Error checking token revocation status for {jti}: {e}", exc_info=True
            )
            # On error, assume not revoked (fail open for availability)
            return False

    async def revoke_all_user_tokens(
        self, user_id: str, reason: Optional[str] = None
    ) -> int:
        """
        Revoke all tokens for a specific user.

        Note: This doesn't add tokens to blacklist individually (would require
        storing all jti values). Instead, it marks the user as having all tokens
        revoked, which should be checked alongside individual token checks.

        Args:
            user_id: User ID whose tokens should be revoked
            reason: Optional reason for revocation

        Returns:
            Number of tokens revoked (approximate)
        """
        try:
            await self.ensure_indexes()

            # Store a user-level revocation marker
            revocation_marker = {
                "user_id": user_id,
                "revoked_at": datetime.utcnow(),
                "reason": reason or "logout_all",
                "expires_at": datetime.utcnow()
                + timedelta(days=30),  # Keep for 30 days
            }

            # Use upsert to update or create marker
            await self.collection.update_one(
                {"user_id": user_id, "type": "user_revocation"},
                {
                    "$set": revocation_marker,
                    "$setOnInsert": {"type": "user_revocation"},
                },
                upsert=True,
            )

            logger.info(f"All tokens revoked for user {user_id} (reason: {reason})")
            # Return approximate count (we don't track exact count)
            return 1
        except (
            OperationFailure,
            ConnectionFailure,
            ServerSelectionTimeoutError,
            ValueError,
            TypeError,
        ) as e:
            logger.error(
                f"Error revoking all tokens for user {user_id}: {e}", exc_info=True
            )
            return 0

    async def is_user_revoked(self, user_id: str) -> bool:
        """
        Check if all tokens for a user have been revoked.

        Args:
            user_id: User ID to check

        Returns:
            True if user has all tokens revoked, False otherwise
        """
        try:
            await self.ensure_indexes()

            marker = await self.collection.find_one(
                {"user_id": user_id, "type": "user_revocation"}
            )

            if marker:
                # Check if marker hasn't expired
                expires_at = marker.get("expires_at")
                if expires_at and isinstance(expires_at, datetime):
                    if datetime.utcnow() < expires_at:
                        return True
                elif expires_at is None:
                    return True

            return False
        except (
            OperationFailure,
            ConnectionFailure,
            ServerSelectionTimeoutError,
            ValueError,
            TypeError,
        ) as e:
            logger.error(
                f"Error checking user revocation status for {user_id}: {e}",
                exc_info=True,
            )
            return False

    async def clear_expired(self) -> int:
        """
        Manually clear expired blacklist entries.

        Note: TTL index should handle this automatically, but this method
        can be used for manual cleanup or verification.

        Returns:
            Number of entries deleted
        """
        try:
            result = await self.collection.delete_many(
                {"expires_at": {"$lt": datetime.utcnow()}}
            )
            deleted_count = result.deleted_count
            if deleted_count > 0:
                logger.info(f"Cleared {deleted_count} expired blacklist entries")
            return deleted_count
        except (
            OperationFailure,
            ConnectionFailure,
            ServerSelectionTimeoutError,
            ValueError,
            TypeError,
        ) as e:
            logger.error(
                f"Error clearing expired blacklist entries: {e}", exc_info=True
            )
            return 0
