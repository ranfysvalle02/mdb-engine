# Vector Hacking API

Demonstrates **vector inversion attacks** — how an LLM can reverse-engineer hidden text by measuring vector distances.

## MDB_ENGINE Best Practices

This example showcases the recommended patterns for building APIs with `mdb_engine`:

### 1. Engine + App Creation

```python
from mdb_engine import MongoDBEngine

# Create engine with connection details
engine = MongoDBEngine(
    mongo_uri=config.mongo_uri,
    db_name=config.db_name,
)

# Create FastAPI app with automatic lifecycle management
app = engine.create_app(
    slug=config.app_slug,
    manifest=Path(__file__).parent / "manifest.json",
    title="Vector Hacking API",
    on_startup=on_startup,
)
```

### 2. Service Initialization

```python
async def on_startup(app, engine, manifest):
    """Create and store service on app.state."""
    from mdb_engine.embeddings.service import get_embedding_service
    
    app.state.vector_service = VectorHackingService(
        embedding_service=get_embedding_service(config={"enabled": True}),
        llm_client=get_llm_client(),
        config=config,
    )
```

### 3. Service Access

```python
from fastapi import Request

def get_service(request: Request) -> VectorHackingService:
    """Get the service from app state."""
    return request.app.state.vector_service
```

### 4. Clean Configuration

```python
# config.py
@dataclass(frozen=True)
class AppConfig:
    app_slug: str = "vector_hacking_api"
    mongo_uri: str = "mongodb://localhost:27017/"
    # ... other settings

config = AppConfig.from_env()
```

## Project Structure

```
api/
├── main.py      # FastAPI app with mdb_engine integration
├── service.py   # Business logic (VectorHackingService)
├── schemas.py   # Pydantic request/response models
├── config.py    # Centralized configuration
└── manifest.json # mdb_engine app manifest
```

## Running

**Full stack (recommended):**
```bash
cd ..
docker-compose up
```

**Development:**
```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/api/attack/start` | POST | Start attack |
| `/api/attack/stop` | POST | Stop attack |
| `/api/attack/status` | GET | Get status |

## Quick Start

```bash
# Start attack with custom target
curl -X POST http://localhost:8000/api/attack/start \
  -H "Content-Type: application/json" \
  -d '{"target": "Hello world"}'

# Or generate a random target
curl -X POST http://localhost:8000/api/attack/start \
  -H "Content-Type: application/json" \
  -d '{"generate_random": true}'

# Check progress
curl http://localhost:8000/api/attack/status

# Stop attack
curl -X POST http://localhost:8000/api/attack/stop
```

## Swagger Docs

http://localhost:8000/docs
