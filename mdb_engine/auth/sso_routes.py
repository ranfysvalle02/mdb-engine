"""
Auto-registered SSO routes for shared-auth apps.

When auth.mode == "shared", the engine auto-registers /auth/callback and
/logout on each child app so that app developers never need to write
(or duplicate) this security-critical boilerplate.

Manifest knobs (all optional, under "auth"):
    on_login_redirect   – path to redirect after login   (default: "/")
    on_logout_redirect  – path to redirect after logout  (default: "{auth_hub_url}/login")
    cookie_max_age      – cookie lifetime in seconds      (default: 86400)
"""

import logging
from typing import Any
from urllib.parse import unquote_plus

from fastapi import FastAPI, Request
from starlette.responses import RedirectResponse

from .cookie_utils import get_secure_cookie_settings
from .jwt import validate_jwt_token_format
from .shared_middleware import AUTH_COOKIE_NAME

logger = logging.getLogger(__name__)

DEFAULT_COOKIE_MAX_AGE = 86400  # 24 hours


def _resolve_auth_hub_url(request: Request) -> str:
    return (
        getattr(request.state, "auth_hub_url", None)
        or getattr(request.app.state, "app_auth_hub_url", None)
        or "/auth-hub"
    )


def register_sso_routes(
    child_app: FastAPI,
    auth_config: dict[str, Any],
) -> None:
    """Register /auth/callback and /logout on *child_app*.

    Called automatically by the multi-app lifespan for every app whose
    manifest declares ``auth.mode: "shared"``.
    """
    on_login_redirect = auth_config.get("on_login_redirect", "/")
    on_logout_redirect = auth_config.get("on_logout_redirect", None)
    cookie_max_age = auth_config.get("cookie_max_age", DEFAULT_COOKIE_MAX_AGE)

    # ------------------------------------------------------------------ #
    # GET /auth/callback
    # ------------------------------------------------------------------ #
    @child_app.get("/auth/callback", include_in_schema=False)
    async def _sso_auth_callback(request: Request, token: str | None = None):
        if not token:
            token = request.query_params.get("token")
        if token:
            token = unquote_plus(token)

        auth_hub_url = _resolve_auth_hub_url(request)

        if not token or not validate_jwt_token_format(token):
            return RedirectResponse(
                url=f"{auth_hub_url}/login?error=invalid_token",
                status_code=302,
            )

        pool = getattr(request.app.state, "user_pool", None)
        if not pool:
            return RedirectResponse(
                url=f"{auth_hub_url}/login?error=pool_not_initialized",
                status_code=302,
            )

        user = await pool.validate_token(token)
        if not user:
            return RedirectResponse(
                url=f"{auth_hub_url}/login?error=invalid_token",
                status_code=302,
            )

        response = RedirectResponse(url=on_login_redirect, status_code=302)
        cookie_settings = get_secure_cookie_settings(request)
        response.set_cookie(
            key=AUTH_COOKIE_NAME,
            value=token,
            httponly=cookie_settings["httponly"],
            samesite=cookie_settings["samesite"],
            secure=cookie_settings["secure"],
            max_age=cookie_max_age,
            path="/",
        )
        return response

    # ------------------------------------------------------------------ #
    # POST /logout   (also accept GET for convenience links)
    # ------------------------------------------------------------------ #
    @child_app.post("/logout", include_in_schema=False)
    @child_app.get("/logout", include_in_schema=False)
    async def _sso_logout(request: Request):
        auth_hub_url = _resolve_auth_hub_url(request)
        redirect_target = on_logout_redirect or f"{auth_hub_url}/login"

        pool = getattr(request.app.state, "user_pool", None)
        token = request.cookies.get(AUTH_COOKIE_NAME)

        if pool and token:
            try:
                await pool.revoke_token(token, reason="logout")
            except (AttributeError, TypeError, ValueError) as exc:
                logger.warning("Failed to revoke token during logout: %s", exc)

        response = RedirectResponse(url=redirect_target, status_code=302)
        cookie_settings = get_secure_cookie_settings(request)
        response.delete_cookie(
            AUTH_COOKIE_NAME,
            path="/",
            domain=None,
            secure=cookie_settings["secure"],
            samesite=cookie_settings["samesite"],
        )
        return response

    logger.info(
        "Auto-registered /auth/callback and /logout on app '%s'",
        getattr(child_app.state, "app_slug", "unknown"),
    )
