# Memory Kitchen Sink - MDB-Engine Memory Features Demo

This example demonstrates **ALL** MDB-Engine memory service features using `inject()` - no CSFLE, just pure memory functionality.

## Features Demonstrated

### Core Memory Operations
- **Inject** - Direct memory injection (bypasses LLM inference)
- **Search** - Semantic search with MongoDB Atlas Vector Search
- **Get All** - Retrieve all memories with filtering
- **Get One** - Retrieve single memory by ID
- **Update** - Update content (auto re-embeds) and metadata
- **Delete** - Single and bulk deletion

### Cognitive Features
- **Analytics** - Memory health metrics (strength, stability, counts)
- **Pruning** - Soft-delete weakest memories to cold storage
- **Cold Storage** - Retrieve pruned memories for audit/recovery
- **Restore** - Bring memories back from cold storage
- **Conflict Detection** - Check for contradictory facts

### Organization Features
- **Categories** - biographical, preferences, work, health, finance, travel, hobbies
- **Buckets** - Group related memories (e.g., "dietary", "projects")
- **Metadata** - Rich custom metadata on every memory
- **Redaction** - PII protection (SSN, credit cards auto-redacted)

## Quick Start

### Option 1: Docker Compose (Recommended)

```bash
# Set your OpenAI API key
export OPENAI_API_KEY=sk-your-key-here

# Start MongoDB and the app
docker-compose up --build

# App available at http://localhost:8000
```

### Option 2: Local Development

```bash
# 1. Start MongoDB
docker run -d --name mongodb -p 27017:27017 mongo:7.0

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set environment variables
cp .env.example .env
# Edit .env with your OPENAI_API_KEY

# 4. Run the app
uvicorn app:app --reload --port 8000
```

## API Endpoints

### Health & Info

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | API overview and endpoint list |
| GET | `/health` | Health check |

### Core Memory Operations

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/memories/inject` | Inject a memory directly |
| POST | `/memories/search` | Semantic search memories |
| GET | `/memories` | Get all memories |
| GET | `/memories/{id}` | Get single memory |
| PUT | `/memories/{id}` | Update a memory |
| DELETE | `/memories/{id}` | Delete a memory |
| DELETE | `/memories` | Delete all memories |

### Cognitive Features

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/memories/analytics` | Get memory analytics |
| POST | `/memories/prune` | Trigger memory pruning |
| GET | `/memories/cold-storage` | Get pruned memories |
| POST | `/memories/{id}/restore` | Restore from cold storage |
| POST | `/memories/check-conflict` | Check for knowledge conflicts |

### Categories & Demo

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/memories/categories` | Get available categories |
| POST | `/demo/seed` | Seed demo memories |
| POST | `/demo/reset` | Delete all demo data |

## Usage Examples

### 1. Seed Demo Data

```bash
curl -X POST http://localhost:8000/demo/seed
```

This creates ~20 memories across all categories with:
- Different importance levels
- Bucket organization
- Rich metadata
- Test PII patterns (to demonstrate redaction)

### 2. Inject a Memory

```bash
curl -X POST http://localhost:8000/memories/inject \
  -H "Content-Type: application/json" \
  -d '{
    "memory": "User prefers TypeScript over JavaScript",
    "category": "preferences",
    "importance": 0.7,
    "bucket_id": "tech_preferences",
    "metadata": {"language": "TypeScript", "vs": "JavaScript"}
  }'
```

### 3. Search Memories

```bash
# Basic search
curl -X POST http://localhost:8000/memories/search \
  -H "Content-Type: application/json" \
  -d '{"query": "What are the user dietary preferences?", "limit": 5}'

# Search with category filter
curl -X POST http://localhost:8000/memories/search \
  -H "Content-Type: application/json" \
  -d '{"query": "work projects", "category": "work", "limit": 3}'
```

### 4. Update a Memory

```bash
curl -X PUT http://localhost:8000/memories/{memory_id} \
  -H "Content-Type: application/json" \
  -d '{"memory": "User now prefers Rust over Python"}'
