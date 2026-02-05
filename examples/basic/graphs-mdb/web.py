#!/usr/bin/env python3
"""
Graphs MDB Example - MDB-Engine Graph Features Demo

This example demonstrates ALL graph service features:
- Node CRUD operations (create, read, update, delete)
- Edge management (relationships between nodes)
- Graph traversal using MongoDB $graphLookup
- Hybrid search (GraphRAG) combining vector search + graph traversal
- LLM-powered node/relationship extraction from text
- Demo data seeding

Run with:
    uvicorn web:app --reload --port 8000
"""

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from mdb_engine import MongoDBEngine

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("graphs_mdb")

# App configuration
APP_SLUG = "graphs_mdb"
DEMO_USER_ID = "demo_user_123"

# Templates directory
templates_dir = (
    Path("/app/templates")
    if Path("/app/templates").exists()
    else Path(__file__).parent / "templates"
)
templates = Jinja2Templates(directory=str(templates_dir))

# Initialize engine
engine = MongoDBEngine(
    mongo_uri=os.getenv("MONGO_URI", "mongodb://localhost:27017"),
    db_name=os.getenv("MONGO_DB_NAME", "graphs_mdb_db"),
)

# Create FastAPI app using MDB-Engine pattern
app = engine.create_app(
    slug=APP_SLUG,
    manifest=Path(__file__).parent / "manifest.json",
    title="Graphs MDB Example",
    version="1.0.0",
)


# =============================================================================
# Pydantic Models for Request Bodies
# =============================================================================


class CreateNodeRequest(BaseModel):
    """Request body for creating/updating a node."""
    node_id: str = Field(..., description="Node ID in format type:name (e.g., person:alex)")
    node_type: str = Field(..., description="Node type: person, interest, event, location, organization, product, concept")
    name: str = Field(..., description="Display name for the node")
    properties: dict[str, Any] = Field(default_factory=dict, description="Additional properties")


class AddEdgeRequest(BaseModel):
    """Request body for adding an edge between nodes."""
    source_id: str = Field(..., description="Source node ID")
    relation: str = Field(..., description="Relationship type (e.g., likes, works_at, knows)")
    target_id: str = Field(..., description="Target node ID")
    properties: dict[str, Any] = Field(default_factory=dict, description="Edge properties")
    weight: float = Field(default=1.0, ge=0.0, le=1.0, description="Relationship strength (0.0-1.0)")


class RemoveEdgeRequest(BaseModel):
    """Request body for removing an edge."""
    source_id: str = Field(..., description="Source node ID")
    relation: str = Field(..., description="Relationship type")
    target_id: str = Field(..., description="Target node ID")


class HybridSearchRequest(BaseModel):
    """Request body for hybrid search (GraphRAG)."""
    query: str = Field(..., description="Search query text")
    max_depth: int = Field(default=2, ge=1, le=5, description="Maximum traversal depth")
    limit: int = Field(default=5, ge=1, le=20, description="Maximum entry nodes from vector search")


class ExtractRequest(BaseModel):
    """Request body for LLM-powered node extraction."""
    text: str = Field(..., description="Text to extract entities and relationships from")
    auto_create: bool = Field(default=True, description="Automatically create extracted nodes/edges")


# =============================================================================
# Helper Functions
# =============================================================================


def get_graph_service():
    """Get the graph service instance."""
    graph = engine.get_graph_service(APP_SLUG)
    if not graph:
        raise HTTPException(503, "Graph service not available. Check manifest configuration.")
    return graph


