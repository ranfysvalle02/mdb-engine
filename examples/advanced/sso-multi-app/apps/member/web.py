#!/usr/bin/env python3
"""
Member - Cognitive Memory Showcase (Member Berries Edition)
===========================================================

"Member when AI agents actually remembered your name?"

A showcase app for MDB-Engine's cognitive memory system, inspired by
South Park's Member Berries. Demonstrates:

- Ebbinghaus Forgetting Curve (server-side decay)
- Flashbulb Memory (emotion-based stability boosts)
- GraphRAG ($graphLookup relationship traversal)
- Bucket Isolation (compartmentalized memory)
- Conflict Detection (contradictory fact resolution)
- Cold Storage (graceful forgetting with audit trail)
- Memory Analytics (cognitive health dashboard)
- sample_mflix $graphLookup demo (movie cast graph traversal)
"""

import json
import logging
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from mdb_engine import MongoDBEngine
from mdb_engine.dependencies import get_scoped_db, get_memory_service

# Import shared security utilities
try:
    from shared_security import get_cookie_settings, validate_jwt_token_format
except ImportError:

    def get_cookie_settings():
        return {"httponly": True, "samesite": "lax", "secure": False}

    def validate_jwt_token_format(token: str) -> bool:
        return bool(token and len(token) > 10)


load_dotenv()

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

# Suppress LiteLLM coroutine warnings
import warnings

warnings.filterwarnings(
    "ignore", message="coroutine.*was never awaited", category=RuntimeWarning
)

APP_SLUG = "member"
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# ============================================================================
# ENGINE INITIALIZATION
# ============================================================================

engine = MongoDBEngine(
    mongo_uri=os.getenv(
        "MONGODB_URI", os.getenv("MONGO_URI", "mongodb://mongodb:27017/")
    ),
    db_name=os.getenv("MONGODB_DB", os.getenv("MONGO_DB_NAME", "oblivio_apps")),
)

# Global references initialized on startup
cognitive_engine = None
llm_service = None


async def on_startup(app_instance, engine_ref, manifest):
    """Initialize CognitiveEngine and services on app startup."""
    global cognitive_engine, llm_service

    from mdb_engine.llm import get_llm_service
    from mdb_engine.memory import CognitiveEngine

    # Initialize LLM service
    llm_config = manifest.get("llm_config", {})
    llm_service = get_llm_service(config=llm_config)

    # Initialize CognitiveEngine
    memory_service = engine_ref.get_memory_service(APP_SLUG)
    if memory_service:
        # Inject LLM service into memory service
        if hasattr(memory_service, "_injected_llm_service"):
            if memory_service._injected_llm_service is None:
                memory_service._injected_llm_service = llm_service
                memory_service.llm_available = True
        else:
            memory_service._injected_llm_service = llm_service
            memory_service.llm_available = True

        try:
            scoped_db = await engine_ref.get_scoped_db(APP_SLUG)
            chat_history_collection = scoped_db["chat_history"]

            cognitive_engine = CognitiveEngine(
                app_slug=APP_SLUG,
                memory_service=memory_service,
                chat_history_collection=chat_history_collection,
                stm_context_limit=10,
                ltm_search_limit=8,
                auto_summarize_threshold=20,
                llm_service=llm_service,
                enable_context_engineering=True,
                stm_raw_window=5,
                enable_entity_extraction=True,
                enable_dynamic_persona=True,
                graph_min_nodes=2,
                graph_min_hop_distance=0,
                graph_min_edges=0,
            )
            logger.info(
                "Member Berry Online: CognitiveEngine with GraphRAG ready"
            )
        except Exception as e:
            logger.error(f"Failed to initialize CognitiveEngine: {e}", exc_info=True)
            cognitive_engine = None
    else:
        logger.warning("Memory service not found - CognitiveEngine disabled")


# Create the FastAPI app
manifest_path = Path(__file__).parent / "manifest.json"
app = engine.create_app(
    slug=APP_SLUG,
    manifest=manifest_path,
    title="Member - Cognitive Memory Showcase",
    description="Member Berries-inspired cognitive memory demo",
    version="1.0.0",
    on_startup=on_startup,
)


# ============================================================================
# AUTH HELPERS (SSO pattern)
# ============================================================================


def get_auth_hub_url() -> str:
    """Get auth hub URL from manifest or environment."""
    manifest = getattr(app.state, "manifest", None)
    if manifest:
        auth_config = manifest.get("auth", {})
        if auth_config.get("mode") == "shared":
            url = auth_config.get("auth_hub_url")
            if url:
                return url
    return os.getenv("AUTH_HUB_URL", "http://localhost:8000")


def get_current_user(request: Request) -> dict | None:
    """Get user from request.state (populated by SharedAuthMiddleware)."""
    return getattr(request.state, "user", None)


