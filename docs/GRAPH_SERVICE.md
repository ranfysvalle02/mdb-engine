# Graph Service

The Graph Service (`mdb_engine.graph`) is a standalone service for building and querying knowledge graphs using MongoDB's `$graphLookup` aggregation. **It is enabled by default** for all MDB-Engine apps, bringing GraphRAG capabilities automatically. It can be used independently or integrated with the Memory Service for enhanced retrieval.

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         Graph Service                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │ Node CRUD    │    │ Edge CRUD    │    │ Traversal    │       │
│  │              │    │              │    │ ($graphLookup)│       │
│  └──────────────┘    └──────────────┘    └──────────────┘       │
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │ GraphRAG     │    │ LLM Extract  │    │ Context      │       │
│  │ (Local/     │    │ (Entities)   │    │ Formatting   │       │
│  │  Global/    │    │              │    │              │       │
│  │  DRIFT)     │    │              │    │              │       │
│  └──────────────┘    └──────────────┘    └──────────────┘       │
│                                                                  │
├─────────────────────────────────────────────────────────────────┤
│  Dependencies: LLMService, EmbeddingService, MongoDB Collection  │
└─────────────────────────────────────────────────────────────────┘
```

### Mixin-Based Architecture

`GraphService` is composed from five specialized mixins:

| Mixin | Module | Responsibility |
|---|---|---|
| `NodeOperationsMixin` | `mdb_engine.graph.nodes` | Node CRUD, embedding backfill, memory reference cleanup |
| `EdgeOperationsMixin` | `mdb_engine.graph.edges` | Edge CRUD, soft-delete (deactivate), neighbor queries |
| `TraversalMixin` | `mdb_engine.graph.traversal` | `$graphLookup`-powered traversal, BFS path finding |
| `SearchMixin` | `mdb_engine.graph.search` | Hybrid search, GraphRAG strategies (local/global/drift), query classification |
| `ExtractionMixin` | `mdb_engine.graph.extraction` | LLM-powered entity/relationship extraction from text |

Supporting modules: `CommunityService` (`mdb_engine.graph.community`), `QueryClassifier` (`mdb_engine.graph.query_classifier`), LLM prompt templates (`mdb_engine.graph.prompts`).

## Installation

The Graph Service is included in `mdb-engine`:

```bash
pip install mdb-engine
```

For LLM-based extraction, ensure you have LiteLLM:

```bash
pip install litellm pydantic
```

## LLM Model Inheritance

**Important**: The Graph Service automatically inherits the LLM model from your app's `llm_config.default_model`. If `graph_config.llm_model` is not explicitly set, it will use the app's default LLM model. This ensures consistent LLM usage across all services (memory, graph, etc.).

**Service-Specific Override**: You can override the model for graph extraction only by setting `graph_config.llm_model` explicitly.

## Quick Start

### Standalone Usage

```python
from mdb_engine.graph import GraphService, get_graph_service
from mdb_engine.llm import LLMService
from mdb_engine.embeddings import EmbeddingService
from pymongo import MongoClient

# Initialize dependencies
client = MongoClient("mongodb://localhost:27017")
collection = client["mydb"]["knowledge_graph"]

llm_service = LLMService(config={"default_model": "gemini/gemini-3-flash-preview"})
embedding_service = EmbeddingService(config={"default_embedding_model": "text-embedding-3-small"})

# Create Graph Service
graph = get_graph_service(
    app_slug="my_app",
    collection=collection,
    config={"enabled": True, "auto_extract": True},
    llm_service=llm_service,
    embedding_service=embedding_service,
)

# Create nodes
graph.upsert_node("person:alex", "person", "Alex", {"occupation": "Engineer"})
graph.upsert_node("interest:golf", "interest", "Golf", {})

# Create relationship
graph.add_edge("person:alex", "likes", "interest:golf", weight=0.9)

