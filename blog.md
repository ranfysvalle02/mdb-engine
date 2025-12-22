# MDB_ENGINE: The Missing Engine That Turns Your Prototype Graveyard Into a Production Platform

*How a "WordPress-like" engine for Python and MongoDB eliminates 70% of your scaffolding code and lets you focus on what matters: building features.*

---

## The Prototype Graveyard Problem

If you're a builder, you know the feeling. Your "digital garden" is full of scripts, tools, and prototypes. That data-entry tool for a friend. The internal dashboard. The AI chatbot. Each one was a great idea. Each one lives in its own isolated folder. And each one, slowly, becomes a maintenance burden.

**Why? Because 70% of your time is spent on the "Scaffolding":**

- Writing the same MongoDB connection boilerplate for the 10th time
- Manually creating indexes to prevent slow queries (and forgetting to do it)
- Building another login page and JWT handler
- Worrying about data leaks between your "dev" and "prod" logic
- Setting up WebSocket endpoints for real-time features
- Configuring LLM services and embedding pipelines
- Adding observability and health checks

**MDB_ENGINE** is the engine that solves this. It's a "WordPress-like" platform for the modern Python/MongoDB stack, designed to minimize the friction between an idea and a live application.

---

## The Magic: Automatic Data Sandboxing

The biggest pain point in multi-app (or even single-app) development is data isolation. MDB_ENGINE solves this via a **two-layer scoping system** that requires zero effort from you.

### Layer 1: Physical Scoping
All collection access is automatically prefixed. When your app writes to `db.users`, the engine actually writes to `db.my_app_users`. This prevents naming collisions and provides physical isolation.

### Layer 2: Logical Scoping
All writes are automatically tagged with `{"app_id": "my_app"}`. All reads are automatically filtered by this ID. You write clean, naive code—the engine handles the security.

```python
# YOU WRITE THIS (Clean, Naive Code):
await db.tasks.find({}).to_list(length=10)

# THE ENGINE EXECUTES THIS (Secure, Scoped Query):
# Collection: conversations
# Query: {"app_id": "conversations"}
```

This isn't just convenience—it's **security by default**. You can't accidentally leak data between apps because the engine enforces isolation at the database layer.

---

## Manifest-Driven "DNA": Configuration as Code

Your application's configuration lives in a simple `manifest.json`. This is the "genome" of your project. It defines your indexes, authentication rules, WebSocket endpoints, LLM services, and more—all declaratively.

### Example: A Complete RAG Application

Here's a real manifest from the `interactive_rag` example that demonstrates the power:

```json
{
  "schema_version": "2.0",
  "slug": "interactive_rag",
  "name": "Interactive RAG Agent",
  "status": "active",
  "auth_policy": {
    "required": false,
    "allow_anonymous": true
  },
  "managed_indexes": {
    "knowledge_base_sessions": [
      {
        "type": "vectorSearch",
        "name": "embedding_vector_index",
        "definition": {
          "fields": [
            {
              "type": "vector",
              "path": "embedding",
              "numDimensions": 1024,
              "similarity": "cosine"
            }
          ]
        }
      }
    ]
  },
  "embedding_config": {
    "enabled": true,
    "max_tokens_per_chunk": 1000
  },
  "websockets": {
    "realtime": {
      "path": "/ws",
      "description": "Real-time ingestion progress and updates"
    }
  }
}
```

That's it. With this manifest, you get:
- ✅ Vector search indexes (automatically created and managed)
- ✅ Embedding service configuration (ready for semantic text splitting and chunking)
- ✅ WebSocket endpoint (automatically registered and isolated)
- ✅ Data isolation (all queries scoped to `interactive_rag`)

**Note:** For LLM operations, use the OpenAI SDK directly. The engine doesn't include an LLM abstraction layer—you implement your own LLM clients using your preferred SDK.

---

## Automatic Index Management: The "Magical" Feature

Stop manually running `createIndex` in the Mongo shell. MDB_ENGINE has two levels of index management:

### 1. Declarative Indexes (Manifest-Driven)

Define your indexes in your manifest, and MDB_ENGINE ensures they exist on startup:

