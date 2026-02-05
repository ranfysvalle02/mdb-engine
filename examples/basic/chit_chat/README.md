# ChitChat - Enterprise AI Chat with Hybrid Memory

A production-ready AI chat application implementing a **Robust Hybrid Memory Architecture** that combines Short-Term Memory (STM) and Long-Term Memory (LTM) for optimal conversational AI performance. This example demonstrates enterprise-grade memory management using MongoDB Atlas Vector Search.

## 🏗️ Enterprise Architecture Highlights

**Why Enterprise-Grade?**
- **CognitiveEngine Integration**: Uses MDB-Engine's `CognitiveEngine` for complete RAG pipeline orchestration
- **Automatic STM + LTM Management**: CognitiveEngine handles Short-Term Memory (chat history) and Long-Term Memory (semantic memories) automatically
- **Intelligent Context Merging**: Automatically retrieves relevant memories and injects them into the conversation context
- **Automatic Fact Extraction**: Facts are extracted from conversations and stored to LTM without manual intervention
- **Scalable**: Built on MongoDB Atlas Vector Search for production-ready semantic memory
- **Best Practices**: This example demonstrates the recommended approach for building AI chat applications with MDB-Engine

**Version**: 3.0.0 (CognitiveEngine Edition)

## Features

### 🧠 Enterprise Hybrid Memory Architecture
- **CognitiveEngine RAG Pipeline**: Complete orchestration of STM (Short-Term Memory) and LTM (Long-Term Memory) via `CognitiveEngine.chat()`
- **Automatic Context Management**: CognitiveEngine automatically retrieves relevant memories and manages chat history
- **Intelligent Memory Extraction**: Facts are automatically extracted from conversations and stored to LTM during chat
- **Optimized STM Window**: Maintains conversational fluency with a 10-message context window
- **Persistent Memory**: Conversations are automatically stored and remembered using MongoDB Atlas Vector Search
- **Semantic Search**: Find relevant memories from past conversations using semantic search
- **Context-Aware Responses**: AI responses are enhanced with relevant memories from your conversation history
- **Memory Explorer UI**: Interactive memory management with inject (💉) and delete (🗑️) capabilities
- **Manual Memory Injection**: Inject memories directly without LLM inference for facts, preferences, or structured data
- **Memory Management**: View, search, edit, inject, and delete all your memories through an intuitive UI
- **Real-Time Updates**: WebSocket support for live memory updates and activity logs

### 💬 Conversation Features
- **Multiple Conversations**: Create and manage multiple conversation threads
- **Beautiful UI**: Modern, responsive interface with MongoDB-inspired dark theme
- **Real-Time Chat**: Instant messaging with AI assistant
- **Conversation History**: Full conversation history with timestamps
- **Memory Stats**: Track memory statistics and activity

### 🤖 AI Integration
- **Multi-Provider Support**: Works with Azure OpenAI or OpenAI (auto-detected from environment variables)
- **Intelligent Memory Extraction**: LLM automatically extracts facts and insights from conversations
- **Cognitive Memory Features**: Importance scoring, reinforcement, decay, merging, and pruning
- **Contextual Responses**: AI uses your conversation history to provide personalized responses

## Quick Start

### Using Docker Compose (Recommended)

```bash
cd examples/basic/chit_chat
docker-compose up
```

The application will be available at `http://localhost:8000`

### Connecting with MongoDB Compass

MongoDB is exposed on port `27017` with no authentication required. Connect using:

**Connection String:**
```
mongodb://localhost:27017/
```

**Or use the connection form:**
- **Host:** `localhost`
- **Port:** `27017`
- **Authentication:** None (no username/password required)

**Default Database:** `conversations_db`

### Manual Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export MONGO_URI="mongodb://localhost:27017"
export MONGO_DB_NAME="conversations_db"
export APP_SECRET_KEY="your-secret-key-here"

# Configure AI provider (choose one)
# The memory service auto-detects from environment variables (same pattern as embeddings)

# Azure OpenAI (recommended)
export AZURE_OPENAI_ENDPOINT="https://your-endpoint.openai.azure.com"
export AZURE_OPENAI_API_KEY="your-api-key"
export AZURE_OPENAI_DEPLOYMENT_NAME="gpt-4o"  # For chat completions
export AZURE_OPENAI_API_VERSION="2024-02-15-preview"

# OR Standard OpenAI
export OPENAI_API_KEY="sk-..."
export OPENAI_MODEL="gpt-4o"  # Optional, defaults to gpt-4o

