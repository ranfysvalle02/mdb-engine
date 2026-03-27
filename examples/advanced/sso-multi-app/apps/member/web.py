#!/usr/bin/env python3
"""
Member - Cognitive Memory Showcase (Member Berries Edition)
===========================================================

"Member when AI agents actually remembered your name?"

A showcase app for MDB-Engine's cognitive memory system, inspired by
South Park's Member Berries. Demonstrates:

- Ebbinghaus Forgetting Curve (server-side decay)
- Flashbulb Memory (emotion-based stability boosts)
- GraphRAG ($graphLookup relationship traversal + path finding)
- Bucket Isolation (compartmentalized memory)
- Conflict Detection (contradictory fact resolution)
- Cold Storage (graceful forgetting with audit trail)
- Memory Analytics (cognitive health dashboard)
- Memory Types (episodic, procedural, working, semantic)
- Memory Timelines (multiverse branching and switching)
- Prospective Memory (intention-based triggers)
- Shared Memory (privacy-safe group memory promotion)
- Reflection & Consolidation (memory lifecycle management)
- Profile Service (auto-materialized user profiles from memory + graph)
- OSI Semantic Models (entity resolution, metric routing, export)
- sample_mflix $graphLookup demo (movie cast graph traversal)

MULTI-APP COMPATIBLE: This module uses the injected `app` and `engine`
variables provided by create_multi_app(). Do NOT create your own
MongoDBEngine or call engine.create_app() here.
"""
from __future__ import annotations

import logging
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from mdb_engine.dependencies import get_scoped_db
from pymongo.errors import PyMongoError

# Import shared security utilities



load_dotenv()

logger = logging.getLogger(__name__)

# Suppress coroutine warnings from LLM SDK async logging
import warnings

warnings.filterwarnings(
    "ignore", message="coroutine.*was never awaited", category=RuntimeWarning
)

APP_SLUG = "member"
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# `app` and `engine` are injected by create_multi_app's route auto-import.
# Do NOT reassign them. They are available as module-level variables.

# Global references (initialized by on_startup, called automatically by create_multi_app)
cognitive_engine = None
timeline_service = None
prospective_memory = None
shared_memory = None
memory_consolidator = None
reflection_service = None
profile_service = None
episodic_collection = None
procedural_collection = None


async def on_startup(app_instance, engine_ref, manifest):
    """Called automatically by create_multi_app after engine init and memory service setup."""
    global cognitive_engine, timeline_service, prospective_memory, shared_memory
    global memory_consolidator, reflection_service, profile_service
    global episodic_collection, procedural_collection

    from mdb_engine.llm import get_llm_service
    from mdb_engine.memory import CognitiveEngine

    llm_config = manifest.get("llm_config", {})
    llm_service = get_llm_service(config=llm_config)

    memory_service = engine_ref.get_memory_service(APP_SLUG)
    if not memory_service:
        logger.warning("Memory service not found for '%s' — CognitiveEngine disabled", APP_SLUG)
        return

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
        logger.info("Member Berry Online: CognitiveEngine with GraphRAG ready")
    except (ImportError, RuntimeError, OSError) as e:
        logger.error("Failed to initialize CognitiveEngine: %s", e, exc_info=True)
        cognitive_engine = None

    # --- Timeline Service ---
    try:
        from mdb_engine.memory.timeline import TimelineService

        timelines_wrapper = scoped_db["timelines"]
        timeline_service = TimelineService(timelines_wrapper)
        await timeline_service.ensure_initialized()
        logger.info("TimelineService initialized")
    except (ImportError, RuntimeError, OSError) as e:
        logger.warning("TimelineService init failed (non-critical): %s", e)
        timeline_service = None

    # --- Prospective Memory ---
    try:
        from mdb_engine.memory.prospective import ProspectiveMemory

        prospective_col = scoped_db["prospective_triggers"]
        prospective_memory = ProspectiveMemory(
            collection=prospective_col,
            embedding_model=manifest.get("memory_config", {}).get("embedding_model", "text-embedding-3-small"),
            embedding_service=engine_ref.get_embedding_service(APP_SLUG),
        )
        logger.info("ProspectiveMemory initialized")
    except (ImportError, RuntimeError, OSError) as e:
        logger.warning("ProspectiveMemory init failed (non-critical): %s", e)
        prospective_memory = None

    # --- Shared Memory ---
    try:
        from mdb_engine.memory.shared import SharedMemory

        shared_memory = SharedMemory(
            semantic_collection=memory_service.collection,
            shared_collection=memory_service.collection,
        )
        logger.info("SharedMemory initialized")
    except (ImportError, RuntimeError, OSError) as e:
        logger.warning("SharedMemory init failed (non-critical): %s", e)
        shared_memory = None

    # --- Memory Type Collections (episodic, procedural) ---
    try:
        episodic_collection = scoped_db["episodic"]
        procedural_collection = scoped_db["procedural"]
        logger.info("Memory type collections initialized (episodic, procedural)")
    except (ImportError, RuntimeError, OSError) as e:
        logger.warning("Memory type collections init failed (non-critical): %s", e)

    # --- Memory Consolidator ---
    try:
        from mdb_engine.memory.consolidator import MemoryConsolidator

        motor_client = engine_ref._connection_manager.mongo_client
        memory_consolidator = MemoryConsolidator(
            db_client=motor_client,
            db_name=engine_ref.db_name,
            model=manifest.get("llm_config", {}).get("providers", {}).get("chat", "gemini/gemini-2.5-flash-lite"),
            episodic_collection=episodic_collection,
            entity_collection=memory_service.collection,
            procedural_collection=procedural_collection,
            llm_service=llm_service,
        )
        logger.info("MemoryConsolidator initialized")
    except (ImportError, RuntimeError, OSError) as e:
        logger.warning("MemoryConsolidator init failed (non-critical): %s", e)
        memory_consolidator = None

    # --- Reflection Service ---
    try:
        from mdb_engine.memory.reflection import ReflectionService

        reflection_config = manifest.get("memory_config", {}).get("reflection", {})
        if reflection_config.get("enabled", False):
            reflection_service = ReflectionService(
                app_slug=APP_SLUG,
                memories_collection=memory_service.collection,
                config=reflection_config,
                llm_service=llm_service,
            )
            logger.info("ReflectionService initialized")
    except (ImportError, RuntimeError, OSError) as e:
        logger.warning("ReflectionService init failed (non-critical): %s", e)
        reflection_service = None

    # --- Profile Service ---
    try:
        from mdb_engine.profile import ProfileService

        profile_config = manifest.get("profile_config", {})
        if profile_config.get("enabled", False):
            user_profile_col = scoped_db[profile_config.get("user_profiles", {}).get("collection_name", "user_profiles")]
            community_profile_col = scoped_db[profile_config.get("community_profile", {}).get("collection_name", "community_profile")]
            graph_service = engine_ref.get_graph_service(APP_SLUG) if hasattr(engine_ref, "get_graph_service") else None
            profile_service = ProfileService(
                app_slug=APP_SLUG,
                user_profile_collection=user_profile_col,
                community_profile_collection=community_profile_col,
                memory_service=memory_service,
                memory_collection=memory_service.collection,
                graph_service=graph_service,
                llm_service=llm_service,
                config=profile_config,
            )
            logger.info("ProfileService initialized")
    except (ImportError, RuntimeError, OSError) as e:
        logger.warning("ProfileService init failed (non-critical): %s", e)
        profile_service = None


# NOTE: New code should use ``Depends(get_engine)`` from mdb_engine.dependencies
# instead of this helper.  Kept here for the existing 20+ call sites.
def _get_engine(request: Request):
    """Get the shared MongoDBEngine from request or app state."""
    return (
        getattr(request.state, "engine", None)
        or getattr(request.app.state, "engine", None)
    )


# ============================================================================
# AUTH HELPERS (SSO pattern)
# ============================================================================


def get_auth_hub_url(request: Request) -> str:
    """Get auth hub URL — uses path prefix in multi-app, falls back to env."""
    # In multi-app mode, request.state.auth_hub_url is set by AppContextMiddleware
    auth_url = getattr(request.state, "auth_hub_url", None)
    if auth_url:
        return auth_url

    # Fallback: check manifest
    manifest = getattr(request.app.state, "app_manifest", None) or getattr(
        request.app.state, "manifest", None
    )
    if manifest:
        auth_config = manifest.get("auth", {})
        if auth_config.get("mode") == "shared":
            url = auth_config.get("auth_hub_url")
            if url:
                return url

    return os.getenv("AUTH_HUB_URL", "/auth-hub")


def get_current_user(request: Request) -> dict | None:
    """Get user from request.state (populated by SharedAuthMiddleware)."""
    return getattr(request.state, "user", None)


def require_user(request: Request) -> dict:
    """Get current user or raise 401."""
    user = get_current_user(request)
    if not user:
        auth_url = get_auth_hub_url(request)
        raise HTTPException(
            status_code=401,
            detail=f"Not authenticated. Please log in at {auth_url}/login",
        )
    return user


# ============================================================================
# SSO ROUTES
# ============================================================================


