#!/usr/bin/env python3
"""
Click Tracker Application
=========================

Tracks user clicks and events with role-based access control using Casbin.

Key Features:
- Uses engine.create_app() for automatic lifecycle management
- Casbin authorization auto-initialized from manifest
- Demo users auto-seeded from manifest config
- RBAC: admin (full), editor (read+write), viewer (read only)
"""

import os
from datetime import datetime
from pathlib import Path

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

APP_SLUG = "click_tracker"

# Initialize the MongoDB Engine
engine = MongoDBEngine(
    mongo_uri=os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
    db_name=os.getenv("MONGODB_DB", "mdb_runtime"),
)

# Create FastAPI app with automatic lifecycle management
# This automatically handles:
# - Engine initialization and shutdown
# - Manifest loading and validation
# - Casbin authorization provider initialization
# - Demo user seeding
# - Multi-site mode detection from manifest
app = engine.create_app(
    slug=APP_SLUG,
    manifest=Path(__file__).parent / "manifest.json",
    title="Click Tracker",
    description="Track user clicks with role-based access control",
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
    """Root endpoint - HTML demo page."""
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
            "GET /": "HTML demo page",
            "POST /login": "Authenticate user",
            "GET /logout": "Logout user",
            "GET /api/me": "Get current user info and permissions",
            "POST /track": "Track a click event (requires write permission)",
            "GET /clicks": "Get click history (requires read permission)",
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
        for action in ["read", "write", "delete"]:
            try:
                if await authz.check(email, "clicks", action):
                    permissions.append(action)
            except Exception:
                pass

    return {
        "email": email,
        "role": user.get("role", "unknown"),
        "permissions": permissions,
    }


# =============================================================================
# Routes: Click Tracking (Protected by Casbin)
# =============================================================================


@app.post("/track")
async def track_click(
    request: Request,
    authz=Depends(get_authz_provider),
    db=Depends(get_scoped_db),
):
    """Track a click event. Requires 'write' permission on 'clicks'."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if authz and not await authz.check(user.get("email"), "clicks", "write"):
        raise HTTPException(status_code=403, detail="Permission denied: cannot write clicks")

    # Parse request body (optional - uses defaults if not provided)
    try:
        body = await request.json()
    except Exception:
        body = {}

    click_doc = {
        "user_id": body.get("user_id", user.get("email")),
        "timestamp": datetime.utcnow(),
        "session_id": body.get("session_id", "default_session"),
        "url": body.get("url", "/"),
        "element": body.get("element", "unknown"),
        "tracked_by": user.get("email"),
    }

    result = await db.clicks.insert_one(click_doc)

    return JSONResponse(content={
        "click_id": str(result.inserted_id),
        "status": "tracked",
    })


@app.get("/clicks")
async def get_clicks(
    request: Request,
    user_id: str = None,
    limit: int = 100,
    authz=Depends(get_authz_provider),
    db=Depends(get_scoped_db),
):
    """Get click history. Requires 'read' permission on 'clicks'."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if authz and not await authz.check(user.get("email"), "clicks", "read"):
        raise HTTPException(status_code=403, detail="Permission denied: cannot read clicks")

    query = {}
    if user_id:
        query["user_id"] = user_id

    clicks = await db.clicks.find(query).sort("timestamp", -1).limit(limit).to_list(length=limit)

    # Convert for JSON
    for click in clicks:
        click["_id"] = str(click["_id"])
        if click.get("timestamp"):
            click["timestamp"] = click["timestamp"].isoformat()

    return {"clicks": clicks, "count": len(clicks)}


@app.delete("/clicks/{click_id}")
async def delete_click(
    click_id: str,
    request: Request,
    authz=Depends(get_authz_provider),
    db=Depends(get_scoped_db),
):
    """Delete a click. Requires 'delete' permission on 'clicks'."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if authz and not await authz.check(user.get("email"), "clicks", "delete"):
        raise HTTPException(status_code=403, detail="Permission denied: cannot delete clicks")

    from bson import ObjectId

    result = await db.clicks.delete_one({"_id": ObjectId(click_id)})

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Click not found")

    return {"deleted": True, "click_id": click_id}


# =============================================================================
# Run with uvicorn (for local development)
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
