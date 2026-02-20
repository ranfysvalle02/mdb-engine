#!/usr/bin/env python3
"""
Perfect Brain - Complete Cognitive Architecture Demo
====================================================

This example demonstrates the FULL MDB-Engine "Perfect Brain" -- every cognitive
memory subsystem, driven entirely by manifest.json configuration.

CORE MEMORY (via manifest):
- Cognitive memory with importance scoring, reinforcement, merging, dedup
- Emotion-weighted recall, spreading activation, salience gating
- Memory categories and bucket organization
- Semantic search with MongoDB Atlas Vector Search
- Memory pruning, cold storage, conflict detection

GRAPH (via manifest):
- Knowledge graph with $graphLookup traversal
- Hybrid search (GraphRAG)
- LLM-powered entity/relationship extraction

PERFECT BRAIN (instantiated directly -- future manifest integration):
- Prospective Memory: intention-based triggers ("when X, do Y")
- Memory Vetoes: user-controlled privacy ("never share this")
- Shared/Group Memory: privacy-safe promotion to team/family level
- Reflective Memory: meta-cognitive insights ("I tend to over-weight...")
- Predictive Memory: counterfactuals and prediction validation
- Memory Versioning: belief evolution tracking over time
- Query-Aware Recall: policy-driven retrieval (fast/critical/exploration)
- Memory Consolidation: episodic -> semantic distillation
- Brain Hygiene: automated maintenance routines

Run with:
    uvicorn app:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from mdb_engine import MongoDBEngine

# Perfect Brain modules (not yet wired through manifest -- instantiated directly)
from mdb_engine.memory import (
    ProspectiveMemory,
    MemoryVeto,
    SharedMemory,
    ReflectiveMemory,
    PredictiveMemory,
    MemoryVersioning,
    QueryAwareRecall,
    MemoryConsolidator,
    run_daily_hygiene,
)
from mdb_engine.memory.timeline import TimelineService

# Load environment variables
load_dotenv()

logger = logging.getLogger("perfect_brain")

# App configuration
APP_SLUG = "perfect_brain"
DEMO_USER_ID = "demo_user_123"
DEMO_GROUP_ID = "team-alpha"

# Initialize engine
from mdb_engine.env import get_mongo_uri, get_db_name

engine = MongoDBEngine(
    mongo_uri=get_mongo_uri(),
    db_name=get_db_name(fallback="perfect_brain_db"),
)

# =============================================================================
# Perfect Brain Subsystem Registry
# =============================================================================
# These subsystems are instantiated at startup using the engine's scoped
# collections. In the future, ServiceInitializer will wire these automatically
# from manifest.json configuration.

brain: dict[str, Any] = {
    "prospective": None,
    "veto": None,
    "shared": None,
    "reflective": None,
    "predictive": None,
    "versioning": None,
    "timeline": None,
    "recall": None,
}


def _init_brain_subsystems():
    """Initialize Perfect Brain subsystems using the engine's scoped DB."""
    global brain

    try:
        scoped_db = engine.get_scoped_db(APP_SLUG)
    except Exception as e:
        logger.warning(f"Could not get scoped DB for brain subsystems: {e}")
        return

    # Each subsystem gets its own collection via the scoped wrapper
    try:
        brain["prospective"] = ProspectiveMemory(
            collection=getattr(scoped_db, "prospective"),
            embedding_model="text-embedding-3-large",
            embedding_dims=3072,
        )
        logger.info("Prospective Memory initialized")
    except Exception as e:
        logger.warning(f"Prospective Memory init failed: {e}")

    try:
        brain["veto"] = MemoryVeto(collection=getattr(scoped_db, "vetoes"))
        logger.info("Memory Veto initialized")
    except Exception as e:
        logger.warning(f"Memory Veto init failed: {e}")

    try:
        memory_col = getattr(scoped_db, "memories")
        shared_col = getattr(scoped_db, "shared")
        brain["shared"] = SharedMemory(
            semantic_collection=memory_col,
            shared_collection=shared_col,
        )
        logger.info("Shared Memory initialized")
    except Exception as e:
        logger.warning(f"Shared Memory init failed: {e}")

    try:
        brain["reflective"] = ReflectiveMemory(
            collection=getattr(scoped_db, "reflective")
        )
        logger.info("Reflective Memory initialized")
    except Exception as e:
        logger.warning(f"Reflective Memory init failed: {e}")

    try:
        brain["predictive"] = PredictiveMemory(
            collection=getattr(scoped_db, "predictive")
        )
        logger.info("Predictive Memory initialized")
    except Exception as e:
        logger.warning(f"Predictive Memory init failed: {e}")

    try:
        brain["versioning"] = MemoryVersioning(
            collection=getattr(scoped_db, "memories")
        )
        logger.info("Memory Versioning initialized")
    except Exception as e:
        logger.warning(f"Memory Versioning init failed: {e}")

    try:
        brain["timeline"] = TimelineService(
            collection=getattr(scoped_db, "timelines")
        )
        logger.info("Timeline Service initialized")
    except Exception as e:
        logger.warning(f"Timeline Service init failed: {e}")

    try:
        brain["recall"] = QueryAwareRecall()
        logger.info("Query-Aware Recall initialized")
    except Exception as e:
        logger.warning(f"Query-Aware Recall init failed: {e}")


async def on_startup(app, engine, manifest):
    """Initialize Perfect Brain subsystems after engine startup."""
    _init_brain_subsystems()
    logger.info("Perfect Brain subsystems initialized")


# Create FastAPI app
app = engine.create_app(
    slug=APP_SLUG,
    manifest=Path(__file__).parent / "manifest.json",
    title="Perfect Brain - Cognitive Architecture Demo",
    version="2.0.0",
    on_startup=on_startup,
)


# =============================================================================
# Pydantic Request Models
# =============================================================================


class InjectMemoryRequest(BaseModel):
    """Request body for injecting a memory."""
    memory: str
    category: str = "general"
    importance: float | None = None
    bucket_id: str | None = None
    bucket_type: str = "general"
    metadata: dict[str, Any] | None = None


class SearchMemoriesRequest(BaseModel):
    """Request body for searching memories."""
    query: str
    limit: int = 5
    category: str | None = None
    bucket_id: str | None = None


class UpdateMemoryRequest(BaseModel):
    """Request body for updating a memory."""
    memory: str | None = None
    metadata: dict[str, Any] | None = None


class ConflictCheckRequest(BaseModel):
    """Request body for checking knowledge conflicts."""
    fact: str


class CreateNodeRequest(BaseModel):
    """Request body for creating/updating a graph node."""
    node_id: str
    node_type: str
    name: str
    properties: dict[str, Any] | None = None


class AddEdgeRequest(BaseModel):
    """Request body for adding an edge between nodes."""
    source_id: str
    relation: str
    target_id: str
    properties: dict[str, Any] | None = None
    weight: float = 1.0


