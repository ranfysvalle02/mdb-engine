---
name: mdb-graph-service
description: Guide for using mdb-engine's graph service (knowledge graph) and GraphRAG. Use when building knowledge graphs, adding graph_config to manifests, using graph traversal, hybrid search, entity extraction, community detection, or GraphRAG features.
---

# MDB-Engine Graph Service & GraphRAG

## 1. Manifest Config

Graph is **enabled by default**. Override to customize:

```json
{
  "graph_config": {
    "collection_name": "kg",
    "auto_extract": true,
    "default_max_depth": 2,
    "node_types": ["person", "interest", "event", "location", "organization"]
  }
}
```

To disable: `"graph_config": {"enabled": false}`.

Collection name default is `"kg"` (prefixed with app slug automatically). **Never use `"__kg"`.** If a `__` prefix is detected, the engine auto-rewrites to the stripped name and logs a deprecation warning.

### With GraphRAG

```json
{
  "graph_config": {
    "node_types": ["person", "interest", "event", "location"]
  },
  "graphrag_config": {
    "community_detection": {"enabled": true},
    "community_summaries": {"enabled": true},
    "query_classification": {"enabled": true, "use_llm": true}
  }
}
```

---

## 2. Route Patterns

### Required dependency — raises 503 if not configured

```python
from mdb_engine.dependencies import get_graph_service

@app.post("/graph/extract")
async def extract(text: str, graph=Depends(get_graph_service)):
    return await graph.extract_graph_from_text(text, user_id="user1")
```

### Optional dependency — returns None gracefully

```python
from mdb_engine.dependencies import get_graph_service_optional

@app.get("/search")
async def search(q: str, graph=Depends(get_graph_service_optional)):
    if graph:
        return await graph.hybrid_search(query=q, user_id="user1")
    return {"results": [], "method": "vector_only"}
```

Use `get_graph_service_optional` for endpoints where graph enhances but isn't required.

### Auto-registered stats endpoint

`/_mdb/graph/stats` is automatically registered when graph is enabled.

---

## 3. Core Operations

### Nodes

```python
await graph.upsert_node(
    node_id="person:alex",
    node_type="person",
    name="Alex",
    properties={"occupation": "Engineer", "city": "NYC"},
    user_id="user1",
)

node = await graph.get_node("person:alex", user_id="user1")
```

Node IDs use `type:identifier` format.

### Edges

```python
await graph.upsert_edge(
    source_id="person:alex",
    target_id="interest:python",
    relationship="interested_in",
    properties={"since": "2020"},
    user_id="user1",
)
```

### Traversal

```python
network = await graph.traverse(
    start_node_id="person:alex",
    max_depth=2,
    user_id="user1",
)
```

### LLM-Powered Entity Extraction

```python
result = await graph.extract_graph_from_text(
    text="Alex is a software engineer who loves Python and lives in NYC.",
    user_id="user1",
)
```

Auto-extracts nodes (person, interest, location) and edges (relationships) using LLM.

---

## 4. Search Methods (GraphRAG)

### Hybrid Search (default)

Vector search finds entry points, graph traversal expands context:

```python
results = await graph.hybrid_search(
    query="What does Alex like?",
    user_id="user1",
    max_depth=2,
    vector_limit=5,
)
```

### Advanced GraphRAG Search

Microsoft Research-style with automatic query classification:

```python
results = await graph.advanced_graph_search(
    query="What are the common themes in our team?",
    user_id="user1",
)
```

Auto-selects the best method:
- **Local Search** — entity-focused queries ("What does Alex like?")
- **Global Search** — thematic queries ("What are the trends?") via map-reduce over communities
- **DRIFT Search** — entity + community context

---

## 5. Memory + Graph Integration

When both `memory_config` and `graph_config` are enabled, the engine auto-injects the graph service into memory. Set `auto_extract: true` in graph_config and the memory service automatically extracts graph nodes when memories are added.

```json
{
  "memory_config": {
    "preset": "full",
    "graph": {"enabled": true, "auto_extract": true}
  },
  "graph_config": {
    "node_types": ["person", "interest", "event"]
  }
}
```

No manual wiring needed. The engine creates shared LLM/embedding services once and passes them to both memory and graph.

---

## 6. Accessing from app.state

After initialization, services are on `app.state`:

```python
request.app.state.graph_service    # GraphService | None
request.app.state.memory_service   # MemoryService | None
request.app.state.llm_service      # LLMService | None
request.app.state.embedding_service  # EmbeddingService | None
```

---

## 7. Multi-App Support

Graph service is fully initialized in both `create_app` and `create_multi_app`. No manual `on_startup` graph wiring is needed.

The initialization order is guaranteed: shared services -> graph -> memory -> Perfect Brain.

---

## 8. Rules

- **DO** use `get_graph_service_optional` for endpoints where graph is optional.
- **DO** use `type:identifier` node ID format (e.g., `person:alex`).
- **DO NOT** use `"__kg"` as collection name — use `"kg"`.
- **DO NOT** manually create LLM/embedding services for graph — they're shared.
- **DO NOT** add `app_id` to vector index definitions — auto-injected.
- **DO NOT** manually initialize graph in `on_startup` — the engine handles it.
- **DO** let the engine handle graph-before-memory init order.
- Graph service retries lazily if startup init fails.
