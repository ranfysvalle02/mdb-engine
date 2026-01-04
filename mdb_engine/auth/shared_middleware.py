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
from typing import Any, Callable, Dict, List, Optional

import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .shared_users import SharedUserPool

logger = logging.getLogger(__name__)

# Cookie and header names for JWT token
AUTH_COOKIE_NAME = "mdb_auth_token"
AUTH_HEADER_NAME = "Authorization"
AUTH_HEADER_PREFIX = "Bearer "


def _get_client_ip(request: Request) -> Optional[str]:
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
        user_pool: Optional[SharedUserPool],
        app_slug: str,
        require_role: Optional[str] = None,
        public_routes: Optional[List[str]] = None,
        role_hierarchy: Optional[Dict[str, List[str]]] = None,
        session_binding: Optional[Dict[str, Any]] = None,
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
        self._cookie_name = cookie_name
        self._header_name = header_name
        self._header_prefix = header_prefix

        logger.info(
            f"SharedAuthMiddleware initialized for '{app_slug}' "
            f"(require_role={require_role}, public_routes={len(self._public_routes)}, "
            f"session_binding={bool(self._session_binding)})"
        )

    def get_user_pool(self, request: Request) -> Optional[SharedUserPool]:
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

        is_public = self._is_public_route(request.url.path)

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
    ) -> Optional[str]:
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

            # Check fingerprint binding (soft check - just warn)
            if self._session_binding.get("bind_fingerprint", True):
                token_fp = payload.get("fp")
                if token_fp:
                    client_fp = _compute_fingerprint(request)
                    if client_fp != token_fp:
                        logger.warning(
                            f"Session fingerprint mismatch for user {payload.get('email')}"
                        )
                        # Soft check - don't reject, just log
                        # Could be legitimate (browser update, different device)

            return None

        except jwt.InvalidTokenError as e:
            logger.warning(f"Error validating session binding: {e}")
            return None  # Don't reject for binding check errors

    def _extract_token(self, request: Request) -> Optional[str]:
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
    manifest_auth: Dict[str, Any],
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
            )

    return ConfiguredSharedAuthMiddleware


def create_shared_auth_middleware_lazy(
    app_slug: str,
    manifest_auth: Dict[str, Any],
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

    # Build role hierarchy from manifest if available
    role_hierarchy = None
    roles = manifest_auth.get("roles", [])
    if roles and len(roles) > 1:
        # Auto-generate hierarchy: each role inherits from roles below it
        role_hierarchy = {}
        for i, role in enumerate(roles):
            if i > 0:
                role_hierarchy[role] = roles[:i]

    # Session binding configuration
    session_binding = manifest_auth.get("session_binding", {})

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
            user_pool: Optional[SharedUserPool] = getattr(request.app.state, "user_pool", None)

            if user_pool is None:
                # User pool not initialized yet, skip auth
                logger.warning(
                    f"LazySharedAuthMiddleware: user_pool not found on app.state for '{app_slug}'"
                )
                return await call_next(request)

            is_public = self._is_public_route(request.url.path)

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

        def _extract_token(self, request: Request) -> Optional[str]:
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

        async def _validate_session_binding(
            self,
            request: Request,
            token: str,
        ) -> Optional[str]:
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
                            logger.warning(
                                f"Session IP mismatch: token={token_ip}, client={client_ip}"
                            )
                            return "Session bound to different IP address"

                # Check fingerprint binding (soft check - just warn)
                if self._session_binding.get("bind_fingerprint", True):
                    token_fp = payload.get("fp")
                    if token_fp:
                        client_fp = _compute_fingerprint(request)
                        if client_fp != token_fp:
                            logger.warning(
                                f"Session fingerprint mismatch for user {payload.get('email')}"
                            )
                            # Soft check - don't reject, just log

                return None

            except jwt.InvalidTokenError as e:
                logger.warning(f"Error validating session binding: {e}")
                return None  # Don't reject for binding check errors

    return LazySharedAuthMiddleware
