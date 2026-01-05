# Embeddings Service Module

Semantic text splitting and embedding generation for MDB_ENGINE applications.

## Features

- **Semantic Text Splitting**: Rust-based semantic-text-splitter for intelligent chunking
- **OpenAI & AzureOpenAI Support**: Auto-detects provider from environment variables
- **Token-Aware**: Never exceeds model token limits
- **Batch Processing**: Efficient batch embedding generation
- **MongoDB Integration**: Built-in support for storing embeddings with metadata
- **Request-Scoped Dependencies**: Clean FastAPI integration via `mdb_engine.dependencies`

## Installation

```bash
pip install semantic-text-splitter openai
```

## Configuration

The embedding service auto-detects the provider from environment variables (same logic as mem0):

- **OpenAI**: Requires `OPENAI_API_KEY`
- **AzureOpenAI**: Requires `AZURE_OPENAI_API_KEY` and `AZURE_OPENAI_ENDPOINT`

Enable embedding service in your `manifest.json`:

```json
{
  "embedding_config": {
    "enabled": true,
    "default_embedding_model": "text-embedding-3-small",
    "max_tokens_per_chunk": 1000,
    "tokenizer_model": "gpt-3.5-turbo"
  }
}
```

## Usage

### 1. FastAPI Routes (Recommended)

Use request-scoped dependencies from `mdb_engine.dependencies`:

```python
from fastapi import Depends
from mdb_engine import MongoDBEngine
from mdb_engine.dependencies import get_embedding_service, get_scoped_db

engine = MongoDBEngine(mongo_uri=..., db_name=...)
app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))

@app.post("/embed")
async def embed_endpoint(
    db=Depends(get_scoped_db),
    embedding_service=Depends(get_embedding_service),
):
    # Services are automatically bound to the current app
    result = await embedding_service.process_and_store(
        text_content="Hello world",
        source_id="doc_1",
        collection=db.knowledge_base,
    )
    return {"chunks_created": result["chunks_created"]}

@app.post("/search")
async def search(
    query: str,
    db=Depends(get_scoped_db),
    embedding_service=Depends(get_embedding_service),
):
    # Generate query embedding
    vectors = await embedding_service.embed_chunks([query])
    # Use vectors for vector search...
    return {"query_vector_dims": len(vectors[0])}
```

### 2. Basic Usage (Standalone)

```python
from mdb_engine.embeddings import EmbeddingService

# Initialize - auto-detects OpenAI or AzureOpenAI from environment variables
embedding_service = EmbeddingService()

# Chunk text
chunks = await embedding_service.chunk_text(
    text_content="Your long document here...",
    max_tokens=1000
)

# Generate embeddings
vectors = await embedding_service.embed_chunks(chunks, model="text-embedding-3-small")
```

### 3. Process and Store in MongoDB

```python
from mdb_engine.embeddings import EmbeddingService

embedding_service = EmbeddingService()

# Process text and store in MongoDB
result = await embedding_service.process_and_store(
    text_content="Your long document here...",
    source_id="doc_101",
    collection=db.knowledge_base,
    max_tokens=1000,
    metadata={"source": "document.pdf", "page": 1}
)

print(f"Created {result['chunks_created']} chunks")
```

### 4. Utility Function (Background Tasks/CLI)

For use outside of FastAPI request handlers:

```python
from mdb_engine.embeddings.dependencies import get_embedding_service_for_app

# In a background task or CLI tool
service = get_embedding_service_for_app("my_app", engine)
if service:
    embeddings = await service.embed_chunks(["Hello world"])
```

### 5. Explicit Provider

```python
from mdb_engine.embeddings import EmbeddingService, OpenAIEmbeddingProvider, EmbeddingProvider

# Use OpenAI explicitly
openai_provider = OpenAIEmbeddingProvider(default_model="text-embedding-3-small")
provider = EmbeddingProvider(embedding_provider=openai_provider)
embedding_service = EmbeddingService(embedding_provider=provider)
```

## Environment Variables

### OpenAI
```bash
export OPENAI_API_KEY="sk-..."
```

### AzureOpenAI
```bash
export AZURE_OPENAI_API_KEY="..."
export AZURE_OPENAI_ENDPOINT="https://your-resource.openai.azure.com/"
export AZURE_OPENAI_API_VERSION="2024-02-15-preview"  # Optional
```

## EmbeddingService Methods

### `chunk_text(text_content, max_tokens=None, tokenizer_model=None)`

Split text into semantic chunks.

```python
chunks = await service.chunk_text("Long document...", max_tokens=1000)
```

### `embed_chunks(chunks, model=None)`

Generate embeddings for text chunks.

```python
vectors = await service.embed_chunks(chunks, model="text-embedding-3-small")
```

### `process_and_store(text_content, source_id, collection, ...)`

Process text and store chunks with embeddings in MongoDB.

```python
result = await service.process_and_store(
    text_content="Long document...",
    source_id="doc_101",
    collection=db.knowledge_base
)
```

### `process_text(text_content, max_tokens=None, ...)`

Process text and return chunks with embeddings (without storing).

```python
results = await service.process_text("Long document...")
```

## Supported Models

- **OpenAI**: `text-embedding-3-small`, `text-embedding-3-large`, `text-embedding-ada-002`
- **AzureOpenAI**: Any Azure OpenAI embedding deployment (e.g., `text-embedding-3-small`)

## Error Handling

All embedding operations raise `EmbeddingServiceError` on failure:

```python
from mdb_engine.embeddings import EmbeddingServiceError

try:
    vectors = await service.embed_chunks(["Hello"])
except EmbeddingServiceError as e:
    print(f"Embedding failed: {e}")
```

## Notes

- The embedding service uses the same auto-detection logic as mem0 for consistency
- LLM functionality (chat completions, structured extraction) should be implemented directly at the example level using the OpenAI SDK or your preferred provider
- For memory functionality, use `mdb_engine.memory.Mem0MemoryService` which handles embeddings and LLM via environment variables
