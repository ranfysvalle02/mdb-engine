#!/usr/bin/env python3
"""
Parallax - Call Transcript Analysis Tool

The Parallax Dashboard analyzes call transcripts from three business perspectives:
1. SALES: Sales opportunities, objections, closing signals, deal value
2. MARKETING: Messaging, positioning, campaign insights, customer sentiment
3. PRODUCT: Feature requests, pain points, product-market fit, use cases
"""
from __future__ import annotations

import asyncio
import logging
import os
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
import json
from openai import AzureOpenAI
from parallax import WATCHLIST, ParallaxEngine
from schema_generator import get_default_lens_configs

from mdb_engine import MongoDBEngine
from mdb_engine.dependencies import get_memory_service, get_scoped_db

# Load environment variables
load_dotenv()

logger = logging.getLogger("Parallax")

# App configuration
APP_SLUG = os.getenv("APP_SLUG", "parallax_memory")

# Get MongoDB connection from environment
from mdb_engine.env import get_mongo_uri, get_db_name

mongo_uri = get_mongo_uri(
    fallback="mongodb://admin:password@mongodb:27017/?authSource=admin&directConnection=true",
)
db_name = get_db_name(fallback="parallax_memory_db")

# Initialize the MongoDB Engine
engine = MongoDBEngine(mongo_uri=mongo_uri, db_name=db_name)

