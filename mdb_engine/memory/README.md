# Memory Service Module

Mem0.ai integration for intelligent memory management in MDB_ENGINE applications. Provides semantic memory storage, retrieval, and inference capabilities with MongoDB integration.

## Features

- **Mem0 Integration**: Wrapper around Mem0.ai for intelligent memory management
- **MongoDB Storage**: Built-in MongoDB vector store integration
- **Auto-Detection**: Automatically detects OpenAI or Azure OpenAI from environment variables
- **Semantic Search**: Vector-based semantic memory search
- **Memory Inference**: Optional LLM-based memory inference and summarization
- **Graph Memory**: Optional graph-based memory relationships (requires graph store config)

## Installation

The memory module requires mem0ai:

```bash
pip install mem0ai
```

## Configuration

### Environment Variables

The service auto-detects the provider from environment variables:

#### OpenAI

```bash
export OPENAI_API_KEY="sk-..."
```

#### Azure OpenAI

```bash
export AZURE_OPENAI_API_KEY="..."
export AZURE_OPENAI_ENDPOINT="https://your-resource.openai.azure.com/"
export AZURE_OPENAI_API_VERSION="2024-02-15-preview"  # Optional
export AZURE_OPENAI_DEPLOYMENT_NAME="gpt-4"  # Optional, for LLM
```

### Manifest Configuration

Enable memory service in your `manifest.json`:

```json
{
  "slug": "my_app",
  "memory_config": {
    "enabled": true,
    "collection_name": "memories",
    "embedding_model": "text-embedding-3-small",
    "embedding_dimensions": 1536,
    "chat_model": "gpt-4",
    "temperature": 0.7,
    "infer": true,
    "enable_graph": false
  }
}
```

## Usage

### Basic Usage

```python
from mdb_engine.memory import Mem0MemoryService
from mdb_engine.core import MongoDBEngine

# Initialize engine
engine = MongoDBEngine(mongo_uri="...", db_name="...")
await engine.initialize()

# Get memory service (automatically configured from manifest)
memory_service = engine.get_memory_service("my_app")

# Add memory
memory = await memory_service.add(
    messages=[{"role": "user", "content": "I love Python programming"}],
    user_id="user123"
)

# Search memories
results = await memory_service.search(
    query="What does the user like?",
    user_id="user123",
    limit=5
)

# Get all memories for user
all_memories = await memory_service.get_all(user_id="user123")
```

### Initialize Memory Service

```python
from mdb_engine.memory import Mem0MemoryService

# Initialize with MongoDB connection
memory_service = Mem0MemoryService(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database",
    collection_name="memories",
    app_slug="my_app",
    embedding_model="text-embedding-3-small",
    embedding_dimensions=1536,
    chat_model="gpt-4",
    temperature=0.7,
    infer=True  # Enable LLM inference
)
```

### Add Memory

Store memories with automatic embedding generation:

```python
# Add single memory
memory = await memory_service.add(
    messages=[{"role": "user", "content": "My favorite color is blue"}],
    user_id="user123",
    metadata={"source": "conversation", "timestamp": "2024-01-01"}
)

# Add multiple memories
memories = await memory_service.add_all(
    memories=[
        {
            "messages": [{"role": "user", "content": "I work at Acme Corp"}],
            "user_id": "user123"
        },
        {
            "messages": [{"role": "user", "content": "I live in San Francisco"}],
            "user_id": "user123"
        }
    ]
)
```

### Search Memories

Semantic search across stored memories:

```python
# Basic search
results = await memory_service.search(
    query="Where does the user work?",
    user_id="user123",
    limit=5
)

# Search with filters
results = await memory_service.search(
    query="What are the user's preferences?",
    user_id="user123",
    limit=10,
    filters={"source": "conversation"}
)
```

### Get Memories

Retrieve memories for a user:

```python
# Get all memories
all_memories = await memory_service.get_all(user_id="user123")

# Get specific memory
memory = await memory_service.get(memory_id="memory_123", user_id="user123")

# Get memories with filters
memories = await memory_service.get_all(
    user_id="user123",
    filters={"source": "conversation"}
)
```

### Update Memory

Update existing memories:

```python
# Update memory
updated = await memory_service.update(
    memory_id="memory_123",
    user_id="user123",
    messages=[{"role": "user", "content": "Updated content"}],
    metadata={"updated": True}
)
```

### Delete Memory

Remove memories:

```python
# Delete single memory
await memory_service.delete(memory_id="memory_123", user_id="user123")

# Delete all memories for user
await memory_service.delete_all(user_id="user123")
```

### Memory Inference

With `infer=True`, the service can generate insights and summaries:

```python
# Get memory insights (requires infer=True)
insights = await memory_service.get_all(user_id="user123")

# Memories include inferred insights and summaries
for memory in insights:
    print(f"Memory: {memory.get('memory')}")
    print(f"Insights: {memory.get('insights')}")
```

## API Reference

### Mem0MemoryService

#### Initialization

```python
Mem0MemoryService(
    mongo_uri: str,
    db_name: str,
    collection_name: str = "memories",
    app_slug: str = None,
    embedding_model: str = "text-embedding-3-small",
    embedding_dimensions: int = None,
    chat_model: str = "gpt-4",
    temperature: float = 0.7,
    infer: bool = True,
    enable_graph: bool = False,
    config: dict = None
)
```

