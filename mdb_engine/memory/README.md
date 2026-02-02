# Memory Service Module

Mem0.ai integration for intelligent memory management in MDB_ENGINE applications. Provides semantic memory storage, retrieval, and inference capabilities with MongoDB integration.

## 🎉 What's New

### Extensible Architecture (Latest)

**Base Class Pattern for Future Extensibility!**

The memory service now uses an abstract base class pattern, enabling future memory provider implementations while maintaining backward compatibility:

- **🏗️ BaseMemoryService**: Abstract base class defining the memory service interface
- **🔌 Provider Extensibility**: Easy to add new memory providers (LangChain, custom implementations, etc.)
- **✅ Backward Compatible**: All existing code continues to work without changes
- **📝 Type Safety**: Better IDE support and type checking with abstract base class
- **🎯 Consistent API**: All memory providers implement the same interface

### v0.7.4 Enhancements

**Enhanced Mem0 Integration - Production Ready!**

- **🔧 Hybrid Update Pattern**: Content updates via Mem0 (triggers re-embedding), metadata updates via direct MongoDB (full control, no API limitations)
- **📊 Direct MongoDB Access**: Reliable data retrieval directly from MongoDB, bypassing Mem0 API inconsistencies
- **🏷️ Full Metadata Support**: Update any metadata field without restrictions - not limited by Mem0's API
- **✅ Correct Mem0 Structure**: Properly handles Mem0's MongoDB structure (`_id` as document ID, `payload` for memory data)
- **🛡️ Robust Error Handling**: Specific exception handling with proper KeyboardInterrupt/SystemExit propagation
- **🔍 Reliable Returns**: Always returns normalized documents fetched directly from MongoDB (guaranteed structure)

> 📖 **Want to understand why we use manual MongoDB access?** See the [Mem0 Implementation Guide](../../docs/guides/MEM0_IMPLEMENTATION.md) for detailed explanations of our architectural decisions, Mem0's MongoDB structure, and things to watch out for.

## Features

- **Extensible Architecture**: Base class pattern allows for multiple memory provider implementations
- **Mem0 Integration**: Default implementation using Mem0.ai for intelligent memory management
- **MongoDB Storage**: Built-in MongoDB vector store integration
- **Auto-Detection**: Automatically detects OpenAI or Azure OpenAI from environment variables
- **Semantic Search**: Vector-based semantic memory search
- **Memory Inference**: Optional LLM-based memory inference and summarization
- **Graph Memory**: Optional graph-based memory relationships (requires graph store config)
- **Bucket Organization**: Built-in support for organizing memories into buckets (general, file, conversation, etc.)
- **Dual Storage**: Store both extracted facts AND raw content for richer context retrieval

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
from mdb_engine.memory import BaseMemoryService  # Base class for type hints
from mdb_engine.core import MongoDBEngine

# Initialize engine
engine = MongoDBEngine(mongo_uri="...", db_name="...")
await engine.initialize()

# Get memory service (automatically configured from manifest)
# Returns BaseMemoryService instance (currently Mem0MemoryService)
memory_service: BaseMemoryService = engine.get_memory_service("my_app")

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

Retrieve memories for a user. The service automatically normalizes Mem0's MongoDB structure (`_id`, `payload`) into a consistent API format:

```python
# Get all memories
all_memories = await memory_service.get_all(user_id="user123")
# Returns normalized format: [{"id": "...", "memory": "...", "metadata": {...}, ...}]

# Get specific memory
memory = await memory_service.get(memory_id="memory_123", user_id="user123")
# Returns normalized format: {"id": "...", "memory": "...", "metadata": {...}, ...}
# Note: memory_id can be either Mem0's _id or the normalized id field

# Get memories with filters
memories = await memory_service.get_all(
    user_id="user123",
    filters={"source": "conversation"}
)
```

**Note**: The service handles Mem0's internal MongoDB structure (`_id` as document ID, `payload` containing memory data) automatically. All methods return normalized documents with consistent `id`, `memory`, `text`, and `metadata` fields.

### Update Memory

