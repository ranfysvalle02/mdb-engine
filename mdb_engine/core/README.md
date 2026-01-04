# Core MongoDB Engine Module

The central orchestration engine for MDB_ENGINE that manages database connections, app registration, manifest validation, index management, and resource lifecycle.

## When to Use What

### Integration Patterns

| Pattern | Best For | Code |
|---------|----------|------|
| **`create_app()`** | New FastAPI apps | `app = engine.create_app(slug, manifest)` |
| **`lifespan()`** | Custom FastAPI setup | `FastAPI(lifespan=engine.lifespan(...))` |
| **Manual** | Existing apps, scripts | `await engine.initialize()` / `await engine.shutdown()` |
| **Context Manager** | Scripts, tests | `async with engine: ...` |

### Feature Activation

| Feature | When to Enable | Configuration |
|---------|---------------|---------------|
| **Ray** | Distributed processing, isolated actors | `enable_ray=True` |
| **Multi-site** | Cross-app data sharing | `read_scopes` in manifest |
| **Auto-indexing** | Let engine optimize queries | Default ON (`auto_index=True`) |
| **App Tokens** | Production security | Set `MDB_ENGINE_MASTER_KEY` |

## Features

- **MongoDBEngine**: Central orchestration for all engine components
- **Manifest System**: JSON schema validation with versioning (v1.0, v2.0)
- **App Registration**: Automatic app setup with isolation and indexing
- **Health Checks**: Built-in health monitoring and status reporting
- **Connection Management**: Efficient MongoDB connection pooling
- **Schema Migration**: Automatic migration between manifest schema versions

## Installation

The core module is part of MDB_ENGINE. No additional installation required.

## Quick Start

### Basic Usage

```python
from mdb_engine import MongoDBEngine

# Initialize engine
engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database"
)

# Initialize connection
await engine.initialize()

# Load and register app from manifest
manifest = await engine.load_manifest("manifest.json")
await engine.register_app(manifest)

# Get scoped database for app
db = engine.get_scoped_db("my_app")
```

## MongoDBEngine

The `MongoDBEngine` class is the central component that orchestrates all engine functionality.

### Initialization

```python
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database",
    manifests_dir=Path("./manifests"),  # Optional
    authz_provider=authz_provider,      # Optional
    max_pool_size=10,                   # Optional
    min_pool_size=1,                    # Optional
    enable_ray=False,                   # Optional: Enable Ray support
    ray_namespace="modular_labs"        # Optional: Ray namespace
)

await engine.initialize()
```

### FastAPI Integration

The simplest way to create a FastAPI app with automatic lifecycle management:

```python
from mdb_engine import MongoDBEngine
from pathlib import Path

engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database"
)

# Create FastAPI app with automatic initialization and cleanup
app = engine.create_app(
    slug="my_app",
    manifest=Path("manifest.json")
)

@app.get("/items")
async def get_items():
    db = engine.get_scoped_db("my_app")
    return await db.items.find({}).to_list(10)
```

This automatically:
- Initializes the engine on startup
- Loads and registers the manifest
- Auto-detects multi-site mode from manifest
- Auto-retrieves app tokens
- Shuts down cleanly on app exit

### Custom Lifespan

For more control, use the `lifespan()` helper:

```python
from fastapi import FastAPI

engine = MongoDBEngine(...)
app = FastAPI(lifespan=engine.lifespan("my_app", Path("manifest.json")))
```

### Get Scoped Database

Get a database wrapper that automatically isolates data by app:

```python
# Basic usage - all operations scoped to "my_app"
db = engine.get_scoped_db("my_app")

# Read from multiple apps, write to one
db = engine.get_scoped_db(
    app_slug="my_app",
    read_scopes=["my_app", "shared_app"],  # Can read from multiple apps
    write_scope="my_app"                    # Write to single app
)

# Disable automatic indexing
db = engine.get_scoped_db("my_app", auto_index=False)
```

### App Registration

Register an app with the MongoDB Engine:

```python
# Load manifest from file
manifest = await engine.load_manifest("manifest.json")

# Register app (creates indexes, sets up auth, etc.)
await engine.register_app(manifest)
```

### Optional Ray Integration

Enable Ray for distributed processing (Ray must be installed):

```python
from mdb_engine import MongoDBEngine

# Enable Ray support
engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database",
    enable_ray=True,
    ray_namespace="my_namespace"
)

await engine.initialize()

# Check if Ray is available
if engine.has_ray:
    print(f"Ray initialized in namespace: {engine.ray_namespace}")
```

Ray integration features:
- Isolated app environments via Ray actors
- Automatic graceful degradation if Ray not installed
- App-specific namespaces for isolation

### Health Checks

Check engine and MongoDB health:

```python
from mdb_engine.observability import check_engine_health, check_mongodb_health

# Check MongoDB connection (mongo_client is for observability only, not data access)
mongodb_status = await check_mongodb_health(engine.mongo_client)
print(mongodb_status)

# ⚠️ SECURITY: engine.mongo_client is for observability/admin operations only
# Always use engine.get_scoped_db() for all data access operations

# Check engine health
engine_status = await check_engine_health(engine)
print(engine_status)
```

### Get Registered Apps

```python
# Get all registered apps
apps = engine.get_registered_apps()
for slug, app_info in apps.items():
    print(f"App: {slug}, Status: {app_info['status']}")
```

## Manifest System

The manifest system provides JSON schema validation, versioning, and migration.

### Manifest Schema Versions

- **v1.0**: Initial schema (default for manifests without version field)
- **v2.0**: Current schema with all features (auth.policy, auth.users, managed_indexes, etc.)

### Manifest Structure

A basic manifest.json:

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My Application",
  "description": "Application description",
  "status": "active",
  "auth_required": true,
  "collections": {
    "users": {
      "indexes": [
        {
          "name": "email_idx",
          "type": "regular",
          "keys": {"email": 1},
          "unique": true
        }
      ]
    }
  }
}
```

### Manifest Validation

```python
from mdb_engine.core import ManifestValidator, validate_manifest

# Using MongoDBEngine
is_valid, error, paths = await engine.validate_manifest(manifest)

# Using validator directly
validator = ManifestValidator()
is_valid, error, paths = validator.validate(manifest)

# Using convenience function
is_valid, error, paths = validate_manifest(manifest)
```

### Manifest Parsing

```python
from mdb_engine.core import ManifestParser

parser = ManifestParser()

# Parse manifest (handles version detection and migration)
parsed = parser.parse(manifest)

# Get schema version
version = parser.get_schema_version(manifest)

# Migrate manifest to target version
migrated = parser.migrate(manifest, target_version="2.0")
```

### Schema Migration

Manifests are automatically migrated to the current schema version:

```python
from mdb_engine.core import migrate_manifest, get_schema_version

# Get current schema version
version = get_schema_version(manifest)  # Returns "1.0" or "2.0"

# Migrate to latest version
migrated = migrate_manifest(manifest)

# Migrate to specific version
migrated = migrate_manifest(manifest, target_version="2.0")
```

### Parallel Manifest Validation

Validate multiple manifests in parallel:

```python
from mdb_engine.core import validate_manifests_parallel

manifests = [
    {"slug": "app1", ...},
    {"slug": "app2", ...},
    {"slug": "app3", ...}
]

results = await validate_manifests_parallel(manifests)
for slug, (is_valid, error, paths) in results.items():
    print(f"{slug}: {'valid' if is_valid else 'invalid'}")
```

### Validation Cache

Validation results are cached for performance:

```python
from mdb_engine.core import clear_validation_cache

# Clear validation cache (useful after schema updates)
clear_validation_cache()
```

## ManifestParser

The `ManifestParser` class handles manifest parsing, version detection, and migration.

### Basic Usage

```python
from mdb_engine.core import ManifestParser

parser = ManifestParser()

# Parse manifest (auto-detects version and migrates if needed)
parsed = parser.parse(manifest)