def require_user(request: Request) -> dict:
    """Get current user or raise 401."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail=f"Not authenticated. Please log in at {get_auth_hub_url()}/login",
        )
    return user


# ============================================================================
# SSO ROUTES
# ============================================================================


@app.get("/login")
async def login_redirect(request: Request):
    """Redirect to auth hub login."""
    from urllib.parse import quote_plus

    base_url = f"{request.url.scheme}://{request.url.hostname}:{request.url.port}"
    callback_url = f"{base_url}/auth/callback"
    return RedirectResponse(
        url=f"{get_auth_hub_url()}/login?redirect_to={quote_plus(callback_url)}",
        status_code=302,
    )


@app.get("/auth/callback")
async def auth_callback(request: Request, token: str = None):
    """Token exchange endpoint for SSO."""
    from urllib.parse import unquote_plus

    if not token:
        token = request.query_params.get("token")
    if token:
        token = unquote_plus(token)

    if not token or not validate_jwt_token_format(token):
        return RedirectResponse(
            url=f"{get_auth_hub_url()}/login?error=invalid_token", status_code=302
        )

    from mdb_engine.auth.shared_users import SharedUserPool

    pool = getattr(app.state, "user_pool", None)
    if not pool:
        return RedirectResponse(
            url=f"{get_auth_hub_url()}/login?error=pool_not_initialized",
            status_code=302,
        )

    user = await pool.validate_token(token)
    if not user:
        return RedirectResponse(
            url=f"{get_auth_hub_url()}/login?error=invalid_token", status_code=302
        )

    response = RedirectResponse(url="/", status_code=302)
    cookie_settings = get_cookie_settings()
    response.set_cookie(
        key="mdb_auth_token",
        value=token,
        httponly=cookie_settings["httponly"],
        samesite=cookie_settings["samesite"],
        secure=cookie_settings["secure"],
        max_age=86400,
        path="/",
    )
    return response


@app.post("/logout")
async def logout(request: Request):
    """Logout and revoke token."""
    from mdb_engine.auth.shared_users import SharedUserPool

    pool = getattr(app.state, "user_pool", None)
    token = request.cookies.get("mdb_auth_token")

    if pool and token:
        try:
            await pool.revoke_token(token, reason="logout")
        except (AttributeError, TypeError) as e:
            logger.warning(f"Failed to revoke token: {e}")

    response = RedirectResponse(url=f"{get_auth_hub_url()}/login", status_code=302)
    cookie_settings = get_cookie_settings()
    response.delete_cookie(
        "mdb_auth_token",
        path="/",
        domain=None,
        secure=cookie_settings["secure"],
        samesite=cookie_settings["samesite"],
    )
    return response


# ============================================================================
# MAIN PAGE
# ============================================================================


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main dashboard page."""
    user = get_current_user(request)
    if not user:
        from urllib.parse import quote_plus

        base_url = f"{request.url.scheme}://{request.url.hostname}:{request.url.port}"
        callback_url = f"{base_url}/auth/callback"
        return RedirectResponse(
            url=f"{get_auth_hub_url()}/login?redirect_to={quote_plus(callback_url)}",
            status_code=302,
        )

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "user": user,
            "app_name": "Member",
            "auth_hub_url": get_auth_hub_url(),
        },
    )


# ============================================================================
# CHAT ENDPOINT (CognitiveEngine)
# ============================================================================