async def on_startup(app, eng, manifest):
    """Initialize Parallax-specific components after engine is ready.
    
    Called by engine.create_app() after full initialization.
    """
    logger.info("Initializing Parallax-specific components...")
    
    # WebSocket removed - not essential for this example
    # Users can manually refresh memories using the refresh button

    # Get scoped database
    db = eng.get_scoped_db(APP_SLUG)

    # Initialize default watchlist config if it doesn't exist
    existing_config = await db.watchlist_config.find_one({"config_type": "watchlist"})
    if not existing_config:
        await db.watchlist_config.insert_one(
            {
                "config_type": "watchlist",
                "keywords": WATCHLIST,
                "scan_limit": 3,  # Default: 3 calls for simple, powerful demo
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
        )
        logger.info(f"Initialized default watchlist: {WATCHLIST}, scan_limit: 3")

    # Initialize/update default lens configurations
    try:
        default_configs = get_default_lens_configs()
        for lens_name, config in default_configs.items():
            existing_lens = await db.lens_configs.find_one({"lens_name": lens_name})
            if not existing_lens:
                # Create new config
                config["created_at"] = datetime.utcnow()
                config["updated_at"] = datetime.utcnow()
                await db.lens_configs.insert_one(config)
                logger.info(f"Initialized default lens config: {lens_name}")
            else:
                # Update existing config with latest defaults (especially for MARKETING improvements)
                config["updated_at"] = datetime.utcnow()
                await db.lens_configs.update_one(
                    {"lens_name": lens_name},
                    {"$set": config},
                )
                logger.info(f"Updated lens config: {lens_name}")
    except (AttributeError, RuntimeError, ConnectionError, ValueError, TypeError):
        logger.warning("Could not initialize/update lens configs", exc_info=True)

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

        # Embedding service removed - using memory-only approach, no RAG/vector search
        
        # Get memory service for cross-call intelligence (optional)
        memory_service = None
        try:
            memory_service = eng.get_memory_service(APP_SLUG)
            if memory_service:
                logger.info("Memory service initialized for cross-call intelligence")
            else:
                logger.warning("Memory service not enabled in manifest.json")
        except Exception as e:
            logger.warning(f"Memory service not available: {e}. Memory tracking will be disabled.")

        # Load watchlist from DB or use default
        try:
            config = await db.watchlist_config.find_one({"config_type": "watchlist"})
            watchlist = config.get("keywords", WATCHLIST) if config else WATCHLIST
        except (AttributeError, KeyError, TypeError):
            watchlist = WATCHLIST

        parallax_engine = ParallaxEngine(
            openai_client,
            db,
            embedding_service=None,  # No embeddings - memory-only approach
            memory_service=memory_service,
            watchlist=watchlist,
            deployment_name=deployment_name,
            app_slug=APP_SLUG,
        )
        app.state.parallax = parallax_engine
        logger.info("Parallax Engine initialized successfully")
        
        # Auto-index and process transcripts on startup
        try:
            transcript_count = await db.call_transcripts.count_documents({
                "chunk_index": {"$exists": False}
            })
            analyzed_count = await db.parallax_reports.count_documents({})
            
            if transcript_count == 0:
                logger.info("No transcripts found in database, auto-indexing from CALLS.json...")
                indexed = await parallax_engine.index_transcripts()
                logger.info(f"Auto-indexed {indexed} call transcripts on startup")
                transcript_count = indexed
            
            # Auto-process exactly 3 calls total (process missing ones if less than 3 analyzed)
            target_calls = 3
            if analyzed_count < target_calls and transcript_count > 0:
                calls_needed = target_calls - analyzed_count
                logger.info(f"Auto-processing {calls_needed} calls to reach {target_calls} total analyzed calls...")
                
                # Get analyzed call IDs
                analyzed_call_ids = set()
                async for report in db.parallax_reports.find({}, {"call_id": 1}):
                    if report.get("call_id"):
                        analyzed_call_ids.add(report.get("call_id"))
                
                logger.info(f"Found {len(analyzed_call_ids)} already analyzed calls: {sorted(analyzed_call_ids)}")
                
                # Get ALL transcripts sorted by timestamp
                all_transcripts = []
                async for transcript in db.call_transcripts.find({
                    "chunk_index": {"$exists": False}
                }).sort("timestamp", 1):
                    all_transcripts.append(transcript)
                
                logger.info(f"Total transcripts in database: {len(all_transcripts)}")
                
                # Get unanalyzed transcripts (up to calls_needed)
                unanalyzed_transcripts = []
                for transcript in all_transcripts:
                    call_id = transcript.get("call_id")
                    if call_id and call_id not in analyzed_call_ids:
                        unanalyzed_transcripts.append(transcript)
                        company = transcript.get("participants", {}).get("company", "Unknown")
                        logger.info(f"Found unanalyzed call: {call_id} - {company}")
                        if len(unanalyzed_transcripts) >= calls_needed:
                            break
                
                logger.info(f"Total unanalyzed transcripts to process: {len(unanalyzed_transcripts)}")
                
                # Process calls in parallel
                if unanalyzed_transcripts:
                    async def process_call(transcript_doc):
                        call_id = transcript_doc.get("call_id")
                        company = transcript_doc.get("participants", {}).get("company", "Unknown")
                        try:
                            logger.info(f"🔄 Starting analysis of call {call_id} ({company})...")
                            result = await parallax_engine.analyze_single_transcript(call_id)
                            if result:
                                logger.info(f"✅ Successfully processed call {call_id} ({company})")
                            else:
                                logger.warning(f"⚠️ Analysis returned None for call {call_id} ({company})")
                        except Exception as e:
                            logger.error(f"❌ Failed to auto-process call {call_id} ({company}): {e}", exc_info=True)
                    
                    # Process all unanalyzed calls in parallel
                    call_ids_to_process = [t.get("call_id") for t in unanalyzed_transcripts]
                    logger.info(f"🚀 Starting parallel processing of {len(unanalyzed_transcripts)} calls: {call_ids_to_process}")
                    
                    # Use create_task to run in background without blocking startup
                    tasks = [process_call(t) for t in unanalyzed_transcripts]
                    asyncio.create_task(asyncio.gather(*tasks, return_exceptions=True))
                    logger.info(f"✅ Started parallel processing of {len(tasks)} calls - memories will populate shortly")
                else:
                    logger.warning(f"No unanalyzed transcripts found. Analyzed: {len(analyzed_call_ids)}, Total transcripts: {transcript_count}")
            else:
                logger.info(f"Found {analyzed_count} analyzed calls (target: {target_calls}) and {transcript_count} transcripts in database")
        except Exception as e:
            logger.warning(f"Failed to auto-process transcripts on startup: {e}", exc_info=True)

    except (AttributeError, RuntimeError, ConnectionError, ValueError, TypeError, KeyError) as e:
        logger.error("Failed to initialize Parallax Engine", exc_info=True)
        raise RuntimeError(f"Parallax Engine initialization failed: {str(e)}") from e

    logger.info("Parallax ready!")


# Create FastAPI app with automatic lifecycle management
# on_startup callback runs within the engine's lifespan context
app = engine.create_app(
    slug=APP_SLUG,
    manifest=Path(__file__).parent / "manifest.json",
    title="Parallax - Call Transcript Analysis",
    description="Multi-lens analysis of call transcripts through SALES, MARKETING, and PRODUCT perspectives",
    version="1.0.0",
    on_startup=on_startup,
)


# WebSocket removed - not essential for this example
# Users can manually refresh memories using the refresh button

# Templates directory
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_parallax(request: Request):
    """Get ParallaxEngine from app state."""
    return getattr(request.app.state, "parallax", None)


async def get_last_scan_timestamp(db) -> datetime | None:
    """Get and parse last scan timestamp from database."""
    scan_config = await db.watchlist_config.find_one({"config_type": "watchlist"})
    if not scan_config or not scan_config.get("last_scan_timestamp"):
        return None
    
    last_scan = scan_config["last_scan_timestamp"]
    if isinstance(last_scan, str):
        parsed = datetime.fromisoformat(last_scan.replace("Z", "+00:00"))
        # Ensure timezone-aware
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    # If it's already a datetime, ensure it's timezone-aware
    if isinstance(last_scan, datetime):
        if last_scan.tzinfo is None:
            return last_scan.replace(tzinfo=timezone.utc)
        return last_scan
    return None


def mark_report_fresh(report: dict, last_scan: datetime | None) -> None:
    """Mark a report as fresh if it's newer than last scan."""
    if last_scan and report.get("timestamp"):
        report_timestamp = report["timestamp"]
        if isinstance(report_timestamp, str):
            parsed = datetime.fromisoformat(report_timestamp.replace("Z", "+00:00"))
            # Ensure timezone-aware
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            report_timestamp = parsed
        
        if isinstance(report_timestamp, datetime):
            # Ensure both are timezone-aware for comparison
            if report_timestamp.tzinfo is None:
                report_timestamp = report_timestamp.replace(tzinfo=timezone.utc)
            if last_scan.tzinfo is None:
                last_scan = last_scan.replace(tzinfo=timezone.utc)
            report["is_fresh"] = report_timestamp > last_scan
        else:
            # Fallback for non-datetime timestamps
            report["is_fresh"] = False


def convert_objectid_to_string(doc: dict) -> dict:
    """Convert MongoDB ObjectId to string for JSON serialization."""
    doc_dict = dict(doc)
    if "_id" in doc_dict:
        doc_dict["_id"] = str(doc_dict["_id"])
    return doc_dict


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


@app.get("/health", response_class=JSONResponse)
async def health_check():
    """Health check endpoint for container healthchecks"""
    health = await engine.get_health_status()
    status_code = 200 if health.get("status") == "healthy" else 503
    return JSONResponse(content=health, status_code=status_code)


# WebSocket endpoint removed - not essential for this example
# Users can trigger scans via POST /api/refresh and manually refresh memories


@app.post("/api/index", response_class=JSONResponse)
async def index_transcripts_endpoint(request: Request, db=Depends(get_scoped_db)):
    """Index call transcripts from CALLS.json (without analyzing)"""
    parallax = get_parallax(request)
    
    if not parallax:
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": "Parallax Engine not initialized"},
        )
    
    try:
        logger.info("Indexing call transcripts...")
        indexed_count = await parallax.index_transcripts()
        
        return {
            "success": True,
            "indexed_count": indexed_count,
            "message": f"Indexed {indexed_count} call transcripts",
        }
    except Exception as e:
        logger.error(f"Failed to index transcripts: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)},
        )