# =============================================================================
# HEALTH & INFO ENDPOINTS
# =============================================================================


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Render the main page with API documentation."""
    return templates.TemplateResponse(request, "index.html", {"app_slug": APP_SLUG})


@app.get("/health")
async def health():
    """Health check endpoint."""
    graph = engine.get_graph_service(APP_SLUG)
    return {
        "status": "healthy",
        "engine_initialized": engine.initialized,
        "graph_service_available": graph is not None,
        "graph_enabled": graph.enabled if graph else False,
    }


@app.get("/api", response_class=JSONResponse)
async def api_overview():
    """API overview with all available endpoints."""
    return {
        "app": "Graphs MDB Example",
        "description": "Demonstrates MDB-Engine graph features: nodes, edges, traversal, GraphRAG, and node extraction",
        "demo_user_id": DEMO_USER_ID,
        "endpoints": {
            "nodes": {
                "create": "POST /graph/nodes - Create or update a node",
                "list": "GET /graph/nodes - List all nodes (with optional type filter)",
                "get": "GET /graph/nodes/{node_id} - Get a single node",
                "delete": "DELETE /graph/nodes/{node_id} - Delete a node",
            },
            "edges": {
                "add": "POST /graph/edges - Add an edge between nodes",
                "remove": "DELETE /graph/edges - Remove an edge",
            },
            "traversal": {
                "traverse": "GET /graph/traverse/{node_id} - Traverse graph from a node",
                "neighbors": "GET /graph/neighbors/{node_id} - Get immediate neighbors",
            },
            "search": {
                "hybrid": "POST /graph/search - Hybrid search (GraphRAG)",
            },
            "extraction": {
                "extract": "POST /graph/extract - Extract entities from text using LLM",
            },
            "stats": {
                "stats": "GET /graph/stats - Get graph statistics",
            },
            "demo": {
                "seed": "POST /demo/seed - Seed demo graph data",
                "reset": "POST /demo/reset - Clear all graph data",
            },
        },
    }


# =============================================================================
# NODE OPERATIONS
# =============================================================================


@app.post("/graph/nodes", response_class=JSONResponse)
async def create_node(request: CreateNodeRequest):
    """
    Create or update a node in the graph.
    
    Node ID format: type:name (e.g., person:alex, interest:golf)
    
    Valid node types:
    - person: People, users, individuals
    - interest: Hobbies, topics, activities
    - event: Meetings, occasions, happenings
    - location: Places, cities, addresses
    - organization: Companies, teams, groups
    - product: Items, goods, services
    - concept: Abstract ideas, skills, qualities
    """
    graph = get_graph_service()
    
    result = await asyncio.to_thread(
        graph.upsert_node,
        node_id=request.node_id,
        node_type=request.node_type,
        name=request.name,
        properties=request.properties,
        user_id=DEMO_USER_ID,
    )
    
    logger.info(f"Created/updated node: {request.node_id}")
    
    return {
        "success": True,
        "node": result,
        "message": f"Node '{request.node_id}' created/updated successfully",
    }


@app.get("/graph/nodes", response_class=JSONResponse)
async def list_nodes(node_type: str | None = None, limit: int = 100):
    """
    List all nodes in the graph.
    
    Optional filters:
    - node_type: Filter by type (person, interest, event, etc.)
    - limit: Maximum number of nodes to return
    """
    graph = get_graph_service()
    
    nodes = await asyncio.to_thread(
        graph.list_nodes,
        node_type=node_type,
        user_id=DEMO_USER_ID,
        limit=limit,
    )
    
    # Clean up nodes for JSON response (convert ObjectId, datetime, etc.)
    cleaned_nodes = []
    for node in nodes:
        cleaned = {
            "_id": node.get("_id"),
            "type": node.get("type"),
            "name": node.get("name"),
            "properties": node.get("properties", {}),
            "edges": node.get("edges", []),
            "created_at": node.get("created_at").isoformat() if node.get("created_at") else None,
            "updated_at": node.get("updated_at").isoformat() if node.get("updated_at") else None,
        }
        cleaned_nodes.append(cleaned)
    
    return {
        "success": True,
        "count": len(cleaned_nodes),
        "filter": {"node_type": node_type} if node_type else None,
        "nodes": cleaned_nodes,
    }


@app.get("/graph/nodes/{node_id:path}", response_class=JSONResponse)
async def get_node(node_id: str):
    """Get a single node by ID."""
    graph = get_graph_service()
    
    node = await asyncio.to_thread(graph.get_node, node_id)
    
    if not node:
        raise HTTPException(404, f"Node '{node_id}' not found")
    
    # Clean up for JSON
    cleaned = {
        "_id": node.get("_id"),
        "type": node.get("type"),
        "name": node.get("name"),
        "properties": node.get("properties", {}),
        "edges": node.get("edges", []),
        "created_at": node.get("created_at").isoformat() if node.get("created_at") else None,
        "updated_at": node.get("updated_at").isoformat() if node.get("updated_at") else None,
    }
    
    return {"success": True, "node": cleaned}


@app.delete("/graph/nodes/{node_id:path}", response_class=JSONResponse)
async def delete_node(node_id: str):
    """
    Delete a node and all edges pointing to it.
    
    This is a hard delete - the node cannot be recovered.
    """
    graph = get_graph_service()
    
    success = await asyncio.to_thread(graph.delete_node, node_id)
    
    return {
        "success": success,
        "message": f"Node '{node_id}' deleted" if success else f"Node '{node_id}' not found",
    }


# =============================================================================
# EDGE OPERATIONS
# =============================================================================


@app.post("/graph/edges", response_class=JSONResponse)
async def add_edge(request: AddEdgeRequest):
    """
    Add an edge (relationship) between two nodes.
    
    Common relationship types:
    - knows, likes, dislikes, loves, hates
    - works_at, lives_in, located_in
    - member_of, part_of, belongs_to
    - parent_of, child_of, sibling_of, spouse_of, friend_of
    - attended, participated_in, created, owns
    - interested_in, skilled_at, studies
    
    Weight (0.0-1.0) indicates relationship strength.
    """
    graph = get_graph_service()
    
    # Ensure source node exists
    source = await asyncio.to_thread(graph.get_node, request.source_id)
    if not source:
        raise HTTPException(404, f"Source node '{request.source_id}' not found. Create it first.")
    
    success = await asyncio.to_thread(
        graph.add_edge,
        source_id=request.source_id,
        relation=request.relation,
        target_id=request.target_id,
        properties=request.properties,
        weight=request.weight,
    )
    
    if success:
        logger.info(f"Added edge: {request.source_id} --{request.relation}--> {request.target_id}")
    
    return {
        "success": success,
        "edge": {
            "source": request.source_id,
            "relation": request.relation,
            "target": request.target_id,
            "weight": request.weight,
        },
        "message": f"Edge added: {request.source_id} --{request.relation}--> {request.target_id}" if success else "Failed to add edge",
    }


@app.delete("/graph/edges", response_class=JSONResponse)
async def remove_edge(request: RemoveEdgeRequest):
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
        "message": f"Edge removed: {request.source_id} --{request.relation}--> {request.target_id}" if success else "Edge not found",
    }


# =============================================================================
# GRAPH TRAVERSAL
# =============================================================================


@app.get("/graph/traverse/{node_id:path}", response_class=JSONResponse)
async def traverse_graph(
    node_id: str,
    max_depth: int = 2,
    include_inactive: bool = False,
):
    """
    Traverse the graph from a starting node using MongoDB's $graphLookup.
    
    Returns all connected nodes up to max_depth hops away.
    
    Parameters:
    - node_id: Starting node ID
    - max_depth: Maximum traversal depth (1-5, default 2)
    - include_inactive: Include deactivated edges
    """
    graph = get_graph_service()
    
    # Validate node exists
    node = await asyncio.to_thread(graph.get_node, node_id)
    if not node:
        raise HTTPException(404, f"Node '{node_id}' not found")
    
    # Clamp max_depth
    max_depth = max(1, min(5, max_depth))
    
    results = await asyncio.to_thread(
        graph.traverse,
        start_id=node_id,
        max_depth=max_depth,
        include_inactive=include_inactive,
    )
    
    return {
        "success": True,
        "start_node": node_id,
        "max_depth": max_depth,
        "count": len(results),
        "nodes": results,
    }


@app.get("/graph/neighbors/{node_id:path}", response_class=JSONResponse)
async def get_neighbors(
    node_id: str,
    relation: str | None = None,
    include_inactive: bool = False,
):
    """
    Get immediate neighbors of a node (1-hop traversal).
    
    Parameters:
    - node_id: Node ID
    - relation: Optional filter by relationship type
    - include_inactive: Include deactivated edges
    """
    graph = get_graph_service()
    
    neighbors = await asyncio.to_thread(
        graph.get_neighbors,
        node_id=node_id,
        relation=relation,
        include_inactive=include_inactive,
    )
    
    # Clean up neighbor data
    cleaned = []
    for n in neighbors:
        node = n.get("node", {})
        cleaned.append({
            "node": {
                "_id": node.get("_id"),
                "type": node.get("type"),
                "name": node.get("name"),
                "properties": node.get("properties", {}),
            },
            "relation": n.get("relation"),
            "weight": n.get("weight", 1.0),
            "properties": n.get("properties", {}),
        })
    
    return {
        "success": True,
        "node_id": node_id,
        "relation_filter": relation,
        "count": len(cleaned),
        "neighbors": cleaned,
    }


# =============================================================================
# HYBRID SEARCH (GraphRAG)
# =============================================================================


@app.post("/graph/search", response_class=JSONResponse)
async def hybrid_search(request: HybridSearchRequest):
    """
    Perform hybrid search combining vector similarity with graph traversal.
    
    This is GraphRAG in action:
    1. Vector search finds semantically similar "entry nodes"
    2. Graph traversal expands context from entry nodes
    3. Results include both entry nodes and graph context
    
    Use the context for LLM prompts to provide rich, connected knowledge.
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


