#!/usr/bin/env python3
"""
Click Tracker Dashboard Application
====================================

Admin dashboard for viewing click analytics from ClickTracker app.
Demonstrates cross-app data access with Casbin authorization.

Key Features:
- Uses engine.create_app() for automatic lifecycle management
- Casbin authorization auto-initialized from manifest
- Demo users auto-seeded from manifest config
- Cross-app read access to click_tracker data
- RBAC: admin (full analytics), analyst (read only)
"""

import os
from datetime import datetime, timedelta
from pathlib import Path

from bson import ObjectId
from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from mdb_engine import MongoDBEngine
from mdb_engine.dependencies import get_scoped_db, get_authz_provider
from mdb_engine.auth import (
    authenticate_app_user,
    create_app_session,
    get_app_user,
    logout_user,
)

# =============================================================================
# App Configuration
# =============================================================================

APP_SLUG = "click_tracker_dashboard"

# Initialize the MongoDB Engine
engine = MongoDBEngine(
    mongo_uri=os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
    db_name=os.getenv("MONGODB_DB", "mdb_runtime"),
)

# Create FastAPI app with automatic lifecycle management
# Multi-site mode is auto-detected from manifest (read_scopes includes click_tracker)
app = engine.create_app(
    slug=APP_SLUG,
    manifest=Path(__file__).parent / "manifest.json",
    title="Click Tracker Dashboard",
    description="Analytics dashboard with cross-app data access",
    version="1.0.0",
)

# Templates
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


# =============================================================================
# Helper: Get Current User
# =============================================================================


async def get_current_user(request: Request):
    """Get the currently authenticated user from session cookie."""
    db = engine.get_scoped_db(APP_SLUG)
    app_config = engine.get_app(APP_SLUG)

    user = await get_app_user(
        request=request,
        slug_id=APP_SLUG,
        db=db,
        config=app_config,
        allow_demo_fallback=False,
    )
    return user


# =============================================================================
# Routes: Pages & Health
# =============================================================================


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Root endpoint - HTML dashboard page."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "app": APP_SLUG,
        "engine": "initialized" if engine.initialized else "starting",
        "authz": "configured" if hasattr(app.state, "authz_provider") else "not_configured",
    }


@app.get("/api")
async def api_info():
    """API info endpoint - lists available endpoints."""
    return {
        "app": APP_SLUG,
        "endpoints": {
            "GET /": "HTML dashboard page",
            "POST /login": "Authenticate user",
            "GET /logout": "Logout user",
            "GET /api/me": "Get current user info and permissions",
            "GET /analytics": "Get click analytics (requires read permission)",
            "GET /health": "Health check",
        },
    }


# =============================================================================
# Routes: Authentication
# =============================================================================


@app.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db=Depends(get_scoped_db),
):
    """Authenticate user and create session."""
    user = await authenticate_app_user(
        db=db,
        email=email,
        password=password,
        collection_name="users",
    )

    if not user:
        return JSONResponse(
            status_code=401,
            content={"success": False, "detail": "Invalid credentials"},
        )

    response = JSONResponse(content={"success": True, "user_id": str(user["_id"])})
    app_config = engine.get_app(APP_SLUG)

    await create_app_session(
        request=request,
        slug_id=APP_SLUG,
        user_id=str(user["_id"]),
        config=app_config,
        response=response,
    )

    return response


@app.post("/logout")
async def logout(request: Request):
    """Clear session and logout user."""
    response = JSONResponse(content={"success": True})
    response = await logout_user(request, response)

    app_config = engine.get_app(APP_SLUG)
    if app_config:
        auth = app_config.get("auth", {})
        users_config = auth.get("users", {})
        cookie_name = f"{users_config.get('session_cookie_name', 'app_session')}_{APP_SLUG}"
        response.delete_cookie(key=cookie_name, httponly=True, samesite="lax")

    return response


# =============================================================================
# Routes: User Info & Permissions
# =============================================================================


