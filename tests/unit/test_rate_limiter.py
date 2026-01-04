"""
Unit tests for Auth Rate Limiting

Tests the rate limiting middleware and decorators for protecting auth endpoints.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from mdb_engine.auth.rate_limiter import (
    DEFAULT_AUTH_RATE_LIMITS,
    AuthRateLimitMiddleware,
    InMemoryRateLimitStore,
    RateLimit,
    create_rate_limit_middleware,
    rate_limit,
)


class TestRateLimit:
    """Tests for RateLimit dataclass."""

    def test_default_values(self):
        """Test RateLimit default values."""
        limit = RateLimit()
        assert limit.max_attempts == 5
        assert limit.window_seconds == 300

    def test_custom_values(self):
        """Test RateLimit with custom values."""
        limit = RateLimit(max_attempts=10, window_seconds=600)
        assert limit.max_attempts == 10
        assert limit.window_seconds == 600

    def test_to_dict(self):
        """Test RateLimit.to_dict method."""
        limit = RateLimit(max_attempts=3, window_seconds=60)
        d = limit.to_dict()
        assert d == {"max_attempts": 3, "window_seconds": 60}


class TestInMemoryRateLimitStore:
    """Tests for InMemoryRateLimitStore."""

    @pytest.fixture
    def store(self):
        """Create a fresh store for each test."""
        return InMemoryRateLimitStore()

    @pytest.mark.asyncio
    async def test_record_attempt_first(self, store):
        """Test recording first attempt."""
        count = await store.record_attempt("test:ip:email", 300)
        assert count == 1

    @pytest.mark.asyncio
    async def test_record_multiple_attempts(self, store):
        """Test recording multiple attempts."""
        await store.record_attempt("test:ip:email", 300)
        await store.record_attempt("test:ip:email", 300)
        count = await store.record_attempt("test:ip:email", 300)
        assert count == 3

    @pytest.mark.asyncio
    async def test_get_count(self, store):
        """Test getting count without recording."""
        await store.record_attempt("test:ip:email", 300)
        await store.record_attempt("test:ip:email", 300)

        count = await store.get_count("test:ip:email", 300)
        assert count == 2

    @pytest.mark.asyncio
    async def test_get_count_empty(self, store):
        """Test getting count for non-existent identifier."""
        count = await store.get_count("nonexistent", 300)
        assert count == 0

    @pytest.mark.asyncio
    async def test_reset(self, store):
        """Test resetting rate limit."""
        await store.record_attempt("test:ip:email", 300)
        await store.record_attempt("test:ip:email", 300)

        await store.reset("test:ip:email")

        count = await store.get_count("test:ip:email", 300)
        assert count == 0

    @pytest.mark.asyncio
    async def test_window_expiry(self, store):
        """Test that old attempts are cleaned up."""
        # Record an attempt
        await store.record_attempt("test:ip:email", 1)  # 1 second window

        # Wait for window to expire
        await asyncio.sleep(1.1)

        # Count should be 0 after window expires
        count = await store.get_count("test:ip:email", 1)
        assert count == 0

    def test_cleanup(self, store):
        """Test manual cleanup of old entries."""
        # Add some entries manually
        import time

        old_time = time.time() - 10000  # Very old
        store._storage["old:key"] = [(old_time, 1)]
        store._storage["new:key"] = [(time.time(), 1)]

        cleaned = store.cleanup(max_age_seconds=5000)

        assert cleaned == 1
        assert "old:key" not in store._storage
        assert "new:key" in store._storage


class TestCreateRateLimitMiddleware:
    """Tests for create_rate_limit_middleware factory."""

    def test_creates_middleware_with_defaults(self):
        """Test creating middleware without config uses defaults."""
        middleware_class = create_rate_limit_middleware({})
        assert middleware_class is not None

    def test_creates_middleware_with_custom_limits(self):
        """Test creating middleware with custom limits."""
        manifest_auth = {
            "rate_limits": {
                "/login": {"max_attempts": 3, "window_seconds": 60},
                "/register": {"max_attempts": 1, "window_seconds": 3600},
            }
        }

        middleware_class = create_rate_limit_middleware(manifest_auth)

        # Create instance and check configuration
        mock_app = MagicMock()
        instance = middleware_class(mock_app)

        assert "/login" in instance._limits
        assert instance._limits["/login"].max_attempts == 3
        assert instance._limits["/login"].window_seconds == 60
        assert "/register" in instance._limits
        assert instance._limits["/register"].max_attempts == 1


class TestAuthRateLimitMiddleware:
    """Tests for AuthRateLimitMiddleware."""

    @pytest.fixture
    def mock_request(self):
        """Create a mock request."""
        request = MagicMock()
        request.url.path = "/login"
        request.method = "POST"
        request.client.host = "192.168.1.1"
        request.headers.get.return_value = None
        request.cookies = {}
        return request

    @pytest.fixture
    def middleware(self):
        """Create middleware instance."""
        mock_app = AsyncMock()
        return AuthRateLimitMiddleware(
            mock_app,
            limits={"/login": RateLimit(max_attempts=3, window_seconds=60)},
        )

    def test_init_with_defaults(self):
        """Test middleware initialization with defaults."""
        mock_app = MagicMock()
        middleware = AuthRateLimitMiddleware(mock_app)

        assert "/login" in middleware._limits
        assert "/register" in middleware._limits

    def test_init_with_custom_limits(self):
        """Test middleware initialization with custom limits."""
        mock_app = MagicMock()
        limits = {"/custom": RateLimit(max_attempts=10, window_seconds=120)}
        middleware = AuthRateLimitMiddleware(mock_app, limits=limits)

        assert "/custom" in middleware._limits
        assert middleware._limits["/custom"].max_attempts == 10

    def test_get_client_ip_direct(self):
        """Test getting client IP from direct connection."""
        mock_app = MagicMock()
        middleware = AuthRateLimitMiddleware(mock_app)

        request = MagicMock()
        request.headers.get.return_value = None
        request.client.host = "192.168.1.1"

        ip = middleware._get_client_ip(request)
        assert ip == "192.168.1.1"

    def test_get_client_ip_forwarded_for(self):
        """Test getting client IP from X-Forwarded-For header."""
        mock_app = MagicMock()
        middleware = AuthRateLimitMiddleware(mock_app)

        request = MagicMock()
        request.headers.get.side_effect = lambda h: (
            "10.0.0.1, 10.0.0.2" if h == "X-Forwarded-For" else None
        )
        request.client.host = "192.168.1.1"

        ip = middleware._get_client_ip(request)
        assert ip == "10.0.0.1"

    def test_get_client_ip_real_ip(self):
        """Test getting client IP from X-Real-IP header."""
        mock_app = MagicMock()
        middleware = AuthRateLimitMiddleware(mock_app)

        request = MagicMock()

        def get_header(h):
            if h == "X-Real-IP":
                return "10.0.0.5"
            return None

        request.headers.get.side_effect = get_header
        request.client.host = "192.168.1.1"

        ip = middleware._get_client_ip(request)
        assert ip == "10.0.0.5"


class TestRateLimitDecorator:
    """Tests for rate_limit decorator."""

    @pytest.mark.asyncio
    async def test_allows_under_limit(self):
        """Test decorator allows requests under limit."""

        @rate_limit(max_attempts=5, window_seconds=60)
        async def test_endpoint(request):
            return {"status": "ok"}

        request = MagicMock()
        request.client.host = "test-decorator-ip"

        result = await test_endpoint(request)
        assert result == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_blocks_over_limit(self):
        """Test decorator blocks requests over limit."""

        @rate_limit(max_attempts=2, window_seconds=60)
        async def test_endpoint_block(request):
            return {"status": "ok"}

        request = MagicMock()
        request.client.host = "test-decorator-block-ip"

        # First two should succeed
        await test_endpoint_block(request)
        await test_endpoint_block(request)

        # Third should be rate limited
        response = await test_endpoint_block(request)
        assert response.status_code == 429


class TestDefaultRateLimits:
    """Tests for default rate limit configuration."""

    def test_login_defaults(self):
        """Test default login rate limits."""
        assert "/login" in DEFAULT_AUTH_RATE_LIMITS
        limit = DEFAULT_AUTH_RATE_LIMITS["/login"]
        assert limit.max_attempts == 5
        assert limit.window_seconds == 300

    def test_register_defaults(self):
        """Test default register rate limits."""
        assert "/register" in DEFAULT_AUTH_RATE_LIMITS
        limit = DEFAULT_AUTH_RATE_LIMITS["/register"]
        assert limit.max_attempts == 3
        assert limit.window_seconds == 3600

    def test_logout_defaults(self):
        """Test default logout rate limits."""
        assert "/logout" in DEFAULT_AUTH_RATE_LIMITS
        limit = DEFAULT_AUTH_RATE_LIMITS["/logout"]
        assert limit.max_attempts == 10
        assert limit.window_seconds == 60