# Traverse graph
results = graph.traverse("person:alex", max_depth=2)
print(f"Found {len(results)} connected nodes")
```

### With MDB-Engine

```python
from mdb_engine import MongoDBEngine

# Graph service is automatically initialized (enabled by default)
engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017")

# No explicit graph_config needed - enabled by default!
# Your minimal manifest.json just needs:
# {
#   "schema_version": "2.0",
#   "slug": "my_app",
#   "name": "My App"
# }

# Get graph service
graph = engine.get_graph_service("my_app")

# Use it
graph.upsert_node("person:alex", "person", "Alex")
```

### FastAPI Integration

```python
from fastapi import Depends, FastAPI
from mdb_engine.graph import get_graph_service_dependency

app = FastAPI()

@app.get("/graph/nodes/{node_id}")
async def get_node(
    node_id: str,
    graph = Depends(get_graph_service_dependency)
):
    return graph.get_node(node_id)

@app.post("/graph/traverse/{start_id}")
async def traverse(
    start_id: str,
    max_depth: int = 2,
    graph = Depends(get_graph_service_dependency)
):
    return graph.traverse(start_id, max_depth=max_depth)
```

## Configuration

### Default Behavior

The Graph Service is **enabled by default**. A minimal manifest gets GraphRAG capabilities automatically:

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My App"
}
```

### Disabling Graph Service

To disable the Graph Service, explicitly set `enabled` to `false`:

```json
{
  "graph_config": {
    "enabled": false
  }
}
```

### Custom Configuration

Override defaults by adding `graph_config` to your manifest.json:

```json
{
  "app_name": "My App",
  "app_slug": "my_app",
  "database_name": "my_database",
  
  "graph_config": {
    "collection_name": "__kg",
    "auto_extract": true,
    "llm_model": "gemini/gemini-3-flash-preview",  // Inherits from llm_config.default_model if not set
    "temperature": 0.0,
    "default_max_depth": 2,
    "vector_index_name": "graph_vector_index",
    "embedding_dims": 1536,
    "node_types": [
      "person",
      "interest",
      "event",
      "location",
      "organization",
      "product",
      "concept"
    ]
  }
}
```

### Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enabled` | boolean | `true` | Enable Graph Service (enabled by default) |
| `collection_name` | string | `"__kg"` | Collection name (prefixed with app slug) |
| `auto_extract` | boolean | `true` | Auto-extract entities from text |
| `llm_model` | string | (inherits from `llm_config.default_model`) | LLM model for extraction. If not set, automatically uses the app's default LLM model from `llm_config.default_model` |
| `temperature` | number | `0.0` | LLM temperature for extraction |
| `default_max_depth` | integer | `2` | Default $graphLookup depth |
| `vector_index_name` | string | `"graph_vector_index"` | Vector index name |
| `embedding_dims` | integer | `1536` | Embedding dimensions |
| `node_types` | array | (see default) | Allowed node types |

## API Reference

### Node Operations

#### `upsert_node()`

Create or update a node in the graph.

```python
node = graph.upsert_node(
    node_id="person:alex",           # Unique ID (format: type:identifier)
    node_type="person",              # Node type
    name="Alex",                     # Display name
    properties={"occupation": "Engineer", "age": 30},  # Custom properties
    user_id="user123",               # Owner user ID
)
```

**Returns**: The created/updated node document.

#### `get_node()`

Retrieve a node by ID.

```python
node = graph.get_node("person:alex")

if node:
    print(f"Name: {node['name']}")
    print(f"Edges: {len(node.get('edges', []))}")
```

**Returns**: Node document or `None` if not found.

#### `delete_node()`

Delete a node and all edges pointing to it.

```python
success = graph.delete_node("person:alex")
```

**Returns**: `True` if deleted, `False` if not found.

#### `list_nodes()`

List nodes with optional filters.

