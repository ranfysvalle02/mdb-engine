# MDB-Engine Components in Simple App

This document isolates the MDB-Engine specific parts of the Simple App example, explains their value, and shows how to implement your own versions.

## Overview

The Simple App example demonstrates the core MDB-Engine features:
- **Automatic database connection management** - No manual connection pooling or lifecycle handling
- **Zero-boilerplate FastAPI setup** - One line creates a fully configured app
- **Manifest-driven configuration** - Indexes, CORS, and settings from JSON
- **Automatic data scoping** - All queries automatically filtered by app_id

**Without MDB-Engine**, you would need to implement:
- MongoDB connection pooling and lifecycle management
- FastAPI startup/shutdown event handlers
- Manual index creation and management
- Database query scoping logic
- Configuration loading and validation

## MDB-Engine Components

### 1. MongoDBEngine Initialization

**What it is:**

```python
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(
    mongo_uri=os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
    db_name=os.getenv("MONGODB_DB", "mdb_runtime"),
)
```

**What it does:**

- Creates a connection pool to MongoDB
- Manages connection lifecycle (connect, reconnect, close)
- Handles connection errors and retries
- Stores connection configuration for later use

**Value:**

- **Connection pooling**: Automatically manages connection pool size and lifecycle
- **Error handling**: Built-in retry logic and connection recovery
- **Resource management**: Proper cleanup on shutdown

**How to implement your own:**

```python
from motor.motor_asyncio import AsyncIOMotorClient
from contextlib import asynccontextmanager

class DatabaseManager:
    def __init__(self, mongo_uri: str, db_name: str):
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.client = None
        self.db = None
    
    async def connect(self):
        """Establish MongoDB connection."""
        self.client = AsyncIOMotorClient(self.mongo_uri)
        self.db = self.client[self.db_name]
        # Test connection
        await self.client.admin.command('ping')
    
    async def disconnect(self):
        """Close MongoDB connection."""
        if self.client:
            self.client.close()
    
    def get_db(self):
        """Get database instance."""
        if not self.db:
            raise RuntimeError("Database not connected")
        return self.db

# Usage
db_manager = DatabaseManager(
    mongo_uri=os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
    db_name=os.getenv("MONGODB_DB", "mdb_runtime"),
)

@asynccontextmanager
async def lifespan(app):
    # Startup
    await db_manager.connect()
    yield
    # Shutdown
    await db_manager.disconnect()
```

**Comparison:**

| Feature | MDB-Engine | Custom Implementation |
|---------|-----------|----------------------|
| Lines of code | 4 | ~30+ |
| Connection pooling | Automatic | Manual |
| Error handling | Built-in | Manual |
| Lifecycle management | Automatic | Manual event handlers |

---

### 2. engine.create_app()

**What it is:**

```python
app = engine.create_app(
    slug=APP_SLUG,
    manifest=Path(__file__).parent / "manifest.json",
    title="Simple App Example",
    description="A simple task management app",
    version="1.0.0",
)
```

**What it does:**

- Creates a FastAPI app instance
- Loads and validates manifest.json configuration
- Creates database indexes from `managed_indexes` in manifest
- Configures CORS from manifest settings
- Sets up startup/shutdown lifecycle
- Registers the app with the engine
- Returns a fully configured FastAPI app

**Value:**

- **Zero boilerplate**: One line replaces ~50+ lines of setup code
- **Manifest-driven**: Configuration in JSON, not code
- **Automatic indexes**: Indexes created from manifest, no manual DDL
- **Lifecycle management**: Startup/shutdown handled automatically
- **Type safety**: Validated configuration prevents runtime errors

**How to implement your own:**

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import json
from pathlib import Path

async def create_indexes(db, manifest):
    """Create indexes from manifest configuration."""
    managed_indexes = manifest.get("managed_indexes", {})
    for collection_name, indexes in managed_indexes.items():
        collection = db[collection_name]
        for index_def in indexes:
            await collection.create_index(
                list(index_def["keys"].items()),
                name=index_def["name"]
            )

