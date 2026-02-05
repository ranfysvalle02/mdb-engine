# Migrating Python Code to MDB-Engine: Complete Guide

**Transform any Python application into a production-ready, secure, multi-tenant system with automatic data isolation, index management, and built-in authorization.**

---

## Table of Contents

1. [What is MDB-Engine?](#what-is-mdb-engine)
2. [The "MDB Magic" Explained](#the-mdb-magic-explained)
3. [Why Migrate?](#why-migrate)
4. [Migration Process Overview](#migration-process-overview)
5. [Step-by-Step Migration Guide](#step-by-step-migration-guide)
6. [Manifest.json Deep Dive](#manifestjson-deep-dive)
7. [Scoped Indexes and Data Access](#scoped-indexes-and-data-access)
8. [Authorization (AuthZ) Out of the Box](#authorization-authz-out-of-the-box)
9. [Before & After Examples](#before--after-examples)
10. [Common Migration Patterns](#common-migration-patterns)
11. [Troubleshooting](#troubleshooting)
12. [Appendix: Shared Authentication (SSO)](#appendix-shared-authentication-sso)

---

## What is MDB-Engine?

**MDB-Engine** is a "WordPress-like" platform for Python/MongoDB applications that provides:

- ✅ **Automatic Data Sandboxing** - All queries automatically scoped to your app
- ✅ **Manifest-Driven Configuration** - Define your app's "DNA" in `manifest.json`
- ✅ **Zero-Boilerplate Auth** - Built-in authentication and authorization (Casbin/OSO)
- ✅ **Automatic Index Management** - Indexes created from manifest definitions + auto-indexing
- ✅ **Built-in Services** - Embeddings, memory, WebSockets, observability
- ✅ **Multi-Tenant Ready** - Secure data isolation out of the box

**Key Philosophy:** *You write clean, naive code. The engine handles the complexity.*

```python
# YOU WRITE THIS (Clean, Naive Code):
await db.tasks.find({}).to_list(length=10)

# THE ENGINE EXECUTES THIS (Secure, Scoped Query):
# Collection: my_app_tasks
# Query: {"app_id": "my_app"}
# Indexes: Automatically created and optimized
# AuthZ: Automatically checked if configured
```

---

## The "MDB Magic" Explained

### 1. **Automatic Data Scoping**

**Without MDB-Engine:**
```python
# Manual scoping - easy to forget, security risk
tasks = await db.tasks.find({"app_id": "my_app", "user_id": user_id}).to_list(10)

# Oops! Forgot app_id - data leak!
tasks = await db.tasks.find({"user_id": user_id}).to_list(10)
```

**With MDB-Engine:**
```python
# Automatic scoping - impossible to leak data
db = engine.get_scoped_db("my_app")
tasks = await db.tasks.find({"user_id": user_id}).to_list(10)
# ✅ Automatically filtered by app_id
# ✅ Collection automatically prefixed: my_app_tasks
```

### 2. **Manifest-Driven Configuration**

**Without MDB-Engine:**
```python
# Scattered configuration across multiple files
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
```

**With MDB-Engine:**
```json
// manifest.json - Everything in one place
{
  "slug": "my_app",
  "managed_indexes": {
    "tasks": [
      {"type": "regular", "keys": {"status": 1, "created_at": -1}},
      {"type": "regular", "keys": {"user_id": 1}}
    ]
  },
  "auth": {
    "policy": {"provider": "casbin", "authorization": {"model": "rbac"}}
  },
  "cors": {"enabled": true}
}
```

```python
# Code becomes:
app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))
# Everything configured automatically!
```

### 3. **Automatic Index Management**

**Two Levels of Magic:**

#### Level 1: Declarative Indexes (Manifest-Driven)
```json
{
  "managed_indexes": {
    "tasks": [
      {"type": "regular", "keys": {"status": 1, "created_at": -1}},
      {"type": "vectorSearch", "name": "vector_idx", "definition": {...}}
    ]
  }
}
```
Indexes are automatically created on app startup.

#### Level 2: Automatic Index Creation (Query-Driven)
```python
# You write this query:
tasks = await db.tasks.find({"status": "active"}).sort("priority", -1).to_list(10)

# The engine automatically:
# 1. Analyzes filter: {"status": "active"} → needs index on "status"
# 2. Analyzes sort: sort("priority", -1) → needs index on "priority"
# 3. Creates composite index: {"status": 1, "priority": -1}
# 4. All in the background, non-blocking
```

### 4. **Authorization Out of the Box**

**Without MDB-Engine:**
```python
# Manual auth setup - lots of boilerplate
from casbin import AsyncEnforcer
enforcer = await AsyncEnforcer(...)

@app.get("/documents")
async def get_documents(user: dict = Depends(get_current_user)):
    # Manual permission check
    if not await enforcer.enforce(user["email"], "documents", "read"):
        raise HTTPException(403, "Access denied")
    return await db.documents.find({}).to_list(10)
```

**With MDB-Engine:**
```json
// manifest.json
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
        ]
      }
    }
  }
}
```

```python
# Code - authZ automatically configured!
from mdb_engine.dependencies import get_authz_provider

@app.get("/documents")
async def get_documents(
    user: dict = Depends(get_current_user),
    authz = Depends(get_authz_provider)
):
    if not await authz.check(user["email"], "documents", "read"):
        raise HTTPException(403, "Access denied")
    return await db.documents.find({}).to_list(10)
```

---

## Why Migrate?

### Benefits

| Feature | Without MDB-Engine | With MDB-Engine |
|---------|-------------------|-----------------|
| **Data Isolation** | Manual `app_id` filtering (error-prone) | Automatic scoping (impossible to leak) |
| **Index Management** | Manual `create_index()` calls | Declarative + automatic |
| **Authentication** | Custom implementation (100+ lines) | Configured in manifest.json |
| **Authorization** | Manual Casbin/OSO setup | Auto-configured from manifest |
| **Multi-Tenancy** | Complex manual implementation | Built-in with scoped databases |
| **Collection Naming** | Manual prefixing | Automatic prefixing |
| **Query Security** | Manual validation | Built-in query validation |
| **Resource Limits** | Manual implementation | Automatic resource limiting |

### Real-World Impact

**Before Migration:**
- 200+ lines of boilerplate code
- Manual index management scripts
- Custom auth implementation
- Security vulnerabilities from forgotten `app_id` filters
- Inconsistent collection naming

**After Migration:**
- ~20 lines of code
- Zero boilerplate
- Secure by default
- Consistent, maintainable configuration

---

## Migration Process Overview

### High-Level Steps

1. **Install MDB-Engine**
   ```bash
   pip install mdb-engine
   ```

2. **Create `manifest.json`**
   - Define app metadata
   - Configure indexes
   - Configure auth/authZ
   - Configure services (optional)

3. **Replace Database Access**
   - Replace `AsyncIOMotorClient` with `MongoDBEngine`
   - Replace raw database access with `engine.get_scoped_db()`

4. **Update Application Code**
   - Remove manual `app_id` filtering
   - Remove manual index creation
   - Remove custom auth setup
   - Use FastAPI dependencies for auth/authZ

5. **Test & Deploy**
   - Test data isolation
   - Verify indexes are created
   - Test authorization

---

## Step-by-Step Migration Guide

### Step 1: Install MDB-Engine

```bash
# Basic installation
pip install mdb-engine

# With optional features
pip install mdb-engine[casbin]  # For Casbin authorization
pip install mdb-engine[oso]    # For OSO authorization
pip install mdb-engine[all]    # All optional features
```

### Step 2: Create `manifest.json`

Create a `manifest.json` file in your project root:

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My Application",
  "status": "active",
  "description": "Description of your app",
  
  "auth": {
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
          ["editor", "documents", "read"],
          ["editor", "documents", "write"],
          ["viewer", "documents", "read"]
        ],
        "initial_roles": [
          {"user": "admin@example.com", "role": "admin"},
          {"user": "editor@example.com", "role": "editor"}
        ]
      }
    },
    "users": {
      "enabled": true,
      "strategy": "app_users",
      "collection_name": "users",
      "session_cookie_name": "my_app_session",
      "session_ttl_seconds": 86400,
      "allow_registration": true,
      "demo_users": [
        {"email": "admin@example.com", "password": "password123", "role": "admin"},
        {"email": "editor@example.com", "password": "password123", "role": "editor"}
      ]
    }
  },
  
  "managed_indexes": {
    "tasks": [
      {
        "type": "regular",
        "keys": {"user_id": 1, "created_at": -1},
        "name": "user_tasks_idx"
      },
      {
        "type": "regular",
        "keys": {"status": 1},
        "name": "status_idx"
      }
    ],
    "users": [
      {
        "type": "regular",
        "keys": {"email": 1},
        "name": "email_idx",
        "options": {
          "unique": true
        }
      }
    ]
  },
  
  "cors": {
    "enabled": true,
    "allow_origins": ["*"],
    "allow_credentials": true
  }
}
```

### Step 3: Replace Database Initialization

**Before:**
```python
from motor.motor_asyncio import AsyncIOMotorClient
from fastapi import FastAPI

app = FastAPI()
client = AsyncIOMotorClient("mongodb://localhost:27017")
db = client.my_database

@app.on_event("startup")
async def startup():
    # Manual index creation
    await db.tasks.create_index([("user_id", 1), ("created_at", -1)])
    await db.tasks.create_index("status")
    await db.users.create_index("email", unique=True)
```

**After:**
```python
from pathlib import Path
from mdb_engine import MongoDBEngine
from fastapi import FastAPI

# Initialize engine
engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database"
)

# Create app with automatic lifecycle management
app = engine.create_app(
    slug="my_app",
    manifest=Path("manifest.json")
)

# Indexes are automatically created from manifest.json!
# Auth is automatically configured from manifest.json!
```

### Step 4: Replace Database Access

**Before:**
```python
@app.get("/tasks")
async def get_tasks(user_id: str):
    # Manual app_id filtering - easy to forget!
    tasks = await db.tasks.find({
        "app_id": "my_app",  # Must remember this!
        "user_id": user_id
    }).to_list(10)
    return tasks
```

**After:**
```python
from mdb_engine.dependencies import get_scoped_db

@app.get("/tasks")
async def get_tasks(user_id: str, db=Depends(get_scoped_db)):
    # Automatic app_id filtering - impossible to forget!
    tasks = await db.tasks.find({
        "user_id": user_id
        # app_id automatically added!
    }).to_list(10)
    return tasks
```

### Step 5: Add Authorization

**Before:**
```python
# Manual auth setup
from casbin import AsyncEnforcer

enforcer = None

@app.on_event("startup")
async def startup():
    global enforcer
    enforcer = await AsyncEnforcer("rbac_model.conf", "rbac_policy.csv")

@app.get("/documents")
async def get_documents(user_email: str):
    # Manual permission check
    if not await enforcer.enforce(user_email, "documents", "read"):
        raise HTTPException(403, "Access denied")
    return await db.documents.find({}).to_list(10)
```

**After:**
```python
from mdb_engine.dependencies import get_current_user, get_authz_provider

@app.get("/documents")
async def get_documents(
    user: dict = Depends(get_current_user),
    authz = Depends(get_authz_provider)
):
    # Automatic permission check
    if not await authz.check(user["email"], "documents", "read"):
        raise HTTPException(403, "Access denied")
    
    db = engine.get_scoped_db("my_app")
    return await db.documents.find({}).to_list(10)
```

---

## Manifest.json Deep Dive

### Basic Structure

```json
{
  "schema_version": "2.0",        // Schema version (required)
  "slug": "my_app",                // Unique app identifier (required)
  "name": "My Application",        // Display name (required)
  "status": "active",              // active | inactive | archived
  "description": "...",             // Optional description
  
  // Authentication & Authorization
  "auth": { ... },
  
  // Index definitions
  "managed_indexes": { ... },
  
  // WebSocket endpoints
  "websockets": { ... },
  
  // Memory service (MongoDB Atlas Vector Search)
  "memory_config": { ... },
  
  // Embedding service
  "embedding_config": { ... },
  
  // CORS settings
  "cors": { ... },
  
  // Data access scopes (multi-app)
  "data_access": {
    "read_scopes": ["my_app", "shared"],
    "write_scope": "my_app"
  }
}
```

### Minimal Manifest

The absolute minimum manifest (3 fields):

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My App"
}
```

This provides:
- ✅ App identification and registration
- ✅ Basic data scoping (all queries automatically filtered by `app_id`)
- ✅ Collection name prefixing (`db.tasks` → `my_app_tasks`)

### Complete Manifest Example

```json
{
  "schema_version": "2.0",
  "slug": "task_manager",
  "name": "Task Manager Application",
  "status": "active",
  "description": "A task management application with RBAC",
  
  "auth": {
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
          ["admin", "tasks", "read"],
          ["admin", "tasks", "write"],
          ["admin", "tasks", "delete"],
          ["user", "tasks", "read"],
          ["user", "tasks", "write"]
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
      "allow_registration": true,
      "demo_users": [
        {"email": "admin@example.com", "password": "password123", "role": "admin"}
      ]
    }
  },
  
  "managed_indexes": {
    "tasks": [
      {
        "type": "regular",
        "keys": {"user_id": 1, "created_at": -1},
        "name": "user_tasks_idx"
      },
      {
        "type": "regular",
        "keys": {"status": 1, "priority": -1},
        "name": "status_priority_idx"
      }
    ],
    "users": [
      {
        "type": "regular",
        "keys": {"email": 1},
        "name": "email_unique_idx",
        "options": {
          "unique": true
        }
      }
    ]
  },
  
  "cors": {
    "enabled": true,
    "allow_origins": ["*"],
    "allow_credentials": true
  }
}
```

---

## Scoped Indexes and Data Access

### Understanding Scoped Databases

When you call `engine.get_scoped_db("my_app")`, you get a **scoped database wrapper** that:

1. **Automatically prefixes collection names**: `db.tasks` → `my_app_tasks`
2. **Automatically filters queries**: Adds `{"app_id": "my_app"}` to all queries
3. **Automatically tags writes**: Adds `app_id: "my_app"` to all inserted documents
4. **Manages indexes**: Indexes are created on the prefixed collection names

### Collection Naming

**Without Scoping:**
```python
# Raw database access
db = client.my_database
await db.tasks.insert_one({"title": "Task 1"})
# Collection: tasks
# Document: {"title": "Task 1"}
```

**With Scoping:**
```python
# Scoped database access
db = engine.get_scoped_db("my_app")
await db.tasks.insert_one({"title": "Task 1"})
# Collection: my_app_tasks (automatically prefixed)
# Document: {"title": "Task 1", "app_id": "my_app"} (automatically tagged)
```

### Index Management

#### Declarative Indexes (Manifest)

Indexes defined in `manifest.json` are automatically created on app registration:

```json
{
  "managed_indexes": {
    "tasks": [
      {
        "type": "regular",
        "keys": {"user_id": 1, "created_at": -1},
        "name": "user_tasks_idx"
      }
    ]
  }
}
```

**What happens:**
- Index is created on collection `my_app_tasks` (prefixed)
- Index name: `user_tasks_idx`
- Automatically includes `app_id` in the index (for scoping)

#### Automatic Index Creation

The engine also automatically creates indexes based on query patterns:

```python
# You write this query:
tasks = await db.tasks.find({"status": "active"}).sort("priority", -1).to_list(10)

# The engine automatically creates:
# Index: {"status": 1, "priority": -1, "app_id": 1}
```

**How it works:**
1. Engine analyzes query filters and sort fields
2. Tracks query frequency
3. Creates indexes automatically for frequently used patterns
4. All in the background, non-blocking

### Cross-App Data Access

For multi-app scenarios, you can configure read scopes:

```json
{
  "data_access": {
    "read_scopes": ["my_app", "shared_data"],
    "write_scope": "my_app"
  }
}
```

```python
# Can read from multiple apps
db = engine.get_scoped_db(
    "my_app",
    read_scopes=["my_app", "shared_data"],
    write_scope="my_app"
)

# Reads from both my_app and shared_data
tasks = await db.tasks.find({}).to_list(10)
# Query: {"app_id": {"$in": ["my_app", "shared_data"]}}

# Writes only to my_app
await db.tasks.insert_one({"title": "Task"})
# Document: {"title": "Task", "app_id": "my_app"}
```

---

## Authorization (AuthZ) Out of the Box

### Supported Providers

MDB-Engine supports two authorization providers out of the box:

1. **Casbin** - Role-Based Access Control (RBAC)
2. **OSO Cloud** - Policy-based authorization

### Casbin RBAC Setup

#### Manifest Configuration

```json
{
  "auth": {
    "policy": {
      "provider": "casbin",
      "required": true,
      "authorization": {
        "model": "rbac",
        "policies_collection": "casbin_policies",
        "link_users_roles": true,
        "default_roles": ["user"],
        "initial_policies": [
          ["admin", "documents", "read"],
          ["admin", "documents", "write"],
          ["admin", "documents", "delete"],
          ["editor", "documents", "read"],
          ["editor", "documents", "write"],
          ["viewer", "documents", "read"]
        ],
        "initial_roles": [
          {"user": "alice@example.com", "role": "admin"},
          {"user": "bob@example.com", "role": "editor"}
        ]
      }
    }
  }
}
```

#### Usage in Code

```python
from mdb_engine.dependencies import get_current_user, get_authz_provider
from fastapi import Depends, HTTPException

@app.get("/documents")
async def get_documents(
    user: dict = Depends(get_current_user),
    authz = Depends(get_authz_provider)
):
    # Check permission
    if not await authz.check(user["email"], "documents", "read"):
        raise HTTPException(403, "Access denied")
    
    db = engine.get_scoped_db("my_app")
    return await db.documents.find({}).to_list(10)

@app.post("/documents")
async def create_document(
    document: dict,
    user: dict = Depends(get_current_user),
    authz = Depends(get_authz_provider)
):
    # Check permission
    if not await authz.check(user["email"], "documents", "write"):
        raise HTTPException(403, "Access denied")
    
    db = engine.get_scoped_db("my_app")
    result = await db.documents.insert_one(document)
    return {"id": str(result.inserted_id)}
```

#### Using Decorators

```python
from mdb_engine.auth.decorators import require_permission, require_admin

@app.get("/documents")
@require_permission("documents", "read")
async def get_documents(user: dict = Depends(get_current_user)):
    db = engine.get_scoped_db("my_app")
    return await db.documents.find({}).to_list(10)

@app.delete("/documents/{doc_id}")
@require_admin
async def delete_document(doc_id: str, user: dict = Depends(get_current_user)):
    db = engine.get_scoped_db("my_app")
    await db.documents.delete_one({"_id": ObjectId(doc_id)})
    return {"success": True}
```

### OSO Cloud Setup

#### Manifest Configuration

```json
{
  "auth": {
    "policy": {
      "provider": "oso",
      "required": true,
      "authorization": {
        "initial_roles": [
          {"user": "alice@example.com", "role": "editor"},
          {"user": "bob@example.com", "role": "viewer"}
        ]
      }
    }
  }
}
```

#### Environment Variables

```bash
export OSO_AUTH="your-oso-api-key"
export OSO_URL="https://cloud.osohq.com"  # Optional, defaults to production
```

#### Usage in Code

Same as Casbin - the `get_authz_provider` dependency automatically returns the correct provider based on your manifest configuration.

### What Gets Auto-Configured

When you use `engine.create_app()` with auth configuration in `manifest.json`:

✅ **Casbin Enforcer** - Automatically created with MongoDB adapter
✅ **Initial Policies** - Loaded from `initial_policies` in manifest
✅ **Role Assignments** - Created from `initial_roles` in manifest
✅ **Demo Users** - Auto-seeded if `demo_users` configured
✅ **Session Management** - Cookie-based sessions configured
✅ **FastAPI Dependencies** - `get_current_user`, `get_authz_provider` available

**All without writing any setup code!**

---

## Memory Service: Intelligent AI Memory Management

MDB-Engine includes a powerful **Memory Service** that provides persistent, semantic memory storage and retrieval using MongoDB Atlas Vector Search. This enables your AI applications to remember user preferences, facts, and conversation context across sessions.

### Key Features

#### 🧠 Intelligent Memory Management
- **Automatic Fact Extraction**: LLM-powered extraction of key facts from conversations
- **Semantic Search**: Find relevant memories using vector similarity search
- **User Isolation**: All memories are scoped per user for privacy and security
- **Metadata Support**: Rich metadata filtering and custom fields
- **Bucket Support**: Organize memories by conversation, category, or custom buckets

#### 🚀 Zero-Configuration Setup
- **Automatic Index Management**: Vector search index is created and managed automatically
- **No Manual Configuration**: Just enable the service in your manifest
- **Smart Updates**: Existing indexes are automatically updated if needed
- **Error Prevention**: Engine prevents manual index conflicts

#### 🎯 Cognitive Features (Optional)
- **Importance Scoring**: AI evaluates memory significance (0.1-1.0 scale)
- **Memory Reinforcement**: Similar memories strengthen existing memories
- **Memory Decay**: Less relevant memories fade over time
- **Memory Merging**: Related memories are combined intelligently
- **Memory Pruning**: Least important memories removed when capacity exceeded

### Quick Start

#### 1. Enable Memory Service in Manifest

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My App",
  "memory_config": {
    "enabled": true,
    "collection_name": "user_memories",
    "embedding_model_dims": 1536
  }
}
```

That's it! The memory service will:
- ✅ Automatically create the collection
- ✅ Automatically create the vector search index with `user_id` filter
- ✅ Configure everything needed for semantic search

#### 2. Use in Your Application

```python
from mdb_engine.dependencies import get_memory_service, get_scoped_db
from fastapi import Depends

@app.post("/chat")
async def chat(
    message: str,
    user_id: str,
    memory=Depends(get_memory_service),
    db=Depends(get_scoped_db)
):
    # Search for relevant memories
    memories = memory.search(
        query=message,
        user_id=user_id,
        limit=5
    )
    
    # Build context from memories
    context = "\n".join([m.get("memory", "") for m in memories])
    
    # Generate response (using your LLM client)
    response = await generate_response(message, context)
    
    # Add new memory from conversation
    memory.add(
        messages=[
            {"role": "user", "content": message},
            {"role": "assistant", "content": response}
        ],
        user_id=user_id
    )
    
    return {"response": response, "memories_used": len(memories)}
```

### Configuration Options

#### Basic Configuration

```json
{
  "memory_config": {
    "enabled": true,
    "collection_name": "user_memories",
    "embedding_model_dims": 1536
  }
}
```

#### Full Configuration with Cognitive Features

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

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `boolean` | `false` | Enable memory service |
| `provider` | `string` | `"cognitive"` | Memory provider (`"cognitive"` or `"custom"`) |
| `collection_name` | `string` | `"{slug}_memories"` | Collection name (auto-prefixed with app slug) |
| `embedding_model` | `string` | `"text-embedding-3-small"` | Embedding model name |
| `chat_model` | `string` | `"gpt-4o"` | LLM model for fact extraction |
| `embedding_model_dims` | `integer` | `1536` | Embedding dimensions (128-4096) |
| `infer` | `boolean` | `true` | Enable LLM fact extraction |
| `async_mode` | `boolean` | `true` | Process memories asynchronously |
| `enable_cognitive` | `boolean` | `true` | Enable cognitive features |
| `max_depth` | `integer\|null` | `100` | Max memories per user (`null` = unlimited) |

### Automatic Index Management

**⚠️ Important**: The memory service **automatically creates and manages its own vector search index**. You do NOT need to (and should NOT) add memory collection indexes to `managed_indexes` in your manifest.

**On Startup:**
1. Service checks if vector search index exists
2. If missing, creates index with correct configuration:
   - Vector field: `embedding` (with specified dimensions)
   - Filter field: `user_id` (required for filtering)
   - Similarity: `cosine`
3. If index exists but missing `user_id` filter, automatically updates it
4. Waits for index to become queryable before allowing searches

**Index Name**: Automatically generated as `{collection_name}_vector_index`
- Example: `conversations_user_memories_vector_index`

### Memory Service Operations

#### Adding Memories

**From Conversations (with LLM inference):**
```python
memory.add(
    messages=[
        {"role": "user", "content": "I'm allergic to peanuts"},
        {"role": "assistant", "content": "I'll remember that."}
    ],
    user_id="user_123"
)
# Automatically extracts facts: "User is allergic to peanuts"
```

**Direct Injection (without LLM inference):**
```python
memory.inject(
    memory="User prefers dark mode",
    user_id="user_123",
    metadata={"source": "settings", "importance": 0.8}
)
```

#### Searching Memories

```python
# Semantic search
memories = memory.search(
    query="What are the user's preferences?",
    user_id="user_123",
    limit=5
)

# With metadata filtering
memories = memory.search(
    query="dietary restrictions",
    user_id="user_123",
    metadata={"category": "health"},
    limit=10
)
```

#### Managing Memories

```python
# Get specific memory
memory_obj = memory.get(memory_id="mem_123", user_id="user_123")

# Get all memories for user
all_memories = memory.get_all(user_id="user_123")

# Update memory
memory.update(
    memory_id="mem_123",
    user_id="user_123",
    memory="Updated memory content",
    metadata={"updated": True}
)

# Delete memory
memory.delete(memory_id="mem_123", user_id="user_123")

# Delete all memories for user
memory.delete_all(user_id="user_123")
```

### Cognitive Features

When `enable_cognitive: true` (default), the memory service includes advanced features:

#### Importance Assessment
Memories are automatically scored for importance (0.1-1.0) based on:
- Uniqueness of information
- Actionability
- Personal significance
- Key facts or decisions

#### Memory Reinforcement
When similar content appears (similarity > 0.85), existing memories are **reinforced** instead of creating duplicates:
- Importance increases
- Access count increments
- Last accessed timestamp updates

#### Memory Decay
Memories that are less relevant to new content gradually **decay** in importance, mimicking natural forgetting.

#### Memory Merging
Related memories (similarity 0.7-0.85) are **merged** into a single cohesive memory:
- LLM combines the texts
- Embeddings are averaged
- Higher importance is used
- Access counts are combined

#### Memory Pruning
When memory count exceeds `max_depth`, least important memories are **pruned** based on effective importance (combines raw importance with access frequency).

### Integration with LLM Services

The memory service works seamlessly with your LLM client:

```python
from mdb_engine.dependencies import get_memory_service, get_llm_client
from openai import AzureOpenAI

@app.post("/chat")
async def chat(
    message: str,
    user_id: str,
    memory=Depends(get_memory_service),
    llm_client=Depends(get_llm_client)
):
    # Get relevant context from memory
    memories = memory.search(query=message, user_id=user_id, limit=5)
    context = "\n".join([m.get("memory", "") for m in memories])
    
    # Generate response with context
    response = llm_client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": f"User context:\n{context}"},
            {"role": "user", "content": message}
        ]
    )
    
    # Store conversation in memory
    memory.add(
        messages=[
            {"role": "user", "content": message},
            {"role": "assistant", "content": response.choices[0].message.content}
        ],
        user_id=user_id
    )
    
    return {"response": response.choices[0].message.content}
```

### Environment Variables

The memory service uses environment variables for LLM/embedding configuration:

```bash
# OpenAI
export OPENAI_API_KEY="your-key"

# Azure OpenAI
export AZURE_OPENAI_API_KEY="your-key"
export AZURE_OPENAI_ENDPOINT="https://your-resource.openai.azure.com"
export AZURE_OPENAI_API_VERSION="2024-02-15-preview"

# Optional: Memory LLM temperature
export MEMORY_LLM_TEMPERATURE="0"
```

### Best Practices

1. **Use Appropriate `max_depth`**: Set based on your use case:
   - Chatbots: 50-100 memories per user
   - Knowledge bases: `null` (unlimited)
   - Personal assistants: 100-200 memories

2. **Leverage Metadata**: Use metadata for filtering and organization:
   ```python
   memory.add(
       messages=[...],
       user_id="user_123",
       metadata={
           "conversation_id": "conv_456",
           "category": "preferences",
           "source": "user_input"
       }
   )
   ```

3. **Use Buckets for Organization**: Group related memories:
   ```python
   memory.add(
       messages=[...],
       user_id="user_123",
       bucket_id="dietary_restrictions",
       bucket_type="health"
   )
   ```

4. **Monitor Memory Growth**: For production apps, set `max_depth` to prevent unbounded growth.

5. **Combine with Chat History**: Use memory service for long-term context, chat history for immediate context.

---

## Before & After Examples

### Example 1: Simple CRUD API

#### Before (Without MDB-Engine)

```python
from fastapi import FastAPI, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId

app = FastAPI()
client = AsyncIOMotorClient("mongodb://localhost:27017")
db = client.my_database

APP_ID = "my_app"

@app.on_event("startup")
async def startup():
    # Manual index creation
    await db.tasks.create_index([("user_id", 1), ("created_at", -1)])
    await db.tasks.create_index("status")

@app.post("/tasks")
async def create_task(task: dict, user_id: str):
    # Manual app_id addition - easy to forget!
    task["app_id"] = APP_ID
    task["user_id"] = user_id
    result = await db.tasks.insert_one(task)
    return {"id": str(result.inserted_id)}

@app.get("/tasks")
async def get_tasks(user_id: str):
    # Manual app_id filtering - easy to forget!
    tasks = await db.tasks.find({
        "app_id": APP_ID,  # Must remember this!
        "user_id": user_id
    }).to_list(10)
    return tasks
```

**Issues:**
- ❌ Manual `app_id` filtering (security risk if forgotten)
- ❌ Manual index creation (scattered code)
- ❌ No authorization
- ❌ Collection naming not prefixed

#### After (With MDB-Engine)

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

# Create app with automatic configuration
app = engine.create_app(
    slug="my_app",
    manifest=Path("manifest.json")
)

@app.post("/tasks")
async def create_task(task: dict, user_id: str, db=Depends(get_scoped_db)):
    # Automatic app_id addition!
    task["user_id"] = user_id
    result = await db.tasks.insert_one(task)
    return {"id": str(result.inserted_id)}

@app.get("/tasks")
async def get_tasks(user_id: str, db=Depends(get_scoped_db)):
    # Automatic app_id filtering!
    tasks = await db.tasks.find({"user_id": user_id}).to_list(10)
    return tasks
```

**manifest.json:**
```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My App",
  "managed_indexes": {
    "tasks": [
      {"type": "regular", "keys": {"user_id": 1, "created_at": -1}},
      {"type": "regular", "keys": {"status": 1}}
    ]
  }
}
```

**Benefits:**
- ✅ Automatic `app_id` filtering (impossible to forget)
- ✅ Declarative index management
- ✅ Collection automatically prefixed: `my_app_tasks`
- ✅ Ready for authorization (just add to manifest)

### Example 2: CRUD API with Authorization

#### Before (Without MDB-Engine)

```python
from fastapi import FastAPI, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from casbin import AsyncEnforcer
from bson import ObjectId

app = FastAPI()
client = AsyncIOMotorClient("mongodb://localhost:27017")
db = client.my_database

enforcer = None

@app.on_event("startup")
async def startup():
    # Manual Casbin setup
    global enforcer
    enforcer = await AsyncEnforcer("rbac_model.conf", "rbac_policy.csv")
    
    # Manual index creation
    await db.tasks.create_index([("user_id", 1), ("created_at", -1)])

def get_current_user(request: Request):
    # Manual user extraction from session
    session_id = request.cookies.get("session_id")
    # ... session lookup logic ...
    return user

@app.get("/tasks")
async def get_tasks(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "Unauthorized")
    
    # Manual permission check
    if not await enforcer.enforce(user["email"], "tasks", "read"):
        raise HTTPException(403, "Access denied")
    
    # Manual app_id filtering
    tasks = await db.tasks.find({
        "app_id": "my_app",
        "user_id": user["_id"]
    }).to_list(10)
    return tasks
```

**Issues:**
- ❌ 50+ lines of boilerplate
- ❌ Manual auth setup
- ❌ Manual permission checks
- ❌ Manual app_id filtering

#### After (With MDB-Engine)

```python
from pathlib import Path
from fastapi import Depends, HTTPException
from mdb_engine import MongoDBEngine
from mdb_engine.dependencies import get_scoped_db, get_current_user, get_authz_provider

# Initialize engine
engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database"
)

# Create app - auth automatically configured!
app = engine.create_app(
    slug="my_app",
    manifest=Path("manifest.json")
)

@app.get("/tasks")
async def get_tasks(
    user: dict = Depends(get_current_user),
    authz = Depends(get_authz_provider),
    db = Depends(get_scoped_db)
):
    # Automatic permission check
    if not await authz.check(user["email"], "tasks", "read"):
        raise HTTPException(403, "Access denied")
    
    # Automatic app_id filtering
    tasks = await db.tasks.find({"user_id": user["_id"]}).to_list(10)
    return tasks
```

**manifest.json:**
```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My App",
  "auth": {
    "policy": {
      "provider": "casbin",
      "required": true,
      "authorization": {
        "model": "rbac",
        "initial_policies": [
          ["admin", "tasks", "read"],
          ["admin", "tasks", "write"],
          ["user", "tasks", "read"]
        ]
      }
    },
    "users": {
      "enabled": true,
      "allow_registration": true
    }
  },
  "managed_indexes": {
    "tasks": [
      {"type": "regular", "keys": {"user_id": 1, "created_at": -1}}
    ]
  }
}
```

**Benefits:**
- ✅ ~15 lines of code (vs 50+ before)
- ✅ Auth automatically configured
- ✅ Permission checks via dependency injection
- ✅ Automatic app_id filtering

### Example 3: AI Chatbot with Memory

#### Before (Without MDB-Engine)

```python
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient
from openai import AzureOpenAI
import os

app = FastAPI()
client = AsyncIOMotorClient("mongodb://localhost:27017")
db = client.my_database
llm_client = AzureOpenAI(...)

APP_ID = "chatbot"

@app.on_event("startup")
async def startup():
    # Manual vector index creation
    await db.user_memories.create_index([
        ("embedding", "vector"),
        ("user_id", 1)
    ])

def get_embedding(text: str) -> list[float]:
    # Manual embedding generation
    response = llm_client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return response.data[0].embedding

@app.post("/chat")
async def chat(message: str, user_id: str):
    # Manual memory search
    query_embedding = get_embedding(message)
    
    # Manual vector search pipeline
    pipeline = [
        {
            "$vectorSearch": {
                "index": "vector_index",
                "path": "embedding",
                "queryVector": query_embedding,
                "filter": {"user_id": user_id, "app_id": APP_ID},
                "numCandidates": 100,
                "limit": 5
            }
        }
    ]
    
    memories = await db.user_memories.aggregate(pipeline).to_list(5)
    context = "\n".join([m["memory"] for m in memories])
    
    # Generate response
    response = llm_client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": f"Context: {context}"},
            {"role": "user", "content": message}
        ]
    )
    
    # Manual memory storage
    memory_text = f"User: {message}\nAssistant: {response.choices[0].message.content}"
    memory_embedding = get_embedding(memory_text)
    
    await db.user_memories.insert_one({
        "app_id": APP_ID,
        "user_id": user_id,
        "memory": memory_text,
        "embedding": memory_embedding
    })
    
    return {"response": response.choices[0].message.content}
```

**Issues:**
- ❌ 100+ lines of boilerplate
- ❌ Manual vector index management
- ❌ Manual embedding generation
- ❌ Manual vector search pipeline
- ❌ No fact extraction or cognitive features
- ❌ No automatic index updates

#### After (With MDB-Engine)

```python
from pathlib import Path
from fastapi import Depends
from mdb_engine import MongoDBEngine
from mdb_engine.dependencies import get_memory_service, get_llm_client, get_llm_model_name

# Initialize engine
engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database"
)

# Create app with memory service configured
app = engine.create_app(
    slug="chatbot",
    manifest=Path("manifest.json")
)

@app.post("/chat")
async def chat(
    message: str,
    user_id: str,
    memory=Depends(get_memory_service),
    llm_client=Depends(get_llm_client),
    model_name=Depends(get_llm_model_name)
):
    # Automatic memory search
    memories = memory.search(
        query=message,
        user_id=user_id,
        limit=5
    )
    
    # Build context
    context = "\n".join([m.get("memory", "") for m in memories])
    
    # Generate response
    response = llm_client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": f"Context: {context}"},
            {"role": "user", "content": message}
        ]
    )
    
    # Automatic memory storage with fact extraction
    memory.add(
        messages=[
            {"role": "user", "content": message},
            {"role": "assistant", "content": response.choices[0].message.content}
        ],
        user_id=user_id
    )
    
    return {"response": response.choices[0].message.content}
```

**manifest.json:**
```json
{
  "schema_version": "2.0",
  "slug": "chatbot",
  "name": "AI Chatbot",
  "memory_config": {
    "enabled": true,
    "collection_name": "user_memories",
    "embedding_model_dims": 1536,
    "enable_cognitive": true,
    "max_depth": 100
  },
  "llm_config": {
    "provider": "azure_openai",
    "model": "gpt-4o"
  }
}
```

**Benefits:**
- ✅ ~30 lines of code (vs 100+ before)
- ✅ Automatic vector index management
- ✅ Automatic embedding generation
- ✅ Automatic fact extraction from conversations
- ✅ Cognitive features (importance, reinforcement, decay, merging, pruning)
- ✅ Automatic index updates
- ✅ User-scoped queries automatically handled

---

## Common Migration Patterns

### Pattern 1: Minimal Migration (Data Scoping Only)

**Use Case:** You just want automatic data isolation, no auth needed.

**Steps:**
1. Create minimal `manifest.json`:
```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My App"
}
```

2. Replace database initialization:
```python
engine = MongoDBEngine(mongo_uri="...", db_name="...")
app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))
```

3. Replace database access:
```python
# Before
tasks = await db.tasks.find({"app_id": "my_app"}).to_list(10)

# After
db = engine.get_scoped_db("my_app")
tasks = await db.tasks.find({}).to_list(10)  # app_id automatically added!
```

### Pattern 2: Add Index Management

**Use Case:** You want declarative index management.

**Steps:**
1. Add `managed_indexes` to `manifest.json`:
```json
{
  "managed_indexes": {
    "tasks": [
      {"type": "regular", "keys": {"user_id": 1, "created_at": -1}}
    ]
  }
}
```

2. Remove manual index creation code:
```python
# Remove this:
@app.on_event("startup")
async def startup():
    await db.tasks.create_index([("user_id", 1), ("created_at", -1)])
```

Indexes are automatically created from manifest!

### Pattern 3: Add Authorization

**Use Case:** You want RBAC authorization.

**Steps:**
1. Add `auth` section to `manifest.json`:
```json
{
  "auth": {
    "policy": {
      "provider": "casbin",
      "authorization": {
        "model": "rbac",
        "initial_policies": [
          ["admin", "tasks", "read"],
          ["admin", "tasks", "write"]
        ]
      }
    }
  }
}
```

2. Use auth dependencies in routes:
```python
from mdb_engine.dependencies import get_current_user, get_authz_provider

@app.get("/tasks")
async def get_tasks(
    user: dict = Depends(get_current_user),
    authz = Depends(get_authz_provider)
):
    if not await authz.check(user["email"], "tasks", "read"):
        raise HTTPException(403, "Access denied")
    # ... rest of code
```

### Pattern 4: Add Memory Service

**Use Case:** You want to add AI memory capabilities to your app.

**Steps:**
1. Add `memory_config` to `manifest.json`:
```json
{
  "memory_config": {
    "enabled": true,
    "collection_name": "user_memories",
    "embedding_model_dims": 1536
  }
}
```

2. Use memory service in routes:
```python
from mdb_engine.dependencies import get_memory_service

@app.post("/chat")
async def chat(
    message: str,
    user_id: str,
    memory=Depends(get_memory_service)
):
    # Search memories
    memories = memory.search(query=message, user_id=user_id, limit=5)
    
    # Add new memory
    memory.add(messages=[...], user_id=user_id)
```

3. Remove manual vector index creation:
```python
# Remove this:
@app.on_event("startup")
async def startup():
    await db.user_memories.create_index([...])  # No longer needed!
```

The memory service automatically creates and manages its vector search index!

### Pattern 5: Full Migration (Everything)

**Use Case:** Complete migration with all features.

**Steps:**
1. Create complete `manifest.json` with:
   - App metadata
   - Auth configuration
   - Index definitions
   - CORS settings
   - Services (memory, embeddings, LLM, WebSockets, etc.)

2. Replace all database access with `get_scoped_db()`

3. Remove all manual setup code:
   - Index creation
   - Auth setup
   - CORS middleware
   - Session management

4. Use FastAPI dependencies:
   - `get_scoped_db` for database access
   - `get_current_user` for authentication
   - `get_authz_provider` for authorization

### Pattern 6: Add Memory Encryption (CSFLE)

**Use Case:** You want to encrypt sensitive memory data at rest.

**Steps:**
1. Update `manifest.json` memory config:
```json
{
  "memory_config": {
    "enabled": true,
    "encrypted": true,
    "collection_name": "user_memories"
  }
}
```

2. Set environment variables (optional for Docker/Dev):
```bash
# Docker: Auto-generated key used automatically
# Manual: MDB_CSFLE_LOCAL_KEY=<your-key>
```

3. No code changes required! The engine handles encryption/decryption transparently.

---

## Troubleshooting

### Common Issues

#### Issue 1: "Engine not initialized"

**Error:**
```
RuntimeError: Engine not initialized
```

**Solution:**
Make sure you're using `engine.create_app()` or calling `await engine.initialize()` before using the engine.

```python
# ✅ Correct
app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))

# ❌ Wrong
engine = MongoDBEngine(...)
db = engine.get_scoped_db("my_app")  # Error: not initialized!
```

#### Issue 2: "Collection not found"

**Error:**
```
CollectionNotFound: Collection 'my_app_tasks' not found
```

**Solution:**
Collections are created automatically on first write. If you're seeing this error, make sure:
1. You're using `get_scoped_db()` (not raw database access)
2. The app slug matches your manifest
3. You're writing to the collection (collections are created lazily)

#### Issue 3: "Authorization provider not found"

**Error:**
```
AttributeError: 'NoneType' object has no attribute 'check'
```

**Solution:**
Make sure you've configured auth in your `manifest.json`:

```json
{
  "auth": {
    "policy": {
      "provider": "casbin",
      "authorization": {
        "model": "rbac"
      }
    }
  }
}
```

#### Issue 4: "Index creation failed"

**Error:**
```
Index creation failed: Invalid index definition
```

**Solution:**
Check your `managed_indexes` in `manifest.json`:
- Use correct index types: `regular`, `text`, `vector`, `ttl`
- Use correct key format: `{"field": 1}` for ascending, `{"field": -1}` for descending
- For vector indexes, include proper `definition` with `fields` array

#### Issue 5: "Query not scoped correctly"

**Symptom:**
Queries return data from other apps.

**Solution:**
Make sure you're using `get_scoped_db()` and not raw database access:

```python
# ✅ Correct
db = engine.get_scoped_db("my_app")
tasks = await db.tasks.find({}).to_list(10)

# ❌ Wrong - no scoping!
db = engine.mongo_db  # This doesn't exist anymore - use get_scoped_db()
tasks = await db.tasks.find({}).to_list(10)
```

### Debugging Tips

1. **Check Engine Status:**
```python
health = await engine.get_health_status()
print(health)
```

2. **Verify App Registration:**
```python
app_config = engine.get_app("my_app")
print(app_config)
```

3. **Check Index Creation:**
```python
db = engine.get_scoped_db("my_app")
indexes = await db.tasks.list_indexes().to_list(length=None)
print([idx["name"] for idx in indexes])
```

4. **Verify Scoping:**
```python
# Check that queries are scoped
db = engine.get_scoped_db("my_app")
# The query will automatically include app_id filter
```

---

## Next Steps

After migration:

1. **Test Data Isolation**
   - Verify queries are scoped correctly
   - Test cross-app data access (if using multi-app)

2. **Verify Indexes**
   - Check that indexes are created
   - Monitor query performance

3. **Test Authorization**
   - Test permission checks
   - Verify role assignments

4. **Monitor Performance**
   - Use engine health checks
   - Monitor metrics

5. **Explore Advanced Features**
   - WebSockets
   - Memory service (MongoDB Atlas Vector Search)
   - Embedding service
   - Multi-app scenarios

---

## Additional Resources

- [MDB-Engine 101 Guide](MDB_ENGINE_101.md) - Complete reference guide
- [Manifest Deep Dive](MANIFEST_DEEP_DIVE.md) - Detailed manifest documentation
- [Authorization Guide](AUTHZ.md) - Complete authZ documentation
- [Quick Start Guide](QUICK_START.md) - Getting started quickly
- [Examples](../examples/) - Working code examples

---

## Appendix: Shared Authentication (SSO)

### Overview

**Shared Authentication** (`auth.mode: "shared"`) enables Single Sign-On (SSO) across multiple apps. Users authenticate once and can access any app that uses shared auth mode, subject to per-app role requirements.

### When to Use Shared Auth

**Use Shared Auth when:**
- ✅ Building a platform with multiple related apps
- ✅ You want users to login once (SSO)
- ✅ You need per-app role management
- ✅ Apps should share user identity
- ✅ You want centralized user management

**Use Per-App Auth (`mode: "app"`) when:**
- ✅ Apps are independent
- ✅ Each app has its own users
- ✅ Simpler setup without SSO needs
- ✅ Apps don't need to share identity

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Shared User Pool                            │
│        _mdb_engine_shared_users collection                      │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ User: alice@example.com                                  │   │
│  │ app_roles:                                               │   │
│  │   click_tracker: ["viewer"]                              │   │
│  │   dashboard: ["editor", "admin"]                         │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┴───────────────┐
              │ Same JWT Token (SSO)          │
              │                               │
     ┌────────▼────────┐            ┌────────▼────────┐
     │  Click Tracker  │            │    Dashboard    │
     │   Port 8000     │            │   Port 8001     │
     │                 │            │                 │
     │ require_role:   │            │ require_role:   │
     │   "viewer"      │            │   "editor"      │
     │                 │            │                 │
     │ ✓ alice can     │            │ ✓ alice can     │
     │   access        │            │   access        │
     └─────────────────┘            └─────────────────┘
```

### Manifest Configuration

#### Basic Shared Auth Setup

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My Application",
  "auth": {
    "mode": "shared",
    "auth_hub_url": "http://localhost:8000",
    "roles": ["viewer", "editor", "admin"],
    "default_role": "viewer",
    "require_role": "viewer",
    "public_routes": ["/health", "/api/public", "/login", "/register"]
  }
}
```

#### Complete Shared Auth Configuration

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My Application",
  "auth": {
    "mode": "shared",
    "auth_hub_url": "http://localhost:8000",
    "related_apps": {
      "dashboard": "http://localhost:8001",
      "click_tracker": "http://localhost:8000"
    },
    "roles": ["viewer", "editor", "admin"],
    "default_role": "viewer",
    "require_role": "viewer",
    "public_routes": ["/health", "/api/public", "/login", "/register"],
    "users": {
      "enabled": true,
      "strategy": "app_users",
      "demo_users": [
        {
          "email": "alice@example.com",
          "password": "password123",
          "app_roles": {
            "my_app": ["admin"],
            "other_app": ["viewer"]
          }
        }
      ],
      "demo_user_seed_strategy": "auto"
    },
    "token_management": {
      "enabled": true,
      "access_token_ttl": 900,
      "refresh_token_ttl": 604800,
      "security": {
        "require_https": false,
        "csrf_protection": true,
        "cookie_secure": "auto",
        "cookie_samesite": "lax"
      }
    }
  }
}
```

### Configuration Fields

| Field | Description | Required |
|-------|-------------|----------|
| `mode` | Set to `"shared"` to enable SSO | Yes |
| `auth_hub_url` | URL of the authentication hub for SSO apps. Used for redirecting unauthenticated users to login. Can be overridden via `AUTH_HUB_URL` environment variable. Example: `"http://localhost:8000"` or `"https://auth.example.com"` | No |
| `related_apps` | Map of related app slugs to their URLs for cross-app navigation. Keys are app slugs, values are URLs. Can be overridden via `{APP_SLUG_UPPER}_URL` environment variables. Example: `{"dashboard": "http://localhost:8001"}` | No |
| `roles` | Available roles for this app (e.g., `["viewer", "editor", "admin"]`) | Yes |
| `default_role` | Role assigned to new users for this app | No |
| `require_role` | Minimum role required to access app (users without this role are denied) | No |
| `public_routes` | Routes that don't require authentication (supports wildcards) | No |

**URL Configuration Priority**:
1. `manifest.auth.auth_hub_url` or `manifest.auth.related_apps[app_slug]` (declarative, versioned)
2. Environment variables (`AUTH_HUB_URL`, `{APP_SLUG}_URL`) (runtime override)
3. Defaults (`http://localhost:8000` or app-specific defaults) (fallback)

### How It Works

1. **Shared User Pool**: Users are stored in `_mdb_engine_shared_users` collection (not app-specific)
2. **JWT Tokens**: Single JWT token (`mdb_auth_token` cookie) works across all apps
3. **Per-App Roles**: Each user has `app_roles` object with roles per app:
   ```json
   {
     "email": "alice@example.com",
     "app_roles": {
       "click_tracker": ["viewer"],
       "dashboard": ["editor", "admin"]
     }
   }
   ```
4. **Auto-Configured Middleware**: `SharedAuthMiddleware` is automatically added by `engine.create_app()`
5. **User Access**: User info is available via `request.state.user` and `request.state.user_roles`

### Code Examples

#### Accessing User in Shared Auth Mode

```python
from fastapi import Request, HTTPException
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(...)
app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))

@app.get("/protected")
async def protected_route(request: Request):
    # User is automatically populated by SharedAuthMiddleware
    user = request.state.user
    
    if not user:
        raise HTTPException(401, "Unauthorized")
    
    # Get user's roles for this app
    roles = request.state.user_roles  # e.g., ["viewer", "editor"]
    
    return {
        "email": user["email"],
        "roles": roles,
        "app_roles": user.get("app_roles", {}).get("my_app", [])
    }
```

#### Checking Role Requirements

```python
@app.get("/admin")
async def admin_route(request: Request):
    user = request.state.user
    
    if not user:
        raise HTTPException(401, "Unauthorized")
    
    # Check if user has required role for this app
    user_roles = request.state.user_roles
    
    if "admin" not in user_roles:
        raise HTTPException(403, "Admin access required")
    
    return {"message": "Admin access granted"}
```

#### Login/Registration with Shared Auth

```python
from mdb_engine.auth.shared_users import SharedUserPool
from fastapi import Request, Response

@app.post("/login")
async def login(request: Request, response: Response, email: str, password: str):
    # Get SharedUserPool from app state (auto-initialized)
    user_pool: SharedUserPool = request.app.state.shared_user_pool
    
    # Authenticate user
    result = await user_pool.authenticate(email, password, request, response)
    
    if result["success"]:
        return {"success": True, "user": result["user"]}
    else:
        raise HTTPException(401, result.get("error", "Login failed"))

@app.post("/register")
async def register(request: Request, response: Response, email: str, password: str):
    user_pool: SharedUserPool = request.app.state.shared_user_pool
    
    # Register user with default role for this app
    result = await user_pool.register(
        email=email,
        password=password,
        app_slug="my_app",
        default_role="viewer",
        request=request,
        response=response
    )
    
    if result["success"]:
        return {"success": True, "user": result["user"]}
    else:
        raise HTTPException(400, result.get("error", "Registration failed"))
```

### Multi-App Example

#### App 1: Click Tracker (Viewer Access)

```json
{
  "slug": "click_tracker",
  "auth": {
    "mode": "shared",
    "roles": ["clicker", "tracker", "admin"],
    "require_role": "clicker",
    "public_routes": ["/health", "/login", "/register"]
  }
}
```

#### App 2: Dashboard (Editor Access Required)

```json
{
  "slug": "dashboard",
  "auth": {
    "mode": "shared",
    "roles": ["clicker", "tracker", "admin"],
    "require_role": "tracker",
    "public_routes": ["/health", "/login"]
  }
}
```

**User Flow:**
1. User registers on Click Tracker → Gets `clicker` role for Click Tracker
2. User logs in → Receives JWT token (`mdb_auth_token` cookie)
3. User visits Dashboard → Automatically authenticated via SSO
4. Dashboard checks role → User has `clicker` role, but Dashboard requires `tracker`
5. User is denied access → Must be granted `tracker` role for Dashboard

### Environment Variables

**Critical:** All apps using shared auth must use the same JWT secret!

```bash
# Required: Same secret for all apps (for SSO to work)
export MDB_ENGINE_JWT_SECRET="your-secret-key-here"

# Or use FLASK_SECRET_KEY (backward compatibility)
export FLASK_SECRET_KEY="your-secret-key-here"

# MongoDB connection (shared database)
export MONGO_URI="mongodb://localhost:27017"
export MONGO_DB_NAME="shared_database"
```

**Generate a secure secret:**
```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

### Setup Script for Shared Auth

Here's a complete shell script to set up shared authentication for multiple apps:

**`setup_shared_auth.sh`:**
```bash
#!/bin/bash
# Setup script for MDB-Engine Shared Authentication (SSO)
# This script configures environment variables for shared auth across multiple apps

set -e  # Exit on error

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== MDB-Engine Shared Auth Setup ===${NC}\n"

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: python3 is required but not found${NC}"
    exit 1
fi

# Generate JWT secret if not provided
if [ -z "$MDB_ENGINE_JWT_SECRET" ]; then
    echo -e "${YELLOW}Generating secure JWT secret...${NC}"
    JWT_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
    echo -e "${GREEN}Generated JWT secret: ${JWT_SECRET}${NC}\n"
else
    JWT_SECRET="$MDB_ENGINE_JWT_SECRET"
    echo -e "${GREEN}Using provided JWT secret${NC}\n"
fi

# MongoDB configuration (with defaults)
MONGO_URI="${MONGO_URI:-mongodb://localhost:27017}"
MONGO_DB_NAME="${MONGO_DB_NAME:-mdb_runtime_shared}"

# Export environment variables
export MDB_ENGINE_JWT_SECRET="$JWT_SECRET"
export FLASK_SECRET_KEY="$JWT_SECRET"  # Backward compatibility
export MONGO_URI="$MONGO_URI"
export MONGO_DB_NAME="$MONGO_DB_NAME"

echo -e "${GREEN}Environment variables configured:${NC}"
echo "  MDB_ENGINE_JWT_SECRET=$JWT_SECRET"
echo "  MONGO_URI=$MONGO_URI"
echo "  MONGO_DB_NAME=$MONGO_DB_NAME"
echo ""

# Create .env file for easy loading
ENV_FILE=".env.shared_auth"
cat > "$ENV_FILE" << EOF
# MDB-Engine Shared Auth Configuration
# Generated on $(date)
# 
# IMPORTANT: Use the SAME JWT_SECRET across all apps for SSO to work!

MDB_ENGINE_JWT_SECRET=$JWT_SECRET
FLASK_SECRET_KEY=$JWT_SECRET
MONGO_URI=$MONGO_URI
MONGO_DB_NAME=$MONGO_DB_NAME
EOF

echo -e "${GREEN}Created ${ENV_FILE} file${NC}"
echo -e "${YELLOW}To use these variables, run: source ${ENV_FILE}${NC}\n"

# Verify MongoDB connection (optional)
if command -v mongosh &> /dev/null || command -v mongo &> /dev/null; then
    echo -e "${YELLOW}Checking MongoDB connection...${NC}"
    MONGO_HOST=$(echo "$MONGO_URI" | sed -n 's/.*:\/\/\([^:]*\).*/\1/p')
    MONGO_PORT=$(echo "$MONGO_URI" | sed -n 's/.*:\([0-9]*\).*/\1/p' || echo "27017")
    
    if nc -z "$MONGO_HOST" "${MONGO_PORT:-27017}" 2>/dev/null; then
        echo -e "${GREEN}✓ MongoDB is reachable at ${MONGO_HOST}:${MONGO_PORT:-27017}${NC}"
    else
        echo -e "${YELLOW}⚠ MongoDB connection check failed (may not be running)${NC}"
    fi
    echo ""
fi

# Instructions
echo -e "${GREEN}=== Setup Complete ===${NC}\n"
echo "Next steps:"
echo "  1. Source the environment file: source ${ENV_FILE}"
echo "  2. Ensure all your apps use the SAME JWT_SECRET"
echo "  3. Configure your manifests with auth.mode: 'shared'"
echo "  4. Start your apps - they will automatically use shared auth"
echo ""
echo -e "${YELLOW}Example manifest.json:${NC}"
cat << 'MANIFEST_EXAMPLE'
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My App",
  "auth": {
    "mode": "shared",
    "roles": ["viewer", "editor", "admin"],
    "require_role": "viewer",
    "public_routes": ["/health", "/login", "/register"]
  }
}
MANIFEST_EXAMPLE
echo ""
```

**Usage:**

1. **Make the script executable:**
```bash
chmod +x setup_shared_auth.sh
```

2. **Run the script:**
```bash
# Basic usage (generates secret automatically)
./setup_shared_auth.sh

# Or provide your own secret
MDB_ENGINE_JWT_SECRET="your-secret-key" ./setup_shared_auth.sh

# Or customize MongoDB connection
MONGO_URI="mongodb://remote-host:27017" MONGO_DB_NAME="my_shared_db" ./setup_shared_auth.sh
```

3. **Load the environment variables:**
```bash
# Source the generated .env file
source .env.shared_auth

# Or export manually
export MDB_ENGINE_JWT_SECRET="..."
export MONGO_URI="mongodb://localhost:27017"
export MONGO_DB_NAME="mdb_runtime_shared"
```

4. **Use in your apps:**
```bash
# App 1
source .env.shared_auth
python app1.py

# App 2 (same environment variables!)
source .env.shared_auth
python app2.py
```

**For Docker/Docker Compose:**

Create a `.env` file that all services can use:

```bash
# .env (for docker-compose)
MDB_ENGINE_JWT_SECRET=your-shared-secret-key-here
MONGO_URI=mongodb://mongo:27017
MONGO_DB_NAME=mdb_runtime_shared
```

Then in your `docker-compose.yml`:
```yaml
services:
  app1:
    env_file:
      - .env
    # ...
  
  app2:
    env_file:
      - .env
    # ...
```

**Quick Setup for Development:**

```bash
#!/bin/bash
# Quick dev setup - sets up shared auth and starts apps

# Run setup script
./setup_shared_auth.sh

# Source environment
source .env.shared_auth

# Start apps (in separate terminals or background)
# Terminal 1
python app1.py

# Terminal 2  
python app2.py
```

### What Gets Auto-Configured

When you use `engine.create_app()` with `auth.mode: "shared"`:

✅ **SharedUserPool** - Centralized user management initialized
✅ **SharedAuthMiddleware** - ASGI middleware automatically added
✅ **JWT Token Management** - Token generation and validation configured
✅ **CSRF Protection** - Automatically enabled for shared auth mode
✅ **Session Management** - Multi-device session tracking
✅ **Cookie Management** - Secure cookie handling for SSO
✅ **Role Checking** - Automatic role requirement enforcement

**All without writing any setup code!**

### Comparing Auth Modes

| Feature | `mode: "app"` | `mode: "shared"` |
|---------|---------------|------------------|
| **User Storage** | Per-app collection (`my_app_users`) | Shared collection (`_mdb_engine_shared_users`) |
| **Login** | Per-app (isolated) | SSO across all apps |
| **Tokens** | App-specific cookies | Shared JWT (`mdb_auth_token`) |
| **Roles** | N/A (use authZ provider) | Per-app roles in `app_roles` |
| **Middleware** | None (manual setup) | `SharedAuthMiddleware` (auto) |
| **Use Case** | Independent apps | Platform with multiple apps |
| **Setup Complexity** | Simple | Moderate (requires JWT secret) |

### Migration from Per-App to Shared Auth

#### Step 1: Update Manifest

```json
{
  "auth": {
    "mode": "shared",  // Changed from "app"
    "roles": ["viewer", "editor", "admin"],
    "require_role": "viewer",
    "public_routes": ["/health", "/login"]
  }
}
```

#### Step 2: Set JWT Secret

```bash
export MDB_ENGINE_JWT_SECRET="your-secret-key"
```

#### Step 3: Update Code

**Before (Per-App Auth):**
```python
from mdb_engine.dependencies import get_current_user

@app.get("/protected")
async def protected(user: dict = Depends(get_current_user)):
    return {"user": user}
```

**After (Shared Auth):**
```python
from fastapi import Request

@app.get("/protected")
async def protected(request: Request):
    user = request.state.user  # Populated by SharedAuthMiddleware
    if not user:
        raise HTTPException(401, "Unauthorized")
    return {"user": user}
```

#### Step 4: Migrate Existing Users (Optional)

If you have existing users in app-specific collections, migrate them:

```python
# Migration script
async def migrate_users_to_shared():
    engine = MongoDBEngine(...)
    await engine.initialize()
    
    # Get users from old app-specific collection
    db = engine.get_scoped_db("my_app")
    old_users = await db.users.find({}).to_list(length=None)
    
    # Get SharedUserPool
    user_pool = SharedUserPool(engine.mongo_db)
    
    # Migrate each user
    for old_user in old_users:
        await user_pool.register(
            email=old_user["email"],
            password_hash=old_user["password_hash"],  # Preserve existing hash
            app_slug="my_app",
            default_role="viewer"
        )
```

### Security Considerations

1. **JWT Secret**: Must be the same across all apps for SSO to work
2. **HTTPS**: Use HTTPS in production (set `require_https: true` in token_management)
3. **CSRF Protection**: Automatically enabled for shared auth mode
4. **Cookie Security**: Cookies are automatically configured with secure settings
5. **Role Validation**: Always validate roles server-side (never trust client)

### Troubleshooting Shared Auth

#### Issue: "User not authenticated across apps"

**Solution:** Ensure all apps use the same `MDB_ENGINE_JWT_SECRET`:
```bash
# App 1
export MDB_ENGINE_JWT_SECRET="shared-secret"

# App 2 (must be the same!)
export MDB_ENGINE_JWT_SECRET="shared-secret"
```

#### Issue: "Role check fails"

**Solution:** Verify user has correct role in `app_roles`:
```python
# Check user's roles
user = request.state.user
app_roles = user.get("app_roles", {}).get("my_app", [])
print(f"User roles for my_app: {app_roles}")
```

#### Issue: "CSRF token validation fails"

**Solution:** CSRF protection is auto-enabled. Ensure:
- Cookies are sent with requests (`credentials: 'include'` in fetch)
- CSRF token is included in request headers
- Same-site cookie settings allow cross-app requests

### Complete Example

See the [Multi-App Shared Auth example](../../examples/advanced/multi_app_shared/) for a complete working implementation with:
- Two apps (Click Tracker and Dashboard)
- Shared authentication
- Per-app role management
- Cross-app data access
- Full SSO flow

---

**Ready to migrate? Start with the minimal manifest and add features as you need them!**
