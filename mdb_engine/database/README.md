# Database Module

Database abstraction layer providing app-scoped database access with automatic data isolation, connection pooling, and MongoDB-style API.

## Features

- **Scoped Database Wrappers**: Automatic app-level data isolation
- **Connection Pooling**: Shared MongoDB connection pool for efficiency
- **MongoDB-Style API**: Familiar Motor/pymongo API with automatic scoping
- **AutoIndexManager**: Automatic index creation based on query patterns
- **AsyncAtlasIndexManager**: Async-native interface for Atlas Search/Vector indexes
- **AppDB Abstraction**: High-level database abstraction for FastAPI

## Installation

The database module is part of MDB_ENGINE. No additional installation required.

## Quick Start

### Basic Usage

```python
from mdb_engine.database import ScopedMongoWrapper
from mdb_engine.core import MongoDBEngine

# Get scoped database from engine
engine = MongoDBEngine(mongo_uri="...", db_name="...")
await engine.initialize()

db = engine.get_scoped_db("my_app")

# All queries automatically scoped to "my_app"
docs = await db.my_collection.find({"status": "active"}).to_list(length=10)
await db.my_collection.insert_one({"name": "Test", "status": "active"})
```

## ScopedMongoWrapper

The `ScopedMongoWrapper` provides automatic app-level data isolation. All read operations are automatically filtered by `app_id`, and all write operations automatically include `app_id`.

### Basic Usage

```python
from mdb_engine.database import ScopedMongoWrapper
from motor.motor_asyncio import AsyncIOMotorDatabase

# Create scoped wrapper
db = ScopedMongoWrapper(
    real_db=mongo_db,
    read_scopes=["my_app"],      # Can read from these apps
    write_scope="my_app",        # Write to this app
    auto_index=True              # Enable automatic indexing
)

# Access collections (MongoDB-style)
collection = db.my_collection

# All operations automatically scoped
doc = await collection.find_one({"name": "test"})
docs = await collection.find({"status": "active"}).to_list(length=10)
await collection.insert_one({"name": "new_doc"})
```

### Cross-App Data Access

Read from multiple apps while writing to one:

```python
# Read from multiple apps, write to one
db = ScopedMongoWrapper(
    real_db=mongo_db,
    read_scopes=["app1", "app2", "shared"],  # Can read from these
    write_scope="app1"                        # Write to this one
)

# Queries will search across app1, app2, and shared
docs = await db.collection.find({}).to_list(length=100)
```

## ScopedCollectionWrapper

The `ScopedCollectionWrapper` automatically injects `app_id` filters into all read operations and adds `app_id` to all write operations.

### Read Operations

All read operations are automatically scoped:

```python
collection = db.my_collection

# find_one - automatically filters by app_id
doc = await collection.find_one({"name": "test"})
# Equivalent to: find_one({"name": "test", "app_id": {"$in": read_scopes}})

# find - automatically filters by app_id
cursor = collection.find({"status": "active"})
docs = await cursor.to_list(length=10)

# count_documents - automatically filters by app_id
count = await collection.count_documents({"status": "active"})

# aggregate - automatically filters by app_id
pipeline = [{"$match": {"status": "active"}}, {"$group": {"_id": "$category"}}]
results = await collection.aggregate(pipeline).to_list(length=100)
```

### Write Operations

All write operations automatically include `app_id`:

```python
# insert_one - automatically adds app_id
result = await collection.insert_one({"name": "New Document"})
# Document stored as: {"name": "New Document", "app_id": "my_app"}

# insert_many - automatically adds app_id to each document
result = await collection.insert_many([
    {"name": "Doc 1"},
    {"name": "Doc 2"}
])

# update_one/update_many - automatically filters by app_id
result = await collection.update_one(
    {"name": "test"},
    {"$set": {"status": "updated"}}
)

# delete_one/delete_many - automatically filters by app_id
result = await collection.delete_one({"name": "test"})
```

### Direct Collection Access

Access the underlying unscoped collection for administrative operations:

```python
# Get unscoped collection (bypasses app_id filtering)
real_collection = collection._collection

# Use for admin operations (use with caution!)
await real_collection.create_index([("field", 1)])
```

## AutoIndexManager

Automatic index creation based on query patterns. Enabled by default for all collections.

### How It Works

The `AutoIndexManager` monitors query patterns and automatically creates indexes to optimize performance:

```python
# AutoIndexManager is enabled by default
db = ScopedMongoWrapper(real_db=mongo_db, read_scopes=["my_app"])

# First query - no index yet
docs = await db.collection.find({"status": "active"}).to_list(length=10)

# AutoIndexManager detects the query pattern and creates an index
# Subsequent queries are optimized automatically
```