@app.post("/api/refresh", response_class=JSONResponse)
async def trigger_refresh(request: Request, db=Depends(get_scoped_db)):
    """Index call transcripts and trigger multi-lens analysis"""
    parallax = get_parallax(request)

    if not parallax:
        return JSONResponse(
            status_code=503, content={"success": False, "error": "Parallax Engine not initialized"}
        )

    try:
        logger.info("🔄 Indexing call transcripts...")
        indexed_count = await parallax.index_transcripts()
        logger.info(f"Indexed {indexed_count} call transcripts")
        
        logger.info("🔄 Triggering Parallax analysis...")
        # Add timeout to prevent hanging (5 minutes max)
        reports = await asyncio.wait_for(parallax.analyze_transcripts(), timeout=300.0)

        # Update last scan timestamp
        try:
            await db.watchlist_config.update_one(
                {"config_type": "watchlist"},
                {"$set": {"last_scan_timestamp": datetime.utcnow()}},
                upsert=True,
            )
        except (AttributeError, RuntimeError, ConnectionError, ValueError):
            logger.warning("Could not update last scan timestamp", exc_info=True)

        if len(reports) == 0:
            logger.info("No new calls found to analyze")
            return {
                "success": True,
                "status": "no_new",
                "new_reports": 0,
                "indexed_count": indexed_count,
                "message": "All calls have already been analyzed.",
            }
        else:
            logger.info(f"Generated {len(reports)} new Parallax reports")
            return {
                "success": True,
                "status": "success",
                "new_reports": len(reports),
                "indexed_count": indexed_count,
                "message": f"Analyzed {len(reports)} new call transcripts",
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


@app.get("/api/reports/{call_id:path}/memories", response_class=JSONResponse)
async def get_call_memories(
    call_id: str,
    db=Depends(get_scoped_db),
    memory_service=Depends(get_memory_service),
):
    """Get memories for a specific call (PER-CALL memories) - MUST BE BEFORE generic /api/reports/{repo_id} route"""
    if not memory_service:
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": "Memory service not enabled"},
        )
    
    # PER-CALL MEMORIES: Use call_id directly as user_id
    user_id_str = f"call_{call_id}"
    
    logger.info(
        f"🔍 RETRIEVING PER-CALL MEMORIES - call_id='{call_id}', user_id='{user_id_str}'",
        extra={"call_id": call_id, "user_id": user_id_str},
    )
    
    import asyncio
    from memory.memory_retriever import MemoryRetriever
    
    retriever = MemoryRetriever(memory_service)
    # Get memories for this specific call (per-call memories)
    memories = await retriever.get_customer_memories(user_id_str, limit=20)
    
    # Get report for company info
    report = await db.parallax_reports.find_one({"call_id": call_id})
    if not report:
        report = await db.parallax_reports.find_one({"repo_id": call_id})
    company = report.get("customer_company", "unknown") if report else "unknown"
    
    logger.info(
        f"🔍 RETRIEVED PER-CALL MEMORIES - call_id='{call_id}', count={len(memories)}",
        extra={"call_id": call_id, "count": len(memories)},
    )
    
    return {
        "success": True,
        "call_id": call_id,
        "company": company,
        "memories": memories,
        "count": len(memories),
    }


