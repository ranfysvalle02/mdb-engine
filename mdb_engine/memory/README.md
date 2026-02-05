# Memory Service Module

Native MongoDB Atlas Vector Search integration for intelligent memory management in MDB_ENGINE applications. Provides semantic memory storage, retrieval, and inference capabilities with full control over the stack.

## 🎉 Features

### Core Capabilities

- **🏗️ BaseMemoryService**: Abstract base class defining the memory service interface
- **🔌 Provider Extensibility**: Easy to add new memory providers
- **✅ Multiple Implementations**: CustomMemoryService (basic) and CognitiveMemoryService (advanced)
- **📝 Type Safety**: Better IDE support and type checking with abstract base class
- **🎯 Consistent API**: All memory providers implement the same interface

### CustomMemoryService (Default)

- **Native MongoDB Atlas Vector Search**: Direct integration with MongoDB
- **Intelligent Fact Extraction**: Uses LLM to extract atomic facts from conversations
- **Embedding Service Integration**: Uses mdb_engine.embeddings for vector generation
- **Metadata Support**: Full support for bucket_id, bucket_type, and custom metadata
- **Automatic Re-embedding**: Updates trigger automatic re-embedding

### CognitiveMemoryService (Advanced)

- **Importance Scoring**: AI evaluates memory significance (0.1-1.0 scale)
- **Memory Reinforcement**: Similar memories strengthen existing memories
- **Memory Decay**: Less relevant memories fade over time
- **Memory Merging**: Related memories are combined intelligently
- **Memory Pruning**: Least important memories removed when capacity exceeded
- **Access Tracking**: Tracks how often memories are accessed
- **Effective Importance**: Combines raw importance with access frequency

### Cognitive Architecture

- **Short-Term Memory (STM)**: Chat history for immediate context
- **Long-Term Memory (LTM)**: Vector store for semantic retrieval
- **CognitiveEngine**: Orchestrates STM + LTM for complete RAG pipeline
- **Auto-Summarization**: Automatically summarizes long sessions

## Installation

The memory module requires:

```bash
pip install pymongo openai
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
export AZURE_OPENAI_API_VERSION="2024-02-01"  # Optional
export AZURE_OPENAI_DEPLOYMENT_NAME="gpt-4o"  # Optional, for LLM
```

### Manifest Configuration

Enable memory service in your `manifest.json`:

```json
{
  "slug": "my_app",
  "llm_config": {
    "enabled": true,
    "default_model": "gemini/gemini-3-flash-preview"
  },
  "memory_config": {
    "enabled": true,
    "collection_name": "memories",
    "embedding_model": "text-embedding-3-small",
    "embedding_dims": 1536,
    "chat_model": "gpt-4o",
    "memory_llm_model": "gemini/gemini-3-flash-preview",  // Inherits from llm_config.default_model if not set
    "temperature": 0,
    "infer": true
  }
}
```

**LLM Model Inheritance**: The memory service automatically inherits the LLM model from `llm_config.default_model`. If `memory_config.memory_llm_model` is not explicitly set, it will use the app's default LLM model. This ensures consistent LLM usage across all services (memory, graph, entity extraction, etc.).

**Service-Specific Override**: You can override the model for memory operations only by setting `memory_config.memory_llm_model` explicitly.

### Temperature Configuration

Temperature controls the randomness of LLM responses in memory operations (fact extraction, importance assessment, memory merging). 

- **Default**: `0` (deterministic output)
- **Range**: `0.0` to `2.0`
- **Configuration**: Can be set via:
  1. **Manifest**: `"temperature": 0` in `memory_config`
  2. **Environment Variable**: `MEMORY_LLM_TEMPERATURE=0`

```bash
# Via environment variable
export MEMORY_LLM_TEMPERATURE=0
```

```json
// Via manifest.json
{
  "memory_config": {
    "temperature": 0
  }
}
```

### Cognitive Memory Configuration

For advanced cognitive features:

```json
{
  "slug": "my_app",
  "memory_config": {
    "enabled": true,
    "provider": "cognitive",
    "max_depth": 50,
    "similarity_threshold": 0.7,
    "reinforcement_factor": 1.1,
    "decay_factor": 0.99,
    "merge_threshold_low": 0.7,
    "merge_threshold_high": 0.85
  }
}
```