class RemoveEdgeRequest(BaseModel):
    """Request body for removing an edge."""
    source_id: str
    relation: str
    target_id: str


class GraphSearchRequest(BaseModel):
    """Request body for hybrid search (GraphRAG)."""
    query: str
    max_depth: int = 2
    limit: int = 5


class ExtractGraphRequest(BaseModel):
    """Request body for LLM-powered graph extraction."""
    text: str
    auto_create: bool = True


# -- Perfect Brain Request Models --


class SetTriggerRequest(BaseModel):
    """Request body for setting a prospective memory trigger."""
    condition: str
    action: str
    metadata: dict[str, Any] | None = None


class CheckTriggersRequest(BaseModel):
    """Request body for checking prospective triggers."""
    context: str


class AddVetoRequest(BaseModel):
    """Request body for adding a memory veto."""
    reason: str | None = None
    scope: str = "all"


class PromoteSharedRequest(BaseModel):
    """Request body for promoting a memory to shared/group level."""
    fact: str
    confidence: float = 0.8
    group_id: str = DEMO_GROUP_ID
    bucket_id: str | None = None


class StoreReflectionRequest(BaseModel):
    """Request body for storing a meta-cognitive reflection."""
    reflection: str
    trigger: str = "manual"
    confidence: float = 0.7


class StorePredictionRequest(BaseModel):
    """Request body for storing a prediction/counterfactual."""
    scenario: str
    origin: str = "hypothesis"
    confidence: float = 0.5


class ValidatePredictionRequest(BaseModel):
    """Request body for validating a prediction."""
    was_correct: bool


class RecallRequest(BaseModel):
    """Request body for policy-driven recall."""
    query: str
    task_type: str = "general"
    risk_tolerance: str = "medium"
    latency_budget: str = "normal"


# =============================================================================
# Helper Functions
# =============================================================================


def get_memory_service():
    """Get the memory service instance."""
    memory = engine.get_memory_service(APP_SLUG)
    if not memory:
        raise HTTPException(503, "Memory service not available")
    return memory


def get_graph_service():
    """Get the graph service instance."""
    graph = engine.get_graph_service(APP_SLUG)
    if not graph:
        raise HTTPException(503, "Graph service not available")
    return graph


def get_brain(subsystem: str):
    """Get a Perfect Brain subsystem, raising 503 if not initialized."""
    service = brain.get(subsystem)
    if not service:
        raise HTTPException(503, f"Brain subsystem '{subsystem}' not initialized")
    return service


# =============================================================================
# ROOT & HEALTH
# =============================================================================


@app.get("/", response_class=JSONResponse)
async def root():
    """Welcome endpoint with full API overview."""
    return {
        "app": "Perfect Brain",
        "description": "Complete cognitive architecture demo for MDB-Engine",
        "version": "2.0.0",
        "user_id": DEMO_USER_ID,
        "brain_subsystems": {
            name: ("active" if svc is not None else "inactive")
            for name, svc in brain.items()
        },
        "endpoints": {
            "memory": {
                "inject": "POST /memories/inject",
                "search": "POST /memories/search",
                "get_all": "GET /memories",
                "get_one": "GET /memories/{id}",
                "update": "PUT /memories/{id}",
                "delete": "DELETE /memories/{id}",
                "delete_all": "DELETE /memories",
                "analytics": "GET /memories/analytics",
                "prune": "POST /memories/prune",
                "cold_storage": "GET /memories/cold-storage",
                "restore": "POST /memories/{id}/restore",
                "conflict": "POST /memories/check-conflict",
                "categories": "GET /memories/categories",
            },
            "graph": {
                "stats": "GET /graph/stats",
                "create_node": "POST /graph/nodes",
                "list_nodes": "GET /graph/nodes",
                "get_node": "GET /graph/nodes/{id}",
                "delete_node": "DELETE /graph/nodes/{id}",
                "add_edge": "POST /graph/edges",
                "remove_edge": "DELETE /graph/edges",
                "traverse": "GET /graph/traverse/{id}",
                "neighbors": "GET /graph/neighbors/{id}",
                "search": "POST /graph/search",
                "extract": "POST /graph/extract",
                "extract_from_memory": "POST /memories/{id}/extract-graph",
            },
            "brain": {
                "dashboard": "GET /brain/dashboard",
                "prospective_set": "POST /brain/prospective/triggers",
                "prospective_list": "GET /brain/prospective/triggers",
                "prospective_check": "POST /brain/prospective/check",
                "veto_add": "POST /brain/veto/{memory_id}",
                "veto_check": "GET /brain/veto/check/{memory_id}",
                "veto_remove": "DELETE /brain/veto/{memory_id}",
                "shared_promote": "POST /brain/shared/promote",
                "shared_list": "GET /brain/shared/",
                "reflective_store": "POST /brain/reflective/",
                "reflective_list": "GET /brain/reflective/",
                "predictive_store": "POST /brain/predictive/",
                "predictive_validate": "POST /brain/predictive/{id}/validate",
                "predictive_accuracy": "GET /brain/predictive/accuracy",
                "versioning_history": "GET /brain/versioning/{entity}",
                "recall": "POST /brain/recall/",
                "consolidate": "POST /brain/health/consolidate",
                "hygiene": "POST /brain/health/hygiene",
            },
            "demo": {
                "seed": "POST /demo/seed",
                "reset": "POST /demo/reset",
            },
        },
    }


@app.get("/health")
async def health():
    """Health check for all subsystems."""
    memory = engine.get_memory_service(APP_SLUG)
    graph = engine.get_graph_service(APP_SLUG)
    return {
        "status": "healthy",
        "engine_initialized": engine.initialized,
        "memory_service": memory is not None,
        "graph_service": graph is not None and graph.enabled,
        "brain_subsystems": {
            name: svc is not None for name, svc in brain.items()
        },
    }


# =============================================================================
# CORE MEMORY OPERATIONS
# =============================================================================


@app.post("/memories/inject", response_class=JSONResponse)
async def inject_memory(request: InjectMemoryRequest):
    """Inject a memory directly (bypasses LLM fact extraction)."""
    memory = get_memory_service()

    metadata = request.metadata or {}
    metadata["category"] = request.category
    metadata["source"] = "manual_injection"
    metadata["injected_at"] = datetime.now(timezone.utc).isoformat()

    if request.importance is not None:
        metadata["manual_importance"] = max(0.1, min(1.0, request.importance))

    result = await memory.inject(
        memory=request.memory,
        user_id=DEMO_USER_ID,
        metadata=metadata,
        bucket_id=request.bucket_id,
        bucket_type=request.bucket_type,
    )

    return {
        "success": True,
        "memory": result,
        "message": f"Memory injected with category '{request.category}'",
    }