@app.get("/login")
async def login_redirect(request: Request):
    """Redirect to auth hub login."""
    from urllib.parse import quote_plus

    auth_url = get_auth_hub_url(request)
    app_prefix = getattr(request.state, "app_base_path", "")
    callback_url = f"{app_prefix}/auth/callback"
    return RedirectResponse(
        url=f"{auth_url}/login?redirect_to={quote_plus(callback_url)}",
        status_code=302,
    )


# /auth/callback and /logout are auto-registered by the engine for shared-auth apps


# ============================================================================
# MAIN PAGE
# ============================================================================


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main dashboard page. Auto-seeds demo data on first visit."""
    user = get_current_user(request)
    if not user:
        from urllib.parse import quote_plus

        auth_url = get_auth_hub_url(request)
        app_prefix = getattr(request.state, "app_base_path", "")
        callback_url = f"{app_prefix}/auth/callback"
        return RedirectResponse(
            url=f"{auth_url}/login?redirect_to={quote_plus(callback_url)}",
            status_code=302,
        )

    # Auto-seed demo data if this user has no memories yet
    engine_ref = _get_engine(request)
    if engine_ref:
        await _auto_seed_if_empty(user["email"], engine_ref)

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "user": user,
            "app_name": "Member",
        },
    )


# ============================================================================
# CHAT ENDPOINT (CognitiveEngine)
# ============================================================================


@app.post("/api/chat")
async def chat(request: Request):
    """Chat with the Member Berry using CognitiveEngine."""
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
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Chat error: %s", e, exc_info=True)
        raise HTTPException(500, f"Chat failed: {str(e)}") from e


# ============================================================================
# MEMORY CRUD
# ============================================================================


@app.get("/api/memories")
async def list_memories(request: Request):
    """List active memories for the current user with decay-ranked strength.

    Uses a direct collection query instead of vector search so that
    browsing (no search query) reliably returns all memories.
    """
    user = require_user(request)
    bucket_id = request.query_params.get("bucket_id")

    engine_ref = _get_engine(request)
    if not engine_ref:
        raise HTTPException(503, "Engine not available")

    memory_service = engine_ref.get_memory_service(APP_SLUG)
    if not memory_service:
        raise HTTPException(503, "Memory service not available")

    try:
        mem_col = memory_service.collection
        query: dict[str, Any] = {
            "user_id": user["email"],
            "is_active": {"$ne": False},
        }
        if bucket_id:
            query["associated_bucket_id"] = bucket_id

        cursor = mem_col.find(query).sort("created_at", -1).limit(100)

        result = []
        now = datetime.now(timezone.utc)
        async for mem in cursor:
            created = mem.get("created_at")
            if isinstance(created, datetime):
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                t_hours = (now - created).total_seconds() / 3600
            else:
                t_hours = 0

            # Fields may live at top-level or inside metadata depending on
            # how the memory service stored them.
            meta = mem.get("metadata", {}) or {}
            importance = mem.get("importance", meta.get("importance", 0.5))
            emotion = mem.get("emotion", meta.get("emotion", 0))
            stability = mem.get("stability", meta.get("stability", 48))
            category = mem.get("category", meta.get("category", "unknown"))

            strength = importance * math.exp(-t_hours / max(stability, 1))

            result.append(
                {
                    "id": str(mem.get("_id", mem.get("id", ""))),
                    "text": mem.get("text", mem.get("memory", "")),
                    "importance": round(importance, 3),
                    "emotion": round(emotion, 3),
                    "stability": round(stability, 1),
                    "strength": round(strength, 4),
                    "category": category,
                    "bucket_id": mem.get("associated_bucket_id", ""),
                    "is_active": mem.get("is_active", True),
                    "created_at": str(mem.get("created_at", "")),
                    "last_accessed": str(mem.get("last_accessed", "")),
                    "access_count": mem.get("access_count", 0),
                    "t_hours": round(t_hours, 1),
                }
            )

        result.sort(key=lambda m: m["strength"], reverse=True)

        return JSONResponse({"memories": result, "count": len(result)})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("List memories error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.post("/api/memories/inject")
async def inject_memory(request: Request):
    """Inject a memory directly (bypasses LLM extraction)."""
    user = require_user(request)
    data = await request.json()

    memory_text = data.get("text", "").strip()
    if not memory_text:
        raise HTTPException(400, "Memory text is required")

    engine_ref = _get_engine(request)
    if not engine_ref:
        raise HTTPException(503, "Engine not available")

    memory_service = engine_ref.get_memory_service(APP_SLUG)
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
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Inject memory error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.delete("/api/memories/{memory_id}")
async def delete_memory(memory_id: str, request: Request):
    """Delete a specific memory."""
    user = require_user(request)

    engine_ref = _get_engine(request)
    if not engine_ref:
        raise HTTPException(503, "Engine not available")

    memory_service = engine_ref.get_memory_service(APP_SLUG)
    if not memory_service:
        raise HTTPException(503, "Memory service not available")

    try:
        if hasattr(memory_service, "delete"):
            await memory_service.delete(memory_id=memory_id, user_id=user["email"])
        else:
            from bson import ObjectId

            scoped_db = await engine_ref.get_scoped_db(APP_SLUG)
            await scoped_db.member_memories.delete_one(
                {"_id": ObjectId(memory_id), "user_id": user["email"]}
            )
        return JSONResponse({"success": True})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Delete memory error: %s", e, exc_info=True)
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

    engine_ref = _get_engine(request)
    if not engine_ref:
        raise HTTPException(503, "Engine not available")

    memory_service = engine_ref.get_memory_service(APP_SLUG)
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
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Search error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


# ============================================================================
# MEMORY ANALYTICS
# ============================================================================


@app.get("/api/analytics")
async def get_analytics(request: Request):
    """Get memory health analytics for the current user."""
    user = require_user(request)
    user_id = user["email"]

    engine_ref = _get_engine(request)
    if not engine_ref:
        raise HTTPException(503, "Engine not available")

    memory_service = engine_ref.get_memory_service(APP_SLUG)
    if not memory_service:
        raise HTTPException(503, "Memory service not available")

    try:
        # Use the memory service's collection directly to avoid scoped DB
        # app_id filter mismatch (memory service writes without app_id)
        mem_col = memory_service.collection

        active_count = await mem_col.count_documents(
            {"user_id": user_id, "is_active": {"$ne": False}}
        )
        cold_count = await mem_col.count_documents(
            {"user_id": user_id, "is_active": False}
        )

        # Category lives inside metadata.category (set by inject)
        cat_pipeline = [
            {"$match": {"user_id": user_id, "is_active": {"$ne": False}}},
            {"$group": {
                "_id": {"$ifNull": ["$metadata.category", "unknown"]},
                "count": {"$sum": 1},
            }},
        ]
        categories = {}
        async for doc in mem_col.aggregate(cat_pipeline):
            cat = doc["_id"] or "unknown"
            categories[cat] = doc["count"]

        now = datetime.now(timezone.utc)
        strength_pipeline = [
            {"$match": {"user_id": user_id, "is_active": {"$ne": False}}},
            {
                "$addFields": {
                    "t_hours": {
                        "$divide": [
                            {"$subtract": [now, "$created_at"]},
                            3600000,
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
        async for doc in mem_col.aggregate(strength_pipeline):
            avg_strength = doc.get("avg_strength", 0)
            weak_count = doc.get("weak_count", 0)
            strong_count = doc.get("strong_count", 0)

        # Graph count via scoped DB (graph nodes DO have app_slug)
        scoped_db = await engine_ref.get_scoped_db(APP_SLUG)
        graph_count = 0
        try:
            kg_col = scoped_db["__kg"]
            graph_count = await kg_col.count_documents({"app_slug": APP_SLUG})
        except (PyMongoError, ValueError):
            pass

        return JSONResponse(
            {
                "active_memories": active_count,
                "cold_storage_memories": cold_count,
                "capacity_used": round(active_count / 1000, 2),
                "average_strength": round(avg_strength, 3) if avg_strength else 0,
                "weak_memories": weak_count,
                "strong_memories": strong_count,
                "categories": categories,
                "graph_nodes": graph_count,
            }
        )
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Analytics error: %s", e, exc_info=True)
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
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Graph nodes error: %s", e, exc_info=True)
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
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Graph traverse error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


# ============================================================================
# COLD STORAGE
# ============================================================================


@app.get("/api/cold-storage")
async def get_cold_storage(request: Request):
    """View pruned/soft-deleted memories in cold storage."""
    user = require_user(request)

    engine_ref = _get_engine(request)
    if not engine_ref:
        raise HTTPException(503, "Engine not available")

    memory_service = engine_ref.get_memory_service(APP_SLUG)
    if not memory_service:
        raise HTTPException(503, "Memory service not available")

    try:
        mem_col = memory_service.collection
        cursor = (
            mem_col.find({"user_id": user["email"], "is_active": False})
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
                    "category": doc.get("metadata", {}).get("category", "unknown"),
                    "pruned_at": str(doc.get("pruned_at", "")),
                    "pruning_reason": doc.get("pruning_reason", "unknown"),
                    "created_at": str(doc.get("created_at", "")),
                }
            )

        return JSONResponse({"memories": memories, "count": len(memories)})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Cold storage error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.post("/api/cold-storage/{memory_id}/restore")
async def restore_from_cold_storage(memory_id: str, request: Request):
    """Restore a memory from cold storage."""
    user = require_user(request)

    engine_ref = _get_engine(request)
    if not engine_ref:
        raise HTTPException(503, "Engine not available")

    memory_service = engine_ref.get_memory_service(APP_SLUG)
    if not memory_service:
        raise HTTPException(503, "Memory service not available")

    try:
        from bson import ObjectId

        mem_col = memory_service.collection
        result = await mem_col.update_one(
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
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Restore error: %s", e, exc_info=True)
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

    engine_ref = _get_engine(request)
    if not engine_ref:
        raise HTTPException(503, "Engine not available")

    memory_service = engine_ref.get_memory_service(APP_SLUG)
    if not memory_service:
        raise HTTPException(503, "Memory service not available")

    try:
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
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Conflict check error: %s", e, exc_info=True)
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
        engine_ref = _get_engine(request)
        motor_client = engine_ref._connection_manager.mongo_client
        mflix_db = motor_client["sample_mflix"]

        if not query:
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
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Mflix actors error: %s", e, exc_info=True)
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
    """Run $graphLookup on sample_mflix.movies to traverse cast relationships."""
    user = require_user(request)
    data = await request.json()

    actor_name = data.get("actor", "").strip()
    if not actor_name:
        raise HTTPException(400, "Actor name is required")

    max_depth = min(int(data.get("max_depth", 1)), 3)

    try:
        engine_ref = _get_engine(request)
        motor_client = engine_ref._connection_manager.mongo_client
        mflix_db = motor_client["sample_mflix"]

        pipeline = [
            {"$match": {"cast": {"$regex": f"^{actor_name}$", "$options": "i"}}},
            {"$limit": 10},
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
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Mflix graphLookup error: %s", e, exc_info=True)
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
# PERSONA API
# ============================================================================


@app.get("/api/persona")
async def get_persona(request: Request):
    """Get current persona configuration (role, description, traits)."""
    require_user(request)

    engine_ref = _get_engine(request)
    if not engine_ref:
        raise HTTPException(503, "Engine not available")

    memory_service = engine_ref.get_memory_service(APP_SLUG)
    if not memory_service or not hasattr(memory_service, "persona_engine") or not memory_service.persona_engine:
        return JSONResponse({"success": False, "error": "Persona feature not enabled"})

    try:
        persona = await memory_service.get_persona()
        safe_persona = {}
        if persona:
            for k, v in persona.items():
                if k == "_id":
                    safe_persona[k] = str(v)
                elif hasattr(v, "isoformat"):
                    safe_persona[k] = v.isoformat()
                else:
                    safe_persona[k] = v
        return JSONResponse({"success": True, "persona": safe_persona})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Get persona error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.put("/api/persona")
async def update_persona(request: Request):
    """Update persona traits to customize Member Berry's personality."""
    require_user(request)
    data = await request.json()

    engine_ref = _get_engine(request)
    if not engine_ref:
        raise HTTPException(503, "Engine not available")

    memory_service = engine_ref.get_memory_service(APP_SLUG)
    if not memory_service or not hasattr(memory_service, "persona_engine") or not memory_service.persona_engine:
        raise HTTPException(400, "Persona feature not enabled")

    try:
        updated = await memory_service.update_persona(
            role=data.get("role"),
            description=data.get("description"),
            traits=data.get("traits"),
        )
        safe_updated = {}
        if updated:
            for k, v in updated.items():
                if k == "_id":
                    safe_updated[k] = str(v)
                elif hasattr(v, "isoformat"):
                    safe_updated[k] = v.isoformat()
                else:
                    safe_updated[k] = v
        return JSONResponse({"success": True, "persona": safe_updated})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Update persona error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


