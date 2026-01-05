#!/usr/bin/env python3
"""
Parallax - GitHub Repository Intelligence Tool

The Parallax Dashboard visualizes GitHub repositories from two focused angles:
1. Relevance: Why this repository/implementation matters given your watchlist - personalized context and urgency
2. Technical: Concise engineering assessment - architecture, patterns, complexity, readiness, use cases
"""
import asyncio
import json
import logging
import os
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from openai import AzureOpenAI
from parallax import WATCHLIST, ParallaxEngine
from schema_generator import get_default_lens_configs

from mdb_engine import MongoDBEngine

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Parallax")

# App configuration
APP_SLUG = os.getenv("APP_SLUG", "parallax")

# Get MongoDB connection from environment
mongo_uri = os.getenv(
    "MONGO_URI",
    "mongodb://admin:password@mongodb:27017/?authSource=admin&directConnection=true",
)
db_name = os.getenv("MONGO_DB_NAME", "parallax_db")

# Initialize the MongoDB Engine
engine = MongoDBEngine(mongo_uri=mongo_uri, db_name=db_name)

# Create FastAPI app with automatic lifecycle management
app = engine.create_app(
    slug=APP_SLUG,
    manifest=Path(__file__).parent / "manifest.json",
    title="Parallax - GitHub Repository Intelligence",
    description="Focused intelligence tool analyzing GitHub repositories (with AGENTS.md/LLMs.md files) from Relevance and Technical perspectives",
    version="1.0.0",
)


# WebSocket connection manager for real-time updates
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket connected. Total connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(f"WebSocket disconnected. Total connections: {len(self.active_connections)}")

    async def send_personal_message(self, message: dict, websocket: WebSocket):
        # Type 4: Let WebSocket errors bubble up to framework handler
        await websocket.send_json(message)

    async def broadcast(self, message: dict):
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except (RuntimeError, ConnectionError, OSError):
                # Type 2: Recoverable - connection error, mark for cleanup
                disconnected.append(connection)

        # Remove disconnected connections
        for conn in disconnected:
            self.disconnect(conn)


manager = ConnectionManager()

# Templates directory
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# Parallax engine instance (initialized on startup)
parallax: Optional[ParallaxEngine] = None


def get_db(request: Request):
    """Get the scoped database from app state"""
    return request.app.state.engine.get_scoped_db(APP_SLUG)


# Global exception handler to ensure all errors return valid JSON
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle all unhandled exceptions and return valid JSON"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": str(exc),
            "detail": f"Internal server error: {str(exc)}",
        },
    )


