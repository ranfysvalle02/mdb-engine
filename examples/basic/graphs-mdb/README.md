# Graphs MDB - MDB-Engine Knowledge Graph Demo

This example demonstrates **all MDB-Engine graph features** including:

- **Node Operations** - Create, read, update, delete graph nodes
- **Edge Management** - Relationships between nodes with weights and properties
- **Graph Traversal** - Multi-hop traversal using MongoDB's `$graphLookup`
- **Hybrid Search (GraphRAG)** - Combine vector search with graph traversal
- **LLM Node Extraction** - Automatically extract nodes and relationships from text

## Quick Start

### 1. Set Up Environment

```bash
cd examples/basic/graphs-mdb

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment Variables

```bash
cp .env.example .env
```

Edit `.env` with your settings:

```env
MDB_MONGO_URI=mongodb://localhost:27017
MDB_DB_NAME=graphs_mdb_db
OPENAI_API_KEY=your_openai_api_key  # Required for LLM extraction and embeddings
```

### 3. Start MongoDB

Using Docker:

```bash
docker-compose up -d
```

Or use an existing MongoDB instance.

### 4. Run the Application

```bash
uvicorn web:app --reload --port 8000
```

Visit http://localhost:8000 to see the interactive UI.

## Features

### Node Types

The graph supports these node types:

| Type | Description | Example |
|------|-------------|---------|
| `person` | People, users, individuals | `person:alex` |
| `interest` | Hobbies, topics, activities | `interest:golf` |
| `event` | Meetings, occasions | `event:q1_review` |
| `location` | Places, cities | `location:seattle` |
| `organization` | Companies, teams | `organization:techcorp` |
| `product` | Items, goods, services | `product:iphone` |
| `concept` | Abstract ideas, skills | `concept:machine_learning` |

### Relationship Types

Common relationships:

- **Social**: `knows`, `friend_of`, `colleague_of`
- **Family**: `parent_of`, `child_of`, `sibling_of`, `spouse_of`
- **Preference**: `likes`, `dislikes`, `loves`, `hates`, `interested_in`
- **Professional**: `works_at`, `manages`, `reports_to`, `skilled_at`
- **Location**: `lives_in`, `located_in`, `visited`
- **Membership**: `member_of`, `part_of`, `belongs_to`
- **Action**: `created`, `owns`, `attended`, `participating_in`

### Edge Properties

- **weight** (0.0-1.0): Relationship strength
- **active** (boolean): Whether the relationship is current
- **properties** (object): Custom metadata

## API Endpoints

### Node Operations

```bash
# Create/update a node
curl -X POST http://localhost:8000/graph/nodes \
  -H "Content-Type: application/json" \
  -d '{
    "node_id": "person:alex",
    "node_type": "person",
    "name": "Alex",
    "properties": {"occupation": "Engineer", "city": "Seattle"}
  }'

# List all nodes
curl http://localhost:8000/graph/nodes

# List nodes by type
curl http://localhost:8000/graph/nodes?node_type=person

# Get a specific node
curl http://localhost:8000/graph/nodes/person:alex

# Delete a node
curl -X DELETE http://localhost:8000/graph/nodes/person:alex
```

### Edge Operations

```bash
# Add an edge
curl -X POST http://localhost:8000/graph/edges \
  -H "Content-Type: application/json" \
  -d '{
    "source_id": "person:alex",
    "relation": "likes",
    "target_id": "interest:golf",
    "weight": 0.9
  }'

# Remove an edge
curl -X DELETE http://localhost:8000/graph/edges \
  -H "Content-Type: application/json" \
  -d '{
    "source_id": "person:alex",
    "relation": "likes",
    "target_id": "interest:golf"
  }'
```

### Graph Traversal

```bash
# Traverse from a node (multi-hop)
curl "http://localhost:8000/graph/traverse/person:john_smith?max_depth=2"

# Get immediate neighbors (1-hop)
curl http://localhost:8000/graph/neighbors/person:john_smith
```

### Hybrid Search (GraphRAG)

```bash
curl -X POST http://localhost:8000/graph/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What does John like?",
    "max_depth": 2,
    "limit": 5
  }'
