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
                logger.debug("CSRF token has wrong format (expected 3 parts)")
                return False

            raw_token, timestamp_str, signature = parts
            timestamp = int(timestamp_str)

            # Check age
            age = time.time() - timestamp
            if age > max_age:
                logger.debug(f"CSRF token expired (age: {age:.0f}s, max: {max_age}s)")
                return False

            # Verify signature
            message = f"{raw_token}:{timestamp_str}"
            expected_sig = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()[
                :16
            ]

            if not hmac.compare_digest(signature, expected_sig):
                logger.warning(
                    f"CSRF token signature mismatch. "
                    f"Token format: signed, Has secret: {bool(secret)}"
                )
                return False

            return True
        except (ValueError, IndexError) as e:
            logger.warning(f"CSRF token validation error: {e}")
            return False

    # Simple token validation (just check it exists and has reasonable length)
    is_valid = len(token) >= CSRF_TOKEN_LENGTH
    if not is_valid:
        logger.debug(f"CSRF token too short (length: {len(token)}, required: {CSRF_TOKEN_LENGTH})")
    return is_valid


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

    def _validate_csrf_token(self, token: str, request: Request) -> bool:
        """
        Validate a CSRF token using the middleware's secret and TTL.

        Args:
            token: CSRF token to validate
            request: FastAPI request (unused, kept for API consistency)

        Returns:
            True if token is valid, False otherwise
        """
        return validate_csrf_token(token, self.secret, self.token_ttl)

    def _websocket_requires_csrf(self, request: Request, path: str) -> bool:
        """
        Check if WebSocket endpoint requires CSRF validation.

        Defaults to True (security by default). Can be disabled per-endpoint via manifest.json:
        websockets.{endpoint}.auth.csrf_required = false

        Args:
            request: FastAPI request
            path: WebSocket path (e.g., "/chat-app/ws")

        Returns:
            True if CSRF validation is required, False otherwise
        """
        # Try parent app first (where websocket_configs should be stored)
        websocket_configs = getattr(request.app.state, "websocket_configs", None)

        # If not found, try to traverse up to find parent app
        if not websocket_configs:
            logger.debug(f"No websocket_configs found on request.app.state for path '{path}'")
            app = request.app
            apps_checked = []
            while app:
                app_title = getattr(app, "title", "unknown")
                apps_checked.append(app_title)
                websocket_configs = getattr(app.state, "websocket_configs", None)
                if websocket_configs:
                    logger.debug(
                        f"Found websocket_configs on app '{app_title}' "
                        f"(checked: {apps_checked})"
                    )
                    break
                parent_app = getattr(app, "app", None)
                if parent_app is app:  # Prevent infinite loop
                    break
                app = parent_app

        if not websocket_configs:
            # No WebSocket configs found - use default (CSRF required for security by default)
            logger.debug(
                f"No websocket_configs found anywhere for path '{path}' - "
                f"using default csrf_required=true"
            )
            return True

        # Normalize path for matching (handle trailing slashes)
        normalized_path = path.rstrip("/")
        logger.debug(
            f"Checking CSRF requirement for path '{normalized_path}' "
            f"against {len(websocket_configs)} app config(s)"
        )

        # Try to find matching app config
        # WebSocket paths are registered as /app-slug/endpoint-path
        # e.g., /chat-app/ws where app_slug="chat-app" and endpoint_path="/ws"
        for app_slug, config in websocket_configs.items():
            logger.debug(f"Checking app '{app_slug}' config with {len(config)} endpoint(s)")
            # Check each endpoint in this app's config
            for endpoint_name, endpoint_config in config.items():
                endpoint_path = endpoint_config.get("path", "")
                # Normalize endpoint path
                normalized_endpoint = endpoint_path.rstrip("/")

                # Build expected full path: /app-slug/endpoint-path
                if normalized_endpoint.startswith("/"):
                    expected_full_path = f"/{app_slug}{normalized_endpoint}"
                else:
                    expected_full_path = f"/{app_slug}/{normalized_endpoint}"

                # Match patterns:
                # 1. Full path match: /chat-app/ws == /chat-app/ws
                # 2. Endpoint-only match: /ws (if path ends with endpoint)
                # 3. Path contains endpoint: /chat-app/ws contains /ws
                matches = (
                    normalized_path == expected_full_path
                    or normalized_path == normalized_endpoint
                    or normalized_path.endswith(normalized_endpoint)
                    or normalized_path.endswith(f"/{app_slug}{normalized_endpoint}")
                )

                if matches:
                    auth_config = endpoint_config.get("auth", {})
                    if isinstance(auth_config, dict):
                        # Return csrf_required setting (defaults to True - security by default)
                        csrf_required = auth_config.get("csrf_required", True)
                        logger.info(
                            f"✅ WebSocket '{normalized_path}' csrf_required={csrf_required} "
                            f"(from app='{app_slug}', endpoint='{endpoint_name}', "
                            f"endpoint_path='{normalized_endpoint}')"
                        )
                        return csrf_required
                    else:
                        logger.debug(
                            f"WebSocket '{normalized_path}' auth_config is not a dict: "
                            f"{type(auth_config)}"
                        )

        # No matching config found - use default (CSRF required for security by default)
        logger.debug(
            f"❌ No WebSocket config match for '{normalized_path}' "
            f"(checked {len(websocket_configs)} app(s)) - using default csrf_required=true"
        )
        return True

    def _is_websocket_upgrade(self, request: Request) -> bool:
        """Check if request is a WebSocket upgrade request."""
        upgrade_header = request.headers.get("upgrade", "").lower()
        connection_header = request.headers.get("connection", "").lower()
        path = request.url.path

        # Primary check: WebSocket upgrade requires both Upgrade: websocket
        # and Connection: Upgrade headers
        has_upgrade_header = upgrade_header == "websocket"
        has_connection_upgrade = "upgrade" in connection_header or "websocket" in connection_header

        # Secondary check: If upgrade header is present but connection is
        # overridden (e.g., by TestClient), check if path matches a known
        # WebSocket route pattern
        path_matches_websocket_route = False
        if has_upgrade_header and not has_connection_upgrade:
            # Check if path matches any configured WebSocket route
            websocket_configs = getattr(request.app.state, "websocket_configs", None)
            if websocket_configs:
                path = request.url.path.rstrip("/") or "/"
                for app_slug, config in websocket_configs.items():
                    for _endpoint_name, endpoint_config in config.items():
                        endpoint_path = endpoint_config.get("path", "").rstrip("/") or "/"
                        # Try various path matching patterns
                        expected_full_path = (
                            f"/{app_slug}{endpoint_path}"
                            if endpoint_path != "/"
                            else f"/{app_slug}"
                        )
                        # Match patterns:
                        # 1. Exact match with app prefix: /app-slug/endpoint-path
                        # 2. Endpoint-only match: /endpoint-path (if path ends with endpoint)
                        # 3. Root match: / matches / or /app-slug
                        if (
                            path == expected_full_path
                            or path.endswith(endpoint_path)
                            or path == endpoint_path
                            or (path == "/" and endpoint_path == "/")
                            or (path == f"/{app_slug}" and endpoint_path == "/")
                        ):
                            path_matches_websocket_route = True
                            break
                    if path_matches_websocket_route:
                        break

        is_websocket = has_upgrade_header and (
            has_connection_upgrade or path_matches_websocket_route
        )

        # Only log actual WebSocket upgrades at INFO level, use DEBUG for non-WebSocket requests
        if is_websocket:
            logger.info(f"🔍 WebSocket upgrade detection for {path}: is_websocket=True")
        else:
            # Use DEBUG level for non-WebSocket requests to reduce log noise
            logger.debug(f"🔍 WebSocket upgrade detection for {path}: is_websocket=False")
        return is_websocket

    def _get_allowed_origins(self, request: Request) -> list[str]:
        """
        Get allowed origins from app state (CORS config) or use request host as fallback.

        For multi-app setups, checks parent app's CORS config first (since WebSocket routes
        are registered on parent app), then falls back to request host.
        """
        try:
            # For WebSocket routes on parent app, request.app is parent app
            # Parent app has merged CORS config from all child apps
            cors_config = getattr(request.app.state, "cors_config", None)
            if cors_config and cors_config.get("allow_origins"):
                origins = cors_config["allow_origins"]
                if origins:
                    return origins if isinstance(origins, list) else [origins]
        except (AttributeError, TypeError, KeyError) as e:
            logger.debug(f"Could not read CORS config from app.state: {e}")

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

        # Final fallback: Use request host (normalize localhost variants)
        try:
            host = request.url.hostname
            scheme = request.url.scheme
            port = request.url.port

            # Normalize localhost variants - return all common variants for development
            # This handles cases where server binds to 0.0.0.0 but browser sends localhost
            if host in ["localhost", "0.0.0.0", "127.0.0.1", "::1"]:
                origins = []
                for localhost_variant in ["localhost", "127.0.0.1"]:
                    if port and port not in [80, 443]:
                        origins.append(f"{scheme}://{localhost_variant}:{port}")
                    else:
                        origins.append(f"{scheme}://{localhost_variant}")
                logger.debug(f"Generated localhost variant origins for host '{host}': {origins}")
                return origins

            # For other hosts, use the actual hostname
            if port and port not in [80, 443]:
                origin = f"{scheme}://{host}:{port}"
            else:
                origin = f"{scheme}://{host}"
            return [origin]
        except (AttributeError, TypeError) as e:
            logger.debug(f"Could not determine origin from request: {e}")
            # Return empty list if we can't determine origin (will reject)
            return []

    def _normalize_origin(self, origin: str) -> str:
        """
        Normalize origin for comparison (handles localhost/0.0.0.0/127.0.0.1/::1 equivalency).

        In development, localhost, 0.0.0.0, 127.0.0.1, and ::1 should be treated as equivalent.
        Also normalizes ports (80/443 vs explicit ports).
        """
        if not origin:
            return origin

        import re

        # Normalize localhost variants - replace all variants with localhost
        # Handle IPv4: 0.0.0.0, 127.0.0.1
        # Handle IPv6: ::1
        # Handle hostname: localhost
        normalized = re.sub(
            r"://(0\.0\.0\.0|127\.0\.0\.1|localhost|::1)",
            "://localhost",
            origin.lower(),
            flags=re.IGNORECASE,
        )

        # Normalize ports: remove :80 for http and :443 for https
        normalized = re.sub(r":80$", "", normalized)
        normalized = re.sub(r":443$", "", normalized)

        return normalized.rstrip("/")

    def _is_development_mode(self) -> bool:
        """Check if running in development mode."""
        import os

        env = os.getenv("ENVIRONMENT", "").lower()
        g_nome_env = os.getenv("G_NOME_ENV", "").lower()
        return env in ["development", "dev"] or g_nome_env in ["development", "dev"]

    def _validate_websocket_origin(self, request: Request) -> bool:
        """
        Validate Origin header for WebSocket upgrade requests.

        Primary defense against Cross-Site WebSocket Hijacking (CSWSH).
        Returns True if Origin is valid, False otherwise.

        In development mode, allows connections without Origin header (with warning).
        """
        origin = request.headers.get("origin")
        if not origin:
            if self._is_development_mode():
                logger.warning(
                    f"WebSocket upgrade missing Origin header in development mode: "
                    f"{request.url.path} - allowing connection"
                )
                return True
            else:
                logger.warning(f"WebSocket upgrade missing Origin header: {request.url.path}")
                return False

        allowed_origins = self._get_allowed_origins(request)
        normalized_origin = self._normalize_origin(origin)

        logger.debug(
            f"Validating WebSocket origin: {origin} (normalized: {normalized_origin}) "
            f"against allowed: {allowed_origins}"
        )

        for allowed in allowed_origins:
            if allowed == "*":
                logger.warning(
                    "WebSocket Origin validation using wildcard '*' - "
                    "not recommended for production"
                )
                return True

            normalized_allowed = self._normalize_origin(allowed)
            if normalized_origin == normalized_allowed:
                logger.debug(
                    f"✅ WebSocket origin validated: {origin} matches {allowed} "
                    f"(normalized: {normalized_origin} == {normalized_allowed})"
                )
                return True

        cors_config = getattr(request.app.state, "cors_config", None)
        cors_enabled = cors_config.get("enabled", False) if cors_config else False
        normalized_allowed_list = [self._normalize_origin(a) for a in allowed_origins]
        logger.warning(
            f"❌ WebSocket upgrade rejected - invalid Origin: {origin} "
            f"(normalized: {normalized_origin}, allowed: {allowed_origins}, "
            f"normalized_allowed: {normalized_allowed_list}, "
            f"app: {getattr(request.app, 'title', 'unknown')}, "
            f"path: {request.url.path}, CORS enabled: {cors_enabled}, "
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
        # CRITICAL: Log EVERY request immediately to catch WebSocket upgrades
        path = request.url.path
        method = request.method
        upgrade_header = request.headers.get("upgrade", "").lower()
        connection_header = request.headers.get("connection", "").lower()
        origin_header = request.headers.get("origin")

        # Log ALL WebSocket-related requests IMMEDIATELY (before any processing)
        if upgrade_header or "websocket" in path.lower() or connection_header == "upgrade":
            import sys

            print(
                f"🚨 [CSRF MIDDLEWARE ENTRY] {method} {path}, "
                f"upgrade={upgrade_header}, connection={connection_header}, "
                f"origin={origin_header}",
                file=sys.stderr,
                flush=True,
            )
            logger.info(
                f"🚨 [CSRF MIDDLEWARE ENTRY] {method} {path}, "
                f"upgrade={upgrade_header}, connection={connection_header}, "
                f"origin={origin_header}"
            )

        try:
            # CRITICAL: Log ALL requests to verify middleware is running
            # Always log WebSocket-related requests
            if upgrade_header or "websocket" in path.lower() or connection_header == "upgrade":
                logger.info(
                    f"🔍 CSRF middleware INTERCEPTED: {method} {path}, "
                    f"upgrade={upgrade_header}, connection={connection_header}, "
                    f"origin={origin_header}"
                )
                import sys

                print(
                    f"🔍 [CSRF MIDDLEWARE] {method} {path}, "
                    f"upgrade={upgrade_header}, origin={origin_header}",
                    file=sys.stderr,
                    flush=True,
                )

            # CRITICAL: Handle WebSocket upgrade requests BEFORE other CSRF checks
            # WebSocket upgrades use cookie-based authentication and require CSRF validation
            is_ws_upgrade = self._is_websocket_upgrade(request)
            logger.info(f"🔍 WebSocket upgrade detection for {path}: is_websocket={is_ws_upgrade}")

            if is_ws_upgrade:
                logger.info(
                    f"🔌 CSRF middleware processing WebSocket upgrade: {path}, "
                    f"origin: {request.headers.get('origin')}"
                )
                # Always validate origin for WebSocket connections (CSWSH protection)
                origin_valid = self._validate_websocket_origin(request)
                logger.info(
                    f"🔍 WebSocket origin validation for {path}: "
                    f"origin={request.headers.get('origin')}, "
                    f"allowed={self._get_allowed_origins(request)}, "
                    f"valid={origin_valid}"
                )
                if not origin_valid:
                    logger.warning(
                        f"❌ WebSocket origin validation failed for {path}: "
                        f"origin={request.headers.get('origin')}, "
                        f"allowed={self._get_allowed_origins(request)}"
                    )
                    return JSONResponse(
                        status_code=status.HTTP_403_FORBIDDEN,
                        content={"detail": "Invalid origin for WebSocket connection"},
                    )

                # Cookie-based authentication requires CSRF protection
                # Check if authentication token cookie is present
                # Use same cookie name as SharedAuthMiddleware for consistency
                from .shared_middleware import AUTH_COOKIE_NAME

                auth_token_cookie = request.cookies.get(AUTH_COOKIE_NAME)
                logger.info(
                    f"🔍 WebSocket auth check for {path}: "
                    f"auth_cookie={'present' if auth_token_cookie else 'missing'}"
                )

                # Check if ticket/session key authentication is required
                # (csrf_required flag controls whether WebSocket needs ticket/session key)
                # If csrf_required=false, we skip ticket validation entirely
                csrf_required = self._websocket_requires_csrf(request, path)
                logger.info(
                    f"🔍 WebSocket auth check for {path}: "
                    f"ticket/session_key_required={csrf_required}"
                )

                # Only validate ticket/session key if:
                # 1. Auth cookie is present (user is authenticated)
                # 2. Ticket/session key is required for this endpoint
                if auth_token_cookie and csrf_required:
                    # WebSocket Authentication (NOT CSRF):
                    # WebSockets use JWT → Ticket → WebSocket flow for authentication.
                    # CSRF protection comes from Origin validation + SameSite cookies.
                    #
                    # Authentication Methods (in order of preference):
                    # 1. Ticket (JWT → Ticket exchange) - preferred for single-app
                    #    - Client: POST /auth/ticket (sends JWT cookie)
                    #    - Server: Validates JWT, generates ticket (UUID)
                    #    - Client: ws://host/app/ws?ticket=<uuid>
                    #    - Server: Validates & consumes ticket (single-use)
                    # 2. Session key - preferred for multi-app SSO
                    #    - Generated via /auth/websocket-session endpoint
                    #    - Encrypted, database-backed, long TTL (24h)
                    # 3. CSRF cookie - backward compatibility only
                    #
                    # CSRF Protection (separate from authentication):
                    # - Origin validation (already done above) - primary CSRF defense
                    # - SameSite cookies - prevents cross-site cookie sending
                    #
                    # Ticket flow: JWT (httpOnly cookie) → POST /auth/ticket → Ticket (UUID)
                    #              → WebSocket connection with ticket → Validated & consumed

                    # Check for session key first (preferred for multi-app setups)
                    session_key = request.query_params.get("session_key") or request.headers.get(
                        "X-WebSocket-Session-Key"
                    )

                    # Check for ticket (preferred for single-app setups)
                    ticket = request.query_params.get("ticket") or request.headers.get(
                        "X-WebSocket-Ticket"
                    )

                    if session_key:
                        # Session key authentication (bypasses CSRF - encrypted and secure)
                        # For WebSocket upgrades, let the handler validate session keys
                        # This allows TestClient to catch WebSocketDisconnect exceptions properly
                        # The handler will validate and raise WebSocketDisconnect if invalid
                        websocket_session_manager = None
                        app = request.app
                        apps_checked = []
                        while app:
                            app_title = getattr(app, "title", "unknown")
                            apps_checked.append(app_title)
                            websocket_session_manager = getattr(
                                app.state, "websocket_session_manager", None
                            )
                            if websocket_session_manager:
                                logger.debug(
                                    f"Found websocket_session_manager on app '{app_title}' "
                                    f"for WebSocket path '{path}' (checked: {apps_checked})"
                                )
                                break
                            parent_app = getattr(app, "app", None)
                            if parent_app is app:  # Prevent infinite loop
                                break
                            app = parent_app

                        if not websocket_session_manager:
                            logger.error(
                                f"❌ WebSocket session key provided for {path} but "
                                "websocket_session_manager not found"
                            )
                            return JSONResponse(
                                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                content={
                                    "detail": (
                                        "WebSocket session manager not available. "
                                        "Server configuration error."
                                    )
                                },
                            )

                        # For WebSocket upgrades, let the handler validate the session key
                        # This ensures TestClient can catch WebSocketDisconnect exceptions
                        # The handler will validate and raise WebSocketDisconnect if invalid
                        logger.info(
                            f"✅ WebSocket session key provided for {path} - "
                            "CSRF validation bypassed (session key will be validated in handler)"
                        )
                    elif ticket:
                        # Ticket-based authentication (preferred)
                        # Get WebSocket ticket store
                        from ..routing.websockets import _global_websocket_ticket_store

                        websocket_ticket_store = _global_websocket_ticket_store

                        # Fallback: Try to get from app state (for backward compatibility)
                        if not websocket_ticket_store:
                            app = request.app
                            apps_checked = []
                            while app:
                                app_title = getattr(app, "title", "unknown")
                                apps_checked.append(app_title)

                                # Get ticket store
                                websocket_ticket_store = getattr(
                                    app.state, "websocket_ticket_store", None
                                )
                                if websocket_ticket_store:
                                    logger.debug(
                                        f"Found websocket_ticket_store on app '{app_title}' "
                                        f"for WebSocket path '{path}' (checked: {apps_checked})"
                                    )
                                    break

                                # Try to get parent app
                                parent_app = getattr(app, "app", None)
                                if parent_app is app:  # Prevent infinite loop
                                    break
                                app = parent_app

                        if not websocket_ticket_store:
                            logger.error(
                                f"❌ WebSocket ticket store not available for {path}. "
                                "Ticket authentication requires websocket_ticket_store "
                                "to be initialized."
                            )
                            return JSONResponse(
                                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                content={
                                    "detail": (
                                        "WebSocket ticket store not available. "
                                        "Server configuration error."
                                    )
                                },
                            )

                        # Validate and consume ticket (atomic operation - single-use)
                        try:
                            logger.info(
                                f"🔍 Validating WebSocket ticket for {path}: "
                                f"ticket={ticket[:16]}... (truncated)"
                            )
                            ticket_data = await websocket_ticket_store.validate_and_consume_ticket(
                                ticket
                            )
                            if not ticket_data:
                                logger.error(
                                    f"❌ WebSocket ticket validation failed for {path}. "
                                    f"Ticket: {ticket[:16]}... "
                                    "Ticket may be expired, invalid, or already used."
                                )
                                return JSONResponse(
                                    status_code=status.HTTP_403_FORBIDDEN,
                                    content={
                                        "detail": (
                                            "WebSocket ticket expired or invalid. "
                                            "Generate a new ticket via /auth/ticket endpoint."
                                        )
                                    },
                                )

                            # Store ticket data in request state for WebSocket handler
                            request.state.websocket_session = ticket_data
                            logger.info(
                                f"✅ WebSocket ticket validated for {path} "
                                f"(user_id: {ticket_data.get('user_id')}, "
                                f"user_email: {ticket_data.get('user_email')})"
                            )
                        except (
                            ValueError,
                            TypeError,
                            AttributeError,
                            RuntimeError,
                        ) as e:
                            logger.error(
                                f"❌ Error validating WebSocket ticket for {path}: {e}",
                                exc_info=True,
                            )
                            return JSONResponse(
                                status_code=status.HTTP_403_FORBIDDEN,
                                content={
                                    "detail": "WebSocket ticket validation error. "
                                    "Generate a new ticket."
                                },
                            )
                    else:
                        # Fallback to CSRF cookie validation (backward compatibility)
                        # For WebSocket, CSRF header is optional (JS can't set headers on upgrade)
                        # but if provided, it must match the cookie
                        cookie_token = request.cookies.get(self.cookie_name)
                        header_token = request.headers.get(self.header_name)

                        if not cookie_token:
                            logger.error(
                                f"❌ WebSocket upgrade missing CSRF cookie for {path}. "
                                "CSRF protection is required. "
                                "Generate ticket via /auth/ticket endpoint or include CSRF cookie."
                            )
                            return JSONResponse(
                                status_code=status.HTTP_403_FORBIDDEN,
                                content={
                                    "detail": (
                                        "CSRF token missing. "
                                        "Generate ticket via /auth/ticket endpoint "
                                        "or include CSRF cookie/token."
                                    )
                                },
                            )

                        # If header is provided, validate it matches the cookie
                        if header_token:
                            if not hmac.compare_digest(cookie_token, header_token):
                                logger.error(
                                    f"❌ WebSocket CSRF token mismatch for {path}. "
                                    "Header token does not match cookie token."
                                )
                                return JSONResponse(
                                    status_code=status.HTTP_403_FORBIDDEN,
                                    content={"detail": "CSRF token invalid."},
                                )

                        # Validate CSRF token (check signature if secret is used)
                        if not self._validate_csrf_token(cookie_token, request):
                            logger.error(f"❌ WebSocket CSRF token validation failed for {path}")
                            return JSONResponse(
                                status_code=status.HTTP_403_FORBIDDEN,
                                content={"detail": "CSRF token validation failed."},
                            )

                        logger.info(
                            f"✅ WebSocket CSRF cookie validated for {path} "
                            "(backward compatibility mode)"
                        )
                elif auth_token_cookie and not csrf_required:
                    logger.info(
                        f"✅ WebSocket CSRF validation skipped for {path} "
                        f"(csrf_required=false) - only origin validation performed"
                    )
                elif not auth_token_cookie:
                    logger.info(
                        f"✅ WebSocket connection allowed for {path} "
                        f"(no auth cookie - WebSocket handler will authenticate)"
                    )

                validation_status = (
                    "CSRF/ticket validated"
                    if auth_token_cookie and csrf_required
                    else "CSRF skipped"
                )
                logger.info(
                    f"✅ WebSocket upgrade CSRF validation passed for {path} "
                    f"(Origin validated, {validation_status})"
                )

                # Origin validated (and CSRF/ticket validated if authenticated
                # and csrf_required=true)
                # Allow WebSocket upgrade to proceed to WebSocket handler
                logger.debug(f"✅ WebSocket upgrade request allowed to proceed: {path}")
                return await call_next(request)

        except (
            AttributeError,
            KeyError,
            RuntimeError,
            ValueError,
            TypeError,
            ConnectionError,
        ) as e:
            # Catch exceptions in WebSocket handling to see what's failing
            logger.error(
                f"❌ CRITICAL: Exception in CSRF middleware WebSocket handling: {e}", exc_info=True
            )
            import sys

            print(f"❌ [CSRF MIDDLEWARE EXCEPTION] {e}", file=sys.stderr, flush=True)
            # Re-raise to see the full error
            raise

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
