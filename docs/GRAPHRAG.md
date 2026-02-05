# GraphRAG: Knowledge Graph-Powered Memory

GraphRAG combines **Vector Search** (semantic similarity) with **Graph Traversal** (structural relationships) to enable multi-hop reasoning queries that standard RAG cannot handle.

> **Note**: The Graph Service is **enabled by default** for all MDB-Engine apps. This brings GraphRAG capabilities automatically without explicit configuration. To disable, set `graph_config.enabled: false` in your manifest. See [GRAPH_SERVICE.md](./GRAPH_SERVICE.md) for detailed API documentation.

## Overview

Traditional RAG finds memories by semantic similarity: "Tell me about Dad" retrieves memories containing "Dad". But it can't answer **relational queries** like:

- "What should I get for my brother's favorite hobby?"
- "Who knows someone that works at Google?"
- "What events did people from Seattle attend?"

GraphRAG solves this by:

1. **Vector Search**: Finds semantically relevant entry points ("my brother" → `person:alex`)
2. **Graph Traversal**: Follows relationships (`alex` → `likes` → `golf`)
3. **Context Assembly**: Combines graph context with memory context for the LLM

```
Query: "What should I get for my brother's favorite hobby?"
         │
         ▼
    ┌─────────────────┐
    │  Vector Search  │  "my brother" → person:alex (0.92 similarity)
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ $graphLookup    │  person:alex ──likes──► interest:golf
    │ Traversal       │                         │
    └────────┬────────┘                         ▼
             │                            ┌──────────────┐
             │                            │ product:clubs│
             ▼                            └──────────────┘
    ┌─────────────────┐
    │ Context for LLM │  "Alex likes golf. Golf products include clubs..."
    └─────────────────┘
```

## When to Use GraphRAG

GraphRAG adds overhead (LLM extraction for each memory), so choose wisely:

| Use Case | GraphRAG? | Why |
|----------|-----------|-----|
| Personal assistant with relationships | ✅ Yes | Need to traverse "my brother likes X" |
| Document Q&A / RAG chatbot | ❌ No | Relationships aren't the focus |
| CRM with contact relationships | ✅ Yes | "Who knows someone at Google?" |
| Simple chat memory | ❌ No | Basic fact storage is sufficient |
| Enterprise knowledge base | ✅ Yes | Complex entity relationships |

### Example Configurations

**Default** (GraphRAG enabled automatically - no config needed):
```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My App"
}
```

**Custom node types** (GraphRAG with custom configuration):
```json
{
  "graph_config": {
    "node_types": ["person", "organization", "project", "document"]
  }
}
```

**Disabled** (opt-out for simple memory without graph overhead):
```json
{
  "graph_config": {
    "enabled": false
  }
}
```

## Quick Start

### 1. GraphRAG is Enabled by Default

The Graph Service is enabled automatically with sensible defaults. A minimal manifest already has GraphRAG:

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My App"
}
```

To customize behavior, add a `graph_config` section:

```json
{
  "graph_config": {
    "collection_name": "__kg",
    "auto_extract": true,
    "default_max_depth": 2
  }
}
```

For memory service integration (both are independent but work together):

```json
{
  "memory_config": {
    "enabled": true
  }
}
```

### 2. Automatic Graph Extraction

When you add memories, entities and relationships are automatically extracted:

```python
# User says: "My brother Alex loves playing golf on weekends"
memory_service.add(
    messages="My brother Alex loves playing golf on weekends",
    user_id="user123"
)

# Graph automatically created:
# person:user123 ──brother──► person:alex
# person:alex ──likes──► interest:golf
# interest:golf ──has_property──► concept:weekends
```

### 3. Query with Graph Context

```python
# CognitiveEngine automatically includes graph context
result = await cognitive_engine.chat(
    user_id="user123",
    session_id="session1",
    user_query="What gift should I get for Alex?"
)