Update existing memories using a **hybrid approach** that combines Mem0's embedding capabilities with direct MongoDB control:

**Architecture:**
- **Content Updates**: Routed via Mem0 (triggers automatic re-embedding)
- **Metadata Updates**: Routed via direct PyMongo (full control, no API limitations)
- **Return Value**: Always fetched from MongoDB (guaranteed correct structure)

```python
# Update memory content and metadata
updated = memory_service.update(
    memory_id="memory_123",
    user_id="user123",
    memory="Updated memory content",
    metadata={"updated": True, "category": "technical"}
)

# Update using messages format
updated = memory_service.update(
    memory_id="memory_123",
    user_id="user123",
    messages=[{"role": "user", "content": "Updated content"}],
    metadata={"updated": True}
)

# Update only metadata (content unchanged) - FULLY SUPPORTED
updated = memory_service.update(
    memory_id="memory_123",
    user_id="user123",
    metadata={"category": "updated", "priority": "high"}
)

# Update only content (no metadata changes)
updated = memory_service.update(
    memory_id="memory_123",
    user_id="user123",
    memory="Updated content only"
)

# Using 'data' parameter
updated = memory_service.update(
    memory_id="memory_123",
    user_id="user123",
    data="Updated content",
    metadata={"updated": True}
)
```

