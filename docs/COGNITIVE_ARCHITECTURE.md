# Cognitive Architecture: Short-Term + Long-Term Memory

## Overview

mdb-engine implements a complete **Cognitive Architecture** that separates concerns between Short-Term Memory (STM) and Long-Term Memory (LTM), mimicking how human memory works.

### Why Separate STM and LTM?

**Vector databases are terrible at "what did I just say 5 seconds ago?"** - that's the job of Chat History (STM). Vector databases excel at semantic retrieval of facts from days/weeks ago - that's LTM.

## Architecture

### 1. Short-Term Memory (STM) / Chat History

**What it is:** The raw log of the current conversation session.

**Storage:** MongoDB Collection `chat_history`

**Schema:**
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

**Retrieval:** Sorted by time, filtered by `session_id`

**Purpose:** Provides immediate context (e.g., "Stop doing *that*" references the previous message)

**Example:**
```python
from mdb_engine.memory import ChatHistoryService

# Initialize
stm = ChatHistoryService(db, collection_name="chat_history")

# Add message
stm.add_message(
    session_id="conversation:123",
    role="user",
    content="I love Python",
    user_id="user_123"
)

# Get context (last 10 messages)
context = stm.get_context(session_id="conversation:123", limit=10)
# Returns: [{"role": "user", "content": "I love Python"}, ...]
```

### 2. Long-Term Memory (LTM) / Vector Store

**What it is:** The `CustomMemoryService` we built - semantic memory storage.

**Storage:** MongoDB Collection `{app_slug}_memories` with Vector Search

**Schema:**
```javascript
{
  "_id": ObjectId("..."),
  "user_id": "user_123",
  "text": "User prefers dark mode UI.",
  "embedding": [0.012, -0.04...],
  "metadata": {
    "bucket_id": "session_55",
    "bucket_type": "chat",
    "source": "manual_inject"
  },
  "created_at": ISODate("..."),
  "updated_at": ISODate("...")
}
```

**Retrieval:** Semantic similarity search

**Purpose:** Recalling facts from days/weeks ago (e.g., "User is allergic to peanuts")

### 3. The Orchestrator: CognitiveEngine

**What it is:** The code that binds STM and LTM together.

**How it works:**
1. Saves user message to STM
2. Searches LTM for relevant facts
3. Fetches STM context (last K messages)
4. Generates LLM response
5. Saves AI response to STM
6. Extracts new facts to LTM (async)

## Usage

### Basic Example

```python
from mdb_engine.memory import CognitiveEngine
from openai import OpenAI
import os

# Initialize
mongo_uri = os.getenv("MONGO_URI")
db_name = "my_app"
app_slug = "my_app"
llm_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Create Cognitive Engine
engine = CognitiveEngine(
    mongo_uri=mongo_uri,
    db_name=db_name,
    app_slug=app_slug,
    stm_context_limit=10,  # Last 10 messages for context
    ltm_search_limit=5,     # Top 5 relevant memories
    auto_summarize_threshold=20,  # Summarize when session > 20 messages
    llm_client=llm_client,
)

# Chat!
user_id = "user_123"
session_id = "conversation:55"

result = engine.chat(
    user_id=user_id,
    session_id=session_id,
    user_query="What's my favorite color?",
)

print(result["response"])  # AI response
print(result["ltm_memories"])  # Relevant facts retrieved
print(result["stm_context"])  # Chat history used
```

### Integration with Existing Memory Service

```python
from mdb_engine import MongoDBEngine
from mdb_engine.memory import CognitiveEngine, CustomMemoryService

# Initialize engine
mdb_engine = MongoDBEngine(mongo_uri="...", db_name="...")
await mdb_engine.initialize()

# Get existing memory service
memory_service = mdb_engine.get_memory_service("my_app")

# Create Cognitive Engine with existing memory service
cognitive_engine = CognitiveEngine(
    mongo_uri=mdb_engine.mongo_uri,
    db_name=mdb_engine.db_name,
    app_slug="my_app",
    memory_service=memory_service,  # Reuse existing service
    llm_client=your_openai_client,
)
```

## Advanced Features

### 1. Session Summarization (Medium-Term Memory)

When a chat session gets too long (> 20 messages), the context window fills up.

**Solution:** Automatically summarize old messages and store in LTM.

```python
# This happens automatically when session_message_count > auto_summarize_threshold
# Or manually:
summary = engine.summarize_session(
    session_id="conversation:123",
    user_id="user_123",
    messages_to_summarize=10,  # Summarize oldest 10 messages
)
```

**How it works:**
1. Detects when `len(stm_context) > threshold`
2. Takes the oldest N messages
3. Asks LLM to "Summarize this conversation chunk into 2-3 sentences"
4. Stores summary in LTM with `metadata={"type": "session_summary"}`
5. Deletes summarized messages from STM

