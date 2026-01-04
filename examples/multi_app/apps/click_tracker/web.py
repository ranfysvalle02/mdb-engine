"""
ClickTracker Application

Tracks user clicks and events. Demonstrates app-level authentication
using the unified MongoDBEngine pattern.

The engine automatically:
- Detects multi-site mode from manifest (cross_app_policy: explicit)
- Auto-retrieves app tokens from environment or database
- Manages lifecycle with FastAPI integration
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pymongo.errors import PyMongoError

from mdb_engine import MongoDBEngine

# Initialize engine
engine = MongoDBEngine(
    mongo_uri=os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
    db_name=os.getenv("MONGODB_DB", "mdb_runtime"),
)

# Create FastAPI app with automatic lifecycle management
# Multi-site mode is auto-detected from manifest
app = engine.create_app(
    slug="click_tracker",
    manifest=Path(__file__).parent / "manifest.json",
    title="Click Tracker",
)

# Templates
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


# Global exception handler to ensure JSON errors
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    import traceback
    traceback.print_exc()
    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail}
        )
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error: {str(exc)}"}
    )


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Root endpoint - HTML demo page."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api")
async def api_info():
    """API info endpoint - lists available endpoints."""
    return {
        "app": "click_tracker",
        "endpoints": {
            "GET /": "HTML demo page",
            "POST /track": "Track a click event",
            "GET /clicks": "Get click history (optional: ?user_id=xxx&limit=100)",
            "GET /health": "Health check",
            "GET /docs": "API documentation (Swagger UI)",
        }
    }


@app.get("/secret-info")
async def secret_info():
    """Get information about app secret configuration."""
    token = engine.get_app_token("click_tracker")
    
    info = {
        "secret_configured": token is not None,
        "engine_initialized": engine._initialized,
        "has_secrets_manager": engine._app_secrets_manager is not None,
    }
    
    if engine._app_secrets_manager:
        try:
            secret_exists = await engine._app_secrets_manager.app_secret_exists("click_tracker")
            info["secret_exists_in_db"] = secret_exists
            if token:
                info["message"] = "Secret is available (auto-retrieved)"
            elif secret_exists:
                info["message"] = "Secret exists in database but not yet retrieved"
            else:
                info["message"] = "No secret found. Register app to generate one."
        except (PyMongoError, ValueError, RuntimeError) as e:
            info["error"] = str(e)
    else:
        info["message"] = "Secrets manager not initialized (set MDB_ENGINE_MASTER_KEY)"
    
    return info


async def _get_db():
    """Get scoped database with auto-retrieved token."""
    token = engine.get_app_token("click_tracker")
    if not token:
        # Try to auto-retrieve
        token = await engine.auto_retrieve_app_token("click_tracker")
    
    if not token:
        raise HTTPException(
            status_code=500,
            detail="App token not available. Set CLICK_TRACKER_SECRET or ensure app is registered."
        )
    
    return engine.get_scoped_db("click_tracker", app_token=token)


@app.post("/track")
async def track_click(click_data: Dict[str, Any] = None):
    """Track a click event - simple demo endpoint."""
    try:
        db = await _get_db()
        
        # Prepare click document (use defaults if not provided)
        click_doc = {
            "user_id": (click_data or {}).get("user_id", "demo_user"),
            "timestamp": datetime.utcnow(),
            "session_id": (click_data or {}).get("session_id", "demo_session"),
            "url": (click_data or {}).get("url", "/"),
            "element": (click_data or {}).get("element", "click-button"),
        }
        
        # Insert click
        result = await db.clicks.insert_one(click_doc)
        
        return JSONResponse(content={"click_id": str(result.inserted_id), "status": "tracked"})
    except HTTPException:
        raise
    except (PyMongoError, ValueError, TypeError, KeyError) as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error tracking click: {str(e)}")


@app.get("/clicks")
async def get_clicks(user_id: str = None, limit: int = 100):
    """Get click history - simple API endpoint."""
    db = await _get_db()
    
    query = {}
    if user_id:
        query["user_id"] = user_id
    
    clicks = await db.clicks.find(query).sort("timestamp", -1).limit(limit).to_list(length=limit)
    
    return {"clicks": clicks, "count": len(clicks)}


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "app": "click_tracker"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
