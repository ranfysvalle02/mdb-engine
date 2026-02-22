# AI Chat App (sso-app-3) - Complete Memory System & GraphRAG Example

A comprehensive AI chat application demonstrating **Context Engineering**, **GraphRAG (Knowledge Graph RAG)**, and advanced memory service features, including Client-Side Field Level Encryption (CSFLE) for secure memory storage.

## Features

- **🎭 Context Engineering**: Dynamic context assembly using Persona, Entity Facts, Dynamic Persona, STM, and LTM
- **🔒 Memory Encryption**: Client-Side Field Level Encryption (CSFLE) for sensitive memories
- **💬 Real-time Streaming**: Server-Sent Events (SSE) for token-by-token AI responses
- **🧠 Cognitive Memory**: Advanced memory service with STM + LTM architecture
- **📊 Memory Explorer**: Interactive UI to explore memories, analytics, and knowledge graph
- **🔐 SSO Authentication**: Shared authentication across multi-app deployments
- **🧠 Perfect Brain Features**: SharedMemory, ReflectiveMemory, PredictiveMemory, ProspectiveMemory, QueryAwareRecall, MemoryVeto, MemoryVersioning
- **🧠 Multi-Tier Memory System**: Episodic, Procedural, Working, and Semantic memory layers with MemoryConsolidator
- **🔄 Memory Consolidation**: Automatic episodic → semantic consolidation via MemoryConsolidator and ReflectionService
- **🕸️ GraphRAG (Knowledge Graph RAG)**: Microsoft-style GraphRAG with $graphLookup traversal, hybrid search, and automatic entity extraction
- **📋 OSI Integration**: Open Semantic Interchange with a comprehensive Family Management semantic model (10 datasets, 12 relationships, 6 governed metrics, 515 synonyms)

## OSI Family Management Model

This app includes the first production OSI semantic model in MDB-Engine. The `osi_config` section in `manifest.json` defines a **Family Management** domain model that teaches the extraction engine family-specific vocabulary:

**Datasets**: `family_member`, `allergy`, `medication`, `medical_condition`, `vaccination`, `appointment`, `routine`, `meal_plan`, `emergency_contact`, `pet`

**How it works**:
- Node types from OSI datasets are added to the extraction type list (e.g., `family_member`, `allergy`)
- Post-extraction entity resolution remaps generic types (e.g., `person:timmy` -> `family_member:timmy`)
- Metric-aware query routing detects governed metrics (e.g., "what meds is everyone on?" -> `active_medications`)
- Models are persisted in MongoDB (`ai-chat_osi_models` collection) for zero-restart API mutations

**Governed Metrics**: `active_medications`, `upcoming_appointments`, `high_severity_allergies`, `overdue_vaccinations`, `weekly_routine_count`, `meals_planned`

**API Endpoints**: `/api/osi/models`, `/api/osi/metrics`, `/api/osi/models/import`, `/api/osi/concepts`, `/api/osi/export`, `/api/osi/discovery-report`

## Context Engineering

This app demonstrates **Context Engineering** - an architectural discipline for constructing optimal LLM context. Context Engineering automatically builds system prompts from multiple memory layers:

### Context Layers

1. **Persona Layer (P_static)**
   - Role, description, and traits from `PersonaEngine`
   - Configured in `manifest.json`
   - Immutable core identity

2. **Entity Memory**
   - Extracted facts: Name, OS, Language, Expertise
   - Automatically extracted from biographical and preference memories
   - Injected into every prompt

3. **Dynamic Persona**
   - Adaptive instructions based on user expertise and emotion
   - Adjusts tone and verbosity dynamically
   - Example: "User is an expert. Be terse. Skip explanations."

4. **Short-Term Memory (STM)**
   - Recent chat history with sliding window optimization
   - Last 5 messages kept raw, older messages summarized
   - Maintains conversation flow

5. **Long-Term Memory (LTM)**
   - Semantic vector search results
   - Relevant memories retrieved based on query similarity
   - Includes document context (author, title, organization)

6. **Graph Context**
   - Knowledge graph data (if enabled)
   - Entity relationships and connections

### Configuration

Context Engineering is enabled in `web.py`:

```python
cognitive_engine = CognitiveEngine(
    app_slug=APP_SLUG,
    memory_service=memory_service,
    chat_history_collection=chat_history_collection,
    stm_context_limit=10,
    ltm_search_limit=12,
    auto_summarize_threshold=20,
    llm_provider=llm_provider,
    # Context Engineering configuration
    enable_context_engineering=True,
    stm_raw_window=5,  # Keep last 5 messages raw
    enable_entity_extraction=True,
    enable_dynamic_persona=True,
)
```

