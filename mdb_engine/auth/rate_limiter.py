"""
Rate Limiting for Authentication Endpoints

Provides rate limiting middleware to protect auth endpoints from brute-force attacks.
Supports both in-memory storage (single instance) and MongoDB-backed storage (distributed).

This module is part of MDB_ENGINE - MongoDB Engine.

Features:
    - Sliding window rate limiting algorithm
    - Per-endpoint configurable limits via manifest
    - IP + optional email-based tracking
    - In-memory (default) or MongoDB storage
    - 429 Too Many Requests with Retry-After header

Usage:
    # Via middleware (recommended for shared auth)
    app.add_middleware(
        AuthRateLimitMiddleware,
        limits={
            "/login": RateLimit(max_attempts=5, window_seconds=300),
            "/register": RateLimit(max_attempts=3, window_seconds=3600),
        }
    )

    # Via decorator (for specific endpoints)
    @app.post("/login")
    @rate_limit(max_attempts=5, window_seconds=300)
    async def login(request: Request):
        ...
"""

import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Tuple

from pymongo.errors import OperationFailure
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)


@dataclass
class RateLimit:
    """Rate limit configuration for an endpoint."""

    max_attempts: int = 5
    window_seconds: int = 300  # 5 minutes

    def to_dict(self) -> Dict[str, int]:
        return {
            "max_attempts": self.max_attempts,
            "window_seconds": self.window_seconds,
        }


# Default rate limits for auth endpoints
DEFAULT_AUTH_RATE_LIMITS: Dict[str, RateLimit] = {
    "/login": RateLimit(max_attempts=5, window_seconds=300),
    "/register": RateLimit(max_attempts=3, window_seconds=3600),
    "/logout": RateLimit(max_attempts=10, window_seconds=60),
}


class InMemoryRateLimitStore:
    """
    In-memory rate limit storage using sliding window algorithm.

    Suitable for single-instance deployments. For distributed systems,
    use MongoDBRateLimitStore instead.
    """

    def __init__(self):
        # Structure: {identifier: [(timestamp, count), ...]}
        self._storage: Dict[str, List[Tuple[float, int]]] = defaultdict(list)

    async def record_attempt(
        self,
        identifier: str,
        window_seconds: int,
    ) -> int:
        """
        Record an attempt and return current count in window.

        Args:
            identifier: Unique identifier (e.g., "login:192.168.1.1:user@example.com")
            window_seconds: Time window in seconds

        Returns:
            Number of attempts in the current window (including this one)
        """
        now = time.time()
        cutoff = now - window_seconds

        # Clean old entries and count current
        entries = self._storage[identifier]
        entries[:] = [(ts, c) for ts, c in entries if ts > cutoff]

        # Add new attempt
        entries.append((now, 1))

        # Return total count
        return sum(c for _, c in entries)

    async def get_count(
        self,
        identifier: str,
        window_seconds: int,
    ) -> int:
        """Get current attempt count without recording."""
        now = time.time()
        cutoff = now - window_seconds

        entries = self._storage.get(identifier, [])
        return sum(c for ts, c in entries if ts > cutoff)

    async def reset(self, identifier: str) -> None:
        """Reset rate limit for an identifier (e.g., after successful login)."""
        self._storage.pop(identifier, None)

    def cleanup(self, max_age_seconds: int = 7200) -> int:
        """
        Clean up old entries to prevent memory growth.

        Args:
            max_age_seconds: Remove entries older than this (default: 2 hours)

        Returns:
            Number of identifiers cleaned up
        """
        now = time.time()
        cutoff = now - max_age_seconds
        cleaned = 0

        identifiers_to_remove = []
        for identifier, entries in self._storage.items():
            entries[:] = [(ts, c) for ts, c in entries if ts > cutoff]
            if not entries:
                identifiers_to_remove.append(identifier)

        for identifier in identifiers_to_remove:
            del self._storage[identifier]
            cleaned += 1

        return cleaned


