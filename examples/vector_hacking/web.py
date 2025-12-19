#!/usr/bin/env python3
"""
FastAPI Web Application for Vector Hacking Example

This demonstrates MDB_RUNTIME with a vector hacking demo including:
- Vector inversion attack visualization
- Real-time status updates
- Modern, responsive UI
"""
import os
import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from pydantic import BaseModel

# Setup logger
logger = logging.getLogger(__name__)

from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

from mdb_runtime import RuntimeEngine


class StartAttackRequest(BaseModel):
    target: Optional[str] = None
    generate_random: bool = False  # If True, generate random target using LLM

# Initialize FastAPI app
app = FastAPI(
    title="Vector Hacking - MDB_RUNTIME Demo",
    description="A demonstration of vector inversion/hacking using LLMs",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify actual origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Templates directory - works in both Docker (/app) and local development
templates_dir = Path("/app/templates")
if not templates_dir.exists():
    templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

# Global engine instance (will be initialized in startup)
engine: Optional[RuntimeEngine] = None
db = None
vector_hacking_service = None

# Import vector hacking service
import sys
from pathlib import Path
import importlib.util

# Add current directory to path for imports
current_dir = Path(__file__).parent
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

# Import using importlib to handle both local and Docker paths
vector_hacking_file = current_dir / "vector_hacking.py"
if vector_hacking_file.exists():
    spec = importlib.util.spec_from_file_location("vector_hacking_module", vector_hacking_file)
    vector_hacking_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vector_hacking_module)
    VectorHackingService = vector_hacking_module.VectorHackingService
else:
    # Fallback: try regular import
    try:
        from vector_hacking import VectorHackingService
    except ImportError:
        raise ImportError(f"Could not find vector_hacking.py in {current_dir}")


@app.on_event("startup")
async def startup_event():
    """Initialize the runtime engine on startup"""
    global engine, db, vector_hacking_service
    
    print("🚀 Starting Vector Hacking Web Application...")
    
    # Get MongoDB connection from environment
    mongo_uri = os.getenv(
        "MONGO_URI", 
        "mongodb://admin:password@mongodb:27017/?authSource=admin"
    )
    db_name = os.getenv("MONGO_DB_NAME", "vector_hacking_db")
    
    # Initialize the runtime engine
    engine = RuntimeEngine(
        mongo_uri=mongo_uri,
        db_name=db_name
    )
    
    # Connect to MongoDB
    await engine.initialize()
    print("✅ Engine initialized successfully")
    
    # Load and register the app manifest
    manifest_path = Path("/app/manifest.json")
    if not manifest_path.exists():
        manifest_path = Path(__file__).parent / "manifest.json"
    
    if manifest_path.exists():
        manifest = await engine.load_manifest(manifest_path)
        success = await engine.register_app(manifest, create_indexes=True)
        if success:
            print(f"✅ App '{manifest['slug']}' registered successfully")
        else:
            print("⚠️  Failed to register app")
    
    # Get scoped database and store globally
    db = engine.get_scoped_db("vector_hacking")
    
    # Initialize vector hacking service with LLM service abstraction
    # LLM service is automatically initialized by RuntimeEngine from manifest.json llm_config
    try:
        # Get LLM service from RuntimeEngine (initialized from manifest.json)
        # This service provides unified interface for all LLM operations
        llm_service = engine.get_llm_service("vector_hacking")
        
        # Get LLM config from manifest.json for reference
        llm_config = {}
        app_config = engine.get_app("vector_hacking")
        if app_config and "llm_config" in app_config:
            llm_config = app_config["llm_config"]
            print(f"✅ LLM config loaded from manifest.json: {llm_config.get('default_chat_model')} / {llm_config.get('default_embedding_model')}")
        
        if not llm_service:
            logger.warning("LLM service not available - ensure llm_config.enabled=true in manifest.json")
        
        # Initialize vector hacking service with LLM service abstraction
        # All LLM calls will go through the unified abstraction
        vector_hacking_service = VectorHackingService(
            mongo_uri=mongo_uri,
            db_name=db_name,
            write_scope="vector_hacking",
            read_scopes=["vector_hacking"],
            llm_service=llm_service,  # LLM service abstraction (from manifest.json)
            llm_config=llm_config      # Config reference (from manifest.json)
        )
        print("✅ Vector hacking service initialized with LLM service abstraction")
    except Exception as e:
        logger.error(f"Failed to initialize vector hacking service: {e}")
        import traceback
        traceback.print_exc()
        vector_hacking_service = None
    
    print("✅ Web application ready!")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    global engine, vector_hacking_service
    
    if vector_hacking_service:
        try:
            await vector_hacking_service.stop_attack()
        except Exception:
            pass
    
    if engine:
        await engine.shutdown()
        print("🧹 Cleaned up and shut down")


