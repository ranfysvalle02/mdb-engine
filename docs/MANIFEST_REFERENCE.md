# manifest.json Reference Guide

**Complete reference for all manifest.json fields and configurations.**

This document provides a comprehensive guide to every field available in `manifest.json`. Use this as a reference when building your application configuration.

---

## Table of Contents

- [Required Fields](#required-fields)
- [App Identity](#app-identity)
- [Data Access](#data-access)
- [Index Management](#index-management)
- [Authentication & Authorization](#authentication--authorization)
- [Token Management](#token-management)
- [AI Services](#ai-services)
- [WebSockets](#websockets)
- [CORS](#cors)
- [Observability](#observability)
- [Collection Settings](#collection-settings)
- [Initial Data](#initial-data)
- [Complete Example](#complete-example)

---

## Required Fields

Every manifest.json **must** include these fields:

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My App"
}
```

### Field Descriptions

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | `string` | Schema version (format: `major.minor`). Defaults to `"2.0"` if not specified. |
| `slug` | `string` | Unique app identifier. Must be lowercase alphanumeric, underscores, or hyphens. Pattern: `^[a-z0-9_-]+$` |
| `name` | `string` | Human-readable app name. Minimum length: 1 character |

---

## App Identity

### Basic Fields

```json
{
  "description": "Optional app description",
  "status": "active",
  "developer_id": "developer@example.com"
}
```

| Field | Type | Default | Description |
|-------|------|--------|-------------|
| `description` | `string` | - | Optional app description |
| `status` | `string` | `"draft"` | App status: `"active"`, `"draft"`, `"archived"`, `"inactive"` |
| `developer_id` | `string` (email) | - | Email of the developer who owns this app |

---

## Data Access

Control how your app accesses data across multiple apps:

```json
{
  "data_access": {
    "read_scopes": ["my_app", "shared_data"],
    "write_scope": "my_app",
    "cross_app_policy": "explicit"
  }
}
```

| Field | Type | Default | Description |
|-------|------|--------|-------------|
| `read_scopes` | `array<string>` | `[slug]` | List of app slugs this app can read from |
| `write_scope` | `string` | `slug` | App slug this app writes to |
| `cross_app_policy` | `string` | `"explicit"` | Policy: `"explicit"` (allow listed apps) or `"deny_all"` (block all cross-app access) |

**Legacy**: `data_scope` (array) is still supported for backward compatibility but `data_access` is preferred.

---

## Index Management

Define indexes declaratively—they're created automatically on app registration:

```json
{
  "managed_indexes": {
    "tasks": [
      {
        "type": "regular",
        "name": "status_sort",
        "keys": {"status": 1, "created_at": -1},
        "options": {
          "background": true
        }
      },
      {
        "type": "regular",
        "name": "user_idx",
        "keys": {"user_id": 1},
        "unique": true
      }
    ],
    "knowledge_base": [
      {
        "type": "vectorSearch",
        "name": "embedding_vector_index",
        "definition": {
          "fields": [{
            "type": "vector",
            "path": "embedding",
            "numDimensions": 1536,
            "similarity": "cosine"
          }]
        }
      }
    ]
  }
}
```

### Index Types

| Type | Description | Required Fields |
|------|-------------|----------------|
| `regular` | Standard MongoDB index | `name`, `type`, `keys` |
| `text` | Full-text search index | `name`, `type`, `keys` |
| `vectorSearch` | MongoDB Atlas Vector Search | `name`, `type`, `definition` |
| `search` | Atlas Search index | `name`, `type`, `definition` |
| `geospatial` | Geospatial index (2dsphere, 2d) | `name`, `type`, `keys` |
| `ttl` | Time-to-live index | `name`, `type`, `keys`, `options.expireAfterSeconds` |
| `partial` | Partial index with filter | `name`, `type`, `keys`, `options.partialFilterExpression` |
| `hybrid` | Hybrid vector + text search | `name`, `type`, `hybrid` |

### Index Options

```json
{
  "options": {
    "unique": true,
    "sparse": false,
    "background": true,
    "expireAfterSeconds": 3600,
    "partialFilterExpression": {"status": "active"},
    "weights": {"title": 10, "content": 5},
    "default_language": "english"
  }
}
```

---

## Authentication & Authorization

### Auth Modes

**Per-App Auth (Default)**:
```json
{
  "auth": {
    "mode": "app"
  }
}
```

**Shared Auth (SSO)**:
```json
{
  "auth": {
    "mode": "shared",
    "auth_hub_url": "http://localhost:8000",
    "related_apps": {
      "dashboard": "http://localhost:8001",
      "click_tracker": "http://localhost:8000"
    },
    "roles": ["viewer", "editor", "admin"],
    "default_role": "viewer",
    "require_role": "viewer",
    "public_routes": ["/health", "/api/public/*"]
  }
}
```

**Shared Auth Fields**:

| Field | Type | Description |
|-------|------|-------------|
| `mode` | `string` | Set to `"shared"` to enable SSO across apps |
| `auth_hub_url` | `string` (URI) | URL of the authentication hub for SSO apps. Used for redirecting unauthenticated users to login. Can be overridden via `AUTH_HUB_URL` environment variable. Example: `"http://localhost:8000"` or `"https://auth.example.com"` |
| `related_apps` | `object` | Map of related app slugs to their URLs for cross-app navigation. Keys are app slugs, values are URLs. Can be overridden via `{APP_SLUG_UPPER}_URL` environment variables. Example: `{"dashboard": "http://localhost:8001"}` |
| `roles` | `array<string>` | Available roles for this app (e.g., `["viewer", "editor", "admin"]`) |
| `default_role` | `string` | Role assigned to new users for this app (must be one of the defined roles) |
| `require_role` | `string` | Minimum role required to access app (users without this role are denied) |
| `public_routes` | `array<string>` | Routes that don't require authentication (supports wildcards, e.g., `["/health", "/api/public/*"]`) |

**Configuration Priority** (for `auth_hub_url` and `related_apps`):
1. `manifest.auth.auth_hub_url` or `manifest.auth.related_apps[app_slug]` (declarative, versioned)
2. Environment variables (`AUTH_HUB_URL`, `{APP_SLUG}_URL`) (runtime override)
3. Defaults (`http://localhost:8000` or app-specific defaults) (fallback)

### Authorization Policy

**Casbin RBAC**:
```json
{
  "auth": {
    "policy": {
      "provider": "casbin",
      "required": true,
      "authorization": {
        "model": "rbac",
        "policies_collection": "casbin_policies",
        "link_users_roles": true,
        "default_roles": ["user", "admin"],
        "initial_policies": [
          ["admin", "documents", "read"],
          ["admin", "documents", "write"],
          ["editor", "documents", "read"]
        ],
        "initial_roles": [
          {"user": "alice@example.com", "role": "admin"}
        ]
      }
    }
  }
}
```

**OSO Cloud**:
```json
{
  "auth": {
    "policy": {
      "provider": "oso",
      "authorization": {
        "api_key": "your-oso-api-key",
        "url": "http://oso-dev:8080",
        "initial_roles": [
          {"user": "alice@example.com", "role": "editor"}
        ],
        "initial_policies": [
          {"role": "admin", "resource": "documents", "action": "read"}
        ]
      }
    }
  }
}
```

### User Management

```json
{
  "auth": {
    "users": {
      "enabled": true,
      "strategy": "app_users",
      "collection_name": "users",
      "session_cookie_name": "app_session",
      "session_ttl_seconds": 86400,
      "allow_registration": true,
      "demo_users": [
        {
          "email": "admin@example.com",
          "password": "password123",
          "role": "admin"
        }
      ],
      "demo_user_seed_strategy": "auto"
    }
  }
}
```

### Policy Fields

| Field | Type | Description |
|-------|------|-------------|
| `provider` | `string` | `"casbin"`, `"oso"`, or `"custom"` |
| `required` | `boolean` | Whether authentication is required (default: `true`) |
| `allow_anonymous` | `boolean` | Allow anonymous access (default: `false`) |
| `allowed_roles` | `array<string>` | Roles that can access this app |
| `allowed_users` | `array<string>` | User emails whitelist |
| `denied_users` | `array<string>` | User emails blacklist |
| `public_routes` | `array<string>` | Routes that don't require auth (supports wildcards) |

---

## Token Management

Enhanced token management with refresh tokens, sessions, and security:

```json
{
  "token_management": {
    "enabled": true,
    "access_token_ttl": 900,
    "refresh_token_ttl": 604800,
    "token_rotation": true,
    "max_sessions_per_user": 10,
    "session_inactivity_timeout": 1800,
    "security": {
      "require_https": false,
      "cookie_secure": "auto",
      "cookie_samesite": "lax",
      "cookie_httponly": true,
      "csrf_protection": true,
      "rate_limiting": {
        "login": {"max_attempts": 5, "window_seconds": 300},
        "register": {"max_attempts": 3, "window_seconds": 600},
        "refresh": {"max_attempts": 10, "window_seconds": 60}
      },
      "password_policy": {
        "allow_plain_text": false,
        "min_length": 8,
        "require_uppercase": true,
        "require_lowercase": true,
        "require_numbers": true,
        "require_special": false
      },
      "session_fingerprinting": {
        "enabled": true,
        "validate_on_login": true,
        "validate_on_refresh": true,
        "validate_on_request": false,
        "strict_mode": false
      },
      "account_lockout": {
        "enabled": true,
        "max_failed_attempts": 5,
        "lockout_duration_seconds": 900,
        "reset_on_success": true
      }
    },
    "auto_setup": true
  }
}
```

---

## AI Services

### Embedding Service

```json
{
  "embedding_config": {
    "enabled": true,
    "max_tokens_per_chunk": 1000,
    "tokenizer_model": "gpt-3.5-turbo",
    "default_embedding_model": "text-embedding-3-small"
  }
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `boolean` | `false` | Enable embedding service |
| `max_tokens_per_chunk` | `integer` | `1000` | Max tokens per chunk (100-10000) |
| `tokenizer_model` | `string` | `"gpt-3.5-turbo"` | Tokenizer model for counting tokens |
| `default_embedding_model` | `string` | `"text-embedding-3-small"` | Default embedding model |

### Memory Service (Mem0)

```json
{
  "memory_config": {
    "enabled": true,
    "collection_name": "user_memories",
    "embedding_model_dims": 1536,
    "enable_graph": true,
    "infer": true,
    "embedding_model": "text-embedding-3-small",
    "chat_model": "gpt-4o",
    "temperature": 0.0,
    "async_mode": true
  }
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `boolean` | `false` | Enable Mem0 memory service |
| `collection_name` | `string` | `"{slug}_memories"` | Collection name for memories |
| `embedding_model_dims` | `integer` | `1536` | Embedding vector dimensions (128-4096) |
| `enable_graph` | `boolean` | `false` | Enable knowledge graph construction |
| `infer` | `boolean` | `true` | Infer memories from conversations |
| `async_mode` | `boolean` | `true` | Process memories asynchronously |

**Note**: Mem0 uses environment variables for LLM/embedding configuration. Set `OPENAI_API_KEY` or `AZURE_OPENAI_API_KEY`/`AZURE_OPENAI_ENDPOINT` in your `.env` file.

---

## WebSockets

Define real-time WebSocket endpoints with secure cookie-based authentication.

**Security Note**: MDB-Engine uses **httpOnly cookies** for WebSocket authentication. Tokens are stored in httpOnly cookies set during login, and the browser automatically sends them on WebSocket upgrade requests. This provides XSS protection and CSRF protection via Origin validation and SameSite cookies.

```json
{
  "websockets": {
    "realtime": {
      "path": "/ws",
      "description": "Real-time updates",
      "auth": {
        "required": true,
        "allow_anonymous": false
      },
      "ping_interval": 30
    },
    "events": {
      "path": "/events",
      "description": "Event stream",
      "auth": {
        "required": true
      }
    }
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | `string` | ✅ | WebSocket path (must start with `/`) |
| `description` | `string` | - | Description of the endpoint |
| `auth.required` | `boolean` | - | Whether auth is required (default: `true`) |
| `auth.allow_anonymous` | `boolean` | - | Allow anonymous connections (default: `false`) |
| `ping_interval` | `integer` | - | Ping interval in seconds (5-300, default: `30`) |

### Authentication

When `auth.required` is `true` (default), clients must authenticate using **httpOnly cookies**:

**Client Implementation:**
```javascript
// No token needed - browser automatically sends httpOnly cookies
// Ensure httpOnly cookie is set during authentication/login
const ws = new WebSocket('wss://api.example.com/app1/ws');
```

**Security Benefits:**
- ✅ XSS protection (tokens not accessible to JavaScript)
- ✅ CSRF protection (double-submit cookie pattern)
- ✅ Avoids URL logging risks (token not in query params)
- ✅ Browser-native support (standard WebSocket API)
- ✅ Server validates token **before** accepting connection

**See Also:**
- [WebSocket Security Guide](../guides/WEBSOCKET_SECURITY_MULTI_APP_SSO.md) - Comprehensive security documentation
- [WebSocket + SSO Multi-App Guide](../guides/WEBSOCKET_SSO_MULTI_APP.md) - Multi-app setup guide

---

## CORS

Configure Cross-Origin Resource Sharing:

```json
{
  "cors": {
    "enabled": true,
    "allow_origins": ["*"],
    "allow_credentials": true,
    "allow_methods": ["GET", "POST", "PUT", "DELETE", "PATCH"],
    "allow_headers": ["*"],
    "expose_headers": ["X-Request-ID"],
    "max_age": 3600
  }
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `boolean` | `false` | Enable CORS |
| `allow_origins` | `array<string>` | `["*"]` | Allowed origins (use `["*"]` for all) |
| `allow_credentials` | `boolean` | `false` | Allow credentials in CORS requests |
| `allow_methods` | `array<string>` | `["GET", "POST", "PUT", "DELETE", "PATCH"]` | Allowed HTTP methods |
| `allow_headers` | `array<string>` | `["*"]` | Allowed headers |
| `expose_headers` | `array<string>` | - | Headers to expose to client |
| `max_age` | `integer` | `3600` | Max age for preflight requests (seconds) |

---

## Observability

Configure health checks, metrics, and logging:

```json
{
  "observability": {
    "health_checks": {
      "enabled": true,
      "endpoint": "/health",
      "interval_seconds": 30
    },
    "metrics": {
      "enabled": true,
      "collect_operation_metrics": true,
      "collect_performance_metrics": true,
      "custom_metrics": ["custom_metric_1", "custom_metric_2"]
    },
    "logging": {
      "level": "INFO",
      "format": "json",
      "include_request_id": true,
      "log_sensitive_data": false
    }
  }
}
```

---

## Collection Settings

Configure collection-level settings:

```json
{
  "collection_settings": {
    "events": {
      "validation": {
        "validator": {
          "$jsonSchema": {
            "bsonType": "object",
            "required": ["timestamp", "event_type"],
            "properties": {
              "timestamp": {"bsonType": "date"},
              "event_type": {"bsonType": "string"}
            }
          }
        },
        "validationLevel": "strict",
        "validationAction": "error"
      }
    },
    "metrics": {
      "timeseries": {
        "timeField": "timestamp",
        "metaField": "metadata",
        "granularity": "seconds"
      }
    },
    "logs": {
      "capped": true,
      "size": 10485760,
      "max": 10000
    }
  }
}
```

---

## Initial Data

Seed collections with initial data:

```json
{
  "initial_data": {
    "roles": [
      {"name": "admin", "permissions": ["read", "write", "delete"]},
      {"name": "user", "permissions": ["read"]}
    ],
    "settings": [
      {"key": "theme", "value": "dark"},
      {"key": "language", "value": "en"}
    ]
  }
}
```

**Note**: Initial data is only inserted if the collection is empty (idempotent).

---

## Multi-App Mounting

Mount multiple FastAPI apps under a single parent app for single-deployment scenarios (e.g., Render.com):

```json
{
  "schema_version": "2.0",
  "multi_app": {
    "enabled": true,
    "apps": [
      {
        "slug": "auth-hub",
        "manifest": "./apps/auth-hub/manifest.json",
        "path_prefix": "/auth-hub"
      },
      {
        "slug": "dashboard",
        "manifest": "./apps/dashboard/manifest.json",
        "path_prefix": "/dashboard"
      }
    ],
    "shared_middleware": {
      "cors": true,
      "rate_limiting": true,
      "health_checks": true
    }
  }
}
```

**Usage:**

```python
from mdb_engine import MongoDBEngine
from pathlib import Path

engine = MongoDBEngine(mongo_uri=..., db_name=...)
app = engine.create_multi_app(
    multi_app_manifest=Path("./multi_app_manifest.json")
)
```

**Benefits:**
- Single FastAPI instance for multiple apps
- Shared engine and connection pool
- SSO/shared auth works seamlessly
- Perfect for single-service deployments (Render.com, Railway, etc.)
- Unified health check endpoint at `/health`

**Path Prefix Rules:**
- Must start with `/`
- Must be unique across all apps
- Cannot conflict with reserved paths (`/health`, `/docs`, `/openapi.json`)
- Cannot be a prefix of another (e.g., `/app` conflicts with `/app/v2`)

## Complete Example

Here's a complete manifest.json demonstrating all major features:

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My Application",
  "description": "A complete example application",
  "status": "active",
  "developer_id": "developer@example.com",
  
  "data_access": {
    "read_scopes": ["my_app", "shared_data"],
    "write_scope": "my_app",
    "cross_app_policy": "explicit"
  },
  
  "managed_indexes": {
    "tasks": [
      {
        "type": "regular",
        "name": "status_sort",
        "keys": {"status": 1, "created_at": -1},
        "options": {"background": true}
      }
    ],
    "knowledge_base": [
      {
        "type": "vectorSearch",
        "name": "embedding_vector_index",
        "definition": {
          "fields": [{
            "type": "vector",
            "path": "embedding",
            "numDimensions": 1536,
            "similarity": "cosine"
          }]
        }
      }
    ]
  },
  
  "auth": {
    "mode": "app",
    "policy": {
      "provider": "casbin",
      "required": true,
      "authorization": {
        "model": "rbac",
        "initial_policies": [
          ["admin", "tasks", "read"],
          ["admin", "tasks", "write"]
        ]
      }
    },
    "users": {
      "enabled": true,
      "allow_registration": true,
      "demo_users": [
        {"email": "admin@example.com", "password": "password123", "role": "admin"}
      ]
    }
  },
  
  "embedding_config": {
    "enabled": true,
    "max_tokens_per_chunk": 1000
  },
  
  "memory_config": {
    "enabled": true,
    "collection_name": "user_memories"
  },
  
  "websockets": {
    "realtime": {
      "path": "/ws",
      "description": "Real-time updates"
    }
  },
  
  "cors": {
    "enabled": true,
    "allow_origins": ["*"]
  },
  
  "observability": {
    "health_checks": {"enabled": true},
    "metrics": {"enabled": true},
    "logging": {"level": "INFO"}
  }
}
```

---

## Validation

Validate your manifest.json before using it:

```python
from mdb_engine.core import ManifestValidator

validator = ManifestValidator()
is_valid, error, paths = validator.validate(manifest_dict)

if not is_valid:
    print(f"Validation error: {error}")
    print(f"Paths: {paths}")
```

---

## Best Practices

1. **Start Minimal**: Begin with just required fields (`schema_version`, `slug`, `name`)
2. **Add Incrementally**: Add features as you need them
3. **Version Control**: Keep your manifest.json in git
4. **Validate Early**: Validate before deployment
5. **Use Examples**: Check `examples/` directory for real-world manifests
6. **Document Choices**: Use `description` field to explain configuration decisions

---

## Related Documentation

- [Quick Start Guide](QUICK_START.md) - Get started with manifest.json
- [Manifest Deep Dive](MANIFEST_DEEP_DIVE.md) - Comprehensive analysis and incremental adoption
- [Architecture](ARCHITECTURE.md) - How manifest.json works under the hood

---

**Remember**: Your `manifest.json` is the foundation of your application. Start simple, grow as needed!
