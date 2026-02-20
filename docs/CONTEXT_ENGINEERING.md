# Context Engineering Guide

## Overview

Context Engineering is the architectural discipline of constructing the "present moment" for an LLM. Since models are stateless, the **Context** is the only reality they know. To optimize this, you must treat the Context Window not as a garbage dump for all history, but as a carefully curated stage.

MDB-Engine's Memory Service implements Context Engineering through the `ChatEngine` (also known as `ChatEngine`), which dynamically assembles context from multiple layers according to the equation:

```
Context = P_static + M_relevant + Q_current
```

Where:
- **P_static**: The Persona Layer (System Instructions) - immutable core identity
- **M_relevant**: Filtered Memory (Retrieved based on the current query)
- **Q_current**: The User's Input

## The Architecture of Context

### A. The Persona Layer (Identity)

This is the immutable core. It does not change between turns.

- **Role**: Who is the model? (e.g., "Senior Python Architect")
- **Description**: What does it do? (e.g., "Expert in Python and system design")
- **Traits**: How does it behave? (e.g., `{"technical_focus": 0.9, "humor": 0.2, "formality": 0.8}`)

The Persona Layer is managed by `PersonaEngine` and stored in MongoDB. It acts as a filter through which all data passes, determining:
- **Salience**: What is worth remembering?
- **Tone & Synthesis**: How is a memory retrieved?
- **Consistency**: Ensures memories align with agent's established "self"

### B. The Memory Layer (State)