@app.post("/memories/search", response_class=JSONResponse)
async def search_memories(request: SearchMemoriesRequest):
    """Semantic search across memories with cognitive ranking."""
    memory = get_memory_service()

    filters = None
    if request.category or request.bucket_id:
        filters = {"metadata": {}}
        if request.category:
            filters["metadata"]["category"] = request.category
        if request.bucket_id:
            filters["metadata"]["bucket_id"] = request.bucket_id

    results = await memory.search(
        query=request.query,
        user_id=DEMO_USER_ID,
        limit=request.limit,
        filters=filters,
    )

    return {
        "success": True,
        "query": request.query,
        "count": len(results),
        "results": results,
    }


@app.get("/memories", response_class=JSONResponse)
async def get_all_memories(limit: int = 100, category: str | None = None):
    """Get all memories for the demo user."""
    memory = get_memory_service()

    filters = None
    if category:
        filters = {"metadata": {"category": category}}

    memories = await memory.get_all(user_id=DEMO_USER_ID, limit=limit, filters=filters)

    return {"success": True, "count": len(memories), "memories": memories}


@app.get("/memories/{memory_id}", response_class=JSONResponse)
async def get_memory(memory_id: str):
    """Get a single memory by ID."""
    memory = get_memory_service()
    result = await memory.get(memory_id=memory_id, user_id=DEMO_USER_ID)
    if not result:
        raise HTTPException(404, f"Memory {memory_id} not found")
    return {"success": True, "memory": result}


@app.put("/memories/{memory_id}", response_class=JSONResponse)
async def update_memory(memory_id: str, request: UpdateMemoryRequest):
    """Update a memory (auto re-embeds if content changes)."""
    memory = get_memory_service()
    result = await memory.update(
        memory_id=memory_id,
        user_id=DEMO_USER_ID,
        memory=request.memory,
        metadata=request.metadata,
    )
    if not result:
        raise HTTPException(404, f"Memory {memory_id} not found")
    return {"success": True, "memory": result}


@app.delete("/memories/{memory_id}", response_class=JSONResponse)
async def delete_memory(memory_id: str):
    """Delete a single memory."""
    memory = get_memory_service()
    success = await memory.delete(memory_id=memory_id, user_id=DEMO_USER_ID)
    return {"success": success, "message": f"Memory {memory_id} deleted" if success else "Failed"}


@app.delete("/memories", response_class=JSONResponse)
async def delete_all_memories():
    """Delete ALL memories for the demo user."""
    memory = get_memory_service()
    all_memories = await memory.get_all(user_id=DEMO_USER_ID, limit=1000)
    count = len(all_memories)
    success = await memory.delete_all(user_id=DEMO_USER_ID)
    return {"success": success, "deleted_count": count if success else 0}


# =============================================================================
# COGNITIVE FEATURES
# =============================================================================


@app.get("/memories/analytics", response_class=JSONResponse)
async def get_memory_analytics():
    """Get cognitive memory analytics (strength, stability, category breakdown)."""
    memory = get_memory_service()
    if not hasattr(memory, "get_memory_analytics"):
        raise HTTPException(501, "Analytics not available")
    analytics = await memory.get_memory_analytics(user_id=DEMO_USER_ID)
    return {"success": True, "analytics": analytics}


@app.post("/memories/prune", response_class=JSONResponse)
async def prune_memories(max_capacity: int | None = None, reason: str = "manual_trigger"):
    """Trigger memory pruning (soft-delete weakest to cold storage)."""
    memory = get_memory_service()
    if not hasattr(memory, "prune_memories"):
        raise HTTPException(501, "Pruning not available")
    pruned_count = await memory.prune_memories(user_id=DEMO_USER_ID, max_capacity=max_capacity, reason=reason)
    return {"success": True, "pruned_count": pruned_count}


@app.get("/memories/cold-storage", response_class=JSONResponse)
async def get_cold_storage(limit: int = 50):
    """Get memories from cold storage (pruned/inactive)."""
    memory = get_memory_service()
    if not hasattr(memory, "get_cold_storage"):
        raise HTTPException(501, "Cold storage not available")
    cold_memories = await memory.get_cold_storage(user_id=DEMO_USER_ID, limit=limit, include_reason=True)
    return {"success": True, "count": len(cold_memories), "memories": cold_memories}


@app.post("/memories/{memory_id}/restore", response_class=JSONResponse)
async def restore_from_cold_storage(memory_id: str):
    """Restore a memory from cold storage to active."""
    memory = get_memory_service()
    if not hasattr(memory, "restore_from_cold_storage"):
        raise HTTPException(501, "Restore not available")
    restored = await memory.restore_from_cold_storage(memory_id=memory_id, user_id=DEMO_USER_ID)
    if not restored:
        raise HTTPException(404, f"Memory {memory_id} not found in cold storage")
    return {"success": True, "memory": restored}


@app.post("/memories/check-conflict", response_class=JSONResponse)
async def check_knowledge_conflict(request: ConflictCheckRequest):
    """Check if new information conflicts with existing knowledge."""
    memory = get_memory_service()
    conflict = await memory.detect_knowledge_conflict(user_id=DEMO_USER_ID, new_fact=request.fact)
    return {"success": True, "fact": request.fact, "has_conflict": conflict is not None, "conflict": conflict}


@app.get("/memories/categories", response_class=JSONResponse)
async def get_categories():
    """Get available memory categories from manifest."""
    categories = [
        {"id": "biographical", "description": "Personal info: name, age, occupation, family"},
        {"id": "preferences", "description": "Likes, dislikes, favorites"},
        {"id": "work", "description": "Job, projects, colleagues"},
        {"id": "health", "description": "Conditions, medications, fitness"},
        {"id": "finance", "description": "Financial goals and preferences"},
        {"id": "travel", "description": "Travel history and preferences"},
        {"id": "hobbies", "description": "Interests and activities"},
        {"id": "relationships", "description": "People and connections"},
        {"id": "goals", "description": "Objectives and aspirations"},
        {"id": "skills", "description": "Abilities and competencies"},
    ]
    return {"success": True, "categories": categories}


# =============================================================================
# GRAPH OPERATIONS
# =============================================================================


@app.get("/graph/stats", response_class=JSONResponse)
async def get_graph_stats():
    """Get graph statistics."""
    graph = get_graph_service()
    stats = await graph.get_stats()
    return {"success": True, "stats": stats}


@app.post("/graph/nodes", response_class=JSONResponse)
async def create_graph_node(request: CreateNodeRequest):
    """Create or update a graph node."""
    graph = get_graph_service()
    result = await graph.upsert_node(
        node_id=request.node_id, node_type=request.node_type,
        name=request.name, properties=request.properties or {},
        user_id=DEMO_USER_ID,
    )
    return {"success": True, "node": result}


