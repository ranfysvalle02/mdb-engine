"""
App-Level User Management Module

Provides utilities for app-level user management.

This module allows apps to manage their own users and sessions
separate from platform-level authentication, while maintaining integration
with the platform's auth system.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import bcrypt
import jwt
from fastapi import Request
from fastapi.responses import Response

try:
    from pymongo.errors import (ConnectionFailure, OperationFailure,
                                ServerSelectionTimeoutError)
except ImportError:
    ConnectionFailure = Exception
    OperationFailure = Exception
    ServerSelectionTimeoutError = Exception

from .dependencies import SECRET_KEY

logger = logging.getLogger(__name__)


def _is_auth_route(request_path: str) -> bool:
    """Check if request path is an authentication route."""
    auth_route_patterns = ["/login", "/register", "/signin", "/signup", "/auth"]
    return any(pattern in request_path.lower() for pattern in auth_route_patterns)


async def _get_app_user_config(
    request: Request,
    slug_id: str,
    config: Optional[Dict[str, Any]],
    get_app_config_func: Optional[Callable[[Request, str, Dict], Awaitable[Dict]]],
) -> Optional[Dict[str, Any]]:
    """Fetch and validate app user config."""
    if config is None:
        if not get_app_config_func:
            raise ValueError(
                "config or get_app_config_func must be provided. "
                "Provide either the config dict directly or a callable that returns it."
            )
        config = await get_app_config_func(request, slug_id, {"auth": 1})

    if not config:
        return None

    auth = config.get("auth", {})
    users_config = auth.get("users", {})
    if not users_config.get("enabled", False):
        return None

    return config


def _convert_user_id_to_objectid(user_id: Any) -> Tuple[Any, Optional[str]]:
    """
    Convert user_id to ObjectId if valid, otherwise keep as string.

    Returns:
        Tuple of (converted_user_id, error_message)
    """
    from bson.objectid import ObjectId

    if isinstance(user_id, str):
        # Check if it's a valid ObjectId string (24 hex characters)
        if len(user_id) == 24 and all(c in "0123456789abcdefABCDEF" for c in user_id):
            try:
                return ObjectId(user_id), None
            except (TypeError, ValueError):
                # If conversion fails (shouldn't happen after format check), keep as string
                # Type 2: Recoverable - return original string as fallback
                return user_id, None
        # If it's not a valid ObjectId format, keep as string
        return user_id, None
    elif not isinstance(user_id, ObjectId):
        # If it's not a string or ObjectId, try to convert (for backward compatibility)
        try:
            return ObjectId(user_id), None
        except (TypeError, ValueError) as e:
            return None, f"Invalid user_id format: {user_id}: {e}"
        except Exception:
            logger.exception("Unexpected error converting user_id to ObjectId")
            raise

    return user_id, None


async def _validate_and_decode_session_token(
    session_token: str, slug_id: str
) -> Tuple[Optional[Dict[str, Any]], Optional[Exception]]:
    """Validate and decode session token."""
    try:
        from .jwt import decode_jwt_token

        payload = decode_jwt_token(session_token, str(SECRET_KEY))

        # Verify it's for this app
        if payload.get("app_slug") != slug_id:
            logger.warning(
                f"Session token for wrong app: expected {slug_id}, got {payload.get('app_slug')}"
            )
            return None, None

        # Get user ID from token
        user_id = payload.get("app_user_id")
        if not user_id:
            return None, None

        return payload, None
    except jwt.ExpiredSignatureError:
        logger.debug(f"Session token expired for app {slug_id}")
        return None, jwt.ExpiredSignatureError()
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid session token for app {slug_id}: {e}")
        return None, e
    except (ValueError, TypeError) as e:
        logger.exception("Validation error getting app sub user")
        return None, e


async def _fetch_app_user_from_db(
    db, collection_name: str, user_id: Any
) -> Optional[Dict[str, Any]]:
    """Fetch user from database."""
    # Use getattr for attribute access (works with both AppDB and ScopedMongoWrapper)
    collection = getattr(db, collection_name)
    user = await collection.find_one({"_id": user_id})
    if not user:
        logger.warning(f"User {user_id} not found in collection {collection_name}")
        return None

    # Add app user ID to user dict
    user["app_user_id"] = str(user["_id"])
    return user


async def get_app_user(
    request: Request,
    slug_id: str,
    db,
    config: Optional[Dict[str, Any]] = None,
    allow_demo_fallback: bool = False,
    get_app_config_func: Optional[
        Callable[[Request, str, Dict], Awaitable[Dict]]
    ] = None,
) -> Optional[Dict[str, Any]]:
    """
    Get app-level user from session cookie.

    This function handles app-level user management by:
    1. Checking for app-specific session cookie
    2. Validating session token
    3. Returning app user data if authenticated
    4. If allow_demo_fallback is True and no session, tries demo mode (for allow_demo_access)

    SECURITY: Demo mode is BLOCKED for authentication routes (login, register, auth).
    Demo users must remain trapped in demo mode - they cannot attempt to login as other users
    or register new accounts. This is a security restriction to prevent privilege escalation.
    Demo users are marked with is_demo=True flag so apps can detect and restrict them.

    Args:
        request: FastAPI Request object
        slug_id: App slug
        db: Database wrapper (ScopedMongoWrapper or AppDB)
        config: Optional app config (if not provided, fetches from request)
        allow_demo_fallback: If True and no session found, try demo mode for seamless demo access
        (SECURITY: Only works on non-auth routes - demo users cannot access login/registration)

    Returns:
        Dict with app user data (with is_demo=True flag if demo user), or None if not authenticated
    """
    # Get and validate config
    config = await _get_app_user_config(request, slug_id, config, get_app_config_func)
    if not config:
        return None

    auth = config.get("auth", {})
    users_config = auth.get("users", {})
    collection_name = users_config.get("collection_name", "users")

    # SECURITY: Check if this is an authentication route
    is_auth_route = _is_auth_route(request.url.path)

    # Get session cookie name
    session_cookie_name = users_config.get("session_cookie_name", "app_session")
    cookie_name = f"{session_cookie_name}_{slug_id}"

    # Get session token from cookie
    session_token = request.cookies.get(cookie_name)
    if not session_token:
        # If no session and demo fallback enabled, try demo mode
        # BUT: SECURITY - Skip demo mode for auth routes
        if allow_demo_fallback and not is_auth_route:
            return await _try_demo_mode(request, slug_id, db, config)
        return None

    # Validate and decode session token
    payload, error = await _validate_and_decode_session_token(session_token, slug_id)
    if not payload:
        # If token validation failed and demo fallback enabled, try demo mode
        if allow_demo_fallback and not is_auth_route and error:
            return await _try_demo_mode(request, slug_id, db, config)
        return None

    # Get user ID from token
    user_id = payload.get("app_user_id")
    if not user_id:
        return None

    # Convert user_id to ObjectId if needed
    user_id, error_msg = _convert_user_id_to_objectid(user_id)
    if user_id is None:
        if error_msg:
            logger.warning(error_msg)
        return None

    # Fetch user from database
    return await _fetch_app_user_from_db(db, collection_name, user_id)


async def _try_demo_mode(
    request: Request, slug_id: str, db, config: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Internal helper: Try to authenticate as demo user if demo mode is enabled.

    This is called when normal authentication fails and allow_demo_access is enabled.
    It gets/creates a demo user automatically, providing seamless demo experience.

    Args:
        request: FastAPI Request object
        slug_id: App slug
        db: Database wrapper
        config: App config (must contain auth.users block)

    Returns:
        Demo user dict if demo mode is enabled and demo user is available, None otherwise
    """
    auth = config.get("auth", {})
    users_config = auth.get("users", {})

    # Check if demo mode is enabled OR intelligent demo auto-linking is enabled
    allow_demo_access = users_config.get("allow_demo_access", False)
    auto_link_demo = users_config.get("auto_link_platform_demo", True)
    seed_strategy = users_config.get("demo_user_seed_strategy", "auto")

    # Enable demo mode if:
    # 1. allow_demo_access is explicitly true, OR
    # 2. auto_link_platform_demo is true and seed_strategy is auto (intelligent demo support)
    # This allows "batteries included" demo access even without explicit allow_demo_access flag
    if not allow_demo_access:
        if not (auto_link_demo and seed_strategy == "auto"):
            return None

    # Check if demo user seeding is allowed
    if seed_strategy == "disabled":
        return None

    try:
        # Get or create demo user
        from ..config import DB_NAME, MONGO_URI

        logger.info(
            f"Demo mode: Attempting to get/create demo user for '{slug_id}' "
            f"(MONGO_URI={MONGO_URI}, DB_NAME={DB_NAME})"
        )

        demo_user = await get_or_create_demo_user(
            db, slug_id, config, MONGO_URI, DB_NAME
        )

        if not demo_user:
            logger.warning(
                f"Demo mode enabled for {slug_id}, but no demo user available. "
                f"Check if platform demo user exists and auto_link_platform_demo is enabled."
            )
            return None

        logger.info(
            f"Demo mode: Auto-authenticating user '{demo_user.get('email')}' "
            f"for app '{slug_id}' (via intelligent demo auto-linking)"
        )

        # Mark in request state that this was demo mode (for potential session creation)
        # Note: Session cookie will be created automatically on next request cycle if needed
        # For seamless demo experience, demo mode works without session cookies initially
        request.state.demo_mode_user = demo_user
        request.state.demo_mode_slug = slug_id

        return demo_user

    except (
        ValueError,
        TypeError,
        AttributeError,
        RuntimeError,
        ConnectionError,
        KeyError,
    ) as e:
        logger.error(f"Demo mode failed for {slug_id}: {e}", exc_info=True)
        return None


