# MDB_ENGINE Examples

This directory contains example applications demonstrating how to use MDB_ENGINE. **All examples follow best practices and use mdb-engine abstractions consistently.**

## Available Examples

### [Hello World](./hello_world/)

A simple, beginner-friendly example that demonstrates:
- Initializing the runtime engine
- Creating and registering an app manifest
- Basic CRUD operations with automatic app scoping
- Using `engine.mongo_db` for top-level database access
- Using `engine.get_scoped_db()` for app-scoped operations
- Authentication with JWT
- WebSocket support with real-time updates
- Health checks and metrics

**Perfect for:** Getting started with MDB_ENGINE

**Run it:**
```bash
cd hello_world
./run_with_docker.sh
```

### [Interactive RAG](./interactive_rag/)

An advanced example demonstrating:
- Embedding service (`EmbeddingService`)
- Vector search with MongoDB Atlas Vector Search
- Knowledge base management with sessions
- Document processing and chunking
- Semantic search and retrieval
- Using OpenAI SDK directly for LLM operations

**Perfect for:** Building RAG applications with vector search

### [Vector Hacking](./vector_hacking/)

A demonstration of:
- Vector inversion attacks using LLM service abstraction
- Real-time attack visualization
- LLM service configuration via manifest.json
- Using `EmbeddingService` for embeddings and OpenAI SDK for chat

**Perfect for:** Understanding vector embeddings and LLM abstractions

## Docker Compose Setup

Each example includes a `docker-compose.yml` file that provides:

### Standard Services

- **MongoDB** - Database server with authentication
- **MongoDB Express** - Web UI for browsing data (optional)

### Quick Start with Docker

```bash
# Navigate to an example
cd hello_world

# Start all services
docker-compose up -d

# Run the example
python main.py

# Stop services
docker-compose down
```

### Service URLs

When Docker Compose is running:

- **MongoDB:** `mongodb://admin:password@localhost:27017/?authSource=admin`
- **MongoDB Express UI:** http://localhost:8081 (admin/admin, optional with `--profile ui`)

## Running Examples

### Prerequisites

**Just Docker and Docker Compose!** That's it.

- Docker Desktop: https://www.docker.com/products/docker-desktop
- Or install separately: https://docs.docker.com/compose/install/

No need to install Python, MongoDB, or MDB_ENGINE - everything runs in containers.

### Quick Start

```bash
cd hello_world
docker-compose up
```

The example will:
1. Build the application (installs MDB_ENGINE automatically)
2. Start MongoDB
3. Start MongoDB Express (Web UI)
4. Run the example automatically
5. Show you all the output

### Example Structure

Each example includes:
- `README.md` - Explanation of what the example does
- `manifest.json` - App configuration manifest
- `main.py` - Main example code
- `Dockerfile` - Builds and runs the example
- `docker-compose.yml` - Orchestrates all services

### Environment Variables

Environment variables are set in `docker-compose.yml`. Common variables:
- `MONGO_URI` - MongoDB connection string (uses Docker service name)
- `MONGO_DB_NAME` - Database name
- `APP_SLUG` - App identifier
- `LOG_LEVEL` - Logging level

### How the Dockerfile Works

The Dockerfile:
1. Copies `mdb_engine` source code from the project root
2. Installs it with `pip install -e` (editable mode)
3. Installs all dependencies from `pyproject.toml`
4. Copies the example files
5. Runs the example automatically

No manual installation needed!

## Troubleshooting

### App Container Issues

1. **View app logs:**
   ```bash
   docker-compose logs app
   ```

2. **Rebuild after code changes:**
   ```bash
   docker-compose up --build
   ```

3. **Check if MongoDB is ready:**
   ```bash
   docker-compose ps mongodb
   docker-compose logs mongodb
   ```

### Port Conflicts

Modify ports in `docker-compose.yml`:

```yaml
services:
  mongodb:
    ports:
      - "27018:27017"  # Change host port
```

### Services Not Starting

1. **Check Docker is running:**
   ```bash
   docker ps
   ```

2. **View all logs:**
   ```bash
   docker-compose logs
   ```