# Get schema version
version = parser.get_schema_version(manifest)

# Check if migration is needed
needs_migration = parser.needs_migration(manifest)
```

## ManifestValidator

The `ManifestValidator` class provides schema validation with caching.

### Basic Usage

```python
from mdb_engine.core import ManifestValidator

validator = ManifestValidator()

# Validate manifest
is_valid, error_message, error_paths = validator.validate(manifest)

if not is_valid:
    print(f"Validation failed: {error_message}")
    print(f"Error paths: {error_paths}")
```

### Validation with Database

Validate manifest and check against database:

```python
from mdb_engine.core import validate_manifest_with_db

is_valid, error, paths = await validate_manifest_with_db(
    manifest=manifest,
    db=db
)
```

## API Reference

### MongoDBEngine

#### Methods

- `initialize()` - Initialize MongoDB connection and engine
- `get_scoped_db(app_slug, read_scopes=None, write_scope=None, auto_index=True)` - Get scoped database wrapper
- `validate_manifest(manifest)` - Validate manifest against schema
- `load_manifest(path)` - Load and validate manifest from file
- `register_app(manifest)` - Register app with engine
- `get_registered_apps()` - Get all registered apps
- `get_app_info(app_slug)` - Get information about registered app
- `unregister_app(app_slug)` - Unregister an app

**FastAPI Integration Methods:**

- `create_app(slug, manifest, title=None, **fastapi_kwargs)` - Create a FastAPI app with automatic lifecycle management
- `lifespan(slug, manifest)` - Create a lifespan context manager for FastAPI

**App Token Methods:**

- `auto_retrieve_app_token(slug)` - Auto-retrieve app token from environment or database
- `get_app_token(slug)` - Get cached app token for a slug

#### Properties

- `mongo_client` - MongoDB client instance (for observability only)
- `initialized` - Whether engine is initialized
- `has_ray` - Whether Ray is enabled and initialized
- `enable_ray` - Whether Ray support is enabled
- `ray_namespace` - Ray namespace for actor isolation

### ManifestValidator

#### Methods

- `validate(manifest)` - Validate manifest against schema
  - Returns: `(is_valid, error_message, error_paths)`

### ManifestParser

#### Methods

- `parse(manifest)` - Parse manifest (auto-migrates if needed)
- `get_schema_version(manifest)` - Get schema version of manifest
- `needs_migration(manifest)` - Check if migration is needed
- `migrate(manifest, target_version=None)` - Migrate manifest to target version

### Functions

- `validate_manifest(manifest)` - Convenience function for validation
- `validate_manifest_with_db(manifest, db)` - Validate with database checks
- `validate_managed_indexes(manifest)` - Validate managed index definitions
- `validate_index_definition(index_def, index_name)` - Validate single index definition
- `get_schema_version(manifest)` - Get schema version
- `migrate_manifest(manifest, target_version=None)` - Migrate manifest
- `get_schema_for_version(version)` - Get schema definition for version
- `clear_validation_cache()` - Clear validation cache
- `validate_manifests_parallel(manifests)` - Validate multiple manifests in parallel

## Configuration

### Environment Variables

```bash
# MongoDB connection pool settings
export MONGO_ACTOR_MAX_POOL_SIZE=10
export MONGO_ACTOR_MIN_POOL_SIZE=1