@app.post("/api/chat")
async def chat(request: Request):
    """Chat with the Member Berry using CognitiveEngine.

    Supports bucket isolation: pass bucket_id and bucket_type to scope memory.
    Returns response, LTM memories, graph context, and extracted memories.
    """
    user = require_user(request)
    if not cognitive_engine:
        raise HTTPException(503, "CognitiveEngine not initialized")

    data = await request.json()
    user_query = data.get("message", "").strip()
    if not user_query:
        raise HTTPException(400, "Message is required")

    session_id = data.get("session_id", f"member:{user['email']}:default")
    bucket_id = data.get("bucket_id")
    bucket_type = data.get("bucket_type")
    extract_facts = data.get("extract_facts", True)

    try:
        result = await cognitive_engine.chat(
            user_id=user["email"],
            session_id=session_id,
            user_query=user_query,
            extract_facts=extract_facts,
            bucket_id=bucket_id,
            bucket_type=bucket_type,
        )

        # Serialize memories for JSON response
        ltm_memories = []
        for mem in result.get("ltm_memories", []):
            ltm_memories.append(
                {
                    "id": str(mem.get("_id", mem.get("id", ""))),
                    "text": mem.get("text", mem.get("memory", "")),
                    "importance": mem.get("importance", 0),
                    "emotion": mem.get("emotion", 0),
                    "stability": mem.get("stability", 48),
                    "category": mem.get("category", "unknown"),
                    "strength": mem.get("strength", mem.get("final_score", 0)),
                    "created_at": str(mem.get("created_at", "")),
                }
            )

        memories_stored = []
        for mem in result.get("memories_stored", []):
            memories_stored.append(
                {
                    "id": str(mem.get("_id", mem.get("id", ""))),
                    "text": mem.get("text", mem.get("memory", "")),
                    "importance": mem.get("importance", 0),
                    "emotion": mem.get("emotion", 0),
                    "category": mem.get("category", "unknown"),
                }
            )

        graph_context = result.get("graph_context")
        graph_data = None
        if graph_context:
            graph_data = {
                "entry_nodes": len(graph_context.get("entry_nodes", [])),
                "related_nodes": len(graph_context.get("graph_context", [])),
                "nodes": [],
            }
            for node in graph_context.get("entry_nodes", [])[:10]:
                graph_data["nodes"].append(
                    {
                        "id": str(node.get("_id", "")),
                        "label": node.get("label", node.get("_id", "")),
                        "type": node.get("type", "unknown"),
                    }
                )

        return JSONResponse(
            {
                "response": result.get("response", ""),
                "ltm_memories": ltm_memories,
                "memories_stored": memories_stored,
                "graph_context": graph_data,
                "session_message_count": result.get("session_message_count", 0),
            }
        )
    except Exception as e:
        logger.error(f"Chat error: {e}", exc_info=True)
        raise HTTPException(500, f"Chat failed: {str(e)}") from e


# ============================================================================
# MEMORY CRUD
# ============================================================================


