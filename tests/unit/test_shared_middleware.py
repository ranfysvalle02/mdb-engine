"""
Unit tests for SharedAuthMiddleware.

Tests the shared auth middleware functionality for multi-app SSO.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestSharedAuthMiddleware:
    """Tests for SharedAuthMiddleware class."""

    @pytest.fixture
    def mock_user_pool(self):
        """Create a mock SharedUserPool."""
        pool = MagicMock()
        pool.validate_token = AsyncMock()
        pool.get_user_roles_for_app = MagicMock(return_value=[])
        pool.user_has_role = MagicMock(return_value=False)
        pool.update_user_roles = AsyncMock(return_value=False)
        pool.get_user_by_email = AsyncMock(return_value=None)
        return pool

    @pytest.fixture
    def mock_app(self):
        """Create a mock ASGI app."""
        return AsyncMock()

    @pytest.fixture
    def middleware(self, mock_app, mock_user_pool):
        """Create a SharedAuthMiddleware instance."""
        from mdb_engine.auth.shared_middleware import SharedAuthMiddleware

        return SharedAuthMiddleware(
            app=mock_app,
            user_pool=mock_user_pool,
            app_slug="test_app",
            require_role=None,
            public_routes=["/health", "/api/public/*"],
        )

    @pytest.fixture
    def middleware_with_role(self, mock_app, mock_user_pool):
        """Create a middleware that requires a role."""
        from mdb_engine.auth.shared_middleware import SharedAuthMiddleware

        return SharedAuthMiddleware(
            app=mock_app,
            user_pool=mock_user_pool,
            app_slug="test_app",
            require_role="viewer",
            public_routes=["/health"],
        )

    def test_init(self, middleware, mock_user_pool):
        """Test middleware initialization."""
        assert middleware._user_pool == mock_user_pool  # noqa: SLF001
        assert middleware._app_slug == "test_app"  # noqa: SLF001
        assert middleware._require_role is None  # noqa: SLF001
        assert "/health" in middleware._public_routes  # noqa: SLF001

    def test_is_public_route_exact_match(self, middleware):
        """Test public route detection with exact match."""
        assert middleware._is_public_route("/health") is True  # noqa: SLF001
        assert middleware._is_public_route("/protected") is False  # noqa: SLF001

    def test_is_public_route_wildcard(self, middleware):
        """Test public route detection with wildcard."""
        assert middleware._is_public_route("/api/public/endpoint") is True  # noqa: SLF001
        assert middleware._is_public_route("/api/public/nested/path") is True  # noqa: SLF001
        assert middleware._is_public_route("/api/private") is False  # noqa: SLF001

    def test_extract_token_from_cookie(self, middleware):
        """Test token extraction from cookie."""
        request = MagicMock()
        request.cookies = {"mdb_auth_token": "test-token"}
        request.headers = {}

        token = middleware._extract_token(request)  # noqa: SLF001
        assert token == "test-token"

    def test_extract_token_from_header(self, middleware):
        """Test token extraction from Authorization header."""
        request = MagicMock()
        request.cookies = {}
        request.headers = {"Authorization": "Bearer test-token-header"}

        token = middleware._extract_token(request)  # noqa: SLF001
        assert token == "test-token-header"

    def test_extract_token_none(self, middleware):
        """Test token extraction when no token present."""
        request = MagicMock()
        request.cookies = {}
        request.headers = {}

        token = middleware._extract_token(request)  # noqa: SLF001
        assert token is None

    def test_extract_token_invalid_header(self, middleware):
        """Test token extraction with invalid header format."""
        request = MagicMock()
        request.cookies = {}
        request.headers = {"Authorization": "Basic credentials"}  # Not Bearer

        token = middleware._extract_token(request)  # noqa: SLF001
        assert token is None

    @pytest.mark.asyncio
    async def test_dispatch_public_route(self, middleware, mock_app):
        """Test public routes bypass auth but still populate user if token present."""
        request = MagicMock()
        request.url.path = "/health"
        request.scope = {}  # Empty scope - will fall back to url.path
        request.cookies = {}  # No token
        request.headers = {}
        request.state = MagicMock()

        call_next = AsyncMock(return_value=MagicMock())

        response = await middleware.dispatch(request, call_next)

        # Should call next without checking auth
        call_next.assert_called_once()
        assert request.state.user is None
        assert request.state.user_roles == []

    @pytest.mark.asyncio
    async def test_dispatch_no_token_no_role_required(self, middleware, mock_user_pool):
        """Test request without token when no role is required."""
        request = MagicMock()
        request.url.path = "/protected"
        request.scope = {}  # Empty scope - will fall back to url.path
        request.cookies = {}
        request.headers = {}
        request.state = MagicMock()

        call_next = AsyncMock(return_value=MagicMock())

        response = await middleware.dispatch(request, call_next)

        # Should proceed without user
        call_next.assert_called_once()
        assert request.state.user is None

    @pytest.mark.asyncio
    async def test_dispatch_no_token_role_required(self, middleware_with_role):
        """Test request without token when role is required."""
        request = MagicMock()
        request.url.path = "/protected"
        request.scope = {}  # Empty scope - will fall back to url.path
        request.cookies = {}
        request.headers = {}
        request.state = MagicMock()

        call_next = AsyncMock()

        response = await middleware_with_role.dispatch(request, call_next)

        # Should return 401
        assert response.status_code == 401
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_valid_token(self, middleware, mock_user_pool):
        """Test request with valid token."""
        from mdb_engine.auth.shared_users import SharedUserPool

        request = MagicMock()
        request.url.path = "/protected"
        request.scope = {}  # Empty scope - will fall back to url.path
        request.cookies = {"mdb_auth_token": "valid-token"}
        request.headers = {}
        request.state = MagicMock()

        user = {"email": "test@example.com", "app_roles": {"test_app": ["viewer"]}}
        mock_user_pool.validate_token.return_value = user

        with patch.object(SharedUserPool, "get_user_roles_for_app", return_value=["viewer"]):
            call_next = AsyncMock(return_value=MagicMock())
            response = await middleware.dispatch(request, call_next)

        assert request.state.user == user
        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_invalid_token(self, middleware, mock_user_pool):
        """Test request with invalid token."""
        request = MagicMock()
        request.url.path = "/protected"
        request.scope = {}  # Empty scope - will fall back to url.path
        request.cookies = {"mdb_auth_token": "invalid-token"}
        request.headers = {}
        request.state = MagicMock()

        mock_user_pool.validate_token.return_value = None

        call_next = AsyncMock()

        response = await middleware.dispatch(request, call_next)

        # Should return 401
        assert response.status_code == 401
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_missing_role(self, middleware_with_role, mock_user_pool):
        """Test request when user lacks required role."""
        from mdb_engine.auth.shared_users import SharedUserPool

        request = MagicMock()
        request.url.path = "/protected"
        request.scope = {}  # Empty scope - will fall back to url.path
        request.cookies = {"mdb_auth_token": "valid-token"}
        request.headers = {}
        request.state = MagicMock()

        user = {"email": "test@example.com", "app_roles": {}}
        mock_user_pool.validate_token.return_value = user

        with patch.object(SharedUserPool, "get_user_roles_for_app", return_value=[]):
            with patch.object(SharedUserPool, "user_has_role", return_value=False):
                call_next = AsyncMock()
                response = await middleware_with_role.dispatch(request, call_next)

        # Should return 403
        assert response.status_code == 403
        call_next.assert_not_called()


class TestCreateSharedAuthMiddleware:
    """Tests for create_shared_auth_middleware factory."""

    def test_create_middleware_class(self):
        """Test creating a configured middleware class."""
        from mdb_engine.auth.shared_middleware import create_shared_auth_middleware
        from mdb_engine.auth.shared_users import SharedUserPool

        mock_pool = MagicMock(spec=SharedUserPool)
        manifest_auth = {
            "mode": "shared",
            "roles": ["viewer", "editor", "admin"],
            "require_role": "viewer",
            "public_routes": ["/health"],
        }

        middleware_class = create_shared_auth_middleware(
            user_pool=mock_pool,
            app_slug="test_app",
            manifest_auth=manifest_auth,
        )

        # Should return a class
        assert callable(middleware_class)

    def test_create_middleware_generates_hierarchy(self):
        """Test that role hierarchy is auto-generated."""
        from mdb_engine.auth.shared_middleware import create_shared_auth_middleware
        from mdb_engine.auth.shared_users import SharedUserPool

        mock_pool = MagicMock(spec=SharedUserPool)
        mock_app = MagicMock()

        manifest_auth = {
            "mode": "shared",
            "roles": ["viewer", "editor", "admin"],
        }

        middleware_class = create_shared_auth_middleware(
            user_pool=mock_pool,
            app_slug="test_app",
            manifest_auth=manifest_auth,
        )

        # Instantiate to check hierarchy was generated
        instance = middleware_class(mock_app)

        # Hierarchy should be: admin > editor > viewer
        assert instance._role_hierarchy is not None  # noqa: SLF001
        assert "admin" in instance._role_hierarchy  # noqa: SLF001
        assert "viewer" in instance._role_hierarchy["admin"]  # noqa: SLF001
        assert "editor" in instance._role_hierarchy["admin"]  # noqa: SLF001


class TestLazySharedAuthMiddleware:
    """Tests for create_shared_auth_middleware_lazy factory."""

    def test_create_lazy_middleware_class(self):
        """Test creating a lazy middleware class without user pool."""
        from mdb_engine.auth.shared_middleware import create_shared_auth_middleware_lazy

        manifest_auth = {
            "mode": "shared",
            "roles": ["viewer", "editor", "admin"],
            "require_role": "viewer",
            "public_routes": ["/health"],
        }

        middleware_class = create_shared_auth_middleware_lazy(
            app_slug="test_app",
            manifest_auth=manifest_auth,
        )

        # Should return a class
        assert callable(middleware_class)

        # Should be able to instantiate without user_pool
        mock_app = MagicMock()
        instance = middleware_class(mock_app)

        assert instance._app_slug == "test_app"  # noqa: SLF001
        assert instance._require_role == "viewer"  # noqa: SLF001
        assert "/health" in instance._public_routes  # noqa: SLF001

    def test_lazy_middleware_generates_hierarchy(self):
        """Test that lazy middleware auto-generates role hierarchy."""
        from mdb_engine.auth.shared_middleware import create_shared_auth_middleware_lazy

        manifest_auth = {
            "mode": "shared",
            "roles": ["viewer", "editor", "admin"],
        }

        middleware_class = create_shared_auth_middleware_lazy(
            app_slug="test_app",
            manifest_auth=manifest_auth,
        )

        mock_app = MagicMock()
        instance = middleware_class(mock_app)

        # Hierarchy should be: admin > editor > viewer
        assert instance._role_hierarchy is not None  # noqa: SLF001
        assert "admin" in instance._role_hierarchy  # noqa: SLF001
        assert "viewer" in instance._role_hierarchy["admin"]  # noqa: SLF001
        assert "editor" in instance._role_hierarchy["admin"]  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_lazy_middleware_no_user_pool(self):
        """Test lazy middleware continues when user_pool not on app.state."""
        from mdb_engine.auth.shared_middleware import create_shared_auth_middleware_lazy

        manifest_auth = {
            "mode": "shared",
            "require_role": "viewer",
            "public_routes": [],
        }

        middleware_class = create_shared_auth_middleware_lazy(
            app_slug="test_app",
            manifest_auth=manifest_auth,
        )

        mock_app = MagicMock()
        instance = middleware_class(mock_app)

        # Create request without user_pool on app.state
        request = MagicMock()
        request.url.path = "/protected"
        request.scope = {}  # Empty scope - will fall back to url.path
        request.cookies = {}
        request.headers = {}
        request.state = MagicMock()
        request.app.state = MagicMock(spec=[])  # No user_pool attribute

        call_next = AsyncMock(return_value=MagicMock())

        response = await instance.dispatch(request, call_next)

        # Should continue without auth check when user_pool is not available
        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_lazy_middleware_with_user_pool(self):
        """Test lazy middleware uses user_pool from app.state."""
        from mdb_engine.auth.shared_middleware import create_shared_auth_middleware_lazy
        from mdb_engine.auth.shared_users import SharedUserPool

        manifest_auth = {
            "mode": "shared",
            "require_role": None,
            "public_routes": [],
        }

        middleware_class = create_shared_auth_middleware_lazy(
            app_slug="test_app",
            manifest_auth=manifest_auth,
        )

        mock_app = MagicMock()
        instance = middleware_class(mock_app)

        # Create mock user pool
        mock_pool = MagicMock()
        user = {"email": "test@example.com", "app_roles": {"test_app": ["viewer"]}}
        mock_pool.validate_token = AsyncMock(return_value=user)

        # Create request with user_pool on app.state
        request = MagicMock()
        request.url.path = "/protected"
        request.scope = {}  # Empty scope - will fall back to url.path
        request.cookies = {"mdb_auth_token": "valid-token"}
        request.headers = {}
        request.state = MagicMock()
        request.app.state.user_pool = mock_pool

        with patch.object(SharedUserPool, "get_user_roles_for_app", return_value=["viewer"]):
            call_next = AsyncMock(return_value=MagicMock())
            response = await instance.dispatch(request, call_next)

        # Should have validated token and set user
        mock_pool.validate_token.assert_called_once_with("valid-token")
        assert request.state.user == user
        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_lazy_middleware_public_route(self):
        """Test lazy middleware bypasses auth enforcement for public routes."""
        from mdb_engine.auth.shared_middleware import create_shared_auth_middleware_lazy

        manifest_auth = {
            "mode": "shared",
            "require_role": "admin",
            "public_routes": ["/health", "/api/public/*"],
        }

        middleware_class = create_shared_auth_middleware_lazy(
            app_slug="test_app",
            manifest_auth=manifest_auth,
        )

        mock_app = MagicMock()
        instance = middleware_class(mock_app)

        # Create request to public route without token
        request = MagicMock()
        request.url.path = "/health"
        request.scope = {}  # Empty scope - will fall back to url.path
        request.cookies = {}  # No token
        request.headers = {}
        request.state = MagicMock()

        mock_pool = MagicMock()
        request.app.state.user_pool = mock_pool

        call_next = AsyncMock(return_value=MagicMock())

        response = await instance.dispatch(request, call_next)

        # Should bypass auth for public route (no token = no validation)
        call_next.assert_called_once()
        mock_pool.validate_token.assert_not_called()


class TestGetRequestPath:
    """Tests for _get_request_path helper function."""

    def test_get_request_path_with_path_prefix(self):
        """Test _get_request_path() strips path prefix when app_base_path is set."""
        from starlette.requests import Request

        from mdb_engine.auth.shared_middleware import _get_request_path

        # Create mock request with path prefix
        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/auth-hub/register"
        request.scope = {}
        request.state = MagicMock()
        request.state.app_base_path = "/auth-hub"

        result = _get_request_path(request)

        assert result == "/register"

    def test_get_request_path_without_path_prefix(self):
        """Test _get_request_path() uses scope path when no app_base_path."""
        from starlette.requests import Request

        from mdb_engine.auth.shared_middleware import _get_request_path

        # Create mock request without path prefix
        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/register"
        request.scope = {"path": "/register"}
        request.state = MagicMock()
        # No app_base_path attribute

        result = _get_request_path(request)

        assert result == "/register"

    def test_get_request_path_prefix_is_entire_path(self):
        """Test _get_request_path() returns '/' when prefix is entire path."""
        from starlette.requests import Request

        from mdb_engine.auth.shared_middleware import _get_request_path

        # Create mock request where prefix matches entire path
        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/auth-hub"
        request.scope = {}
        request.state = MagicMock()
        request.state.app_base_path = "/auth-hub"

        result = _get_request_path(request)

        assert result == "/"

    def test_get_request_path_prefix_not_matching(self):
        """Test _get_request_path() falls back when prefix doesn't match path."""
        from starlette.requests import Request

        from mdb_engine.auth.shared_middleware import _get_request_path

        # Create mock request where prefix doesn't match
        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/other-app/register"
        request.scope = {"path": "/register"}
        request.state = MagicMock()
        request.state.app_base_path = "/auth-hub"

        result = _get_request_path(request)

        # Should fall back to scope path
        assert result == "/register"

    def test_get_request_path_no_scope_path(self):
        """Test _get_request_path() falls back to url.path when no scope path."""
        from starlette.requests import Request

        from mdb_engine.auth.shared_middleware import _get_request_path

        # Create mock request without scope path
        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/register"
        request.scope = {}
        request.state = MagicMock()
        # No app_base_path attribute

        result = _get_request_path(request)

        assert result == "/register"

    def test_get_request_path_with_nested_path_prefix(self):
        """Test _get_request_path() handles nested paths correctly."""
        from starlette.requests import Request

        from mdb_engine.auth.shared_middleware import _get_request_path

        # Create mock request with nested path
        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/auth-hub/api/users/123"
        request.scope = {}
        request.state = MagicMock()
        request.state.app_base_path = "/auth-hub"

        result = _get_request_path(request)

        assert result == "/api/users/123"

    def test_get_request_path_with_trailing_slash_prefix(self):
        """Test _get_request_path() handles trailing slash in prefix correctly."""
        from starlette.requests import Request

        from mdb_engine.auth.shared_middleware import _get_request_path

        # Create mock request with trailing slash in prefix (shouldn't happen but test it)
        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/auth-hub/login"
        request.scope = {}
        request.state = MagicMock()
        request.state.app_base_path = "/auth-hub/"

        result = _get_request_path(request)

        # Should still work - prefix matching handles this
        assert result == "/login" or result == "login"  # Depending on implementation

    def test_get_request_path_with_trailing_slash_in_path(self):
        """Test _get_request_path() handles trailing slash in path correctly."""
        from starlette.requests import Request

        from mdb_engine.auth.shared_middleware import _get_request_path

        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/auth-hub/login/"
        request.scope = {}
        request.state = MagicMock()
        request.state.app_base_path = "/auth-hub"

        result = _get_request_path(request)

        assert result == "/login/"