# Server selection timeout
export MONGO_SERVER_SELECTION_TIMEOUT_MS=5000
```

### MongoDBEngine Parameters

- `mongo_uri` (required): MongoDB connection URI
- `db_name` (required): Database name
- `manifests_dir` (optional): Directory containing manifest files
- `authz_provider` (optional): Authorization provider instance
- `max_pool_size` (optional): Maximum connection pool size (default: 10)
- `min_pool_size` (optional): Minimum connection pool size (default: 1)
- `enable_ray` (optional): Enable Ray support for distributed processing (default: False)
- `ray_namespace` (optional): Ray namespace for actor isolation (default: "modular_labs")

## Manifest Schema Features

### Collections

Define collections with indexes:

```json
{
  "collections": {
    "users": {
      "indexes": [
        {
          "name": "email_idx",
          "type": "regular",
          "keys": {"email": 1},
          "unique": true
        }
      ]
    }
  }
}
```

### Authentication & Authorization

Configure unified authentication and authorization:

```json
{
  "auth_required": true,
  "auth_policy": {
    "provider": "casbin",
    "required": true,
    "allow_anonymous": false,
    "authorization": {
      "model": "rbac",
      "policies_collection": "casbin_policies",
      "link_users_roles": true,
      "default_roles": ["user", "admin"]
    }
  }
}
```

**Key Features:**
- **Auto-created Provider:** Casbin provider automatically created from manifest (default)
- **MongoDB-backed:** Policies stored in MongoDB collection
- **App-Level User Management:** App-level users automatically get Casbin roles
- **Extensible:** Supports custom providers, models, and manual setup

**Extensibility:**
- Use `"provider": "oso"` for OSO/Polar-based authorization
- Use `"provider": "custom"` and set `app.state.authz_provider` manually
- Provide custom Casbin model files via `authorization.model` path
- Implement `AuthorizationProvider` protocol for fully custom authorization logic

### App-Level User Management

Configure app-level user management:

```json
{
  "auth": {
    "users": {
      "strategy": "app_users",
      "allow_registration": true,
      "demo_users": [
        {
          "email": "demo@example.com",
          "password": "demo123",
          "role": "user"
        }
      ]
    }
  }
}
```

### Indexes

Define various index types:

```json
{
  "collections": {
    "documents": {
      "indexes": [
        {
          "name": "text_search",
          "type": "text",
          "keys": {"title": "text", "content": "text"}
        },
        {
          "name": "vector_search",
          "type": "vector_search",
          "definition": {
            "fields": [{
              "type": "vector",
              "path": "embedding",
              "numDimensions": 1536
            }]
          }
        }
      ]
    }
  }
}
```

## Best Practices

1. **Always validate manifests** - Use `validate_manifest()` before registration
2. **Use schema versioning** - Specify `schema_version` in manifests
3. **Initialize once** - Create one MongoDBEngine instance per application
4. **Use scoped databases** - Always use `get_scoped_db()` for data isolation
5. **Monitor health** - Regularly check engine and MongoDB health
6. **Cache validation** - Validation results are cached automatically
7. **Handle errors** - Wrap initialization in try/except blocks
8. **Clean up** - Unregister apps when no longer needed

## Error Handling

```python
from mdb_engine.exceptions import InitializationError

from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

try:
    await engine.initialize()
except InitializationError as e:
    print(f"Initialization failed: {e}")
    print(f"MongoDB URI: {e.mongo_uri}")
except (ConnectionFailure, ServerSelectionTimeoutError) as e:
    print(f"MongoDB connection error: {e}")
```

## Integration Examples

### FastAPI Integration

```python
from fastapi import FastAPI
from mdb_engine import MongoDBEngine

app = FastAPI()
engine = None

@app.on_event("startup")
async def startup():
    global engine
    engine = MongoDBEngine(mongo_uri="...", db_name="...")
    await engine.initialize()

    manifest = await engine.load_manifest("manifest.json")
    await engine.register_app(manifest)

@app.get("/data")
async def get_data():
    db = engine.get_scoped_db("my_app")
    docs = await db.collection.find({}).to_list(length=10)
    return {"data": docs}
```

### Multiple Apps

```python
# Register multiple apps
manifests = [
    await engine.load_manifest("app1/manifest.json"),
    await engine.load_manifest("app2/manifest.json"),
    await engine.load_manifest("app3/manifest.json")
]

for manifest in manifests:
    await engine.register_app(manifest)

# Access different apps
db1 = engine.get_scoped_db("app1")
db2 = engine.get_scoped_db("app2")
```

## Related Modules

- **`database/`** - Database abstraction and scoped wrappers
- **`auth/`** - Authentication and authorization
- **`indexes/`** - Index management
- **`observability/`** - Health checks and metrics
