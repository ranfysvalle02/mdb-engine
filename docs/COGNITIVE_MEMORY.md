# Cognitive Memory Service

## Overview

The **CognitiveMemoryService** extends `CustomMemoryService` with advanced cognitive memory management features inspired by human memory processes. It implements importance scoring, reinforcement, decay, merging, and pruning to create a sophisticated memory system.

## New Features (v2.0)

- **Redaction Layer**: Configurable privacy protection for sensitive data (SSN, credit cards, passwords, API keys)
- **Memory Categories**: Automatic categorization of memories (biographical, preferences, temporal, relational)
- **Reflection Service**: Periodic memory consolidation to prevent bloat
- **GraphRAG**: Full knowledge graph with `$graphLookup` traversal for multi-hop reasoning (see [GRAPHRAG.md](GRAPHRAG.md))
- **Deduplication**: "Last seen" pattern prevents duplicate memories

## Key Features

### 1. Importance Assessment

Memories are assessed for importance using LLM evaluation (0.1-1.0 scale).

**Factors considered:**
- Uniqueness of information
- Actionability
- Personal significance
- Key facts or decisions

```python
from mdb_engine.memory import CognitiveMemoryService

service = CognitiveMemoryService(
    mongo_uri="...",
    db_name="...",
    app_slug="my_app",
    config={
        "max_depth": 50,  # Maximum memories per user
    }
)

# Importance is automatically assessed when adding memories
memories = service.add(
    messages="User is allergic to peanuts",
    user_id="user_123"
)
# Memory will have importance score between 0.1-1.0
```

### 2. Memory Reinforcement

When similar content appears (similarity > 0.85), existing memories are **reinforced** instead of creating duplicates.

**Reinforcement effects:**
- Importance increases: `importance * reinforcement_factor` (default: 1.1)
- Access count increments
- Last accessed timestamp updates

```python
# First time: Creates new memory
service.add(messages="I love Python", user_id="user_123")

# Second time (similar): Reinforces existing memory
service.add(messages="Python is my favorite language", user_id="user_123")
# Existing memory importance increases, access_count++
```

### 3. Memory Decay

Memories that are less relevant to new content gradually **decay** in importance.

**Decay mechanism:**
- Similarity < threshold: `importance * decay_factor` (default: 0.99)
- Prevents importance from dropping below 0.1
- Mimics forgetting unused information

### 4. Memory Merging

Related memories (similarity 0.7-0.85) are **merged** into a single cohesive memory.

**Merge process:**
1. LLM combines the two texts into one unified memory
2. Embeddings are averaged
3. Higher importance is used (boosted by 10%)
4. Access counts are combined
5. Old memory is deleted

```python
# These will be merged if similarity is 0.7-0.85
service.add(messages="User prefers dark mode", user_id="user_123")
service.add(messages="User likes dark themes", user_id="user_123")
# Result: Single merged memory with combined information
```

### 5. Memory Pruning

When memory count exceeds `max_depth`, least important memories are **pruned**.

**Pruning criteria:**
- Sorted by effective importance (ascending)
- Effective importance = `importance * (1 + ln(access_count + 1))`
- Lowest scoring memories are deleted first

### 6. Effective Importance

Memories are ranked by **effective importance**, which combines:
- Raw importance (AI-assessed value)
- Access frequency (usage patterns)

**Formula:** `importance * (1 + ln(access_count + 1))`

This ensures frequently accessed memories rank higher, even if their raw importance is lower.

## Configuration