### Persona Configuration

Configure the persona in `manifest.json`:

```json
{
  "memory_config": {
    "persona": {
      "enabled": true,
      "default_role": "Orby - AI Assistant",
      "default_description": "Orby is an intelligent AI assistant with access to stored memories...",
      "default_traits": {
        "technical_focus": 0.6,
        "humor": 0.3,
        "formality": 0.6,
        "empathy": 0.7,
        "creativity": 0.5
      }
    }
  }
}
```

### UI Features

The app includes a **Context Engineering Panel** that displays:

- **🎭 Persona**: Current persona role and description
- **📋 Entity Facts**: Extracted facts (Name, OS, Language, Expertise)
- **⚙️ Dynamic Instructions**: Persona adaptation instructions (collapsible)
- **📝 STM Summary**: Summary of older chat history (collapsible)

The panel appears automatically when Context Engineering metadata is available in responses.

## Memory Encryption (CSFLE)

The app uses **Client-Side Field Level Encryption** to encrypt sensitive memory content at rest:

```json
{
  "memory_config": {
    "enabled": true,
    "encrypted": true
  }
}
```

### What Gets Encrypted

- **Encrypted**: `content`, `text` (memory content)
- **Queryable** (NOT encrypted): `user_id`, `session_id`, `created_at`, `importance`, `embedding`, `category`

### Auto-Key Generation (Docker)

When running with Docker Compose, the encryption key is auto-generated on first startup and persisted in the `csfle_keys` volume.

## Perfect Brain Features

This app demonstrates all Perfect Brain memory features for advanced memory management:

### Shared/Group Memory

Privacy-safe promotion of facts within groups (teams, families, organizations):

**Endpoints:**
- `POST /api/memories/shared/promote` - Promote user memory to shared/group memory
- `GET /api/memories/shared` - Query shared memories with bucket filtering
- `GET /api/memories/shared/stats` - Get shared memory statistics

**Example:**
```bash
# Promote a fact to shared memory (team example)
POST /api/memories/shared/promote
{
  "fact": "We prefer async/await patterns for I/O operations",
  "source_user_ids": ["user1", "user2", "user3"],
  "group_id": "team-001",
  "bucket_id": "category:CODE:team-001",
  "confidence": 0.85
}

# Query shared memories in CODE bucket
GET /api/memories/shared?group_id=team-001&bucket_id=category:CODE:team-001&query=coding+patterns
```

### Reflective Memory

Meta-cognitive insights about system behavior:

**Endpoints:**
- `POST /api/memories/reflections` - Store a reflection
- `GET /api/memories/reflections` - Get reflections with bucket filtering

**Example:**
```bash
# Store a reflection
POST /api/memories/reflections
{
  "reflection": "I tend to over-weight recent conversations",
  "trigger": "performance_review",
  "confidence": 0.8,
  "bucket_id": "category:CODE:user123"
}
```

### Predictive Memory

Counterfactuals, simulations, and future scenarios:

**Endpoints:**
- `POST /api/memories/predictions` - Store a prediction
- `POST /api/memories/predictions/{prediction_id}/validate` - Validate a prediction
- `GET /api/memories/predictions` - Get predictions with bucket filtering

**Example:**
```bash
# Store a prediction
POST /api/memories/predictions
{
  "scenario": "If we switch to TypeScript, we'll reduce bugs by 30%",
  "origin": "pattern_analysis",
  "confidence": 0.7,
  "group_id": "team-001",
  "bucket_id": "category:CODE:team-001"
}

# Validate when outcome is known
POST /api/memories/predictions/{prediction_id}/validate
{
  "actual_outcome": "Bug rate reduced by 25%",
  "was_correct": true
}
```

### Query-Aware Recall

Policy-driven memory retrieval:

**Enhanced Endpoint:**
- `GET /api/memories/search` - Now supports `task_type`, `risk_tolerance`, `latency_budget`, `timeline_id`, `min_confidence` parameters

**Example:**
```bash
# Fast answer (low latency, lower confidence)
GET /api/memories/search?query=user+preferences&task_type=fast_answer&risk_tolerance=low&latency_budget=fast&bucket_id=category:CODE:user123&timeline_id=root&min_confidence=0.5

# Critical decision (high confidence, exhaustive search, specific timeline)
GET /api/memories/search?query=medical+history&task_type=critical_decision&risk_tolerance=low&latency_budget=deep&bucket_id=category:HEALTH:user123&timeline_id=root&min_confidence=0.8
```

