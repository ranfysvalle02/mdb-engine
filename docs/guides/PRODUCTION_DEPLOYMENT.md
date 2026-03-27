# Production Deployment Guide

A consolidated checklist and reference for deploying MDB-Engine applications to production.

---

## Table of Contents

1. [Pre-Deployment Checklist](#pre-deployment-checklist)
2. [MongoDB Configuration](#mongodb-configuration)
3. [Connection Pool Tuning](#connection-pool-tuning)
4. [Authentication & Security](#authentication-security)
5. [Rate Limiting for Production](#rate-limiting-for-production)
6. [CORS & CSRF](#cors-csrf)
7. [Observability & Monitoring](#observability-monitoring)
8. [Health Checks](#health-checks)
9. [AI Services Configuration](#ai-services-configuration)
10. [Docker Deployment](#docker-deployment)
11. [Environment Variables Reference](#environment-variables-reference)

---

## Pre-Deployment Checklist

- [ ] MongoDB connection string uses a replica set (required for change streams, transactions, CSFLE)
- [ ] `MONGODB_URI` is set via environment variable (not hardcoded)
- [ ] All API keys are in environment variables, not in code or manifests
- [ ] `auth.jwt_secret` is a strong, unique secret (minimum 32 characters)
- [ ] CORS origins are restricted to your actual domains (no `*` in production)
- [ ] CSRF protection is enabled for browser-facing apps
- [ ] Rate limiting uses `MongoDBRateLimitStore` for multi-instance deployments
- [ ] Health check endpoint is configured for your load balancer / orchestrator
- [ ] OpenTelemetry is configured if you need distributed tracing
- [ ] `schema_version: "2.0"` is set in all manifests

---

## MongoDB Configuration

### Replica Set

A replica set is **strongly recommended** for production. It enables:
- Automatic failover
- Change streams (used by some memory features)
- Multi-document transactions
- Client-Side Field Level Encryption (CSFLE)

```bash
# Atlas (recommended for production)
MONGODB_URI="mongodb+srv://user:pass@cluster.mongodb.net/mydb?retryWrites=true&w=majority"

# Self-hosted replica set
MONGODB_URI="mongodb://user:pass@host1:27017,host2:27017,host3:27017/mydb?replicaSet=rs0&retryWrites=true&w=majority"
```

### Database Naming

```bash
MDB_DB_NAME="my_production_db"
```

Each app gets its own collection prefix (`{slug}_`), so multiple apps can share a single database safely.

---

## Connection Pool Tuning

The engine exposes `max_pool_size` and `min_pool_size` on the `MongoDBEngine` constructor:

```python
engine = MongoDBEngine(
    mongo_uri=os.getenv("MONGODB_URI"),
    db_name=os.getenv("MDB_DB_NAME"),
    max_pool_size=200,   # Default: 100. Increase for high-concurrency apps.
    min_pool_size=20,    # Default: 10. Keep warm connections ready.
)
```

**Guidelines:**

| Deployment | `max_pool_size` | `min_pool_size` |
|---|---|---|
| Low traffic (< 50 req/s) | 50 | 5 |
| Medium traffic (50-200 req/s) | 100 | 10 |
| High traffic (200+ req/s) | 200-500 | 20-50 |
| Multi-app (shared engine) | 200+ | 20+ |

Monitor `connections.current` and `connections.available` in MongoDB to tune. If you consistently hit `max_pool_size`, increase it. If idle connections waste resources, lower `min_pool_size`.

---

## Authentication & Security

### JWT Configuration

Set a strong, unique secret in your manifest:

```json
{
  "auth": {
    "jwt_secret": "${JWT_SECRET}",
    "access_token_expire_minutes": 30,
    "refresh_token_expire_minutes": 10080
  }
}
```

Use environment variable interpolation — never commit secrets to version control.

### Password Policy

Enable password policy enforcement in the manifest:

```json
{
  "auth": {
    "password_policy": {
      "min_length": 12,
      "require_uppercase": true,
      "require_lowercase": true,
      "require_digit": true,
      "require_special": true,
      "check_common_passwords": true
    }
  }
}
```

### CSFLE (Client-Side Field Level Encryption)

For encrypting sensitive fields at the application layer before they reach MongoDB:

```python
from mdb_engine.core.csfle import CSFLEConfig

csfle_config = CSFLEConfig(
    key_vault_namespace="encryption.__keyVault",
    kms_providers={"local": {"key": master_key}},
)

engine = MongoDBEngine(csfle_config=csfle_config)
```

See `docs/guides/CSFLE_SETUP.md` for full setup with AWS KMS, Azure Key Vault, or GCP KMS.

---

## Rate Limiting for Production

**The default in-memory rate limiter does not work across multiple instances.** For any deployment with more than one process or container, use the MongoDB-backed store:

```python
from mdb_engine.auth.rate_limiter import (
    AuthRateLimitMiddleware,
    MongoDBRateLimitStore,
    RateLimit,
    create_rate_limit_store,
)

# Create a production-ready MongoDB-backed store
store = create_rate_limit_store(db=engine.get_raw_db())

# Apply to your app
app.add_middleware(
    AuthRateLimitMiddleware,
    store=store,
    limits={
        "/login": RateLimit(max_attempts=5, window_seconds=300),
        "/register": RateLimit(max_attempts=3, window_seconds=3600),
    },
)
```

The MongoDB store uses TTL indexes for automatic cleanup — no manual maintenance required.

---

## CORS & CSRF

### CORS

Restrict origins to your actual domains:

```json
{
  "auth": {
    "cors": {
      "origins": ["https://app.example.com", "https://admin.example.com"],
      "allow_credentials": true,
      "allow_methods": ["GET", "POST", "PUT", "DELETE"],
      "allow_headers": ["Authorization", "Content-Type", "X-CSRF-Token"]
    }
  }
}
```

**Never use `"origins": ["*"]` in production** — it disables credential-based CORS.

### CSRF

Enable CSRF protection for browser-facing apps:

```json
{
  "auth": {
    "csrf": {
      "enabled": true,
      "cookie_secure": true,
      "cookie_samesite": "strict"
    }
  }
}
```

Requires HTTPS. See `docs/guides/CSRF_PROTECTION.md` for full details.

---

## Observability & Monitoring

### OpenTelemetry Distributed Tracing

MDB-Engine includes built-in OpenTelemetry support. Install the extras and configure:

```bash
pip install mdb-engine[otel]
```

```python
from mdb_engine.observability import (
    init_tracer_provider,
    instrument_fastapi,
    instrument_pymongo,
)

# Initialize tracing (call once at startup)
init_tracer_provider(
    service_name="my-app",
    exporter="otlp",  # or "console" for debugging
    endpoint="http://otel-collector:4317",
)

# Auto-instrument FastAPI and PyMongo
instrument_fastapi(app)
instrument_pymongo()
```

This gives you:
- Distributed traces across HTTP requests
- MongoDB query spans with timing
- Automatic context propagation

If the `[otel]` extra is not installed, all tracing functions gracefully no-op.

### Structured Logging

MDB-Engine uses structured logging with correlation IDs:

```python
from mdb_engine.observability import get_logger, set_correlation_id

logger = get_logger(__name__)
```

Configure log level via `LOG_LEVEL` environment variable (default: `INFO`).

### Metrics

The built-in `MetricsCollector` tracks operation counts, latencies, and error rates:

```python
from mdb_engine.observability import MetricsCollector

metrics = MetricsCollector()
```

Metrics are in-memory with LRU eviction (max 10,000 entries). For persistent metrics, export to your monitoring system via OpenTelemetry or a custom exporter.

---

## Health Checks

Configure your load balancer to hit the health endpoint:

```python
from mdb_engine.observability import check_health

@app.get("/health")
async def health():
    return await check_health(engine)
```

Or use the built-in `RequestContext` pattern:

```python
@app.get("/health")
async def health(ctx: RequestContext = Depends()):
    health = {"status": "healthy", "app": ctx.slug, "services": {}}

    try:
        engine_health = await ctx.engine.get_health_status()
        health["services"]["database"] = engine_health.get("mongodb", "unknown")
    except (ConnectionError, TimeoutError, OSError):
        health["services"]["database"] = "connection_failed"
        health["status"] = "degraded"

    health["services"]["embedding"] = "ok" if ctx.embedding_service else "not_configured"
    health["services"]["memory"] = "ok" if ctx.memory else "not_configured"
    health["services"]["llm"] = "ok" if ctx.llm else "not_configured"

    status_code = 200 if health["status"] == "healthy" else 503
    return JSONResponse(health, status_code=status_code)
```

Set your load balancer health check to `GET /health` with a 5-second timeout.

---

## AI Services Configuration

### Embedding Providers

MDB-Engine auto-detects your embedding provider from environment variables:

| Provider | Required Environment Variables | Model Example |
|---|---|---|
| OpenAI | `OPENAI_API_KEY` | `text-embedding-3-small` |
| Azure OpenAI | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT` | `text-embedding-3-small` |
| Voyage AI | `VOYAGE_API_KEY` | `voyage-3`, `voyage-3-lite`, etc. |

Set the model in your manifest's `embedding_config.default_embedding_model` (and install `openai` and/or `voyageai` as needed).

### LLM Providers

Configure via `llm_config` in the manifest. **LLMService** uses native SDKs for **OpenAI**, **Azure OpenAI**, and **Google Gemini** (`pip install openai google-genai`). Use `provider/model` strings (for example `openai/gpt-4o`, `azure/your-deployment`, `gemini/gemini-2.5-flash`).

```json
{
  "llm_config": {
    "enabled": true,
    "default_model": "openai/gpt-4o",
    "temperature": 0.7
  }
}
```

### Memory Pipeline Tuning

For production memory workloads, tune these manifest settings:

```json
{
  "memory_config": {
    "max_depth": 1000,
    "similarity_threshold": 0.7,
    "extraction_model": "gpt-4o-mini",
    "enable_cognitive": true
  }
}
```

- `max_depth` — caps memory growth per user (prevents unbounded storage)
- `similarity_threshold` — controls deduplication aggressiveness (higher = stricter)
- `extraction_model` — use a fast model for extraction to reduce latency

---

## Docker Deployment

### Minimal Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "web:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

### Docker Compose with MongoDB

```yaml
version: "3.8"
services:
  app:
    build: .
    ports:
      - "8000:8000"
    environment:
      - MONGODB_URI=mongodb://mongo:27017/mydb
      - MDB_DB_NAME=mydb
      - JWT_SECRET=${JWT_SECRET}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
    depends_on:
      - mongo
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 5s
      retries: 3

  mongo:
    image: mongo:7
    volumes:
      - mongo_data:/data/db
    command: ["--replSet", "rs0"]

volumes:
  mongo_data:
```

### Multi-Worker Considerations

When running with `--workers > 1`:
- Use `MongoDBRateLimitStore` (in-memory store is per-process)
- WebSocket connections are pinned to a single worker — use sticky sessions if behind a load balancer
- Each worker gets its own connection pool — adjust `max_pool_size` accordingly (total connections = workers x max_pool_size)

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `MONGODB_URI` | Yes | `mongodb://localhost:27017` | MongoDB connection string |
| `MDB_DB_NAME` | No | `mdb_engine` | Database name |
| `JWT_SECRET` | For auth | None | JWT signing secret |
| `OPENAI_API_KEY` | For AI | None | OpenAI API key |
| `AZURE_OPENAI_API_KEY` | For Azure AI | None | Azure OpenAI key |
| `AZURE_OPENAI_ENDPOINT` | For Azure AI | None | Azure OpenAI endpoint |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | For Gemini LLM | None | Google Gemini API key |
| `VOYAGE_API_KEY` | For Voyage embeddings | None | Voyage AI API key |
| `LOG_LEVEL` | No | `INFO` | Logging level |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | For tracing | None | OTLP collector endpoint |

---

## Further Reading

- [Manifest Reference](../MANIFEST_REFERENCE.md) — full manifest schema documentation
- [CSFLE Setup](CSFLE_SETUP.md) — field-level encryption with KMS providers
- [CSRF Protection](CSRF_PROTECTION.md) — CSRF configuration for browser apps
- [SSO Multi-App Setup](SSO_MULTI_APP_SETUP.md) — multi-app deployment on single instances
- [WebSocket Security](WEBSOCKET_SECURITY_ELEGANT_SOLUTION.md) — ticket-based WebSocket auth
- [Best Practices](../BEST_PRACTICES.md) — dependency injection patterns and anti-patterns