# ============================================================================
# DEMO SEED (core logic + auto-seed on first visit)
# ============================================================================


async def _seed_for_user(user_id: str, engine_ref) -> dict:
    """Core seed logic — used by both the API endpoint and auto-seed on first visit.

    Returns a dict of counts for each category seeded.
    """
    memory_service = engine_ref.get_memory_service(APP_SLUG)
    if not memory_service:
        return {"error": "Memory service not available"}

    seeded = {"memories": 0, "graph_nodes": 0, "mflix_movies": 0, "episodes": 0, "procedures": 0, "triggers": 0, "timelines": 0}

    # ------------------------------------------------------------------
    # 1. Sample memories (movie-themed, with varying ages for decay demo)
    # ------------------------------------------------------------------
    sample_memories = [
        {
            "text": "Just watched Oppenheimer -- Nolan outdid himself, the Trinity test scene was breathtaking",
            "metadata": {"importance": 0.95, "emotion": 0.9, "category": "reviews"},
            "age_hours": 0,
        },
        {
            "text": "Tom Hanks is my all-time favorite actor. Forrest Gump changed how I see movies",
            "metadata": {"importance": 0.9, "emotion": 0.7, "category": "favorites"},
            "age_hours": 6,
        },
        {
            "text": "Rewatched The Shawshank Redemption for the third time -- still cried at the ending",
            "metadata": {"importance": 0.85, "emotion": 0.8, "category": "reviews"},
            "age_hours": 48,
        },
        {
            "text": "Kevin Bacon in Apollo 13 with Tom Hanks -- six degrees of separation is real",
            "metadata": {"importance": 0.7, "emotion": 0.5, "category": "actors"},
            "age_hours": 72,
        },
        {
            "text": "I prefer sci-fi and drama over horror. Jump scares are cheap, give me existential dread",
            "metadata": {"importance": 0.6, "emotion": 0.2, "category": "preferences"},
            "age_hours": 24,
        },
        {
            "text": "The Princess Bride is my comfort movie. 'As you wish' -- perfection",
            "metadata": {"importance": 0.5, "emotion": 0.7, "category": "nostalgia"},
            "age_hours": 120,
        },
        {
            "text": "Saw Se7en on a date night -- terrible choice, incredible movie. Brad Pitt was unhinged",
            "metadata": {"importance": 0.6, "emotion": 0.6, "category": "experiences"},
            "age_hours": 168,
        },
        {
            "text": "Good Will Hunting -- Robin Williams' park bench scene is the best acting ever committed to film",
            "metadata": {"importance": 0.5, "emotion": 0.8, "category": "reviews"},
            "age_hours": 200,
        },
    ]

    # ------------------------------------------------------------------
    # 2. Sample graph nodes (movie-centric knowledge graph)
    # ------------------------------------------------------------------
    sample_nodes = [
        # Actors
        {"_id": "actor:tom_hanks", "type": "actor", "label": "Tom Hanks", "edges": [
            {"relation": "starred_in", "target": "movie:forrest_gump", "active": True},
            {"relation": "starred_in", "target": "movie:apollo_13", "active": True},
            {"relation": "starred_in", "target": "movie:cast_away", "active": True},
        ]},
        {"_id": "actor:kevin_bacon", "type": "actor", "label": "Kevin Bacon", "edges": [
            {"relation": "starred_in", "target": "movie:apollo_13", "active": True},
            {"relation": "starred_in", "target": "movie:mystic_river", "active": True},
            {"relation": "starred_in", "target": "movie:a_few_good_men", "active": True},
        ]},
        {"_id": "actor:brad_pitt", "type": "actor", "label": "Brad Pitt", "edges": [
            {"relation": "starred_in", "target": "movie:se7en", "active": True},
            {"relation": "starred_in", "target": "movie:fight_club", "active": True},
        ]},
        {"_id": "actor:robin_williams", "type": "actor", "label": "Robin Williams", "edges": [
            {"relation": "starred_in", "target": "movie:good_will_hunting", "active": True},
        ]},
        {"_id": "actor:morgan_freeman", "type": "actor", "label": "Morgan Freeman", "edges": [
            {"relation": "starred_in", "target": "movie:shawshank_redemption", "active": True},
            {"relation": "starred_in", "target": "movie:se7en", "active": True},
        ]},
        {"_id": "actor:matt_damon", "type": "actor", "label": "Matt Damon", "edges": [
            {"relation": "starred_in", "target": "movie:good_will_hunting", "active": True},
            {"relation": "starred_in", "target": "movie:saving_private_ryan", "active": True},
        ]},
        # Movies
        {"_id": "movie:forrest_gump", "type": "movie", "label": "Forrest Gump", "edges": [
            {"relation": "genre", "target": "genre:drama", "active": True},
        ]},
        {"_id": "movie:apollo_13", "type": "movie", "label": "Apollo 13", "edges": [
            {"relation": "genre", "target": "genre:drama", "active": True},
            {"relation": "directed_by", "target": "director:ron_howard", "active": True},
        ]},
        {"_id": "movie:shawshank_redemption", "type": "movie", "label": "The Shawshank Redemption", "edges": [
            {"relation": "genre", "target": "genre:drama", "active": True},
        ]},
        {"_id": "movie:oppenheimer", "type": "movie", "label": "Oppenheimer", "edges": [
            {"relation": "genre", "target": "genre:drama", "active": True},
            {"relation": "genre", "target": "genre:sci_fi", "active": True},
            {"relation": "directed_by", "target": "director:nolan", "active": True},
        ]},
        {"_id": "movie:princess_bride", "type": "movie", "label": "The Princess Bride", "edges": [
            {"relation": "genre", "target": "genre:comedy", "active": True},
        ]},
        {"_id": "movie:se7en", "type": "movie", "label": "Se7en", "edges": [
            {"relation": "genre", "target": "genre:thriller", "active": True},
        ]},
        {"_id": "movie:good_will_hunting", "type": "movie", "label": "Good Will Hunting", "edges": [
            {"relation": "genre", "target": "genre:drama", "active": True},
        ]},
        # Directors
        {"_id": "director:nolan", "type": "director", "label": "Christopher Nolan", "edges": [
            {"relation": "directed", "target": "movie:oppenheimer", "active": True},
        ]},
        {"_id": "director:ron_howard", "type": "director", "label": "Ron Howard", "edges": [
            {"relation": "directed", "target": "movie:apollo_13", "active": True},
        ]},
        # Genres
        {"_id": "genre:drama", "type": "genre", "label": "Drama", "edges": []},
        {"_id": "genre:sci_fi", "type": "genre", "label": "Sci-Fi", "edges": []},
        {"_id": "genre:comedy", "type": "genre", "label": "Comedy", "edges": []},
        {"_id": "genre:thriller", "type": "genre", "label": "Thriller", "edges": []},
        # User preferences node
        {"_id": "person:user_prefs", "type": "person", "label": "My Preferences", "edges": [
            {"relation": "favorite_actor", "target": "actor:tom_hanks", "active": True},
            {"relation": "favorite_genre", "target": "genre:drama", "active": True},
            {"relation": "favorite_genre", "target": "genre:sci_fi", "active": True},
            {"relation": "comfort_movie", "target": "movie:princess_bride", "active": True},
        ]},
    ]

    # ------------------------------------------------------------------
    # 3. Sample mflix movies (overlapping casts for $graphLookup demo)
    # ------------------------------------------------------------------
    sample_mflix_movies = [
        {"title": "Forrest Gump", "year": 1994, "cast": ["Tom Hanks", "Robin Wright", "Gary Sinise", "Sally Field"], "genres": ["Drama", "Romance"], "_seeded_by": "member_demo"},
        {"title": "Cast Away", "year": 2000, "cast": ["Tom Hanks", "Helen Hunt", "Nick Searcy"], "genres": ["Adventure", "Drama"], "_seeded_by": "member_demo"},
        {"title": "You've Got Mail", "year": 1998, "cast": ["Tom Hanks", "Meg Ryan", "Greg Kinnear", "Parker Posey"], "genres": ["Comedy", "Romance"], "_seeded_by": "member_demo"},
        {"title": "Sleepless in Seattle", "year": 1993, "cast": ["Tom Hanks", "Meg Ryan", "Bill Pullman", "Rosie O'Donnell"], "genres": ["Comedy", "Drama", "Romance"], "_seeded_by": "member_demo"},
        {"title": "Apollo 13", "year": 1995, "cast": ["Tom Hanks", "Kevin Bacon", "Bill Paxton", "Gary Sinise", "Ed Harris"], "genres": ["Adventure", "Drama", "History"], "_seeded_by": "member_demo"},
        {"title": "The Green Mile", "year": 1999, "cast": ["Tom Hanks", "Michael Clarke Duncan", "Sam Rockwell", "Barry Pepper"], "genres": ["Crime", "Drama", "Fantasy"], "_seeded_by": "member_demo"},
        {"title": "Saving Private Ryan", "year": 1998, "cast": ["Tom Hanks", "Matt Damon", "Tom Sizemore", "Barry Pepper", "Vin Diesel"], "genres": ["Drama", "War"], "_seeded_by": "member_demo"},
        {"title": "A Few Good Men", "year": 1992, "cast": ["Tom Cruise", "Jack Nicholson", "Demi Moore", "Kevin Bacon"], "genres": ["Drama", "Thriller"], "_seeded_by": "member_demo"},
        {"title": "Footloose", "year": 1984, "cast": ["Kevin Bacon", "Lori Singer", "John Lithgow", "Dianne Wiest"], "genres": ["Drama", "Music", "Romance"], "_seeded_by": "member_demo"},
        {"title": "Mystic River", "year": 2003, "cast": ["Sean Penn", "Tim Robbins", "Kevin Bacon", "Laurence Fishburne"], "genres": ["Crime", "Drama", "Mystery"], "_seeded_by": "member_demo"},
        {"title": "The Shawshank Redemption", "year": 1994, "cast": ["Tim Robbins", "Morgan Freeman", "Bob Gunton", "William Sadler"], "genres": ["Drama"], "_seeded_by": "member_demo"},
        {"title": "Good Will Hunting", "year": 1997, "cast": ["Robin Williams", "Matt Damon", "Ben Affleck", "Minnie Driver"], "genres": ["Drama", "Romance"], "_seeded_by": "member_demo"},
        {"title": "The Bourne Identity", "year": 2002, "cast": ["Matt Damon", "Franka Potente", "Chris Cooper", "Julia Stiles"], "genres": ["Action", "Mystery", "Thriller"], "_seeded_by": "member_demo"},
        {"title": "Ocean's Eleven", "year": 2001, "cast": ["George Clooney", "Brad Pitt", "Matt Damon", "Julia Roberts"], "genres": ["Crime", "Thriller"], "_seeded_by": "member_demo"},
        {"title": "Fight Club", "year": 1999, "cast": ["Brad Pitt", "Edward Norton", "Helena Bonham Carter", "Meat Loaf"], "genres": ["Drama"], "_seeded_by": "member_demo"},
        {"title": "Se7en", "year": 1995, "cast": ["Brad Pitt", "Morgan Freeman", "Gwyneth Paltrow", "Kevin Spacey"], "genres": ["Crime", "Drama", "Mystery"], "_seeded_by": "member_demo"},
        {"title": "The Princess Bride", "year": 1987, "cast": ["Robin Wright", "Cary Elwes", "Mandy Patinkin", "Billy Crystal"], "genres": ["Adventure", "Comedy", "Fantasy"], "_seeded_by": "member_demo"},
        {"title": "When Harry Met Sally", "year": 1989, "cast": ["Meg Ryan", "Billy Crystal", "Carrie Fisher", "Bruno Kirby"], "genres": ["Comedy", "Drama", "Romance"], "_seeded_by": "member_demo"},
    ]

    mem_col = memory_service.collection

    try:
        # --- Seed memories (with varying ages for decay demo) ---
        for mem in sample_memories:
            try:
                result = await memory_service.inject(
                    memory=mem["text"],
                    user_id=user_id,
                    metadata=mem["metadata"],
                    bucket_id=f"category:personal:{user_id}",
                    bucket_type="category",
                )
                seeded["memories"] += 1

                # Backdate to simulate age for decay demonstration
                age_hours = mem.get("age_hours", 0)
                if age_hours > 0 and result and result.get("id"):
                    from bson import ObjectId

                    backdated = datetime.now(timezone.utc) - timedelta(hours=age_hours)
                    await mem_col.update_one(
                        {"_id": ObjectId(result["id"])},
                        {"$set": {"created_at": backdated, "last_accessed": backdated}},
                    )
            except (PyMongoError, ValueError) as e:
                logger.warning("Failed to seed memory: %s", e)

        # --- Seed graph nodes directly (no dependency on graph service) ---
        try:
            scoped_db = await engine_ref.get_scoped_db(APP_SLUG)
            kg_collection = scoped_db["__kg"]

            for node in sample_nodes:
                try:
                    node["app_slug"] = APP_SLUG
                    node["user_id"] = user_id
                    node["created_at"] = datetime.now(timezone.utc)
                    await kg_collection.update_one(
                        {"_id": node["_id"], "user_id": user_id},
                        {"$set": node},
                        upsert=True,
                    )
                    seeded["graph_nodes"] += 1
                except (PyMongoError, ValueError) as e:
                    logger.warning("Failed to seed graph node: %s", e)
        except (PyMongoError, ValueError) as e:
            logger.warning("Failed to get scoped DB for graph seeding: %s", e)

        # --- Seed sample_mflix movies for $graphLookup demo ---
        try:
            motor_client = engine_ref._connection_manager.mongo_client
            mflix_db = motor_client["sample_mflix"]

            for movie in sample_mflix_movies:
                try:
                    await mflix_db.movies.update_one(
                        {"title": movie["title"], "_seeded_by": "member_demo"},
                        {"$set": movie},
                        upsert=True,
                    )
                    seeded["mflix_movies"] += 1
                except (PyMongoError, ValueError) as e:
                    logger.warning("Failed to seed mflix movie '%s': %s", movie["title"], e)
        except (PyMongoError, ValueError) as e:
            logger.warning("Failed to seed mflix movies: %s", e)

        # --- Seed episodic memories ---
        if episodic_collection:
            sample_episodes = [
                {"text": "Watched Oppenheimer at the IMAX downtown with friends — the sound design was overwhelming", "emotion": 0.9, "context": "movie night", "tags": ["imax", "friends", "oppenheimer"]},
                {"text": "Had a movie marathon on a rainy Sunday: Princess Bride → Good Will Hunting → Shawshank", "emotion": 0.7, "context": "lazy day", "tags": ["marathon", "comfort", "rainy"]},
                {"text": "Debated best Nolan film at dinner — I argued Interstellar, everyone else said Dark Knight", "emotion": 0.5, "context": "dinner party", "tags": ["nolan", "debate", "friends"]},
            ]
            for ep in sample_episodes:
                try:
                    ep_doc = {
                        "user_id": user_id, "app_slug": APP_SLUG,
                        "text": ep["text"], "emotion": ep["emotion"],
                        "context": ep["context"], "tags": ep["tags"],
                        "memory_type": "episodic",
                        "created_at": datetime.now(timezone.utc) - timedelta(hours=len(sample_episodes) * 12),
                    }
                    await episodic_collection.insert_one(ep_doc)
                    seeded["episodes"] += 1
                except (PyMongoError, ValueError) as e:
                    logger.warning("Failed to seed episode: %s", e)

        # --- Seed procedural memories ---
        if procedural_collection:
            sample_procedures = [
                {
                    "name": "How to pick a movie for date night",
                    "description": "A foolproof process for choosing a crowd-pleasing date-night film",
                    "steps": ["Check partner's mood (action vs cozy)", "Eliminate anything over 2.5 hours", "Pick from the overlap of both watchlists", "Fallback: The Princess Bride"],
                    "category": "movie_selection",
                },
                {
                    "name": "Six Degrees of Kevin Bacon",
                    "description": "How to trace any actor back to Kevin Bacon",
                    "steps": ["Pick any actor", "Find a movie they share with another actor", "Repeat until you reach Kevin Bacon", "Count the links (most are 3 or fewer)"],
                    "category": "trivia",
                },
            ]
            for proc in sample_procedures:
                try:
                    proc_doc = {
                        "user_id": user_id, "app_slug": APP_SLUG,
                        "name": proc["name"], "description": proc["description"],
                        "steps": proc["steps"], "category": proc["category"],
                        "memory_type": "procedural", "version": 1,
                        "created_at": datetime.now(timezone.utc),
                        "updated_at": datetime.now(timezone.utc),
                    }
                    await procedural_collection.insert_one(proc_doc)
                    seeded["procedures"] += 1
                except (PyMongoError, ValueError) as e:
                    logger.warning("Failed to seed procedure: %s", e)

        # --- Seed prospective triggers ---
        if prospective_memory:
            sample_triggers = [
                {"condition": "new Christopher Nolan movie announced", "action": "Tell me immediately — I want to see it opening weekend!"},
                {"condition": "someone mentions a Tom Hanks movie I haven't seen", "action": "Add it to my watchlist and remind me this weekend"},
            ]
            for trig in sample_triggers:
                try:
                    await prospective_memory.set_trigger(
                        condition=trig["condition"],
                        action=trig["action"],
                        user_id=user_id,
                        one_shot=False,
                    )
                    seeded["triggers"] += 1
                except (PyMongoError, ValueError) as e:
                    logger.warning("Failed to seed trigger: %s", e)

        # --- Seed a forked timeline ---
        if timeline_service:
            demo_timelines = [
                "What-If: Only Sci-Fi",
                "What-If: Horror Fan",
                "What-If: 90s Nostalgia Only",
            ]
            for tl_name in demo_timelines:
                try:
                    root = await timeline_service.get_active_timeline(user_id)
                    await timeline_service.fork_timeline(
                        current_timeline=root,
                        new_name=tl_name,
                        user_id=user_id,
                        app_slug=APP_SLUG,
                    )
                    seeded["timelines"] += 1
                except (PyMongoError, ValueError) as e:
                    logger.warning("Failed to seed timeline '%s': %s", tl_name, e)

        return seeded
    except Exception as e:
        logger.error("Seed error: %s", e, exc_info=True)
        return {"error": str(e)}


