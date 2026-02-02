# MDB_ENGINE Quick Start Guide

## 🎯 The Key to Everything: manifest.json

**`manifest.json` is the heart of your application.** It's a single configuration file that defines your app's identity, data structure, authentication, indexes, and services. Think of it as your app's DNA—everything flows from this file.

### Why manifest.json Matters

- **Single Source of Truth**: All configuration in one place
- **Zero Boilerplate**: No scattered setup code across multiple files
- **Automatic Setup**: Indexes, auth, services configured automatically
- **Version Control Friendly**: Your entire app config is versioned
- **Incremental Adoption**: Start minimal, add features as needed

---

## Installation

```bash
pip install mdb-engine
```

Or install from source:

```bash
pip install -e .
```

---

## Your First manifest.json

Every app starts with a `manifest.json`. Here's the absolute minimum:

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My App"
}
```

That's it! Just 3 fields. This minimal manifest gives you:
- ✅ App identification and registration
- ✅ Automatic data scoping (all queries filtered by `app_id`)
- ✅ Collection name prefixing (`db.tasks` → `my_app_tasks`)

### Create Your manifest.json

1. Create a `manifest.json` file in your project root
2. Add the three required fields above
3. You're ready to go!

---

## Quick Start: From manifest.json to Running App

### Step 1: Create Your manifest.json

```json
{
  "schema_version": "2.0",
  "slug": "task_manager",
  "name": "Task Manager",
  "status": "active",
  "managed_indexes": {
    "tasks": [
      {
        "type": "regular",
        "keys": {"status": 1, "created_at": -1},
        "name": "status_sort"
      }
    ]
  }
}
```

### Step 2: Create Your FastAPI App

```python
from pathlib import Path
from fastapi import Depends
from mdb_engine import MongoDBEngine
from mdb_engine.dependencies import get_scoped_db

# Initialize engine
engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database"
)

# Create app - manifest.json is loaded automatically!
app = engine.create_app(
    slug="task_manager",
    manifest=Path("manifest.json")
)

# Use request-scoped database - automatically isolated!
@app.post("/tasks")
async def create_task(task: dict, db=Depends(get_scoped_db)):
    result = await db.tasks.insert_one(task)
    return {"id": str(result.inserted_id)}

@app.get("/tasks")
async def list_tasks(db=Depends(get_scoped_db)):
    return await db.tasks.find({"status": "pending"}).to_list(length=10)