# Note: The memory service auto-detects the provider from environment variables
# (same pattern as the embeddings service)

# Run the application
uvicorn web:app --host 0.0.0.0 --port 8000 --reload
```

## API Endpoints

### Health Check
- `GET /health` - Health check endpoint for container orchestration (no auth required)

### Authentication
- `GET /login` - Login page
- `POST /login` - Authenticate user (returns JSON)
- `GET /register` - Registration page
- `POST /register` - Create new user (returns JSON)
- `POST /logout` - Logout user (returns JSON, requires CSRF token)

### Conversations
- `GET /conversations` - List all conversations
- `GET /conversations/{conversation_id}` - View a conversation
- `POST /api/conversations` - Create a new conversation
- `POST /api/conversations/{conversation_id}/messages` - Send a message
- `DELETE /api/conversations/{conversation_id}` - Delete a conversation

### Memory Management
- `GET /api/memories` - Get all memories for current user
- `GET /api/memories/search?query={query}&limit={limit}&filters={filters}` - Search memories using MongoDB Atlas Vector Search with advanced filtering
- `GET /api/memories/{memory_id}` - Get a specific memory
- `POST /api/memories/inject` - Manually inject a memory without LLM inference (💉)
  - Supports optional `bucket_id` and `bucket_type` parameters for grouping memories
  - Works perfectly fine without bucket parameters
- `PUT /api/memories/{memory_id}` - Update a memory
  - Update memory content via `data` field in request body
  - Direct MongoDB update - fast and reliable
  - Metadata is preserved automatically
- `DELETE /api/memories/{memory_id}` - Delete a memory (🗑️)
- `DELETE /api/memories` - Delete all memories for current user
- `GET /api/memories/stats` - Get memory statistics

## Usage Examples

**Note:** All POST/PUT/DELETE requests require the `X-CSRF-Token` header with the value from the `csrf_token` cookie.

### Create a Conversation

```bash
curl -X POST "http://localhost:8000/api/conversations" \
  -H "Cookie: your-session-cookie; csrf_token=your-csrf-token" \
  -H "X-CSRF-Token: your-csrf-token"
```

### Send a Message

```bash
curl -X POST "http://localhost:8000/api/conversations/{conversation_id}/messages" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -H "Cookie: your-session-cookie; csrf_token=your-csrf-token" \
  -H "X-CSRF-Token: your-csrf-token" \
  -d "message=What was my first message to you?"
```

**Response includes memory context:**
```json
{
  "success": true,
  "message": {
    "role": "assistant",
    "content": "...",
    "created_at": "2026-02-03T12:00:00Z"
  },
  "memory_context": {
    "query": "What was my first message to you?",
    "used_memories": 3,
    "memories": [...],
    "search_details": [...]
  },
  "memory_operations": {
    "search_performed": true,
    "memories_found": 3,
    "storage_scheduled": true
  }
}
```

### Logout

```bash
curl -X POST "http://localhost:8000/logout" \
  -H "Cookie: your-session-cookie; csrf_token=your-csrf-token" \
  -H "X-CSRF-Token: your-csrf-token"
```

### Search Memories

```bash
# Basic search
curl "http://localhost:8000/api/memories/search?query=travel%20plans&limit=5" \
  -H "Cookie: your-session-cookie"

# Search with filters
curl "http://localhost:8000/api/memories/search?query=preferences&limit=10&filters=%7B%22OR%22%3A%5B%7B%22user_id%22%3A%22alex%22%7D%2C%7B%22agent_id%22%3A%7B%22in%22%3A%5B%22assistant%22%5D%7D%7D%5D%7D" \
  -H "Cookie: your-session-cookie"

# Search with version parameter
curl "http://localhost:8000/api/memories/search?query=travel%20plans&version=v2&limit=5" \
  -H "Cookie: your-session-cookie"
```

**Filter Syntax:**
- Supports metadata filtering with custom filter structures
- Supports operators like `in` for array matching
- Example: `{"OR": [{"user_id": "alex"}, {"agent_id": {"in": ["assistant"]}}]}`
- URL encode filters when passing as query parameter

### Get Memory Statistics

```bash
curl "http://localhost:8000/api/memories/stats" \
  -H "Cookie: your-session-cookie"
```

### Inject Memory (Manual Insertion)

```bash
# Basic injection
curl -X POST "http://localhost:8000/api/memories/inject" \
  -H "Content-Type: application/json" \
  -H "Cookie: your-session-cookie; csrf_token=your-csrf-token" \
  -H "X-CSRF-Token: your-csrf-token" \
  -d '{
    "memory": "User prefers dark mode interfaces",
    "metadata": {"source": "manual", "category": "preference"}
  }'