```python
# List all people
people = graph.list_nodes(node_type="person", limit=50)

# List nodes for a specific user
user_nodes = graph.list_nodes(user_id="user123", limit=100)
```

### Edge Operations

#### `add_edge()`

Create a directed edge between nodes.

```python
success = graph.add_edge(
    source_id="person:alex",
    relation="likes",
    target_id="interest:golf",
    properties={"since": "2020"},    # Optional edge properties
    weight=0.9,                      # Relationship strength (0.0-1.0)
)
```

**Returns**: `True` if created/updated, `False` on error.

#### `remove_edge()`

Permanently remove an edge.

```python
success = graph.remove_edge(
    source_id="person:alex",
    relation="likes",
    target_id="interest:golf",
)
```

#### `update_edge()`

Update edge properties.

```python
success = graph.update_edge(
    source_id="person:alex",
    relation="likes",
    target_id="interest:golf",
    updates={"weight": 0.95, "properties": {"intensity": "high"}},
)
```

#### `deactivate_edge()`

Soft-delete an edge (useful for temporal relationships).

```python
# Alex no longer works at OldCorp
graph.deactivate_edge(
    source_id="person:alex",
    relation="works_at",
    target_id="organization:oldcorp",
)
```

### Graph Traversal

#### `traverse()`

Perform multi-hop graph traversal using MongoDB's `$graphLookup`.

```python
results = graph.traverse(
    start_id="person:alex",
    max_depth=2,                     # Maximum hops
    relation_filter=["likes", "knows"],  # Optional: only follow these
    include_inactive=False,          # Include soft-deleted edges?
)

# Returns list of:
# [
#   {"node": {...}, "hop_distance": 0},  # Starting node
#   {"node": {...}, "hop_distance": 1},  # Direct connections
#   {"node": {...}, "hop_distance": 2},  # 2-hop connections
# ]
```

#### `get_neighbors()`

Get immediate neighbors of a node.

```python
neighbors = graph.get_neighbors(
    node_id="person:alex",
    relation="likes",                # Optional: filter by relation
)

# Returns:
# [
#   {"relation": "likes", "target": "interest:golf", "weight": 0.9, ...},
#   {"relation": "likes", "target": "interest:tennis", "weight": 0.6, ...},
# ]
```

### Hybrid Search

Combine vector similarity search with graph traversal.

#### `hybrid_search()`

```python
results = graph.hybrid_search(
    query="What does Alex like?",    # Natural language query
    user_id="user123",
    max_depth=2,                     # Graph traversal depth
    vector_limit=5,                  # Max vector search results
)

# Returns:
# {
#   "entry_nodes": [                 # Semantically similar nodes
#     {"_id": "person:alex", "name": "Alex", "similarity": 0.92, ...}
#   ],
#   "graph_context": [               # Nodes from traversal
#     {"_id": "interest:golf", "hop_distance": 1, ...}
#   ],
#   "total_nodes": 6
# }
```

#### GraphRAG Search Methods (Recommended)

GraphRAG provides specialized search methods that automatically classify queries and route to the appropriate strategy:

**Query Classification:**
```python
query_type = graph.classify_query("What does Alex like?")
# Returns: "local", "global", "drift", or "basic"
```

**Local Search** - Entity-focused queries:
```python
results = await graph.local_search(
    query="What does Alex like?",  # Entity-focused query
    user_id="user123",
    max_depth=2,
)

# Returns:
# {
#   "query_type": "local",
#   "entry_nodes": [...],           # Vector search results
#   "graph_context": [...],         # Traversed nodes
#   "community_summaries": [...],    # Community summaries for context
#   "total_nodes": 8
# }
```

**Global Search** - Thematic queries:
```python
results = await graph.global_search(
    query="What are common interests?",  # Thematic query
    user_id="user123",
    max_communities=10,
)

# Returns:
# {
#   "query_type": "global",
#   "communities": [...],           # Relevant communities
#   "partial_responses": [...],     # Partial responses per community
#   "synthesized_answer": "...",    # Final synthesized answer
#   "total_communities": 5
# }
```