### 2. User Profiling (Metadata Filters)

Enforce strict separation using `user_id` and `bucket_id`.

**Family Mode Example:**
```python
# Memories shared among family members
engine.ltm.add(
    messages="User prefers dark mode",
    user_id="family_group",
    bucket_id="family_chat",
    bucket_type="family",
)

# Search only family memories
memories = engine.ltm.search(
    query="user preferences",
    user_id="family_group",
    filters={"metadata.bucket_type": "family"},
)
```

### 3. "Thought" Storage

Store internal AI reasoning that the user didn't see.

```python
engine.inject_thought(
    user_id="user_123",
    thought="User seems frustrated, should be more empathetic",
    session_id="conversation:123",
    visibility="private",
    metadata={"reasoning": "tone_analysis"},
)
```

### 4. Full Context Retrieval

Get complete context (STM + LTM) for debugging or inspection.

```python
context = engine.get_full_context(
    user_id="user_123",
    session_id="conversation:123",
    query="user preferences",  # Optional query for LTM search
)

print(context["stm_context"])  # Chat history
print(context["ltm_memories"])  # Relevant facts
```

## Data Flow Example

### Scenario: "The Pizza Preference"

**Turn 1:**

* **User:** "Hi, I'm allergic to mushrooms."
* **STM:** Saves: `User: I'm allergic to mushrooms`
* **LTM Extraction:** Detects fact "User is allergic to mushrooms" → **Embeds & Saves to Vector DB**
* **AI:** "Noted. I'll remember that."

**Turn 2 (One week later, new session):**

* **User:** "Order me a pizza."
* **STM:** Saves `User: Order me a pizza` (Empty context otherwise - new session)
* **LTM Search:** Query "pizza" → Finds vector "User is allergic to mushrooms"
* **Prompt to LLM:**
  ```
  System: RELEVANT FACT: User is allergic to mushrooms.
  Chat: User: Order me a pizza.
  ```
* **AI:** "Sure! What toppings would you like? I'll make sure to avoid mushrooms."

## Configuration

### CognitiveEngine Parameters

```python
CognitiveEngine(
    mongo_uri="...",                    # MongoDB connection
    db_name="...",                       # Database name
    app_slug="...",                      # App slug
    memory_service=None,                 # Optional CustomMemoryService (creates if None)
    chat_history_collection="chat_history",  # STM collection name
    stm_context_limit=10,                # Number of recent messages for context
    ltm_search_limit=5,                  # Number of relevant memories to retrieve
    auto_summarize_threshold=20,         # Auto-summarize when session > N messages
    llm_client=None,                     # OpenAI client (required)
)
```

### ChatHistoryService Parameters

```python
ChatHistoryService(
    db=db_instance,                      # MongoDB database
    collection_name="chat_history",      # Collection name
)
```

## Best Practices

1. **Use session_id consistently**: Use `f"conversation:{conversation_id}"` as session_id
2. **Set appropriate limits**: 
   - `stm_context_limit`: 10-20 messages (depends on your LLM context window)
   - `ltm_search_limit`: 3-5 memories (more can add noise)
3. **Enable auto-summarization**: Set `auto_summarize_threshold` to prevent context overflow
4. **Use bucket_id for organization**: Group related memories (e.g., `bucket_id="conversation:123"`)
5. **Monitor memory growth**: Regularly review LTM to ensure quality facts are stored

## Performance Considerations

- **STM queries are fast**: Simple MongoDB queries by session_id
- **LTM queries use Vector Search**: Requires MongoDB Atlas Vector Search index
- **Fact extraction is async**: Doesn't block response generation
- **Summarization is expensive**: Only runs when threshold is exceeded

## Using CognitiveEngine

If you're manually managing STM and LTM, you can simplify with `CognitiveEngine`:

1. **Replace manual STM storage** with `ChatHistoryService`
2. **Replace manual LTM search** with `CognitiveEngine.chat()`
3. **Remove manual context construction** - CognitiveEngine handles it
4. **Keep existing LTM data** - CognitiveEngine uses your existing `CustomMemoryService`

## Troubleshooting

### "No LLM client available"

**Solution:** Pass `llm_client` to `CognitiveEngine` constructor.

### "Session context too long"

**Solution:** Lower `stm_context_limit` or enable auto-summarization.

### "LTM search returns irrelevant results"

**Solution:** Adjust `ltm_search_limit` or improve fact extraction prompts.

## See Also

- [Cognitive Memory Service](./COGNITIVE_MEMORY.md)
- [Custom Memory Service](../mdb_engine/memory/custom.py)
- [Memory Orchestrator](../mdb_engine/memory/orchestrator.py)