@app.get("/api/memories")
async def list_memories(request: Request, db=Depends(get_scoped_db)):
    """List active memories for the current user with decay-ranked strength."""
    user = require_user(request)
    bucket_id = request.query_params.get("bucket_id")

    memory_service = engine.get_memory_service(APP_SLUG)
    if not memory_service:
        raise HTTPException(503, "Memory service not available")

    try:
        # Search with a broad query to get all memories
        filters = {}
        if bucket_id:
            filters["associated_bucket_id"] = bucket_id

        memories = await memory_service.search(
            query="*",
            user_id=user["email"],
            limit=100,
            filters=filters,
        )

        result = []
        now = datetime.now(timezone.utc)
        for mem in memories:
            created = mem.get("created_at")
            if isinstance(created, datetime):
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                t_hours = (now - created).total_seconds() / 3600
            else:
                t_hours = 0

            importance = mem.get("importance", 0.5)
            stability = mem.get("stability", 48)
            # Ebbinghaus decay: S = R * exp(-t / H)
            strength = importance * math.exp(-t_hours / max(stability, 1))

            result.append(
                {
                    "id": str(mem.get("_id", mem.get("id", ""))),
                    "text": mem.get("text", mem.get("memory", "")),
                    "importance": round(importance, 3),
                    "emotion": round(mem.get("emotion", 0), 3),
                    "stability": round(stability, 1),
                    "strength": round(strength, 4),
                    "category": mem.get("category", "unknown"),
                    "bucket_id": mem.get("associated_bucket_id", ""),
                    "is_active": mem.get("is_active", True),
                    "created_at": str(mem.get("created_at", "")),
                    "last_accessed": str(mem.get("last_accessed", "")),
                    "access_count": mem.get("access_count", 0),
                    "t_hours": round(t_hours, 1),
                }
            )

        # Sort by strength descending
        result.sort(key=lambda m: m["strength"], reverse=True)

        return JSONResponse({"memories": result, "count": len(result)})
    except Exception as e:
        logger.error(f"List memories error: {e}", exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.post("/api/memories/inject")
async def inject_memory(request: Request):
    """Inject a memory directly (bypasses LLM extraction)."""
    user = require_user(request)
    data = await request.json()

    memory_text = data.get("text", "").strip()
    if not memory_text:
        raise HTTPException(400, "Memory text is required")

    memory_service = engine.get_memory_service(APP_SLUG)
    if not memory_service:
        raise HTTPException(503, "Memory service not available")

    try:
        result = await memory_service.inject(
            memory=memory_text,
            user_id=user["email"],
            metadata={
                "importance": data.get("importance", 0.7),
                "emotion": data.get("emotion", 0.3),
                "category": data.get("category", "biographical"),
            },
            bucket_id=data.get("bucket_id"),
            bucket_type=data.get("bucket_type"),
        )

        return JSONResponse(
            {
                "success": True,
                "memory_id": str(result.get("_id", result.get("id", ""))),
                "text": memory_text,
            },
            status_code=201,
        )
    except Exception as e:
        logger.error(f"Inject memory error: {e}", exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.delete("/api/memories/{memory_id}")
async def delete_memory(memory_id: str, request: Request):
    """Delete a specific memory."""
    user = require_user(request)

    memory_service = engine.get_memory_service(APP_SLUG)
    if not memory_service:
        raise HTTPException(503, "Memory service not available")

    try:
        if hasattr(memory_service, "delete"):
            await memory_service.delete(memory_id=memory_id, user_id=user["email"])
        else:
            # Fallback: direct collection delete
            from bson import ObjectId

            scoped_db = await engine.get_scoped_db(APP_SLUG)
            await scoped_db.member_memories.delete_one(
                {"_id": ObjectId(memory_id), "user_id": user["email"]}
            )
        return JSONResponse({"success": True})
    except Exception as e:
        logger.error(f"Delete memory error: {e}", exc_info=True)
        raise HTTPException(500, str(e)) from e


# ============================================================================
# MEMORY SEARCH
# ============================================================================


@app.post("/api/memories/search")
async def search_memories(request: Request):
    """Semantic search through memories with decay ranking."""
    user = require_user(request)
    data = await request.json()

    query = data.get("query", "").strip()
    if not query:
        raise HTTPException(400, "Query is required")

    memory_service = engine.get_memory_service(APP_SLUG)
    if not memory_service:
        raise HTTPException(503, "Memory service not available")

    try:
        results = await memory_service.search(
            query=query,
            user_id=user["email"],
            limit=data.get("limit", 10),
            filters=data.get("filters", {}),
        )

        memories = []
        for mem in results:
            memories.append(
                {
                    "id": str(mem.get("_id", mem.get("id", ""))),
                    "text": mem.get("text", mem.get("memory", "")),
                    "importance": mem.get("importance", 0),
                    "emotion": mem.get("emotion", 0),
                    "strength": mem.get("strength", mem.get("final_score", 0)),
                    "category": mem.get("category", "unknown"),
                    "similarity": mem.get("similarity", 0),
                }
            )

        return JSONResponse({"results": memories, "count": len(memories)})
    except Exception as e:
        logger.error(f"Search error: {e}", exc_info=True)
        raise HTTPException(500, str(e)) from e


# ============================================================================
# MEMORY ANALYTICS
# ============================================================================


@app.get("/api/analytics")
async def get_analytics(request: Request, db=Depends(get_scoped_db)):
    """Get memory health analytics for the current user."""
    user = require_user(request)
    user_id = user["email"]

    try:
        # Count active memories
        active_count = await db.member_memories.count_documents(
            {"user_id": user_id, "is_active": {"$ne": False}}
        )

        # Count cold storage
        cold_count = await db.member_memories.count_documents(
            {"user_id": user_id, "is_active": False}
        )

        # Aggregate category counts
        pipeline = [
            {"$match": {"user_id": user_id, "is_active": {"$ne": False}}},
            {"$group": {"_id": "$category", "count": {"$sum": 1}}},
        ]
        categories = {}
        async for doc in db.member_memories.aggregate(pipeline):
            cat = doc["_id"] or "unknown"
            categories[cat] = doc["count"]

        # Calculate average strength via aggregation
        now = datetime.now(timezone.utc)
        strength_pipeline = [
            {"$match": {"user_id": user_id, "is_active": {"$ne": False}}},
            {
                "$addFields": {
                    "t_hours": {
                        "$divide": [
                            {"$subtract": [now, "$created_at"]},
                            3600000,  # ms to hours
                        ]
                    }
                }
            },
            {
                "$addFields": {
                    "calc_strength": {
                        "$multiply": [
                            {"$ifNull": ["$importance", 0.5]},
                            {
                                "$exp": {
                                    "$divide": [
                                        {"$multiply": ["$t_hours", -1]},
                                        {"$ifNull": ["$stability", 48]},
                                    ]
                                }
                            },
                        ]
                    }
                }
            },
            {
                "$group": {
                    "_id": None,
                    "avg_strength": {"$avg": "$calc_strength"},
                    "weak_count": {
                        "$sum": {
                            "$cond": [{"$lt": ["$calc_strength", 0.3]}, 1, 0]
                        }
                    },
                    "strong_count": {
                        "$sum": {
                            "$cond": [{"$gt": ["$calc_strength", 0.7]}, 1, 0]
                        }
                    },
                }
            },
        ]

        avg_strength = 0
        weak_count = 0
        strong_count = 0
        async for doc in db.member_memories.aggregate(strength_pipeline):
            avg_strength = doc.get("avg_strength", 0)
            weak_count = doc.get("weak_count", 0)
            strong_count = doc.get("strong_count", 0)

        # Graph node count
        graph_count = await db["__kg"].count_documents({"app_slug": APP_SLUG})

        return JSONResponse(
            {
                "active_memories": active_count,
                "cold_storage_memories": cold_count,
                "capacity_used": round(active_count / 1000, 2),  # max_capacity=1000
                "average_strength": round(avg_strength, 3) if avg_strength else 0,
                "weak_memories": weak_count,
                "strong_memories": strong_count,
                "categories": categories,
                "graph_nodes": graph_count,
            }
        )
    except Exception as e:
        logger.error(f"Analytics error: {e}", exc_info=True)
        raise HTTPException(500, str(e)) from e


# ============================================================================
# GRAPH EXPLORER
# ============================================================================


@app.get("/api/graph/nodes")
async def list_graph_nodes(request: Request, db=Depends(get_scoped_db)):
    """List knowledge graph nodes."""
    user = require_user(request)
    limit = int(request.query_params.get("limit", "50"))
    node_type = request.query_params.get("type")

    try:
        query = {"app_slug": APP_SLUG}
        if node_type:
            query["type"] = node_type

        cursor = db["__kg"].find(query).sort("_id", -1).limit(limit)
        nodes = []
        async for doc in cursor:
            edges = doc.get("edges", [])
            nodes.append(
                {
                    "id": str(doc["_id"]),
                    "label": doc.get("label", str(doc["_id"])),
                    "type": doc.get("type", "unknown"),
                    "edge_count": len(edges),
                    "edges": [
                        {
                            "relation": e.get("relation", ""),
                            "target": str(e.get("target", "")),
                            "active": e.get("active", True),
                        }
                        for e in edges[:10]
                    ],
                }
            )

        return JSONResponse({"nodes": nodes, "count": len(nodes)})
    except Exception as e:
        logger.error(f"Graph nodes error: {e}", exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.post("/api/graph/traverse")
async def traverse_graph(request: Request, db=Depends(get_scoped_db)):
    """Traverse knowledge graph using $graphLookup."""
    user = require_user(request)
    data = await request.json()

    start_id = data.get("start_id", "").strip()
    if not start_id:
        raise HTTPException(400, "start_id is required")

    max_depth = min(int(data.get("max_depth", 2)), 5)

    try:
        pipeline = [
            {"$match": {"_id": start_id, "app_slug": APP_SLUG}},
            {
                "$graphLookup": {
                    "from": db["__kg"].name,
                    "startWith": "$edges.target",
                    "connectFromField": "edges.target",
                    "connectToField": "_id",
                    "as": "network",
                    "maxDepth": max_depth - 1,
                    "depthField": "hop_distance",
                    "restrictSearchWithMatch": {"app_slug": APP_SLUG},
                }
            },
        ]

        results = []
        async for doc in db["__kg"].aggregate(pipeline):
            start_node = {
                "id": str(doc["_id"]),
                "label": doc.get("label", str(doc["_id"])),
                "type": doc.get("type", "unknown"),
                "hop": 0,
            }
            network = []
            for node in doc.get("network", []):
                network.append(
                    {
                        "id": str(node["_id"]),
                        "label": node.get("label", str(node["_id"])),
                        "type": node.get("type", "unknown"),
                        "hop": node.get("hop_distance", 0) + 1,
                        "edges": [
                            {
                                "relation": e.get("relation", ""),
                                "target": str(e.get("target", "")),
                            }
                            for e in node.get("edges", [])[:5]
                        ],
                    }
                )

            results.append(
                {
                    "start": start_node,
                    "network": sorted(network, key=lambda n: n["hop"]),
                    "total_nodes": len(network) + 1,
                }
            )

        if not results:
            return JSONResponse(
                {"start": None, "network": [], "total_nodes": 0, "message": "Node not found"}
            )

        return JSONResponse(results[0])
    except Exception as e:
        logger.error(f"Graph traverse error: {e}", exc_info=True)
        raise HTTPException(500, str(e)) from e


# ============================================================================
# COLD STORAGE
# ============================================================================


@app.get("/api/cold-storage")
async def get_cold_storage(request: Request, db=Depends(get_scoped_db)):
    """View pruned/soft-deleted memories in cold storage."""
    user = require_user(request)

    try:
        cursor = (
            db.member_memories.find({"user_id": user["email"], "is_active": False})
            .sort("pruned_at", -1)
            .limit(50)
        )

        memories = []
        async for doc in cursor:
            memories.append(
                {
                    "id": str(doc["_id"]),
                    "text": doc.get("text", doc.get("memory", "")),
                    "importance": doc.get("importance", 0),
                    "category": doc.get("category", "unknown"),
                    "pruned_at": str(doc.get("pruned_at", "")),
                    "pruning_reason": doc.get("pruning_reason", "unknown"),
                    "created_at": str(doc.get("created_at", "")),
                }
            )

        return JSONResponse({"memories": memories, "count": len(memories)})
    except Exception as e:
        logger.error(f"Cold storage error: {e}", exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.post("/api/cold-storage/{memory_id}/restore")
async def restore_from_cold_storage(memory_id: str, request: Request, db=Depends(get_scoped_db)):
    """Restore a memory from cold storage."""
    user = require_user(request)

    try:
        from bson import ObjectId

        result = await db.member_memories.update_one(
            {"_id": ObjectId(memory_id), "user_id": user["email"], "is_active": False},
            {
                "$set": {"is_active": True},
                "$unset": {"pruned_at": "", "pruning_reason": ""},
            },
        )

        if result.matched_count == 0:
            raise HTTPException(404, "Memory not found in cold storage")

        return JSONResponse({"success": True, "memory_id": memory_id})
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Restore error: {e}", exc_info=True)
        raise HTTPException(500, str(e)) from e


# ============================================================================
# CONFLICT DETECTION
# ============================================================================


@app.post("/api/conflicts/check")
async def check_conflict(request: Request):
    """Check if a new fact conflicts with existing memories."""
    user = require_user(request)
    data = await request.json()

    new_fact = data.get("fact", "").strip()
    if not new_fact:
        raise HTTPException(400, "Fact text is required")

    memory_service = engine.get_memory_service(APP_SLUG)
    if not memory_service:
        raise HTTPException(503, "Memory service not available")

    try:
        # Search for similar existing memories
        similar = await memory_service.search(
            query=new_fact,
            user_id=user["email"],
            limit=5,
        )

        conflicts = []
        for mem in similar:
            similarity = mem.get("similarity", mem.get("score", 0))
            if similarity > 0.7:
                conflicts.append(
                    {
                        "id": str(mem.get("_id", mem.get("id", ""))),
                        "text": mem.get("text", mem.get("memory", "")),
                        "similarity": round(similarity, 3),
                        "importance": mem.get("importance", 0),
                        "category": mem.get("category", "unknown"),
                    }
                )

        return JSONResponse(
            {
                "new_fact": new_fact,
                "potential_conflicts": conflicts,
                "has_conflicts": len(conflicts) > 0,
            }
        )
    except Exception as e:
        logger.error(f"Conflict check error: {e}", exc_info=True)
        raise HTTPException(500, str(e)) from e


# ============================================================================
# SAMPLE_MFLIX $GRAPHLOOKUP DEMO
# ============================================================================


@app.get("/api/mflix/actors")
async def search_mflix_actors(request: Request):
    """Search for actors in sample_mflix.movies collection."""
    user = require_user(request)
    query = request.query_params.get("q", "").strip()
    limit = min(int(request.query_params.get("limit", "20")), 50)

    try:
        # Access the sample_mflix database directly (not scoped)
        motor_client = engine._connection_manager.mongo_client
        mflix_db = motor_client["sample_mflix"]

        if not query:
            # Return distinct actors (sample)
            pipeline = [
                {"$unwind": "$cast"},
                {"$group": {"_id": "$cast"}},
                {"$sort": {"_id": 1}},
                {"$limit": limit},
            ]
            actors = []
            async for doc in mflix_db.movies.aggregate(pipeline):
                actors.append(doc["_id"])
            return JSONResponse({"actors": actors, "count": len(actors)})

        # Search for movies featuring an actor
        pipeline = [
            {"$match": {"cast": {"$regex": query, "$options": "i"}}},
            {"$project": {"title": 1, "year": 1, "cast": 1, "genres": 1}},
            {"$sort": {"year": -1}},
            {"$limit": limit},
        ]

        movies = []
        async for doc in mflix_db.movies.aggregate(pipeline):
            movies.append(
                {
                    "title": doc.get("title", ""),
                    "year": doc.get("year"),
                    "cast": doc.get("cast", [])[:8],
                    "genres": doc.get("genres", []),
                }
            )

        return JSONResponse(
            {"query": query, "movies": movies, "count": len(movies)}
        )
    except Exception as e:
        logger.error(f"Mflix actors error: {e}", exc_info=True)
        return JSONResponse(
            {
                "error": "sample_mflix not available. See the README for seeding instructions.",
                "movies": [],
                "count": 0,
            },
            status_code=200,
        )


@app.post("/api/mflix/graph-lookup")
async def mflix_graph_lookup(request: Request):
    """Run $graphLookup on sample_mflix.movies to traverse cast relationships.

    Finds co-star networks: "Member when Actor X was in Movie Y with Actor Z..."
    """
    user = require_user(request)
    data = await request.json()

    actor_name = data.get("actor", "").strip()
    if not actor_name:
        raise HTTPException(400, "Actor name is required")

    max_depth = min(int(data.get("max_depth", 1)), 3)

    try:
        motor_client = engine._connection_manager.mongo_client
        mflix_db = motor_client["sample_mflix"]

        # Step 1: Find movies featuring the actor
        # Step 2: Use $graphLookup to traverse cast connections
        # Since movies is a flat collection, we simulate graph traversal:
        # Find all movies with the actor, then find co-stars, then find
        # movies those co-stars appeared in together.
        pipeline = [
            # Start: find movies with this actor
            {"$match": {"cast": {"$regex": f"^{actor_name}$", "$options": "i"}}},
            {"$limit": 10},
            # Unwind cast to get co-stars
            {"$unwind": "$cast"},
            # Group to get unique co-stars and their shared movies
            {
                "$group": {
                    "_id": "$cast",
                    "shared_movies": {"$addToSet": "$title"},
                    "movie_count": {"$sum": 1},
                }
            },
            # Exclude the original actor
            {
                "$match": {
                    "_id": {"$not": {"$regex": f"^{actor_name}$", "$options": "i"}}
                }
            },
            {"$sort": {"movie_count": -1}},
            {"$limit": 20},
        ]

        co_stars = []
        async for doc in mflix_db.movies.aggregate(pipeline):
            co_stars.append(
                {
                    "actor": doc["_id"],
                    "shared_movies": doc["shared_movies"][:5],
                    "movie_count": doc["movie_count"],
                }
            )

        # If depth > 1, find second-degree connections
        second_degree = []
        if max_depth > 1 and co_stars:
            top_costar = co_stars[0]["actor"]
            pipeline2 = [
                {"$match": {"cast": top_costar}},
                {"$limit": 5},
                {"$unwind": "$cast"},
                {
                    "$group": {
                        "_id": "$cast",
                        "shared_movies": {"$addToSet": "$title"},
                        "movie_count": {"$sum": 1},
                    }
                },
                {
                    "$match": {
                        "_id": {
                            "$not": {
                                "$regex": f"^({actor_name}|{top_costar})$",
                                "$options": "i",
                            }
                        }
                    }
                },
                {"$sort": {"movie_count": -1}},
                {"$limit": 10},
            ]

            async for doc in mflix_db.movies.aggregate(pipeline2):
                second_degree.append(
                    {
                        "actor": doc["_id"],
                        "via": top_costar,
                        "shared_movies": doc["shared_movies"][:3],
                        "movie_count": doc["movie_count"],
                    }
                )

        return JSONResponse(
            {
                "actor": actor_name,
                "co_stars": co_stars,
                "second_degree": second_degree,
                "total_connections": len(co_stars) + len(second_degree),
            }
        )
    except Exception as e:
        logger.error(f"Mflix graphLookup error: {e}", exc_info=True)
        return JSONResponse(
            {
                "error": "sample_mflix not available. See the README for seeding instructions.",
                "actor": actor_name,
                "co_stars": [],
                "second_degree": [],
                "total_connections": 0,
            },
            status_code=200,
        )


# ============================================================================
# DEMO SEED
# ============================================================================


@app.post("/api/demo/seed")
async def seed_demo_data(request: Request):
    """Pre-populate sample memories and graph nodes for demonstration."""
    user = require_user(request)
    user_id = user["email"]

    memory_service = engine.get_memory_service(APP_SLUG)
    if not memory_service:
        raise HTTPException(503, "Memory service not available")

    seeded = {"memories": 0, "graph_nodes": 0}

    # Sample memories with varying importance/emotion for decay demo
    sample_memories = [
        {
            "text": "User's name is Alex and they live in Seattle",
            "metadata": {
                "importance": 0.9,
                "emotion": 0.3,
                "category": "biographical",
            },
        },
        {
            "text": "Alex's brother Mike likes golf and works at Google",
            "metadata": {
                "importance": 0.8,
                "emotion": 0.4,
                "category": "relationships",
            },
        },
        {
            "text": "Alex just got promoted to VP of Engineering - huge career milestone",
            "metadata": {
                "importance": 0.95,
                "emotion": 0.9,
                "category": "work",
            },
        },
        {
            "text": "Alex prefers dark roast coffee, always orders a cortado",
            "metadata": {
                "importance": 0.4,
                "emotion": 0.1,
                "category": "preferences",
            },
        },
        {
            "text": "Alex is allergic to shellfish - this is medically important",
            "metadata": {
                "importance": 0.95,
                "emotion": 0.6,
                "category": "health",
            },
        },
        {
            "text": "Alex's daughter Lily has a piano recital next Friday",
            "metadata": {
                "importance": 0.7,
                "emotion": 0.5,
                "category": "relationships",
            },
        },
        {
            "text": "Alex wants to learn Rust programming by end of Q2",
            "metadata": {
                "importance": 0.6,
                "emotion": 0.3,
                "category": "goals",
            },
        },
        {
            "text": "Member when Star Wars came out and changed everything? Classic.",
            "metadata": {
                "importance": 0.5,
                "emotion": 0.7,
                "category": "nostalgia",
            },
        },
    ]

    try:
        for mem in sample_memories:
            try:
                await memory_service.inject(
                    memory=mem["text"],
                    user_id=user_id,
                    metadata=mem["metadata"],
                    bucket_id=f"category:personal:{user_id}",
                    bucket_type="category",
                )
                seeded["memories"] += 1
            except Exception as e:
                logger.warning(f"Failed to seed memory: {e}")

        # Seed graph nodes
        graph_service = engine.get_graph_service(APP_SLUG)
        if graph_service:
            sample_nodes = [
                {
                    "_id": "person:alex",
                    "type": "person",
                    "label": "Alex",
                    "edges": [
                        {"relation": "brother", "target": "person:mike", "active": True},
                        {"relation": "daughter", "target": "person:lily", "active": True},
                        {"relation": "lives_in", "target": "location:seattle", "active": True},
                        {"relation": "works_at", "target": "organization:current_company", "active": True},
                        {"relation": "allergic_to", "target": "food:shellfish", "active": True},
                        {"relation": "likes", "target": "interest:coffee", "active": True},
                    ],
                },
                {
                    "_id": "person:mike",
                    "type": "person",
                    "label": "Mike",
                    "edges": [
                        {"relation": "brother", "target": "person:alex", "active": True},
                        {"relation": "likes", "target": "interest:golf", "active": True},
                        {"relation": "works_at", "target": "organization:google", "active": True},
                    ],
                },
                {
                    "_id": "person:lily",
                    "type": "person",
                    "label": "Lily",
                    "edges": [
                        {"relation": "parent", "target": "person:alex", "active": True},
                        {"relation": "likes", "target": "interest:piano", "active": True},
                    ],
                },
                {
                    "_id": "location:seattle",
                    "type": "location",
                    "label": "Seattle",
                    "edges": [],
                },
                {
                    "_id": "organization:google",
                    "type": "organization",
                    "label": "Google",
                    "edges": [],
                },
                {
                    "_id": "interest:golf",
                    "type": "interest",
                    "label": "Golf",
                    "edges": [],
                },
                {
                    "_id": "interest:piano",
                    "type": "interest",
                    "label": "Piano",
                    "edges": [],
                },
                {
                    "_id": "interest:coffee",
                    "type": "interest",
                    "label": "Coffee",
                    "edges": [],
                },
                {
                    "_id": "food:shellfish",
                    "type": "food",
                    "label": "Shellfish",
                    "edges": [],
                },
            ]

            scoped_db = await engine.get_scoped_db(APP_SLUG)
            kg_collection = scoped_db["__kg"]

            for node in sample_nodes:
                try:
                    node["app_slug"] = APP_SLUG
                    node["user_id"] = user_id
                    node["created_at"] = datetime.now(timezone.utc)
                    await kg_collection.update_one(
                        {"_id": node["_id"]},
                        {"$set": node},
                        upsert=True,
                    )
                    seeded["graph_nodes"] += 1
                except Exception as e:
                    logger.warning(f"Failed to seed graph node: {e}")

        return JSONResponse(
            {
                "success": True,
                "seeded": seeded,
                "message": f"Seeded {seeded['memories']} memories and {seeded['graph_nodes']} graph nodes. Oh, I member!",
            }
        )
    except Exception as e:
        logger.error(f"Seed error: {e}", exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.post("/api/demo/reset")
async def reset_demo_data(request: Request, db=Depends(get_scoped_db)):
    """Clear all demo data for the current user."""
    user = require_user(request)
    user_id = user["email"]

    try:
        mem_result = await db.member_memories.delete_many({"user_id": user_id})
        kg_result = await db["__kg"].delete_many(
            {"app_slug": APP_SLUG, "user_id": user_id}
        )
        chat_result = await db.chat_history.delete_many({"user_id": user_id})

        return JSONResponse(
            {
                "success": True,
                "deleted": {
                    "memories": mem_result.deleted_count,
                    "graph_nodes": kg_result.deleted_count,
                    "chat_messages": chat_result.deleted_count,
                },
            }
        )
    except Exception as e:
        logger.error(f"Reset error: {e}", exc_info=True)
        raise HTTPException(500, str(e)) from e


# ============================================================================
# HEALTH & INFO
# ============================================================================


@app.get("/health")
async def health():
    """Health check."""
    return {
        "status": "healthy",
        "app": APP_SLUG,
        "auth": "shared",
        "cognitive_engine": cognitive_engine is not None,
    }


@app.get("/api/me")
async def get_me(request: Request):
    """Get current user info."""
    user = require_user(request)
    return {
        "email": user["email"],
        "roles": user.get("app_roles", {}).get(APP_SLUG, []),
        "app": APP_SLUG,
    }


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    import uvicorn

    logger.info("Starting Member - Cognitive Memory Showcase on 0.0.0.0:8000")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