```

**What just happened?**
- ✅ Engine loaded your `manifest.json`
- ✅ App registered with slug `task_manager`
- ✅ Indexes created automatically from `managed_indexes`
- ✅ Database queries automatically scoped to your app
- ✅ Lifecycle management handled (startup/shutdown)

---

## Understanding manifest.json Structure

### Required Fields

Every manifest must have these three fields:

```json
{
  "schema_version": "2.0",    // Schema version (always "2.0" for new apps)
  "slug": "my_app",           // Unique app identifier (lowercase, alphanumeric, underscores, hyphens)
  "name": "My App"            // Human-readable app name
}
```

### Core Sections

Your manifest can include these powerful sections:

#### 1. Data Isolation (`data_access`)

Control how your app accesses data:

```json
{
  "data_access": {
    "read_scopes": ["my_app", "shared_data"],  // Collections you can read from
    "write_scope": "my_app"                     // Where writes go
  }
}
```

**Default**: If omitted, `read_scopes` defaults to `[slug]` and `write_scope` defaults to `slug`.

#### 2. Index Management (`managed_indexes`)

Define indexes declaratively—they're created automatically:

```json
{
  "managed_indexes": {
    "tasks": [
      {
        "type": "regular",
        "keys": {"status": 1, "created_at": -1},
        "name": "status_sort"
      },
      {
        "type": "regular",
        "keys": {"user_id": 1},
        "name": "user_idx",
        "unique": true
      }
    ],
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

**Supported index types**: `regular`, `text`, `vectorSearch`, `ttl`, `compound`

#### 3. Authentication & Authorization (`auth`)

Configure authentication and authorization declaratively:

```json
{
  "auth": {
    "mode": "app",  // "app" (per-app auth) or "shared" (SSO across apps)
    "policy": {
      "provider": "casbin",  // "casbin" or "oso"
      "required": true,
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
      "strategy": "app_users",
      "allow_registration": true,
      "demo_users": [
        {
          "email": "alice@example.com",
          "password": "password123",
          "role": "admin"
        }
      ]
    }
  }
}
```

**What this gives you automatically:**
- ✅ Casbin enforcer with policies
- ✅ Demo users with bcrypt-hashed passwords
- ✅ Session cookie authentication
- ✅ `get_current_user()` dependency ready to use

#### 4. AI Services

**Embedding Service** (`embedding_config`):

```json
{
  "embedding_config": {
    "enabled": true,
    "max_tokens_per_chunk": 1000,
    "default_embedding_model": "text-embedding-3-small"
  }
}
```

**Memory Service** (`memory_config`):

```json
{
  "memory_config": {
    "enabled": true,
    "collection_name": "user_memories",
    "enable_graph": true
  }
}
```

Both services become available via dependencies:
- `get_embedding_service()` - Text chunking and embeddings
- `get_memory_service()` - Persistent AI memory (Mem0) with inject, delete, search, and update capabilities

#### 5. WebSockets (`websockets`)

Define real-time endpoints:

```json
{
  "websockets": {
    "realtime": {
      "path": "/ws",
      "description": "Real-time updates",
      "auth": {
        "required": false,
        "allow_anonymous": true
      }
    }
  }
}
```

#### 6. CORS (`cors`)

Configure CORS settings:

```json
{
  "cors": {
    "enabled": true,
    "allow_origins": ["*"],
    "allow_credentials": true,
    "allow_methods": ["*"],
    "allow_headers": ["*"]
  }
}
```

---

## Complete manifest.json Example

Here's a complete manifest.json that demonstrates all major features:

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My Application",
  "description": "A complete example app",
  "status": "active",
  
  "data_access": {
    "read_scopes": ["my_app", "shared_data"],
    "write_scope": "my_app"
  },
  
  "managed_indexes": {
    "tasks": [
      {
        "type": "regular",
        "keys": {"status": 1, "created_at": -1},
        "name": "status_sort"
      }
    ],
    "users": [
      {
        "type": "regular",
        "keys": {"email": 1},
        "name": "email_unique",
        "unique": true
      }
    ]
  },
  
  "auth": {
    "mode": "app",
    "policy": {
      "provider": "casbin",
      "required": true,
      "authorization": {
        "model": "rbac",
        "initial_policies": [
          ["admin", "tasks", "read"],
          ["admin", "tasks", "write"],
          ["user", "tasks", "read"]
        ],
        "initial_roles": [
          {"user": "admin@example.com", "role": "admin"}
        ]
      }
    },
    "users": {
      "enabled": true,
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
  
  "embedding_config": {
    "enabled": true,
    "max_tokens_per_chunk": 1000
  },
  
  "memory_config": {
    "enabled": true,
    "collection_name": "user_memories"
  },
  
  "websockets": {
    "realtime": {
      "path": "/ws",
      "description": "Real-time updates"
    }
  },
  
  "cors": {
    "enabled": true,
    "allow_origins": ["*"]
  }
}
```

---

## Using Your manifest.json in Code

### Pattern 1: create_app() (Recommended)

The simplest way—everything is automatic:

```python
from pathlib import Path
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(mongo_uri="...", db_name="...")
app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))

# Everything configured from manifest.json:
# - Indexes created
# - Auth setup
# - Services initialized
# - Dependencies available
```

### Pattern 2: Lifespan Only

For more control over FastAPI app creation:

```python
from fastapi import FastAPI
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(...)
app = FastAPI(
    title="My App",
    lifespan=engine.lifespan("my_app", Path("manifest.json"))
)
```

### Pattern 3: Manual Control

Full control over initialization:

```python
@app.on_event("startup")
async def startup():
    await engine.initialize()
    manifest = await engine.load_manifest(Path("manifest.json"))
    await engine.register_app(manifest, create_indexes=True)
```

---

## Request-Scoped Dependencies

Once your manifest.json is loaded, these dependencies become available:

| Dependency | Description | Requires in manifest.json |
|------------|-------------|---------------------------|
| `get_scoped_db` | Scoped database for current app | (always available) |
| `get_current_user` | Authenticated user | `auth.users.enabled: true` |
| `get_authz_provider` | Authorization provider | `auth.policy.provider` |
| `get_embedding_service` | Embedding service | `embedding_config.enabled: true` |
| `get_memory_service` | Memory service | `memory_config.enabled: true` |
| `get_llm_client` | LLM client (OpenAI/Azure) | (auto-detected from env) |
| `AppContext` | All-in-one context | (combines all above) |

### Example: Using Dependencies

```python
from fastapi import Depends
from mdb_engine.dependencies import (
    get_scoped_db,
    get_current_user,
    get_authz_provider,
    get_embedding_service
)

@app.get("/tasks")
async def get_tasks(
    db=Depends(get_scoped_db),
    user=Depends(get_current_user),
    authz=Depends(get_authz_provider)
):
    # Check permission
    if not await authz.check(user["email"], "tasks", "read"):
        raise HTTPException(403, "Permission denied")
    
    # Query automatically scoped to app
    return await db.tasks.find({}).to_list(100)

@app.post("/embed")
async def embed_text(
    text: str,
    db=Depends(get_scoped_db),
    embedding_service=Depends(get_embedding_service)
):
    # Embedding service configured from manifest.json
    result = await embedding_service.process_and_store(
        text_content=text,
        source_id="doc_1",
        collection=db.knowledge_base
    )
    return {"chunks_created": result["chunks_created"]}
```

### AppContext - All Services in One

For routes that need multiple services:

```python
from mdb_engine.dependencies import AppContext

@app.post("/ai-chat")
async def chat(query: str, ctx: AppContext = Depends()):
    user = ctx.require_user()  # Raises 401 if not authenticated
    ctx.require_role("user")   # Raises 403 if missing role
    
    # Everything available: ctx.db, ctx.embedding_service, ctx.memory, ctx.llm
    if ctx.llm:
        response = ctx.llm.chat.completions.create(
            model=ctx.llm_model,
            messages=[{"role": "user", "content": query]}
        )
        return {"response": response.choices[0].message.content}
```

---

## Incremental Adoption

You don't need everything at once! Start minimal and add features:

### Level 1: Minimal (Just Data Scoping)

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My App"
}
```

**What you get**: Data isolation, collection prefixing

### Level 2: Add Indexes

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My App",
  "managed_indexes": {
    "tasks": [
      {"type": "regular", "keys": {"status": 1}}
    ]
  }
}
```

**What you get**: Automatic index creation

### Level 3: Add Authentication

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My App",
  "auth": {
    "policy": {"provider": "casbin"},
    "users": {"enabled": true}
  }
}
```

