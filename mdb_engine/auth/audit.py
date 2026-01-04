"""
Authentication Audit Logging

Structured audit logging for authentication events. Provides forensics,
compliance support, and security monitoring capabilities.

This module is part of MDB_ENGINE - MongoDB Engine.

Features:
    - Structured event logging to MongoDB
    - TTL-based automatic cleanup
    - Query helpers for security analysis
    - Integration with shared auth middleware

Usage:
    # Initialize audit logger
    audit = AuthAuditLog(mongo_db)

    # Log authentication events
    await audit.log_event(
        action=AuthAction.LOGIN_SUCCESS,
        user_email="user@example.com",
        success=True,
        ip_address="192.168.1.1",
        user_agent="Mozilla/5.0...",
    )

    # Query for security analysis
    failed_logins = await audit.get_failed_logins(
        email="user@example.com",
        hours=24,
    )
"""

import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import OperationFailure

logger = logging.getLogger(__name__)

# Collection name for audit logs
AUDIT_COLLECTION = "_mdb_engine_auth_audit"

# Default retention period (90 days)
DEFAULT_RETENTION_DAYS = 90


class AuthAction(str, Enum):
    """Authentication action types for audit logging."""

    # Authentication events
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    REGISTER = "register"

    # Token events
    TOKEN_REFRESHED = "token_refreshed"
    TOKEN_REVOKED = "token_revoked"
    TOKEN_EXPIRED = "token_expired"

    # Account events
    PASSWORD_CHANGED = "password_changed"
    PASSWORD_RESET_REQUESTED = "password_reset_requested"
    PASSWORD_RESET_COMPLETED = "password_reset_completed"

    # Role/permission events
    ROLE_GRANTED = "role_granted"
    ROLE_REVOKED = "role_revoked"

    # Security events
    ACCOUNT_LOCKED = "account_locked"
    ACCOUNT_UNLOCKED = "account_unlocked"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"