### Disable Auto-Indexing

```python
# Disable automatic indexing
db = ScopedMongoWrapper(
    real_db=mongo_db,
    read_scopes=["my_app"],
    auto_index=False  # Disable automatic indexing
)
```

## AsyncAtlasIndexManager

Async-native interface for managing Atlas Search and Vector indexes.

### Basic Usage

```python
from mdb_engine.database import AsyncAtlasIndexManager

# Get index manager from collection (automatically uses unscoped collection)
collection = db.my_collection
index_manager = collection.index_manager  # Secure way to access index manager

# Create vector search index
await index_manager.create_vector_search_index(
    name="vector_idx",
    definition={
        "fields": [{
            "type": "vector",
            "path": "embedding",
            "numDimensions": 1536,
            "similarity": "cosine"
        }]
    },
    wait_for_ready=True
)

# Create search index
await index_manager.create_search_index(
    name="text_search",
    definition={
        "mappings": {
            "dynamic": False,
            "fields": {
                "title": {"type": "string"},
                "content": {"type": "string"}
            }
        }
    },
    wait_for_ready=True
)

# List indexes
indexes = await index_manager.list_search_indexes()

# Get index status
index_info = await index_manager.get_search_index("vector_idx")

# Drop index
await index_manager.drop_search_index("vector_idx", wait_for_drop=True)
```

### Vector Search Index

```python
# Create vector search index
await index_manager.create_vector_search_index(
    name="embeddings_idx",
    definition={
        "fields": [{
            "type": "vector",
            "path": "embedding",
            "numDimensions": 1536,
            "similarity": "cosine"
        }]
    }
)

# Update vector index
await index_manager.update_search_index(
    name="embeddings_idx",
    definition={
        "fields": [{
            "type": "vector",
            "path": "embedding",
            "numDimensions": 3072,  # Updated dimensions
            "similarity": "dotProduct"
        }]
    }
)
```

### Search Index

```python
# Create Lucene search index
await index_manager.create_search_index(
    name="full_text_search",
    definition={
        "mappings": {
            "dynamic": False,
            "fields": {
                "title": {
                    "type": "string",
                    "analyzer": "lucene.standard"
                },
                "content": {
                    "type": "string",
                    "analyzer": "lucene.english"
                }
            }
        }
    }
)
```

## Security Features

The database module includes comprehensive security controls to prevent unauthorized access, NoSQL injection, resource exhaustion, and ensure data isolation:

### Query Security

All queries are automatically validated for security:
- **Dangerous operator blocking**: Blocks `$where`, `$eval`, `$function`, and `$accumulator` operators that allow JavaScript execution
- **Query depth limits**: Prevents deeply nested queries that could cause performance issues
- **Regex complexity limits**: Prevents ReDoS (Regular Expression Denial of Service) attacks
- **Pipeline validation**: Validates aggregation pipelines for safety and complexity

```python
# These queries will be blocked:
db.collection.find({"$where": "this.status === 'active'"})  # ❌ Dangerous operator
db.collection.aggregate([{"$match": {}}] * 100)  # ❌ Too many pipeline stages

# These queries are safe:
db.collection.find({"status": "active"})  # ✅ Safe
db.collection.find({"age": {"$gt": 18}})  # ✅ Safe
```

### Resource Limits

All operations have automatic resource limits to prevent resource exhaustion:
- **Query timeouts**: All queries automatically have `maxTimeMS` set (default: 30 seconds, max: 5 minutes)
- **Result size limits**: Maximum 10,000 documents per query (configurable)
- **Batch size limits**: Maximum 1,000 documents per cursor batch
- **Document size validation**: Documents are validated before insert (16MB MongoDB limit)

```python
# Timeouts are automatically enforced:
db.collection.find({"status": "active"})  # Automatically has maxTimeMS=30000

# Result limits are enforced:
db.collection.find({}, limit=20000)  # Automatically capped to 10,000

# Document sizes are validated:
large_doc = {"data": "x" * (20 * 1024 * 1024)}  # ❌ Exceeds 16MB limit
await db.collection.insert_one(large_doc)  # Raises ResourceLimitExceeded
```

### Collection Name Validation

All collection names are validated for security:

### Collection Name Validation