async def create_app_session(
    request: Request,
    slug_id: str,
    user_id: str,
    config: Optional[Dict[str, Any]] = None,
    response: Optional[Response] = None,
    get_app_config_func: Optional[
        Callable[[Request, str, Dict], Awaitable[Dict]]
    ] = None,
) -> str:
    """
    Create a app-specific session token and set cookie.

    Args:
        request: FastAPI Request object
        slug_id: App slug
        user_id: User ID (from app's users collection)
        config: Optional app config
        response: Optional Response object to set cookie on (creates new if None)

    Returns:
        Session token string
    """
    # Get auth.users config
    if config is None:
        if not get_app_config_func:
            raise ValueError(
                "config or get_app_config_func must be provided. "
                "Provide either the config dict directly or a callable that returns it."
            )
        config = await get_app_config_func(request, slug_id, {"auth": 1})

    if not config:
        raise ValueError(f"App config not found for {slug_id}")

    auth = config.get("auth", {})
    users_config = auth.get("users", {})
    if not users_config.get("enabled", False):
        raise ValueError(f"App-level user management not enabled for app {slug_id}")

    # Get session TTL
    session_ttl = users_config.get("session_ttl_seconds", 86400)

    # Create JWT payload
    payload = {
        "app_slug": slug_id,
        "app_user_id": str(user_id),
        "exp": datetime.utcnow() + timedelta(seconds=session_ttl),
        "iat": datetime.utcnow(),
    }

    # Sign token
    # Ensure SECRET_KEY is a string (not bytes) for jwt.encode
    secret_key = str(SECRET_KEY)
    if isinstance(secret_key, bytes):
        secret_key = secret_key.decode("utf-8")
    elif not isinstance(secret_key, str):
        secret_key = str(secret_key)

    token = jwt.encode(payload, secret_key, algorithm="HS256")
    # Ensure token is a string (some PyJWT versions return bytes)
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    elif not isinstance(token, str):
        token = str(token)

    # Set cookie if response provided
    if response:
        session_cookie_name = users_config.get("session_cookie_name", "app_session")
        cookie_name = f"{session_cookie_name}_{slug_id}"

        # Determine secure cookie setting
        should_use_secure = (
            request.url.scheme == "https" or os.getenv("G_NOME_ENV") == "production"
        )

        response.set_cookie(
            key=cookie_name,
            value=token,
            httponly=True,
            secure=should_use_secure,
            samesite="lax",
            max_age=session_ttl,
        )

    return token


