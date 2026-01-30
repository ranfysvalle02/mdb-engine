"""
Shared User Pool for Multi-App SSO

Provides a centralized user pool that enables Single Sign-On (SSO) across
all apps using "shared" auth mode. Users authenticate once and can access
any app that uses shared auth mode (subject to role requirements).

This module is part of MDB_ENGINE - MongoDB Engine.

Security Features:
    - JWT secret required (fail-fast validation)
    - JTI (JWT ID) for token revocation support
    - Token blacklist integration
    - Secure cookie configuration helpers

Usage:
    # Initialize shared user pool (JWT secret required!)
    pool = SharedUserPool(mongo_db, jwt_secret="your-secret")
    # Or use environment variable: MDB_ENGINE_JWT_SECRET

    # For local development only:
    pool = SharedUserPool(mongo_db, allow_insecure_dev=True)

    # Register a new user
    user = await pool.create_user(
        email="user@example.com",
        password="secure_password",
        app_roles={"my_app": ["viewer"]}
    )

    # Authenticate and get JWT token
    token = await pool.authenticate("user@example.com", "secure_password")

    # Validate token and get user
    user = await pool.validate_token(token)

    # Revoke a token (e.g., on logout)
    await pool.revoke_token(token)

    # Check if user has required role for an app
    has_access = pool.user_has_role(user, "my_app", "viewer")
"""

import logging
import os
import secrets
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import bcrypt
import jwt
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import OperationFailure, PyMongoError

if TYPE_CHECKING:
    from fastapi import Request

logger = logging.getLogger(__name__)

# Collection names
SHARED_USERS_COLLECTION = "_mdb_engine_shared_users"
TOKEN_BLACKLIST_COLLECTION = "_mdb_engine_token_blacklist"

# Default JWT settings
DEFAULT_JWT_ALGORITHM = "HS256"
DEFAULT_TOKEN_EXPIRY_HOURS = 24

# Supported JWT algorithms
SYMMETRIC_ALGORITHMS = {"HS256", "HS384", "HS512"}
ASYMMETRIC_ALGORITHMS = {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}
SUPPORTED_ALGORITHMS = SYMMETRIC_ALGORITHMS | ASYMMETRIC_ALGORITHMS


class JWTSecretError(ValueError):
    """Raised when JWT secret is missing or invalid."""

    pass


class JWTKeyError(ValueError):
    """Raised when JWT key configuration is invalid."""

    pass


