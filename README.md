# mdb-engine

**The MongoDB Engine for Python Apps** — Auto-sandboxing, index management, and auth in one package.

[![PyPI](https://img.shields.io/pypi/v/mdb-engine)](https://pypi.org/project/mdb-engine/)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](https://opensource.org/licenses/AGPL-3.0)

---

## 🎯 manifest.json: The Key to Everything

**`manifest.json` is the foundation of your application.** It's a single configuration file that defines your app's identity, data structure, authentication, indexes, and services. Everything flows from this file.

### Your First manifest.json

Create a `manifest.json` file with just 3 fields:

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My App"
}
```

That's it! This minimal manifest gives you:
- ✅ Automatic data scoping (all queries filtered by `app_id`)
- ✅ Collection name prefixing (`db.tasks` → `my_app_tasks`)
- ✅ App registration and lifecycle management

**Learn more**: [Quick Start Guide](docs/QUICK_START.md) | [Manifest Deep Dive](docs/MANIFEST_DEEP_DIVE.md)

---

## Installation

```bash
pip install mdb-engine
```

---

## 30-Second Quick Start: Build a Todo List API

Let's build a complete CRUD todo list app in 3 steps!

### Step 1: Create `manifest.json`

```json
{
  "schema_version": "2.0",
  "slug": "todo_app",
  "name": "Todo List App",
  "managed_indexes": {
    "todos": [
      {
        "type": "regular",
        "keys": {"completed": 1, "created_at": -1},
        "name": "completed_sort"
      }
    ]
  }
}
```

### Step 2: Create `app.py` with Full CRUD

```python
from datetime import datetime
from pathlib import Path
from typing import Optional

from bson import ObjectId
from fastapi import Depends, HTTPException
from pydantic import BaseModel

from mdb_engine import MongoDBEngine
from mdb_engine.dependencies import get_scoped_db

# Initialize engine
engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database"
)

# Create app - manifest.json loaded automatically!
app = engine.create_app(
    slug="todo_app",
    manifest=Path("manifest.json")
)

# Pydantic models
class TodoCreate(BaseModel):
    title: str
    description: Optional[str] = None

class TodoUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    completed: Optional[bool] = None

# CREATE - Add a new todo
@app.post("/todos")
async def create_todo(todo: TodoCreate, db=Depends(get_scoped_db)):
    doc = {
        **todo.dict(),
        "completed": False,
        "created_at": datetime.utcnow()
    }
    result = await db.todos.insert_one(doc)
    return {"id": str(result.inserted_id), "message": "Todo created"}

# READ - List all todos
@app.get("/todos")
async def list_todos(completed: Optional[bool] = None, db=Depends(get_scoped_db)):
    query = {}
    if completed is not None:
        query["completed"] = completed
    
    todos = await db.todos.find(query).sort("created_at", -1).to_list(length=100)
    for todo in todos:
        todo["_id"] = str(todo["_id"])
    return {"todos": todos, "count": len(todos)}

# READ - Get single todo
@app.get("/todos/{todo_id}")
async def get_todo(todo_id: str, db=Depends(get_scoped_db)):
    todo = await db.todos.find_one({"_id": ObjectId(todo_id)})
    if not todo:
        raise HTTPException(status_code=404, detail="Todo not found")
    todo["_id"] = str(todo["_id"])
    return todo

