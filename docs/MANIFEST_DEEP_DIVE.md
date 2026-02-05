# Deep Analysis: manifest.json and Getting Started with mdb-engine

## Executive Summary

mdb-engine is designed to work **with you** rather than requiring you to build entirely on it. The `manifest.json` file serves as an optional but powerful configuration layer that enables declarative setup of complex features. You can use as much or as little as you need.

## Part 1: The Nuances of manifest.json

### Minimal vs. Full Manifest

**Absolute Minimum (3 fields):**

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My App"
}
```

This minimal manifest provides:

- App identification and registration
- Basic data scoping (all queries automatically filtered by `app_id`)
- Collection name prefixing (`db.tasks` → `my_app_tasks`)

**What's Missing (and That's OK):**

- No indexes defined → Engine still works, you just won't have optimized queries
- No auth config → App works without authentication
- No WebSockets → App works without real-time features
- No embedding/memory config → App works without AI features

### Schema Versioning and Backward Compatibility

The manifest system supports multiple schema versions:

- **v1.0**: Legacy format (still supported)
- **v2.0**: Current format with all features

The engine automatically:

- Detects schema version from `schema_version` field (defaults to 2.0)
- Migrates older manifests to current schema when needed
- Validates against the appropriate schema version

### Field Categories: Required vs. Optional

#### Required Fields

- `slug`: Unique app identifier (lowercase alphanumeric, underscores, hyphens)
- `name`: Human-readable app name

#### Optional but Powerful Fields

**Data Isolation:**

- `data_access.read_scopes`: Cross-app data access (defaults to `[slug]`)
- `data_access.write_scope`: Where writes go (defaults to `slug`)

**Authentication & Authorization:**

- `auth.policy`: Authorization provider (Casbin/OSO) - completely optional
- `auth.users`: User management strategy - optional
- `auth.mode`: Per-app vs shared auth - defaults to "app"
- `auth.auth_hub_url`: URL of authentication hub for SSO apps (shared mode only) - optional
- `auth.related_apps`: Map of related app slugs to URLs for cross-app navigation (shared mode) - optional

**Index Management:**

- `managed_indexes`: Index definitions - optional, but recommended for performance

**AI/ML Services:**

- `embedding_config`: Text chunking and vector embeddings - optional
- `memory_config`: Memory service (MongoDB Atlas Vector Search) - optional
  - **Intelligent Memory Management**: LLM-powered fact extraction, semantic search, user isolation
  - **Cognitive Features**: Importance scoring, reinforcement, decay, merging, pruning (optional)
  - **Automatic Index Management**: Vector search index created and managed automatically
  - **Zero Configuration**: Just enable in manifest - no manual index setup needed

**Real-time:**

- `websockets`: WebSocket endpoint definitions - optional

**Infrastructure:**

- `cors`: CORS configuration - optional
- `observability`: Metrics and logging config - optional

### The "Graceful Degradation" Philosophy

When optional fields are omitted, the engine:

1. **Doesn't fail** - App still works
2. **Defaults to sensible values** - e.g., `read_scopes` defaults to `[slug]`
3. **Services return None** - e.g., `get_memory_service()` returns `None` if not configured
4. **Logs warnings** - Informs you what's not configured but doesn't break

Example from code:

```python
# From engine.py - WebSocket registration
websockets_config = self.get_websocket_config(slug)
if not websockets_config:
    logger.debug(f"No WebSocket configuration found for app '{slug}' - WebSocket support disabled")
    return  # Gracefully exits, doesn't raise error
```

## Part 2: The Convenience manifest.json Provides

### 1. Declarative Configuration Over Code

**Without manifest.json:**

```python
# Manual setup - scattered across multiple files
@app.on_event("startup")
async def startup():
    # Create indexes
    await db.tasks.create_index([("status", 1), ("created_at", -1)])
    await db.tasks.create_index("user_id")
    
    # Setup auth
    from mdb_engine.auth import setup_casbin
    await setup_casbin(db, model="rbac", policies=[...])
    
    # Setup CORS
    app.add_middleware(CORSMiddleware, ...)
    
    # Register WebSockets
    app.websocket("/ws")(websocket_handler)
