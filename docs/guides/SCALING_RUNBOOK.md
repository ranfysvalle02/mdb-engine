# Scaling Runbook

A single, authoritative guide for scaling mdb-engine applications from a single
process to multi-node production clusters.

---

## Table of Contents

1. [Connection Pool Sizing](#connection-pool-sizing)
2. [Horizontal Scaling](#horizontal-scaling)
3. [WebSocket at Scale](#websocket-at-scale)
4. [Rate Limiting at Scale](#rate-limiting-at-scale)
5. [Stateful Components Summary](#stateful-components-summary)

---

## Connection Pool Sizing

### How the pool works

Each `MongoDBEngine` instance creates a single Motor `AsyncIOMotorClient` with a
connection pool.  The pool lazily opens connections up to `max_pool_size` and
keeps at least `min_pool_size` warm.  When all connections are checked out, new
requests block until one is returned or `serverSelectionTimeoutMS` (default 5 s)
expires.

### Defaults

The canonical defaults live in `mdb_engine/constants.py`:

| Parameter | Default | Env override (MongoDBEngine) |
|-----------|---------|------------------------------|
| `max_pool_size` | **50** | Constructor arg |
| `min_pool_size` | **10** | Constructor arg |
| `maxIdleTimeMS` | 45 000 ms | Constructor arg |
| `serverSelectionTimeoutMS` | 5 000 ms | Constructor arg |

Override at construction time:

```python
engine = MongoDBEngine(
    mongo_uri=os.getenv("MONGODB_URI"),
    db_name=os.getenv("MDB_DB_NAME"),
    max_pool_size=200,
    min_pool_size=20,
)
```

### Sizing formula

```
per_worker_pool = ceil(target_concurrent_requests / num_workers) + headroom
total_connections = num_workers * per_worker_pool
```

`headroom` accounts for background tasks, memory service calls, and change-stream
watchers.  A safe starting point is 20 % of the base.

### Quick-reference table

| Deployment | Workers | `max_pool_size` | `min_pool_size` | Total connections |
|---|---|---|---|---|
| Dev / CI | 1 | 50 (default) | 10 | 50 |
| Low traffic (< 50 req/s) | 2 | 50 | 5 | 100 |
| Medium traffic (50-200 req/s) | 4 | 100 | 10 | 400 |
| High traffic (200+ req/s) | 8 | 150 | 20 | 1 200 |
| Multi-app platform | 4 | 200 | 30 | 800 |

**Atlas limits:** Free/Shared clusters allow 500 connections.  Dedicated M10+
allows thousands.  Always check your Atlas tier limit and keep `total_connections`
below it.

### Monitoring

The `/health` endpoint includes pool metrics when the engine is initialized:

```bash
curl -s http://localhost:8000/health | python -m json.tool
```

Key fields in the response:

- `pool_usage_percent` — if consistently > 80 %, increase `max_pool_size`
- `current_connections` — server-wide count from `serverStatus`
- `max_pool_size` / `min_pool_size` — configured values

Pool usage above 80 % triggers a `WARNING` log automatically.  Above 90 % the
health check reports `DEGRADED`.

---

## Horizontal Scaling

### What is stateless and what is not

mdb-engine apps are **mostly stateless**.  Database state lives in MongoDB, and
most services are request-scoped via FastAPI `Depends()`.

The following components hold **in-process state** and need special handling when
you run more than one worker or container:

| Component | Default storage | Multi-instance fix |
|---|---|---|
| Rate limit counters | In-memory | Use `MongoDBRateLimitStore` (auto-wired when `rate_limit_store` is `"auto"` or `"mongodb"`) |
| WebSocket ticket store | In-memory | Use `MongoDBWebSocketTicketStore` (set `websocket_ticket_store: "mongodb"` in manifest) |
| WebSocket connections | In-memory | Sticky sessions (LB) or `BroadcastBackend` for cross-process fan-out |
| WebSocket rooms | In-memory | Sticky sessions or `MongoDBChangeStreamBackend` |
| Metrics collector | In-memory LRU | Export via OpenTelemetry; don't rely on per-instance counters |

### uvicorn workers (simplest)

```bash
uvicorn web:app --host 0.0.0.0 --port 8000 --workers 4
```

Each worker is a separate process with its own connection pool, rate-limit store,
and WebSocket manager.  Total MongoDB connections = `workers * max_pool_size`.

### Gunicorn with uvicorn workers (recommended for production)

```bash
pip install gunicorn
gunicorn web:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers 4 \
    --bind 0.0.0.0:8000 \
    --timeout 120 \
    --graceful-timeout 30 \
    --keep-alive 5
```

Gunicorn manages worker lifecycle (restarts on crash, graceful reload with
`SIGHUP`).  `--workers` should be `2 * cpu_cores + 1` as a starting point.

### Docker Compose with replicas

```yaml
services:
  app:
    build: .
    deploy:
      replicas: 3
    environment:
      MONGODB_URI: mongodb://mongo:27017/mydb
      MDB_DB_NAME: mydb
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 15s
      timeout: 5s
      retries: 3

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
    depends_on:
      - app

  mongo:
    image: mongo:7
    command: ["--replSet", "rs0"]
    volumes:
      - mongo_data:/data/db

volumes:
  mongo_data:
```

### Kubernetes Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mdb-app
spec:
  replicas: 3
  selector:
    matchLabels:
      app: mdb-app
  template:
    metadata:
      labels:
        app: mdb-app
    spec:
      containers:
        - name: app
          image: my-registry/mdb-app:latest
          ports:
            - containerPort: 8000
          env:
            - name: MONGODB_URI
              valueFrom:
                secretKeyRef:
                  name: mdb-secrets
                  key: mongodb-uri
            - name: MDB_DB_NAME
              value: "production"
          readinessProbe:
            httpGet:
              path: /ready
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 15
            periodSeconds: 30
          resources:
            requests:
              cpu: 250m
              memory: 256Mi
            limits:
              cpu: "1"
              memory: 512Mi
---
apiVersion: v1
kind: Service
metadata:
  name: mdb-app
spec:
  selector:
    app: mdb-app
  ports:
    - port: 80
      targetPort: 8000
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: mdb-app
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: mdb-app
  minReplicas: 2
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

The multi-app parent exposes `GET /ready` (K8s readiness probe) automatically.
Single apps expose `GET /health`.

---

## WebSocket at Scale

### Decision tree

```
Single worker?
  └─ Yes → Everything works out of the box.
  └─ No (multiple workers / containers)
       ├─ Need broadcast across processes?
       │    ├─ No  → Use sticky sessions at the load balancer.
       │    └─ Yes → Use MongoDBChangeStreamBackend (see below).
       └─ WebSocket tickets failing across workers?
            └─ Switch to MongoDBWebSocketTicketStore (manifest flag).
```

### Sticky sessions (nginx)

When each client stays pinned to one worker, in-process broadcast works fine.

```nginx
upstream mdb_app {
    ip_hash;  # Sticky sessions by client IP
    server app1:8000;
    server app2:8000;
    server app3:8000;
}

server {
    listen 80;

    location /ws/ {
        proxy_pass http://mdb_app;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
    }

    location / {
        proxy_pass http://mdb_app;
    }
}
```

### Sticky sessions (AWS ALB)

Enable stickiness on the target group:

```
Target Group → Attributes → Stickiness → Enable
  Type: Application-based cookie
  Duration: 1 day
```

### Sticky sessions (Traefik)

```yaml
http:
  services:
    mdb-app:
      loadBalancer:
        sticky:
          cookie:
            name: mdb_ws_affinity
        servers:
          - url: "http://app1:8000"
          - url: "http://app2:8000"
```

### Cross-process broadcast (MongoDBChangeStreamBackend)

When sticky sessions are not sufficient (e.g. you need server-initiated
broadcast to reach all connected clients regardless of which process they are
on), configure the pluggable broadcast backend:

```python
from mdb_engine.routing.websockets import (
    WebSocketConnectionManager,
    set_broadcast_backend,
)
from mdb_engine.routing._broadcast import MongoDBChangeStreamBackend

backend = MongoDBChangeStreamBackend(db=engine.connection_manager.mongo_db)
await backend.initialize()
set_broadcast_backend(backend)
```

This uses a MongoDB capped collection as a pub/sub channel.  Each process
subscribes to the change stream and delivers messages to its local connections.
No Redis or external message broker required.

### WebSocket ticket store for multi-instance

The default in-memory `WebSocketTicketStore` only works within a single process.
For multi-worker deployments, use `MongoDBWebSocketTicketStore`:

```python
from mdb_engine.auth.websocket_tickets import MongoDBWebSocketTicketStore

ticket_store = MongoDBWebSocketTicketStore(
    db=engine.connection_manager.mongo_db,
    ticket_ttl_seconds=10,
)
```

Or set `websocket_ticket_store: "mongodb"` in your manifest to have it
auto-configured.

---

## Rate Limiting at Scale

### The problem

mdb-engine has four separate rate-limiting mechanisms, and **three of them are
in-memory only by default**:

| Mechanism | Scope | Default store | Multi-instance? |
|---|---|---|---|
| `AuthRateLimitMiddleware` | Auth endpoints (`/login`, `/register`) | In-memory | Only with MongoDB store |
| Per-collection `rate_limits` | CRUD endpoints | In-memory | Only with MongoDB store |
| `@rate_limit` decorator | Any endpoint | In-memory | No (single process) |
| `app_auth_routes.py` inline | Login/register | In-memory dict | No (single process) |

In a multi-worker or multi-container deployment, the default in-memory stores
are **per-process**, meaning rate limits are effectively divided by the number of
workers.

### Auto-wired MongoDB store (0.12+)

Starting in 0.12, the engine auto-wires `MongoDBRateLimitStore` for the auth
middleware and per-collection rate limits when a database connection is available.
This is controlled by the `rate_limit_store` manifest key:

```json
{
  "auth": {
    "rate_limit_store": "auto"
  }
}
```

| Value | Behavior |
|---|---|
| `"auto"` (default) | Use MongoDB store if DB is available, otherwise in-memory |
| `"mongodb"` | Require MongoDB store; fail if DB is unavailable |
| `"memory"` | Always use in-memory (legacy behavior) |

### Manual wiring (if you need full control)

```python
from mdb_engine.auth.rate_limiter import (
    MongoDBRateLimitStore,
    AuthRateLimitMiddleware,
    RateLimit,
)

store = MongoDBRateLimitStore(db=engine.get_raw_db())

app.add_middleware(
    AuthRateLimitMiddleware,
    store=store,
    limits={
        "/login": RateLimit(max_attempts=5, window_seconds=300),
        "/register": RateLimit(max_attempts=3, window_seconds=3600),
    },
)
```

### Migration checklist (dev → production)

1. Set `rate_limit_store: "auto"` or `"mongodb"` in your manifest `auth` section
2. Verify the `_mdb_engine_rate_limits` collection is created with TTL index
3. Remove any manual `app.add_middleware(AuthRateLimitMiddleware)` calls
   (the engine handles it)
4. Test with `--workers 2` locally and confirm rate limits are shared
5. Monitor the `_mdb_engine_rate_limits` collection size (TTL index handles
   cleanup automatically)

### Limitations

- The `@rate_limit` decorator always uses the module-level in-memory store.
  For distributed limiting of custom endpoints, use the middleware approach or
  add your own `MongoDBRateLimitStore` dependency.
- The inline rate limits in `app_auth_routes.py` (login lockout, registration
  throttle) are always in-memory.  For multi-instance deployments, the
  `AuthRateLimitMiddleware` is the primary defense.

---

## Stateful Components Summary

Quick reference for what needs attention at each scaling tier:

### Tier 1: Single process (dev / small prod)

Everything works out of the box.  No changes needed.

### Tier 2: Multiple workers (`--workers N`)

| Component | Action |
|---|---|
| Connection pool | Set `max_pool_size = desired / N` per worker |
| Rate limiting | Ensure `rate_limit_store: "auto"` (default in 0.12+) |
| WS tickets | Switch to `MongoDBWebSocketTicketStore` |
| WS broadcast | Use sticky sessions at LB level |

### Tier 3: Multiple containers / nodes

| Component | Action |
|---|---|
| Connection pool | Each container has its own pool; total = containers * workers * max_pool_size |
| Rate limiting | MongoDB store is required (same DB, shared counters) |
| WS tickets | `MongoDBWebSocketTicketStore` required |
| WS broadcast | Sticky sessions or `MongoDBChangeStreamBackend` |
| Health checks | Point LB at `/health` (single-app) or `/ready` (multi-app) |
| Metrics | Export via OpenTelemetry; per-instance `MetricsCollector` is not aggregated |

---

## Further Reading

- [Production Deployment Guide](PRODUCTION_DEPLOYMENT.md) — security, CORS, CSRF, Docker
- [WebSocket Security](WEBSOCKET_SECURITY_ELEGANT_SOLUTION.md) — ticket auth model
- [WebSocket Troubleshooting](WEBSOCKET_TROUBLESHOOTING.md) — common connection issues
- [Best Practices](../BEST_PRACTICES.md) — DI patterns, avoiding global state