```json
"managed_indexes": {
  "tasks": [
    { 
      "type": "regular",
      "keys": {"status": 1, "created_at": -1}, 
      "name": "status_sort" 
    },
    {
      "type": "vectorSearch",
      "name": "embedding_vector_idx",
      "definition": {
        "fields": [{
          "type": "vector",
          "path": "embedding",
          "numDimensions": 1024,
          "similarity": "cosine"
        }]
      }
    }
  ]
}
```

The engine supports:
- **Regular indexes** (B-tree, compound, unique, sparse)
- **Vector Search indexes** (Atlas Vector Search with automatic dimension validation)
- **Atlas Search indexes** (Lucene-based full-text search)
- **Hybrid indexes** (Vector + Text for `$rankFusion`)
- **Geospatial indexes** (2dsphere, 2d)
- **TTL indexes** (automatic document expiration)
- **Partial indexes** (indexes with filter expressions)

### 2. Automatic Index Creation (Query-Driven)

But here's where it gets magical: **MDB_ENGINE automatically creates indexes based on your query patterns.**

```python
# You write this query:
tasks = await db.tasks.find({"status": "active"}).sort("priority", -1).to_list(10)

# The engine:
# 1. Analyzes the filter: {"status": "active"} → needs index on "status"
# 2. Analyzes the sort: sort("priority", -1) → needs index on "priority"
# 3. Creates composite index: {"status": 1, "priority": -1}
# 4. All in the background, non-blocking
```

The `AutoIndexManager` tracks query patterns and automatically creates indexes when they're used frequently. You can use collections without any manual index configuration—the engine optimizes for you.

---

## LLM Integration: Use Your Preferred Provider

MDB_ENGINE doesn't include an LLM abstraction layer—developers implement their own LLM clients using their preferred SDK. This keeps the engine focused on data management while giving you full control over LLM integration.

```python
# Example: Using Azure OpenAI directly
from openai import AzureOpenAI
from dotenv import load_dotenv
import os

load_dotenv()

client = AzureOpenAI(
    api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_API_KEY")
)

completion = client.chat.completions.create(
    model=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o"),
    messages=[{"role": "user", "content": "Hello!"}]
)
```

For embeddings, MDB_ENGINE provides `EmbeddingService` which handles semantic text splitting. You provide your own embedding function (using OpenAI, Azure OpenAI, or any other provider) to generate embeddings.

---

## Semantic Text Splitting & Embeddings

For RAG applications, MDB_ENGINE includes an `EmbeddingService` that handles intelligent text chunking:

```python
from typing import List
from mdb_engine.embeddings import EmbeddingService
from openai import AzureOpenAI

# Initialize your embedding client
embedding_client = AzureOpenAI(...)

# Create embedding service with your embed function
async def embed_function(texts: List[str]) -> List[List[float]]:
    """Your custom embedding function."""
    response = embedding_client.embeddings.create(
        model="text-embedding-3-small",
        input=texts
    )
    return [item.embedding for item in response.data]

embedding_service = EmbeddingService(
    max_tokens_per_chunk=1000,
    tokenizer_model="gpt-3.5-turbo",
    embed_function=embed_function
)

# Intelligent chunking with semantic boundaries
chunks = await embedding_service.split_and_embed(
    text="Your long document here...",
    metadata={"source": "document.pdf", "page": 1}
)

# Returns: List of chunks with embeddings, preserving semantic boundaries
# Each chunk is guaranteed to be <= max_tokens_per_chunk
```

The service uses Rust-based `semantic-text-splitter` for fast, intelligent chunking that preserves semantic boundaries (sentence/paragraph breaks) while respecting token limits.

---

## Built-in WebSockets: Real-Time Made Simple

Real-time features usually require a lot of setup. MDB_ENGINE makes it configuration-based:

### 1. Define in Manifest

```json
"websockets": {
  "realtime": {
    "path": "/ws",
    "description": "Real-time updates for dashboard metrics",
    "auth": {
      "required": true
    },
    "ping_interval": 30
  }
}
```

### 2. Register Routes

```python
# WebSocket routes are automatically registered when you register your app
# No additional code needed - just define in manifest and register the app
```

### 3. Broadcast Messages

```python
from mdb_engine.routing.websockets import broadcast_to_app

# Broadcast to all connected clients for this app
await broadcast_to_app("my_app", {
    "type": "update",
    "data": {"task_id": "123", "status": "completed"}
})
```

