# Goodbye, litellm

On **March 24, 2026**, litellm versions **1.82.7** and **1.82.8** were published to PyPI containing malware. No corresponding tag or release existed on GitHub — the package was uploaded directly to PyPI, bypassing normal release processes.

**mdb-engine has removed litellm entirely** and replaced it with direct, native SDK calls.

---

## What happened

The compromised litellm releases contained a `.pth` file (`litellm_init.pth`) that executes automatically on every Python process startup when litellm is installed. The payload operates in three stages:

1. **Collection** — Harvests SSH keys, `.env` files, AWS/GCP/Azure credentials, Kubernetes configs, database passwords, `.gitconfig`, shell history, crypto wallet files, and anything matching common secret patterns.
2. **Exfiltration** — Encrypts collected data with a hardcoded RSA public key and POSTs it to `https://models.litellm.cloud/` (not part of legitimate litellm infrastructure).
3. **Lateral movement** — If a Kubernetes service account token is present, reads all cluster secrets, attempts to create privileged pods on every node in `kube-system`, and installs a persistent backdoor via systemd.

The attack was first discovered by [FutureSearch](https://futuresearch.ai/blog/litellm-pypi-supply-chain-attack/) when the package was pulled in as a transitive dependency by an MCP plugin running inside Cursor. The `.pth` launcher also created an accidental fork bomb due to recursive interpreter startup.

### Key links

- **Incident report**: [futuresearch.ai/blog/litellm-pypi-supply-chain-attack](https://futuresearch.ai/blog/litellm-pypi-supply-chain-attack/)
- **GitHub issue**: [BerriAI/litellm#24512](https://github.com/BerriAI/litellm/issues/24512) (closed as "not planned" by owner, spammed by bots)
- **PyPI security**: Compromised versions were eventually yanked after report to `security@pypi.org`

---

## What changed in mdb-engine

### Before

```
LLMService → _LLMProvider → litellm.acompletion() → OpenAI / Azure / Gemini APIs
EmbeddingProvider → LiteLLMEmbeddingProvider → litellm.aembedding() → Various APIs
```

### After

```
LLMService → _LLMProvider → Native SDK dispatch:
  ├── openai.AsyncOpenAI          (OpenAI)
  ├── openai.AsyncAzureOpenAI     (Azure OpenAI)
  └── google.genai.Client         (Google Gemini)

EmbeddingProvider → Provider-specific classes:
  ├── OpenAIEmbeddingProvider      (openai SDK)
  ├── AzureOpenAIEmbeddingProvider (openai SDK)
  └── VoyageAIEmbeddingProvider    (voyageai SDK)
```

### Summary of changes

| Area | Change |
|------|--------|
| `mdb_engine/llm/service.py` | Replaced `litellm.acompletion` with native SDK routing (`openai`, `google-genai`) |
| `mdb_engine/embeddings/service.py` | Replaced `LiteLLMEmbeddingProvider` with `VoyageAIEmbeddingProvider` |
| `mdb_engine/memory/conflict.py` | Replaced `litellm.exceptions` with `mdb_engine.exceptions` |
| `mdb_engine/memory/cognitive.py` | Same exception swap; simplified `_init_llm_client` |
| `mdb_engine/exceptions.py` | Added `LLMAPIError`, `LLMAuthenticationError`, `LLMNotFoundError`, `LLMRateLimitError` |
| `pyproject.toml` | Removed `litellm`; added `google-genai`, `voyageai` |
| Manifest schema | Removed `litellm_config` key entirely |
| All docstrings, docs, examples | Purged litellm references |

---

## Supported providers

| Provider | Chat completions | Embeddings | SDK |
|----------|:---:|:---:|-----|
| OpenAI | Yes | Yes | `openai` |
| Azure OpenAI | Yes | Yes | `openai` (AsyncAzureOpenAI) |
| Google Gemini | Yes | — | `google-genai` |
| VoyageAI | — | Yes | `voyageai` |

Anthropic support is planned for a future release.

---

## Migration guide

### If you had `litellm_config` in your manifest

Remove it. The `litellm_config` key is no longer recognized. Retries and timeouts are handled by the `resilience` config block (which already existed independently of litellm):

```json
{
  "llm_config": {
    "providers": {
      "chat": "openai/gpt-4o"
    },
    "resilience": {
      "max_retries": 3,
      "timeout": 60
    }
  }
}
```

### If you used litellm-only providers (Cohere, Bedrock, Mistral, etc.)

These are no longer supported out of the box. You have two options:

1. Switch to a supported provider (OpenAI, Azure, Gemini, VoyageAI)
2. Implement a custom `BaseEmbeddingProvider` subclass for your provider

### Model string format

The `provider/model` convention is unchanged:

```
openai/gpt-4o
azure/my-deployment
gemini/gemini-3-flash-preview
```

### Dependencies

```bash
# Before
pip install mdb-engine[ai]  # pulled in litellm

# After (same command, different deps)
pip install mdb-engine[ai]  # pulls in openai, google-genai, voyageai
```

---

## What we gained

- **No supply chain risk** from a large, heavily-targeted dependency
- **Faster installs** — litellm's dependency tree was enormous
- **Full control** over API calls, error handling, and response parsing
- **Simpler debugging** — no middleware between us and the provider SDKs
- **Smaller attack surface** — fewer transitive dependencies

## What we intentionally dropped

- The "100+ providers" claim — we now explicitly support 4 providers
- Automatic litellm-level retries — replaced by our own `resilience` config
- `litellm_config` manifest key — replaced by `resilience` config
- Cohere, Bedrock, Mistral, HuggingFace, Ollama, Groq, Together AI embedding support via litellm
- Anthropic chat support (planned for future addition)

---

*This change was made in response to a real supply chain attack. Trust is earned, and dependencies are liabilities. We chose to own our provider integrations rather than delegate them to a single point of failure.*