### Memory Vetoes

User-controlled "never share" flags:

**Endpoints:**
- `POST /api/memories/vetoes` - Add a memory veto
- `DELETE /api/memories/vetoes/{memory_id}` - Remove a veto
- `GET /api/memories/vetoes` - Get user's vetoes

**Example:**
```bash
# Add a veto
POST /api/memories/vetoes
{
  "memory_id": "mem123",
  "scope": "shared"
}
```

### Prospective Memory ("Remember to do X when Y happens")

**AI Superpower**: Prospective memory lets you set intention-based triggers that fire when future context matches. This is something the human brain struggles with -- but mdb-engine does perfectly.

**How It Works:**
- Set a trigger with a condition ("user mentions project deadline") and an action ("remind about risk assessment")
- The condition is embedded as a vector
- Every incoming query is checked against active triggers via similarity
- When a trigger fires, the action is surfaced as a reminder in the AI's response
- One-shot triggers deactivate after firing; recurring triggers keep firing

**Endpoints:**
- `POST /api/prospective/triggers` - Set a new prospective trigger
- `GET /api/prospective/triggers` - List active triggers
- `DELETE /api/prospective/triggers/{trigger_id}` - Deactivate a trigger
- `POST /api/memories/vetoes` - Add memory veto (never share this memory)
- `DELETE /api/memories/vetoes/{memory_id}` - Remove memory veto

**Example - Intentional Forgetting:**
```bash
# Set a prospective trigger
POST /api/prospective/triggers
{
  "condition": "user asks about pricing or costs",
  "action": "Suggest the enterprise plan with volume discounts",
  "one_shot": false
}

# Memory still exists but has lower confidence
# Won't appear in high-confidence searches (min_confidence >= 0.8)
# Will appear in low-confidence searches (min_confidence < 0.5)
```

**Example - Memory Veto (Never Share):**
```bash
# Add a veto to prevent sharing a memory
POST /api/memories/vetoes
{
  "memory_id": "mem456",
  "scope": "shared"  # Prevent sharing in shared/group contexts
}

# Memory is still accessible to the user
# But will never be shared with others
```

### Memory Versioning

Track belief evolution over time:

**Endpoint:**
- `GET /api/memories/{entity_name}/history` - Get version history

**Example:**
```bash
GET /api/memories/programming_language/history
# Returns version history showing how preferences changed over time
```

### Enhanced Memory Stats

The `/api/memories/stats` endpoint now includes Perfect Brain feature counts:

```json
{
  "success": true,
  "stats": {
    "file_contexts": {...},
    "general_buckets": {...},
    "bucket_files": {...},
    "perfect_brain": {
      "shared_memories": 42,
      "reflections": 15,
      "predictions": {
        "total": 8,
        "validated": 3,
        "unvalidated": 5
      },
      "vetoes": 2
    }
  }
}
```

### Bucket Filtering

All Perfect Brain features support bucket filtering for contextual isolation:

- Shared memories can be filtered by bucket (e.g., "team CODE bucket")
- Reflections can be scoped to buckets
- Predictions can be filtered by bucket
- Query-aware recall respects bucket filters

See [Perfect Brain Documentation](../../../../docs/PERFECT_BRAIN.md) for comprehensive details.

## Cognitive OS Memory Features

This app demonstrates the Cognitive Operating System memory architecture:

### Perfect Recall

**True Perfect Recall** -- every memory is always searchable, forever:
- No decay (confidence is a static trust signal, not a timer)
- No pruning (memories never deleted)
- No filtering (all memories accessible regardless of confidence)
- Ranking handles relevance: similarity, importance, emotion, recency, access count
- Better than the brain: the brain forgets -- that's a bug, not a feature

### Timelines/Multiverse

Multiple parallel memory timelines for counterfactual reasoning:

**Endpoints:**
- `POST /api/memories/timelines/fork` - Fork a new timeline
- `GET /api/memories/timelines` - List user's timelines
- `GET /api/memories/timelines/{timeline_id}/ancestry` - Get timeline ancestry chain
- `GET /api/memories/timelines/current` - Get current active timeline
- `POST /api/memories/timelines/switch` - Switch active timeline
- `GET /api/memories/search?timeline_id=branch_abc` - Search within specific timeline

