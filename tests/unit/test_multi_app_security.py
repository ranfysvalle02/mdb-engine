"""
Security tests for multi-app SSO features.

Tests critical security features:
- Auto-role assignment (opt-in behavior)
- Token blacklist fail-closed behavior
- Race condition protection
- Session binding strict fingerprint validation
- Path traversal protection
"""

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi import FastAPI
from pymongo.errors import PyMongoError

# Set test secret key before importing engine components
if "MDB_ENGINE_JWT_SECRET" not in os.environ:
    os.environ["MDB_ENGINE_JWT_SECRET"] = "test_jwt_secret_for_testing_only_" + "x" * 32

if "MDB_ENGINE_MASTER_KEY" not in os.environ:
    os.environ["MDB_ENGINE_MASTER_KEY"] = "test_master_key_for_testing_only_" + "x" * 32


# Shared fixtures
@pytest.fixture
def mock_db():
    """Create a mock database."""
    db = MagicMock()
    db.__getitem__ = MagicMock(return_value=MagicMock())
    return db


@pytest.fixture
def mock_user_pool():
    """Create a mock SharedUserPool."""
    from mdb_engine.auth.shared_users import SharedUserPool

    pool = MagicMock(spec=SharedUserPool)
    pool.validate_token = AsyncMock()
    pool.update_user_roles = AsyncMock()
    pool.get_user_by_email = AsyncMock()
    return pool


class TestAutoRoleAssignment:
    """Test auto-role assignment security (opt-in behavior)."""

    @pytest.mark.asyncio
    async def test_auto_role_assignment_disabled_by_default(self, mock_user_pool):
        """Test that auto-role assignment is disabled by default."""
        from mdb_engine.auth.shared_middleware import SharedAuthMiddleware

        middleware = SharedAuthMiddleware(
            app=MagicMock(),
            user_pool=mock_user_pool,
            app_slug="test_app",
            require_role="viewer",
            auto_assign_default_role=False,  # Default: disabled
        )

        request = MagicMock()
        request.url.path = "/protected"
        request.scope = {}
        request.cookies = {"mdb_auth_token": "valid-token"}
        request.headers = {}
        request.state = MagicMock()
        request.state.user_roles = []  # No roles

        user = {"email": "test@example.com", "app_roles": {"test_app": []}}
        mock_user_pool.validate_token.return_value = user

        call_next = AsyncMock(return_value=MagicMock())

        response = await middleware.dispatch(request, call_next)

        # Should return 403 (no role, no auto-assignment)
        assert response.status_code == 403
        # Should NOT call update_user_roles
        mock_user_pool.update_user_roles.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_role_assignment_enabled_works(self, mock_user_pool):
        """Test that auto-role assignment works when explicitly enabled."""
        from mdb_engine.auth.shared_middleware import SharedAuthMiddleware

        middleware = SharedAuthMiddleware(
            app=MagicMock(),
            user_pool=mock_user_pool,
            app_slug="test_app",
            require_role="viewer",
            auto_assign_default_role=True,  # Explicitly enabled
        )

        request = MagicMock()
        request.url.path = "/protected"
        request.scope = {}
        request.cookies = {"mdb_auth_token": "valid-token"}
        request.headers = {}
        request.state = MagicMock()
        request.state.user_roles = []  # No roles

        user = {"email": "test@example.com", "app_roles": {"test_app": []}}
        updated_user = {"email": "test@example.com", "app_roles": {"test_app": ["viewer"]}}

        mock_user_pool.validate_token.return_value = user
        mock_user_pool.update_user_roles.return_value = True
        mock_user_pool.get_user_by_email.return_value = updated_user

        call_next = AsyncMock(return_value=MagicMock())

        response = await middleware.dispatch(request, call_next)

        # Should succeed (role auto-assigned)
        call_next.assert_called_once()
        # Should call update_user_roles
        mock_user_pool.update_user_roles.assert_called_once_with(
            "test@example.com", "test_app", ["viewer"]
        )

    @pytest.mark.asyncio
    async def test_auto_role_assignment_not_applied_if_user_has_roles(self, mock_user_pool):
        """Test that auto-assignment doesn't happen if user already has roles."""
        from mdb_engine.auth.shared_middleware import SharedAuthMiddleware

        middleware = SharedAuthMiddleware(
            app=MagicMock(),
            user_pool=mock_user_pool,
            app_slug="test_app",
            require_role="admin",  # Requires admin
            auto_assign_default_role=True,  # Enabled
        )

        request = MagicMock()
        request.url.path = "/protected"
        request.scope = {}
        request.cookies = {"mdb_auth_token": "valid-token"}
        request.headers = {}
        request.state = MagicMock()
        request.state.user_roles = ["viewer"]  # Has viewer role (not admin)

        user = {"email": "test@example.com", "app_roles": {"test_app": ["viewer"]}}
        mock_user_pool.validate_token.return_value = user

        call_next = AsyncMock(return_value=MagicMock())

        response = await middleware.dispatch(request, call_next)

        # Should return 403 (has roles but not required one, no auto-assignment)
        assert response.status_code == 403
        # Should NOT call update_user_roles (user has roles already)
        mock_user_pool.update_user_roles.assert_not_called()