# Injection with bucket_id for grouping
curl -X POST "http://localhost:8000/api/memories/inject" \
  -H "Content-Type: application/json" \
  -H "Cookie: your-session-cookie; csrf_token=your-csrf-token" \
  -H "X-CSRF-Token: your-csrf-token" \
  -d '{
    "memory": "User prefers Python programming",
    "bucket_id": "bucket:general:user123",
    "bucket_type": "general",
    "metadata": {"source": "manual", "category": "preference"}
  }'
```

**Bucket Support (Optional):**
- `bucket_id`: **Optional** - Groups memories together (e.g., by conversation, category, etc.)
- `bucket_type`: **Optional** - Type of bucket ("general", "conversation", "file", "category")
- Both are stored in metadata when provided
- If not provided, memories work normally without bucket grouping

### Update Memory

```bash
# Update content only
curl -X PUT "http://localhost:8000/api/memories/{memory_id}" \
  -H "Content-Type: application/json" \
  -H "Cookie: your-session-cookie; csrf_token=your-csrf-token" \
  -H "X-CSRF-Token: your-csrf-token" \
  -d '{
    "data": "Updated memory content"
  }'

# Update content only
curl -X PUT "http://localhost:8000/api/memories/{memory_id}" \
  -H "Content-Type: application/json" \
  -H "Cookie: your-session-cookie; csrf_token=your-csrf-token" \
  -H "X-CSRF-Token: your-csrf-token" \
  -d '{
    "data": "Updated memory content"
  }'
```

**Note:** Updates are direct MongoDB operations. Metadata is preserved automatically.

### Delete Memory

```bash
curl -X DELETE "http://localhost:8000/api/memories/{memory_id}" \
  -H "Cookie: your-session-cookie; csrf_token=your-csrf-token" \
  -H "X-CSRF-Token: your-csrf-token"
```

## Architecture

### Enterprise-Grade Hybrid Memory Architecture

The application uses **MDB-Engine's CognitiveEngine** for complete RAG pipeline orchestration:

1. **Short-Term Memory (STM)**: Managed by `ChatHistoryService` - stores recent chat history for conversational fluency
2. **Long-Term Memory (LTM)**: Managed by `CognitiveMemoryService` - stores semantic memories using MongoDB Atlas Vector Search
3. **CognitiveEngine**: Orchestrates STM + LTM automatically via `chat()` method

### Key Architectural Features

**CognitiveEngine Integration:**
- Uses `CognitiveEngine.chat()` for complete RAG pipeline
- Automatically handles STM context retrieval and LTM memory search
- Automatically extracts facts from conversations and stores to LTM
- No manual STM/LTM handling needed - CognitiveEngine does it all

**Automatic Context Management:**
- CognitiveEngine retrieves relevant memories (LTM) based on user query
- CognitiveEngine manages chat history (STM) with configurable context window (10 messages)
- Context is automatically merged into system prompts

**Optimized Configuration:**
- `stm_context_limit=10`: Maintains conversational fluency
- `ltm_search_limit=5`: Retrieves top 5 relevant memories
- `auto_summarize_threshold=20`: Triggers summarization for long sessions

### Message Flow (CognitiveEngine Architecture)

When a user sends a message, the system uses CognitiveEngine's complete RAG pipeline:

1. **CognitiveEngine.chat()** handles everything:
   - Saves user message to STM
   - Searches LTM for relevant memories (semantic search)
   - Retrieves STM context (last 10 messages)
   - Constructs system prompt with LTM memories
   - Generates LLM response with full context
   - Saves AI response to STM
   - Extracts facts from conversation and stores to LTM (automatic)
2. **Message Sync**: Messages are synced to `messages` collection for UI compatibility
3. **Response**: Returns AI response with memory context metadata

This architecture ensures:
- ✅ **Simple**: Single method call handles entire RAG pipeline
- ✅ **Automatic**: Memory extraction happens automatically
- ✅ **Best Practices**: Uses MDB-Engine's recommended approach
- ✅ **Scalable**: Built on MongoDB Atlas Vector Search

### Application Structure

The application uses MDB_ENGINE's recommended `engine.create_app()` pattern:

```python
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(
    mongo_uri=os.getenv("MONGO_URI"),
    db_name=os.getenv("MONGO_DB_NAME"),
)

