"""
Unit tests for SharedUserPool.

Tests the shared user pool functionality for multi-app SSO,
including security features like JWT secret validation, JTI,
token revocation, and secure cookies.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestJWTSecretValidation:
    """Tests for JWT secret validation (fail-fast security)."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database."""
        db = MagicMock()
        db.__getitem__ = MagicMock(return_value=MagicMock())
        return db

    def test_requires_jwt_secret_in_production(self, mock_db):
        """Test that missing JWT secret raises error."""
        from mdb_engine.auth.shared_users import JWTSecretError, SharedUserPool

        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(JWTSecretError, match="JWT secret required"):
                SharedUserPool(mongo_db=mock_db)

    def test_allows_insecure_dev_mode(self, mock_db):
        """Test that allow_insecure_dev=True generates secret."""
        from mdb_engine.auth.shared_users import SharedUserPool

        with patch.dict("os.environ", {}, clear=True):
            pool = SharedUserPool(mongo_db=mock_db, allow_insecure_dev=True)
            assert pool._jwt_secret is not None
            assert len(pool._jwt_secret) > 20

    def test_uses_env_variable(self, mock_db):
        """Test that MDB_ENGINE_JWT_SECRET env var is used."""
        from mdb_engine.auth.shared_users import SharedUserPool

        with patch.dict("os.environ", {"MDB_ENGINE_JWT_SECRET": "env-secret"}):
            pool = SharedUserPool(mongo_db=mock_db)
            assert pool._jwt_secret == "env-secret"

    def test_explicit_secret_overrides_env(self, mock_db):
        """Test that explicit jwt_secret parameter overrides env."""
        from mdb_engine.auth.shared_users import SharedUserPool

        with patch.dict("os.environ", {"MDB_ENGINE_JWT_SECRET": "env-secret"}):
            pool = SharedUserPool(mongo_db=mock_db, jwt_secret="explicit-secret")
            assert pool._jwt_secret == "explicit-secret"


class TestTokenJTI:
    """Tests for JWT ID (JTI) functionality."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database."""
        db = MagicMock()
        db.__getitem__ = MagicMock(return_value=MagicMock())
        return db

    @pytest.fixture
    def user_pool(self, mock_db):
        """Create a SharedUserPool instance."""
        from mdb_engine.auth.shared_users import SharedUserPool

        return SharedUserPool(
            mongo_db=mock_db,
            jwt_secret="test-secret-key",
        )

    def test_token_contains_jti(self, user_pool):
        """Test that generated tokens contain JTI claim."""
        import jwt

        user = {"_id": "user123", "email": "test@example.com"}
        token = user_pool._generate_token(user)

        payload = jwt.decode(token, user_pool._jwt_secret, algorithms=["HS256"])

        assert "jti" in payload
        assert len(payload["jti"]) > 10  # JTI should be a random string

    def test_tokens_have_unique_jti(self, user_pool):
        """Test that each token has a unique JTI."""
        import jwt

        user = {"_id": "user123", "email": "test@example.com"}

        token1 = user_pool._generate_token(user)
        token2 = user_pool._generate_token(user)

        payload1 = jwt.decode(token1, user_pool._jwt_secret, algorithms=["HS256"])
        payload2 = jwt.decode(token2, user_pool._jwt_secret, algorithms=["HS256"])

        assert payload1["jti"] != payload2["jti"]