# Track which users have been auto-seeded this process lifetime
_auto_seeded_users: set[str] = set()


async def _auto_seed_if_empty(user_id: str, engine_ref) -> None:
    """Auto-seed demo data on first visit if user has no memories."""
    if user_id in _auto_seeded_users:
        return
    _auto_seeded_users.add(user_id)

    memory_service = engine_ref.get_memory_service(APP_SLUG)
    if not memory_service:
        return

    try:
        count = await memory_service.collection.count_documents(
            {"user_id": user_id, "is_active": {"$ne": False}}
        )
        if count > 0:
            return

        logger.info("Auto-seeding demo data for new user: %s", user_id)
        result = await _seed_for_user(user_id, engine_ref)
        logger.info("Auto-seed complete for %s: %s", user_id, result)
    except Exception as e:
        logger.warning("Auto-seed failed for %s (non-fatal): %s", user_id, e)


@app.post("/api/demo/seed")
async def seed_demo_data(request: Request):
    """Pre-populate sample memories, graph nodes, and mflix movies for demonstration."""
    user = require_user(request)
    engine_ref = _get_engine(request)
    if not engine_ref:
        raise HTTPException(503, "Engine not available")

    seeded = await _seed_for_user(user["email"], engine_ref)
    if "error" in seeded:
        raise HTTPException(503, seeded["error"])

    return JSONResponse(
        {
            "success": True,
            "seeded": seeded,
            "message": (
                f"Seeded {seeded['memories']} memories, "
                f"{seeded['graph_nodes']} graph nodes, "
                f"{seeded['mflix_movies']} movies, "
                f"{seeded['episodes']} episodes, "
                f"{seeded['procedures']} procedures, "
                f"{seeded['triggers']} triggers, "
                f"{seeded['timelines']} timelines. Oh, I member!"
            ),
        }
    )


