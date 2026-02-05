# MDB-Engine Deep Analysis

**Comprehensive Implementation Analysis and Architecture Documentation**

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Architecture Overview](#architecture-overview)
3. [Core Components Deep Dive](#core-components-deep-dive)
4. [Data Scoping and Isolation](#data-scoping-and-isolation)
5. [Manifest System](#manifest-system)
6. [Authentication & Authorization](#authentication--authorization)
7. [Index Management](#index-management)
8. [Memory Service Architecture](#memory-service-architecture)
9. [Service Initialization](#service-initialization)
10. [Dependency Injection System](#dependency-injection-system)
11. [Connection Management](#connection-management)
12. [WebSocket Integration](#websocket-integration)
13. [Multi-App Architecture](#multi-app-architecture)
14. [Security Architecture](#security-architecture)
15. [Performance Optimizations](#performance-optimizations)
16. [Error Handling & Observability](#error-handling--observability)
17. [Examples & Use Cases](#examples--use-cases)
18. [Appendix](#appendix)

---

## Executive Summary

MDB-Engine is a sophisticated MongoDB runtime engine designed for building multi-tenant, secure, and scalable Python applications. It provides automatic data isolation, manifest-driven configuration, and a comprehensive set of services including vector search, memory management, authentication, and authorization.

### Key Design Principles

1. **Manifest-Driven Configuration**: Single source of truth for app configuration
2. **Automatic Data Isolation**: Multi-tenant ready with zero boilerplate
3. **Security by Default**: Envelope encryption, token verification, CSRF protection
4. **Protocol-Based Architecture**: Type-safe, testable, and extensible
5. **Incremental Adoption**: Start minimal, add features as needed

### Technology Stack

- **Database**: MongoDB (Motor/PyMongo async drivers)
- **Web Framework**: FastAPI
- **Vector Search**: MongoDB Atlas Vector Search
- **Authentication**: JWT, Casbin, OSO Cloud
- **Encryption**: Cryptography (AES-256-GCM envelope encryption)
- **AI/ML**: OpenAI, LiteLLM (100+ providers)

---

## Architecture Overview

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Application Layer                          │
│  (FastAPI Routes, WebSocket Handlers, CLI)                  │
└──────────────────────┬───────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                    MongoDBEngine                             │
│  • App Registration & Lifecycle                              │
│  • Connection Management                                     │
│  • Service Orchestration                                     │
│  • Security & Authentication                                 │
└──────┬───────────────────────────────────────────────────────┘
       │
       ├──► AppRegistrationManager
       │    • Manifest Validation & Parsing
       │    • App State Management
       │    • Configuration Persistence
       │
       ├──► ConnectionManager
       │    • MongoDB Connection Pool
       │    • Health Monitoring
       │    • CSFLE Support
       │
       ├──► ServiceInitializer
       │    • Graph Service
       │    • Memory Service
       │    • Embedding Service
       │    • LLM Service
       │
       ├──► IndexManager
       │    • Index Creation & Validation
       │    • Auto-Indexing
       │    • Vector Search Indexes
       │
       └──► AppSecretsManager
            • Token Management
            • Envelope Encryption
            • Cross-App Authentication
```

### Component Interaction Flow

```
Application Startup
    │
    ├─► MongoDBEngine.initialize()
    │   │
    │   ├─► ConnectionManager.initialize()
    │   │   └─► Create MongoDB connection pool
    │   │
    │   ├─► EnvelopeEncryptionService()
    │   │   └─► Load master key from env
    │   │
    │   ├─► AppSecretsManager()
    │   │   └─► Initialize secret storage
    │   │
    │   ├─► AppRegistrationManager()
    │   │   └─► Setup manifest validators
    │   │
    │   └─► ServiceInitializer()
    │       └─► Prepare service factories
    │
    ├─► engine.create_app(slug, manifest)
    │   │
    │   ├─► Load & validate manifest
    │   │   └─► ManifestValidator.validate()
    │   │
    │   ├─► Register app
    │   │   └─► AppRegistrationManager.register_app()
    │   │       │
    │   │       ├─► Create indexes
    │   │       │   └─► IndexManager.create_app_indexes()
    │   │       │
    │   │       ├─► Initialize services
    │   │       │   ├─► Graph Service
    │   │       │   ├─► Memory Service
    │   │       │   └─► Embedding Service
    │   │       │
    │   │       └─► Setup authentication
    │   │           └─► AuthIntegration.setup()
    │   │
    │   └─► Return FastAPI app
    │
    └─► FastAPI lifespan events
        ├─► Startup: Ensure services ready
        └─► Shutdown: Cleanup connections
```

---

## Core Components Deep Dive

### MongoDBEngine

The central orchestration class that manages all engine components.

#### Initialization Process

```python
class MongoDBEngine:
    def __init__(
        self,
        mongo_uri: str,
        db_name: str,
        manifests_dir: Path | None = None,
        authz_provider: Optional["AuthorizationProvider"] = None,
        max_pool_size: int = DEFAULT_MAX_POOL_SIZE,
        min_pool_size: int = DEFAULT_MIN_POOL_SIZE,
        enable_ray: bool = False,
        csfle_config: Optional["CSFLEConfig"] = None,
    ):
        # Initialize component managers
        self._connection_manager = ConnectionManager(...)
        self.manifest_validator = ManifestValidator()
        self.manifest_parser = ManifestParser()
        
        # Managers initialized after connection
        self._app_registration_manager: AppRegistrationManager | None = None
        self._index_manager: IndexManager | None = None
        self._service_initializer: ServiceInitializer | None = None
        self._encryption_service: EnvelopeEncryptionService | None = None
        self._app_secrets_manager: AppSecretsManager | None = None
```

**Key Responsibilities:**

1. **Connection Management**: Maintains MongoDB connection pool
2. **App Registration**: Manages app lifecycle and configuration
3. **Service Orchestration**: Initializes and manages optional services
4. **Security**: Handles encryption and token verification
5. **Health Monitoring**: Provides health check endpoints

#### Scoped Database Access

```python
def get_scoped_db(
    self,
    app_slug: str,
    app_token: str | None = None,
    read_scopes: list[str] | None = None,
    write_scope: str | None = None,
    auto_index: bool = True,
) -> ScopedMongoWrapper:
    """
    Returns a ScopedMongoWrapper that automatically:
    1. Filters all queries by app_id
    2. Validates app token (if provided)
    3. Enforces read/write scopes
    4. Enables auto-indexing (if enabled)
    """
```

**Security Flow:**

```
get_scoped_db("my_app", app_token="secret")
    │
    ├─► Verify app_token (if secrets manager available)
    │   └─► AppSecretsManager.verify_app_secret()
    │       ├─► Decrypt stored secret
    │       └─► Constant-time comparison
    │
    ├─► Validate read_scopes
    │   └─► Check against manifest authorization
    │
    └─► Return ScopedMongoWrapper
        └─► Configured with read/write scopes
```

### ScopedMongoWrapper

The core data access layer that provides automatic data isolation.

#### Architecture

```python
class ScopedMongoWrapper:
    """
    Wraps AsyncIOMotorDatabase to provide:
    - Automatic app_id filtering
    - Cross-app access validation
    - Auto-indexing
    - Query validation
    - Resource limiting
    """
    
    def __init__(
        self,
        real_db: AsyncIOMotorDatabase,
        read_scopes: list[str],
        write_scope: str,
        auto_index: bool = True,
        app_slug: str | None = None,
        app_token: str | None = None,
        app_secrets_manager: Optional["AppSecretsManager"] = None,
    ):
        self._db = real_db
        self._read_scopes = read_scopes  # e.g., ["app_a", "app_b"]
        self._write_scope = write_scope  # e.g., "app_a"
        self._auto_index = auto_index
        self._wrapper_cache: dict[str, ScopedCollectionWrapper] = {}
```

#### Collection Access Pattern

```python
# When accessing db.my_collection:
db.my_collection
    │
    ├─► ScopedMongoWrapper.__getattr__("my_collection")
    │   │
    │   ├─► Check cache for existing wrapper
    │   │
    │   ├─► Create ScopedCollectionWrapper
    │   │   └─► Configure with read/write scopes
    │   │
    │   └─► Cache wrapper for reuse
    │
    └─► Return ScopedCollectionWrapper
```

#### Query Filter Injection

**Read Operations:**

```python
class ScopedCollectionWrapper:
    def _inject_read_filter(self, filter: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """
        Combines user filter with mandatory app_id scope filter.
        
        Example:
            User query: {"status": "active"}
            Injected: {"$and": [
                {"status": "active"},
                {"app_id": {"$in": ["app_a", "app_b"]}}
            ]}
        """
        scope_filter = {"app_id": {"$in": self._read_scopes}}
        
        if not filter:
            return scope_filter
        
        return {"$and": [filter, scope_filter]}
```

**Write Operations:**

```python
async def insert_one(self, document: Mapping[str, Any], *args, **kwargs):
    """
    Automatically adds app_id to inserted documents.
    
    Example:
        User inserts: {"name": "John", "email": "john@example.com"}
        Actually inserted: {
            "name": "John",
            "email": "john@example.com",
            "app_id": "app_a"  # From write_scope
        }
    """
    # Ensure app_id is set
    if "app_id" not in document:
        document = {**document, "app_id": self._write_scope}
    else:
        # Validate app_id matches write_scope
        if document["app_id"] != self._write_scope:
            raise ValueError(f"Cannot write to app_id '{document['app_id']}'")
    
    return await self._collection.insert_one(document, *args, **kwargs)
```

**Aggregation Pipeline Handling:**

```python
async def aggregate(self, pipeline: list[dict[str, Any]], *args, **kwargs):
    """
    Handles special cases:
    1. $vectorSearch stage: Embeds scope in filter
    2. Regular pipeline: Prepends $match stage
    """
    first_stage = pipeline[0] if pipeline else {}
    first_stage_op = list(first_stage.keys())[0] if first_stage else None
    
    if first_stage_op == "$vectorSearch":
        # Embed scope in $vectorSearch filter
        vs_stage = first_stage["$vectorSearch"]
        existing_filter = vs_stage.get("filter", {})
        scope_filter = {"app_id": {"$in": self._read_scopes}}
        
        if existing_filter:
            new_filter = {"$and": [existing_filter, scope_filter]}
        else:
            new_filter = scope_filter
        
        vs_stage["filter"] = new_filter
        return self._collection.aggregate(pipeline, *args, **kwargs)
    else:
        # Prepend $match stage
        scope_match = {"$match": {"app_id": {"$in": self._read_scopes}}}
        scoped_pipeline = [scope_match] + pipeline
        return self._collection.aggregate(scoped_pipeline, *args, **kwargs)
```

### Auto-Indexing System

The wrapper includes a "magical" auto-indexing feature that analyzes query patterns and creates indexes automatically.

```python
class AutoIndexManager:
    """
    Analyzes query patterns and creates indexes automatically.
    
    Features:
    - Analyzes filter keys
    - Analyzes sort specifications
    - Creates compound indexes when appropriate
    - Background index creation (non-blocking)
    """
    
    async def analyze_and_create_index(
        self,
        collection_name: str,
        filter: dict[str, Any] | None,
        sort: list[tuple[str, int]] | None,
    ):
        """
        Example:
            Query: db.users.find({"status": "active"}).sort("created_at", -1)
            
            Analysis:
            - Filter keys: ["status"]
            - Sort keys: [("created_at", -1)]
            - Suggested index: {"status": 1, "created_at": -1}
            
            Creates index in background if not exists.
        """
```

---

## Data Scoping and Isolation

### Collection Naming Convention

All collections are automatically prefixed with the app slug:

```
User defines: "tasks"
Engine creates: "my_app_tasks"

User defines: "users"
Engine creates: "my_app_users"
```

### App ID Filtering

Every document automatically includes an `app_id` field:

```python
# User inserts:
await db.tasks.insert_one({"title": "Task 1"})

# Actually stored:
{
    "_id": ObjectId("..."),
    "title": "Task 1",
    "app_id": "my_app"  # Automatically added
}
```

### Cross-App Access

Apps can read from multiple apps if authorized in manifest:

```json
{
  "data_access": {
    "read_scopes": ["my_app", "shared_app"],
    "write_scope": "my_app"
  }
}
```

**Implementation:**

```python
# App "my_app" requests access to "shared_app"
db = engine.get_scoped_db(
    app_slug="my_app",
    read_scopes=["my_app", "shared_app"]  # Must be in manifest
)

# Query automatically filters by both apps
results = await db.shared_tasks.find({})
# Executes: {"app_id": {"$in": ["my_app", "shared_app"]}}
```

### Security Boundaries

1. **Collection Prefixing**: Prevents accidental cross-app access
2. **App ID Filtering**: Enforced at query level
3. **Token Verification**: Required for app-level authentication
4. **Scope Validation**: Read scopes validated against manifest

---

## Manifest System

### Manifest Structure

The manifest is a JSON file that defines the entire app configuration:

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My Application",
  "status": "active",
  "data_access": {
    "read_scopes": ["my_app"],
    "write_scope": "my_app"
  },
  "managed_indexes": {
    "tasks": [
      {
        "type": "regular",
        "keys": {"status": 1, "created_at": -1},
        "name": "status_sort"
      }
    ]
  },
  "auth": {
    "policy": {
      "provider": "casbin",
      "required": false
    },
    "users": {
      "enabled": true,
      "strategy": "app_users"
    }
  },
  "memory_config": {
    "enabled": true,
    "collection_name": "memories",
    "embedding_model": "text-embedding-3-small"
  }
}
```

### Manifest Validation

The validation system uses JSON Schema with versioning:

```python
class ManifestValidator:
    """
    Validates manifests against versioned JSON schemas.
    
    Features:
    - Schema versioning (1.0, 2.0, etc.)
    - Caching for performance
    - Detailed error reporting with paths
    - Migration support
    """
    
    @staticmethod
    def validate(manifest_data: dict[str, Any]) -> tuple[bool, str | None, list[str] | None]:
        """
        Returns:
            (is_valid, error_message, error_paths)
        
        Example error:
            is_valid: False
            error_message: "Invalid index type 'invalid_type'"
            error_paths: ["managed_indexes.tasks[0].type"]
        """
```

**Validation Flow:**

```
ManifestValidator.validate(manifest)
    │
    ├─► Get schema version
    │   └─► manifest.get("schema_version", "1.0")
    │
    ├─► Load schema for version
    │   └─► get_schema_for_version(version)
    │
    ├─► Validate against schema
    │   └─► jsonschema.validate(instance=manifest, schema=schema)
    │
    └─► Return (is_valid, error_message, error_paths)
```

### Manifest Parsing

```python
class ManifestParser:
    """
    Loads manifests from various sources with validation.
    """
    
    @staticmethod
    async def load_from_file(path: Path, validate: bool = True) -> dict[str, Any]:
        """
        Loads manifest from file.
        
        Example:
            manifest = await ManifestParser.load_from_file(
                Path("manifest.json"),
                validate=True
            )
        """
    
    @staticmethod
    async def load_from_dict(data: dict[str, Any], validate: bool = True) -> dict[str, Any]:
        """Loads manifest from dictionary."""
    
    @staticmethod
    async def load_from_string(content: str, validate: bool = True) -> dict[str, Any]:
        """Loads manifest from JSON string."""
```

---

## Authentication & Authorization

### Authentication Architecture

MDB-Engine supports multiple authentication strategies:

1. **App-Level Authentication**: Token-based app identity verification
2. **User-Level Authentication**: JWT-based user authentication
3. **Authorization**: Casbin or OSO Cloud for RBAC

### App-Level Authentication

**Envelope Encryption:**

```python
class EnvelopeEncryptionService:
    """
    Implements envelope encryption for app secrets.
    
    Architecture:
    Master Key (MK) → Encrypts → Data Encryption Key (DEK)
    DEK → Encrypts → App Secret
    
    Storage:
    {
        "encrypted_secret": "<base64>",
        "encrypted_dek": "<base64>",
        "algorithm": "AES-256-GCM"
    }
    """
    
    def encrypt_secret(self, secret: str) -> dict[str, Any]:
        """
        1. Generate DEK (32 bytes)
        2. Encrypt secret with DEK
        3. Encrypt DEK with master key
        4. Return encrypted data
        """
    
    def decrypt_secret(self, encrypted_data: dict[str, Any]) -> str:
        """
        1. Decrypt DEK with master key
        2. Decrypt secret with DEK
        3. Return plaintext secret
        """
```

**App Secrets Management:**

```python
class AppSecretsManager:
    """
    Manages app secret tokens with encryption.
    
    Features:
    - Secure storage in MongoDB
    - Constant-time token verification
    - Secret rotation support
    - Lazy decryption (only when needed)
    """
    
    async def store_app_secret(self, app_slug: str, secret: str):
        """
        Stores encrypted app secret.
        
        Flow:
        1. Generate secret (if not provided)
        2. Encrypt with EnvelopeEncryptionService
        3. Store in _mdb_engine_app_secrets collection
        """
    
    async def verify_app_secret(self, app_slug: str, token: str) -> bool:
        """
        Verifies app token with constant-time comparison.
        
        Security:
        - Uses secrets.compare_digest() for timing attack prevention
        - Lazy decryption (only decrypts when needed)
        """
```

### User-Level Authentication

**JWT Token Management:**

```python
class TokenManager:
    """
    Manages JWT tokens for user authentication.
    
    Features:
    - Access token (short-lived)
    - Refresh token (long-lived)
    - Token rotation
    - Session management
    - CSRF protection
    """
    
    async def create_tokens(self, user_id: str, email: str) -> dict[str, str]:
        """
        Creates access and refresh tokens.
        
        Returns:
        {
            "access_token": "...",
            "refresh_token": "...",
            "token_type": "bearer"
        }
        """
```

**Session Management:**

```python
class SessionManager:
    """
    Manages user sessions with MongoDB storage.
    
    Features:
    - Session cookies
    - Session fingerprinting
    - Inactivity timeout
    - Max sessions per user
    """
```

### Authorization Providers

**Casbin Integration:**

```python
class CasbinAuthorizationProvider:
    """
    Casbin-based RBAC authorization.
    
    Model: RBAC (Role-Based Access Control)
    Storage: MongoDB collection (casbin_policies)
    
    Example policies:
    - p, admin, documents, write
    - p, user, documents, read
    - g, alice, admin
    """
    
    async def check(self, subject: str, resource: str, action: str) -> bool:
        """
        Checks if subject has permission for action on resource.
        
        Example:
            await authz.check("alice", "documents", "write")
            # Returns True if alice has admin role
        """
```

**OSO Cloud Integration:**

```python
class OSOAuthorizationProvider:
    """
    OSO Cloud-based authorization.
    
    Features:
    - Policy as code
    - Fine-grained permissions
    - Relationship-based access control
    """
```

---

## Index Management

### Index Types

MDB-Engine supports multiple index types:

1. **Regular Indexes**: B-tree indexes for standard queries
2. **Text Indexes**: Full-text search
3. **Vector Search Indexes**: MongoDB Atlas Vector Search
4. **TTL Indexes**: Time-to-live expiration
5. **Geospatial Indexes**: Geographic queries

### Index Definition Format

```json
{
  "managed_indexes": {
    "tasks": [
      {
        "type": "regular",
        "keys": {"status": 1, "created_at": -1},
        "name": "status_sort",
        "options": {
          "background": true,
          "unique": false
        }
      },
      {
        "type": "vectorSearch",
        "name": "embedding_idx",
        "definition": {
          "fields": [
            {
              "type": "vector",
              "path": "embedding",
              "numDimensions": 1536,
              "similarity": "cosine"
            }
          ]
        }
      }
    ]
  }
}
```

### Index Creation Process

```python
class IndexManager:
    """
    Manages index creation and validation.
    """
    
    async def create_app_indexes(self, slug: str, manifest: ManifestDict):
        """
        Creates all indexes defined in manifest.
        
        Process:
        1. Validate index definitions
        2. Prefix collection names with app slug
        3. Prefix index names with app slug
        4. Create indexes (background by default)
        5. Wait for indexes to be ready
        """
```

**Index Naming Convention:**

```
User defines: "status_sort"
Engine creates: "my_app_status_sort"

User defines: "embedding_idx"
Engine creates: "my_app_embedding_idx"
```

### Auto-Indexing

The `ScopedCollectionWrapper` includes automatic index creation based on query patterns:

```python
class AutoIndexManager:
    """
    Automatically creates indexes based on query patterns.
    
    Analysis:
    - Extracts filter keys
    - Extracts sort keys
    - Suggests compound indexes
    - Creates indexes in background
    """
    
    async def analyze_query(self, filter: dict, sort: list[tuple]):
        """
        Analyzes query and suggests index.
        
        Example:
            Query: find({"status": "active"}).sort("created_at", -1)
            Suggested: {"status": 1, "created_at": -1}
        """
```

---

## Memory Service Architecture

### Overview

The Memory Service provides semantic memory management using MongoDB Atlas Vector Search. It supports two implementations:

1. **CustomMemoryService**: Basic memory with fact extraction
2. **CognitiveMemoryService**: Advanced memory with importance scoring, decay, and reinforcement

### CustomMemoryService

**Architecture:**

```python
class CustomMemoryService(BaseMemoryService):
    """
    Basic memory service with LLM fact extraction.
    
    Features:
    - Semantic search using vector embeddings
    - Automatic fact extraction from conversations
    - Metadata support (bucket_id, bucket_type)
    - Automatic re-embedding on updates
    """
    
    def add(
        self,
        messages: list[dict[str, str]],
        user_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Adds memories from conversation.
        
        Process:
        1. Extract facts using LLM
        2. Generate embeddings for facts
        3. Store in MongoDB with vector index
        4. Return created memories
        """
```

**Fact Extraction:**

```python
async def _extract_facts(self, messages: list[dict]) -> list[str]:
    """
    Uses LLM to extract atomic facts from conversation.
    
    Example:
        Input: [
            {"role": "user", "content": "I'm allergic to peanuts"},
            {"role": "assistant", "content": "I'll remember that."}
        ]
        
        Output: ["User is allergic to peanuts"]
    """
```

### CognitiveMemoryService

**Advanced Features:**

1. **Importance Scoring**: AI evaluates memory significance (0.1-1.0)
2. **Memory Reinforcement**: Similar memories strengthen existing ones
3. **Memory Decay**: Less relevant memories fade over time
4. **Memory Merging**: Related memories are combined
5. **Memory Pruning**: Least important memories removed when capacity exceeded

**Architecture:**

```python
class CognitiveMemoryService(BaseMemoryService):
    """
    Advanced memory service with cognitive features.
    
    Components:
    - ImportanceEngine: Scores memory importance
    - DecayEngine: Implements memory decay
    - ReinforcementEngine: Strengthens similar memories
    - PruningEngine: Removes low-importance memories
    """
    
    async def add(
        self,
        messages: list[dict[str, str]],
        user_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Adds memories with cognitive processing.
        
        Process:
        1. Extract facts
        2. Score importance
        3. Check for similar memories (reinforcement)
        4. Apply decay to existing memories
        5. Store new memories
        6. Prune if capacity exceeded
        """
```

**Importance Scoring:**

```python
async def _score_importance(self, memory: str) -> float:
    """
    Uses LLM to score memory importance (0.1-1.0).
    
    Criteria:
    - Personal information: 0.9-1.0
    - Preferences: 0.7-0.9
    - Casual mentions: 0.3-0.5
    - Trivial facts: 0.1-0.3
    """
```

**Memory Decay:**

```python
async def _apply_decay(self, memory: dict[str, Any]) -> float:
    """
    Applies time-based decay to memory importance.
    
    Formula:
    decayed_importance = base_importance * exp(-decay_rate * time_since_access)
    
    Parameters:
    - decay_rate: Based on memory stability
    - time_since_access: Hours since last access
    """
```

### Vector Search Integration

**Index Configuration:**

```python
async def _ensure_memory_vector_index(
    self,
    slug: str,
    collection_name: str,
    index_name: str,
    embedding_dims: int = 1536,
):
    """
    Automatically creates vector search index for memory service.
    
    Index Definition:
    {
        "fields": [
            {
                "type": "filter",
                "path": "user_id"
            },
            {
                "type": "filter",
                "path": "is_active"
            },
            {
                "type": "vector",
                "path": "embedding",
                "numDimensions": 1536,
                "similarity": "cosine"
            }
        ]
    }
    """
```

**Search Implementation:**

```python
async def search(
    self,
    query: str,
    user_id: str | None = None,
    limit: int = 5,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Semantic search using vector similarity.
    
    Process:
    1. Generate query embedding
    2. Build $vectorSearch aggregation pipeline
    3. Filter by user_id and is_active
    4. Return top-k similar memories
    """
```

---

## Service Initialization

### ServiceInitializer

The `ServiceInitializer` manages the lifecycle of optional services:

```python
class ServiceInitializer:
    """
    Initializes and manages optional services.
    
    Services:
    - Graph Service (knowledge graph)
    - Memory Service (vector search)
    - Embedding Service (text embeddings)
    - LLM Service (chat completions)
    """
    
    async def initialize_graph_service(
        self,
        slug: str,
        config: dict[str, Any],
    ) -> GraphServiceProtocol | None:
        """
        Initializes graph service if enabled.
        
        Features:
        - Node/edge storage
        - Graph traversal ($graphLookup)
        - Relationship queries
        """
    
    async def initialize_memory_service(
        self,
        slug: str,
        config: dict[str, Any],
    ) -> MemoryServiceProtocol | None:
        """
        Initializes memory service if enabled.
        
        Steps:
        1. Create vector search index
        2. Initialize embedding service
        3. Initialize LLM service (if needed)
        4. Create memory service instance
        """
```

### Service Dependencies

Services follow a dependency injection pattern:

```python
# Memory service depends on embedding service
memory_service = get_memory_service(
    app_slug="my_app",
    collection=collection,
    config=memory_config,
    embedding_service=embedding_service,  # Injected
    llm_service=llm_service,  # Injected
)
```

### Protocol-Based Architecture

Services implement Python Protocols for type safety:

```python
class MemoryServiceProtocol(Protocol):
    """Protocol for memory service implementations."""
    
    def add(
        self,
        messages: list[dict[str, str]],
        user_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...
    
    def search(
        self,
        query: str,
        user_id: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]: ...
```

**Benefits:**

1. **Type Safety**: Full IDE autocompletion
2. **Testability**: Easy mocking
3. **Flexibility**: Swap implementations without code changes

---

## Dependency Injection System

### RequestContext

The `RequestContext` provides all-in-one dependency access:

```python
class RequestContext:
    """
    All-in-one request context with lazy-loaded dependencies.
    
    Usage:
        @app.post("/documents")
        async def create_doc(data: DocCreate, ctx: RequestContext = Depends()):
            doc_id = await ctx.uow.documents.add(doc)
            return {"id": doc_id}
    """
    
    @property
    def engine(self) -> MongoDBEngine:
        """Get MongoDBEngine instance."""
    
    @property
    def db(self) -> ScopedMongoWrapper:
        """Get scoped database wrapper."""
    
    @property
    def uow(self) -> UnitOfWork:
        """Get Unit of Work for repository access."""
    
    @property
    def memory(self) -> BaseMemoryService | None:
        """Get memory service if configured."""
    
    @property
    def embedding_service(self) -> EmbeddingService | None:
        """Get embedding service if configured."""
```

### Individual Dependencies

For fine-grained control, use individual dependencies:

```python
@app.get("/documents")
async def list_documents(
    db: ScopedMongoWrapper = Depends(get_scoped_db),
    memory: BaseMemoryService | None = Depends(get_memory_service),
):
    """Use individual dependencies."""
    documents = await db.documents.find({}).to_list(length=10)
    return documents
```

### DI Container

MDB-Engine includes a dependency injection container:

```python
from mdb_engine.di import Container, Scope

# Register service
container = Container()
container.register(MyService, factory=create_my_service, scope=Scope.SINGLETON)

# Resolve service
service = container.resolve(MyService)
```

---

## Connection Management

### ConnectionManager

Manages MongoDB connection pool with health monitoring:

```python
class ConnectionManager:
    """
    Manages MongoDB connection pool.
    
    Features:
    - Connection pooling (min/max pool size)
    - Health monitoring
    - CSFLE support (Client-Side Field Level Encryption)
    - Automatic reconnection
    """
    
    def __init__(
        self,
        mongo_uri: str,
        db_name: str,
        max_pool_size: int = 100,
        min_pool_size: int = 10,
        csfle_config: Optional["CSFLEConfig"] = None,
    ):
        """
        Initializes connection manager.
        
        Pool Configuration:
        - max_pool_size: Maximum connections (default: 100)
        - min_pool_size: Minimum connections (default: 10)
        """
    
    async def initialize(self):
        """
        Establishes MongoDB connection.
        
        Process:
        1. Create AsyncIOMotorClient
        2. Configure connection pool
        3. Test connection
        4. Setup CSFLE (if configured)
        """
```

### Health Monitoring

```python
async def check_mongodb_health(client: AsyncIOMotorClient) -> dict[str, Any]:
    """
    Checks MongoDB connection health.
    
    Returns:
    {
        "status": "healthy" | "unhealthy",
        "latency_ms": 12.5,
        "server_info": {...}
    }
    """
```

---

## WebSocket Integration

### WebSocket Session Management

```python
class WebSocketSessionManager:
    """
    Manages WebSocket sessions with secure authentication.
    
    Features:
    - Ticket-based authentication
    - Session encryption
    - Automatic cleanup
    """
    
    async def create_session(
        self,
        user_id: str,
        app_slug: str,
        ttl_seconds: int = 300,
    ) -> str:
        """
        Creates WebSocket session ticket.
        
        Returns encrypted ticket for WebSocket connection.
        """
```

### WebSocket Routing

```python
from mdb_engine.routing.websockets import register_websocket_endpoint

# Register WebSocket endpoint from manifest
register_websocket_endpoint(
    app=fastapi_app,
    path="/ws",
    handler=websocket_handler,
    auth_required=True,
)
```

---

## Multi-App Architecture

### Multi-App Mounting

MDB-Engine supports mounting multiple FastAPI apps:

```python
# Create parent app
parent_app = engine.create_multi_app(
    apps=[
        {
            "slug": "auth-hub",
            "manifest": Path("./auth-hub/manifest.json"),
            "path_prefix": "/auth-hub"
        },
        {
            "slug": "dashboard",
            "manifest": Path("./dashboard/manifest.json"),
            "path_prefix": "/dashboard"
        }
    ]
)
```

**Architecture:**

```
Parent App (Port 8000)
    │
    ├─► /auth-hub → Auth Hub App
    ├─► /dashboard → Dashboard App
    └─► /health → Unified health check
```

### Shared Resources

- Single MongoDBEngine instance
- Shared connection pool
- SharedUserPool (for SSO)
- Unified health monitoring

---

## Security Architecture

### Security Layers

1. **App-Level Security**:
   - Envelope encryption for secrets
   - Token verification
   - Scope validation

2. **User-Level Security**:
   - JWT tokens
   - Session management
   - CSRF protection

3. **Data Security**:
   - Automatic app_id filtering
   - Collection prefixing
   - Query validation

4. **Network Security**:
   - CORS configuration
   - Rate limiting
   - IP validation

### CSRF Protection

```python
class CSRFProtection:
    """
    CSRF protection middleware.
    
    Features:
    - Double-submit cookie pattern
    - Token generation
    - Token validation
    """
```

### Rate Limiting

```python
class RateLimiter:
    """
    Rate limiting middleware.
    
    Configuration:
    {
        "rate_limiting": {
            "login": {"max_attempts": 5, "window_seconds": 300},
            "register": {"max_attempts": 3, "window_seconds": 600}
        }
    }
    """
```

---

## Performance Optimizations

### Connection Pooling

- Min/max pool size configuration
- Connection reuse
- Automatic connection management

### Query Optimization

- Automatic index creation
- Query pattern analysis
- Background index creation

### Caching

- Manifest validation cache
- Auth config cache
- Collection wrapper cache

### Async Operations

- All database operations are async
- Parallel service initialization
- Background index creation

---

## Error Handling & Observability

### Error Types

```python
class MongoDBEngineError(Exception):
    """Base exception for MDB-Engine."""
    pass

class ManifestValidationError(MongoDBEngineError):
    """Manifest validation failed."""
    pass

class AppRegistrationError(MongoDBEngineError):
    """App registration failed."""
    pass
```

### Observability

**Logging:**

```python
from mdb_engine.observability import get_logger

logger = get_logger(__name__)
logger.info("Operation completed", extra={"app_slug": "my_app"})
```

**Metrics:**

```python
from mdb_engine.observability import record_operation

record_operation(
    "app_registration.register_app",
    duration_ms=125.5,
    success=True,
    app_slug="my_app"
)
```

**Health Checks:**

```python
@app.get("/health")
async def health():
    status = await engine.get_health_status()
    return status
```

---

## Examples & Use Cases

### Example 1: Basic CRUD App

```python
from mdb_engine import MongoDBEngine
from mdb_engine.dependencies import get_scoped_db
from fastapi import FastAPI, Depends

engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="mydb")
app = engine.create_app(slug="todo_app", manifest=Path("manifest.json"))

@app.post("/todos")
async def create_todo(todo: dict, db=Depends(get_scoped_db)):
    result = await db.todos.insert_one(todo)
    return {"id": str(result.inserted_id)}

@app.get("/todos")
async def list_todos(db=Depends(get_scoped_db)):
    todos = await db.todos.find({}).to_list(length=100)
    return {"todos": todos}
```

### Example 2: AI Chat with Memory

```python
from mdb_engine.dependencies import RequestContext

@app.post("/chat")
async def chat(message: str, ctx: RequestContext = Depends()):
    user = ctx.require_user()
    
    # Search memories
    memories = await asyncio.to_thread(
        ctx.memory.search,
        query=message,
        user_id=user["id"],
        limit=5
    )
    
    # Generate response with context
    response = await generate_response(message, memories)
    
    # Store conversation
    await asyncio.to_thread(
        ctx.memory.add,
        messages=[
            {"role": "user", "content": message},
            {"role": "assistant", "content": response}
        ],
        user_id=user["id"]
    )
    
    return {"response": response}
```

### Example 3: Multi-App Setup

```python
# Parent app with multiple child apps
parent_app = engine.create_multi_app(
    apps=[
        {"slug": "auth", "manifest": Path("./auth/manifest.json"), "path_prefix": "/auth"},
        {"slug": "api", "manifest": Path("./api/manifest.json"), "path_prefix": "/api"},
    ]
)
```

---

## Appendix

### Appendix A: Complete Manifest Example

```json
{
  "schema_version": "2.0",
  "slug": "enterprise_app",
  "name": "Enterprise Application",
  "description": "Full-featured enterprise app",
  "status": "active",
  "data_access": {
    "read_scopes": ["enterprise_app", "shared_data"],
    "write_scope": "enterprise_app"
  },
  "auth": {
    "policy": {
      "provider": "casbin",
      "required": true,
      "authorization": {
        "model": "rbac",
        "policies_collection": "policies"
      }
    },
    "users": {
      "enabled": true,
      "strategy": "app_users",
      "collection_name": "users",
      "allow_registration": true
    }
  },
  "token_management": {
    "enabled": true,
    "access_token_ttl": 900,
    "refresh_token_ttl": 604800,
    "csrf_protection": true
  },
  "managed_indexes": {
    "documents": [
      {
        "type": "regular",
        "keys": {"user_id": 1, "created_at": -1},
        "name": "user_documents_idx"
      },
      {
        "type": "text",
        "keys": {"title": "text", "content": "text"},
        "name": "fulltext_idx"
      }
    ]
  },
  "memory_config": {
    "enabled": true,
    "provider": "cognitive",
    "collection_name": "memories",
    "embedding_model": "text-embedding-3-small",
    "enable_cognitive": true,
    "max_depth": 500
  },
  "graph_config": {
    "enabled": true,
    "collection_name": "graph_nodes"
  },
  "websockets": {
    "realtime": {
      "path": "/ws",
      "auth": {"required": true}
    }
  },
  "cors": {
    "enabled": true,
    "allow_origins": ["https://app.example.com"],
    "allow_credentials": true
  }
}
```

### Appendix B: ScopedMongoWrapper Complete API

```python
class ScopedMongoWrapper:
    """Complete API reference."""
    
    # Collection access
    def __getattr__(self, name: str) -> ScopedCollectionWrapper:
        """Access collection: db.my_collection"""
    
    # Direct database operations
    async def list_collection_names(self) -> list[str]:
        """List all collections (filtered by app)"""
    
    async def create_collection(self, name: str, **kwargs):
        """Create collection (with app prefix)"""

class ScopedCollectionWrapper:
    """Complete API reference."""
    
    # Read operations
    async def find(self, filter: dict = None, **kwargs) -> AsyncIOMotorCursor:
        """Find documents (auto-filtered by app_id)"""
    
    async def find_one(self, filter: dict = None, **kwargs) -> dict | None:
        """Find one document (auto-filtered by app_id)"""
    
    async def count_documents(self, filter: dict = None, **kwargs) -> int:
        """Count documents (auto-filtered by app_id)"""
    
    async def aggregate(self, pipeline: list[dict], **kwargs) -> AsyncIOMotorCursor:
        """Aggregate pipeline (auto-scoped)"""
    
    # Write operations
    async def insert_one(self, document: dict, **kwargs) -> InsertOneResult:
        """Insert document (auto-adds app_id)"""
    
    async def insert_many(self, documents: list[dict], **kwargs) -> InsertManyResult:
        """Insert documents (auto-adds app_id)"""
    
    async def update_one(self, filter: dict, update: dict, **kwargs) -> UpdateResult:
        """Update document (auto-filtered by app_id)"""
    
    async def update_many(self, filter: dict, update: dict, **kwargs) -> UpdateResult:
        """Update documents (auto-filtered by app_id)"""
    
    async def delete_one(self, filter: dict, **kwargs) -> DeleteResult:
        """Delete document (auto-filtered by app_id)"""
    
    async def delete_many(self, filter: dict, **kwargs) -> DeleteResult:
        """Delete documents (auto-filtered by app_id)"""
    
    # Index management
    @property
    def index_manager(self) -> AsyncAtlasIndexManager:
        """Access index manager for manual index operations"""
```

### Appendix C: Memory Service Complete API

```python
class BaseMemoryService(Protocol):
    """Base memory service protocol."""
    
    def add(
        self,
        messages: list[dict[str, str]],
        user_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Add memories from conversation."""
    
    def inject(
        self,
        memory: str,
        user_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Inject memory directly (no LLM inference)."""
    
    def search(
        self,
        query: str,
        user_id: str | None = None,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Search memories semantically."""
    
    def update(
        self,
        memory_id: str,
        memory: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Update existing memory."""
    
    def delete(self, memory_id: str) -> bool:
        """Delete memory."""
    
    def get(self, memory_id: str) -> dict[str, Any] | None:
        """Get memory by ID."""
    
    def list(
        self,
        user_id: str | None = None,
        limit: int = 100,
        skip: int = 0,
    ) -> list[dict[str, Any]]:
        """List memories."""
```

### Appendix D: Authentication Flow Diagrams

**App Registration Flow:**

```
register_app(manifest)
    │
    ├─► Validate manifest
    │   └─► ManifestValidator.validate()
    │
    ├─► Extract read_scopes
    │   └─► Store in _app_read_scopes mapping
    │
    ├─► Generate app secret
    │   └─► secrets.token_urlsafe(32)
    │
    ├─► Encrypt secret
    │   └─► EnvelopeEncryptionService.encrypt_secret()
    │       ├─► Generate DEK
    │       ├─► Encrypt secret with DEK
    │       └─► Encrypt DEK with master key
    │
    ├─► Store encrypted secret
    │   └─► AppSecretsManager.store_app_secret()
    │       └─► Store in _mdb_engine_app_secrets
    │
    └─► Return success
```

**Runtime Access Flow:**

```
get_scoped_db(app_slug, app_token)
    │
    ├─► Verify app_token
    │   └─► AppSecretsManager.verify_app_secret()
    │       ├─► Read encrypted secret
    │       ├─► Decrypt secret
    │       └─► Constant-time comparison
    │
    ├─► Validate read_scopes
    │   └─► Check against manifest authorization
    │
    └─► Return ScopedMongoWrapper
        └─► Configured with scopes
```

### Appendix E: Index Creation Examples

**Regular Index:**

```python
# Manifest definition
{
  "managed_indexes": {
    "tasks": [
      {
        "type": "regular",
        "keys": {"status": 1, "created_at": -1},
        "name": "status_sort"
      }
    ]
  }
}

# Created index
{
  "name": "my_app_status_sort",
  "key": {"status": 1, "created_at": -1},
  "background": true
}
```

**Vector Search Index:**

```python
# Manifest definition
{
  "managed_indexes": {
    "embeddings": [
      {
        "type": "vectorSearch",
        "name": "embedding_idx",
        "definition": {
          "fields": [
            {
              "type": "vector",
              "path": "embedding",
              "numDimensions": 1536,
              "similarity": "cosine"
            }
          ]
        }
      }
    ]
  }
}
```

### Appendix F: Error Handling Examples

**Manifest Validation Error:**

```python
try:
    await engine.register_app(manifest)
except ManifestValidationError as e:
    print(f"Validation failed: {e.message}")
    print(f"Error paths: {e.error_paths}")
    # Error paths: ["managed_indexes.tasks[0].type"]
```

**App Registration Error:**

```python
try:
    await engine.register_app(manifest)
except AppRegistrationError as e:
    print(f"Registration failed: {e}")
    # Check logs for detailed error
```

**Token Verification Error:**

```python
try:
    db = engine.get_scoped_db("my_app", app_token="invalid")
except ValueError as e:
    print(f"Token verification failed: {e}")
    # ValueError: Invalid app token
```

### Appendix G: Performance Tuning

**Connection Pool Configuration:**

```python
engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="mydb",
    max_pool_size=200,  # Increase for high concurrency
    min_pool_size=20,   # Keep warm connections
)
```

**Auto-Indexing Configuration:**

```python
# Disable auto-indexing for performance-critical apps
db = engine.get_scoped_db("my_app", auto_index=False)
```

**Memory Service Configuration:**

```python
{
  "memory_config": {
    "enabled": true,
    "async_mode": true,  # Process memories asynchronously
    "max_depth": 1000,   # Limit memory count per user
    "cognitive": {
      "pruning": {
        "enabled": true,
        "max_capacity": 1000,
        "prune_percentage": 0.1
      }
    }
  }
}
```

### Appendix H: Testing Examples

**Unit Test Example:**

```python
import pytest
from mdb_engine import MongoDBEngine

@pytest.mark.asyncio
async def test_scoped_db_filtering():
    engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test")
    await engine.initialize()
    
    db = engine.get_scoped_db("test_app")
    
    # Insert document
    await db.tasks.insert_one({"title": "Test"})
    
    # Query should only return documents for test_app
    results = await db.tasks.find({}).to_list(length=10)
    assert all(doc["app_id"] == "test_app" for doc in results)
```

**Integration Test Example:**

```python
@pytest.mark.asyncio
async def test_memory_service():
    engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test")
    await engine.initialize()
    
    manifest = {
        "schema_version": "2.0",
        "slug": "test_app",
        "name": "Test App",
        "memory_config": {
            "enabled": True,
            "collection_name": "memories"
        }
    }
    
    await engine.register_app(manifest)
    
    memory = engine.get_memory_service("test_app")
    assert memory is not None
    
    # Add memory
    memories = memory.add(
        messages=[{"role": "user", "content": "I like pizza"}],
        user_id="user123"
    )
    
    assert len(memories) > 0
```

---

## Conclusion

MDB-Engine provides a comprehensive, production-ready solution for building MongoDB-based applications with automatic data isolation, manifest-driven configuration, and a rich set of services. Its architecture emphasizes security, scalability, and developer experience, making it an ideal choice for multi-tenant applications requiring sophisticated data management and AI capabilities.

The engine's modular design, protocol-based architecture, and extensive feature set make it suitable for a wide range of use cases, from simple CRUD applications to complex AI-powered systems with semantic memory and knowledge graphs.

---

**Document Version**: 1.0  
**Last Updated**: 2026-02-05  
**MDB-Engine Version**: 0.7.5
