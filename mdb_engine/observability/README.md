# Observability Module

Comprehensive observability tools for MDB_ENGINE applications including metrics collection, structured logging with correlation IDs, and health check monitoring.

## Features

- **Metrics Collection**: Operation timing, error rates, and performance statistics
- **Structured Logging**: Contextual logging with correlation IDs and app context
- **Health Checks**: MongoDB, engine, and connection pool health monitoring
- **Context Tracking**: Automatic context propagation across async operations

## Installation

The observability module is part of MDB_ENGINE. No additional installation required.

## Quick Start

### Metrics

```python
from mdb_engine.observability import record_operation, get_metrics_collector

# Record an operation
record_operation(
    operation_name="database.query",
    duration_ms=45.2,
    success=True,
    app_slug="my_app",
    collection_name="users"
)

# Get metrics summary
collector = get_metrics_collector()
summary = collector.get_summary()
print(summary)
```

### Logging

```python
from mdb_engine.observability import get_logger, set_correlation_id, set_app_context

# Get contextual logger
logger = get_logger(__name__)

# Set correlation ID for request tracking
correlation_id = set_correlation_id()

# Set app context
set_app_context(app_slug="my_app", user_id="user123")

# Log with automatic context
logger.info("Processing request", extra={"operation": "process_data"})
```

### Health Checks

```python
from mdb_engine.observability import check_mongodb_health, check_engine_health, HealthChecker

# Check MongoDB health
mongodb_status = await check_mongodb_health(engine.mongo_client)
print(mongodb_status.status)  # "healthy", "degraded", or "unhealthy"

# Check engine health
engine_status = await check_engine_health(engine)
print(engine_status.status)

# Use HealthChecker for multiple checks
checker = HealthChecker()
checker.register_check(check_mongodb_health)
checker.register_check(check_engine_health)

results = await checker.check_all()
print(results)
```

## Metrics Collection

### Record Operations

Track operation performance and errors:

```python
from mdb_engine.observability import record_operation
import time

# Record successful operation
start = time.time()
result = await db.collection.find_one({"_id": "doc123"})
duration_ms = (time.time() - start) * 1000

record_operation(
    operation_name="database.find_one",
    duration_ms=duration_ms,
    success=True,
    app_slug="my_app",
    collection_name="documents"
)

# Record failed operation
from pymongo.errors import PyMongoError

try:
    await db.collection.insert_one(document)
except PyMongoError as e:
    record_operation(
        operation_name="database.insert_one",
        duration_ms=0,
        success=False,
        app_slug="my_app",
        error=str(e)
    )
```

### Get Metrics

Retrieve collected metrics:

```python
from mdb_engine.observability import get_metrics_collector

collector = get_metrics_collector()

# Get all metrics
all_metrics = collector.get_metrics()

# Get metrics for specific operation
db_metrics = collector.get_metrics("database")

# Get summary
summary = collector.get_summary()
print(summary["summary"]["database.find_one"])
# {
#     "operation": "database.find_one",
#     "count": 150,
#     "avg_duration_ms": 45.2,
#     "min_duration_ms": 12.5,
#     "max_duration_ms": 234.8,
#     "error_count": 2,
#     "error_rate_percent": 1.33,
#     "last_execution": "2024-01-01T12:00:00"
# }
```

### Metrics Collector

Use the MetricsCollector directly:

```python
from mdb_engine.observability import MetricsCollector

collector = MetricsCollector()

# Record operations
collector.record_operation("custom.operation", 100.5, success=True)
collector.record_operation("custom.operation", 50.2, success=True, tag="read")
collector.record_operation("custom.operation", 200.0, success=False, tag="write")

# Get metrics
metrics = collector.get_metrics("custom.operation")

# Get operation count
count = collector.get_operation_count("custom.operation")

# Reset metrics
collector.reset()
```

## Structured Logging

### Contextual Logger

Get a logger that automatically adds context:

```python
from mdb_engine.observability import get_logger, set_correlation_id, set_app_context

logger = get_logger(__name__)

# Set correlation ID (for request tracking)
correlation_id = set_correlation_id()  # Generates UUID

# Set app context
set_app_context(app_slug="my_app", user_id="user123", collection_name="users")

# Log messages (context automatically added)
logger.info("User logged in")
logger.error("Failed to process request", extra={"error": "timeout"})
logger.debug("Processing data", extra={"item_count": 100})
```

### Correlation IDs

Track requests across services:

```python
from mdb_engine.observability import (
    set_correlation_id,
    get_correlation_id,
    clear_correlation_id
)

# Set correlation ID at request start
correlation_id = set_correlation_id("req-12345")

# Get current correlation ID
current_id = get_correlation_id()  # Returns "req-12345"

# Clear correlation ID
clear_correlation_id()
```

### App Context

Set context that's automatically included in all logs:

```python
from mdb_engine.observability import set_app_context, clear_app_context

# Set app context
set_app_context(
    app_slug="my_app",
    user_id="user123",
    collection_name="documents",
    operation="process_data"
)

# All subsequent logs include this context
logger.info("Processing document")

# Clear context
clear_app_context()
```

### Logging Context

Get current logging context:

```python
from mdb_engine.observability import get_logging_context

context = get_logging_context()
# {
#     "timestamp": "2024-01-01T12:00:00",
#     "correlation_id": "req-12345",
#     "app_slug": "my_app",
#     "user_id": "user123"
# }
```

### Log Operations

Log operations with structured context:

```python
from mdb_engine.observability import log_operation
import logging

logger = logging.getLogger(__name__)

# Log successful operation
log_operation(
    logger=logger,
    operation="process_document",
    level=logging.INFO,
    success=True,
    duration_ms=125.5,
    document_id="doc123"
)

# Log failed operation
log_operation(
    logger=logger,
    operation="process_document",
    level=logging.ERROR,
    success=False,
    error="timeout"
)
```

## Health Checks

### MongoDB Health

Check MongoDB connection health:

```python
from mdb_engine.observability import check_mongodb_health

# Check health
result = await check_mongodb_health(engine.mongo_client, timeout_seconds=5.0)

print(result.status)      # "healthy", "degraded", or "unhealthy"
print(result.message)     # Human-readable message
print(result.details)     # Additional details
print(result.timestamp)   # Check timestamp
```

### Engine Health

Check MongoDBEngine health:

```python
from mdb_engine.observability import check_engine_health

result = await check_engine_health(engine)

print(result.status)      # "healthy" or "unhealthy"
print(result.message)
print(result.details)     # Includes app_count, etc.
```

### Connection Pool Health

Check connection pool health:

```python
from mdb_engine.observability import check_pool_health

result = await check_pool_health(engine.mongo_client)

print(result.status)
print(result.details)     # Includes pool_size, active_connections, etc.
```

### HealthChecker

Register and run multiple health checks:

```python
from mdb_engine.observability import HealthChecker, check_mongodb_health, check_engine_health

checker = HealthChecker()

# Register checks
checker.register_check(lambda: check_mongodb_health(engine.mongo_client))
checker.register_check(lambda: check_engine_health(engine))

# Run all checks
results = await checker.check_all()

print(results["status"])      # Overall status
print(results["checks"])      # List of individual check results
print(results["timestamp"])   # Check timestamp
```

### Custom Health Checks

Create custom health checks:

```python
from mdb_engine.observability import HealthCheckResult, HealthStatus

async def check_custom_service():
    try:
        # Check your service
        response = await my_service.ping()
        if response.ok:
            return HealthCheckResult(
                name="custom_service",
                status=HealthStatus.HEALTHY,
                message="Service is healthy",
                details={"response_time_ms": response.elapsed.total_seconds() * 1000}
            )
        else:
            return HealthCheckResult(
                name="custom_service",
                status=HealthStatus.DEGRADED,
                message="Service responded with error",
                details={"status_code": response.status_code}
            )
    except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
        return HealthCheckResult(
            name="custom_service",
            status=HealthStatus.UNHEALTHY,
            message=f"Service check failed: {str(e)}"
        )

# Register custom check
checker = HealthChecker()
checker.register_check(check_custom_service)
```