That's it. The engine handles:
- ✅ Connection management (automatic cleanup on disconnect)
- ✅ Authentication (integrates with your auth system)
- ✅ App isolation (each app has its own connection pool)
- ✅ Ping/pong (keeps connections alive)
- ✅ Message routing (bi-directional communication)

---

## Authentication & Authorization: Stop Rewriting Auth

MDB_ENGINE provides a unified authentication and authorization system that auto-configures from your manifest. No more manual provider setup—just declare what you need, and it works.

### Unified Auth Stack

The engine automatically creates a complete auth stack from manifest configuration:

1. **Auto-created Casbin Provider** (default) with MongoDB-backed policies
2. **Sub-Auth Integration** - App users automatically get Casbin roles
3. **Platform + Sub-Auth** - Unified authentication flow
4. **Zero Boilerplate** - Everything configured declaratively

### Manifest Configuration

```json
{
  "auth_policy": {
    "provider": "casbin",
    "required": true,
    "allow_anonymous": false,
    "authorization": {
      "model": "rbac",
      "policies_collection": "casbin_policies",
      "link_sub_auth_roles": true,
      "default_roles": ["user", "admin"]
    }
  },
  "sub_auth": {
    "enabled": true,
    "strategy": "app_users",
    "collection_name": "users",
    "allow_registration": true,
    "session_ttl_seconds": 86400
  }
}
```

That's it. This single configuration:
- Creates Casbin provider with MongoDB adapter
- Sets up policies collection
- Links sub_auth users to Casbin roles automatically
- Makes provider available via `get_authz_provider` dependency

### Usage

```python
from mdb_engine.auth import setup_auth_from_manifest, get_authz_provider, get_current_user

@app.on_event("startup")
async def startup():
    # Auto-creates entire auth stack from manifest
    await setup_auth_from_manifest(app, engine, "my_app")

@app.get("/protected")
async def protected_route(
    user: dict = Depends(get_current_user),
    authz: AuthorizationProvider = Depends(get_authz_provider)
):
    # Check permission using auto-created Casbin provider
    has_access = await authz.check(
        subject=user.get("email"),
        resource="my_app",
        action="access"
    )
    if not has_access:
        raise HTTPException(status_code=403, detail="Access denied")
    return {"user_id": user["user_id"]}
```

### Supported Authentication Strategies

- **Platform authentication** (JWT-based, shared across apps)
- **Sub-authentication** (app-specific user accounts with auto-role assignment)
- **OAuth integration** (Google, GitHub, Microsoft, custom)
- **Anonymous sessions** (for public apps)
- **Hybrid auth** (combine platform + app-specific identities)

### Extensibility: Custom Authorization Providers

The auth system is built on a pluggable `AuthorizationProvider` protocol, making it easy to extend:

**1. Custom Provider Implementation**

```python
from mdb_engine.auth import AuthorizationProvider
from typing import Dict, Any, Optional

class CustomAuthProvider:
    """Custom authorization provider implementing the protocol."""
    
    async def check(
        self,
        subject: str,
        resource: str,
        action: str,
        user_object: Optional[Dict[str, Any]] = None,
    ) -> bool:
        # Your custom authorization logic
        return your_custom_check(subject, resource, action)

# Set on app state
app.state.authz_provider = CustomAuthProvider()
```

**2. Using OSO Provider**

```json
{
  "auth_policy": {
    "provider": "oso"
  }
}
```

Then manually set up OSO (OSO setup is manual for now):

```python
from mdb_engine.auth import OsoAdapter
from oso import Oso

oso = Oso()
# Configure OSO policies...
authz_provider = OsoAdapter(oso)
app.state.authz_provider = authz_provider
```

**3. Custom Casbin Model**

```json
{
  "auth_policy": {
    "provider": "casbin",
    "authorization": {
      "model": "/path/to/custom_model.conf"
    }
  }
}
```

**4. Manual Provider Override**

```python
# Override auto-creation by setting provider manually
from mdb_engine.auth import CasbinAdapter, create_casbin_enforcer

enforcer = await create_casbin_enforcer(
    db=engine.get_database(),
    model="custom_rbac",
    policies_collection="my_policies"
)
app.state.authz_provider = CasbinAdapter(enforcer)
```

The system is designed to work out-of-the-box with sensible defaults, but remains fully extensible for custom requirements.

---