@app.get("/api/reports", response_class=JSONResponse)
async def get_reports(
    limit: int = 50,
    keyword: str = None,
    sort_by: str = "date_desc",
    max_limit: int = None,
    call_type: str = None,
    db=Depends(get_scoped_db),
):
    """
    Get Parallax reports for call transcripts

    Args:
        limit: Maximum number of reports to return (default: 50)
        keyword: Optional keyword to filter by (must be in matched_keywords)
        sort_by: Sort order - "date_desc", "date_asc", "relevance"
        max_limit: Maximum limit allowed (if set, limits the limit parameter)
        call_type: Optional call type filter (e.g., "sales_discovery", "customer_support")
    """

    # Apply max_limit if specified
    if max_limit is not None and limit > max_limit:
        limit = max_limit

    # Ensure limit is reasonable (between 1 and 1000)
    limit = max(1, min(1000, limit))

    query = {}

    # If filtering by keyword
    if keyword:
        query["matched_keywords"] = {"$in": [keyword]}
    
    # If filtering by call type
    if call_type:
        # Try to match call_type from reports (stored in repo_name or metadata)
        query["$or"] = [
            {"repo_name": {"$regex": call_type, "$options": "i"}},
            {"call_id": {"$regex": call_type, "$options": "i"}},
        ]

    last_scan = await get_last_scan_timestamp(db)

    # For relevance sorting, we need to fetch more and sort in memory
    # For date sorting, we can use MongoDB sort
    if sort_by == "relevance":
        # Fetch more reports for relevance sorting (need to sort by matched_keywords count)
        reports = await db.parallax_reports.find(query).to_list(length=limit * 3)

        # Mark reports as fresh
        if last_scan:
            for r in reports:
                mark_report_fresh(r, last_scan)

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
            for r in reports:
                mark_report_fresh(r, last_scan)

    # Convert to dict format for JSON serialization
    reports_data = [convert_objectid_to_string(r) for r in reports]

    return {
        "success": True,
        "reports": reports_data,
        "count": len(reports_data),
        "sort_by": sort_by,
    }


@app.get("/api/watchlist", response_class=JSONResponse)
async def get_watchlist(request: Request):
    """Get current watchlist configuration"""
    parallax = get_parallax(request)

    if not parallax:
        return JSONResponse(
            status_code=503, content={"success": False, "error": "Parallax Engine not initialized"}
        )

    keywords = await parallax.get_watchlist()
    scan_limit = await parallax.get_scan_limit()

    return {
        "success": True,
        "watchlist": keywords,
        "scan_limit": scan_limit,
    }


@app.post("/api/watchlist", response_class=JSONResponse)
async def update_watchlist(request: Request):
    """Update watchlist configuration"""
    parallax = get_parallax(request)

    if not parallax:
        return JSONResponse(
            status_code=503, content={"success": False, "error": "Parallax Engine not initialized"}
        )

    data = await request.json()
    keywords = data.get("watchlist", [])
    scan_limit = data.get("scan_limit")

    if not isinstance(keywords, list):
        return JSONResponse(
            status_code=400, content={"success": False, "error": "watchlist must be a list"}
        )

    if scan_limit is not None:
        scan_limit = int(scan_limit)
        if scan_limit < 1 or scan_limit > 100:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "scan_limit must be between 1 and 100"},
            )

    success = await parallax.update_watchlist(keywords, scan_limit=scan_limit)

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
async def get_lenses(db=Depends(get_scoped_db)):
    """Get all lens configurations"""
    lenses = await db.lens_configs.find({}).to_list(length=10)
    lenses_data = [convert_objectid_to_string(lens) for lens in lenses]
    return {"success": True, "lenses": lenses_data}


@app.get("/api/lenses/{lens_name}", response_class=JSONResponse)
async def get_lens(lens_name: str, db=Depends(get_scoped_db)):
    """Get a specific lens configuration"""
    lens = await db.lens_configs.find_one({"lens_name": lens_name})
    if not lens:
        return JSONResponse(
            status_code=404, content={"success": False, "error": f"Lens '{lens_name}' not found"}
        )

    return {"success": True, "lens": convert_objectid_to_string(lens)}


@app.get("/api/customers/{company}/memories", response_class=JSONResponse)
async def get_customer_memories(
    company: str,
    query: Optional[str] = None,
    limit: int = 10,
    memory_service=Depends(get_memory_service),
    db=Depends(get_scoped_db),
):
    """Get memories for a customer company (aggregates all call memories for that company)"""
    if not memory_service:
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": "Memory service not enabled"},
        )
    
    # URL decode the company name
    import urllib.parse
    company_decoded = urllib.parse.unquote(company)
    logger.info(f"Retrieving aggregated memories for company: '{company_decoded}'")
    
    # Get all call_ids for this company
    reports = await db.parallax_reports.find(
        {"customer_company": company_decoded}
    ).to_list(length=100)
    
    call_ids = [r.get("call_id") for r in reports if r.get("call_id")]
    logger.info(f"Found {len(call_ids)} calls for company '{company_decoded}'")
    
    # Aggregate memories from all calls for this company
    from memory.memory_retriever import MemoryRetriever
    retriever = MemoryRetriever(memory_service)
    
    all_memories = []
    for call_id in call_ids:
        user_id_str = f"call_{call_id}"
        call_memories = await retriever.get_customer_memories(user_id_str, query, limit=50)
        # Add call_id to each memory for context
        for mem in call_memories:
            if isinstance(mem, dict):
                mem["call_id"] = call_id
        all_memories.extend(call_memories)
    
    # Limit and deduplicate if needed
    if limit:
        all_memories = all_memories[:limit]
    
    logger.info(f"Found {len(all_memories)} total memories for company '{company_decoded}' across {len(call_ids)} calls")
    
    return {
        "success": True,
        "company": company_decoded,
        "memories": all_memories,
        "count": len(all_memories),
        "call_count": len(call_ids),
    }


