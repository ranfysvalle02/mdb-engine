# Parallax - GitHub Repository Intelligence Tool

**A focused tool for analyzing GitHub repositories from two perspectives.**

Parallax searches GitHub repositories matching your watchlist keywords (with AGENTS.md or LLMs.md files) and analyzes them from Relevance and Technical perspectives.

## The Parallax Concept

In astronomy, **Parallax** is the apparent displacement of an object when viewed from two different lines of sight. In this tool, it represents one repository viewed from two angles:

1. **Relevance Lens** - Why this repository/implementation matters given your watchlist, code-level insights, urgency
2. **Technical Lens** - Code architecture, design patterns, technology stack, complexity, readiness

## Key Features

- **Watchlist Filtering** - Automatically finds repositories matching your keywords
- **File Detection** - Searches for repositories with AGENTS.md or LLMs.md files
- **Search Configuration** - Configurable minimum stars and language filters
- **Dual Analysis** - Concurrent Relevance and Technical perspectives
- **Code-Focused Analysis** - Analyzes actual code implementation, not just descriptions
- **Structured Output** - Pydantic schemas ensure consistent analysis
- **Scannable Feed** - Compact 2-column layout for quick insights
- **Real-time Dashboard** - Clean interface for monitoring GitHub repositories
- **MDB_ENGINE Integration** - Uses `engine.create_app()` for automatic lifecycle management

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
       ├──► ParallaxEngine
       │    ├──► GitHub GraphQL API
       │    │    └──► Search repos by keywords
       │    │    └──► Extract AGENTS.md/LLMs.md
       │    ├──► Relevance Agent (Code-focused)
       │    └──► Technical Agent (Code-focused)
       │
       └──► MongoDB (via MDB_ENGINE)
            └── parallax_reports
```

## Prerequisites

- Python 3.8+
- Docker and Docker Compose (for containerized setup)
- OR MongoDB running locally (for local setup)
- MDB_ENGINE installed
- **LLM API Key** (OpenAI, Azure OpenAI, or compatible provider) - **REQUIRED**
- **GitHub Personal Access Token** - **REQUIRED** for GraphQL API access

## Setup

### Using Docker Compose (Recommended)

**1. Set up your API keys:**

Create a `.env` file in this directory:

```bash
# GitHub Personal Access Token (REQUIRED)
GITHUB_TOKEN=your-github-token-here

# OpenAI (default)
OPENAI_API_KEY=your-openai-api-key-here

# OR Azure OpenAI
AZURE_OPENAI_API_KEY=your-azure-key
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o
AZURE_OPENAI_API_VERSION=2024-02-01