app = engine.create_app(
    slug="conversations",
    manifest=Path(__file__).parent / "manifest.json",
    version="2.0.0",  # Enterprise Version
)
```

This pattern automatically handles:

1. **Engine Lifecycle**: Automatic initialization on startup and cleanup on shutdown
2. **Manifest Loading**: Validates and registers the app from manifest.json
3. **Auth Setup**: Configures authentication based on manifest settings
4. **CORS Configuration**: Sets up CORS from manifest cors config
5. **WebSocket Routes**: Registers WebSocket endpoints from manifest
6. **Database Scoping**: `get_scoped_db()` provides app-isolated database access
7. **Index Management**: Indexes are created automatically from manifest configuration
8. **Memory Service**: MongoDB Atlas Vector Search integration for intelligent memory management
9. **LLM Client Initialization**: Auto-detects and initializes OpenAI or Azure OpenAI clients
10. **Cognitive Engine**: Background services for summarization and fact extraction

## Memory Configuration

The application uses **CognitiveMemoryService** (unified, customizable memory service) with MongoDB Atlas Vector Search. Key features:

- **Automatic Fact Extraction**: LLM automatically extracts facts from conversations (asynchronous, non-blocking)
- **Semantic Search**: Find relevant memories using MongoDB Atlas Vector Search
- **Hybrid Architecture**: Combines STM (chat history) and LTM (vector search) for optimal context
- **Parallel Retrieval**: Fetches STM and LTM simultaneously for guaranteed context coverage
- **Deterministic Merging**: Explicitly injects memories into system prompts
- **Cognitive Features** (optional, enabled by default):
  - Importance scoring (AI evaluates memory significance)
  - Memory reinforcement (similar memories strengthen)
  - Memory decay (unused memories fade)
  - Memory merging (related memories combine)
  - Memory pruning (capacity management)
- **Advanced Filtering**: Supports metadata filtering with custom filter structures
- **User Isolation**: Memories are scoped per user for privacy
- **Full Control**: Native MongoDB Atlas Vector Search - no third-party dependencies
- **Simple Queries**: Direct MongoDB queries for fast, predictable retrieval
- **Bucket Support (Optional)**: Group memories by conversation, category, or custom buckets

Configuration in `manifest.json`:
```json
{
  "memory_config": {
    "enabled": true,
    "provider": "cognitive",
    "collection_name": "user_memories",
    "embedding_model": "text-embedding-3-small",
    "chat_model": "gpt-4o",
    "embedding_model_dims": 1536,
    "infer": true,
    "async_mode": true,
    "enable_cognitive": true,
    "max_depth": 100
  }
}
```

**Key Configuration Options:**
- `provider`: "cognitive" (default, unified memory service) or "custom" (backwards compatibility)
- `collection_name`: Memory collection name (default: "{app_slug}_memories"). Will be prefixed with app slug automatically.
- `embedding_model`: Embedding model name (default: "text-embedding-3-small")
- `chat_model`: LLM model for fact extraction (default: "gpt-4o")
- `embedding_model_dims`: Embedding dimensions (default: 1536)
- `enable_cognitive`: Enable cognitive features (default: true). Set to false for basic memory service.
- `max_depth`: Maximum memories per user (default: 100). Set to null for unlimited.
- `infer`: Enable LLM fact extraction (default: true)
- `async_mode`: Process memories asynchronously (default: true)

**⚠️ Important: Automatic Index Management**

The memory service **automatically creates and manages its own vector search index**. You do NOT need to (and should NOT) add memory collection indexes to `managed_indexes` in your manifest. The service will:

1. **Auto-create the index** on startup with the correct configuration:
   - Vector field: `embedding` (with dimensions from `embedding_model_dims`)
   - Filter field: `user_id` (required for user-scoped vector search queries)
   - Similarity: `cosine`
2. **Auto-generate the index name** as `{collection_name}_vector_index` (e.g., `conversations_user_memories_vector_index`)
3. **Auto-update existing indexes** if they're missing the `user_id` filter
4. **Ensure the index is queryable** before allowing searches

The `user_id` filter is **required** for vector search queries that filter by user. The service automatically includes this in the index definition to ensure all queries work correctly.

If you try to manually define vector search indexes for memory collections in `managed_indexes`, the engine will raise an error to prevent conflicts. The memory service has full control over its indexes to ensure consistency and prevent configuration errors.

**📚 For complete memory service documentation, see [Memory Service Guide](../../docs/MEMORY_SERVICE.md)**

### CognitiveEngine Usage

This example uses **CognitiveEngine** - MDB-Engine's recommended way to handle complete RAG pipelines. CognitiveEngine orchestrates both Short-Term Memory (STM) and Long-Term Memory (LTM) automatically.

**Key Benefits:**
- **Single Method Call**: `cognitive_engine.chat()` handles everything
- **Automatic Memory Extraction**: Facts are extracted and stored automatically
- **Context Management**: STM and LTM are managed automatically
- **Best Practices**: This is the recommended approach for building AI chat apps

**Example Usage:**

```python
from mdb_engine.memory.orchestrator import CognitiveEngine