async def load_manifest(manifest_path: Path):
    """Load and validate manifest."""
    with open(manifest_path) as f:
        manifest = json.load(f)
    # Validate schema_version, slug, etc.
    return manifest

@asynccontextmanager
async def lifespan(app):
    # Startup
    manifest = await load_manifest(Path("manifest.json"))
    await db_manager.connect()
    await create_indexes(db_manager.get_db(), manifest)
    yield
    # Shutdown
    await db_manager.disconnect()

def create_app(slug: str, manifest_path: Path, title: str):
    """Create FastAPI app with manual setup."""
    manifest = json.load(open(manifest_path))
    
    app = FastAPI(
        title=title,
        lifespan=lifespan
    )
    
    # Configure CORS from manifest
    # NOTE: MDB Engine handles CORS automatically via manifest.json
    # This example shows manual setup for reference only
    cors_config = manifest.get("cors", {})
    if cors_config.get("enabled", False):
        allow_origins = cors_config.get("allow_origins", ["http://localhost:3000"])
        allow_credentials = cors_config.get("allow_credentials", False)
        
        # Security: Never use wildcard with credentials
        if "*" in allow_origins and allow_credentials:
            raise ValueError(
                "Cannot use wildcard origins (*) with allow_credentials=true. "
                "Use specific origins instead."
            )
        
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allow_origins,
            allow_credentials=allow_credentials,
            allow_methods=cors_config.get("allow_methods", ["*"]),
            allow_headers=cors_config.get("allow_headers", ["*"]),
        )
    
    return app

# Usage
app = create_app(
    slug="simple_app",
    manifest_path=Path("manifest.json"),
    title="Simple App Example",
)
```

**Comparison:**

| Feature | MDB-Engine | Custom Implementation |
|---------|-----------|----------------------|
| Lines of code | 1 | ~80+ |
| Manifest loading | Automatic | Manual JSON parsing |
| Index creation | Automatic | Manual DDL code |
| CORS setup | From manifest | Manual middleware |
| Lifecycle | Automatic | Manual lifespan handler |
| Validation | Built-in | Manual checks |

---

### 3. get_scoped_db() Dependency

**What it is:**

```python
from mdb_engine.dependencies import get_scoped_db

@app.get("/api/tasks")
async def list_tasks(db=Depends(get_scoped_db)):
    tasks = await db.tasks.find({}).to_list(length=100)
    return {"tasks": tasks}
```

**What it does:**

- Provides a database connection scoped to the current app
- Automatically filters all queries by `app_id` field
- Ensures data isolation between different apps
- Handles connection pooling and error recovery
- Works as a FastAPI dependency (injected automatically)

**Value:**

- **Data isolation**: Prevents accidental cross-app data access
- **Security**: Automatic filtering prevents data leaks
- **Simplicity**: No manual filtering needed in queries
- **Consistency**: All queries automatically scoped
- **Type safety**: Database operations are type-checked

**How to implement your own:**

```python
from fastapi import Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import Annotated

APP_SLUG = "simple_app"

class ScopedDatabase:
    """Database wrapper that automatically scopes queries."""
    
    def __init__(self, db: AsyncIOMotorDatabase, app_id: str):
        self._db = db
        self.app_id = app_id
    
    def __getattr__(self, name):
        """Proxy collection access with automatic scoping."""
        collection = getattr(self._db, name)
        return ScopedCollection(collection, self.app_id)

class ScopedCollection:
    """Collection wrapper that adds app_id filter."""
    
    def __init__(self, collection, app_id: str):
        self._collection = collection
        self.app_id = app_id
    
    def find(self, filter=None, **kwargs):
        """Add app_id to filter automatically."""
        if filter is None:
            filter = {}
        filter["app_id"] = self.app_id
        return self._collection.find(filter, **kwargs)
    
    def insert_one(self, document, **kwargs):
        """Add app_id to document automatically."""
        document["app_id"] = self.app_id
        return self._collection.insert_one(document, **kwargs)
    
    def update_one(self, filter, update, **kwargs):
        """Add app_id to filter automatically."""
        if filter is None:
            filter = {}
        filter["app_id"] = self.app_id
        return self._collection.update_one(filter, update, **kwargs)
    
    def delete_one(self, filter, **kwargs):
        """Add app_id to filter automatically."""
        if filter is None:
            filter = {}
        filter["app_id"] = self.app_id
        return self._collection.delete_one(filter, **kwargs)

