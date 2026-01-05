"""
Analytics Dashboard - MDB-Engine SSO Demo
=========================================

Demonstrates Single Sign-On (SSO) and Cross-App Data Access:
- SharedUserPool for centralized user management
- JWT tokens shared across all apps
- Cross-app data access (reads click_tracker's clicks)
- Admin can manage roles for ALL apps

Access Levels:
- clicker: No dashboard access
- tracker: View analytics
- admin: Analytics + user management

Demo Users (defined in manifest.json, auto-seeded by engine):
- alice@example.com - admin on both apps (password: password123)
- bob@example.com - tracker on both apps (password: password123)
- charlie@example.com - clicker on both apps (password: password123)

SSO Magic: If you logged into Click Tracker, you're already logged in here!
"""

import os
from datetime import datetime, timedelta
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

APP_SLUG = "dashboard"

# create_app() handles everything:
# - Manifest loading & validation
# - SharedAuthMiddleware (auto-added for auth.mode="shared")
# - Demo user seeding to SharedUserPool (from manifest.json demo_users)
# - Cross-app read_scopes from manifest
#
# No need for @app.on_event("startup") - engine handles lifecycle!
app = engine.create_app(
    slug=APP_SLUG,
    manifest=Path(__file__).parent / "manifest.json",
    title="Analytics Dashboard (SSO)",
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


def get_roles(user: dict, app_slug: str = APP_SLUG) -> list:
    """Get user's roles for specified app."""
    if not user:
        return []
    app_roles = user.get("app_roles", {})
    return app_roles.get(app_slug, [])


def get_primary_role(user: dict) -> str:
    """Get user's primary role for display."""
    roles = get_roles(user)
    if "admin" in roles:
        return "admin"
    if "tracker" in roles:
        return "tracker"
    return "clicker"


def can_view_analytics(user: dict) -> bool:
    """Only trackers and admins can view analytics."""
    roles = get_roles(user)
    return "tracker" in roles or "admin" in roles


def can_manage_users(user: dict) -> bool:
    """Only admins can manage users."""
    return "admin" in get_roles(user)


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
        "can_view_analytics": can_view_analytics(user) if user else False,
        "sso": True,
    })

    # Set shared JWT cookie
    response.set_cookie(
        key=AUTH_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=86400,
        path="/",
    )

    return response


@app.post("/logout")
async def logout(request: Request):
    """Revoke token and clear cookie."""
    pool = get_user_pool()
    token = request.cookies.get(AUTH_COOKIE)

    if pool and token:
        try:
            await pool.revoke_token(token)
        except Exception:
            pass

    response = JSONResponse(content={"success": True})
    response.delete_cookie(AUTH_COOKIE, path="/")
    return response


# =============================================================================
# API Routes
# =============================================================================

@app.get("/api/me")
async def get_me(request: Request):
    """Get current user info."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")

    return {
        "email": user["email"],
        "roles": get_roles(user),
        "role": get_primary_role(user),
        "can_view_analytics": can_view_analytics(user),
        "can_manage_users": can_manage_users(user),
        "sso": True,
    }


@app.get("/analytics")
async def get_analytics(request: Request, hours: int = 24, db=Depends(get_scoped_db)):
    """
    Get click analytics from click_tracker's data.

    This demonstrates CROSS-APP DATA ACCESS:
    - Dashboard has read_scopes: ["dashboard", "click_tracker"]
    - So it can read click_tracker's clicks collection
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")

    if not can_view_analytics(user):
        raise HTTPException(403, "Tracker or admin role required")

    since = datetime.utcnow() - timedelta(hours=hours)

    # CROSS-APP ACCESS: Read click_tracker's clicks collection!
    # This works because manifest has read_scopes: ["dashboard", "click_tracker"]
    clicks_collection = db.get_collection("click_tracker_clicks")
    clicks = await clicks_collection.find({"timestamp": {"$gte": since}}).to_list(1000)

    # Aggregate stats
    role_counts = {}
    url_counts = {}
    for c in clicks:
        r = c.get("user_role", "unknown")
        role_counts[r] = role_counts.get(r, 0) + 1

        url = c.get("url", "/")
        url_counts[url] = url_counts.get(url, 0) + 1

    top_urls = sorted(url_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "period_hours": hours,
        "total_clicks": len(clicks),
        "unique_users": len(set(c.get("user_id") for c in clicks)),
        "clicks_by_role": role_counts,
        "top_urls": [{"url": u, "count": c} for u, c in top_urls],
        "recent_clicks": [
            {
                "user_id": c["user_id"],
                "user_role": c.get("user_role", "unknown"),
                "url": c.get("url", "/"),
                "timestamp": c["timestamp"].isoformat(),
            }
            for c in sorted(clicks, key=lambda x: x["timestamp"], reverse=True)[:20]
        ],
        "cross_app_access": True,  # Indicate we're reading from click_tracker
    }


# =============================================================================
# Admin Routes
# =============================================================================

@app.get("/admin/users")
async def list_users(request: Request, db=Depends(get_scoped_db)):
    """List all shared users. Requires: admin role."""
    user = get_current_user(request)
    if not user or not can_manage_users(user):
        raise HTTPException(403, "Admin role required")

    # Get shared users collection directly
    raw_db = engine._client[engine._db_name]
    shared_users = await raw_db["_mdb_engine_shared_users"].find({}).to_list(100)

    return {
        "users": [
            {
                "email": u["email"],
                "app_roles": u.get("app_roles", {}),
                "click_tracker_role": get_roles(u, "click_tracker"),
                "dashboard_role": get_roles(u, "dashboard"),
                "created_at": u.get("created_at").isoformat() if u.get("created_at") else None,
            }
            for u in shared_users
        ],
        "shared_pool": True,
    }


@app.post("/admin/update-role")
async def update_role(
    request: Request,
    email: str = Form(...),
    role: str = Form(...),
    target_app: str = Form("dashboard"),
):
    """
    Update user role for a specific app.

    Admins can update roles for ANY app (click_tracker or dashboard)!
    """
    user = get_current_user(request)
    if not user or not can_manage_users(user):
        raise HTTPException(403, "Admin role required")

    if role not in ["clicker", "tracker", "admin"]:
        raise HTTPException(400, "Invalid role")

    if target_app not in ["click_tracker", "dashboard"]:
        raise HTTPException(400, "Invalid target app")

    pool = get_user_pool()
    if not pool:
        raise HTTPException(500, "User pool not initialized")

    # Update the user's role for the target app
    success = await pool.update_user_roles(email, target_app, [role])

    if not success:
        raise HTTPException(404, "User not found")

    return {
        "success": True,
        "message": f"{email} is now {role} on {target_app}",
        "target_app": target_app,
    }


# =============================================================================
# Pages
# =============================================================================

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Dashboard page."""
    user = get_current_user(request)
    role = get_primary_role(user) if user else None

    return templates.TemplateResponse("index.html", {
        "request": request,
        "user": user,
        "role": role,
        "can_view_analytics": can_view_analytics(user) if user else False,
        "can_manage_users": can_manage_users(user) if user else False,
    })


@app.get("/health")
async def health():
    return {"status": "healthy", "app": APP_SLUG, "auth": "sso"}


@app.get("/api")
async def api_info():
    return {
        "app": APP_SLUG,
        "auth_mode": "shared (SSO)",
        "cross_app_access": ["click_tracker"],
        "demo_users": [
            "alice@example.com (admin)",
            "bob@example.com (tracker)",
            "charlie@example.com (clicker - no access)"
        ],
        "password": "password123",
        "sso_note": "Login on Click Tracker = logged in here too!",
    }