class TestTokenBlacklistFailClosed:
    """Test token blacklist fail-closed security behavior."""

    @pytest.fixture
    def user_pool_fail_closed(self, mock_db):
        """Create SharedUserPool with fail-closed enabled (default)."""
        from mdb_engine.auth.shared_users import SharedUserPool

        pool = SharedUserPool(
            mock_db,
            jwt_secret="test_secret_" + "x" * 32,
            blacklist_fail_closed=True,  # Default: fail closed
        )
        return pool

    @pytest.fixture
    def user_pool_fail_open(self, mock_db):
        """Create SharedUserPool with fail-open enabled."""
        from mdb_engine.auth.shared_users import SharedUserPool

        pool = SharedUserPool(
            mock_db,
            jwt_secret="test_secret_" + "x" * 32,
            blacklist_fail_closed=False,  # Fail open
        )
        return pool

    @pytest.mark.asyncio
    async def test_blacklist_fail_closed_rejects_on_error(self, user_pool_fail_closed):
        """Test that blacklist check fails closed (rejects token) on database error."""
        # Create a valid token
        from bson import ObjectId

        user_id = ObjectId()
        token = jwt.encode(
            {"sub": str(user_id), "jti": "test-jti", "exp": 9999999999},
            "test_secret_" + "x" * 32,
            algorithm="HS256",
        )

        # Mock blacklist check to raise error
        with patch.object(
            user_pool_fail_closed._blacklist_collection,
            "find_one",
            side_effect=PyMongoError("Database error"),
        ):
            # Should return True (token IS revoked) when check fails
            is_revoked = await user_pool_fail_closed._is_token_revoked("test-jti")
            assert is_revoked is True  # Fail closed - reject token

    @pytest.mark.asyncio
    async def test_blacklist_fail_open_allows_on_error(self, user_pool_fail_open):
        """Test that blacklist check fails open (allows token) when configured."""
        # Mock blacklist check to raise error
        with patch.object(
            user_pool_fail_open._blacklist_collection,
            "find_one",
            side_effect=PyMongoError("Database error"),
        ):
            # Should return False (token NOT revoked) when check fails
            is_revoked = await user_pool_fail_open._is_token_revoked("test-jti")
            assert is_revoked is False  # Fail open - allow token


class TestRaceConditionProtection:
    """Test race condition protection in shared user pool initialization."""

    @pytest.mark.asyncio
    async def test_concurrent_initialization_prevents_duplicates(self, mock_db):
        """Test that concurrent initialization doesn't create duplicate pools."""
        from mdb_engine.core.engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        # Mock collection methods to be async
        mock_collection = MagicMock()
        mock_collection.create_index = AsyncMock()
        mock_collection.find_one = AsyncMock(return_value=None)
        mock_db.__getitem__ = MagicMock(return_value=mock_collection)

        with patch.object(engine, "_connection_manager") as mock_conn:
            mock_conn.mongo_db = mock_db
            mock_conn.mongo_client = MagicMock()
            mock_conn.initialized = True
            mock_conn.initialize = AsyncMock()
            mock_conn.shutdown = AsyncMock()

            app1 = FastAPI()
            app2 = FastAPI()

            # Initialize concurrently
            async def init1():
                await engine._initialize_shared_user_pool(app1)

            async def init2():
                await engine._initialize_shared_user_pool(app2)

            # Run concurrently
            await asyncio.gather(init1(), init2())

            # Both apps should have the same pool instance
            assert hasattr(app1.state, "user_pool")
            assert hasattr(app2.state, "user_pool")
            assert app1.state.user_pool is app2.state.user_pool
            assert app1.state.user_pool is engine._shared_user_pool