@app.get("/api/me")
async def get_me(request: Request, authz=Depends(get_authz_provider)):
    """Get current user info and their permissions."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    email = user.get("email", "unknown")
    permissions = []

    if authz:
        for action in ["read", "export"]:
            try:
                if await authz.check(email, "analytics", action):
                    permissions.append(action)
            except Exception:
                pass

    return {
        "email": email,
        "role": user.get("role", "unknown"),
        "permissions": permissions,
    }


# =============================================================================
# Routes: Analytics (Protected by Casbin, Cross-App Data Access)
# =============================================================================


@app.get("/analytics")
async def get_analytics(
    request: Request,
    hours: int = 24,
    authz=Depends(get_authz_provider),
    db=Depends(get_scoped_db),
):
    """
    Get click analytics from ClickTracker app.
    
    Demonstrates cross-app data access - Dashboard reads from ClickTracker's collections.
    Requires 'read' permission on 'analytics'.
    """
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if authz and not await authz.check(user.get("email"), "analytics", "read"):
        raise HTTPException(status_code=403, detail="Permission denied: cannot read analytics")

    # Access ClickTracker's clicks collection (cross-app read)
    # Collection name is prefixed: click_tracker_clicks
    since = datetime.utcnow() - timedelta(hours=hours)

    # Query ClickTracker's clicks collection
    clicks = await db.get_collection("click_tracker_clicks").find(
        {"timestamp": {"$gte": since}}
    ).sort("timestamp", -1).to_list(length=1000)

    # Convert for JSON
    def convert_for_json(obj):
        if isinstance(obj, ObjectId):
            return str(obj)
        elif isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, dict):
            return {k: convert_for_json(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert_for_json(item) for item in obj]
        return obj

    clicks = convert_for_json(clicks)

    # Aggregate analytics
    total_clicks = len(clicks)
    unique_users = len(set(c.get("user_id") for c in clicks))
    unique_sessions = len(set(c.get("session_id") for c in clicks if c.get("session_id")))

    # Top URLs
    url_counts = {}
    for click in clicks:
        url = click.get("url", "unknown")
        url_counts[url] = url_counts.get(url, 0) + 1
    top_urls = sorted(url_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    # Top elements
    element_counts = {}
    for click in clicks:
        element = click.get("element", "unknown")
        element_counts[element] = element_counts.get(element, 0) + 1
    top_elements = sorted(element_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    return JSONResponse(content={
        "period_hours": hours,
        "total_clicks": total_clicks,
        "unique_users": unique_users,
        "unique_sessions": unique_sessions,
        "top_urls": [{"url": url, "count": count} for url, count in top_urls],
        "top_elements": [{"element": elem, "count": count} for elem, count in top_elements],
        "recent_clicks": clicks[:50],
        "queried_by": user.get("email"),
    })


@app.get("/export")
async def export_analytics(
    request: Request,
    hours: int = 24,
    authz=Depends(get_authz_provider),
    db=Depends(get_scoped_db),
):
    """
    Export full click data (admin only).
    Requires 'export' permission on 'analytics'.
    """
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if authz and not await authz.check(user.get("email"), "analytics", "export"):
        raise HTTPException(status_code=403, detail="Permission denied: cannot export analytics")

    since = datetime.utcnow() - timedelta(hours=hours)

    clicks = await db.get_collection("click_tracker_clicks").find(
        {"timestamp": {"$gte": since}}
    ).sort("timestamp", -1).to_list(length=10000)

    # Convert for JSON
    def convert_for_json(obj):
        if isinstance(obj, ObjectId):
            return str(obj)
        elif isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, dict):
            return {k: convert_for_json(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert_for_json(item) for item in obj]
        return obj

    return JSONResponse(content={
        "export_type": "full",
        "period_hours": hours,
        "total_records": len(clicks),
        "exported_by": user.get("email"),
        "exported_at": datetime.utcnow().isoformat(),
        "data": convert_for_json(clicks),
    })


# =============================================================================
# Run with uvicorn (for local development)
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
