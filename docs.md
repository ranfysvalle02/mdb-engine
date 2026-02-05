# MDB-Engine Documentation (v0.7.6)

**The MongoDB Engine for Python Apps** — Auto-sandboxing, index management, auth, and comprehensive AI services in one package.

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Core Concepts](#core-concepts)
3. [API Reference](#api-reference)
4. [Manifest Configuration](#manifest-configuration)
5. [Services](#services)
6. [Authentication & Authorization](#authentication--authorization)
7. [Advanced Patterns](#advanced-patterns)
8. [Migration Guide](#migration-guide)

---

## Quick Start

### Installation

```bash
pip install mdb-engine
```

### Prerequisites

MDB-Engine requires a running MongoDB instance:

```bash
# Docker (recommended)
docker run -d --name mdb_mongodb -p 27017:27017 mongo:7.0

# Or Docker Compose
docker-compose up -d mongodb
```

### Minimal Example

**1. Create `manifest.json`**

```json
{
  "schema_version": "2.0",
  "slug": "todo_app",
  "name": "Todo List App"
}
```

**2. Create `app.py`**

```python
from pathlib import Path
from fastapi import Depends
from mdb_engine import MongoDBEngine
from mdb_engine.dependencies import get_scoped_db

engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database"
)

app = engine.create_app(
    slug="todo_app",
    manifest=Path("manifest.json")
)

@app.post("/tasks")
async def create_task(task: dict, db=Depends(get_scoped_db)):
    result = await db.tasks.insert_one(task)
    return {"id": str(result.inserted_id)}

@app.get("/tasks")
async def list_tasks(db=Depends(get_scoped_db)):
    return await db.tasks.find({}).to_list(length=100)
```

**3. Run**

```bash
uvicorn app:app --reload
```

**What you get automatically:**
- ✅ Data isolation (all queries filtered by `app_id`)
- ✅ Collection prefixing (`db.tasks` → `todo_app_tasks`)
- ✅ Lifecycle management (startup/shutdown)
- ✅ Health endpoints

---

## Core Concepts

### The manifest.json File

The `manifest.json` is the heart of your application. It defines:

| Section | Purpose |
|---------|---------|
| `slug`, `name` | App identity and registration |
| `managed_indexes` | Declarative index definitions |
| `auth` | Authentication and authorization |
| `memory_config` | AI memory service configuration (CognitiveMemoryService) |
| `graph_config` | Knowledge graph service configuration |
| `embedding_config` | Text embedding service |
| `llm_config` | LLM provider configuration (100+ providers via LiteLLM) |
| `websockets` | Real-time WebSocket endpoints |
| `cors` | CORS settings |
| `observability` | Health checks and metrics |

### Data Scoping

All database operations through `get_scoped_db` are automatically scoped:

```python
# You write:
await db.tasks.find({"status": "active"}).to_list(10)

# Engine executes:
# Collection: my_app_tasks (prefixed)
# Query: {"status": "active", "app_id": "my_app"} (filtered)
```

This makes multi-tenant applications secure by default.

### Engine Lifecycle

```python
# Option 1: Automatic (recommended)
app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))

# Option 2: Manual control
@app.on_event("startup")
async def startup():
    await engine.initialize()
    manifest = await engine.load_manifest(Path("manifest.json"))
    await engine.register_app(manifest, create_indexes=True)

@app.on_event("shutdown")
async def shutdown():
    await engine.shutdown()
```

---

## API Reference

### MongoDBEngine

The central orchestrator for all MDB-Engine functionality.

#### Constructor

```python
MongoDBEngine(
    mongo_uri: str,
    db_name: str,
    manifests_dir: Path | None = None,
    authz_provider: AuthorizationProvider | None = None,
    max_pool_size: int = 100,
    min_pool_size: int = 10,
    enable_ray: bool = False,
    ray_namespace: str = "modular_labs",
    csfle_config: CSFLEConfig | None = None,
)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `mongo_uri` | `str` | MongoDB connection URI |
| `db_name` | `str` | Database name |
| `manifests_dir` | `Path \| None` | Directory containing manifest files |
| `authz_provider` | `AuthorizationProvider \| None` | Custom authorization provider |
| `max_pool_size` | `int` | Maximum connection pool size (default: 100) |
| `min_pool_size` | `int` | Minimum connection pool size (default: 10) |
| `enable_ray` | `bool` | Enable Ray for distributed processing |
| `csfle_config` | `CSFLEConfig \| None` | Client-side field-level encryption config |

#### Methods

##### `async initialize() -> None`

Initialize MongoDB connection and all components.

```python
await engine.initialize()
```

##### `create_app(slug, manifest, title, version, on_startup) -> FastAPI`

Create a FastAPI application with automatic lifecycle management.

```python
app = engine.create_app(
    slug="my_app",
    manifest=Path("manifest.json"),
    title="My Application",
    version="1.0.0",
    on_startup=async_startup_callback,  # Optional
)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `slug` | `str` | Unique app identifier |
| `manifest` | `Path \| dict` | Path to manifest or manifest dict |
| `title` | `str \| None` | FastAPI app title |
| `version` | `str` | App version (default: "1.0.0") |
| `on_startup` | `Callable \| None` | Async callback: `async def fn(app, engine, manifest)` |

##### `get_scoped_db(slug, read_scopes, write_scope) -> ScopedMongoWrapper`

Get a database wrapper with automatic scoping.

```python
db = engine.get_scoped_db("my_app")
# Or with cross-app access:
db = engine.get_scoped_db(
    "my_app",
    read_scopes=["my_app", "shared_data"],
    write_scope="my_app"
)
```

##### `get_memory_service(slug) -> BaseMemoryService | None`

Get the memory service for an app.

```python
memory = engine.get_memory_service("my_app")
if memory:
    results = memory.search(query="...", user_id="user123")
```

##### `get_embedding_service(slug) -> EmbeddingService | None`

Get the embedding service for an app.

##### `get_graph_service(slug) -> GraphService | None`

Get the graph service for an app (if enabled in manifest).

##### `get_llm_service(slug) -> LLMService | None`

Get the LLM service for an app (if enabled in manifest).

##### `async register_app(manifest, create_indexes) -> None`

Register an app from a manifest dictionary.

```python
manifest = {"schema_version": "2.0", "slug": "my_app", "name": "My App"}
await engine.register_app(manifest, create_indexes=True)
```

##### `async load_manifest(path) -> dict`

Load and validate a manifest from file.

```python
manifest = await engine.load_manifest(Path("manifest.json"))
```

##### `get_app(slug) -> dict | None`

Get an app's configuration.

```python
config = engine.get_app("my_app")
```

##### `async get_health_status() -> dict`

Get health status of all components.

```python
status = await engine.get_health_status()
# {"status": "healthy", "mongodb": "connected", ...}
```

##### `async shutdown() -> None`

Clean shutdown of all connections.

```python
await engine.shutdown()
```

#### Properties

| Property | Type | Description |
|----------|------|-------------|
| `initialized` | `bool` | Whether engine is initialized |
| `apps` | `dict[str, dict]` | Registered apps |
| `mongo_client` | `AsyncIOMotorClient` | Direct MongoDB client access |

---

### ScopedMongoWrapper

Database wrapper with automatic app scoping.

#### Usage

```python
db = engine.get_scoped_db("my_app")

# All operations are automatically scoped
await db.tasks.find({"status": "active"}).to_list(10)
await db.tasks.insert_one({"title": "Task 1"})
await db.tasks.update_one({"_id": id}, {"$set": {"done": True}})
await db.tasks.delete_one({"_id": id})
```

#### Behavior

| Operation | What Happens |
|-----------|--------------|
| `db.collection` | Returns prefixed collection (`my_app_collection`) |
| `find()`, `find_one()` | Adds `{"app_id": "my_app"}` to query |
| `insert_one()`, `insert_many()` | Adds `{"app_id": "my_app"}` to documents |
| `update_one()`, etc. | Adds `{"app_id": "my_app"}` to filter |
| `delete_one()`, etc. | Adds `{"app_id": "my_app"}` to filter |

---

### CognitiveMemoryService

The primary memory service implementation with advanced cognitive features. Provides intelligent memory management using MongoDB Atlas Vector Search.

**LLM Model Inheritance**: Memory service automatically uses the LLM model from `llm_config.default_model`. Override with `memory_config.memory_llm_model` if needed. All memory operations (fact extraction, importance assessment) use this model.

#### Methods

##### `add(messages, user_id, metadata, bucket_id, bucket_type) -> list[dict]`

Add memories with LLM fact extraction.

```python
memories = memory.add(
    messages=[
        {"role": "user", "content": "I'm allergic to peanuts"},
        {"role": "assistant", "content": "I'll remember that."}
    ],
    user_id="user123",
    metadata={"conversation_id": "conv456"}
)
# Returns: [{"id": "...", "memory": "User is allergic to peanuts", ...}]
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `messages` | `str \| list[dict]` | Content or chat messages |
| `user_id` | `str \| None` | User scoping (recommended) |
| `metadata` | `dict \| None` | Additional metadata |
| `bucket_id` | `str \| None` | Optional memory grouping |
| `bucket_type` | `str \| None` | Bucket type (e.g., "conversation") |

##### `inject(memory, user_id, metadata, bucket_id, bucket_type) -> dict`

Inject memory directly without LLM inference.

```python
result = memory.inject(
    memory="User prefers dark mode",
    user_id="user123",
    metadata={"category": "preferences"}
)
```

##### `search(query, user_id, limit, filters) -> list[dict]`

Semantic search using MongoDB Atlas Vector Search.

```python
results = memory.search(
    query="What are the user's allergies?",
    user_id="user123",
    limit=5,
    filters={"metadata": {"category": "health"}}
)
# Returns: [{"id": "...", "memory": "...", "score": 0.95, ...}]
```

##### `get(memory_id, user_id) -> dict | None`

Get a single memory by ID.

```python
memory_obj = memory.get(memory_id="mem123", user_id="user123")
```

##### `get_all(user_id, limit, filters) -> list[dict]`

Get all memories for a user.

```python
all_memories = memory.get_all(user_id="user123", limit=100)
```

##### `update(memory_id, user_id, memory, metadata) -> dict | None`

Update memory content (automatically re-embeds).

```python
updated = memory.update(
    memory_id="mem123",
    user_id="user123",
    memory="Updated content",
    metadata={"updated": True}
)
```

##### `delete(memory_id, user_id) -> bool`

Delete a single memory.

```python
success = memory.delete(memory_id="mem123", user_id="user123")
```

##### `delete_all(user_id) -> bool`

Delete all memories for a user.

```python
success = memory.delete_all(user_id="user123")
```

#### Cognitive Features (when `enable_cognitive: true`)

**Available Features**:
- **Importance Scoring**: AI evaluates memory significance (0.1-1.0 scale)
- **Memory Reinforcement**: Similar memories strengthen existing ones
- **Memory Decay**: Less relevant memories fade over time
- **Memory Merging**: Related memories combined intelligently
- **Memory Pruning**: Least important memories removed when capacity exceeded
- **Cold Storage**: Pruned memories retained for potential recovery
- **Parallel Processing**: Optimized parallel execution (5-10x faster)
- **Knowledge Conflict Detection**: Identifies logical contradictions

##### `prune_memories(user_id, max_capacity, reason) -> int`

Soft-delete weakest memories to cold storage.

```python
pruned_count = memory.prune_memories(
    user_id="user123",
    max_capacity=100,
    reason="capacity_limit"
)
```

##### `get_cold_storage(user_id, limit, include_reason) -> list[dict]`

Get pruned memories from cold storage.

```python
cold_memories = memory.get_cold_storage(user_id="user123", limit=50)
```

##### `restore_from_cold_storage(memory_id, user_id) -> dict | None`

Restore a memory from cold storage.

```python
restored = memory.restore_from_cold_storage(
    memory_id="mem123",
    user_id="user123"
)
```

##### `get_memory_analytics(user_id) -> dict`

Get memory health metrics.

```python
analytics = memory.get_memory_analytics(user_id="user123")
# {"active_memories": 50, "average_strength": 0.75, ...}
```

##### `detect_knowledge_conflict(user_id, new_fact) -> str | None`

Check for logical contradictions.

```python
conflict = memory.detect_knowledge_conflict(
    user_id="user123",
    new_fact="User is allergic to penicillin"
)
# Returns conflict description or None
```

---

### LLMService

Unified LLM interface powered by LiteLLM (100+ providers).

**CRITICAL**: All services that require an LLM automatically inherit the model from `llm_config.default_model` in the manifest. This ensures consistent LLM usage across the entire application.

**Services that inherit LLM model**:
- Memory Service (fact extraction, importance assessment, memory merging)
- Graph Service (entity/relationship extraction)
- Reflection Service (memory consolidation)
- Memory Fusion Service (fact deduplication)
- Perception Engine (perception extraction)

#### Constructor

```python
from mdb_engine.llm import get_llm_service

service = get_llm_service(config={
    "default_model": "gemini/gemini-3-flash-preview",
    "fallbacks": ["gpt-4o-mini", "claude-3-5-haiku"],
    "litellm_config": {
        "num_retries": 2,
        "request_timeout": 60
    }
})
```

#### LLM Model Inheritance

When you configure `llm_config.default_model` in your manifest, all services automatically use this model:

- **Memory Service**: Fact extraction, importance assessment, memory merging
- **Graph Service**: Entity/relationship extraction
- **Reflection Service**: Memory consolidation and salience assessment
- **Memory Fusion Service**: Intelligent fact deduplication

**Example**: If your `llm_config.default_model` is `"gemini/gemini-3-flash-preview"`, all these services will use Gemini, not hardcoded defaults.

**Service-Specific Overrides**: You can override the model for specific services if needed:

```json
{
  "llm_config": {
    "default_model": "gemini/gemini-3-flash-preview"
  },
  "memory_config": {
    "memory_llm_model": "openai/gpt-4o"  // Override for memory operations only
  },
  "graph_config": {
    "llm_model": "anthropic/claude-sonnet-4"  // Override for graph extraction only
  }
}
```

#### Methods

##### `async chat_completion(messages, model, temperature, max_tokens, response_format) -> str`

Generate chat completion response.

```python
# Basic usage
response = await service.chat_completion(
    messages=[{"role": "user", "content": "Hello!"}],
    temperature=0.7
)

# With specific model
response = await service.chat_completion(
    messages=[{"role": "user", "content": "Hello!"}],
    model="gemini/gemini-3-flash-preview"
)

# Structured output with Pydantic
from pydantic import BaseModel

class MovieInfo(BaseModel):
    title: str
    year: int
    genre: str

response = await service.chat_completion(
    messages=[{"role": "user", "content": "Extract: The Matrix (1999) sci-fi"}],
    response_format=MovieInfo
)
```

#### Supported Providers (100+ via LiteLLM)

| Provider | Model Format | Required Env Vars |
|----------|--------------|-------------------|
| OpenAI | `openai/gpt-4o` | `OPENAI_API_KEY` |
| Azure OpenAI | `azure/{deployment}` | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT` |
| Anthropic | `anthropic/claude-sonnet-4-20250514` | `ANTHROPIC_API_KEY` |
| Google Gemini | `gemini/gemini-3-flash-preview` | `GEMINI_API_KEY` |
| Cohere | `cohere/command-r-plus` | `COHERE_API_KEY` |
| Ollama | `ollama/llama2` | (local) |
| Groq | `groq/llama-3.1-70b-versatile` | `GROQ_API_KEY` |

---

### EmbeddingService

Text chunking and embedding generation. Supports multiple embedding providers including OpenAI, Voyage AI, and more.

**Features**:
- Semantic text chunking respecting token limits
- Multiple embedding model support
- Batch embedding operations
- MongoDB integration for vector storage

#### Constructor

```python
from mdb_engine.embeddings import get_embedding_service

service = get_embedding_service(config={
    "default_embedding_model": "text-embedding-3-small",
    "max_tokens_per_chunk": 1000,
    "tokenizer_model": "gpt-3.5-turbo"
})
```

#### Methods

##### `chunk_text(text, max_tokens) -> list[str]`

Split text into semantic chunks.

```python
chunks = service.chunk_text(text="Long document...", max_tokens=500)
```

##### `async embed_chunks(chunks, model) -> list[list[float]]`

Generate embeddings for chunks.

```python
embeddings = await service.embed_chunks(chunks=["Hello", "World"])
```

##### `async process_and_store(text_content, source_id, collection, metadata) -> dict`

Chunk, embed, and store in MongoDB.

```python
result = await service.process_and_store(
    text_content="Long document...",
    source_id="doc123",
    collection=db.documents,
    metadata={"author": "John"}
)
# {"chunks_created": 5, "source_id": "doc123", ...}
```

---

### FastAPI Dependencies

#### Available Dependencies

```python
from mdb_engine.dependencies import (
    get_engine,             # -> MongoDBEngine
    get_app_slug,            # -> str
    get_app_config,          # -> dict
    get_scoped_db,           # -> ScopedMongoWrapper
    get_unit_of_work,        # -> UnitOfWork
    get_embedding_service,   # -> EmbeddingService | None
    get_memory_service,      # -> BaseMemoryService | None
    get_llm_client,          # -> OpenAI | AzureOpenAI | None
    get_llm_model_name,      # -> str
    get_authz_provider,      # -> AuthorizationProvider | None
    get_current_user,         # -> dict | None
    get_user_roles,          # -> list[str]
    require_user,            # Factory -> requires auth
    require_role,             # Factory -> requires role
    RequestContext,          # All-in-one context
)

# Additional service dependencies
from mdb_engine.graph.dependencies import get_graph_service_dependency  # -> GraphService | None
from mdb_engine.llm.dependencies import get_llm_service_dependency      # -> LLMService | None
```

#### RequestContext

All-in-one request context with lazy-loaded dependencies.

```python
from mdb_engine.dependencies import RequestContext

@app.post("/action")
async def action(ctx: RequestContext = Depends()):
    # Properties (lazy-loaded)
    db = ctx.db                     # ScopedMongoWrapper
    memory = ctx.memory             # BaseMemoryService | None
    embedding = ctx.embedding_service  # EmbeddingService | None
    graph_service = ctx.graph_service  # GraphService | None (if available)
    llm_service = ctx.llm_service     # LLMService | None (if available)
    llm = ctx.llm                   # OpenAI | AzureOpenAI | None (legacy)
    model = ctx.llm_model           # str
    user = ctx.user                 # dict | None
    roles = ctx.user_roles          # list[str]
    authz = ctx.authz               # AuthorizationProvider | None
    config = ctx.config             # dict (manifest)
    slug = ctx.slug                 # str

    # Methods
    user = ctx.require_user()       # Raises 401 if not authenticated
    ctx.require_role("admin")       # Raises 403 if missing role
    has_perm = await ctx.check_permission("resource", "action")
```

---

## Manifest Configuration

### Complete Schema

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My Application",
  "description": "Optional description",
  "status": "active",
  "developer_id": "developer@example.com",

  "data_access": {
    "read_scopes": ["my_app", "shared_data"],
    "write_scope": "my_app"
  },

  "managed_indexes": {
    "collection_name": [
      {
        "type": "regular",
        "keys": {"field": 1, "other_field": -1},
        "name": "field_idx",
        "unique": false,
        "sparse": false,
        "background": true
      },
      {
        "type": "text",
        "keys": {"content": "text"},
        "name": "content_text_idx"
      },
      {
        "type": "ttl",
        "keys": {"expires_at": 1},
        "expireAfterSeconds": 3600
      },
      {
        "type": "vectorSearch",
        "name": "vector_idx",
        "definition": {
          "fields": [{
            "type": "vector",
            "path": "embedding",
            "numDimensions": 1536,
            "similarity": "cosine"
          }]
        }
      }
    ]
  },

  "auth": {
    "mode": "app",
    "policy": {
      "provider": "casbin",
      "required": true,
      "allow_anonymous": false,
      "authorization": {
        "model": "rbac",
        "policies_collection": "casbin_policies",
        "link_users_roles": true,
        "default_roles": ["user"],
        "initial_policies": [
          ["admin", "documents", "read"],
          ["admin", "documents", "write"],
          ["admin", "documents", "delete"],
          ["user", "documents", "read"]
        ],
        "initial_roles": [
          {"user": "admin@example.com", "role": "admin"}
        ]
      }
    },
    "users": {
      "enabled": true,
      "strategy": "app_users",
      "collection_name": "users",
      "session_cookie_name": "app_session",
      "session_ttl_seconds": 86400,
      "allow_registration": true,
      "demo_users": [
        {
          "email": "admin@example.com",
          "password": "password123",
          "role": "admin"
        }
      ]
    }
  },

  "token_management": {
    "enabled": true,
    "access_token_ttl": 900,
    "refresh_token_ttl": 604800,
    "token_rotation": true,
    "max_sessions_per_user": 10,
    "session_inactivity_timeout": 1800,
    "security": {
      "require_https": false,
      "cookie_secure": "auto",
      "cookie_samesite": "lax",
      "cookie_httponly": true,
      "csrf_protection": true,
      "rate_limiting": {
        "login": {"max_attempts": 5, "window_seconds": 300},
        "register": {"max_attempts": 3, "window_seconds": 600}
      },
      "password_policy": {
        "min_length": 8,
        "require_uppercase": true,
        "require_lowercase": true,
        "require_numbers": true,
        "require_special": false
      }
    }
  },

  "memory_config": {
    "enabled": true,
    "provider": "cognitive",
    "collection_name": "user_memories",
    "embedding_model": "text-embedding-3-small",
    "embedding_model_dims": 1536,
    "infer": true,
    "enable_cognitive": true,
    "max_depth": 100,
    "memory_llm_model": null,  # Override llm_config.default_model if needed
    "cognitive": {
      "enabled": true,
      "decay": {
        "enabled": true,
        "default_stability_hours": 48
      },
      "emotion": {
        "enabled": true,
        "flashbulb_threshold": 0.7
      },
      "pruning": {
        "enabled": true,
        "max_capacity": 500,
        "strategy": "soft_delete"
      },
      "cold_storage": {
        "enabled": true,
        "retention_days": 365
      }
    },
    "redaction": {
      "enabled": true,
      "provider": "regexp",
      "replacement": "[REDACTED]",
      "patterns": {
        "ssn": true,
        "credit_card": true,
        "password": true,
        "api_key": true
      }
    },
    "reflection": {
      "enabled": true,
      "interval_hours": 24,
      "message_threshold": 50
    },
    "entities": {
      "enabled": true,
      "auto_extract": true
    }
  },

  "graph_config": {
    "enabled": true,
    "auto_extract": true,
    "collection_name": "knowledge_graph",
    "llm_model": null  # Override llm_config.default_model if needed
  },

  "llm_config": {
    "enabled": true,
    "default_model": "gemini/gemini-3-flash-preview",
    "fallbacks": ["gpt-4o-mini"],
    "litellm_config": {
      "num_retries": 2,
      "request_timeout": 60
    }
  },

  "embedding_config": {
    "enabled": true,
    "default_embedding_model": "text-embedding-3-small",
    "max_tokens_per_chunk": 1000,
    "tokenizer_model": "gpt-3.5-turbo"
  },

  "websockets": {
    "realtime": {
      "path": "/ws",
      "description": "Real-time updates",
      "auth": {
        "required": true,
        "allow_anonymous": false
      },
      "ping_interval": 30,
      "ticket_ttl_seconds": 10
    }
  },

  "cors": {
    "enabled": true,
    "allow_origins": ["*"],
    "allow_credentials": true,
    "allow_methods": ["*"],
    "allow_headers": ["*"]
  },

  "observability": {
    "health_checks": {
      "enabled": true,
      "endpoint": "/health"
    },
    "metrics": {
      "enabled": true,
      "collect_operation_metrics": true
    }
  }
}
```

### Index Types

| Type | Description | Example Keys |
|------|-------------|--------------|
| `regular` | Standard B-tree index | `{"field": 1}` or `{"a": 1, "b": -1}` |
| `text` | Full-text search | `{"content": "text"}` |
| `vectorSearch` | MongoDB Atlas Vector Search | See definition format |
| `ttl` | Time-to-live expiration | `{"expires_at": 1}` + `expireAfterSeconds` |
| `geospatial` | Geographic queries | `{"location": "2dsphere"}` |

---

### RedactionService

Standalone redaction service for protecting sensitive data (PII). Supports multiple providers: REGEXP (default) and Microsoft Presidio.

```python
from mdb_engine.redaction import get_redaction_service

# REGEXP provider (default)
redactor = get_redaction_service(config={
    "provider": "regexp",
    "patterns": {
        "ssn": True,
        "credit_card": True,
        "email": True
    },
    "replacement": "[REDACTED]"
})

# Presidio provider (advanced)
redactor = get_redaction_service(config={
    "provider": "presidio",
    "entities": ["NAME", "PHONE_NUMBER", "EMAIL_ADDRESS", "SSN"]
})

text = "John Doe's phone is 123-456-7890 and email is john@example.com"
redacted = redactor.redact(text)
```

**Integration with Memory Service**: Configure in `memory_config.redaction` to automatically redact sensitive data before storage.

---

### GDPR Services

GDPR compliance helpers for user data management.

```python
from mdb_engine.gdpr import (
    DataDiscoveryService,
    DataExportService,
    DataDeletionService,
    DataRectificationService
)

# Discover all user data across collections
discovery = DataDiscoveryService(db)
user_data = discovery.discover_user_data(user_id="user123")

# Export user data (Right to Access)
export_service = DataExportService(db)
export_data = export_service.export_user_data(user_id="user123")

# Delete user data (Right to Erasure)
deletion = DataDeletionService(db)
deletion.delete_user_data(user_id="user123", anonymize=True)

# Rectify user data (Right to Rectification)
rectification = DataRectificationService(db)
rectification.rectify_user_data(user_id="user123", updates={"email": "new@example.com"})
```

**GDPR Rights Supported**:
- **Right to Access**: Export all user data
- **Right to Erasure**: Delete user data (with anonymization option)
- **Right to Rectification**: Update incorrect user data
- **Data Discovery**: Find all collections containing user data

---

## Services

### Memory Service

The memory service provides semantic memory management using MongoDB Atlas Vector Search.

#### Configuration

```json
{
  "memory_config": {
    "enabled": true,
    "collection_name": "user_memories",
    "embedding_model_dims": 1536,
    "infer": true,
    "enable_cognitive": true,
    "max_depth": 100
  }
}
```

**Important:** The memory service automatically creates and manages its vector search index. Do NOT add memory collection indexes to `managed_indexes`.

#### Usage Pattern

```python
import asyncio
from mdb_engine.dependencies import get_memory_service

@app.post("/chat")
async def chat(message: str, user_id: str):
    memory = engine.get_memory_service("my_app")

    # Memory operations are synchronous - use asyncio.to_thread
    memories = await asyncio.to_thread(
        memory.search,
        query=message,
        user_id=user_id,
        limit=5
    )

    # Process response...

    await asyncio.to_thread(
        memory.add,
        messages=[
            {"role": "user", "content": message},
            {"role": "assistant", "content": response}
        ],
        user_id=user_id
    )
```

#### Cognitive Features

When `enable_cognitive: true`:

- **Importance Scoring**: AI evaluates memory significance (0.1-1.0)
- **Memory Reinforcement**: Similar memories strengthen existing ones
- **Memory Decay**: Less relevant memories fade over time
- **Memory Merging**: Related memories combined intelligently
- **Memory Pruning**: Least important memories removed when capacity exceeded
- **Cold Storage**: Pruned memories retained for potential recovery
- **Parallel Processing**: Optimized parallel execution for importance assessment and vector searches (5-10x faster)

**LLM Model**: Memory service operations (fact extraction, importance assessment) automatically use the LLM model configured in `llm_config.default_model`. Override with `memory_config.memory_llm_model` if needed.

**Additional Memory Services**:
- **MemoryFusionService**: Intelligent fact deduplication to prevent duplicate memories
- **ReflectionService**: Periodic memory consolidation to prevent bloat and improve quality
- **PerceptionEngine**: Extracts perceptions and insights from conversations
- **ProceduralMemoryService**: Stores procedural knowledge and workflows


### CognitiveEngine

Complete RAG pipeline orchestrator with STM (Short-Term Memory) + LTM (Long-Term Memory).

```python
from mdb_engine.memory.orchestrator import CognitiveEngine

cognitive_engine = CognitiveEngine(
    app_slug="my_app",
    memory_service=memory_service,
    chat_history_collection=chat_history_col,
    stm_context_limit=10,
    ltm_search_limit=5,
    auto_summarize_threshold=20,
    llm_provider=llm_provider,
)

# Single call handles everything:
# 1. Saves user message to STM
# 2. Searches LTM for relevant memories
# 3. Generates LLM response with context
# 4. Saves AI response to STM
# 5. Extracts facts to LTM
result = await cognitive_engine.chat(
    user_id="user123",
    session_id="session456",
    user_query="What did we discuss about the project?",
    system_prompt="You are a helpful assistant.",
    extract_facts=True
)
```

**Additional Memory Services**:
- **MemoryFusionService**: Intelligent fact deduplication
- **ReflectionService**: Periodic memory consolidation to prevent bloat
- **PerceptionEngine**: Extracts perceptions and insights from conversations
- **ProceduralMemoryService**: Stores procedural knowledge and workflows

---

### GraphService

Standalone knowledge graph service using MongoDB's native `$graphLookup` for graph traversal.

#### Features

- **MongoDB $graphLookup**: Native integration for efficient graph traversal
- **LLM-Powered Extraction**: Extracts entities and relationships from text
- **Hybrid Search**: Combines vector similarity with graph context (GraphRAG)
- **Temporal Edges**: Support for active/inactive relationships with timestamps
- **Multi-Tenant**: App-scoped isolation for multi-app deployments
- **Standalone Service**: Can be used independently or with MemoryService

#### Usage

```python
from mdb_engine.graph import GraphService, get_graph_service

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

# Extract graph from text (GraphRAG)
result = await graph_service.extract_graph_from_text("My brother Alex loves golf", "user123")
```

#### FastAPI Integration

```python
from mdb_engine.graph.dependencies import get_graph_service_dependency

@app.post("/graph/extract")
async def extract_graph(
    text: str,
    graph_service: GraphService = Depends(get_graph_service_dependency)
):
    result = await graph_service.extract_graph_from_text(text, "user123")
    return result
```

---

## Authentication & Authorization

### Per-App Auth (mode: "app")

Isolated users per application.

```json
{
  "auth": {
    "mode": "app",
    "policy": {
      "provider": "casbin",
      "authorization": {
        "model": "rbac",
        "initial_policies": [
          ["admin", "documents", "read"],
          ["admin", "documents", "write"]
        ]
      }
    },
    "users": {
      "enabled": true,
      "allow_registration": true
    }
  }
}
```

### Shared Auth (mode: "shared") - SSO

Single Sign-On across multiple applications.

```json
{
  "auth": {
    "mode": "shared",
    "auth_hub_url": "http://localhost:8000",
    "roles": ["viewer", "editor", "admin"],
    "default_role": "viewer",
    "require_role": "viewer",
    "public_routes": ["/health", "/login"]
  }
}
```

**Requirements:**
- All apps must use the same `MDB_ENGINE_JWT_SECRET`
- All apps must share the same MongoDB database

### Using Authorization

```python
from mdb_engine.dependencies import get_current_user, get_authz_provider

@app.get("/documents")
async def get_documents(
    user=Depends(get_current_user),
    authz=Depends(get_authz_provider),
    db=Depends(get_scoped_db)
):
    if not user:
        raise HTTPException(401, "Authentication required")

    if not await authz.check(user["email"], "documents", "read"):
        raise HTTPException(403, "Permission denied")

    return await db.documents.find({}).to_list(100)
```

### Auth Decorators

```python
from mdb_engine.auth.decorators import require_auth, rate_limit_auth

@app.get("/protected")
@require_auth()
async def protected_route(request: Request):
    user = request.state.user
    return {"user": user["email"]}

@app.post("/login")
@rate_limit_auth(endpoint="login")
async def login(email: str, password: str):
    # Rate limited: 5 attempts per 5 minutes
    pass
```

---

## Advanced Patterns

### Multi-App with Cross-App Data Access

```python
# App A can read from App B's data
db = engine.get_scoped_db(
    "app_a",
    read_scopes=["app_a", "app_b"],  # Can read from both
    write_scope="app_a"               # Writes only to app_a
)

# Reads from both apps:
all_tasks = await db.tasks.find({}).to_list(100)

# Writes only to app_a:
await db.tasks.insert_one({"title": "New task"})
```

### Custom Startup Logic

```python
async def on_startup(app, engine, manifest):
    """Called after manifest is loaded and app is registered."""
    # Initialize custom services
    memory = engine.get_memory_service(manifest["slug"])

    # Register WebSocket handlers
    engine.register_websocket_routes(app, manifest["slug"])

    # Custom initialization
    logger.info(f"App {manifest['slug']} initialized")

app = engine.create_app(
    slug="my_app",
    manifest=Path("manifest.json"),
    on_startup=on_startup
)
```

### WebSocket with Ticket Authentication

```javascript
// Client-side
// Step 1: Get ticket (requires JWT cookie from login)
const ticketRes = await fetch('/auth/ticket', {
    method: 'POST',
    credentials: 'include'
});
const { ticket } = await ticketRes.json();

// Step 2: Connect with ticket (must be within 10 seconds)
const ws = new WebSocket(`wss://api.example.com/my_app/ws?ticket=${ticket}`);
```

```python
# Server-side - register message handlers
from mdb_engine.routing.websockets import register_message_handler, broadcast_to_app

async def handle_message(ws, msg):
    # Process incoming message
    await broadcast_to_app("my_app", {"type": "update", "data": msg})

register_message_handler("my_app", "realtime", handle_message)
```

### Health Checks

```python
@app.get("/health")
async def health():
    status = await engine.get_health_status()
    return {
        "status": status.get("status", "unknown"),
        "mongodb": status.get("mongodb", "unknown"),
        "initialized": engine.initialized
    }
```

---

## Migration Guide

### Before (Without MDB-Engine)

```python
from motor.motor_asyncio import AsyncIOMotorClient
from fastapi import FastAPI

app = FastAPI()
client = AsyncIOMotorClient("mongodb://localhost:27017")
db = client.my_database

APP_ID = "my_app"

@app.on_event("startup")
async def startup():
    await db.tasks.create_index([("user_id", 1), ("created_at", -1)])

@app.get("/tasks")
async def get_tasks(user_id: str):
    # Manual app_id filtering - easy to forget!
    return await db.tasks.find({
        "app_id": APP_ID,
        "user_id": user_id
    }).to_list(10)

@app.post("/tasks")
async def create_task(task: dict):
    # Manual app_id addition - easy to forget!
    task["app_id"] = APP_ID
    result = await db.tasks.insert_one(task)
    return {"id": str(result.inserted_id)}
```

**Problems:**
- ❌ Manual `app_id` filtering (security risk if forgotten)
- ❌ Manual index creation
- ❌ No automatic scoping
- ❌ No auth integration

### After (With MDB-Engine)

```python
from pathlib import Path
from fastapi import Depends
from mdb_engine import MongoDBEngine
from mdb_engine.dependencies import get_scoped_db

engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database"
)

app = engine.create_app(
    slug="my_app",
    manifest=Path("manifest.json")
)

@app.get("/tasks")
async def get_tasks(user_id: str, db=Depends(get_scoped_db)):
    # Automatic app_id filtering!
    return await db.tasks.find({"user_id": user_id}).to_list(10)

@app.post("/tasks")
async def create_task(task: dict, db=Depends(get_scoped_db)):
    # Automatic app_id addition!
    result = await db.tasks.insert_one(task)
    return {"id": str(result.inserted_id)}
```

**manifest.json:**
```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My App",
  "managed_indexes": {
    "tasks": [
      {"type": "regular", "keys": {"user_id": 1, "created_at": -1}}
    ]
  }
}
```

**Benefits:**
- ✅ Automatic `app_id` scoping (impossible to leak data)
- ✅ Declarative index management
- ✅ Collection prefixing
- ✅ Lifecycle management
- ✅ Ready for auth, memory, and AI services

---

## Environment Variables

```bash
# MongoDB
MONGO_URI=mongodb://localhost:27017
MONGO_DB_NAME=my_database

# OpenAI
OPENAI_API_KEY=sk-...

# Azure OpenAI
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=https://....openai.azure.com
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o
AZURE_OPENAI_API_VERSION=2024-02-01

# Gemini
GEMINI_API_KEY=...

# Anthropic
ANTHROPIC_API_KEY=...

# Groq
GROQ_API_KEY=...

# Auth (for shared auth mode)
MDB_ENGINE_JWT_SECRET=your-secret-key

# Memory Service
MEMORY_LLM_TEMPERATURE=0
```

---

## Troubleshooting

### Common Issues

#### "Engine not initialized"

```python
# Make sure to use create_app() or call initialize()
app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))

# Or manually:
await engine.initialize()
```

#### Memory operations blocking event loop

```python
# WRONG
memories = memory.search(query=q, user_id=uid)

# CORRECT
memories = await asyncio.to_thread(memory.search, query=q, user_id=uid)
```

#### "Collection not found"

Collections are created on first write. Make sure you're using `get_scoped_db()`.

#### CORS issues

Check your `cors` configuration in manifest:

```json
{
  "cors": {
    "enabled": true,
    "allow_origins": ["http://localhost:3000"],
    "allow_credentials": true
  }
}
```

#### Shared auth not working

Ensure all apps use the same `MDB_ENGINE_JWT_SECRET` environment variable.

---

## Architecture Highlights

- **Protocol-Based**: Services implement Python Protocols for type safety and testability
- **Dependency Injection**: All services support DI for modular, testable architectures
- **Standalone Services**: Graph, LLM, Embedding, Redaction can be used independently
- **Multi-App Support**: SSO and cross-app data access with shared auth
- **GDPR Compliant**: Built-in data discovery, export, deletion, and rectification
- **WebSocket Support**: Real-time updates with ticket-based authentication
- **CSFLE Support**: Client-side field-level encryption for sensitive data
- **Version**: 0.7.6

## Links

- [GitHub Repository](https://github.com/ranfysvalle02/mdb-engine)
- [Examples](https://github.com/ranfysvalle02/mdb-engine/tree/main/examples)
- [Quick Start Guide](docs/QUICK_START.md)
- [Manifest Reference](docs/MANIFEST_REFERENCE.md)
- [Memory Service Documentation](docs/MEMORY_SERVICE.md)
- [Graph Service Documentation](docs/GRAPH_SERVICE.md)
- [Architecture Documentation](docs/ARCHITECTURE.md)