async def get_scoped_db() -> ScopedDatabase:
    """FastAPI dependency for scoped database access."""
    db = db_manager.get_db()
    return ScopedDatabase(db, APP_SLUG)

# Usage
@app.get("/api/tasks")
async def list_tasks(db: Annotated[ScopedDatabase, Depends(get_scoped_db)]):
    # Query automatically filtered by app_id
    tasks = await db.tasks.find({}).to_list(length=100)
    return {"tasks": tasks}
```

**Comparison:**

| Feature | MDB-Engine | Custom Implementation |
|---------|-----------|----------------------|
| Lines of code | 1 import | ~60+ |
| Automatic scoping | Yes | Manual wrapper classes |
| Query filtering | Transparent | Explicit filter addition |
| Insert scoping | Automatic | Manual app_id addition |
| Error handling | Built-in | Manual |
| Type hints | Full support | Manual annotations |

---

### 4. Manifest-Driven Index Creation

**What it is:**

In `manifest.json`:

```json
{
  "managed_indexes": {
    "tasks": [
      {
        "type": "regular",
        "keys": {"created_at": -1},
        "name": "tasks_created_at_idx"
      },
      {
        "type": "regular",
        "keys": {"completed": 1},
        "name": "tasks_completed_idx"
      }
    ]
  }
}
```

**What it does:**

- Reads index definitions from manifest.json
- Creates indexes automatically on app startup
- Validates index definitions before creation
- Handles index creation errors gracefully
- Supports regular indexes and vector indexes
- Idempotent (safe to run multiple times)

**Value:**

- **Declarative**: Indexes defined in configuration, not code
- **Version controlled**: Index changes tracked in git
- **Automatic**: No manual DDL scripts needed
- **Safe**: Validated before creation
- **Consistent**: Same indexes in dev/staging/prod

**How to implement your own:**

```python
async def create_indexes_from_manifest(db, manifest_path: Path):
    """Create indexes from manifest configuration."""
    with open(manifest_path) as f:
        manifest = json.load(f)
    
    managed_indexes = manifest.get("managed_indexes", {})
    
    for collection_name, indexes in managed_indexes.items():
        collection = db[collection_name]
        
        for index_def in indexes:
            index_type = index_def.get("type", "regular")
            index_name = index_def.get("name")
            keys = index_def.get("keys", {})
            
            if index_type == "regular":
                # Convert keys dict to list of tuples
                key_list = [(k, v) for k, v in keys.items()]
                
                try:
                    # Check if index already exists
                    existing_indexes = await collection.list_indexes().to_list()
                    index_names = [idx["name"] for idx in existing_indexes]
                    
                    if index_name not in index_names:
                        await collection.create_index(
                            key_list,
                            name=index_name
                        )
                        print(f"Created index {index_name} on {collection_name}")
                    else:
                        print(f"Index {index_name} already exists")
                except Exception as e:
                    print(f"Error creating index {index_name}: {e}")
            elif index_type == "vector":
                # Vector index creation (MongoDB Atlas specific)
                # Requires additional configuration
                pass

# Usage in lifespan
@asynccontextmanager
async def lifespan(app):
    await db_manager.connect()
    await create_indexes_from_manifest(
        db_manager.get_db(),
        Path("manifest.json")
    )
    yield
    await db_manager.disconnect()
```

**Comparison:**

| Feature | MDB-Engine | Custom Implementation |
|---------|-----------|----------------------|
| Lines of code | 0 (in manifest) | ~40+ |
| Index definition | JSON config | Python code |
| Validation | Automatic | Manual |
| Error handling | Built-in | Manual try/except |
| Idempotency | Automatic | Manual checks |
| Vector indexes | Supported | Manual Atlas API calls |

---

## Complete Replacement Guide

### Step 1: Replace MongoDBEngine

**Remove:**
```python
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(...)
```

**Replace with:**
```python
from motor.motor_asyncio import AsyncIOMotorClient

