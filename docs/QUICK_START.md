# MDB_RUNTIME Quick Start Guide

## Installation

```bash
pip install mdb-runtime
```

Or install from source:

```bash
pip install -e .
```

## Basic Usage

### 1. Initialize the Runtime Engine

```python
from mdb_runtime import RuntimeEngine
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
from mdb_runtime.database import ScopedMongoWrapper, AppDB

# Authentication
from mdb_runtime.auth import get_current_user, require_admin

# Manifest validation
from mdb_runtime.core import ManifestValidator

validator = ManifestValidator()
is_valid, error, paths = validator.validate(manifest)

# Index management
from mdb_runtime.indexes import AsyncAtlasIndexManager
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


## Observability

```python
# Health checks
health = await engine.get_health_status()
print(health["status"])  # "healthy", "degraded", "unhealthy"

# Metrics
metrics = engine.get_metrics()
print(metrics["summary"])

# Structured logging with correlation IDs
from mdb_runtime.observability import get_logger, set_correlation_id

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
pytest --cov=mdb_runtime --cov-report=html
```

## Package Structure

```
mdb_runtime/
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