```python
CognitiveMemoryService(
    mongo_uri="...",
    db_name="...",
    app_slug="my_app",
    config={
        # Cognitive parameters
        "max_depth": 50,                    # Maximum memories per user
        "similarity_threshold": 0.7,        # Threshold for reinforcement/decay
        "reinforcement_factor": 1.1,        # Strength of reinforcement
        "decay_factor": 0.99,               # Rate of memory decay
        "merge_threshold_low": 0.7,         # Lower bound for merging
        "merge_threshold_high": 0.85,       # Upper bound for merging
        
        # Standard CustomMemoryService config
        "collection_name": "memories",
        "embedding_model": "text-embedding-3-small",
        "chat_model": "gpt-4o",
        "embedding_dims": 1536,
        "infer": True,
        # Note: index_name is auto-generated as "{collection_name}_vector_index"
        # The memory service automatically creates and manages the vector search index
        # The index includes:
        # - Vector field: "embedding" (with specified dimensions)
        # - Filter field: "user_id" (required for user-scoped queries)
        # - Similarity: "cosine"
    }
)
```

## Usage

### Basic Usage

```python
from mdb_engine.memory import CognitiveMemoryService

service = CognitiveMemoryService(
    mongo_uri="mongodb://...",
    db_name="my_app",
    app_slug="my_app",
)

# Add memories (importance assessed automatically)
memories = service.add(
    messages="User prefers email communication",
    user_id="user_123",
    metadata={"source": "conversation"}
)

# Search (ranked by effective importance)
results = service.search(
    query="communication preferences",
    user_id="user_123",
    limit=5
)

# Results include cognitive metadata
for result in results:
    print(f"Memory: {result['memory']}")
    print(f"Importance: {result['importance']}")
    print(f"Effective Importance: {result['effective_importance']}")
    print(f"Access Count: {result['access_count']}")
    print(f"Similarity: {result['similarity']}")
```

### Integration with CognitiveEngine

```python
from mdb_engine.memory import CognitiveEngine, CognitiveMemoryService

# Create cognitive memory service
memory_service = CognitiveMemoryService(
    mongo_uri="...",
    db_name="...",
    app_slug="my_app",
    config={"max_depth": 50}
)

# Use with CognitiveEngine
engine = CognitiveEngine(
    mongo_uri="...",
    db_name="...",
    app_slug="my_app",
    memory_service=memory_service,  # Use cognitive service
    llm_client=openai_client,
)

# Chat with cognitive memory management
result = engine.chat(
    user_id="user_123",
    session_id="conversation:55",
    user_query="What are my preferences?",
)
```

## Memory Lifecycle

```
New Content
    ↓
Generate Embedding
    ↓
Find Similar Memories
    ↓
┌─────────────────────────────────────┐
│ Similarity > 0.85?                  │
│ → Reinforce existing memory         │
└─────────────────────────────────────┘
    ↓ No
┌─────────────────────────────────────┐
│ Similarity 0.7-0.85?                │
│ → Merge with existing memory        │
└─────────────────────────────────────┘
    ↓ No
┌─────────────────────────────────────┐
│ Assess Importance (LLM)             │
│ → Create new memory node            │
└─────────────────────────────────────┘
    ↓
Update Other Memories (Reinforce/Decay)
    ↓
Prune if Count > Max Depth
    ↓
Complete
```

## Search Ranking

Search results are ranked by **combined score**:

```
combined_score = similarity * effective_importance
```

Where:
- `similarity`: Vector search similarity score (0-1)
- `effective_importance`: `importance * (1 + ln(access_count + 1))`

This ensures:
1. Semantically relevant memories rank high
2. Frequently accessed memories rank higher
3. Important memories rank higher
4. Balance between relevance and usage patterns

## Database Schema

Memories include cognitive fields:

```javascript
{
  "_id": ObjectId("..."),
  "user_id": "user_123",
  "text": "User prefers dark mode",
  "embedding": [0.012, -0.04...],
  "importance": 0.8,              // AI-assessed importance (0.1-1.0)
  "access_count": 5,              // Number of times accessed
  "last_accessed": ISODate("..."), // Last access timestamp
  "metadata": {
    "bucket_id": "session_55",
    "merged": false,              // Whether this memory was merged
  },
  "created_at": ISODate("..."),
  "updated_at": ISODate("...")
}
```

## Best Practices

1. **Set appropriate max_depth**: Balance between memory capacity and performance
   - Small apps: 20-50 memories
   - Medium apps: 50-100 memories
   - Large apps: 100-200 memories

