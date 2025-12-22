# ChitChat - AI Chat with Memory

A beautiful AI chat application with persistent memory capabilities, demonstrating MDB_ENGINE's powerful features. This example showcases how easy it is to build a production-ready conversational AI application with intelligent memory management using Mem0.

## Features

### 🧠 Memory & Context
- **Persistent Memory**: Conversations are automatically stored and remembered using Mem0
- **Semantic Search**: Find relevant memories from past conversations using semantic search
- **Context-Aware Responses**: AI responses are enhanced with relevant memories from your conversation history
- **Memory Management**: View, search, and manage all your memories through an intuitive UI
- **Real-Time Updates**: WebSocket support for live memory updates and activity logs

### 💬 Conversation Features
- **Multiple Conversations**: Create and manage multiple conversation threads
- **Beautiful UI**: Modern, responsive interface with MongoDB-inspired dark theme
- **Real-Time Chat**: Instant messaging with AI assistant
- **Conversation History**: Full conversation history with timestamps
- **Memory Stats**: Track memory statistics and activity

### 🤖 AI Integration
- **Multi-Provider Support**: Works with Azure OpenAI, OpenAI, Anthropic, or Ollama (local)
- **Intelligent Memory Extraction**: Mem0 automatically extracts facts and insights from conversations
- **Knowledge Graph**: Optional knowledge graph for entity relationships (when enabled)
- **Contextual Responses**: AI uses your conversation history to provide personalized responses

## Quick Start

### Using Docker Compose (Recommended)

```bash
cd examples/chit_chat
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
export FLASK_SECRET_KEY="your-secret-key-here"

# Configure AI provider (choose one)
# Azure OpenAI (recommended)
export AZURE_OPENAI_ENDPOINT="https://your-endpoint.openai.azure.com"
export AZURE_OPENAI_API_KEY="your-api-key"
export AZURE_OPENAI_DEPLOYMENT_NAME="gpt-4o"
export AZURE_OPENAI_API_VERSION="2024-02-15-preview"

# OR Standard OpenAI
export OPENAI_API_KEY="sk-..."
export OPENAI_MODEL="gpt-4-turbo-preview"

# OR Anthropic
export ANTHROPIC_API_KEY="sk-ant-..."
export ANTHROPIC_MODEL="claude-3-opus-20240229"

# OR Ollama (local)
export OLLAMA_BASE_URL="http://localhost:11434/v1"
export OLLAMA_MODEL="llama2"

# Run the application
python web.py
```

## API Endpoints

### Authentication
- `GET /login` - Login page
- `POST /login` - Authenticate user
- `GET /register` - Registration page
- `POST /register` - Create new user
- `GET /logout` - Logout user

### Conversations
- `GET /conversations` - List all conversations
- `GET /conversations/{conversation_id}` - View a conversation
- `POST /api/conversations` - Create a new conversation
- `POST /api/conversations/{conversation_id}/messages` - Send a message
- `DELETE /api/conversations/{conversation_id}` - Delete a conversation

### Memory Management
- `GET /api/memories` - Get all memories for current user
- `GET /api/memories/search?query={query}` - Search memories semantically
- `GET /api/memories/{memory_id}` - Get a specific memory
- `PUT /api/memories/{memory_id}` - Update a memory
- `DELETE /api/memories/{memory_id}` - Delete a memory
- `DELETE /api/memories` - Delete all memories for current user
- `GET /api/memories/stats` - Get memory statistics

## Usage Examples

### Create a Conversation

```bash
curl -X POST "http://localhost:8000/api/conversations" \
  -H "Cookie: your-session-cookie"
```

### Send a Message

```bash
curl -X POST "http://localhost:8000/api/conversations/{conversation_id}/messages" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -H "Cookie: your-session-cookie" \
  -d "message=What was my first message to you?"
```

### Search Memories

```bash
curl "http://localhost:8000/api/memories/search?query=travel%20plans" \
  -H "Cookie: your-session-cookie"
```

### Get Memory Statistics

```bash
curl "http://localhost:8000/api/memories/stats" \
  -H "Cookie: your-session-cookie"
```

## Architecture

The application uses MDB_ENGINE's core features:

1. **App Registration**: MongoDBEngine automatically registers the app from manifest.json
2. **Database Scoping**: `get_scoped_db()` creates app-scoped database wrapper
3. **Automatic Filtering**: All queries and inserts automatically include app_id
4. **Index Management**: Indexes are created automatically from manifest configuration
5. **Memory Service**: Mem0 integration for intelligent memory management
6. **LLM Service**: Abstracted LLM service supporting multiple providers
7. **WebSocket Support**: Real-time updates for memory events

## Memory Configuration

The application uses Mem0 for memory management. Key features:

- **Automatic Fact Extraction**: Mem0 automatically extracts facts from conversations
- **Semantic Search**: Find relevant memories using vector similarity search
- **Knowledge Graph**: Optional entity relationship mapping (enabled in manifest)
- **User Isolation**: Memories are scoped per user for privacy
- **Metadata Support**: Memories include metadata for filtering and organization

Configuration in `manifest.json`:
```json
{
  "memory_config": {
    "enabled": true,
    "collection_name": "user_memories",
    "embedding_model_dims": 1536,
    "enable_graph": true,
    "infer": true
  }
}
```

## Configuration

The application configuration is in `manifest.json`:

- `sub_auth`: User authentication configuration
- `token_management`: JWT token and session management
- `memory_config`: Mem0 memory service configuration
- `managed_indexes`: Automatic index creation
- `websockets`: WebSocket endpoint configuration

## Development

### Project Structure

```
chit_chat/
├── web.py              # Main FastAPI application
├── manifest.json       # App configuration
├── seed_demo.py        # Demo data seeding script (legacy)
├── templates/          # HTML templates
│   ├── base.html       # Base template
│   ├── conversations.html  # Conversation list
│   ├── conversation.html   # Chat interface
│   ├── login.html      # Login page
│   └── register.html   # Registration page
└── requirements.txt    # Python dependencies
```

## Troubleshooting

### Memory Not Updating

1. Check that Mem0 is properly configured in `manifest.json`
2. Verify MongoDB connection is working
3. Check logs for Mem0 errors
4. Ensure `MEM0_DIR` environment variable is set (defaults to `/tmp/.mem0`)

### AI Features Not Working

1. Check AI provider environment variables are set
2. Verify API keys are valid
3. Check logs for AI provider errors
4. Ensure the LLM service is properly initialized

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
