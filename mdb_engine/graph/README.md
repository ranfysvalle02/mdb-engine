# Graph Service

The Graph Service provides a standalone knowledge graph implementation using MongoDB's native `$graphLookup` aggregation stage for efficient graph traversal.

## Overview

This service enables GraphRAG (Graph-enhanced Retrieval Augmented Generation) by combining:
- **Vector Search**: Finds semantically similar nodes (entry points)
- **Graph Traversal**: Explores relationships via `$graphLookup`
- **LLM Extraction**: Automatically extracts entities and relationships from text

## Key Features

- **MongoDB Native**: Uses `$graphLookup` for efficient multi-hop traversal
- **LLM-Powered Extraction**: Automatic entity/relationship extraction from text
- **Hybrid Search**: Combines vector similarity with graph context
- **Temporal Edges**: Support for active/inactive relationships
- **Multi-Tenant**: App-scoped isolation for multi-app deployments
- **Standalone**: Can be used independently or with MemoryService

## Configuration

Configure via `graph_config` in `manifest.json`:

```json
{
  "llm_config": {
    "enabled": true,
    "default_model": "gemini/gemini-3-flash-preview"
  },
  "graph_config": {
    "enabled": true,
    "collection_name": "kg",
    "auto_extract": true,
    "llm_model": "gemini/gemini-3-flash-preview",  // Inherits from llm_config.default_model if not set
    "temperature": 0.0,
    "default_max_depth": 2,
    "vector_index_name": "graph_vector_index",
    "node_types": ["person", "interest", "event", "location", "organization", "product", "concept"]
  }
}
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enabled` | boolean | `false` | Enable graph service |
| `collection_name` | string | `"kg"` | MongoDB collection for graph nodes |
| `auto_extract` | boolean | `true` | Auto-extract from text |
| `llm_model` | string | (inherits from `llm_config.default_model`) | LLM for extraction. If not set, automatically uses the app's default LLM model from `llm_config.default_model` |
| `temperature` | float | `0.0` | LLM temperature |
| `default_max_depth` | integer | `2` | Default traversal depth |
| `vector_index_name` | string | `"graph_vector_index"` | Name of vector index |
| `node_types` | array | (see above) | Allowed node types |

**LLM Model Inheritance**: The graph service automatically inherits the LLM model from `llm_config.default_model`. If `graph_config.llm_model` is not explicitly set, it will use the app's default LLM model. This ensures consistent LLM usage across all services (memory, graph, entity extraction, etc.).

**Service-Specific Override**: You can override the model for graph extraction only by setting `graph_config.llm_model` explicitly.

## Usage

### Standalone Usage

```python
from mdb_engine.graph import get_graph_service
from mdb_engine.llm import get_llm_service
from mdb_engine.embeddings import get_embedding_service

# Initialize services
llm_service = get_llm_service()
embedding_service = get_embedding_service()

# Create graph service
graph_service = get_graph_service(
    app_slug="my_app",
    collection=db.knowledge_graph,
    config={"enabled": True, "auto_extract": True},
    llm_service=llm_service,
    embedding_service=embedding_service,
)

# Add nodes
graph_service.upsert_node(
    node_id="person:alex",
    node_type="person",
    name="Alex",
    properties={"occupation": "Engineer"},
    user_id="user123"
)

# Add edges
graph_service.add_edge(
    source_id="person:alex",
    relation="likes",
    target_id="interest:golf",
    weight=0.9
)

# Traverse the graph
network = graph_service.traverse("person:alex", max_depth=2)

# Extract from text
result = await graph_service.extract_graph_from_text(
    text="My brother Alex loves playing golf on weekends",
    user_id="user123"
)
```

### FastAPI Dependency Injection

```python
from fastapi import Depends
from mdb_engine.graph.dependencies import get_graph_service_dependency
from mdb_engine.graph import GraphService

@app.post("/graph/extract")
async def extract_graph(
    text: str,
    user_id: str,
    graph_service: GraphService = Depends(get_graph_service_dependency)
):
    result = await graph_service.extract_graph_from_text(text, user_id)
    return result

@app.get("/graph/traverse/{node_id}")
def traverse_graph(
    node_id: str,
    max_depth: int = 2,
    graph_service: GraphService = Depends(get_graph_service_dependency)
):
    results = graph_service.traverse(node_id, max_depth=max_depth)
    return {"nodes": results}
```

### With MDB-Engine

```python
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(mongo_uri="...", db_name="mydb")
await engine.initialize()

# Register app with graph_config
await engine.register_app(manifest={
    "slug": "my_app",
    "graph_config": {
        "enabled": True,
        "auto_extract": True
    }
})

# Get graph service
graph_service = engine.get_graph_service("my_app")
```

## Node Schema

```json
{
  "_id": "person:alex",
  "type": "person",
  "name": "Alex",
  "properties": {
    "occupation": "Engineer",
    "location": "Seattle"
  },
  "edges": [
    {
      "relation": "likes",
      "target": "interest:golf",
      "weight": 0.9,
      "active": true,
      "created_at": "2024-01-15T10:30:00Z"
    }
  ],
  "embedding": [0.1, 0.2, ...],
  "app_slug": "my_app",
  "user_id": "user123",
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:30:00Z"
}
```

## API Reference

### Node Operations

- `upsert_node(node_id, node_type, name, properties, user_id, embedding)` - Create/update node
- `get_node(node_id)` - Get node by ID
- `delete_node(node_id)` - Delete node and its edges
- `list_nodes(node_type, user_id, limit)` - List nodes with filtering

### Edge Operations

- `add_edge(source_id, relation, target_id, properties, weight, active)` - Add relationship
- `remove_edge(source_id, relation, target_id)` - Remove relationship
- `update_edge(source_id, relation, target_id, updates)` - Update relationship
- `deactivate_edge(source_id, relation, target_id)` - Soft delete relationship

### Graph Traversal

- `traverse(start_id, max_depth, relation_filter, include_inactive, include_start)` - Multi-hop traversal
- `get_neighbors(node_id, relation, include_inactive)` - Get immediate neighbors
- `find_path(start_id, end_id, max_depth)` - Find path between nodes

### Hybrid Search

- `hybrid_search(query, user_id, max_depth, vector_limit, include_inactive)` - Vector + graph search
- `format_graph_context(hybrid_results, max_nodes, include_edges)` - Format for LLM prompt

### Extraction

- `extract_graph_from_text(text, user_id, auto_create_nodes)` - LLM-powered extraction

### Statistics

- `get_stats()` - Get node/edge counts

## Dependencies

- **Required**: `pymongo`
- **For Extraction**: `openai`, `google-genai` (as required by your `llm_config` models), `pydantic`
- **For Hybrid Search**: Embedding service configured

## See Also

- [GraphRAG Documentation](../../docs/GRAPHRAG.md)
- [Memory Service](../memory/README.md)
- [LLM Service](../llm/README.md)