2. **Tune similarity thresholds**:
   - Higher merge_threshold_high (0.85-0.9): More aggressive reinforcement
   - Lower merge_threshold_low (0.6-0.7): More aggressive merging

3. **Monitor memory quality**:
   - Review importance distributions
   - Check merge frequency
   - Monitor prune events

4. **Use with CognitiveEngine**: Combines cognitive memory with STM/LTM architecture

## Performance Considerations

- **Importance assessment**: Adds LLM call per fact (can be expensive)
- **Memory merging**: Adds LLM call per merge (infrequent)
- **Pruning**: O(n log n) sort operation (runs only when needed)
- **Search**: Same performance as CustomMemoryService (with ranking overhead)

## Comparison: Custom vs Cognitive

| Feature | CustomMemoryService | CognitiveMemoryService |
|---------|-------------------|----------------------|
| Basic CRUD | ✅ | ✅ |
| Vector Search | ✅ | ✅ |
| Importance Scoring | ❌ | ✅ |
| Reinforcement | ❌ | ✅ |
| Decay | ❌ | ✅ |
| Merging | ❌ | ✅ |
| Pruning | ❌ | ✅ |
| Access Tracking | ❌ | ✅ |
| Effective Importance | ❌ | ✅ |

## Switching to CognitiveMemoryService

```python
# Basic memory service
from mdb_engine.memory import CustomMemoryService

service = CustomMemoryService(...)

# Advanced cognitive memory service (drop-in replacement)
from mdb_engine.memory import CognitiveMemoryService

service = CognitiveMemoryService(...)
# All existing methods work the same way
# Cognitive features are automatic
```

---

## Redaction Layer (Privacy Protection)

The Redaction Layer protects sensitive data from being stored in memory or sent to LLMs for fact extraction.

### Built-in Patterns

| Pattern | Default | Description |
|---------|---------|-------------|
| `ssn` | Enabled | Social Security Numbers (XXX-XX-XXXX) |
| `credit_card` | Enabled | Credit card numbers (13-16 digits) |
| `phone` | Disabled | Phone numbers |
| `email` | Disabled | Email addresses |
| `api_key` | Enabled | API keys and secrets |
| `password` | Enabled | Password assignments |
| `bearer_token` | Enabled | Bearer tokens |
| `aws_key` | Enabled | AWS access keys |

### Configuration

```json
{
  "memory_config": {
    "redaction": {
      "enabled": true,
      "replacement": "[REDACTED]",
      "patterns": {
        "ssn": true,
        "credit_card": true,
        "email": false,
        "phone": true,
        "custom": ["\\bsecret_\\w+"]
      },
      "allow_list": ["support@company.com"]
    }
  }
}
```

### Programmatic Usage

```python
from mdb_engine.memory import RedactionService

redactor = RedactionService(config={
    "enabled": True,
    "patterns": {"ssn": True, "credit_card": True},
})

# Redact text
clean_text = redactor.redact("My SSN is 123-45-6789")
# Returns: "My SSN is [REDACTED]"

# Test what would be redacted
result = redactor.test_redaction("SSN: 123-45-6789")
# Returns: {"would_redact": True, "matches": ["123-45-6789"], ...}

# Add custom pattern at runtime
redactor.add_pattern("employee_id", r"EMP\d{6}")
```

---

## Memory Categories

Memories are automatically categorized during fact extraction for better organization and retrieval.

### Categories

**Important:** Memory categories are semantic classifications. "general" is NOT a memory category - it's only used for `bucket_type` filtering (like "work", "coding", "general"). Every memory MUST have one of these four categories:

| Category | Description | Examples |
|----------|-------------|----------|
| `biographical` | Personal info | Name, age, occupation, family, location |
| `preferences` | Likes/dislikes | Favorite foods, brand preferences, style choices |
| `temporal` | Time-bound info | Current projects, deadlines, short-term goals |
| `relational` | Relationships | How user feels about others, communication preferences, family members |

