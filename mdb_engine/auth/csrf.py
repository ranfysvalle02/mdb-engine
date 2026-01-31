"""
CSRF Protection Middleware

Implements the Double-Submit Cookie pattern for Cross-Site Request Forgery protection.
Auto-enabled for shared auth mode, with manifest-configurable options.

This module is part of MDB_ENGINE - MongoDB Engine.

Security Features:
    - Double-submit cookie pattern (industry standard)
    - Cryptographically secure token generation
    - Configurable exempt routes for APIs
    - SameSite cookie attribute for additional protection
    - Token rotation on each request (optional)

Usage:
    # Auto-enabled for shared auth mode in engine.create_app()

    # Or manual usage:
    from mdb_engine.auth.csrf import CSRFMiddleware
    app.add_middleware(CSRFMiddleware, exempt_routes=["/api/*"])

    # In templates, include the token:
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">

    # Or in JavaScript:
    fetch('/endpoint', {
        headers: {'X-CSRF-Token': getCookie('csrf_token')}
    })
"""

import fnmatch
import hashlib
import hmac
import logging
import os
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# Token settings
CSRF_TOKEN_LENGTH = 32  # 256 bits
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_FORM_FIELD = "csrf_token"
DEFAULT_TOKEN_TTL = 3600  # 1 hour

# Methods that require CSRF validation
UNSAFE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


def generate_csrf_token(secret: str | None = None) -> str:
    """
    Generate a cryptographically secure CSRF token.

    Args:
        secret: Optional secret for HMAC signing (adds tamper detection)

    Returns:
        URL-safe base64 encoded token
    """
    raw_token = secrets.token_urlsafe(CSRF_TOKEN_LENGTH)

    if secret:
        # Add HMAC signature for tamper detection
        timestamp = str(int(time.time()))
        message = f"{raw_token}:{timestamp}"
        signature = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()[:16]
        return f"{raw_token}:{timestamp}:{signature}"

    return raw_token


def validate_csrf_token(
    token: str,
    secret: str | None = None,
    max_age: int = DEFAULT_TOKEN_TTL,
) -> bool:
    """
    Validate a CSRF token.

    Args:
        token: The token to validate
        secret: Optional secret for HMAC verification
        max_age: Maximum token age in seconds

    Returns:
        True if valid, False otherwise
    """
    if not token:
        return False

    if secret and ":" in token:
        # Verify HMAC-signed token
        try:
            parts = token.split(":")
            if len(parts) != 3:
                return False

            raw_token, timestamp_str, signature = parts
            timestamp = int(timestamp_str)

            # Check age
            if time.time() - timestamp > max_age:
                logger.debug("CSRF token expired")
                return False

            # Verify signature
            message = f"{raw_token}:{timestamp_str}"
            expected_sig = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()[
                :16
            ]

            if not hmac.compare_digest(signature, expected_sig):
                logger.warning("CSRF token signature mismatch")
                return False

            return True
        except (ValueError, IndexError) as e:
            logger.warning(f"CSRF token validation error: {e}")
            return False

    # Simple token validation (just check it exists and has reasonable length)
    return len(token) >= CSRF_TOKEN_LENGTH


