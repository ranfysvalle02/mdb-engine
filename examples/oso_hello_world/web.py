#!/usr/bin/env python3
"""
OSO Cloud Hello World Example

An interactive demo showing OSO Cloud authorization.
Improved UX/UI version.
"""
import os
import logging
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, Depends, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from mdb_engine import MongoDBEngine
from mdb_engine.auth import (
    get_authz_provider,
    setup_auth_from_manifest,
    get_app_user,
    authenticate_app_user,
    create_app_session,
    logout_user,
)
from mdb_engine.auth.provider import AuthorizationProvider

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Global engine instance
engine: Optional[MongoDBEngine] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for app initialization and cleanup."""
    global engine
    
    logger.info("=" * 60)
    logger.info("🚀 LIFESPAN STARTUP: Starting OSO Cloud Hello World Application...")
    logger.info("=" * 60)
    
    mongo_uri = os.getenv("MONGO_URI", "mongodb://mongodb:27017/")
    db_name = os.getenv("MONGO_DB_NAME", "oso_hello_world_db")
    
    engine = MongoDBEngine(mongo_uri=mongo_uri, db_name=db_name)
    await engine.initialize()
    
    manifest_path = Path(__file__).parent / "manifest.json"
    if manifest_path.exists():
        manifest = await engine.load_manifest(manifest_path)
        await engine.register_app(manifest, create_indexes=True)
    
    await setup_auth_from_manifest(app, engine, "oso_hello_world")
    
    if hasattr(app.state, "authz_provider"):
        logger.info("✅ Demo ready! Users: alice@example.com (editor), bob@example.com (viewer)")
    
    yield
    
    if engine:
        await engine.shutdown()

app = FastAPI(
    title="OSO Cloud Hello World",
    version="1.0.0",
    lifespan=lifespan
)

# CORS and stale session cleanup are now handled automatically by setup_auth_from_manifest()
# based on manifest.json configuration

templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

def get_db():
    if not engine: raise HTTPException(503, "Engine not initialized")
    return engine.get_scoped_db("oso_hello_world")

async def get_current_app_user(request: Request):
    """Helper to get current app user for oso_hello_world app."""
    db = get_db()
    app_config = engine.get_app("oso_hello_world") if engine else None
    
    app_user = await get_app_user(
        request=request,
        slug_id="oso_hello_world",
        db=db,
        config=app_config,
        allow_demo_fallback=False
    )
    
    if not app_user:
        # Check for stale cookie
        auth = app_config.get("auth", {}) if app_config else {}
        users_config = auth.get("users", {})
        cookie_name = f"{users_config.get('session_cookie_name', 'app_session')}_oso_hello_world"
        if request.cookies.get(cookie_name):
            request.state.clear_stale_session = True
            
    return app_user

# ============================================================================
# Routes
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    """
    Handle login returning JSON instead of Redirect for better UI/UX.
    """
    try:
        db = get_db()
        app_config = engine.get_app("oso_hello_world") if engine else None
        
        user = await authenticate_app_user(
            db=db,
            email=email,
            password=password,
            collection_name="users"
        )
        
        if not user:
            return JSONResponse(
                status_code=401,
                content={"success": False, "detail": "Invalid credentials"}
            )
        
        # Prepare success response
        response = JSONResponse(content={"success": True, "user_id": str(user["_id"])})
        
        # This helper attaches the cookie to the response object
        await create_app_session(
            request=request,
            slug_id="oso_hello_world",
            user_id=str(user["_id"]),
            config=app_config,
            response=response
        )
        
        logger.info(f"✅ User logged in via AJAX: {email}")
        return response
            
    except Exception as e:
        logger.error(f"❌ Login error: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"success": False, "detail": str(e)})

@app.get("/logout")
async def logout(request: Request):
    """Logout returning JSON"""
    response = JSONResponse(content={"success": True})
    response = await logout_user(request, response)
    
    # Clear the app-specific session cookie
    # Try to get cookie name from config, fallback to known values
    cookie_names_to_clear = []
    
    if engine:
        app_config = engine.get_app("oso_hello_world")
        if app_config:
            auth = app_config.get("auth", {})
            users_config = auth.get("users", {})
            session_cookie_name = users_config.get("session_cookie_name", "app_session")
            cookie_name = f"{session_cookie_name}_oso_hello_world"
            cookie_names_to_clear.append(cookie_name)
    
    # Also try the default name in case config lookup fails
    cookie_names_to_clear.append("oso_hello_world_session_oso_hello_world")
    cookie_names_to_clear.append("app_session_oso_hello_world")
    
    # Get cookie settings to match how it was set
    should_use_secure = request.url.scheme == "https" or os.getenv("G_NOME_ENV") == "production"
    
    # Delete all possible cookie names (deduplicated)
    for cookie_name in set(cookie_names_to_clear):
        response.delete_cookie(
            key=cookie_name,
            httponly=True,
            secure=should_use_secure,
            samesite="lax"
        )
    
    return response

@app.get("/api/me")
async def get_current_user_info(
    request: Request,
    authz: AuthorizationProvider = Depends(get_authz_provider)
):
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
        "role": "editor" if "write" in permissions else "viewer"
    }

@app.get("/api/oso-status")
async def get_oso_status():
    status = {"connected": False}
    if hasattr(app.state, "authz_provider"):
        try:
            # Simple check
            await app.state.authz_provider.check("test", "test", "test")
            status["connected"] = True
        except Exception:
            pass # Keep false
    return status

@app.get("/api/documents")
async def list_documents(
    request: Request,
    authz: AuthorizationProvider = Depends(get_authz_provider)
):
    user = await get_current_app_user(request)
    if not user: raise HTTPException(401)
    
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
                "created_at": d.get("created_at").isoformat()
            } for d in docs
        ]
    }

@app.post("/api/documents")
async def create_document(
    request: Request,
    authz: AuthorizationProvider = Depends(get_authz_provider)
):
    user = await get_current_app_user(request)
    if not user: raise HTTPException(401)
    
    if not await authz.check(user.get("email"), "documents", "write"):
        raise HTTPException(403, "Permission denied")
    
    body = await request.json()
    db = get_db()
    
    doc = {
        "title": body.get("title", "Untitled"),
        "content": body.get("content", ""),
        "created_by": user.get("email"),
        "created_at": datetime.utcnow()
    }
    
    res = await db.documents.insert_one(doc)
    doc["_id"] = str(res.inserted_id)
    doc["created_at"] = doc["created_at"].isoformat()
    
    return {"success": True, "document": doc}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)