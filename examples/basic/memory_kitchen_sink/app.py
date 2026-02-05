#!/usr/bin/env python3
"""
Memory Kitchen Sink - MDB-Engine Memory + Graph Features Demo

This example demonstrates ALL memory service features using inject()
PLUS graph/knowledge graph features:

MEMORY FEATURES:
- Basic memory injection with metadata
- Memory categories and buckets
- Semantic search with filters
- Memory updates (automatic re-embedding)
- Memory deletion (single and bulk)
- Cognitive features:
  - Memory analytics
  - Memory pruning (soft-delete)
  - Cold storage retrieval
  - Cold storage restoration
  - Knowledge conflict detection
- Redaction (PII protection)

GRAPH FEATURES:
- Node CRUD operations
- Edge management (relationships)
- Graph traversal using MongoDB $graphLookup
- Hybrid search (GraphRAG)
- LLM-powered graph node extraction
- Memory-to-graph integration

NO CSFLE - just pure memory + graph features!

Run with:
    uvicorn app:app --reload --port 8000
"""

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from mdb_engine import MongoDBEngine

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("memory_kitchen_sink")

# App configuration
APP_SLUG = "memory_kitchen_sink"
DEMO_USER_ID = "demo_user_123"

# Initialize engine (NO CSFLE!)
engine = MongoDBEngine(
    mongo_uri=os.getenv("MONGO_URI", "mongodb://localhost:27017"),
    db_name=os.getenv("MONGO_DB_NAME", "memory_kitchen_sink_db"),
)

# Create FastAPI app
app = engine.create_app(
    slug=APP_SLUG,
    manifest=Path(__file__).parent / "manifest.json",
    title="Memory Kitchen Sink",
    version="1.0.0",
)


# =============================================================================
# Pydantic Models for Request Bodies
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


# =============================================================================
# Graph Request Models
# =============================================================================


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
    """Request body for LLM-powered graph node extraction."""
    text: str
    auto_create: bool = True


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
        raise HTTPException(503, "Graph service not available. Check manifest configuration.")
    return graph


# =============================================================================
# HEALTH & INFO ENDPOINTS
# =============================================================================


