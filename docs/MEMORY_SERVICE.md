# Memory Service - Complete Guide

## Overview

The **Memory Service** is MDB-Engine's intelligent memory management system for AI applications. It provides persistent, semantic memory storage and retrieval using MongoDB Atlas Vector Search, enabling your AI applications to remember user preferences, facts, and conversation context across sessions.

What makes MDB-Engine's memory service unique is its **Cognitive Architecture** - a biologically-inspired system that implements:

- **Ebbinghaus Forgetting Curve**: Memories decay over time unless reinforced
- **Flashbulb Memory**: High-emotion events are remembered with exceptional clarity
- **Spacing Effect**: Retrieved memories become harder to forget
- **Conflict Resolution**: Prevents storing contradictory facts
- **Cold Storage**: Paper trail for analytics and recovery

## Why Cognitive Memory?

### The Problem with Standard RAG

Traditional RAG (Retrieval-Augmented Generation) systems have a critical flaw: they treat all memories equally. A casual comment from three weeks ago has the same weight as a core fact mentioned yesterday. This leads to:

- **Context pollution**: LLMs distracted by irrelevant old memories
- **Contradictions**: AI confidently holds two conflicting facts as equally true
- **Memory bloat**: Unlimited growth without intelligent pruning
- **No emotional intelligence**: Can't distinguish significant life events from trivial facts

### The Cognitive Solution

MDB-Engine's memory service implements a **Silicon Hippocampus** - a dynamic cognitive system that:

1. **Prioritizes by Time**: Recent memories are "stronger" than old ones
2. **Rewards Rehearsal**: Frequently accessed memories become permanent
3. **Responds to Emotion**: High-emotion events get exceptional persistence
4. **Maintains Consistency**: Detects and flags contradictions
5. **Forgets Gracefully**: Weak memories fade to cold storage, not deletion

## Key Features

### Core Features (Always Available)
- **Automatic Fact Extraction**: LLM-powered extraction of key facts from conversations
- **Semantic Search**: Find relevant memories using vector similarity search
- **User Isolation**: All memories are scoped per user for privacy and security
- **Metadata Support**: Rich metadata filtering and custom fields
- **Bucket Awareness**: Full memory isolation between categories (work vs personal)
- **Zero-Configuration Setup**: Automatic index management
- **Memory Categories**: Every memory is automatically categorized as biographical, preferences, temporal, or relational (never "general")

### Cognitive Features (Enabled by Default)
- **Retrieval Strength**: `S = R × exp(-t / H)` - Ebbinghaus Forgetting Curve
- **Stability Growth**: Memories grow stronger with each retrieval (Spacing Effect)
- **Emotional Salience**: High-emotion facts get higher initial stability
- **Conflict Detection**: LLM-based logical consistency checking
- **Soft-Delete Pruning**: Weak memories move to cold storage
- **Memory Analytics**: Track memory health and patterns

### GraphRAG (Optional - `graph.enabled`)
- **Knowledge Graph**: Build entity-relationship graphs from memories
- **$graphLookup Traversal**: Multi-hop reasoning ("What does my brother like?")
- **Hybrid Search**: Combine vector similarity with graph structure
- **Auto-Extraction**: LLM extracts nodes (people, places, interests) and edges
- **See**: [GRAPHRAG.md](GRAPHRAG.md) for full documentation

### Bucket Awareness (Enterprise Feature)
- **Category Isolation**: Memories in "work" bucket won't appear when using "personal"
- **File Memory Integration**: Uploaded documents linked to their category bucket
- **Cross-Reference Support**: `associated_bucket_id` links related memories
- **CognitiveEngine Support**: Pass `bucket_id` to filter LTM search and storage

## Memory Categories vs Bucket Types

**Important Distinction:**

- **Memory Categories** (semantic classification): `biographical`, `preferences`, `temporal`, `relational`
  - Every memory MUST have one of these four categories
  - Automatically assigned during fact extraction
  - Used for semantic organization and retrieval
  - "general" is NOT a memory category