class DatabaseManager:
    # ... (implementation from section 1)
```

### Step 2: Replace engine.create_app()

**Remove:**
```python
app = engine.create_app(
    slug=APP_SLUG,
    manifest=Path(__file__).parent / "manifest.json",
    ...
)
```

**Replace with:**
```python
app = FastAPI(title="Simple App Example")

@asynccontextmanager
async def lifespan(app):
    await db_manager.connect()
    manifest = await load_manifest(Path("manifest.json"))
    await create_indexes_from_manifest(db_manager.get_db(), manifest)
    yield
    await db_manager.disconnect()

app.router.lifespan_context = lifespan
```

### Step 3: Replace get_scoped_db()

**Remove:**
```python
from mdb_engine.dependencies import get_scoped_db

@app.get("/api/tasks")
async def list_tasks(db=Depends(get_scoped_db)):
    ...
```

**Replace with:**
```python
async def get_scoped_db():
    db = db_manager.get_db()
    return ScopedDatabase(db, APP_SLUG)

@app.get("/api/tasks")
async def list_tasks(db=Depends(get_scoped_db)):
    # Now using custom ScopedDatabase
    ...
```

### Step 4: Update All Queries

**MDB-Engine (automatic scoping):**
```python
tasks = await db.tasks.find({}).to_list(length=100)
```

**Custom (manual scoping):**
```python
tasks = await db.tasks.find({"app_id": APP_SLUG}).to_list(length=100)
```

Or use the ScopedDatabase wrapper from section 3.

---

## Migration Guide

### Migrating FROM MDB-Engine TO Custom Implementation

1. **Replace imports:**
   ```python
   # Remove
   from mdb_engine import MongoDBEngine
   from mdb_engine.dependencies import get_scoped_db
   
   # Add
   from motor.motor_asyncio import AsyncIOMotorClient
   ```

2. **Add DatabaseManager class** (from section 1)

3. **Replace engine initialization** (from section 1)

4. **Replace create_app()** (from section 2)

5. **Replace get_scoped_db()** (from section 3)

6. **Update all routes** to use custom dependency

7. **Add manual index creation** (from section 4)

8. **Add app_id to all inserts:**
   ```python
   # Before (automatic)
   await db.tasks.insert_one(task_doc)
   
   # After (manual)
   task_doc["app_id"] = APP_SLUG
   await db.tasks.insert_one(task_doc)
   ```

### Migrating FROM Custom Implementation TO MDB-Engine

1. **Remove custom database code:**
   - Remove DatabaseManager class
   - Remove ScopedDatabase/ScopedCollection classes
   - Remove manual index creation code

2. **Add MDB-Engine:**
   ```python
   from mdb_engine import MongoDBEngine
   from mdb_engine.dependencies import get_scoped_db
   
   engine = MongoDBEngine(...)
   app = engine.create_app(...)
   ```

3. **Update routes:**
   ```python
   # Remove manual app_id filtering
   # MDB-Engine handles it automatically
   ```

4. **Move index definitions to manifest.json**

5. **Remove app_id from inserts** (handled automatically)

---

## Summary

MDB-Engine provides significant value by eliminating boilerplate and handling complex infrastructure concerns:

| Component | Lines Saved | Complexity Reduced |
|-----------|-------------|-------------------|
| MongoDBEngine | ~30 lines | Connection pooling, error handling |
| create_app() | ~80 lines | Lifecycle, indexes, CORS |
| get_scoped_db() | ~60 lines | Query scoping, data isolation |
| Manifest indexes | ~40 lines | DDL management, validation |
| **Total** | **~210 lines** | **Significant complexity reduction** |

The custom implementations shown above are simplified versions. Production-ready implementations would need additional error handling, logging, monitoring, and edge case handling that MDB-Engine provides out of the box.