class MongoDBRateLimitStore:
    """
    MongoDB-backed rate limit storage for distributed deployments.

    Uses TTL indexes for automatic cleanup.
    """

    COLLECTION = "_mdb_engine_rate_limits"

    def __init__(self, db):
        """
        Initialize MongoDB rate limit store.

        Args:
            db: MongoDB database instance (Motor AsyncIOMotorDatabase)
        """
        self._db = db
        self._collection = db[self.COLLECTION]
        self._indexes_created = False

    async def ensure_indexes(self) -> None:
        """Create necessary indexes."""
        if self._indexes_created:
            return

        try:
            # Compound index for lookups
            await self._collection.create_index(
                [("identifier", 1), ("timestamp", -1)], name="identifier_timestamp_idx"
            )
            # TTL index for cleanup
            await self._collection.create_index(
                "expires_at", expireAfterSeconds=0, name="expires_at_ttl_idx"
            )
            self._indexes_created = True
            logger.info("Rate limit indexes ensured")
        except OperationFailure as e:
            logger.warning(f"Failed to create rate limit indexes: {e}")

    async def record_attempt(
        self,
        identifier: str,
        window_seconds: int,
    ) -> int:
        """Record an attempt and return current count in window."""
        await self.ensure_indexes()

        now = datetime.utcnow()
        expires_at = now + timedelta(seconds=window_seconds)
        cutoff = now - timedelta(seconds=window_seconds)

        # Insert attempt
        await self._collection.insert_one(
            {
                "identifier": identifier,
                "timestamp": now,
                "expires_at": expires_at,
            }
        )

        # Count attempts in window
        count = await self._collection.count_documents(
            {
                "identifier": identifier,
                "timestamp": {"$gte": cutoff},
            }
        )

        return count

    async def get_count(
        self,
        identifier: str,
        window_seconds: int,
    ) -> int:
        """Get current attempt count without recording."""
        await self.ensure_indexes()

        cutoff = datetime.utcnow() - timedelta(seconds=window_seconds)

        count = await self._collection.count_documents(
            {
                "identifier": identifier,
                "timestamp": {"$gte": cutoff},
            }
        )

        return count

    async def reset(self, identifier: str) -> None:
        """Reset rate limit for an identifier."""
        await self._collection.delete_many({"identifier": identifier})


# Global in-memory store (shared across middleware instances in same process)
_default_store = InMemoryRateLimitStore()