**Example:**
```bash
# Fork a timeline for "What if I quit my job?"
POST /api/memories/timelines/fork
{
  "current_timeline": "root",
  "new_name": "What if I quit my job?"
}

# Get timeline ancestry (shows inheritance chain)
GET /api/memories/timelines/branch_abc/ancestry
# Returns: ["branch_abc", "root"]

# Switch to the forked timeline
POST /api/memories/timelines/switch
{
  "timeline_id": "branch_abc"
}

# Get current active timeline
GET /api/memories/timelines/current
# Returns: {"timeline_id": "branch_abc"}

# Search memories in the forked timeline
GET /api/memories/search?query=work+preferences&timeline_id=branch_abc&min_confidence=0.7
```

### Graph Links

Explicit relationships between memories:

**Link Types:**
- `derived_from`: Memory derived from other memories (semantic distillation)
- `contradicts`: Memory that contradicts another (Bayesian updates)
- `deprecated`: Memory marked as outdated (preserved for audit trail)

**Endpoints:**
- `POST /api/memories/{memory_id}/contradict` - Mark contradiction
- `POST /api/memories/with-links` - Create memory with graph links

**Example:**
```bash
# Create memory with derived_from links
POST /api/memories/with-links
{
  "content": "User prefers concise explanations",
  "derived_from": ["mem123", "mem456"],
  "timeline_id": "root",
  "confidence": 0.9
}

# Mark a contradiction
POST /api/memories/new_mem123/contradict
{
  "contradicted_memory_id": "old_mem456"
}
```

### Confidence-Based Retrieval

Explicit confidence scores with filtering:

**Search Parameters:**
- `min_confidence`: Minimum confidence threshold (0.0 to 1.0)
- `timeline_id`: Timeline to search in (default: "root")

**Example:**
```bash
# High-confidence search (only memories with confidence >= 0.8)
GET /api/memories/search?query=user+preferences&min_confidence=0.8&timeline_id=root

# Low-confidence search (include speculative memories)
GET /api/memories/search?query=possible+interests&min_confidence=0.3
```

### Timeline Inheritance

When searching in a forked timeline, memories from parent timelines are automatically included:
- Timeline C (child of B, child of Root) includes memories from C, B, and Root
- Enables counterfactual reasoning: "What if I had chosen differently?" while preserving actual memories

**Understanding Timeline Inheritance:**
```bash
# Get the ancestry chain for a timeline
GET /api/memories/timelines/branch_xyz/ancestry
# Returns: ["branch_xyz", "branch_abc", "root"]

# This shows that when searching in branch_xyz:
# 1. First searches branch_xyz
# 2. Then searches branch_abc (parent)
# 3. Finally searches root (grandparent)
# 
# This enables counterfactual reasoning while preserving actual memories
```

### Timeline Management

**Switching Timelines:**
Users can switch between timelines to work in different contexts:

```bash
# Switch to a forked timeline
POST /api/memories/timelines/switch
{
  "timeline_id": "branch_abc"
}

# All subsequent memory operations use this timeline by default
# (unless explicitly overridden with timeline_id parameter)

# Get current active timeline
GET /api/memories/timelines/current
# Returns: {"timeline_id": "branch_abc"}
```

## Multi-Tier Memory System

This app implements a complete multi-tier memory architecture with automatic consolidation:

### Memory Layers

1. **Working Memory**: Short-term active context (24-hour TTL)
2. **Episodic Memory**: Raw chronological interactions
3. **Semantic Memory**: Structured entity facts and relationships
4. **Procedural Memory**: Executable skills, tools, and workflows
5. **Reflective Memory**: Meta-cognitive insights
6. **Predictive Memory**: Counterfactuals and simulations

### Memory Consolidation

The **MemoryConsolidator** automatically transforms episodic memories into semantic facts and procedural lessons:

**Endpoint:**
- `POST /api/memories/consolidate` - Trigger manual consolidation

**Example:**
```bash
POST /api/memories/consolidate
{
  "limit": 10,
  "force": false
}

# Returns:
{
  "success": true,
  "entities_extracted": 5,
  "procedures_created": 2,
  "episodes_processed": 10
}
```

### Episodic Memory

Raw chronological interactions that are automatically recorded during chat:

**Endpoints:**
- `POST /api/memories/episodic` - Record an episode
- `GET /api/memories/episodic` - Query episodes (with filters: session_id, consolidated, bucket_id)
- `GET /api/memories/episodic/{episode_id}` - Get specific episode