#### Methods

- `add(messages, user_id, metadata=None)` - Add single memory
- `add_all(memories)` - Add multiple memories
- `search(query, user_id, limit=10, filters=None)` - Search memories
- `get(memory_id, user_id)` - Get specific memory
- `get_all(user_id, filters=None)` - Get all memories for user
- `update(memory_id, user_id, messages=None, metadata=None)` - Update memory
- `delete(memory_id, user_id)` - Delete memory
- `delete_all(user_id)` - Delete all memories for user

## Configuration Options

### Embedding Model

Choose embedding model based on your needs:

```python
# Small, fast, cost-effective
embedding_model="text-embedding-3-small"  # 1536 dimensions

# Large, more accurate
embedding_model="text-embedding-3-large"  # 3072 dimensions

# Legacy (still supported)
embedding_model="text-embedding-ada-002"  # 1536 dimensions
```

### Chat Model

For inference (`infer=True`), choose chat model:

```python
# GPT-4 (more capable, more expensive)
chat_model="gpt-4"

# GPT-3.5 Turbo (faster, cheaper)
chat_model="gpt-3.5-turbo"

# GPT-4 Turbo (balanced)
chat_model="gpt-4-turbo-preview"
```

### Temperature

Control randomness in LLM inference:

```python
# Low temperature (more deterministic)
temperature=0.3

# Medium temperature (balanced)
temperature=0.7

# High temperature (more creative)
temperature=1.0
```

## Integration with MongoDBEngine

The memory service integrates seamlessly with MongoDBEngine:

```python
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(mongo_uri="...", db_name="...")
await engine.initialize()

# Load manifest with memory_config
manifest = await engine.load_manifest("manifest.json")
await engine.register_app(manifest)

# Get memory service (automatically configured from manifest)
memory_service = engine.get_memory_service("my_app")
```

## Use Cases

### Conversational Memory

Store and retrieve conversation context:

```python
# Store conversation
await memory_service.add(
    messages=[
        {"role": "user", "content": "I'm planning a trip to Japan"},
        {"role": "assistant", "content": "That sounds exciting! When are you going?"}
    ],
    user_id="user123"
)

# Later, retrieve context
context = await memory_service.search(
    query="What trips is the user planning?",
    user_id="user123"
)
```

### User Preferences

Store user preferences and retrieve them:

```python
# Store preference
await memory_service.add(
    messages=[{"role": "user", "content": "I prefer dark mode interfaces"}],
    user_id="user123",
    metadata={"type": "preference", "category": "ui"}
)

# Retrieve preferences
preferences = await memory_service.search(
    query="What are the user's UI preferences?",
    user_id="user123",
    filters={"type": "preference"}
)
```

### Knowledge Base

Build a knowledge base from user interactions:

```python
# Add knowledge
await memory_service.add(
    messages=[{"role": "user", "content": "The project deadline is next Friday"}],
    user_id="user123",
    metadata={"type": "knowledge", "topic": "project"}
)

# Query knowledge
knowledge = await memory_service.search(
    query="When is the project deadline?",
    user_id="user123"
)
```

## Best Practices

1. **Use appropriate embedding models** - Choose based on accuracy vs. cost trade-offs
2. **Enable inference selectively** - Only enable `infer=True` when you need LLM insights
3. **Add metadata** - Include metadata for better filtering and organization
4. **Limit search results** - Use `limit` parameter to control result size
5. **Filter by user** - Always specify `user_id` for user-specific memories
6. **Monitor costs** - Track API usage for embedding and LLM calls
7. **Clean up old memories** - Periodically delete outdated memories
8. **Use semantic queries** - Leverage semantic search for natural language queries

## Error Handling

```python
from mdb_engine.memory import Mem0MemoryServiceError

try:
    memory = await memory_service.add(
        messages=[{"role": "user", "content": "Test"}],
        user_id="user123"
    )
except Mem0MemoryServiceError as e:
    print(f"Memory service error: {e}")
except (ValueError, TypeError, ConnectionError) as e:
    print(f"Configuration or connection error: {e}")
```

## Environment Variables Reference

### OpenAI

```bash
export OPENAI_API_KEY="sk-..."
```

### Azure OpenAI

```bash
export AZURE_OPENAI_API_KEY="..."
export AZURE_OPENAI_ENDPOINT="https://your-resource.openai.azure.com/"
export AZURE_OPENAI_API_VERSION="2024-02-15-preview"
export AZURE_OPENAI_DEPLOYMENT_NAME="gpt-4"  # For LLM
```

## Graph Memory (Advanced)

Enable graph-based memory relationships:

```json
{
  "memory_config": {
    "enabled": true,
    "enable_graph": true,
    "graph_store": {
      "provider": "neo4j",
      "config": {
        "uri": "bolt://localhost:7687",
        "user": "neo4j",
        "password": "password"
      }
    }
  }
}
```

**Note**: Graph memory requires additional graph store configuration (Neo4j, Memgraph, etc.).

## Related Modules

- **`embeddings/`** - Embedding generation service
- **`database/`** - MongoDB integration
- **`core/`** - MongoDBEngine integration