@app.get("/api/master-memory", response_class=JSONResponse)
async def get_master_memory(
    request: Request,
    query: Optional[str] = None,
    limit: int = 20,
    memory_service=Depends(get_memory_service),
):
    """Get global master-memory entries (cross-call intelligence across all customers)"""
    if not memory_service:
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": "Memory service not enabled"},
        )
    
    parallax = get_parallax(request)
    if not parallax or not parallax.master_memory_manager:
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": "Master memory manager not available"},
        )
    
    try:
        memories = await parallax.master_memory_manager.get_master_context(
            query=query,
            limit=limit,
        )
        
        return {
            "success": True,
            "memories": memories,
            "count": len(memories),
            "query": query,
        }
    except Exception as e:
        logger.error(f"Failed to get master-memory: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)},
        )


@app.get("/api/master-memory/patterns", response_class=JSONResponse)
async def get_master_memory_patterns(
    request: Request,
    limit: int = 10,
    memory_service=Depends(get_memory_service),
):
    """Get cross-customer patterns from master-memory"""
    if not memory_service:
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": "Memory service not enabled"},
        )
    
    parallax = get_parallax(request)
    if not parallax or not parallax.master_memory_manager:
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": "Master memory manager not available"},
        )
    
    try:
        patterns = await parallax.master_memory_manager.get_cross_customer_patterns(
            limit=limit,
        )
        
        return {
            "success": True,
            "patterns": patterns,
            "count": len(patterns),
        }
    except Exception as e:
        logger.error(f"Failed to get master-memory patterns: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)},
        )


@app.post("/api/master-memory/chat", response_class=JSONResponse)
async def chat_with_master_memory(
    request: Request,
    memory_service=Depends(get_memory_service),
):
    """Chat with master memory - uses master-memory context for responses"""
    # Parse request body
    try:
        body = await request.json()
        message = body.get("message", "")
        if not message:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "Message is required"},
            )
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": f"Invalid request body: {str(e)}"},
        )
    
    if not memory_service:
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": "Memory service not enabled"},
        )
    
    parallax = get_parallax(request)
    if not parallax or not parallax.master_memory_manager:
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": "Master memory manager not available"},
        )
    
    try:
        # Get OpenAI client from parallax engine
        if not parallax.openai_client:
            return JSONResponse(
                status_code=503,
                content={"success": False, "error": "OpenAI client not available"},
            )
        
        # Retrieve relevant memories from master-memory
        context_memories = []
        memory_search_details = []
        
        relevant_memories = await parallax.master_memory_manager.get_master_context(
            query=message,
            limit=5,
        )
        
        if relevant_memories:
            for m in relevant_memories:
                if isinstance(m, dict):
                    memory_text = m.get("memory") or m.get("data", {}).get("memory", "")
                    if memory_text:
                        context_memories.append(memory_text)
                        memory_search_details.append({
                            "memory": memory_text,
                            "id": m.get("id"),
                            "score": m.get("score"),
                            "metadata": m.get("metadata", {}),
                        })
                elif isinstance(m, str):
                    context_memories.append(m)
                    memory_search_details.append({"memory": m, "score": None})
        
        # Format messages for LLM
        messages = []
        
        # Add relevant memories as system context
        if context_memories:
            memory_context = "Relevant context from call analysis across all customers:\n" + "\n".join(
                f"- {m}" for m in context_memories[:5]
            )
            messages.append({
                "role": "system",
                "content": f"You are an AI assistant with access to insights from customer call analyses. "
                          f"{memory_context}\n\n"
                          f"Use this context to provide insights about customer patterns, trends, and cross-call intelligence. "
                          f"Be specific and reference the insights when relevant.",
            })
        else:
            messages.append({
                "role": "system",
                "content": "You are an AI assistant with access to customer call analysis insights. "
                          "Answer questions about customer patterns, trends, and cross-call intelligence.",
            })
        
        # Add user message
        messages.append({"role": "user", "content": message})
        
        # Get AI response
        import asyncio
        completion = await asyncio.to_thread(
            parallax.openai_client.chat.completions.create,
            model=parallax.deployment_name,
            messages=messages,
            temperature=0.7,
            max_tokens=1000,
        )
        ai_response = completion.choices[0].message.content
        
        # Store conversation turn in master-memory
        if parallax.master_memory_manager:
            try:
                await parallax.master_memory_manager.add_call_insights(
                    call_id=f"chat_{datetime.utcnow().isoformat()}",
                    customer_company="system",
                    lens_insights={},
                    transcript_summary=f"User: {message}\nAssistant: {ai_response}",
                    metadata={
                        "type": "chat",
                        "user_message": message,
                    },
                )
            except Exception as e:
                logger.warning(f"Failed to store chat in master-memory: {e}")
        
        return {
            "success": True,
            "response": ai_response,
            "memory_context": {
                "used_memories": len(context_memories),
                "memories": context_memories[:3],
                "search_details": memory_search_details[:3],
            } if context_memories else None,
        }
    except Exception as e:
        logger.error(f"Failed to chat with master-memory: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)},
        )


