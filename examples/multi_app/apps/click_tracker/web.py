"""
ClickTracker Application

Tracks user clicks and events. Demonstrates app-level authentication.
"""

import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from mdb_engine import MongoDBEngine

# App secret from environment (will be auto-retrieved from DB if not set)
CLICK_TRACKER_SECRET = os.getenv("CLICK_TRACKER_SECRET")

# Global engine instance
engine: MongoDBEngine = None

# Cache for auto-retrieved secret
_secret_retrieved = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for app initialization and cleanup."""
    global engine
    
    # Initialize engine
    engine = MongoDBEngine(
        mongo_uri=os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
        db_name=os.getenv("MONGODB_DB", "mdb_runtime"),
    )
    
    await engine.initialize()
    
    # Ensure secrets manager is initialized
    if not engine._app_secrets_manager:
        print("⚠️  Secrets manager not initialized. Checking master key...")
        master_key = os.getenv("MDB_ENGINE_MASTER_KEY")
        if not master_key:
            print("❌ MDB_ENGINE_MASTER_KEY not set in environment!")
            print("   Run: ./scripts/setup.sh to generate master key")
            sys.exit(1)
        else:
            print(f"✅ Master key found (first 10 chars: {master_key[:10]}...)")
            # Try to manually initialize secrets manager
            from mdb_engine.core.app_secrets import AppSecretsManager
            from mdb_engine.core.encryption import EnvelopeEncryptionService
            encryption_service = EnvelopeEncryptionService(master_key=master_key)
            engine._app_secrets_manager = AppSecretsManager(
                mongo_db=engine._connection_manager.mongo_db,
                encryption_service=encryption_service
            )
            print("✅ Secrets manager initialized manually")
    
    # Load manifest
    from pathlib import Path
    manifest_path = Path(__file__).parent / "manifest.json"
    manifest = await engine.load_manifest(manifest_path)
    
    # Register app (generates secret if not exists)
    await engine.register_app(manifest)
    
    # Auto-retrieve secret from DB if not in environment (no restart needed!)
    global CLICK_TRACKER_SECRET, _secret_retrieved
    if not CLICK_TRACKER_SECRET and engine._app_secrets_manager:
        try:
            secret_exists = await engine._app_secrets_manager.app_secret_exists("click_tracker")
            if secret_exists:
                CLICK_TRACKER_SECRET = await engine._app_secrets_manager.get_app_secret("click_tracker")
                _secret_retrieved = True
                print(f"✅ Auto-retrieved app secret from database")
                print("💡 Tip: Add CLICK_TRACKER_SECRET to .env for better performance (optional)")
            else:
                # This shouldn't happen, but if it does, the secret will be auto-retrieved on first request
                print("⚠️  Secret not found in database after registration. Will auto-retrieve on first request.")
        except Exception as e:
            print(f"⚠️  Could not auto-retrieve secret at startup: {e}")
            print("   Secret will be auto-retrieved on first request")
    
    # Verify secret if provided (either from env or auto-retrieved)
    if CLICK_TRACKER_SECRET and engine._app_secrets_manager:
        try:
            is_valid = await engine._app_secrets_manager.verify_app_secret("click_tracker", CLICK_TRACKER_SECRET)
            if not is_valid:
                print("ERROR: Invalid CLICK_TRACKER_SECRET")
                sys.exit(1)
        except Exception as e:
            print(f"Warning: Could not verify secret: {e}")
    
    yield
    
    # Cleanup
    if engine:
        await engine.shutdown()


app = FastAPI(title="Click Tracker", lifespan=lifespan)

# Templates
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


# Global exception handler to ensure JSON errors
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
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
async def root():
    """Root endpoint - HTML demo page."""
    return templates.TemplateResponse("index.html", {"request": {}})


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
    # Try to ensure secret is available
    try:
        await _ensure_secret()
        secret_available = True
    except:
        secret_available = False
    
    info = {
        "secret_configured": bool(CLICK_TRACKER_SECRET),
        "secret_available": secret_available,
        "auto_retrieved": _secret_retrieved and not os.getenv("CLICK_TRACKER_SECRET"),
        "engine_initialized": engine is not None,
    }
    
    if engine and engine._app_secrets_manager:
        try:
            secret_exists = await engine._app_secrets_manager.app_secret_exists("click_tracker")
            info["secret_exists_in_db"] = secret_exists
            if secret_available:
                info["message"] = "Secret is available (auto-retrieved from database if not in environment)"
            elif secret_exists:
                info["message"] = "Secret exists in database but could not be retrieved. Check logs."
            else:
                info["message"] = "Secret not found in database. Check docker-compose logs for generated secret."
        except Exception as e:
            info["error"] = str(e)
    else:
        info["message"] = "Engine or secrets manager not initialized"
    
    return info


async def _ensure_secret():
    """Ensure app secret is available, auto-retrieve/generate from DB if needed."""
    global CLICK_TRACKER_SECRET, _secret_retrieved
    
    if CLICK_TRACKER_SECRET:
        return CLICK_TRACKER_SECRET
    
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    if not engine._app_secrets_manager:
        raise HTTPException(status_code=500, detail="Secrets manager not initialized")
    
    try:
        # Try to retrieve from database
        secret_exists = await engine._app_secrets_manager.app_secret_exists("click_tracker")
        
        if secret_exists:
            CLICK_TRACKER_SECRET = await engine._app_secrets_manager.get_app_secret("click_tracker")
            _secret_retrieved = True
            print(f"✅ Retrieved secret from database")
            return CLICK_TRACKER_SECRET
        
        # Secret doesn't exist - re-register to generate it
        print("⚠️  Secret not found, re-registering app to generate secret...")
        from pathlib import Path
        manifest_path = Path(__file__).parent / "manifest.json"
        manifest = await engine.load_manifest(manifest_path)
        await engine.register_app(manifest)
        
        # Try again after registration
        secret_exists = await engine._app_secrets_manager.app_secret_exists("click_tracker")
        if secret_exists:
            CLICK_TRACKER_SECRET = await engine._app_secrets_manager.get_app_secret("click_tracker")
            _secret_retrieved = True
            print(f"✅ Generated and retrieved secret")
            return CLICK_TRACKER_SECRET
        else:
            raise Exception("Secret was not generated during registration")
            
    except Exception as e:
        error_msg = f"Error retrieving/generating secret: {e}"
        print(f"❌ {error_msg}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"App secret error: {str(e)}. Check docker-compose logs for details."
        )


@app.post("/track")
async def track_click(click_data: Dict[str, Any] = None):
    """
    Track a click event - simple demo endpoint.
    """
    try:
        app_secret = await _ensure_secret()
        
        if not engine:
            raise HTTPException(status_code=500, detail="Engine not initialized")
        
        # Verify token first (async)
        if engine._app_secrets_manager:
            is_valid = await engine._app_secrets_manager.verify_app_secret("click_tracker", app_secret)
            if not is_valid:
                raise HTTPException(status_code=403, detail="Invalid app token")
        
        # Get scoped database with app token
        db = engine.get_scoped_db("click_tracker", app_token=app_secret)
        
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
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error tracking click: {str(e)}")


@app.get("/clicks")
async def get_clicks(user_id: str = None, limit: int = 100):
    """Get click history - simple API endpoint."""
    app_secret = await _ensure_secret()
    
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    db = engine.get_scoped_db("click_tracker", app_token=app_secret)
    
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