**Key Features:**
- **Hybrid Architecture**: Mem0 handles embeddings, MongoDB handles data persistence
- **Full Metadata Support**: Update any metadata field (not limited by Mem0 API)
- **Preserves Memory ID**: The original memory ID is maintained
- **Preserves Creation Timestamp**: `created_at` is not modified
- **Updates Timestamp**: `updated_at` is automatically set to current time
- **Recomputes Embeddings**: If content changes, the embedding vector is automatically recomputed via Mem0
- **Reliable Returns**: Always returns the actual document from MongoDB (not Mem0's response format)
- **Partial Updates**: Can update content only, metadata only, or both
- **Security**: Validates user_id ownership before allowing updates
- **Mem0 Structure Aware**: Correctly handles Mem0's MongoDB structure (`_id` as document ID, `payload` for memory data)
- **Direct MongoDB Access**: Uses PyMongo for reliable data operations, ensuring consistency
- **Normalized Responses**: All methods return consistent document structure regardless of Mem0's internal format

### Delete Memory

Remove memories:

```python
# Delete single memory
await memory_service.delete(memory_id="memory_123", user_id="user123")

# Delete all memories for user
await memory_service.delete_all(user_id="user123")
```

### Bucket Organization

Organize memories into buckets for better management:

```python
# Add memory to a bucket
memory = await memory_service.add(
    messages=[{"role": "user", "content": "I love Python programming"}],
    user_id="user123",
    bucket_id="coding:user123",
    bucket_type="general",
    metadata={"category": "coding"}
)

# Get all buckets for a user
buckets = await memory_service.get_buckets(user_id="user123")

# Get only file buckets
file_buckets = await memory_service.get_buckets(
    user_id="user123",
    bucket_type="file"
)

# Get all memories in a specific bucket
bucket_memories = await memory_service.get_bucket_memories(
    bucket_id="file:document.pdf:user123",
    user_id="user123"
)
```

### Store Both Facts and Raw Content

Store extracted facts alongside raw content for richer context:

```python
# Store both extracted facts and raw content
facts, raw_memory_id = await memory_service.add_with_raw_content(
    messages=[{"role": "user", "content": "Extract key facts from this document..."}],
    raw_content="Full document text here...",
    user_id="user123",
    bucket_id="file:document.pdf:user123",
    bucket_type="file",
    infer=True  # Extract facts
)

# Later, retrieve raw content when needed
raw_content = await memory_service.get_raw_content(
    bucket_id="file:document.pdf:user123",
    user_id="user123"
)

# Or include raw content when getting bucket memories
all_memories = await memory_service.get_bucket_memories(
    bucket_id="file:document.pdf:user123",
    user_id="user123",
    include_raw_content=True
)
```

### Bucket Types

Common bucket types:
- **`general`**: General purpose buckets (e.g., category-based)
- **`file`**: File-specific buckets (one per uploaded file)
- **`conversation`**: Conversation-specific buckets
- **`user`**: User-level buckets

```python
# General bucket (category-based)
await memory_service.add(
    messages=[{"role": "user", "content": "I prefer dark mode"}],
    user_id="user123",
    bucket_id="preferences:user123",
    bucket_type="general"
)

# File bucket
await memory_service.add(
    messages=[{"role": "user", "content": "Document content..."}],
    user_id="user123",
    bucket_id="file:report.pdf:user123",
    bucket_type="file",
    metadata={"filename": "report.pdf"}
)
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

## Architecture

### Base Class Pattern

The memory service uses an abstract base class pattern for extensibility:

```python
from mdb_engine.memory import BaseMemoryService, MemoryServiceError

# BaseMemoryService defines the interface
# Mem0MemoryService implements it (default provider)
# Future providers can inherit from BaseMemoryService
```

**Benefits:**
- **Type Safety**: Use `BaseMemoryService` for type hints
- **Extensibility**: Easy to add new providers (LangChain, custom, etc.)
- **Consistency**: All providers implement the same interface
- **Backward Compatible**: Existing code works without changes

### Creating Custom Memory Providers

To create a custom memory provider, inherit from `BaseMemoryService`:

```python
from mdb_engine.memory import BaseMemoryService, MemoryServiceError

class CustomMemoryService(BaseMemoryService):
    """Custom memory service implementation."""
    
    def __init__(self, mongo_uri: str, db_name: str, app_slug: str, config: dict | None = None):
        # Initialize your custom implementation
        pass
    
    def add(self, messages, user_id=None, metadata=None, **kwargs):
        # Implement add method
        pass
    
    # Implement all other abstract methods...
    def get_all(self, user_id=None, limit=100, filters=None, **kwargs):
        pass
    
    def search(self, query, user_id=None, limit=5, filters=None, **kwargs):
        pass
    
    def get(self, memory_id, user_id=None, **kwargs):
        pass
    
    def delete(self, memory_id, user_id=None, **kwargs):
        pass
    
    def delete_all(self, user_id=None, **kwargs):
        pass
    
    def update(self, memory_id, user_id=None, memory=None, metadata=None, **kwargs):
        pass
```

## API Reference

### BaseMemoryService

Abstract base class for all memory service implementations. Defines the standard interface.

### Mem0MemoryService

Default implementation using Mem0.ai. Inherits from `BaseMemoryService`.

#### Initialization

```python
Mem0MemoryService(
    mongo_uri: str,
    db_name: str,
    app_slug: str,
    config: dict = None  # Optional configuration
)

# Or use the factory function
from mdb_engine.memory import get_memory_service

memory_service = get_memory_service(
    mongo_uri="...",
    db_name="...",
    app_slug="...",
    config={...},
    provider="mem0"  # Default, future providers can be specified here
)
```

#### Methods

- `add(messages, user_id, metadata=None, bucket_id=None, bucket_type=None, store_raw_content=False, raw_content=None)` - Add single memory with optional bucket and raw content storage
- `add_with_raw_content(messages, raw_content, user_id, bucket_id=None, bucket_type=None)` - Store both extracted facts and raw content
- `get_buckets(user_id, bucket_type=None, limit=None)` - Get all buckets for a user
- `get_bucket_memories(bucket_id, user_id, include_raw_content=False, limit=None)` - Get all memories in a bucket
- `get_raw_content(bucket_id, user_id)` - Get raw content for a bucket
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
from mdb_engine.memory import MemoryServiceError, Mem0MemoryServiceError

try:
    memory = await memory_service.add(
        messages=[{"role": "user", "content": "Test"}],
        user_id="user123"
    )
except MemoryServiceError as e:
    # Base exception for all memory service errors
    print(f"Memory service error: {e}")
except Mem0MemoryServiceError as e:
    # Specific exception for Mem0 implementation
    print(f"Mem0 memory service error: {e}")
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