**Example:**
```bash
# Episodes are automatically recorded during chat
# But you can also manually record:
POST /api/memories/episodic
{
  "session_id": "conv123",
  "role": "user",
  "content": "I'm working on a Python project",
  "bucket_id": "category:CODE:user123"
}

# Query unconsolidated episodes
GET /api/memories/episodic?consolidated=false&limit=50
```

### Procedural Memory

Stores executable knowledge, workflows, and skills:

**Endpoints:**
- `POST /api/memories/procedural` - Store a procedure
- `GET /api/memories/procedural` - Query procedures (with filters: task_type, bucket_id)
- `GET /api/memories/procedural/{procedure_id}` - Get specific procedure
- `PUT /api/memories/procedural/{procedure_id}` - Update procedure (e.g., success rate)

**Example:**
```bash
# Store a procedure
POST /api/memories/procedural
{
  "task_type": "debugging",
  "procedure": "1. Check logs 2. Reproduce issue 3. Isolate cause",
  "success_rate": 0.85,
  "bucket_id": "category:CODE:user123"
}

# Update success rate after using procedure
PUT /api/memories/procedural/{procedure_id}
{
  "success_rate": 0.90
}
```

### Working Memory

Short-term active context for sessions (24-hour TTL):

**Endpoints:**
- `POST /api/memories/working/context` - Set working context
- `GET /api/memories/working/context?session_id=conv123` - Get working context
- `DELETE /api/memories/working/context?session_id=conv123` - Clear working context

**Example:**
```bash
# Set working context (automatically done during chat)
POST /api/memories/working/context
{
  "session_id": "conv123",
  "context": {
    "current_topic": "Python debugging",
    "category": "work",
    "last_message": "How do I debug memory leaks?"
  }
}

# Get working context
GET /api/memories/working/context?session_id=conv123
```

### Semantic Entity Memory

Structured facts and entities extracted from conversations:

**Endpoints:**
- `POST /api/memories/semantic/entity` - Update/create entity
- `GET /api/memories/semantic/entity` - Search entities (with query and bucket_id filters)
- `GET /api/memories/semantic/entity/{entity_name}` - Get specific entity

**Example:**
```bash
# Update entity (automatically done during consolidation)
POST /api/memories/semantic/entity
{
  "entity_name": "programming_language",
  "attributes": {"preference": "Python", "experience_years": 5},
  "confidence": 0.9,
  "bucket_id": "category:CODE:user123"
}

# Search entities
GET /api/memories/semantic/entity?query=programming+preferences&bucket_id=category:CODE:user123
```

### Reflection Service

Periodic memory consolidation service:

**Endpoint:**
- `POST /api/memories/reflection/run` - Trigger reflection/consolidation

**Example:**
```bash
POST /api/memories/reflection/run

# Returns:
{
  "success": true,
  "memories_consolidated": 25,
  "reflections_created": 3
}
```

### Integration with Chat Flow

The multi-tier memory system is automatically integrated into the chat flow:

1. **Episodic Recording**: Every user and assistant message is automatically recorded as an episode
2. **Working Context**: Working context is set before each chat interaction
3. **Consolidation**: Episodes are periodically consolidated into semantic facts and procedures
4. **Entity Updates**: Semantic entities are updated based on extracted memories

### Enhanced Memory Stats

The `/api/memories/stats` endpoint now includes multi-tier memory statistics:

```json
{
  "success": true,
  "stats": {
    "perfect_brain": {
      "episodic": {
        "total": 150,
        "consolidated": 120,
        "unconsolidated": 30
      },
      "procedural": {
        "total": 8
      },
      "working": {
        "active_sessions": 3
      },
      "semantic": {
        "total_entities": 45
      },
      "consolidation": {
        "available": true
      }
    }
  }
}
```

## GraphRAG (Knowledge Graph RAG)

This app implements **Microsoft-style GraphRAG** - a powerful enhancement to traditional RAG that uses knowledge graphs for multi-hop reasoning queries.

### What is GraphRAG?

GraphRAG combines **Vector Search** (semantic similarity) with **Graph Traversal** (structural relationships) to enable queries that standard RAG cannot handle:

**Traditional RAG** can answer: "Tell me about Alex" (finds memories containing "Alex")

**GraphRAG** can answer: "What should I get for my brother's favorite hobby?" (traverses: user → brother → person:alex → likes → interest:golf)

### How It Works