class CSRFMiddleware(BaseHTTPMiddleware):
    """
    CSRF Protection Middleware using Double-Submit Cookie pattern.

    The double-submit cookie pattern works by:
    1. Setting a CSRF token in a cookie (with HttpOnly=False so JS can read it)
    2. Requiring the same token in a header or form field
    3. Since attackers can't read cookies from other domains, they can't forge requests

    Additional protection from SameSite=Lax cookies prevents the browser from
    sending cookies on cross-site requests.
    """

    def __init__(
        self,
        app,
        secret: str | None = None,
        exempt_routes: list[str] | None = None,
        exempt_methods: set[str] | None = None,
        cookie_name: str = CSRF_COOKIE_NAME,
        header_name: str = CSRF_HEADER_NAME,
        form_field: str = CSRF_FORM_FIELD,
        token_ttl: int = DEFAULT_TOKEN_TTL,
        rotate_tokens: bool = False,
        secure_cookies: bool = True,
    ):
        """
        Initialize CSRF middleware.

        Args:
            app: FastAPI application
            secret: Secret for HMAC token signing (recommended for production)
            exempt_routes: Routes exempt from CSRF (supports wildcards: /api/*)
            exempt_methods: HTTP methods exempt from CSRF (default: safe methods)
            cookie_name: Name of the CSRF cookie
            header_name: Name of the CSRF header
            form_field: Name of the CSRF form field
            token_ttl: Token time-to-live in seconds
            rotate_tokens: Rotate token on each request (more secure, less convenient)
            secure_cookies: Use Secure cookie flag (auto-detect HTTPS)
        """
        super().__init__(app)
        self.secret = secret or os.getenv("MDB_ENGINE_CSRF_SECRET")
        self.exempt_routes = exempt_routes or []
        self.exempt_methods = exempt_methods or {"GET", "HEAD", "OPTIONS", "TRACE"}
        self.cookie_name = cookie_name
        self.header_name = header_name
        self.form_field = form_field
        self.token_ttl = token_ttl
        self.rotate_tokens = rotate_tokens
        self.secure_cookies = secure_cookies

        logger.info(
            f"CSRFMiddleware initialized (exempt_routes={self.exempt_routes}, "
            f"rotate_tokens={rotate_tokens})"
        )

    def _is_exempt(self, path: str) -> bool:
        """Check if a path is exempt from CSRF validation."""
        # WebSocket upgrade requests are handled separately in dispatch()
        # Don't exempt them here - they need origin validation
        for pattern in self.exempt_routes:
            if fnmatch.fnmatch(path, pattern):
                return True
        return False

    def _is_websocket_upgrade(self, request: Request) -> bool:
        """Check if request is a WebSocket upgrade request."""
        upgrade_header = request.headers.get("upgrade", "").lower()
        return upgrade_header == "websocket"

    def _get_allowed_origins(self, request: Request) -> list[str]:
        """
        Get allowed origins from app state (CORS config) or use request host as fallback.

        For multi-app setups, checks parent app's CORS config first (since WebSocket routes
        are registered on parent app), then falls back to request host.
        """
        try:
            # Check current app's CORS config (parent app for WebSocket routes in multi-app)
            cors_config = getattr(request.app.state, "cors_config", None)
            if cors_config and cors_config.get("allow_origins"):
                origins = cors_config["allow_origins"]
                if origins:
                    return origins if isinstance(origins, list) else [origins]
        except (AttributeError, TypeError, KeyError):
            pass

        # Fallback: Check if this is a multi-app setup and try to find mounted app's CORS config
        try:
            if hasattr(request.app.state, "mounted_apps"):
                # This is a parent app in multi-app setup
                # Try to find which mounted app this request is for
                path = request.url.path
                mounted_apps = request.app.state.mounted_apps

                # Find matching mounted app by path prefix
                for app_info in mounted_apps:
                    path_prefix = app_info.get("path_prefix", "")
                    if path_prefix and path.startswith(path_prefix):
                        # Try to get child app's CORS config if available
                        # Note: Child app might not be directly accessible, so we rely on
                        # parent app's merged CORS config (set during mounting)
                        break
        except (AttributeError, TypeError, KeyError):
            pass

        # Final fallback: Use request host
        try:
            host = request.url.hostname
            scheme = request.url.scheme
            port = request.url.port
            if port and port not in [80, 443]:
                origin = f"{scheme}://{host}:{port}"
            else:
                origin = f"{scheme}://{host}"
            return [origin]
        except (AttributeError, TypeError):
            return []

    def _validate_websocket_origin(self, request: Request) -> bool:
        """
        Validate Origin header for WebSocket upgrade requests.

        Primary defense against Cross-Site WebSocket Hijacking (CSWSH).
        Returns True if Origin is valid, False otherwise.
        """
        origin = request.headers.get("origin")
        if not origin:
            logger.warning(f"WebSocket upgrade missing Origin header: {request.url.path}")
            return False

        allowed_origins = self._get_allowed_origins(request)

        for allowed in allowed_origins:
            if allowed == "*":
                logger.warning(
                    "WebSocket Origin validation using wildcard '*' - "
                    "not recommended for production"
                )
                return True
            if origin == allowed or origin.rstrip("/") == allowed.rstrip("/"):
                return True

        logger.warning(
            f"WebSocket upgrade rejected - invalid Origin: {origin} "
            f"(allowed: {allowed_origins}, app: {getattr(request.app, 'title', 'unknown')}, "
            f"has_cors_config: {hasattr(request.app.state, 'cors_config')})"
        )
        return False

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """
        Process request through CSRF middleware.
        """
        path = request.url.path
        method = request.method

        # CRITICAL: Handle WebSocket upgrade requests BEFORE other CSRF checks
        # WebSocket upgrades don't use CSRF tokens, but need origin validation
        if self._is_websocket_upgrade(request):
            # Validate origin for WebSocket connections (CSWSH protection)
            if not self._validate_websocket_origin(request):
                logger.warning(
                    f"WebSocket origin validation failed for {path}: "
                    f"origin={request.headers.get('origin')}, "
                    f"allowed={self._get_allowed_origins(request)}"
                )
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "Invalid origin for WebSocket connection"},
                )
            # Origin validated - allow WebSocket upgrade to proceed
            # No CSRF token check needed for WebSocket upgrades
            return await call_next(request)

        if self._is_exempt(path):
            return await call_next(request)

        # Skip safe methods
        if method in self.exempt_methods:
            # Generate and set token for GET requests (for forms)
            response = await call_next(request)

            # Set CSRF token cookie if not present
            if not request.cookies.get(self.cookie_name):
                token = generate_csrf_token(self.secret)
                self._set_csrf_cookie(request, response, token)

            # Make token available in request state for templates
            request.state.csrf_token = request.cookies.get(self.cookie_name) or generate_csrf_token(
                self.secret
            )

            return response

        # Validate CSRF token for unsafe methods
        cookie_token = request.cookies.get(self.cookie_name)
        if not cookie_token:
            logger.warning(f"CSRF cookie missing for {method} {path}")
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "CSRF token missing"},
            )

        # Get token from header or form
        header_token = request.headers.get(self.header_name)
        form_token = None

        # Note: Form-based CSRF token extraction not implemented.
        # For now, we rely on header-based CSRF for all requests.
        # TODO: Implement request.form() based extraction if needed.

        submitted_token = header_token or form_token

        if not submitted_token:
            logger.warning(f"CSRF token not submitted for {method} {path}")
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "CSRF token not provided in header or form"},
            )

        # Compare tokens (constant-time comparison)
        if not hmac.compare_digest(cookie_token, submitted_token):
            logger.warning(f"CSRF token mismatch for {method} {path}")
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "CSRF token invalid"},
            )

        # Validate token (check signature if secret is used)
        if self.secret and not validate_csrf_token(cookie_token, self.secret, self.token_ttl):
            logger.warning(f"CSRF token validation failed for {method} {path}")
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "CSRF token expired or invalid"},
            )

        # Process request
        response = await call_next(request)

        # Optionally rotate token
        if self.rotate_tokens:
            new_token = generate_csrf_token(self.secret)
            self._set_csrf_cookie(request, response, new_token)

        return response

    def _set_csrf_cookie(
        self,
        request: Request,
        response: Response,
        token: str,
    ) -> None:
        """Set the CSRF token cookie."""
        is_https = request.url.scheme == "https"
        is_production = os.getenv("ENVIRONMENT", "").lower() == "production"

        response.set_cookie(
            key=self.cookie_name,
            value=token,
            httponly=False,  # Must be readable by JavaScript
            secure=self.secure_cookies and (is_https or is_production),
            samesite="lax",  # Provides CSRF protection + allows top-level navigation
            max_age=self.token_ttl,
            path="/",
        )