@app.get("/api/reports/{repo_id:path}", response_class=JSONResponse)
async def get_report(repo_id: str, db=Depends(get_scoped_db)):
    """Get a single Parallax report by call_id or repo_id - MUST BE AFTER /memories route"""
    repo_id_decoded = urllib.parse.unquote(repo_id)

    # Try call_id first, then repo_id for backward compatibility
    report = await db.parallax_reports.find_one({"call_id": repo_id_decoded})
    if not report:
        report = await db.parallax_reports.find_one({"repo_id": repo_id_decoded})
    if not report:
        report = await db.parallax_reports.find_one({"repo_id": repo_id})

    if not report:
        logger.warning(f"Report not found for call_id/repo_id: {repo_id}")
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": f"Report not found for call_id: {repo_id}"},
        )

    report_dict = convert_objectid_to_string(report)
    last_scan = await get_last_scan_timestamp(db)
    if last_scan:
        mark_report_fresh(report_dict, last_scan)

    return {"success": True, "report": report_dict}


@app.get("/api/transcripts", response_class=JSONResponse)
async def get_transcripts(
    limit: int = 3,  # Default: 3 calls for simple demo
    db=Depends(get_scoped_db),
):
    """
    Get call transcripts with their chunks and analysis status.
    
    Returns transcripts (non-chunk documents) with:
    - Metadata (call_id, call_type, participants, timestamp)
    - Whether chunks exist
    - Whether report exists (analyzed status)
    """
    try:
        # Fetch main transcript documents (exclude chunks)
        transcripts = await db.call_transcripts.find({
            "chunk_index": {"$exists": False}
        }).sort("timestamp", -1).limit(limit).to_list(length=limit)
        
        # Enrich each transcript with chunk count and analysis status
        enriched_transcripts = []
        for transcript in transcripts:
            call_id = transcript.get("call_id")
            if not call_id:
                continue
            
            # Count chunks for this call
            chunk_count = await db.call_transcripts.count_documents({
                "call_id": call_id,
                "chunk_index": {"$exists": True}
            })
            
            # Check if analyzed (has report)
            has_report = await db.parallax_reports.find_one({"call_id": call_id}) is not None
            
            transcript_dict = convert_objectid_to_string(transcript)
            transcript_dict["chunk_count"] = chunk_count
            transcript_dict["has_chunks"] = chunk_count > 0
            transcript_dict["is_analyzed"] = has_report
            
            enriched_transcripts.append(transcript_dict)
        
        return {
            "success": True,
            "transcripts": enriched_transcripts,
            "count": len(enriched_transcripts),
        }
    except Exception as e:
        logger.error(f"Failed to get transcripts: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)},
        )


@app.get("/api/transcripts/{call_id}/chunks", response_class=JSONResponse)
async def get_transcript_chunks(
    call_id: str,
    db=Depends(get_scoped_db),
):
    """Get all chunks for a specific call transcript"""
    try:
        chunks = await db.call_transcripts.find({
            "call_id": call_id,
            "chunk_index": {"$exists": True}
        }).sort("chunk_index", 1).to_list(length=100)
        
        chunks_data = [convert_objectid_to_string(chunk) for chunk in chunks]
        
        return {
            "success": True,
            "call_id": call_id,
            "chunks": chunks_data,
            "count": len(chunks_data),
        }
    except Exception as e:
        logger.error(f"Failed to get chunks for {call_id}: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)},
        )


@app.post("/api/calls/{call_id}/analyze", response_class=JSONResponse)
async def analyze_single_call(
    call_id: str,
    request: Request,
    db=Depends(get_scoped_db),
):
    """Analyze a single call transcript"""
    parallax = get_parallax(request)
    
    if not parallax:
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": "Parallax Engine not initialized"},
        )
    
    try:
        logger.info(f"Analyzing single call: {call_id}")
        
        # Collect progress updates
        progress_updates = []
        
        # Progress callback to collect updates
        async def progress_callback(update: dict):
            progress_updates.append(update)
            logger.debug(f"Progress update for {call_id}: {update}")
        
        report = await parallax.analyze_single_transcript(
            call_id,
            progress_callback=progress_callback,
        )
        
        if not report:
            return JSONResponse(
                status_code=404,
                content={"success": False, "error": f"Failed to analyze call {call_id} or call not found"},
            )
        
        # Convert report to dict
        report_dict = (
            report.model_dump() if hasattr(report, "model_dump") else report.dict()
        )
        report_dict = convert_objectid_to_string(report_dict)
        
        return {
            "success": True,
            "call_id": call_id,
            "report": report_dict,
            "progress": progress_updates,
            "message": f"Successfully analyzed call {call_id}",
        }
    except Exception as e:
        logger.error(f"Failed to analyze call {call_id}: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)},
        )