@app.get("/graph/nodes", response_class=JSONResponse)
async def list_graph_nodes(node_type: str | None = None, limit: int = 100):
    """List all graph nodes."""
    graph = get_graph_service()
    nodes = await graph.list_nodes(node_type=node_type, user_id=DEMO_USER_ID, limit=limit)
    cleaned = [
        {"_id": n.get("_id"), "type": n.get("type"), "name": n.get("name"),
         "properties": n.get("properties", {}), "edges": n.get("edges", [])}
        for n in nodes
    ]
    return {"success": True, "count": len(cleaned), "nodes": cleaned}


@app.get("/graph/nodes/{node_id:path}", response_class=JSONResponse)
async def get_graph_node(node_id: str):
    """Get a single graph node."""
    graph = get_graph_service()
    node = await graph.get_node(node_id)
    if not node:
        raise HTTPException(404, f"Node '{node_id}' not found")
    return {
        "success": True,
        "node": {"_id": node.get("_id"), "type": node.get("type"), "name": node.get("name"),
                 "properties": node.get("properties", {}), "edges": node.get("edges", [])},
    }


@app.delete("/graph/nodes/{node_id:path}", response_class=JSONResponse)
async def delete_graph_node(node_id: str):
    """Delete a graph node and all edges pointing to it."""
    graph = get_graph_service()
    success = await graph.delete_node(node_id)
    return {"success": success}


@app.post("/graph/edges", response_class=JSONResponse)
async def add_graph_edge(request: AddEdgeRequest):
    """Add an edge (relationship) between two nodes."""
    graph = get_graph_service()
    source = await graph.get_node(request.source_id)
    if not source:
        raise HTTPException(404, f"Source node '{request.source_id}' not found")
    success = await graph.add_edge(
        source_id=request.source_id, relation=request.relation,
        target_id=request.target_id, properties=request.properties or {},
        weight=request.weight,
    )
    return {
        "success": success,
        "edge": {"source": request.source_id, "relation": request.relation,
                 "target": request.target_id, "weight": request.weight},
    }


@app.delete("/graph/edges", response_class=JSONResponse)
async def remove_graph_edge(request: RemoveEdgeRequest):
    """Remove an edge between two nodes."""
    graph = get_graph_service()
    success = await graph.remove_edge(
        source_id=request.source_id, relation=request.relation, target_id=request.target_id,
    )
    return {"success": success}


@app.get("/graph/traverse/{node_id:path}", response_class=JSONResponse)
async def traverse_graph(node_id: str, max_depth: int = 2):
    """Traverse the graph from a starting node."""
    graph = get_graph_service()
    node = await graph.get_node(node_id)
    if not node:
        raise HTTPException(404, f"Node '{node_id}' not found")
    max_depth = max(1, min(5, max_depth))
    results = await graph.traverse(start_id=node_id, max_depth=max_depth)
    return {"success": True, "start_node": node_id, "max_depth": max_depth, "count": len(results), "nodes": results}


@app.get("/graph/neighbors/{node_id:path}", response_class=JSONResponse)
async def get_graph_neighbors(node_id: str, relation: str | None = None):
    """Get immediate neighbors of a node (1-hop)."""
    graph = get_graph_service()
    neighbors = await graph.get_neighbors(node_id=node_id, relation=relation)
    return {"success": True, "node_id": node_id, "count": len(neighbors), "neighbors": neighbors}


@app.post("/graph/search", response_class=JSONResponse)
async def graph_hybrid_search(request: GraphSearchRequest):
    """Hybrid search: vector similarity + graph traversal (GraphRAG)."""
    graph = get_graph_service()
    results = await graph.hybrid_search(
        query=request.query, user_id=DEMO_USER_ID,
        max_depth=request.max_depth, vector_limit=request.limit,
    )
    context_str = graph.format_graph_context(results, max_nodes=10)
    return {
        "success": True,
        "query": request.query,
        "entry_nodes_count": len(results.get("entry_nodes", [])),
        "graph_context_count": len(results.get("graph_context", [])),
        "total_nodes": results.get("total_nodes", 0),
        "entry_nodes": results.get("entry_nodes", []),
        "graph_context": results.get("graph_context", []),
        "formatted_context": context_str,
    }


@app.post("/graph/extract", response_class=JSONResponse)
async def extract_graph_entities(request: ExtractGraphRequest):
    """Extract entities and relationships from text using LLM."""
    graph = get_graph_service()
    result = await graph.extract_graph_from_text(
        text=request.text, user_id=DEMO_USER_ID, auto_create_nodes=request.auto_create,
    )
    return {
        "success": True,
        "input_text": request.text,
        "nodes_created": result.get("nodes_created", 0),
        "edges_created": result.get("edges_created", 0),
        "extracted": result.get("extracted"),
    }


@app.post("/memories/{memory_id}/extract-graph", response_class=JSONResponse)
async def extract_graph_from_memory(memory_id: str, auto_create: bool = True):
    """Extract graph entities from an existing memory."""
    memory = get_memory_service()
    graph = get_graph_service()
    mem = await memory.get(memory_id=memory_id, user_id=DEMO_USER_ID)
    if not mem:
        raise HTTPException(404, f"Memory '{memory_id}' not found")
    memory_text = mem.get("memory", "")
    if not memory_text:
        return {"success": False, "error": "Memory has no text content"}
    result = await graph.extract_graph_from_text(
        text=memory_text, user_id=DEMO_USER_ID, auto_create_nodes=auto_create,
    )
    return {
        "success": True,
        "memory_id": memory_id,
        "memory_text": memory_text,
        "nodes_created": result.get("nodes_created", 0),
        "edges_created": result.get("edges_created", 0),
        "extracted": result.get("extracted"),
    }


# =============================================================================
# PERFECT BRAIN: PROSPECTIVE MEMORY
# =============================================================================


@app.post("/brain/prospective/triggers", response_class=JSONResponse)
async def set_prospective_trigger(request: SetTriggerRequest):
    """
    Set a prospective memory trigger ("when X happens, do Y").

    The condition is embedded as a vector. When future context matches the
    condition via vector similarity, the trigger fires and the action is surfaced.
    """
    prospective = get_brain("prospective")
    trigger_id = await prospective.set_trigger(
        condition=request.condition,
        action=request.action,
        user_id=DEMO_USER_ID,
        metadata=request.metadata,
    )
    return {
        "success": True,
        "trigger_id": trigger_id,
        "condition": request.condition,
        "action": request.action,
    }


@app.get("/brain/prospective/triggers", response_class=JSONResponse)
async def list_prospective_triggers():
    """List all active prospective memory triggers."""
    prospective = get_brain("prospective")
    triggers = await prospective.get_active_triggers(user_id=DEMO_USER_ID)
    return {"success": True, "count": len(triggers), "triggers": triggers}


