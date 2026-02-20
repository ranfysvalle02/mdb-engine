# MDB_ENGINE Package Documentation

MongoDB Engine - Package-level documentation.

## Package Overview

MDB_ENGINE provides a complete MongoDB Engine for building MongoDB applications with automatic data isolation, authentication, and resource management.

## Core Modules

### `core` - MongoDB Engine

**MongoDBEngine** (`core/engine.py`)
- Central orchestration for all engine components
- Manages database connections, app registration, and lifecycle
- Provides health checks and metrics

**Manifest System** (`core/manifest.py`)
- JSON schema validation with versioning (v1.0, v2.0)
- Manifest parsing and migration
- Index definition validation

### `database` - Database Layer

**ScopedMongoWrapper** (`database/scoped_wrapper.py`)
- Automatic app isolation
- Transparent query filtering by `app_id`
- MongoDB-style API with automatic scoping

**Connection Management** (`database/connection.py`)
- Shared connection pooling for efficient resource usage
- Pool metrics and monitoring

### `auth` - Authentication & Authorization

**AuthorizationProvider** (`auth/provider.py`)
- Pluggable authorization interface
- Casbin and OSO adapters
- Caching for performance

**JWT & Dependencies** (`auth/jwt.py`, `auth/dependencies.py`)
- JWT token handling
- FastAPI dependencies for auth

### `indexes` - Index Management

**Index Orchestration** (`indexes/manager.py`)
- High-level index creation from manifest definitions
- Support for all index types (regular, vector, search, TTL, etc.)

**Helper Functions** (`indexes/helpers.py`)
- Common index operations
- Key normalization and validation

### `observability` - Monitoring & Metrics

**Metrics** (`observability/metrics.py`)
- Operation timing and statistics
- Error rate tracking
- Performance metrics

**Logging** (`observability/logging.py`)
- Structured logging with correlation IDs
- App context tracking
- Contextual logger adapter

**Health Checks** (`observability/health.py`)
- MongoDB health checks
- Engine health status
- Connection pool monitoring

### `utils` - Utilities

**Validation** (`utils/validation.py`)
- Collection name validation
- App slug validation
- MongoDB URI validation

**Constants** (`constants.py`)
- All shared constants in one place
- No magic numbers

## Usage Examples

### Basic Engine Usage

```python
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database"
)

await engine.initialize()
db = engine.get_scoped_db("my_app")
```

### Manifest Validation

```python
from mdb_engine.core import ManifestValidator

validator = ManifestValidator()
is_valid, error, paths = validator.validate(manifest)
```

### Database Scoping

```python
from mdb_engine.database import ScopedMongoWrapper

# All queries automatically scoped to app
db = engine.get_scoped_db("my_app")
docs = await db.collection.find({"status": "active"}).to_list(length=10)
```

### Observability

```python
from mdb_engine.observability import get_metrics_collector, get_logger

# Metrics
collector = get_metrics_collector()
summary = collector.get_summary()

# Structured logging
logger = get_logger(__name__)
logger.info("Operation completed")
```

## API Reference

See individual module docstrings for detailed API documentation.

## Status

✅ **Production Ready** - All core features implemented and tested
✅ **Code Quality** - Comprehensive test suite, type hints, observability
✅ **Documentation** - Complete API documentation

## License

MIT License
