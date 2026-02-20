#!/usr/bin/env python3
"""
OAuth Demo - Login with Google & GitHub

Demonstrates how to use MDB-Engine's built-in OAuth 2.0 / OIDC support
to authenticate users via external identity providers.

All the heavy lifting (Authlib setup, route registration, user creation,
JWT token issuance) is handled automatically by the engine based on the
manifest.json configuration.  This file only contains the application
routes that consume the authenticated user.

Usage:
    1. Set environment variables (see .env.example)
    2. Run: uvicorn web:app --reload --port 8000
    3. Visit http://localhost:8000
    4. Click "Login with Google" or "Login with GitHub"

Auto-generated routes (by MDB-Engine):
    GET /auth/oauth/providers           - List available providers
    GET /auth/oauth/google/login        - Redirect to Google login
    GET /auth/oauth/google/callback     - Google callback handler
    GET /auth/oauth/github/login        - Redirect to GitHub login
    GET /auth/oauth/github/callback     - GitHub callback handler
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from mdb_engine import MongoDBEngine
from mdb_engine.auth.users import get_app_user

load_dotenv()

logger = logging.getLogger("oauth_demo")

APP_SLUG = "oauth-demo"

from mdb_engine.env import get_mongo_uri, get_db_name

engine = MongoDBEngine(
    mongo_uri=get_mongo_uri(fallback="mongodb://localhost:27017/"),
    db_name=get_db_name(fallback="oauth_demo_db"),
)

app = engine.create_app(
    slug=APP_SLUG,
    manifest=Path(__file__).parent / "manifest.json",
    title="OAuth Demo",
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

async def _current_user(request: Request):
    """Get current authenticated user (if any)."""
    if not engine.initialized:
        return None
    db = await engine.get_scoped_db(APP_SLUG)
    config = engine.get_app(APP_SLUG)
    return await get_app_user(
        request=request,
        slug_id=APP_SLUG,
        db=db,
        config=config,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

LOGIN_PAGE_HTML = """\
<!DOCTYPE html>
<html>
<head><title>OAuth Demo</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 480px; margin: 80px auto; text-align: center; }
  a.btn { display: inline-block; margin: 12px; padding: 12px 24px;
          border-radius: 6px; text-decoration: none; color: #fff; font-weight: 600; }
  .google { background: #4285F4; }
  .github { background: #333; }
</style>
</head>
<body>
  <h1>OAuth Demo</h1>
  <p>Sign in with an external identity provider.</p>
  <a class="btn google" href="/auth/oauth/google/login">Login with Google</a>
  <a class="btn github" href="/auth/oauth/github/login">Login with GitHub</a>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = await _current_user(request)
    if user:
        name = user.get("display_name") or user.get("email", "User")
        avatar = user.get("avatar_url", "")
        avatar_html = f'<img src="{avatar}" width="64" style="border-radius:50%"><br>' if avatar else ""
        return HTMLResponse(
            f"<html><body style='font-family:system-ui;max-width:480px;margin:80px auto;text-align:center'>"
            f"<h1>Welcome, {name}!</h1>"
            f"{avatar_html}"
            f"<p>Email: {user.get('email')}</p>"
            f"<p>Role: {user.get('role')}</p>"
            f"<p>Provider: {user.get('oauth_provider', 'N/A')}</p>"
            f'<a href="/logout">Logout</a>'
            f"</body></html>"
        )
    return HTMLResponse(LOGIN_PAGE_HTML)


@app.get("/logout")
async def logout(request: Request):
    from mdb_engine.auth.cookie_utils import clear_auth_cookies

    response = RedirectResponse(url="/")
    clear_auth_cookies(response, request)
    return response


@app.get("/me", response_class=JSONResponse)
async def me(request: Request):
    """Return current user info as JSON (useful for SPAs)."""
    user = await _current_user(request)
    if not user:
        return JSONResponse({"authenticated": False}, status_code=401)
    return JSONResponse({
        "authenticated": True,
        "email": user.get("email"),
        "display_name": user.get("display_name"),
        "role": user.get("role"),
        "oauth_provider": user.get("oauth_provider"),
    })
