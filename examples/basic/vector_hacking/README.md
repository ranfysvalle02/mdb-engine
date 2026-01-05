# Vector Hacking Example

A demonstration of vector inversion/hacking using LLMs with MDB_ENGINE.

## Overview

This example demonstrates:
- **MDB_ENGINE Integration** - Using `engine.create_app()` for automatic lifecycle management
- **Vector Inversion Attack** - Real-time visualization of LLM-based vector inversion
- **Embedding Service** - Using MDB_ENGINE's EmbeddingService for vector operations
- **Modern UI** - Responsive, game-like interface with real-time updates

## Architecture

```
┌─────────────┐
│   Browser   │
│ (Dashboard) │
└──────┬──────┘
       │ HTTP
       ▼
┌─────────────┐
│  FastAPI    │
│  (web.py)   │
└──────┬──────┘
       │
       ├──► VectorHackingService
       │    ├──► Azure OpenAI (chat completions)
       │    └──► EmbeddingService (vector embeddings)
       │
       └──► MongoDB (via MDB_ENGINE)
            └── experiments, embeddings
```

## Prerequisites

- Python 3.8+
- Docker and Docker Compose (for containerized setup)
- OR MongoDB running locally
- MDB_ENGINE installed
- **API Keys** (required):
  - `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT` - For LLM and embeddings
  - OR `OPENAI_API_KEY` - For OpenAI
- **Manifest Configuration**: `embedding_config` must be enabled in `manifest.json`

## Quick Start

### Using Docker Compose (Recommended)

**1. Set up your API keys:**

Create a `.env` file:

```bash
# Azure OpenAI (recommended)
AZURE_OPENAI_API_KEY=your-azure-key-here
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o

# OR Standard OpenAI
OPENAI_API_KEY=sk-your-openai-key-here
```

**2. Run the application:**

```bash
docker-compose up
```

**3. Access the Dashboard:**

- **Web Application**: http://localhost:8000
- **Health Check**: http://localhost:8000/health

**With optional MongoDB Express UI:**

```bash
docker-compose --profile ui up
```

Then visit http://localhost:8081 to browse the database.

**Stop everything:**

```bash
docker-compose down
```

**Rebuild after code changes:**

```bash
docker-compose up --build
```

## MDB_ENGINE Integration

This example uses the recommended `engine.create_app()` pattern:

```python
from mdb_engine import MongoDBEngine
from pathlib import Path

# Initialize the MongoDB Engine
engine = MongoDBEngine(mongo_uri=mongo_uri, db_name=db_name)

# Create FastAPI app with automatic lifecycle management
app = engine.create_app(
    slug="vector_hacking",
    manifest=Path(__file__).parent / "manifest.json",
    title="Vector Hacking - MDB_ENGINE Demo",
    version="1.0.0",
)

# Use dependency injection for database access
from mdb_engine.dependencies import get_scoped_db

@app.get("/items")
async def get_items(db=Depends(get_scoped_db)):
    return await db.items.find({}).to_list(100)
```

This automatically handles:
- Engine initialization on startup
- Manifest loading and app registration
- CORS configuration from manifest
- Graceful shutdown

## Project Structure

```
vector_hacking/
├── web.py              # FastAPI app with MDB_ENGINE integration
├── vector_hacking.py   # VectorHackingService - attack logic
├── manifest.json       # MDB_ENGINE app configuration
├── Dockerfile          # Multi-stage Docker build
├── docker-compose.yml  # Full stack with MongoDB
├── requirements.txt    # Python dependencies
└── templates/
    └── index.html      # Dashboard UI
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Vector hacking dashboard (HTML) |
| `/health` | GET | Health check for container healthchecks |
| `/start` | POST | Start the vector hacking attack (requires CSRF token) |
| `/stop` | POST | Stop the attack (requires CSRF token) |
| `/api/status` | GET | Get current attack status |
| `/api/generate-target` | POST | Generate a random target phrase (requires CSRF token) |
| `/api/reset` | POST | Reset attack state (requires CSRF token) |
| `/api/restart` | POST | Restart attack (requires CSRF token) |
| `/api/health` | GET | Health status API (legacy) |

**Note:** POST endpoints require the `X-CSRF-Token` header with the value from the `csrf_token` cookie.

## How Vector Inversion Works

1. **Target Vector**: A target text (e.g., "Be mindful") is embedded into a vector
2. **LLM Guessing**: An LLM generates guesses for what the text might be
3. **Error Calculation**: Each guess is embedded and compared to the target vector
4. **Iterative Improvement**: The LLM uses feedback (error values) to improve guesses
5. **Success Condition**: When the vector error drops below threshold (0.6), attack succeeds

### Configuration

Attack parameters in `vector_hacking.py`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `TARGET` | "Be mindful of your thoughts" | Target text to reverse-engineer |
| `MATCH_ERROR` | 0.6 | Error threshold for success |
| `TEXT_SIMILARITY_THRESHOLD` | 0.85 | Text similarity threshold |
| `COST_LIMIT` | 100.0 | Maximum cost before stopping |

## Web UI Features

- **Real-time Progress** - Watch the attack progress live
- **Vector Error Metrics** - See how close guesses are getting
- **Cost Tracking** - Monitor API usage costs
- **Terminal Log** - View detailed attack logs
- **Game-like Experience** - Victory celebration on success

## Troubleshooting

### Missing API Keys

```bash
docker-compose exec app env | grep -E "API_KEY|ENDPOINT"
```

### Vector Hacking Not Starting

1. Check API keys are set correctly
2. Check logs: `docker-compose logs app`
3. Verify Azure OpenAI endpoint is accessible

### MongoDB Connection Issues

```bash
docker-compose ps mongodb
docker-compose logs mongodb
```

### Rebuild After Code Changes

```bash
docker-compose build --no-cache
docker-compose up
```

## Security Considerations

This demo illustrates the importance of:
- Protecting embedding vectors from side-channel attacks
- Not exposing error messages that reveal vector distances
- Using proper access controls on vector databases
- Understanding security implications of embedding-based systems

## Resources

- [MDB_ENGINE Documentation](../../../mdb_engine/README.md)
- [MDB_ENGINE Core Module](../../../mdb_engine/core/README.md)
- [Examples Overview](../../README.md)

## License

Same as MDB_ENGINE project.