**What you get**: Auth setup, user management, session handling

### Level 4: Add AI Services

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My App",
  "embedding_config": {"enabled": true},
  "memory_config": {"enabled": true}
}
```

**What you get**: Embedding service, memory service

---

## manifest.json Best Practices

1. **Start Minimal**: Begin with just `slug`, `name`, and `schema_version`
2. **Version Control**: Keep your manifest.json in git—it's your app's configuration
3. **Validate Early**: Use `ManifestValidator` to check your manifest before deployment
4. **Use Examples**: Check `examples/` directory for real-world manifest.json files
5. **Document Your Choices**: Use `description` field to explain why you configured things a certain way

---

## Validation and Error Handling

Validate your manifest.json before using it:

```python
from mdb_engine.core import ManifestValidator

validator = ManifestValidator()
is_valid, error, paths = validator.validate(manifest_dict)

if not is_valid:
    print(f"Validation error: {error}")
    print(f"Paths: {paths}")
```

---

## Next Steps

- **Reference Guide**: Check [MANIFEST_REFERENCE.md](MANIFEST_REFERENCE.md) for complete field documentation
- **Deep Dive**: Read [MANIFEST_DEEP_DIVE.md](MANIFEST_DEEP_DIVE.md) for comprehensive manifest.json analysis
- **Examples**: Explore `examples/` directory for complete manifest.json files
- **Architecture**: Understand how it all works in [ARCHITECTURE.md](ARCHITECTURE.md)
- **Best Practices**: Learn patterns in [BEST_PRACTICES.md](BEST_PRACTICES.md)

---

## Key Takeaways

1. **manifest.json is the foundation** - Everything starts here
2. **Start minimal** - Add features as you need them
3. **Automatic setup** - Indexes, auth, services configured from manifest
4. **Zero boilerplate** - No scattered setup code
5. **Version controlled** - Your entire app config in one file

**Remember**: Your `manifest.json` defines your app. The engine reads it and sets everything up automatically. Start simple, grow as needed!