@app.on_event("startup")
async def post_startup():
    """Initialize Parallax-specific components after engine is ready"""
    global parallax

    logger.info("Initializing Parallax-specific components...")

    # Get scoped database
    db = engine.get_scoped_db(APP_SLUG)

    # Set global engine for embedding dependency injection
    from mdb_engine.embeddings.dependencies import set_global_engine

    set_global_engine(engine, app_slug=APP_SLUG)

    # Initialize embedding service if configured in manifest.json
    from mdb_engine.embeddings import get_embedding_service

    app_config = engine.get_app(APP_SLUG)
    embedding_config = app_config.get("embedding_config", {}) if app_config else {}
    if embedding_config:
        embedding_service = get_embedding_service(config=embedding_config)
        logger.info("EmbeddingService initialized from manifest.json")
    else:
        logger.debug(
            "No embedding_config found in manifest.json - embedding service not initialized"
        )

    # Initialize default watchlist config if it doesn't exist
    existing_config = await db.watchlist_config.find_one({"config_type": "watchlist"})
    if not existing_config:
        await db.watchlist_config.insert_one(
            {
                "config_type": "watchlist",
                "keywords": WATCHLIST,
                "scan_limit": 50,  # Default: check top 50 stories
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
        )
        logger.info(f"Initialized default watchlist: {WATCHLIST}, scan_limit: 50")

    # Initialize default lens configurations if they don't exist
    try:
        default_configs = get_default_lens_configs()
        for lens_name, config in default_configs.items():
            existing_lens = await db.lens_configs.find_one({"lens_name": lens_name})
            if not existing_lens:
                config["created_at"] = datetime.utcnow()
                config["updated_at"] = datetime.utcnow()
                await db.lens_configs.insert_one(config)
                logger.info(f"Initialized default lens config: {lens_name}")
    except (AttributeError, RuntimeError, ConnectionError, ValueError, TypeError):
        # Type 2: Recoverable - lens config initialization failed, continue without defaults
        logger.warning("Could not initialize lens configs", exc_info=True)

    # Initialize Parallax Engine
    try:
        # Create Azure OpenAI client
        api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        key = os.getenv("AZURE_OPENAI_API_KEY")
        deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")

        if not endpoint or not key:
            logger.error(
                "Azure OpenAI not configured! Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY environment variables."
            )
            raise RuntimeError("Azure OpenAI not configured - required for Parallax")

        openai_client = AzureOpenAI(api_version=api_version, azure_endpoint=endpoint, api_key=key)

        # Get GitHub token
        github_token = os.getenv("GITHUB_TOKEN")
        if not github_token:
            logger.error("GITHUB_TOKEN not set! Set it as an environment variable.")
            raise RuntimeError("GITHUB_TOKEN is required for GitHub GraphQL API")

        # Load watchlist from DB or use default
        try:
            config = await db.watchlist_config.find_one({"config_type": "watchlist"})
            watchlist = config.get("keywords", WATCHLIST) if config else WATCHLIST
        except (AttributeError, KeyError, TypeError):
            # Type 2: Recoverable - config read failed, use default watchlist
            watchlist = WATCHLIST

        parallax = ParallaxEngine(
            openai_client,
            db,
            watchlist=watchlist,
            deployment_name=deployment_name,
            github_token=github_token,
        )
        logger.info("Parallax Engine initialized successfully")

    except (AttributeError, RuntimeError, ConnectionError, ValueError, TypeError, KeyError) as e:
        # Type 2: Recoverable - startup initialization failed, re-raise with context
        logger.error("Failed to initialize Parallax Engine", exc_info=True)
        raise RuntimeError(f"Parallax Engine initialization failed: {str(e)}") from e

    logger.info("Parallax ready!")


@app.get("/health", response_class=JSONResponse)
async def health_check():
    """Health check endpoint for container healthchecks"""
    health = await engine.get_health_status()
    status_code = 200 if health.get("status") == "healthy" else 503
    return JSONResponse(content=health, status_code=status_code)


@app.websocket("/ws/scan")
async def websocket_scan(websocket: WebSocket):
    """WebSocket endpoint for real-time scan progress and reports"""
    await manager.connect(websocket)
    try:
        while True:
            # Wait for scan request from client
            data = await websocket.receive_json()

            if data.get("action") == "start_scan":
                global parallax
                if not parallax:
                    await manager.send_personal_message(
                        {"type": "error", "message": "Parallax Engine not initialized"}, websocket
                    )
                    continue

                # Progress callback to send updates via WebSocket
                async def progress_callback(update: dict):
                    # Include repo_id for fetching the report
                    if update.get("type") == "report_complete":
                        update["repo_id"] = update.get("repo_id") or update.get("repo_name", "")
                    await manager.send_personal_message(update, websocket)

                try:
                    logger.info("🔄 Starting WebSocket scan...")
                    await manager.send_personal_message(
                        {"type": "scan_started", "message": "Starting analysis..."}, websocket
                    )

                    # Run analysis with progress callback
                    reports = await asyncio.wait_for(
                        parallax.analyze_repositories(progress_callback=progress_callback),
                        timeout=300.0,
                    )

                    # Update last scan timestamp
                    db = engine.get_scoped_db(APP_SLUG)
                    try:
                        await db.watchlist_config.update_one(
                            {"config_type": "watchlist"},
                            {"$set": {"last_scan_timestamp": datetime.utcnow()}},
                            upsert=True,
                        )
                    except (AttributeError, RuntimeError, ConnectionError, ValueError):
                        # Type 2: Recoverable - timestamp update failed, continue
                        logger.warning("Could not update last scan timestamp", exc_info=True)

                    # Send final summary
                    await manager.send_personal_message(
                        {
                            "type": "scan_complete",
                            "total_reports": len(reports),
                            "message": f"Analysis complete: {len(reports)} repositories analyzed",
                        },
                        websocket,
                    )

                except asyncio.TimeoutError:
                    await manager.send_personal_message(
                        {"type": "error", "message": "Analysis timed out after 5 minutes"},
                        websocket,
                    )
                except (RuntimeError, ConnectionError, OSError, AttributeError) as e:
                    # Type 2: Recoverable - WebSocket send failed, try to send error message
                    logger.error("Error in WebSocket scan", exc_info=True)
                    try:
                        await manager.send_personal_message(
                            {"type": "error", "message": f"Scan failed: {str(e)}"}, websocket
                        )
                    except (RuntimeError, ConnectionError, OSError):
                        pass  # Can't send error message, connection likely closed

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except (RuntimeError, ConnectionError, OSError, AttributeError):
        # Type 2: Recoverable - WebSocket error, disconnect gracefully
        logger.error("WebSocket error", exc_info=True)
        manager.disconnect(websocket)


@app.post("/api/refresh", response_class=JSONResponse)
async def trigger_refresh(request: Request):
    """Trigger the multi-agent analysis manually"""
    global parallax

    if not parallax:
        return JSONResponse(
            status_code=503, content={"success": False, "error": "Parallax Engine not initialized"}
        )

    try:
        logger.info("🔄 Triggering Parallax analysis...")
        # Add timeout to prevent hanging (5 minutes max)
        reports = await asyncio.wait_for(parallax.analyze_feed(), timeout=300.0)

        # Update last scan timestamp
        db = get_db(request)
        try:
            await db.watchlist_config.update_one(
                {"config_type": "watchlist"},
                {"$set": {"last_scan_timestamp": datetime.utcnow()}},
                upsert=True,
            )
        except (AttributeError, RuntimeError, ConnectionError, ValueError):
            # Type 2: Recoverable - timestamp update failed, continue
            logger.warning("Could not update last scan timestamp", exc_info=True)

        if len(reports) == 0:
            logger.info("No new repositories found matching watchlist criteria")
            return {
                "success": True,
                "status": "no_new",
                "new_reports": 0,
                "message": "Found nothing new. All matching repositories have already been analyzed.",
            }
        else:
            logger.info(f"Generated {len(reports)} new Parallax reports")
            return {
                "success": True,
                "status": "success",
                "new_reports": len(reports),
                "message": f"Analyzed {len(reports)} new repositories",
            }
    except asyncio.TimeoutError:
        logger.error("Parallax analysis timed out after 5 minutes")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "Analysis timed out",
                "detail": "The analysis took too long. Try reducing the scan limit or check your LLM API configuration.",
            },
        )
    # Type 4: Let other errors bubble up to framework handler