- **Bucket Types** (organizational filtering): `general`, `work`, `coding`, `file`, `conversation`, etc.
  - Used for memory isolation and filtering
  - Set via `bucket_type` parameter
  - "general" is a valid bucket_type for default/unfiltered memories
  - Examples: `bucket_type="work"` filters to work-related memories

**Example:**
```python
# Memory category: "relational" (about family relationships)
# Bucket type: "general" (default bucket, no filtering)
memory_service.add(
    messages="My sister Emily is a doctor",
    user_id="user123",
    bucket_type="general"  # This is bucket_type, NOT memory category
)
# Result: category="relational", metadata.bucket_type="general"
```

## LLM Model Inheritance

**Important**: The Memory Service automatically inherits the LLM model from your app's `llm_config.default_model`. If `memory_config.memory_llm_model` is not explicitly set, it will use the app's default LLM model. This ensures consistent LLM usage across all services (memory, graph, reflection, fusion, etc.).

**Service-Specific Override**: You can override the model for memory operations only by setting `memory_config.memory_llm_model` explicitly.

## Quick Start

### 1. Enable Memory Service

Add to your `manifest.json`:

```json
{
  "llm_config": {
    "enabled": true,
    "default_model": "gemini/gemini-3-flash-preview"
  },
  "memory_config": {
    "enabled": true,
    "collection_name": "user_memories",
    "cognitive": {
      "enabled": true,
      "decay": {
        "enabled": true,
        "default_stability_hours": 24
      },
      "emotion": {
        "enabled": true,
        "flashbulb_threshold": 0.7
      },
      "conflict_resolution": {
        "enabled": true
      },
      "pruning": {
        "max_capacity": 1000,
        "strategy": "soft_delete"
      },
      "cold_storage": {
        "enabled": true
      }
    }
  }
}
```

### 2. Use in Your Application

```python
from mdb_engine.dependencies import get_memory_service

@app.post("/chat")
async def chat(message: str, user_id: str, memory=Depends(get_memory_service)):
    # Check for conflicts before storing
    conflict = await memory.detect_knowledge_conflict(user_id, message)
    if conflict:
        logger.warning(f"Conflict detected: {conflict}")
    
    # Search for relevant memories (decay-aware ranking)
    memories = memory.search(
        query=message,
        user_id=user_id,
        limit=5,
        use_decay=True  # Enable decay-aware ranking
    )
    
    # Add new memory from conversation (with emotion extraction)
    memory.add(
        messages=[
            {"role": "user", "content": message},
            {"role": "assistant", "content": response}
        ],
        user_id=user_id
    )
    
    return {"response": response, "memories_used": len(memories)}
```

## Memory Encryption (CSFLE)

You can encrypt sensitive memory content at rest using MongoDB Client-Side Field Level Encryption (CSFLE).

### Enable Encryption

Add `"encrypted": true` to your memory configuration:

```json
{
  "memory_config": {
    "enabled": true,
    "encrypted": true
  }
}
```

This automatically encrypts the `content` and `text` fields of your memories. Metadata fields (`user_id`, `created_at`, `embedding`) remain unencrypted to allow for querying and vector search.

For setup details, see the [CSFLE Setup Guide](./guides/CSFLE_SETUP.md).

## The Math Layer

### Ebbinghaus Forgetting Curve

The core formula for retrieval strength:

```
S = R × exp(-t / H)
```

Where:
- **S**: Retrieval Strength (0.0 to 1.0) - how "present" the memory is
- **R**: Raw Importance (0.1 to 1.0) - AI-assessed significance
- **t**: Time since last access (hours)
- **H**: Stability (hours) - the memory's "half-life"

**Example**: A memory with importance 0.8 and stability 24 hours:
- After 0 hours: S = 0.8 × exp(0) = 0.8
- After 24 hours: S = 0.8 × exp(-1) = 0.29
- After 48 hours: S = 0.8 × exp(-2) = 0.11

### The Spacing Effect

