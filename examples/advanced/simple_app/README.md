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

engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="mdb_runtime",
)

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

## Key Features Demonstrated

- **`engine.create_app()`** - Automatic initialization, manifest loading, and cleanup
- **Scoped Database Access** - All queries automatically filtered by app_id
- **Manifest-Driven Indexes** - Indexes auto-created from manifest.json
- **Optional Ray Support** - Enable with `ENABLE_RAY=true`

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

## Optional Ray Support

Enable Ray for distributed processing:

```python
engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="mdb_runtime",
    enable_ray=True,  # Enable Ray
)
```

Or via environment variable:

```bash
ENABLE_RAY=true docker-compose up
```

Check Ray status:

```bash
curl http://localhost:8000/api/status
```

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

