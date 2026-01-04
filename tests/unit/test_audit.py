"""
Unit tests for Auth Audit Logging

Tests the audit logging functionality for authentication events.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from mdb_engine.auth.audit import (
    AUDIT_COLLECTION,
    DEFAULT_RETENTION_DAYS,
    AuthAction,
    AuthAuditLog,
)


class TestAuthAction:
    """Tests for AuthAction enum."""

    def test_login_success_value(self):
        """Test LOGIN_SUCCESS action value."""
        assert AuthAction.LOGIN_SUCCESS.value == "login_success"

    def test_login_failed_value(self):
        """Test LOGIN_FAILED action value."""
        assert AuthAction.LOGIN_FAILED.value == "login_failed"

    def test_logout_value(self):
        """Test LOGOUT action value."""
        assert AuthAction.LOGOUT.value == "logout"

    def test_register_value(self):
        """Test REGISTER action value."""
        assert AuthAction.REGISTER.value == "register"

    def test_token_revoked_value(self):
        """Test TOKEN_REVOKED action value."""
        assert AuthAction.TOKEN_REVOKED.value == "token_revoked"

    def test_role_granted_value(self):
        """Test ROLE_GRANTED action value."""
        assert AuthAction.ROLE_GRANTED.value == "role_granted"

    def test_role_revoked_value(self):
        """Test ROLE_REVOKED action value."""
        assert AuthAction.ROLE_REVOKED.value == "role_revoked"

    def test_rate_limit_exceeded_value(self):
        """Test RATE_LIMIT_EXCEEDED action value."""
        assert AuthAction.RATE_LIMIT_EXCEEDED.value == "rate_limit_exceeded"

    def test_all_actions_are_strings(self):
        """Test that all actions are string enums."""
        for action in AuthAction:
            assert isinstance(action.value, str)


class TestAuthAuditLogInit:
    """Tests for AuthAuditLog initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default retention."""
        mock_db = MagicMock()
        audit_log = AuthAuditLog(mock_db)

        assert audit_log._retention_days == DEFAULT_RETENTION_DAYS
        assert audit_log._indexes_created is False

    def test_init_with_custom_retention(self):
        """Test initialization with custom retention period."""
        mock_db = MagicMock()
        audit_log = AuthAuditLog(mock_db, retention_days=30)

        assert audit_log._retention_days == 30

    def test_collection_name(self):
        """Test that correct collection is used."""
        mock_db = MagicMock()
        audit_log = AuthAuditLog(mock_db)

        mock_db.__getitem__.assert_called_with(AUDIT_COLLECTION)


class TestAuthAuditLogEnsureIndexes:
    """Tests for AuthAuditLog.ensure_indexes method."""

    @pytest.mark.asyncio
    async def test_creates_indexes(self):
        """Test that indexes are created."""
        mock_db = MagicMock()
        mock_collection = AsyncMock()
        mock_db.__getitem__.return_value = mock_collection

        audit_log = AuthAuditLog(mock_db)
        await audit_log.ensure_indexes()

        assert mock_collection.create_index.call_count >= 4  # At least 4 indexes
        assert audit_log._indexes_created is True

    @pytest.mark.asyncio
    async def test_skips_if_already_created(self):
        """Test that indexes are not recreated."""
        mock_db = MagicMock()
        mock_collection = AsyncMock()
        mock_db.__getitem__.return_value = mock_collection

        audit_log = AuthAuditLog(mock_db)
        audit_log._indexes_created = True

        await audit_log.ensure_indexes()

        mock_collection.create_index.assert_not_called()


class TestAuthAuditLogEvents:
    """Tests for AuthAuditLog.log_event method."""

    @pytest.fixture
    def audit_log(self):
        """Create audit log with mocked collection."""
        mock_db = MagicMock()
        mock_collection = AsyncMock()
        mock_collection.insert_one.return_value = MagicMock(inserted_id="test_id")
        mock_db.__getitem__.return_value = mock_collection

        audit = AuthAuditLog(mock_db)
        audit._indexes_created = True  # Skip index creation
        return audit

    @pytest.mark.asyncio
    async def test_log_event_basic(self, audit_log):
        """Test logging a basic event."""
        doc_id = await audit_log.log_event(
            action=AuthAction.LOGIN_SUCCESS,
            success=True,
            user_email="test@example.com",
        )

        assert doc_id == "test_id"
        audit_log._collection.insert_one.assert_called_once()

        call_args = audit_log._collection.insert_one.call_args[0][0]
        assert call_args["action"] == "login_success"
        assert call_args["success"] is True
        assert call_args["user_email"] == "test@example.com"

    @pytest.mark.asyncio
    async def test_log_event_with_details(self, audit_log):
        """Test logging event with all details."""
        doc_id = await audit_log.log_event(
            action=AuthAction.LOGIN_FAILED,
            success=False,
            user_email="test@example.com",
            user_id="user_123",
            app_slug="my_app",
            ip_address="192.168.1.1",
            user_agent="Mozilla/5.0",
            details={"reason": "invalid_password"},
        )

        call_args = audit_log._collection.insert_one.call_args[0][0]
        assert call_args["user_id"] == "user_123"
        assert call_args["app_slug"] == "my_app"
        assert call_args["ip_address"] == "192.168.1.1"
        assert call_args["user_agent"] == "Mozilla/5.0"
        assert call_args["details"]["reason"] == "invalid_password"

    @pytest.mark.asyncio
    async def test_log_event_sets_expiry(self, audit_log):
        """Test that expires_at is set correctly."""
        await audit_log.log_event(
            action=AuthAction.LOGIN_SUCCESS,
            success=True,
        )

        call_args = audit_log._collection.insert_one.call_args[0][0]
        assert "expires_at" in call_args
        assert "timestamp" in call_args

        # Expiry should be ~90 days from now
        expected_expiry = datetime.utcnow() + timedelta(days=90)
        actual_expiry = call_args["expires_at"]
        assert abs((expected_expiry - actual_expiry).total_seconds()) < 5


class TestAuthAuditLogConvenience:
    """Tests for convenience methods."""

    @pytest.fixture
    def audit_log(self):
        """Create audit log with mocked collection."""
        mock_db = MagicMock()
        mock_collection = AsyncMock()
        mock_collection.insert_one.return_value = MagicMock(inserted_id="test_id")
        mock_db.__getitem__.return_value = mock_collection

        audit = AuthAuditLog(mock_db)
        audit._indexes_created = True
        return audit

    @pytest.mark.asyncio
    async def test_log_login_success(self, audit_log):
        """Test log_login_success convenience method."""
        await audit_log.log_login_success(
            email="test@example.com",
            ip_address="192.168.1.1",
            app_slug="my_app",
        )

        call_args = audit_log._collection.insert_one.call_args[0][0]
        assert call_args["action"] == "login_success"
        assert call_args["success"] is True

    @pytest.mark.asyncio
    async def test_log_login_failed(self, audit_log):
        """Test log_login_failed convenience method."""
        await audit_log.log_login_failed(
            email="test@example.com",
            reason="invalid_password",
            ip_address="192.168.1.1",
        )

        call_args = audit_log._collection.insert_one.call_args[0][0]
        assert call_args["action"] == "login_failed"
        assert call_args["success"] is False
        assert call_args["details"]["reason"] == "invalid_password"

    @pytest.mark.asyncio
    async def test_log_logout(self, audit_log):
        """Test log_logout convenience method."""
        await audit_log.log_logout(
            email="test@example.com",
            ip_address="192.168.1.1",
        )

        call_args = audit_log._collection.insert_one.call_args[0][0]
        assert call_args["action"] == "logout"
        assert call_args["success"] is True

    @pytest.mark.asyncio
    async def test_log_register(self, audit_log):
        """Test log_register convenience method."""
        await audit_log.log_register(
            email="new@example.com",
            ip_address="192.168.1.1",
            user_agent="Mozilla/5.0",
        )

        call_args = audit_log._collection.insert_one.call_args[0][0]
        assert call_args["action"] == "register"
        assert call_args["success"] is True

    @pytest.mark.asyncio
    async def test_log_role_change_grant(self, audit_log):
        """Test log_role_change for role grant."""
        await audit_log.log_role_change(
            email="test@example.com",
            app_slug="my_app",
            old_roles=["viewer"],
            new_roles=["viewer", "editor"],
            changed_by="admin@example.com",
        )

        call_args = audit_log._collection.insert_one.call_args[0][0]
        assert call_args["action"] == "role_granted"
        assert call_args["details"]["old_roles"] == ["viewer"]
        assert call_args["details"]["new_roles"] == ["viewer", "editor"]

    @pytest.mark.asyncio
    async def test_log_role_change_revoke(self, audit_log):
        """Test log_role_change for role revoke."""
        await audit_log.log_role_change(
            email="test@example.com",
            app_slug="my_app",
            old_roles=["viewer", "editor"],
            new_roles=["viewer"],
            changed_by="admin@example.com",
        )

        call_args = audit_log._collection.insert_one.call_args[0][0]
        assert call_args["action"] == "role_revoked"

    @pytest.mark.asyncio
    async def test_log_token_revoked(self, audit_log):
        """Test log_token_revoked convenience method."""
        await audit_log.log_token_revoked(
            email="test@example.com",
            reason="logout",
        )

        call_args = audit_log._collection.insert_one.call_args[0][0]
        assert call_args["action"] == "token_revoked"
        assert call_args["details"]["reason"] == "logout"

    @pytest.mark.asyncio
    async def test_log_rate_limit_exceeded(self, audit_log):
        """Test log_rate_limit_exceeded convenience method."""
        await audit_log.log_rate_limit_exceeded(
            ip_address="192.168.1.1",
            endpoint="/login",
            email="test@example.com",
        )

        call_args = audit_log._collection.insert_one.call_args[0][0]
        assert call_args["action"] == "rate_limit_exceeded"
        assert call_args["success"] is False
        assert call_args["details"]["endpoint"] == "/login"


class TestAuthAuditLogQueries:
    """Tests for query methods."""

    @pytest.fixture
    def audit_log(self):
        """Create audit log with mocked collection."""
        mock_db = MagicMock()
        mock_collection = MagicMock()  # Use regular MagicMock for chain methods
        mock_db.__getitem__.return_value = mock_collection

        audit = AuthAuditLog(mock_db)
        audit._indexes_created = True
        return audit

    @pytest.mark.asyncio
    async def test_get_recent_events(self, audit_log):
        """Test get_recent_events query."""
        # Setup chained mock: find().sort().limit() returns async iterable
        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(
            return_value=[
                {"_id": "id1", "action": "login_success"},
                {"_id": "id2", "action": "login_failed"},
            ]
        )

        # Chain the mocks: find() -> sort() -> limit() -> cursor
        mock_limit = MagicMock(return_value=mock_cursor)
        mock_sort = MagicMock(return_value=MagicMock(limit=mock_limit))
        mock_find = MagicMock(return_value=MagicMock(sort=mock_sort))
        audit_log._collection.find = mock_find

        events = await audit_log.get_recent_events(hours=24)

        assert len(events) == 2
        assert events[0]["_id"] == "id1"
        assert events[1]["_id"] == "id2"

    @pytest.mark.asyncio
    async def test_count_failed_logins(self, audit_log):
        """Test count_failed_logins query."""
        audit_log._collection.count_documents = AsyncMock(return_value=5)

        count = await audit_log.count_failed_logins(
            email="test@example.com",
            hours=1,
        )

        assert count == 5
        audit_log._collection.count_documents.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_security_summary(self, audit_log):
        """Test get_security_summary aggregation."""
        # Setup mock cursor for aggregation
        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(
            return_value=[
                {"_id": {"action": "login_success", "success": True}, "count": 100},
                {"_id": {"action": "login_failed", "success": False}, "count": 10},
                {"_id": {"action": "register", "success": True}, "count": 5},
            ]
        )

        audit_log._collection.aggregate = MagicMock(return_value=mock_cursor)

        summary = await audit_log.get_security_summary(hours=24)

        assert summary["login_success"] == 100
        assert summary["login_failed"] == 10
        assert summary["registrations"] == 5
        assert summary["period_hours"] == 24