Every time a memory is retrieved, its stability grows:

```
H_new = H_old × (1.2 + similarity + emotion × 1.5)
```

This mimics how human memory strengthens through rehearsal:
- A memory accessed daily becomes nearly permanent
- A memory never accessed fades quickly

### Flashbulb Memory

Initial stability is based on emotional intensity:

```
H_initial = default + (emotion × max_multiplier)
```

**Example** (default=24, multiplier=100):
- Neutral fact (emotion=0.2): stability = 44 hours
- Significant event (emotion=0.7): stability = 94 hours
- Life-changing event (emotion=0.95): stability = 119 hours

## The Memory Schema

Each memory document contains:

```python
{
    "user_id": "u123",
    "text": "User just got promoted to Senior Architect.",
    "embedding": [0.012, -0.04, ...],  # Vector for semantic search
    
    # Cognitive Fields
    "importance": 0.9,      # Raw Importance (R) - 0.1 to 1.0
    "stability": 94.0,      # Stability (H) - hours until strength drops
    "emotion": 0.85,        # Emotional intensity at recording
    "access_count": 5,      # How many times retrieved
    "last_accessed": ISODate("2026-02-03T10:00:00Z"),
    
    # Soft-Delete Fields
    "is_active": True,      # False = in cold storage
    "pruned_at": null,      # When moved to cold storage
    "pruning_reason": null, # Why pruned
    
    # Organization
    "category": "biographical",
    "metadata": {
        "bucket_id": "conversation:123",
        "source": "chat"
    },
    
    "created_at": ISODate("2026-02-01T14:30:00Z"),
    "updated_at": ISODate("2026-02-03T10:00:00Z")
}
```

## Configuration Reference

### Full Configuration Example

```json
{
  "memory_config": {
    "enabled": true,
    "provider": "cognitive",
    "collection_name": "user_memories",
    "embedding_model": "text-embedding-3-small",
    "chat_model": "gpt-4o",
    "memory_llm_model": "gemini/gemini-3-flash-preview",  // Inherits from llm_config.default_model if not set
    "embedding_model_dims": 1536,
    "infer": true,
    "async_mode": true,
    "enable_cognitive": true,
    "max_depth": 100,
    "similarity_threshold": 0.7,
    "reinforcement_factor": 1.1,
    "decay_factor": 0.99,
    "merge_threshold_low": 0.7,
    "merge_threshold_high": 0.85,
    "duplicate_threshold": 0.90,
    
    "cognitive": {
      "enabled": true,
      
      "decay": {
        "enabled": true,
        "default_stability_hours": 24,
        "min_stability": 0.1,
        "use_server_side_pipeline": true
      },
      
      "emotion": {
        "enabled": true,
        "flashbulb_threshold": 0.7,
        "max_stability_multiplier": 100
      },
      
      "conflict_resolution": {
        "enabled": true,
        "similarity_threshold": 0.85,
        "llm_model": null
      },
      
      "pruning": {
        "enabled": true,
        "max_capacity": 1000,
        "prune_percentage": 0.1,
        "strategy": "soft_delete"
      },
      
      "cold_storage": {
        "enabled": true,
        "retention_days": 365
      }
    },
    
    "redaction": {
      "enabled": true,
      "replacement": "[REDACTED]",
      "patterns": {
        "ssn": true,
        "credit_card": true,
        "password": true,
        "api_key": true,
        "phone": false,
        "email": false
      }
    },
    
    "categories": {
      "enabled": true,
      "custom_categories": ["work", "health", "finance", "travel"]
    },

    "encryption": {
      "enabled": true,
      "encrypted": true,
      "kms_provider": "local" 
    },
    
    "reflection": {
      "enabled": true,
      "interval_hours": 24,
      "message_threshold": 50,
      "min_salience_to_keep": 0.3,
      "store_reflections": true
    }
  }
}
```