@app.get("/api/reports/{repo_id:path}", response_class=JSONResponse)
async def get_report(request: Request, repo_id: str):
    """Get a single Parallax report by repo_id (supports slashes in repo_id like 'owner/repo')"""
    db = get_db(request)

    # FastAPI automatically URL-decodes the path parameter, so repo_id should be correct
    # But let's also try URL-decoding just in case
    repo_id_decoded = urllib.parse.unquote(repo_id)

    # Try both the original and decoded version
    report = await db.parallax_reports.find_one({"repo_id": repo_id_decoded})
    if not report:
        report = await db.parallax_reports.find_one({"repo_id": repo_id})

    if not report:
        # Log for debugging
        logger.warning(f"Report not found for repo_id: {repo_id} (decoded: {repo_id_decoded})")
        # Try to find any reports to see what format they're stored in
        sample = await db.parallax_reports.find_one({})
        if sample:
            logger.debug(f"Sample repo_id format in DB: {sample.get('repo_id')}")
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": f"Report not found for repo_id: {repo_id}"},
        )

    # Convert ObjectId to string
    report_dict = dict(report)
    report_dict["_id"] = str(report_dict.get("_id", ""))

    # Mark as fresh if needed
    scan_config = await db.watchlist_config.find_one({"config_type": "watchlist"})
    if scan_config and scan_config.get("last_scan_timestamp"):
        last_scan = scan_config["last_scan_timestamp"]
        if isinstance(last_scan, str):
            last_scan = datetime.fromisoformat(last_scan.replace("Z", "+00:00"))
        if last_scan:
            last_scan_iso = last_scan.isoformat()
            report_dict["is_fresh"] = (
                report_dict.get("timestamp") and report_dict["timestamp"] > last_scan_iso
            )

    return {"success": True, "report": report_dict}