# LLM receives:
# KNOWLEDGE GRAPH CONTEXT:
# - Alex (person) [occupation: Engineer]
#   → likes → interest:golf
#   → brother → person:user123
# - Golf (interest)
#   → related_to → product:golf_clubs
```

## Schema

### Node Document

Nodes are stored in the `__kg` collection:

```json
{
  "_id": "person:alex",
  "type": "person",
  "name": "Alex",
  "properties": {
    "occupation": "Software Engineer",
    "location": "Seattle"
  },
  "edges": [
    {
      "relation": "likes",
      "target": "interest:golf",
      "properties": {"since": "2020"},
      "weight": 0.9,
      "active": true,
      "created_at": "2024-01-15T10:30:00Z"
    },
    {
      "relation": "works_at",
      "target": "organization:tech_corp",
      "weight": 1.0,
      "active": true
    }
  ],
  "embedding": [0.1, 0.2, ...],
  "app_slug": "myapp",
  "user_id": "user123",
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:30:00Z"
}
```

### Node Types

| Type | Description | Examples |
|------|-------------|----------|
| `person` | People, users, individuals | "Alex", "Mom", "Dr. Smith" |
| `interest` | Hobbies, topics, activities | "Golf", "Cooking", "Python" |
| `event` | Meetings, occasions | "Birthday Party", "Conference" |
| `location` | Places, cities, addresses | "Seattle", "Office", "Home" |
| `organization` | Companies, teams, groups | "Google", "Book Club" |
| `product` | Items, goods, services | "Golf Clubs", "iPhone" |
| `concept` | Abstract ideas, skills | "Efficiency", "Leadership" |

### Relationship Types

Common relationships extracted by the LLM:

| Category | Relationships |
|----------|--------------|
| Social | `knows`, `friend_of`, `colleague` |
| Family | `parent_of`, `child_of`, `sibling_of`, `spouse_of` |
| Preference | `likes`, `dislikes`, `loves`, `hates`, `interested_in` |
| Professional | `works_at`, `manages`, `reports_to`, `skilled_at` |
| Location | `lives_in`, `located_in`, `visited` |
| Membership | `member_of`, `part_of`, `belongs_to` |
| Action | `created`, `owns`, `attended`, `participated_in` |

## API Reference

### GraphService Methods

The Graph Service (`mdb_engine.graph.GraphService`) provides all graph operations:

```python
from mdb_engine.graph import GraphService, get_graph_service

# Get service from engine
graph_service = engine.get_graph_service("my_app")

# Or create directly
graph_service = get_graph_service(
    app_slug="my_app",
    collection=collection,
    config={"enabled": True},
    llm_service=llm_service,
    embedding_service=embedding_service,
)
```

#### Node Operations

```python
# Upsert a node
graph_service.upsert_node(
    node_id="person:alex",
    node_type="person",
    name="Alex",
    properties={"occupation": "Engineer"},
    user_id="user123"
)

# Get a node
node = graph_service.get_node("person:alex")

# List nodes
nodes = graph_service.list_nodes(node_type="person", user_id="user123", limit=50)

# Delete a node (and all edges pointing to it)
graph_service.delete_node("person:alex")
```

#### Edge Operations

```python
# Add an edge
graph_service.add_edge(
    source_id="person:alex",
    relation="likes",
    target_id="interest:golf",
    properties={"since": "2020"},
    weight=0.9
)

# Update an edge
graph_service.update_edge(
    source_id="person:alex",
    relation="likes",
    target_id="interest:golf",
    updates={"weight": 0.95, "properties": {"intensity": "high"}}
)

# Deactivate an edge (soft delete for temporal relationships)
graph_service.deactivate_edge(
    source_id="person:alex",
    relation="works_at",
    target_id="organization:old_company"
)

# Remove an edge (hard delete)
graph_service.remove_edge(
    source_id="person:alex",
    relation="likes",
    target_id="interest:golf"
)
```

#### Graph Traversal

```python
# Traverse from a node (uses $graphLookup)
results = graph_service.traverse(
    start_id="person:alex",
    max_depth=2,
    relation_filter=["likes", "knows"],  # Optional: only follow these relations
    include_inactive=False
)

# Returns:
# [
#   {"node": {...}, "hop_distance": 0},  # Alex
#   {"node": {...}, "hop_distance": 1},  # Golf (alex likes golf)
#   {"node": {...}, "hop_distance": 2},  # Golf Club (golf related_to clubs)
# ]

# Get immediate neighbors
neighbors = graph_service.get_neighbors("person:alex", relation="likes")

# Find path between nodes
path = graph_service.find_path("person:alex", "organization:google", max_depth=5)
```

#### Hybrid Search

```python
# Combine vector search with graph traversal
results = graph_service.hybrid_search(
    query="What does Alex like?",
    user_id="user123",
    max_depth=2,
    vector_limit=5
)

# Returns:
# {
#   "entry_nodes": [...],     # Nodes found via vector similarity
#   "graph_context": [...],   # Nodes found via traversal
#   "total_nodes": 12
# }

# Format for LLM prompt
context_str = graph_service.format_graph_context(results, max_nodes=10)
```

#### Auto-Extraction (Async)

```python
# Extract graph from text (async method)
result = await graph_service.extract_graph_from_text(
    text="My colleague Sarah works at Google and loves hiking",
    user_id="user123"
)

# Sync wrapper (for memory service integration)
result = graph_service.extract_graph_from_memory(
    memory_text="My colleague Sarah works at Google and loves hiking",
    user_id="user123"
)