**Note:** If a memory doesn't clearly fit any category, the system automatically detects the most appropriate one using heuristics and LLM analysis. The category is never left as "general".

### Configuration

```json
{
  "memory_config": {
    "categories": {
      "enabled": true,
      "custom_categories": ["work", "health", "hobbies"]
    }
  }
}
```

### Memory Schema with Category

```javascript
{
  "_id": ObjectId("..."),
  "user_id": "user_123",
  "text": "User works at Google",
  "category": "biographical",  // Top-level field for querying
  "metadata": {
    "category": "biographical",
    "bucket_id": "conversation:123"
  },
  "importance": 0.8,
  "created_at": ISODate("...")
}
```

### Searching by Category

```python
# Search within a specific category
results = service.search(
    query="where does the user work",
    user_id="user_123",
    filters={"metadata": {"category": "biographical"}}
)
```

---

## Reflection Service (Memory Consolidation)

The Reflection Service periodically consolidates atomic memories into narrative summaries, preventing memory bloat.

### How It Works

1. **Trigger**: Time-based (24h default) or count-based (50 memories default)
2. **Consolidate**: LLM summarizes recent memories into a narrative
3. **Store**: Reflection saved in `{app_slug}_reflections` collection
4. **Prune**: Optionally removes low-importance memories

### Configuration

```json
{
  "memory_config": {
    "reflection": {
      "enabled": true,
      "interval_hours": 24,
      "message_threshold": 50,
      "min_salience_to_keep": 0.4,
      "store_reflections": true
    }
  }
}
```

### Programmatic Usage

```python
from mdb_engine.memory import ReflectionService

reflection_service = ReflectionService(
    app_slug="my_app",
    memories_collection=memories_col,
    reflections_collection=reflections_col,
    config={"enabled": True}
)

# Check if reflection is needed
should, reason = reflection_service.should_reflect("user_123")

# Run reflection
result = reflection_service.run_reflection("user_123")
# Returns: {
#   "success": True,
#   "memories_processed": 45,
#   "reflection_content": "The user is a software engineer...",
#   "memories_pruned": 12
# }

# Get recent reflections
reflections = reflection_service.get_recent_reflections("user_123", limit=5)
```

### Reflection Schema

```javascript
{
  "_id": ObjectId("..."),
  "user_id": "user_123",
  "content": "The user is a Python developer who works at Tech Corp...",
  "type": "periodic_summary",
  "period_start": ISODate("2024-01-14T00:00:00Z"),
  "period_end": ISODate("2024-01-15T00:00:00Z"),
  "memories_consolidated": 45,
  "memory_ids": ["mem1", "mem2", ...],
  "created_at": ISODate("2024-01-15T00:00:00Z")
}
```

---

## Full Manifest Configuration Example

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My Application",
  "memory_config": {
    "enabled": true,
    "collection_name": "user_memories",
    "embedding_model": "text-embedding-3-small",
    "chat_model": "gpt-4o",
    "infer": true,
    
    "enable_cognitive": true,
    "max_depth": 100,
    "similarity_threshold": 0.7,
    "reinforcement_factor": 1.1,
    "decay_factor": 0.99,
    
    "redaction": {
      "enabled": true,
      "patterns": {
        "ssn": true,
        "credit_card": true,
        "password": true
      }
    },
    
    "categories": {
      "enabled": true,
      "custom_categories": ["work", "health"]
    },
    
    "reflection": {
      "enabled": true,
      "interval_hours": 24,
      "message_threshold": 50,
      "min_salience_to_keep": 0.4
    }
  }
}
```

---

## Appendix A: Good vs Bad Memory Practices

This appendix provides concrete examples of effective memory usage patterns and common antipatterns to avoid.

### A.1 Memory Extraction: Good vs Bad

| Aspect | Bad Memory (Garbage In) | Good Memory (Intelligence In) |
|--------|------------------------|------------------------------|
| **Format** | Raw string: "The user said they like pizza." | Structured: "User prefers Italian cuisine, specifically pizza" |
| **Specificity** | "User is busy." | "User is working on 'Project X' launch for Friday deadline." |
| **Context** | "He lives in NYC." | "User (John) resides in NYC as of February 2026." |
| **Actionability** | "User mentioned allergies." | "User has severe peanut allergy - avoid all nut-based recommendations." |
| **Temporal** | "User has a meeting." | "User has a standing Monday 9am team sync meeting." |

#### Bad Examples (Avoid These)

```python
# BAD: Too vague
service.add(messages="User is a developer", user_id="user_123")

