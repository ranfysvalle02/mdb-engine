"""
Click Tracker - MDB-Engine SSO Demo
===================================

Demonstrates Single Sign-On (SSO) authentication:
- SharedUserPool for centralized user management
- JWT tokens shared across all apps
- Login once = authenticated everywhere
- Per-app roles (click_tracker has different permissions than dashboard)

Demo Users (defined in manifest.json, auto-seeded by engine):
- alice@example.com - admin on both apps (password: password123)
- bob@example.com - tracker on both apps (password: password123)
- charlie@example.com - clicker on both apps (password: password123)

SSO Magic: Login here, then visit Dashboard - you're already logged in!
"""

import os
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, Request, Depends, Form
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from mdb_engine import MongoDBEngine
from mdb_engine.dependencies import get_scoped_db

# =============================================================================
# App Setup
# =============================================================================

engine = MongoDBEngine(
    mongo_uri=os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
    db_name=os.getenv("MONGODB_DB", "mdb_runtime"),
)

APP_SLUG = "click_tracker"

# create_app() handles everything:
# - Manifest loading & validation
# - SharedAuthMiddleware (auto-added for auth.mode="shared")
# - Demo user seeding to SharedUserPool (from manifest.json demo_users)
# - CORS, etc.
#
# No need for @app.on_event("startup") - engine handles lifecycle!
app = engine.create_app(
    slug=APP_SLUG,
    manifest=Path(__file__).parent / "manifest.json",
    title="Click Tracker (SSO)",
)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Shared cookie name for SSO
AUTH_COOKIE = "mdb_auth_token"


# =============================================================================
# Auth Helpers
# =============================================================================

def get_user_pool():
    """Get the shared user pool from app state (initialized by engine)."""
    return getattr(app.state, "user_pool", None)


def get_current_user(request: Request) -> dict:
    """Get user from request.state (populated by SharedAuthMiddleware)."""
    return getattr(request.state, "user", None)


def get_roles(user: dict) -> list:
    """Get user's roles for this app."""
    if not user:
        return []
    app_roles = user.get("app_roles", {})
    return app_roles.get(APP_SLUG, [])


def get_primary_role(user: dict) -> str:
    """Get user's primary role for display."""
    roles = get_roles(user)
    # Priority: admin > tracker > clicker
    if "admin" in roles:
        return "admin"
    if "tracker" in roles:
        return "tracker"
    return "clicker"


def can_view_all(user: dict) -> bool:
    """Trackers and admins can view all clicks."""
    roles = get_roles(user)
    return "tracker" in roles or "admin" in roles


# =============================================================================
# Auth Routes
# =============================================================================

@app.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    """Authenticate via SharedUserPool and set JWT cookie."""
    pool = get_user_pool()
    if not pool:
        raise HTTPException(500, "User pool not initialized")

    # Authenticate and get JWT token
    token = await pool.authenticate(email, password)

    if not token:
        print(f"Authentication failed: user '{email}' not found or inactive")
        return JSONResponse(status_code=401, content={
            "success": False,
            "detail": "Invalid credentials"
        })

    # Get user for response
    user = await pool.validate_token(token)
    role = get_primary_role(user) if user else "unknown"

    response = JSONResponse(content={
        "success": True,
        "user": {"email": email, "role": role},
        "sso": True,  # Indicate SSO login
    })

    # Set shared JWT cookie (works across all apps on same domain)
    response.set_cookie(
        key=AUTH_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=86400,  # 24 hours
        path="/",
    )

    return response


@app.post("/logout")
async def logout(request: Request):
    """Revoke token and clear cookie."""
    pool = get_user_pool()

    # Get token from cookie
    token = request.cookies.get(AUTH_COOKIE)

    # Revoke token if we have pool and token
    if pool and token:
        try:
            await pool.revoke_token(token)
        except Exception:
            pass  # Token may already be invalid

    response = JSONResponse(content={"success": True})
    response.delete_cookie(AUTH_COOKIE, path="/")
    return response


# =============================================================================
# API Routes
# =============================================================================

@app.get("/api/me")
async def get_me(request: Request):
    """Get current user info and permissions."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")

    return {
        "email": user["email"],
        "roles": get_roles(user),
        "role": get_primary_role(user),
        "can_view_all": can_view_all(user),
        "sso": True,
    }


@app.post("/track")
async def track_click(request: Request, db=Depends(get_scoped_db)):
    """Track a click event."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")

    body = await request.json() if request.headers.get("content-type") == "application/json" else {}

    result = await db.clicks.insert_one({
        "user_id": user["email"],
        "user_role": get_primary_role(user),
        "url": body.get("url", "/"),
        "element": body.get("element", "button"),
        "timestamp": datetime.utcnow(),
    })

    return {"success": True, "click_id": str(result.inserted_id)}


@app.get("/clicks")
async def get_clicks(request: Request, limit: int = 50, db=Depends(get_scoped_db)):
    """Get clicks - filtered by role permissions."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")

    # Clickers only see their own clicks
    query = {} if can_view_all(user) else {"user_id": user["email"]}

    clicks = await db.clicks.find(query).sort("timestamp", -1).limit(limit).to_list(limit)

    return {
        "clicks": [
            {
                "id": str(c["_id"]),
                "user_id": c["user_id"],
                "user_role": c.get("user_role", "unknown"),
                "url": c.get("url", "/"),
                "timestamp": c["timestamp"].isoformat(),
            }
            for c in clicks
        ],
        "count": len(clicks),
        "viewing_all": can_view_all(user),
    }


# =============================================================================
# Pages
# =============================================================================

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Main page with demo user login."""
    user = get_current_user(request)
    role = get_primary_role(user) if user else None

    return templates.TemplateResponse("index.html", {
        "request": request,
        "user": user,
        "role": role,
        "can_view_all": can_view_all(user) if user else False,
    })


@app.get("/health")
async def health():
    return {"status": "healthy", "app": APP_SLUG, "auth": "sso"}


@app.get("/api")
async def api_info():
    return {
        "app": APP_SLUG,
        "auth_mode": "shared (SSO)",
        "demo_users": [
            "alice@example.com (admin)",
            "bob@example.com (tracker)",
            "charlie@example.com (clicker)"
        ],
        "password": "password123",
        "sso_note": "Login here = logged into Dashboard too!",
    }