async def authenticate_app_user(
    db,
    email: str,
    password: str,
    store_id: Optional[str] = None,
    collection_name: str = "users",
) -> Optional[Dict[str, Any]]:
    """
    Authenticate a user against app-specific users collection.

    Args:
        db: Database wrapper
        email: User email
        password: Plain text password
        store_id: Optional store ID filter (for store_factory multi-store scenario)
        collection_name: Collection name for users (default: "users")

    Returns:
        User dict if authenticated, None otherwise
    """
    try:
        # Validate email format
        if not email or not isinstance(email, str) or "@" not in email:
            logger.debug(f"Invalid email format for authentication: {email}")
            return None

        # Build query
        query = {"email": email}
        if store_id:
            try:
                from bson.objectid import ObjectId

                query["store_id"] = ObjectId(store_id)
            except (TypeError, ValueError) as e:
                logger.warning(f"Invalid store_id format: {store_id}: {e}")
                return None
            except Exception:
                logger.exception("Unexpected error converting store_id to ObjectId")
                raise

        # Find user
        # Use getattr to access collection (works with ScopedMongoWrapper and AppDB)
        collection = getattr(db, collection_name)
        user = await collection.find_one(query)
        if not user:
            return None

        # Check password (bcrypt only - plain text support removed for security)
        stored_password = user.get("password_hash") or user.get("password")

        if not stored_password:
            return None

        # Only allow bcrypt hashed passwords
        if isinstance(stored_password, bytes) or (
            isinstance(stored_password, str) and stored_password.startswith("$2b$")
        ):
            if isinstance(stored_password, str):
                stored_password = stored_password.encode("utf-8")
            if isinstance(password, str):
                password = password.encode("utf-8")

            if bcrypt.checkpw(password, stored_password):
                return user
        else:
            # Password is not bcrypt hashed - reject for security
            logger.warning(
                f"User {email} has non-bcrypt password hash - password verification rejected"
            )
            return None

        return None

    except (ValueError, TypeError):
        logger.exception("Validation error authenticating app user")
        return None
    except Exception:
        logger.exception("Unexpected error authenticating app user")
        # Re-raise unexpected errors for debugging
        raise


async def create_app_user(
    db,
    email: str,
    password: str,
    role: str = "user",
    store_id: Optional[str] = None,
    collection_name: str = "users",
) -> Optional[Dict[str, Any]]:
    """
    Create a new user in app-specific users collection.

    Args:
        db: Database wrapper
        email: User email
        password: Plain text password (will be hashed with bcrypt)
        role: User role (default: "user")
        store_id: Optional store ID (for store_factory)
        collection_name: Collection name for users

    Returns:
        Created user dict, or None if creation failed
    """
    try:
        import datetime

        from bson.objectid import ObjectId

        # Validate email format
        if (
            not email
            or not isinstance(email, str)
            or "@" not in email
            or "." not in email
        ):
            logger.warning(f"Invalid email format: {email}")
            return None

        # Validate password
        if not password or not isinstance(password, str) or len(password) == 0:
            logger.warning("Invalid password (empty or not a string)")
            return None

        # Check if user already exists
        query = {"email": email}
        if store_id:
            try:
                query["store_id"] = ObjectId(store_id)
            except (ValueError, TypeError, AttributeError) as e:
                logger.warning(f"Invalid store_id format: {store_id}: {e}")
                return None

        # Use getattr for attribute access (works with both AppDB and ScopedMongoWrapper)
        collection = getattr(db, collection_name)
        existing = await collection.find_one(query)
        if existing:
            return None

        # Always hash password (plain text support removed for security)
        try:
            password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
        except ValueError:
            logger.exception("Error encoding password for hashing")
            return None
        except Exception:
            logger.exception("Unexpected error hashing password")
            # Re-raise unexpected errors
            raise

        # Create user document
        user_doc = {
            "email": email,
            "password_hash": password_hash,
            "role": role,
            "date_created": datetime.datetime.utcnow(),
        }

        if store_id:
            user_doc["store_id"] = ObjectId(store_id)

        # Insert user
        # Use getattr for attribute access (works with both AppDB and ScopedMongoWrapper)
        collection = getattr(db, collection_name)
        result = await collection.insert_one(user_doc)
        user_doc["_id"] = result.inserted_id
        user_doc["app_user_id"] = str(result.inserted_id)

        return user_doc

    except (ValueError, TypeError):
        logger.exception("Validation error creating app user")
        return None
    except Exception:
        logger.exception("Unexpected error creating app user")
        # Re-raise unexpected errors for debugging
        raise