# Returns:
# {
#   "nodes_created": 3,  # person:sarah, organization:google, interest:hiking
#   "edges_created": 2,  # works_at, likes
#   "extracted": {...}   # Raw extraction
# }
```

## Configuration Reference

Graph configuration is now a **top-level** manifest section:

```json
{
  "graph_config": {
    "enabled": true,
    "collection_name": "__kg",
    "auto_extract": true,
    "llm_model": "openai/gpt-4o",
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

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enabled` | boolean | `true` | Enable Graph Service (enabled by default) |
| `collection_name` | string | `"__kg"` | MongoDB collection for graph nodes (prefixed with app slug) |
| `auto_extract` | boolean | `true` | Auto-extract entities/relationships from text |
| `llm_model` | string | (from llm_config) | LLM model for graph extraction |
| `temperature` | number | `0.0` | Temperature for LLM extraction |
| `default_max_depth` | integer | `2` | Default traversal depth for $graphLookup |
| `vector_index_name` | string | `"graph_vector_index"` | Name of MongoDB Atlas Vector Search index |
| `embedding_dims` | integer | `1536` | Embedding dimensions for vector index |
| `node_types` | array | (see above) | Allowed node types for extraction |

## Use Cases

### Family Assistant

```
User: "What should I get for my brother's birthday?"

Graph traversal:
user → brother → person:alex → likes → interest:golf
                              → born_in → month:march

Response: "Based on Alex's love of golf and his March birthday, 
          consider golf accessories or a round at a new course!"
```

### Professional Network

```
User: "Who might know someone at Google?"

Graph traversal:
user → knows → person:sarah → works_at → organization:google
user → knows → person:mike → knows → person:jane → works_at → organization:google

Response: "Sarah works at Google directly. Mike knows Jane who also works there."
```

### Event Planning

```
User: "What activities would work for our book club?"

Graph traversal:
organization:book_club → member_of → [person:alice, person:bob, person:carol]
person:alice → likes → interest:wine
person:bob → likes → interest:hiking  
person:carol → likes → interest:wine, interest:reading

Response: "Wine tasting would appeal to most members (Alice, Carol). 
          Consider a nature walk with reading for variety."
```

## $graphLookup Explained

The core of GraphRAG is MongoDB's `$graphLookup` aggregation stage:

```javascript
{
  "$graphLookup": {
    "from": "__kg",                    // Collection to search
    "startWith": "$edges.target",       // Start from direct edges
    "connectFromField": "edges.target", // Field containing next targets
    "connectToField": "_id",           // Field to match against
    "as": "network",                   // Output array name
    "maxDepth": 2,                     // How many hops
    "depthField": "hop_distance",      // Track distance
    "restrictSearchWithMatch": {       // Filter during traversal
      "app_slug": "myapp"
    }
  }
}
```

This performs a recursive graph traversal in a single database operation, following edges from node to node up to `maxDepth` hops.

## Best Practices

### 1. Node ID Convention

Use `type:identifier` format for clear, collision-free IDs:

```
person:alex          (not just "alex")
interest:golf        (not just "golf")
organization:google  (not just "google")
```

### 2. Temporal Relationships

Use the `active` flag for relationships that change over time:

```python
# Alex no longer works at OldCorp
graph_store.deactivate_edge("person:alex", "works_at", "organization:oldcorp")

# Add new job
graph_store.add_edge("person:alex", "works_at", "organization:newcorp")
```

### 3. Weight Relationships

Use `weight` (0.0-1.0) to indicate relationship strength:

```python
# Strong preference
graph_store.add_edge("person:alex", "loves", "interest:golf", weight=0.95)

# Mild interest
graph_store.add_edge("person:alex", "likes", "interest:tennis", weight=0.6)
```

### 4. Depth Limits

- Depth 1: Direct relationships only
- Depth 2: One hop away (most common)
- Depth 3+: Use sparingly (exponential growth)

### 5. Combine with Vector Memory

GraphRAG works best alongside vector memory:

- **Vector Memory**: "What did Alex say about golf last week?"
- **Graph Memory**: "What does Alex like?" (follows relationships)

Both are included in `CognitiveEngine.chat()` automatically.

## Performance Considerations

1. **Index on `edges.target`**: Automatically created for efficient traversal
2. **App-scoped queries**: Always filter by `app_slug` for multi-tenant isolation
3. **Depth limits**: Keep `max_depth` ≤ 3 for production workloads
4. **Vector index**: Enable for hybrid search entry point efficiency

## Troubleshooting

### Graph extraction returns no nodes

Check that:
1. `auto_extract` is `true` in `graph_config`
2. LLM service is configured (check `llm_config` or LLM API keys)
3. LLM API key is set (OPENAI_API_KEY, AZURE_OPENAI_API_KEY, etc.)
4. Text contains extractable entities

### Traversal returns empty results

Verify:
1. Start node exists: `graph_service.get_node("person:alex")`
2. Edges exist: Check `node["edges"]` array
3. App slug matches: `graph_service.app_slug`
4. Edges are active: `edge["active"] == True`

### Hybrid search not finding nodes

Ensure:
1. Embedding service is configured (Graph Service requires EmbeddingService)
2. Vector index exists in MongoDB Atlas
3. Query is semantically related to node names/properties

### Graph Service not initialized

Check that:
1. Graph is enabled by default - ensure `graph_config.enabled` is not explicitly set to `false`
2. LLM service is configured if using auto_extract (gracefully degrades if unavailable)
3. Embedding service is configured for hybrid search (gracefully degrades if unavailable)
4. Check logs for initialization errors
