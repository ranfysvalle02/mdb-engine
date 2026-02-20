# OSI Reference -- Open Semantic Interchange

Complete reference for MDB-Engine's OSI integration. OSI lets you teach the engine your domain vocabulary so it extracts, resolves, and queries entities using your terminology instead of generic LLM guesses.

---

## Overview

OSI semantic models define **what your data looks like**: entity types (datasets), their fields, synonyms, relationships, and governed metrics. The engine uses these definitions for:

- **Entity resolution**: LLM extracts "Tom Hanks" as `person:tom_hanks` -- OSI remaps it to `actor:tom_hanks` because your YAML defines an `actor` dataset with synonyms like "star", "performer", "cast member".
- **Graph extraction enrichment**: Dataset names and synonyms are injected into the LLM extraction prompt, improving accuracy.
- **Metric-aware query routing**: Queries like "what was the total revenue?" are classified as `osi_metric` because your YAML defines a `total_revenue` metric with synonyms.
- **Semantic discovery**: The engine identifies graph entities not yet in your OSI models and can auto-generate YAML for them.

**YAML is the single source of truth.** All models live in `.yaml` files in a `semantic_models/` directory. The engine syncs YAML to MongoDB on startup and watches for file changes at runtime.

### Architecture

```
semantic_models/*.yaml
        |
        v
  [OSI Loader] -- reads YAML, validates schema
        |
        v
  [OSI Registry] -- indexes datasets, synonyms, metrics
        |
        +---> [Graph Service] -- OSI datasets replace node_types
        |                        OSI prompt context enriches extraction
        |
        +---> [Entity Resolver] -- post-extraction type remapping
        |
        +---> [Query Classifier] -- metric synonym detection
        |
        v
  [OSI Model Store] -- MongoDB persistence ({slug}_osi_models)
                        SHA-256 content-hash for change detection
```

---

## Quick Start

### 1. Add osi_config to your manifest.json

```json
{
  "osi_config": {
    "enabled": true,
    "models_path": "semantic_models/",
    "entity_resolution": true
  }
}
```

### 2. Create a YAML file

Create `semantic_models/my_domain.yaml` alongside your manifest:

```yaml
semantic_model:
  - name: my_domain
    description: My domain knowledge model
    datasets:
      - name: customer
        ai_context:
          synonyms: [customer, client, buyer, account]
      - name: product
        ai_context:
          synonyms: [product, item, SKU, offering]
    relationships:
      - name: purchased
        left_dataset: customer
        right_dataset: product
        ai_context:
          synonyms: [purchased, bought, ordered]
```

### 3. Or let auto-scaffold generate it

If you have `graph_config.node_types` but no YAML files, the engine auto-scaffolds a starter YAML file on first boot:

```json
{
  "graph_config": {
    "node_types": ["customer", "product", "order"]
  },
  "osi_config": {
    "enabled": true,
    "models_path": "semantic_models/"
  }
}
```

On startup, the engine writes `semantic_models/{slug}_knowledge.yaml` with datasets for each node type, basic synonyms, and common relationship patterns.

---

## YAML File Format

YAML files must contain a `semantic_model` key wrapping a list of model definitions.

### Full Schema

```yaml
semantic_model:
  - name: string              # REQUIRED -- unique model name
    description: string       # Optional -- human-readable description
    ai_context:               # Optional -- model-level metadata
      synonyms: [string, ...]  # Alternative names for this domain

    datasets:                  # List of entity type definitions
      - name: string           # REQUIRED -- becomes a node type
        source: string         # Optional -- data source path
        primary_key: [string]  # Optional -- primary key field(s)
        fields:                # Optional -- field definitions
          - name: string       # REQUIRED -- field name
            expression:        # Optional -- SQL-like expression
              dialects:
                - dialect: ANSI_SQL
                  expression: string
            dimension:         # Optional
              is_time: boolean
            ai_context:
              synonyms: [string, ...]
        ai_context:
          synonyms: [string, ...]  # IMPORTANT -- powers entity resolution

    relationships:             # List of entity relationships
      - name: string           # REQUIRED -- relationship name
        left_dataset: string   # Source entity type
        right_dataset: string  # Target entity type
        cardinality: string    # one_to_one, one_to_many, many_to_many
        ai_context:
          synonyms: [string, ...]

    metrics:                   # List of governed metrics
      - name: string           # REQUIRED -- metric name
        description: string    # Optional
        expression:            # Optional -- how to compute
          dialects:
            - dialect: ANSI_SQL
              expression: string
        type: string           # count, sum, average, etc.
        ai_context:
          synonyms: [string, ...]  # Powers metric query detection
```