def get_db():
    """Get the scoped database"""
    global engine, db
    if not engine:
        raise HTTPException(status_code=503, detail="Engine not initialized")
    if db is None:
        return engine.get_scoped_db("vector_hacking")
    return db


# ============================================================================
# Routes
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Home page - shows the vector hacking interface"""
    if vector_hacking_service:
        try:
            # Render the index page using the service's template rendering
            html_content = await vector_hacking_service.render_index()
            return HTMLResponse(content=html_content)
        except Exception as e:
            logger.error(f"Error rendering index: {e}")
            # Fallback to direct template rendering
            try:
                return templates.TemplateResponse("index.html", {"request": request})
            except Exception:
                return HTMLResponse(content="<h1>Vector Hacking Demo</h1><p>Template rendering failed. Check logs.</p>")
    else:
        # Fallback if service not available
        try:
            return templates.TemplateResponse("index.html", {"request": request})
        except Exception:
            return HTMLResponse(content="<h1>Vector Hacking Demo</h1><p>Vector hacking service not initialized. Please check configuration.</p>")


@app.post("/start", response_class=JSONResponse)
async def start_attack(request: Optional[StartAttackRequest] = None):
    """
    Start the vector hacking attack.
    
    Can use:
    - Custom target (if request.target is provided)
    - AI-generated random target (if request.generate_random is True)
    - Default target (if neither is provided)
    """
    if not vector_hacking_service:
        raise HTTPException(
            status_code=503, 
            detail="Vector hacking service not initialized. Check LLM service configuration."
        )
    
    try:
        # Get parameters from request
        target = request.target if request and request.target else None
        generate_random = request.generate_random if request else False
        
        result = await vector_hacking_service.start_attack(
            custom_target=target,
            generate_random=generate_random
        )
        return result
    except Exception as e:
        logger.error(f"Error starting attack: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/stop", response_class=JSONResponse)
async def stop_attack():
    """Stop the vector hacking attack"""
    if not vector_hacking_service:
        raise HTTPException(
            status_code=503, 
            detail="Vector hacking service not initialized"
        )
    
    try:
        result = await vector_hacking_service.stop_attack()
        return result
    except Exception as e:
        logger.error(f"Error stopping attack: {e}")
        raise HTTPException(status_code=500, detail=str(e))




@app.get("/api/status", response_class=JSONResponse)
async def get_attack_status():
    """Get the current status of the vector hacking attack"""
    if not vector_hacking_service:
        return {
            "status": "not_available",
            "running": False,
            "error": "Vector hacking service not initialized. Check LLM service configuration."
        }
    
    try:
        status_data = await vector_hacking_service.get_status()
        return status_data
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        return {
            "status": "error",
            "running": False,
            "error": str(e)
        }


@app.post("/api/generate-target", response_class=JSONResponse)
async def generate_random_target():
    """Generate a random target phrase using LLM"""
    if not vector_hacking_service:
        raise HTTPException(
            status_code=503,
            detail="Vector hacking service not initialized"
        )
    
    try:
        target = await vector_hacking_service.generate_random_target()
        return {"target": target, "status": "generated"}
    except Exception as e:
        logger.error(f"Error generating random target: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/reset", response_class=JSONResponse)
async def reset_attack(request: Optional[StartAttackRequest] = None):
    """
    Reset attack state for a new attack - game-like experience.
    
    Can optionally set a new target or generate a random one.
    """
    if not vector_hacking_service:
        raise HTTPException(
            status_code=503,
            detail="Vector hacking service not initialized"
        )
    
    try:
        # Get parameters from request
        new_target = request.target if request and request.target else None
        generate_random = request.generate_random if request else False
        
        result = await vector_hacking_service.reset_attack(
            new_target=new_target,
            generate_random=generate_random
        )
        return result
    except Exception as e:
        logger.error(f"Error resetting attack: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/restart", response_class=JSONResponse)
async def restart_attack():
    """
    Restart attack - resets state and signals frontend to reload page.
    
    This endpoint resets the state. The frontend will reload the page
    for a fresh game-like experience.
    """
    if not vector_hacking_service:
        raise HTTPException(
            status_code=503,
            detail="Vector hacking service not initialized"
        )
    
    try:
        # Simple reset - stop current attack and clear all state
        await vector_hacking_service.stop_attack()
        await vector_hacking_service.reset_attack()
        
        return {
            "status": "ready",
            "reload": True,
            "message": "State reset. Page will reload.",
            "reload": True  # Signal frontend to reload page
        }
    except Exception as e:
        logger.error(f"Error restarting attack: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health", response_class=JSONResponse)
async def health_api():
    """Health status API endpoint"""
    if not engine:
        raise HTTPException(status_code=503, detail="Engine not initialized")
    
    health = await engine.get_health_status()
    return health


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, ws="auto")