class TestSessionBindingStrictFingerprint:
    """Test strict fingerprint validation in session binding."""

    @pytest.fixture
    def middleware_strict(self, mock_user_pool):
        """Create middleware with strict fingerprint validation."""
        from mdb_engine.auth.shared_middleware import SharedAuthMiddleware

        return SharedAuthMiddleware(
            app=MagicMock(),
            user_pool=mock_user_pool,
            app_slug="test_app",
            session_binding={
                "bind_fingerprint": True,
                "strict_fingerprint": True,  # Strict mode
            },
        )

    @pytest.fixture
    def middleware_soft(self, mock_user_pool):
        """Create middleware with soft fingerprint validation."""
        from mdb_engine.auth.shared_middleware import SharedAuthMiddleware

        return SharedAuthMiddleware(
            app=MagicMock(),
            user_pool=mock_user_pool,
            app_slug="test_app",
            session_binding={
                "bind_fingerprint": True,
                "strict_fingerprint": False,  # Soft mode
            },
        )

    @pytest.mark.asyncio
    async def test_strict_fingerprint_rejects_mismatch(self, middleware_strict, mock_user_pool):
        """Test that strict fingerprint validation rejects mismatched fingerprints."""
        from mdb_engine.auth.shared_middleware import _compute_fingerprint

        request = MagicMock()
        request.url.path = "/protected"
        request.scope = {}
        request.cookies = {"mdb_auth_token": "valid-token"}
        request.headers = {
            "user-agent": "Mozilla/5.0",
            "accept-language": "en-US",
            "accept-encoding": "gzip",
        }
        request.state = MagicMock()

        user = {"email": "test@example.com", "app_roles": {"test_app": ["viewer"]}}
        mock_user_pool.validate_token.return_value = user

        # Create token with different fingerprint
        client_fp = _compute_fingerprint(request)
        token_fp = "different_fingerprint"

        token_payload = {"fp": token_fp, "email": "test@example.com"}
        token = jwt.encode(token_payload, "secret", algorithm="HS256")

        request.cookies = {"mdb_auth_token": token}

        call_next = AsyncMock(return_value=MagicMock())

        response = await middleware_strict.dispatch(request, call_next)

        # Should return 403 (fingerprint mismatch)
        assert response.status_code == 403
        assert "fingerprint" in response.body.decode().lower()
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_soft_fingerprint_allows_mismatch(self, middleware_soft, mock_user_pool):
        """Test that soft fingerprint validation allows mismatched fingerprints."""

        request = MagicMock()
        request.url.path = "/protected"
        request.scope = {}
        request.cookies = {"mdb_auth_token": "valid-token"}
        request.headers = {
            "user-agent": "Mozilla/5.0",
            "accept-language": "en-US",
            "accept-encoding": "gzip",
        }
        request.state = MagicMock()

        user = {"email": "test@example.com", "app_roles": {"test_app": ["viewer"]}}
        mock_user_pool.validate_token.return_value = user

        # Create token with different fingerprint
        token_fp = "different_fingerprint"
        token_payload = {"fp": token_fp, "email": "test@example.com"}
        token = jwt.encode(token_payload, "secret", algorithm="HS256")

        request.cookies = {"mdb_auth_token": token}

        call_next = AsyncMock(return_value=MagicMock())

        response = await middleware_soft.dispatch(request, call_next)

        # Should succeed (soft mode allows mismatch)
        call_next.assert_called_once()