# Embeddings use the same API keys as above (OpenAI or AzureOpenAI)
```

**Getting a GitHub Token:**
1. Go to https://github.com/settings/tokens
2. Click "Generate new token (classic)"
3. Give it a name (e.g., "Parallax")
4. Select scopes: `public_repo` (to read public repositories)
5. Generate and copy the token

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
- Click "Scan" to start searching and analyzing GitHub repositories

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

1. **Search & Filter** - Searches GitHub repositories by watchlist keywords using GraphQL API
2. **File Extraction** - Extracts AGENTS.md or LLMs.md file content from matching repositories
3. **Fan-Out** - Launches two concurrent agents:
   - **Relevance Agent** - Analyzes code-level relevance to watchlist keywords, implementation insights, urgency
   - **Technical Agent** - Analyzes code architecture, design patterns, tech stack, complexity, readiness
4. **Aggregate** - Combines both viewpoints into a `ParallaxReport`
5. **Store** - Saves to MongoDB for dashboard visualization

### The Schemas

Each agent uses a strict Pydantic schema:

- **RelevanceView** - Relevance score, why it matters (code-level), key insight, urgency
- **TechnicalView** - Architecture, tech stack, complexity, readiness, code patterns

### The Dashboard

The dashboard displays each repository in a 2-column layout, showing Relevance and Technical perspectives side-by-side for quick scanning. Each card shows:
- Repository name, owner, and star count
- Which file was found (AGENTS.md or LLMs.md)
- Matched keywords from your watchlist

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Parallax dashboard (HTML) |
| `/health` | GET | Health check for container healthchecks |
| `/api/refresh` | POST | Trigger analysis of GitHub repositories (requires CSRF token) |
| `/api/reports` | GET | Get recent Parallax reports (JSON) |
| `/api/reports/{repo_id}` | GET | Get a single report by repo ID |
| `/api/watchlist` | GET | Get current watchlist and search configuration |
| `/api/watchlist` | POST | Update watchlist keywords and search parameters (requires CSRF token) |
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
    title="Parallax - GitHub Repository Intelligence",
    description="...",
    version="1.0.0",
)

# Use dependency injection for database access
from mdb_engine.dependencies import get_scoped_db

@app.get("/reports")
async def get_reports(db=Depends(get_scoped_db)):
    return await db.reports.find({}).to_list(100)
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
├── parallax.py         # ParallaxEngine - GitHub search & LLM analysis
├── schemas.py          # Pydantic models for reports
├── schema_generator.py # Dynamic schema generation for lenses
├── manifest.json       # MDB_ENGINE app configuration
├── Dockerfile          # Multi-stage Docker build
├── docker-compose.yml  # Full stack with MongoDB
├── requirements.txt    # Python dependencies
└── templates/
    └── parallax_dashboard.html  # Dashboard UI
```

## Watchlist Keywords and Search Configuration

The default watchlist includes:
- **MongoDB** - Database and related technologies
- **agents** - AI agents and automation
- **memory** - Memory systems and context

### Search Parameters

You can configure search parameters in the Settings modal:

- **Keywords** - List of keywords to search for (e.g., "MongoDB", "vector", "python")
- **Scan Limit** - Number of repositories to search per keyword (1-500, default: 50)
- **Minimum Stars** - Only search repositories with at least this many stars (0-100000, default: 50)
- **Language Filter** - Optional programming language filter (e.g., "python", "javascript", "typescript")

### How Search Works

For each keyword in your watchlist, Parallax searches GitHub using:
```
{keyword} agent stars:>{min_stars} [language:{language}]
```

For example, with watchlist `["MongoDB", "vector"]`, `min_stars: 50`, and `language: "python"`:
- `MongoDB agent stars:>50 language:python`
- `vector agent stars:>50 language:python`

Results are aggregated and deduplicated across all keyword searches. Only repositories with AGENTS.md or LLMs.md files in the root are analyzed.

## Troubleshooting

### Missing API Keys

If you see errors about missing API keys:

1. **Check environment variables:**
   ```bash
   docker-compose exec app env | grep -E "API_KEY|GITHUB_TOKEN"
   ```

2. **Set in `.env` file:**
   ```bash
   GITHUB_TOKEN=your-github-token-here
   OPENAI_API_KEY=your-openai-key-here
   # OR
   AZURE_OPENAI_API_KEY=your-azure-key
   AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
   ```

3. **Restart the service:**
   ```bash
   docker-compose restart app
   ```

### GitHub Token Issues

If you see GraphQL API errors:

1. **Verify token has correct permissions:**
   - Token needs `public_repo` scope to read public repositories
   - Check token at https://github.com/settings/tokens

2. **Check rate limits:**
   - GitHub GraphQL API allows 5000 requests/hour
   - If you hit limits, wait or use a token with higher rate limits

3. **Verify token is set:**
   ```bash
   docker-compose exec app env | grep GITHUB_TOKEN
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

## Resources

- [MDB_ENGINE Documentation](../../mdb_engine/README.md)
- [MDB_ENGINE Core Module](../../mdb_engine/core/README.md)
- [Examples Overview](../README.md)

## License

Same as MDB_ENGINE project.