class TestTokenRevocation:
    """Tests for token revocation functionality."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database."""
        db = MagicMock()
        mock_collection = AsyncMock()
        mock_blacklist = AsyncMock()
        mock_blacklist.find_one = AsyncMock(return_value=None)
        mock_blacklist.update_one = AsyncMock()

        def get_collection(name):
            if "blacklist" in name:
                return mock_blacklist
            return mock_collection

        db.__getitem__ = MagicMock(side_effect=get_collection)
        return db

    @pytest.fixture
    def user_pool(self, mock_db):
        """Create a SharedUserPool instance."""
        from mdb_engine.auth.shared_users import SharedUserPool

        pool = SharedUserPool(
            mongo_db=mock_db,
            jwt_secret="test-secret-key",
        )
        pool._blacklist_indexes_created = True
        return pool

    @pytest.mark.asyncio
    async def test_revoke_token_success(self, user_pool):
        """Test successful token revocation."""

        # Generate a valid token
        user = {"_id": "user123", "email": "test@example.com"}
        token = user_pool._generate_token(user)

        user_pool._blacklist_collection.update_one = AsyncMock()

        result = await user_pool.revoke_token(token, reason="logout")

        assert result is True
        user_pool._blacklist_collection.update_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_revoke_invalid_token_fails(self, user_pool):
        """Test that revoking invalid token fails gracefully."""
        result = await user_pool.revoke_token("invalid-token", reason="logout")
        assert result is False

    @pytest.mark.asyncio
    async def test_validate_revoked_token_fails(self, user_pool):
        """Test that revoked tokens are rejected during validation."""
        import jwt
        from bson import ObjectId

        user_id = str(ObjectId())
        token = jwt.encode(
            {
                "sub": user_id,
                "email": "test@example.com",
                "jti": "test-jti-123",
                "exp": datetime.utcnow() + timedelta(hours=24),
            },
            user_pool._jwt_secret,
            algorithm="HS256",
        )

        # Mock blacklist check to return that token is revoked
        user_pool._blacklist_collection.find_one = AsyncMock(
            return_value={
                "jti": "test-jti-123",
                "expires_at": datetime.utcnow() + timedelta(hours=24),
            }
        )

        user = await user_pool.validate_token(token)

        assert user is None  # Should fail due to revocation

    @pytest.mark.asyncio
    async def test_validate_non_revoked_token_succeeds(self, user_pool):
        """Test that non-revoked tokens are accepted."""
        import jwt
        from bson import ObjectId

        user_id = str(ObjectId())
        token = jwt.encode(
            {
                "sub": user_id,
                "email": "test@example.com",
                "jti": "test-jti-456",
                "exp": datetime.utcnow() + timedelta(hours=24),
            },
            user_pool._jwt_secret,
            algorithm="HS256",
        )

        # Mock blacklist check to return None (not revoked)
        user_pool._blacklist_collection.find_one = AsyncMock(return_value=None)

        # Mock user lookup
        user_pool._collection.find_one = AsyncMock(
            return_value={
                "_id": ObjectId(user_id),
                "email": "test@example.com",
                "is_active": True,
                "app_roles": {},
            }
        )

        user = await user_pool.validate_token(token)

        assert user is not None
        assert user["email"] == "test@example.com"


class TestSecureCookies:
    """Tests for secure cookie configuration."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database."""
        db = MagicMock()
        db.__getitem__ = MagicMock(return_value=MagicMock())
        return db

    @pytest.fixture
    def user_pool(self, mock_db):
        """Create a SharedUserPool instance."""
        from mdb_engine.auth.shared_users import SharedUserPool

        return SharedUserPool(
            mongo_db=mock_db,
            jwt_secret="test-secret-key",
            token_expiry_hours=12,
        )

    def test_get_secure_cookie_config(self, user_pool):
        """Test getting secure cookie configuration."""
        mock_request = MagicMock()
        mock_request.url.scheme = "https"

        with patch("mdb_engine.auth.cookie_utils.get_secure_cookie_settings") as mock_settings:
            mock_settings.return_value = {
                "httponly": True,
                "secure": True,
                "samesite": "strict",
            }

            config = user_pool.get_secure_cookie_config(mock_request)

            assert config["key"] == "mdb_auth_token"
            assert config["max_age"] == 12 * 3600  # 12 hours in seconds
            assert config["httponly"] is True
            assert config["secure"] is True

    def test_token_expiry_property(self, user_pool):
        """Test token_expiry_hours property."""
        assert user_pool.token_expiry_hours == 12