**DRIFT Search** - Entity queries with community context:
```python
results = await graph.drift_search(
    query="What is the context around Project X?",  # Entity + community
    user_id="user123",
    max_depth=2,
)

# Returns:
# {
#   "query_type": "drift",
#   "entry_nodes": [...],
#   "graph_context": [...],
#   "community_summaries": [...],
#   "total_nodes": 12
# }
```

**Automatic Routing:**
GraphRAG automatically classifies queries and routes to the appropriate method. You can also use `classify_query()` to determine the query type before searching.

#### `advanced_graph_search()` (Legacy - Use GraphRAG methods instead)

Microsoft Research-style GraphRAG with query decomposition and pathfinding. **Note**: For new code, use `local_search()`, `global_search()`, or `drift_search()` instead, which provide automatic query classification.

```python
results = await graph.advanced_graph_search(
    query="How is Alex related to Project Hades?",
    user_id="user123",
    max_depth=2,
)
```

**When to Use:**
- Use GraphRAG methods (`local_search`, `global_search`, `drift_search`) for automatic query classification
- Use `advanced_graph_search()` only if you need the specific pathfinding strategy
- Use `hybrid_search()` for simple semantic similarity queries

#### `format_graph_context()`

Format search results as LLM context.

```python
context = graph.format_graph_context(
    hybrid_results=results,
    max_nodes=10,
    include_edges=True,
)

# Returns formatted string:
# KNOWLEDGE GRAPH CONTEXT:
# - Alex (person) [occupation: Engineer]
#   → likes → interest:golf
# - Golf (interest)
#   → related_to → product:golf_clubs
```

#### `format_graph_narrative()`

Format advanced search results with strategy-aware narrative generation.

```python
narrative = graph.format_graph_narrative(
    graph_result=advanced_results,  # From advanced_graph_search()
    max_nodes=10,
)

# Returns formatted string with strategy context:
# [RAW GRAPH DATA]
# Search Strategy: pathfinding
# Entry Entities: Alex, Project Hades
# 
# Node: Alex (person) [hop=0] [PATH NODE]. Links: works_at Google, likes Coffee
# Node: Google (organization) [hop=1] [PATH NODE]. Links: owns Project Hades
# Node: Project Hades (project) [hop=2] [PATH NODE]. Links: ...
# [/RAW GRAPH DATA]
```

### LLM-Based Extraction

#### `extract_graph_from_text()` (async)

Extract entities and relationships from text using LLM.

```python
result = await graph.extract_graph_from_text(
    text="My colleague Sarah works at Google and loves hiking",
    user_id="user123",
    auto_create_nodes=True,          # Create nodes automatically
)

# Returns:
# {
#   "nodes_created": 3,
#   "edges_created": 2,
#   "extracted": {
#     "nodes": [
#       {"id": "person:sarah", "type": "person", "name": "Sarah"},
#       {"id": "organization:google", "type": "organization", "name": "Google"},
#       {"id": "interest:hiking", "type": "interest", "name": "Hiking"}
#     ],
#     "edges": [
#       {"source": "person:sarah", "relation": "works_at", "target": "organization:google"},
#       {"source": "person:sarah", "relation": "likes", "target": "interest:hiking"}
#     ]
#   }
# }
```

#### `extract_graph_from_memory()` (async)

Convenience wrapper used by the memory service for automatic graph extraction.

```python
result = await graph.extract_graph_from_memory(
    memory_text="My colleague Sarah works at Google",
    user_id="user123",
)
```

### Statistics

#### `get_stats()`

Get graph statistics.

```python
stats = graph.get_stats()

# Returns:
# {
#   "enabled": True,
#   "app_slug": "my_app",
#   "collection_name": "my_app__kg",
#   "total_nodes": 150,
#   "auto_extract": True,
#   "default_max_depth": 2
# }
```

