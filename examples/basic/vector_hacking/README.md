# Vector Hacking Example

A demonstration of vector inversion/hacking using LLMs with MDB_ENGINE.

This example shows:
- How to initialize the MongoDB Engine
- How to create and register an app manifest
- Vector inversion attack using LLMs and embeddings
- Real-time visualization of the attack progress
- How to use the scoped database wrapper

## Prerequisites

- Python 3.8+
- Docker and Docker Compose (for containerized setup)
- OR MongoDB running locally (for local setup)
- MDB_ENGINE installed
- **API Keys** (for vector hacking features):
  - `OPENAI_API_KEY` or `AZURE_OPENAI_API_KEY` - For LLM-based guessing and embeddings

## Setup

### Using Docker Compose (Recommended)

Everything runs in Docker - MongoDB, the application, and optional services.

**Just run:**
```bash
docker-compose up
```

That's it! The Docker Compose setup will:
1. Build the application Docker image using multi-stage build (installs MDB_ENGINE automatically)
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

1. **OPENAI_API_KEY** - For LLM-based guessing and embeddings (OpenAI)
   - Get your key from: https://platform.openai.com/api-keys

   OR

2. **AZURE_OPENAI_API_KEY** and **AZURE_OPENAI_ENDPOINT** - For Azure OpenAI
   - Get your credentials from your Azure OpenAI resource

**Option 1: .env file (Recommended)**
Docker Compose automatically loads environment variables from a `.env` file in the same directory. Create a `.env` file in the `vector_hacking` directory:

```bash
# .env file
# Option 1: Standard OpenAI
OPENAI_API_KEY=sk-your-openai-key-here

# Option 2: Azure OpenAI
AZURE_OPENAI_API_KEY=your-azure-key-here
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o

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
# Standard OpenAI
export OPENAI_API_KEY=sk-your-openai-key-here

# OR Azure OpenAI
export AZURE_OPENAI_API_KEY=your-azure-key-here
export AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/

docker-compose up
```

**Option 3: Directly in docker-compose.yml**
Edit `docker-compose.yml` and set the values directly:
```yaml
environment:
  - OPENAI_API_KEY=sk-your-openai-key-here
  # OR for Azure OpenAI:
  # - AZURE_OPENAI_API_KEY=your-azure-key-here
  # - AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
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

1. **Initializes the MongoDB Engine** - Connects to MongoDB and sets up the engine
2. **Registers the App** - Loads the manifest and registers the "vector_hacking" app
3. **Creates Data** - Inserts sample documents (experiments)
4. **Queries Data** - Demonstrates find operations with automatic app scoping
5. **Updates Data** - Shows how updates work with scoped database
6. **Shows Health Status** - Displays engine health information via API
7. **Vector Hacking** - Uses OpenAI SDK directly for chat completions and embeddings with concurrent asyncio processing

## Expected Output

```
🚀 Initializing MDB_ENGINE for Vector Hacking Demo...
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

Notice that when you insert documents, you don't specify `app_id`. MDB_ENGINE automatically adds it:

```python
# You write:
await db.experiments.insert_one({"name": "Test", "status": "pending"})

# MDB_ENGINE stores:
{
  "name": "Test",
  "status": "pending",
  "app_id": "vector_hacking"  # ← Added automatically
}
```

### Automatic Filtering

When you query, MDB_ENGINE automatically filters by `app_id`:

```python
# You write:
experiments = await db.experiments.find({"status": "pending"}).to_list(length=10)

# MDB_ENGINE executes:
db.experiments.find({
  "$and": [
    {"app_id": {"$in": ["vector_hacking"]}},  # ← Added automatically
    {"status": "pending"}
  ]
})
```

## LLM Usage

This demo uses the OpenAI SDK directly for LLM interactions. The code uses Azure OpenAI or standard OpenAI clients for chat completions and embeddings.

### Configuration

API keys are configured via environment variables:

```bash
# For OpenAI
OPENAI_API_KEY=your-openai-key

# For Azure OpenAI
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_KEY=your-azure-key
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o

# Embeddings use the same API keys as above (OpenAI or Azure OpenAI)
```

### Usage in Vector Hacking Demo

The demo uses OpenAI SDK directly:

#### 1. Chat Completions (Text Generation)

```python
# In vector_hacking.py
from openai import AzureOpenAI

client = AzureOpenAI(...)
response = client.chat.completions.create(
    model="gpt-3.5-turbo",
    messages=messages,
    temperature=0.8,
    max_tokens=15
)
```

This generates text guesses for the vector inversion attack.

#### 2. Embeddings (Vector Generation)

```python
# Uses EmbeddingService which uses mem0 for embeddings
from mdb_engine.embeddings import EmbeddingService

embedding_service = EmbeddingService(...)
embeddings = await embedding_service.embed_chunks([text])
```

This generates vector embeddings for both the target text and each guess, enabling distance calculations.

### Error Handling & Retries

The demo includes retry logic for:
- **Rate Limits**: Exponential backoff retry
- **Transient Errors**: Automatic retries
- **Timeout Errors**: Retry with backoff
- **Service Unavailable**: Graceful degradation

### Cost Tracking

The demo tracks costs for each API call:
- Chat completions: ~$0.0001 per request
- Embeddings: ~$0.0001 per request
- Total cost accumulates and displays in real-time in the UI

## Vector Hacking Details

### How It Works

1. **Target Vector**: A target text ("Be mindful") is embedded into a vector using OpenAI/AzureOpenAI
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
- `default_embedding_model`: Embedding model to use (default: "text-embedding-3-small", configured in manifest.json)
- `NUM_PARALLEL_GUESSES`: Number of parallel guesses per iteration (default: 3)

## Docker Compose Services

The `docker-compose.yml` file includes:

### App Service
- **Container:** `vector_hacking_app`
- **Purpose:** Runs the vector_hacking example
- **Build:** Automatically builds from Dockerfile
- **Dependencies:** Waits for MongoDB to be healthy
- **API Keys:** Requires OPENAI_API_KEY (or AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT)

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
# Use OPENAI_API_KEY or AZURE_OPENAI_API_KEY (see above)

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

### ModuleNotFoundError: No module named 'mdb_engine'

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

If you modify `mdb_engine` or the example code:

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

- **OpenAI SDK** for direct LLM access (Azure OpenAI or standard OpenAI)
- **OpenAI/AzureOpenAI** for generating embeddings (via EmbeddingService, auto-detected from env vars)
- **OpenAI GPT-3.5-turbo** for generating guesses (default, configurable)
- **MDB_ENGINE** for data persistence and app scoping
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