@app.get("/api/reports", response_class=JSONResponse)
async def get_reports(
    request: Request,
    limit: int = 50,
    keyword: str = None,
    sort_by: str = "date_desc",
    max_limit: int = None,
):
    """
    Get Parallax reports

    Args:
        limit: Maximum number of reports to return (default: 50)
        keyword: Optional keyword to filter by (must be in matched_keywords)
        sort_by: Sort order - "date_desc", "date_asc", "relevance"
        max_limit: Maximum limit allowed (if set, limits the limit parameter)
    """
    db = get_db(request)

    # Apply max_limit if specified
    if max_limit is not None and limit > max_limit:
        limit = max_limit

    # Ensure limit is reasonable (between 1 and 1000)
    limit = max(1, min(1000, limit))

    query = {}

    # If filtering by keyword
    if keyword:
        query["matched_keywords"] = {"$in": [keyword]}

    # Get last scan timestamp to determine freshness
    scan_config = await db.watchlist_config.find_one({"config_type": "watchlist"})
    last_scan = None
    if scan_config and scan_config.get("last_scan_timestamp"):
        last_scan = scan_config["last_scan_timestamp"]
        # Convert to datetime if it's a string
        if isinstance(last_scan, str):
            last_scan = datetime.fromisoformat(last_scan.replace("Z", "+00:00"))

    # For relevance sorting, we need to fetch more and sort in memory
    # For date sorting, we can use MongoDB sort
    if sort_by == "relevance":
        # Fetch more reports for relevance sorting (need to sort by matched_keywords count)
        reports = await db.parallax_reports.find(query).to_list(length=limit * 3)

        # Mark reports as fresh
        if last_scan:
            last_scan_iso = last_scan.isoformat()
            for r in reports:
                r["is_fresh"] = r.get("timestamp") and r["timestamp"] > last_scan_iso

        # Sort by relevance: more matched keywords = higher relevance, then by timestamp
        reports.sort(
            key=lambda x: (
                -len(x.get("matched_keywords", [])),  # Negative for descending
                (
                    x.get("relevance", {}).get("relevance_score", 0)
                    if isinstance(x.get("relevance"), dict)
                    else 0
                ),
                x.get("timestamp", "") if x.get("timestamp") else "",
            ),
            reverse=True,
        )

        # Limit after sorting
        reports = reports[:limit]
    else:
        # Determine sort order for date-based sorting
        sort_order = [("timestamp", -1)]  # Default: newest first
        if sort_by == "date_asc":
            sort_order = [("timestamp", 1)]  # Oldest first

        reports = (
            await db.parallax_reports.find(query)
            .sort(sort_order)
            .limit(limit)
            .to_list(length=limit)
        )

        # Mark reports as fresh
        if last_scan:
            last_scan_iso = last_scan.isoformat()
            for r in reports:
                r["is_fresh"] = r.get("timestamp") and r["timestamp"] > last_scan_iso

    # Convert to dict format for JSON serialization
    reports_data = []
    for r in reports:
        # Convert ObjectId to string
        r_dict = dict(r)
        r_dict["_id"] = str(r_dict.get("_id", ""))
        reports_data.append(r_dict)

    return {
        "success": True,
        "reports": reports_data,
        "count": len(reports_data),
        "sort_by": sort_by,
    }


@app.get("/api/watchlist", response_class=JSONResponse)
async def get_watchlist(request: Request):
    """Get current watchlist configuration"""
    global parallax

    if not parallax:
        return JSONResponse(
            status_code=503, content={"success": False, "error": "Parallax Engine not initialized"}
        )

    # Type 4: Let errors bubble up to framework handler
    keywords = await parallax.get_watchlist()
    scan_limit = await parallax.get_scan_limit()
    min_stars = parallax.min_stars
    language_filter = parallax.language_filter or ""

    # Get max_limit from config
    db = get_db(request)
    config = await db.watchlist_config.find_one({"config_type": "watchlist"})
    max_limit = config.get("max_limit") if config else None

    return {
        "success": True,
        "watchlist": keywords,
        "scan_limit": scan_limit,
        "min_stars": min_stars,
        "language_filter": language_filter,
        "max_limit": max_limit,
    }