## Integration with Memory Service

The Graph Service integrates seamlessly with the Memory Service for GraphRAG:

```python
from mdb_engine.memory import CognitiveMemoryService
from mdb_engine.graph import GraphService

# Graph Service is injected into Memory Service
memory_service = CognitiveMemoryService(
    app_slug="my_app",
    collection=memory_collection,
    config=memory_config,
    graph_service=graph_service,  # GraphRAG integration
)

# When adding memories, graph extraction happens automatically
memory_service.add(
    messages="My brother Alex loves golf",
    user_id="user123",
)
# Creates: person:user123 --brother--> person:alex --likes--> interest:golf
```

## Integration with CognitiveEngine

The CognitiveEngine orchestrator uses the Graph Service for RAG:

```python
from mdb_engine.memory import CognitiveEngine

engine = CognitiveEngine(
    app_slug="my_app",
    memory_service=memory_service,
    chat_history_collection=chat_collection,
    graph_service=graph_service,  # Enables graph context in chat
)

# Graph context is automatically included in responses
result = await engine.chat(
    user_id="user123",
    session_id="session1",
    user_query="What gift should I get for Alex?",
)

# LLM receives graph context about Alex's interests
print(result["graph_context"])  # Related nodes from traversal
```

## Node ID Conventions

Use the `type:identifier` format for node IDs:

```
person:alex              # Person named Alex
interest:golf            # Interest in golf
organization:google      # Google organization
event:birthday_2024      # 2024 birthday event
location:seattle         # Seattle location
product:golf_clubs       # Golf clubs product
concept:efficiency       # Abstract concept
```

Benefits:
- Collision-free (same name, different types)
- Self-documenting
- Easy to parse/filter

## Best Practices

### 1. Depth Limits

Keep traversal depth reasonable:
- **Depth 1**: Direct relationships only
- **Depth 2**: Most common - one hop away
- **Depth 3+**: Use sparingly (exponential growth)

### 2. Temporal Relationships

Use soft-delete for relationships that change:

```python
# Deactivate old job
graph.deactivate_edge("person:alex", "works_at", "organization:oldcorp")

# Add new job
graph.add_edge("person:alex", "works_at", "organization:newcorp")

# History is preserved for analytics
```

### 3. Relationship Weights

Use weights (0.0-1.0) to indicate strength:

```python
graph.add_edge("person:alex", "loves", "interest:golf", weight=0.95)   # Strong
graph.add_edge("person:alex", "likes", "interest:tennis", weight=0.6)  # Mild
```

### 4. Combine with Vector Memory

Graph + Vector is more powerful than either alone:
- **Vector**: "What did Alex say about golf last week?" (semantic search)
- **Graph**: "What does Alex like?" (relationship traversal)

## Error Handling

```python
from mdb_engine.graph import GraphServiceError

try:
    graph.upsert_node(...)
except GraphServiceError as e:
    print(f"Graph operation failed: {e}")
```

## Troubleshooting

### Graph Service not initialized

1. Graph is enabled by default - check if `graph_config.enabled` is explicitly set to `false`
2. Verify LLM service is configured (for extraction features)
3. Verify Embedding service is configured (for hybrid search features)
4. Check logs for initialization errors - graph gracefully degrades if dependencies are unavailable

### Extraction returns no entities

1. Ensure `auto_extract` is `true`
2. Verify LLM API key is set
3. Check text contains extractable entities
4. Verify Pydantic is installed (`pip install pydantic`)

### Hybrid search returns empty

1. Nodes need embeddings (check `embedding` field)
2. Vector index must exist in MongoDB Atlas
3. Query should be semantically related to nodes

### Traversal returns empty

1. Verify start node exists: `graph.get_node(start_id)`
2. Check edges exist: `node["edges"]`
3. Verify edges are active: `edge["active"] == True`
4. Check app_slug matches