```

**With manifest.json:**

```json
{
  "slug": "my_app",
  "name": "My App",
  "managed_indexes": {
    "tasks": [
      {"type": "regular", "keys": {"status": 1, "created_at": -1}},
      {"type": "regular", "keys": {"user_id": 1}}
    ]
  },
  "auth": {
    "policy": {"provider": "casbin", "authorization": {"model": "rbac"}}
  },
  "cors": {"enabled": true, "allow_origins": ["*"]},
  "websockets": {"realtime": {"path": "/ws"}}
}
```

**Code becomes:**

```python
app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))
# Everything configured automatically!
```

### 2. Automatic Index Creation and Management

The `managed_indexes` section enables:

- **Declarative index definitions** - No manual `create_index()` calls
- **Automatic creation on app registration** - Indexes created when app starts
- **Version control friendly** - Index definitions live in JSON, not scattered code
- **Multiple index types** - Regular, text, vector, TTL, geospatial

Example:

```json
{
  "managed_indexes": {
    "knowledge_base": [
      {
        "type": "vectorSearch",
        "name": "embedding_vector_index",
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
  }
}
```

This automatically creates a MongoDB Atlas Vector Search index - no manual Atlas UI configuration needed.

### 3. Zero-Boilerplate Authentication

The `auth` section enables automatic setup of:

- **Casbin RBAC** - Role-based access control with MongoDB backend
- **OSO Cloud** - Policy-based authorization
- **User management** - App-level or shared users
- **Demo users** - Auto-created for development

Example:

```json
{
  "auth": {
    "policy": {
      "provider": "casbin",
      "authorization": {
        "model": "rbac",
        "initial_policies": [
          ["admin", "documents", "read"],
          ["admin", "documents", "write"],
          ["editor", "documents", "read"]
        ],
        "initial_roles": [
          {"user": "alice@example.com", "role": "admin"}
        ]
      }
    },
    "users": {
      "enabled": true,
      "demo_users": [
        {"email": "alice@example.com", "password": "password123", "role": "admin"}
      ]
    }
  }
}
```

This automatically:

- Creates Casbin enforcer with policies
- Creates demo users with bcrypt-hashed passwords
- Sets up session cookie authentication
- Provides `get_current_user()` dependency

### 4. Service Auto-Configuration

**Embedding Service:**

```json
{
  "embedding_config": {
    "enabled": true,
    "max_tokens_per_chunk": 1000,
    "default_embedding_model": "text-embedding-3-small"
  }
}
```

Automatically configures `EmbeddingService` - available via `Depends(get_embedding_service)`.

**Memory Service:**

```json
{
  "memory_config": {
    "enabled": true,
    "provider": "cognitive",
    "collection_name": "user_memories",
    "embedding_model": "text-embedding-3-small",
    "chat_model": "gpt-4o",
    "embedding_model_dims": 1536,
    "infer": true,
    "async_mode": true,
    "enable_cognitive": true,
    "max_depth": 100
  }
}
```

Automatically configures memory service with:
- ✅ **Vector search index** created and managed automatically (no manual index definition needed)
- ✅ **LLM fact extraction** from conversations (when `infer: true`)
- ✅ **Semantic search** capabilities via MongoDB Atlas Vector Search
- ✅ **Cognitive features** (importance scoring, reinforcement, decay, merging, pruning) when enabled
- ✅ **User isolation** - all memories scoped per user automatically
- ✅ Available via `Depends(get_memory_service)` dependency

**Key Features:**
- **Automatic Index Management**: The memory service creates its own vector search index with `user_id` filter - you never need to define it in `managed_indexes`
- **Fact Extraction**: Automatically extracts atomic facts from conversations using LLM inference
- **Direct Injection**: Can inject memories directly without LLM inference (for structured data, preferences, facts)
- **Cognitive Memory**: Optional advanced features including importance scoring, memory reinforcement, decay, merging, and pruning
- **Metadata Support**: Full support for custom metadata, bucket_id, and bucket_type for organization

**Usage Pattern:**
```python
from mdb_engine.dependencies import get_memory_service

@app.post("/chat")
async def chat(message: str, user_id: str, memory=Depends(get_memory_service)):
    if memory:  # Gracefully handles if not configured
        # Search for relevant memories
        memories = memory.search(query=message, user_id=user_id, limit=5)
        context = "\n".join([m.get("memory", "") for m in memories])
        
        # Add new memory from conversation
        memory.add(messages=[...], user_id=user_id)
```

### 5. Multi-App Coordination

The `data_access` section enables secure cross-app data sharing:

```json
{
  "data_access": {
    "read_scopes": ["dashboard", "analytics", "shared_data"],
    "write_scope": "dashboard",
    "cross_app_policy": "explicit"
  }
}
```

This allows the dashboard app to read from multiple apps while maintaining isolation.

## Part 3: Getting Started - Incremental Adoption

### Level 1: Minimal Integration (Just Database Scoping)

**Use case:** You have an existing FastAPI app and just want automatic data isolation.

```python
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(mongo_uri="...", db_name="...")
await engine.initialize()

# Minimal manifest
manifest = {
    "schema_version": "2.0",
    "slug": "my_app",
    "name": "My App"
}
await engine.register_app(manifest)

# Use scoped database
db = engine.get_scoped_db("my_app")
await db.items.find({}).to_list(10)  # Automatically scoped!
```

**What you get:**

- Automatic `app_id` filtering on all queries
- Collection name prefixing (`my_app_items`)
- Data isolation between apps

**What you don't need:**

- Auth configuration
- Index definitions (you can create manually)
- Any other manifest fields

### Level 2: Add Index Management

**Use case:** You want declarative index management.

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My App",
  "managed_indexes": {
    "items": [
      {"type": "regular", "keys": {"status": 1, "created_at": -1}}
    ]
  }
}
```

**What you get:**

- Indexes automatically created on app registration
- Index definitions in version control
- No manual `create_index()` calls needed

### Level 3: Add Authentication

**Use case:** You need user authentication and authorization.

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My App",
  "auth": {
    "policy": {
      "provider": "casbin",
      "required": true,
      "authorization": {"model": "rbac"}
    },
    "users": {
      "enabled": true,
      "allow_registration": true
    }
  }
}
```

**What you get:**

- Automatic auth setup via `engine.create_app()`
- `get_current_user()` dependency
- Session management
- Role-based access control

### Level 4: Add AI Services

**Use case:** You're building a RAG or chat application.

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My App",
  "embedding_config": {"enabled": true},
  "memory_config": {"enabled": true}
}
```

**What you get:**

- `get_embedding_service()` dependency
- `get_memory_service()` dependency
- Auto-configured LLM client via `get_llm_client()`

### Level 5: Full Integration

**Use case:** You want everything configured declaratively.

Use `engine.create_app()` with a complete manifest - everything is auto-configured.

## Part 4: Working "With You" - Not "On Top Of You"

### Component-Level Usage

You can use individual components without the manifest system:

**Just Database Scoping:**

```python
from mdb_engine.database import ScopedMongoWrapper

# Create scoped wrapper directly
wrapper = ScopedMongoWrapper(
    mongo_db=raw_db,
    app_slug="my_app",
    read_scopes=["my_app"],
    write_scope="my_app"
)
```

**Just Manifest Validation:**

```python
from mdb_engine.core import ManifestValidator

validator = ManifestValidator()
is_valid, error, paths = validator.validate(manifest_dict)
```

**Just Index Management:**

```python
from mdb_engine.indexes import AsyncAtlasIndexManager

index_manager = AsyncAtlasIndexManager(mongo_client)
await index_manager.create_app_indexes("my_app", manifest)
```

**Just Auth Setup:**

```python
from mdb_engine.auth import setup_auth_from_manifest

await setup_auth_from_manifest(app, engine, "my_app")
```

### No Lock-In

**Export Your Data:**

```bash
# Export app data using standard MongoDB tools
mongodump --query='{"app_id": "my_app"}' --db=my_database
```

**Standard Motor API:**

The scoped database uses standard Motor/PyMongo APIs - no custom query language.

**No Framework Dependency:**

While optimized for FastAPI, you can use the engine with any async framework or even scripts.

### Flexible Integration Patterns

**Pattern 1: Full Integration (Recommended for New Apps)**

```python
app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))
```

**Pattern 2: Lifecycle Only**

```python
app = FastAPI(lifespan=engine.lifespan("my_app", Path("manifest.json")))
```

**Pattern 3: Manual Control**

```python
@app.on_event("startup")
async def startup():
    await engine.initialize()
    manifest = await engine.load_manifest(Path("manifest.json"))
    await engine.register_app(manifest)
```

**Pattern 4: Script/CLI Usage**

```python
async with MongoDBEngine(...) as engine:
    db = engine.get_scoped_db("my_app")
    # Use database
```

### Optional Features Pattern

All advanced features are optional and gracefully degrade:

**WebSockets:**

- If not in manifest → No WebSocket routes registered
- If dependencies missing → Logs warning, continues

**Memory Service:**

- If not configured → `get_memory_service()` returns `None`
- Your code checks: `if memory: memory.search(...)`
- **Automatic Index Management**: Even when enabled, you don't need to define memory indexes in `managed_indexes` - the service manages its own vector search index automatically
- **Graceful Degradation**: Memory operations are optional - your app works fine without them

**Embedding Service:**

- If not configured → `get_embedding_service()` returns `None`
- Your code checks: `if embedding_service: ...`

**Authorization:**

- If not configured → `get_authz_provider()` returns `None`
- Your code can implement custom auth or skip checks

## Part 5: Real-World Examples

### Example 1: Simple CRUD API (Minimal Manifest)

```json
{
  "schema_version": "2.0",
  "slug": "tasks",
  "name": "Task Manager",
  "managed_indexes": {
    "tasks": [
      {"type": "regular", "keys": {"user_id": 1, "created_at": -1}}
    ]
  }
}
```

```python
from mdb_engine import MongoDBEngine
from mdb_engine.dependencies import get_scoped_db

engine = MongoDBEngine(...)
app = engine.create_app(slug="tasks", manifest=Path("manifest.json"))

@app.get("/tasks")
async def list_tasks(db=Depends(get_scoped_db)):
    return await db.tasks.find({}).to_list(100)
```

**What's used:** Database scoping, index management

**What's not used:** Auth, WebSockets, AI services

### Example 2: RAG Application (Selective Features)

```json
{
  "schema_version": "2.0",
  "slug": "rag_app",
  "name": "RAG System",
  "managed_indexes": {
    "documents": [
      {"type": "vectorSearch", "name": "vector_idx", ...}
    ]
  },
  "embedding_config": {"enabled": true}
}
```

**What's used:** Database scoping, vector indexes, embedding service

**What's not used:** Auth (public), memory service, WebSockets

### Example 3: Multi-Tenant SaaS (Full Features)

```json
{
  "schema_version": "2.0",
  "slug": "saas_app",
  "name": "SaaS Platform",
  "auth": {"policy": {...}, "users": {...}},
  "managed_indexes": {...},
  "websockets": {...},
  "data_access": {"read_scopes": ["shared", "saas_app"]}
}
```

**What's used:** Everything - auth, indexes, WebSockets, cross-app access

## Key Takeaways

1. **manifest.json is optional** - You can use mdb-engine with minimal or no manifest
2. **Graceful degradation** - Missing features don't break the app
3. **Incremental adoption** - Add features as you need them
4. **Component-level usage** - Use individual components without the full system
5. **No lock-in** - Standard APIs, exportable data, framework-agnostic
6. **Declarative convenience** - When you use manifest.json, it eliminates boilerplate

The engine works **with you** by:

- Providing sensible defaults
- Making features optional
- Using standard APIs
- Allowing component-level usage
- Supporting multiple integration patterns
- Not requiring you to rebuild on top of it

You can start minimal and grow into more features, or use just the parts that make sense for your use case.
