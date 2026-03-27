# Upgrading to mdb-engine 0.10.0

**Release focus:** Remove `litellm` dependency entirely. Replace with direct native SDK calls for OpenAI, Azure OpenAI, Google Gemini, and VoyageAI. This is a **security-driven change** in response to the [litellm PyPI supply chain attack](https://futuresearch.ai/blog/litellm-pypi-supply-chain-attack/) on March 24, 2026.

> See [`bye-litellm.md`](bye-litellm.md) for the full incident report, architecture diagrams, and rationale.

---

## Quick checklist

1. `pip install --upgrade mdb-engine[ai]`
2. Remove `litellm_config` from every `manifest.json` (search for `"litellm_config"`)
3. Remove `litellm` from any app-level `requirements.txt` or `Dockerfile`
4. If using VoyageAI embeddings, ensure `VOYAGE_API_KEY` is set
5. If importing `LiteLLMEmbeddingProvider` directly, rename to `VoyageAIEmbeddingProvider`
6. Run your app — everything else is behind the scenes

---

## What changed

### litellm removed — native SDKs replace it

| Before (0.9.x) | After (0.10.0) |
|---|---|
| `litellm.acompletion()` | `openai.AsyncOpenAI` / `openai.AsyncAzureOpenAI` / `google.genai.Client` |
| `litellm.aembedding()` | `voyageai.AsyncClient` |
| `litellm.exceptions.*` | `mdb_engine.exceptions.LLM*Error` |
| `litellm_config` manifest key | Removed (use `resilience` instead) |
| `LiteLLMEmbeddingProvider` | `VoyageAIEmbeddingProvider` |
| `pip install litellm` (~200+ transitive deps) | `pip install openai google-genai voyageai` |

### Supported providers

| Provider | Chat | Embeddings | SDK |
|---|:---:|:---:|---|
| OpenAI | Yes | Yes | `openai` |
| Azure OpenAI | Yes | Yes | `openai` (`AsyncAzureOpenAI`) |
| Google Gemini | Yes | — | `google-genai` |
| VoyageAI | — | Yes | `voyageai` |

Anthropic support is planned for a future release.

---

## Breaking changes

### 1. `litellm_config` removed from manifests

**Before (0.9.x):**

```json
{
  "llm_config": {
    "providers": { "chat": "openai/gpt-4o" },
    "litellm_config": {
      "num_retries": 3,
      "request_timeout": 60
    }
  }
}
```

**After (0.10.0):**

```json
{
  "llm_config": {
    "providers": { "chat": "openai/gpt-4o" },
    "resilience": {
      "max_retries": 3,
      "timeout": 60
    }
  }
}
```

The `llm_config` schema now uses `"additionalProperties": false`, so manifests with `litellm_config` will **fail validation on startup**.

**Action:** Delete `litellm_config` from every manifest. If you relied on `num_retries` or `request_timeout`, move them to the `resilience` block (which already existed in 0.9.x and is provider-agnostic).

---

### 2. `LiteLLMEmbeddingProvider` renamed

**Before:**

```python
from mdb_engine.embeddings import LiteLLMEmbeddingProvider

provider = LiteLLMEmbeddingProvider(default_model="voyage/voyage-3")
```

**After:**

```python
from mdb_engine.embeddings import VoyageAIEmbeddingProvider

provider = VoyageAIEmbeddingProvider(default_model="voyage-3")
```

Note the model string no longer needs a `voyage/` prefix — the VoyageAI SDK handles routing natively.

**Action:** If you import `LiteLLMEmbeddingProvider` anywhere, rename it. If you use `EmbeddingProvider` or `EmbeddingService` (the standard path), no change needed — auto-detection works the same.

---

### 3. Dependencies changed

**Before:**

```txt
# requirements.txt or Dockerfile
litellm>=1.0.0
```

**After:**

```txt
# requirements.txt or Dockerfile
openai>=1.0.0
google-genai>=1.0.0
voyageai>=0.3.0
```

Or just use the extras (recommended):

```bash
pip install mdb-engine[ai]
```

**Action:** Remove `litellm` from any app-level dependency files. The `[ai]` extra now pulls `openai`, `google-genai`, and `voyageai` instead.

---

### 4. Custom exception types

If you catch litellm exceptions directly:

**Before:**

```python
from litellm.exceptions import RateLimitError, APIError
```

**After:**

```python
from mdb_engine.exceptions import LLMRateLimitError, LLMAPIError
```

Full mapping:

| litellm exception | mdb-engine exception |
|---|---|
| `litellm.exceptions.APIError` | `mdb_engine.exceptions.LLMAPIError` |
| `litellm.exceptions.AuthenticationError` | `mdb_engine.exceptions.LLMAuthenticationError` |
| `litellm.exceptions.NotFoundError` | `mdb_engine.exceptions.LLMNotFoundError` |
| `litellm.exceptions.RateLimitError` | `mdb_engine.exceptions.LLMRateLimitError` |

All four inherit from `MongoDBEngineError` (which inherits `RuntimeError`), so existing `except RuntimeError` blocks will still catch them.

---

## What did NOT change

- **`LLMService.chat_completion()`** — same signature, same return type (`str`)
- **`LLMService.chat_completion_stream()`** — same yield behavior, same `__REASONING__` markers
- **`EmbeddingService.embed()`** — same signature, same return type (`list[list[float]]`)
- **Model string format** — `provider/model` (e.g., `openai/gpt-4o`, `gemini/gemini-3-flash-preview`)
- **Environment variables** — `OPENAI_API_KEY`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `GEMINI_API_KEY`, `VOYAGE_API_KEY`
- **Manifest structure** — `llm_config.providers`, `llm_config.fallbacks`, `llm_config.temperature`, `llm_config.persona` all work the same
- **`resilience` config** — already independent of litellm; unchanged
- **`temperature.py`** — provider-aware temperature adjustment; unchanged
- **Dependency injection** — `Depends(get_llm_service)`, `Depends(get_embedding_service)` work the same

---

## New features in this release

### Programmatic fallback loop

Fallback models now run through a simple retry loop in `_LLMProvider.chat_completion()` instead of delegating to litellm's internal fallback mechanism. This gives you:

- Clear logging when a fallback is triggered (`"Model 'X' failed, trying next fallback..."`)
- Cross-provider fallbacks (e.g., primary `gemini/...`, fallback `openai/...`) that create the correct SDK client automatically
- No hidden behavior from litellm's `context_window_fallbacks` or `content_policy_fallbacks`

### New custom exceptions

Four new exception classes in `mdb_engine.exceptions`:

```python
from mdb_engine.exceptions import (
    LLMAPIError,
    LLMAuthenticationError,
    LLMNotFoundError,
    LLMRateLimitError,
)
```

All inherit from `MongoDBEngineError`, so they work with existing error handling patterns.

### Gemini thinking/reasoning via native SDK

Streaming reasoning from Gemini models (via `thinking_config`) now goes through the `google-genai` SDK directly, using `Part.thought` to detect thinking content. The `reasoning_effort` parameter maps to `ThinkingConfig(thinking_level=...)` natively.

---

## Providers that no longer work

If your app used litellm-only providers through mdb-engine, they are no longer supported:

- Anthropic (planned for future release)
- Cohere
- AWS Bedrock
- Mistral
- HuggingFace
- Ollama
- Groq
- Together AI

If you need one of these, you can implement a custom `BaseEmbeddingProvider` for embeddings or wrap the provider's SDK in a route handler for chat completions.

---

## Why this was done

On March 24, 2026, litellm versions 1.82.7 and 1.82.8 were compromised on PyPI. The malicious payload exfiltrated credentials, SSH keys, cloud configs, and attempted lateral movement into Kubernetes clusters. The attack exploited Python's `.pth` auto-execution mechanism, meaning the payload ran on **every Python process startup** where litellm was installed — not just when litellm was imported.

- [Incident report (FutureSearch)](https://futuresearch.ai/blog/litellm-pypi-supply-chain-attack/)
- [GitHub issue #24512](https://github.com/BerriAI/litellm/issues/24512)

mdb-engine's integration was well-contained behind `LLMService` and `EmbeddingProvider` abstractions, making a clean removal feasible without changing any public API.