1. **Automatic Extraction**: When memories are stored, entities and relationships are automatically extracted using LLM
2. **Graph Building**: Nodes (people, interests, organizations) and edges (relationships) are created in the knowledge graph
3. **Hybrid Search**: Combines vector similarity (finds entry points) with graph traversal (follows relationships)
4. **Multi-Hop Reasoning**: Uses MongoDB's `$graphLookup` for efficient multi-hop queries

### GraphRAG Endpoints

#### Graph Statistics
- `GET /api/graph/stats` - Get graph statistics (total nodes, edges, etc.)

#### Hybrid Search (GraphRAG Core)
- `GET /api/graph/search?query=what+does+alex+like&max_depth=2` - Hybrid search combining vector + graph

**Example:**
```bash
GET /api/graph/search?query=what+does+my+brother+like&max_depth=2

# Returns:
{
  "success": true,
  "entry_nodes": [
    {"_id": "person:alex", "type": "person", "name": "Alex", ...}
  ],
  "graph_context": [
    {"_id": "interest:golf", "type": "interest", "name": "Golf", ...},
    {"_id": "product:golf_clubs", "type": "product", "name": "Golf Clubs", ...}
  ],
  "total_nodes": 12
}
```

#### Graph Traversal
- `GET /api/graph/traverse?node_id=person:alex&max_depth=2` - Traverse graph from a node

**Example:**
```bash
GET /api/graph/traverse?node_id=person:alex&max_depth=2

# Returns all nodes reachable from Alex within 2 hops
```

#### Node Management
- `GET /api/graph/nodes` - List all nodes (with optional `node_type` filter)
- `GET /api/graph/nodes/{node_id}` - Get specific node
- `POST /api/graph/nodes` - Create/update a node
- `DELETE /api/graph/nodes/{node_id}` - Delete a node

**Example - Create Node:**
```bash
POST /api/graph/nodes
{
  "node_id": "person:alex",
  "node_type": "person",
  "name": "Alex",
  "properties": {
    "occupation": "Software Engineer",
    "location": "Seattle"
  }
}
```

#### Edge Management
- `POST /api/graph/edges` - Create an edge between nodes
- `DELETE /api/graph/edges` - Remove an edge

**Example - Create Edge:**
```bash
POST /api/graph/edges
{
  "source_id": "person:alex",
  "relation": "likes",
  "target_id": "interest:golf",
  "weight": 0.9,
  "properties": {"since": "2020"}
}
```

#### Graph Extraction (GraphRAG)
- `POST /api/graph/extract` - Extract entities and relationships from text

**Example:**
```bash
POST /api/graph/extract
{
  "text": "My colleague Sarah works at Google and loves hiking"
}

# Returns:
{
  "success": true,
  "result": {
    "nodes_created": 3,  # person:sarah, organization:google, interest:hiking
    "edges_created": 2,  # works_at, likes
    "extracted": {...}
  }
}
```

#### Advanced Graph Operations
- `GET /api/graph/path?source_id=person:alex&target_id=organization:google` - Find path between nodes
- `GET /api/graph/neighbors?node_id=person:alex&relation=likes` - Get neighbors of a node
- `GET /api/graph/visualize?node_id=person:alex&max_depth=2` - Get graph data for visualization

**Example - Find Path:**
```bash
GET /api/graph/path?source_id=person:alex&target_id=organization:google&max_depth=5

# Returns path: alex → knows → person:sarah → works_at → google
```

### GraphRAG in Chat Flow

GraphRAG is **fully automatic** via CognitiveEngine - no manual code needed:

1. **Automatic Extraction**: When memories are stored, entities and relationships are automatically extracted (if `graph_config.auto_extract: true`)
2. **Query Classification**: CognitiveEngine automatically classifies queries (local/global/drift/basic) and routes to appropriate GraphRAG search method
3. **Graph Context**: Graph context is automatically included in every AI response via `CognitiveEngine.chat()`
4. **Deduplication**: Graph context is automatically deduplicated against memory context

**Example Chat:**
```python
# User sends message
result = await cognitive_engine.chat(
    user_id="user123",
    session_id="session1",
    user_query="My brother Alex loves golf"
)

# Behind the scenes:
# 1. Memory stored in LTM (vector search)
# 2. Graph extraction triggered automatically:
#    - person:user123 ──brother──► person:alex
#    - person:alex ──likes──► interest:golf
# 3. Graph nodes and edges created automatically

# User asks follow-up question
result = await cognitive_engine.chat(
    user_id="user123",
    session_id="session1",
    user_query="What should I get Alex for his birthday?"
)

# Behind the scenes:
# 1. Query classified: "local" (entity-focused)
# 2. Graph local_search() finds: person:alex → likes → interest:golf
# 3. Graph context + LTM memories combined automatically
# 4. AI Response: "Since Alex loves golf, consider golf accessories..."
```