This is where optimization happens. You cannot feed the model the entire chat history (it's too expensive and confusing). You must categorize memory:

1. **Short-Term Memory (STM)**: The last N turns of raw conversation (maintains immediate flow)
2. **Long-Term Memory (LTM)**: Semantic vector store for facts retrieved based on similarity to current query (RAG)
3. **Entity Memory**: Key-value facts extracted and stored (e.g., `User_Name: Alice`, `Language: Python`, `Expertise: expert`)
4. **Graph Context**: Knowledge graph relationships via GraphRAG (optional)

### C. Dynamic Persona Adaptation

The persona can be dynamically adapted based on retrieved memories:

- **Expertise-based**: If `Expertise == "expert"` → terse persona. If `Expertise == "beginner"` → verbose persona.
- **Emotion-based**: High-emotion memories → empathetic persona
- **Trait-based**: Uses persona traits to adjust tone (humor, formality, empathy)

## Context Engineering Features

### 1. Persona Integration

The `PersonaEngine` is automatically integrated into system prompt construction when Context Engineering is enabled.

**Example**:
```python
from mdb_engine.memory import ChatEngine  # preferred name (CognitiveEngine also works)

# PersonaEngine is automatically accessed from memory_service
chat_engine = ChatEngine(
    app_slug="my_app",
    memory_service=memory_service,  # Contains PersonaEngine
    chat_history_collection=chat_collection,
    enable_context_engineering=True,  # Enabled by default
)

# Persona is automatically retrieved and used in system prompt
result = await chat_engine.chat(
    user_id="user123",
    session_id="session456",
    user_query="How do I optimize Python code?",
)
```

### 2. Entity Memory Extraction

Entity facts are automatically extracted from retrieved memories and injected into every prompt.

**Extracted Facts**:
- **Name**: User's name
- **OS**: Operating system (Ubuntu, Windows, macOS, Linux)
- **Language**: Programming language (Python, JavaScript, Java, Rust, Go)
- **Expertise**: Skill level (expert, intermediate, beginner)
- **UI_Preference**: Interface preferences (dark mode, light mode)

**Example**:
```python
# Memories with category="biographical" are analyzed
# Entity facts are extracted and injected into system prompt

result = await chat_engine.chat(...)
print(result["entity_facts"])
# {"Name": "Alice", "OS": "Ubuntu", "Language": "Python", "Expertise": "expert"}
```

### 3. Dynamic Persona Adaptation

The persona adapts based on user context:

**Expertise-based Adaptation**:
- Expert → "Be concise. Skip explanations. Assume technical knowledge."
- Beginner → "Be educational. Explain concepts clearly. Provide examples."
- Intermediate → "Provide balanced explanations with some detail."

**Emotion-based Adaptation**:
- High-emotion memories → "Be empathetic. Acknowledge feelings."

**Trait-based Adaptation**:
- High humor trait → "Use appropriate humor when relevant."
- High formality → "Maintain a professional and formal tone."
- High empathy → "Show high empathy and emotional intelligence."

### 4. Sliding Window + Summary Pattern

To optimize token usage, the STM context uses a sliding window:

- **Last N messages** (default: 5) are kept raw for immediate context
- **Older messages** are summarized into a single paragraph
- Summary is injected as "[PREVIOUS CONTEXT]" section

**Configuration**:
```python
chat_engine = ChatEngine(
    app_slug="my_app",
    memory_service=memory_service,
    chat_history_collection=chat_collection,
    stm_raw_window=5,  # Number of recent messages to keep raw
)
```

## System Prompt Structure

The context-engineered system prompt follows this structure:

```
[PERSONA LAYER]
{persona.role}
{persona.description}

Traits: {formatted_traits}

[META-INSTRUCTIONS]
{dynamic_instructions}

[USER CONTEXT]
Known Facts: {entity_facts_formatted}

[RELEVANT MEMORY]
{ltm_context}

[GRAPH CONTEXT]
{graph_context}

[PREVIOUS CONTEXT]
{stm_summary}

[CHAT HISTORY]
{recent_stm_messages}
```

## Configuration

Context Engineering is enabled by default. You can configure it when creating `ChatEngine`:

```python
chat_engine = ChatEngine(
    app_slug="my_app",
    memory_service=memory_service,
    chat_history_collection=chat_collection,
    # Context Engineering configuration
    enable_context_engineering=True,      # Enable/disable all features (default: True)
    stm_raw_window=5,                      # Messages to keep raw before summarizing (default: 5)
    enable_entity_extraction=True,         # Enable entity fact extraction (default: True)
    enable_dynamic_persona=True,           # Enable dynamic persona adaptation (default: True)
)
```

## Usage Examples

### Basic Usage

```python
from mdb_engine.memory import ChatEngine

# Create engine with Context Engineering enabled (default)
chat_engine = ChatEngine(
    app_slug="my_app",
    memory_service=memory_service,
    chat_history_collection=chat_collection,
    llm_provider=llm_provider,
)

# Chat with automatic context engineering
result = await chat_engine.chat(
    user_id="user123",
    session_id="session456",
    user_query="How do I optimize Python code?",
    extract_facts=True,
)

# Access Context Engineering metadata
print(result["persona_used"])          # Persona document used
print(result["entity_facts"])          # Extracted entity facts
print(result["dynamic_instructions"])  # Dynamic persona instructions
print(result["stm_summary"])           # STM summary (if created)
```

### Setting Up Persona

```python
# PersonaEngine is automatically initialized in MemoryService
# You can update it via the memory service

memory_service = get_memory_service(app_slug="my_app", collection=collection)

# Update persona
if memory_service.persona_engine:
    memory_service.persona_engine.update_persona(
        role="Senior Python Architect",
        description="Expert in Python and system design. Concise and technical.",
        traits={
            "technical_focus": 0.9,
            "humor": 0.2,
            "formality": 0.8,
            "empathy": 0.6,
            "creativity": 0.4,
        },
    )
```

### Disabling Context Engineering

```python
# Disable all Context Engineering features
chat_engine = ChatEngine(
    app_slug="my_app",
    memory_service=memory_service,
    chat_history_collection=chat_collection,
    enable_context_engineering=False,  # Falls back to original behavior
)
```

## Benefits

1. **Token Efficiency**: Only relevant memories injected, not entire history
2. **Persona Consistency**: Persona layer ensures consistent identity across conversations
3. **Adaptive Behavior**: Persona adapts based on user context (expertise, emotion)
4. **Information Density**: Entity facts provide high-value, low-token context
5. **Context Curation**: Sliding window + summary prevents context bloat

## How It Works

### Step-by-Step Process

1. **User Query Arrives**: `chat_engine.chat()` is called
2. **Parallel Fetch**: LTM, STM, and Graph context are fetched in parallel
3. **Entity Extraction**: Entity facts are extracted from retrieved memories
4. **Persona Retrieval**: Persona is retrieved from `PersonaEngine` (if available)
5. **Dynamic Adaptation**: Dynamic persona instructions are generated based on context
6. **STM Optimization**: STM context is optimized with sliding window + summary
7. **Prompt Construction**: Context-engineered system prompt is assembled
8. **LLM Generation**: LLM generates response with optimized context
9. **Memory Storage**: New facts are extracted and stored to LTM

### Context Assembly Flow

```
User Query
    │
    ▼
┌─────────────────┐
│ Parallel Fetch  │ ──► LTM (vector search)
│                 │ ──► STM (chat history)
│                 │ ──► Graph (GraphRAG, optional)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Extract Entities│ ──► Name, OS, Language, Expertise
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Get Persona     │ ──► Role, Description, Traits
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Dynamic Adapt   │ ──► Generate instructions based on context
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Optimize STM    │ ──► Sliding window + summary
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Construct Prompt│ ──► Assemble all layers
└────────┬────────┘
         │
         ▼
    LLM Response
```

## Real-World Example: AI Chat App (sso-app-3)

The **AI Chat** app (`examples/advanced/sso-multi-app/apps/sso-app-3`) is a complete implementation of Context Engineering with a beautiful UI that displays Context Engineering metadata.

### Features Demonstrated

- ✅ **Context Engineering enabled** in `ChatEngine` initialization
- ✅ **Persona configuration** in `manifest.json` (Orby - AI Assistant)
- ✅ **Entity extraction** from biographical memories
- ✅ **Dynamic persona adaptation** based on user expertise
- ✅ **STM optimization** with sliding window + summary
- ✅ **UI display** of Context Engineering metadata (persona, entity facts, dynamic instructions)
- ✅ **Streaming support** with Context Engineering context
- ✅ **Memory encryption** (CSFLE) for secure storage

### Key Implementation Details

**ChatEngine Initialization** (`web.py`):
```python
chat_engine = ChatEngine(
    app_slug=APP_SLUG,
    memory_service=memory_service,
    chat_history_collection=chat_history_collection,
    stm_context_limit=10,
    ltm_search_limit=12,
    llm_provider=llm_provider,
    # Context Engineering configuration
    enable_context_engineering=True,
    stm_raw_window=5,
    enable_entity_extraction=True,
    enable_dynamic_persona=True,
)
```

**Persona Configuration** (`manifest.json`):
```json
{
  "memory_config": {
    "persona": {
      "enabled": true,
      "default_role": "Orby - AI Assistant",
      "default_description": "Orby is an intelligent AI assistant...",
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

**Response Handling**:
```python
result = await chat_engine.chat(
    user_id=user_id,
    session_id=cid,
    user_query=full_input,
    system_prompt=None,  # Let Context Engineering build it
)

# Extract Context Engineering metadata
persona_used = result.get("persona_used")
entity_facts = result.get("entity_facts", {})
dynamic_instructions = result.get("dynamic_instructions", "")
stm_summary = result.get("stm_summary")
```

**UI Display**: The app includes a Context Engineering panel that shows:
- 🎭 Persona role and description
- 📋 Extracted entity facts
- ⚙️ Dynamic persona instructions (collapsible)
- 📝 STM summary (collapsible)

### Running the Example

```bash
# With Docker Compose
cd examples/advanced/sso-multi-app
docker-compose up ai-chat

# Or with multi-app mounting
cd examples/advanced/sso-multi-app/apps
uvicorn multi_app_main:app --host 0.0.0.0 --port 8000
```

Access the app at http://localhost:8000/ai-chat (multi-app) or http://localhost:8003 (standalone).

### See the Example

- **Full Documentation**: [sso-app-3 README](https://github.com/ranfysvalle02/mdb-engine/blob/main/examples/advanced/sso-multi-app/apps/sso-app-3/README.md)
- **Source Code**: `examples/advanced/sso-multi-app/apps/sso-app-3/web.py`
- **Manifest**: `examples/advanced/sso-multi-app/apps/sso-app-3/manifest.json`
- **UI Template**: `examples/advanced/sso-multi-app/apps/sso-app-3/templates/conversation.html`

## Best Practices

1. **Set Persona Early**: Configure persona at app initialization for consistent identity
2. **Use Entity Facts**: Store biographical memories with `category="biographical"` for entity extraction
3. **Monitor Token Usage**: Adjust `stm_raw_window` based on your token budget
4. **Enable Dynamic Persona**: Let the system adapt to user expertise and emotion
5. **Review Context Metadata**: Check `result["persona_used"]`, `result["entity_facts"]` for debugging
6. **Study Real Examples**: See `sso-app-3` for a complete implementation with UI

## Related Documentation

- [Memory Service Guide](./MEMORY_SERVICE.md) - Complete memory service documentation
- [Memory Service Guide](./MEMORY_SERVICE.md) - Complete memory service guide
- [Memory System Complete Reference](./MEMORY_SYSTEM_COMPLETE.md) - Technical architecture details
- [Best Practices](./BEST_PRACTICES.md) - Production patterns

## Summary

Context Engineering transforms your Memory Service from a simple storage system into a **Context Engine** that optimizes the "present moment" for the LLM. By carefully curating context from Persona, Entity Facts, and Filtered Memory, you maximize information density while minimizing token usage, resulting in more accurate and contextually appropriate responses.