# UPDATE - Update a todo
@app.put("/todos/{todo_id}")
async def update_todo(todo_id: str, todo: TodoUpdate, db=Depends(get_scoped_db)):
    updates = {k: v for k, v in todo.dict(exclude_unset=True).items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    updates["updated_at"] = datetime.utcnow()
    result = await db.todos.update_one(
        {"_id": ObjectId(todo_id)},
        {"$set": updates}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Todo not found")
    return {"message": "Todo updated"}

# DELETE - Delete a todo
@app.delete("/todos/{todo_id}")
async def delete_todo(todo_id: str, db=Depends(get_scoped_db)):
    result = await db.todos.delete_one({"_id": ObjectId(todo_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Todo not found")
    return {"message": "Todo deleted"}
```

### Step 3: Run It!

```bash
# Start MongoDB (if not running)
mongod

# Install dependencies
pip install mdb-engine fastapi uvicorn

# Run the app
uvicorn app:app --reload
```

**Test your API:**
```bash
# Create a todo
curl -X POST http://localhost:8000/todos \
  -H "Content-Type: application/json" \
  -d '{"title": "Buy groceries", "description": "Milk and eggs"}'

# List todos
curl http://localhost:8000/todos

# Update a todo (replace {id} with actual ID)
curl -X PUT http://localhost:8000/todos/{id} \
  -H "Content-Type: application/json" \
  -d '{"completed": true}'

# Delete a todo
curl -X DELETE http://localhost:8000/todos/{id}
```

**What just happened?**
- ✅ **Automatic scoping**: All queries filtered by `app_id` — your data is isolated
- ✅ **Indexes created**: The `completed_sort` index was created automatically
- ✅ **Lifecycle managed**: Startup/shutdown handled automatically
- ✅ **Zero boilerplate**: No connection setup, no index scripts, no auth handlers

**That's it!** You now have a fully functional, production-ready todo API with automatic data sandboxing, index management, and lifecycle handling.

---

## Basic Examples

### 1. Index Management

Define indexes in your `manifest.json` — they're auto-created on startup:

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My App",
  "status": "active",
  "managed_indexes": {
    "tasks": [
      {
        "type": "regular",
        "keys": {"status": 1, "created_at": -1},
        "name": "status_sort"
      },
      {
        "type": "regular",
        "keys": {"priority": -1},
        "name": "priority_idx"
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
  }
}
```

Supported index types: `regular`, `text`, `vector`, `ttl`, `compound`.

### 2. CRUD Operations (Auto-Scoped)

All database operations are automatically scoped to your app. Use `Depends(get_scoped_db)` in route handlers:

```python
from mdb_engine.dependencies import get_scoped_db

@app.post("/tasks")
async def create_task(task: dict, db=Depends(get_scoped_db)):
    result = await db.tasks.insert_one(task)
    return {"id": str(result.inserted_id)}

@app.get("/tasks")
async def list_tasks(db=Depends(get_scoped_db)):
    return await db.tasks.find({"status": "pending"}).to_list(length=10)

@app.put("/tasks/{task_id}")
async def update_task(task_id: str, db=Depends(get_scoped_db)):
    await db.tasks.update_one({"_id": task_id}, {"$set": {"status": "done"}})
    return {"updated": True}

@app.delete("/tasks/{task_id}")
async def delete_task(task_id: str, db=Depends(get_scoped_db)):
    await db.tasks.delete_one({"_id": task_id})
    return {"deleted": True}
```

**What happens under the hood:**
```python
# You write:
await db.tasks.find({}).to_list(length=10)

# Engine executes:
# Collection: my_app_tasks
# Query: {"app_id": "my_app"}
```

### 3. Health Checks

Built-in observability:

```python
@app.get("/health")
async def health():
    status = await engine.get_health_status()
    return {"status": status.get("status", "unknown")}
```

---

## Why mdb-engine?

- **manifest.json is everything** — Single source of truth for your entire app configuration
- **Zero boilerplate** — No more connection setup, index creation scripts, or auth handlers
- **Data isolation** — Multi-tenant ready with automatic app sandboxing
- **Manifest-driven** — Define your app's "DNA" in JSON, not scattered code
- **Incremental adoption** — Start minimal, add features as needed
- **No lock-in** — Standard Motor/PyMongo underneath; export anytime with `mongodump --query='{"app_id":"my_app"}'`

---

## Advanced Features

| Feature | Description | Learn More |
|---------|-------------|------------|
| **Authentication** | JWT + Casbin/OSO RBAC | [Auth Guide](https://github.com/ranfysvalle02/mdb-engine/blob/main/docs/AUTHZ.md) |
| **Vector Search** | Atlas Vector Search + embeddings | [RAG Example](https://github.com/ranfysvalle02/mdb-engine/tree/main/examples/basic/interactive_rag) |
| **Memory Service** | Persistent AI memory with Mem0 | [Chat Example](https://github.com/ranfysvalle02/mdb-engine/tree/main/examples/basic/chit_chat) |
| **WebSockets** | Real-time updates from manifest | [Docs](https://github.com/ranfysvalle02/mdb-engine/blob/main/docs/ARCHITECTURE.md) |
| **Multi-App** | Secure cross-app data access | [Multi-App Example](https://github.com/ranfysvalle02/mdb-engine/tree/main/examples/advanced/multi_app) |
| **SSO** | Shared auth across apps | [Shared Auth Example](https://github.com/ranfysvalle02/mdb-engine/tree/main/examples/advanced/multi_app_shared) |

### AppContext — All Services in One Place ✨

```python
from fastapi import Depends
from mdb_engine.dependencies import AppContext

@app.post("/ai-chat")
async def chat(query: str, ctx: AppContext = Depends()):
    user = ctx.require_user()  # 401 if not logged in
    ctx.require_role("user")   # 403 if missing role
    
    # Everything available: ctx.db, ctx.embedding_service, ctx.memory, ctx.llm
    if ctx.llm:
        response = ctx.llm.chat.completions.create(
            model=ctx.llm_model,
            messages=[{"role": "user", "content": query}]
        )
        return {"response": response.choices[0].message.content}
```

---

## Full Examples

Clone and run:

```bash
git clone https://github.com/ranfysvalle02/mdb-engine.git
cd mdb-engine/examples/basic/chit_chat
docker-compose up --build
```

### Basic Examples

| Example | Description |
|---------|-------------|
| [chit_chat](https://github.com/ranfysvalle02/mdb-engine/tree/main/examples/basic/chit_chat) | AI chat with persistent memory |
| [interactive_rag](https://github.com/ranfysvalle02/mdb-engine/tree/main/examples/basic/interactive_rag) | RAG with vector search |
| [oso_hello_world](https://github.com/ranfysvalle02/mdb-engine/tree/main/examples/basic/oso_hello_world) | OSO Cloud authorization |
| [parallax](https://github.com/ranfysvalle02/mdb-engine/tree/main/examples/basic/parallax) | Dynamic schema generation |
| [vector_hacking](https://github.com/ranfysvalle02/mdb-engine/tree/main/examples/basic/vector_hacking) | Vector embeddings & attacks |

### Advanced Examples

| Example | Description |
|---------|-------------|
| [simple_app](https://github.com/ranfysvalle02/mdb-engine/tree/main/examples/advanced/simple_app) | Task management with `create_app()` pattern |
| [multi_app](https://github.com/ranfysvalle02/mdb-engine/tree/main/examples/advanced/multi_app) | Multi-tenant with cross-app access |
| [multi_app_shared](https://github.com/ranfysvalle02/mdb-engine/tree/main/examples/advanced/multi_app_shared) | SSO with shared user pool |

---

## Manual Setup (Alternative)

If you need more control over the FastAPI lifecycle:

```python
from pathlib import Path
from fastapi import FastAPI
from mdb_engine import MongoDBEngine

app = FastAPI()
engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="my_database")

@app.on_event("startup")
async def startup():
    await engine.initialize()
    manifest = await engine.load_manifest(Path("manifest.json"))
    await engine.register_app(manifest, create_indexes=True)

@app.on_event("shutdown")
async def shutdown():
    await engine.shutdown()

@app.get("/items")
async def get_items():
    db = engine.get_scoped_db("my_app")
    return await db.items.find({}).to_list(length=10)
```

---

## Understanding manifest.json

Your `manifest.json` is the heart of your application. It defines:

- **App Identity**: `slug`, `name`, `description`
- **Data Access**: `data_access.read_scopes`, `data_access.write_scope`
- **Indexes**: `managed_indexes` (regular, vector, text, TTL, compound)
- **Authentication**: `auth.policy`, `auth.users` (Casbin/OSO, demo users)
- **AI Services**: `embedding_config`, `memory_config`
- **Real-time**: `websockets` endpoints
- **CORS**: `cors` settings

**Start minimal, grow as needed.** You can begin with just `slug`, `name`, and `schema_version`, then add features incrementally.

**📖 Learn More:**
- [Quick Start Guide](docs/QUICK_START.md) - Get started with manifest.json
- [Manifest Deep Dive](docs/MANIFEST_DEEP_DIVE.md) - Comprehensive manifest.json guide
- [Examples](examples/) - Real-world manifest.json files

---

## Links

- [GitHub Repository](https://github.com/ranfysvalle02/mdb-engine)
- [Documentation](https://github.com/ranfysvalle02/mdb-engine/tree/main/docs)
- [All Examples](https://github.com/ranfysvalle02/mdb-engine/tree/main/examples)
- [Quick Start Guide](docs/QUICK_START.md) - **Start here!**
- [Contributing](CONTRIBUTING.md)

---

**Stop building scaffolding. Start building features.**