async def get_or_create_anonymous_user(
    request: Request,
    slug_id: str,
    db,
    config: Optional[Dict[str, Any]] = None,
    get_app_config_func: Optional[
        Callable[[Request, str, Dict], Awaitable[Dict]]
    ] = None,
) -> Optional[Dict[str, Any]]:
    """
    Get or create an anonymous user for anonymous_session strategy.

    Args:
        request: FastAPI Request object
        slug_id: App slug
        db: Database wrapper
        config: Optional app config

    Returns:
        Anonymous user dict
    """
    # Get auth.users config
    if config is None:
        if not get_app_config_func:
            raise ValueError(
                "config or get_app_config_func must be provided. "
                "Provide either the config dict directly or a callable that returns it."
            )
        config = await get_app_config_func(request, slug_id, {"auth": 1})

    if not config:
        return None

    auth = config.get("auth", {})
    users_config = auth.get("users", {})
    if users_config.get("strategy") != "anonymous_session":
        return None

    # Get or create anonymous user ID from session
    session_cookie_name = users_config.get("session_cookie_name", "app_session")
    cookie_name = f"{session_cookie_name}_{slug_id}"

    anonymous_id = request.cookies.get(cookie_name)
    if not anonymous_id:
        # Generate new anonymous ID
        prefix = users_config.get("anonymous_user_prefix", "guest")
        anonymous_id = f"{prefix}_{uuid.uuid4().hex[:8]}"

    # Create or get anonymous user
    collection_name = users_config.get("collection_name", "users")
    # Use getattr to access collection (works with ScopedMongoWrapper and ExperimentDB)
    collection = getattr(db, collection_name)
    user = await collection.find_one({"email": anonymous_id})

    if not user:
        import datetime

        user_doc = {
            "email": anonymous_id,
            "role": "anonymous",
            "is_anonymous": True,
            "date_created": datetime.datetime.utcnow(),
        }
        # Use getattr to access collection (works with ScopedMongoWrapper and AppDB)
        collection = getattr(db, collection_name)
        result = await collection.insert_one(user_doc)
        user_doc["_id"] = result.inserted_id
        user = user_doc

    user["app_user_id"] = str(user["_id"])
    return user


async def get_platform_demo_user(
    mongo_uri: str, db_name: str
) -> Optional[Dict[str, Any]]:
    """
    Get platform demo user information from top-level database.

    Args:
        mongo_uri: MongoDB connection URI
        db_name: Database name

    Returns:
        Dict with demo user info (email, password from config, user_id) or None if not available
    """
    try:
        from ..config import (DEMO_EMAIL_DEFAULT, DEMO_ENABLED,
                              DEMO_PASSWORD_DEFAULT)

        if not DEMO_ENABLED or not DEMO_EMAIL_DEFAULT:
            return None

        # Access top-level database
        from motor.motor_asyncio import AsyncIOMotorClient

        client = AsyncIOMotorClient(mongo_uri)
        top_level_db = client[db_name]

        # Check if demo user exists
        demo_user = await top_level_db.users.find_one(
            {"email": DEMO_EMAIL_DEFAULT}, {"_id": 1, "email": 1}
        )
        client.close()

        if demo_user:
            return {
                "email": DEMO_EMAIL_DEFAULT,
                "password": DEMO_PASSWORD_DEFAULT,  # For demo purposes
                "platform_user_id": str(demo_user["_id"]),
                "platform_user": demo_user,
            }

        return None

    except (
        ValueError,
        TypeError,
        AttributeError,
        RuntimeError,
        ConnectionError,
        KeyError,
    ) as e:
        logger.error(f"Error getting platform demo user: {e}", exc_info=True)
        return None


