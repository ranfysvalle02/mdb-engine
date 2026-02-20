# Perfect Brain - Complete Cognitive Architecture Demo

This example demonstrates the **entire** MDB-Engine "Perfect Brain" -- every cognitive memory subsystem, all driven by a single `manifest.json`.

## Architecture

```
                         manifest.json
                              |
                        MongoDBEngine
                       /      |      \
             MemoryService  GraphService  LLMService
                  |             |            |
           CognitiveMemory   GraphRAG     LiteLLM
           /  |    |    \       |
      Working Episodic Semantic Procedural
                         \
                  Perfect Brain Layer
                 / |  |  |  |  |  \  \
      Prospective Veto Shared Reflective Predictive
        Versioning  Timeline  QueryAwareRecall
```

## What This Demo Showcases

### Core Memory (via manifest)
- **Cognitive Memory** with importance scoring, reinforcement, merging, and dedup detection
- **Emotion-Weighted Recall** -- emotionally charged memories rank higher
- **Spreading Activation** -- graph-connected memories discovered associatively
- **Salience-Gated Encoding** -- low-value messages skip expensive extraction
- **Memory Categories** -- biographical, preferences, work, health, finance, travel, hobbies, relationships, goals, skills
- **Bucket Organization** -- group related memories by context
- **Memory Pruning** -- soft-delete weakest to cold storage
- **Conflict Detection** -- prevent contradictory facts

### Knowledge Graph (via manifest)
- **Node/Edge CRUD** with typed schemas
- **$graphLookup Traversal** -- multi-hop relationship queries
- **Hybrid Search (GraphRAG)** -- vector similarity + graph context
- **LLM-Powered Extraction** -- automatic entity/relationship discovery

### Perfect Brain Subsystems (directly instantiated)
| Subsystem | What It Does |
|---|---|
| **Prospective Memory** | Intention-based triggers ("when X happens, do Y") |
| **Memory Vetoes** | User-controlled privacy ("never share this memory") |
| **Shared/Group Memory** | Privacy-safe promotion to team/family level |
| **Reflective Memory** | Meta-cognitive insights ("I tend to over-weight...") |
| **Predictive Memory** | Counterfactuals and prediction validation |
| **Memory Versioning** | Track how beliefs evolve over time |
| **Query-Aware Recall** | Policy-driven retrieval (fast/critical/exploration) |
| **Memory Consolidation** | Episodic -> semantic knowledge distillation |
| **Brain Hygiene** | Automated maintenance routines |

## Quick Start

### 1. Set up environment

```bash
cp .env.example .env
# Edit .env with your real keys:
#   MDB_MONGO_URI   - MongoDB Atlas connection string
#   OPENAI_API_KEY  - OpenAI API key
#   VOYAGE_API_KEY  - Voyage AI key (optional, for reranking)
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Run

```bash
uvicorn app:app --reload --port 8000
```

### 4. Seed demo data

```bash
curl -X POST http://localhost:8000/demo/seed
```

This seeds 20 memories, 12 graph nodes, 12 edges, 4 prospective triggers, 3 reflections, 3 predictions, and 1 shared memory.

### 5. Explore the dashboard

```bash
curl http://localhost:8000/brain/dashboard
```

## API Reference

### Memory CRUD

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/memories/inject` | Inject a memory directly |
| POST | `/memories/search` | Semantic search with cognitive ranking |
| GET | `/memories` | Get all memories |
| GET | `/memories/{id}` | Get single memory |
| PUT | `/memories/{id}` | Update (auto re-embeds) |
| DELETE | `/memories/{id}` | Delete one |
| DELETE | `/memories` | Delete all |

### Cognitive Features

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/memories/analytics` | Memory health metrics |
| POST | `/memories/prune` | Soft-delete weakest to cold storage |
| GET | `/memories/cold-storage` | View pruned memories |
| POST | `/memories/{id}/restore` | Restore from cold storage |
| POST | `/memories/check-conflict` | Detect contradictory facts |
| GET | `/memories/categories` | List available categories |

### Knowledge Graph

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/graph/stats` | Graph statistics |
| POST | `/graph/nodes` | Create/update node |
| GET | `/graph/nodes` | List all nodes |
| GET | `/graph/nodes/{id}` | Get node |
| DELETE | `/graph/nodes/{id}` | Delete node |
| POST | `/graph/edges` | Add relationship edge |
| DELETE | `/graph/edges` | Remove edge |
| GET | `/graph/traverse/{id}` | Multi-hop traversal |
| GET | `/graph/neighbors/{id}` | 1-hop neighbors |
| POST | `/graph/search` | Hybrid search (GraphRAG) |
| POST | `/graph/extract` | LLM entity extraction |
| POST | `/memories/{id}/extract-graph` | Extract graph from memory |

