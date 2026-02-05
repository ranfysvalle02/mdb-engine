# AI Chat App (sso-app-3) - Context Engineering Example

A beautiful AI chat application demonstrating **Context Engineering** and advanced memory service features, including Client-Side Field Level Encryption (CSFLE) for secure memory storage.

## Features

- **🎭 Context Engineering**: Dynamic context assembly using Persona, Entity Facts, Dynamic Persona, STM, and LTM
- **🔒 Memory Encryption**: Client-Side Field Level Encryption (CSFLE) for sensitive memories
- **💬 Real-time Streaming**: Server-Sent Events (SSE) for token-by-token AI responses
- **🧠 Cognitive Memory**: Advanced memory service with STM + LTM architecture
- **📊 Memory Explorer**: Interactive UI to explore memories, analytics, and knowledge graph
- **🔐 SSO Authentication**: Shared authentication across multi-app deployments

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
- **Graph**: Stored in `__kg` collection (if enabled)

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

- [Context Engineering Documentation](../../../../docs/CONTEXT_ENGINEERING.md) - Comprehensive guide
- [Memory Service Documentation](../../../../docs/MEMORY_SERVICE.md) - Memory service overview
- [Cognitive Architecture](../../../../docs/COGNITIVE_ARCHITECTURE.md) - STM + LTM architecture
- [CSFLE Setup Guide](../../../../docs/guides/CSFLE_SETUP.md) - Encryption setup
- [SSO Multi-App README](../README.md) - Multi-app deployment guide