All collection names are validated for security:
- **Format validation**: Must match MongoDB naming rules (alphanumeric, underscore, dot, hyphen)
- **Length limits**: 1-255 characters
- **Reserved names**: System collections (`apps_config`) are blocked
- **Reserved prefixes**: Collections starting with `system`, `admin`, `config`, or `local` are blocked
- **Path traversal protection**: Blocks attempts to use `..`, `/`, or `\` in collection names

```python
# These will raise ValueError:
db.system_users  # Reserved prefix
db.apps_config    # Reserved name
db["../other"]    # Path traversal attempt
db["123invalid"] # Invalid format (starts with number)
```

### Cross-App Access Control

Cross-app collection access is strictly controlled:
- Apps can only read from collections of apps listed in their `read_scopes`
- Unauthorized cross-app access attempts are logged and blocked
- All cross-app access is logged for audit purposes

```python
# App can only read from authorized apps
db = engine.get_scoped_db(
    "my_app",
    read_scopes=["my_app", "shared_app"]  # Can read from these apps
)

# This works (authorized):
collection = db.get_collection("shared_app_data")

# This fails (unauthorized):
collection = db.get_collection("other_app_data")  # Raises ValueError
```

### Scope Validation

The `get_scoped_db()` method validates all scopes:
- `read_scopes` must be a non-empty list of valid app slugs
- `write_scope` must be a non-empty string
- Invalid scopes raise `ValueError` with clear error messages

### Audit Logging

All security-relevant events are logged:
- Invalid collection name attempts
- Unauthorized cross-app access attempts
- Reserved name/prefix access attempts
- Collection name validation failures

Logs include app context (app_slug, collection_name, action) for security monitoring.

### Best Practices

1. **Always use scoped databases**: Never access raw MongoDB clients or databases
2. **Validate collection names**: Use descriptive, valid collection names
3. **Limit cross-app access**: Only grant `read_scopes` to apps that need cross-app data
4. **Monitor audit logs**: Review security logs regularly for suspicious patterns
5. **Follow naming conventions**: Use lowercase, underscore-separated names (e.g., `user_profiles`)

## Connection Pooling

The database module provides shared MongoDB connection pooling for efficient resource usage. Connection pooling is handled automatically by the engine - users should always use `engine.get_scoped_db()` for database access.

**Security Note:** Direct MongoDB client creation functions are internal and not part of the public API. Always use scoped databases to ensure proper app isolation.

### Pool Metrics

Monitor connection pool usage:

```python
from mdb_engine.database import get_pool_metrics

# Get pool metrics
metrics = await get_pool_metrics(client)

print(f"Pool size: {metrics['pool_size']}")
print(f"Active connections: {metrics['active_connections']}")
print(f"Available connections: {metrics['available_connections']}")
```

### Verify Connection

```python
from mdb_engine.database import verify_shared_client

# Verify client is connected
is_connected = await verify_shared_client()
if not is_connected:
    print("MongoDB client is not connected")
```

## AppDB Abstraction

High-level database abstraction for FastAPI applications.

### Basic Usage

```python
from mdb_engine.database import AppDB, Collection, get_app_db
from fastapi import Depends

# In FastAPI route
@app.get("/data")
async def get_data(db: AppDB = Depends(get_app_db)):
    # MongoDB-style operations
    doc = await db.my_collection.find_one({"_id": "doc123"})
    docs = await db.my_collection.find({"status": "active"}).to_list(length=10)
    return {"data": docs}
```

### Collection Wrapper

Use the `Collection` wrapper for MongoDB-style API:

```python
from mdb_engine.database import Collection

collection = Collection(db.my_collection)

# MongoDB-style methods
doc = await collection.find_one({"_id": "doc123"})
docs = await collection.find({"status": "active"}).to_list(length=10)
await collection.insert_one({"name": "New Doc"})
count = await collection.count_documents({})
```

## API Reference

### ScopedMongoWrapper

#### Initialization

```python
ScopedMongoWrapper(
    real_db: AsyncIOMotorDatabase,
    read_scopes: List[str],
    write_scope: str,
    auto_index: bool = True
)
```

#### Methods

- `get_collection(name)` - Get scoped collection wrapper
- Collections accessed via attribute (e.g., `db.my_collection`)

### ScopedCollectionWrapper

#### Read Methods

- `find_one(filter, **kwargs)` - Find single document (auto-scoped)
- `find(filter, **kwargs)` - Find documents (auto-scoped)
- `count_documents(filter, **kwargs)` - Count documents (auto-scoped)
- `aggregate(pipeline, **kwargs)` - Aggregate pipeline (auto-scoped)
- `distinct(key, filter=None, **kwargs)` - Distinct values (auto-scoped)

#### Write Methods

- `insert_one(document)` - Insert document (auto-adds app_id)
- `insert_many(documents)` - Insert multiple documents (auto-adds app_id)
- `update_one(filter, update, **kwargs)` - Update document (auto-scoped)
- `update_many(filter, update, **kwargs)` - Update documents (auto-scoped)
- `delete_one(filter, **kwargs)` - Delete document (auto-scoped)
- `delete_many(filter, **kwargs)` - Delete documents (auto-scoped)

#### Properties

- `_collection` - Underlying unscoped collection (for admin operations)
- `index_manager` - AsyncAtlasIndexManager instance

### AsyncAtlasIndexManager

#### Methods

- `create_vector_search_index(name, definition, wait_for_ready=True, timeout=300)` - Create vector index
- `create_search_index(name, definition, wait_for_ready=True, timeout=300)` - Create search index
- `get_search_index(name)` - Get index info
- `list_search_indexes()` - List all indexes
- `update_search_index(name, definition, wait_for_ready=True, timeout=300)` - Update index
- `drop_search_index(name, wait_for_drop=True, timeout=60)` - Drop index

### Connection Pooling

#### Functions

- `get_shared_mongo_client(mongo_uri, max_pool_size=None, min_pool_size=None, ...)` - Get shared client
- `verify_shared_client()` - Verify client connection
- `get_pool_metrics(client=None)` - Get pool metrics
- `register_client_for_metrics(client)` - Register client for metrics
- `close_shared_client()` - Close shared client

### AppDB

#### Functions

- `get_app_db()` - FastAPI dependency for AppDB

## Configuration

### Environment Variables

```bash
# Connection pool settings
export MONGO_ACTOR_MAX_POOL_SIZE=10
export MONGO_ACTOR_MIN_POOL_SIZE=1