class AuthAuditLog:
    """
    Manages structured audit logging for authentication events.

    Logs are stored in MongoDB with TTL index for automatic cleanup.
    Provides query helpers for security analysis and compliance.

    Schema for audit documents:
        {
            "_id": ObjectId,
            "timestamp": datetime,
            "action": str (AuthAction value),
            "success": bool,
            "user_email": str | None,
            "user_id": str | None,
            "app_slug": str | None,
            "ip_address": str | None,
            "user_agent": str | None,
            "details": dict | None,
            "expires_at": datetime (for TTL)
        }
    """

    def __init__(
        self,
        mongo_db: AsyncIOMotorDatabase,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ):
        """
        Initialize the audit logger.

        Args:
            mongo_db: MongoDB database instance
            retention_days: How long to keep audit logs (default: 90 days)
        """
        self._db = mongo_db
        self._collection = mongo_db[AUDIT_COLLECTION]
        self._retention_days = retention_days
        self._indexes_created = False

        logger.info(f"AuthAuditLog initialized (retention: {retention_days} days)")

    async def ensure_indexes(self) -> None:
        """Create necessary indexes for the audit collection."""
        if self._indexes_created:
            return

        try:
            # Index for querying by user
            await self._collection.create_index(
                [("user_email", 1), ("timestamp", -1)], name="user_email_timestamp_idx"
            )

            # Index for querying by action type
            await self._collection.create_index(
                [("action", 1), ("timestamp", -1)], name="action_timestamp_idx"
            )

            # Index for querying by IP address
            await self._collection.create_index(
                [("ip_address", 1), ("timestamp", -1)], name="ip_timestamp_idx"
            )

            # Index for querying by app
            await self._collection.create_index(
                [("app_slug", 1), ("timestamp", -1)], name="app_timestamp_idx"
            )

            # TTL index for automatic cleanup
            await self._collection.create_index(
                "expires_at", expireAfterSeconds=0, name="expires_at_ttl_idx"
            )

            self._indexes_created = True
            logger.info("AuthAuditLog indexes ensured")
        except OperationFailure as e:
            logger.warning(f"Failed to create audit indexes: {e}")

    async def log_event(
        self,
        action: AuthAction,
        success: bool,
        user_email: Optional[str] = None,
        user_id: Optional[str] = None,
        app_slug: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Log an authentication event.

        Args:
            action: Type of authentication action
            success: Whether the action succeeded
            user_email: User's email address
            user_id: User's ID
            app_slug: App where the action occurred
            ip_address: Client IP address
            user_agent: Client user agent string
            details: Additional details (e.g., failure reason)

        Returns:
            Inserted document ID as string
        """
        await self.ensure_indexes()

        now = datetime.utcnow()
        expires_at = now + timedelta(days=self._retention_days)

        doc = {
            "timestamp": now,
            "action": action.value if isinstance(action, AuthAction) else action,
            "success": success,
            "user_email": user_email,
            "user_id": user_id,
            "app_slug": app_slug,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "details": details,
            "expires_at": expires_at,
        }

        result = await self._collection.insert_one(doc)

        # Log to application logger as well
        log_level = logging.INFO if success else logging.WARNING
        logger.log(
            log_level,
            f"AUTH_AUDIT: {action.value if isinstance(action, AuthAction) else action} "
            f"success={success} email={user_email} ip={ip_address} app={app_slug}",
        )

        return str(result.inserted_id)

    async def log_login_success(
        self,
        email: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        app_slug: Optional[str] = None,
    ) -> str:
        """Convenience method to log successful login."""
        return await self.log_event(
            action=AuthAction.LOGIN_SUCCESS,
            success=True,
            user_email=email,
            ip_address=ip_address,
            user_agent=user_agent,
            app_slug=app_slug,
        )

    async def log_login_failed(
        self,
        email: str,
        reason: str = "invalid_credentials",
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        app_slug: Optional[str] = None,
    ) -> str:
        """Convenience method to log failed login."""
        return await self.log_event(
            action=AuthAction.LOGIN_FAILED,
            success=False,
            user_email=email,
            ip_address=ip_address,
            user_agent=user_agent,
            app_slug=app_slug,
            details={"reason": reason},
        )

    async def log_logout(
        self,
        email: str,
        ip_address: Optional[str] = None,
        app_slug: Optional[str] = None,
    ) -> str:
        """Convenience method to log logout."""
        return await self.log_event(
            action=AuthAction.LOGOUT,
            success=True,
            user_email=email,
            ip_address=ip_address,
            app_slug=app_slug,
        )

    async def log_register(
        self,
        email: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        app_slug: Optional[str] = None,
    ) -> str:
        """Convenience method to log new user registration."""
        return await self.log_event(
            action=AuthAction.REGISTER,
            success=True,
            user_email=email,
            ip_address=ip_address,
            user_agent=user_agent,
            app_slug=app_slug,
        )

    async def log_role_change(
        self,
        email: str,
        app_slug: str,
        old_roles: List[str],
        new_roles: List[str],
        changed_by: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> str:
        """Log a role change event."""
        action = (
            AuthAction.ROLE_GRANTED if len(new_roles) > len(old_roles) else AuthAction.ROLE_REVOKED
        )
        return await self.log_event(
            action=action,
            success=True,
            user_email=email,
            app_slug=app_slug,
            ip_address=ip_address,
            details={
                "old_roles": old_roles,
                "new_roles": new_roles,
                "changed_by": changed_by,
            },
        )

    async def log_token_revoked(
        self,
        email: str,
        reason: str = "logout",
        ip_address: Optional[str] = None,
        app_slug: Optional[str] = None,
    ) -> str:
        """Log token revocation."""
        return await self.log_event(
            action=AuthAction.TOKEN_REVOKED,
            success=True,
            user_email=email,
            ip_address=ip_address,
            app_slug=app_slug,
            details={"reason": reason},
        )

    async def log_rate_limit_exceeded(
        self,
        ip_address: str,
        endpoint: str,
        email: Optional[str] = None,
        app_slug: Optional[str] = None,
    ) -> str:
        """Log rate limit exceeded event."""
        return await self.log_event(
            action=AuthAction.RATE_LIMIT_EXCEEDED,
            success=False,
            user_email=email,
            ip_address=ip_address,
            app_slug=app_slug,
            details={"endpoint": endpoint},
        )

    # ==========================================================================
    # Query Methods for Security Analysis
    # ==========================================================================

    async def get_recent_events(
        self,
        hours: int = 24,
        action: Optional[AuthAction] = None,
        success: Optional[bool] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Get recent audit events.

        Args:
            hours: How many hours back to search
            action: Filter by action type
            success: Filter by success status
            limit: Maximum results to return

        Returns:
            List of audit event documents
        """
        since = datetime.utcnow() - timedelta(hours=hours)

        query: Dict[str, Any] = {"timestamp": {"$gte": since}}
        if action:
            query["action"] = action.value if isinstance(action, AuthAction) else action
        if success is not None:
            query["success"] = success

        cursor = self._collection.find(query).sort("timestamp", -1).limit(limit)
        events = await cursor.to_list(length=limit)

        # Convert ObjectIds to strings
        for event in events:
            event["_id"] = str(event["_id"])

        return events

    async def get_failed_logins(
        self,
        email: Optional[str] = None,
        ip_address: Optional[str] = None,
        hours: int = 24,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Get failed login attempts.

        Args:
            email: Filter by email
            ip_address: Filter by IP address
            hours: How many hours back to search
            limit: Maximum results

        Returns:
            List of failed login events
        """
        since = datetime.utcnow() - timedelta(hours=hours)

        query: Dict[str, Any] = {
            "action": AuthAction.LOGIN_FAILED.value,
            "timestamp": {"$gte": since},
        }
        if email:
            query["user_email"] = email
        if ip_address:
            query["ip_address"] = ip_address

        cursor = self._collection.find(query).sort("timestamp", -1).limit(limit)
        events = await cursor.to_list(length=limit)

        for event in events:
            event["_id"] = str(event["_id"])

        return events

    async def get_user_activity(
        self,
        email: str,
        hours: int = 168,  # 7 days
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Get all activity for a specific user.

        Args:
            email: User email
            hours: How many hours back to search
            limit: Maximum results

        Returns:
            List of user's audit events
        """
        since = datetime.utcnow() - timedelta(hours=hours)

        cursor = (
            self._collection.find(
                {
                    "user_email": email,
                    "timestamp": {"$gte": since},
                }
            )
            .sort("timestamp", -1)
            .limit(limit)
        )

        events = await cursor.to_list(length=limit)

        for event in events:
            event["_id"] = str(event["_id"])

        return events

    async def get_ip_activity(
        self,
        ip_address: str,
        hours: int = 24,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Get all activity from a specific IP address.

        Useful for investigating suspicious activity.

        Args:
            ip_address: IP address to search
            hours: How many hours back
            limit: Maximum results

        Returns:
            List of audit events from that IP
        """
        since = datetime.utcnow() - timedelta(hours=hours)

        cursor = (
            self._collection.find(
                {
                    "ip_address": ip_address,
                    "timestamp": {"$gte": since},
                }
            )
            .sort("timestamp", -1)
            .limit(limit)
        )

        events = await cursor.to_list(length=limit)

        for event in events:
            event["_id"] = str(event["_id"])

        return events

    async def count_failed_logins(
        self,
        email: Optional[str] = None,
        ip_address: Optional[str] = None,
        hours: int = 1,
    ) -> int:
        """
        Count failed login attempts in a time window.

        Useful for detecting brute-force attacks.

        Args:
            email: Filter by email
            ip_address: Filter by IP
            hours: Time window

        Returns:
            Number of failed login attempts
        """
        since = datetime.utcnow() - timedelta(hours=hours)

        query: Dict[str, Any] = {
            "action": AuthAction.LOGIN_FAILED.value,
            "timestamp": {"$gte": since},
        }
        if email:
            query["user_email"] = email
        if ip_address:
            query["ip_address"] = ip_address

        return await self._collection.count_documents(query)

    async def get_security_summary(
        self,
        hours: int = 24,
    ) -> Dict[str, Any]:
        """
        Get security summary statistics.

        Args:
            hours: Time window for statistics

        Returns:
            Dict with security metrics
        """
        since = datetime.utcnow() - timedelta(hours=hours)

        # Use aggregation for efficient counting
        pipeline = [
            {"$match": {"timestamp": {"$gte": since}}},
            {
                "$group": {
                    "_id": {"action": "$action", "success": "$success"},
                    "count": {"$sum": 1},
                }
            },
        ]

        cursor = self._collection.aggregate(pipeline)
        results = await cursor.to_list(length=100)

        # Build summary
        summary = {
            "period_hours": hours,
            "total_events": 0,
            "login_success": 0,
            "login_failed": 0,
            "registrations": 0,
            "logouts": 0,
            "tokens_revoked": 0,
            "rate_limits_exceeded": 0,
        }

        for result in results:
            action = result["_id"]["action"]
            _success = result["_id"]["success"]  # noqa: F841 - extracted for potential future use
            count = result["count"]

            summary["total_events"] += count

            if action == AuthAction.LOGIN_SUCCESS.value:
                summary["login_success"] = count
            elif action == AuthAction.LOGIN_FAILED.value:
                summary["login_failed"] = count
            elif action == AuthAction.REGISTER.value:
                summary["registrations"] = count
            elif action == AuthAction.LOGOUT.value:
                summary["logouts"] = count
            elif action == AuthAction.TOKEN_REVOKED.value:
                summary["tokens_revoked"] = count
            elif action == AuthAction.RATE_LIMIT_EXCEEDED.value:
                summary["rate_limits_exceeded"] = count

        return summary
