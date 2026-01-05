"""
Simple App Example

Demonstrates the unified MongoDBEngine pattern with create_app() for automatic
lifecycle management. This is the recommended way to build FastAPI apps with MDB Engine.

Features demonstrated:
- engine.create_app() for automatic initialization and cleanup
- Scoped database access with get_scoped_db()
- Automatic manifest loading and registration
- Optional Ray support

Run with:
    docker-compose up --build
    # Or directly:
    uvicorn web:app --reload
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from mdb_engine import MongoDBEngine
from mdb_engine.dependencies import get_scoped_db

# =============================================================================
# Initialize Engine with create_app() - The Recommended Pattern
# =============================================================================

# Initialize engine (optional Ray support - only activates if Ray is installed)
engine = MongoDBEngine(
    mongo_uri=os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
    db_name=os.getenv("MONGODB_DB", "mdb_runtime"),
    enable_ray=os.getenv("ENABLE_RAY", "false").lower() == "true",
)

# Create FastAPI app with automatic lifecycle management
# This automatically:
# - Initializes the engine on startup
# - Loads and registers the manifest
# - Auto-detects multi-site mode from manifest
# - Auto-retrieves app tokens
# - Shuts down the engine on app shutdown
app = engine.create_app(
    slug="simple_app",
    manifest=Path(__file__).parent / "manifest.json",
    title="Simple App Example",
)

# Templates
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


# =============================================================================
# Pydantic Models
# =============================================================================

class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    completed: Optional[bool] = None


# =============================================================================
# Routes
# =============================================================================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, db=Depends(get_scoped_db)):
    """Home page with task list."""
    tasks = await db.tasks.find({}).sort("created_at", -1).to_list(length=100)
    
    return templates.TemplateResponse("index.html", {
        "request": request,
        "tasks": tasks,
        "ray_enabled": engine.has_ray,
    })


@app.get("/api/tasks")
async def list_tasks(completed: Optional[bool] = None, db=Depends(get_scoped_db)):
    """List all tasks."""
    query = {}
    if completed is not None:
        query["completed"] = completed
    
    tasks = await db.tasks.find(query).sort("created_at", -1).to_list(length=100)
    
    # Convert ObjectId to string for JSON serialization
    for task in tasks:
        task["_id"] = str(task["_id"])
    
    return {"tasks": tasks, "count": len(tasks)}


@app.post("/api/tasks")
async def create_task(task: TaskCreate, db=Depends(get_scoped_db)):
    """Create a new task."""
    task_doc = {
        "title": task.title,
        "description": task.description,
        "completed": False,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    
    result = await db.tasks.insert_one(task_doc)
    
    return {
        "id": str(result.inserted_id),
        "message": "Task created successfully",
    }


@app.put("/api/tasks/{task_id}")
async def update_task(task_id: str, task: TaskUpdate, db=Depends(get_scoped_db)):
    """Update a task."""
    from bson import ObjectId
    
    updates = {"updated_at": datetime.utcnow()}
    if task.title is not None:
        updates["title"] = task.title
    if task.description is not None:
        updates["description"] = task.description
    if task.completed is not None:
        updates["completed"] = task.completed
    
    result = await db.tasks.update_one(
        {"_id": ObjectId(task_id)},
        {"$set": updates}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return {"message": "Task updated successfully"}


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str, db=Depends(get_scoped_db)):
    """Delete a task."""
    from bson import ObjectId
    
    result = await db.tasks.delete_one({"_id": ObjectId(task_id)})
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return {"message": "Task deleted successfully"}


@app.post("/api/tasks/{task_id}/toggle")
async def toggle_task(task_id: str, db=Depends(get_scoped_db)):
    """Toggle task completion status."""
    from bson import ObjectId
    
    # Get current task
    task = await db.tasks.find_one({"_id": ObjectId(task_id)})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Toggle completed status
    await db.tasks.update_one(
        {"_id": ObjectId(task_id)},
        {"$set": {
            "completed": not task.get("completed", False),
            "updated_at": datetime.utcnow()
        }}
    )
    
    return {"message": "Task toggled", "completed": not task.get("completed", False)}


@app.get("/health")
async def health():
    """Health check endpoint."""
    health_status = await engine.get_health_status()
    return {
        "status": health_status.get("status", "unknown"),
        "app": "simple_app",
        "ray_enabled": engine.has_ray,
    }


@app.get("/api/status")
async def status():
    """Application status with engine details."""
    return {
        "app": "simple_app",
        "engine_initialized": engine._initialized,
        "ray_enabled": engine.enable_ray,
        "ray_available": engine.has_ray,
        "ray_namespace": engine.ray_namespace if engine.has_ray else None,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

