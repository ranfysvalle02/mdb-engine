"""
ClickTrackerDashboard Application

Admin dashboard for viewing click analytics from ClickTracker app.
Demonstrates cross-app data access with secure app-level authentication.
"""

import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from bson import ObjectId

from mdb_engine import MongoDBEngine

# App secret from environment (will be auto-retrieved from DB if not set)
DASHBOARD_SECRET = os.getenv("DASHBOARD_SECRET")

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
    manifest_path = Path(__file__).parent / "manifest.json"
    manifest = await engine.load_manifest(manifest_path)
    
    # Register app (generates secret if not exists)
    try:
        await engine.register_app(manifest)
        print("✅ App registered successfully")
    except Exception as e:
        print(f"⚠️  Registration error (may already be registered): {e}")
    
    # Auto-retrieve secret from DB if not in environment (no restart needed!)
    global DASHBOARD_SECRET, _secret_retrieved
    if not DASHBOARD_SECRET and engine._app_secrets_manager:
        try:
            secret_exists = await engine._app_secrets_manager.app_secret_exists("click_tracker_dashboard")
            if secret_exists:
                DASHBOARD_SECRET = await engine._app_secrets_manager.get_app_secret("click_tracker_dashboard")
                _secret_retrieved = True
                print(f"✅ Auto-retrieved app secret from database")
                print("💡 Tip: Add DASHBOARD_SECRET to .env for better performance (optional)")
            else:
                print("⚠️  Secret not found in database after registration. Will auto-retrieve on first request.")
        except Exception as e:
            print(f"⚠️  Could not auto-retrieve secret at startup: {e}")
            import traceback
            traceback.print_exc()
            print("   Secret will be auto-retrieved on first request")
    
    # Verify secret if provided (either from env or auto-retrieved)
    if DASHBOARD_SECRET and engine._app_secrets_manager:
        try:
            is_valid = await engine._app_secrets_manager.verify_app_secret("click_tracker_dashboard", DASHBOARD_SECRET)
            if not is_valid:
                print("⚠️  Invalid DASHBOARD_SECRET, will regenerate on first request")
                DASHBOARD_SECRET = None  # Clear invalid secret
        except Exception as e:
            print(f"⚠️  Could not verify secret: {e}")
            DASHBOARD_SECRET = None  # Clear on error
    
    yield
    
    # Cleanup
    if engine:
        await engine.shutdown()


app = FastAPI(title="Click Tracker Dashboard", lifespan=lifespan)

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
    """Root endpoint - HTML dashboard page."""
    return templates.TemplateResponse("index.html", {"request": {}})


@app.get("/api")
async def api_info():
    """API info endpoint - lists available endpoints."""
    return {
        "app": "click_tracker_dashboard",
        "endpoints": {
            "GET /": "HTML dashboard page",
            "GET /analytics": "Get click analytics (optional: ?hours=24)",
            "GET /dashboard": "Dashboard UI with analytics",
            "GET /health": "Health check",
            "GET /docs": "API documentation (Swagger UI)",
        }
    }


async def _ensure_secret():
    """Ensure app secret is available, auto-retrieve/generate from DB if needed."""
    global DASHBOARD_SECRET, _secret_retrieved
    
    if DASHBOARD_SECRET:
        return DASHBOARD_SECRET
    
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    if not engine._app_secrets_manager:
        raise HTTPException(status_code=500, detail="Secrets manager not initialized")
    
    try:
        # Try to retrieve from database
        secret_exists = await engine._app_secrets_manager.app_secret_exists("click_tracker_dashboard")
        
        if secret_exists:
            DASHBOARD_SECRET = await engine._app_secrets_manager.get_app_secret("click_tracker_dashboard")
            _secret_retrieved = True
            print(f"✅ Retrieved secret from database")
            return DASHBOARD_SECRET
        
        # Secret doesn't exist - re-register to generate it
        print("⚠️  Secret not found, re-registering app to generate secret...")
        manifest_path = Path(__file__).parent / "manifest.json"
        manifest = await engine.load_manifest(manifest_path)
        await engine.register_app(manifest)
        
        # Try again after registration
        secret_exists = await engine._app_secrets_manager.app_secret_exists("click_tracker_dashboard")
        if secret_exists:
            DASHBOARD_SECRET = await engine._app_secrets_manager.get_app_secret("click_tracker_dashboard")
            _secret_retrieved = True
            print(f"✅ Generated and retrieved secret")
            return DASHBOARD_SECRET
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


@app.get("/analytics")
async def get_analytics(hours: int = 24):
    """
    Get click analytics (reads from ClickTracker app).
    
    Demonstrates cross-app data access - Dashboard reads from ClickTracker's collections.
    Requires valid app_token for Dashboard.
    """
    app_secret = await _ensure_secret()
    
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    # Verify token first (async)
    if engine._app_secrets_manager:
        is_valid = await engine._app_secrets_manager.verify_app_secret("click_tracker_dashboard", app_secret)
        if not is_valid:
            raise HTTPException(status_code=403, detail="Invalid app token")
    
    # Get scoped database with app token
    # Note: read_scopes includes "click_tracker" for cross-app access
    db = engine.get_scoped_db(
        "click_tracker_dashboard",
        app_token=app_secret,
    )
    
    # Access ClickTracker's clicks collection (cross-app read)
    # Collection name is prefixed: click_tracker_clicks
    since = datetime.utcnow() - timedelta(hours=hours)
    
    # Query ClickTracker's clicks collection
    clicks = await db.get_collection("click_tracker_clicks").find(
        {"timestamp": {"$gte": since}}
    ).sort("timestamp", -1).to_list(length=1000)
    
    # Convert ObjectIds and datetimes to JSON-serializable types
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
    
    return JSONResponse(content={
        "period_hours": hours,
        "total_clicks": total_clicks,
        "unique_users": unique_users,
        "unique_sessions": unique_sessions,
        "top_urls": [{"url": url, "count": count} for url, count in top_urls],
        "clicks": clicks[:100],  # Return first 100 clicks
    })


@app.get("/dashboard")
async def dashboard():
    """
    Dashboard UI endpoint.
    
    Returns dashboard data with analytics from ClickTracker.
    """
    # Secret will be auto-retrieved in get_analytics
    # Return dashboard data
    analytics = await get_analytics(hours=24)
    
    return JSONResponse(content={
        "title": "Click Tracker Dashboard",
        "analytics": analytics,
    })


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "app": "click_tracker_dashboard"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)