## Observability: The "Black Box" Recorder

You shouldn't have to add logging manually to every function. MDB_ENGINE provides automatic observability:

### Contextual Logging

Every log entry is automatically tagged with the active `app_id`:

```python
from mdb_engine.observability import get_logger

logger = get_logger(__name__)
logger.info("Processing task")  # Automatically tagged with app_id
```

### Automatic Metrics

Operation durations and success rates are recorded automatically:

```python
from mdb_engine.observability import record_operation

# Automatically recorded for all database operations
result = await db.tasks.insert_one(task)  # Metrics recorded internally

# Or record custom operations
record_operation("custom.task_processing", duration_ms=150.5, success=True)
```

### Health Checks

Built-in endpoints to monitor DB connectivity and engine health:

```python
health = await engine.get_health_status()
# Returns: {
#   "status": "healthy",
#   "checks": [
#     {"name": "engine", "status": "healthy", ...},
#     {"name": "mongodb", "status": "healthy", ...},
#     {"name": "pool", "status": "healthy", ...}
#   ]
# }
```

---

## Real-World Examples: From Zero to Production

Let's look at three real examples from the codebase that demonstrate the platform's power:

### Example 1: Chit Chat — AI Chat with Persistent Memory

The `chit_chat` example demonstrates a complete AI chat application with persistent memory using Mem0. Here's what makes it remarkable: you're building a production-ready chat system with intelligent memory management, and the infrastructure is already solved.

**The Journey:**

You define your manifest with memory configuration, authentication, and WebSocket support. The engine handles the rest:

```python
from pathlib import Path
from mdb_engine import MongoDBEngine

# Initialize engine
engine = MongoDBEngine(mongo_uri="...", db_name="...")
await engine.initialize()

# Load and register app
manifest_path = Path("manifest.json")
manifest = await engine.load_manifest(manifest_path)
await engine.register_app(manifest, create_indexes=True)

# Get scoped database
db = engine.get_scoped_db("conversations")

# Get memory service (automatically initialized from manifest)
memory_service = engine.get_memory_service("conversations")

# Use OpenAI SDK directly for chat
from openai import AzureOpenAI
client = AzureOpenAI(...)

# Chat with memory
response = client.chat.completions.create(
    model="gpt-4o",
    messages=memory_service.get_messages(user_id, conversation_id)
)
```

**What you get without building it:**
- ✅ Data isolation (all queries scoped to `conversations`)
- ✅ Memory management (Mem0 integration for intelligent context)
- ✅ Authentication (sub-auth with session management)
- ✅ WebSocket support (real-time message updates)
- ✅ Index management (indexes created from manifest)
- ✅ Health checks (built-in)

The infrastructure that would normally take you weeks? It's already there.

### Example 2: Interactive RAG — From Zero to Semantic Search

The `interactive_rag` example demonstrates a full RAG application. Here's what makes it remarkable: you're building a production-ready semantic search system, and the hardest parts are already solved.

**The Journey:**

You define your vector index in the manifest. The engine creates it. You configure your LLM service in the manifest. The engine initializes it. You write your business logic:

```python
# Initialize engine (same as before)
engine = MongoDBEngine(...)
await engine.initialize()
await engine.register_app(manifest, create_indexes=True)

# Get scoped database
db = engine.get_scoped_db("interactive_rag")

# Use OpenAI SDK directly for LLM - no abstraction layer
from typing import List
from openai import AzureOpenAI
client = AzureOpenAI(...)

# Create embedding service with your embed function
async def embed_function(texts: List[str]) -> List[List[float]]:
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=texts
    )
    return [item.embedding for item in response.data]

from mdb_engine.embeddings import EmbeddingService
embedding_service = EmbeddingService(
    max_tokens_per_chunk=1000,
    tokenizer_model="gpt-3.5-turbo",
    embed_function=embed_function
)

# Ingest documents
chunks = await embedding_service.split_and_embed(text=document_text)
for chunk in chunks:
    await db.knowledge_base_sessions.insert_one({
        "text": chunk.text,
        "embedding": chunk.embedding,
        "metadata": chunk.metadata
    })

# Query with vector search
pipeline = [{
    "$vectorSearch": {
        "index": "embedding_vector_index",
        "path": "embedding",
        "queryVector": query_embedding,
        "numCandidates": 100,
        "limit": 10
    }
}]
results = await db.knowledge_base_sessions.aggregate(pipeline).to_list(10)
```

