# Memory Deep Dive: Building AI That Remembers

> **A practical guide to implementing intelligent memory in your AI applications with MDB-Engine**

## Table of Contents

1. [The Memory Problem](#the-memory-problem)
2. [Memory Architecture Overview](#memory-architecture-overview)
3. [Getting Started: Your First Memory-Enabled App](#getting-started-your-first-memory-enabled-app)
4. [The Three Memory Operations](#the-three-memory-operations)
5. [When to Use What: Decision Guide](#when-to-use-what-decision-guide)
6. [Cognitive Features Explained](#cognitive-features-explained)
7. [Real-World Patterns](#real-world-patterns)
8. [Performance Optimization](#performance-optimization)
9. [Common Mistakes and How to Avoid Them](#common-mistakes-and-how-to-avoid-them)
10. [Production Checklist](#production-checklist)

---

## The Memory Problem

### Why Your AI Needs Memory

Every conversation with a standard LLM starts from scratch. The model doesn't know:
- That you prefer Python over JavaScript
- That you're allergic to shellfish
- That you mentioned your daughter's birthday is next week
- That you hate bullet points in responses

This creates a fundamental disconnect. Humans don't repeat themselves every conversation. We expect our AI assistant to *remember*.

### The RAG Trap

Most developers solve this with basic RAG (Retrieval-Augmented Generation):

```python
# The naive approach
memories = vector_search(query, user_id)
context = "\n".join(memories)
response = llm.generate(f"Context: {context}\n\nUser: {query}")
```

This works... until it doesn't:

1. **Context Pollution**: Old, irrelevant memories flood the context
2. **Contradictions**: "User loves seafood" alongside "User is allergic to shellfish"
3. **Memory Bloat**: Thousands of memories with no pruning strategy
4. **No Temporal Awareness**: A preference from 2 years ago treated same as yesterday's

### The MDB-Engine Solution

MDB-Engine implements a **Cognitive Memory Architecture** that mimics how human memory actually works:

```
                    ┌─────────────────────────────────────┐
                    │         Human Memory Model          │
                    ├─────────────────────────────────────┤
                    │  Hippocampus → STM (Short-Term)     │
                    │  Cortex → LTM (Long-Term)           │
                    │  Amygdala → Emotion (Flashbulb)     │
                    │  Prefrontal → Conflict Resolution   │
                    └─────────────────────────────────────┘
                                    ↓
                    ┌─────────────────────────────────────┐
                    │      MDB-Engine Memory Model        │
                    ├─────────────────────────────────────┤
                    │  ChatHistoryService → STM           │
                    │  CognitiveMemoryService → LTM       │
                    │  Emotion Extraction → Stability     │
                    │  Conflict Detection → Integrity     │
                    └─────────────────────────────────────┘
```

---

## Memory Architecture Overview

### The Two-Tier System

MDB-Engine uses a two-tier memory system:

#### Tier 1: Short-Term Memory (STM)
- **What it is**: Recent conversation history
- **Storage**: `chat_history` collection
- **Scope**: Per-session (conversation)
- **Lifetime**: Session duration
- **Purpose**: Maintain conversational context

#### Tier 2: Long-Term Memory (LTM)
- **What it is**: Extracted facts and knowledge
- **Storage**: `{app_slug}_memories` collection
- **Scope**: Per-user (across all sessions)
- **Lifetime**: Persistent (with decay)
- **Purpose**: Remember user permanently

### How They Work Together

```
User: "I'm heading to Tokyo next month for my anniversary"
                │
                ▼
┌───────────────────────────────────────────────────────────┐
│                    STM (Session)                          │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ [user] I'm heading to Tokyo next month...           │  │
│  │ [assistant] That sounds wonderful! Is this your...  │  │
│  │ [user] Yes, 10 years!                               │  │
│  └─────────────────────────────────────────────────────┘  │
│                         ↓                                 │
│              Fact Extraction (LLM)                        │
│                         ↓                                 │
└───────────────────────────────────────────────────────────┘
                          │
                          ▼
┌───────────────────────────────────────────────────────────┐
│                    LTM (Permanent)                        │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ • User is planning a trip to Tokyo                  │  │
│  │   [category: temporal, emotion: 0.7, stability: 94] │  │
│  │                                                     │  │
│  │ • User's anniversary is next month                  │  │
│  │   [category: biographical, emotion: 0.8, stability: │  │
│  │    104]                                             │  │
│  │                                                     │  │
│  │ • User has been married for 10 years               │  │
│  │   [category: biographical, emotion: 0.75, stability:│  │
│  │    99]                                             │  │
│  └─────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────┘
```

### The Memory Document

Every memory in LTM has this structure:

```python
{
    # Identity
    "_id": ObjectId("..."),
    "user_id": "user_abc123",
    "text": "User has been married for 10 years",
    "embedding": [0.012, -0.04, ...],  # 1536 dimensions
    
    # Cognitive Fields
    "importance": 0.75,      # Raw importance (0.1-1.0)
    "stability": 99.0,       # Hours until strength halves
    "emotion": 0.75,         # Emotional intensity
    "access_count": 3,       # Times retrieved
    "last_accessed": ISODate("2026-02-03T10:00:00Z"),
    
    # Soft-Delete (Cold Storage)
    "is_active": True,
    "pruned_at": null,
    "pruning_reason": null,
    
    # Organization
    "category": "biographical",
    "metadata": {
        "session_id": "conv_xyz",
        "source": "chat"
    },
    
    # Timestamps
    "created_at": ISODate("2026-02-01T14:30:00Z"),
    "updated_at": ISODate("2026-02-03T10:00:00Z")
}
```

---

## Getting Started: Your First Memory-Enabled App

### Step 1: Configure Your Manifest

```json
{
  "slug": "my-ai-app",
  "name": "My AI Assistant",
  
  "memory_config": {
    "enabled": true,
    "provider": "cognitive",
    "collection_name": "user_memories",
    "embedding_model": "text-embedding-3-small",
    "memory_llm_model": "openai/gpt-4o",
    "infer": true,
    "enable_cognitive": true,
    "max_depth": 500,
    
    "cognitive": {
      "enabled": true,
      "decay": {
        "enabled": true,
        "default_stability_hours": 48
      },
      "emotion": {
        "enabled": true
      },
      "conflict_resolution": {
        "enabled": true
      },
      "pruning": {
        "max_capacity": 500,
        "strategy": "soft_delete"
      },
      "cold_storage": {
        "enabled": true
      }
    },
    
    "categories": {
      "enabled": true,
      "custom_categories": ["work", "health", "finance"]
    },
    
    "redaction": {
      "enabled": true,
      "patterns": {
        "ssn": true,
        "credit_card": true,
        "password": true
      }
    }
  }
}
```

### Step 2: Use CognitiveEngine (Recommended)

The **CognitiveEngine** handles everything for you:

```python
from mdb_engine.memory.orchestrator import CognitiveEngine

# Initialize once at startup
cognitive_engine = CognitiveEngine(
    app_slug="my-ai-app",
    memory_service=memory_service,
    chat_history_collection=chat_history_collection,
    stm_context_limit=10,      # Last 10 messages
    ltm_search_limit=5,        # Top 5 memories
    auto_summarize_threshold=20,
    llm_provider=llm_provider,
)

# Use in your chat endpoint
@app.post("/chat")
async def chat(user_id: str, message: str):
    result = await cognitive_engine.chat(
        user_id=user_id,
        session_id=session_id,
        user_query=message,
        system_prompt="You are a helpful assistant.",
        extract_facts=True,  # Auto-extract to LTM
    )
    
    return {
        "response": result["response"],
        "memories_used": len(result["ltm_memories"]),
        "new_facts_stored": len(result["memories_stored"]),
    }
```

**What CognitiveEngine does automatically:**
1. Saves user message to STM
2. Searches LTM for relevant memories
3. Retrieves recent STM context
4. Builds prompt with context
5. Generates LLM response
6. Saves response to STM
7. Extracts facts to LTM (with emotion)
8. Handles pruning when capacity exceeded

### Step 3: Direct Memory Service Access

For more control, use the memory service directly:

```python
from mdb_engine.dependencies import get_memory_service

@app.post("/remember")
async def add_memory(user_id: str, memory: str):
    memory_service = engine.get_memory_service("my-ai-app")
    
    # Add with LLM extraction
    result = memory_service.add(
        messages=[{"role": "user", "content": memory}],
        user_id=user_id,
    )
    
    return {"stored": len(result)}

@app.get("/recall")
async def search_memory(user_id: str, query: str):
    memory_service = engine.get_memory_service("my-ai-app")
    
    # Search with decay-aware ranking
    memories = memory_service.search(
        query=query,
        user_id=user_id,
        limit=5,
        use_decay=True,
    )
    
    return {"memories": memories}
```

---

## The Three Memory Operations

### 1. `add()` - Intelligent Extraction

**Use when**: Processing user conversations

```python
# From conversation
memories = memory_service.add(
    messages=[
        {"role": "user", "content": "I'm John, I work at Google, and I love hiking"},
        {"role": "assistant", "content": "Nice to meet you, John!"}
    ],
    user_id="user123",
)

# Result: 3 memories extracted
# - "User's name is John" [biographical, emotion: 0.3]
# - "User works at Google" [biographical, emotion: 0.5]
# - "User loves hiking" [preferences, emotion: 0.4]
```

**What happens under the hood:**
1. LLM extracts atomic facts from text
2. Each fact gets category + emotion
3. Embeddings generated for each fact
4. Initial stability calculated from emotion
5. **Duplicate detection**: Memories with similarity ≥ `duplicate_threshold` (default: 0.90) are detected as duplicates and the existing memory is boosted instead of creating a new one
6. Similar memories (between `merge_threshold_high` and `duplicate_threshold`) are reinforced
7. Moderately similar memories (between `merge_threshold_low` and `merge_threshold_high`) are merged
8. New memories are created only if no similar memories are found

### 2. `inject()` - Direct Storage

**Use when**: You know exactly what to store

```python
# System knowledge
memory_service.inject(
    memory="User prefers concise responses",
    user_id="user123",
    importance=0.9,
    emotion=0.5,
    metadata={"source": "onboarding", "type": "preference"}
)

# Critical health info
memory_service.inject(
    memory="User is allergic to penicillin",
    user_id="user123",
    importance=0.99,
    emotion=0.95,  # High emotion = high stability
    metadata={"source": "health_form", "critical": True}
)
```

**When to use `inject()` vs `add()`:**

| Use `inject()` when... | Use `add()` when... |
|------------------------|---------------------|
| You have structured data | Processing raw conversation |
| Importing from external source | User is chatting naturally |
| Setting system preferences | You want LLM to filter noise |
| Critical info (health, legal) | You want emotion detection |
| Exact wording matters | Paraphrasing is acceptable |

### 3. `search()` - Intelligent Retrieval

**Use when**: Building context for LLM

```python
# Basic search
memories = memory_service.search(
    query="What does the user like to eat?",
    user_id="user123",
    limit=5,
)

# Decay-aware search (recommended)
memories = memory_service.search(
    query="What does the user like to eat?",
    user_id="user123",
    limit=5,
    use_decay=True,  # Recent memories rank higher
)

# With metadata filter
memories = memory_service.search(
    query="dietary preferences",
    user_id="user123",
    limit=5,
    filters={"metadata.category": "preferences"}
)
```

**Understanding search results:**

```python
{
    "id": "507f...",
    "memory": "User loves sushi",
    "score": 0.72,           # Combined ranking score
    "similarity": 0.85,      # Vector similarity
    "strength": 0.85,        # Current retrieval strength (decay)
    "stability": 124.5,      # Current half-life (hours)
    "emotion": 0.6,
    "importance": 0.7,
    "access_count": 5,
    "category": "preferences",
    "last_accessed": "2026-02-03T10:00:00Z"
}
```

---

## When to Use What: Decision Guide

### Memory Strategy Decision Tree

```
Need to store information?
│
├─► Is it from user conversation?
│   │
│   ├─► Yes → Use add() with infer=True
│   │         LLM will extract facts, filter noise,
│   │         detect emotion
│   │
│   └─► No → Is it structured/critical data?
│       │
│       ├─► Yes → Use inject()
│       │         Set importance and emotion manually
│       │
│       └─► No → Use add() with infer=False
│                 Store as-is without extraction


Need to retrieve information?
│
├─► Building LLM context?
│   │
│   └─► Yes → Use search() with use_decay=True
│             Recent memories rank higher
│             Spacing effect strengthens retrieved memories
│
├─► Displaying to user?
│   │
│   └─► Yes → Use get_all() with limit
│             Returns all memories chronologically
│
└─► Need specific memory?
    │
    └─► Yes → Use get(memory_id)
```

### Configuration Decision Guide

#### Stability (Half-Life)

| Use Case | Stability | Rationale |
|----------|-----------|-----------|
| Session-based assistant | 4-8 hours | Memories from session are relevant |
| Daily companion | 24-48 hours | Memories decay within days |
| Personal assistant | 72-168 hours | Week-scale persistence |
| Knowledge base | 720+ hours | Month-scale, nearly permanent |

```json
"decay": {
  "default_stability_hours": 48  // Choose based on use case
}
```

#### Max Capacity

| Use Case | Capacity | Rationale |
|----------|----------|-----------|
| Quick demo | 50-100 | Fast, low storage |
| Personal app | 500-1000 | Good balance |
| Enterprise | 5000-10000 | Large scale |
| Compliance-heavy | 10000+ | Retain everything |

```json
"pruning": {
  "max_capacity": 500  // Weak memories pruned when exceeded
}
```

#### Emotion Threshold

| Threshold | Effect |
|-----------|--------|
| 0.5 | Many memories get stability boost |
| 0.7 (default) | Only significant events |
| 0.9 | Only major life events |

```json
"emotion": {
  "flashbulb_threshold": 0.7
}
```

---

## Cognitive Features Explained

### 1. Memory Decay (Ebbinghaus Curve)

**The Formula:**
```
Strength = Importance × exp(-time / stability)
```

**Visual:**
```
Strength
1.0 │╲
    │ ╲
0.8 │  ╲
    │   ╲
0.6 │    ╲
    │     ╲____
0.4 │          ╲____
    │               ╲____
0.2 │                    ╲____
    │                         ╲____
0.0 └─────────────────────────────────► Time (hours)
    0   24   48   72   96   120  144
```

**Why it matters:**
- Old memories naturally fade
- Context stays relevant
- No manual cleanup needed

**Example:**
```python
# A memory with importance=0.8, stability=24 hours:
# At 0 hours:  strength = 0.80
# At 24 hours: strength = 0.29
# At 48 hours: strength = 0.11

# A high-emotion memory with stability=120 hours:
# At 0 hours:  strength = 0.80
# At 24 hours: strength = 0.65
# At 48 hours: strength = 0.53
```

### 2. Spacing Effect (Rehearsal)

Every time you retrieve a memory, it gets stronger:

```python
new_stability = old_stability × (1.2 + similarity + emotion × 1.5)
```

**Why it matters:**
- Frequently used memories become permanent
- One-time mentions fade naturally
- Mimics how human memory works

**Example:**
```python
# Initial: stability = 24 hours
# Retrieved once (similarity=0.8): stability = 24 × (1.2 + 0.8 + 0) = 48 hours
# Retrieved again (similarity=0.9): stability = 48 × (1.2 + 0.9 + 0) = 100.8 hours
# After 5 retrievals: effectively permanent
```

### 3. Flashbulb Memory (Emotion)

High-emotion events get exceptional persistence:

```python
initial_stability = default + (emotion × multiplier)
# Example: 24 + (0.9 × 100) = 114 hours
```

**Why it matters:**
- "I got promoted!" persists longer than "I had coffee"
- Critical life events remembered with clarity
- Mimics human emotional memory

**Example inputs and emotion scores:**
```python
"I usually drink coffee" → emotion: 0.2
"I prefer dark mode" → emotion: 0.3
"I got a new job!" → emotion: 0.7
"My grandmother passed away" → emotion: 0.95
"I just had a baby!" → emotion: 0.98
```

### 4. Conflict Resolution

Prevents contradictory knowledge:

```python
conflict = await memory_service.detect_knowledge_conflict(
    user_id="user123",
    new_fact="User is allergic to shellfish"
)

if conflict:
    # "This conflicts with: 'User loves seafood'"
    # Handle: ask user, update old memory, or flag
```

**Why it matters:**
- Prevents "digital dementia"
- Maintains logical consistency
- Catches data entry errors

### 5. Duplicate Detection and Reinforcement

Prevents storing semantically identical memories with different wording:

```python
# User says: "My sister Emily and brother-in-law David enjoy skiing and jazz"
# Later says: "Emily and David both like skiing and listening to jazz"

# Without duplicate detection: Creates 2 separate memories
# With duplicate detection: Boosts existing memory, no duplicate created
```

**How it works:**

1. **Duplicate Detection** (similarity ≥ `duplicate_threshold`, default: 0.90):
   - Memories that are semantically identical but phrased differently are detected
   - Existing memory is boosted (importance increased by `reinforcement_factor`)
   - `mention_count` and `access_count` are incremented
   - No new memory is created

2. **Reinforcement** (similarity between `merge_threshold_high` and `duplicate_threshold`):
   - Very similar memories are reinforced
   - Text may be merged if new version contains more specific information
   - Importance is boosted

3. **Merging** (similarity between `merge_threshold_low` and `merge_threshold_high`):
   - Moderately similar memories are merged into one
   - Preserves all unique information from both

**Configuration:**

```json
{
  "memory_config": {
    "duplicate_threshold": 0.90,      // Threshold for duplicate detection
    "merge_threshold_high": 0.85,     // Upper bound for reinforcement
    "merge_threshold_low": 0.70,      // Lower bound for merging
    "reinforcement_factor": 1.1        // Importance boost when reinforced
  }
}
```

**Why it matters:**
- Prevents memory bloat from repetitive information
- Ensures important facts are reinforced, not duplicated
- Maintains clean, useful memory database
- Mimics how humans reinforce memories through repetition

### 6. Soft-Delete Pruning

When capacity is exceeded, weak memories move to cold storage:

```python
# What happens:
{
    "text": "User mentioned liking vanilla",
    "is_active": False,           # Moved to cold storage
    "pruned_at": ISODate(...),    # When it was pruned
    "pruning_reason": "capacity_limit_reached"
}
```

**Why soft-delete:**
- Paper trail for auditing
- Analytics on what users "forget"
- Recovery if needed
- GDPR/compliance friendly

---

## Real-World Patterns

### Pattern 1: The Personal Assistant

```python
# manifest.json
{
  "memory_config": {
    "cognitive": {
      "decay": {"default_stability_hours": 72},  # 3-day default
      "emotion": {"enabled": true},
      "pruning": {"max_capacity": 1000}
    },
    "categories": {
      "custom_categories": ["work", "family", "health", "hobbies"]
    }
  }
}

# Usage
@app.post("/chat")
async def personal_chat(user_id: str, message: str):
    # Check conflicts for important statements
    if contains_fact_statement(message):
        conflict = await memory_service.detect_knowledge_conflict(
            user_id, extract_fact(message)
        )
        if conflict:
            return {"response": f"Wait, you previously said: {conflict}. Has that changed?"}
    
    # Normal processing with CognitiveEngine
    result = await cognitive_engine.chat(...)
    return result
```

### Pattern 2: Customer Support Bot

```python
# manifest.json
{
  "memory_config": {
    "cognitive": {
      "decay": {"default_stability_hours": 168},  # Week retention
      "conflict_resolution": {"enabled": true},
      "pruning": {"max_capacity": 200}  # Per customer
    }
  }
}

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
            memory="Customer is a VIP account - prioritize their requests",
            user_id=customer_id,
            importance=0.99,
            emotion=0.8,  # High stability
            metadata={"source": "crm", "type": "status"}
        )
```

### Pattern 3: Healthcare Companion (GDPR)

```python
# manifest.json - Maximum retention, no hard deletes
{
  "memory_config": {
    "cognitive": {
      "decay": {"default_stability_hours": 8760},  # 1 year default
      "pruning": {
        "max_capacity": 10000,
        "strategy": "soft_delete"  # NEVER hard delete
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

# Critical health data - maximum persistence
async def record_allergy(patient_id: str, allergy: str):
    memory_service.inject(
        memory=f"CRITICAL: Patient is allergic to {allergy}",
        user_id=patient_id,
        importance=0.99,
        emotion=0.99,  # Maximum stability
        metadata={
            "type": "allergy",
            "severity": "critical",
            "recorded_by": "medical_staff",
            "legal_retention": True
        }
    )
```

### Pattern 4: Learning App

```python
# Track what user has learned
async def after_lesson(user_id: str, lesson_id: str, content: str):
    # Store learned concepts
    memory_service.add(
        messages=[{"role": "system", "content": f"User completed lesson on: {content}"}],
        user_id=user_id,
        metadata={"lesson_id": lesson_id, "type": "learned"}
    )

# Use spaced repetition for review
async def get_review_items(user_id: str):
    # Find memories with low strength (need review)
    analytics = memory_service.get_memory_analytics(user_id)
    
    all_memories = memory_service.get_all(user_id, limit=1000)
    
    # Find weak memories that need reinforcement
    needs_review = [
        m for m in all_memories
        if m.get("metadata", {}).get("type") == "learned"
        and CognitiveMath.get_current_strength(m) < 0.5
    ]
    
    return needs_review
```

### Pattern 5: Multi-User Collaboration

```python
# Each user has their own memory space
# Shared knowledge injected to all team members

async def share_knowledge(team_id: str, knowledge: str, shared_by: str):
    team_members = await get_team_members(team_id)
    
    for member_id in team_members:
        memory_service.inject(
            memory=f"Team knowledge: {knowledge}",
            user_id=member_id,
            importance=0.8,
            metadata={
                "type": "team_knowledge",
                "team_id": team_id,
                "shared_by": shared_by
            }
        )
```

---

## Performance Optimization

### 1. Use Server-Side Decay Pipeline

Enable for best performance:

```json
"decay": {
  "use_server_side_pipeline": true  // MongoDB handles decay math
}
```

This moves the exponential calculation to MongoDB's aggregation pipeline using `$exp`, avoiding N+1 queries.

### 2. Appropriate Limits

```python
# Don't fetch more than needed
memories = memory_service.search(query, user_id, limit=5)  # Not limit=100

# For display, paginate
memories = memory_service.get_all(user_id, limit=20)  # Not all 1000
```

### 3. Use Metadata Filters

```python
# Faster: filter in query
memories = memory_service.search(
    query="preferences",
    user_id=user_id,
    filters={"metadata.category": "preferences"}
)

# Slower: filter in Python
memories = memory_service.search(query="preferences", user_id=user_id)
memories = [m for m in memories if m["metadata"].get("category") == "preferences"]
```

### 4. Batch Operations

```python
# Good: single add() call with multiple messages
memory_service.add(
    messages=[
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "..."},
        {"role": "user", "content": "..."},
    ],
    user_id=user_id,
)

# Bad: multiple add() calls
for msg in messages:
    memory_service.add(messages=[msg], user_id=user_id)
```

### 5. Monitor Memory Health

```python
# Periodic health check
analytics = memory_service.get_memory_analytics(user_id)

if analytics["capacity_used"] > 0.9:
    logger.warning(f"User {user_id} near capacity")

if analytics["average_strength"] < 0.3:
    logger.info(f"User {user_id} has mostly weak memories - may need engagement")
```

---

## Common Mistakes and How to Avoid Them

### Mistake 1: Storing Everything

```python
# ❌ Bad: Store every message
memory_service.add(
    messages=[{"role": "user", "content": "Hello!"}],
    user_id=user_id,
)
# This creates noise and wastes capacity

# ✅ Good: Let inference filter
# infer=True (default) will return empty for trivial messages
```

### Mistake 2: Ignoring Conflicts

```python
# ❌ Bad: Blindly store
memory_service.add(messages=[...], user_id=user_id)

# ✅ Good: Check conflicts for important facts
if is_factual_statement(message):
    conflict = await memory_service.detect_knowledge_conflict(user_id, message)
    if conflict:
        # Handle the conflict
```

### Mistake 3: Wrong Stability for Use Case

```python
# ❌ Bad: 24h stability for a knowledge base
"decay": {"default_stability_hours": 24}
# Knowledge decays too fast

# ✅ Good: Match stability to use case
# Knowledge base: 720 hours (month)
# Personal assistant: 72 hours (3 days)
# Session helper: 8 hours
```

### Mistake 4: Hard-Deleting in Production

```python
# ❌ Bad: Hard delete for capacity
"pruning": {"strategy": "hard_delete"}
# No recovery, no audit trail

# ✅ Good: Soft delete with cold storage
"pruning": {"strategy": "soft_delete"}
"cold_storage": {"enabled": true, "retention_days": 365}
```

### Mistake 5: Not Using Categories

```python
# ❌ Bad: Everything is "general"
# Hard to filter, hard to analyze

# ✅ Good: Enable categories
"categories": {
  "enabled": true,
  "custom_categories": ["work", "health", "travel"]
}
# Then filter: filters={"metadata.category": "work"}
```

### Mistake 6: Forgetting Redaction

```python
# ❌ Bad: No redaction
# "User's SSN is 123-45-6789" → stored in plain text

# ✅ Good: Enable redaction
"redaction": {
  "enabled": true,
  "patterns": {"ssn": true, "credit_card": true, "password": true}
}
# "User's SSN is 123-45-6789" → "User's SSN is [REDACTED]"
```

---

## Production Checklist

### Before Launch

- [ ] **Stability configured** for your use case
- [ ] **Max capacity set** appropriately
- [ ] **Redaction enabled** for sensitive data
- [ ] **Conflict resolution** enabled
- [ ] **Soft-delete** (not hard-delete) for pruning
- [ ] **Cold storage** enabled with appropriate retention
- [ ] **Categories** defined for your domain
- [ ] **Vector index** created (automatic via MDB-Engine)
- [ ] **Environment variables** set (LLM API keys, MongoDB URI)

### Monitoring

```python
@app.get("/admin/memory-health")
async def memory_health():
    # Per-user analytics
    users = await get_active_users()
    
    health_report = []
    for user_id in users:
        analytics = memory_service.get_memory_analytics(user_id)
        health_report.append({
            "user_id": user_id,
            "active": analytics["active_memories"],
            "cold": analytics["cold_storage_memories"],
            "capacity": analytics["capacity_used"],
            "avg_strength": analytics["average_strength"],
        })
    
    return {"users": health_report}
```

### Alerts to Configure

1. **Capacity Warning**: User > 80% of max_capacity
2. **Weak Memories**: Average strength < 0.3 (engagement issue)
3. **Cold Storage Growth**: Unusual pruning patterns
4. **Conflict Rate**: High conflict detection rate (data quality issue)

---

## Summary

MDB-Engine's memory system gives your AI human-like memory capabilities:

| Feature | Benefit |
|---------|---------|
| **Decay** | Old memories fade naturally |
| **Spacing Effect** | Used memories become permanent |
| **Flashbulb** | Important events persist |
| **Conflict Resolution** | No contradictions |
| **Soft-Delete** | Audit trail + recovery |
| **Categories** | Organized knowledge |
| **Redaction** | Privacy protection |

The key is matching configuration to your use case:

- **Session helper**: Low stability, low capacity
- **Personal assistant**: Medium stability, medium capacity
- **Knowledge base**: High stability, high capacity
- **Compliance**: Maximum retention, soft-delete only

Start with the defaults, monitor your analytics, and tune from there. Your AI will thank you (and remember to do so).
