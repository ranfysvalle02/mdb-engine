# MDB_ENGINE Quick Start Guide

## Installation

```bash
pip install mdb-engine
```

Or install from source:

```bash
pip install -e .
```

## Basic Usage

### 1. Initialize the Runtime Engine

```python
from mdb_engine import RuntimeEngine
from pathlib import Path

# Create engine instance
engine = RuntimeEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database",
    manifests_dir=Path("manifests")
)

# Initialize (async)
await engine.initialize()
```

### 2. Get Scoped Database Access

```python
# Get app-scoped database
db = engine.get_scoped_db("my_app")

# Use MongoDB-style API
doc = await db.my_collection.find_one({"name": "test"})
docs = await db.my_collection.find({"status": "active"}).to_list(length=10)
await db.my_collection.insert_one({"name": "New Doc"})
```

### 3. Register Apps

```python
# Load and validate manifest
manifest = await engine.load_manifest(Path("manifests/my_app/manifest.json"))

# Register app (automatically creates indexes)
await engine.register_app(manifest)

# Or reload all active apps from database
count = await engine.reload_apps()
```

### 4. Use Individual Components

```python
# Database scoping
from mdb_engine.database import ScopedMongoWrapper, AppDB

# Authentication & Authorization
from mdb_engine.auth import (
    setup_auth_from_manifest,
    get_current_user,
    get_authz_provider,
    require_admin
)

# Manifest validation
from mdb_engine.core import ManifestValidator

validator = ManifestValidator()
is_valid, error, paths = validator.validate(manifest)

# Index management
from mdb_engine.indexes import AsyncAtlasIndexManager
```

## Context Manager Usage

```python
# Automatic cleanup
async with RuntimeEngine(mongo_uri, db_name) as engine:
    await engine.reload_apps()
    db = engine.get_scoped_db("my_app")
    # ... use engine
    # Automatic cleanup on exit
```


## Authentication & Authorization

### Unified Auth Setup

Configure authentication and authorization in your `manifest.json`:

```json
{
  "auth_policy": {
    "provider": "casbin",
    "required": true,
    "authorization": {
      "model": "rbac",
      "policies_collection": "casbin_policies",
      "link_sub_auth_roles": true,
      "default_roles": ["user", "admin"]
    }
  },
  "sub_auth": {
    "enabled": true,
    "strategy": "app_users"
  }
}
```

Then in your FastAPI app startup:

```python
from mdb_engine.auth import setup_auth_from_manifest

@app.on_event("startup")
async def startup():
    await engine.initialize()
    await engine.register_app(manifest)
    
    # Auto-creates Casbin provider from manifest
    await setup_auth_from_manifest(app, engine, "my_app")
```

### Using Authorization

```python
from mdb_engine.auth import get_authz_provider, get_current_user
from fastapi import Depends

@app.get("/protected")
async def protected_route(
    user: dict = Depends(get_current_user),
    authz: AuthorizationProvider = Depends(get_authz_provider)
):
    # Check permission using auto-created provider
    has_access = await authz.check(
        subject=user.get("email"),
        resource="my_app",
        action="access"
    )
    if not has_access:
        raise HTTPException(status_code=403, detail="Access denied")
    return {"user_id": user["user_id"]}
```

### Extensibility

**Custom Provider:**
```python
from mdb_engine.auth import AuthorizationProvider

class CustomProvider:
    async def check(self, subject, resource, action, user_object=None):
        # Your custom logic
        return True

app.state.authz_provider = CustomProvider()
```

**OSO Provider:**
```json
{
  "auth_policy": {
    "provider": "oso"
  }
}
```

**Custom Casbin Model:**
```json
{
  "auth_policy": {
    "provider": "casbin",
    "authorization": {
      "model": "/path/to/custom_model.conf"
    }
  }
}
```

## Observability

```python
# Health checks
health = await engine.get_health_status()
print(health["status"])  # "healthy", "degraded", "unhealthy"

# Metrics
metrics = engine.get_metrics()
print(metrics["summary"])

# Structured logging with correlation IDs
from mdb_engine.observability import get_logger, set_correlation_id

correlation_id = set_correlation_id()
logger = get_logger(__name__)
logger.info("Operation completed")  # Includes correlation_id automatically
```

## Testing

Run the test suite:

```bash
# Install test dependencies
pip install -e ".[test]"

# Run all tests
pytest

# Run with coverage
pytest --cov=mdb_engine --cov-report=html
```

## Package Structure

```
mdb_engine/
├── core/              # RuntimeEngine, Manifest validation
├── database/          # Scoped wrappers, AppDB, connection pooling
├── auth/              # Authentication, authorization
├── indexes/           # Index management
├── observability/     # Metrics, logging, health checks
├── utils/             # Utility functions
└── constants.py       # Shared constants
```

## Features

- ✅ **Automatic App Isolation** - All queries automatically scoped
- ✅ **Manifest Validation** - JSON schema validation with versioning
- ✅ **Index Management** - Automatic index creation and management
- ✅ **Observability** - Built-in metrics, logging, and health checks
- ✅ **Type Safety** - Comprehensive type hints
- ✅ **Test Infrastructure** - Full test suite

## Next Steps

- See main [README.md](../README.md) for detailed documentation
- See [Project Structure](../PROJECT_STRUCTURE.md) for organization
- Check [tests/README.md](../tests/README.md) for testing information