@app.get("/api/stats", response_class=JSONResponse)
async def get_stats(db=Depends(get_scoped_db)):
    """Get statistics about analyzed calls"""
    try:
        # Count total analyzed calls (reports)
        analyzed_count = await db.parallax_reports.count_documents({})
        
        # Count total transcripts
        total_transcripts = await db.call_transcripts.count_documents({
            "chunk_index": {"$exists": False}
        })
        
        # Count transcripts with chunks - use find and extract unique call_ids
        chunks_cursor = db.call_transcripts.find({
            "chunk_index": {"$exists": True}
        })
        chunks_docs = await chunks_cursor.to_list(length=1000)
        unique_call_ids = set(doc.get("call_id") for doc in chunks_docs if doc.get("call_id"))
        chunks_count = len(unique_call_ids)
        
        # Count customer profiles (Longitudinal AI)
        profile_count = await db.customer_profiles.count_documents({})
        
        return {
            "success": True,
            "stats": {
                "analyzed_calls": analyzed_count,
                "total_transcripts": total_transcripts,
                "transcripts_with_chunks": chunks_count,
                "can_show_memories": True,  # Always show memories if memory service is enabled
                "customer_profiles": profile_count,
                "longitudinal_ai_enabled": analyzed_count >= 2,
            },
        }
    except Exception as e:
        logger.error(f"Failed to get stats: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)},
        )


@app.get("/api/customers/{company}/intelligence", response_class=JSONResponse)
async def get_customer_intelligence(
    company: str,
    request: Request,
    db=Depends(get_scoped_db),
):
    """Get complete longitudinal intelligence for a customer"""
    parallax = get_parallax(request)
    
    if not parallax or not parallax.intelligence_orchestrator:
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": "Intelligence Orchestrator not available"},
        )
    
    try:
        import urllib.parse
        company_decoded = urllib.parse.unquote(company)
        
        intelligence = await parallax.intelligence_orchestrator.get_customer_intelligence(
            company_decoded
        )
        
        return {
            "success": True,
            "company": company_decoded,
            "intelligence": intelligence,
        }
    except Exception as e:
        logger.error(f"Failed to get customer intelligence: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)},
        )


@app.get("/api/customers/{company}/profile", response_class=JSONResponse)
async def get_customer_profile(
    company: str,
    db=Depends(get_scoped_db),
):
    """Get customer profile with relationship health score"""
    try:
        import urllib.parse
        company_decoded = urllib.parse.unquote(company)
        
        profile = await db.customer_profiles.find_one(
            {"customer_company": company_decoded}
        )
        
        if not profile:
            return JSONResponse(
                status_code=404,
                content={"success": False, "error": f"Profile not found for {company_decoded}"},
            )
        
        profile.pop("_id", None)
        
        return {
            "success": True,
            "company": company_decoded,
            "profile": profile,
        }
    except Exception as e:
        logger.error(f"Failed to get customer profile: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)},
        )


@app.get("/api/customers/{company}/patterns", response_class=JSONResponse)
async def get_customer_patterns(
    company: str,
    limit: int = 5,
    db=Depends(get_scoped_db),
):
    """Get detected patterns for a customer (Sentinel analysis)"""
    try:
        import urllib.parse
        company_decoded = urllib.parse.unquote(company)
        
        patterns = await db.sentinel_patterns.find(
            {"customer_company": company_decoded}
        ).sort("timestamp", -1).limit(limit).to_list(length=limit)
        
        patterns_data = [convert_objectid_to_string(p) for p in patterns]
        
        return {
            "success": True,
            "company": company_decoded,
            "patterns": patterns_data,
            "count": len(patterns_data),
        }
    except Exception as e:
        logger.error(f"Failed to get customer patterns: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)},
        )


@app.get("/api/customers/{company}/contradictions", response_class=JSONResponse)
async def get_customer_contradictions(
    company: str,
    db=Depends(get_scoped_db),
):
    """Get detected contradictions for a customer"""
    try:
        import urllib.parse
        company_decoded = urllib.parse.unquote(company)
        
        contradiction_doc = await db.contradictions.find_one(
            {"customer_company": company_decoded},
            sort=[("timestamp", -1)]
        )
        
        contradictions = contradiction_doc.get("contradictions", []) if contradiction_doc else []
        
        return {
            "success": True,
            "company": company_decoded,
            "contradictions": contradictions,
            "count": len(contradictions),
        }
    except Exception as e:
        logger.error(f"Failed to get customer contradictions: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)},
        )