@app.post("/brain/prospective/check", response_class=JSONResponse)
async def check_prospective_triggers(request: CheckTriggersRequest):
    """
    Check if any triggers fire for the given context.

    Uses vector similarity between the context and stored trigger conditions.
    """
    prospective = get_brain("prospective")
    fired = await prospective.check_triggers(
        current_context=request.context,
        user_id=DEMO_USER_ID,
    )
    return {
        "success": True,
        "context": request.context,
        "triggers_fired": len(fired),
        "fired": fired,
    }


# =============================================================================
# PERFECT BRAIN: MEMORY VETOES
# =============================================================================


@app.post("/brain/veto/{memory_id}", response_class=JSONResponse)
async def add_memory_veto(memory_id: str, request: AddVetoRequest):
    """
    Veto a memory (mark as "never share, even abstractly").

    Prevents promotion to shared/group memory. Scopes:
    - "all": never share in any context
    - "family"/"team": don't promote to that group level
    - "system": don't promote to system-wide memory
    """
    veto = get_brain("veto")
    result = await veto.add_veto(
        memory_id=memory_id, user_id=DEMO_USER_ID,
        reason=request.reason, scope=request.scope,
    )
    return {"success": True, "veto": result}


@app.get("/brain/veto/check/{memory_id}", response_class=JSONResponse)
async def check_memory_veto(memory_id: str, target_scope: str = "all"):
    """Check if a memory is vetoed for a specific scope."""
    veto = get_brain("veto")
    is_vetoed = await veto.check_veto(memory_id=memory_id, user_id=DEMO_USER_ID, target_scope=target_scope)
    return {"success": True, "memory_id": memory_id, "is_vetoed": is_vetoed, "scope": target_scope}


@app.delete("/brain/veto/{memory_id}", response_class=JSONResponse)
async def remove_memory_veto(memory_id: str):
    """Remove a veto from a memory."""
    veto = get_brain("veto")
    success = await veto.remove_veto(memory_id=memory_id, user_id=DEMO_USER_ID)
    return {"success": success, "memory_id": memory_id}


@app.get("/brain/veto/", response_class=JSONResponse)
async def list_user_vetoes():
    """List all vetoes for the demo user."""
    veto = get_brain("veto")
    vetoes = await veto.get_user_vetoes(user_id=DEMO_USER_ID)
    return {"success": True, "count": len(vetoes), "vetoes": vetoes}


# =============================================================================
# PERFECT BRAIN: SHARED / GROUP MEMORY
# =============================================================================


@app.post("/brain/shared/promote", response_class=JSONResponse)
async def promote_to_shared(request: PromoteSharedRequest):
    """
    Promote a fact to shared/group-level memory.

    Shared memory is:
    - Derived (distilled patterns, not raw transcripts)
    - Anonymized (no private details)
    - Consensual (explicit promotion)
    """
    shared = get_brain("shared")
    result = await shared.promote_to_shared(
        fact=request.fact,
        source_user_ids=[DEMO_USER_ID],
        confidence=request.confidence,
        group_id=request.group_id,
        bucket_id=request.bucket_id,
    )
    return {"success": True, "shared_memory": result}


@app.get("/brain/shared/", response_class=JSONResponse)
async def get_shared_memories(group_id: str = DEMO_GROUP_ID, min_confidence: float = 0.0):
    """Get shared memories for a group."""
    shared = get_brain("shared")
    memories = await shared.get_shared_memory(group_id=group_id, min_confidence=min_confidence)
    return {"success": True, "group_id": group_id, "count": len(memories), "memories": memories}


# =============================================================================
# PERFECT BRAIN: REFLECTIVE MEMORY
# =============================================================================


@app.post("/brain/reflective/", response_class=JSONResponse)
async def store_reflection(request: StoreReflectionRequest):
    """
    Store a meta-cognitive reflection.

    Reflective memory enables the system to:
    - Recognize its own biases
    - Learn from past mistakes
    - Adapt behavior based on self-awareness
    """
    reflective = get_brain("reflective")
    result = await reflective.store_reflection(
        reflection=request.reflection,
        trigger=request.trigger,
        confidence=request.confidence,
        scope="user",
        user_id=DEMO_USER_ID,
    )
    return {"success": True, "reflection": result}


@app.get("/brain/reflective/", response_class=JSONResponse)
async def get_reflections(min_confidence: float = 0.0, limit: int = 50):
    """Get meta-cognitive reflections."""
    reflective = get_brain("reflective")
    reflections = await reflective.get_reflections(
        scope="user", user_id=DEMO_USER_ID, min_confidence=min_confidence, limit=limit,
    )
    return {"success": True, "count": len(reflections), "reflections": reflections}


# =============================================================================
# PERFECT BRAIN: PREDICTIVE MEMORY
# =============================================================================


@app.post("/brain/predictive/", response_class=JSONResponse)
async def store_prediction(request: StorePredictionRequest):
    """
    Store a prediction or counterfactual scenario.

    Origins: "simulation", "counterfactual", "hypothesis", "pattern"
    """
    predictive = get_brain("predictive")
    result = await predictive.store_prediction(
        scenario=request.scenario,
        origin=request.origin,
        confidence=request.confidence,
        scope="user",
        user_id=DEMO_USER_ID,
    )
    return {"success": True, "prediction": result}


@app.post("/brain/predictive/{prediction_id}/validate", response_class=JSONResponse)
async def validate_prediction(prediction_id: str, request: ValidatePredictionRequest):
    """Validate a prediction against reality."""
    predictive = get_brain("predictive")
    result = await predictive.validate_prediction(
        prediction_id=prediction_id,
        was_correct=request.was_correct,
    )
    return {"success": True, "result": result}


@app.get("/brain/predictive/accuracy", response_class=JSONResponse)
async def get_prediction_accuracy():
    """Get prediction accuracy statistics."""
    predictive = get_brain("predictive")
    accuracy = await predictive.get_prediction_accuracy(scope="user", user_id=DEMO_USER_ID)
    return {"success": True, "accuracy": accuracy}


@app.get("/brain/predictive/", response_class=JSONResponse)
async def list_predictions(limit: int = 50):
    """List predictions for the demo user."""
    predictive = get_brain("predictive")
    predictions = await predictive.get_predictions(scope="user", user_id=DEMO_USER_ID, limit=limit)
    return {"success": True, "count": len(predictions), "predictions": predictions}


# =============================================================================
# PERFECT BRAIN: MEMORY VERSIONING
# =============================================================================


@app.get("/brain/versioning/{entity_name}", response_class=JSONResponse)
async def get_version_history(entity_name: str):
    """
    Get belief evolution history for an entity.

    Tracks how the system's understanding of an entity changed over time.
    """
    versioning = get_brain("versioning")
    history = await versioning.get_version_history(
        entity_name=entity_name,
        scope="user",
        user_id=DEMO_USER_ID,
    )
    return {"success": True, "entity": entity_name, "versions": history}


