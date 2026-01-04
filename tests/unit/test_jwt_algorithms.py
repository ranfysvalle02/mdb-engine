"""
Unit tests for JWT algorithm support.

Tests cover:
- HS256 (symmetric HMAC) - default
- RS256 (RSA asymmetric)
- ES256 (ECDSA asymmetric)
- Key validation and error handling
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mdb_engine.auth.shared_users import (
    ASYMMETRIC_ALGORITHMS,
    SUPPORTED_ALGORITHMS,
    SYMMETRIC_ALGORITHMS,
    JWTKeyError,
    JWTSecretError,
    SharedUserPool,
)


class TestAlgorithmConstants:
    """Tests for algorithm constants."""

    def test_symmetric_algorithms(self):
        """Test symmetric algorithm list."""
        assert "HS256" in SYMMETRIC_ALGORITHMS
        assert "HS384" in SYMMETRIC_ALGORITHMS
        assert "HS512" in SYMMETRIC_ALGORITHMS

    def test_asymmetric_algorithms(self):
        """Test asymmetric algorithm list."""
        assert "RS256" in ASYMMETRIC_ALGORITHMS
        assert "RS384" in ASYMMETRIC_ALGORITHMS
        assert "RS512" in ASYMMETRIC_ALGORITHMS
        assert "ES256" in ASYMMETRIC_ALGORITHMS
        assert "ES384" in ASYMMETRIC_ALGORITHMS
        assert "ES512" in ASYMMETRIC_ALGORITHMS

    def test_all_supported(self):
        """Test all algorithms are in supported set."""
        assert SUPPORTED_ALGORITHMS == SYMMETRIC_ALGORITHMS | ASYMMETRIC_ALGORITHMS


class TestHS256Algorithm:
    """Tests for HS256 (default HMAC) algorithm."""

    @pytest.fixture
    def mock_mongo_db(self):
        """Create mock MongoDB database."""
        db = MagicMock()
        collection = AsyncMock()
        collection.create_index = AsyncMock()
        db.__getitem__ = MagicMock(return_value=collection)
        return db

    def test_hs256_with_secret(self, mock_mongo_db):
        """Test HS256 initialization with secret."""
        pool = SharedUserPool(
            mock_mongo_db,
            jwt_secret="test-secret-key",
            jwt_algorithm="HS256",
        )

        assert pool.jwt_algorithm == "HS256"
        assert pool.is_asymmetric is False

    def test_hs256_without_secret_fails(self, mock_mongo_db):
        """Test HS256 without secret raises error."""
        with pytest.raises(JWTSecretError):
            SharedUserPool(
                mock_mongo_db,
                jwt_algorithm="HS256",
                allow_insecure_dev=False,
            )

    def test_hs256_allow_insecure_dev(self, mock_mongo_db):
        """Test HS256 with allow_insecure_dev generates secret."""
        pool = SharedUserPool(
            mock_mongo_db,
            jwt_algorithm="HS256",
            allow_insecure_dev=True,
        )

        assert pool.jwt_algorithm == "HS256"
        # Should have auto-generated secret
        assert pool._jwt_secret is not None


class TestRS256Algorithm:
    """Tests for RS256 (RSA) algorithm."""

    @pytest.fixture
    def mock_mongo_db(self):
        """Create mock MongoDB database."""
        db = MagicMock()
        collection = AsyncMock()
        collection.create_index = AsyncMock()
        db.__getitem__ = MagicMock(return_value=collection)
        return db

    # Note: We use a minimal RSA key format for testing
    # In real usage, this would be a proper PEM-encoded key
    RSA_PRIVATE_KEY = """-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA0Z3VS5JJcds3xfn/ygWyF8PbnGy0AHB7MxQPpg+WjhvwRqNL
EtYplvPWvyDk+tT3s7VLR9PYT/Zn7h0J8bTO8MBjQn4RKphW8M4QH5k
-----END RSA PRIVATE KEY-----"""

    def test_rs256_without_key_fails(self, mock_mongo_db):
        """Test RS256 without private key raises error."""
        with pytest.raises(JWTKeyError) as exc_info:
            SharedUserPool(
                mock_mongo_db,
                jwt_algorithm="RS256",
                allow_insecure_dev=False,
            )

        assert "Private key required" in str(exc_info.value)

    def test_rs256_allow_insecure_dev_still_fails(self, mock_mongo_db):
        """Test RS256 with allow_insecure_dev still requires key."""
        with pytest.raises(JWTKeyError) as exc_info:
            SharedUserPool(
                mock_mongo_db,
                jwt_algorithm="RS256",
                allow_insecure_dev=True,
            )

        assert "cannot auto-generate" in str(exc_info.value).lower()

    def test_rs256_with_private_key(self, mock_mongo_db):
        """Test RS256 with private key initializes correctly."""
        # Use a dummy key that won't actually work for signing
        # but will pass initialization
        pool = SharedUserPool(
            mock_mongo_db,
            jwt_secret=self.RSA_PRIVATE_KEY,
            jwt_algorithm="RS256",
        )

        assert pool.jwt_algorithm == "RS256"
        assert pool.is_asymmetric is True

    def test_rs256_with_public_key(self, mock_mongo_db):
        """Test RS256 with both private and public keys."""
        public_key = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA
-----END PUBLIC KEY-----"""

        pool = SharedUserPool(
            mock_mongo_db,
            jwt_secret=self.RSA_PRIVATE_KEY,
            jwt_public_key=public_key,
            jwt_algorithm="RS256",
        )

        assert pool.jwt_algorithm == "RS256"
        assert pool.is_asymmetric is True


