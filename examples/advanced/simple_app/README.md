# Simple App Example

A minimal task management app demonstrating the **unified MongoDBEngine pattern** with `create_app()` for automatic lifecycle management.

## When to Use This Pattern

| Use `create_app()` when... | Use manual pattern when... |
|---------------------------|---------------------------|
| Building a new FastAPI app | Adding to existing FastAPI app |
| Want zero boilerplate | Need custom middleware order |
| Single manifest per app | Complex multi-stage startup |
| Standard lifecycle is fine | Need fine-grained control |

**Bottom line:** Start with `create_app()`. Only switch to manual if you hit a specific limitation.

## Overview

This example showcases the recommended way to build FastAPI apps with MDB Engine:

```python
from mdb_engine import MongoDBEngine
from pathlib import Path

engine = MongoDBEngine()  # reads MONGODB_URI from env, defaults to localhost

# One line - automatic lifecycle management
app = engine.create_app(
    slug="simple_app",
    manifest=Path("manifest.json"),
)

@app.get("/items")
async def get_items():
    db = engine.get_scoped_db("simple_app")
    return await db.items.find({}).to_list(10)
```

> **Tip:** For the absolute simplest start (no manifest), use `quickstart()`:
> ```python
> from mdb_engine import quickstart
> app = quickstart("simple_app")
> ```

## Key Features Demonstrated

- **`engine.create_app()`** - Automatic initialization, manifest loading, and cleanup
- **Scoped Database Access** - All queries automatically filtered by app_id
- **Manifest-Driven Indexes** - Indexes auto-created from manifest.json

## Quick Start

### With Docker Compose (Recommended)

```bash
cd examples/simple_app
docker-compose up --build
```

Open http://localhost:8000

### Without Docker

```bash
# Start MongoDB
mongod --dbpath /tmp/mongodb

# Install dependencies
pip install -r requirements.txt

# Run the app
uvicorn web:app --reload
```

## What `create_app()` Does

When you call `engine.create_app()`, it automatically:

1. **On Startup:**
   - Initializes the MongoDBEngine
   - Connects to MongoDB
   - Loads and validates the manifest
   - Registers the app (creates indexes)
   - Auto-retrieves app tokens if needed
   - Exposes engine state on `app.state`

2. **On Shutdown:**
   - Closes MongoDB connections cleanly
   - Cleans up resources

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | HTML task list |
| `/api/tasks` | GET | List all tasks |
| `/api/tasks` | POST | Create a task (requires CSRF token) |
| `/api/tasks/{id}` | PUT | Update a task (requires CSRF token) |
| `/api/tasks/{id}` | DELETE | Delete a task (requires CSRF token) |
| `/api/tasks/{id}/toggle` | POST | Toggle completion (requires CSRF token) |
| `/health` | GET | Health check |
| `/api/status` | GET | Engine status |

**Note:** POST, PUT, and DELETE endpoints require the `X-CSRF-Token` header with the value from the `csrf_token` cookie.

## File Structure

```
simple_app/
├── web.py              # FastAPI app with create_app()
├── manifest.json       # App configuration
├── templates/
│   └── index.html      # Task list UI
├── docker-compose.yml  # Docker orchestration
├── Dockerfile
├── requirements.txt
└── README.md
```

## Code Organization

The `web.py` file is organized into clear sections to help you understand what's MDB-Engine specific vs reusable:

### 1. **Configuration** (Lines ~40-45)
   - Application constants
   - **Reusable**: Works with any framework

### 2. **MDB-Engine Setup** (Lines ~50-85)
   - `MongoDBEngine()` initialization
   - `engine.create_app()` call
   - **MDB-Engine specific**: This is what you'd replace if using a different framework

### 3. **Reusable Components** (Lines ~90-150)
   - Pydantic models (`TaskCreate`, `TaskUpdate`)
   - Business logic functions (`create_task_document()`, `serialize_task()`, etc.)
   - **Reusable**: These work with any MongoDB driver or database backend

### 4. **Routes** (Lines ~155+)
   - FastAPI route handlers
   - **Mixed**: Routes use MDB-Engine dependencies (`get_scoped_db`) but call reusable business logic functions

### Key Insight

The code clearly separates:
- **MDB-Engine specifics**: Engine initialization, `get_scoped_db()` dependency, `engine.create_app()`
- **Reusable logic**: Business functions, data models, query building

This makes it easy to:
1. Understand what MDB-Engine provides (scoped DB access, lifecycle management)
2. Extract reusable components for use in other projects
3. See how to adapt the pattern to other frameworks

## Comparison: Old vs New Pattern

### Old Pattern (Manual Lifecycle)

```python
app = FastAPI()
engine = MongoDBEngine(...)

@app.on_event("startup")
async def startup():
    await engine.initialize()
    manifest = await engine.load_manifest(...)
    await engine.register_app(manifest)

@app.on_event("shutdown")
async def shutdown():
    await engine.shutdown()
```

### New Pattern (Automatic Lifecycle)

```python
engine = MongoDBEngine(...)
app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))
# That's it! 
```

## Learn More

- [Quick Start Guide](../../docs/QUICK_START.md)
- [MDB Engine 101](../../docs/MDB_ENGINE_101.md)
- [Core Module Docs](../../mdb_engine/core/README.md)