@app.get("/brain/versioning/{entity_name}/at/{timestamp}", response_class=JSONResponse)
async def get_belief_at_time(entity_name: str, timestamp: str):
    """Get what the system believed about an entity at a specific time."""
    versioning = get_brain("versioning")
    try:
        dt = datetime.fromisoformat(timestamp)
    except ValueError:
        raise HTTPException(400, f"Invalid timestamp format: {timestamp}. Use ISO 8601.")
    belief = await versioning.get_belief_at_time(
        entity_name=entity_name,
        timestamp=dt,
        scope="user",
        user_id=DEMO_USER_ID,
    )
    return {"success": True, "entity": entity_name, "at": timestamp, "belief": belief}


# =============================================================================
# PERFECT BRAIN: QUERY-AWARE RECALL
# =============================================================================


@app.post("/brain/recall/", response_class=JSONResponse)
async def policy_driven_recall(request: RecallRequest):
    """
    Policy-driven memory retrieval.

    Adapts recall strategy based on:
    - task_type: "fast_answer" | "general" | "critical_decision" | "exploration"
    - risk_tolerance: "low" | "medium" | "high"
    - latency_budget: "fast" | "normal" | "deep"
    """
    recall = get_brain("recall")
    memory = get_memory_service()

    # Get the underlying collection from the memory service
    collection = getattr(memory, "collection", None)
    if not collection:
        raise HTTPException(503, "Cannot access memory collection for recall")

    result = await recall.recall(
        query=request.query,
        user_id=DEMO_USER_ID,
        collection=collection,
        task_type=request.task_type,
        risk_tolerance=request.risk_tolerance,
        latency_budget=request.latency_budget,
        scope="user",
        memory_veto=brain.get("veto"),
    )

    return {
        "success": True,
        "query": request.query,
        "policy": {
            "task_type": request.task_type,
            "risk_tolerance": request.risk_tolerance,
            "latency_budget": request.latency_budget,
        },
        "result": result,
    }


# =============================================================================
# PERFECT BRAIN: BRAIN HEALTH & MAINTENANCE
# =============================================================================