class TestSharedUserPool:
    """Tests for SharedUserPool class."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database."""
        db = MagicMock()
        db.__getitem__ = MagicMock(return_value=MagicMock())
        return db

    @pytest.fixture
    def user_pool(self, mock_db):
        """Create a SharedUserPool instance for testing."""
        from mdb_engine.auth.shared_users import SharedUserPool

        pool = SharedUserPool(
            mongo_db=mock_db,
            jwt_secret="test-secret-key",
            token_expiry_hours=24,
        )
        # Set up blacklist collection mock
        pool._blacklist_collection = AsyncMock()
        pool._blacklist_collection.find_one = AsyncMock(return_value=None)
        return pool

    def test_init(self, user_pool):
        """Test SharedUserPool initialization."""
        assert user_pool._jwt_secret == "test-secret-key"
        assert user_pool._token_expiry_hours == 24
        assert user_pool._jwt_algorithm == "HS256"

    def test_init_requires_secret_or_insecure_dev(self, mock_db):
        """Test that secret is required unless allow_insecure_dev=True."""
        from mdb_engine.auth.shared_users import JWTSecretError, SharedUserPool

        with patch.dict("os.environ", {}, clear=True):
            # Should raise without secret or allow_insecure_dev
            with pytest.raises(JWTSecretError):
                SharedUserPool(mongo_db=mock_db)

            # Should work with allow_insecure_dev=True
            pool = SharedUserPool(mongo_db=mock_db, allow_insecure_dev=True)
            assert pool._jwt_secret is not None
            assert len(pool._jwt_secret) > 20

    @pytest.mark.asyncio
    async def test_ensure_indexes(self, user_pool):
        """Test that indexes are created for users and blacklist."""
        user_pool._collection.create_index = AsyncMock()
        user_pool._blacklist_collection.create_index = AsyncMock()

        await user_pool.ensure_indexes()

        # Should create user indexes
        assert user_pool._collection.create_index.call_count == 2
        # Should create blacklist indexes
        assert user_pool._blacklist_collection.create_index.call_count == 2

    @pytest.mark.asyncio
    async def test_create_user_success(self, user_pool):
        """Test successful user creation."""
        user_pool._collection.find_one = AsyncMock(return_value=None)
        user_pool._collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id="user123"))

        user = await user_pool.create_user(
            email="test@example.com",
            password="secure_password",
            app_roles={"my_app": ["viewer"]},
        )

        assert user["email"] == "test@example.com"
        assert user["app_roles"] == {"my_app": ["viewer"]}
        assert "password_hash" not in user  # Sanitized
        assert user["is_active"] is True

    @pytest.mark.asyncio
    async def test_create_user_duplicate_email(self, user_pool):
        """Test that duplicate email raises ValueError."""
        user_pool._collection.find_one = AsyncMock(return_value={"email": "existing@example.com"})

        with pytest.raises(ValueError, match="already exists"):
            await user_pool.create_user(
                email="existing@example.com",
                password="password",
            )

    @pytest.mark.asyncio
    async def test_authenticate_success(self, user_pool):
        """Test successful authentication."""
        import bcrypt

        password_hash = bcrypt.hashpw(b"correct_password", bcrypt.gensalt()).decode("utf-8")

        user_pool._collection.find_one = AsyncMock(
            return_value={
                "_id": "user123",
                "email": "test@example.com",
                "password_hash": password_hash,
                "is_active": True,
                "app_roles": {},
            }
        )
        user_pool._collection.update_one = AsyncMock()

        token = await user_pool.authenticate(
            email="test@example.com",
            password="correct_password",
        )

        assert token is not None
        # Update last_login should be called
        user_pool._collection.update_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_authenticate_wrong_password(self, user_pool):
        """Test authentication with wrong password."""
        import bcrypt

        password_hash = bcrypt.hashpw(b"correct_password", bcrypt.gensalt()).decode("utf-8")

        user_pool._collection.find_one = AsyncMock(
            return_value={
                "_id": "user123",
                "email": "test@example.com",
                "password_hash": password_hash,
                "is_active": True,
            }
        )

        token = await user_pool.authenticate(
            email="test@example.com",
            password="wrong_password",
        )

        assert token is None

    @pytest.mark.asyncio
    async def test_authenticate_user_not_found(self, user_pool):
        """Test authentication when user not found."""
        user_pool._collection.find_one = AsyncMock(return_value=None)

        token = await user_pool.authenticate(
            email="nonexistent@example.com",
            password="password",
        )

        assert token is None

    @pytest.mark.asyncio
    async def test_validate_token_success(self, user_pool):
        """Test successful token validation."""
        import jwt
        from bson import ObjectId

        # Generate a valid token
        user_id = str(ObjectId())
        token = jwt.encode(
            {
                "sub": user_id,
                "email": "test@example.com",
                "exp": datetime.utcnow() + timedelta(hours=24),
            },
            user_pool._jwt_secret,
            algorithm="HS256",
        )

        user_pool._collection.find_one = AsyncMock(
            return_value={
                "_id": ObjectId(user_id),
                "email": "test@example.com",
                "is_active": True,
                "app_roles": {"my_app": ["viewer"]},
            }
        )

        user = await user_pool.validate_token(token)

        assert user is not None
        assert user["email"] == "test@example.com"
        assert "password_hash" not in user

    @pytest.mark.asyncio
    async def test_validate_token_expired(self, user_pool):
        """Test validation of expired token."""
        import jwt

        # Generate an expired token
        token = jwt.encode(
            {
                "sub": "user123",
                "email": "test@example.com",
                "exp": datetime.utcnow() - timedelta(hours=1),
            },
            user_pool._jwt_secret,
            algorithm="HS256",
        )

        user = await user_pool.validate_token(token)

        assert user is None

    @pytest.mark.asyncio
    async def test_validate_token_invalid(self, user_pool):
        """Test validation of invalid token."""
        user = await user_pool.validate_token("invalid-token")
        assert user is None

    @pytest.mark.asyncio
    async def test_update_user_roles(self, user_pool):
        """Test updating user roles."""
        user_pool._collection.update_one = AsyncMock(return_value=MagicMock(modified_count=1))

        result = await user_pool.update_user_roles(
            email="test@example.com",
            app_slug="my_app",
            roles=["editor", "admin"],
        )

        assert result is True
        user_pool._collection.update_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_user_roles_not_found(self, user_pool):
        """Test updating roles for non-existent user."""
        user_pool._collection.update_one = AsyncMock(return_value=MagicMock(modified_count=0))

        result = await user_pool.update_user_roles(
            email="nonexistent@example.com",
            app_slug="my_app",
            roles=["viewer"],
        )

        assert result is False

    def test_user_has_role_direct(self, user_pool):
        """Test checking if user has role directly."""
        user = {
            "email": "test@example.com",
            "app_roles": {
                "my_app": ["viewer", "editor"],
            },
        }

        assert user_pool.user_has_role(user, "my_app", "viewer") is True
        assert user_pool.user_has_role(user, "my_app", "editor") is True
        assert user_pool.user_has_role(user, "my_app", "admin") is False
        assert user_pool.user_has_role(user, "other_app", "viewer") is False

    def test_user_has_role_with_hierarchy(self, user_pool):
        """Test checking roles with hierarchy."""
        user = {
            "email": "test@example.com",
            "app_roles": {
                "my_app": ["admin"],
            },
        }

        role_hierarchy = {
            "admin": ["editor", "viewer"],
            "editor": ["viewer"],
        }

        # Admin inherits editor and viewer
        assert user_pool.user_has_role(user, "my_app", "admin", role_hierarchy) is True
        assert user_pool.user_has_role(user, "my_app", "editor", role_hierarchy) is True
        assert user_pool.user_has_role(user, "my_app", "viewer", role_hierarchy) is True

    def test_get_user_roles_for_app(self, user_pool):
        """Test getting user roles for an app."""
        user = {
            "email": "test@example.com",
            "app_roles": {
                "my_app": ["viewer", "editor"],
                "other_app": ["admin"],
            },
        }

        roles = user_pool.get_user_roles_for_app(user, "my_app")
        assert roles == ["viewer", "editor"]

        roles = user_pool.get_user_roles_for_app(user, "unknown_app")
        assert roles == []

    @pytest.mark.asyncio
    async def test_deactivate_user(self, user_pool):
        """Test deactivating a user."""
        user_pool._collection.update_one = AsyncMock(return_value=MagicMock(modified_count=1))

        result = await user_pool.deactivate_user("test@example.com")

        assert result is True

    @pytest.mark.asyncio
    async def test_activate_user(self, user_pool):
        """Test activating a user."""
        user_pool._collection.update_one = AsyncMock(return_value=MagicMock(modified_count=1))

        result = await user_pool.activate_user("test@example.com")

        assert result is True