def create_csrf_middleware(
    manifest_auth: dict[str, Any],
    secret: str | None = None,
) -> type:
    """
    Create CSRF middleware from manifest configuration.

    Args:
        manifest_auth: Auth section from manifest
        secret: Optional CSRF secret (defaults to env var)

    Returns:
        Configured CSRFMiddleware class
    """
    csrf_config = manifest_auth.get("csrf_protection", True)

    # Handle boolean or object config
    if isinstance(csrf_config, bool):
        if not csrf_config:
            # Return a no-op middleware
            class NoOpMiddleware(BaseHTTPMiddleware):
                async def dispatch(self, request, call_next):
                    return await call_next(request)

            return NoOpMiddleware

        # Use defaults
        exempt_routes = manifest_auth.get("public_routes", [])
        rotate_tokens = False
        token_ttl = DEFAULT_TOKEN_TTL
    else:
        # Object configuration
        exempt_routes = csrf_config.get("exempt_routes", manifest_auth.get("public_routes", []))
        rotate_tokens = csrf_config.get("rotate_tokens", False)
        token_ttl = csrf_config.get("token_ttl", DEFAULT_TOKEN_TTL)

    # Create configured middleware class
    class ConfiguredCSRFMiddleware(CSRFMiddleware):
        def __init__(self, app):
            super().__init__(
                app,
                secret=secret or os.getenv("MDB_ENGINE_CSRF_SECRET"),
                exempt_routes=exempt_routes,
                rotate_tokens=rotate_tokens,
                token_ttl=token_ttl,
            )

    return ConfiguredCSRFMiddleware


# Dependency for getting CSRF token in routes
def get_csrf_token(request: Request) -> str:
    """
    Get or generate CSRF token for use in templates.

    Usage in FastAPI route:
        @app.get("/form")
        def form_page(csrf_token: str = Depends(get_csrf_token)):
            return templates.TemplateResponse("form.html", {"csrf_token": csrf_token})
    """
    # Try to get from request state (set by middleware)
    if hasattr(request.state, "csrf_token"):
        return request.state.csrf_token

    # Try to get from cookie
    token = request.cookies.get(CSRF_COOKIE_NAME)
    if token:
        return token

    # Generate new token
    secret = os.getenv("MDB_ENGINE_CSRF_SECRET")
    return generate_csrf_token(secret)