3. **Check service health:**
   ```bash
   docker-compose ps
   ```

## Best Practices: Using MDB_ENGINE Abstractions

**All examples in this directory follow these best practices.** When building your own applications, always use mdb-engine abstractions:

### ✅ DO: Use Runtime Engine Abstractions

```python
from mdb_engine import RuntimeEngine

# Initialize engine
engine = RuntimeEngine(mongo_uri=mongo_uri, db_name=db_name)
await engine.initialize()

# For app-scoped data (recommended for most operations)
db = engine.get_scoped_db("my_app")
await db.my_collection.insert_one({"name": "Test"})

# For top-level database access (e.g., shared collections like users)
top_level_db = engine.mongo_db
await top_level_db.users.find_one({"email": "user@example.com"})

# For LLM operations (use OpenAI SDK directly)
from openai import AzureOpenAI
client = AzureOpenAI(...)
response = client.chat.completions.create(...)
```

### ❌ DON'T: Create Direct MongoDB Clients

```python
# ❌ BAD: Creates new connection, bypasses pooling and observability
from motor.motor_asyncio import AsyncIOMotorClient
client = AsyncIOMotorClient(mongo_uri)
db = client[db_name]
await db.collection.find_one({})
client.close()  # Manual cleanup needed

# ✅ GOOD: Uses engine's managed connection
db = engine.mongo_db
await db.collection.find_one({})
# No cleanup needed - engine manages connections
```

### Key Benefits of Using Abstractions

1. **Connection Pooling**: Reuses managed connection pools automatically
2. **Observability**: All operations tracked by runtime metrics
3. **Resource Management**: Automatic cleanup, no manual client management
4. **App Scoping**: Automatic data isolation with `get_scoped_db()`
5. **Index Management**: Automatic index creation from manifest.json
6. **Health Checks**: Built-in health monitoring via `engine.get_health_status()`
7. **Embedding Service**: Provider-agnostic embeddings via EmbeddingService

### Common Patterns

#### Pattern 1: App-Scoped Data (Most Common)
```python
# All operations automatically scoped to "my_app"
db = engine.get_scoped_db("my_app")
await db.products.insert_one({"name": "Widget"})
products = await db.products.find({}).to_list(length=10)
```

#### Pattern 2: Top-Level Collections (Shared Data)
```python
# For collections shared across apps (e.g., users, sessions)
top_db = engine.mongo_db
user = await top_db.users.find_one({"email": "user@example.com"})
```

#### Pattern 3: LLM Operations
```python
# Use OpenAI SDK directly for chat completions
from openai import AzureOpenAI
client = AzureOpenAI(...)
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}]
)

# Use EmbeddingService for embeddings
from mdb_engine.embeddings import EmbeddingService
embedding_service = EmbeddingService(...)
embeddings = await embedding_service.embed_chunks(["text to embed"])
```

#### Pattern 4: FastAPI Dependencies
```python
from fastapi import Depends

def get_db():
    """FastAPI dependency for scoped database"""
    return engine.get_scoped_db("my_app")

@app.get("/items")
async def get_items(db = Depends(get_db)):
    items = await db.items.find({}).to_list(length=10)
    return items
```

## Contributing Examples

If you've built something cool with MDB_ENGINE, consider contributing an example! Examples should:

- **Use mdb-engine abstractions exclusively** - Never create direct MongoDB clients
- Use `engine.mongo_db` for top-level database access (not `AsyncIOMotorClient`)
- Use `engine.get_scoped_db()` for app-scoped operations
- Use OpenAI SDK directly for LLM operations
- Be self-contained and runnable
- Include a README explaining what they demonstrate
- Include a `docker-compose.yml` with all required services
- Use clear, well-commented code
- Focus on specific features or use cases
- Include a manifest.json file
- Include environment variable examples
- Demonstrate best practices that others can follow

## Need Help?

- Check the [main README](../../README.md) for general documentation
- See the [Quick Start Guide](../../docs/QUICK_START.md) for detailed setup instructions
- Open an issue if you encounter problems