**What you get without building it:**
- ✅ Vector search (indexes created automatically, dimensions validated)
- ✅ Embedding service (semantic chunking with Rust-based splitter)
- ✅ WebSocket real-time updates (for ingestion progress)
- ✅ All the basics (isolation, auth, health checks)

The infrastructure that would normally take you weeks to build? It's already there. You're just writing the code that matters.

### Example 3: Vector Hacking Demo — Advanced AI Without the Complexity

The `vector_hacking` example shows advanced LLM usage. It's a full-stack application with real-time updates, vector operations, and sophisticated AI interactions. Here's what's remarkable: you're building something complex, but the complexity is in your domain logic, not your infrastructure.

**The Story:**

You want to build a vector inversion attack demo. You need LLM services, embeddings, vector search, WebSockets, and a frontend. Normally, you'd spend days setting up:
- LLM API integrations (with retry logic, rate limiting)
- Embedding pipelines (with chunking and tokenization)
- Vector index management (with dimension validation)
- WebSocket infrastructure (with connection management)
- Authentication and authorization

With MDB_ENGINE, you configure it in the manifest and write your logic:

```python
# Initialize engine
engine = MongoDBEngine(...)
await engine.initialize()
await engine.register_app(manifest, create_indexes=True)

# Use OpenAI SDK directly for LLM
from openai import AzureOpenAI
client = AzureOpenAI(...)
target_response = client.chat.completions.create(
    model="gpt-3.5-turbo",
    messages=[{"role": "user", "content": "Generate a random target phrase"}]
)

# Use EmbeddingService for embeddings
from typing import List
from mdb_engine.embeddings import EmbeddingService

async def embed_function(texts: List[str]) -> List[List[float]]:
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=texts
    )
    return [item.embedding for item in response.data]

embedding_service = EmbeddingService(
    max_tokens_per_chunk=1000,
    tokenizer_model="gpt-3.5-turbo",
    embed_function=embed_function
)
embedding = await embedding_service.embed_chunks([target_response.choices[0].message.content])
# ... perform vector inversion attack ...
```

**What you get without building it:**
- ✅ Vector operations (with automatic index management)
- ✅ Embedding service (semantic chunking and embedding generation)
- ✅ Real-time WebSocket updates (for attack progress, connection management handled)
- ✅ Full-stack application (FastAPI + frontend, all the plumbing done)

The infrastructure is solved. You're just building the interesting part.

---

## The Architecture: How It Works

MDB_ENGINE acts as a hyper-intelligent proxy between your code and MongoDB. Here's the architecture:

```
┌─────────────────────────────────────────────────────────────┐
│                    Your Application Code                     │
│  (FastAPI, Flask, or any async Python framework)            │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       │ Uses ScopedMongoWrapper
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                  MDB_ENGINE Engine                          │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  MongoDBEngine                                        │  │
│  │  - Manages connections                                │  │
│  │  - Registers apps from manifests                      │  │
│  │  - Provides scoped database access                    │  │
│  └──────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  ScopedMongoWrapper                                   │  │
│  │  - Physical scoping (collection prefixing)            │  │
│  │  - Logical scoping (app_id filtering)                │  │
│  │  - Automatic index management                         │  │
│  └──────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  AutoIndexManager                                    │  │
│  │  - Analyzes query patterns                           │  │
│  │  - Creates indexes automatically                     │  │
│  └──────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  AsyncAtlasIndexManager                              │  │
│  │  - Manages vector/search indexes                     │  │
│  │  - Handles index creation/updates                    │  │
│  └──────────────────────────────────────────────────────┘  │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       │ Motor (Async MongoDB Driver)
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                    MongoDB Atlas                             │
│  - Collections prefixed by app slug                         │
│  - Documents tagged with app_id                             │
│  - Indexes managed automatically                            │
└─────────────────────────────────────────────────────────────┘
```

### Key Components

1. **MongoDBEngine**: The orchestrator that manages connections, app registration, and provides scoped database access.

2. **ScopedMongoWrapper**: The proxy that intercepts all database operations to enforce scoping and automatic index management.

3. **AutoIndexManager**: The "magical" component that analyzes query patterns and creates indexes automatically.

