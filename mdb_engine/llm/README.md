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

| Provider        | Example models | Web search grounding (`enable_web_search`) |
|----------------|----------------|----------------|
| **OpenAI**     | `openai/gpt-4o`, `openai/gpt-4o-mini`, `openai/gpt-4-turbo` | Not supported yet (policy logs/raises/routes) |
| **Azure OpenAI** | `azure/<deployment-name>` | Not supported yet (policy logs/raises/routes) |
| **Gemini**     | `gemini/gemini-2.5-flash`, `gemini/gemini-2.5-pro`, ... | Supported on `2.5-*`; **not** on `-latest` aliases — see the [capability registry](#model-capabilities-registry) and `grounding_policy` |

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

### Web search / grounding (Gemini)

Gemini models can answer with **live web search grounding** (Google Search) and return the
source citations. Enable it with a single provider-agnostic flag — you never touch the
`google-genai` SDK directly.

```python
from mdb_engine.llm import get_llm_service

llm_service = get_llm_service(config={"providers": {"chat": "gemini/gemini-3-flash-preview"}})

# Plain text (default return type unchanged)
text = await llm_service.chat_completion(
    messages=[{"role": "user", "content": "What happened in AI this week?"}],
    provider_name="chat",
    enable_web_search=True,
)
```

#### Getting the citations back (non-streaming)

Pass `return_metadata=True` to receive a `GroundedCompletion` instead of a plain `str`:

```python
from mdb_engine.llm import GroundedCompletion

result = await llm_service.chat_completion(
    messages=[{"role": "user", "content": "What happened in AI this week?"}],
    provider_name="chat",
    enable_web_search=True,
    return_metadata=True,
)
assert isinstance(result, GroundedCompletion)
result.text          # -> str
result.citations     # -> [{"title", "uri", "domain", "redirect_uri"}, ...]
result.grounded      # -> bool (True when >=1 citation was returned)
result.model_used    # -> the model that actually answered (see grounding_policy)
result.finish_reason # -> "STOP" | "MAX_TOKENS" | "SAFETY" | ... | None
```

`return_metadata` defaults to `False`, so existing callers keep getting a `str`.

Each citation is normalized: Google returns `uri` as a
`vertexaisearch.cloud.google.com/grounding-api-redirect/...` redirect with the real publisher
in `title`, so `domain` gives you the clean host (e.g. `livemint.com`) for display while
`redirect_uri`/`uri` remain the working links.

#### Citations while streaming

Streaming mirrors the `__REASONING__:` convention: when grounding is requested and citations
are found, a single trailing `__GROUNDING__:{json}` event is emitted before the stream ends.
Callers that don't care can ignore it.

```python
import json

async for chunk in llm_service.chat_completion_stream(
    messages=[{"role": "user", "content": "What happened in AI this week?"}],
    provider_name="chat",
    enable_web_search=True,
):
    if chunk.startswith("__GROUNDING__:"):
        citations = json.loads(chunk[len("__GROUNDING__:"):])["citations"]
        # render source chips / footnotes
        continue
    if chunk.startswith("__REASONING"):
        continue  # optional thinking trace
    print(chunk, end="", flush=True)
```

> [!IMPORTANT]
> **Grounding is gated by the model, not just the engine.** The `gemini-2.5-*` family surfaces
> Google Search grounding, but the `-latest` aliases (`gemini-flash-latest`, `gemini-pro-latest`)
> **do not** — they return zero citations even when explicitly told to search. The engine encodes
> this in its [capability registry](#model-capabilities-registry); use `grounding_policy` (below)
> so `enable_web_search=True` is never a silent no-op.

> [!NOTE]
> - For **non-Gemini** providers (OpenAI/Azure), `enable_web_search=True` currently has no built-in
>   equivalent; under the default `best_effort` policy it logs and continues ungrounded.
> - Gemini does **not** allow grounding together with a JSON `response_format` (structured
>   output). When both are requested, grounding is dropped (with a warning) so the structured
>   call still succeeds.
> - A model that rejects the grounding tool degrades gracefully: the request is retried once
>   without tools instead of failing.
> - **SDK/streaming:** `google-genai` 2.x async streaming crashes on `aiohttp < 3.14`. The `ai`/`all`
>   extras pin `aiohttp>=3.14`, and the engine self-heals by forcing the httpx transport if an
>   incompatible `aiohttp` is detected.

### Grounding policy (capability-aware negotiation)

`grounding_policy` decides what happens when `enable_web_search=True` but the resolved model
can't actually ground:

| Policy | Behavior when the model can't ground |
| --- | --- |
| `"best_effort"` (default) | Log a warning and continue **ungrounded** (back-compatible). |
| `"require"` | Raise `GroundingUnsupportedError` — fail loudly instead of shipping empty citations. |
| `"auto"` | Transparently **route this turn** to a grounding-capable model for the same provider (e.g. `gemini-flash-latest` → `gemini-2.5-flash`), reported in `GroundedCompletion.model_used`. |

```python
# Keep the "-latest feel" for normal chat, borrow a 2.5 model only for grounded turns.
result = await llm_service.chat_completion(
    messages=[{"role": "user", "content": "What happened in AI this week?"}],
    model="gemini/gemini-flash-latest",
    enable_web_search=True,
    grounding_policy="auto",       # routes to gemini/gemini-2.5-flash for this turn
    return_metadata=True,
)
result.model_used  # -> "gemini/gemini-2.5-flash" (or response model id)
```

Configure the routing target in your manifest `llm_config` (defaults to `gemini/gemini-2.5-flash`):

```json
{ "llm_config": { "grounding_model": "gemini/gemini-2.5-flash" } }
```

### Typed streaming events

`stream()` is a structured alternative to `chat_completion_stream` that removes the
`startswith("__REASONING__")` / `startswith("__GROUNDING__")` string-sniffing. The legacy
sentinels remain for back-compat.

```python
from mdb_engine.llm import TextDelta, ReasoningDelta, GroundingEvent, DoneEvent

async for ev in llm_service.stream(
    messages=[{"role": "user", "content": "What happened in AI this week?"}],
    enable_web_search=True,
    grounding_policy="auto",
):
    match ev:
        case ReasoningDelta(text):  ...  # optional thinking trace
        case TextDelta(text):       print(text, end="", flush=True)
        case GroundingEvent(citations): ...  # render source chips
        case DoneEvent(grounded=g, model_used=m): ...  # "Answered with m · N sources"
```

## Model Capabilities Registry

The engine is the **single source of truth** for what each model can do, so apps don't hardcode
(and re-learn) model knowledge. Build your model selector, reasoning control, and web-search
toggle from the registry instead of a hand-maintained catalog.

```python
# Which models can ground? (for a provider dropdown / toggle)
for m in llm_service.list_models(provider="gemini", web_search=True):
    print(m.model, m.default_reasoning, m.max_input_tokens)

# Resolve any model (canonical id, bare id, alias, or unknown — always returns a value)
caps = llm_service.get_capabilities("gemini/gemini-flash-latest")
caps.web_search   # -> False (the -latest alias can't ground)
caps.thinking     # -> True

# Quick boolean checks
llm_service.supports("web_search", "gemini/gemini-2.5-flash")  # -> True
```

`ModelCapabilities` fields: `model`, `family`, `provider`, `thinking`, `default_reasoning`,
`web_search`, `vision`, `structured_output`, `max_input_tokens`, `aliases`, `notes`.

**Curated grounding matrix (verified):**

| Model | Grounds? |
| --- | --- |
| `gemini/gemini-2.5-flash` | ✅ |
| `gemini/gemini-2.5-flash-lite` | ✅ |
| `gemini/gemini-2.5-pro` | ✅ |
| `gemini/gemini-2.0-flash` | ✅ |
| `gemini/gemini-flash-latest` | ❌ |
| `gemini/gemini-pro-latest` | ❌ |
| `openai/*`, `azure/*` | ❌ (no built-in web search yet) |

**Overrides:** correct or extend the map without an engine release via manifest
`llm_config.model_overrides`:

```json
{ "llm_config": { "model_overrides": { "gemini/gemini-flash-latest": { "web_search": true } } } }
```

### Temperature note for Gemini 3

Gemini 3 models (e.g. `gemini-3-flash-preview`) are tuned for `temperature=1.0`. Lower values can cause poor behavior on some tasks. The service applies provider-specific temperature adjustment; see `mdb_engine.llm.temperature`.

## Examples

See `examples/basic/chit_chat` and `examples/advanced/sso-multi-app/apps/sso-app-3` for complete examples.