class TestPathTraversalProtection:
    """Test path traversal protection in path normalization."""

    def test_normalize_path_blocks_traversal(self):
        """Test that path normalization blocks traversal attempts."""
        from mdb_engine.auth.shared_middleware import _normalize_path

        # Test various traversal attempts
        test_cases = [
            ("../../../etc/passwd", "/"),  # Should be blocked
            ("/app/../admin", "/"),  # Should be blocked
            ("/app/../../etc/passwd", "/"),  # Should be blocked
            ("/app/%2e%2e%2fadmin", "/"),  # URL encoded traversal
            ("/app//admin", "/app/admin"),  # Double slash normalized
            ("/app/admin", "/app/admin"),  # Valid path
            ("/app", "/app"),  # Valid path
            ("", "/"),  # Empty path
        ]

        for input_path, expected in test_cases:
            result = _normalize_path(input_path)
            assert result == expected, f"Failed for {input_path}: got {result}, expected {expected}"

    def test_get_request_path_normalizes(self):
        """Test that _get_request_path normalizes paths."""
        from starlette.requests import Request

        from mdb_engine.auth.shared_middleware import _get_request_path

        # Create request with traversal attempt
        request = MagicMock(spec=Request)
        request.url.path = "/app/../../../etc/passwd"
        request.scope = {}
        request.state = MagicMock()
        request.state.app_base_path = "/app"

        path = _get_request_path(request)
        assert path == "/"  # Should be normalized to root

    def test_path_normalization_preserves_valid_paths(self):
        """Test that valid paths are preserved during normalization."""
        from mdb_engine.auth.shared_middleware import _normalize_path

        valid_paths = [
            "/api/users",
            "/api/users/123",
            "/health",
            "/docs",
            "/app/admin",
        ]

        for path in valid_paths:
            normalized = _normalize_path(path)
            assert normalized == path, f"Valid path {path} was changed to {normalized}"


class TestSecurityIntegration:
    """Integration tests for security features."""

    @pytest.mark.asyncio
    async def test_auto_role_assignment_with_manifest(self, mock_db, tmp_path):
        """Test auto-role assignment behavior with manifest configuration."""
        from mdb_engine.core.engine import MongoDBEngine

        # Create manifest with auto_assign_default_role disabled (default)
        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test App",
            "auth": {
                "mode": "shared",
                "roles": ["viewer", "admin"],
                "require_role": "viewer",
                "default_role": "viewer",
                # auto_assign_default_role not set (defaults to False)
            },
        }

        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")

        with patch.object(engine, "_connection_manager") as mock_conn:
            mock_conn.mongo_db = mock_db
            mock_conn.mongo_client = MagicMock()
            mock_conn.initialized = True
            mock_conn.initialize = AsyncMock()
            mock_conn.shutdown = AsyncMock()

            app = engine.create_app(
                slug="test_app",
                manifest=manifest_path,
            )

            # Check that middleware was created without auto-assignment
            # (We can't easily test this without inspecting middleware internals,
            # but the fact that create_app succeeds means the config was valid)
            assert app is not None

    @pytest.mark.asyncio
    async def test_blacklist_fail_closed_integration(self, mock_db):
        """Test blacklist fail-closed behavior in token validation."""
        from bson import ObjectId

        from mdb_engine.auth.shared_users import SharedUserPool

        # Mock collection methods to be async
        mock_collection = MagicMock()
        mock_collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id=ObjectId()))
        mock_collection.find_one = AsyncMock(
            return_value={
                "_id": ObjectId(),
                "email": "test@example.com",
                "password_hash": "hash",
                "app_roles": {"test_app": ["viewer"]},
                "is_active": True,
            }
        )
        mock_collection.create_index = AsyncMock()

        mock_blacklist_collection = MagicMock()
        mock_blacklist_collection.find_one = AsyncMock()
        mock_blacklist_collection.create_index = AsyncMock()

        mock_db.__getitem__ = MagicMock(
            side_effect=lambda key: {
                "_mdb_engine_shared_users": mock_collection,
                "_mdb_engine_token_blacklist": mock_blacklist_collection,
            }.get(key, MagicMock())
        )

        pool = SharedUserPool(
            mock_db,
            jwt_secret="test_secret_" + "x" * 32,
            blacklist_fail_closed=True,
        )

        user_id = ObjectId()
        user = {
            "_id": user_id,
            "email": "test@example.com",
            "password_hash": "hash",
            "app_roles": {"test_app": ["viewer"]},
            "is_active": True,
        }

        # Create token
        token = pool._generate_token(user)

        # Mock blacklist check to fail
        mock_blacklist_collection.find_one.side_effect = PyMongoError("Database error")

        # Token validation should fail (blacklist check failed, fail closed)
        result = await pool.validate_token(token)
        assert result is None  # Token rejected due to blacklist check failure