async def _link_platform_demo_user(
    db, slug_id: str, collection_name: str, mongo_uri: str, db_name: str
) -> Optional[Dict[str, Any]]:
    """Link platform demo user to app demo user."""
    import datetime

    try:
        logger.debug(
            f"ensure_demo_users_exist: Auto-linking platform demo user for '{slug_id}'"
        )
        platform_demo = await get_platform_demo_user(mongo_uri, db_name)
        if not platform_demo:
            logger.warning(
                f"ensure_demo_users_exist: Platform demo user not found for '{slug_id}'"
            )
            return None

        # Check if app demo user already exists for platform demo
        collection = getattr(db, collection_name)
        existing = await collection.find_one({"email": platform_demo["email"]})

        if existing:
            return existing

        # Create app demo user linked to platform demo
        platform_password = platform_demo.get("password", "demo123")
        password_hash = None
        try:
            password_hash = bcrypt.hashpw(
                platform_password.encode("utf-8"), bcrypt.gensalt()
            )
        except (ValueError, TypeError, AttributeError) as e:
            logger.error(
                f"Error hashing password for platform demo user "
                f"{platform_demo['email']}: {e}",
                exc_info=True,
            )
            return None

        if password_hash:
            user_doc = {
                "email": platform_demo["email"],
                "password_hash": password_hash,
                "role": "user",
                "platform_user_id": platform_demo["platform_user_id"],
                "is_demo": True,
                "date_created": datetime.datetime.utcnow(),
            }
            collection = getattr(db, collection_name)
            result = await collection.insert_one(user_doc)
            user_doc["_id"] = result.inserted_id
            user_doc["app_user_id"] = str(result.inserted_id)
            logger.info(
                f"ensure_demo_users_exist: Created app demo user "
                f"'{platform_demo['email']}' for '{slug_id}'"
            )
            return user_doc

        return None
    except (
        ValueError,
        TypeError,
        AttributeError,
        RuntimeError,
        ConnectionError,
        KeyError,
    ) as e:
        logger.error(
            f"ensure_demo_users_exist: Error auto-linking platform demo "
            f"user for '{slug_id}': {e}",
            exc_info=True,
        )
        return None