@app.post("/brain/health/consolidate", response_class=JSONResponse)
async def trigger_consolidation():
    """
    Trigger memory consolidation (episodic -> semantic distillation).

    The consolidator extracts entities and procedures from raw episodes,
    stores them in semantic/procedural memory, and marks episodes as processed.
    """
    try:
        scoped_db = engine.get_scoped_db(APP_SLUG)
        consolidator = MemoryConsolidator(
            db_client=scoped_db,
            db_name=get_db_name(fallback="perfect_brain_db"),
            model="gpt-4o",
        )
        result = await asyncio.to_thread(
            consolidator.consolidate_episodes, agent_id=DEMO_USER_ID,
        )
        return {"success": True, "consolidation": result}
    except Exception as e:
        logger.error(f"Consolidation failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@app.post("/brain/health/hygiene", response_class=JSONResponse)
async def trigger_hygiene():
    """
    Run daily brain hygiene (consolidation + maintenance).

    Automates the "learning" process: distill episodic memories into
    structured knowledge without any decay or forgetting.
    """
    try:
        scoped_db = engine.get_scoped_db(APP_SLUG)
        result = await run_daily_hygiene(
            agent_id=DEMO_USER_ID,
            db_client=scoped_db,
            db_name=get_db_name(fallback="perfect_brain_db"),
        )
        return {"success": True, "hygiene": result}
    except Exception as e:
        logger.error(f"Hygiene failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@app.get("/brain/dashboard", response_class=JSONResponse)
async def brain_dashboard():
    """
    Complete view of all brain subsystems.

    Returns stats for: memory, graph, prospective, vetoes, shared,
    reflective, predictive, versioning, and timelines.
    """
    dashboard: dict[str, Any] = {"success": True, "subsystems": {}}

    # Memory stats
    try:
        memory = engine.get_memory_service(APP_SLUG)
        if memory:
            all_mem = await memory.get_all(user_id=DEMO_USER_ID, limit=10000)
            dashboard["subsystems"]["memory"] = {
                "status": "active",
                "total_memories": len(all_mem),
            }
            if hasattr(memory, "get_memory_analytics"):
                analytics = await memory.get_memory_analytics(user_id=DEMO_USER_ID)
                dashboard["subsystems"]["memory"]["analytics"] = analytics
    except Exception as e:
        dashboard["subsystems"]["memory"] = {"status": "error", "error": str(e)}

    # Graph stats
    try:
        graph = engine.get_graph_service(APP_SLUG)
        if graph and graph.enabled:
            stats = await graph.get_stats()
            dashboard["subsystems"]["graph"] = {"status": "active", "stats": stats}
    except Exception as e:
        dashboard["subsystems"]["graph"] = {"status": "error", "error": str(e)}

    # Prospective triggers
    try:
        if brain.get("prospective"):
            triggers = await brain["prospective"].get_active_triggers(user_id=DEMO_USER_ID)
            dashboard["subsystems"]["prospective"] = {
                "status": "active", "active_triggers": len(triggers),
            }
    except Exception as e:
        dashboard["subsystems"]["prospective"] = {"status": "error", "error": str(e)}

    # Vetoes
    try:
        if brain.get("veto"):
            vetoes = await brain["veto"].get_user_vetoes(user_id=DEMO_USER_ID)
            dashboard["subsystems"]["veto"] = {
                "status": "active", "vetoed_memories": len(vetoes),
            }
    except Exception as e:
        dashboard["subsystems"]["veto"] = {"status": "error", "error": str(e)}

    # Shared memory
    try:
        if brain.get("shared"):
            shared = await brain["shared"].get_shared_memory(group_id=DEMO_GROUP_ID)
            dashboard["subsystems"]["shared"] = {
                "status": "active", "group_id": DEMO_GROUP_ID, "shared_count": len(shared),
            }
    except Exception as e:
        dashboard["subsystems"]["shared"] = {"status": "error", "error": str(e)}

    # Reflective memory
    try:
        if brain.get("reflective"):
            reflections = await brain["reflective"].get_reflections(scope="user", user_id=DEMO_USER_ID)
            dashboard["subsystems"]["reflective"] = {
                "status": "active", "reflections_count": len(reflections),
            }
    except Exception as e:
        dashboard["subsystems"]["reflective"] = {"status": "error", "error": str(e)}

    # Predictive memory
    try:
        if brain.get("predictive"):
            predictions = await brain["predictive"].get_predictions(scope="user", user_id=DEMO_USER_ID)
            accuracy = await brain["predictive"].get_prediction_accuracy(scope="user", user_id=DEMO_USER_ID
            )
            dashboard["subsystems"]["predictive"] = {
                "status": "active", "predictions_count": len(predictions),
                "accuracy": accuracy,
            }
    except Exception as e:
        dashboard["subsystems"]["predictive"] = {"status": "error", "error": str(e)}

    # Recall policy
    dashboard["subsystems"]["recall"] = {
        "status": "active" if brain.get("recall") else "inactive",
        "modes": ["fast_answer", "general", "critical_decision", "exploration"],
    }

    # Versioning
    dashboard["subsystems"]["versioning"] = {
        "status": "active" if brain.get("versioning") else "inactive",
    }

    # Timeline
    dashboard["subsystems"]["timeline"] = {
        "status": "active" if brain.get("timeline") else "inactive",
    }

    return dashboard


# =============================================================================
# DEMO DATA SEEDING
# =============================================================================


@app.post("/demo/seed", response_class=JSONResponse)
async def seed_demo_data():
    """
    Seed the database with rich demo data across ALL brain subsystems.

    Creates:
    - 20 memories across 10 categories with rich metadata
    - 12 graph nodes with 12 relationship edges
    - 4 prospective triggers
    - 3 reflections
    - 3 predictions
    - 1 shared memory
    """
    memory = get_memory_service()
    results: dict[str, Any] = {}

    # ── Memories ─────────────────────────────────────────────────────────
    demo_memories = [
        # Biographical
        {"memory": "User's name is John Smith", "category": "biographical",
         "importance": 1.0, "bucket_id": "identity"},
        {"memory": "User is 35 years old, born on March 15, 1990", "category": "biographical",
         "importance": 0.9, "bucket_id": "identity"},
        {"memory": "User works as a Senior Software Engineer at TechCorp", "category": "biographical",
         "importance": 0.8, "bucket_id": "identity"},
        {"memory": "User lives in San Francisco, California", "category": "biographical",
         "importance": 0.7, "bucket_id": "identity"},
        # Preferences
        {"memory": "User prefers dark mode in all applications", "category": "preferences",
         "importance": 0.6, "bucket_id": "ui"},
        {"memory": "User is vegetarian and allergic to peanuts", "category": "preferences",
         "importance": 0.95, "bucket_id": "dietary"},
        {"memory": "User's favorite programming language is Python", "category": "preferences",
         "importance": 0.5, "bucket_id": "tech"},
        {"memory": "User prefers morning meetings, never schedule after 4pm", "category": "preferences",
         "importance": 0.7, "bucket_id": "scheduling"},
        # Work
        {"memory": "User is leading the Q1 data migration project", "category": "work",
         "importance": 0.8, "bucket_id": "projects"},
        {"memory": "User's manager is Sarah Johnson", "category": "work",
         "importance": 0.6, "bucket_id": "team"},
        {"memory": "User has a weekly 1:1 meeting every Tuesday at 10am", "category": "work",
         "importance": 0.5, "bucket_id": "schedule"},
        # Health
        {"memory": "User takes blood pressure medication daily in the morning", "category": "health",
         "importance": 0.9, "bucket_id": "medications"},
        {"memory": "User runs 5km every morning for exercise", "category": "health",
         "importance": 0.5, "bucket_id": "fitness"},
        # Finance
        {"memory": "User is saving for a house down payment, goal is $100k by 2026", "category": "finance",
         "importance": 0.7, "bucket_id": "goals"},
        {"memory": "User prefers index funds over individual stocks", "category": "finance",
         "importance": 0.5, "bucket_id": "investments"},
        # Travel
        {"memory": "User visited Japan in 2023 and loved it, wants to return", "category": "travel",
         "importance": 0.4, "bucket_id": "history"},
        {"memory": "User prefers aisle seats on flights", "category": "travel",
         "importance": 0.3, "bucket_id": "preferences"},
        # Hobbies
        {"memory": "User plays chess competitively, ELO rating around 1800", "category": "hobbies",
         "importance": 0.5, "bucket_id": "games"},
        {"memory": "User is learning to play guitar, started 6 months ago", "category": "hobbies",
         "importance": 0.4, "bucket_id": "music"},
        # Goals
        {"memory": "User wants to become a Staff Engineer within 2 years", "category": "goals",
         "importance": 0.8, "bucket_id": "career"},
    ]

    injected = []
    for mem_data in demo_memories:
        result = await memory.inject(
            memory=mem_data["memory"],
            user_id=DEMO_USER_ID,
            metadata={
                "category": mem_data["category"],
                "manual_importance": mem_data.get("importance"),
                "source": "demo_seed",
            },
            bucket_id=mem_data.get("bucket_id"),
            bucket_type=mem_data["category"],
        )
        injected.append({"id": result.get("id"), "memory": mem_data["memory"][:50] + "..."})

    results["memories"] = {"count": len(injected), "items": injected}

    # ── Graph ────────────────────────────────────────────────────────────
    graph = engine.get_graph_service(APP_SLUG)
    nodes_created = 0
    edges_created = 0

    if graph and graph.enabled:
        demo_nodes = [
            {"node_id": "person:john_smith", "node_type": "person", "name": "John Smith",
             "properties": {"occupation": "Senior Software Engineer", "age": 35}},
            {"node_id": "person:sarah_johnson", "node_type": "person", "name": "Sarah Johnson",
             "properties": {"role": "Manager"}},
            {"node_id": "organization:techcorp", "node_type": "organization", "name": "TechCorp",
             "properties": {"industry": "Technology"}},
            {"node_id": "location:san_francisco", "node_type": "location", "name": "San Francisco",
             "properties": {"state": "CA", "country": "USA"}},
            {"node_id": "location:japan", "node_type": "location", "name": "Japan",
             "properties": {"type": "country", "visited": 2023}},
            {"node_id": "interest:python", "node_type": "skill", "name": "Python Programming",
             "properties": {"category": "technology"}},
            {"node_id": "interest:chess", "node_type": "interest", "name": "Chess",
             "properties": {"elo_rating": 1800, "level": "competitive"}},
            {"node_id": "interest:guitar", "node_type": "interest", "name": "Guitar",
             "properties": {"level": "beginner", "duration": "6 months"}},
            {"node_id": "interest:running", "node_type": "interest", "name": "Running",
             "properties": {"distance": "5km", "frequency": "daily"}},
            {"node_id": "project:q1_migration", "node_type": "project", "name": "Q1 Data Migration",
             "properties": {"quarter": "Q1", "role": "lead"}},
            {"node_id": "goal:house_savings", "node_type": "goal", "name": "House Down Payment",
             "properties": {"target": 100000, "deadline": "2026"}},
            {"node_id": "goal:staff_engineer", "node_type": "goal", "name": "Staff Engineer Promotion",
             "properties": {"timeline": "2 years"}},
        ]

        for nd in demo_nodes:
            await graph.upsert_node(
                node_id=nd["node_id"], node_type=nd["node_type"],
                name=nd["name"], properties=nd.get("properties", {}),
                user_id=DEMO_USER_ID,
            )
            nodes_created += 1

        demo_edges = [
            {"source": "person:john_smith", "relation": "works_at", "target": "organization:techcorp"},
            {"source": "person:john_smith", "relation": "reports_to", "target": "person:sarah_johnson"},
            {"source": "person:john_smith", "relation": "leading", "target": "project:q1_migration"},
            {"source": "person:john_smith", "relation": "lives_in", "target": "location:san_francisco"},
            {"source": "person:john_smith", "relation": "visited", "target": "location:japan"},
            {"source": "organization:techcorp", "relation": "located_in", "target": "location:san_francisco"},
            {"source": "person:john_smith", "relation": "expert_in", "target": "interest:python"},
            {"source": "person:john_smith", "relation": "plays", "target": "interest:chess"},
            {"source": "person:john_smith", "relation": "learning", "target": "interest:guitar"},
            {"source": "person:john_smith", "relation": "does", "target": "interest:running"},
            {"source": "person:john_smith", "relation": "saving_for", "target": "goal:house_savings"},
            {"source": "person:john_smith", "relation": "pursuing", "target": "goal:staff_engineer"},
        ]

        for edge in demo_edges:
            success = await graph.add_edge(
                source_id=edge["source"], relation=edge["relation"],
                target_id=edge["target"], weight=0.9,
            )
            if success:
                edges_created += 1

    results["graph"] = {"nodes_created": nodes_created, "edges_created": edges_created}

    # ── Prospective Triggers ─────────────────────────────────────────────
    try:
        prospective = brain.get("prospective")
        if prospective:
            triggers_data = [
                {
                    "condition": "user mentions project deadline or timeline for data migration",
                    "action": "Remind the user about the pending risk assessment for the Q1 migration project",
                },
                {
                    "condition": "user asks about restaurants or food recommendations",
                    "action": "Remember: user is vegetarian and allergic to peanuts. Only suggest safe options.",
                },
                {
                    "condition": "user mentions Japan trip or travel planning for Asia",
                    "action": "User loved Japan in 2023 and wants to return. Suggest itinerary ideas.",
                },
                {
                    "condition": "user asks about investment advice or portfolio allocation",
                    "action": "User prefers index funds over individual stocks. Frame advice accordingly.",
                },
            ]
            seeded_triggers = []
            for t in triggers_data:
                tid = await prospective.set_trigger(
                    condition=t["condition"], action=t["action"], user_id=DEMO_USER_ID,
                )
                seeded_triggers.append(tid)
            results["prospective_triggers"] = {"count": len(seeded_triggers)}
    except Exception as e:
        results["prospective_triggers"] = {"error": str(e)}

    # ── Reflections ──────────────────────────────────────────────────────
    try:
        reflective = brain.get("reflective")
        if reflective:
            reflections_data = [
                {
                    "reflection": "I notice this user values precision and structured information over casual conversation",
                    "trigger": "pattern_analysis",
                    "confidence": 0.85,
                },
                {
                    "reflection": "Health-related memories should always be treated with highest importance due to safety implications",
                    "trigger": "safety_review",
                    "confidence": 0.95,
                },
                {
                    "reflection": "The user's work and career goals are deeply interconnected - promotions depend on project success",
                    "trigger": "relationship_discovery",
                    "confidence": 0.75,
                },
            ]
            for r in reflections_data:
                await reflective.store_reflection(
                    reflection=r["reflection"], trigger=r["trigger"],
                    confidence=r["confidence"], scope="user", user_id=DEMO_USER_ID,
                )
            results["reflections"] = {"count": len(reflections_data)}
    except Exception as e:
        results["reflections"] = {"error": str(e)}

    # ── Predictions ──────────────────────────────────────────────────────
    try:
        predictive = brain.get("predictive")
        if predictive:
            predictions_data = [
                {
                    "scenario": "User will ask about Japan trip planning within the next quarter",
                    "origin": "pattern",
                    "confidence": 0.7,
                },
                {
                    "scenario": "If the Q1 migration project succeeds, user will push for Staff Engineer promotion",
                    "origin": "hypothesis",
                    "confidence": 0.8,
                },
                {
                    "scenario": "User engagement increases when responses include code examples in Python",
                    "origin": "simulation",
                    "confidence": 0.65,
                },
            ]
            for p in predictions_data:
                await predictive.store_prediction(
                    scenario=p["scenario"], origin=p["origin"],
                    confidence=p["confidence"], scope="user", user_id=DEMO_USER_ID,
                )
            results["predictions"] = {"count": len(predictions_data)}
    except Exception as e:
        results["predictions"] = {"error": str(e)}

    # ── Shared Memory ────────────────────────────────────────────────────
    try:
        shared = brain.get("shared")
        if shared:
            await shared.promote_to_shared(
                fact="Team members generally prefer morning standup meetings over afternoon ones",
                source_user_ids=[DEMO_USER_ID, "colleague_456"],
                confidence=0.85,
                group_id=DEMO_GROUP_ID,
            )
            results["shared_memory"] = {"count": 1}
    except Exception as e:
        results["shared_memory"] = {"error": str(e)}

    return {
        "success": True,
        "message": "Perfect Brain seeded with rich demo data",
        "results": results,
    }


@app.post("/demo/reset", response_class=JSONResponse)
async def reset_demo_data():
    """Delete ALL demo data across every brain subsystem."""
    memory = get_memory_service()
    deleted: dict[str, Any] = {}

    # Delete memories
    all_memories = await memory.get_all(user_id=DEMO_USER_ID, limit=10000)
    memory_count = len(all_memories)
    await memory.delete_all(user_id=DEMO_USER_ID)
    deleted["memories"] = memory_count

    # Delete graph nodes
    graph = engine.get_graph_service(APP_SLUG)
    if graph and graph.enabled:
        all_nodes = await graph.list_nodes(user_id=DEMO_USER_ID, limit=1000)
        for node in all_nodes:
            await graph.delete_node(node["_id"])
        deleted["graph_nodes"] = len(all_nodes)

    # Clear brain subsystem collections
    for subsystem_name in ["prospective", "veto", "shared", "reflective", "predictive"]:
        svc = brain.get(subsystem_name)
        if svc and hasattr(svc, "collection"):
            try:
                await asyncio.to_thread(svc.collection.delete_many, {})
                deleted[subsystem_name] = "cleared"
            except Exception as e:
                deleted[subsystem_name] = f"error: {e}"

    # Clear shared collection specifically
    if brain.get("shared") and hasattr(brain["shared"], "shared_collection"):
        try:
            await asyncio.to_thread(brain["shared"].shared_collection.delete_many, {})
            deleted["shared_collection"] = "cleared"
        except Exception:
            logger.debug("Failed to clear shared collection during cleanup", exc_info=True)

    return {"success": True, "deleted": deleted}


# =============================================================================
# RUN
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