class TestES256Algorithm:
    """Tests for ES256 (ECDSA) algorithm."""

    @pytest.fixture
    def mock_mongo_db(self):
        """Create mock MongoDB database."""
        db = MagicMock()
        collection = AsyncMock()
        collection.create_index = AsyncMock()
        db.__getitem__ = MagicMock(return_value=collection)
        return db

    EC_PRIVATE_KEY = """-----BEGIN EC PRIVATE KEY-----
MHQCAQEEICBQw0Nl8FpSFPJeqj3FVcLjnw5KjANkG5x+w7s+
-----END EC PRIVATE KEY-----"""

    def test_es256_without_key_fails(self, mock_mongo_db):
        """Test ES256 without private key raises error."""
        with pytest.raises(JWTKeyError):
            SharedUserPool(
                mock_mongo_db,
                jwt_algorithm="ES256",
                allow_insecure_dev=False,
            )

    def test_es256_with_private_key(self, mock_mongo_db):
        """Test ES256 with private key initializes correctly."""
        pool = SharedUserPool(
            mock_mongo_db,
            jwt_secret=self.EC_PRIVATE_KEY,
            jwt_algorithm="ES256",
        )

        assert pool.jwt_algorithm == "ES256"
        assert pool.is_asymmetric is True


class TestUnsupportedAlgorithm:
    """Tests for unsupported algorithms."""

    @pytest.fixture
    def mock_mongo_db(self):
        """Create mock MongoDB database."""
        db = MagicMock()
        collection = AsyncMock()
        collection.create_index = AsyncMock()
        db.__getitem__ = MagicMock(return_value=collection)
        return db

    def test_unsupported_algorithm_fails(self, mock_mongo_db):
        """Test unsupported algorithm raises error."""
        with pytest.raises(JWTKeyError) as exc_info:
            SharedUserPool(
                mock_mongo_db,
                jwt_secret="test-secret",
                jwt_algorithm="INVALID",
            )

        assert "Unsupported" in str(exc_info.value)

    def test_none_algorithm_fails(self, mock_mongo_db):
        """Test 'none' algorithm is not supported (security)."""
        with pytest.raises(JWTKeyError):
            SharedUserPool(
                mock_mongo_db,
                jwt_secret="test-secret",
                jwt_algorithm="none",
            )


class TestTokenGenerationAndValidation:
    """Tests for token generation with different algorithms."""

    @pytest.fixture
    def mock_mongo_db(self):
        """Create mock MongoDB database."""
        db = MagicMock()
        collection = AsyncMock()
        collection.find_one = AsyncMock(return_value=None)
        collection.create_index = AsyncMock()
        db.__getitem__ = MagicMock(return_value=collection)
        return db

    @pytest.mark.asyncio
    async def test_hs256_token_generation(self, mock_mongo_db):
        """Test token generation with HS256."""
        import bcrypt

        password = "TestPassword123"
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

        user_doc = {
            "_id": "user123",
            "email": "test@example.com",
            "password_hash": password_hash,
            "app_roles": {},
            "is_active": True,
        }

        collection = AsyncMock()
        collection.find_one = AsyncMock(return_value=user_doc)
        collection.update_one = AsyncMock()
        collection.create_index = AsyncMock()
        mock_mongo_db.__getitem__ = MagicMock(return_value=collection)

        pool = SharedUserPool(
            mock_mongo_db,
            jwt_secret="test-secret-key-for-hs256",
            jwt_algorithm="HS256",
        )

        token = await pool.authenticate(
            email="test@example.com",
            password=password,
        )

        assert token is not None

        # Verify token can be validated
        import jwt

        payload = jwt.decode(token, "test-secret-key-for-hs256", algorithms=["HS256"])
        assert payload["email"] == "test@example.com"
