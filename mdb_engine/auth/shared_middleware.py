"""
Shared Auth Middleware for Multi-App SSO

ASGI middleware that handles authentication for apps using "shared" auth mode.
Automatically validates JWT tokens and populates request.state with user info.

This module is part of MDB_ENGINE - MongoDB Engine.

Usage (auto-configured by engine.create_app() when auth.mode="shared"):
    # Middleware is automatically added when manifest has auth.mode="shared"

    # Access user in route handlers:
    @app.get("/protected")
    async def protected(request: Request):
        user = request.state.user  # None if not authenticated
        if not user:
            raise HTTPException(status_code=401)
        return {"email": user["email"]}

Manual usage:
    from mdb_engine.auth import SharedAuthMiddleware, SharedUserPool

    pool = SharedUserPool(db)
    app.add_middleware(
        SharedAuthMiddleware,
        user_pool=pool,
        require_role="viewer",
        public_routes=["/health", "/api/public/*"],
    )
"""

import fnmatch
import hashlib
import logging
from collections.abc import Callable
from typing import Any

import jwt
from pymongo.errors import PyMongoError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .shared_users import SharedUserPool

logger = logging.getLogger(__name__)

# Cookie and header names for JWT token
AUTH_COOKIE_NAME = "mdb_auth_token"
AUTH_HEADER_NAME = "Authorization"
AUTH_HEADER_PREFIX = "Bearer "


def _get_client_ip(request: Request) -> str | None:
    """Extract client IP address from request, handling proxies."""
    # Check X-Forwarded-For header (behind load balancer/proxy)
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        # Take the first IP (original client)
        return forwarded_for.split(",")[0].strip()

    # Check X-Real-IP header
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip

    # Fall back to direct client
    if request.client:
        return request.client.host

    return None


def _compute_fingerprint(request: Request) -> str:
    """Compute a device fingerprint from request characteristics."""
    components = [
        request.headers.get("user-agent", ""),
        request.headers.get("accept-language", ""),
        request.headers.get("accept-encoding", ""),
    ]
    fingerprint_string = "|".join(components)
    return hashlib.sha256(fingerprint_string.encode()).hexdigest()


def _get_request_path(request: Request) -> str:
    """
    Get the request path relative to the mount point.

    For mounted apps (via create_multi_app), strips the path prefix from
    request.url.path using request.state.app_base_path. For non-mounted apps,
    uses request.scope["path"] if available, otherwise falls back to request.url.path.

    This ensures public routes in manifests (which are relative paths like "/")
    match correctly when apps are mounted at prefixes like "/auth-hub".

    SECURITY: Normalizes and validates paths to prevent path traversal attacks.
    """

    # Check if this is a mounted app with a path prefix
    app_base_path = getattr(request.state, "app_base_path", None)
    # Ensure app_base_path is a string (not a MagicMock in tests)
    if app_base_path and isinstance(app_base_path, str):
        # Ensure request.url.path is a string before calling startswith
        url_path = str(request.url.path) if hasattr(request.url, "path") else None
        if url_path and url_path.startswith(app_base_path):
            # Strip the path prefix to get relative path
            relative_path = url_path[len(app_base_path) :]
            # Normalize and sanitize path to prevent traversal attacks
            relative_path = _normalize_path(relative_path)
            # Ensure path starts with / (handle case where prefix is entire path)
            return relative_path if relative_path else "/"

    # Fall back to scope["path"] for mounted apps (if available)
    # This handles cases where Starlette/FastAPI sets it correctly
    if "path" in request.scope:
        return _normalize_path(request.scope["path"])

    # Default to url.path for non-mounted apps
    # Ensure we return a string
    if hasattr(request.url, "path"):
        return _normalize_path(str(request.url.path))
    return "/"