**Key Point**: Never manually call `extract_graph_from_text()` or graph search methods. CognitiveEngine handles everything automatically.

### Graph Configuration

GraphRAG is enabled by default in `manifest.json`:

```json
{
  "graph_config": {
    "enabled": true,
    "auto_extract": true,
    "default_max_depth": 2,
    "node_types": [
      "person",
      "interest",
      "event",
      "location",
      "organization",
      "product",
      "concept",
      "document",
      "project"
    ]
  },
  "graphrag_config": {
    "enabled": true,
    "community_detection": {
      "enabled": true,
      "rebuild_threshold": 100
    },
    "query_classification": {
      "enabled": true,
      "use_llm": true,
      "cache_results": true
    },
    "search_methods": {
      "local_enabled": true,
      "global_enabled": true,
      "drift_enabled": true,
      "basic_fallback": true
    }
  }
}
```

**graphrag_config** enables advanced GraphRAG features:
- **Query Classification**: Automatically routes queries to Local/Global/DRIFT search
- **Community Detection**: Hierarchical community detection with summaries
- **Search Methods**: Enable/disable specific GraphRAG search methods

### Node Types

- **person**: People, users, individuals
- **interest**: Hobbies, topics, activities
- **event**: Meetings, occasions
- **location**: Places, cities, addresses
- **organization**: Companies, teams, groups
- **product**: Items, goods, services
- **concept**: Abstract ideas, skills
- **document**: Documents, files
- **project**: Projects, initiatives

### Relationship Types

Common relationships extracted automatically:
- **Social**: `knows`, `friend_of`, `colleague`
- **Family**: `parent_of`, `child_of`, `sibling_of`, `brother`, `sister`
- **Preference**: `likes`, `dislikes`, `loves`, `hates`, `interested_in`
- **Professional**: `works_at`, `manages`, `reports_to`, `skilled_at`
- **Location**: `lives_in`, `located_in`, `visited`
- **Membership**: `member_of`, `part_of`, `belongs_to`
- **Action**: `created`, `owns`, `attended`, `participated_in`

### Use Cases

**Family Assistant:**
```
User: "What should I get for my brother's birthday?"
→ Traverses: user → brother → person:alex → likes → interest:golf
→ Response: "Based on Alex's love of golf, consider golf accessories!"
```

**Professional Network:**
```
User: "Who might know someone at Google?"
→ Traverses: user → knows → person:sarah → works_at → organization:google
→ Response: "Sarah works at Google directly."
```

**Event Planning:**
```
User: "What activities would work for our book club?"
→ Traverses: organization:book_club → member_of → [person:alice, person:bob]
→ person:alice → likes → interest:wine
→ Response: "Wine tasting would appeal to most members."
```

### GraphRAG vs Traditional RAG

| Feature | Traditional RAG | GraphRAG |
|---------|----------------|----------|
| **Query Type** | "Tell me about X" | "What does X's Y like?" |
| **Reasoning** | Single-hop | Multi-hop |
| **Relationships** | Not captured | Explicitly modeled |
| **Complex Queries** | Limited | Powerful |
| **Use Case** | Document Q&A | Relationship queries |

See [GraphRAG Documentation](../../../../docs/GRAPHRAG.md) for comprehensive details.

## Usage

### Starting the App

```bash
# With Docker Compose (from sso-multi-app root)
docker-compose up ai-chat

# Or with multi-app mounting
cd apps
uvicorn multi_app_main:app --host 0.0.0.0 --port 8000
```

### Accessing the App

- **Multi-app mounting**: http://localhost:8000/ai-chat
- **Standalone**: http://localhost:8003

### Using Context Engineering

1. **Start chatting** - The AI will automatically use Context Engineering
2. **View Context Engineering panel** - Check the sidebar to see how context is built
3. **Reveal expertise** - Say things like "I'm a Python expert" to see dynamic persona adaptation
4. **Share preferences** - Mention OS, language, or preferences to see entity extraction
5. **Long conversations** - Watch STM optimization as chat history grows

### Example Interactions

**User**: "I'm a senior Python developer working on a FastAPI project."

