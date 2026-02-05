# MDB-Engine Memory System: Deep Analysis

> **Comprehensive technical analysis of the cognitive memory architecture, implementation details, and system design**

**Date:** February 5, 2026  
**Version:** 2.0  
**Status:** Production-Ready

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Architecture Overview](#architecture-overview)
3. [Core Components](#core-components)
4. [Cognitive Features Deep Dive](#cognitive-features-deep-dive)
5. [Data Flow & Lifecycle](#data-flow--lifecycle)
6. [Mathematical Models](#mathematical-models)
7. [Storage & Retrieval](#storage--retrieval)
8. [Advanced Features](#advanced-features)
9. [Performance Characteristics](#performance-characteristics)
10. [Security & Privacy](#security--privacy)
11. [Integration Patterns](#integration-patterns)
12. [Limitations & Trade-offs](#limitations--trade-offs)
13. [Future Enhancements](#future-enhancements)

---

## Executive Summary

MDB-Engine's Memory System is a **biologically-inspired cognitive architecture** that transforms AI applications from stateless responders into intelligent companions with persistent, context-aware memory. Unlike traditional RAG systems that treat all memories equally, this system implements:

- **Ebbinghaus Forgetting Curve**: Memories decay over time unless reinforced
- **Spacing Effect**: Frequently accessed memories become permanent
- **Flashbulb Memory**: High-emotion events persist with exceptional clarity
- **Conflict Resolution**: Prevents storing contradictory facts
- **Soft-Delete Pruning**: Weak memories move to cold storage, not oblivion

### Key Metrics

- **Memory Capacity**: Configurable (default: 1000 active memories per user)
- **Decay Model**: Exponential decay with configurable half-life (default: 24 hours)
- **Search Performance**: Sub-100ms for vector search with decay-aware ranking
- **Storage**: MongoDB Atlas Vector Search (native integration)
- **LLM Integration**: Supports 100+ providers via LiteLLM

---

## Architecture Overview

### Two-Tier Memory System

```
┌─────────────────────────────────────────────────────────────┐
│                    USER INTERACTION                         │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
        ┌───────────────────────────────┐
        │    CognitiveEngine             │
        │    (Orchestrator)              │
        └───────────┬───────────────────┘
                    │
        ┌───────────┴───────────┐
        │                       │
        ▼                       ▼
┌───────────────┐      ┌───────────────┐
│  STM          │      │  LTM           │
│  (Short-Term) │      │  (Long-Term)  │
├───────────────┤      ├───────────────┤
│ Chat History  │      │ Vector Store  │
│ Raw Messages  │      │ Extracted     │
│ Session-Scoped│      │ Facts         │
│ Fast Retrieval│      │ User-Scoped   │
│ TTL: 24h      │      │ Semantic      │
└───────────────┘      │ Search        │
                       │ Decay-Aware   │
                       │ Ranking       │
                       └───────────────┘
```

### Component Hierarchy

```
BaseMemoryService (Abstract)
    │
    └── CognitiveMemoryService (Concrete Implementation)
            │
            ├── Fact Extraction (LLM-powered)
            ├── Embedding Generation
            ├── Vector Search (MongoDB Atlas)
            ├── Cognitive Math Layer
            │   ├── Ebbinghaus Decay
            │   ├── Spacing Effect
            │   └── Flashbulb Memory
            ├── Conflict Detection
            ├── Duplicate Detection
            ├── Memory Merging
            ├── Pruning & Cold Storage
            └── Analytics

CognitiveEngine (Orchestrator)
    │
    ├── ChatHistoryService (STM)
    ├── CognitiveMemoryService (LTM)
    ├── LLM Provider Abstraction
    └── Context Assembly

Supporting Services
    ├── ReflectionService (Consolidation)
    ├── MemoryFusionService (Deduplication)
    ├── RedactionService (Privacy)
    └── PerceptionEngine (Context Analysis)
```

---

## Core Components

### 1. BaseMemoryService (Abstract Interface)

**Purpose**: Defines the contract for all memory service implementations.

**Key Methods**:
- `add()` - Add memories with LLM extraction
- `inject()` - Direct memory injection
- `search()` - Semantic search with decay-aware ranking
- `get_all()` - Retrieve all memories with filtering
- `update()` - Update existing memory
- `delete()` - Delete memory (soft or hard)

**Design Pattern**: Abstract Base Class (ABC) for extensibility

### 2. CognitiveMemoryService (Primary Implementation)

**Purpose**: The main memory service with cognitive features.

**Key Features**:
- **Fact Extraction**: LLM-powered extraction of atomic facts from conversations
- **Emotion Detection**: Automatic assessment of emotional intensity (0.0-1.0)
- **Category Classification**: Automatic categorization (biographical, preferences, temporal, relational)
- **Importance Scoring**: AI-assessed significance (0.1-1.0)
- **Stability Calculation**: Initial stability based on emotion
- **Duplicate Detection**: Prevents semantically identical memories
- **Memory Merging**: Combines related memories intelligently
- **Reinforcement**: Strengthens existing memories on retrieval

**Configuration**:
```python
{
    "max_depth": 1000,                    # Max active memories
    "similarity_threshold": 0.7,          # General similarity threshold
    "duplicate_threshold": 0.90,          # Duplicate detection threshold
    "merge_threshold_low": 0.70,          # Lower bound for merging
    "merge_threshold_high": 0.85,         # Upper bound for reinforcement
    "reinforcement_factor": 1.1,         # Importance boost on reinforcement
    "decay_factor": 0.99,                 # Decay rate for unused memories
    "enable_cognitive": True,             # Enable cognitive features
    "infer": True,                        # Enable LLM fact extraction
}
```

### 3. CognitiveEngine (Orchestrator)

**Purpose**: Coordinates STM and LTM to provide complete context.

**Workflow**:
1. Save user message to STM
2. Search LTM for relevant memories (decay-aware)
3. Retrieve STM context (last N messages)
4. Assemble prompt with context
5. Generate LLM response
6. Save response to STM
7. Extract facts to LTM (async, non-blocking)

**Key Parameters**:
- `stm_context_limit`: Number of recent messages (default: 10)
- `ltm_search_limit`: Number of memories to retrieve (default: 5)
- `auto_summarize_threshold`: Trigger summarization (default: 20)

### 4. ChatHistoryService (STM)

**Purpose**: Manages short-term conversation context.

**Schema**:
```javascript
{
    "_id": ObjectId("..."),
    "session_id": "conversation:123",
    "user_id": "user_123",
    "role": "user" | "assistant" | "system",
    "content": "Message text",
    "created_at": ISODate("..."),
    "metadata": {}
}
```

**Features**:
- Fast retrieval by `session_id`
- TTL index (24h default) for automatic cleanup
- Indexed on `(session_id, created_at)` and `(user_id, created_at)`

---

## Cognitive Features Deep Dive

### 1. Ebbinghaus Forgetting Curve

**Mathematical Model**:
```
S = R × exp(-t / H)
```

Where:
- **S**: Retrieval Strength (0.0 to 1.0) - how "present" the memory is
- **R**: Raw Importance (0.1 to 1.0) - AI-assessed significance
- **t**: Time since last access (hours)
- **H**: Stability (hours) - the memory's "half-life"

**Implementation**:
```python
class CognitiveMath:
    @staticmethod
    def get_current_strength(doc: dict) -> float:
        importance = doc.get("importance", 0.5)
        stability = doc.get("stability", 24.0)
        last_accessed = doc.get("last_accessed")
        
        if not last_accessed:
            return importance
        
        t_hours = (now - last_accessed).total_seconds() / 3600.0
        strength = importance * math.exp(-t_hours / stability)
        
        return max(min(strength, 1.0), 0.01)
```

**Decay Examples**:
- Memory with `importance=0.8`, `stability=24h`:
  - 0 hours: strength = 0.80
  - 24 hours: strength = 0.29 (half-life)
  - 48 hours: strength = 0.11
  - 72 hours: strength = 0.04

**Server-Side Pipeline** (MongoDB Aggregation):
```javascript
{
    "$addFields": {
        "t_hours": {
            "$divide": [
                {"$subtract": [now, {"$ifNull": ["$last_accessed", now]}]},
                3600000  // milliseconds to hours
            ]
        },
        "strength": {
            "$multiply": [
                "$_importance",
                {"$exp": {"$divide": [{"$multiply": ["$t_hours", -1]}, "$_stability"]}}
            ]
        }
    }
}
```

### 2. Spacing Effect (Rehearsal)

**Mathematical Model**:
```
H_new = H_old × (1.2 + similarity + emotion × 1.5)
```

**Why It Matters**:
- Every retrieval strengthens the memory
- Frequently accessed memories become permanent
- Mimics human memory consolidation

**Implementation**:
```python
@staticmethod
def grow_stability(
    current_stability: float,
    similarity: float = 0.0,
    emotion: float = 0.0,
) -> float:
    growth_factor = 1.2  # Base 20% increase
    growth_factor += similarity  # Relevance boost
    growth_factor += emotion * 1.5  # Emotional boost
    
    new_stability = current_stability * growth_factor
    return min(new_stability, MAX_STABILITY)  # Cap at 10000 hours
```

**Example Growth**:
- Initial: `stability = 24 hours`
- Retrieved once (`similarity=0.8`): `stability = 24 × (1.2 + 0.8) = 48 hours`
- Retrieved again (`similarity=0.9`): `stability = 48 × (1.2 + 0.9) = 100.8 hours`
- After 5 retrievals: effectively permanent (>1000 hours)

### 3. Flashbulb Memory (Emotion)

**Mathematical Model**:
```
H_initial = default + (emotion × max_multiplier)
```

**Implementation**:
```python
@staticmethod
def calculate_initial_stability(
    emotion: float,
    default_hours: float = 24.0,
    max_multiplier: float = 100.0,
) -> float:
    stability = default_hours + (emotion * max_multiplier)
    return stability
```

**Examples**:
- Neutral fact (`emotion=0.2`): `stability = 24 + 20 = 44 hours`
- Significant event (`emotion=0.7`): `stability = 24 + 70 = 94 hours`
- Life-changing event (`emotion=0.95`): `stability = 24 + 95 = 119 hours`

**Emotion Detection**:
- LLM-powered assessment during fact extraction
- Considers linguistic markers, context, and user sentiment
- Range: 0.0 (mundane) to 1.0 (highly emotional)

### 4. Conflict Detection

**Purpose**: Prevents "digital dementia" - storing contradictory facts.

**Process**:
1. Generate embedding for new fact
2. Find similar existing memories (`similarity >= 0.85`)
3. Build context with existing knowledge
4. LLM analyzes for logical contradictions
5. Return conflict description if found

**LLM Prompt**:
```
You are a logical consistency engine. Your job is to detect contradictions.

EXISTING KNOWLEDGE:
- User loves seafood
- User prefers Italian cuisine

NEW INFORMATION:
User is allergic to shellfish

Does the 'NEW INFORMATION' logically contradict any of the 'EXISTING KNOWLEDGE'?

Rules:
1. A contradiction means two statements cannot both be true at the same time.
2. Updates to information are NOT contradictions.
3. Different preferences at different times are NOT contradictions.
4. Only flag clear logical contradictions.

If you find a CONTRADICTION, explain it briefly in 1-2 sentences.
If there is NO CONTRADICTION, respond with exactly: CLEAN
```

**Usage**:
```python
conflict = await memory_service.detect_knowledge_conflict(
    user_id="user123",
    new_fact="User is allergic to shellfish",
    similarity_threshold=0.85
)

if conflict:
    # Handle conflict: ask user, update old memory, or flag
    logger.warning(f"Conflict detected: {conflict}")
```

### 5. Duplicate Detection & Reinforcement

**Similarity Thresholds**:
- **Duplicate** (`similarity >= 0.90`): Boost existing memory, no new memory created
- **Reinforcement** (`0.85 <= similarity < 0.90`): Strengthen existing memory
- **Merge** (`0.70 <= similarity < 0.85`): Merge related memories
- **New** (`similarity < 0.70`): Create new memory

**Duplicate Handling**:
```python
if similarity >= duplicate_threshold:
    # Boost existing memory
    existing_memory["importance"] *= reinforcement_factor  # Default: 1.1
    existing_memory["access_count"] += 1
    existing_memory["last_accessed"] = now
    existing_memory["mention_count"] = existing_memory.get("mention_count", 0) + 1
    # No new memory created
```

**Reinforcement**:
```python
elif similarity >= merge_threshold_high:
    # Reinforce existing memory
    existing_memory["importance"] *= reinforcement_factor
    existing_memory["access_count"] += 1
    # Optionally merge text if new version is more specific
    if is_more_specific(new_text, existing_text):
        existing_memory["text"] = merge_texts(existing_text, new_text)
```

**Merging**:
```python
elif similarity >= merge_threshold_low:
    # Merge memories using LLM
    merged_text = await llm_merge_memories(existing_text, new_text)
    merged_importance = max(existing_importance, new_importance) * 1.1
    merged_access_count = existing_access_count + new_access_count
    
    # Update existing memory, delete new one
    update_memory(existing_id, merged_text, merged_importance)
    delete_memory(new_id)
```

### 6. Memory Pruning & Cold Storage

**Purpose**: Manage capacity by moving weak memories to cold storage.

**Pruning Criteria**:
- Capacity exceeded (`active_count > max_capacity`)
- Score memories by effective strength
- Prune weakest memories (lowest strength)

**Scoring**:
```python
if use_strength:
    # Use decay-aware strength
    score = CognitiveMath.get_current_strength(memory)
else:
    # Use effective importance
    score = importance * (1 + math.log(access_count + 1))
```

**Soft-Delete Process**:
```python
def prune_memories(
    user_id: str,
    max_capacity: int = 1000,
    prune_percentage: float = 0.1,
    reason: str = "capacity_limit_reached"
) -> int:
    active_count = count_active_memories(user_id)
    
    if active_count <= max_capacity:
        return 0
    
    # Calculate how many to prune (with buffer)
    to_prune = int((active_count - max_capacity) * (1 + prune_percentage))
    
    # Get weakest memories
    weakest = get_memories_sorted_by_strength(user_id, limit=to_prune)
    
    # Soft-delete (move to cold storage)
    for memory in weakest:
        memory["is_active"] = False
        memory["pruned_at"] = now
        memory["pruning_reason"] = reason
    
    return len(weakest)
```

**Cold Storage Schema**:
```javascript
{
    "_id": ObjectId("..."),
    "user_id": "user_123",
    "text": "User mentioned liking vanilla",
    "is_active": false,              // Moved to cold storage
    "pruned_at": ISODate("..."),     // When pruned
    "pruning_reason": "capacity_limit_reached",
    "importance": 0.3,
    "stability": 12.5,
    // ... other fields preserved
}
```

**Recovery**:
```python
def restore_from_cold_storage(memory_id: str, user_id: str):
    memory = get_memory(memory_id, user_id)
    if memory and not memory.get("is_active"):
        memory["is_active"] = True
        memory["pruned_at"] = None
        memory["pruning_reason"] = None
        update_memory(memory)
        return memory
    return None
```

---

## Data Flow & Lifecycle

### Memory Creation Flow

```
User Input: "I'm John, I work at Google, and I love hiking"
    │
    ▼
┌─────────────────────────────────────────┐
│  CognitiveEngine.add_message()         │
│  → Save to STM (ChatHistoryService)    │
└───────────────┬─────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────┐
│  CognitiveEngine.extract_facts()       │
│  → LLM Fact Extraction                  │
└───────────────┬─────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────┐
│  Extract 3 Facts:                      │
│  1. "User's name is John"              │
│  2. "User works at Google"             │
│  3. "User loves hiking"                │
└───────────────┬─────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────┐
│  For Each Fact:                        │
│  ├─ Generate Embedding                 │
│  ├─ Detect Emotion (LLM)               │
│  ├─ Assign Category (LLM)              │
│  ├─ Calculate Importance (LLM)         │
│  └─ Calculate Initial Stability        │
└───────────────┬─────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────┐
│  Check for Duplicates/Similar          │
│  ├─ similarity >= 0.90 → Boost        │
│  ├─ 0.85 <= similarity < 0.90 → Reinforce
│  ├─ 0.70 <= similarity < 0.85 → Merge
│  └─ similarity < 0.70 → Create New    │
└───────────────┬─────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────┐
│  Store in MongoDB                       │
│  → {app_slug}_memories collection       │
│  → Vector Search Index                  │
└─────────────────────────────────────────┘
```

### Memory Retrieval Flow

```
Query: "What does the user like?"
    │
    ▼
┌─────────────────────────────────────────┐
│  Generate Query Embedding              │
└───────────────┬─────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────┐
│  MongoDB Vector Search                  │
│  → $vectorSearch aggregation            │
│  → Filter: is_active=true, user_id      │
│  → Limit: 15 candidates                 │
└───────────────┬─────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────┐
│  Decay-Aware Ranking Pipeline          │
│  ├─ Calculate t_hours (time elapsed)   │
│  ├─ Calculate strength (S = R×e^(-t/H))│
│  ├─ Calculate final_score              │
│  │   (similarity × weight +            │
│  │    strength × weight)               │
│  └─ Sort by final_score DESC           │
└───────────────┬─────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────┐
│  Update Retrieved Memories             │
│  ├─ Increment access_count             │
│  ├─ Update last_accessed               │
│  └─ Grow stability (Spacing Effect)    │
└───────────────┬─────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────┐
│  Return Top N Results                   │
│  → Limit: 5 (default)                   │
└─────────────────────────────────────────┘
```

### Memory Lifecycle States

```
┌─────────────┐
│   CREATED   │ → Initial state after extraction
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   ACTIVE    │ → is_active=true, searchable
└──────┬──────┘
       │
       ├─→ Retrieved → Stability grows (Spacing Effect)
       │
       ├─→ Similar memory added → Reinforced/Merged
       │
       └─→ Capacity exceeded → Pruned
              │
              ▼
       ┌─────────────┐
       │ COLD STORAGE│ → is_active=false, archived
       └──────┬──────┘
              │
              ├─→ Restored → Back to ACTIVE
              │
              └─→ Retention expired → Hard delete (optional)
```

---

## Mathematical Models

### 1. Retrieval Strength (Ebbinghaus)

**Formula**: `S = R × exp(-t / H)`

**Variables**:
- `S`: Retrieval Strength (0.0 to 1.0)
- `R`: Raw Importance (0.1 to 1.0)
- `t`: Time since last access (hours)
- `H`: Stability (half-life in hours)

**Properties**:
- Exponential decay
- Never reaches zero (minimum: 0.01)
- Capped at importance value (maximum: 1.0)

### 2. Stability Growth (Spacing Effect)

**Formula**: `H_new = H_old × (1.2 + similarity + emotion × 1.5)`

**Variables**:
- `H_new`: New stability value
- `H_old`: Current stability value
- `similarity`: Query-memory similarity (0.0 to 1.0)
- `emotion`: Emotional intensity (0.0 to 1.0)

**Properties**:
- Multiplicative growth
- Minimum growth: 20% (base factor)
- Maximum growth: ~370% (high similarity + high emotion)
- Capped at `MAX_STABILITY` (10000 hours)

### 3. Initial Stability (Flashbulb)

**Formula**: `H_initial = default + (emotion × max_multiplier)`

**Variables**:
- `default`: Base stability (default: 24 hours)
- `emotion`: Emotional intensity (0.0 to 1.0)
- `max_multiplier`: Maximum boost (default: 100 hours)

**Properties**:
- Linear relationship with emotion
- Range: 24 hours (neutral) to 124 hours (high emotion)

### 4. Combined Score (Search Ranking)

**Formula**: `score = (similarity × w_sim) + (strength × w_str)`

**Variables**:
- `similarity`: Vector search similarity (0.0 to 1.0)
- `strength`: Current retrieval strength (0.0 to 1.0)
- `w_sim`: Similarity weight (default: 0.6)
- `w_str`: Strength weight (default: 0.4)

**Properties**:
- Weighted average
- Balances semantic relevance with temporal relevance
- Configurable weights for different use cases

### 5. Effective Importance (Pruning)

**Formula**: `effective_importance = importance × (1 + ln(access_count + 1))`

**Variables**:
- `importance`: Raw importance (0.1 to 1.0)
- `access_count`: Number of times retrieved

**Properties**:
- Logarithmic growth with access count
- Frequently accessed memories rank higher
- Prevents pruning of important, frequently-used memories

---

## Storage & Retrieval

### MongoDB Schema

**Memory Document**:
```javascript
{
    "_id": ObjectId("507f1f77bcf86cd799439011"),
    
    // Identity & Content
    "user_id": "user_123",
    "text": "User just got promoted to Senior Architect.",
    "embedding": [0.012, -0.04, 0.089, ...],  // 1536 dimensions
    
    // Cognitive Fields
    "importance": 0.9,              // Raw Importance (R) - 0.1 to 1.0
    "stability": 94.0,              // Stability (H) - hours until strength halves
    "emotion": 0.85,                // Emotional intensity at recording
    "access_count": 5,               // How many times retrieved
    "last_accessed": ISODate("2026-02-03T10:00:00Z"),
    "mention_count": 2,               // How many times mentioned
    
    // Organization
    "category": "biographical",      // biographical, preferences, temporal, relational
    "metadata": {
        "bucket_id": "conversation:123",
        "bucket_type": "general",
        "source": "chat",
        "session_id": "conv_xyz"
    },
    
    // Soft-Delete Fields
    "is_active": true,               // False = in cold storage
    "pruned_at": null,               // When moved to cold storage
    "pruning_reason": null,          // Why pruned
    
    // Timestamps
    "created_at": ISODate("2026-02-01T14:30:00Z"),
    "updated_at": ISODate("2026-02-03T10:00:00Z")
}
```

### Vector Search Index

**Configuration**:
```javascript
{
    "name": "{collection_name}_vector_index",
    "type": "vectorSearch",
    "definition": {
        "fields": [
            {
                "type": "vector",
                "path": "embedding",
                "numDimensions": 1536,
                "similarity": "cosine"
            },
            {
                "type": "filter",
                "path": "user_id"
            }
        ]
    }
}
```

**Index Requirements**:
- Vector field: `embedding` (1536 dimensions for OpenAI `text-embedding-3-small`)
- Filter field: `user_id` (required for user-scoped queries)
- Similarity metric: `cosine`
- Auto-created by `CognitiveMemoryService` on initialization

### Search Pipeline

**MongoDB Aggregation Pipeline**:
```javascript
[
    // Stage 1: Vector Search
    {
        "$vectorSearch": {
            "index": "memories_vector_index",
            "path": "embedding",
            "queryVector": [0.012, -0.04, ...],
            "numCandidates": 100,  // limit * 20
            "limit": 15,           // limit * 3 for decay filtering
            "filter": {
                "user_id": "user_123",
                "is_active": true
            }
        }
    },
    
    // Stage 2: Add similarity and time calculations
    {
        "$addFields": {
            "similarity": {"$meta": "vectorSearchScore"},
            "t_hours": {
                "$divide": [
                    {"$subtract": [now, {"$ifNull": ["$last_accessed", now]}]},
                    3600000
                ]
            },
            "_stability": {
                "$max": [
                    {"$ifNull": ["$stability", 24.0]},
                    0.1
                ]
            },
            "_importance": {"$ifNull": ["$importance", 0.5]}
        }
    },
    
    // Stage 3: Calculate retrieval strength
    {
        "$addFields": {
            "strength": {
                "$multiply": [
                    "$_importance",
                    {"$exp": {"$divide": [{"$multiply": ["$t_hours", -1]}, "$_stability"]}}
                ]
            }
        }
    },
    
    // Stage 4: Calculate final score
    {
        "$addFields": {
            "final_score": {
                "$add": [
                    {"$multiply": ["$similarity", 0.6]},
                    {"$multiply": ["$strength", 0.4]}
                ]
            }
        }
    },
    
    // Stage 5: Sort and limit
    {
        "$sort": {"final_score": -1}
    },
    {
        "$limit": 5
    }
]
```

**Performance**:
- Vector search: ~50-100ms (MongoDB Atlas)
- Decay calculation: Server-side (no N+1 queries)
- Total latency: ~100-150ms for typical queries

---

## Advanced Features

### 1. Reflection Service (Memory Consolidation)

**Purpose**: Periodically consolidate atomic memories into narrative summaries.

**Process**:
1. Trigger: Time-based (24h default) or count-based (50 memories)
2. Collect recent memories
3. LLM generates narrative summary
4. Store reflection in `{app_slug}_reflections` collection
5. Optionally prune low-salience memories

**Configuration**:
```json
{
    "reflection": {
        "enabled": true,
        "interval_hours": 24,
        "message_threshold": 50,
        "min_salience_to_keep": 0.4,
        "store_reflections": true
    }
}
```

**Reflection Schema**:
```javascript
{
    "_id": ObjectId("..."),
    "user_id": "user_123",
    "content": "The user is a Python developer who works at Tech Corp...",
    "type": "periodic_summary",
    "period_start": ISODate("2026-02-01T00:00:00Z"),
    "period_end": ISODate("2026-02-02T00:00:00Z"),
    "memories_consolidated": 45,
    "memory_ids": ["mem1", "mem2", ...],
    "created_at": ISODate("2026-02-02T00:00:00Z")
}
```

### 2. Memory Fusion Service (Deduplication)

**Purpose**: Intelligently merge related facts before storage.

**Process**:
1. Cluster facts by embedding similarity (Union-Find algorithm)
2. For each cluster:
   - If `similarity >= threshold`: Use LLM to synthesize
   - Fallback: Simple text merge
   - Fallback: Pass-through (no fusion)
3. Return fused facts

**Configuration**:
```json
{
    "fusion": {
        "enabled": true,
        "similarity_threshold": 0.8,
        "use_llm": true,
        "llm_model": "openai/gpt-4o"
    }
}
```

**Fusion Example**:
```python
Input:
- "User loves chocolate"
- "Chocolate is user's favorite candy"
- "User enjoys chocolate desserts"

Output:
- "User loves chocolate, which is their favorite candy, and enjoys chocolate desserts"
```

### 3. Redaction Service (Privacy)

**Purpose**: Protect sensitive data from being stored.

**Patterns**:
- SSN: `XXX-XX-XXXX`
- Credit Card: 13-16 digits
- API Keys: Common patterns
- Passwords: Assignment statements
- Bearer Tokens: `Bearer ...`
- AWS Keys: `AKIA...`

**Configuration**:
```json
{
    "redaction": {
        "enabled": true,
        "replacement": "[REDACTED]",
        "patterns": {
            "ssn": true,
            "credit_card": true,
            "api_key": true,
            "password": true
        },
        "allow_list": ["support@company.com"]
    }
}
```

**Usage**:
```python
redactor = RedactionService(config={...})
clean_text = redactor.redact("My SSN is 123-45-6789")
# Returns: "My SSN is [REDACTED]"
```

### 4. Bucket Awareness

**Purpose**: Complete memory isolation between contexts.

**Bucket Types**:
- `category:{name}:{user_id}` - Category bucket (e.g., "work", "personal")
- `session:{id}` - Session-scoped (default)
- `file:{filename}:{user_id}` - File bucket

**Search Filtering**:
```python
# Search only "work" bucket memories
memories = memory_service.search(
    query="meetings",
    user_id="user123",
    filters={"metadata.associated_bucket_id": "category:work:user123"}
)
```

**File Memory Integration**:
- File uploaded to "work" bucket
- `bucket_id` = `"file:report.pdf:user123"`
- `associated_bucket_id` = `"category:work:user123"`
- Searches find both conversation and file memories

### 5. GraphRAG Integration

**Purpose**: Build knowledge graphs from memories for multi-hop reasoning.

**Features**:
- Entity extraction (people, places, interests)
- Relationship extraction
- `$graphLookup` traversal for multi-hop queries
- Hybrid search (vector + graph)

**Example Query**:
```
"What does my brother like?"
→ Graph traversal: User → Brother → Preferences
→ Returns: "Your brother likes skiing and jazz"
```

**See**: [GRAPHRAG.md](./GRAPHRAG.md) for full documentation

---

## Performance Characteristics

### Latency Benchmarks

| Operation | Typical Latency | Notes |
|-----------|----------------|-------|
| Vector Search (5 results) | 50-100ms | MongoDB Atlas Vector Search |
| Decay-Aware Search | 100-150ms | Includes server-side decay calculation |
| Fact Extraction (3 facts) | 500-2000ms | LLM call (varies by provider) |
| Memory Injection | 50-100ms | Direct storage, no LLM |
| Conflict Detection | 300-800ms | Vector search + LLM analysis |
| Pruning (1000 memories) | 200-500ms | Sort + soft-delete batch |

### Scalability

**Memory Capacity**:
- Per-user limit: Configurable (default: 1000)
- Total storage: Limited by MongoDB cluster size
- Vector index: Supports millions of documents

**Concurrent Operations**:
- Parallel fact extraction: Semaphore limit (default: 10)
- Batch embedding: Chunked (default: 100 per batch)
- Async operations: Non-blocking fact extraction

**Optimization Strategies**:
1. **Server-Side Decay**: Use MongoDB aggregation pipeline
2. **Batch Operations**: Group multiple facts in single `add()` call
3. **Metadata Filtering**: Filter in query, not Python
4. **Appropriate Limits**: Don't fetch more than needed
5. **Cache Embeddings**: Reuse embeddings for similar queries

### Resource Usage

**Memory (RAM)**:
- Per-memory document: ~2-5 KB (with embedding)
- 1000 memories: ~2-5 MB per user
- Vector index: In-memory (MongoDB Atlas managed)

**Storage (Disk)**:
- Per-memory document: ~5-10 KB (MongoDB storage overhead)
- 1000 memories: ~5-10 MB per user
- Cold storage: Same size (soft-delete, not deletion)

**Network**:
- Embedding generation: ~1-2 KB per fact
- Vector search: ~10-50 KB per query (depends on dimensions)
- LLM calls: Varies by provider and model

---

## Security & Privacy

### Data Isolation

**User Scoping**:
- All queries filtered by `user_id`
- Vector search includes `user_id` in filter
- No cross-user memory leakage

**Bucket Isolation**:
- Memories scoped by `bucket_id` and `associated_bucket_id`
- Complete isolation between contexts
- File memories linked via `associated_bucket_id`

### Encryption

**CSFLE (Client-Side Field Level Encryption)**:
- Encrypts `content` and `text` fields
- Metadata fields remain unencrypted (for querying)
- Supports AWS KMS, Azure Key Vault, local keys

**Configuration**:
```json
{
    "memory_config": {
        "encrypted": true,
        "encryption": {
            "enabled": true,
            "kms_provider": "local"
        }
    }
}
```

### Redaction

**Automatic PII Protection**:
- SSN, credit cards, passwords redacted before storage
- Configurable patterns
- Allow-list for exceptions

**See**: [Redaction Service](#3-redaction-service-privacy)

### GDPR Compliance

**Data Retention**:
- Cold storage retention: Configurable (default: 365 days)
- Soft-delete: Audit trail for compliance
- Hard-delete: Optional, user-initiated

**Right to Erasure**:
```python
# Soft-delete (compliance-friendly)
memory_service.delete_all(user_id="user123", hard_delete=False)

# Hard-delete (permanent removal)
memory_service.delete_all(user_id="user123", hard_delete=True)
```

**See**: [GDPR_COMPLIANCE.md](./GDPR_COMPLIANCE.md) for full documentation

---

## Integration Patterns

### Pattern 1: Basic Chat Application

```python
from mdb_engine.memory import CognitiveEngine, OpenAIProvider
from openai import OpenAI

# Initialize
llm_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
llm_provider = OpenAIProvider(llm_client)

engine = CognitiveEngine(
    app_slug="my_chat_app",
    memory_service=memory_service,
    chat_history_collection=chat_history_collection,
    llm_provider=llm_provider,
    stm_context_limit=10,
    ltm_search_limit=5,
)

# Chat endpoint
@app.post("/chat")
async def chat(user_id: str, message: str, session_id: str):
    result = await engine.chat(
        user_id=user_id,
        session_id=session_id,
        user_query=message,
        extract_facts=True,
    )
    
    return {
        "response": result["response"],
        "memories_used": len(result["ltm_memories"]),
    }
```

### Pattern 2: Customer Support Bot

```python
# On ticket creation - inject account context
async def on_new_ticket(customer_id: str, account_data: dict):
    memory_service.inject(
        memory=f"Customer is on {account_data['plan']} plan",
        user_id=customer_id,
        importance=0.9,
        metadata={"source": "crm", "type": "account"}
    )
    
    if account_data.get("vip"):
        memory_service.inject(
            memory="Customer is a VIP account - prioritize requests",
            user_id=customer_id,
            importance=0.99,
            emotion=0.8,
            metadata={"source": "crm", "type": "status"}
        )

# During support conversation
@app.post("/support/chat")
async def support_chat(customer_id: str, message: str):
    # Check for conflicts
    conflict = await memory_service.detect_knowledge_conflict(
        customer_id, message
    )
    if conflict:
        return {"response": f"Wait, you previously said: {conflict}"}
    
    # Normal processing
    result = await cognitive_engine.chat(...)
    return result
```

### Pattern 3: Healthcare Companion (GDPR)

```python
# Configuration
manifest = {
    "memory_config": {
        "cognitive": {
            "decay": {"default_stability_hours": 8760},  # 1 year
            "pruning": {
                "max_capacity": 10000,
                "strategy": "soft_delete"  # Never hard delete
            },
            "cold_storage": {
                "enabled": true,
                "retention_days": 2555  # 7 years (legal requirement)
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

# Critical health info
memory_service.inject(
    memory="Patient is allergic to penicillin",
    user_id=patient_id,
    importance=0.99,
    emotion=0.95,  # Maximum stability
    metadata={"type": "allergy", "severity": "critical"}
)
```

### Pattern 4: Multi-App SSO

```python
# Shared memory across apps
memory_service.add(
    messages=[{"role": "user", "content": "I prefer dark mode"}],
    user_id=user_id,
    metadata={"app_slug": "auth-hub", "shared": True}
)

# App-specific memory
memory_service.add(
    messages=[{"role": "user", "content": "Project deadline is Friday"}],
    user_id=user_id,
    metadata={"app_slug": "project-manager", "bucket_id": "work"}
)
```

---

## Limitations & Trade-offs

### 1. LLM Dependency

**Limitation**: Fact extraction, emotion detection, and conflict detection require LLM calls.

**Impact**:
- Latency: 500-2000ms per extraction
- Cost: Per-token pricing
- Reliability: Depends on LLM provider availability

**Mitigation**:
- Async extraction (non-blocking)
- Batch processing
- Fallback to direct injection
- Caching for similar inputs

### 2. Vector Search Limitations

**Limitation**: Semantic similarity may not capture all relationships.

**Impact**:
- False positives: Unrelated memories with high similarity
- False negatives: Related memories with low similarity
- Language bias: Embeddings trained on specific corpora

**Mitigation**:
- Decay-aware ranking (temporal relevance)
- Metadata filtering
- Hybrid search (vector + graph)
- Manual memory injection for critical facts

### 3. Capacity Constraints

**Limitation**: Fixed `max_capacity` per user.

**Impact**:
- Pruning may remove important memories
- Cold storage grows over time
- No automatic importance re-assessment

**Mitigation**:
- Configurable capacity
- Reflection service (consolidation)
- Manual importance adjustment
- Analytics to monitor pruning patterns

### 4. Decay Model Assumptions

**Limitation**: Ebbinghaus model assumes exponential decay.

**Impact**:
- May not match all use cases
- Fixed half-life may be too aggressive/conservative
- No context-aware decay

**Mitigation**:
- Configurable stability
- Emotion-based initial stability
- Spacing effect (rehearsal)
- Manual stability adjustment

### 5. Conflict Detection Accuracy

**Limitation**: LLM-based conflict detection may have false positives/negatives.

**Impact**:
- May flag non-contradictions
- May miss subtle contradictions
- Depends on LLM reasoning ability

**Mitigation**:
- Configurable similarity threshold
- Manual review of conflicts
- User confirmation for important conflicts
- Fine-tuned prompts

---

## Future Enhancements

### Planned Features

1. **Adaptive Decay**: Context-aware decay rates based on memory category
2. **Memory Clustering**: Automatic grouping of related memories
3. **Temporal Reasoning**: Better handling of time-bound information
4. **Multi-Modal Memory**: Support for images, audio, and structured data
5. **Federated Memory**: Cross-user memory sharing (with privacy controls)
6. **Memory Versioning**: Track changes to memories over time
7. **Confidence Scoring**: LLM confidence in fact extraction
8. **Memory Templates**: Pre-defined memory structures for common use cases

### Research Areas

1. **Neural Memory Networks**: Learn optimal decay rates from usage patterns
2. **Causal Reasoning**: Understand cause-effect relationships in memories
3. **Memory Compression**: More efficient storage of similar memories
4. **Transfer Learning**: Apply memories across similar users (privacy-preserving)
5. **Memory Explainability**: Explain why certain memories were retrieved

---

## Conclusion

MDB-Engine's Memory System represents a significant advancement in AI memory architecture, combining:

- **Biological Inspiration**: Ebbinghaus decay, spacing effect, flashbulb memory
- **Practical Engineering**: MongoDB Atlas Vector Search, efficient pipelines
- **Production Readiness**: Conflict detection, privacy protection, GDPR compliance
- **Extensibility**: Abstract base class, modular services, provider abstraction

The system transforms AI applications from stateless responders into intelligent companions that remember, learn, and adapt over time. By implementing cognitive memory dynamics, it addresses the fundamental limitations of traditional RAG systems while maintaining scalability and performance.

**Key Takeaways**:
1. **Two-tier architecture** (STM + LTM) provides optimal context retrieval
2. **Decay-aware ranking** ensures temporal relevance
3. **Conflict detection** prevents "digital dementia"
4. **Soft-delete pruning** maintains audit trail and recovery capability
5. **Bucket awareness** enables complete memory isolation

The memory system is production-ready, well-documented, and continuously evolving based on real-world usage patterns and research advances in cognitive science and AI.

---

## References

- [Memory Service Documentation](./MEMORY_SERVICE.md)
- [Memory Deep Dive Guide](./MEMORY_DEEP_DIVE.md)
- [Cognitive Memory Technical Details](./COGNITIVE_MEMORY.md)
- [Cognitive Architecture Overview](./COGNITIVE_ARCHITECTURE.md)
- [GDPR Compliance Guide](./GDPR_COMPLIANCE.md)
- [GraphRAG Documentation](./GRAPHRAG.md)

---

**Document Version**: 1.0  
**Last Updated**: February 5, 2026  
**Maintained By**: MDB-Engine Team