def _normalize_path(path: str) -> str:
    """
    Normalize and sanitize a path to prevent path traversal attacks.

    Args:
        path: Raw path string

    Returns:
        Normalized path starting with /
    """
    from pathlib import PurePath
    from urllib.parse import unquote

    if not path:
        return "/"

    # Preserve trailing slash (except for root)
    has_trailing_slash = path.endswith("/") and path != "/"

    # Decode URL encoding
    try:
        decoded = unquote(path)
    except (ValueError, UnicodeDecodeError):
        # If decoding fails, use original path
        decoded = path

    # Normalize path separators and resolve relative components
    try:
        # Use PurePath to normalize without accessing filesystem
        normalized = PurePath(decoded).as_posix()
    except (ValueError, TypeError):
        # If normalization fails, use decoded path
        normalized = decoded

    # Reject path traversal attempts
    if ".." in normalized or normalized.startswith("/") and normalized != "/":
        # Check if it's a legitimate absolute path (starts with /)
        if normalized.startswith("/") and ".." not in normalized:
            # Valid absolute path
            pass
        else:
            logger.warning(f"Path traversal attempt detected: {path} -> {normalized}")
            return "/"  # Return root path for safety

    # Ensure path starts with /
    if not normalized.startswith("/"):
        normalized = "/" + normalized

    # Restore trailing slash if it was present (except for root)
    if has_trailing_slash and normalized != "/" and not normalized.endswith("/"):
        normalized = normalized + "/"

    return normalized


class SharedAuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware for shared authentication across multi-app deployments.

    Features:
    - Reads JWT from cookie or Authorization header
    - Validates token and populates request.state.user
    - Checks role requirements if configured
    - Skips authentication for public routes
    - Returns 401/403 JSON responses for auth failures

    The middleware sets:
    - request.state.user: Dict with user info (or None if not authenticated)
    - request.state.user_roles: List of user's roles for current app
    """

    def __init__(
        self,
        app: Callable,
        user_pool: SharedUserPool | None,
        app_slug: str,
        require_role: str | None = None,
        public_routes: list[str] | None = None,
        role_hierarchy: dict[str, list[str]] | None = None,
        session_binding: dict[str, Any] | None = None,
        auto_assign_default_role: bool = False,
        cookie_name: str = AUTH_COOKIE_NAME,
        header_name: str = AUTH_HEADER_NAME,
        header_prefix: str = AUTH_HEADER_PREFIX,
    ):
        """
        Initialize shared auth middleware.

        Args:
            app: ASGI application
            user_pool: SharedUserPool instance (optional for lazy loading)
            app_slug: Current app's slug (for role checking)
            require_role: Role required to access this app (None = no role check)
            public_routes: List of route patterns that don't require auth.
                          Supports wildcards, e.g., ["/health", "/api/public/*"]
            role_hierarchy: Optional role hierarchy for inheritance
            session_binding: Session binding configuration:
                - bind_ip: Strict - reject if IP changes
                - bind_fingerprint: Soft - log warning if fingerprint changes
                - allow_ip_change_with_reauth: Allow IP change on re-authentication
            auto_assign_default_role: If True, automatically assign require_role to users
                                     with no roles for this app (default: False).
                                     SECURITY: Only enable if explicitly needed - requires
                                     default_role in manifest to match require_role.
            cookie_name: Name of auth cookie (default: mdb_auth_token)
            header_name: Name of auth header (default: Authorization)
            header_prefix: Prefix for header value (default: "Bearer ")
        """
        super().__init__(app)
        self._user_pool = user_pool
        self._app_slug = app_slug
        self._require_role = require_role
        self._public_routes = public_routes or []
        self._role_hierarchy = role_hierarchy
        self._session_binding = session_binding or {}
        self._auto_assign_default_role = auto_assign_default_role
        self._cookie_name = cookie_name
        self._header_name = header_name
        self._header_prefix = header_prefix

        logger.info(
            f"SharedAuthMiddleware initialized for '{app_slug}' "
            f"(require_role={require_role}, public_routes={len(self._public_routes)}, "
            f"session_binding={bool(self._session_binding)})"
        )

    def get_user_pool(self, request: Request) -> SharedUserPool | None:
        """Get the user pool instance. Override in subclasses for lazy loading."""
        return self._user_pool

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Response],
    ) -> Response:
        """Process request through auth middleware."""
        # Initialize request state
        request.state.user = None
        request.state.user_roles = []

        # Get user pool
        user_pool = self.get_user_pool(request)
        if not user_pool:
            # User pool not available (e.g., lazy loading failed), skip auth if not strict
            # But here we default to skipping for robustness if pool is missing
            # However, for Lazy middleware, we want to skip if not initialized yet
            return await call_next(request)

        is_public = self._is_public_route(_get_request_path(request))

        # Extract token from cookie or header
        token = self._extract_token(request)

        if not token:
            # No token provided
            if not is_public and self._require_role:
                return self._unauthorized_response("Authentication required")
            # No role required or public route, continue without user
            return await call_next(request)

        # Validate token and get user
        user = await user_pool.validate_token(token)

        if not user:
            # Invalid token - for public routes, continue without user
            if is_public:
                return await call_next(request)
            return self._unauthorized_response("Invalid or expired token")

        # Validate session binding if configured
        binding_error = await self._validate_session_binding(request, token)
        if binding_error:
            if is_public:
                # For public routes, log but continue
                logger.warning(f"Session binding mismatch on public route: {binding_error}")
            else:
                return self._forbidden_response(binding_error)

        # Set user on request state
        request.state.user = user
        request.state.user_roles = SharedUserPool.get_user_roles_for_app(user, self._app_slug)

        # Check role requirement (only for non-public routes)
        if not is_public and self._require_role:
            user_roles = request.state.user_roles
            has_required_role = SharedUserPool.user_has_role(
                user,
                self._app_slug,
                self._require_role,
                self._role_hierarchy,
            )

            if not has_required_role:
                # Auto-assign required role ONLY if explicitly enabled and user has no roles
                # SECURITY: This is opt-in to prevent privilege escalation. Only enable if
                # explicitly needed and default_role matches require_role in manifest.
                if not user_roles and self._auto_assign_default_role:
                    user_email = user.get("email")
                    if user_email:
                        try:
                            # Auto-assign the required role
                            success = await user_pool.update_user_roles(
                                user_email, self._app_slug, [self._require_role]
                            )
                            if success:
                                # Refresh user data to include new role
                                user = await user_pool.get_user_by_email(user_email)
                                if user:
                                    request.state.user = user
                                    request.state.user_roles = [self._require_role]
                                    logger.info(
                                        f"Auto-assigned role '{self._require_role}' to user "
                                        f"{user_email} for app '{self._app_slug}' "
                                        f"(auto_assign_default_role enabled)"
                                    )
                                else:
                                    logger.warning(
                                        f"Failed to refresh user after auto-assigning role: "
                                        f"{user_email}"
                                    )
                            else:
                                logger.warning(
                                    f"Failed to auto-assign role '{self._require_role}' to "
                                    f"user {user_email} for app '{self._app_slug}'"
                                )
                        except (PyMongoError, ValueError, AttributeError) as e:
                            logger.error(
                                f"Error auto-assigning role to user {user_email}: {e}",
                                exc_info=True,
                            )

                # Check again after potential auto-assignment
                if not SharedUserPool.user_has_role(
                    user,
                    self._app_slug,
                    self._require_role,
                    self._role_hierarchy,
                ):
                    return self._forbidden_response(
                        f"Role '{self._require_role}' required for this app"
                    )

        return await call_next(request)

    async def _validate_session_binding(
        self,
        request: Request,
        token: str,
    ) -> str | None:
        """
        Validate session binding claims in token.

        Returns error message if validation fails, None if OK.
        """
        if not self._session_binding:
            return None

        try:
            # Decode token without verification to get claims
            # (verification already done in validate_token)
            payload = jwt.decode(token, options={"verify_signature": False})

            # Check IP binding
            if self._session_binding.get("bind_ip", False):
                token_ip = payload.get("ip")
                if token_ip:
                    client_ip = _get_client_ip(request)
                    if client_ip and client_ip != token_ip:
                        logger.warning(f"Session IP mismatch: token={token_ip}, client={client_ip}")
                        return "Session bound to different IP address"

            # Check fingerprint binding (strict by default for security)
            bind_fingerprint = self._session_binding.get("bind_fingerprint", True)
            strict_fingerprint = self._session_binding.get(
                "strict_fingerprint", True
            )  # Default: strict
            if bind_fingerprint:
                token_fp = payload.get("fp")
                if token_fp:
                    client_fp = _compute_fingerprint(request)
                    if client_fp != token_fp:
                        if strict_fingerprint:
                            logger.warning(
                                f"Session fingerprint mismatch for user {payload.get('email')} - "
                                f"rejecting request (strict_fingerprint=True)"
                            )
                            return "Session bound to different device/fingerprint"
                        else:
                            logger.warning(
                                f"Session fingerprint mismatch for user {payload.get('email')} - "
                                f"allowing (strict_fingerprint=False)"
                            )
                            # Soft check - don't reject, just log
                            # Could be legitimate (browser update, different device)

            return None

        except jwt.InvalidTokenError as e:
            logger.warning(f"Error validating session binding: {e}")
            return None  # Don't reject for binding check errors

    def _extract_token(self, request: Request) -> str | None:
        """Extract JWT token from cookie or header."""
        # Try cookie first
        token = request.cookies.get(self._cookie_name)
        if token:
            return token

        # Try Authorization header
        auth_header = request.headers.get(self._header_name)
        if auth_header and auth_header.startswith(self._header_prefix):
            return auth_header[len(self._header_prefix) :]

        return None

    def _is_public_route(self, path: str) -> bool:
        """Check if path matches any public route pattern."""
        for pattern in self._public_routes:
            # Normalize pattern for fnmatch
            if not pattern.startswith("/"):
                pattern = "/" + pattern

            # Check exact match
            if path == pattern:
                return True

            # Check wildcard match
            if fnmatch.fnmatch(path, pattern):
                return True

            # Check prefix match for patterns ending with /*
            if pattern.endswith("/*"):
                prefix = pattern[:-2]
                if path.startswith(prefix):
                    return True

        return False

    @staticmethod
    def _unauthorized_response(detail: str) -> JSONResponse:
        """Return 401 Unauthorized response."""
        return JSONResponse(
            status_code=401,
            content={"detail": detail, "error": "unauthorized"},
        )

    @staticmethod
    def _forbidden_response(detail: str) -> JSONResponse:
        """Return 403 Forbidden response."""
        return JSONResponse(
            status_code=403,
            content={"detail": detail, "error": "forbidden"},
        )


def create_shared_auth_middleware(
    user_pool: SharedUserPool,
    app_slug: str,
    manifest_auth: dict[str, Any],
) -> type:
    """
    Factory function to create SharedAuthMiddleware configured from manifest.

    Args:
        user_pool: SharedUserPool instance
        app_slug: Current app's slug
        manifest_auth: Auth section from manifest

    Returns:
        Configured middleware class ready to add to FastAPI app

    Usage:
        middleware_class = create_shared_auth_middleware(pool, "my_app", manifest["auth"])
        app.add_middleware(middleware_class)
    """
    require_role = manifest_auth.get("require_role")
    public_routes = manifest_auth.get("public_routes", [])
    auto_assign_default_role = manifest_auth.get("auto_assign_default_role", False)
    default_role = manifest_auth.get("default_role")

    # Security: Only allow auto-assignment if default_role matches require_role
    if auto_assign_default_role and require_role and default_role != require_role:
        logger.warning(
            f"Security: auto_assign_default_role enabled but default_role '{default_role}' "
            f"does not match require_role '{require_role}' for app '{app_slug}'. "
            f"Auto-assignment disabled for security."
        )
        auto_assign_default_role = False

    # Build role hierarchy from manifest if available
    role_hierarchy = None
    roles = manifest_auth.get("roles", [])
    if roles and len(roles) > 1:
        # Auto-generate hierarchy: each role inherits from roles below it
        # e.g., roles=["viewer", "editor", "admin"] -> admin > editor > viewer
        role_hierarchy = {}
        for i, role in enumerate(roles):
            if i > 0:
                role_hierarchy[role] = roles[:i]

    # Create a wrapper class with the configuration baked in
    class ConfiguredSharedAuthMiddleware(SharedAuthMiddleware):
        def __init__(self, app: Callable):
            super().__init__(
                app=app,
                user_pool=user_pool,
                app_slug=app_slug,
                require_role=require_role,
                public_routes=public_routes,
                role_hierarchy=role_hierarchy,
                auto_assign_default_role=auto_assign_default_role,
            )

    return ConfiguredSharedAuthMiddleware


def _build_role_hierarchy(manifest_auth: dict[str, Any]) -> dict[str, list[str]] | None:
    """Build role hierarchy from manifest roles."""
    roles = manifest_auth.get("roles", [])
    if not roles or len(roles) <= 1:
        return None

    # Auto-generate hierarchy: each role inherits from roles below it
    role_hierarchy = {}
    for i, role in enumerate(roles):
        if i > 0:
            role_hierarchy[role] = roles[:i]
    return role_hierarchy


def _is_public_route_helper(path: str, public_routes: list[str]) -> bool:
    """Check if path matches any public route pattern."""
    for pattern in public_routes:
        # Normalize pattern for fnmatch
        if not pattern.startswith("/"):
            pattern = "/" + pattern

        # Check exact match
        if path == pattern:
            return True

        # Check wildcard match
        if fnmatch.fnmatch(path, pattern):
            return True

        # Check prefix match for patterns ending with /*
        if pattern.endswith("/*"):
            prefix = pattern[:-2]
            if path.startswith(prefix):
                return True

    return False


def _extract_token_helper(
    request: Request, cookie_name: str, header_name: str, header_prefix: str
) -> str | None:
    """Extract JWT token from cookie or header."""
    # Try cookie first
    token = request.cookies.get(cookie_name)
    if token:
        return token

    # Try Authorization header
    auth_header = request.headers.get(header_name)
    if auth_header and auth_header.startswith(header_prefix):
        return auth_header[len(header_prefix) :]

    return None


def _create_lazy_middleware_class(  # noqa: C901
    app_slug: str,
    require_role: str | None,
    public_routes: list[str],
    role_hierarchy: dict[str, list[str]] | None,
    session_binding: dict[str, Any],
    auto_assign_default_role: bool = False,
) -> type:
    """Create the LazySharedAuthMiddleware class with configuration."""

    class LazySharedAuthMiddleware(BaseHTTPMiddleware):
        """
        Lazy version of SharedAuthMiddleware that gets user_pool from app.state.

        This enables adding middleware at app creation time while deferring
        the actual user pool initialization to the lifespan startup.
        """

        def __init__(self, app: Callable):
            super().__init__(app)
            self._app_slug = app_slug
            self._require_role = require_role
            self._public_routes = public_routes
            self._role_hierarchy = role_hierarchy
            self._session_binding = session_binding
            self._auto_assign_default_role = auto_assign_default_role
            self._cookie_name = AUTH_COOKIE_NAME
            self._header_name = AUTH_HEADER_NAME
            self._header_prefix = AUTH_HEADER_PREFIX

            logger.info(
                f"LazySharedAuthMiddleware initialized for '{app_slug}' "
                f"(require_role={require_role}, public_routes={len(self._public_routes)}, "
                f"session_binding={bool(self._session_binding)})"
            )

        async def dispatch(
            self,
            request: Request,
            call_next: Callable[[Request], Response],
        ) -> Response:
            """Process request through auth middleware."""
            # Initialize request state
            request.state.user = None
            request.state.user_roles = []

            # Get user_pool from app.state (set during lifespan)
            user_pool: SharedUserPool | None = getattr(request.app.state, "user_pool", None)

            if user_pool is None:
                # User pool not initialized yet, skip auth
                logger.warning(
                    f"LazySharedAuthMiddleware: user_pool not found on app.state for '{app_slug}'"
                )
                return await call_next(request)

            is_public = _is_public_route_helper(_get_request_path(request), self._public_routes)
            token = _extract_token_helper(
                request, self._cookie_name, self._header_name, self._header_prefix
            )

            # Handle unauthenticated requests
            if not token:
                return await self._handle_no_token(is_public, request, call_next)

            # Authenticate and authorize user
            auth_result = await self._authenticate_and_authorize(
                request, user_pool, token, is_public, call_next
            )
            if auth_result is not None:
                return auth_result

            return await call_next(request)

        async def _authenticate_and_authorize(
            self,
            request: Request,
            user_pool: SharedUserPool,
            token: str,
            is_public: bool,
            call_next: Callable[[Request], Response],
        ) -> Response | None:
            """Authenticate user and check authorization."""
            # Validate token and get user
            user = await user_pool.validate_token(token)
            if not user:
                return await self._handle_invalid_token(is_public, request, call_next)

            # Validate session binding if configured
            binding_error = await self._validate_session_binding(request, token)
            if binding_error:
                return await self._handle_binding_error(
                    binding_error, is_public, request, call_next
                )

            # Set user on request state
            request.state.user = user
            request.state.user_roles = SharedUserPool.get_user_roles_for_app(user, self._app_slug)

            # Check role requirement (only for non-public routes)
            if not is_public and self._require_role:
                role_check_result = await self._check_and_assign_role(user, user_pool, request)
                if role_check_result is not None:
                    return role_check_result

            return None

        async def _handle_no_token(
            self,
            is_public: bool,
            request: Request,
            call_next: Callable[[Request], Response],
        ) -> Response:
            """Handle request with no token."""
            if not is_public and self._require_role:
                return self._unauthorized_response("Authentication required")
            return await call_next(request)

        async def _handle_invalid_token(
            self,
            is_public: bool,
            request: Request,
            call_next: Callable[[Request], Response],
        ) -> Response:
            """Handle request with invalid token."""
            if is_public:
                return await call_next(request)
            return self._unauthorized_response("Invalid or expired token")

        async def _handle_binding_error(
            self,
            binding_error: str,
            is_public: bool,
            request: Request,
            call_next: Callable[[Request], Response],
        ) -> Response:
            """Handle session binding validation error."""
            if is_public:
                logger.warning(f"Session binding mismatch on public route: {binding_error}")
                return await call_next(request)
            return self._forbidden_response(binding_error)

        @staticmethod
        def _unauthorized_response(detail: str) -> JSONResponse:
            """Return 401 Unauthorized response."""
            return JSONResponse(
                status_code=401,
                content={"detail": detail, "error": "unauthorized"},
            )

        @staticmethod
        def _forbidden_response(detail: str) -> JSONResponse:
            """Return 403 Forbidden response."""
            return JSONResponse(
                status_code=403,
                content={"detail": detail, "error": "forbidden"},
            )

        async def _check_and_assign_role(
            self,
            user: dict[str, Any],
            user_pool: SharedUserPool,
            request: Request,
        ) -> Response | None:
            """
            Check if user has required role and auto-assign if needed.

            Returns Response if access should be denied, None if OK.
            """
            user_roles = request.state.user_roles
            has_required_role = SharedUserPool.user_has_role(
                user,
                self._app_slug,
                self._require_role,
                self._role_hierarchy,
            )

            if has_required_role:
                return None

            # Auto-assign required role ONLY if explicitly enabled and user has no roles
            # SECURITY: This is opt-in to prevent privilege escalation
            if not user_roles and self._auto_assign_default_role:
                await self._try_auto_assign_role(user, user_pool, request)

            # Check again after potential auto-assignment
            if not SharedUserPool.user_has_role(
                user,
                self._app_slug,
                self._require_role,
                self._role_hierarchy,
            ):
                return self._forbidden_response(
                    f"Role '{self._require_role}' required for this app"
                )

            return None

        async def _try_auto_assign_role(
            self,
            user: dict[str, Any],
            user_pool: SharedUserPool,
            request: Request,
        ) -> None:
            """
            Attempt to auto-assign required role to user.

            SECURITY: Only called if auto_assign_default_role is enabled and user has
            no roles. This prevents privilege escalation.
            """
            user_email = user.get("email")
            if not user_email:
                return

            try:
                # Auto-assign the required role
                success = await user_pool.update_user_roles(
                    user_email, self._app_slug, [self._require_role]
                )
                if success:
                    # Refresh user data to include new role
                    updated_user = await user_pool.get_user_by_email(user_email)
                    if updated_user:
                        request.state.user = updated_user
                        request.state.user_roles = [self._require_role]
                        logger.info(
                            f"Auto-assigned role '{self._require_role}' to user "
                            f"{user_email} for app '{self._app_slug}' "
                            f"(auto_assign_default_role enabled)"
                        )
                    else:
                        logger.warning(
                            f"Failed to refresh user after auto-assigning role: " f"{user_email}"
                        )
                else:
                    logger.warning(
                        f"Failed to auto-assign role '{self._require_role}' to "
                        f"user {user_email} for app '{self._app_slug}'"
                    )
            except (PyMongoError, ValueError, AttributeError) as e:
                logger.error(
                    f"Error auto-assigning role to user {user_email}: {e}",
                    exc_info=True,
                )

        async def _validate_session_binding(
            self,
            request: Request,
            token: str,
        ) -> str | None:
            """
            Validate session binding claims in token.

            Returns error message if validation fails, None if OK.
            """
            if not self._session_binding:
                return None

            try:
                # Decode token without verification to get claims
                # (verification already done in validate_token)
                payload = jwt.decode(token, options={"verify_signature": False})

                # Check IP binding
                ip_error = self._check_ip_binding(request, payload)
                if ip_error:
                    return ip_error

                # Check fingerprint binding (strict by default)
                fingerprint_error = await self._check_fingerprint_binding(request, payload)
                if fingerprint_error:
                    return fingerprint_error

                return None

            except jwt.InvalidTokenError as e:
                logger.warning(f"Error validating session binding: {e}")
                return None  # Don't reject for binding check errors

        def _check_ip_binding(self, request: Request, payload: dict) -> str | None:
            """Check IP binding from token payload."""
            if not self._session_binding.get("bind_ip", False):
                return None

            token_ip = payload.get("ip")
            if not token_ip:
                return None

            client_ip = _get_client_ip(request)
            if client_ip and client_ip != token_ip:
                logger.warning(f"Session IP mismatch: token={token_ip}, client={client_ip}")
                return "Session bound to different IP address"

            return None

        async def _check_fingerprint_binding(self, request: Request, payload: dict) -> str | None:
            """
            Check fingerprint binding from token payload.

            Returns error message if validation fails, None if OK.
            """
            if not self._session_binding.get("bind_fingerprint", True):
                return None

            token_fp = payload.get("fp")
            if not token_fp:
                return None

            strict_fingerprint = self._session_binding.get(
                "strict_fingerprint", True
            )  # Default: strict
            client_fp = _compute_fingerprint(request)
            if client_fp != token_fp:
                if strict_fingerprint:
                    logger.warning(
                        f"Session fingerprint mismatch for user {payload.get('email')} - "
                        f"rejecting request (strict_fingerprint=True)"
                    )
                    return "Session bound to different device/fingerprint"
                else:
                    logger.warning(
                        f"Session fingerprint mismatch for user {payload.get('email')} - "
                        f"allowing (strict_fingerprint=False)"
                    )
                    # Soft check - don't reject, just log
                    return None
            return None

    return LazySharedAuthMiddleware


def create_shared_auth_middleware_lazy(
    app_slug: str,
    manifest_auth: dict[str, Any],
) -> type:
    """
    Factory function to create a lazy SharedAuthMiddleware that reads user_pool from app.state.

    This allows middleware to be added at app creation time (before startup),
    while the actual SharedUserPool is initialized during the lifespan.
    The middleware accesses `request.app.state.user_pool` at request time.

    Args:
        app_slug: Current app's slug
        manifest_auth: Auth section from manifest

    Returns:
        Configured middleware class ready to add to FastAPI app

    Usage:
        # At app creation time:
        middleware_class = create_shared_auth_middleware_lazy("my_app", manifest["auth"])
        app.add_middleware(middleware_class)

        # During lifespan startup:
        app.state.user_pool = SharedUserPool(db)
    """
    require_role = manifest_auth.get("require_role")
    public_routes = manifest_auth.get("public_routes", [])
    auto_assign_default_role = manifest_auth.get("auto_assign_default_role", False)
    default_role = manifest_auth.get("default_role")

    # Security: Only allow auto-assignment if default_role matches require_role
    if auto_assign_default_role and require_role and default_role != require_role:
        logger.warning(
            f"Security: auto_assign_default_role enabled but default_role '{default_role}' "
            f"does not match require_role '{require_role}' for app '{app_slug}'. "
            f"Auto-assignment disabled for security."
        )
        auto_assign_default_role = False

    role_hierarchy = _build_role_hierarchy(manifest_auth)
    session_binding = manifest_auth.get("session_binding", {})

    return _create_lazy_middleware_class(
        app_slug,
        require_role,
        public_routes,
        role_hierarchy,
        session_binding,
        auto_assign_default_role,
    )
