#!/usr/bin/env python3
"""
Simple App Example
==================

A clean, minimal example demonstrating the unified MongoDBEngine pattern with 
create_app() for automatic lifecycle management.

This example shows:
- How to use engine.create_app() for automatic initialization and cleanup
- How to use get_scoped_db() for database access scoped to your app
- How to build a simple task management API
- How to separate MDB-Engine specifics from reusable business logic

Key Concepts:
- engine.create_app() - Creates FastAPI app with automatic lifecycle management
- get_scoped_db() - Provides database access scoped to your app
- Manifest-driven configuration - Indexes and auth configured via manifest.json

Run with:
    docker-compose up --build
    # Or directly:
    uvicorn web:app --reload
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from bson import ObjectId
from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from mdb_engine import MongoDBEngine
from mdb_engine.dependencies import get_scoped_db

# ============================================================================
# CONFIGURATION
# ============================================================================
# Application constants and configuration
# These are reusable across different database backends

APP_SLUG = "simple_app"

# ============================================================================
# STEP 1: INITIALIZE MONGODB ENGINE
# ============================================================================
# The MongoDBEngine is the core of mdb-engine. It manages:
# - Database connections
# - App registration and configuration
# - Index creation (from manifest.json)
# - Lifecycle management
#
# This is MDB-Engine specific - you would replace this with your own
# database connection logic if not using mdb-engine.

from mdb_engine.env import get_mongo_uri, get_db_name

engine = MongoDBEngine(
    mongo_uri=get_mongo_uri(),
    db_name=get_db_name(fallback="mdb_runtime"),
)

# ============================================================================
# STEP 2: CREATE FASTAPI APP WITH MDB-ENGINE
# ============================================================================
# engine.create_app() does the heavy lifting:
# - Loads manifest.json configuration
# - Creates indexes (from managed_indexes in manifest)
# - Configures CORS, middleware, etc.
# - Returns a fully configured FastAPI app
#
# This is MDB-Engine specific - the create_app() method handles all the
# boilerplate of FastAPI + MongoDB setup.

app = engine.create_app(
    slug=APP_SLUG,
    manifest=Path(__file__).parent / "manifest.json",
    title="Simple App Example",
    description="A simple task management app demonstrating the unified MongoDBEngine pattern",
    version="1.0.0",
)

# Template engine for rendering HTML
# This is reusable - Jinja2 templates work with any FastAPI app
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# ============================================================================
# REUSABLE COMPONENTS
# ============================================================================
# These components are independent of MDB-Engine and can be reused
# in any FastAPI application. They define your business logic.

# ----------------------------------------------------------------------------
# Data Models (Pydantic)
# ----------------------------------------------------------------------------
# These models define the structure of your data and are reusable
# across different database backends.

class TaskCreate(BaseModel):
    """Request model for creating a new task."""
    title: str
    description: Optional[str] = None


class TaskUpdate(BaseModel):
    """Request model for updating an existing task."""
    title: Optional[str] = None
    description: Optional[str] = None
    completed: Optional[bool] = None


# ----------------------------------------------------------------------------
# Business Logic Functions
# ----------------------------------------------------------------------------
# These functions contain your business logic and are independent of
# MDB-Engine. They work with any MongoDB database connection.

def create_task_document(title: str, description: Optional[str] = None) -> dict:
    """
    Create a task document with standard fields.
    
    This is reusable business logic - it doesn't depend on MDB-Engine.
    You could use this same function with any MongoDB driver.
    
    Args:
        title: Task title
        description: Optional task description
        
    Returns:
        Dictionary representing a task document
    """
    return {
        "title": title,
        "description": description,
        "completed": False,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }


def serialize_task(task: dict) -> dict:
    """
    Convert a task document to a JSON-serializable format.
    
    This is reusable - handles ObjectId conversion for any MongoDB document.
    
    Args:
        task: Task document from MongoDB
        
    Returns:
        Task document with _id converted to string
    """
    if "_id" in task:
        task["_id"] = str(task["_id"])
    return task


def build_task_query(completed: Optional[bool] = None) -> dict:
    """
    Build a MongoDB query filter for tasks.
    
    This is reusable query building logic.
    
    Args:
        completed: Optional filter by completion status
        
    Returns:
        MongoDB query dictionary
    """
    query = {}
    if completed is not None:
        query["completed"] = completed
    return query

# ============================================================================
# ROUTES
# ============================================================================
# Routes combine MDB-Engine dependencies (get_scoped_db) with reusable
# business logic functions. The routes themselves are FastAPI-specific
# but the pattern is reusable.

# ----------------------------------------------------------------------------
# Web Routes (HTML)
# ----------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, db=Depends(get_scoped_db)):
    """
    Home page with task list.
    
    MDB-Engine specific: Uses get_scoped_db() dependency to get database
    access scoped to this app. The database connection is automatically
    filtered by app_id for data isolation.
    
    Reusable: The template rendering and task fetching logic could work
    with any database backend.
    """
    import json
    # Query tasks - database is automatically scoped to this app
    tasks = await db.tasks.find({}).sort("created_at", -1).to_list(length=100)
    
    # Load manifest for display
    manifest_path = Path(__file__).parent / "manifest.json"
    with open(manifest_path) as f:
        manifest = json.load(f)
    
    return templates.TemplateResponse("index.html", {
        "request": request,
        "tasks": tasks,
        "manifest": manifest,
    })


# ----------------------------------------------------------------------------
# API Routes (JSON)
# ----------------------------------------------------------------------------

@app.get("/api/tasks")
async def list_tasks(completed: Optional[bool] = None, db=Depends(get_scoped_db)):
    """
    List all tasks, optionally filtered by completion status.
    
    MDB-Engine specific: Uses get_scoped_db() for scoped database access.
    
    Reusable: The query building and serialization logic is independent
    of MDB-Engine.
    """
    # Build query using reusable function
    query = build_task_query(completed)
    
    # Fetch tasks - database is automatically scoped to this app
    tasks = await db.tasks.find(query).sort("created_at", -1).to_list(length=100)
    
    # Serialize tasks using reusable function
    for task in tasks:
        serialize_task(task)
    
    return {"tasks": tasks, "count": len(tasks)}


@app.post("/api/tasks")
async def create_task(task: TaskCreate, db=Depends(get_scoped_db)):
    """
    Create a new task.
    
    MDB-Engine specific: Uses get_scoped_db() for scoped database access.
    
    Reusable: The task document creation logic is independent of MDB-Engine.
    """
    # Create task document using reusable function
    task_doc = create_task_document(task.title, task.description)
    
    # Insert into database - automatically scoped to this app
    result = await db.tasks.insert_one(task_doc)
    
    return {
        "id": str(result.inserted_id),
        "message": "Task created successfully",
    }


@app.put("/api/tasks/{task_id}")
async def update_task(task_id: str, task: TaskUpdate, db=Depends(get_scoped_db)):
    """
    Update an existing task.
    
    MDB-Engine specific: Uses get_scoped_db() for scoped database access.
    
    Reusable: The update logic and ObjectId conversion are standard MongoDB
    operations that work with any MongoDB driver.
    """
    # Build update document
    updates = {"updated_at": datetime.utcnow()}
    if task.title is not None:
        updates["title"] = task.title
    if task.description is not None:
        updates["description"] = task.description
    if task.completed is not None:
        updates["completed"] = task.completed
    
    # Update task - database is automatically scoped to this app
    result = await db.tasks.update_one(
        {"_id": ObjectId(task_id)},
        {"$set": updates}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return {"message": "Task updated successfully"}


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str, db=Depends(get_scoped_db)):
    """
    Delete a task.
    
    MDB-Engine specific: Uses get_scoped_db() for scoped database access.
    
    Reusable: The delete operation is standard MongoDB and works with any driver.
    """
    # Delete task - database is automatically scoped to this app
    result = await db.tasks.delete_one({"_id": ObjectId(task_id)})
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return {"message": "Task deleted successfully"}


@app.post("/api/tasks/{task_id}/toggle")
async def toggle_task(task_id: str, db=Depends(get_scoped_db)):
    """
    Toggle task completion status.
    
    MDB-Engine specific: Uses get_scoped_db() for scoped database access.
    
    Reusable: The toggle logic is standard business logic independent of
    the database framework.
    """
    # Get current task - database is automatically scoped to this app
    task = await db.tasks.find_one({"_id": ObjectId(task_id)})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Toggle completed status
    new_completed = not task.get("completed", False)
    await db.tasks.update_one(
        {"_id": ObjectId(task_id)},
        {"$set": {
            "completed": new_completed,
            "updated_at": datetime.utcnow()
        }}
    )
    
    return {"message": "Task toggled", "completed": new_completed}


# ----------------------------------------------------------------------------
# Health & Status Routes
# ----------------------------------------------------------------------------

@app.get("/health")
async def health():
    """
    Health check endpoint for container orchestration.
    
    MDB-Engine specific: Uses engine.get_health_status() to check
    MongoDB connection health.
    
    This endpoint is useful for Docker healthchecks and monitoring.
    """
    health_status = await engine.get_health_status()
    return {
        "status": health_status.get("status", "unknown"),
        "app": APP_SLUG,
    }


@app.get("/api/status")
async def status():
    """
    Application status with engine details.
    
    MDB-Engine specific: Accesses engine properties to report status.
    
    Useful for debugging and monitoring engine state.
    """
    return {
        "app": APP_SLUG,
        "engine_initialized": engine.initialized,
    }


@app.get("/api/manifest")
async def get_manifest():
    """
    Get the application manifest configuration.
    
    MDB-Engine specific: Returns the manifest.json that was loaded during app creation.
    
    Useful for displaying configuration in the UI.
    """
    import json
    manifest_path = Path(__file__).parent / "manifest.json"
    with open(manifest_path) as f:
        manifest = json.load(f)
    return manifest


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