@app.post("/api/watchlist", response_class=JSONResponse)
async def update_watchlist(request: Request):
    """Update watchlist configuration"""
    global parallax

    if not parallax:
        return JSONResponse(
            status_code=503, content={"success": False, "error": "Parallax Engine not initialized"}
        )

    # Type 4: Let errors bubble up to framework handler
    data = await request.json()
    keywords = data.get("watchlist", [])
    scan_limit = data.get("scan_limit")
    min_stars = data.get("min_stars")
    language_filter = data.get("language_filter")
    max_limit = data.get("max_limit")

    if not isinstance(keywords, list):
        return JSONResponse(
            status_code=400, content={"success": False, "error": "watchlist must be a list"}
        )

    if scan_limit is not None:
        scan_limit = int(scan_limit)
        if scan_limit < 1 or scan_limit > 500:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "scan_limit must be between 1 and 500"},
            )

    if min_stars is not None:
        min_stars = int(min_stars)
        if min_stars < 0 or min_stars > 100000:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "min_stars must be between 0 and 100000"},
            )

    # Validate language filter if provided
    if language_filter is not None and language_filter:
        language_filter = str(language_filter).strip().lower()
        # GitHub supports common language names
        if not language_filter:
            language_filter = None

    # Validate max_limit if provided
    if max_limit is not None:
        max_limit = int(max_limit)
        if max_limit < 1 or max_limit > 1000:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "max_limit must be between 1 and 1000"},
            )

    success = await parallax.update_watchlist(
        keywords, scan_limit=scan_limit, min_stars=min_stars, language_filter=language_filter
    )

    # Update max_limit in watchlist_config if provided
    if max_limit is not None:
        db = get_db(request)
        await db.watchlist_config.update_one(
            {"config_type": "watchlist"}, {"$set": {"max_limit": max_limit}}, upsert=True
        )
    if success:
        return {
            "success": True,
            "watchlist": keywords,
            "scan_limit": parallax.scan_limit,
            "message": "Watchlist updated successfully",
        }
    else:
        return JSONResponse(
            status_code=500, content={"success": False, "error": "Failed to update watchlist"}
        )


@app.get("/api/lenses", response_class=JSONResponse)
async def get_lenses(request: Request):
    """Get all lens configurations"""
    db = get_db(request)

    # Type 4: Let errors bubble up to framework handler
    lenses = await db.lens_configs.find({}).to_list(length=10)
    # Convert ObjectId to string for JSON serialization
    lenses_data = []
    for lens in lenses:
        lens_dict = dict(lens)
        lens_dict["_id"] = str(lens_dict.get("_id", ""))
        lenses_data.append(lens_dict)

    return {"success": True, "lenses": lenses_data}


@app.get("/api/lenses/{lens_name}", response_class=JSONResponse)
async def get_lens(request: Request, lens_name: str):
    """Get a specific lens configuration"""
    db = get_db(request)

    # Type 4: Let errors bubble up to framework handler
    lens = await db.lens_configs.find_one({"lens_name": lens_name})
    if not lens:
        return JSONResponse(
            status_code=404, content={"success": False, "error": f"Lens '{lens_name}' not found"}
        )

    lens_dict = dict(lens)
    lens_dict["_id"] = str(lens_dict.get("_id", ""))

    return {"success": True, "lens": lens_dict}


