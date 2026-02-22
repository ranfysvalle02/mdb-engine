---
name: mdb-memory-service
description: Guide for using mdb-engine's memory service, presets, Perfect Brain, ChatEngine, and embedding/LLM services. Use when building AI features with memory, adding memory_config to manifests, using presets (basic/smart/full), wiring Perfect Brain components, or working with ChatEngine.
---

# MDB-Engine Memory Service

## 1. Manifest Config (Simplest to Most Complex)

### Shorthand — just turn it on

```json
{"memory_config": true}
```

All defaults: `text-embedding-3-small`, 1536d, cognitive enabled, infer=true.

### Named Preset

```json
{"memory_config": "smart"}
```

| Preset | What's enabled |
|--------|---------------|
| `"basic"` | Infer only, no cognitive |
| `"smart"` | Cognitive + categories + salience gate + memory types |
| `"full"` | Everything: reflection, graph, emotion, conflict resolution, pruning |

### Preset + Overrides

```json
{
  "memory_config": {
    "preset": "full",
    "embedding_model": "text-embedding-3-large",
    "max_depth": 500,
    "categories": {
      "custom_categories": ["work", "health", "finance"]
    }
  }
}
```

Dimensions auto-detect from model name (`text-embedding-3-large` = 3072).

### With Perfect Brain

```json
{
  "memory_config": {
    "preset": "full",
    "perfect_brain": {
      "enabled": true,
      "memory_veto": true,
      "shared_memory": true,
      "timeline_service": true,
      "consolidator": {"enabled": true, "interval_hours": 6}
    }
  }
}
```

---

## 2. Route Patterns

### Basic Memory Operations

```python
from mdb_engine.dependencies import get_memory_service

@app.post("/remember")
async def remember(text: str, memory=Depends(get_memory_service)):
    return await memory.add(messages=text, user_id="user1")

@app.get("/recall")
async def recall(q: str, memory=Depends(get_memory_service)):
    return await memory.search(query=q, user_id="user1", limit=5)

@app.post("/inject")
async def inject(fact: str, memory=Depends(get_memory_service)):
    return await memory.inject(memory=fact, user_id="user1")
```

### ChatEngine (Full RAG Orchestrator)

```python
from mdb_engine.memory import ChatEngine

chat_engine = ChatEngine(memory_service=memory, config={...})
response = await chat_engine.chat(
    messages=[{"role": "user", "content": "What do you remember about me?"}],
    user_id="user1",
)
```

ChatEngine handles: STM (chat history) + LTM (vector memories) + LLM response + fact extraction.

### Perfect Brain Components

```python
from mdb_engine.dependencies import get_perfect_brain

@app.get("/vetoes")
async def vetoes(user_id: str, brain=Depends(get_perfect_brain)):
    return await brain.memory_veto.get_user_vetoes(user_id=user_id)

@app.post("/consolidate")
async def consolidate(user_id: str, brain=Depends(get_perfect_brain)):
    return await brain.consolidator.consolidate_episodes(user_id)
```

Available components: `shared_memory`, `memory_veto`, `prospective_memory`, `cognitive_memory`, `timeline_service`, `memory_versioning`, `consolidator`, `reflection_service`.

### Embedding and LLM Services

Shared services are created once during init and reused everywhere:

```python
from mdb_engine.dependencies import get_embedding_service, get_llm_service

@app.post("/embed")
async def embed(text: str, svc=Depends(get_embedding_service)):
    return await svc.embed([text])

@app.post("/generate")
async def generate(prompt: str, llm=Depends(get_llm_service)):
    return await llm.chat_completion(
        messages=[{"role": "user", "content": prompt}],
        provider_name="chat",  # Named provider from llm_config.providers
    )
```

Or via RequestContext:

```python
ctx.embedding_service   # EmbeddingService | None
ctx.llm_service         # LLMService | None
ctx.memory              # MemoryService | None
```

---

## 3. LLM Config (Named Providers)

```json
{
  "llm_config": {
    "providers": {
      "chat": "openai/gpt-4o",
      "extraction": "openai/gpt-4o-mini",
      "analysis": "gemini/gemini-2.5-flash-lite"
    }
  }
}
```

Memory uses `extraction_provider` from `memory_config` to pick the fast provider:

```json
{
  "memory_config": {
    "preset": "smart",
    "extraction_provider": "extraction"
  }
}
```

---

## 4. Key Config Options (Non-Default Only)

Only specify what differs from defaults. Everything else auto-detects.

| Key | Default | When to override |
|-----|---------|-----------------|
| `embedding_model` | `text-embedding-3-small` | Using a different model |
| `collection_name` | `{slug}_memories` | Custom collection name |
| `max_depth` | `100` | Need more/fewer memories per user |
| `infer` | `true` | Set `false` to skip LLM extraction |
| `extraction_provider` | (none) | Named provider for fast extraction |
| `chat_model` | `gpt-4o` | Using Gemini/Claude for memory ops |
| `temperature` | `0.0` | Non-zero for creative extraction |

---

## 5. Rules

- **DO** use presets. `"memory_config": true` or `"memory_config": "smart"` covers most apps.
- **DO NOT** specify `embedding_model_dims` — auto-detected from model name.
- **DO NOT** specify `provider: "cognitive"` — it's the only provider.
- **DO NOT** add `app_id` to vector index definitions — auto-injected.
- **DO NOT** create duplicate `EmbeddingService`/`LLMService` instances — use `Depends()` or `engine.get_*()`.
- **DO** put `perfect_brain` inside `memory_config`, not at top level.