## Usage

### Basic Usage

```python
from mdb_engine import MongoDBEngine

# Initialize engine
engine = MongoDBEngine(mongo_uri="...", db_name="...")
await engine.initialize()

# Get memory service (automatically configured from manifest)
memory_service = engine.get_memory_service("my_app")

# Add memory
memories = memory_service.add(
    messages=[{"role": "user", "content": "I love Python programming"}],
    user_id="user123"
)

# Search memories
results = memory_service.search(
    query="What does the user like?",
    user_id="user123",
    limit=5
)

# Get all memories for user
all_memories = memory_service.get_all(user_id="user123")
```

### Cognitive Memory Service

```python
from mdb_engine.memory import CognitiveMemoryService

# Initialize with cognitive features
memory_service = CognitiveMemoryService(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database",
    app_slug="my_app",
    config={
        "max_depth": 50,
        "similarity_threshold": 0.7,
    }
)

# Add memory (automatic importance assessment, reinforcement, merging)
memories = memory_service.add(
    messages="User prefers dark mode",
    user_id="user123"
)

# Search (ranked by effective importance)
results = memory_service.search(
    query="user preferences",
    user_id="user123",
    limit=5
)
```

### Cognitive Engine (STM + LTM)

```python
from mdb_engine.memory import CognitiveEngine
from openai import OpenAI

# Initialize
engine = CognitiveEngine(
    mongo_uri="...",
    db_name="...",
    app_slug="my_app",
    llm_client=OpenAI(api_key="..."),
)

# Chat with automatic STM + LTM orchestration
result = engine.chat(
    user_id="user_123",
    session_id="conversation:55",
    user_query="What's my favorite color?",
)

print(result["response"])  # AI response
print(result["ltm_memories"])  # Relevant facts retrieved
print(result["stm_context"])  # Chat history used
```

## API Reference

### BaseMemoryService Methods

All memory services implement these methods:

- `add()` - Add memories with automatic fact extraction
- `inject()` - Direct injection bypassing LLM
- `search()` - Semantic search with filtering
- `get()` - Retrieve single memory by ID
- `get_all()` - Retrieve all memories with filtering
- `update()` - Update memory with automatic re-embedding
- `delete()` - Delete single memory
- `delete_all()` - Delete all memories for a user

## MongoDB Atlas Vector Search Setup

**✅ Automatic Index Management**: The memory service **automatically creates and manages its own vector search index**. You do NOT need to manually create the index in MongoDB Atlas.

When you enable the memory service in your manifest, it will:

1. **Auto-create the vector search index** on startup with:
   - Vector field: `embedding` (with dimensions from `embedding_model_dims`)
   - Filter field: `user_id` (required for user-scoped vector search queries)
   - Similarity: `cosine`
2. **Auto-generate the index name** as `{collection_name}_vector_index` (e.g., `my_app_memories_vector_index`)
3. **Auto-update existing indexes** if they're missing the `user_id` filter
4. **Wait for the index to be queryable** before allowing searches

**⚠️ Important**: Do NOT manually define memory collection indexes in `managed_indexes`. The engine will raise an error if you try to manually manage memory indexes, as the memory service needs full control over its indexes.

**Why `user_id` filter?**: MongoDB Atlas Vector Search requires filter fields to be explicitly defined in the index. The `user_id` filter enables efficient user-scoped queries, which is essential for memory isolation and privacy.

**Manual Index Creation (Not Recommended)**

If you need to manually create the index (not recommended), the configuration would be:

```json
{
  "fields": [
    {
      "numDimensions": 1536,
      "path": "embedding",
      "similarity": "cosine",
      "type": "vector"
    },
    {
      "path": "user_id",
      "type": "filter"
    },
    {
      "path": "metadata.bucket_id",
      "type": "filter"
    },
    {
      "path": "metadata.bucket_type",
      "type": "filter"
    }
  ]
}
```

## Documentation

- [Cognitive Architecture](../../docs/COGNITIVE_ARCHITECTURE.md)
- [Cognitive Memory Service](../../docs/COGNITIVE_MEMORY.md)
- [Base Memory Service API](base.py)

## See Also

- [Embedding Service](../embeddings/README.md)
- [MongoDB Engine Core](../core/README.md)