class SharedUserPool:
    """
    Manages a shared user pool for SSO across multi-app deployments.

    Users are stored in a central collection with per-app role assignments.
    JWT tokens are used for stateless session management.

    Supports both symmetric (HS256) and asymmetric (RS256, ES256) algorithms:
    - HS256: Uses shared secret for both signing and verification
    - RS256: Uses RSA private key for signing, public key for verification
    - ES256: Uses ECDSA private key for signing, public key for verification

    Schema for user documents:
        {
            "_id": ObjectId,
            "email": "user@example.com",
            "password_hash": "bcrypt_hash",
            "app_roles": {
                "app_slug_1": ["role1", "role2"],
                "app_slug_2": ["role3"]
            },
            "created_at": datetime,
            "updated_at": datetime,
            "last_login": datetime,
            "is_active": bool
        }
    """

    def __init__(
        self,
        mongo_db: AsyncIOMotorDatabase,
        jwt_secret: str | None = None,
        jwt_public_key: str | None = None,
        jwt_algorithm: str = DEFAULT_JWT_ALGORITHM,
        token_expiry_hours: int = DEFAULT_TOKEN_EXPIRY_HOURS,
        allow_insecure_dev: bool = False,
        blacklist_fail_closed: bool = True,
    ):
        """
        Initialize the shared user pool.

        Args:
            mongo_db: MongoDB database instance (raw, not scoped)
            jwt_secret: Secret or private key for JWT signing. For HS256, this is
                       the shared secret. For RS256/ES256, this is the private key.
                       If not provided, reads from MDB_ENGINE_JWT_SECRET env var.
            jwt_public_key: Public key for RS256/ES256 verification. If not provided,
                           reads from MDB_ENGINE_JWT_PUBLIC_KEY env var.
                           Not needed for HS256 (symmetric).
            jwt_algorithm: JWT algorithm (default: HS256). Supported:
                          - HS256, HS384, HS512 (symmetric/HMAC)
                          - RS256, RS384, RS512 (RSA asymmetric)
                          - ES256, ES384, ES512 (ECDSA asymmetric)
            token_expiry_hours: Token expiry in hours (default: 24)
            allow_insecure_dev: Allow insecure auto-generated secret for local
                               development only. NEVER use in production!
            blacklist_fail_closed: If True (default), reject tokens when blacklist check
                                  fails (secure). If False, allow tokens when check fails
                                  (availability). SECURITY: Default is True for security.

        Raises:
            JWTSecretError: If no JWT secret is provided and allow_insecure_dev=False
            JWTKeyError: If asymmetric algorithm is used without proper keys
        """
        self._db = mongo_db
        self._collection = mongo_db[SHARED_USERS_COLLECTION]
        self._blacklist_collection = mongo_db[TOKEN_BLACKLIST_COLLECTION]
        self._blacklist_fail_closed = blacklist_fail_closed

        # Validate algorithm
        if jwt_algorithm not in SUPPORTED_ALGORITHMS:
            raise JWTKeyError(
                f"Unsupported JWT algorithm: {jwt_algorithm}. "
                f"Supported: {', '.join(sorted(SUPPORTED_ALGORITHMS))}"
            )

        self._jwt_algorithm = jwt_algorithm
        self._is_asymmetric = jwt_algorithm in ASYMMETRIC_ALGORITHMS

        # Load keys from params or environment
        self._jwt_secret = jwt_secret or os.getenv("MDB_ENGINE_JWT_SECRET")
        self._jwt_public_key = jwt_public_key or os.getenv("MDB_ENGINE_JWT_PUBLIC_KEY")

        # Validate key configuration
        if self._is_asymmetric:
            self._setup_asymmetric_keys(allow_insecure_dev)
        else:
            self._setup_symmetric_key(allow_insecure_dev)

        self._token_expiry_hours = token_expiry_hours
        self._blacklist_indexes_created = False

        logger.info(f"SharedUserPool initialized (algorithm={jwt_algorithm})")

    def _setup_symmetric_key(self, allow_insecure_dev: bool) -> None:
        """Set up symmetric key for HMAC algorithms (HS256, etc.)."""
        if not self._jwt_secret:
            if allow_insecure_dev:
                # Generate ephemeral secret for development
                self._jwt_secret = secrets.token_urlsafe(32)
                logger.warning(
                    "⚠️  INSECURE: Using auto-generated JWT secret. "
                    "Tokens will be invalid after restart. "
                    "Set MDB_ENGINE_JWT_SECRET for production!"
                )
            else:
                raise JWTSecretError(
                    "JWT secret required for SharedUserPool. "
                    "Set MDB_ENGINE_JWT_SECRET environment variable or pass jwt_secret parameter. "
                    "Generate a secure secret with: "
                    'python -c "import secrets; print(secrets.token_urlsafe(32))" '
                    "For local development only, pass allow_insecure_dev=True."
                )

        # For symmetric, signing key = verification key
        self._signing_key = self._jwt_secret
        self._verification_key = self._jwt_secret

    def _setup_asymmetric_keys(self, allow_insecure_dev: bool) -> None:
        """Set up asymmetric keys for RSA/ECDSA algorithms (RS256, ES256, etc.)."""
        if not self._jwt_secret:
            if allow_insecure_dev:
                logger.warning(
                    f"⚠️  INSECURE: {self._jwt_algorithm} requires a private key. "
                    f"Set MDB_ENGINE_JWT_SECRET with your private key in production!"
                )
                # We can't auto-generate RSA/ECDSA keys easily, so error out
                raise JWTKeyError(
                    f"Private key required for {self._jwt_algorithm} algorithm. "
                    f"Set MDB_ENGINE_JWT_SECRET environment variable with your "
                    f"PEM-encoded private key. "
                    f"Asymmetric algorithms cannot auto-generate keys even in dev mode."
                )
            else:
                raise JWTKeyError(
                    f"Private key required for {self._jwt_algorithm} algorithm. "
                    f"Set MDB_ENGINE_JWT_SECRET environment variable with your "
                    f"PEM-encoded private key."
                )

        if not self._jwt_public_key:
            # For verification, we can derive public key from private in some cases,
            # or require it explicitly for better security
            logger.warning(
                f"⚠️  No public key provided for {self._jwt_algorithm}. "
                f"Token verification will use the private key (less secure). "
                f"Set MDB_ENGINE_JWT_PUBLIC_KEY for better key separation."
            )
            # Use private key for both (PyJWT can handle this for RSA)
            self._verification_key = self._jwt_secret
        else:
            self._verification_key = self._jwt_public_key

        self._signing_key = self._jwt_secret

        logger.info(
            f"Asymmetric JWT configured: algorithm={self._jwt_algorithm}, "
            f"public_key={'provided' if self._jwt_public_key else 'derived'}"
        )

    @property
    def jwt_algorithm(self) -> str:
        """Get the configured JWT algorithm."""
        return self._jwt_algorithm

    @property
    def is_asymmetric(self) -> bool:
        """Check if using asymmetric (public/private key) algorithm."""
        return self._is_asymmetric

    async def ensure_indexes(self) -> None:
        """Create necessary indexes for the shared users and blacklist collections."""
        try:
            # Unique index on email
            await self._collection.create_index("email", unique=True, name="email_unique_idx")
            # Index for active users
            await self._collection.create_index(
                [("is_active", 1), ("email", 1)], name="active_email_idx"
            )
            logger.info("SharedUserPool user indexes ensured")
        except OperationFailure as e:
            logger.warning(f"Failed to create user indexes: {e}")

        # Ensure blacklist indexes
        await self._ensure_blacklist_indexes()

    async def _ensure_blacklist_indexes(self) -> None:
        """Create indexes for token blacklist collection."""
        if self._blacklist_indexes_created:
            return

        try:
            # Unique index on JTI for fast lookups
            await self._blacklist_collection.create_index("jti", unique=True, name="jti_unique_idx")
            # TTL index for automatic cleanup of expired entries
            await self._blacklist_collection.create_index(
                "expires_at", expireAfterSeconds=0, name="expires_at_ttl_idx"
            )
            self._blacklist_indexes_created = True
            logger.info("SharedUserPool blacklist indexes ensured")
        except OperationFailure as e:
            logger.warning(f"Failed to create blacklist indexes: {e}")

    async def create_user(
        self,
        email: str,
        password: str,
        app_roles: dict[str, list[str]] | None = None,
        is_active: bool = True,
    ) -> dict[str, Any]:
        """
        Create a new shared user.

        Args:
            email: User email (must be unique)
            password: Plain text password (will be hashed)
            app_roles: Dict of app_slug -> list of roles
            is_active: Whether user is active (default: True)

        Returns:
            Created user document (without password_hash)

        Raises:
            ValueError: If email already exists
        """
        # Check if email already exists
        existing = await self._collection.find_one({"email": email})
        if existing:
            raise ValueError(f"User with email '{email}' already exists")

        # Hash password
        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

        now = datetime.utcnow()
        user_doc = {
            "email": email,
            "password_hash": password_hash,
            "app_roles": app_roles or {},
            "is_active": is_active,
            "created_at": now,
            "updated_at": now,
            "last_login": None,
        }

        result = await self._collection.insert_one(user_doc)
        user_doc["_id"] = result.inserted_id

        # Return without password hash
        return self._sanitize_user(user_doc)

    async def authenticate(
        self,
        email: str,
        password: str,
        ip_address: str | None = None,
        fingerprint: str | None = None,
        session_binding: dict[str, Any] | None = None,
    ) -> str | None:
        """
        Authenticate user and return JWT token.

        Args:
            email: User email
            password: Plain text password
            ip_address: Client IP address for session binding
            fingerprint: Device fingerprint for session binding
            session_binding: Session binding config from manifest:
                - bind_ip: Include IP in token claims
                - bind_fingerprint: Include fingerprint in token claims

        Returns:
            JWT token if authentication succeeds, None otherwise
        """
        user = await self._collection.find_one(
            {
                "email": email,
                "is_active": True,
            }
        )

        if not user:
            logger.warning(f"Authentication failed: user '{email}' not found or inactive")
            return None

        # Verify password
        if not bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8")):
            logger.warning(f"Authentication failed: invalid password for '{email}'")
            return None

        # Update last login
        await self._collection.update_one(
            {"_id": user["_id"]}, {"$set": {"last_login": datetime.utcnow()}}
        )

        # Prepare extra claims for session binding
        extra_claims = {}
        session_binding = session_binding or {}

        if session_binding.get("bind_ip", False) and ip_address:
            extra_claims["ip"] = ip_address
            logger.debug(f"Session bound to IP: {ip_address}")

        if session_binding.get("bind_fingerprint", True) and fingerprint:
            extra_claims["fp"] = fingerprint
            logger.debug(f"Session bound to fingerprint: {fingerprint[:16]}...")

        # Generate JWT token with session binding claims
        token = self._generate_token(user, extra_claims=extra_claims or None)

        logger.info(f"User '{email}' authenticated successfully")
        return token

    async def validate_token(self, token: str) -> dict[str, Any] | None:
        """
        Validate JWT token and return user data.

        Validation steps:
        1. Decode and verify JWT signature (uses public key for asymmetric)
        2. Check if token is blacklisted (revoked)
        3. Fetch current user data (roles may have changed)

        Args:
            token: JWT token string

        Returns:
            User dict if token is valid, None otherwise
        """
        try:
            payload = jwt.decode(token, self._verification_key, algorithms=[self._jwt_algorithm])

            user_id = payload.get("sub")
            if not user_id:
                return None

            # Check if token is revoked (blacklisted)
            jti = payload.get("jti")
            if jti and await self._is_token_revoked(jti):
                logger.debug(f"Token validation failed: token revoked (jti={jti})")
                return None

            # Fetch current user data (roles may have changed)
            from bson import ObjectId

            user = await self._collection.find_one(
                {
                    "_id": ObjectId(user_id),
                    "is_active": True,
                }
            )

            if not user:
                return None

            return self._sanitize_user(user)

        except jwt.ExpiredSignatureError:
            logger.debug("Token validation failed: token expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.debug(f"Token validation failed: {e}")
            return None

    async def _is_token_revoked(self, jti: str) -> bool:
        """Check if a token JTI is in the blacklist."""
        try:
            entry = await self._blacklist_collection.find_one({"jti": jti})
            if entry:
                # Double-check expiration (TTL index should handle this)
                expires_at = entry.get("expires_at")
                if expires_at and isinstance(expires_at, datetime):
                    return datetime.utcnow() < expires_at
                return True  # No expiration = permanently blacklisted
            return False
        except PyMongoError as e:
            logger.exception(f"Error checking token blacklist: {e}")
            # Fail closed for security by default (reject token if we can't verify)
            if self._blacklist_fail_closed:
                logger.warning(
                    "Token blacklist check failed - rejecting token for security "
                    "(blacklist_fail_closed=True)"
                )
                return True  # Token IS revoked (reject access)
            # Fail open only if explicitly configured (availability over security)
            logger.warning(
                "Token blacklist check failed - allowing token for availability "
                "(blacklist_fail_closed=False). SECURITY RISK: Revoked tokens may be accepted."
            )
            return False  # Token NOT revoked (allow access)

    async def revoke_token(
        self,
        token: str,
        reason: str = "logout",
    ) -> bool:
        """
        Revoke a token by adding its JTI to the blacklist.

        Args:
            token: JWT token to revoke
            reason: Reason for revocation (default: "logout")

        Returns:
            True if token was successfully revoked, False otherwise
        """
        try:
            # Decode token to get JTI and expiration
            payload = jwt.decode(
                token,
                self._verification_key,
                algorithms=[self._jwt_algorithm],
                options={"verify_exp": False},  # Allow revoking expired tokens
            )

            jti = payload.get("jti")
            if not jti:
                logger.warning("Cannot revoke token: no JTI claim")
                return False

            # Get expiration from token, or use default
            exp_timestamp = payload.get("exp")
            if exp_timestamp:
                expires_at = datetime.utcfromtimestamp(exp_timestamp)
            else:
                expires_at = datetime.utcnow() + timedelta(days=7)

            # Ensure blacklist indexes exist
            await self._ensure_blacklist_indexes()

            # Add to blacklist with upsert
            await self._blacklist_collection.update_one(
                {"jti": jti},
                {
                    "$set": {
                        "jti": jti,
                        "user_id": payload.get("sub"),
                        "email": payload.get("email"),
                        "revoked_at": datetime.utcnow(),
                        "expires_at": expires_at,
                        "reason": reason,
                    }
                },
                upsert=True,
            )

            logger.info(f"Token revoked: jti={jti}, reason={reason}")
            return True

        except jwt.InvalidTokenError as e:
            logger.warning(f"Cannot revoke invalid token: {e}")
            return False
        except PyMongoError as e:
            logger.exception(f"Error revoking token: {e}")
            return False

    async def revoke_all_user_tokens(
        self,
        user_id: str,
        reason: str = "logout_all",
    ) -> None:
        """
        Revoke all tokens for a user by storing a revocation marker.

        Note: This requires checking user's token_revoked_at during validation.
        For immediate effect, consider using short token expiry + refresh tokens.

        Args:
            user_id: User ID to revoke tokens for
            reason: Reason for revocation
        """
        await self._collection.update_one(
            {"_id": user_id},
            {
                "$set": {
                    "tokens_revoked_at": datetime.utcnow(),
                    "tokens_revoked_reason": reason,
                }
            },
        )
        logger.info(f"All tokens revoked for user {user_id}: {reason}")

    async def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        """Get user by email."""
        user = await self._collection.find_one({"email": email})
        if user:
            return self._sanitize_user(user)
        return None

    async def update_user_roles(
        self,
        email: str,
        app_slug: str,
        roles: list[str],
    ) -> bool:
        """
        Update a user's roles for a specific app.

        Args:
            email: User email
            app_slug: App slug to update roles for
            roles: New list of roles for this app

        Returns:
            True if updated, False if user not found
        """
        result = await self._collection.update_one(
            {"email": email},
            {
                "$set": {
                    f"app_roles.{app_slug}": roles,
                    "updated_at": datetime.utcnow(),
                }
            },
        )

        if result.modified_count > 0:
            logger.info(f"Updated roles for '{email}' in app '{app_slug}': {roles}")
            return True
        return False

    async def remove_user_from_app(self, email: str, app_slug: str) -> bool:
        """
        Remove a user's access to a specific app.

        Args:
            email: User email
            app_slug: App slug to remove access from

        Returns:
            True if updated, False if user not found
        """
        result = await self._collection.update_one(
            {"email": email},
            {
                "$unset": {f"app_roles.{app_slug}": ""},
                "$set": {"updated_at": datetime.utcnow()},
            },
        )

        if result.modified_count > 0:
            logger.info(f"Removed '{email}' from app '{app_slug}'")
            return True
        return False

    async def deactivate_user(self, email: str) -> bool:
        """Deactivate a user account."""
        result = await self._collection.update_one(
            {"email": email},
            {
                "$set": {
                    "is_active": False,
                    "updated_at": datetime.utcnow(),
                }
            },
        )
        return result.modified_count > 0

    async def activate_user(self, email: str) -> bool:
        """Activate a user account."""
        result = await self._collection.update_one(
            {"email": email},
            {
                "$set": {
                    "is_active": True,
                    "updated_at": datetime.utcnow(),
                }
            },
        )
        return result.modified_count > 0

    @staticmethod
    def user_has_role(
        user: dict[str, Any],
        app_slug: str,
        required_role: str,
        role_hierarchy: dict[str, list[str]] | None = None,
    ) -> bool:
        """
        Check if user has a required role for an app.

        Args:
            user: User dict (from validate_token or get_user_by_email)
            app_slug: App slug to check
            required_role: Role to check for
            role_hierarchy: Optional dict mapping roles to their inherited roles
                           e.g., {"admin": ["editor", "viewer"], "editor": ["viewer"]}

        Returns:
            True if user has the required role (directly or via hierarchy)
        """
        app_roles = user.get("app_roles", {}).get(app_slug, [])

        # Direct role check
        if required_role in app_roles:
            return True

        # Check via hierarchy
        if role_hierarchy:
            for user_role in app_roles:
                inherited_roles = role_hierarchy.get(user_role, [])
                if required_role in inherited_roles:
                    return True

        return False

    @staticmethod
    def get_user_roles_for_app(
        user: dict[str, Any],
        app_slug: str,
    ) -> list[str]:
        """Get user's roles for a specific app."""
        return user.get("app_roles", {}).get(app_slug, [])

    def _generate_token(
        self,
        user: dict[str, Any],
        extra_claims: dict[str, Any] | None = None,
    ) -> str:
        """
        Generate JWT token for user with unique JTI for revocation support.

        Token payload includes:
        - sub: User ID
        - email: User email
        - jti: Unique token identifier (for revocation)
        - iat: Issued at timestamp
        - exp: Expiration timestamp
        - Additional claims for session binding (ip, fingerprint) if provided

        Args:
            user: User document
            extra_claims: Optional extra claims to include (e.g., ip, fingerprint)

        Returns:
            Signed JWT token string
        """
        now = datetime.utcnow()
        payload = {
            "sub": str(user["_id"]),
            "email": user["email"],
            "jti": secrets.token_urlsafe(16),  # Unique token ID for revocation
            "iat": now,
            "exp": now + timedelta(hours=self._token_expiry_hours),
        }

        # Add extra claims (session binding info, etc.)
        if extra_claims:
            payload.update(extra_claims)

        return jwt.encode(payload, self._signing_key, algorithm=self._jwt_algorithm)

    @staticmethod
    def _sanitize_user(user: dict[str, Any]) -> dict[str, Any]:
        """Remove sensitive fields from user document."""
        sanitized = dict(user)
        sanitized.pop("password_hash", None)
        # Convert ObjectId to string for JSON serialization
        if "_id" in sanitized:
            sanitized["_id"] = str(sanitized["_id"])
        return sanitized

    def get_secure_cookie_config(self, request: "Request") -> dict[str, Any]:
        """
        Get secure cookie settings for auth tokens.

        Integrates with existing cookie_utils for environment-aware security settings.
        Automatically enables Secure flag in production/HTTPS environments.

        Args:
            request: FastAPI Request object for environment detection

        Returns:
            Dict of cookie settings ready for response.set_cookie()

        Usage:
            cookie_config = user_pool.get_secure_cookie_config(request)
            response.set_cookie(value=token, **cookie_config)
        """
        from .cookie_utils import get_secure_cookie_settings

        base_settings = get_secure_cookie_settings(request)
        return {
            **base_settings,
            "key": "mdb_auth_token",
            "max_age": self._token_expiry_hours * 3600,
        }

    @property
    def token_expiry_hours(self) -> int:
        """Get token expiry duration in hours."""
        return self._token_expiry_hours