class AuthRateLimitMiddleware(BaseHTTPMiddleware):
    """
    ASGI middleware for rate limiting authentication endpoints.

    Automatically protects /login, /register, and other auth endpoints
    from brute-force attacks.

    Features:
    - Configurable per-endpoint limits
    - IP + email tracking for login attempts
    - 429 responses with Retry-After header
    - Skips rate limiting for non-auth endpoints

    Usage:
        # Basic usage with defaults
        app.add_middleware(AuthRateLimitMiddleware)

        # Custom limits
        app.add_middleware(
            AuthRateLimitMiddleware,
            limits={"/login": RateLimit(max_attempts=3, window_seconds=60)}
        )

        # With MongoDB storage for distributed deployments
        app.add_middleware(
            AuthRateLimitMiddleware,
            store=MongoDBRateLimitStore(db)
        )
    """

    def __init__(
        self,
        app: Callable,
        limits: Optional[Dict[str, RateLimit]] = None,
        store: Optional[InMemoryRateLimitStore] = None,
        include_email_in_key: bool = True,
    ):
        """
        Initialize rate limit middleware.

        Args:
            app: ASGI application
            limits: Dict of path -> RateLimit config. Defaults to DEFAULT_AUTH_RATE_LIMITS.
            store: Rate limit storage backend. Defaults to in-memory store.
            include_email_in_key: Include email in rate limit key for more granular limits.
        """
        super().__init__(app)
        self._limits = limits or DEFAULT_AUTH_RATE_LIMITS
        self._store = store or _default_store
        self._include_email_in_key = include_email_in_key

        logger.info(
            f"AuthRateLimitMiddleware initialized with limits for: {list(self._limits.keys())}"
        )

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Response],
    ) -> Response:
        """Process request through rate limiter."""
        path = request.url.path
        method = request.method

        # Only rate limit POST requests to configured endpoints
        if method != "POST" or path not in self._limits:
            return await call_next(request)

        limit = self._limits[path]
        identifier = await self._build_identifier(request, path)

        # Check current count (before recording this attempt)
        current_count = await self._store.get_count(identifier, limit.window_seconds)

        if current_count >= limit.max_attempts:
            logger.warning(
                f"Rate limit exceeded: {identifier} "
                f"({current_count}/{limit.max_attempts} in {limit.window_seconds}s)"
            )
            return self._rate_limit_response(limit.window_seconds)

        # Record this attempt
        await self._store.record_attempt(identifier, limit.window_seconds)

        # Process request
        response = await call_next(request)

        # Reset on successful login (2xx response)
        if response.status_code < 300 and path == "/login":
            await self._store.reset(identifier)

        return response

    async def _build_identifier(self, request: Request, path: str) -> str:
        """Build rate limit identifier from request."""
        parts = [path]

        # Add client IP
        client_ip = self._get_client_ip(request)
        parts.append(client_ip)

        # Optionally add email for more granular rate limiting
        if self._include_email_in_key:
            email = await self._extract_email(request)
            if email:
                parts.append(email)

        return ":".join(parts)

    def _get_client_ip(self, request: Request) -> str:
        """Get client IP, respecting proxy headers."""
        # Check X-Forwarded-For header (set by proxies/load balancers)
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # Take the first IP (original client)
            return forwarded_for.split(",")[0].strip()

        # Check X-Real-IP header
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()

        # Fall back to direct client IP
        if request.client:
            return request.client.host

        return "unknown"

    async def _extract_email(self, request: Request) -> Optional[str]:
        """Try to extract email from request body."""
        try:
            # Only try to read body for JSON requests
            content_type = request.headers.get("content-type", "")
            if "application/json" in content_type:
                # Note: This consumes the body, so we need to be careful
                # In practice, this is called before the body is read by the route
                body = await request.body()
                if body:
                    import json

                    data = json.loads(body)
                    return data.get("email")
        except (ValueError, UnicodeDecodeError, KeyError):
            pass
        return None

    @staticmethod
    def _rate_limit_response(retry_after: int) -> JSONResponse:
        """Return 429 Too Many Requests response."""
        return JSONResponse(
            status_code=429,
            content={
                "detail": f"Too many attempts. Please try again in {retry_after} seconds.",
                "error": "rate_limit_exceeded",
                "retry_after": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )


def create_rate_limit_middleware(
    manifest_auth: Dict[str, Any],
    store: Optional[InMemoryRateLimitStore] = None,
) -> type:
    """
    Factory function to create rate limit middleware from manifest config.

    Args:
        manifest_auth: Auth section from manifest
        store: Optional storage backend

    Returns:
        Configured middleware class

    Manifest format:
        {
            "auth": {
                "rate_limits": {
                    "/login": {"max_attempts": 5, "window_seconds": 300},
                    "/register": {"max_attempts": 3, "window_seconds": 3600}
                }
            }
        }
    """
    rate_limits_config = manifest_auth.get("rate_limits", {})

    limits: Dict[str, RateLimit] = {}
    for path, config in rate_limits_config.items():
        limits[path] = RateLimit(
            max_attempts=config.get("max_attempts", 5),
            window_seconds=config.get("window_seconds", 300),
        )

    # Use defaults if no config provided
    if not limits:
        limits = DEFAULT_AUTH_RATE_LIMITS.copy()

    class ConfiguredRateLimitMiddleware(AuthRateLimitMiddleware):
        def __init__(self, app: Callable):
            super().__init__(app, limits=limits, store=store)

    return ConfiguredRateLimitMiddleware


def rate_limit(
    max_attempts: int = 5,
    window_seconds: int = 300,
    key_func: Optional[Callable[[Request], str]] = None,
):
    """
    Decorator for rate limiting individual endpoints.

    Usage:
        @app.post("/login")
        @rate_limit(max_attempts=5, window_seconds=300)
        async def login(request: Request):
            ...

    Args:
        max_attempts: Maximum attempts in window
        window_seconds: Time window in seconds
        key_func: Optional function to generate rate limit key from request
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(request: Request, *args, **kwargs):
            # Build identifier
            if key_func:
                identifier = key_func(request)
            else:
                client_ip = request.client.host if request.client else "unknown"
                identifier = f"{func.__name__}:{client_ip}"

            # Check rate limit
            count = await _default_store.record_attempt(identifier, window_seconds)

            if count > max_attempts:
                logger.warning(f"Rate limit exceeded: {identifier} ({count}/{max_attempts})")
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": f"Too many attempts. Try again in {window_seconds} seconds.",
                        "error": "rate_limit_exceeded",
                    },
                    headers={"Retry-After": str(window_seconds)},
                )

            return await func(request, *args, **kwargs)

        return wrapper

    return decorator
