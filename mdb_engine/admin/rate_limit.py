"""
Admin plane rate limiting.

Thin middleware that computes the bucket key + limits and delegates
counting to a pluggable :class:`RateLimitStore`. The store is what
determines whether the limiter is correct under horizontal scale:

- :class:`InMemoryRateLimitStore` — single-process only (default).
- :class:`MongoRateLimitStore` — correct across workers / pods.

Select via manifest ``admin_api.rate_limits.backend = "memory"|"mongo"``.
Defaults are 120/min for ``GET`` / ``HEAD`` and 15/min for mutating
methods, overridable per-bucket in the manifest.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .rate_limit_stores import InMemoryRateLimitStore, RateLimitStore

logger = logging.getLogger(__name__)


DEFAULT_RATE_LIMITS: dict[str, dict[str, int]] = {
    "read": {"max": 120, "window_seconds": 60},
    "write": {"max": 15, "window_seconds": 60},
}


def _bucket_for_method(method: str) -> str:
    return "read" if method.upper() in {"GET", "HEAD", "OPTIONS"} else "write"


class AdminRateLimitMiddleware(BaseHTTPMiddleware):
    """Per-token / per-IP limiter scoped to one admin prefix.

    Only requests under ``admin_prefix`` are counted; everything else
    passes through untouched so this middleware is safe to stack with
    app-level rate limiters on the same :class:`FastAPI` instance.

    The actual counting lives in the injected :class:`RateLimitStore`
    — swap implementations to change the consistency model without
    touching this class.
    """

    def __init__(
        self,
        app: Any,
        *,
        admin_prefix: str,
        limits: dict[str, dict[str, int]] | None = None,
        store: RateLimitStore | None = None,
    ):
        super().__init__(app)
        self._prefix = admin_prefix.rstrip("/") + "/"
        resolved = dict(DEFAULT_RATE_LIMITS)
        if limits:
            for k, v in limits.items():
                resolved[k] = {**resolved.get(k, {}), **v}
        self._limits = resolved
        self._store: RateLimitStore = store or InMemoryRateLimitStore()

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if not path.startswith(self._prefix):
            return await call_next(request)
        # Never rate-limit the liveness probe — k8s / LBs call it
        # unauthenticated and aggressively, and a 429 here would
        # cascade into a restart loop.
        if path.rstrip("/").endswith("/health/live"):
            return await call_next(request)

        bucket = _bucket_for_method(request.method)
        cfg = self._limits.get(bucket, DEFAULT_RATE_LIMITS[bucket])
        max_attempts = int(cfg.get("max", DEFAULT_RATE_LIMITS[bucket]["max"]))
        window = int(cfg.get("window_seconds", DEFAULT_RATE_LIMITS[bucket]["window_seconds"]))

        principal = self._extract_principal(request)
        module = self._module_from_path(path)
        key = f"{principal}|{module}|{bucket}"

        allowed, retry_after = await self._store.hit(
            key,
            max_attempts=max_attempts,
            window_seconds=window,
        )
        if not allowed:
            slug = request.query_params.get("slug") or "?"
            self._emit_rate_limited(slug=slug, module=module, endpoint=path)
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "admin plane rate limit exceeded",
                    "bucket": bucket,
                    "retry_after_seconds": retry_after,
                },
                headers={
                    "Retry-After": str(retry_after),
                    "Cache-Control": "no-store",
                },
            )
        return await call_next(request)

    def _module_from_path(self, path: str) -> str:
        rest = path[len(self._prefix) :]
        return rest.split("/", 1)[0] or "_root"

    @staticmethod
    def _extract_principal(request: Request) -> str:
        """Opaque, **process-stable** identifier for rate-limit bucketing.

        Uses SHA-256 (not Python's ``hash()``) so the identifier is
        stable across worker restarts, forks, and ``PYTHONHASHSEED``.
        Process-local hashing turned a restart into "free bucket" for
        every caller — and made brute-force retry windows mushy.
        """
        token = request.headers.get("X-App-Token")
        if token:
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            return f"tok:{digest[:16]}"
        client = request.client.host if request.client else "unknown"
        return f"ip:{client}"

    @staticmethod
    def _emit_rate_limited(*, slug: str, module: str, endpoint: str) -> None:
        try:  # nosemgrep
            from ..core.reconciler_events import emit_event
            from .events import EVENT_RATE_LIMITED

            emit_event(EVENT_RATE_LIMITED, slug=slug, module=module, endpoint=endpoint)
        except Exception:  # noqa: BLE001
            pass


__all__ = ["AdminRateLimitMiddleware", "DEFAULT_RATE_LIMITS"]