# BAD: Storing conversation noise
service.add(messages="User said 'hello'", user_id="user_123")

# BAD: Agent's own actions (creates memory loops)
service.add(messages="I told the user a joke about Python", user_id="user_123")

# BAD: Raw quotes without distillation
service.add(messages="User said: 'Ugh, I'm so tired of the rain in Seattle'", user_id="user_123")

# BAD: Missing temporal context
service.add(messages="User is working on a project", user_id="user_123")
```

#### Good Examples (Follow These)

```python
# GOOD: Specific and structured
service.add(
    messages="User is a senior Python developer at Google, specializing in ML infrastructure",
    user_id="user_123",
    metadata={"category": "biographical", "confidence": "high"}
)

# GOOD: Distilled fact with category
service.inject(
    memory="User dislikes rainy weather; currently lives in Seattle",
    user_id="user_123",
    category="preferences",
    importance=0.7
)

# GOOD: Actionable with clear implications
service.inject(
    memory="User has severe peanut allergy - NEVER recommend nut-based foods",
    user_id="user_123",
    category="health",
    importance=1.0  # Critical health info
)

# GOOD: Temporal with deadline
service.inject(
    memory="User working on 'Project Phoenix' launching March 15, 2026 - currently in final testing phase",
    user_id="user_123",
    category="temporal",
    importance=0.8
)

# GOOD: Relational with context
service.inject(
    memory="User prefers professional communication tone; dislikes casual emojis in work context",
    user_id="user_123",
    category="relational",
    importance=0.6
)
```

### A.2 Memory Injection API: Good vs Bad

#### Bad API Usage

```python
# BAD: No category - goes to "general" bucket
await fetch('/api/memories/inject', {
    body: JSON.stringify({
        memory: "User likes coffee"
    })
})