### Configuration Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `boolean` | `false` | Enable memory service |
| `provider` | `string` | `"cognitive"` | Memory provider (`"cognitive"` or `"custom"`) |
| `collection_name` | `string` | `"{slug}_memories"` | Collection name |
| `embedding_model` | `string` | `"text-embedding-3-small"` | Embedding model name |
| `memory_llm_model` | `string` | (inherits from `llm_config.default_model`) | LLM for memory operations. If not set, automatically uses the app's default LLM model from `llm_config.default_model` |
| `enable_cognitive` | `boolean` | `true` | Enable cognitive features |
| `max_depth` | `integer\|null` | `100` | Max memories per user |
| `similarity_threshold` | `number` | `0.7` | General similarity threshold |
| `reinforcement_factor` | `number` | `1.1` | Importance boost factor when memory is reinforced |
| `merge_threshold_low` | `number` | `0.7` | Lower bound for memory merging (similarity between low and high) |
| `merge_threshold_high` | `number` | `0.85` | Upper bound for memory merging (similarity between high and duplicate) |
| `duplicate_threshold` | `number` | `0.90` | Threshold for duplicate detection - memories with similarity ≥ this are boosted instead of creating duplicates |

### Cognitive Configuration

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `cognitive.decay.enabled` | `boolean` | `true` | Enable Ebbinghaus decay |
| `cognitive.decay.default_stability_hours` | `number` | `24` | Default half-life |
| `cognitive.decay.use_server_side_pipeline` | `boolean` | `true` | Use MongoDB aggregation |
| `cognitive.emotion.enabled` | `boolean` | `true` | Enable emotion extraction |
| `cognitive.emotion.flashbulb_threshold` | `number` | `0.7` | High-emotion threshold |
| `cognitive.emotion.max_stability_multiplier` | `number` | `100` | Max stability boost |
| `cognitive.conflict_resolution.enabled` | `boolean` | `true` | Enable conflict detection |
| `cognitive.pruning.max_capacity` | `integer` | `1000` | Max active memories |
| `cognitive.pruning.strategy` | `string` | `"soft_delete"` | `"soft_delete"` or `"hard_delete"` |
| `cognitive.cold_storage.enabled` | `boolean` | `true` | Enable cold storage |
| `cognitive.cold_storage.retention_days` | `integer` | `365` | Days to retain |

## API Reference

### Adding Memories

#### `add()` - Add with Cognitive Extraction

```python
memories = memory_service.add(
    messages=[
        {"role": "user", "content": "I just got promoted to Senior Engineer!"}
    ],
    user_id="user123"
)

# Returns:
[{
    "id": "507f1f77bcf86cd799439011",
    "memory": "User got promoted to Senior Engineer",
    "category": "biographical",
    "emotion": 0.9,
    "stability": 114.0,  # High emotion = high stability
    "importance": 0.85,
    "action": "created"
}]
```

#### `inject()` - Direct Injection with Emotion

```python
memory = memory_service.inject(
    memory="User prefers dark mode interfaces",
    user_id="user123",
    emotion=0.4,  # Moderate preference
    importance=0.7,
    metadata={"category": "preferences"}
)
```

### Searching Memories

#### `search()` - Decay-Aware Search

```python
results = memory_service.search(
    query="What does the user like?",
    user_id="user123",
    limit=5,
    use_decay=True  # Enable decay-aware ranking
)

# Results include strength scores
[{
    "id": "507f...",
    "memory": "User loves chocolate",
    "score": 0.72,       # Combined score
    "similarity": 0.85,  # Vector similarity
    "strength": 0.85,    # Current retrieval strength
    "stability": 124.5,  # Current stability
    "emotion": 0.6,
    "last_accessed": "2026-02-03T10:00:00Z"
}]
```

### Conflict Detection

#### `detect_knowledge_conflict()` - Check for Contradictions

```python
# Async version
conflict = await memory_service.detect_knowledge_conflict(
    user_id="user123",
    new_fact="User is allergic to shellfish"
)

if conflict:
    print(f"Conflict detected: {conflict}")
    # Handle conflict (ask for clarification, update old memory, etc.)

# Sync version
conflict = memory_service.detect_knowledge_conflict_sync(
    user_id="user123",
    new_fact="User loves seafood"
)
```

