# Vector Hacking Example

A demonstration of vector inversion/hacking using LLMs with MDB_RUNTIME.

This example shows:
- How to initialize the runtime engine
- How to create and register an app manifest
- Vector inversion attack using LLMs and embeddings
- Real-time visualization of the attack progress
- How to use the scoped database wrapper

## Prerequisites

- Python 3.8+
- Docker and Docker Compose (for containerized setup)
- OR MongoDB running locally (for local setup)
- MDB_RUNTIME installed
- **API Keys** (for vector hacking features):
  - `OPENAI_API_KEY` - For LLM-based guessing
  - `VOYAGE_API_KEY` - For embedding generation

## Setup

### Using Docker Compose (Recommended)

Everything runs in Docker - MongoDB, the application, and optional services.

**Just run:**
```bash
docker-compose up
```

That's it! The Docker Compose setup will:
1. Build the application Docker image using multi-stage build (installs MDB_RUNTIME automatically)
2. Start MongoDB with authentication and health checks
3. Start the **web application** on http://localhost:8000
4. Start MongoDB Express (Web UI) - optional, use `--profile ui`
5. Show you all the output

**Access the Web UI:**
- 🌐 **Web Application**: http://localhost:8000
- The vector hacking interface will be available immediately

**With optional services:**
```bash
# Include MongoDB Express UI
docker-compose --profile ui up
```

**View logs:**
```bash
# All services
docker-compose logs -f

# Just the app
docker-compose logs -f app

# Just MongoDB
docker-compose logs -f mongodb
```

**Stop everything:**
```bash
docker-compose down
```

**Rebuild after code changes:**
```bash
docker-compose up --build
```

**Production mode:**
```bash
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

**What gets started:**
- ✅ **App Container** - Runs the vector_hacking example automatically
- ✅ **MongoDB** - Database on port 27017
- ✅ **MongoDB Express** - Web UI on http://localhost:8081 (optional, with `--profile ui`)

**Access the services:**
- 🌐 **Web Application**: http://localhost:8000
- MongoDB: `mongodb://admin:password@localhost:27017/?authSource=admin`
- MongoDB Express UI: http://localhost:8081 (login: admin/admin, optional)

**Run in detached mode:**
```bash
docker-compose up -d
docker-compose logs -f app  # Follow app logs
```

**Clean up (removes all data):**
```bash
docker-compose down -v
```

### Setting API Keys

The vector hacking demo **requires** API keys to function. You need:

1. **OPENAI_API_KEY** - For LLM-based guessing (GPT-3.5-turbo)
   - Get your key from: https://platform.openai.com/api-keys
   
2. **VOYAGE_API_KEY** - For embedding generation
   - Get your key from: https://www.voyageai.com/

**Option 1: .env file (Recommended)**
Docker Compose automatically loads environment variables from a `.env` file in the same directory. Create a `.env` file in the `vector_hacking` directory:

```bash
# .env file
OPENAI_API_KEY=sk-your-openai-key-here
VOYAGE_API_KEY=your-voyage-key-here

# Optional: Override other settings
APP_PORT=8000
MONGO_PORT=27017
LOG_LEVEL=INFO
```

Then run:
```bash
docker-compose up
```

**Note:** All configuration values in `docker-compose.yml` support environment variable substitution. You can override any setting via `.env` file.

**Option 2: Export before running**
```bash
export OPENAI_API_KEY=sk-your-openai-key-here
export VOYAGE_API_KEY=your-voyage-key-here
docker-compose up
```

**Option 3: Directly in docker-compose.yml**
Edit `docker-compose.yml` and set the values directly:
```yaml
environment:
  - OPENAI_API_KEY=sk-your-openai-key-here
  - VOYAGE_API_KEY=your-voyage-key-here
```

**Note:** Without these API keys, the vector hacking attack will not start. You'll see an error message in the logs indicating that the target vector could not be initialized.

## Testing

After running `docker-compose up`, the app automatically runs and tests itself. You'll see the output directly in your terminal.

**To test manually:**

1. **Run the example:**
   ```bash
   docker-compose up
   ```

2. **View the output:**
   The app will automatically:
   - Connect to MongoDB
   - Register the app
   - Create sample data
   - Query the data
   - Update records
   - Display health status

3. **Check logs:**
   ```bash
   # View all logs
   docker-compose logs -f
   
   # View just the app logs
   docker-compose logs -f app
   ```

4. **Run again (after stopping):**
   ```bash
   docker-compose down
   docker-compose up
   ```

5. **Test with MongoDB Express UI:**
   ```bash
   docker-compose --profile ui up
   ```
   Then visit http://localhost:8081 to browse the database and see the created data.

