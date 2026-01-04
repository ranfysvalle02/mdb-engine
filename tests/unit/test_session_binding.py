"""
Unit tests for session binding features.

Tests cover:
- IP binding in JWT claims
- Fingerprint binding in JWT claims
- Session validation with binding checks
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from mdb_engine.auth.shared_middleware import _compute_fingerprint, _get_client_ip


class TestClientIPExtraction:
    """Tests for client IP extraction."""

    def test_direct_client_ip(self):
        """Test extracting IP from direct client."""
        request = MagicMock()
        request.headers = {}
        request.client = MagicMock()
        request.client.host = "192.168.1.100"

        ip = _get_client_ip(request)
        assert ip == "192.168.1.100"

    def test_x_forwarded_for_single(self):
        """Test extracting IP from X-Forwarded-For (single)."""
        request = MagicMock()
        request.headers = {"x-forwarded-for": "10.0.0.1"}
        request.client = MagicMock()
        request.client.host = "192.168.1.1"

        ip = _get_client_ip(request)
        assert ip == "10.0.0.1"

    def test_x_forwarded_for_chain(self):
        """Test extracting first IP from X-Forwarded-For chain."""
        request = MagicMock()
        request.headers = {"x-forwarded-for": "10.0.0.1, 10.0.0.2, 10.0.0.3"}
        request.client = MagicMock()
        request.client.host = "192.168.1.1"

        ip = _get_client_ip(request)
        assert ip == "10.0.0.1"

    def test_x_real_ip(self):
        """Test extracting IP from X-Real-IP."""
        request = MagicMock()
        request.headers = {"x-real-ip": "10.0.0.5"}
        request.client = MagicMock()
        request.client.host = "192.168.1.1"

        ip = _get_client_ip(request)
        assert ip == "10.0.0.5"

    def test_x_forwarded_for_precedence(self):
        """Test X-Forwarded-For takes precedence over X-Real-IP."""
        request = MagicMock()
        request.headers = {"x-forwarded-for": "10.0.0.1", "x-real-ip": "10.0.0.5"}
        request.client = MagicMock()
        request.client.host = "192.168.1.1"

        ip = _get_client_ip(request)
        assert ip == "10.0.0.1"

    def test_no_client(self):
        """Test handling when client is None."""
        request = MagicMock()
        request.headers = {}
        request.client = None

        ip = _get_client_ip(request)
        assert ip is None


class TestFingerprintComputation:
    """Tests for device fingerprint computation."""

    def test_basic_fingerprint(self):
        """Test basic fingerprint generation."""
        request = MagicMock()
        request.headers = {
            "user-agent": "Mozilla/5.0 Chrome/120",
            "accept-language": "en-US,en;q=0.9",
            "accept-encoding": "gzip, deflate, br",
        }

        fingerprint = _compute_fingerprint(request)
        assert fingerprint is not None
        assert len(fingerprint) == 64  # SHA256 hex digest

    def test_fingerprint_consistency(self):
        """Test that same headers produce same fingerprint."""
        request = MagicMock()
        request.headers = {
            "user-agent": "Mozilla/5.0 Chrome/120",
            "accept-language": "en-US,en;q=0.9",
            "accept-encoding": "gzip, deflate, br",
        }

        fp1 = _compute_fingerprint(request)
        fp2 = _compute_fingerprint(request)
        assert fp1 == fp2

    def test_fingerprint_changes_with_headers(self):
        """Test that different headers produce different fingerprint."""
        request1 = MagicMock()
        request1.headers = {
            "user-agent": "Mozilla/5.0 Chrome/120",
            "accept-language": "en-US",
            "accept-encoding": "gzip",
        }

        request2 = MagicMock()
        request2.headers = {
            "user-agent": "Mozilla/5.0 Firefox/120",
            "accept-language": "en-US",
            "accept-encoding": "gzip",
        }

        fp1 = _compute_fingerprint(request1)
        fp2 = _compute_fingerprint(request2)
        assert fp1 != fp2

    def test_missing_headers(self):
        """Test fingerprint with missing headers."""
        request = MagicMock()
        request.headers = {}

        fingerprint = _compute_fingerprint(request)
        assert fingerprint is not None
        assert len(fingerprint) == 64


class TestSessionBindingConfig:
    """Tests for session binding configuration."""

    def test_default_binding_config(self):
        """Test default session binding configuration."""
        from mdb_engine.auth.shared_middleware import create_shared_auth_middleware_lazy

        manifest_auth = {
            "roles": ["viewer"],
            "public_routes": ["/"],
        }

        middleware_class = create_shared_auth_middleware_lazy(
            app_slug="test_app",
            manifest_auth=manifest_auth,
        )

        assert middleware_class is not None

    def test_custom_binding_config(self):
        """Test custom session binding configuration."""
        from mdb_engine.auth.shared_middleware import create_shared_auth_middleware_lazy

        manifest_auth = {
            "roles": ["viewer"],
            "public_routes": ["/"],
            "session_binding": {
                "bind_ip": True,
                "bind_fingerprint": True,
                "allow_ip_change_with_reauth": False,
            },
        }

        middleware_class = create_shared_auth_middleware_lazy(
            app_slug="test_app",
            manifest_auth=manifest_auth,
        )

        assert middleware_class is not None


class TestSharedUserPoolSessionBinding:
    """Tests for SharedUserPool session binding in authenticate."""

    @pytest.fixture
    def mock_mongo_db(self):
        """Create mock MongoDB database."""
        db = MagicMock()
        collection = AsyncMock()

        # Mock user document
        user_doc = {
            "_id": "user123",
            "email": "test@example.com",
            "password_hash": "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewKyNi",
            "app_roles": {"test_app": ["viewer"]},
            "is_active": True,
            "created_at": datetime.utcnow(),
        }

        collection.find_one = AsyncMock(return_value=user_doc)
        collection.update_one = AsyncMock()
        collection.create_index = AsyncMock()

        db.__getitem__ = MagicMock(return_value=collection)

        return db

    @pytest.mark.asyncio
    async def test_authenticate_with_session_binding(self, mock_mongo_db):
        """Test authentication with session binding claims."""
        import bcrypt

        from mdb_engine.auth.shared_users import SharedUserPool

        # Create proper password hash
        password = "TestPassword123"
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

        user_doc = {
            "_id": "user123",
            "email": "test@example.com",
            "password_hash": password_hash,
            "app_roles": {"test_app": ["viewer"]},
            "is_active": True,
        }

        collection = AsyncMock()
        collection.find_one = AsyncMock(return_value=user_doc)
        collection.update_one = AsyncMock()
        collection.create_index = AsyncMock()
        mock_mongo_db.__getitem__ = MagicMock(return_value=collection)

        pool = SharedUserPool(
            mock_mongo_db,
            jwt_secret="test-secret-key-for-testing",
            allow_insecure_dev=True,
        )

        # Authenticate with binding info
        token = await pool.authenticate(
            email="test@example.com",
            password=password,
            ip_address="192.168.1.100",
            fingerprint="abc123fingerprint",
            session_binding={
                "bind_ip": True,
                "bind_fingerprint": True,
            },
        )

        assert token is not None

        # Decode and verify claims
        import jwt

        payload = jwt.decode(token, options={"verify_signature": False})
        assert payload["ip"] == "192.168.1.100"
        assert payload["fp"] == "abc123fingerprint"

    @pytest.mark.asyncio
    async def test_authenticate_without_session_binding(self, mock_mongo_db):
        """Test authentication without session binding."""
        import bcrypt

        from mdb_engine.auth.shared_users import SharedUserPool

        password = "TestPassword123"
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

        user_doc = {
            "_id": "user123",
            "email": "test@example.com",
            "password_hash": password_hash,
            "app_roles": {"test_app": ["viewer"]},
            "is_active": True,
        }

        collection = AsyncMock()
        collection.find_one = AsyncMock(return_value=user_doc)
        collection.update_one = AsyncMock()
        collection.create_index = AsyncMock()
        mock_mongo_db.__getitem__ = MagicMock(return_value=collection)

        pool = SharedUserPool(
            mock_mongo_db,
            jwt_secret="test-secret-key-for-testing",
            allow_insecure_dev=True,
        )

        # Authenticate without binding
        token = await pool.authenticate(
            email="test@example.com",
            password=password,
        )

        assert token is not None

        # Decode and verify no binding claims
        import jwt

        payload = jwt.decode(token, options={"verify_signature": False})
        assert "ip" not in payload
        assert "fp" not in payload
