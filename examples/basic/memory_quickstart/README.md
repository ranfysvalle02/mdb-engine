# Memory Quickstart

Minimal AI memory app — store facts and recall them by meaning.

## Prerequisites

- Python 3.8+
- MongoDB running (localhost:27017 or set `MONGODB_URI`)
- OpenAI API key (or change the models in `manifest.json`)

## Run

```bash
pip install mdb-engine fastapi uvicorn
export OPENAI_API_KEY=sk-...
uvicorn web:app --reload
```

## Test

```bash
# Store a memory (facts are extracted by the LLM automatically)
curl -X POST "http://localhost:8000/remember?text=I+love+hiking+in+the+mountains"

# Recall by meaning
curl "http://localhost:8000/recall?q=outdoor+activities"

# List all memories
curl http://localhost:8000/memories
```

## How it works

1. **`/remember`** sends text to the memory service, which uses the LLM to extract atomic facts and stores them as vector embeddings in MongoDB.
2. **`/recall`** performs semantic search — finds memories by meaning, not just keywords.
3. **`/memories`** lists all stored memories for the user.

## The manifest.json

This example uses a minimal manifest that enables three services:

- **`llm_config`**: Configures the LLM (GPT-4o-mini) for fact extraction
- **`embedding_config`**: Configures the embedding model for vector search
- **`memory_config`**: Enables the memory service with auto-extraction (`infer: true`)

## Next steps

- See [chit_chat](../chit_chat/) for a full AI chat app with short-term + long-term memory
- Add `enable_cognitive: true` in `memory_config` for importance scoring, decay, and merging
- Add `graph_config` for knowledge graph features (GraphRAG)
