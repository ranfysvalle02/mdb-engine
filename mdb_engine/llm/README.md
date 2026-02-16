# LLM Service Module

Unified LLM service for chat completions and text generation across MDB_ENGINE applications.
Powered by [LiteLLM](https://github.com/BerriAI/litellm) - supports 100+ LLM providers.

## Features

- **100+ Provider Support**: OpenAI, Azure OpenAI, Anthropic, Google Gemini, Cohere, HuggingFace, Bedrock, Vertex AI, Ollama, Groq, Together AI, and 90+ more
- **Auto-Detection**: Automatically detects provider from environment variables or manifest config
- **Unified API**: Same OpenAI-compatible interface across all providers
- **Manifest Configuration**: Configure via `llm_config` in manifest.json
- **FastAPI Integration**: Clean dependency injection support
- **Memory Service Integration**: Works seamlessly with cognitive memory service
- **Exception Mapping**: LiteLLM maps all provider exceptions to OpenAI-compatible exceptions

## Installation

```bash
pip install litellm
```

## Configuration

The LLM service uses LiteLLM model format: `provider/model` (e.g., `openai/gpt-4o`, `gemini/gemini-3-flash-preview`).

Auto-detects from environment variables or configure via manifest:

- **OpenAI**: Requires `OPENAI_API_KEY` → uses `openai/gpt-4o`
- **Azure OpenAI**: Requires `AZURE_OPENAI_API_KEY` and `AZURE_OPENAI_ENDPOINT` → uses `azure/{deployment_name}`
- **Gemini**: Requires `GEMINI_API_KEY` → uses `gemini/gemini-3-flash-preview`
- **Anthropic**: Requires `ANTHROPIC_API_KEY` → uses `anthropic/claude-sonnet-4-20250514`
- **And 95+ more providers** - see [LiteLLM docs](https://docs.litellm.ai/docs/providers)

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

LiteLLM supports automatic failover to backup models if the primary model fails. This provides redundancy without manual error handling:

```json
{
  "llm_config": {
    "enabled": true,
    "default_model": "gemini/gemini-3-flash-preview",
    "fallbacks": ["gpt-4o-mini"]
  }
}
```

**How it works:**
1. Primary attempt: Tries `gemini/gemini-3-flash-preview`
2. Automatic switch: If Gemini returns `429 (Rate Limit)` or `500 (Server Error)`, LiteLLM automatically tries `gpt-4o-mini`
3. Further fallbacks: If that fails, tries `claude-3-5-haiku-20241022`
4. Uniform output: Regardless of which model responds, you get the same response format

**Benefits:**
- **Redundancy**: No single point of failure
- **Timeouts**: Automatically switches if a model is hanging
- **Cost optimization**: Put cheapest model first, most expensive last
- **Zero code changes**: Works automatically with existing code

### LiteLLM Configuration Options

Pass LiteLLM configuration options directly through `litellm_config`:

```json
{
  "llm_config": {
    "enabled": true,
    "default_model": "gemini/gemini-3-flash-preview",
    "fallbacks": ["gpt-4o-mini"],
    "litellm_config": {
      "num_retries": 2,
      "request_timeout": 60,
      "max_budget": 0.01
    }
  }
}
```

Common options:
- `num_retries`: Number of retries per request (default: 0)
- `request_timeout`: Request timeout in seconds (default: 600)
- `max_budget`: Maximum budget per request
- `drop_params`: Drop unsupported parameters

See [LiteLLM Configuration Docs](https://docs.litellm.ai/docs/completion/configuration) for full list.

Or for Gemini without fallbacks:

```json
{
  "llm_config": {
    "enabled": true,
    "default_model": "gemini/gemini-3-flash-preview"
  }
}
```

Or for Azure OpenAI (use your deployment name, not model name):

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

Or for Anthropic:

```json
{
  "llm_config": {
    "enabled": true,
    "default_model": "anthropic/claude-sonnet-4-20250514"
  }
}
```

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
        model="gpt-4o"  # Optional, uses default from config if not specified
    )
    return {"response": response}
```

### 2. Basic Usage (Standalone)

```python
from mdb_engine.llm import LLMService, get_llm_service

# Initialize - auto-detects provider from environment variables
llm_service = get_llm_service(config={"default_model": "gpt-4o"})

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

# Override model per request
llm_service = get_llm_service(config={"default_model": "openai/gpt-4o"})

response = await llm_service.chat_completion(
    messages=[{"role": "user", "content": "Hello!"}],
    model="anthropic/claude-sonnet-4-20250514"  # Override for this request
)
```

### 4. Integration with Memory Service

The LLM service integrates seamlessly with the cognitive memory service:

```python
from mdb_engine.llm import get_llm_service
from mdb_engine.memory.orchestrator import CognitiveEngine

# Get LLM service
llm_service = get_llm_service(config={"default_model": "gpt-4o"})

# Use with CognitiveEngine
cognitive_engine = CognitiveEngine(
    app_slug="my-app",
    memory_service=memory_service,
    chat_history_collection=chat_history_collection,
    llm_service=llm_service,
)
```

## Supported Providers

LiteLLM supports 100+ providers. Common examples:

- **OpenAI**: `openai/gpt-4o`, `openai/gpt-4-turbo`, `openai/gpt-3.5-turbo`
- **Azure OpenAI**: `azure/gpt-4o`, `azure/gpt-35-turbo` (uses deployment names)
- **Anthropic**: `anthropic/claude-sonnet-4-20250514`, `anthropic/claude-opus-3`
- **Gemini**: `gemini/gemini-3-flash-preview`, `gemini/gemini-pro`
- **Cohere**: `cohere/command-r-plus`, `cohere/command-r`
- **HuggingFace**: `huggingface/meta-llama/Llama-2-70b-chat-hf`
- **Bedrock**: `bedrock/anthropic.claude-3-sonnet-20240229-v1:0`
- **Vertex AI**: `vertex_ai/gemini-pro`
- **Ollama**: `ollama/llama2`
- **Groq**: `groq/llama-3.1-70b-versatile`
- **Together AI**: `together_ai/meta-llama/Llama-3-70b-chat-hf`

See [LiteLLM Supported Providers](https://docs.litellm.ai/docs/providers) for the complete list.

## Provider-Specific Notes

### Model Format
Use LiteLLM's format: `provider/model`. Examples:
- `openai/gpt-4o`
- `azure/my-gpt4-deployment` (Azure deployment name - **not** the model name!)
- `gemini/gemini-3-flash-preview`
- `anthropic/claude-sonnet-4-20250514`

### Azure OpenAI Important Notes
- **Deployment Name vs Model Name**: Azure uses deployment names, not model names
  - ✅ Correct: `azure/my-gpt4-deployment` (your deployment name)
  - ❌ Wrong: `azure/gpt-4o` (model name - will fail with `DeploymentNotFound`)
- **Environment Variables**: LiteLLM supports both formats:
  - `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT` (our current format)
  - `AZURE_API_KEY` + `AZURE_API_BASE` (alternative format)
- **Deployment Name**: Set via `AZURE_OPENAI_DEPLOYMENT_NAME` env var or specify in model string

### Environment Variables
LiteLLM automatically detects providers from environment variables:
- `OPENAI_API_KEY` → `openai/*`
- **Azure OpenAI** (supports both formats):
  - `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT` → `azure/*`
  - `AZURE_API_KEY` + `AZURE_API_BASE` → `azure/*`
  - **Important**: Use deployment name, not model name (e.g., `azure/my-gpt4-deployment`)
- `ANTHROPIC_API_KEY` → `anthropic/*`
- `GEMINI_API_KEY` → `gemini/*`
- And many more - see [LiteLLM environment variables](https://docs.litellm.ai/docs/)

> [!TIP]
> **Azure OpenAI Deployment Names**: The model string must be `azure/<your_deployment_name>`. The deployment name is what you chose when deploying the model in Azure AI Studio, not the model name itself. For example, if you deployed `gpt-4o` as `my-gpt4-deployment`, use `azure/my-gpt4-deployment` in your config.

## API Reference

### LLMService

Main service class for LLM operations.

#### Methods

- `chat_completion(messages, model=None, temperature=0.7, max_tokens=None, **kwargs) -> str`
  - Generate a chat completion response
  - `messages`: List of message dicts with 'role' and 'content' keys
  - `model`: Optional model identifier (overrides default)
  - `temperature`: Sampling temperature (0.0-2.0)
  - `max_tokens`: Maximum tokens to generate

### Internal: _LLMProvider

Internal provider wrapper (not part of public API). Auto-detects from environment variables or uses configured LiteLLM model string. Use `LLMService` instead.

## Advanced Features

### Automatic Fallback (Built-in)

LiteLLM provides **automatic failover** - no manual error handling needed! Configure fallbacks in your manifest:

```json
{
  "llm_config": {
    "enabled": true,
    "default_model": "gemini/gemini-3-flash-preview",
    "fallbacks": ["gpt-4o-mini"]
  }
}
```

**How it works:**
1. **Primary attempt**: Tries `gemini/gemini-3-flash-preview`
2. **Automatic switch**: If Gemini returns `429 (Rate Limit)` or `500 (Server Error)`, LiteLLM automatically tries `gpt-4o-mini`
3. **Further fallbacks**: If that fails, tries `claude-3-5-haiku-20241022`
4. **Uniform output**: Regardless of which model responds, you get the same response format

**Benefits:**
- ✅ **Redundancy**: No single point of failure
- ✅ **Timeouts**: Automatically switches if a model is hanging (configurable via `litellm_config.request_timeout`)
- ✅ **Cost optimization**: Put cheapest model first, most expensive last
- ✅ **Zero code changes**: Works automatically with existing code

**Example with Structured Output:**

```python
from mdb_engine.llm import get_llm_service
from pydantic import BaseModel

class Recipe(BaseModel):
    recipe_name: str
    ingredients: list[str]
    prep_time_minutes: int

# Service automatically uses fallbacks configured in manifest
llm_service = get_llm_service()

response_text = await llm_service.chat_completion(
    messages=[{"role": "user", "content": "Extract recipe info"}],
    response_format=Recipe  # Pydantic model for structured output
)

recipe = Recipe.model_validate_json(response_text)
# Works regardless of which model (primary or fallback) responded!
```

### Manual Fallback Strategy (Legacy)

If you need manual control, you can still implement fallback by catching exceptions:

```python
from mdb_engine.llm import get_llm_service

llm_service = get_llm_service(config={"default_model": "gemini/gemini-3-flash-preview"})

models = [
    "gemini/gemini-3-flash-preview",
    "openai/gpt-4o",
    "anthropic/claude-sonnet-4-20250514"
]

for model in models:
    try:
        response = await llm_service.chat_completion(
            messages=[{"role": "user", "content": "Hello!"}],
            model=model
        )
        break  # Success, exit loop
    except Exception as e:
        logger.warning(f"Model {model} failed: {e}, trying next...")
        continue
```

### Streaming

LiteLLM supports streaming responses. You can use it directly with LiteLLM:

```python
import litellm
from litellm import completion

response = completion(
    model="gemini/gemini-3-flash-preview",
    messages=[{"role": "user", "content": "Tell me a story"}],
    stream=True
)

for chunk in response:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

### Temperature Warning for Gemini 3

⚠️ **Important**: Gemini 3 models (like `gemini-3-flash-preview`) work best with `temperature=1.0`. Setting temperature < 1.0 can cause infinite loops, degraded reasoning, and failures on complex tasks.

```python
# ✅ Recommended for Gemini 3
response = await llm_service.chat_completion(
    messages=[{"role": "user", "content": "Hello!"}],
    model="gemini/gemini-3-flash-preview",
    temperature=1.0  # Default, recommended
)

# ⚠️ Not recommended for Gemini 3
response = await llm_service.chat_completion(
    messages=[{"role": "user", "content": "Hello!"}],
    model="gemini/gemini-3-flash-preview",
    temperature=0.7  # Can cause issues
)
```

## Examples

See `examples/basic/chit_chat` and `examples/advanced/sso-multi-app/apps/sso-app-3` for complete examples.
