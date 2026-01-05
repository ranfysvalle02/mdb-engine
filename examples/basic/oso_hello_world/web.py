#!/usr/bin/env python3
"""
OSO Cloud Hello World Example
=============================

A clean, minimal example showing OSO Cloud authorization with mdb-engine.

Key Features:
- Uses engine.create_app() for automatic lifecycle management
- OSO Cloud provider auto-initialized from manifest config
- Demo users auto-seeded from manifest config
- Simple RBAC: Alice (editor) can read+write, Bob (viewer) can only read
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
from mdb_engine.dependencies import get_scoped_db, get_authz_provider
from mdb_engine.auth import (
    authenticate_app_user,
    create_app_session,
    get_app_user,
    logout_user,
)

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# =============================================================================
# App Configuration
# =============================================================================

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
# - OSO Cloud provider initialization (from manifest auth.policy.provider: "oso")
# - Demo user seeding (from manifest auth.users.demo_users)
# - CORS, security middleware, etc.
app = engine.create_app(
    slug=APP_SLUG,
    manifest=Path(__file__).parent / "manifest.json",
    title="OSO Cloud Hello World",
    description="Interactive demo showing OSO Cloud authorization with mdb-engine",
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
# Routes: Health & Status
# =============================================================================


@app.get("/health", response_class=JSONResponse)
async def health():
    """Health check endpoint for container orchestration."""
    return {
        "status": "healthy",
        "app": APP_SLUG,
        "engine": "initialized" if engine.initialized else "starting",
        "oso": "configured" if hasattr(app.state, "authz_provider") else "not_configured",
    }


@app.get("/api/oso-status")
async def oso_status(authz=Depends(get_authz_provider)):
    """Check if OSO Cloud is connected and working."""
    if not authz:
        return {"connected": False, "error": "Provider not initialized"}

    try:
        # Test with a simple authorization check
        await authz.check("test@test.com", "documents", "read")
        return {"connected": True}
    except Exception as e:
        logger.warning(f"OSO status check failed: {e}")
        return {"connected": False, "error": str(e)}


# =============================================================================
# Routes: Pages
# =============================================================================


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Render the main demo page."""
    return templates.TemplateResponse("index.html", {"request": request})


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
    # Authenticate against app's users collection
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

    # Create session and set cookie
    response = JSONResponse(content={"success": True, "user_id": str(user["_id"])})
    app_config = engine.get_app(APP_SLUG)

    await create_app_session(
        request=request,
        slug_id=APP_SLUG,
        user_id=str(user["_id"]),
        config=app_config,
        response=response,
    )

    logger.info(f"✅ User logged in: {email}")
    return response


@app.post("/logout")
async def logout(request: Request):
    """Clear session and logout user."""
    response = JSONResponse(content={"success": True})
    response = await logout_user(request, response)

    # Clear the app-specific session cookie
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

    if not authz:
        raise HTTPException(status_code=503, detail="Authorization service unavailable")

    email = user.get("email", "unknown")

    # Check what permissions this user has
    permissions = []
    for action in ["read", "write"]:
        try:
            if await authz.check(email, "documents", action):
                permissions.append(action)
        except Exception as e:
            logger.warning(f"Permission check failed for {email}/{action}: {e}")

    return {
        "user_id": user.get("user_id"),
        "email": email,
        "permissions": permissions,
        "role": "editor" if "write" in permissions else "viewer",
    }


# =============================================================================
# Routes: Documents (Protected by OSO)
# =============================================================================


@app.get("/api/documents")
async def list_documents(
    request: Request,
    authz=Depends(get_authz_provider),
    db=Depends(get_scoped_db),
):
    """List all documents. Requires 'read' permission."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if not authz:
        raise HTTPException(status_code=503, detail="Authorization service unavailable")

    # Check read permission via OSO
    if not await authz.check(user.get("email"), "documents", "read"):
        raise HTTPException(status_code=403, detail="Permission denied: cannot read documents")

    # Fetch documents
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
    request: Request,
    authz=Depends(get_authz_provider),
    db=Depends(get_scoped_db),
):
    """Create a new document. Requires 'write' permission."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if not authz:
        raise HTTPException(status_code=503, detail="Authorization service unavailable")

    # Check write permission via OSO
    if not await authz.check(user.get("email"), "documents", "write"):
        raise HTTPException(status_code=403, detail="Permission denied: cannot write documents")

    # Parse request body
    body = await request.json()

    # Create document
    doc = {
        "title": body.get("title", "Untitled"),
        "content": body.get("content", ""),
        "created_by": user.get("email"),
        "created_at": datetime.utcnow(),
    }

    result = await db.documents.insert_one(doc)
    doc["_id"] = str(result.inserted_id)
    doc["created_at"] = doc["created_at"].isoformat()

    logger.info(f"📝 Document created by {user.get('email')}: {doc['title']}")
    return {"success": True, "document": doc}


# =============================================================================
# Run with uvicorn (for local development)
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
