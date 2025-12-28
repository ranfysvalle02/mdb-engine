"""
Authentication Utility Functions

High-level utility functions for common authentication flows.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import hashlib
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import bcrypt
from fastapi import Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from .cookie_utils import clear_auth_cookies, set_auth_cookies
from .dependencies import SECRET_KEY, get_session_manager, get_token_blacklist
from .jwt import generate_token_pair

logger = logging.getLogger(__name__)


def _detect_browser(user_agent: str) -> str:
    """Detect browser from user agent string."""
    if not user_agent:
        return "unknown"

    ua_lower = user_agent.lower()
    if "chrome" in ua_lower and "edg" not in ua_lower:
        return "chrome"
    if "firefox" in ua_lower:
        return "firefox"
    if "safari" in ua_lower and "chrome" not in ua_lower:
        return "safari"
    if "edg" in ua_lower:
        return "edge"
    if "opera" in ua_lower:
        return "opera"
    return "unknown"


def _detect_os_and_device_type(user_agent: str) -> Tuple[str, str]:
    """Detect OS and device type from user agent string."""
    if not user_agent:
        return "unknown", "desktop"

    ua_lower = user_agent.lower()
    if "windows" in ua_lower:
        return "windows", "desktop"
    if "mac" in ua_lower or "darwin" in ua_lower:
        return "macos", "desktop"
    if "linux" in ua_lower:
        return "linux", "desktop"
    if "android" in ua_lower:
        return "android", "mobile"
    if "iphone" in ua_lower:
        return "ios", "mobile"
    if "ipad" in ua_lower:
        return "ios", "tablet"
    return "unknown", "desktop"


def get_device_info(request: Request) -> Dict[str, Any]:
    """
    Extract device information from request.

    Args:
        request: FastAPI Request object

    Returns:
        Dictionary with device_id, user_agent, browser, OS, IP, device_type
    """
    user_agent = request.headers.get("user-agent", "")
    ip_address = request.client.host if request.client else None

    # Generate or get device ID from cookie
    device_id = request.cookies.get("device_id")
    if not device_id:
        device_id = str(uuid.uuid4())

    browser = _detect_browser(user_agent)
    os, device_type = _detect_os_and_device_type(user_agent)

    return {
        "device_id": device_id,
        "user_agent": user_agent,
        "browser": browser,
        "os": os,
        "ip_address": ip_address,
        "device_type": device_type,
    }


def validate_password_strength(
    password: str,
    min_length: Optional[int] = None,
    require_uppercase: Optional[bool] = None,
    require_lowercase: Optional[bool] = None,
    require_numbers: Optional[bool] = None,
    require_special: Optional[bool] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[str]]:
    """
    Validate password strength with configurable rules.

    Args:
        password: Password to validate
        min_length: Minimum password length (default: from config or 8)
        require_uppercase: Require uppercase letters (default: from config or True)
        require_lowercase: Require lowercase letters (default: from config or True)
        require_numbers: Require numbers (default: from config or True)
        require_special: Require special characters (default: from config or False)
        config: Optional password_policy config dict from manifest

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []

    if not password:
        return False, ["Password is required"]

    if config:
        min_length = (
            min_length if min_length is not None else config.get("min_length", 8)
        )
        require_uppercase = (
            require_uppercase
            if require_uppercase is not None
            else config.get("require_uppercase", True)
        )
        require_lowercase = (
            require_lowercase
            if require_lowercase is not None
            else config.get("require_lowercase", True)
        )
        require_numbers = (
            require_numbers
            if require_numbers is not None
            else config.get("require_numbers", True)
        )
        require_special = (
            require_special
            if require_special is not None
            else config.get("require_special", False)
        )
    else:
        min_length = min_length if min_length is not None else 8
        require_uppercase = require_uppercase if require_uppercase is not None else True
        require_lowercase = require_lowercase if require_lowercase is not None else True
        require_numbers = require_numbers if require_numbers is not None else True
        require_special = require_special if require_special is not None else False

    if len(password) < min_length:
        errors.append(f"Password must be at least {min_length} characters long")

    if require_uppercase and not re.search(r"[A-Z]", password):
        errors.append("Password must contain at least one uppercase letter")

    if require_lowercase and not re.search(r"[a-z]", password):
        errors.append("Password must contain at least one lowercase letter")

    if require_numbers and not re.search(r"\d", password):
        errors.append("Password must contain at least one number")

    if require_special and not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        errors.append("Password must contain at least one special character")

    return len(errors) == 0, errors