# =============================================================================
# LLM-POWERED NODE EXTRACTION
# =============================================================================


@app.post("/graph/extract", response_class=JSONResponse)
async def extract_entities(request: ExtractRequest):
    """
    Extract entities and relationships from text using LLM.
    
    The LLM analyzes the text and extracts:
    - Nodes: People, places, organizations, interests, events, etc.
    - Edges: Relationships between the entities
    
    If auto_create=True (default), the extracted nodes and edges are
    automatically created in the graph.
    
    Example input:
    "My brother Alex loves golf and works at TechCorp in Seattle."
    
    Extracts:
    - Nodes: person:alex, interest:golf, organization:techcorp, location:seattle
    - Edges: person:user -> brother -> person:alex
             person:alex -> likes -> interest:golf
             person:alex -> works_at -> organization:techcorp
             organization:techcorp -> located_in -> location:seattle
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
        "auto_created": request.auto_create,
        "nodes_created": result.get("nodes_created", 0),
        "edges_created": result.get("edges_created", 0),
        "extracted": result.get("extracted"),
        "error": result.get("error"),
    }


# =============================================================================
# GRAPH STATISTICS
# =============================================================================


@app.get("/graph/stats", response_class=JSONResponse)
async def get_stats():
    """Get graph statistics including node/edge counts by type."""
    graph = get_graph_service()
    
    stats = await asyncio.to_thread(graph.get_stats)
    
    return {
        "success": True,
        "stats": stats,
    }


# =============================================================================
# DEMO DATA SEEDING
# =============================================================================


@app.post("/demo/seed", response_class=JSONResponse)
async def seed_demo_data():
    """
    Seed the graph with demo data to explore features.
    
    Creates a knowledge graph representing:
    - People: John Smith (user), Sarah Johnson (manager), Alex Chen (colleague)
    - Organizations: TechCorp, Acme Inc
    - Interests: Python, Chess, Hiking, Guitar
    - Locations: San Francisco, Tokyo
    - Events: Q1 Migration project, Annual Review
    
    With relationships connecting them all!
    """
    graph = get_graph_service()
    
    # Define demo nodes
    demo_nodes = [
        # People
        {"node_id": "person:john_smith", "node_type": "person", "name": "John Smith", 
         "properties": {"occupation": "Senior Software Engineer", "age": 35}},
        {"node_id": "person:sarah_johnson", "node_type": "person", "name": "Sarah Johnson",
         "properties": {"occupation": "Engineering Manager", "department": "Platform"}},
        {"node_id": "person:alex_chen", "node_type": "person", "name": "Alex Chen",
         "properties": {"occupation": "Software Engineer", "specialty": "ML"}},
        
        # Organizations
        {"node_id": "organization:techcorp", "node_type": "organization", "name": "TechCorp",
         "properties": {"industry": "Technology", "size": "Enterprise", "founded": 2010}},
        {"node_id": "organization:acme_inc", "node_type": "organization", "name": "Acme Inc",
         "properties": {"industry": "Consulting", "size": "Medium"}},
        
        # Interests
        {"node_id": "interest:python", "node_type": "interest", "name": "Python Programming",
         "properties": {"category": "technology", "skill_level": "expert"}},
        {"node_id": "interest:chess", "node_type": "interest", "name": "Chess",
         "properties": {"category": "games", "type": "strategy"}},
        {"node_id": "interest:hiking", "node_type": "interest", "name": "Hiking",
         "properties": {"category": "outdoor", "type": "exercise"}},
        {"node_id": "interest:guitar", "node_type": "interest", "name": "Guitar",
         "properties": {"category": "music", "skill_level": "beginner"}},
        
        # Locations
        {"node_id": "location:san_francisco", "node_type": "location", "name": "San Francisco",
         "properties": {"state": "CA", "country": "USA", "type": "city"}},
        {"node_id": "location:tokyo", "node_type": "location", "name": "Tokyo",
         "properties": {"country": "Japan", "type": "city"}},
        
        # Events
        {"node_id": "event:q1_migration", "node_type": "event", "name": "Q1 Data Migration",
         "properties": {"quarter": "Q1 2024", "status": "in_progress", "priority": "high"}},
        {"node_id": "event:annual_review", "node_type": "event", "name": "Annual Performance Review",
         "properties": {"date": "2024-03-15", "type": "evaluation"}},
        
        # Concepts
        {"node_id": "concept:machine_learning", "node_type": "concept", "name": "Machine Learning",
         "properties": {"domain": "AI", "complexity": "advanced"}},
        {"node_id": "concept:agile", "node_type": "concept", "name": "Agile Development",
         "properties": {"domain": "methodology", "type": "process"}},
    ]
    
    # Define demo edges
    demo_edges = [
        # John's relationships
        {"source": "person:john_smith", "relation": "works_at", "target": "organization:techcorp", "weight": 1.0},
        {"source": "person:john_smith", "relation": "reports_to", "target": "person:sarah_johnson", "weight": 0.9},
        {"source": "person:john_smith", "relation": "colleague_of", "target": "person:alex_chen", "weight": 0.8},
        {"source": "person:john_smith", "relation": "lives_in", "target": "location:san_francisco", "weight": 1.0},
        {"source": "person:john_smith", "relation": "likes", "target": "interest:python", "weight": 0.95},
        {"source": "person:john_smith", "relation": "likes", "target": "interest:chess", "weight": 0.7},
        {"source": "person:john_smith", "relation": "learning", "target": "interest:guitar", "weight": 0.5},
        {"source": "person:john_smith", "relation": "participating_in", "target": "event:q1_migration", "weight": 1.0},
        {"source": "person:john_smith", "relation": "skilled_at", "target": "concept:machine_learning", "weight": 0.6},
        
        # Sarah's relationships
        {"source": "person:sarah_johnson", "relation": "works_at", "target": "organization:techcorp", "weight": 1.0},
        {"source": "person:sarah_johnson", "relation": "manages", "target": "person:john_smith", "weight": 0.9},
        {"source": "person:sarah_johnson", "relation": "manages", "target": "person:alex_chen", "weight": 0.9},
        {"source": "person:sarah_johnson", "relation": "likes", "target": "interest:hiking", "weight": 0.8},
        {"source": "person:sarah_johnson", "relation": "skilled_at", "target": "concept:agile", "weight": 0.9},
        
        # Alex's relationships
        {"source": "person:alex_chen", "relation": "works_at", "target": "organization:techcorp", "weight": 1.0},
        {"source": "person:alex_chen", "relation": "colleague_of", "target": "person:john_smith", "weight": 0.8},
        {"source": "person:alex_chen", "relation": "likes", "target": "interest:python", "weight": 0.9},
        {"source": "person:alex_chen", "relation": "skilled_at", "target": "concept:machine_learning", "weight": 0.95},
        {"source": "person:alex_chen", "relation": "visited", "target": "location:tokyo", "weight": 0.5},
        
        # Organization relationships
        {"source": "organization:techcorp", "relation": "located_in", "target": "location:san_francisco", "weight": 1.0},
        {"source": "organization:techcorp", "relation": "partner_of", "target": "organization:acme_inc", "weight": 0.6},
        
        # Event relationships
        {"source": "event:q1_migration", "relation": "owned_by", "target": "organization:techcorp", "weight": 1.0},
        {"source": "event:annual_review", "relation": "conducted_by", "target": "person:sarah_johnson", "weight": 1.0},
    ]
    
    # Create nodes
    nodes_created = 0
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
    
    # Create edges
    edges_created = 0
    for edge_data in demo_edges:
        success = await asyncio.to_thread(
            graph.add_edge,
            source_id=edge_data["source"],
            relation=edge_data["relation"],
            target_id=edge_data["target"],
            weight=edge_data.get("weight", 1.0),
        )
        if success:
            edges_created += 1
    
    logger.info(f"Seeded demo data: {nodes_created} nodes, {edges_created} edges")
    
    return {
        "success": True,
        "nodes_created": nodes_created,
        "edges_created": edges_created,
        "message": f"Created {nodes_created} nodes and {edges_created} edges",
        "sample_queries": [
            "GET /graph/nodes - See all nodes",
            "GET /graph/traverse/person:john_smith - Traverse from John",
            "POST /graph/search with query='What does John like?' - Hybrid search",
            "POST /graph/extract with text='John visited Tokyo last summer' - Extract entities",
        ],
    }


@app.post("/demo/reset", response_class=JSONResponse)
async def reset_demo_data():
    """Delete all graph data and start fresh."""
    graph = get_graph_service()
    
    # Get all nodes first to count them
    all_nodes = await asyncio.to_thread(
        graph.list_nodes,
        user_id=DEMO_USER_ID,
        limit=1000,
    )
    
    count = len(all_nodes)
    
    # Delete each node (this also removes edges)
    for node in all_nodes:
        await asyncio.to_thread(graph.delete_node, node["_id"])
    
    logger.info(f"Reset demo data: deleted {count} nodes")
    
    return {
        "success": True,
        "deleted_count": count,
        "message": f"Deleted {count} nodes. Run POST /demo/seed to re-populate.",
    }


# =============================================================================
# RUN WITH UVICORN
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
