"""
ClickTrackerDashboard Application

Admin dashboard for viewing click analytics from ClickTracker app.
Demonstrates cross-app data access with secure app-level authentication
using the unified MongoDBEngine pattern.

The engine automatically:
- Detects multi-site mode from manifest (read_scopes includes click_tracker)
- Auto-retrieves app tokens from environment or database
- Manages lifecycle with FastAPI integration
"""

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from bson import ObjectId

from mdb_engine import MongoDBEngine

# Initialize engine
engine = MongoDBEngine(
    mongo_uri=os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
    db_name=os.getenv("MONGODB_DB", "mdb_runtime"),
)

# Create FastAPI app with automatic lifecycle management
# Multi-site mode is auto-detected from manifest (read_scopes includes click_tracker)
app = engine.create_app(
    slug="click_tracker_dashboard",
    manifest=Path(__file__).parent / "manifest.json",
    title="Click Tracker Dashboard",
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
    """Root endpoint - HTML dashboard page."""
    return templates.TemplateResponse("index.html", {"request": request})


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


async def _get_db():
    """Get scoped database with auto-retrieved token."""
    token = engine.get_app_token("click_tracker_dashboard")
    if not token:
        # Try to auto-retrieve
        token = await engine.auto_retrieve_app_token("click_tracker_dashboard")
    
    if not token:
        raise HTTPException(
            status_code=500,
            detail="App token not available. Set CLICK_TRACKER_DASHBOARD_SECRET or ensure app is registered."
        )
    
    return engine.get_scoped_db("click_tracker_dashboard", app_token=token)


@app.get("/analytics")
async def get_analytics(hours: int = 24):
    """
    Get click analytics (reads from ClickTracker app).
    
    Demonstrates cross-app data access - Dashboard reads from ClickTracker's collections.
    Requires valid app_token for Dashboard.
    """
    db = await _get_db()
    
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
    url_counts: Dict[str, int] = {}
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
    # Get analytics data
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