6. **Verify data in MongoDB:**
   ```bash
   # Connect to MongoDB container
   docker-compose exec mongodb mongosh -u admin -p password --authenticationDatabase admin
   
   # In mongosh:
   use vector_hacking_db
   db.experiments.find()
   ```

## What This Example Does

### Web Application Features

The vector_hacking example includes a **full-featured web application** with:

1. **🎯 Vector Inversion Attack** - Interactive demonstration of vector inversion
   - Real-time progress visualization
   - Attack metrics and statistics
   - Terminal-style log output
   - Start/stop controls

2. **📊 Dashboard** - Beautiful, modern UI showing:
   - Current best guess approximation
   - Vector error metrics
   - Attack progress visualization
   - Cost tracking
   - Model information

3. **🎨 Modern UI/UX** - Responsive design with:
   - Dark theme optimized for security demos
   - Smooth animations
   - Mobile-friendly layout
   - Real-time updates

### Backend Features

1. **Initializes the Runtime Engine** - Connects to MongoDB and sets up the runtime
2. **Registers the App** - Loads the manifest and registers the "vector_hacking" app
3. **Creates Data** - Inserts sample documents (experiments)
4. **Queries Data** - Demonstrates find operations with automatic app scoping
5. **Updates Data** - Shows how updates work with scoped database
6. **Shows Health Status** - Displays engine health information via API
7. **Vector Hacking** - Uses LLM service abstraction (LiteLLM) for chat completions and embeddings with concurrent asyncio processing

## Expected Output

```
🚀 Initializing MDB_RUNTIME for Vector Hacking Demo...
✅ Engine initialized successfully
✅ App 'vector_hacking' registered successfully

📝 Creating sample data...
✅ Created experiment: Initial Test
✅ Created experiment: Vector Inversion Demo

🔍 Querying data...
✅ Found 2 experiments
   - Initial Test (status: completed)
   - Vector Inversion Demo (status: pending)

🔍 Finding pending experiments...
✅ Found 1 pending experiments

✏️  Updating experiment...
✅ Updated experiment status to 'running'

🔍 Verifying update...
✅ Found updated experiment: Initial Test

📊 Health Status:
   - Status: connected
   - App Count: 1
   - Initialized: True

✅ Example completed successfully!
```

## Understanding the Code

### Manifest (`manifest.json`)

The manifest defines your app configuration:
- `slug`: Unique identifier for your app
- `name`: Human-readable name
- `status`: App status (active, draft, etc.)
- `managed_indexes`: Indexes to create automatically
- `websockets`: WebSocket endpoint configuration

### App Scoping

Notice that when you insert documents, you don't specify `app_id`. MDB_RUNTIME automatically adds it:

```python
# You write:
await db.experiments.insert_one({"name": "Test", "status": "pending"})

# MDB_RUNTIME stores:
{
  "name": "Test",
  "status": "pending",
  "app_id": "vector_hacking"  # ← Added automatically
}
```

### Automatic Filtering

When you query, MDB_RUNTIME automatically filters by `app_id`:

```python
# You write:
experiments = await db.experiments.find({"status": "pending"}).to_list(length=10)

# MDB_RUNTIME executes:
db.experiments.find({
  "$and": [
    {"app_id": {"$in": ["vector_hacking"]}},  # ← Added automatically
    {"status": "pending"}
  ]
})
```

## LLM Usage via LiteLLM