### Cold Storage Operations

#### `get_cold_storage()` - Retrieve Pruned Memories

```python
cold_memories = memory_service.get_cold_storage(
    user_id="user123",
    limit=50,
    include_reason=True
)

# Returns:
[{
    "id": "507f...",
    "memory": "User mentioned they like vanilla ice cream",
    "pruned_at": "2026-02-01T12:00:00Z",
    "pruning_reason": "capacity_limit_reached",
    "importance": 0.3,
    "stability": 12.5
}]
```

#### `restore_from_cold_storage()` - Recover a Memory

```python
restored = memory_service.restore_from_cold_storage(
    memory_id="507f1f77bcf86cd799439011",
    user_id="user123"
)
```

### Analytics

#### `get_memory_analytics()` - Memory Health Dashboard

```python
analytics = memory_service.get_memory_analytics(user_id="user123")

# Returns:
{
    "user_id": "user123",
    "active_memories": 450,
    "cold_storage_memories": 150,
    "total_memories": 600,
    "capacity_used": 0.45,  # 45% of max_capacity
    "average_strength": 0.62,
    "average_stability": 48.5,
    "average_emotion": 0.35,
    "weak_memories": 45,    # strength < 0.3
    "strong_memories": 120, # strength > 0.7
    "categories": {
        "biographical": 80,
        "preferences": 150,
        "temporal": 100,
        "relational": 60
        // Note: "general" may appear in analytics for backward compatibility with old memories,
        // but new memories will never have "general" as a category
    }
}
```

## Bucket Awareness

Bucket Awareness ensures complete memory isolation between different contexts. Memories stored in the "work" bucket will never appear when searching from the "personal" bucket.

### How It Works

1. **Conversation memories** are stored with `bucket_id` and `associated_bucket_id`
2. **File memories** (uploaded documents) use `associated_bucket_id` to link to their category
3. **Search** filters by `associated_bucket_id` to find BOTH conversation and file memories

### Using CognitiveEngine with Bucket Awareness

```python
from mdb_engine.memory import CognitiveEngine

# Chat with "work" context - only work memories retrieved
result = await cognitive_engine.chat(
    user_id="user123",
    session_id="conversation:456",
    user_query="What meetings do I have?",
    bucket_id="category:work:user123",     # Bucket filter
    bucket_type="category",                 # Bucket type
    extract_facts=True
)

# Chat with "personal" context - work memories are invisible
result = await cognitive_engine.chat(
    user_id="user123",
    session_id="conversation:789",
    user_query="What did I plan for this weekend?",
    bucket_id="category:personal:user123",
    bucket_type="category",
    extract_facts=True
)
```

### File Memory Integration

When files are uploaded to a bucket, their extracted facts are automatically linked:

```python
# File uploaded to "work" bucket
# bucket_id = "file:quarterly_report.pdf:user123"
# associated_bucket_id = "category:work:user123"  (links to category)

# Later searches find BOTH:
# - Conversation memories where associated_bucket_id = "category:work:user123"
# - File memories where associated_bucket_id = "category:work:user123"
```

### Bucket ID Patterns

| Pattern | Description | Example |
|---------|-------------|---------|
| `category:{name}:{user_id}` | Category bucket | `category:work:user123` |
| `session:{id}` | Session-scoped (default) | `session:conv456` |
| `file:{filename}:{user_id}` | File bucket | `file:report.pdf:user123` |

### API Endpoints with Bucket Awareness

```python
# Search memories in a specific bucket
GET /api/memories/search?query=meetings&context_id=category:work:user123

# Get all memories in a bucket
GET /api/memories/by-context?bucket_id=category:work:user123
```

## Use Cases

### 1. Personal AI Assistant