**Context Engineering extracts**:
- Entity Fact: `Expertise: High`
- Dynamic Persona: "User is an expert. Be terse. Skip explanations."

**User**: "I use macOS and prefer TypeScript for frontend."

**Context Engineering extracts**:
- Entity Fact: `OS: macOS`
- Entity Fact: `Language: TypeScript`

## API Endpoints

### Send Message (Non-Streaming)

```bash
POST /api/conversations/{cid}/messages
Content-Type: application/x-www-form-urlencoded

message=Hello&category=general
```

**Response includes Context Engineering metadata**:

```json
{
  "success": true,
  "message": {
    "role": "assistant",
    "content": "Hello! How can I help you?"
  },
  "context_engineering": {
    "persona": {
      "role": "Orby - AI Assistant",
      "description": "Orby is an intelligent AI assistant...",
      "traits": {...}
    },
    "entity_facts": {
      "Name": "Alice",
      "OS": "macOS",
      "Language": "TypeScript",
      "Expertise": "High"
    },
    "dynamic_instructions": "User is an expert. Be terse...",
    "stm_summary": "Previous conversation about..."
  }
}
```

### Send Message (Streaming)

```bash
POST /api/conversations/{cid}/messages/stream
Content-Type: application/x-www-form-urlencoded

message=Hello&category=general&reasoning_effort=medium
```

**SSE Events**:
- `context`: Context Engineering metadata and retrieved memories
- `chunk`: Response content chunks
- `reasoning`: AI reasoning/thinking content
- `done`: Completion event

## Architecture

### Context Engineering Flow

1. **User sends message** → `send_message()` or `send_message_stream()`
2. **CognitiveEngine.chat()** called with `system_prompt=None`
3. **Context Engineering builds system prompt**:
   - Fetches persona from PersonaEngine
   - Extracts entity facts from memories
   - Builds dynamic persona instructions
   - Retrieves LTM memories (semantic search)
   - Optimizes STM (sliding window + summary)
   - Retrieves graph context (if enabled)
4. **Assembles context-engineered prompt** using `_construct_context_engineered_prompt()`
5. **LLM generates response** using context-engineered prompt
6. **Response includes Context Engineering metadata** for UI display

### Memory Storage

- **STM**: Stored in `chat_history` collection
- **LTM**: Stored in `user_memories` collection (with encryption if enabled)
- **Graph**: Stored in `kg` collection (if enabled)

## Configuration Files

### manifest.json

Key configuration sections:

- `memory_config.persona`: Persona configuration
- `memory_config.encrypted`: Enable CSFLE encryption
- `memory_config.cognitive`: Cognitive memory features
- `memory_config.graph`: Knowledge graph configuration
- `llm_config`: LLM provider configuration

### web.py

- `CognitiveEngine` initialization with Context Engineering flags
- `send_message()`: Non-streaming endpoint
- `send_message_stream()`: Streaming endpoint with Context Engineering

## Troubleshooting

### Context Engineering Not Working

- Check `enable_context_engineering=True` in `CognitiveEngine` initialization
- Verify persona is configured in `manifest.json`
- Check logs for Context Engineering metadata

### Persona Not Appearing

- Ensure `persona.enabled=true` in `manifest.json`
- Verify `PersonaEngine` is initialized (check logs)
- Check that `memory_service` has `persona_engine` attribute

### Entity Facts Not Extracted

- Ensure `enable_entity_extraction=True`
- Check that biographical/preference memories exist
- Verify memory search is returning results

### Streaming Not Working

- Check `llm_service` is initialized
- Verify LLM provider supports streaming
- Check browser console for SSE errors

## See Also

- [Perfect Brain Documentation](../../../../docs/PERFECT_BRAIN.md) - Comprehensive guide to Perfect Brain features
- [Context Engineering Documentation](../../../../docs/CONTEXT_ENGINEERING.md) - Comprehensive guide
- [Memory Service Documentation](../../../../docs/MEMORY_SERVICE.md) - Memory service overview
- [Files and Buckets Guide](../../../../docs/guides/FILES_AND_BUCKETS.md) - Bucket organization and shared memory
- [Cognitive Architecture](../../../../docs/COGNITIVE_ARCHITECTURE.md) - STM + LTM architecture
- [CSFLE Setup Guide](../../../../docs/guides/CSFLE_SETUP.md) - Encryption setup
- [SSO Multi-App README](../README.md) - Multi-app deployment guide