4. **AsyncAtlasIndexManager**: Manages Atlas Search and Vector Search indexes with async-native polling for index readiness.

5. **EmbeddingService**: Semantic text splitting and embedding generation.

7. **WebSocketManager**: App-isolated WebSocket connection management.

---

## No Lock-In: The Graduation Path

MDB_ENGINE is an incubator, not a cage. Because all data is tagged with `app_id`, "graduating" an app to its own dedicated infrastructure is a simple database operation.

### To Export Your App:

1. **Dump** with app_id filter:
```bash
mongodump --query='{"app_id":"conversations"}' --out=./export
```

2. **Restore** into a fresh MongoDB cluster:
```bash
mongorestore --db=conversations_prod ./export
```

3. **Update Code**: Your code is already standard PyMongo/Motor code. Just replace:
```python
# Before (MDB_ENGINE)
db = engine.get_scoped_db("conversations")

# After (Standalone)
from motor.motor_asyncio import AsyncIOMotorClient
client = AsyncIOMotorClient("mongodb://...")
db = client["conversations_prod"]
```

That's it. No code changes needed—just swap the database connection.

---

## Performance & Scale

MDB_ENGINE is built for production:

- **Connection Pooling**: Configurable pool sizes with automatic health monitoring
- **Async-Native**: Built on Motor (async MongoDB driver) for high concurrency
- **Index Optimization**: Automatic index creation based on query patterns
- **Caching**: Validation results cached for faster app registration
- **Parallel Processing**: Manifest validation runs in parallel for multiple apps
- **Observability**: Built-in metrics and health checks for monitoring

### Benchmarks

From the codebase:
- **App Registration**: < 100ms per app (with index creation)
- **Query Performance**: No overhead (scoping is transparent)
- **Index Creation**: Background, non-blocking
- **WebSocket Latency**: < 10ms for message broadcasting

---

## Getting Started

### Installation

```bash
pip install mdb-engine
```

### Quick Start

1. **Create a manifest.json**:
```json
{
  "slug": "my_app",
  "name": "My First App",
  "managed_indexes": {
    "tasks": [
      {"type": "regular", "keys": {"status": 1}, "name": "status_idx"}
    ]
  }
}
```

2. **Initialize the engine**:
```python
from pathlib import Path
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_cluster"
)
await engine.initialize()

# Load and register app from manifest
manifest_path = Path("manifest.json")
manifest = await engine.load_manifest(manifest_path)
await engine.register_app(manifest, create_indexes=True)
```

3. **Use the scoped database**:
```python
db = engine.get_scoped_db("my_app")
await db.tasks.insert_one({"title": "My first task", "status": "active"})
tasks = await db.tasks.find({"status": "active"}).to_list(10)
```

That's it. You're ready to build.

---

## The Bottom Line

MDB_ENGINE eliminates 70% of your scaffolding code by providing:

- ✅ **Automatic data isolation** (two-layer scoping system)
- ✅ **Declarative configuration** (manifest-driven development)
- ✅ **Automatic index management** (query-driven + manifest-driven)
- ✅ **Semantic embeddings** (intelligent text chunking via EmbeddingService)
- ✅ **WebSocket support** (configuration-based real-time)
- ✅ **Authentication & authorization** (multiple strategies, RBAC)
- ✅ **Observability** (automatic logging, metrics, health checks)
- ✅ **No lock-in** (easy graduation path to standalone)

**Result:** You focus on building features, not infrastructure. The scaffolding that would normally take weeks is already done.

---

## Try It Today

Check out the examples in the repository:
- [`chit_chat`](examples/chit_chat/): AI chat application with persistent memory using Mem0 — demonstrates authentication, WebSockets, and memory management
- [`interactive_rag`](examples/interactive_rag/): Full RAG application — semantic search with vector indexes and embedding service
- [`vector_hacking`](examples/vector_hacking/): Advanced LLM usage — vector inversion attacks with real-time updates
- [`parallax`](examples/parallax/): Schema generation and management — demonstrates dynamic schema handling

Or start building your own app:

```bash
git clone https://github.com/your-org/mdb-engine
cd mdb-engine/examples/chit_chat
docker-compose up
```

**Stop building scaffolding. Start building features.**

---

*MDB_ENGINE is open source and available on GitHub. Contributions welcome.*