This demo uses the **MDB_RUNTIME LLM Service abstraction** powered by [LiteLLM](https://docs.litellm.ai/) for all LLM interactions. This provides a unified, provider-agnostic interface for chat completions and embeddings.

### Configuration

LLM settings are configured in `manifest.json`:

```json
{
  "llm_config": {
    "enabled": true,
    "default_chat_model": "gpt-3.5-turbo",
    "default_embedding_model": "voyage/voyage-2",
    "default_temperature": 0.8,
    "max_retries": 4
  }
}
```

### How It Works

1. **RuntimeEngine Initialization**: When the app starts, `RuntimeEngine` reads the `llm_config` from `manifest.json` and automatically initializes an `LLMService` instance for the app.

2. **Provider Routing**: LiteLLM handles provider routing based on the model prefix:
   - `gpt-3.5-turbo`, `gpt-4o` → OpenAI
   - `claude-3-opus-20240229` → Anthropic
   - `voyage/voyage-2` → VoyageAI
   - `text-embedding-3-small` → OpenAI
   - `cohere/embed-english-v3.0` → Cohere

3. **API Key Management**: LiteLLM automatically reads API keys from environment variables:
   - `OPENAI_API_KEY` for OpenAI models
   - `ANTHROPIC_API_KEY` for Anthropic models
   - `VOYAGE_API_KEY` for VoyageAI embeddings
   - `COHERE_API_KEY` for Cohere embeddings

### Usage in Vector Hacking Demo

The demo uses the LLM service in two ways:

#### 1. Chat Completions (Text Generation)

```python
# In vector_hacking.py
TEXT = await llm_service.chat(
    messages,
    model=llm_config.get("default_chat_model", "gpt-3.5-turbo"),
    temperature=llm_config.get("default_temperature", 0.8),
    max_tokens=15
)
```

This generates text guesses for the vector inversion attack. The model is configurable via `manifest.json`.

#### 2. Embeddings (Vector Generation)

```python
# In vector_hacking.py
embedding_model = llm_config.get("default_embedding_model", "voyage/voyage-2")
vectors = await llm_service.embed(TEXT, model=embedding_model)
```

This generates vector embeddings for both the target text and each guess, enabling distance calculations.

### Switching Models

To use different models, simply update `manifest.json`:

```json
{
  "llm_config": {
    "enabled": true,
    "default_chat_model": "claude-3-opus-20240229",  // Switch to Claude
    "default_embedding_model": "text-embedding-3-small",  // Switch to OpenAI embeddings
    "default_temperature": 0.7,
    "max_retries": 4
  }
}
```

No code changes required! The abstraction handles provider routing automatically.

### Supported Models

Via LiteLLM, you can use any of these models (and more):

**Chat Models:**
- OpenAI: `gpt-4o`, `gpt-3.5-turbo`, `gpt-4-turbo-preview`
- Anthropic: `claude-3-opus-20240229`, `claude-3-sonnet-20240229`, `claude-3-haiku-20240307`
- Google: `gemini/gemini-pro`, `gemini/gemini-pro-vision`
- Meta: `meta-llama/Llama-3-8b-chat-hf`

**Embedding Models:**
- VoyageAI: `voyage/voyage-2`, `voyage/voyage-large-2` (default, SOTA)
- OpenAI: `text-embedding-3-small`, `text-embedding-3-large`
- Cohere: `cohere/embed-english-v3.0`, `cohere/embed-multilingual-v3.0`

See [LiteLLM documentation](https://docs.litellm.ai/) for the complete list.

### Error Handling & Retries

The LLM service automatically handles:
- **Rate Limits**: Exponential backoff retry
- **Transient Errors**: Automatic retries (configurable via `max_retries`)
- **Timeout Errors**: Retry with backoff
- **Service Unavailable**: Graceful degradation

All retry logic is handled transparently - you don't need to implement it yourself.

### Cost Tracking

The demo tracks costs for each API call:
- Chat completions: ~$0.0001 per request
- Embeddings: ~$0.0001 per request
- Total cost accumulates and displays in real-time in the UI

### Benefits of the Abstraction

1. **Provider Agnostic**: Switch between OpenAI, Anthropic, VoyageAI without code changes
2. **Resilient**: Built-in retry logic for rate limits and transient errors
3. **Observable**: Structured logging for latency, model usage, and costs
4. **Type Safe**: Optional structured extraction via Instructor + Pydantic
5. **Configurable**: All settings in `manifest.json`, no hardcoded values

### Advanced Usage

For more advanced features (structured extraction, batch processing, etc.), see the [LLM Service documentation](../../mdb_runtime/llm/README.md).

## Vector Hacking Details

### How It Works

1. **Target Vector**: A target text ("Be mindful") is embedded into a vector using Voyage AI
2. **LLM Guessing**: An LLM (GPT-3.5-turbo) generates guesses for what the text might be
3. **Error Calculation**: Each guess is embedded and compared to the target vector
4. **Iterative Improvement**: The LLM uses feedback (error values) to improve guesses
5. **Success Condition**: When the vector error drops below a threshold (0.4), the attack succeeds

### API Endpoints

- `GET /` - Main vector hacking interface
- `POST /start` - Start the vector hacking attack
- `POST /stop` - Stop the attack
- `GET /api/status` - Get current attack status
- `GET /api/health` - Get system health status

### Configuration

The attack parameters can be modified in `vector_hacking.py`:
- `TARGET`: The target text to reverse-engineer
- `MATCH_ERROR`: Error threshold for success (default: 0.4)
- `COST_LIMIT`: Maximum cost before stopping (default: 60.0)
- `VOYAGE_MODEL`: Embedding model to use (default: "voyage-2")
- `NUM_PARALLEL_GUESSES`: Number of parallel guesses per iteration (default: 3)

## Docker Compose Services

The `docker-compose.yml` file includes:

### App Service
- **Container:** `vector_hacking_app`
- **Purpose:** Runs the vector_hacking example
- **Build:** Automatically builds from Dockerfile
- **Dependencies:** Waits for MongoDB to be healthy
- **API Keys:** Requires OPENAI_API_KEY and VOYAGE_API_KEY

### MongoDB
- **Port:** 27017
- **Credentials:** admin/password (change in production!)
- **Database:** vector_hacking_db
- **Health Check:** Enabled (app waits for this)

### MongoDB Express (Web UI)
- **URL:** http://localhost:8081
- **Credentials:** admin/admin
- **Purpose:** Browse and manage your MongoDB data visually

### Environment Variables

Docker Compose automatically loads environment variables from a `.env` file in the same directory. All configuration values in `docker-compose.yml` use environment variable substitution with sensible defaults.

**To customize configuration, create a `.env` file:**

```bash
# Required for vector hacking features
OPENAI_API_KEY=your_key_here
VOYAGE_API_KEY=your_key_here

# Optional: Override other settings
APP_PORT=8000
MONGO_URI=mongodb://admin:password@mongodb:27017/?authSource=admin
MONGO_DB_NAME=vector_hacking_db
APP_SLUG=vector_hacking
LOG_LEVEL=INFO
MONGO_PORT=27017
MONGO_INITDB_ROOT_USERNAME=admin
MONGO_INITDB_ROOT_PASSWORD=password
```

**All environment variables are optional** (except API keys for vector hacking) - the docker-compose.yml file provides defaults for all values. You only need to set variables you want to override.

### Customizing the Setup

1. **Modify `docker-compose.yml`** to change service configurations

2. **Modify `Dockerfile`** to change the build process

3. **Rebuild and restart:**
   ```bash
   docker-compose up --build
   ```

## Troubleshooting

### ModuleNotFoundError: No module named 'mdb_runtime'

If you see this error, the package wasn't installed correctly during the Docker build. Fix it by:

1. **Rebuild the Docker image:**
   ```bash
   docker-compose build --no-cache
   docker-compose up
   ```

2. **Or force a complete rebuild:**
   ```bash
   docker-compose down
   docker-compose build --no-cache
   docker-compose up
   ```

### App Won't Start

1. **Check if MongoDB is healthy:**
   ```bash
   docker-compose ps
   docker-compose logs mongodb
   ```

2. **Check app logs:**
   ```bash
   docker-compose logs app
   ```

3. **Rebuild the app:**
   ```bash
   docker-compose up --build
   ```

### MongoDB Connection Issues

The app connects to MongoDB using the service name `mongodb` (Docker networking).
If you see connection errors:

1. **Verify MongoDB is running:**
   ```bash
   docker-compose ps mongodb
   ```

2. **Check MongoDB is healthy:**
   ```bash
   docker-compose logs mongodb | grep "health"
   ```

3. **Test connection from app container:**
   ```bash
   docker-compose exec app python -c "from motor.motor_asyncio import AsyncIOMotorClient; import asyncio; asyncio.run(AsyncIOMotorClient('mongodb://admin:password@mongodb:27017/?authSource=admin').admin.command('ping'))"
   ```

### Vector Hacking Not Working

If the vector hacking attack doesn't start:

1. **Check API keys are set:**
   ```bash
   docker-compose exec app env | grep API_KEY
   ```

2. **Check LLM service is initialized:**
   ```bash
   docker-compose logs app | grep -i "LLM Service"
   ```

3. **Check for errors in logs:**
   ```bash
   docker-compose logs app | grep -i error
   ```

### Port Conflicts

If ports are already in use, modify `docker-compose.yml`:

```yaml
services:
  mongodb:
    ports:
      - "27018:27017"  # Change host port
```

Then update `MONGO_URI` in the app service to use the new port if accessing from outside Docker.

### Rebuild After Code Changes

If you modify `mdb_runtime` or the example code:

```bash
docker-compose up --build
```

## Next Steps

- Try modifying the target text in `vector_hacking.py`
- Experiment with different embedding models
- Adjust the error threshold and cost limits
- Add more collections for tracking experiments
- Check out the hello_world example for authentication patterns
- Explore MongoDB Express UI: `docker-compose --profile ui up` then http://localhost:8081

---

## Understanding Vector Inversion

Vector inversion is a security demonstration showing how embeddings can potentially be reverse-engineered. This example uses:

- **LiteLLM** for provider-agnostic LLM access (via MDB_RUNTIME LLM Service)
- **Voyage AI** for generating embeddings (default, configurable)
- **OpenAI GPT-3.5-turbo** for generating guesses (default, configurable)
- **MDB_RUNTIME** for data persistence and app scoping
- **Asyncio** for concurrent processing

The attack works by:
1. Starting with a target embedding vector
2. Using an LLM to generate text guesses
3. Embedding each guess and comparing to the target
4. Using the error feedback to improve guesses iteratively
5. Continuing until a match is found or cost limit is reached

This demonstrates the importance of:
- Protecting embedding vectors from side-channel attacks
- Not exposing error messages that reveal vector distances
- Using proper access controls on vector databases
- Understanding the security implications of embedding-based systems