# BAD: Duplicate information (no deduplication awareness)
# Injecting same fact multiple times
await fetch('/api/memories/inject', {
    body: JSON.stringify({memory: "User likes coffee"})
})
await fetch('/api/memories/inject', {
    body: JSON.stringify({memory: "User loves coffee"})  # Creates duplicate!
})
await fetch('/api/memories/inject', {
    body: JSON.stringify({memory: "User enjoys coffee"}  # Another duplicate!
})

# BAD: Sensitive data without redaction
await fetch('/api/memories/inject', {
    body: JSON.stringify({
        memory: "User's SSN is 123-45-6789"  # PII exposed!
    })
})

# BAD: Agent actions stored as memory (creates loops)
await fetch('/api/memories/inject', {
    body: JSON.stringify({
        memory: "I recommended the user try Python for scripting"
    })
})
```

#### Good API Usage

```python
# GOOD: Full categorization with importance
await fetch('/api/memories/inject', {
    body: JSON.stringify({
        memory: "User prefers morning meetings between 9-11am",
        category: "preferences",
        importance: 0.7,
        metadata: {
            "verified": true,
            "source": "direct_statement"
        }
    })
})

# GOOD: Using conversation context for grouping
await fetch('/api/memories/inject', {
    body: JSON.stringify({
        memory: "User's project 'Atlas' uses PostgreSQL with Redis caching",
        category: "work",
        importance: 0.8,
        conversation_id: "conv_12345"  # Links to conversation
    })
})

# GOOD: Health information with high importance
await fetch('/api/memories/inject', {
    body: JSON.stringify({
        memory: "User is vegetarian for ethical reasons",
        category: "health",
        importance: 0.9,
        metadata: {
            "dietary_restriction": true,
            "reason": "ethical"
        }
    })
})

# GOOD: Biographical with structured data
await fetch('/api/memories/inject', {
    body: JSON.stringify({
        memory: "User is Alex Chen, VP of Engineering at TechCorp, based in San Francisco",
        category: "biographical",
        importance: 0.85
    })
})
```

### A.3 Category Selection Guidelines

| Category | When to Use | Example Facts | Importance Range |
|----------|-------------|---------------|------------------|
| **biographical** | Identity, role, location | "Alex, 32, Seattle, Software Engineer" | 0.7-0.9 |
| **preferences** | Likes, dislikes, favorites | "Prefers dark mode, dislikes meetings after 4pm" | 0.5-0.8 |
| **temporal** | Projects, deadlines, current state | "Working on Q1 launch, deadline March 1" | 0.6-0.9 |
| **relational** | Communication style, feelings | "Prefers direct feedback, formal tone" | 0.5-0.7 |
| **work** | Job-related, projects, tools | "Uses VS Code, manages team of 5" | 0.6-0.8 |
| **health** | Medical, dietary, fitness | "Peanut allergy, vegetarian, runs marathons" | 0.8-1.0 |
| **finance** | Budget, goals, preferences | "Saving for house, prefers value over premium" | 0.6-0.8 |
| **general** | Miscellaneous facts | "Owns a golden retriever named Max" | 0.4-0.6 |

### A.4 Importance Scoring Guidelines

```
1.0  ████████████  CRITICAL - Health/safety, never forget
0.9  ███████████   HIGH - Core identity, key constraints
0.8  ██████████    IMPORTANT - Major preferences, active projects
0.7  █████████     NOTABLE - Useful context, recurring topics
0.6  ████████      MODERATE - Helpful background info
0.5  ███████       AVERAGE - General preferences
0.4  ██████        LOW - Nice to have context
0.3  █████         MINOR - Small talk, ephemeral
0.2  ████          TRIVIAL - Likely to be pruned
0.1  ███           MINIMAL - Candidate for immediate pruning
```

**Importance Examples:**

```python
# 1.0 - Critical health/safety
{"memory": "User has severe shellfish allergy - anaphylactic reaction risk", "importance": 1.0}

# 0.9 - Core identity
{"memory": "User is CEO of a Fortune 500 company", "importance": 0.9}

# 0.8 - Active priority
{"memory": "User's product launch is next week - highest priority", "importance": 0.8}

# 0.6 - Useful context
{"memory": "User commutes 45 minutes each way", "importance": 0.6}

# 0.4 - Background info
{"memory": "User's favorite coffee shop is Blue Bottle", "importance": 0.4}

# 0.2 - Trivial
{"memory": "User mentioned they watched a movie last weekend", "importance": 0.2}
```

### A.5 Common Antipatterns and Solutions

#### Antipattern 1: Memory Loops

**Problem:** Agent stores its own actions, then retrieves and repeats them.

```python
# BAD: Creates a loop
service.add("I recommended Python for the scripting task")
# Later: "I see I recommended Python before, I'll recommend it again!"
```

**Solution:** Filter out agent actions from fact extraction.

```python
# GOOD: Only store user facts
if not fact.startswith("I ") and not fact.startswith("The assistant"):
    service.add(fact, user_id=user_id)
```

#### Antipattern 2: Duplicate Memories

**Problem:** Same fact stored multiple times with slight variations.

```python
# These create 3 separate memories saying the same thing
service.add("User likes coffee")
service.add("User enjoys coffee")
service.add("User loves drinking coffee")
```

**Solution:** Use the "Last Seen" update pattern (built into CognitiveMemoryService).

```python
# CognitiveMemoryService automatically detects duplicates
# and updates last_mentioned timestamp instead of creating new entries
service.add("User likes coffee")  # Creates new
service.add("User enjoys coffee")  # Similarity > 0.95 = updates existing
```

#### Antipattern 3: Storing Sensitive Data

**Problem:** PII, credentials, or secrets stored in memory.

```python
# BAD: Sensitive data exposed
service.add("User's password is hunter2")
service.add("User's SSN is 123-45-6789")
service.add("API key: sk-abc123...")
```

**Solution:** Enable redaction layer in manifest.

```json
{
  "memory_config": {
    "redaction": {
      "enabled": true,
      "patterns": {
        "ssn": true,
        "password": true,
        "api_key": true
      }
    }
  }
}
```

#### Antipattern 4: Over-Specific Temporal Data

**Problem:** Storing dates that quickly become outdated.

```python
# BAD: Will be wrong tomorrow
service.add("Today is January 15, 2026")
service.add("User's meeting is in 2 hours")
```

**Solution:** Store relative context or omit ephemeral details.

```python
# GOOD: Lasting information
service.add("User has standing Monday 10am team meetings")
service.add("User typically prefers afternoon meetings")
```

#### Antipattern 5: Context-Free Facts

**Problem:** Facts without enough context to be useful.

```python
# BAD: Who? What? When?
service.add("Project deadline is Friday")
service.add("The manager approved it")
service.add("Using the new API")
```

**Solution:** Include relevant context.

```python
# GOOD: Full context
service.add("User's 'Project Phoenix' deadline is Friday March 8, 2026")
service.add("User's manager Sarah approved the budget increase for Q2")
service.add("User's team is migrating to the new GraphQL API this quarter")
```

### A.6 Memory Injection UI Best Practices

#### For Frontend Developers

```html
<!-- GOOD: Provide category selection with descriptions -->
<div class="category-grid">
    <button data-category="biographical" title="Name, age, job, location">
        👤 Biographical
    </button>
    <button data-category="preferences" title="Likes, dislikes, favorites">
        ❤️ Preferences
    </button>
    <!-- ... more categories ... -->
</div>

<!-- GOOD: Importance slider with visual feedback -->
<input type="range" min="0.1" max="1.0" step="0.1" 
       oninput="updateImportanceColor(this.value)">
```

```javascript
// GOOD: Validate before injection
async function injectMemory(memory, category, importance) {
    // Validation
    if (!memory.trim()) {
        showError("Memory content is required");
        return;
    }
    
    if (memory.length < 10) {
        showWarning("Memory seems too short - consider adding more context");
    }
    
    if (memory.length > 500) {
        showWarning("Memory is long - consider breaking into multiple facts");
    }
    
    // Check for potential PII
    const piiPatterns = [/\d{3}-\d{2}-\d{4}/, /\b\d{16}\b/];
    if (piiPatterns.some(p => p.test(memory))) {
        showError("Sensitive data detected - please remove before injecting");
        return;
    }
    
    // Inject
    const response = await fetch('/api/memories/inject', {
        method: 'POST',
        body: JSON.stringify({ memory, category, importance })
    });
}
```

### A.7 Complete Example: Building User Profile

Here's a comprehensive example showing proper memory usage to build a user profile over time:

```python
from mdb_engine.memory import CognitiveMemoryService

service = CognitiveMemoryService(
    mongo_uri="mongodb://...",
    db_name="my_app",
    app_slug="my_app",
    config={
        "max_depth": 100,
        "enable_cognitive": True,
        "categories": {"enabled": True},
        "redaction": {"enabled": True, "patterns": {"ssn": True, "credit_card": True}},
    }
)

user_id = "user_alex_123"

# Session 1: Initial conversation
# User: "Hi, I'm Alex. I'm a software engineer at Google."
service.inject(
    memory="User is Alex, a software engineer at Google",
    user_id=user_id,
    category="biographical",
    importance=0.85
)

# User: "I mainly work with Python and Go"
service.inject(
    memory="User's primary programming languages are Python and Go",
    user_id=user_id,
    category="work",
    importance=0.7
)

# Session 2: Learning preferences
# User: "I prefer working in the mornings, I'm not a night owl"
service.inject(
    memory="User prefers morning work hours; not productive at night",
    user_id=user_id,
    category="preferences",
    importance=0.6
)

# User: "Dark mode everything please, light mode hurts my eyes"
service.inject(
    memory="User strongly prefers dark mode interfaces; light mode causes eye strain",
    user_id=user_id,
    category="preferences",
    importance=0.7
)

# Session 3: Health & constraints
# User: "I'm vegetarian and allergic to peanuts"
service.inject(
    memory="User is vegetarian",
    user_id=user_id,
    category="health",
    importance=0.8
)

service.inject(
    memory="User has peanut allergy - avoid all nut-based recommendations",
    user_id=user_id,
    category="health",
    importance=1.0  # Critical health info
)

# Session 4: Current projects
# User: "I'm leading the migration to Kubernetes, deadline is end of Q1"
service.inject(
    memory="User leading Kubernetes migration project at Google; deadline Q1 2026",
    user_id=user_id,
    category="temporal",
    importance=0.85
)

# Later: Search for relevant context
results = service.search(
    query="What are the user's dietary restrictions?",
    user_id=user_id,
    limit=5
)

# Results will include:
# 1. "User has peanut allergy..." (importance: 1.0, category: health)
# 2. "User is vegetarian" (importance: 0.8, category: health)

# Get all biographical info
bio_memories = service.search(
    query="user background and identity",
    user_id=user_id,
    filters={"category": "biographical"}
)
```

### A.8 Debugging Memory Issues

```python
# Check memory statistics
stats = service.get_stats(user_id="user_123")
print(f"Total memories: {stats['total_count']}")
print(f"By category: {stats['by_category']}")
print(f"Average importance: {stats['avg_importance']}")

# Find potential duplicates
memories = service.get_all(user_id="user_123")
for i, m1 in enumerate(memories):
    for m2 in memories[i+1:]:
        similarity = compute_similarity(m1['embedding'], m2['embedding'])
        if similarity > 0.9:
            print(f"Potential duplicate: '{m1['text']}' ≈ '{m2['text']}'")

# Review low-importance memories (candidates for pruning)
low_importance = [m for m in memories if m.get('importance', 0.5) < 0.3]
print(f"Low importance memories ({len(low_importance)}):")
for m in low_importance:
    print(f"  - {m['text'][:50]}... (importance: {m['importance']})")

# Check for memory without categories
uncategorized = [m for m in memories if not m.get('category')]
print(f"Uncategorized memories: {len(uncategorized)}")
```

---

## Appendix B: Quick Reference Card

### Memory Categories

| Icon | Category | Use For |
|------|----------|---------|
| 👤 | biographical | Name, job, location, age |
| ❤️ | preferences | Likes, dislikes, favorites |
| 📅 | temporal | Projects, deadlines, current state |
| 👥 | relational | Communication style, relationships |
| 💼 | work | Job tasks, tools, team |
| 🏥 | health | Medical, dietary, fitness |
| 💰 | finance | Budget, financial goals |
| 📝 | general | Everything else |

### Importance Quick Guide

| Score | Meaning | Examples |
|-------|---------|----------|
| 1.0 | Critical/Safety | Allergies, medical conditions |
| 0.8 | Important | Core identity, active priorities |
| 0.6 | Useful | Preferences, background |
| 0.4 | Nice-to-have | Minor details |
| 0.2 | Trivial | Ephemeral, small talk |

### Memory Injection Checklist

- [ ] Content is specific and actionable?
- [ ] Category is appropriate?
- [ ] Importance reflects actual significance?
- [ ] No sensitive data (PII, credentials)?
- [ ] Includes necessary context?
- [ ] Not storing agent's own actions?
- [ ] Not a duplicate of existing memory?

---

## See Also

- [Cognitive Architecture](./COGNITIVE_ARCHITECTURE.md)
- [Base Memory Service API](../mdb_engine/memory/base.py)
- [GDPR Compliance](./GDPR_COMPLIANCE.md)
- [MongoDB Partners AI Memory](https://github.com/mongodb-partners/ai-memory)