@app.post("/api/lenses/{lens_name}", response_class=JSONResponse)
async def update_lens(request: Request, lens_name: str):
    """Update a lens configuration"""
    global parallax
    db = get_db(request)

    if not parallax:
        return JSONResponse(
            status_code=503, content={"success": False, "error": "Parallax Engine not initialized"}
        )

    # Type 4: Let errors bubble up to framework handler
    data = await request.json()

    # Validate required fields
    if "schema_fields" not in data:
        return JSONResponse(
            status_code=400, content={"success": False, "error": "schema_fields is required"}
        )

    # Update the lens config
    update_data = {
        "lens_name": lens_name,
        "prompt_template": data.get("prompt_template", ""),
        "schema_fields": data.get("schema_fields", []),
        "updated_at": datetime.utcnow(),
    }

    result = await db.lens_configs.update_one(
        {"lens_name": lens_name}, {"$set": update_data}, upsert=True
    )

    # Clear the cache so new config is loaded
    if hasattr(parallax, "lens_configs"):
        parallax.lens_configs.pop(lens_name, None)
    if hasattr(parallax, "lens_models"):
        parallax.lens_models.pop(lens_name, None)

    logger.info(f"Updated lens configuration: {lens_name}")

    return {
        "success": True,
        "lens": update_data,
        "message": f"Lens '{lens_name}' updated successfully",
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    keyword: str = None,
    sort_by: str = "date_desc",
    limit: int = 50,
    max_limit: int = None,
):
    """
    The Parallax Dashboard.
    Renders the 3-Column Split View.

    Args:
        keyword: Optional keyword to filter by
        sort_by: Sort order - "date_desc", "date_asc", "relevance"
        limit: Maximum number of reports to display (default: 50)
        max_limit: Maximum limit allowed (if set, limits the limit parameter)
    """
    db = get_db(request)

    # Apply max_limit if specified
    if max_limit is not None and limit > max_limit:
        limit = max_limit

    # Ensure limit is reasonable
    limit = max(1, min(1000, limit))

    try:
        query = {}

        # If filtering by keyword
        if keyword:
            query["matched_keywords"] = {"$in": [keyword]}

        # Get last scan timestamp to determine freshness
        scan_config = await db.watchlist_config.find_one({"config_type": "watchlist"})
        last_scan = None
        if scan_config and scan_config.get("last_scan_timestamp"):
            last_scan = scan_config["last_scan_timestamp"]
            # Convert to datetime if it's a string
            if isinstance(last_scan, str):
                last_scan = datetime.fromisoformat(last_scan.replace("Z", "+00:00"))

        # For relevance sorting, we need to fetch more and sort in memory
        # For date sorting, we can use MongoDB sort
        if sort_by == "relevance":
            # Fetch more reports for relevance sorting
            reports = await db.parallax_reports.find(query).to_list(length=limit * 3)

            # Mark reports as fresh
            if last_scan:
                last_scan_iso = last_scan.isoformat()
                for r in reports:
                    r["is_fresh"] = r.get("timestamp") and r["timestamp"] > last_scan_iso

            # Sort by relevance: more matched keywords = higher relevance, then by relevance_score, then timestamp
            reports.sort(
                key=lambda x: (
                    -len(x.get("matched_keywords", [])),  # Negative for descending
                    (
                        x.get("relevance", {}).get("relevance_score", 0)
                        if isinstance(x.get("relevance"), dict)
                        else 0
                    ),
                    x.get("timestamp", "") if x.get("timestamp") else "",
                ),
                reverse=True,
            )

            # Limit after sorting
            reports = reports[:limit]
        else:
            # Determine sort order for date-based sorting
            sort_order = [("timestamp", -1)]  # Default: newest first
            if sort_by == "date_asc":
                sort_order = [("timestamp", 1)]  # Oldest first

            reports = (
                await db.parallax_reports.find(query)
                .sort(sort_order)
                .limit(limit)
                .to_list(length=limit)
            )

            # Mark reports as fresh
            if last_scan:
                last_scan_iso = last_scan.isoformat()
                for r in reports:
                    r["is_fresh"] = r.get("timestamp") and r["timestamp"] > last_scan_iso

        # Get current watchlist and max_limit
        watchlist = WATCHLIST
        max_limit_val = None
        global parallax
        if parallax:
            watchlist = await parallax.get_watchlist()
            # Get max_limit from config
            config = await db.watchlist_config.find_one({"config_type": "watchlist"})
            max_limit_val = config.get("max_limit") if config else None

        # Apply max_limit to limit if specified
        if max_limit_val is not None and limit > max_limit_val:
            limit = max_limit_val

    except (AttributeError, RuntimeError, ConnectionError, ValueError, TypeError, KeyError):
        # Type 2: Recoverable - dashboard data fetch failed, use defaults
        logger.error("Error fetching reports for dashboard", exc_info=True)
        reports = []
        watchlist = WATCHLIST
        max_limit_val = None

    # Render the dashboard template
    return templates.TemplateResponse(
        "parallax_dashboard.html",
        {
            "request": request,
            "reports": reports,
            "watchlist": watchlist,  # Pass as list, not joined string
            "selected_keyword": keyword,
            "sort_by": sort_by,
            "limit": limit,
            "max_limit": max_limit_val,
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
