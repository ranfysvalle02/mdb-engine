# Parallax - GitHub Repository Intelligence Tool

**🔭 A focused tool for analyzing GitHub repositories from two perspectives.**

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

## Architecture

1. **The Source** - Searches GitHub repositories via GraphQL API
2. **The Filter** - Detects repositories matching watchlist keywords with minimum stars threshold
3. **File Extraction** - Extracts AGENTS.md or LLMs.md file content from matching repos
4. **The Parallax View (Fan-Out)** - Orchestrator splits each repository into two concurrent agent streams
5. **The Form Extractor** - Each agent enforces a strict Pydantic schema to structure code analysis

## Prerequisites

**⚠️ IMPORTANT: This demo REQUIRES orchestration dependencies!**

- Python 3.8+
- Docker and Docker Compose (for containerized setup)
- OR MongoDB running locally (for local setup)
- MDB_ENGINE installed
- **LLM API Key** (OpenAI, Azure OpenAI, or compatible provider) - **REQUIRED**
- **GitHub Personal Access Token** - **REQUIRED** for GraphQL API access
- **Dependencies** - **REQUIRED**:
  ```bash
  pip install langchain langchain-community langchain-core pydantic pydantic-settings httpx requests pyyaml
  ```

**Note:** Without these dependencies, the application will fail to start. This demo is focused on multi-agent orchestration capabilities.

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
- 🌐 **Parallax Dashboard**: http://localhost:8000
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

- `GET /` - Parallax dashboard (HTML)
- `POST /api/refresh` - Trigger analysis of GitHub repositories
- `GET /api/reports` - Get recent Parallax reports (JSON)
- `GET /api/watchlist` - Get current watchlist and search configuration
- `POST /api/watchlist` - Update watchlist keywords and search parameters (min_stars, language_filter)

## Understanding the Code

### Manifest (`manifest.json`)

The manifest defines:
- App configuration (slug: `parallax`, name, description)
- LLM configuration (enabled, default models, temperature: 0.0 for factual analysis)
- Indexes for `parallax_reports` collection

### App Scoping

All data is automatically scoped to the `parallax` app:

```python
# You write:
await db.parallax_reports.insert_one(report.dict())

# MDB_ENGINE stores:
{
    "repo_id": "owner/repo-name",
    "repo_name": "repo-name",
    "repo_owner": "owner",
    "stars": 150,
    "file_found": "AGENTS.md",
    "original_title": "repo-name",
    "url": "https://github.com/owner/repo-name",
    "relevance": {...},
    "product": {...},  # Technical lens (kept for backward compatibility)
    "matched_keywords": ["MongoDB", "vector"],
    "timestamp": "2024-01-01T00:00:00",
    "app_id": "parallax"  # ← Added automatically
}
```

### LLM Integration

The example uses MDB_ENGINE's LLM service with LangChain adapters:

```python
from openai import AzureOpenAI
from parallax import ParallaxEngine

# Initialize OpenAI client directly
client = AzureOpenAI(...)
parallax = ParallaxEngine(client, db)
reports = await parallax.analyze_feed()
```

## Watchlist Keywords and Search Configuration

The default watchlist includes:
- **MongoDB** - Database and related technologies
- **vector** - Vector databases and embeddings
- **AI/ML** - AI/ML related technologies

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

You can customize the watchlist and search parameters via the Settings modal in the dashboard.

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

## How It Works

1. **Search Phase**: For each keyword in your watchlist, Parallax searches GitHub using GraphQL:
   - Query: `{keyword} agent stars:>{min_stars} [language:{language}]`
   - Fetches repositories with AGENTS.md or LLMs.md files
   - Filters by minimum stars and optional language

2. **Extraction Phase**: For each matching repository:
   - Extracts AGENTS.md or LLMs.md file content
   - Parses YAML frontmatter if present
   - Stores full file content for analysis

3. **Analysis Phase**: For each repository (if not already cached):
   - Launches two concurrent LLM agents:
     - **Relevance**: Analyzes code-level relevance to watchlist keywords
     - **Technical**: Analyzes code architecture, patterns, and implementation
   - Both agents use structured Pydantic schemas for consistent output

4. **Storage Phase**: Results are stored in MongoDB with:
   - Repository metadata (name, owner, stars, URL)
   - Matched keywords
   - Both lens analyses
   - Timestamp for freshness tracking

## License

Same as MDB_ENGINE project.
