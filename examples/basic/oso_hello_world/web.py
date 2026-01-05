#!/usr/bin/env python3
"""
OSO Cloud Hello World Example

An interactive demo showing OSO Cloud authorization with mdb-engine.
Uses engine.create_app() pattern for automatic lifecycle management.
"""
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from mdb_engine import MongoDBEngine
from mdb_engine.auth import (
    authenticate_app_user,
    create_app_session,
    get_app_user,
    get_authz_provider,
    logout_user,
)
from mdb_engine.auth.provider import AuthorizationProvider

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# App configuration
APP_SLUG = "oso_hello_world"

# Initialize the MongoDB Engine
engine = MongoDBEngine(
    mongo_uri=os.getenv("MONGO_URI", "mongodb://mongodb:27017/"),
    db_name=os.getenv("MONGO_DB_NAME", "oso_hello_world_db"),
)

# Create FastAPI app with automatic lifecycle management
# This automatically handles:
# - Engine initialization and shutdown
# - Manifest loading and validation
# - Auth setup from manifest (OSO, CORS, etc.)
app = engine.create_app(
    slug=APP_SLUG,
    manifest=Path(__file__).parent / "manifest.json",
    title="OSO Cloud Hello World",
    description="An interactive demo showing OSO Cloud authorization",
    version="1.0.0",
)

# Templates
templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))


# ============================================================================
# Dependency Injection
# ============================================================================


def get_db():
    """Get the scoped database"""
    if not engine.initialized:
        raise HTTPException(503, "Engine not initialized")
    return engine.get_scoped_db(APP_SLUG)


async def get_current_app_user(request: Request):
    """Helper to get current app user for oso_hello_world app."""
    db = get_db()
    app_config = engine.get_app(APP_SLUG)

    app_user = await get_app_user(
        request=request,
        slug_id=APP_SLUG,
        db=db,
        config=app_config,
        allow_demo_fallback=False,
    )

    if not app_user:
        # Check for stale cookie
        auth = app_config.get("auth", {}) if app_config else {}
        users_config = auth.get("users", {})
        cookie_name = f"{users_config.get('session_cookie_name', 'app_session')}_{APP_SLUG}"
        if request.cookies.get(cookie_name):
            request.state.clear_stale_session = True

    return app_user


# ============================================================================
# Health Check Endpoint (for container healthchecks)
# ============================================================================


@app.get("/health", response_class=JSONResponse)
async def health_check():
    """Health check endpoint for container orchestration."""
    health_status = {
        "status": "healthy",
        "app": APP_SLUG,
        "engine_initialized": engine.initialized,
    }

    # Check MongoDB connection
    if engine.initialized:
        try:
            db = engine.get_scoped_db(APP_SLUG)
            await db.command("ping")
            health_status["database"] = "connected"
        except Exception as e:
            health_status["status"] = "degraded"
            health_status["database"] = f"error: {str(e)}"
    else:
        health_status["status"] = "starting"
        health_status["database"] = "not_connected"

    # Check OSO provider
    if hasattr(app.state, "authz_provider"):
        health_status["oso"] = "configured"
    else:
        health_status["oso"] = "not_configured"

    status_code = 200 if health_status["status"] == "healthy" else 503
    return JSONResponse(health_status, status_code=status_code)


# ============================================================================
# Routes
# ============================================================================


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Render the main page."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    """Handle login returning JSON for better UI/UX."""
    db = get_db()
    app_config = engine.get_app(APP_SLUG)

    user = await authenticate_app_user(
        db=db, email=email, password=password, collection_name="users"
    )

    if not user:
        return JSONResponse(
            status_code=401, content={"success": False, "detail": "Invalid credentials"}
        )

    # Prepare success response
    response = JSONResponse(content={"success": True, "user_id": str(user["_id"])})

    # This helper attaches the cookie to the response object
    await create_app_session(
        request=request,
        slug_id=APP_SLUG,
        user_id=str(user["_id"]),
        config=app_config,
        response=response,
    )

    logger.info(f"✅ User logged in: {email}")
    return response


@app.get("/logout")
async def logout(request: Request):
    """Logout returning JSON"""
    response = JSONResponse(content={"success": True})
    response = await logout_user(request, response)

    # Clear the app-specific session cookie
    cookie_names_to_clear = []

    app_config = engine.get_app(APP_SLUG)
    if app_config:
        auth = app_config.get("auth", {})
        users_config = auth.get("users", {})
        session_cookie_name = users_config.get("session_cookie_name", "app_session")
        cookie_name = f"{session_cookie_name}_{APP_SLUG}"
        cookie_names_to_clear.append(cookie_name)

    # Also try default names in case config lookup fails
    cookie_names_to_clear.append(f"oso_hello_world_session_{APP_SLUG}")
    cookie_names_to_clear.append(f"app_session_{APP_SLUG}")

    # Get cookie settings to match how it was set
    should_use_secure = request.url.scheme == "https" or os.getenv("APP_ENV") == "production"

    # Delete all possible cookie names (deduplicated)
    for cookie_name in set(cookie_names_to_clear):
        response.delete_cookie(
            key=cookie_name, httponly=True, secure=should_use_secure, samesite="lax"
        )

    return response


@app.get("/api/me")
async def get_current_user_info(
    request: Request, authz: AuthorizationProvider = Depends(get_authz_provider)
):
    """Get current user information and permissions."""
    user = await get_current_app_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user_email = user.get("email", "unknown")

    permissions = []
    for action in ["read", "write"]:
        if await authz.check(user_email, "documents", action):
            permissions.append(action)

    return {
        "user_id": user.get("user_id"),
        "email": user_email,
        "permissions": permissions,
        "role": "editor" if "write" in permissions else "viewer",
    }


@app.get("/api/oso-status")
async def get_oso_status():
    """Check OSO Cloud connection status."""
    status = {"connected": False}
    if hasattr(app.state, "authz_provider"):
        try:
            await app.state.authz_provider.check("test", "test", "test")
            status["connected"] = True
        except (AttributeError, RuntimeError, ConnectionError, ValueError):
            pass  # Keep connected=False
    return status


@app.get("/api/documents")
async def list_documents(
    request: Request, authz: AuthorizationProvider = Depends(get_authz_provider)
):
    """List all documents (requires read permission)."""
    user = await get_current_app_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")

    if not await authz.check(user.get("email"), "documents", "read"):
        raise HTTPException(403, "Permission denied")

    db = get_db()
    docs = await db.documents.find({}).sort("created_at", -1).to_list(100)

    return {
        "documents": [
            {
                "id": str(d["_id"]),
                "title": d.get("title"),
                "content": d.get("content"),
                "created_by": d.get("created_by"),
                "created_at": d.get("created_at").isoformat() if d.get("created_at") else None,
            }
            for d in docs
        ]
    }


@app.post("/api/documents")
async def create_document(
    request: Request, authz: AuthorizationProvider = Depends(get_authz_provider)
):
    """Create a new document (requires write permission)."""
    user = await get_current_app_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")

    if not await authz.check(user.get("email"), "documents", "write"):
        raise HTTPException(403, "Permission denied")

    body = await request.json()
    db = get_db()

    doc = {
        "title": body.get("title", "Untitled"),
        "content": body.get("content", ""),
        "created_by": user.get("email"),
        "created_at": datetime.utcnow(),
    }

    res = await db.documents.insert_one(doc)
    doc["_id"] = str(res.inserted_id)
    doc["created_at"] = doc["created_at"].isoformat()

    return {"success": True, "document": doc}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
