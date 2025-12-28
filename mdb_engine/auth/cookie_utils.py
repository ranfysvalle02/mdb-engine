"""
Cookie Security Utilities

Provides secure cookie configuration helpers based on manifest settings and environment.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import logging
import os
from typing import Any, Dict, Optional

from fastapi import Request

logger = logging.getLogger(__name__)


def get_secure_cookie_settings(
    request: Request, config: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Get secure cookie settings based on manifest config and request environment.

    Args:
        request: FastAPI Request object
        config: Optional token_management config from manifest (if None, uses defaults)

    Returns:
        Dictionary of cookie settings for FastAPI response.set_cookie()
    """
    # Default settings
    secure = False
    httponly = True
    samesite = "lax"

    # Get security config from token_management
    if config:
        security = config.get("security", {})

        # HttpOnly flag
        httponly = security.get("cookie_httponly", True)

        # SameSite flag
        samesite_str = security.get("cookie_samesite", "lax")
        samesite = samesite_str.lower()

        # Secure flag - determine based on config and environment
        cookie_secure = security.get("cookie_secure", "auto")

        if cookie_secure == "auto":
            # Auto-detect: secure if HTTPS or production environment
            is_https = request.url.scheme == "https"
            is_production = (
                os.getenv("G_NOME_ENV") == "production"
                or os.getenv("ENVIRONMENT") == "production"
            )
            secure = is_https or is_production
        elif cookie_secure == "true":
            secure = True
        else:
            secure = False
    else:
        # No config - use environment-based defaults
        is_https = request.url.scheme == "https"
        is_production = (
            os.getenv("G_NOME_ENV") == "production"
            or os.getenv("ENVIRONMENT") == "production"
        )
        secure = is_https or is_production

    return {
        "httponly": httponly,
        "secure": secure,
        "samesite": samesite,
    }


def set_auth_cookies(
    response,
    access_token: str,
    refresh_token: Optional[str] = None,
    request: Optional[Request] = None,
    config: Optional[Dict[str, Any]] = None,
    access_token_ttl: Optional[int] = None,
    refresh_token_ttl: Optional[int] = None,
):
    """
    Set authentication cookies on a response with secure settings.

    Args:
        response: FastAPI Response object
        access_token: Access token to set in cookie
        refresh_token: Optional refresh token to set in cookie
        request: Optional Request object for environment detection
        config: Optional token_management config from manifest
        access_token_ttl: Optional access token TTL in seconds (from config if not provided)
        refresh_token_ttl: Optional refresh token TTL in seconds (from config if not provided)
    """
    # Get cookie settings
    if request:
        cookie_settings = get_secure_cookie_settings(request, config)
    else:
        cookie_settings = {
            "httponly": True,
            "secure": os.getenv("G_NOME_ENV") == "production",
            "samesite": "lax",
        }

    # Get TTLs
    if access_token_ttl is None and config:
        access_token_ttl = config.get("access_token_ttl", 900)
    elif access_token_ttl is None:
        access_token_ttl = 900  # Default 15 minutes

    if refresh_token_ttl is None and config:
        refresh_token_ttl = config.get("refresh_token_ttl", 604800)
    elif refresh_token_ttl is None:
        refresh_token_ttl = 604800  # Default 7 days

    # Set access token cookie
    response.set_cookie(
        key="token", value=access_token, max_age=access_token_ttl, **cookie_settings
    )

    # Set refresh token cookie if provided
    if refresh_token:
        response.set_cookie(
            key="refresh_token",
            value=refresh_token,
            max_age=refresh_token_ttl,
            **cookie_settings,
        )


def clear_auth_cookies(response, request: Optional[Request] = None):
    """
    Clear authentication cookies from response.

    Args:
        response: FastAPI Response object
        request: Optional Request object for environment detection
    """
    # Get cookie settings for samesite (needed for deletion)
    if request:
        cookie_settings = get_secure_cookie_settings(request)
        samesite = cookie_settings.get("samesite", "lax")
        secure = cookie_settings.get("secure", False)
    else:
        samesite = "lax"
        secure = os.getenv("G_NOME_ENV") == "production"

    # Delete access token cookie
    response.delete_cookie(key="token", httponly=True, secure=secure, samesite=samesite)

    # Delete refresh token cookie
    response.delete_cookie(
        key="refresh_token", httponly=True, secure=secure, samesite=samesite
    )
