# Parallax - Call Transcript Analysis Tool

**A multi-lens analysis tool for call transcripts from three business perspectives.**

Parallax analyzes call transcripts through SALES, MARKETING, and PRODUCT lenses to extract actionable insights.

## The Parallax Concept

In astronomy, **Parallax** is the apparent displacement of an object when viewed from different lines of sight. In this tool, it represents one call transcript viewed from three business angles:

1. **SALES Lens** - Sales opportunities, objections, closing signals, deal value, decision stage
2. **MARKETING Lens** - Messaging resonance, positioning, campaign insights, customer sentiment
3. **PRODUCT Lens** - Feature requests, pain points, product-market fit, use cases

## Key Features

- **Call Transcript Analysis** - Processes synthetic call transcripts from CALLS.json
- **Vector Search** - Indexes transcripts with embeddings for semantic search
- **Multi-Lens Analysis** - Concurrent SALES, MARKETING, and PRODUCT perspectives
- **Structured Output** - Pydantic schemas ensure consistent analysis
- **Scannable Dashboard** - Clean 3-column layout for quick insights
- **Manual Refresh** - Simple refresh button to update memories (WebSocket removed for simplicity)
- **Per-Call Memory Extraction** - Intelligent memory extraction using Mem0 with conversation turn parsing
- **Visual Progress Tracking** - See AI analysis steps in real-time
- **MDB_ENGINE Integration** - Uses `engine.create_app()` for automatic lifecycle management

## Architecture

```
┌─────────────┐
│   Browser   │
│ (Dashboard) │
└──────┬──────┘
       │ HTTP/WebSocket
       ▼
┌─────────────┐
│  FastAPI    │
│  (web.py)   │
└──────┬──────┘
       │
       ├──► ParallaxEngine
       │    ├──► Load CALLS.json
       │    │    └──► Index transcripts with embeddings
       │    ├──► SALES Agent
       │    ├──► MARKETING Agent
       │    └──► PRODUCT Agent
       │
       └──► MongoDB (via MDB_ENGINE)
            ├── call_transcripts (with embeddings)
            └── parallax_reports
```

## Prerequisites

- Python 3.8+
- Docker and Docker Compose (for containerized setup)
- OR MongoDB running locally (for local setup)
- MDB_ENGINE installed
- **LLM API Key** (OpenAI, Azure OpenAI, or compatible provider) - **REQUIRED**
- **OpenAI API Key** (for embeddings) - **REQUIRED** for vector search

## Setup

### Using Docker Compose (Recommended)

**1. Set up your API keys:**

Create a `.env` file in this directory:

```bash
# OpenAI (for embeddings and LLM)
OPENAI_API_KEY=your-openai-api-key-here

# OR Azure OpenAI
AZURE_OPENAI_API_KEY=your-azure-key
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o
AZURE_OPENAI_API_VERSION=2024-02-01

# Embeddings use the same API keys as above (OpenAI or AzureOpenAI)
```

**2. Run the application:**

```bash
docker-compose up
```

That's it! The Docker Compose setup will:
1. Build the application Docker image
2. Start MongoDB with authentication
3. Start the **Parallax dashboard** on http://localhost:8000
4. Show you all the output

**Access the Dashboard:**
- **Parallax Dashboard**: http://localhost:8000
- Click "Scan" to index call transcripts and start analysis

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

## How It Works

### The Parallax Engine

The `ParallaxEngine` orchestrates the analysis:

1. **Load Transcripts** - Loads call transcripts from CALLS.json file
2. **Index** - Indexes transcripts in MongoDB with embeddings for vector search
3. **Fan-Out** - Launches three concurrent agents:
   - **SALES Agent** - Analyzes sales opportunities, objections, closing signals, deal value
   - **MARKETING Agent** - Analyzes messaging, positioning, campaign insights, sentiment
   - **PRODUCT Agent** - Analyzes feature requests, pain points, product-market fit
4. **Aggregate** - Combines all three viewpoints into a `ParallaxReport`
5. **Store** - Saves to MongoDB for dashboard visualization

### The Schemas

Each agent uses a strict Pydantic schema:

- **SALESView** - Deal value, decision stage, products discussed, objections, closing probability
- **MARKETINGView** - Customer sentiment, messaging resonance, value propositions, competitive mentions
- **PRODUCTView** - Feature requests, pain points, use cases, product fit score

### The Dashboard

The dashboard displays each call transcript in a 3-column layout, showing SALES, MARKETING, and PRODUCT perspectives side-by-side for quick scanning. Each card shows:
- Call ID, type, and participants
- Timestamp and duration
- Key insights from each lens
- **Visual Progress Tracking** - When analyzing a call, see real-time progress through 9 steps:
  1. Transcript loaded and parsed
  2. Retrieving relevant memories from past calls
  3. Analyzing through SALES, MARKETING, PRODUCT lenses
  4. Multi-lens analysis complete
  5. Extracting relevant snippets using RAG
  6. Snippets extracted
  7. Parsing transcript and extracting memories
  8. Memories stored
  9. Analysis complete

### Memory Extraction (Clever Usage)

Parallax uses **per-call memory extraction** with a clever approach to maximize memory quality:

1. **Conversation Turn Parsing**: Instead of sending raw transcripts as a single blob, Parallax intelligently parses transcripts into proper conversation turns:
   - Customer lines → `"user"` messages
   - Agent lines → `"assistant"` messages
   - This format matches what Mem0 expects for effective memory extraction

