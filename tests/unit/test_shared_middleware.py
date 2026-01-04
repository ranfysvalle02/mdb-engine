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
        assert middleware._user_pool == mock_user_pool
        assert middleware._app_slug == "test_app"
        assert middleware._require_role is None
        assert "/health" in middleware._public_routes

    def test_is_public_route_exact_match(self, middleware):
        """Test public route detection with exact match."""
        assert middleware._is_public_route("/health") is True
        assert middleware._is_public_route("/protected") is False

    def test_is_public_route_wildcard(self, middleware):
        """Test public route detection with wildcard."""
        assert middleware._is_public_route("/api/public/endpoint") is True
        assert middleware._is_public_route("/api/public/nested/path") is True
        assert middleware._is_public_route("/api/private") is False

    def test_extract_token_from_cookie(self, middleware):
        """Test token extraction from cookie."""
        request = MagicMock()
        request.cookies = {"mdb_auth_token": "test-token"}
        request.headers = {}

        token = middleware._extract_token(request)
        assert token == "test-token"

    def test_extract_token_from_header(self, middleware):
        """Test token extraction from Authorization header."""
        request = MagicMock()
        request.cookies = {}
        request.headers = {"Authorization": "Bearer test-token-header"}

        token = middleware._extract_token(request)
        assert token == "test-token-header"

    def test_extract_token_none(self, middleware):
        """Test token extraction when no token present."""
        request = MagicMock()
        request.cookies = {}
        request.headers = {}

        token = middleware._extract_token(request)
        assert token is None

    def test_extract_token_invalid_header(self, middleware):
        """Test token extraction with invalid header format."""
        request = MagicMock()
        request.cookies = {}
        request.headers = {"Authorization": "Basic credentials"}  # Not Bearer

        token = middleware._extract_token(request)
        assert token is None

    @pytest.mark.asyncio
    async def test_dispatch_public_route(self, middleware, mock_app):
        """Test public routes bypass auth but still populate user if token present."""
        request = MagicMock()
        request.url.path = "/health"
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
        assert instance._role_hierarchy is not None
        assert "admin" in instance._role_hierarchy
        assert "viewer" in instance._role_hierarchy["admin"]
        assert "editor" in instance._role_hierarchy["admin"]


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

        assert instance._app_slug == "test_app"
        assert instance._require_role == "viewer"
        assert "/health" in instance._public_routes

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
        assert instance._role_hierarchy is not None
        assert "admin" in instance._role_hierarchy
        assert "viewer" in instance._role_hierarchy["admin"]
        assert "editor" in instance._role_hierarchy["admin"]

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