# Server selection timeout
export MONGO_SERVER_SELECTION_TIMEOUT_MS=5000
export MONGO_MAX_IDLE_TIME_MS=45000
```

### AutoIndexManager Settings

```python
# Configure auto-indexing thresholds
from mdb_engine.constants import AUTO_INDEX_HINT_THRESHOLD

# Threshold for creating indexes (default: 100 queries)
AUTO_INDEX_HINT_THRESHOLD = 100
```

## Best Practices

1. **Always use scoped wrappers** - Never access unscoped collections directly
2. **Use connection pooling** - Use `get_shared_mongo_client()` for efficiency
3. **Monitor pool metrics** - Regularly check pool usage to prevent exhaustion
4. **Enable auto-indexing** - Let AutoIndexManager optimize queries automatically
5. **Use read_scopes carefully** - Only enable cross-app reads when necessary
6. **Handle errors** - Wrap database operations in try/except blocks
7. **Use transactions** - For multi-document operations requiring consistency
8. **Index management** - Use AsyncAtlasIndexManager for Atlas indexes

## Error Handling

```python
from pymongo.errors import OperationFailure, AutoReconnect

try:
    doc = await collection.find_one({"_id": "doc123"})
except OperationFailure as e:
    print(f"MongoDB operation failed: {e.details}")
except AutoReconnect as e:
    print(f"MongoDB reconnection: {e}")
except (ConnectionFailure, ServerSelectionTimeoutError) as e:
    print(f"Connection error: {e}")
```

## Integration Examples

### FastAPI Integration (Recommended)

Use the request-scoped `get_scoped_db` dependency from `mdb_engine.dependencies`:

```python
from fastapi import Depends
from mdb_engine import MongoDBEngine
from mdb_engine.dependencies import get_scoped_db

engine = MongoDBEngine(mongo_uri="...", db_name="...")
app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))

@app.get("/data")
async def get_data(db=Depends(get_scoped_db)):
    # db is automatically scoped to "my_app"
    docs = await db.my_collection.find({}).to_list(length=10)
    return {"data": docs}

@app.post("/data")
async def create_data(db=Depends(get_scoped_db)):
    # Writes are automatically scoped to "my_app"
    result = await db.my_collection.insert_one({"name": "New Document"})
    return {"inserted_id": str(result.inserted_id)}
```

### Legacy FastAPI Integration

For apps not using `engine.create_app()`:

```python
from fastapi import FastAPI, Depends
from mdb_engine.database import get_app_db, AppDB
from mdb_engine.core import MongoDBEngine

app = FastAPI()
engine = MongoDBEngine(mongo_uri="...", db_name="...")

@app.on_event("startup")
async def startup():
    await engine.initialize()

@app.get("/data")
async def get_data(db: AppDB = Depends(get_app_db)):
    docs = await db.collection.find({}).to_list(length=10)
    return {"data": docs}
```

### Multiple Apps

```python
# Access different apps
db1 = engine.get_scoped_db("app1")
db2 = engine.get_scoped_db("app2")

# Cross-app read (read from app1, write to app2)
shared_db = engine.get_scoped_db(
    app_slug="shared",
    read_scopes=["app1", "app2"],
    write_scope="shared"
)
```

## Related Modules

- **`core/`** - MongoDBEngine for app registration
- **`indexes/`** - Index management orchestration
- **`observability/`** - Metrics and logging
