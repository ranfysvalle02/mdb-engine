"""
Security Middleware

Middleware for enforcing security settings from manifest configuration.

This module is part of MDB_ENGINE - MongoDB Engine.

Security Features:
    - HTTPS enforcement in production
    - HSTS (HTTP Strict Transport Security) header
    - Security headers (X-Content-Type-Options, X-Frame-Options, etc.)
    - CSRF token generation (legacy, prefer CSRFMiddleware for new apps)
"""

import logging
import os
import secrets
from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# Default HSTS settings
DEFAULT_HSTS_MAX_AGE = 31536000  # 1 year in seconds


def _is_production() -> bool:
    """Check if we're running in production environment."""
    return (
        os.getenv("MDB_ENGINE_ENV", "").lower() == "production"
        or os.getenv("ENVIRONMENT", "").lower() == "production"
        or os.getenv("G_NOME_ENV", "").lower() == "production"
    )


class SecurityMiddleware(BaseHTTPMiddleware):
    """
    Middleware for enforcing security settings from manifest.

    Features:
    - HTTPS enforcement in production
    - HSTS header for forcing HTTPS
    - Security headers (X-Content-Type-Options, X-Frame-Options, etc.)
    - Legacy CSRF token generation (prefer CSRFMiddleware for new apps)
    """

    def __init__(
        self,
        app,
        require_https: bool = False,
        csrf_protection: bool = True,
        security_headers: bool = True,
        hsts_config: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize security middleware.

        Args:
            app: FastAPI application
            require_https: Require HTTPS in production (default: False, auto-detected)
            csrf_protection: Enable legacy CSRF protection (default: True)
            security_headers: Add security headers (default: True)
            hsts_config: HSTS configuration dict with keys:
                - enabled: Enable HSTS (default: True in production)
                - max_age: Max-age in seconds (default: 31536000)
                - include_subdomains: Include subdomains (default: True)
                - preload: Add preload directive (default: False)
        """
        super().__init__(app)
        self.require_https = require_https
        self.csrf_protection = csrf_protection
        self.security_headers = security_headers

        # HSTS configuration
        self.hsts_config = hsts_config or {}
        self.hsts_enabled = self.hsts_config.get("enabled", True)
        self.hsts_max_age = self.hsts_config.get("max_age", DEFAULT_HSTS_MAX_AGE)
        self.hsts_include_subdomains = self.hsts_config.get("include_subdomains", True)
        self.hsts_preload = self.hsts_config.get("preload", False)

    def _build_hsts_header(self) -> str:
        """Build the HSTS header value."""
        parts = [f"max-age={self.hsts_max_age}"]

        if self.hsts_include_subdomains:
            parts.append("includeSubDomains")

        if self.hsts_preload:
            parts.append("preload")

        return "; ".join(parts)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """
        Process request through security middleware.
        """
        is_production = _is_production()
        is_https = request.url.scheme == "https"

        # Check HTTPS requirement
        if self.require_https and is_production and not is_https:
            if request.method == "GET":
                # Redirect to HTTPS
                https_url = str(request.url).replace("http://", "https://", 1)
                return RedirectResponse(url=https_url, status_code=301)
            else:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="HTTPS required in production",
                )

        # Generate CSRF token if not present (for GET requests) - legacy support
        if self.csrf_protection and request.method == "GET":
            csrf_token = request.cookies.get("csrf_token")
            if not csrf_token:
                csrf_token = secrets.token_urlsafe(32)
                # Will be set in response

        # Process request
        response = await call_next(request)

        # Set security headers
        if self.security_headers:
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["X-XSS-Protection"] = "1; mode=block"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

            # Permissions-Policy (modern replacement for some legacy headers)
            response.headers["Permissions-Policy"] = (
                "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
                "magnetometer=(), microphone=(), payment=(), usb=()"
            )

            # Content Security Policy (basic)
            if request.url.path.startswith("/api"):
                response.headers["Content-Security-Policy"] = "default-src 'self'"

        # Add HSTS header in production (only over HTTPS or always if configured)
        if self.hsts_enabled and (is_production or is_https):
            response.headers["Strict-Transport-Security"] = self._build_hsts_header()

        # Set CSRF token cookie if generated (legacy support)
        if (
            self.csrf_protection
            and request.method == "GET"
            and not request.cookies.get("csrf_token")
        ):
            csrf_token = secrets.token_urlsafe(32)
            response.set_cookie(
                key="csrf_token",
                value=csrf_token,
                httponly=True,
                secure=is_https or is_production,
                samesite="lax",
                max_age=86400,  # 24 hours
            )

        return response


class StaleSessionMiddleware(BaseHTTPMiddleware):
    """
    Middleware for cleaning up stale session cookies.

        When get_app_user() detects a stale/invalid session cookie,
    it sets request.state.clear_stale_session = True. This middleware
    then removes the cookie from the response.
    """

    def __init__(self, app, slug_id: str, engine=None):
        """
        Initialize stale session middleware.

        Args:
            app: FastAPI application
            slug_id: App slug identifier
            engine: Optional MongoDBEngine instance for getting app config
        """
        super().__init__(app)
        self.slug_id = slug_id
        self.engine = engine

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """
        Process request and clean up stale session cookies if needed.

        This middleware only acts when request.state.clear_stale_session is set to True
        by get_app_user() when it detects an invalid/stale session cookie.
        It gracefully handles missing config and only processes requests for apps
        that use auth.users.
        """
        # Process request first
        response = await call_next(request)

        # Check if we need to clear a stale session cookie
        # Only act if explicitly flagged - this ensures we don't interfere with
        # apps that don't use get_app_user()
        if hasattr(request.state, "clear_stale_session") and request.state.clear_stale_session:
            try:
                # Get cookie name from app config
                cookie_name = None

                # Try to get from app state first (set during setup_auth_from_manifest)
                if hasattr(request.app.state, "auth_config"):
                    try:
                        auth_config = request.app.state.auth_config
                        auth = auth_config.get("auth", {})
                        users_config = auth.get("users", {})
                        if users_config.get("enabled", False):
                            session_cookie_name = users_config.get(
                                "session_cookie_name", "app_session"
                            )
                            cookie_name = f"{session_cookie_name}_{self.slug_id}"
                    except (AttributeError, KeyError, TypeError):
                        pass

                # Fallback: get from engine if available
                if not cookie_name and self.engine:
                    try:
                        app_config = self.engine.get_app(self.slug_id)
                        if app_config:
                            auth = app_config.get("auth", {})
                            users_config = auth.get("users", {})
                            if users_config.get("enabled", False):
                                session_cookie_name = users_config.get(
                                    "session_cookie_name", "app_session"
                                )
                                cookie_name = f"{session_cookie_name}_{self.slug_id}"
                    except (AttributeError, KeyError, TypeError, Exception):
                        pass

                # Final fallback to default naming convention
                if not cookie_name:
                    cookie_name = f"app_session_{self.slug_id}"

                # Get cookie settings to match how it was set
                should_use_secure = (
                    request.url.scheme == "https" or os.getenv("G_NOME_ENV") == "production"
                )

                # Delete the stale cookie
                response.delete_cookie(
                    key=cookie_name,
                    httponly=True,
                    secure=should_use_secure,
                    samesite="lax",
                )
                logger.debug(f"Cleared stale session cookie '{cookie_name}' for {self.slug_id}")
            except (ValueError, TypeError, AttributeError, RuntimeError) as e:
                # Don't fail the request if cookie cleanup fails
                logger.warning(
                    f"Error clearing stale session cookie for {self.slug_id}: {e}",
                    exc_info=True,
                )

        return response


def create_security_middleware(config: dict) -> Callable:
    """
    Create security middleware from manifest config.

    Args:
        config: token_management.security config from manifest

    Returns:
        SecurityMiddleware instance
    """
    security = config.get("security", {})

    return SecurityMiddleware(
        app=None,  # Will be set by FastAPI
        require_https=security.get("require_https", False),
        csrf_protection=security.get("csrf_protection", True),
        security_headers=True,
    )