```python
class PersonalAssistant:
    async def chat(self, user_id: str, message: str):
        # Check for conflicts
        conflict = await self.memory.detect_knowledge_conflict(user_id, message)
        
        # Get relevant memories with decay awareness
        memories = self.memory.search(
            query=message,
            user_id=user_id,
            limit=5,
            use_decay=True
        )
        
        # Build context from strong memories only
        strong_memories = [m for m in memories if m["strength"] > 0.5]
        context = "\n".join([m["memory"] for m in strong_memories])
        
        # Generate response
        response = await self.llm.generate(
            system=f"User context:\n{context}",
            user=message
        )
        
        # Store conversation with emotional analysis
        self.memory.add(
            messages=[
                {"role": "user", "content": message},
                {"role": "assistant", "content": response}
            ],
            user_id=user_id
        )
        
        return response
```

### 2. Customer Support Bot

```python
# High-importance facts persist
memory.inject(
    memory="Customer is a Premium tier subscriber",
    user_id=customer_id,
    importance=0.95,
    emotion=0.6,  # Important but not emotional
    metadata={"type": "account_status"}
)

# Support context adapts over time
memories = memory.search(
    query="billing issue",
    user_id=customer_id,
    filters={"metadata.type": {"$in": ["billing", "account_status"]}}
)
```

### 3. Healthcare Companion (GDPR Compliant)

```python
# Configure for healthcare compliance
manifest = {
    "memory_config": {
        "cognitive": {
            "pruning": {
                "strategy": "soft_delete"  # Never hard delete
            },
            "cold_storage": {
                "enabled": true,
                "retention_days": 2555  # 7 years
            }
        },
        "redaction": {
            "enabled": true,
            "patterns": {
                "ssn": true,
                "phone": true,
                "email": true
            }
        }
    }
}

# Critical health info gets high emotion
memory.inject(
    memory="Patient is allergic to penicillin",
    user_id=patient_id,
    importance=0.99,
    emotion=0.95,  # Critical - maximum stability
    metadata={"type": "allergy", "severity": "critical"}
)
```

## Best Practices

### 1. Tune Stability for Your Use Case

```python
# Short-term assistant (session-based)
"default_stability_hours": 4

# Personal assistant (daily interactions)
"default_stability_hours": 24

# Long-term knowledge base
"default_stability_hours": 168  # 1 week
```

### 2. Use Emotion Appropriately

```python
# Let the LLM assess emotion (recommended)
memory.add(messages=[...], user_id=user_id)

# Or set manually for critical facts
memory.inject(
    memory="User has diabetes",
    user_id=user_id,
    emotion=0.9,  # Critical health info
    importance=0.95
)
```

### 3. Handle Conflicts Gracefully

```python
conflict = await memory.detect_knowledge_conflict(user_id, new_fact)
if conflict:
    # Option 1: Ask for clarification
    return f"I noticed something that might contradict what you said before: {conflict}"
    
    # Option 2: Update old memory
    old_memories = memory.search(query=new_fact, user_id=user_id, limit=1)
    memory.update(memory_id=old_memories[0]["id"], memory=new_fact)
    
    # Option 3: Store both with metadata
    memory.inject(
        memory=new_fact,
        user_id=user_id,
        metadata={"supersedes": old_memories[0]["id"]}
    )
```

### 4. Monitor Memory Health

```python
analytics = memory.get_memory_analytics(user_id)

# Alert if too many weak memories
if analytics["weak_memories"] > analytics["active_memories"] * 0.5:
    logger.warning(f"User {user_id} has many weak memories - consider reflection")

# Alert if approaching capacity
if analytics["capacity_used"] > 0.9:
    logger.warning(f"User {user_id} approaching memory capacity")
```

## What NOT to Do

### Don't Store Everything

```python
# ❌ Bad: Storing trivial conversation
memory.add(messages=[
    {"role": "user", "content": "Hello"},
    {"role": "assistant", "content": "Hi there!"}
], user_id=user_id)

# ✅ Good: Let the LLM filter (infer=True is default)
# It will return empty if no facts to extract
```

### Don't Disable Cold Storage in Production

