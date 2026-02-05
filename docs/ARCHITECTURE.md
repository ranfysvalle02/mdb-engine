# MDB Engine Architecture

## Table of Contents

1. [Overview](#overview)
2. [Core Components](#core-components)
3. [App Authentication Architecture](#app-authentication-architecture)
4. [Data Flow](#data-flow)
5. [Security Boundaries](#security-boundaries)

## Overview

MDB_ENGINE is a MongoDB runtime engine that provides secure, multi-tenant database access with automatic data isolation, cross-app access control, and app-level authentication.

## Core Components

### MongoDBEngine

The main orchestration engine that manages:
- Database connections
- App registration
- Authentication/authorization
- Index management
- Resource lifecycle

### ScopedMongoWrapper

Provides automatic data isolation by:
- Injecting `app_id` filters into queries
- Validating cross-app access
- Enforcing write scopes

### AppRegistrationManager

Manages app lifecycle:
- Manifest validation
- App configuration storage
- Index creation
- Service initialization

## App Authentication Architecture

### System Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    Application Layer                          │
│  (ClickTracker, Dashboard, etc.)                            │
└──────────────────────┬───────────────────────────────────────┘
                        │ Provides app_token
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                  MongoDBEngine                                │
│  • Verifies app_token                                         │
│  • Validates read_scopes                                      │
│  • Manages app secrets                                        │
└──────┬───────────────────────────────────────────────────────┘
       │
       ├──► AppSecretsManager
       │    • Stores encrypted secrets in _mdb_engine_app_secrets
       │    • Verifies tokens (constant-time comparison)
       │    • Rotates secrets
       │
       └──► EnvelopeEncryptionService
            • Encrypts/decrypts secrets using envelope encryption
            • Manages master key
            • Generates DEKs
```

### Component Interactions

#### Registration Flow

```
Application
    │
    ├─► register_app(manifest)
    │
    ▼
MongoDBEngine
    │
    ├─► Extract data_access.read_scopes
    │   └─► Store in _app_read_scopes mapping
    │
    ├─► Generate app secret (secrets.token_urlsafe(32))
    │
    ├─► AppSecretsManager.store_app_secret()
    │   │
    │   ├─► EnvelopeEncryptionService.encrypt_secret()
    │   │   ├─► Generate DEK
    │   │   ├─► Encrypt secret with DEK
    │   │   └─► Encrypt DEK with master key
    │   │
    │   └─► Store in _mdb_engine_app_secrets collection
    │
    └─► Return success
```

#### Runtime Access Flow

```
Application
    │
    ├─► get_scoped_db(app_slug, app_token)
    │
    ▼
MongoDBEngine
    │
    ├─► AppSecretsManager.verify_app_secret()
    │   │
    │   ├─► Read encrypted secret from _mdb_engine_app_secrets
    │   │
    │   ├─► EnvelopeEncryptionService.decrypt_secret()
    │   │   ├─► Decrypt DEK with master key
    │   │   └─► Decrypt secret with DEK
    │   │
    │   └─► Compare tokens (constant-time)
    │       ├─► Match → Continue
    │       └─► Mismatch → Raise ValueError
    │
    ├─► Validate read_scopes against manifest authorization
    │   ├─► Authorized → Continue
    │   └─► Unauthorized → Raise ValueError
    │
    └─► Return ScopedMongoWrapper
```

### Data Flow

#### Envelope Encryption Flow

```
Master Key (MK)
    │
    │ Encrypts
    ▼
Data Encryption Key (DEK) ──► Encrypted DEK (stored)
    │
    │ Encrypts
    ▼
App Secret ──► Encrypted Secret (stored)
```

**Storage Format:**
```json
{
  "_id": "app_slug",
  "encrypted_secret": "<base64-encoded>",
  "encrypted_dek": "<base64-encoded>",
  "algorithm": "AES-256-GCM",
  "created_at": ISODate(...),
  "updated_at": ISODate(...),
  "rotation_count": 0
}
```

#### Cross-App Access Flow

```
Application A
    │
    ├─► get_scoped_db("app_a", app_token="secret_a", read_scopes=["app_a", "app_b"])
    │
    ▼
MongoDBEngine
    │
    ├─► Verify app_token (app_a)
    │
    ├─► Validate read_scopes
    │   └─► Check: ["app_a", "app_b"] ⊆ manifest.read_scopes
    │
    └─► Return ScopedMongoWrapper
        │
        ├─► Query: app_b_collection.find({})
        │   │
        │   ├─► Extract app_slug: "app_b"
        │   │
        │   ├─► Validate: "app_b" in read_scopes? ✅
        │   │
        │   └─► Inject filter: {"app_id": {"$in": ["app_a", "app_b"]}}
        │
        └─► Execute query
```

### Security Boundaries

#### Collection Access Control

```
┌─────────────────────────────────────────┐
│  _mdb_engine_app_secrets                 │
│  • Only accessible via raw MongoDB       │
│  • Never accessible via scoped wrapper   │
│  • Only MongoDBEngine can access         │
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│  apps_config                             │
│  • Engine internal                       │
│  • Reserved collection name              │
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│  app_*_collection                        │
│  • Accessible via scoped wrapper         │
│  • Automatic app_id filtering            │
│  • Cross-app access validated            │
└─────────────────────────────────────────┘
```

#### Master Key Security

- **Storage**: Environment variable (`MDB_ENGINE_MASTER_KEY`)
- **Format**: Base64-encoded 32-byte key (256 bits)
- **Access**: Only `EnvelopeEncryptionService` can access
- **Rotation**: Planned feature (re-encrypt all DEKs)

#### App Secret Security

- **Generation**: Random 256-bit secret (`secrets.token_urlsafe(32)`)
- **Storage**: Encrypted in `_mdb_engine_app_secrets`
- **Verification**: Constant-time comparison (`secrets.compare_digest()`)
- **Rotation**: Via `AppSecretsManager.rotate_app_secret()`

### Integration Points

#### OSO Authorization

```
Application
    │
    ├─► FastAPI endpoint
    │
    ├─► get_current_user() dependency
    │   └─► OSO checks user authentication
    │
    ├─► authz.check() dependency
    │   └─► OSO checks authorization (read/write/admin)
    │
    └─► get_scoped_db(app_token)
        └─► Engine verifies app identity
```

**Layered Security:**
1. **OSO**: User-level authorization (who can do what)
2. **App Authentication**: App-level identity verification
3. **Manifest Config**: Cross-app access authorization
4. **Scoped Wrapper**: Runtime scope validation

## Multi-App Mounting Architecture

MDB-ENGINE supports mounting multiple FastAPI apps under a single parent app, enabling true multi-app convenience for single-deployment scenarios (e.g., Render.com, Railway).

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────┐
│              Parent FastAPI App (Port 8000)              │
│  • Unified health check (/health)                      │
│  • Shared middleware (CORS, rate limiting)            │
│  • Shared MongoDBEngine instance                        │
└──────────────┬──────────────────────────────────────────┘
               │
       ┌───────┼───────┐
       │       │       │
       ▼       ▼       ▼
┌──────────┐ ┌──────────┐ ┌──────────┐
│ Auth Hub │ │ pwd-zero │ │   FLUX   │
│ /auth-hub│ │/pwd-zero │ │  /flux   │
└────┬─────┘ └────┬─────┘ └────┬─────┘
     │            │            │
     └────────────┼────────────┘
                  │
                  ▼
         ┌─────────────────┐
         │ MongoDBEngine   │
         │ (Shared)        │
         └────────┬────────┘
                  │
                  ▼
         ┌─────────────────┐
         │ SharedUserPool  │
         │ (SSO Support)   │
         └─────────────────┘
```

### Key Components

**Parent App:**
- Created via `engine.create_multi_app()`
- Manages engine lifecycle (initialization/shutdown)
- Provides unified `/health` endpoint
- Handles shared middleware (CORS, rate limiting)
- Mounts child apps at path prefixes

**Child Apps:**
- Created via `engine.create_app()` with `is_sub_app=True`
- Share parent's engine instance
- Share parent's lifespan (no separate initialization)
- Maintain own routes, middleware, and state
- Accessible at configured path prefixes

**Shared Resources:**
- Single MongoDBEngine instance
- Single MongoDB connection pool
- SharedUserPool (if any app uses shared auth)
- Unified health monitoring

### Configuration

**Programmatic:**

```python
app = engine.create_multi_app(
    apps=[
        {"slug": "auth-hub", "manifest": Path("./auth-hub/manifest.json"), "path_prefix": "/auth-hub"},
        {"slug": "dashboard", "manifest": Path("./dashboard/manifest.json"), "path_prefix": "/dashboard"}
    ]
)
```

**Manifest-Based:**

```json
{
  "multi_app": {
    "enabled": true,
    "apps": [
      {"slug": "auth-hub", "manifest": "./auth-hub/manifest.json", "path_prefix": "/auth-hub"}
    ]
  }
}
```

### Path Prefix Validation

The engine validates path prefixes to ensure:
- All prefixes start with `/`
- No prefix conflicts (one cannot be a prefix of another)
- No conflicts with reserved paths (`/health`, `/docs`, `/openapi.json`)
- All prefixes are unique

### Shared Auth in Multi-App Context

When using shared auth (`auth.mode: "shared"`):
- SharedUserPool is initialized once at parent level
- All child apps using shared auth reference the same user pool
- SSO works seamlessly across mounted apps
- Tokens validated consistently across all apps

### Benefits

1. **Single Deployment**: Deploy multiple apps as one service
2. **Resource Efficiency**: Shared engine and connection pool
3. **SSO Support**: Shared auth works seamlessly
4. **Unified Monitoring**: Single health check endpoint
5. **Easy Configuration**: Declarative via manifest or programmatic

## Service Architecture & Modularity

MDB-Engine is designed with modularity in mind. Each service can be used standalone or composed together, with clear dependency contracts defined via Python Protocols.

### Service Dependency Matrix

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Service Dependency Matrix                             │
├────────────────────────┬─────────────────────────────────────────────────────┤
│ Service                │ Required Dependencies      │ Optional Dependencies  │
├────────────────────────┼──────────────────────────────┼──────────────────────┤
│ LLMService             │ litellm, API keys          │ (none)                │
│ EmbeddingService       │ semantic-text-splitter,    │ (none)                │
│                        │ openai/voyageai            │                       │
│ GraphService           │ MongoDB Collection         │ LLMService, Embedding │
│ CognitiveMemoryService │ MongoDB Collection,        │ GraphService          │
│                        │ EmbeddingService           │                       │
└────────────────────────┴──────────────────────────────┴──────────────────────┘
```

### Protocol-Based Architecture

Services implement Python Protocols (`mdb_engine.core.protocols`) for:
- **Type Safety**: Full IDE autocompletion and type checking
- **Testability**: Easy mocking with any Protocol-conforming object
- **Flexibility**: Swap implementations without changing consuming code

**Available Protocols:**

| Protocol                  | Description                                    |
|---------------------------|------------------------------------------------|
| `LLMServiceProtocol`      | Chat completion interface (sync/async)         |
| `EmbeddingServiceProtocol`| Text embedding generation                      |
| `TextChunkerProtocol`     | Semantic text chunking                         |
| `GraphServiceProtocol`    | Knowledge graph operations                     |
| `MemoryServiceProtocol`   | Long-term memory with semantic search          |

### Standalone Usage Patterns

#### Using LLMService Standalone

```python
from mdb_engine.llm import LLMService

# LLMService only requires litellm and API keys
llm = LLMService(config={"default_model": "openai/gpt-4o"})

# Sync call
response = llm.chat_completion_sync([
    {"role": "user", "content": "Hello!"}
])
print(response)

# Async call
import asyncio
response = asyncio.run(llm.chat_completion([
    {"role": "user", "content": "Hello!"}
]))
```

#### Using EmbeddingService Standalone

```python
from mdb_engine.embeddings import EmbeddingProvider

# EmbeddingService only requires openai/voyageai packages
embeddings = EmbeddingProvider(config={
    "default_embedding_model": "text-embedding-3-small"
})

# Generate embeddings
vectors = asyncio.run(embeddings.embed(["Hello world", "Test text"]))
```

#### Using GraphService Standalone

```python
from mdb_engine.graph import GraphService
from pymongo import MongoClient

# GraphService needs MongoDB collection, optionally LLM/Embeddings
client = MongoClient("mongodb://localhost:27017")
collection = client["mydb"]["graph_nodes"]

graph = GraphService(
    app_slug="my_app",
    config={"enabled": True},
    collection=collection,
)

# Create nodes and edges
graph.upsert_node("person:alice", "person", "Alice", {"age": 30})
graph.upsert_node("interest:hiking", "interest", "Hiking")
graph.add_edge("person:alice", "ENJOYS", "interest:hiking")

# Traverse the graph
results = graph.traverse("person:alice", max_depth=2)
```

#### Using CognitiveMemoryService with Dependency Injection

```python
from mdb_engine.memory import get_memory_service
from mdb_engine.llm import LLMService
from mdb_engine.embeddings import EmbeddingProvider
from pymongo import MongoClient

# Create services independently
llm = LLMService(config={"default_model": "gpt-4o"})
embeddings = EmbeddingProvider()

# Inject services into memory service
client = MongoClient("mongodb://localhost:27017")
collection = client["mydb"]["memories"]

memory = get_memory_service(
    app_slug="my_app",
    collection=collection,
    config={"enabled": True, "infer": True},
    llm_service=llm,           # Inject LLM service
    embedding_service=embeddings,  # Inject embedding service
)

# Memory service uses injected services instead of creating its own
memories = memory.add("User said they live in Seattle", user_id="user123")
```

### Dependency Injection in ServiceInitializer

The `ServiceInitializer` manages service lifecycle for MDB-Engine apps:

```python
# ServiceInitializer stores services with Protocol type hints
self._graph_services: dict[str, GraphServiceProtocol] = {}
self._memory_services: dict[str, MemoryServiceProtocol] = {}

# Get typed services
graph = service_initializer.get_graph_service("my_app")  # -> GraphServiceProtocol | None
memory = service_initializer.get_memory_service("my_app")  # -> MemoryServiceProtocol | None
```

### Testing with Protocols

Protocols enable easy mocking:

```python
from mdb_engine.core.protocols import LLMServiceProtocol

class MockLLMService:
    """Mock LLM service for testing."""
    
    async def chat_completion(self, messages, **kwargs):
        return "Mocked response"
    
    def chat_completion_sync(self, messages, **kwargs):
        return "Mocked response"

# MockLLMService implements LLMServiceProtocol
assert isinstance(MockLLMService(), LLMServiceProtocol)  # True

# Use in tests
memory = get_memory_service(
    app_slug="test_app",
    collection=mock_collection,
    llm_service=MockLLMService(),
)
```

## Related Documentation

- [App Authentication Guide](APP_AUTHENTICATION.md) - Detailed authentication guide
- [Security Guide](SECURITY.md) - Overall security architecture
- [Authorization Guide](AUTHZ.md) - OSO/Casbin authorization

