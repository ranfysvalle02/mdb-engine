"""
Authentication Utility Functions

High-level utility functions for common authentication flows.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import re
import uuid
import hashlib
import logging
import bcrypt
from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime
from fastapi import Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from .jwt import generate_token_pair
from .cookie_utils import set_auth_cookies, clear_auth_cookies
from .session_manager import SessionManager
from .token_store import TokenBlacklist
from .dependencies import SECRET_KEY, get_token_blacklist, get_session_manager

logger = logging.getLogger(__name__)


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
    
    # Parse user agent (simple parsing)
    browser = "unknown"
    os = "unknown"
    device_type = "desktop"
    
    if user_agent:
        ua_lower = user_agent.lower()
        
        # Browser detection
        if "chrome" in ua_lower and "edg" not in ua_lower:
            browser = "chrome"
        elif "firefox" in ua_lower:
            browser = "firefox"
        elif "safari" in ua_lower and "chrome" not in ua_lower:
            browser = "safari"
        elif "edg" in ua_lower:
            browser = "edge"
        elif "opera" in ua_lower:
            browser = "opera"
        
        # OS detection
        if "windows" in ua_lower:
            os = "windows"
        elif "mac" in ua_lower or "darwin" in ua_lower:
            os = "macos"
        elif "linux" in ua_lower:
            os = "linux"
        elif "android" in ua_lower:
            os = "android"
            device_type = "mobile"
        elif "iphone" in ua_lower or "ipad" in ua_lower:
            os = "ios"
            device_type = "mobile" if "iphone" in ua_lower else "tablet"
    
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
    config: Optional[Dict[str, Any]] = None
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
        min_length = min_length if min_length is not None else config.get("min_length", 8)
        require_uppercase = require_uppercase if require_uppercase is not None else config.get("require_uppercase", True)
        require_lowercase = require_lowercase if require_lowercase is not None else config.get("require_lowercase", True)
        require_numbers = require_numbers if require_numbers is not None else config.get("require_numbers", True)
        require_special = require_special if require_special is not None else config.get("require_special", False)
    else:
        min_length = min_length if min_length is not None else 8
        require_uppercase = require_uppercase if require_uppercase is not None else True
        require_lowercase = require_lowercase if require_lowercase is not None else True
        require_numbers = require_numbers if require_numbers is not None else True
        require_special = require_special if require_special is not None else False
    
    if len(password) < min_length:
        errors.append(f"Password must be at least {min_length} characters long")
    
    if require_uppercase and not re.search(r'[A-Z]', password):
        errors.append("Password must contain at least one uppercase letter")
    
    if require_lowercase and not re.search(r'[a-z]', password):
        errors.append("Password must contain at least one lowercase letter")
    
    if require_numbers and not re.search(r'\d', password):
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
    redirect_url: Optional[str] = None
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
            return {
                "success": False,
                "error": "Invalid email format"
            }
        
        # Find user by email
        user = await db.users.find_one({"email": email})
        
        if not user:
            return {
                "success": False,
                "error": "Invalid email or password"
            }
        
        # Verify password
        password_hash = user.get("password_hash") or user.get("password")
        if not password_hash:
            return {
                "success": False,
                "error": "Invalid email or password"
            }
        
        # Check password (bcrypt only - plain text support removed for security)
        password_valid = False
        if isinstance(password_hash, bytes) or (isinstance(password_hash, str) and password_hash.startswith("$2b$")):
            # Bcrypt hash
            if isinstance(password_hash, str):
                password_hash = password_hash.encode("utf-8")
            if isinstance(password, str):
                password_bytes = password.encode("utf-8")
            else:
                password_bytes = password
            
            try:
                password_valid = bcrypt.checkpw(password_bytes, password_hash)
            except Exception as e:
                logger.debug(f"Bcrypt check failed: {e}")
                password_valid = False
        else:
            # Password is not bcrypt hashed - reject for security
            logger.warning(f"User {email} has non-bcrypt password hash - password verification rejected")
            password_valid = False
        
        if not password_valid:
            return {
                "success": False,
                "error": "Invalid email or password"
            }
        
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
            SECRET_KEY,
            device_info=device_info,
            access_token_ttl=access_token_ttl,
            refresh_token_ttl=refresh_token_ttl
        )
        
        # Create session if session manager available
        session_mgr = await get_session_manager(request)
        if session_mgr:
            await session_mgr.create_session(
                user_id=user_data["email"],
                device_id=device_info["device_id"],
                refresh_jti=token_metadata.get("refresh_jti"),
                device_info=device_info,
                ip_address=device_info.get("ip_address")
            )
        
        # Create response
        if redirect_url:
            response = RedirectResponse(url=redirect_url, status_code=302)
        else:
            response = JSONResponse({"success": True, "user": {"email": user["email"], "user_id": str(user["_id"])}})
        
        # Set cookies
        set_auth_cookies(
            response,
            access_token,
            refresh_token,
            request=request,
            config=config,
            access_token_ttl=access_token_ttl,
            refresh_token_ttl=refresh_token_ttl
        )
        
        # Set device_id cookie
        response.set_cookie(
            key="device_id",
            value=device_info["device_id"],
            max_age=31536000,  # 1 year
            httponly=False,  # Allow JS access for device tracking
            secure=request.url.scheme == "https" if request else False,
            samesite="lax"
        )
        
        return {
            "success": True,
            "user": user,
            "response": response,
            "token_metadata": token_metadata
        }
        
    except Exception as e:
        logger.error(f"Error in login_user: {e}", exc_info=True)
        return {
            "success": False,
            "error": "Login failed. Please try again."
        }


async def register_user(
    request: Request,
    email: str,
    password: str,
    db,
    config: Optional[Dict[str, Any]] = None,
    extra_data: Optional[Dict[str, Any]] = None,
    redirect_url: Optional[str] = None
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
        # Validate email format
        if not email or "@" not in email or "." not in email:
            return {
                "success": False,
                "error": "Invalid email format"
            }
        
        # Get password policy from config
        password_policy = None
        if config:
            security = config.get("security", {})
            password_policy = security.get("password_policy")
        elif hasattr(request, 'app'):
            from .config_helpers import get_password_policy
            password_policy = get_password_policy(request)
        
        # Validate password strength
        is_valid, errors = validate_password_strength(password, config=password_policy)
        if not is_valid:
            return {
                "success": False,
                "error": "; ".join(errors)
            }
        
        # Check if user already exists
        existing = await db.users.find_one({"email": email})
        if existing:
            return {
                "success": False,
                "error": "User with this email already exists"
            }
        
        # Hash password
        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
        
        # Create user document
        user_doc = {
            "email": email,
            "password_hash": password_hash,
            "role": "user",
            "date_created": datetime.utcnow(),
        }
        
        if extra_data:
            user_doc.update(extra_data)
        
        # Insert user
        result = await db.users.insert_one(user_doc)
        user_doc["_id"] = result.inserted_id
        
        # Get device info
        device_info = get_device_info(request)
        
        # Prepare user data for token
        user_data = {
            "user_id": str(user_doc["_id"]),
            "email": user_doc["email"],
            "role": user_doc.get("role", "user"),
        }
        
        # Generate token pair
        access_token, refresh_token, token_metadata = generate_token_pair(
            user_data,
            SECRET_KEY,
            device_info=device_info
        )
        
        # Create session if session manager available
        session_mgr = await get_session_manager(request)
        if session_mgr:
            await session_mgr.create_session(
                user_id=user_data["email"],
                device_id=device_info["device_id"],
                refresh_jti=token_metadata.get("refresh_jti"),
                device_info=device_info,
                ip_address=device_info.get("ip_address")
            )
        
        # Create response
        if redirect_url:
            response = RedirectResponse(url=redirect_url, status_code=302)
        else:
            response = JSONResponse({"success": True, "user": {"email": user_doc["email"], "user_id": str(user_doc["_id"])}})
        
        # Set cookies
        set_auth_cookies(
            response,
            access_token,
            refresh_token,
            request=request,
            config=config
        )
        
        # Set device_id cookie
        response.set_cookie(
            key="device_id",
            value=device_info["device_id"],
            max_age=31536000,
            httponly=False,
            secure=request.url.scheme == "https" if request else False,
            samesite="lax"
        )
        
        return {
            "success": True,
            "user": user_doc,
            "response": response,
            "token_metadata": token_metadata
        }
        
    except Exception as e:
        logger.error(f"Error in register_user: {e}", exc_info=True)
        return {
            "success": False,
            "error": "Registration failed. Please try again."
        }


async def logout_user(
    request: Request,
    response: Response,
    user_id: Optional[str] = None
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
        # Get user from token if not provided
        if not user_id:
            from .dependencies import get_current_user_from_request
            user = await get_current_user_from_request(request)
            if user:
                user_id = user.get("email") or user.get("user_id")
        
        # Revoke tokens if blacklist available
        blacklist = await get_token_blacklist(request)
        if blacklist and user_id:
            # Get token metadata
            token = request.cookies.get("token")
            if token:
                from .jwt import extract_token_metadata
                metadata = extract_token_metadata(token, SECRET_KEY)
                if metadata:
                    jti = metadata.get("jti")
                    if jti:
                        await blacklist.revoke_token(jti, user_id=user_id, reason="logout")
            
            # Revoke refresh token
            refresh_token = request.cookies.get("refresh_token")
            if refresh_token:
                from .jwt import extract_token_metadata
                metadata = extract_token_metadata(refresh_token, SECRET_KEY)
                if metadata:
                    jti = metadata.get("jti")
                    if jti:
                        await blacklist.revoke_token(jti, user_id=user_id, reason="logout")
        
        # Revoke session if session manager available
        session_mgr = await get_session_manager(request)
        if session_mgr:
            refresh_token = request.cookies.get("refresh_token")
            if refresh_token:
                from .jwt import extract_token_metadata
                metadata = extract_token_metadata(refresh_token, SECRET_KEY)
                if metadata:
                    refresh_jti = metadata.get("jti")
                    if refresh_jti:
                        await session_mgr.revoke_session_by_refresh_token(refresh_jti)
        
        # Clear cookies
        clear_auth_cookies(response, request)
        
        return response
        
    except Exception as e:
        logger.error(f"Error in logout_user: {e}", exc_info=True)
        # Still clear cookies even if revocation fails
        clear_auth_cookies(response, request)
        return response