def _validate_demo_user_config(
    demo_user_config: Any, slug_id: str
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Validate demo user configuration."""
    if not isinstance(demo_user_config, dict):
        return None, f"Invalid demo_user_config entry (not a dict): {demo_user_config}"

    extra_data = demo_user_config.get("extra_data", {})
    if not isinstance(extra_data, dict):
        logger.warning(
            f"Invalid extra_data for demo user config (not a dict): {extra_data}"
        )
        extra_data = {}

    return {
        "email": demo_user_config.get("email"),
        "password": demo_user_config.get("password"),
        "role": demo_user_config.get("role", "user"),
        "auto_create": demo_user_config.get("auto_create", True),
        "link_to_platform": demo_user_config.get("link_to_platform", False),
        "extra_data": extra_data,
    }, None


async def _resolve_demo_user_email_password(
    email: Optional[str],
    password: Optional[str],
    mongo_uri: Optional[str],
    db_name: Optional[str],
    slug_id: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Resolve email and password from config or platform demo."""
    # If email not specified, try platform demo
    if not email:
        if mongo_uri and db_name:
            platform_demo = await get_platform_demo_user(mongo_uri, db_name)
            if platform_demo:
                email = platform_demo["email"]
                if not password:
                    password = platform_demo["password"]
            else:
                logger.warning(
                    f"No email specified and platform demo not available for {slug_id}"
                )
                return None, None
        else:
            logger.warning(
                f"No email specified and cannot access platform demo for {slug_id}"
            )
            return None, None

    # Validate email format
    if not isinstance(email, str) or "@" not in email or "." not in email:
        logger.warning(f"Invalid email format for demo user: {email}")
        return None, None

    if not password:
        # Try to get from platform demo
        if mongo_uri and db_name:
            platform_demo = await get_platform_demo_user(mongo_uri, db_name)
            if platform_demo and platform_demo.get("email") == email:
                password = platform_demo.get("password")

        if not password:
            password = "demo123"  # Fallback default

    # Validate password is not empty
    if not password or not isinstance(password, str) or len(password) == 0:
        logger.warning(f"Invalid password for demo user {email}")
        return None, None

    return email, password


async def _create_demo_user_from_config(
    db,
    slug_id: str,
    collection_name: str,
    email: str,
    password: str,
    role: str,
    extra_data: Dict[str, Any],
    link_to_platform: bool,
    mongo_uri: Optional[str],
    db_name: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Create a demo user from configuration."""
    import datetime

    # Hash password with bcrypt
    try:
        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    except (ValueError, TypeError, AttributeError) as e:
        logger.error(
            f"Error hashing password for demo user {email}: {e}", exc_info=True
        )
        return None

    # Create user document
    user_doc = {
        "email": email,
        "password_hash": password_hash,
        "role": role,
        "is_demo": True,
        "date_created": datetime.datetime.utcnow(),
    }

    # Link to platform demo if requested
    if link_to_platform and mongo_uri and db_name:
        try:
            platform_demo = await get_platform_demo_user(mongo_uri, db_name)
            if platform_demo and platform_demo.get("email") == email:
                user_doc["platform_user_id"] = platform_demo.get("platform_user_id")
        except (
            ValueError,
            TypeError,
            AttributeError,
            RuntimeError,
            ConnectionError,
            KeyError,
        ) as e:
            logger.warning(f"Could not link platform demo for {email}: {e}")

    # Handle custom _id from extra_data if provided
    custom_id = extra_data.pop("_id", None)

    # Add extra data
    user_doc.update(extra_data)

    # Insert user
    try:
        collection = getattr(db, collection_name)

        if custom_id:
            user_doc["_id"] = custom_id
            try:
                result = await collection.insert_one(user_doc)
            except (
                OperationFailure,
                ConnectionFailure,
                ServerSelectionTimeoutError,
                ValueError,
                TypeError,
            ) as e:
                # If user already exists with this _id, just fetch it
                if isinstance(e, OperationFailure) and (
                    "duplicate" in str(e).lower() or "E11000" in str(e)
                ):
                    existing = await collection.find_one({"_id": custom_id})
                    if existing:
                        logger.info(
                            f"Demo user {email} already exists with "
                            f"_id={custom_id} for {slug_id}"
                        )
                        return existing
                raise
        else:
            result = await collection.insert_one(user_doc)

        user_doc["_id"] = result.inserted_id if not custom_id else custom_id
        user_doc["app_user_id"] = str(user_doc["_id"])
        logger.info(
            f"Created demo user {email} for {slug_id} with _id={user_doc['_id']}"
        )
        return user_doc
    except (
        OperationFailure,
        ConnectionFailure,
        ServerSelectionTimeoutError,
        ValueError,
        TypeError,
        AttributeError,
        RuntimeError,
    ) as e:
        logger.error(
            f"Error creating demo user {email} for {slug_id}: {e}",
            exc_info=True,
        )
        return None


async def ensure_demo_users_exist(
    db,
    slug_id: str,
    config: Optional[Dict[str, Any]] = None,
    mongo_uri: Optional[str] = None,
    db_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Intelligently ensure demo users exist for a app based on manifest configuration.

    This function:
    1. Checks manifest auth.users.demo_users configuration
    2. If demo_users array is empty, automatically uses platform demo user if available
    3. Creates app-specific demo users as needed
    4. Links to platform demo user if configured

    Args:
        db: Database wrapper
        slug_id: App slug
        config: Optional app config (fetches if not provided)
        mongo_uri: Optional MongoDB URI for accessing platform demo user
        db_name: Optional database name for accessing platform demo user

    Returns:
        List of demo user dicts that were created or already exist
    """
    if config is None:
        # Config not provided - cannot create demo users without config
        logger.warning(
            f"Config not provided for {slug_id}, skipping demo user creation. "
            f"Pass config explicitly when calling from actor context."
        )
        return []

    if not config:
        return []

    auth = config.get("auth", {})
    users_config = auth.get("users", {})
    if not users_config.get("enabled", False):
        return []

    # Check seed strategy
    seed_strategy = users_config.get("demo_user_seed_strategy", "auto")
    if seed_strategy == "disabled":
        return []

    collection_name = users_config.get("collection_name", "users")
    auto_link = users_config.get("auto_link_platform_demo", True)
    demo_users_config = users_config.get("demo_users", [])

    created_users = []

    # Auto-link platform demo user if enabled
    if auto_link and seed_strategy == "auto" and mongo_uri and db_name:
        platform_user = await _link_platform_demo_user(
            db, slug_id, collection_name, mongo_uri, db_name
        )
        if platform_user:
            created_users.append(platform_user)

    # Process configured demo_users
    for demo_user_config in demo_users_config:
        try:
            # Validate config structure
            validated_config, error = _validate_demo_user_config(
                demo_user_config, slug_id
            )
            if not validated_config:
                if error:
                    logger.warning(error)
                continue

            # Resolve email and password
            email, password = await _resolve_demo_user_email_password(
                validated_config["email"],
                validated_config["password"],
                mongo_uri,
                db_name,
                slug_id,
            )
            if not email or not password:
                continue

            # Check if user already exists
            collection = getattr(db, collection_name)
            existing = await collection.find_one({"email": email})
            if existing:
                created_users.append(existing)
                continue

            if not validated_config["auto_create"]:
                continue

            # Create user from config
            user = await _create_demo_user_from_config(
                db,
                slug_id,
                collection_name,
                email,
                password,
                validated_config["role"],
                validated_config["extra_data"],
                validated_config["link_to_platform"],
                mongo_uri,
                db_name,
            )
            if user:
                created_users.append(user)

        except (
            ValueError,
            TypeError,
            AttributeError,
            RuntimeError,
            ConnectionError,
            KeyError,
        ) as e:
            logger.error(
                f"Error processing demo user config for {slug_id}: {e}",
                exc_info=True,
            )

    return created_users


async def get_or_create_demo_user_for_request(
    request: Request,
    slug_id: str,
    db,
    config: Optional[Dict[str, Any]] = None,
    get_app_config_func: Optional[
        Callable[[Request, str, Dict], Awaitable[Dict]]
    ] = None,
) -> Optional[Dict[str, Any]]:
    """
    Get or create a demo user for the current request context.

    This is a convenience function that intelligently:
    1. Checks if platform demo user is accessing the app
    2. Creates/links app demo user if needed
    3. Returns the app demo user

    Args:
        request: FastAPI Request object
        slug_id: App slug
        db: Database wrapper
        config: Optional app config
        get_app_config_func: Optional callable to get app config

    Returns:
        Demo user dict if available, None otherwise
    """
    # Check if platform user is demo user
    try:
        from .dependencies import get_current_user_from_request

        platform_user = await get_current_user_from_request(request)

        if platform_user:
            from ..config import DEMO_EMAIL_DEFAULT

            if platform_user.get("email") == DEMO_EMAIL_DEFAULT:
                # Platform demo user accessing app - ensure app demo exists
                if config is None:
                    if not get_app_config_func:
                        raise ValueError(
                            "config or get_app_config_func must be provided. "
                            "Provide either the config dict directly or a callable that returns it."
                        )
                    config = await get_app_config_func(request, slug_id, {"auth": 1})

                if config:
                    auth = config.get("auth", {})
                    users_config = auth.get("users", {})
                    if users_config.get("enabled", False):
                        collection_name = users_config.get("collection_name", "users")

                        # Check if app demo user exists
                        # Use getattr to access collection (works with ScopedMongoWrapper and AppDB)
                        collection = getattr(db, collection_name)
                        app_demo = await collection.find_one(
                            {"email": DEMO_EMAIL_DEFAULT}
                        )

                        if app_demo:
                            app_demo["app_user_id"] = str(app_demo["_id"])
                            return app_demo

                        # Try to create it
                        try:
                            from ..config import DB_NAME, MONGO_URI

                            await ensure_demo_users_exist(
                                db, slug_id, config, MONGO_URI, DB_NAME
                            )
                            # Use getattr to access collection (works with
                            # ScopedMongoWrapper and AppDB)
                            collection = getattr(db, collection_name)
                            app_demo = await collection.find_one(
                                {"email": DEMO_EMAIL_DEFAULT}
                            )
                            if app_demo:
                                app_demo["app_user_id"] = str(app_demo["_id"])
                                return app_demo
                        except (
                            ValueError,
                            TypeError,
                            AttributeError,
                            RuntimeError,
                            ConnectionError,
                            KeyError,
                        ) as e:
                            logger.warning(f"Could not auto-create demo user: {e}")
    except (
        ValueError,
        TypeError,
        AttributeError,
        RuntimeError,
        ConnectionError,
        KeyError,
    ) as e:
        logger.debug(f"Could not check platform demo user: {e}")

    return None


async def get_or_create_demo_user(
    db,
    slug_id: str,
    config: Dict[str, Any],
    mongo_uri: Optional[str] = None,
    db_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Get or create a demo user for an app.

    This function intelligently finds or creates a demo user based on auth.users configuration:
    1. Checks demo_users array for configured demo users
    2. Falls back to platform demo user if available and auto_link_platform_demo is true
    3. Creates the demo user if it doesn't exist

    Args:
        db: Database wrapper
        slug_id: App slug
        config: Experiment config (must contain auth.users block)
        mongo_uri: Optional MongoDB URI for accessing platform demo user
        db_name: Optional database name for accessing platform demo user

    Returns:
        Demo user dict if found/created, None otherwise
    """
    auth = config.get("auth", {})
    users_config = auth.get("users", {})
    if not users_config.get("enabled", False):
        return None

    collection_name = users_config.get("collection_name", "users")

    # First, try to ensure demo users exist (will create if needed)
    try:
        logger.debug(
            f"get_or_create_demo_user: Ensuring demo users exist for '{slug_id}' "
            f"(mongo_uri={'provided' if mongo_uri else 'not provided'}, "
            f"db_name={'provided' if db_name else 'not provided'})"
        )

        demo_users = await ensure_demo_users_exist(
            db, slug_id, config, mongo_uri, db_name
        )

        if demo_users and len(demo_users) > 0:
            # Return the first demo user (usually the primary one)
            demo_user = demo_users[0]
            if isinstance(demo_user, dict):
                demo_user["app_user_id"] = str(demo_user.get("_id"))
            logger.info(
                f"get_or_create_demo_user: Found/created {len(demo_users)} "
                f"demo user(s) for '{slug_id}'"
            )
            return demo_user
        else:
            logger.warning(
                f"get_or_create_demo_user: ensure_demo_users_exist returned "
                f"empty list for '{slug_id}'"
            )
    except (
        ValueError,
        TypeError,
        AttributeError,
        RuntimeError,
        ConnectionError,
        KeyError,
    ) as e:
        logger.error(
            f"get_or_create_demo_user: Could not ensure demo users exist for '{slug_id}': {e}",
            exc_info=True,
        )

    # Fallback: Try to find any demo user in the collection
    # Look for users with "demo" in email or role
    try:
        from ..config import DEMO_EMAIL_DEFAULT

        # Use getattr to access collection (works with ScopedMongoWrapper and AppDB)
        collection = getattr(db, collection_name)
        demo_user = await collection.find_one(
            {
                "$or": [
                    {"email": DEMO_EMAIL_DEFAULT},
                    {"email": {"$regex": "^demo@", "$options": "i"}},
                    {"role": {"$in": ["demo", "Demo", "DEMO"]}},
                ]
            }
        )

        if demo_user:
            demo_user["app_user_id"] = str(demo_user.get("_id"))
            return demo_user
    except (
        ValueError,
        TypeError,
        AttributeError,
        RuntimeError,
        ConnectionError,
        KeyError,
    ) as e:
        logger.debug(f"Could not find demo user: {e}")

    return None


async def ensure_demo_users_for_actor(
    db, slug_id: str, mongo_uri: str, db_name: str
) -> List[Dict[str, Any]]:
    """
    Convenience function for actors to ensure demo users exist.

    This function reads manifest.json from the app directory and automatically
    ensures demo users are created based on auth.users configuration.

    This is the recommended way for actors to call ensure_demo_users_exist during
    initialization, as it automatically loads the manifest config.

    Note: If the manifest file is not accessible via filesystem (e.g., not in the expected
    location), this function gracefully handles the error and returns an empty list.
    The platform will still auto-detect and link platform demo users on first access
    via request context.

    Example usage in actor.initialize():
        from mdb_engine.auth import ensure_demo_users_for_actor
        demo_users = await ensure_demo_users_for_actor(
            db=self.db,
            slug_id=self.write_scope,
            mongo_uri=self.mongo_uri,
            db_name=self.db_name
        )
        if demo_users:
            logger.info(f"Ensured {len(demo_users)} demo user(s) exist")

    Args:
        db: Database wrapper
        slug_id: App slug
        mongo_uri: MongoDB connection URI
        db_name: Database name

    Returns:
        List of demo user dicts that were created or already exist
    """
    try:
        import json
        from pathlib import Path

        # Try to load manifest.json from multiple possible locations
        # First try: relative to users.py location
        base_dir = Path(__file__).resolve().parent.parent
        apps_dir = base_dir / "apps" / slug_id
        manifest_path = apps_dir / "manifest.json"

        # Alternative: try current working directory
        if not manifest_path.exists():
            try:
                from pathlib import Path

                cwd = Path.cwd()
                alt_path = cwd / "apps" / slug_id / "manifest.json"
                if alt_path.exists():
                    manifest_path = alt_path
                    logger.debug(f"Using manifest from alternative path: {alt_path}")
            except OSError:
                # Type 2: Recoverable - if cwd() fails, just skip alternative path
                pass

        if not manifest_path.exists():
            logger.warning(
                f"Manifest not found at {manifest_path} for {slug_id}. "
                f"Demo users will be auto-created on first access via request context."
            )
            return []

        try:
            with open(manifest_path, "r") as f:
                config = json.load(f)
        except json.JSONDecodeError as e:
            logger.error(
                f"Invalid JSON in manifest.json for {slug_id}: {e}", exc_info=True
            )
            return []

        # Ensure demo users exist
        return await ensure_demo_users_exist(db, slug_id, config, mongo_uri, db_name)

    except FileNotFoundError:
        logger.debug(
            f"Manifest file not accessible for {slug_id}. "
            f"Demo users will be auto-created on first access."
        )
        return []
    except PermissionError as e:
        logger.warning(f"Permission denied reading manifest for {slug_id}: {e}")
        return []
    except (
        ValueError,
        TypeError,
        AttributeError,
        RuntimeError,
        ConnectionError,
        KeyError,
        PermissionError,
    ) as e:
        logger.error(
            f"Error ensuring demo users for actor {slug_id}: {e}", exc_info=True
        )
        return []


async def sync_app_user_to_casbin(
    user: Dict[str, Any],
    authz_provider,
    role: Optional[str] = None,
    app_slug: Optional[str] = None,
) -> bool:
    """
    Sync app-level user to Casbin by assigning a role.

    This function automatically assigns a Casbin role to an app-level user,
    linking the app user ID to the role in Casbin's grouping rules.

    Args:
        user: App-level user dict (must have _id or app_user_id)
        authz_provider: AuthorizationProvider instance (should be CasbinAdapter)
        role: Role name to assign (if None, uses user.get('role') or 'user')
        app_slug: App slug for logging (optional)

    Returns:
        True if role was assigned successfully, False otherwise
    """
    try:
        # Check if provider is CasbinAdapter
        if not hasattr(authz_provider, "_enforcer"):
            logger.debug(
                "sync_app_user_to_casbin: Provider is not CasbinAdapter, skipping"
            )
            return False

        enforcer = authz_provider._enforcer

        # Get user ID
        user_id = str(user.get("_id") or user.get("app_user_id", ""))
        if not user_id:
            logger.warning("sync_app_user_to_casbin: User has no _id or app_user_id")
            return False

        # Determine role
        if not role:
            role = user.get("role") or "user"

        # Create subject identifier (use app_user_id as subject)
        subject = user_id

        # Check if role assignment already exists
        existing_roles = await enforcer.get_roles_for_user(subject)
        if role in existing_roles:
            logger.debug(
                f"sync_app_user_to_casbin: User {subject} already has role {role}"
            )
            return True

        # Add grouping policy: user -> role
        added = await enforcer.add_grouping_policy(subject, role)

        if added:
            # Save policy to database
            await enforcer.save_policy()
            logger.info(
                f"sync_app_user_to_casbin: Assigned role '{role}' to user '{subject}'"
                + (f" in app '{app_slug}'" if app_slug else "")
            )
            return True
        else:
            logger.warning(
                f"sync_app_user_to_casbin: Failed to assign role '{role}' to user '{subject}'"
            )
            return False

    except (
        ImportError,
        AttributeError,
        TypeError,
        ValueError,
        RuntimeError,
        ConnectionError,
        KeyError,
    ) as e:
        logger.error(
            f"sync_app_user_to_casbin: Error syncing user to Casbin: {e}", exc_info=True
        )
        return False


def get_app_user_role(
    user: Dict[str, Any], config: Optional[Dict[str, Any]] = None
) -> str:
    """
    Determine Casbin role for app-level user.

    Args:
        user: Sub-auth user dict
        config: Optional app config (for default role)

    Returns:
        Role name (default: "user")
    """
    # Check user's role field
    role = user.get("role")
    if role:
        return str(role)

    # Check config for default role
    if config:
        auth_policy = config.get("auth_policy", {})
        authorization = auth_policy.get("authorization", {})
        default_roles = authorization.get("default_roles", [])
        if default_roles:
            # Use first default role as fallback
            return default_roles[0]

    # Final fallback
    return "user"