```

### 5. Get Memory Analytics

```bash
curl http://localhost:8000/memories/analytics
```

Response:
```json
{
  "success": true,
  "analytics": {
    "active_memories": 20,
    "cold_storage_memories": 0,
    "average_strength": 0.75,
    "average_stability": 48.0,
    "weak_memories": 2,
    "strong_memories": 15,
    "categories": {
      "biographical": 4,
      "preferences": 4,
      "work": 3,
      "health": 2,
      "finance": 2,
      "travel": 2,
      "hobbies": 2,
      "general": 2
    }
  }
}
```

### 6. Prune Memories

```bash
# Prune to max 10 memories (soft-delete weakest)
curl -X POST "http://localhost:8000/memories/prune?max_capacity=10"
```

### 7. Check Cold Storage

```bash
curl http://localhost:8000/memories/cold-storage
```

### 8. Restore from Cold Storage

```bash
curl -X POST http://localhost:8000/memories/{memory_id}/restore
```

### 9. Check for Knowledge Conflicts

```bash
curl -X POST http://localhost:8000/memories/check-conflict \
  -H "Content-Type: application/json" \
  -d '{"fact": "User is allergic to vegetables"}'
```

If this conflicts with "User is vegetarian", you'll get:
```json
{
  "success": true,
  "fact": "User is allergic to vegetables",
  "has_conflict": true,
  "conflict_description": "This conflicts with existing memory that user is vegetarian..."
}
```

## Manifest Configuration

The `manifest.json` enables all memory features:

```json
{
  "memory_config": {
    "enabled": true,
    "provider": "cognitive",
    "enable_cognitive": true,
    "cognitive": {
      "decay": {"enabled": true},
      "emotion": {"enabled": true},
      "pruning": {"enabled": true, "max_capacity": 100},
      "cold_storage": {"enabled": true}
    },
    "redaction": {
      "enabled": true,
      "patterns": {"ssn": true, "credit_card": true}
    },
    "categories": {
      "enabled": true,
      "custom_categories": ["biographical", "preferences", "work", "health"]
    }
  }
}
```

## Memory Document Structure

Each memory stored in MongoDB looks like:

```json
{
  "_id": "ObjectId(...)",
  "memory": "User prefers dark mode",
  "text": "User prefers dark mode",
  "embedding": [0.123, 0.456, ...],
  "user_id": "demo_user_123",
  "metadata": {
    "category": "preferences",
    "bucket_id": "ui_preferences",
    "bucket_type": "preferences",
    "source": "manual_injection",
    "manual_importance": 0.6
  },
  "importance": 0.6,
  "stability": 48.0,
  "access_count": 0,
  "last_accessed": "2024-01-15T10:30:00Z",
  "is_active": true,
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:30:00Z"
}
```

## Key Points

1. **No LLM Inference**: All memories use `inject()` which bypasses the fact extraction LLM. You provide exact text to store.

2. **Automatic Embedding**: Even with `inject()`, embeddings are automatically generated for semantic search.

3. **No CSFLE**: This example doesn't use Client-Side Field Level Encryption. Data is stored in plain text.

4. **Sync Methods**: Memory operations are synchronous. The app uses `asyncio.to_thread()` to run them in FastAPI's async context.

5. **Single User**: Demo uses a fixed `DEMO_USER_ID`. In production, you'd get this from authentication.

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MONGO_URI` | MongoDB connection string | `mongodb://localhost:27017` |
| `MONGO_DB_NAME` | Database name | `memory_kitchen_sink_db` |
| `OPENAI_API_KEY` | OpenAI API key for embeddings | Required |

## Files

```
memory_kitchen_sink/
├── app.py              # Main FastAPI application
├── manifest.json       # MDB-Engine configuration
├── requirements.txt    # Python dependencies
├── docker-compose.yml  # Docker setup with MongoDB
├── Dockerfile          # Container definition
├── .env.example        # Environment template
└── README.md           # This file
```