@app.post("/api/demo/reset")
async def reset_demo_data(request: Request):
    """Clear all demo data for the current user."""
    user = require_user(request)
    user_id = user["email"]

    engine_ref = _get_engine(request)
    if not engine_ref:
        raise HTTPException(503, "Engine not available")

    try:
        # Use memory service collection directly (same as analytics fix)
        memory_service = engine_ref.get_memory_service(APP_SLUG)
        mem_col = memory_service.collection if memory_service else None
        mem_result_count = 0
        if mem_col:
            mem_result = await mem_col.delete_many({"user_id": user_id})
            mem_result_count = mem_result.deleted_count

        # Graph nodes via scoped DB (they have app_slug)
        scoped_db = await engine_ref.get_scoped_db(APP_SLUG)
        kg_result = await scoped_db["__kg"].delete_many(
            {"app_slug": APP_SLUG, "user_id": user_id}
        )
        chat_result = await scoped_db.chat_history.delete_many({"user_id": user_id})

        # Clean up seeded mflix movies (only those tagged by member_demo)
        mflix_deleted = 0
        try:
            motor_client = engine_ref._connection_manager.mongo_client
            if motor_client:
                mflix_db = motor_client["sample_mflix"]
                mflix_result = await mflix_db.movies.delete_many(
                    {"_seeded_by": "member_demo"}
                )
                mflix_deleted = mflix_result.deleted_count
        except PyMongoError as e:
            logger.warning("Failed to clean mflix seed data: %s", e)

        # Clean up new feature collections
        ep_deleted = proc_deleted = trig_deleted = tl_deleted = wm_deleted = prof_deleted = 0
        try:
            if episodic_collection:
                r = await episodic_collection.delete_many({"user_id": user_id})
                ep_deleted = r.deleted_count
            if procedural_collection:
                r = await procedural_collection.delete_many({"user_id": user_id})
                proc_deleted = r.deleted_count
            # Prospective triggers
            trig_col = scoped_db["prospective_triggers"]
            r = await trig_col.delete_many({"user_id": user_id})
            trig_deleted = r.deleted_count
            # Timelines
            tl_col = scoped_db["timelines"]
            r = await tl_col.delete_many({"user_id": user_id})
            tl_deleted = r.deleted_count
            # Working memory
            wm_col = scoped_db["working_memory"]
            r = await wm_col.delete_many({"user_id": user_id})
            wm_deleted = r.deleted_count
            # User profile
            prof_col = scoped_db["user_profiles"]
            r = await prof_col.delete_many({"user_id": user_id})
            prof_deleted = r.deleted_count
        except PyMongoError as e:
            logger.warning("Failed to clean new feature data: %s", e)

        return JSONResponse(
            {
                "success": True,
                "deleted": {
                    "memories": mem_result_count,
                    "graph_nodes": kg_result.deleted_count,
                    "chat_messages": chat_result.deleted_count,
                    "mflix_movies": mflix_deleted,
                    "episodes": ep_deleted,
                    "procedures": proc_deleted,
                    "triggers": trig_deleted,
                    "timelines": tl_deleted,
                    "working_memory": wm_deleted,
                    "profiles": prof_deleted,
                },
            }
        )
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Reset error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.post("/api/demo/simulate-decay")
async def simulate_decay(request: Request):
    """Fast-forward time to demonstrate Ebbinghaus memory decay."""
    user = require_user(request)
    user_id = user["email"]
    data = await request.json()

    hours = min(int(data.get("hours", 24)), 720)
    if hours <= 0:
        raise HTTPException(400, "Hours must be positive")

    engine_ref = _get_engine(request)
    if not engine_ref:
        raise HTTPException(503, "Engine not available")

    memory_service = engine_ref.get_memory_service(APP_SLUG)
    if not memory_service:
        raise HTTPException(503, "Memory service not available")

    try:
        mem_col = memory_service.collection
        ms_offset = hours * 3600000

        result = await mem_col.update_many(
            {"user_id": user_id, "is_active": {"$ne": False}},
            [{"$set": {
                "created_at": {"$subtract": ["$created_at", ms_offset]},
                "last_accessed": {"$subtract": [
                    {"$ifNull": ["$last_accessed", "$created_at"]},
                    ms_offset,
                ]},
            }}],
        )

        return JSONResponse({
            "success": True,
            "affected": result.modified_count,
            "hours": hours,
            "message": f"Fast-forwarded {hours}h on {result.modified_count} memories",
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Simulate decay error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.post("/api/demo/force-prune")
async def force_prune(request: Request):
    """Force-prune weak memories to cold storage based on Ebbinghaus strength."""
    user = require_user(request)
    user_id = user["email"]
    data = await request.json()

    threshold = float(data.get("threshold", 0.3))

    engine_ref = _get_engine(request)
    if not engine_ref:
        raise HTTPException(503, "Engine not available")

    memory_service = engine_ref.get_memory_service(APP_SLUG)
    if not memory_service:
        raise HTTPException(503, "Memory service not available")

    try:
        mem_col = memory_service.collection
        now = datetime.now(timezone.utc)

        cursor = mem_col.find(
            {"user_id": user_id, "is_active": {"$ne": False}},
            {"_id": 1, "importance": 1, "created_at": 1, "stability": 1},
        )

        ids_to_prune = []
        async for doc in cursor:
            created = doc.get("created_at")
            if isinstance(created, datetime):
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                t_hours = (now - created).total_seconds() / 3600
            else:
                t_hours = 0

            importance = doc.get("importance", 0.5)
            stability = doc.get("stability", 48)
            strength = importance * math.exp(-t_hours / max(stability, 1))

            if strength < threshold:
                ids_to_prune.append(doc["_id"])

        pruned = 0
        if ids_to_prune:
            result = await mem_col.update_many(
                {"_id": {"$in": ids_to_prune}},
                {"$set": {
                    "is_active": False,
                    "pruned_at": now,
                    "pruning_reason": "ebbinghaus_decay",
                }},
            )
            pruned = result.modified_count

        return JSONResponse({
            "success": True,
            "pruned": pruned,
            "threshold": threshold,
            "message": (
                f"Pruned {pruned} memories below {threshold:.0%} strength to cold storage"
                if pruned > 0
                else "No memories below the threshold -- try simulating more time first"
            ),
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Force prune error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


# ============================================================================
# MEMORY TYPES (episodic, procedural, working)
# ============================================================================


@app.post("/api/memories/episodic")
async def record_episode(request: Request):
    """Record an episodic memory (time-stamped event)."""
    user = require_user(request)
    if not episodic_collection:
        raise HTTPException(503, "Episodic memory not available")

    data = await request.json()
    episode_text = data.get("text", "").strip()
    if not episode_text:
        raise HTTPException(400, "Episode text is required")

    try:
        doc = {
            "user_id": user["email"],
            "app_slug": APP_SLUG,
            "text": episode_text,
            "emotion": data.get("emotion", 0.5),
            "context": data.get("context", ""),
            "tags": data.get("tags", []),
            "memory_type": "episodic",
            "created_at": datetime.now(timezone.utc),
        }
        result = await episodic_collection.insert_one(doc)
        return JSONResponse(
            {"success": True, "episode_id": str(result.inserted_id), "text": episode_text},
            status_code=201,
        )
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Record episode error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.get("/api/memories/episodic")
async def list_episodes(request: Request):
    """Query episodic memories with optional tag/date filters."""
    user = require_user(request)
    if not episodic_collection:
        raise HTTPException(503, "Episodic memory not available")

    tag = request.query_params.get("tag")
    limit = min(int(request.query_params.get("limit", "50")), 100)

    try:
        query: dict[str, Any] = {"user_id": user["email"]}
        if tag:
            query["tags"] = tag

        cursor = episodic_collection.find(query).sort("created_at", -1).limit(limit)
        episodes = []
        async for doc in cursor:
            episodes.append({
                "id": str(doc["_id"]),
                "text": doc.get("text", ""),
                "emotion": doc.get("emotion", 0),
                "context": doc.get("context", ""),
                "tags": doc.get("tags", []),
                "created_at": str(doc.get("created_at", "")),
            })
        return JSONResponse({"episodes": episodes, "count": len(episodes)})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("List episodes error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.get("/api/memories/episodic/{episode_id}")
async def get_episode(episode_id: str, request: Request):
    """Get a specific episodic memory."""
    user = require_user(request)
    if not episodic_collection:
        raise HTTPException(503, "Episodic memory not available")

    try:
        from bson import ObjectId

        doc = await episodic_collection.find_one(
            {"_id": ObjectId(episode_id), "user_id": user["email"]}
        )
        if not doc:
            raise HTTPException(404, "Episode not found")
        return JSONResponse({
            "id": str(doc["_id"]),
            "text": doc.get("text", ""),
            "emotion": doc.get("emotion", 0),
            "context": doc.get("context", ""),
            "tags": doc.get("tags", []),
            "created_at": str(doc.get("created_at", "")),
        })
    except HTTPException:
        raise
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Get episode error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.post("/api/memories/procedural")
async def store_procedure(request: Request):
    """Store a procedural memory (skill, workflow, how-to)."""
    user = require_user(request)
    if not procedural_collection:
        raise HTTPException(503, "Procedural memory not available")

    data = await request.json()
    name = data.get("name", "").strip()
    steps = data.get("steps", [])
    if not name:
        raise HTTPException(400, "Procedure name is required")

    try:
        doc = {
            "user_id": user["email"],
            "app_slug": APP_SLUG,
            "name": name,
            "description": data.get("description", ""),
            "steps": steps,
            "category": data.get("category", "general"),
            "memory_type": "procedural",
            "version": 1,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        result = await procedural_collection.insert_one(doc)
        return JSONResponse(
            {"success": True, "procedure_id": str(result.inserted_id), "name": name},
            status_code=201,
        )
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Store procedure error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.get("/api/memories/procedural")
async def list_procedures(request: Request):
    """Query procedural memories."""
    user = require_user(request)
    if not procedural_collection:
        raise HTTPException(503, "Procedural memory not available")

    category = request.query_params.get("category")
    limit = min(int(request.query_params.get("limit", "50")), 100)

    try:
        query: dict[str, Any] = {"user_id": user["email"]}
        if category:
            query["category"] = category

        cursor = procedural_collection.find(query).sort("updated_at", -1).limit(limit)
        procedures = []
        async for doc in cursor:
            procedures.append({
                "id": str(doc["_id"]),
                "name": doc.get("name", ""),
                "description": doc.get("description", ""),
                "steps": doc.get("steps", []),
                "category": doc.get("category", "general"),
                "version": doc.get("version", 1),
                "created_at": str(doc.get("created_at", "")),
                "updated_at": str(doc.get("updated_at", "")),
            })
        return JSONResponse({"procedures": procedures, "count": len(procedures)})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("List procedures error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.put("/api/memories/procedural/{procedure_id}")
async def update_procedure(procedure_id: str, request: Request):
    """Update a procedural memory (increments version)."""
    user = require_user(request)
    if not procedural_collection:
        raise HTTPException(503, "Procedural memory not available")

    data = await request.json()

    try:
        from bson import ObjectId

        update_fields: dict[str, Any] = {"updated_at": datetime.now(timezone.utc)}
        if "steps" in data:
            update_fields["steps"] = data["steps"]
        if "description" in data:
            update_fields["description"] = data["description"]
        if "name" in data:
            update_fields["name"] = data["name"]

        result = await procedural_collection.update_one(
            {"_id": ObjectId(procedure_id), "user_id": user["email"]},
            {"$set": update_fields, "$inc": {"version": 1}},
        )
        if result.matched_count == 0:
            raise HTTPException(404, "Procedure not found")
        return JSONResponse({"success": True, "procedure_id": procedure_id})
    except HTTPException:
        raise
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Update procedure error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.post("/api/memories/working/context")
async def set_working_context(request: Request):
    """Set working memory context (short-lived, auto-expires)."""
    user = require_user(request)
    data = await request.json()
    context_text = data.get("text", "").strip()
    if not context_text:
        raise HTTPException(400, "Context text is required")

    engine_ref = _get_engine(request)
    if not engine_ref:
        raise HTTPException(503, "Engine not available")

    try:
        scoped_db = await engine_ref.get_scoped_db(APP_SLUG)
        working_col = scoped_db["working_memory"]
        ttl_hours = data.get("ttl_hours", 24)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)

        await working_col.update_one(
            {"user_id": user["email"], "key": data.get("key", "default")},
            {"$set": {
                "user_id": user["email"],
                "app_slug": APP_SLUG,
                "key": data.get("key", "default"),
                "text": context_text,
                "memory_type": "working",
                "expires_at": expires_at,
                "updated_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        )
        return JSONResponse({"success": True, "expires_at": expires_at.isoformat()})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Set working context error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.get("/api/memories/working/context")
async def get_working_context(request: Request):
    """Get current working memory context."""
    user = require_user(request)

    engine_ref = _get_engine(request)
    if not engine_ref:
        raise HTTPException(503, "Engine not available")

    try:
        scoped_db = await engine_ref.get_scoped_db(APP_SLUG)
        working_col = scoped_db["working_memory"]
        now = datetime.now(timezone.utc)

        cursor = working_col.find({
            "user_id": user["email"],
            "$or": [{"expires_at": {"$gt": now}}, {"expires_at": {"$exists": False}}],
        }).sort("updated_at", -1)

        contexts = []
        async for doc in cursor:
            contexts.append({
                "key": doc.get("key", "default"),
                "text": doc.get("text", ""),
                "expires_at": doc.get("expires_at", "").isoformat() if hasattr(doc.get("expires_at", ""), "isoformat") else str(doc.get("expires_at", "")),
                "updated_at": str(doc.get("updated_at", "")),
            })
        return JSONResponse({"contexts": contexts, "count": len(contexts)})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Get working context error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.delete("/api/memories/working/context")
async def clear_working_context(request: Request):
    """Clear all working memory context."""
    user = require_user(request)

    engine_ref = _get_engine(request)
    if not engine_ref:
        raise HTTPException(503, "Engine not available")

    try:
        scoped_db = await engine_ref.get_scoped_db(APP_SLUG)
        working_col = scoped_db["working_memory"]
        result = await working_col.delete_many({"user_id": user["email"]})
        return JSONResponse({"success": True, "deleted": result.deleted_count})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Clear working context error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


# ============================================================================
# MEMORY TIMELINES
# ============================================================================


@app.post("/api/memories/timelines/fork")
async def fork_timeline(request: Request):
    """Fork a new timeline from the current one."""
    user = require_user(request)
    if not timeline_service:
        raise HTTPException(503, "Timeline service not available")

    data = await request.json()
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(400, "Timeline name is required")

    try:
        current = await timeline_service.get_active_timeline(user["email"])
        new_id = await timeline_service.fork_timeline(
            current_timeline=current,
            new_name=name,
            user_id=user["email"],
            app_slug=APP_SLUG,
        )
        return JSONResponse({"success": True, "timeline_id": new_id, "name": name}, status_code=201)
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Fork timeline error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.get("/api/memories/timelines")
async def list_timelines(request: Request):
    """List all timelines for the current user."""
    user = require_user(request)
    if not timeline_service:
        raise HTTPException(503, "Timeline service not available")

    try:
        timelines = await timeline_service.list_timelines(user_id=user["email"])
        active = await timeline_service.get_active_timeline(user["email"])
        result = []
        for tl in timelines:
            result.append({
                "id": tl.get("timeline_id", str(tl.get("_id", ""))),
                "name": tl.get("name", ""),
                "parent": tl.get("parent"),
                "is_active": tl.get("timeline_id", str(tl.get("_id", ""))) == active,
                "created_at": str(tl.get("created_at", "")),
            })
        return JSONResponse({"timelines": result, "active": active, "count": len(result)})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("List timelines error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.get("/api/memories/timelines/current")
async def get_current_timeline(request: Request):
    """Get the current active timeline."""
    user = require_user(request)
    if not timeline_service:
        raise HTTPException(503, "Timeline service not available")

    try:
        active_id = await timeline_service.get_active_timeline(user["email"])
        timeline = await timeline_service.get_timeline(active_id) if active_id else None
        return JSONResponse({
            "timeline_id": active_id,
            "timeline": {
                "id": timeline.get("timeline_id", str(timeline.get("_id", ""))),
                "name": timeline.get("name", ""),
                "parent": timeline.get("parent"),
                "created_at": str(timeline.get("created_at", "")),
            } if timeline else None,
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Get current timeline error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.post("/api/memories/timelines/switch")
async def switch_timeline(request: Request):
    """Switch to a different timeline."""
    user = require_user(request)
    if not timeline_service:
        raise HTTPException(503, "Timeline service not available")

    data = await request.json()
    timeline_id = data.get("timeline_id", "").strip()
    if not timeline_id:
        raise HTTPException(400, "timeline_id is required")

    try:
        await timeline_service.set_active_timeline(user["email"], timeline_id)
        return JSONResponse({"success": True, "active_timeline": timeline_id})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Switch timeline error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.get("/api/memories/timelines/{timeline_id}/ancestry")
async def get_timeline_ancestry(timeline_id: str, request: Request):
    """Get the ancestry chain for a timeline."""
    require_user(request)
    if not timeline_service:
        raise HTTPException(503, "Timeline service not available")

    try:
        ancestry = await timeline_service.get_timeline_ancestry(timeline_id)
        return JSONResponse({"timeline_id": timeline_id, "ancestry": ancestry})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Timeline ancestry error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


# ============================================================================
# PROSPECTIVE MEMORY (triggers / reminders)
# ============================================================================


@app.post("/api/prospective/triggers")
async def set_trigger(request: Request):
    """Set a prospective memory trigger ('remind me when...')."""
    user = require_user(request)
    if not prospective_memory:
        raise HTTPException(503, "Prospective memory not available")

    data = await request.json()
    condition = data.get("condition", "").strip()
    action = data.get("action", "").strip()
    if not condition or not action:
        raise HTTPException(400, "Both 'condition' and 'action' are required")

    try:
        trigger_id = await prospective_memory.set_trigger(
            condition=condition,
            action=action,
            user_id=user["email"],
            metadata=data.get("metadata"),
            one_shot=data.get("one_shot", True),
        )
        return JSONResponse(
            {"success": True, "trigger_id": trigger_id, "condition": condition, "action": action},
            status_code=201,
        )
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Set trigger error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.get("/api/prospective/triggers")
async def list_triggers(request: Request):
    """List active prospective memory triggers."""
    user = require_user(request)
    if not prospective_memory:
        raise HTTPException(503, "Prospective memory not available")

    try:
        triggers = await prospective_memory.get_active_triggers(
            user_id=user["email"],
            limit=int(request.query_params.get("limit", "20")),
        )
        result = []
        for t in triggers:
            result.append({
                "id": str(t.get("_id", t.get("id", ""))),
                "condition": t.get("condition", ""),
                "action": t.get("action", ""),
                "one_shot": t.get("one_shot", True),
                "active": t.get("active", True),
                "created_at": str(t.get("created_at", "")),
            })
        return JSONResponse({"triggers": result, "count": len(result)})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("List triggers error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.delete("/api/prospective/triggers/{trigger_id}")
async def deactivate_trigger(trigger_id: str, request: Request):
    """Deactivate a prospective memory trigger."""
    require_user(request)
    if not prospective_memory:
        raise HTTPException(503, "Prospective memory not available")

    try:
        success = await prospective_memory.deactivate_trigger(trigger_id)
        if not success:
            raise HTTPException(404, "Trigger not found")
        return JSONResponse({"success": True, "trigger_id": trigger_id})
    except HTTPException:
        raise
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Deactivate trigger error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


# ============================================================================
# SHARED MEMORIES
# ============================================================================


@app.post("/api/memories/shared/promote")
async def promote_to_shared(request: Request):
    """Promote a memory fact to shared (group-visible) status."""
    user = require_user(request)
    if not shared_memory:
        raise HTTPException(503, "Shared memory not available")

    data = await request.json()
    fact = data.get("fact", "").strip()
    if not fact:
        raise HTTPException(400, "Fact text is required")

    group_id = data.get("group_id", f"member:shared:{APP_SLUG}")

    try:
        result = await shared_memory.promote_to_shared(
            fact=fact,
            source_user_ids=[user["email"]],
            confidence=data.get("confidence", 0.8),
            group_id=group_id,
            anonymize=data.get("anonymize", True),
            metadata=data.get("metadata"),
        )
        return JSONResponse({
            "success": True,
            "shared_id": str(result.get("_id", result.get("id", ""))),
            "fact": fact,
            "group_id": group_id,
        }, status_code=201)
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Promote to shared error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.get("/api/memories/shared")
async def get_shared_memories(request: Request):
    """Get shared memories for a group."""
    user = require_user(request)
    if not shared_memory:
        raise HTTPException(503, "Shared memory not available")

    group_id = request.query_params.get("group_id", f"member:shared:{APP_SLUG}")
    limit = min(int(request.query_params.get("limit", "20")), 50)

    try:
        memories = await shared_memory.get_shared_memory(
            group_id=group_id,
            limit=limit,
            user_id=user["email"],
        )
        result = []
        for mem in memories:
            result.append({
                "id": str(mem.get("_id", mem.get("id", ""))),
                "text": mem.get("text", mem.get("fact", mem.get("memory", ""))),
                "confidence": mem.get("confidence", 0),
                "shared_by_count": len(mem.get("source_user_ids", [])),
                "created_at": str(mem.get("created_at", "")),
            })
        return JSONResponse({"memories": result, "count": len(result), "group_id": group_id})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Get shared memories error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.get("/api/memories/shared/stats")
async def get_shared_stats(request: Request):
    """Get shared memory statistics."""
    require_user(request)
    if not shared_memory:
        raise HTTPException(503, "Shared memory not available")

    group_id = request.query_params.get("group_id", f"member:shared:{APP_SLUG}")

    try:
        stats = await shared_memory.get_shared_stats(group_id=group_id)
        return JSONResponse({"success": True, "stats": stats, "group_id": group_id})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Shared stats error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


# ============================================================================
# REFLECTION & CONSOLIDATION
# ============================================================================


@app.post("/api/memories/reflection/run")
async def run_reflection(request: Request):
    """Trigger a reflection cycle on current memories."""
    user = require_user(request)
    if not reflection_service:
        raise HTTPException(503, "Reflection service not available")

    try:
        result = await reflection_service.run_reflection(user_id=user["email"])
        return JSONResponse({"success": True, "reflection": result})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Run reflection error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.post("/api/memories/consolidate")
async def consolidate_memories(request: Request):
    """Trigger manual memory consolidation (extract entities, create procedures)."""
    user = require_user(request)
    if not memory_consolidator:
        raise HTTPException(503, "Memory consolidator not available")

    try:
        result = await memory_consolidator.consolidate_episodes(
            agent_id=user["email"],
            limit=int(request.query_params.get("limit", "10")),
        )
        return JSONResponse({"success": True, "consolidation": result})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Consolidate error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


# ============================================================================
# PROFILE SERVICE
# ============================================================================


@app.get("/api/profile")
async def get_user_profile(request: Request):
    """Get the current user's materialized profile."""
    user = require_user(request)
    if not profile_service:
        raise HTTPException(503, "Profile service not available")

    try:
        profile = await profile_service.get_user_profile(user_id=user["email"])
        if not profile:
            return JSONResponse({"success": True, "profile": None, "message": "No profile yet. Use /api/profile/build to create one."})

        safe = {}
        for k, v in profile.items():
            if k == "_id":
                safe[k] = str(v)
            elif hasattr(v, "isoformat"):
                safe[k] = v.isoformat()
            else:
                safe[k] = v
        return JSONResponse({"success": True, "profile": safe})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Get profile error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.get("/api/profile/text")
async def get_profile_text(request: Request):
    """Get profile as formatted text (for prompt injection)."""
    user = require_user(request)
    if not profile_service:
        raise HTTPException(503, "Profile service not available")

    try:
        text = await profile_service.get_user_profile_text(user_id=user["email"])
        return JSONResponse({"success": True, "text": text})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Get profile text error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.post("/api/profile/build")
async def build_profile(request: Request):
    """Full profile rebuild from all memories and graph data."""
    user = require_user(request)
    if not profile_service:
        raise HTTPException(503, "Profile service not available")

    try:
        profile = await profile_service.build_user_profile(user_id=user["email"])
        safe = {}
        for k, v in (profile or {}).items():
            if k == "_id":
                safe[k] = str(v)
            elif hasattr(v, "isoformat"):
                safe[k] = v.isoformat()
            else:
                safe[k] = v
        return JSONResponse({"success": True, "profile": safe})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Build profile error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.post("/api/profile/incremental")
async def incremental_profile_update(request: Request):
    """Incremental profile update based on new memories."""
    user = require_user(request)
    if not profile_service:
        raise HTTPException(503, "Profile service not available")

    data = await request.json()
    new_memories = data.get("memories", [])

    try:
        profile = await profile_service.incremental_update(
            user_id=user["email"],
            new_memories=new_memories,
        )
        safe = {}
        for k, v in (profile or {}).items():
            if k == "_id":
                safe[k] = str(v)
            elif hasattr(v, "isoformat"):
                safe[k] = v.isoformat()
            else:
                safe[k] = v
        return JSONResponse({"success": True, "profile": safe})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Incremental profile error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.delete("/api/profile")
async def delete_profile(request: Request):
    """Delete the current user's profile."""
    user = require_user(request)
    if not profile_service:
        raise HTTPException(503, "Profile service not available")

    try:
        success = await profile_service.delete_user_profile(user_id=user["email"])
        return JSONResponse({"success": success})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Delete profile error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


# ============================================================================
# OSI ROUTES (semantic model management)
# ============================================================================

try:
    from mdb_engine.osi.routes import router as osi_router

    app.include_router(osi_router)
    logger.info("OSI routes mounted at /api/osi")
except ImportError:
    logger.warning("OSI routes not available (mdb_engine.osi.routes not found)")
except (ImportError, RuntimeError, OSError) as e:
    logger.warning("Failed to mount OSI routes: %s", e)


# ============================================================================
# GRAPH PATH FINDING & NEIGHBORS
# ============================================================================


@app.get("/api/graph/path")
async def find_graph_path(request: Request, db=Depends(get_scoped_db)):
    """Find shortest path between two graph nodes (BFS)."""
    user = require_user(request)
    source_id = request.query_params.get("source", "").strip()
    target_id = request.query_params.get("target", "").strip()
    if not source_id or not target_id:
        raise HTTPException(400, "Both 'source' and 'target' query params are required")

    max_depth = min(int(request.query_params.get("max_depth", "5")), 10)

    try:
        engine_ref = _get_engine(request)
        graph_service = engine_ref.get_graph_service(APP_SLUG) if engine_ref and hasattr(engine_ref, "get_graph_service") else None

        if graph_service and hasattr(graph_service, "find_path"):
            path = await graph_service.find_path(
                start_id=source_id,
                end_id=target_id,
                max_depth=max_depth,
            )
            return JSONResponse({
                "success": True,
                "source": source_id,
                "target": target_id,
                "path": path if path else [],
                "found": path is not None and len(path) > 0,
                "hops": len(path) - 1 if path else 0,
            })

        # Fallback: manual BFS over the __kg collection
        kg_col = db["__kg"]
        visited = set()
        queue = [(source_id, [source_id])]
        visited.add(source_id)

        while queue:
            current_id, current_path = queue.pop(0)
            if len(current_path) - 1 > max_depth:
                break

            node = await kg_col.find_one({"_id": current_id, "app_slug": APP_SLUG})
            if not node:
                continue

            for edge in node.get("edges", []):
                neighbor = str(edge.get("target", ""))
                if neighbor == target_id:
                    final_path = current_path + [neighbor]
                    return JSONResponse({
                        "success": True,
                        "source": source_id,
                        "target": target_id,
                        "path": final_path,
                        "found": True,
                        "hops": len(final_path) - 1,
                    })
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, current_path + [neighbor]))

        return JSONResponse({
            "success": True,
            "source": source_id,
            "target": target_id,
            "path": [],
            "found": False,
            "hops": 0,
            "message": f"No path found within {max_depth} hops",
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Graph path error: %s", e, exc_info=True)
        raise HTTPException(500, str(e)) from e


@app.get("/api/graph/neighbors")
async def get_graph_neighbors(request: Request, db=Depends(get_scoped_db)):
    """Get immediate neighbors of a graph node."""
    user = require_user(request)
    node_id = request.query_params.get("node_id", "").strip()
    if not node_id:
        raise HTTPException(400, "'node_id' query param is required")

    try:
        kg_col = db["__kg"]
        node = await kg_col.find_one({"_id": node_id, "app_slug": APP_SLUG})
        if not node:
            raise HTTPException(404, "Node not found")

        neighbors = []
        for edge in node.get("edges", []):
            target_id = str(edge.get("target", ""))
            target_node = await kg_col.find_one({"_id": target_id, "app_slug": APP_SLUG})
            neighbors.append({
                "id": target_id,
                "label": target_node.get("label", target_id) if target_node else target_id,
                "type": target_node.get("type", "unknown") if target_node else "unknown",
                "relation": edge.get("relation", ""),
                "active": edge.get("active", True),
            })

        return JSONResponse({
            "node_id": node_id,
            "label": node.get("label", node_id),
            "type": node.get("type", "unknown"),
            "neighbors": neighbors,
            "count": len(neighbors),
        })
    except HTTPException:
        raise
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error("Graph neighbors error: %s", e, exc_info=True)
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
        "timeline_service": timeline_service is not None,
        "prospective_memory": prospective_memory is not None,
        "shared_memory": shared_memory is not None,
        "profile_service": profile_service is not None,
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
