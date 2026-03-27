# LLM Service Module

Unified LLM service for chat completions and text generation across MDB_ENGINE applications.
Uses **native SDKs**: [OpenAI](https://github.com/openai/openai-python) (OpenAI and Azure OpenAI) and [Google Gen AI](https://github.com/googleapis/python-genai) (Gemini). Supports **OpenAI, Azure OpenAI, and Google Gemini**.

## Features

- **Three backends**: OpenAI, Azure OpenAI, and Google Gemini via their official SDKs
- **Auto-Detection**: Automatically detects provider from environment variables or manifest config
- **Unified API**: Same chat completion interface across all supported providers
- **Manifest Configuration**: Configure via `llm_config` in manifest.json
- **FastAPI Integration**: Clean dependency injection support
- **Memory Service Integration**: Works seamlessly with cognitive memory service
- **Resilience**: Optional retry, backoff, timeout, and circuit breaker via `llm_config.resilience`

## Installation

```bash
pip install openai google-genai
```

Install only the SDKs you need (e.g. `openai` if you use OpenAI/Azure only; add `google-genai` for Gemini).

## Configuration

The LLM service uses model strings in **`provider/model`** form (for example `openai/gpt-4o`, `gemini/gemini-3-flash-preview`, `azure/your-deployment-name`).

Auto-detects from environment variables or configure via manifest:

- **OpenAI**: `OPENAI_API_KEY` → default such as `openai/gpt-4o`
- **Azure OpenAI**: `AZURE_OPENAI_API_KEY` and `AZURE_OPENAI_ENDPOINT` → `azure/{deployment_name}`
- **Gemini**: `GEMINI_API_KEY` or `GOOGLE_API_KEY` → `gemini/gemini-3-flash-preview`

Enable LLM service in your `manifest.json`:

```json
{
  "llm_config": {
    "enabled": true,
    "default_model": "openai/gpt-4o"
  }
}
```

### Fallback Models (Automatic Failover)

If the primary model fails, `LLMService` tries each entry in `fallbacks` in order (each must use a supported `provider/model` and the matching env credentials):

```json
{
  "llm_config": {
    "enabled": true,
    "default_model": "gemini/gemini-3-flash-preview",
    "fallbacks": ["openai/gpt-4o-mini"]
  }
}
```

**How it works:**

1. Primary attempt: uses `default_model`
2. On failure: tries the next model in `fallbacks` (cross-provider fallbacks use the correct SDK per model prefix)
3. Uniform output: string content from the first model that succeeds

**Benefits:**

- **Redundancy**: Alternate provider when one is rate-limited or down
- **Cost**: Prefer a fast/cheap model as primary and a stronger one as fallback (or the reverse)
- **Zero extra code**: Configured in the manifest

### Resilience (Retries, Timeout, Circuit Breaker)

Tune retries, backoff, timeouts, and circuit breaking with `llm_config.resilience` (see manifest schema for all fields):

```json
{
  "llm_config": {
    "enabled": true,
    "default_model": "gemini/gemini-3-flash-preview",
    "fallbacks": ["openai/gpt-4o-mini"],
    "resilience": {
      "max_retries": 2,
      "timeout": 60,
      "backoff_base": 1.0,
      "backoff_max": 30.0
    }
  }
}
```

Or for Gemini without fallbacks:

```json
{
  "llm_config": {
    "enabled": true,
    "default_model": "gemini/gemini-3-flash-preview"
  }
}
```

Or for Azure OpenAI (use your **deployment** name, not the public model name):

```json
{
  "llm_config": {
    "enabled": true,
    "default_model": "azure/my-gpt4-deployment"
  }
}
```

> [!NOTE]
> For Azure OpenAI, the model string uses your **deployment name** (what you named it in Azure AI Studio), not the underlying model name. If you deployed `gpt-4o` as `my-gpt4-deployment`, use `azure/my-gpt4-deployment`.

### Named providers (`providers`)

For multiple logical models (e.g. chat vs extraction), use `providers` and pass `provider_name` in code—each value is still `provider/model`. See the manifest schema for `llm_config.providers`.

## Usage

### 1. FastAPI Routes (Recommended)

Use request-scoped dependencies:

```python
from fastapi import Depends
from mdb_engine import MongoDBEngine
from mdb_engine.llm.dependencies import get_llm_service_dependency

engine = MongoDBEngine(mongo_uri=..., db_name=...)
app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))

@app.post("/chat")
async def chat_endpoint(
    messages: list[dict],
    llm_service=Depends(get_llm_service_dependency),
):
    response = await llm_service.chat_completion(
        messages=messages,
        model="openai/gpt-4o"  # Optional; uses default from config if omitted
    )
    return {"response": response}
```

### 2. Basic Usage (Standalone)

```python
from mdb_engine.llm import LLMService, get_llm_service

# Initialize — auto-detects provider from environment variables
llm_service = get_llm_service(config={"default_model": "openai/gpt-4o"})

# Generate completion
response = await llm_service.chat_completion(
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is the capital of France?"}
    ]
)
print(response)  # "The capital of France is Paris."
```

### 3. With Custom Model Override

```python
from mdb_engine.llm import get_llm_service

llm_service = get_llm_service(config={"default_model": "openai/gpt-4o"})

response = await llm_service.chat_completion(
    messages=[{"role": "user", "content": "Hello!"}],
    model="gemini/gemini-3-flash-preview"  # Override for this request
)
```

### 4. Integration with Memory Service

The LLM service integrates with the cognitive memory service:

```python
from mdb_engine.llm import get_llm_service
from mdb_engine.memory.orchestrator import CognitiveEngine

llm_service = get_llm_service(config={"default_model": "openai/gpt-4o"})

cognitive_engine = CognitiveEngine(
    app_slug="my-app",
    memory_service=memory_service,
    chat_history_collection=chat_history_collection,
    llm_service=llm_service,
)
```

## Supported Providers

| Provider        | Example models |
|----------------|----------------|
| **OpenAI**     | `openai/gpt-4o`, `openai/gpt-4o-mini`, `openai/gpt-4-turbo` |
| **Azure OpenAI** | `azure/<deployment-name>` |
| **Gemini**     | `gemini/gemini-3-flash-preview`, `gemini/gemini-2.5-flash`, etc. |

## Provider-Specific Notes

### Model format

Use `provider/model`:

- `openai/gpt-4o`
- `azure/my-gpt4-deployment` (Azure deployment name — **not** the catalog model id alone)
- `gemini/gemini-3-flash-preview`

### Azure OpenAI

- **Deployment vs model name**: use `azure/<deployment>` only.
- **Environment**: `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT`, or `AZURE_API_KEY` + `AZURE_API_BASE`.
- Optional: `AZURE_OPENAI_API_VERSION` / `OPENAI_API_VERSION`.

### Environment variables

- `OPENAI_API_KEY` → OpenAI models (`openai/...`)
- `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT` (or `AZURE_API_KEY` + `AZURE_API_BASE`) → `azure/...`
- `GEMINI_API_KEY` or `GOOGLE_API_KEY` → `gemini/...`

> [!TIP]
> The Azure model string must be `azure/<your_deployment_name>` — the name from Azure AI Studio, not the raw model name.

## API Reference

### LLMService

Main service class for LLM operations.

#### Methods

- `chat_completion(messages, model=None, temperature=0.7, max_tokens=None, **kwargs) -> str`
  - Generate a chat completion response
  - `messages`: list of dicts with `role` and `content`
  - `model`: optional `provider/model` override
  - `temperature`, `max_tokens`: passed through (provider-specific rules still apply, e.g. Gemini 3 temperature)
- `chat_completion_stream(...)` — async iterator of text (and optional reasoning) chunks using the same native SDKs

### Internal: _LLMProvider

Internal provider wrapper (not public API). Resolves `provider/model`, constructs OpenAI, Azure OpenAI, or Gemini clients, and applies `resilience` when configured. Prefer `LLMService` / `get_llm_service`.

## Advanced Features

### Automatic fallback (manifest)

Same behavior as [Fallback Models](#fallback-models-automatic-failover) above: `fallbacks` is tried after the primary model fails.

### Structured output

Pass a Pydantic model as `response_format` where supported (see `LLMService.chat_completion` docstring).

### Manual fallback (application code)

```python
from mdb_engine.llm import get_llm_service

llm_service = get_llm_service(config={"default_model": "gemini/gemini-3-flash-preview"})

models = [
    "gemini/gemini-3-flash-preview",
    "openai/gpt-4o",
]

for model in models:
    try:
        response = await llm_service.chat_completion(
            messages=[{"role": "user", "content": "Hello!"}],
            model=model,
        )
        break
    except Exception as e:
        logger.warning("Model %s failed: %s, trying next...", model, e)
        continue
```

### Streaming (native SDKs via `LLMService`)

Use `chat_completion_stream` on `LLMService` (or the underlying provider) so traffic goes through the same manifest config and clients:

```python
from mdb_engine.llm import get_llm_service

llm_service = get_llm_service(config={"default_model": "gemini/gemini-3-flash-preview"})

async for chunk in llm_service.chat_completion_stream(
    messages=[{"role": "user", "content": "Tell me a short story"}],
):
    if chunk.startswith("__REASONING__:"):
        # optional reasoning trace from Gemini
        continue
    print(chunk, end="", flush=True)
```

### Temperature note for Gemini 3

Gemini 3 models (e.g. `gemini-3-flash-preview`) are tuned for `temperature=1.0`. Lower values can cause poor behavior on some tasks. The service applies provider-specific temperature adjustment; see `mdb_engine.llm.temperature`.

## Examples

See `examples/basic/chit_chat` and `examples/advanced/sso-multi-app/apps/sso-app-3` for complete examples.