```python
# ❌ Bad: Hard delete with no paper trail
"pruning": {
    "strategy": "hard_delete"
}

# ✅ Good: Soft delete for recovery and analytics
"pruning": {
    "strategy": "soft_delete"
}
```

### Don't Ignore Conflicts

```python
# ❌ Bad: Blindly storing without checking
memory.add(messages=[...], user_id=user_id)

# ✅ Good: Check for conflicts first
conflict = await memory.detect_knowledge_conflict(user_id, new_fact)
if not conflict:
    memory.add(messages=[...], user_id=user_id)
```

## Troubleshooting

### Memories Decaying Too Fast

**Symptoms**: Important memories have low strength after short time

**Solutions**:
1. Increase `default_stability_hours`
2. Ensure emotion extraction is working (check `emotion` field)
3. Access memories more frequently to trigger spacing effect

### Conflict Detection Too Sensitive

**Symptoms**: False positives for updates vs contradictions

**Solutions**:
1. Increase `conflict_resolution.similarity_threshold`
2. Review the conflict detection prompts in logs
3. Consider context in your application logic

### Cold Storage Growing Too Large

**Symptoms**: Too many pruned memories

**Solutions**:
1. Reduce `cold_storage.retention_days`
2. Implement periodic hard-delete of very old cold storage
3. Analyze cold storage patterns to improve initial importance scoring

## Architecture

### Memory Flow

```
User Input
    │
    ▼
┌─────────────────┐
│ Conflict Check  │ ──► Detect contradictions with existing memories
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Fact Extraction │ ──► LLM extracts atomic facts with emotion
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Stability Calc  │ ──► Initial stability based on emotion
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Vector Search   │ ──► Check for duplicates/reinforcement
└────────┬────────┘
         │
         ├──► Similar (>0.85): Reinforce existing
         │
         ├──► Related (0.7-0.85): Merge memories
         │
         └──► New: Create memory document
                    │
                    ▼
            ┌───────────────┐
            │ MongoDB Atlas │
            │ Active Memory │
            └───────────────┘
                    │
                    │ (When capacity exceeded)
                    ▼
            ┌───────────────┐
            │ Cold Storage  │
            │ (is_active=F) │
            └───────────────┘
```

### Search Flow

```
Query
    │
    ▼
┌─────────────────┐
│ Embed Query     │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────┐
│ MongoDB Aggregation Pipeline    │
│                                 │
│ 1. $vectorSearch (is_active=T)  │
│ 2. $addFields (similarity)      │
│ 3. $addFields (t = now - last)  │
│ 4. $addFields (strength = R*e^) │
│ 5. $addFields (final_score)     │
│ 6. $sort (final_score DESC)     │
│ 7. $limit                       │
└────────┬────────────────────────┘
         │
         ▼
┌─────────────────┐
│ Update Access   │ ──► Increment access_count, grow stability
└────────┬────────┘
         │
         ▼
    Return Results
```

## Related Documentation

- [Cognitive Architecture](./COGNITIVE_ARCHITECTURE.md) - Deep dive into the cognitive system
- [Cognitive Memory](./COGNITIVE_MEMORY.md) - Technical implementation details
- [GDPR Compliance](./GDPR_COMPLIANCE.md) - Privacy and data protection
- [Manifest Reference](./MANIFEST_REFERENCE.md) - Configuration reference
- [Best Practices](./BEST_PRACTICES.md) - Production patterns

## Summary

MDB-Engine's Memory Service transforms your AI from a stateless responder into a **cognitive companion** that:

1. **Remembers what matters** - Important facts persist, trivial ones fade
2. **Learns through repetition** - Frequently accessed memories become permanent
3. **Feels the weight of moments** - Emotional events create lasting impressions
4. **Maintains consistency** - Contradictions are detected and flagged
5. **Forgets gracefully** - Weak memories move to cold storage, not oblivion

This is not just a vector database wrapper - it's a **Silicon Hippocampus** that brings human-like memory dynamics to your AI applications.