class TestPublicRoutesWithPathPrefix:
    """Comprehensive tests for public routes with path prefixes to prevent regressions."""

    @pytest.fixture
    def mock_user_pool(self):
        """Create a mock SharedUserPool."""
        pool = MagicMock()
        pool.validate_token = AsyncMock(return_value=None)
        pool.get_user_roles_for_app = MagicMock(return_value=[])
        pool.user_has_role = MagicMock(return_value=False)
        return pool

    @pytest.fixture
    def mock_app(self):
        """Create a mock ASGI app."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_shared_middleware_public_route_with_path_prefix(self, mock_app, mock_user_pool):
        """Test SharedAuthMiddleware correctly identifies public routes with path prefix."""
        from mdb_engine.auth.shared_middleware import SharedAuthMiddleware

        middleware = SharedAuthMiddleware(
            app=mock_app,
            user_pool=mock_user_pool,
            app_slug="test_app",
            require_role="viewer",
            public_routes=["/login", "/register", "/api/public/*"],
        )

        # Create request with path prefix
        request = MagicMock()
        request.url.path = "/auth-hub/login"
        request.scope = {}
        request.state = MagicMock()
        request.state.app_base_path = "/auth-hub"
        request.cookies = {}
        request.headers = {}

        call_next = AsyncMock(return_value=MagicMock())

        response = await middleware.dispatch(request, call_next)

        # Should bypass auth (public route)
        call_next.assert_called_once()
        assert response is not None

    @pytest.mark.asyncio
    async def test_shared_middleware_wildcard_public_route_with_path_prefix(
        self, mock_app, mock_user_pool
    ):  # noqa: E501
        """Test SharedAuthMiddleware correctly handles wildcard public routes with path prefix."""
        from mdb_engine.auth.shared_middleware import SharedAuthMiddleware

        middleware = SharedAuthMiddleware(
            app=mock_app,
            user_pool=mock_user_pool,
            app_slug="test_app",
            require_role="viewer",
            public_routes=["/api/public/*"],
        )

        # Test nested path under wildcard route
        request = MagicMock()
        request.url.path = "/auth-hub/api/public/endpoint"
        request.scope = {}
        request.state = MagicMock()
        request.state.app_base_path = "/auth-hub"
        request.cookies = {}
        request.headers = {}

        call_next = AsyncMock(return_value=MagicMock())

        response = await middleware.dispatch(request, call_next)

        # Should bypass auth (wildcard public route)
        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_lazy_middleware_public_route_with_path_prefix(self, mock_app):
        """Test LazySharedAuthMiddleware correctly identifies public routes with path prefix."""
        from mdb_engine.auth.shared_middleware import create_shared_auth_middleware_lazy

        manifest_auth = {
            "mode": "shared",
            "require_role": "viewer",
            "public_routes": ["/login", "/register"],
        }

        middleware_class = create_shared_auth_middleware_lazy(
            app_slug="test_app",
            manifest_auth=manifest_auth,
        )

        instance = middleware_class(mock_app)

        # Create mock user pool
        mock_pool = MagicMock()
        mock_pool.validate_token = AsyncMock(return_value=None)

        # Create request with path prefix
        request = MagicMock()
        request.url.path = "/auth-hub/login"
        request.scope = {}
        request.state = MagicMock()
        request.state.app_base_path = "/auth-hub"
        request.cookies = {}
        request.headers = {}
        request.app.state.user_pool = mock_pool

        call_next = AsyncMock(return_value=MagicMock())

        response = await instance.dispatch(request, call_next)

        # Should bypass auth (public route)
        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_shared_middleware_protected_route_with_path_prefix(
        self, mock_app, mock_user_pool
    ):  # noqa: E501
        """Test SharedAuthMiddleware correctly protects non-public routes with path prefix."""
        from mdb_engine.auth.shared_middleware import SharedAuthMiddleware

        middleware = SharedAuthMiddleware(
            app=mock_app,
            user_pool=mock_user_pool,
            app_slug="test_app",
            require_role="viewer",
            public_routes=["/login", "/register"],
        )

        # Create request to protected route with path prefix
        request = MagicMock()
        request.url.path = "/auth-hub/protected"
        request.scope = {}
        request.state = MagicMock()
        request.state.app_base_path = "/auth-hub"
        request.cookies = {}
        request.headers = {}

        call_next = AsyncMock()

        response = await middleware.dispatch(request, call_next)

        # Should return 401 (protected route, no token)
        assert response.status_code == 401
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_shared_middleware_root_public_route_with_path_prefix(
        self, mock_app, mock_user_pool
    ):  # noqa: E501
        """Test SharedAuthMiddleware correctly handles root public route with path prefix."""
        from mdb_engine.auth.shared_middleware import SharedAuthMiddleware

        middleware = SharedAuthMiddleware(
            app=mock_app,
            user_pool=mock_user_pool,
            app_slug="test_app",
            require_role="viewer",
            public_routes=["/"],
        )

        # Test root path with prefix
        request = MagicMock()
        request.url.path = "/auth-hub"
        request.scope = {}
        request.state = MagicMock()
        request.state.app_base_path = "/auth-hub"
        request.cookies = {}
        request.headers = {}

        call_next = AsyncMock(return_value=MagicMock())

        response = await middleware.dispatch(request, call_next)

        # Should bypass auth (root is public)
        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_shared_middleware_multiple_public_routes_with_path_prefix(
        self, mock_app, mock_user_pool
    ):  # noqa: E501
        """Test SharedAuthMiddleware handles multiple public routes with path prefix."""
        from mdb_engine.auth.shared_middleware import SharedAuthMiddleware

        middleware = SharedAuthMiddleware(
            app=mock_app,
            user_pool=mock_user_pool,
            app_slug="test_app",
            require_role="viewer",
            public_routes=["/", "/health", "/login", "/register", "/api/public/*"],
        )

        public_paths = [
            "/auth-hub/",
            "/auth-hub/health",
            "/auth-hub/login",
            "/auth-hub/register",
            "/auth-hub/api/public/test",
        ]

        for path in public_paths:
            request = MagicMock()
            request.url.path = (
                path.rstrip("/") if path.endswith("/") and path != "/auth-hub/" else path
            )
            request.scope = {}
            request.state = MagicMock()
            request.state.app_base_path = "/auth-hub"
            request.cookies = {}
            request.headers = {}

            call_next = AsyncMock(return_value=MagicMock())

            response = await middleware.dispatch(request, call_next)

            # All should bypass auth
            call_next.assert_called_once()
            call_next.reset_mock()