## API Reference

### Metrics

#### Functions

- `record_operation(operation_name, duration_ms, success=True, **tags)` - Record operation
- `get_metrics_collector()` - Get global metrics collector

#### MetricsCollector

- `record_operation(operation_name, duration_ms, success=True, **tags)` - Record operation
- `get_metrics(operation_name=None)` - Get metrics
- `get_summary()` - Get metrics summary
- `get_operation_count(operation_name)` - Get operation count
- `reset()` - Reset all metrics

### Logging

#### Functions

- `get_logger(name)` - Get contextual logger
- `set_correlation_id(correlation_id=None)` - Set correlation ID
- `get_correlation_id()` - Get current correlation ID
- `clear_correlation_id()` - Clear correlation ID
- `set_app_context(app_slug=None, **kwargs)` - Set app context
- `clear_app_context()` - Clear app context
- `get_logging_context()` - Get current logging context
- `log_operation(logger, operation, level, success, duration_ms, **context)` - Log operation

#### ContextualLoggerAdapter

Logger adapter that automatically adds context to log records.

### Health Checks

#### Functions

- `check_mongodb_health(mongo_client, timeout_seconds=5.0)` - Check MongoDB health
- `check_engine_health(engine)` - Check engine health
- `check_pool_health(mongo_client)` - Check connection pool health

#### HealthChecker

- `register_check(check_func)` - Register health check function
- `check_all()` - Run all registered checks

#### HealthCheckResult

- `name` - Check name
- `status` - HealthStatus enum value
- `message` - Human-readable message
- `details` - Additional details dict
- `timestamp` - Check timestamp
- `to_dict()` - Convert to dictionary

#### HealthStatus

Enum values:
- `HEALTHY` - Service is healthy
- `DEGRADED` - Service is degraded but functional
- `UNHEALTHY` - Service is unhealthy
- `UNKNOWN` - Health status unknown

## Integration Examples

### FastAPI Health Endpoint

```python
from fastapi import FastAPI
from mdb_engine.observability import HealthChecker, check_mongodb_health, check_engine_health

app = FastAPI()

@app.get("/health")
async def health_check():
    checker = HealthChecker()
    checker.register_check(lambda: check_mongodb_health(engine.mongo_client))
    checker.register_check(lambda: check_engine_health(engine))

    results = await checker.check_all()
    return results
```

### Request Correlation

```python
from fastapi import Request
from mdb_engine.observability import set_correlation_id, get_logger

logger = get_logger(__name__)

@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    # Set correlation ID from header or generate new one
    correlation_id = request.headers.get("X-Correlation-ID")
    set_correlation_id(correlation_id)

    response = await call_next(request)
    response.headers["X-Correlation-ID"] = get_correlation_id()
    return response
```

### Metrics Endpoint

```python
from fastapi import FastAPI
from mdb_engine.observability import get_metrics_collector

app = FastAPI()

@app.get("/metrics")
async def get_metrics():
    collector = get_metrics_collector()
    return collector.get_summary()
```

## Best Practices

1. **Use correlation IDs** - Set correlation ID at request start for distributed tracing
2. **Set app context early** - Set app context as early as possible in request lifecycle
3. **Record all operations** - Record metrics for all significant operations
4. **Use structured logging** - Include structured context in logs for better searchability
5. **Monitor health regularly** - Set up periodic health checks
6. **Track error rates** - Monitor error rates in metrics
7. **Use appropriate log levels** - Use DEBUG for development, INFO for production
8. **Include context** - Always include relevant context (user_id, app_slug, etc.)

## Related Modules

- **`core/`** - MongoDBEngine integration
- **`database/`** - Database operation metrics
- **`auth/`** - Authentication event logging