@app.get("/api/customers/{company}/continuity", response_class=JSONResponse)
async def get_customer_continuity(
    company: str,
    db=Depends(get_scoped_db),
):
    """Get continuity summary for a customer (State of the Union)"""
    try:
        import urllib.parse
        company_decoded = urllib.parse.unquote(company)
        
        summary = await db.continuity_summaries.find_one(
            {"customer_company": company_decoded}
        )
        
        if not summary:
            return JSONResponse(
                status_code=404,
                content={"success": False, "error": f"Continuity summary not found for {company_decoded}"},
            )
        
        summary.pop("_id", None)
        
        return {
            "success": True,
            "company": company_decoded,
            "continuity": summary,
        }
    except Exception as e:
        logger.error(f"Failed to get customer continuity: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)},
        )


@app.get("/api/customers", response_class=JSONResponse)
async def get_all_customers(
    limit: int = 50,
    db=Depends(get_scoped_db),
):
    """Get all customer profiles"""
    try:
        profiles = await db.customer_profiles.find({}).limit(limit).to_list(length=limit)
        
        profiles_data = [convert_objectid_to_string(p) for p in profiles]
        
        return {
            "success": True,
            "customers": profiles_data,
            "count": len(profiles_data),
        }
    except Exception as e:
        logger.error(f"Failed to get customers: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)},
        )


@app.post("/api/lenses/{lens_name}", response_class=JSONResponse)
async def update_lens(request: Request, lens_name: str, db=Depends(get_scoped_db)):
    """Update a lens configuration"""
    parallax = get_parallax(request)

    if not parallax:
        return JSONResponse(
            status_code=503, content={"success": False, "error": "Parallax Engine not initialized"}
        )

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
    limit: int = 3,  # Default: 3 calls for simple, powerful demo
    view: str = "transcripts",  # New: "transcripts" or "reports"
    db=Depends(get_scoped_db),
):
    """
    The Parallax Dashboard.
    Shows transcripts by default, with option to view analyzed reports.

    Args:
        keyword: Optional keyword to filter by (for reports view)
        sort_by: Sort order - "date_desc", "date_asc", "relevance" (for reports view)
        limit: Maximum number of items to display (default: 3 for demo)
        view: View mode - "transcripts" (default) or "reports"
    """
    # Ensure limit is reasonable
    limit = max(1, min(1000, limit))

    try:
        # Get current watchlist
        watchlist = WATCHLIST
        parallax = get_parallax(request)
        if parallax:
            watchlist = await parallax.get_watchlist()
        
        # Get stats for conditional UI elements
        analyzed_count = await db.parallax_reports.count_documents({})
        # Always show memories if memory service is enabled (even for single calls)
        can_show_memories = True  # Simplified - always show if memory service available
        
        transcripts = []
        reports = []
        
        if view == "transcripts":
            # Load transcripts (non-chunk documents)
            transcript_docs = await db.call_transcripts.find({
                "chunk_index": {"$exists": False}
            }).sort("timestamp", -1).limit(limit).to_list(length=limit)
            
            # Enrich with chunk count and analysis status
            for transcript in transcript_docs:
                call_id = transcript.get("call_id")
                if not call_id:
                    continue
                
                chunk_count = await db.call_transcripts.count_documents({
                    "call_id": call_id,
                    "chunk_index": {"$exists": True}
                })
                
                has_report = await db.parallax_reports.find_one({"call_id": call_id}) is not None
                
                transcript_dict = convert_objectid_to_string(transcript)
                transcript_dict["chunk_count"] = chunk_count
                transcript_dict["has_chunks"] = chunk_count > 0
                transcript_dict["is_analyzed"] = has_report
                
                transcripts.append(transcript_dict)
        else:
            # Load reports (existing logic)
            query = {}
            
            # If filtering by keyword
            if keyword:
                query["matched_keywords"] = {"$in": [keyword]}
            
            last_scan = await get_last_scan_timestamp(db)
            
            # For relevance sorting, we need to fetch more and sort in memory
            # For date sorting, we can use MongoDB sort
            if sort_by == "relevance":
                # Fetch more reports for relevance sorting
                reports = await db.parallax_reports.find(query).to_list(length=limit * 3)
                
                # Mark reports as fresh
                if last_scan:
                    for r in reports:
                        mark_report_fresh(r, last_scan)
                
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
                    for r in reports:
                        mark_report_fresh(r, last_scan)
            
            # Convert reports to dict format
            reports = [convert_objectid_to_string(r) for r in reports]

    except (AttributeError, RuntimeError, ConnectionError, ValueError, TypeError, KeyError) as e:
        logger.error("Error fetching data for dashboard", exc_info=True)
        transcripts = []
        reports = []
        watchlist = WATCHLIST
        can_show_memories = True  # Always show if memory service available

    return templates.TemplateResponse(
        "parallax_dashboard.html",
        {
            "request": request,
            "transcripts": transcripts,
            "reports": reports,
            "watchlist": watchlist,
            "selected_keyword": keyword,
            "sort_by": sort_by,
            "limit": limit,
            "view": view,  # "transcripts" or "reports"
            "can_show_memories": can_show_memories,
            "analyzed_count": analyzed_count if 'analyzed_count' in locals() else 0,
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