# Initialize CognitiveEngine (done in web.py startup)
cognitive_engine = CognitiveEngine(
    app_slug=APP_SLUG,
    memory_service=memory_service,
    chat_history_collection=chat_history_collection,
    stm_context_limit=10,
    ltm_search_limit=5,
    auto_summarize_threshold=20,
    llm_client=llm_client,
)

# Use CognitiveEngine for complete RAG pipeline
result = await cognitive_engine.chat(
    user_id=user_id,
    session_id=conversation_id,
    user_query=message,
    system_prompt="You are a helpful AI assistant.",
    extract_facts=True,  # Automatically extract facts
)

# Result contains:
# - response: AI response text
# - stm_context: Short-term memory context used
# - ltm_memories: Long-term memories retrieved
# - session_message_count: Number of messages in session
```

**📚 For complete CognitiveEngine documentation, see [Cognitive Memory Guide](../../docs/COGNITIVE_MEMORY.md)**

## Configuration

The application configuration is in `manifest.json`:

- `auth.users`: App-level user management configuration
- `token_management`: JWT token and session management
- `memory_config`: Memory service configuration (vector search index is automatically created)
- `managed_indexes`: Automatic index creation for regular collections (memory collections are managed automatically)
- `websockets`: WebSocket endpoint configuration

## Development

### Project Structure

```
chit_chat/
├── web.py              # Main FastAPI application (uses engine.create_app())
├── manifest.json       # App configuration (indexes, auth, memory, websockets)
├── templates/          # HTML templates
│   ├── base.html       # Base template
│   ├── conversations.html  # Conversation list
│   ├── conversation.html   # Chat interface
│   ├── login.html      # Login page
│   └── register.html   # Registration page
├── docker-compose.yml  # Docker configuration
├── Dockerfile          # Container build configuration
└── requirements.txt    # Python dependencies
```

## Troubleshooting

### Memory Not Updating

1. Check that memory service is properly configured in `manifest.json` (should have `"enabled": true`)
2. Verify MongoDB connection is working
3. Check logs for memory service errors - the vector search index is automatically created on startup
4. Verify LLM API keys are set (OPENAI_API_KEY or AZURE_OPENAI_API_KEY/AZURE_OPENAI_ENDPOINT)
5. Check logs for index creation messages - you should see: `✅ Successfully created vector search index`
6. If vector search returns 0 results, check that the index is queryable (wait a few moments after startup for index to build)

### AI Features Not Working

1. Check AI provider environment variables are set
2. Verify API keys are valid
3. Check logs for AI provider errors
4. Ensure the LLM service is properly initialized

## Testing Memory and Bucket Functionality

A comprehensive test script is provided to verify all memory and bucket functionality:

```bash
# Make sure the app is running first
cd examples/basic/chit_chat
python test_memory_buckets.py
```

The test script verifies:
- ✅ Memory injection WITHOUT bucket_id (optional parameter)
- ✅ Memory injection WITH bucket_id and bucket_type
- ✅ Native MongoDB Atlas Vector Search with LLM fact extraction
- ✅ Direct MongoDB operations for updates and retrieval
- ✅ bucket_id and bucket_type supported via metadata
- ✅ Custom metadata fields supported
- ✅ Memory retrieval and listing
- ✅ Update memory content

**Environment Variables:**
- `TEST_BASE_URL`: Base URL for the app (default: `http://localhost:8000`)
- `TEST_EMAIL`: Test user email (default: `test@example.com`)
- `TEST_PASSWORD`: Test user password (default: `testpassword123`)

### Database Connection Issues

1. Ensure MongoDB is running
2. Check MONGO_URI environment variable
3. Verify database credentials
4. Check network connectivity

### WebSocket Not Connecting

1. Verify WebSocket endpoint is configured in manifest
2. Check browser console for connection errors
3. Ensure authentication is working (WebSocket requires auth)
4. Check firewall/proxy settings

## License

MIT License