```

### LLM Entity Extraction

```bash
curl -X POST http://localhost:8000/graph/extract \
  -H "Content-Type: application/json" \
  -d '{
    "text": "My colleague Sarah works at Google and loves hiking.",
    "auto_create": true
  }'
```

### Statistics

```bash
curl http://localhost:8000/graph/stats
```

### Demo Data

```bash
# Seed demo graph data
curl -X POST http://localhost:8000/demo/seed

# Reset all graph data
curl -X POST http://localhost:8000/demo/reset
```

## Demo Data Structure

When you run `POST /demo/seed`, the following graph is created:

```
                        organization:techcorp
                              |
            +-------works_at--+--located_in-------+
            |                 |                   |
     person:john_smith   person:alex_chen   location:san_francisco
            |                 |
    +-------+-------+         |
    |       |       |    skilled_at
reports_to likes  learning    |
    |       |       |    concept:machine_learning
    v       v       v
person:sarah  interest:python  interest:guitar
    |              |
 manages      interest:chess
    |              |
    +-----colleague_of-----+
```

## How GraphRAG Works

1. **Vector Search**: Finds semantically similar "entry nodes" based on your query
2. **Graph Traversal**: Expands context by following relationships from entry nodes
3. **Context Combination**: Returns both entry nodes and connected graph context
4. **LLM Integration**: Use `formatted_context` in your LLM prompts

Example:

```python
# Search returns rich context
results = graph.hybrid_search("What does John like?", user_id="demo")

# Use in LLM prompt
prompt = f"""
{results['formatted_context']}

Based on the above knowledge graph context, answer: What does John like?
"""
```

## Configuration

The `manifest.json` configures the graph service:

```json
{
  "graph_config": {
    "enabled": true,
    "collection_name": "__kg",
    "auto_extract": true,
    "default_max_depth": 2,
    "vector_index_name": "graph_vector_index",
    "node_types": ["person", "interest", "event", "location", "organization", "product", "concept"]
  },
  "llm_config": {
    "enabled": true,
    "default_model": "openai/gpt-4o"
  },
  "embedding_config": {
    "enabled": true,
    "default_embedding_model": "text-embedding-3-small"
  }
}
```

## Node Schema

Nodes are stored in MongoDB with this structure:

```json
{
  "_id": "person:alex",
  "type": "person",
  "name": "Alex",
  "properties": {
    "occupation": "Engineer",
    "city": "Seattle"
  },
  "edges": [
    {
      "relation": "likes",
      "target": "interest:golf",
      "weight": 0.9,
      "active": true,
      "created_at": "2024-01-15T10:30:00Z",
      "updated_at": "2024-01-15T10:30:00Z"
    }
  ],
  "embedding": [0.1, 0.2, ...],
  "app_slug": "graphs_mdb",
  "user_id": "demo_user_123",
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:30:00Z"
}
```

## Best Practices

1. **Node ID Convention**: Use `type:lowercase_name` format (e.g., `person:alex`)
2. **Depth Limits**: Keep `max_depth` <= 3 for production (default: 2)
3. **Temporal Relationships**: Use `deactivate_edge()` for relationships that change
4. **Relationship Weights**: Use `weight` to indicate relationship strength
5. **Combine with Memory**: Graph + Vector Memory is more powerful than either alone

## Docker Deployment

```bash
docker-compose up -d
```

This starts:
- MongoDB (with Atlas Local for vector search)
- The Graphs MDB application

## Troubleshooting

### Graph service not available

Check that `graph_config.enabled` is `true` in your manifest.

### Node extraction fails

Ensure `OPENAI_API_KEY` is set and the LLM service is configured.

### Vector search returns empty

Vector search requires:
1. MongoDB Atlas or Atlas Local (for `$vectorSearch`)
2. Nodes with embeddings (auto-generated when embedding service is configured)
3. A vector search index named `graph_vector_index`

## Learn More

- [MDB-Engine Documentation](../../docs/)
- [Graph Service Reference](../../docs/GRAPH_SERVICE.md)
- [GraphRAG Guide](../../docs/GRAPHRAG.md)