### Key Rules

- **`name` is required** on models, datasets, relationships, and metrics.
- **`ai_context.synonyms`** on datasets is critical for entity resolution. Without synonyms, the engine can't remap extracted entities to your types.
- **`relationships`** should reference datasets defined in the same model. The validator warns on unresolved references.
- The `semantic_model` wrapper is required at the top level of YAML files.

---

## Complete Example

From the Member app (`examples/advanced/sso-multi-app/apps/member/semantic_models/movies.yaml`):

```yaml
semantic_model:
  - name: movie_knowledge
    description: >
      Movie enthusiast knowledge model for cognitive memory.
    ai_context:
      synonyms:
        - movie database
        - film library
        - cinema knowledge

    datasets:
      - name: actor
        source: mdb_engine.ai_chat.actor
        primary_key: [actor_id]
        fields:
          - name: actor_name
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: name
            ai_context:
              synonyms: [name, star name, performer name]
        ai_context:
          synonyms:
            - actor
            - actress
            - star
            - performer
            - cast member
            - co-star
            - movie star

      - name: movie
        source: mdb_engine.ai_chat.movie
        primary_key: [movie_id]
        fields:
          - name: title
            ai_context:
              synonyms: [movie title, film title, name]
          - name: year
            dimension:
              is_time: true
            ai_context:
              synonyms: [release year, came out]
        ai_context:
          synonyms:
            - movie
            - film
            - picture
            - flick
            - feature
            - motion picture

      - name: director
        ai_context:
          synonyms: [director, filmmaker, auteur, helmer]

      - name: genre
        ai_context:
          synonyms: [genre, type, category, style]

    relationships:
      - name: starred_in
        left_dataset: actor
        right_dataset: movie
        cardinality: many_to_many
        ai_context:
          synonyms: [starred in, acted in, appeared in, was in]

      - name: directed
        left_dataset: director
        right_dataset: movie
        cardinality: one_to_many
        ai_context:
          synonyms: [directed, made, helmed]

      - name: genre_of
        left_dataset: movie
        right_dataset: genre
        cardinality: many_to_many
        ai_context:
          synonyms: [genre, type of, categorized as]

    metrics:
      - name: movies_watched
        description: Number of movies mentioned
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: "COUNT(DISTINCT movie_id)"
        type: count
        ai_context:
          synonyms: [movies watched, films seen, watch count]
```

---

## Configuration Reference

Add to your `manifest.json`:

```json
{
  "osi_config": {
    "enabled": true,
    "models_path": "semantic_models/",
    "entity_resolution": true,
    "metric_routing": false,
    "export_enabled": false,
    "sync_interval_minutes": 60
  }
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | boolean | `false` | Master switch. When false, OSI is completely disabled. |
| `models_path` | string | - | Path to directory containing YAML files (relative to manifest). Convention: `semantic_models/`. |
| `models` | string[] | - | Specific YAML file paths to load (alternative to directory). |
| `entity_resolution` | boolean | `true` | Remap extracted node types to OSI dataset names via synonym matching. |
| `metric_routing` | boolean | `false` | Classify queries against governed metric synonyms. |
| `export_enabled` | boolean | `false` | Enable the `/api/osi/export` endpoint. |
| `sync_interval_minutes` | integer | `60` | How often the file watcher checks for YAML changes (0 = disabled). |

**Tier 1 shortcut**: If you only need extraction enrichment without the full config, add `osi_models_path` directly to `graph_config`:

```json
{
  "graph_config": {
    "osi_models_path": "semantic_models/"
  }
}
```

---

## How Sync Works

YAML files and MongoDB stay synchronized automatically.

### Startup

1. The engine reads all YAML files from `models_path`.
2. Computes SHA-256 hash of the **actual file bytes** (not just path names).
3. Compares with the hash stored in MongoDB (`{slug}_osi_models._meta`).
4. If different (or first run): re-seeds MongoDB from YAML, updates the stored hash.
5. If same: skips seeding, loads from MongoDB (preserves any API-added models).

### Runtime (File Watcher)

- Polls YAML file modification timestamps at `sync_interval_minutes`.
- When a file changes: re-seeds MongoDB (`force=True` bypasses hash check), then reloads the in-memory registry.
- Result: edit a YAML file, wait for the next poll, and the change is live in both the registry and MongoDB.

### API Mutations

- `POST /api/osi/models/import` writes directly to MongoDB with `origin: "api_import"`.
- These persist across restarts (they're in MongoDB, not YAML).
- On next restart, if YAML hasn't changed, API-added models are preserved alongside YAML-seeded models.

---

## Validation

Every YAML file is validated on load. Validation catches structural errors early with clear, actionable messages.

### What's Validated

- **Required fields**: `name` on models, datasets, relationships, metrics.
- **Type checking**: synonyms must be lists of strings, datasets must be dicts, etc.
- **Cross-references**: relationship `left_dataset`/`right_dataset` must reference existing datasets (warns if not found).
- **Best practices**: warns when datasets have no synonyms (entity resolution will be less effective).

### Error Format

Each error/warning has:
- **`path`**: Where in the model the issue is (e.g., `model[movie_knowledge].datasets[0].ai_context.synonyms`)
- **`message`**: What's wrong
- **`suggestion`**: How to fix it (when applicable)

### API Endpoint

```
POST /api/osi/validate
Content-Type: text/plain

<raw YAML content>
```

Response:

```json
{
  "valid": false,
  "models_checked": 1,
  "error_count": 1,
  "warning_count": 2,
  "errors": [
    {"path": "model[test].datasets[0].name", "message": "'name' must be a non-empty string, got NoneType"}
  ],
  "warnings": [
    {"path": "model[test].datasets[1].ai_context", "message": "Dataset 'widget' has no synonyms", "suggestion": "Add ai_context.synonyms"}
  ]
}
```

### Python API

```python
from mdb_engine.osi import validate_osi_model, validate_osi_yaml, OsiValidationResult

# Validate a parsed model dict
result: OsiValidationResult = validate_osi_model(model_dict)
if not result.valid:
    for err in result.errors:
        print(f"[{err.path}] {err.message}")

# Validate raw YAML string
result = validate_osi_yaml(yaml_string)
```

---

## Auto-Scaffold

When `osi_config.enabled=true` but no YAML files exist, the engine auto-generates a starter model.

### How It Works

1. Reads `graph_config.node_types` from the manifest.
2. Generates a dataset for each node type with:
   - `name`, `source`, `primary_key`
   - A `name` field with basic synonyms
   - `ai_context.synonyms` from a built-in map of common types
3. If `memory_config.categories` exist, adds a `user_preference` dataset.
4. Auto-generates common relationships (e.g., actor+movie = `starred_in`).
5. Writes the YAML file to `models_path/{slug}_knowledge.yaml`.
6. Loads it into the registry.

### Built-In Synonym Map

The scaffolder knows synonyms for common entity types:

| Type | Generated Synonyms |
|------|--------------------|
| actor | actor, actress, star, performer, cast member, lead, co-star, movie star |
| movie | movie, film, picture, flick, feature, motion picture, title, release |
| director | director, filmmaker, auteur, helmer, directed by |
| genre | genre, type, category, style, kind |
| person | person, individual, someone, user, human, character |
| location | location, place, city, country, region, area, where |
| organization | organization, company, firm, corporation, institution, org |
| product | product, item, good, merchandise, offering |
| (unknown) | type_name, type name (space-separated fallback) |

### Python API

```python
from mdb_engine.osi import scaffold_osi_model, scaffold_to_yaml

# Generate a model dict
model = scaffold_osi_model(
    app_slug="my_app",
    node_types=["actor", "movie", "genre"],
    categories=["favorites", "watchlist"],
)

