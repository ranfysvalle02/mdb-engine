# LLM Service Module

Enterprise-grade LLM service integration for MDB_RUNTIME applications.

## Features

- **Provider Agnostic**: Switch between OpenAI, Anthropic, Gemini, VoyageAI via LiteLLM
- **Resilient**: Automatic retries with exponential backoff for rate limits and transient errors
- **Type Safe**: Returns validated Pydantic objects via Instructor
- **Observability**: Structured logging for latency, model usage, and token costs
- **Async Native**: Optimized for FastAPI/Uvicorn workers

## Installation

```bash
pip install litellm instructor pydantic pydantic-settings tenacity
```

## Configuration

Enable LLM service in your `manifest.json`:

```json
{
  "llm_config": {
    "enabled": true,
    "default_chat_model": "gpt-4o",
    "default_embedding_model": "voyage/voyage-2",
    "default_temperature": 0.0,
    "max_retries": 4
  }
}
```

## Usage

### 1. In FastAPI Routes (Recommended)

```python
from fastapi import FastAPI, Depends
from mdb_runtime.llm.dependencies import get_llm_service_dependency
from mdb_runtime.llm import LLMService

app = FastAPI()

# Set global engine during startup
from mdb_runtime.llm.dependencies import set_global_engine
set_global_engine(engine)

@app.get("/chat")
async def chat_endpoint(
    llm: LLMService = Depends(get_llm_service_dependency("my_app"))
):
    response = await llm.chat("Tell me a joke")
    return {"response": response}
```

### 2. Direct Access via Engine

```python
from mdb_runtime.core.engine import RuntimeEngine

engine = RuntimeEngine(...)
await engine.initialize()

# Get LLM service for an app
llm_service = engine.get_llm_service("my_app")
if llm_service:
    response = await llm_service.chat("Hello, world!")
```

### 3. Structured Extraction (Type-Safe)

```python
from pydantic import BaseModel

class UserInfo(BaseModel):
    name: str
    age: int
    email: str

# Extract structured data from text
user = await llm_service.extract(
    prompt="John Doe is 30 years old and his email is john@example.com",
    schema=UserInfo
)

print(user.name)  # "John Doe"
print(user.age)   # 30
```

### 4. Embeddings (Vector Search)

```python
# Single document
vector = await llm_service.embed("Hello, world!")
# Returns: [[0.1, 0.2, ...]]

# Batch embedding (faster)
documents = ["Apple", "Banana", "Cherry"]
vectors = await llm_service.embed(documents)
# Returns: [[0.1, ...], [0.2, ...], [0.3, ...]]
```

### 5. Model Override

```python
# Use different model for specific call
response = await llm_service.chat(
    "Tell me a joke",
    model="claude-3-opus-20240229"
)

# Different embedding model
vectors = await llm_service.embed(
    "Hello",
    model="text-embedding-3-small"
)
```

## API Reference

### LLMService Methods

#### `extract(prompt, schema, model=None, **kwargs) -> T`
Extract structured data from text using Pydantic schema.

#### `chat(prompt, model=None, **kwargs) -> str`
Generate raw text response.

#### `embed(text, model=None) -> List[List[float]]`
Generate vector embeddings.

## Environment Variables

The service automatically reads API keys from environment variables:

- `OPENAI_API_KEY` - OpenAI API key
- `ANTHROPIC_API_KEY` - Anthropic API key
- `VOYAGE_API_KEY` - VoyageAI API key
- `COHERE_API_KEY` - Cohere API key

## Error Handling

All LLM operations raise `LLMServiceError` on failure:

```python
from mdb_runtime.llm import LLMServiceError

try:
    response = await llm_service.chat("Hello")
except LLMServiceError as e:
    # Handle error (rate limits, API errors, etc.)
    print(f"LLM error: {e}")
```

## Supported Models

Via LiteLLM, supports:
- **OpenAI**: gpt-4o, gpt-3.5-turbo, text-embedding-3-small, etc.
- **Anthropic**: claude-3-opus, claude-3-sonnet, claude-3-haiku
- **Google**: gemini/gemini-pro, gemini/gemini-pro-vision
- **VoyageAI**: voyage/voyage-2, voyage/voyage-large-2
- **Cohere**: cohere/embed-english-v3.0

See [LiteLLM documentation](https://docs.litellm.ai/) for full model list.

