# Memory Service Module

Native MongoDB Atlas Vector Search integration for intelligent memory management in MDB-Engine applications. Provides semantic memory storage, retrieval, and inference capabilities with full control over the stack.

## Features

### Core Capabilities

- **BaseMemoryService**: Abstract base class defining the memory service interface
- **CognitiveMemoryService**: The default (and only) memory service — configurable from basic to advanced
- **Provider Extensibility**: Easy to add new memory providers via the base class
- **Type Safety**: Full type hints and abstract base class for IDE support

### CognitiveMemoryService

- **Perfect Recall**: All memories are permanently accessible, ranked by importance + access frequency
- **Importance Scoring**: AI evaluates memory significance (0.1-1.0 scale)
- **Memory Reinforcement**: Similar memories strengthen existing memories (similarity 0.85-0.90)
- **Memory Merging**: Related memories are combined intelligently (similarity 0.70-0.85)
- **Duplicate Detection**: Prevents semantically identical memories (similarity >= 0.90)
- **Access Tracking**: Tracks how often memories are accessed (affects ranking)
- **Knowledge Graph Integration**: Automatically extracts entities/relationships to `GraphService` when `memory_config.graph.enabled` is set

### Cognitive Architecture

- **Short-Term Memory (STM)**: Chat history for immediate context
- **Long-Term Memory (LTM)**: Vector store for semantic retrieval
- **CognitiveEngine**: Orchestrates STM + LTM for complete RAG pipeline
- **Auto-Summarization**: Automatically summarizes long sessions

### Perfect Brain Features

- **Multi-Tier Memory Architecture**: Working, Episodic, Semantic, Procedural, Reflective, Predictive layers
- **Memory Consolidator**: Reflection loop that distills episodic memories into semantic facts
- **CognitiveMemory**: Multi-tier memory system controller
- **Shared/Group Memory**: Privacy-safe promotion of facts within groups
- **Reflective Memory**: Meta-cognitive insights about system behavior and patterns
- **Predictive Memory**: Counterfactuals, simulations, and future scenarios with validation
- **Memory Vetoes**: User-controlled "never share" flags for sensitive memories
- **Query-Aware Recall**: Policy-driven memory retrieval based on task type, risk tolerance, and latency budget
- **Memory Versioning**: Track belief evolution and historical states over time
- **Timeline Service**: Multiverse support for counterfactual reasoning and parallel timelines
- **Bucket Filtering**: All memory types support bucket filtering for contextual isolation

## Configuration

### Manifest Configuration

Enable memory service in your `manifest.json`:

```json
{
  "slug": "my_app",
  "llm_config": {
    "enabled": true,
    "default_model": "openai/gpt-4o"
  },
  "memory_config": {
    "enabled": true,
    "collection_name": "memories",
    "embedding_model": "text-embedding-3-small",
    "embedding_dims": 1536,
    "infer": true,

    "graph": {
      "enabled": true,
      "auto_extract": true
    }
  },
  "graph_config": {
    "enabled": true,
    "collection_name": "kg",
    "auto_extract": true,
    "default_max_depth": 2,
    "node_types": ["person", "interest", "event", "location", "organization", "product", "concept"]
  }
}
```

**LLM Model Inheritance**: The memory service automatically inherits the LLM model from `llm_config.default_model` if `memory_config.memory_llm_model` is not explicitly set.

### Graph Integration

Graph extraction is controlled by two config sections working together:

1. **`graph_config`** (top-level) — initializes the standalone `GraphService` with MongoDB `$graphLookup` traversal.
2. **`memory_config.graph`** — controls whether the memory service calls `GraphService.extract_graph_from_text()` when new memories are added.

Both must be enabled for automatic graph extraction during `add()`. Graph extraction is non-blocking — failures never prevent memory storage.

### Cognitive Memory Configuration

For advanced cognitive features:

```json
{
  "memory_config": {
    "enabled": true,
    "provider": "cognitive",
    "similarity_threshold": 0.7,
    "reinforcement_factor": 1.1,
    "merge_threshold_low": 0.7,
    "merge_threshold_high": 0.85
  }
}
```

## Usage

### Basic Usage

```python
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(mongo_uri="...", db_name="...")
await engine.initialize()

memory_service = engine.get_memory_service("my_app")

# Add memory (extracts facts, assesses importance, deduplicates)
memories = memory_service.add(
    messages=[{"role": "user", "content": "I love Python programming"}],
    user_id="user123"
)

# Search memories (ranked by similarity * effective_importance)
results = await memory_service.search(
    query="What does the user like?",
    user_id="user123",
    limit=5
)
```

### Cognitive Engine (STM + LTM)

```python
from mdb_engine.memory import CognitiveEngine

engine = CognitiveEngine(
    mongo_uri="...",
    db_name="...",
    app_slug="my_app",
    llm_service=llm_service,
)

result = engine.chat(
    user_id="user_123",
    session_id="conversation:55",
    user_query="What's my favorite color?",
)
```

## API Reference

### BaseMemoryService Methods

All memory services implement:

- `add()` — Add memories with automatic fact extraction
- `inject()` — Direct injection bypassing LLM
- `search()` — Semantic search with filtering
- `get()` — Retrieve single memory by ID
- `get_all()` — Retrieve all memories with filtering
- `update()` — Update memory with automatic re-embedding
- `delete()` — Delete single memory
- `delete_all()` — Delete all memories for a user

## Available Modules

### Core Services
- `BaseMemoryService` — Abstract base class
- `CognitiveMemoryService` — The memory service (configurable)
- `get_memory_service()` — Factory function

### Multi-Tier System
- `CognitiveMemory` — Multi-tier memory system controller
- `MemoryConsolidator` — Reflection loop for memory consolidation

### Perfect Brain Features
- `ReflectiveMemory` — Meta-cognitive insights
- `PredictiveMemory` — Counterfactuals and simulations
- `SharedMemory` — Privacy-safe group memory
- `MemoryVeto` — User-controlled sharing restrictions
- `QueryAwareRecall` — Policy-driven memory retrieval
- `MemoryVersioning` — Belief evolution tracking
- `TimelineService` — Multiverse support
- `ProspectiveMemory` — Intention-based triggers

### Orchestration
- `CognitiveEngine` — STM + LTM orchestrator
- `ChatHistoryService` — Short-term memory management
- `ReflectionService` — Memory consolidation service

### Procedural Memory
- `ProceduralMemory` — Executable skills and workflows
- `retrieve_procedural_memory()` — Retrieve procedural knowledge

## Documentation

- [Memory Service Guide](../../docs/MEMORY_SERVICE.md)
- [Memory System Complete Reference](../../docs/MEMORY_SYSTEM_COMPLETE.md)
- [Graph Service](../../docs/GRAPH_SERVICE.md)
- [Files and Buckets Guide](../../docs/guides/FILES_AND_BUCKETS.md)
