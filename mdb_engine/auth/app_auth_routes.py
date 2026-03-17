"""
App-level user authentication routes and session middleware.

When ``auth.users.enabled`` is set in the manifest, ``create_app()``
automatically mounts JSON auth routes (``/auth/register``, ``/auth/login``,
``/auth/logout``, ``/auth/me``) and a session middleware that populates
``request.state.user`` / ``request.state.user_roles``.

This makes ``require_user()`` and ``require_role()`` work out of the box
for per-collection ``auth`` config in auto-CRUD manifests.
"""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError
from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from ..engine import MongoDBEngine

logger = logging.getLogger(__name__)


def _user_payload(user: dict[str, Any] | None) -> dict[str, Any] | None:
    """Produce a safe, serialisable user dict for JSON responses."""
    if not user:
        return None
    return {
        "id": str(user.get("_id") or user.get("app_user_id", "")),
        "email": user.get("email"),
        "name": user.get("name"),
        "role": str(user.get("role") or "guest"),
    }


# ── Router factory ──────────────────────────────────────────────────────


def create_app_auth_router(
    *,
    engine: MongoDBEngine,
    slug: str,
    manifest_data: dict[str, Any],
    users_config: dict[str, Any],
) -> APIRouter:
    """Return an ``APIRouter`` with ``/auth/*`` JSON endpoints.

    Routes:

    * ``GET  /auth/me``       — current session info
    * ``POST /auth/register`` — create account (if ``allow_registration``)
    * ``POST /auth/login``    — authenticate
    * ``POST /auth/logout``   — clear session cookie
    """
    from .users import _resolve_env_placeholders, authenticate_app_user, create_app_session, create_app_user

    router = APIRouter(tags=["auth"], include_in_schema=False)
    collection_name = users_config.get("collection_name", "users")
    cookie_name_prefix = users_config.get("session_cookie_name", "app_session")
    registration_role = str(users_config.get("registration_role", "guest"))

    # Parse allow_registration: bool for back-compat, or string mode
    _raw_reg = users_config.get("allow_registration", False)
    if isinstance(_raw_reg, str):
        registration_mode = _raw_reg  # "invite_only", "true", "false"
    else:
        registration_mode = "open" if _raw_reg else "disabled"

    # Pre-resolve invite codes (supports {{env.*}} placeholders)
    invite_codes: list[str] = []
    if registration_mode == "invite_only":
        raw_codes = users_config.get("invite_codes", [])
        invite_codes = [str(c) for c in _resolve_env_placeholders(raw_codes) if c]

    # Login rate limiting (in-memory, resets on restart)
    max_login_attempts = int(users_config.get("max_login_attempts", 5))
    login_lockout_seconds = int(users_config.get("login_lockout_seconds", 900))
    _login_attempts: dict[str, list[float]] = {}

    def _check_rate_limit(email: str) -> None:
        now = time.monotonic()
        attempts = _login_attempts.get(email, [])
        # Prune old attempts outside the window
        attempts = [t for t in attempts if now - t < login_lockout_seconds]
        _login_attempts[email] = attempts
        if len(attempts) >= max_login_attempts:
            retry_after = int(login_lockout_seconds - (now - attempts[0]))
            raise HTTPException(
                status_code=429,
                detail="Too many login attempts. Try again later.",
                headers={"Retry-After": str(max(1, retry_after))},
            )

    def _record_failed_attempt(email: str) -> None:
        _login_attempts.setdefault(email, []).append(time.monotonic())

    def _clear_attempts(email: str) -> None:
        _login_attempts.pop(email, None)

    # Registration rate limiting (per IP, in-memory)
    max_reg_attempts = int(users_config.get("max_registration_attempts", 5))
    reg_window_seconds = int(users_config.get("registration_window_seconds", 3600))
    _reg_attempts: dict[str, list[float]] = {}

    def _check_registration_rate(ip: str) -> None:
        now = time.monotonic()
        attempts = _reg_attempts.get(ip, [])
        attempts = [t for t in attempts if now - t < reg_window_seconds]
        _reg_attempts[ip] = attempts
        if len(attempts) >= max_reg_attempts:
            retry_after = int(reg_window_seconds - (now - attempts[0]))
            raise HTTPException(
                status_code=429,
                detail="Too many registration attempts. Try again later.",
                headers={"Retry-After": str(max(1, retry_after))},
            )

    def _record_registration(ip: str) -> None:
        _reg_attempts.setdefault(ip, []).append(time.monotonic())

    @router.get("/auth/me")
    async def auth_me(request: Request):
        user = getattr(request.state, "user", None)
        return {"authenticated": bool(user), "user": _user_payload(user)}

    @router.post("/auth/register")
    async def auth_register(request: Request):
        if registration_mode == "disabled":
            raise HTTPException(status_code=403, detail="Registration is disabled")

        client_ip = request.client.host if request.client else "unknown"
        _check_registration_rate(client_ip)

        body = await request.json()
        email = str(body.get("email", "")).strip().lower()
        password = str(body.get("password", "")).strip()
        name = str(body.get("name", "")).strip()

        if registration_mode == "invite_only":
            code = str(body.get("invite_code", "")).strip()
            if not code or code not in invite_codes:
                raise HTTPException(status_code=403, detail="Valid invite code required")

        if not email or "@" not in email:
            raise HTTPException(status_code=422, detail="Valid email is required")
        if len(password) < 6:
            raise HTTPException(status_code=422, detail="Password must be at least 6 characters")

        db = await engine.get_scoped_db(slug)
        users_col = getattr(db, collection_name)
        if await users_col.find_one({"email": email}):
            raise HTTPException(status_code=409, detail="User already exists")

        user = await create_app_user(
            db=db,
            email=email,
            password=password,
            role=registration_role,
            collection_name=collection_name,
        )
        if not user:
            raise HTTPException(status_code=400, detail="Could not create user")

        _record_registration(client_ip)

        if name:
            await users_col.update_one({"_id": user["_id"]}, {"$set": {"name": name}})
            user["name"] = name

        response = JSONResponse({"ok": True, "user": _user_payload(user)})
        await create_app_session(
            request=request,
            slug_id=slug,
            user_id=str(user["_id"]),
            config=manifest_data,
            response=response,
        )
        return response

    @router.post("/auth/login")
    async def auth_login(request: Request):
        body = await request.json()
        email = str(body.get("email", "")).strip().lower()
        password = str(body.get("password", "")).strip()

        if not email or not password:
            raise HTTPException(status_code=422, detail="Email and password are required")

        _check_rate_limit(email)

        db = await engine.get_scoped_db(slug)
        user = await authenticate_app_user(
            db=db,
            email=email,
            password=password,
            collection_name=collection_name,
        )
        if not user:
            _record_failed_attempt(email)
            raise HTTPException(status_code=401, detail="Invalid credentials")

        _clear_attempts(email)
        response = JSONResponse({"ok": True, "user": _user_payload(user)})
        await create_app_session(
            request=request,
            slug_id=slug,
            user_id=str(user["_id"]),
            config=manifest_data,
            response=response,
        )
        return response

    @router.post("/auth/logout")
    async def auth_logout(request: Request):
        cookie = f"{cookie_name_prefix}_{slug}"
        secure = request.url.scheme == "https" or os.getenv("G_NOME_ENV") == "production"
        response = JSONResponse({"ok": True})
        response.delete_cookie(key=cookie, httponly=True, secure=secure, samesite="lax")
        return response

    return router


# ── Session middleware factory ───────────────────────────────────────────


def create_app_user_session_middleware(
    *,
    engine: MongoDBEngine,
    slug: str,
    manifest_data: dict[str, Any],
) -> type[BaseHTTPMiddleware]:
    """Return a middleware class that populates ``request.state.user``.

    For every request the middleware:

    1. Reads the app-user session cookie.
    2. Validates it via ``get_app_user()``.
    3. Sets ``request.state.user`` and ``request.state.user_roles``.

    This enables ``require_user()`` / ``require_role()`` and the
    per-collection ``"auth"`` config in auto-CRUD to work in ``app``
    auth mode.
    """
    from .users import get_app_user

    class AppUserSessionMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            request.state.user = None
            request.state.user_roles = []

            try:
                db = await engine.get_scoped_db(slug)
                user = await get_app_user(
                    request,
                    slug,
                    db,
                    config=manifest_data,
                    allow_demo_fallback=False,
                )
                if user:
                    request.state.user = user
                    request.state.user_roles = [str(user.get("role") or "guest")]
            except (HTTPException, PyMongoError, KeyError, ValueError, AttributeError):
                pass

            return await call_next(request)

    return AppUserSessionMiddleware