# Generate YAML string ready to write to a file
yaml_str = scaffold_to_yaml(
    app_slug="my_app",
    node_types=["actor", "movie", "genre"],
)
```

---

## Entity Resolution

When the LLM extracts entities from text, the OSI resolver remaps node types to match your domain vocabulary.

### Flow

1. **LLM extracts**: `{type: "person", name: "Tom Hanks"}` from "Tom Hanks is my favorite actor."
2. **Resolver checks OSI**: Is "person" a dataset? No. Is it a synonym? It matches `actor` dataset (synonyms include "person"? No, but "star" and "performer" match via type/name lookup).
3. **Remap**: `person:tom_hanks` becomes `actor:tom_hanks`.
4. **Tag**: Node gets `_osi_dataset: "actor"` and `_osi_original_type: "person"` in properties.

### Resolution Priority

1. Exact type match (type == dataset name)
2. Type synonym match (type is in a dataset's synonyms)
3. Name synonym match (entity name is in a dataset's synonyms)
4. Partial match (type contains or is contained in a synonym)

---

## API Endpoints

All endpoints are under `/api/osi/` and require the OSI router to be mounted.

### POST /api/osi/validate

Validate YAML without loading it. No side effects.

**Request**: Raw YAML in body (`Content-Type: text/plain`)

**Response**:
```json
{"valid": true, "models_checked": 1, "error_count": 0, "warning_count": 0, "errors": [], "warnings": []}
```

### GET /api/osi/models

List all loaded semantic models.

**Response**:
```json
{"models": [{"name": "movie_knowledge", "datasets": 5, "metrics": 3, "relationships": 5, "status": "approved"}], "total": 1}
```

### GET /api/osi/metrics

List all governed metric definitions.

**Response**:
```json
{"metrics": [{"name": "movies_watched", "description": "...", "expression": "COUNT(DISTINCT movie_id)", "synonyms": ["films seen"], "model": "movie_knowledge"}], "total": 1}
```

### POST /api/osi/models/import

Validate and import YAML. Validates schema first; rejects with errors if invalid.

**Request**: Raw YAML in body

**Response (success)**:
```json
{"success": true, "models_loaded": 1, "message": "Imported 1 model(s) -- live immediately"}
```

**Response (validation failure)**:
```json
{"success": false, "message": "Validation failed with 2 error(s)", "errors": [...], "warnings": [...]}
```

### POST /api/osi/export

Export the knowledge graph as OSI-compatible YAML.

**Response**: `Content-Type: application/x-yaml` with the generated YAML.

### GET /api/osi/discovery-report

Gap analysis: identifies graph entity types not yet in OSI models.

**Response**:
```json
{"new_entity_types": [{"type": "event", "count": 5}], "stats": {"unmatched_types": 1}}
```

### GET /api/osi/concepts

List discovered concept definitions (auto-generated from conversations).

### POST /api/osi/concepts/{id}/approve

Promote a discovered concept to governed status, loading its generated YAML into the registry.

### POST /api/osi/concepts/{id}/reject

Reject a discovered concept.

### POST /api/osi/reload

Force reload the registry from MongoDB.

---

## Python API

### Public Imports

```python
from mdb_engine.osi import (
    # Registry
    OsiModelRegistry,
    OsiModelStore,

    # Validation
    validate_osi_model,
    validate_osi_yaml,
    validate_osi_models,
    OsiValidationResult,

    # Scaffolding
    scaffold_osi_model,
    scaffold_to_yaml,
)
```

### OsiModelRegistry

The central service for managing OSI models within an app.

```python
registry = OsiModelRegistry(app_slug="my_app", config=osi_config, store=store)
await registry.load()

# Lookups
registry.get_dataset("actor")         # -> dict or None
registry.get_metric("total_revenue")  # -> dict or None
registry.match_entity("Tom Hanks", "person")  # -> matched dataset dict or None

# Prompt context for LLM extraction
context: str | None = registry.get_prompt_context()

# Node types for graph service
types: list[str] = registry.get_node_types()

# Write-through mutations
await registry.add_model(model_dict, origin="api_import")
await registry.remove_model("old_model")

# Reload
await registry.reload()
```

### OsiValidationResult

```python
result = validate_osi_model(model)
result.valid          # bool
result.errors         # list[OsiValidationIssue]
result.warnings       # list[OsiValidationIssue]
result.models_checked # int
result.to_dict()      # JSON-serializable dict

# Each issue:
issue.path        # "model[name].datasets[0].ai_context"
issue.message     # "synonyms must be a list of strings"
issue.suggestion  # "Add ai_context.synonyms with common names"
```
