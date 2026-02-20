"""
Graph Service Module
--------------------
This module provides a standalone knowledge graph service using MongoDB's
native $graphLookup aggregation stage for efficient graph traversal.

Key Features:
- **MongoDB $graphLookup**: Native integration with MongoDB for graph traversal
- **LLM-Powered Extraction**: Uses LLM to extract entities and relationships from text
- **Hybrid Search**: Combines vector similarity with graph context (GraphRAG)
- **Temporal Edges**: Support for active/inactive relationships with timestamps
- **Multi-Tenant**: App-scoped isolation for multi-app deployments
- **Standalone Service**: Can be used independently or with MemoryService

Dependencies:
    pip install pymongo litellm

Usage:
    # Standalone usage
    from mdb_engine.graph import get_graph_service, GraphService

    graph_service = get_graph_service(
        app_slug="my_app",
        config={"enabled": True, "auto_extract": True},
        collection=db.knowledge_graph,
        llm_service=llm_service,
        embedding_service=embedding_service,
    )

    # Add nodes and edges
    graph_service.upsert_node("person:alex", "person", "Alex", {"occupation": "Engineer"})
    graph_service.add_edge("person:alex", "likes", "interest:golf", weight=0.9)

    # Traverse the graph
    network = graph_service.traverse("person:alex", max_depth=2)

    # Extract graph from text
    result = await graph_service.extract_graph_from_text("My brother Alex loves golf", "user123")

FastAPI Dependency Injection:
    from mdb_engine.graph.dependencies import get_graph_service_dependency

    @app.post("/graph/extract")
    async def extract_graph(
        text: str,
        graph_service: GraphService = Depends(get_graph_service_dependency)
    ):
        result = await graph_service.extract_graph_from_text(text, "user123")
        return result
"""

from .base import BaseGraphService, GraphServiceError
from .service import GraphService, get_graph_service

# GraphRAG components
try:
    from .community import CommunityService, get_community_service
    from .query_classifier import QueryClassifier, get_query_classifier
except ImportError:
    # Graceful degradation if dependencies missing
    CommunityService = None
    get_community_service = None
    QueryClassifier = None
    get_query_classifier = None

__all__ = [
    # Base classes
    "BaseGraphService",
    "GraphServiceError",
    # Implementation
    "GraphService",
    # Factory function
    "get_graph_service",
    # GraphRAG components
    "CommunityService",
    "get_community_service",
    "QueryClassifier",
    "get_query_classifier",
]