@app.get("/", response_class=JSONResponse)
async def root():
    """Welcome endpoint with API overview."""
    return {
        "app": "Memory Kitchen Sink",
        "description": "Demonstrates ALL MDB-Engine memory + graph features using inject()",
        "user_id": DEMO_USER_ID,
        "endpoints": {
            "memory": {
                "inject": "POST /memories/inject - Inject a memory directly",
                "search": "POST /memories/search - Semantic search memories",
                "get_all": "GET /memories - Get all memories",
                "get_one": "GET /memories/{id} - Get single memory",
                "update": "PUT /memories/{id} - Update a memory",
                "delete": "DELETE /memories/{id} - Delete a memory",
                "delete_all": "DELETE /memories - Delete all memories",
                "analytics": "GET /memories/analytics - Get memory analytics",
                "prune": "POST /memories/prune - Trigger memory pruning",
                "cold_storage": "GET /memories/cold-storage - Get pruned memories",
                "restore": "POST /memories/{id}/restore - Restore from cold storage",
                "conflict": "POST /memories/check-conflict - Check for conflicts",
                "categories": "GET /memories/categories - Get available categories",
            },
            "graph": {
                "stats": "GET /graph/stats - Get graph statistics",
                "create_node": "POST /graph/nodes - Create/update a node",
                "list_nodes": "GET /graph/nodes - List all nodes",
                "get_node": "GET /graph/nodes/{node_id} - Get a node",
                "delete_node": "DELETE /graph/nodes/{node_id} - Delete a node",
                "add_edge": "POST /graph/edges - Add edge between nodes",
                "remove_edge": "DELETE /graph/edges - Remove an edge",
                "traverse": "GET /graph/traverse/{node_id} - Traverse from node",
                "neighbors": "GET /graph/neighbors/{node_id} - Get neighbors",
                "search": "POST /graph/search - Hybrid search (GraphRAG)",
                "extract": "POST /graph/extract - Extract entities from text",
                "extract_from_memory": "POST /memories/{id}/extract-graph - Extract graph from memory",
            },
            "demo": {
                "seed": "POST /demo/seed - Seed demo data (memories + graph)",
                "reset": "POST /demo/reset - Reset all demo data",
                "workflow_search": "GET /demo/workflow/search-and-update",
                "workflow_categories": "GET /demo/workflow/category-breakdown",
                "workflow_memory_to_graph": "GET /demo/workflow/memory-to-graph",
                "workflow_graphrag": "GET /demo/workflow/graphrag-search",
            },
        },
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    memory = engine.get_memory_service(APP_SLUG)
    graph = engine.get_graph_service(APP_SLUG)
    return {
        "status": "healthy",
        "engine_initialized": engine.initialized,
        "memory_service_available": memory is not None,
        "graph_service_available": graph is not None,
        "graph_enabled": graph.enabled if graph else False,
    }


# =============================================================================
# CORE MEMORY OPERATIONS (using inject!)
# =============================================================================


@app.post("/memories/inject", response_class=JSONResponse)
async def inject_memory(request: InjectMemoryRequest):
    """
    Inject a memory directly (bypasses LLM inference).
    
    This is the core method for adding memories without fact extraction.
    Supports:
    - Custom categories
    - Manual importance scoring
    - Bucket organization
    - Rich metadata
    """
    memory = get_memory_service()
    
    # Build metadata with category
    metadata = request.metadata or {}
    metadata["category"] = request.category
    metadata["source"] = "manual_injection"
    metadata["injected_at"] = datetime.utcnow().isoformat()
    
    if request.importance is not None:
        metadata["manual_importance"] = max(0.1, min(1.0, request.importance))
    
    # Inject the memory
    result = await asyncio.to_thread(
        memory.inject,
        memory=request.memory,
        user_id=DEMO_USER_ID,
        metadata=metadata,
        bucket_id=request.bucket_id,
        bucket_type=request.bucket_type,
    )
    
    logger.info(f"✅ Injected memory: {result.get('id')}")
    
    return {
        "success": True,
        "memory": result,
        "message": f"Memory injected with category '{request.category}'",
    }


@app.post("/memories/search", response_class=JSONResponse)
async def search_memories(request: SearchMemoriesRequest):
    """
    Semantic search across memories using MongoDB Atlas Vector Search.
    
    Supports filtering by:
    - Category
    - Bucket ID
    - Custom metadata fields
    """
    memory = get_memory_service()
    
    # Build filters
    filters = None
    if request.category or request.bucket_id:
        filters = {"metadata": {}}
        if request.category:
            filters["metadata"]["category"] = request.category
        if request.bucket_id:
            filters["metadata"]["bucket_id"] = request.bucket_id
    
    results = await asyncio.to_thread(
        memory.search,
        query=request.query,
        user_id=DEMO_USER_ID,
        limit=request.limit,
        filters=filters,
    )
    
    return {
        "success": True,
        "query": request.query,
        "filters": filters,
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
    
    memories = await asyncio.to_thread(
        memory.get_all,
        user_id=DEMO_USER_ID,
        limit=limit,
        filters=filters,
    )
    
    return {
        "success": True,
        "count": len(memories),
        "memories": memories,
    }


@app.get("/memories/{memory_id}", response_class=JSONResponse)
async def get_memory(memory_id: str):
    """Get a single memory by ID."""
    memory = get_memory_service()
    
    result = await asyncio.to_thread(
        memory.get,
        memory_id=memory_id,
        user_id=DEMO_USER_ID,
    )
    
    if not result:
        raise HTTPException(404, f"Memory {memory_id} not found")
    
    return {"success": True, "memory": result}


@app.put("/memories/{memory_id}", response_class=JSONResponse)
async def update_memory(memory_id: str, request: UpdateMemoryRequest):
    """
    Update a memory's content and/or metadata.
    
    If content changes, the embedding is automatically regenerated!
    """
    memory = get_memory_service()
    
    result = await asyncio.to_thread(
        memory.update,
        memory_id=memory_id,
        user_id=DEMO_USER_ID,
        memory=request.memory,
        metadata=request.metadata,
    )
    
    if not result:
        raise HTTPException(404, f"Memory {memory_id} not found")
    
    return {
        "success": True,
        "memory": result,
        "message": "Memory updated (embedding regenerated if content changed)",
    }


@app.delete("/memories/{memory_id}", response_class=JSONResponse)
async def delete_memory(memory_id: str):
    """Delete a single memory."""
    memory = get_memory_service()
    
    success = await asyncio.to_thread(
        memory.delete,
        memory_id=memory_id,
        user_id=DEMO_USER_ID,
    )
    
    return {
        "success": success,
        "message": f"Memory {memory_id} deleted" if success else "Failed to delete",
    }


@app.delete("/memories", response_class=JSONResponse)
async def delete_all_memories():
    """Delete ALL memories for the demo user."""
    memory = get_memory_service()
    
    # Get count first
    all_memories = await asyncio.to_thread(
        memory.get_all,
        user_id=DEMO_USER_ID,
        limit=1000,
    )
    count = len(all_memories)
    
    success = await asyncio.to_thread(
        memory.delete_all,
        user_id=DEMO_USER_ID,
    )
    
    return {
        "success": success,
        "deleted_count": count if success else 0,
        "message": f"Deleted {count} memories" if success else "Failed to delete",
    }


# =============================================================================
# COGNITIVE FEATURES
# =============================================================================


@app.get("/memories/analytics", response_class=JSONResponse)
async def get_memory_analytics():
    """
    Get cognitive memory analytics.
    
    Returns metrics like:
    - Active vs cold storage memories
    - Average strength and stability
    - Weak/strong memory counts
    - Category breakdown
    """
    memory = get_memory_service()
    
    if not hasattr(memory, "get_memory_analytics"):
        raise HTTPException(501, "Analytics not available for this memory provider")
    
    analytics = await asyncio.to_thread(
        memory.get_memory_analytics,
        user_id=DEMO_USER_ID,
    )
    
    return {"success": True, "analytics": analytics}


@app.post("/memories/prune", response_class=JSONResponse)
async def prune_memories(max_capacity: int | None = None, reason: str = "manual_trigger"):
    """
    Trigger memory pruning.
    
    Soft-deletes the weakest memories based on retrieval strength,
    moving them to cold storage for potential recovery.
    """
    memory = get_memory_service()
    
    if not hasattr(memory, "prune_memories"):
        raise HTTPException(501, "Pruning not available for this memory provider")
    
    pruned_count = await asyncio.to_thread(
        memory.prune_memories,
        user_id=DEMO_USER_ID,
        max_capacity=max_capacity,
        reason=reason,
    )
    
    return {
        "success": True,
        "pruned_count": pruned_count,
        "message": f"Pruned {pruned_count} memories to cold storage",
    }


@app.get("/memories/cold-storage", response_class=JSONResponse)
async def get_cold_storage(limit: int = 50):
    """
    Get memories from cold storage (pruned/inactive memories).
    
    Cold storage provides:
    - Audit trail of forgotten memories
    - Recovery capability
    - Analytics on memory patterns
    """
    memory = get_memory_service()
    
    if not hasattr(memory, "get_cold_storage"):
        raise HTTPException(501, "Cold storage not available for this memory provider")
    
    cold_memories = await asyncio.to_thread(
        memory.get_cold_storage,
        user_id=DEMO_USER_ID,
        limit=limit,
        include_reason=True,
    )
    
    return {
        "success": True,
        "count": len(cold_memories),
        "memories": cold_memories,
    }


@app.post("/memories/{memory_id}/restore", response_class=JSONResponse)
async def restore_from_cold_storage(memory_id: str):
    """
    Restore a memory from cold storage to active status.
    
    Allows recovery of accidentally pruned or needed memories.
    """
    memory = get_memory_service()
    
    if not hasattr(memory, "restore_from_cold_storage"):
        raise HTTPException(501, "Restore not available for this memory provider")
    
    restored = await asyncio.to_thread(
        memory.restore_from_cold_storage,
        memory_id=memory_id,
        user_id=DEMO_USER_ID,
    )
    
    if not restored:
        raise HTTPException(404, f"Memory {memory_id} not found in cold storage")
    
    return {
        "success": True,
        "memory": restored,
        "message": f"Memory {memory_id} restored from cold storage",
    }


@app.post("/memories/check-conflict", response_class=JSONResponse)
async def check_knowledge_conflict(request: ConflictCheckRequest):
    """
    Check if new information conflicts with existing knowledge.
    
    Prevents the AI from developing "digital dementia" -
    holding contradictory facts as equally true.
    """
    memory = get_memory_service()
    
    # Try sync version first, then async
    if hasattr(memory, "detect_knowledge_conflict_sync"):
        conflict = await asyncio.to_thread(
            memory.detect_knowledge_conflict_sync,
            user_id=DEMO_USER_ID,
            new_fact=request.fact,
        )
    elif hasattr(memory, "detect_knowledge_conflict"):
        conflict = await memory.detect_knowledge_conflict(
            user_id=DEMO_USER_ID,
            new_fact=request.fact,
        )
    else:
        raise HTTPException(501, "Conflict detection not available")
    
    return {
        "success": True,
        "fact": request.fact,
        "has_conflict": conflict is not None,
        "conflict_description": conflict,
    }


# =============================================================================
# CATEGORIES & ORGANIZATION
# =============================================================================


@app.get("/memories/categories", response_class=JSONResponse)
async def get_categories():
    """Get available memory categories."""
    categories = [
        {"id": "biographical", "name": "Biographical", "icon": "👤", 
         "description": "Personal info: name, age, occupation, family, location"},
        {"id": "preferences", "name": "Preferences", "icon": "❤️", 
         "description": "Likes, dislikes, preferences, favorites"},
        {"id": "work", "name": "Work", "icon": "💼", 
         "description": "Job-related information and projects"},
        {"id": "health", "name": "Health", "icon": "🏥", 
         "description": "Health conditions, medications, fitness"},
        {"id": "finance", "name": "Finance", "icon": "💰", 
         "description": "Financial preferences and goals"},
        {"id": "travel", "name": "Travel", "icon": "✈️", 
         "description": "Travel history and preferences"},
        {"id": "hobbies", "name": "Hobbies", "icon": "🎮", 
         "description": "Hobbies, interests, activities"},
        {"id": "general", "name": "General", "icon": "📝", 
         "description": "Other facts and information"},
    ]
    
    return {"success": True, "categories": categories}


# =============================================================================
# GRAPH OPERATIONS
# =============================================================================


@app.get("/graph/stats", response_class=JSONResponse)
async def get_graph_stats():
    """Get graph statistics including node/edge counts by type."""
    graph = get_graph_service()
    stats = await asyncio.to_thread(graph.get_stats)
    return {"success": True, "stats": stats}


@app.post("/graph/nodes", response_class=JSONResponse)
async def create_graph_node(request: CreateNodeRequest):
    """Create or update a node in the graph."""
    graph = get_graph_service()
    
    result = await asyncio.to_thread(
        graph.upsert_node,
        node_id=request.node_id,
        node_type=request.node_type,
        name=request.name,
        properties=request.properties or {},
        user_id=DEMO_USER_ID,
    )
    
    logger.info(f"Created/updated graph node: {request.node_id}")
    
    return {
        "success": True,
        "node": result,
        "message": f"Node '{request.node_id}' created/updated",
    }


@app.get("/graph/nodes", response_class=JSONResponse)
async def list_graph_nodes(node_type: str | None = None, limit: int = 100):
    """List all nodes in the graph."""
    graph = get_graph_service()
    
    nodes = await asyncio.to_thread(
        graph.list_nodes,
        node_type=node_type,
        user_id=DEMO_USER_ID,
        limit=limit,
    )
    
    # Clean up for JSON serialization
    cleaned = []
    for node in nodes:
        cleaned.append({
            "_id": node.get("_id"),
            "type": node.get("type"),
            "name": node.get("name"),
            "properties": node.get("properties", {}),
            "edges": node.get("edges", []),
        })
    
    return {
        "success": True,
        "count": len(cleaned),
        "filter": {"node_type": node_type} if node_type else None,
        "nodes": cleaned,
    }


@app.get("/graph/nodes/{node_id:path}", response_class=JSONResponse)
async def get_graph_node(node_id: str):
    """Get a single node by ID."""
    graph = get_graph_service()
    
    node = await asyncio.to_thread(graph.get_node, node_id)
    
    if not node:
        raise HTTPException(404, f"Node '{node_id}' not found")
    
    return {
        "success": True,
        "node": {
            "_id": node.get("_id"),
            "type": node.get("type"),
            "name": node.get("name"),
            "properties": node.get("properties", {}),
            "edges": node.get("edges", []),
        },
    }


@app.delete("/graph/nodes/{node_id:path}", response_class=JSONResponse)
async def delete_graph_node(node_id: str):
    """Delete a node and all edges pointing to it."""
    graph = get_graph_service()
    
    success = await asyncio.to_thread(graph.delete_node, node_id)
    
    return {
        "success": success,
        "message": f"Node '{node_id}' deleted" if success else f"Node '{node_id}' not found",
    }


@app.post("/graph/edges", response_class=JSONResponse)
async def add_graph_edge(request: AddEdgeRequest):
    """Add an edge (relationship) between two nodes."""
    graph = get_graph_service()
    
    # Ensure source node exists
    source = await asyncio.to_thread(graph.get_node, request.source_id)
    if not source:
        raise HTTPException(404, f"Source node '{request.source_id}' not found")
    
    success = await asyncio.to_thread(
        graph.add_edge,
        source_id=request.source_id,
        relation=request.relation,
        target_id=request.target_id,
        properties=request.properties or {},
        weight=request.weight,
    )
    
    return {
        "success": success,
        "edge": {
            "source": request.source_id,
            "relation": request.relation,
            "target": request.target_id,
            "weight": request.weight,
        },
        "message": f"Edge added: {request.source_id} --{request.relation}--> {request.target_id}" if success else "Failed",
    }


@app.delete("/graph/edges", response_class=JSONResponse)
async def remove_graph_edge(request: RemoveEdgeRequest):
    """Remove an edge between two nodes."""
    graph = get_graph_service()
    
    success = await asyncio.to_thread(
        graph.remove_edge,
        source_id=request.source_id,
        relation=request.relation,
        target_id=request.target_id,
    )
    
    return {
        "success": success,
        "message": f"Edge removed" if success else "Edge not found",
    }


@app.get("/graph/traverse/{node_id:path}", response_class=JSONResponse)
async def traverse_graph(node_id: str, max_depth: int = 2):
    """Traverse the graph from a starting node."""
    graph = get_graph_service()
    
    # Validate node exists
    node = await asyncio.to_thread(graph.get_node, node_id)
    if not node:
        raise HTTPException(404, f"Node '{node_id}' not found")
    
    max_depth = max(1, min(5, max_depth))
    
    results = await asyncio.to_thread(
        graph.traverse,
        start_id=node_id,
        max_depth=max_depth,
    )
    
    return {
        "success": True,
        "start_node": node_id,
        "max_depth": max_depth,
        "count": len(results),
        "nodes": results,
    }


@app.get("/graph/neighbors/{node_id:path}", response_class=JSONResponse)
async def get_graph_neighbors(node_id: str, relation: str | None = None):
    """Get immediate neighbors of a node (1-hop)."""
    graph = get_graph_service()
    
    neighbors = await asyncio.to_thread(
        graph.get_neighbors,
        node_id=node_id,
        relation=relation,
    )
    
    return {
        "success": True,
        "node_id": node_id,
        "count": len(neighbors),
        "neighbors": neighbors,
    }


@app.post("/graph/search", response_class=JSONResponse)
async def graph_hybrid_search(request: GraphSearchRequest):
    """
    Hybrid search combining vector similarity with graph traversal (GraphRAG).
    
    1. Vector search finds semantically similar entry nodes
    2. Graph traversal expands context from entry nodes
    3. Returns both for rich LLM context
    """
    graph = get_graph_service()
    
    results = await asyncio.to_thread(
        graph.hybrid_search,
        query=request.query,
        user_id=DEMO_USER_ID,
        max_depth=request.max_depth,
        vector_limit=request.limit,
    )
    
    # Format context for LLM usage
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
    """
    Extract entities and relationships from text using LLM.
    
    Automatically identifies:
    - People, organizations, locations, interests, events, etc.
    - Relationships between entities
    """
    graph = get_graph_service()
    
    result = await graph.extract_graph_from_text(
        text=request.text,
        user_id=DEMO_USER_ID,
        auto_create_nodes=request.auto_create,
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
    """
    Extract graph entities from an existing memory.
    
    This demonstrates memory-to-graph integration:
    1. Retrieves the memory text
    2. Uses LLM to extract entities and relationships
    3. Optionally creates nodes/edges in the graph
    """
    memory = get_memory_service()
    graph = get_graph_service()
    
    # Get the memory
    mem = await asyncio.to_thread(
        memory.get,
        memory_id=memory_id,
        user_id=DEMO_USER_ID,
    )
    
    if not mem:
        raise HTTPException(404, f"Memory '{memory_id}' not found")
    
    memory_text = mem.get("memory", "")
    if not memory_text:
        return {
            "success": False,
            "error": "Memory has no text content",
        }
    
    # Extract graph from memory text
    result = await graph.extract_graph_from_text(
        text=memory_text,
        user_id=DEMO_USER_ID,
        auto_create_nodes=auto_create,
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
# DEMO DATA SEEDING
# =============================================================================


@app.post("/demo/seed", response_class=JSONResponse)
async def seed_demo_memories():
    """
    Seed the database with demo memories across all categories.
    
    Demonstrates various memory features:
    - Different categories
    - Buckets for organization
    - Importance scores
    - Rich metadata
    - Redaction patterns (SSN, credit card)
    """
    memory = get_memory_service()
    
    demo_memories = [
        # Biographical
        {
            "memory": "User's name is John Smith",
            "category": "biographical",
            "importance": 1.0,
            "bucket_id": "identity",
            "metadata": {"type": "name", "verified": True}
        },
        {
            "memory": "User is 35 years old, born on March 15, 1990",
            "category": "biographical",
            "importance": 0.9,
            "bucket_id": "identity",
            "metadata": {"type": "age"}
        },
        {
            "memory": "User works as a Senior Software Engineer at TechCorp",
            "category": "biographical",
            "importance": 0.8,
            "bucket_id": "identity",
            "metadata": {"type": "occupation", "company": "TechCorp"}
        },
        {
            "memory": "User lives in San Francisco, California",
            "category": "biographical",
            "importance": 0.7,
            "bucket_id": "identity",
            "metadata": {"type": "location", "city": "San Francisco", "state": "CA"}
        },
        
        # Preferences
        {
            "memory": "User prefers dark mode in all applications",
            "category": "preferences",
            "importance": 0.6,
            "bucket_id": "ui_preferences",
            "metadata": {"type": "ui", "setting": "dark_mode"}
        },
        {
            "memory": "User is vegetarian and allergic to peanuts",
            "category": "preferences",
            "importance": 0.95,
            "bucket_id": "dietary",
            "metadata": {"type": "dietary", "allergies": ["peanuts"], "diet": "vegetarian"}
        },
        {
            "memory": "User's favorite programming language is Python",
            "category": "preferences",
            "importance": 0.5,
            "bucket_id": "tech_preferences",
            "metadata": {"type": "tech", "language": "Python"}
        },
        {
            "memory": "User prefers morning meetings, never schedule after 4pm",
            "category": "preferences",
            "importance": 0.7,
            "bucket_id": "scheduling",
            "metadata": {"type": "scheduling", "preferred_hours": "9am-4pm"}
        },
        
        # Work
        {
            "memory": "User is leading the Q1 data migration project",
            "category": "work",
            "importance": 0.8,
            "bucket_id": "projects",
            "metadata": {"project": "data_migration", "role": "lead", "quarter": "Q1"}
        },
        {
            "memory": "User's manager is Sarah Johnson",
            "category": "work",
            "importance": 0.6,
            "bucket_id": "team",
            "metadata": {"relationship": "manager", "name": "Sarah Johnson"}
        },
        {
            "memory": "User has a weekly 1:1 meeting every Tuesday at 10am",
            "category": "work",
            "importance": 0.5,
            "bucket_id": "schedule",
            "metadata": {"meeting_type": "1:1", "day": "Tuesday", "time": "10am"}
        },
        
        # Health
        {
            "memory": "User takes blood pressure medication daily in the morning",
            "category": "health",
            "importance": 0.9,
            "bucket_id": "medications",
            "metadata": {"condition": "blood_pressure", "frequency": "daily"}
        },
        {
            "memory": "User runs 5km every morning for exercise",
            "category": "health",
            "importance": 0.5,
            "bucket_id": "fitness",
            "metadata": {"activity": "running", "distance": "5km", "frequency": "daily"}
        },
        
        # Finance
        {
            "memory": "User is saving for a house down payment, goal is $100k by 2026",
            "category": "finance",
            "importance": 0.7,
            "bucket_id": "goals",
            "metadata": {"goal": "house_down_payment", "target": 100000, "deadline": "2026"}
        },
        {
            "memory": "User prefers index funds over individual stocks",
            "category": "finance",
            "importance": 0.5,
            "bucket_id": "investments",
            "metadata": {"preference": "index_funds"}
        },
        
        # Travel
        {
            "memory": "User visited Japan in 2023 and loved it, wants to return",
            "category": "travel",
            "importance": 0.4,
            "bucket_id": "history",
            "metadata": {"destination": "Japan", "year": 2023, "sentiment": "positive"}
        },
        {
            "memory": "User prefers aisle seats on flights",
            "category": "travel",
            "importance": 0.3,
            "bucket_id": "preferences",
            "metadata": {"type": "flight", "seat_preference": "aisle"}
        },
        
        # Hobbies
        {
            "memory": "User plays chess competitively, ELO rating around 1800",
            "category": "hobbies",
            "importance": 0.5,
            "bucket_id": "games",
            "metadata": {"hobby": "chess", "level": "competitive", "rating": 1800}
        },
        {
            "memory": "User is learning to play guitar, started 6 months ago",
            "category": "hobbies",
            "importance": 0.4,
            "bucket_id": "music",
            "metadata": {"hobby": "guitar", "duration": "6 months", "level": "beginner"}
        },
        
        # Test Redaction - These contain PII patterns that should be redacted
        {
            "memory": "User's SSN is 123-45-6789 (for tax purposes)",
            "category": "general",
            "importance": 0.1,
            "metadata": {"type": "test_redaction", "note": "SSN should be redacted"}
        },
        {
            "memory": "User's credit card ends in 4242-4242-4242-4242",
            "category": "general",
            "importance": 0.1,
            "metadata": {"type": "test_redaction", "note": "CC should be redacted"}
        },
    ]
    
    injected = []
    for mem_data in demo_memories:
        result = await asyncio.to_thread(
            memory.inject,
            memory=mem_data["memory"],
            user_id=DEMO_USER_ID,
            metadata={
                "category": mem_data["category"],
                "manual_importance": mem_data.get("importance"),
                "source": "demo_seed",
                **mem_data.get("metadata", {})
            },
            bucket_id=mem_data.get("bucket_id"),
            bucket_type=mem_data["category"],
        )
        injected.append({
            "id": result.get("id"),
            "memory": mem_data["memory"][:50] + "...",
            "category": mem_data["category"],
        })
    
    logger.info(f"✅ Seeded {len(injected)} demo memories")
    
    # Also seed graph data
    graph = engine.get_graph_service(APP_SLUG)
    nodes_created = 0
    edges_created = 0
    
    if graph and graph.enabled:
        # Create nodes based on seeded memories
        demo_nodes = [
            # People from memories
            {"node_id": "person:john_smith", "node_type": "person", "name": "John Smith",
             "properties": {"occupation": "Senior Software Engineer", "age": 35}},
            {"node_id": "person:sarah_johnson", "node_type": "person", "name": "Sarah Johnson",
             "properties": {"role": "Manager"}},
            
            # Organizations
            {"node_id": "organization:techcorp", "node_type": "organization", "name": "TechCorp",
             "properties": {"industry": "Technology"}},
            
            # Locations
            {"node_id": "location:san_francisco", "node_type": "location", "name": "San Francisco",
             "properties": {"state": "CA", "country": "USA"}},
            {"node_id": "location:japan", "node_type": "location", "name": "Japan",
             "properties": {"type": "country", "visited": 2023}},
            
            # Interests
            {"node_id": "interest:python", "node_type": "interest", "name": "Python Programming",
             "properties": {"category": "technology"}},
            {"node_id": "interest:chess", "node_type": "interest", "name": "Chess",
             "properties": {"elo_rating": 1800, "level": "competitive"}},
            {"node_id": "interest:guitar", "node_type": "interest", "name": "Guitar",
             "properties": {"level": "beginner", "duration": "6 months"}},
            {"node_id": "interest:running", "node_type": "interest", "name": "Running",
             "properties": {"distance": "5km", "frequency": "daily"}},
            {"node_id": "interest:vegetarian", "node_type": "interest", "name": "Vegetarian Diet",
             "properties": {"type": "dietary"}},
            
            # Events
            {"node_id": "event:q1_migration", "node_type": "event", "name": "Q1 Data Migration",
             "properties": {"quarter": "Q1", "role": "lead"}},
            
            # Concepts
            {"node_id": "concept:house_savings", "node_type": "concept", "name": "House Down Payment",
             "properties": {"goal": 100000, "deadline": "2026"}},
        ]
        
        # Create nodes
        for node_data in demo_nodes:
            await asyncio.to_thread(
                graph.upsert_node,
                node_id=node_data["node_id"],
                node_type=node_data["node_type"],
                name=node_data["name"],
                properties=node_data.get("properties", {}),
                user_id=DEMO_USER_ID,
            )
            nodes_created += 1
        
        # Create edges representing relationships from memories
        demo_edges = [
            # Work relationships
            {"source": "person:john_smith", "relation": "works_at", "target": "organization:techcorp"},
            {"source": "person:john_smith", "relation": "reports_to", "target": "person:sarah_johnson"},
            {"source": "person:john_smith", "relation": "leading", "target": "event:q1_migration"},
            
            # Location relationships
            {"source": "person:john_smith", "relation": "lives_in", "target": "location:san_francisco"},
            {"source": "person:john_smith", "relation": "visited", "target": "location:japan"},
            {"source": "organization:techcorp", "relation": "located_in", "target": "location:san_francisco"},
            
            # Interest/preference relationships
            {"source": "person:john_smith", "relation": "likes", "target": "interest:python"},
            {"source": "person:john_smith", "relation": "plays", "target": "interest:chess"},
            {"source": "person:john_smith", "relation": "learning", "target": "interest:guitar"},
            {"source": "person:john_smith", "relation": "does", "target": "interest:running"},
            {"source": "person:john_smith", "relation": "follows", "target": "interest:vegetarian"},
            
            # Goals
            {"source": "person:john_smith", "relation": "saving_for", "target": "concept:house_savings"},
        ]
        
        for edge_data in demo_edges:
            success = await asyncio.to_thread(
                graph.add_edge,
                source_id=edge_data["source"],
                relation=edge_data["relation"],
                target_id=edge_data["target"],
                weight=edge_data.get("weight", 0.9),
            )
            if success:
                edges_created += 1
        
        logger.info(f"✅ Seeded {nodes_created} graph nodes and {edges_created} edges")
    
    return {
        "success": True,
        "memories": {
            "count": len(injected),
            "items": injected,
        },
        "graph": {
            "nodes_created": nodes_created,
            "edges_created": edges_created,
        },
        "message": f"Seeded {len(injected)} memories + {nodes_created} graph nodes with {edges_created} edges",
    }


@app.post("/demo/reset", response_class=JSONResponse)
async def reset_demo_data():
    """Delete all demo data (memories + graph) and start fresh."""
    memory = get_memory_service()
    
    # Get memory count first
    all_memories = await asyncio.to_thread(
        memory.get_all,
        user_id=DEMO_USER_ID,
        limit=1000,
    )
    memory_count = len(all_memories)
    
    # Delete all memories
    await asyncio.to_thread(
        memory.delete_all,
        user_id=DEMO_USER_ID,
    )
    
    # Also delete graph data
    graph = engine.get_graph_service(APP_SLUG)
    node_count = 0
    
    if graph and graph.enabled:
        all_nodes = await asyncio.to_thread(
            graph.list_nodes,
            user_id=DEMO_USER_ID,
            limit=1000,
        )
        node_count = len(all_nodes)
        
        for node in all_nodes:
            await asyncio.to_thread(graph.delete_node, node["_id"])
    
    logger.info(f"🗑️ Reset demo data: deleted {memory_count} memories and {node_count} graph nodes")
    
    return {
        "success": True,
        "deleted": {
            "memories": memory_count,
            "graph_nodes": node_count,
        },
        "message": f"Deleted {memory_count} memories and {node_count} graph nodes. Run POST /demo/seed to re-populate.",
    }


# =============================================================================
# EXAMPLE WORKFLOWS
# =============================================================================


@app.get("/demo/workflow/search-and-update", response_class=JSONResponse)
async def workflow_search_and_update():
    """
    Demo workflow: Search for memories and show how to update them.
    """
    memory = get_memory_service()
    
    # Search for preferences
    results = await asyncio.to_thread(
        memory.search,
        query="What are the user's dietary preferences?",
        user_id=DEMO_USER_ID,
        limit=3,
    )
    
    return {
        "workflow": "search-and-update",
        "description": "Found memories about dietary preferences. To update, use PUT /memories/{id}",
        "search_query": "What are the user's dietary preferences?",
        "results": results,
        "next_step": "Copy a memory ID and call PUT /memories/{id} with new content",
    }


@app.get("/demo/workflow/category-breakdown", response_class=JSONResponse)
async def workflow_category_breakdown():
    """
    Demo workflow: Show memory count by category.
    """
    memory = get_memory_service()
    
    categories = ["biographical", "preferences", "work", "health", "finance", "travel", "hobbies", "general"]
    breakdown = {}
    
    for cat in categories:
        memories = await asyncio.to_thread(
            memory.get_all,
            user_id=DEMO_USER_ID,
            limit=1000,
            filters={"metadata": {"category": cat}},
        )
        breakdown[cat] = len(memories)
    
    total = sum(breakdown.values())
    
    return {
        "workflow": "category-breakdown",
        "description": "Memory count by category",
        "total_memories": total,
        "breakdown": breakdown,
    }


@app.get("/demo/workflow/memory-to-graph", response_class=JSONResponse)
async def workflow_memory_to_graph():
    """
    Demo workflow: Show how memories can be converted to graph knowledge.
    
    This demonstrates the memory-to-graph integration pattern:
    1. Get memories about work relationships
    2. Show how they map to graph nodes and edges
    3. Demonstrate the extraction process
    """
    memory = get_memory_service()
    graph = engine.get_graph_service(APP_SLUG)
    
    # Search for work-related memories
    work_memories = await asyncio.to_thread(
        memory.search,
        query="work relationships manager colleagues",
        user_id=DEMO_USER_ID,
        limit=5,
    )
    
    # Get graph stats
    graph_stats = {}
    if graph and graph.enabled:
        graph_stats = await asyncio.to_thread(graph.get_stats)
    
    # Get person nodes from graph
    person_nodes = []
    if graph and graph.enabled:
        persons = await asyncio.to_thread(
            graph.list_nodes,
            node_type="person",
            user_id=DEMO_USER_ID,
            limit=10,
        )
        for p in persons:
            person_nodes.append({
                "_id": p.get("_id"),
                "name": p.get("name"),
                "edges": len(p.get("edges", [])),
            })
    
    return {
        "workflow": "memory-to-graph",
        "description": "Shows how memories become graph knowledge",
        "work_memories": [
            {"id": m.get("id"), "memory": m.get("memory"), "score": m.get("score")}
            for m in work_memories
        ],
        "graph_stats": graph_stats,
        "person_nodes": person_nodes,
        "how_it_works": [
            "1. Memory: 'User's manager is Sarah Johnson' -> Graph: person:john_smith --reports_to--> person:sarah_johnson",
            "2. Memory: 'User works as a Senior Software Engineer at TechCorp' -> Graph: person:john_smith --works_at--> organization:techcorp",
            "3. Use POST /memories/{id}/extract-graph to convert any memory to graph entities",
        ],
    }


@app.get("/demo/workflow/graphrag-search", response_class=JSONResponse)
async def workflow_graphrag_search():
    """
    Demo workflow: Demonstrate GraphRAG (hybrid search with graph context).
    
    GraphRAG combines:
    1. Vector search to find semantically relevant entry points
    2. Graph traversal to expand context with connected knowledge
    """
    graph = engine.get_graph_service(APP_SLUG)
    
    if not graph or not graph.enabled:
        return {
            "workflow": "graphrag-search",
            "error": "Graph service not enabled",
        }
    
    # Perform hybrid search
    query = "What does John Smith like and where does he work?"
    
    results = await asyncio.to_thread(
        graph.hybrid_search,
        query=query,
        user_id=DEMO_USER_ID,
        max_depth=2,
        vector_limit=3,
    )
    
    # Format for LLM
    context_str = graph.format_graph_context(results, max_nodes=10)
    
    return {
        "workflow": "graphrag-search",
        "description": "Demonstrates hybrid search combining vector similarity with graph traversal",
        "query": query,
        "results": {
            "entry_nodes": len(results.get("entry_nodes", [])),
            "graph_context_nodes": len(results.get("graph_context", [])),
            "total_nodes": results.get("total_nodes", 0),
        },
        "entry_nodes": [
            {"_id": n.get("_id"), "name": n.get("name"), "similarity": n.get("similarity")}
            for n in results.get("entry_nodes", [])
        ],
        "formatted_llm_context": context_str,
        "how_to_use": "Use the formatted_llm_context in your LLM prompt to provide rich, connected knowledge",
    }


# =============================================================================
# RUN WITH UVICORN
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