### Perfect Brain

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/brain/dashboard` | Full brain status across all subsystems |
| POST | `/brain/prospective/triggers` | Set intention trigger |
| GET | `/brain/prospective/triggers` | List active triggers |
| POST | `/brain/prospective/check` | Check triggers against context |
| POST | `/brain/veto/{memory_id}` | Veto a memory (never share) |
| GET | `/brain/veto/check/{memory_id}` | Check if memory is vetoed |
| DELETE | `/brain/veto/{memory_id}` | Remove veto |
| GET | `/brain/veto/` | List all vetoes |
| POST | `/brain/shared/promote` | Promote fact to group level |
| GET | `/brain/shared/` | Get shared group memories |
| POST | `/brain/reflective/` | Store meta-cognitive reflection |
| GET | `/brain/reflective/` | Get reflections |
| POST | `/brain/predictive/` | Store prediction/counterfactual |
| POST | `/brain/predictive/{id}/validate` | Validate prediction |
| GET | `/brain/predictive/accuracy` | Prediction accuracy stats |
| GET | `/brain/predictive/` | List predictions |
| GET | `/brain/versioning/{entity}` | Belief evolution history |
| GET | `/brain/versioning/{entity}/at/{ts}` | Belief at specific time |
| POST | `/brain/recall/` | Policy-driven retrieval |
| POST | `/brain/health/consolidate` | Trigger episode consolidation |
| POST | `/brain/health/hygiene` | Run daily brain hygiene |

### Demo Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/demo/seed` | Seed all subsystems with demo data |
| POST | `/demo/reset` | Delete all demo data |

## Usage Examples

### Set a prospective trigger

```bash
curl -X POST http://localhost:8000/brain/prospective/triggers \
  -H "Content-Type: application/json" \
  -d '{
    "condition": "user mentions deployment or release",
    "action": "Remind about the staging environment bug from last sprint"
  }'
```

### Check triggers against context

```bash
curl -X POST http://localhost:8000/brain/prospective/check \
  -H "Content-Type: application/json" \
  -d '{"context": "When are we deploying the new feature?"}'
```

### Veto a memory

```bash
curl -X POST http://localhost:8000/brain/veto/MEMORY_ID_HERE \
  -H "Content-Type: application/json" \
  -d '{"reason": "Contains salary information", "scope": "all"}'
```

### Policy-driven recall

```bash
# Fast answer (shallow, quick)
curl -X POST http://localhost:8000/brain/recall/ \
  -H "Content-Type: application/json" \
  -d '{"query": "What is the user name?", "task_type": "fast_answer", "latency_budget": "fast"}'

# Critical decision (deep, cross-checked)
curl -X POST http://localhost:8000/brain/recall/ \
  -H "Content-Type: application/json" \
  -d '{"query": "User allergies and dietary restrictions", "task_type": "critical_decision", "risk_tolerance": "low"}'
```

### Store and validate a prediction

```bash
# Store
curl -X POST http://localhost:8000/brain/predictive/ \
  -H "Content-Type: application/json" \
  -d '{"scenario": "User will ask about Japan trip by March", "origin": "pattern", "confidence": 0.7}'

# Validate (replace PREDICTION_ID)
curl -X POST http://localhost:8000/brain/predictive/PREDICTION_ID/validate \
  -H "Content-Type: application/json" \
  -d '{"was_correct": true}'

# Check accuracy
curl http://localhost:8000/brain/predictive/accuracy
```

## Manifest Configuration

The `manifest.json` enables everything:

- **`text-embedding-3-large`** (3072 dims) for maximum embedding quality
- **All cognitive features** -- emotion, conflict resolution, pruning, cold storage
- **Spreading activation** + salience gating for intelligent recall
- **Memory types** -- working (24h TTL), episodic (2yr), semantic, procedural
- **Consolidation** -- auto-extract entities, route by type, link to graph
- **Graph** -- 10 node types, 3-hop traversal, auto-extraction
- **Persona** -- "Atlas" cognitive companion with defined traits

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `MDB_MONGO_URI` | Yes | MongoDB Atlas connection string |
| `MDB_DB_NAME` | Yes | Database name (default: `perfect_brain_db`) |
| `OPENAI_API_KEY` | Yes | OpenAI API key for embeddings + LLM |
| `VOYAGE_API_KEY` | No | Voyage AI key for reranking |

## Files

```
memory_kitchen_sink/
├── app.py              # FastAPI app with ALL brain subsystems
├── manifest.json       # Perfect Brain configuration
├── requirements.txt    # Python dependencies
├── .env                # Environment variables (git-ignored)
├── .env.example        # Environment template
├── docker-compose.yml  # Docker setup with MongoDB
├── Dockerfile          # Container definition
└── README.md           # This file
```

## Design Note

The Perfect Brain modules (`ProspectiveMemory`, `MemoryVeto`, `SharedMemory`, etc.) are instantiated **directly in app.py** using the engine's scoped collections. This is intentional -- these modules are fully built but not yet wired through `ServiceInitializer` from the manifest. This demo serves as both a showcase and integration blueprint for future engine-level automation, where a single `"profile": "companion"` in your manifest would activate everything automatically.