def generate_session_fingerprint(request: Request, device_id: str) -> str:
    """
    Generate a session fingerprint from request characteristics.

    Fingerprint is a hash of user-agent, IP address, device ID, and accept-language.
    Used to detect session hijacking and unauthorized access.

    Args:
        request: FastAPI Request object
        device_id: Device identifier

    Returns:
        SHA256 hash of fingerprint components as hex string
    """
    components = [
        request.headers.get("user-agent", ""),
        request.client.host if request.client else "",
        device_id,
        request.headers.get("accept-language", ""),
    ]
    fingerprint_string = "|".join(components)
    return hashlib.sha256(fingerprint_string.encode()).hexdigest()


async def login_user(
    request: Request,
    email: str,
    password: str,
    db,
    config: Optional[Dict[str, Any]] = None,
    remember_me: bool = False,
    redirect_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Handle user login with automatic token generation and cookie setting.

    Args:
        request: FastAPI Request object
        email: User email
        password: User password
        db: Database instance (top-level or app-specific)
        config: Optional token_management config from manifest
        remember_me: If True, extends token TTL (default: False)
        redirect_url: Optional redirect URL after login (default: "/dashboard")

    Returns:
        Dictionary with:
        - success: bool
        - user: user dict if successful
        - response: Response object with cookies set (if successful)
        - error: error message (if failed)
    """
    try:
        # Validate email format
        if not email or "@" not in email:
            return {"success": False, "error": "Invalid email format"}

        # Find user by email
        user = await db.users.find_one({"email": email})

        if not user:
            return {"success": False, "error": "Invalid email or password"}

        # Verify password
        password_hash = user.get("password_hash") or user.get("password")
        if not password_hash:
            return {"success": False, "error": "Invalid email or password"}

        # Check password (bcrypt only - plain text support removed for security)
        password_valid = False
        if isinstance(password_hash, bytes) or (
            isinstance(password_hash, str) and password_hash.startswith("$2b$")
        ):
            # Bcrypt hash
            if isinstance(password_hash, str):
                password_hash = password_hash.encode("utf-8")
            if isinstance(password, str):
                password_bytes = password.encode("utf-8")
            else:
                password_bytes = password

            try:
                password_valid = bcrypt.checkpw(password_bytes, password_hash)
            except (ValueError, TypeError, AttributeError) as e:
                logger.debug(f"Bcrypt check failed: {e}")
                password_valid = False
        else:
            # Password is not bcrypt hashed - reject for security
            logger.warning(
                f"User {email} has non-bcrypt password hash - password verification rejected"
            )
            password_valid = False

        if not password_valid:
            return {"success": False, "error": "Invalid email or password"}

        # Get device info
        device_info = get_device_info(request)

        # Prepare user data for token
        user_data = {
            "user_id": str(user["_id"]),
            "email": user["email"],
        }

        # Add role if present
        if "role" in user:
            user_data["role"] = user["role"]

        # Get token TTLs from config
        access_token_ttl = None
        refresh_token_ttl = None
        if config:
            access_token_ttl = config.get("access_token_ttl")
            refresh_token_ttl = config.get("refresh_token_ttl")
            if remember_me:
                # Extend refresh token TTL for remember me
                refresh_token_ttl = refresh_token_ttl * 2 if refresh_token_ttl else None

        # Generate token pair
        access_token, refresh_token, token_metadata = generate_token_pair(
            user_data,
            str(SECRET_KEY),
            device_info=device_info,
            access_token_ttl=access_token_ttl,
            refresh_token_ttl=refresh_token_ttl,
        )

        # Create session if session manager available
        session_mgr = await get_session_manager(request)
        if session_mgr:
            await session_mgr.create_session(
                user_id=user_data["email"],
                device_id=device_info["device_id"],
                refresh_jti=token_metadata.get("refresh_jti"),
                device_info=device_info,
                ip_address=device_info.get("ip_address"),
            )

        # Create response
        if redirect_url:
            response = RedirectResponse(url=redirect_url, status_code=302)
        else:
            response = JSONResponse(
                {
                    "success": True,
                    "user": {"email": user["email"], "user_id": str(user["_id"])},
                }
            )

        # Set cookies
        set_auth_cookies(
            response,
            access_token,
            refresh_token,
            request=request,
            config=config,
            access_token_ttl=access_token_ttl,
            refresh_token_ttl=refresh_token_ttl,
        )

        # Set device_id cookie
        response.set_cookie(
            key="device_id",
            value=device_info["device_id"],
            max_age=31536000,  # 1 year
            httponly=False,  # Allow JS access for device tracking
            secure=request.url.scheme == "https" if request else False,
            samesite="lax",
        )

        return {
            "success": True,
            "user": user,
            "response": response,
            "token_metadata": token_metadata,
        }

    except (
        ValueError,
        TypeError,
        AttributeError,
        KeyError,
        RuntimeError,
        ConnectionError,
    ) as e:
        logger.error(f"Error in login_user: {e}", exc_info=True)
        return {"success": False, "error": "Login failed. Please try again."}


def _validate_email_format(email: str) -> bool:
    """Validate basic email format."""
    return bool(email and "@" in email and "." in email)


def _get_password_policy_from_config(
    request: Request, config: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Get password policy from config or request."""
    if config:
        security = config.get("security", {})
        return security.get("password_policy")
    if hasattr(request, "app"):
        from .config_helpers import get_password_policy

        return get_password_policy(request)
    return None


async def _create_user_document(
    email: str, password: str, extra_data: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Create user document with hashed password."""
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    user_doc = {
        "email": email,
        "password_hash": password_hash,
        "role": "user",
        "date_created": datetime.utcnow(),
    }
    if extra_data:
        user_doc.update(extra_data)
    return user_doc


def _create_registration_response(
    user_doc: Dict[str, Any], redirect_url: Optional[str]
) -> Response:
    """Create response for registration."""
    if redirect_url:
        return RedirectResponse(url=redirect_url, status_code=302)
    return JSONResponse(
        {
            "success": True,
            "user": {
                "email": user_doc["email"],
                "user_id": str(user_doc["_id"]),
            },
        }
    )


async def register_user(
    request: Request,
    email: str,
    password: str,
    db,
    config: Optional[Dict[str, Any]] = None,
    extra_data: Optional[Dict[str, Any]] = None,
    redirect_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Handle user registration with automatic token generation.

    Args:
        request: FastAPI Request object
        email: User email
        password: User password
        db: Database instance
        config: Optional token_management config from manifest
        extra_data: Optional extra user data to store
        redirect_url: Optional redirect URL after registration

    Returns:
        Dictionary with success, user, response, or error
    """
    try:
        if not _validate_email_format(email):
            return {"success": False, "error": "Invalid email format"}

        password_policy = _get_password_policy_from_config(request, config)
        is_valid, errors = validate_password_strength(password, config=password_policy)
        if not is_valid:
            return {"success": False, "error": "; ".join(errors)}

        existing = await db.users.find_one({"email": email})
        if existing:
            return {"success": False, "error": "User with this email already exists"}

        user_doc = await _create_user_document(email, password, extra_data)
        result = await db.users.insert_one(user_doc)
        user_doc["_id"] = result.inserted_id

        device_info = get_device_info(request)
        user_data = {
            "user_id": str(user_doc["_id"]),
            "email": user_doc["email"],
            "role": user_doc.get("role", "user"),
        }

        access_token, refresh_token, token_metadata = generate_token_pair(
            user_data, str(SECRET_KEY), device_info=device_info
        )

        session_mgr = await get_session_manager(request)
        if session_mgr:
            await session_mgr.create_session(
                user_id=user_data["email"],
                device_id=device_info["device_id"],
                refresh_jti=token_metadata.get("refresh_jti"),
                device_info=device_info,
                ip_address=device_info.get("ip_address"),
            )

        response = _create_registration_response(user_doc, redirect_url)
        set_auth_cookies(
            response, access_token, refresh_token, request=request, config=config
        )

        response.set_cookie(
            key="device_id",
            value=device_info["device_id"],
            max_age=31536000,
            httponly=False,
            secure=request.url.scheme == "https" if request else False,
            samesite="lax",
        )

        return {
            "success": True,
            "user": user_doc,
            "response": response,
            "token_metadata": token_metadata,
        }

    except (
        ValueError,
        TypeError,
        AttributeError,
        KeyError,
        RuntimeError,
        ConnectionError,
    ) as e:
        logger.error(f"Error in register_user: {e}", exc_info=True)
        return {"success": False, "error": "Registration failed. Please try again."}


async def _get_user_id_from_request(
    request: Request, user_id: Optional[str]
) -> Optional[str]:
    """Extract user_id from request if not provided."""
    if user_id:
        return user_id

    from .dependencies import get_current_user_from_request

    user = await get_current_user_from_request(request)
    if user:
        return user.get("email") or user.get("user_id")
    return None


async def _revoke_token_from_cookie(
    request: Request,
    cookie_name: str,
    blacklist: Any,
    user_id: str,
    reason: str = "logout",
) -> None:
    """Revoke a token from a cookie if present."""
    token = request.cookies.get(cookie_name)
    if not token:
        return

    from .jwt import extract_token_metadata

    metadata = extract_token_metadata(token, str(SECRET_KEY))
    if metadata:
        jti = metadata.get("jti")
        if jti:
            await blacklist.revoke_token(jti, user_id=user_id, reason=reason)


async def _revoke_all_tokens(request: Request, user_id: str) -> None:
    """Revoke all tokens (access and refresh) for a user."""
    blacklist = await get_token_blacklist(request)
    if not blacklist:
        return

    await _revoke_token_from_cookie(request, "token", blacklist, user_id)
    await _revoke_token_from_cookie(request, "refresh_token", blacklist, user_id)


async def _revoke_session(request: Request) -> None:
    """Revoke session using refresh token."""
    session_mgr = await get_session_manager(request)
    if not session_mgr:
        return

    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        return

    from .jwt import extract_token_metadata

    metadata = extract_token_metadata(refresh_token, str(SECRET_KEY))
    if metadata:
        refresh_jti = metadata.get("jti")
        if refresh_jti:
            await session_mgr.revoke_session_by_refresh_token(refresh_jti)


async def logout_user(
    request: Request, response: Response, user_id: Optional[str] = None
) -> Response:
    """
    Handle user logout with token revocation and cookie clearing.

    Args:
        request: FastAPI Request object
        response: Response object to modify
        user_id: Optional user ID (extracted from token if not provided)

    Returns:
        Response with cleared cookies
    """
    try:
        user_id = await _get_user_id_from_request(request, user_id)

        if user_id:
            await _revoke_all_tokens(request, user_id)

        await _revoke_session(request)
        clear_auth_cookies(response, request)

        return response

    except (
        ValueError,
        TypeError,
        AttributeError,
        KeyError,
        RuntimeError,
        ConnectionError,
    ) as e:
        logger.error(f"Error in logout_user: {e}", exc_info=True)
        # Still clear cookies even if revocation fails
        clear_auth_cookies(response, request)
        return response