2. **Per-Call Storage**: Each call's memories are stored with `user_id = "call_{call_id}"`, ensuring:
   - Memories are isolated per call (not aggregated by company)
   - Easy retrieval of memories for a specific call
   - Clean separation for cross-call intelligence

3. **Rich Context**: The memory extractor includes:
   - Full conversation turns (parsed from transcript)
   - Lens insights (SALES, MARKETING, PRODUCT summaries)
   - Call metadata (customer, company, timestamp, call type)

4. **Why This Works**: Mem0.ai extracts memories best from natural conversation flows. By parsing transcripts into proper user/assistant pairs, we give Mem0 the structure it needs to identify:
   - Customer preferences ("We train thousands of models")
   - Pain points ("We need to track versions, metrics")
   - Facts and details ("We're planning v3")
   - Use cases and requirements

This approach transforms raw transcripts into structured conversation data that Mem0 can effectively process, resulting in higher-quality memory extraction compared to sending transcripts as unstructured text.

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Parallax dashboard (HTML) |
| `/health` | GET | Health check for container healthchecks |
| `/api/refresh` | POST | Index transcripts and trigger analysis (requires CSRF token) |
| `/api/reports` | GET | Get recent Parallax reports (JSON) |
| `/api/reports/{call_id}` | GET | Get a single report by call ID |
| `/api/watchlist` | GET | Get current watchlist keywords |
| `/api/watchlist` | POST | Update watchlist keywords (requires CSRF token) |
| `/api/lenses` | GET | Get all lens configurations |
| `/api/lenses/{lens_name}` | GET/POST | Get or update a specific lens configuration (POST requires CSRF token) |

**Note:** POST endpoints require the `X-CSRF-Token` header with the value from the `csrf_token` cookie.

## MDB_ENGINE Integration

This example uses the recommended `engine.create_app()` pattern for FastAPI integration:

```python
from mdb_engine import MongoDBEngine
from pathlib import Path

# Initialize the MongoDB Engine
engine = MongoDBEngine(mongo_uri=mongo_uri, db_name=db_name)

# Create FastAPI app with automatic lifecycle management
app = engine.create_app(
    slug="parallax",
    manifest=Path(__file__).parent / "manifest.json",
    title="Parallax - Call Transcript Analysis",
    description="...",
    version="1.0.0",
)

# Use dependency injection for database access
from mdb_engine.dependencies import get_scoped_db

@app.get("/reports")
async def get_reports(db=Depends(get_scoped_db)):
    return await db.parallax_reports.find({}).to_list(100)
```

This automatically handles:
- Engine initialization on startup
- Manifest loading and app registration
- CORS configuration from manifest
- Graceful shutdown

## Project Structure

```
parallax/
├── web.py              # FastAPI app with MDB_ENGINE integration
├── parallax.py         # ParallaxEngine - Transcript loading & LLM analysis
├── schemas.py          # Pydantic models for reports
├── schema_generator.py # Dynamic schema generation for lenses
├── CALLS.json          # Synthetic call transcripts (30 calls)
├── manifest.json       # MDB_ENGINE app configuration
├── Dockerfile          # Multi-stage Docker build
├── docker-compose.yml  # Full stack with MongoDB
├── requirements.txt    # Python dependencies
└── templates/
    └── parallax_dashboard.html  # Dashboard UI
```

## Call Transcripts

The example includes `CALLS.json` with 30 synthetic call transcripts covering:
- Sales discovery calls
- Customer support calls
- Product demos
- Sales follow-ups

Each transcript includes:
- Call metadata (ID, timestamp, duration, type)
- Participants (agent, customer, company, role)
- Full transcript text
- Metadata (products mentioned, pain points, budget range, etc.)

## Watchlist Keywords

The default watchlist includes:
- **MongoDB** - Database and related technologies
- **Atlas** - MongoDB Atlas cloud service
- **database** - Database-related discussions

You can customize the watchlist in the Settings modal to filter calls by keywords mentioned in transcripts.

## Troubleshooting

### Missing API Keys

If you see errors about missing API keys:

1. **Check environment variables:**
   ```bash
   docker-compose exec app env | grep -E "API_KEY"
   ```

2. **Set in `.env` file:**
   ```bash
   OPENAI_API_KEY=your-openai-key-here
   # OR
   AZURE_OPENAI_API_KEY=your-azure-key
   AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
   ```

3. **Restart the service:**
   ```bash
   docker-compose restart app
   ```

### ModuleNotFoundError

Rebuild the Docker image:

```bash
docker-compose build --no-cache
docker-compose up
```

### MongoDB Connection Issues

1. **Verify MongoDB is running:**
   ```bash
   docker-compose ps mongodb
   ```

2. **Check MongoDB logs:**
   ```bash
   docker-compose logs mongodb
   ```

### Embedding Service Issues

If vector search is not working:

1. **Check embedding service is enabled in manifest.json:**
   ```json
   {
     "embedding_config": {
       "enabled": true,
       "default_embedding_model": "text-embedding-3-small"
     }
   }
   ```

2. **Verify OpenAI API key is set:**
   ```bash
   docker-compose exec app env | grep OPENAI_API_KEY
   ```

## Resources

- [MDB_ENGINE Documentation](../../mdb_engine/README.md)
- [MDB_ENGINE Core Module](../../mdb_engine/core/README.md)
- [Examples Overview](../README.md)

## License

Same as MDB_ENGINE project.
