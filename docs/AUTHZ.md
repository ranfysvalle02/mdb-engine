# Authorization (AuthZ) System

Comprehensive guide to the pluggable authorization system in MDB_ENGINE.

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [AuthorizationProvider Protocol](#authorizationprovider-protocol)
- [Built-in Implementations](#built-in-implementations)
  - [CasbinAdapter](#casbinadapter)
  - [OsoAdapter](#osoadapter)
- [Usage](#usage)
  - [Basic Usage](#basic-usage)
  - [FastAPI Dependencies](#fastapi-dependencies)
  - [Manual Authorization Checks](#manual-authorization-checks)
- [Configuration](#configuration)
  - [Manifest Configuration](#manifest-configuration)
  - [Environment Variables](#environment-variables)
- [Extending the System](#extending-the-system)
  - [Creating a Custom Provider](#creating-a-custom-provider)
  - [Registering a Custom Provider](#registering-a-custom-provider)
- [Best Practices](#best-practices)
- [API Reference](#api-reference)

## Overview

MDB_ENGINE uses a **pluggable authorization architecture** based on the Strategy pattern. This allows you to:

- **Swap authorization backends** without changing application code
- **Use different providers** for different apps (Casbin, OSO, or custom)
- **Extend the system** with your own authorization logic
- **Test easily** by mocking the `AuthorizationProvider` interface

The system is built around a simple protocol (`AuthorizationProvider`) that all authorization providers must implement. The engine and FastAPI dependencies work with this protocol, not specific implementations.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Application Code                          │
│  (FastAPI routes, business logic, etc.)                     │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        │ Uses
                        ▼
┌─────────────────────────────────────────────────────────────┐
│              AuthorizationProvider Protocol                  │
│  async def check(subject, resource, action, user_object)     │
└───────────────────────┬─────────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        │               │               │
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ CasbinAdapter│ │ OsoAdapter    │ │ CustomAdapter│
│              │ │               │ │              │
│ Uses Casbin  │ │ Uses OSO Cloud│ │ Your Logic   │
│ AsyncEnforcer│ │ / Polar       │ │              │
└──────────────┘ └──────────────┘ └──────────────┘
```

**Key Design Principles:**

1. **Protocol-Based**: Uses Python's `Protocol` for structural subtyping (duck typing)
2. **Async-First**: All authorization checks are asynchronous
3. **Caching**: Built-in adapters include result caching for performance
4. **Pluggable**: Easy to swap providers via configuration or code
5. **Type-Safe**: Full type hints for IDE support and type checking

## AuthorizationProvider Protocol

The `AuthorizationProvider` protocol defines the contract that all authorization providers must follow:

```python
from typing import Protocol, Dict, Any, Optional

class AuthorizationProvider(Protocol):
    """
    Defines the "contract" for any pluggable authorization provider.
    """

    async def check(
        self,
        subject: str,
        resource: str,
        action: str,
        user_object: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Checks if a subject is allowed to perform an action on a resource.
        
        Args:
            subject: The entity requesting access (typically user ID or email)
            resource: The resource being accessed (e.g., "documents", "admin_panel")
            action: The action being performed (e.g., "read", "write", "delete")
            user_object: Optional full user object for context-aware authorization
        
        Returns:
            True if authorized, False otherwise
        """
        ...
```

**Protocol Requirements:**

- Must implement `async def check(...)` method
- Must accept `subject`, `resource`, `action` as strings
- Must accept optional `user_object` for context
- Must return `bool` (True = authorized, False = denied)

**Note:** This is a `Protocol`, not an abstract base class. Any class that implements the `check` method signature will satisfy the protocol (structural subtyping).

## Built-in Implementations

### CasbinAdapter

**Location:** `mdb_engine.auth.provider.CasbinAdapter`

**Description:** Implements authorization using [Casbin](https://casbin.org/), a powerful access control library supporting ACL, RBAC, ABAC, and more.

**Features:**
- MongoDB-backed policy storage
- Built-in caching (configurable TTL)
- Thread pool execution (non-blocking)
- Support for RBAC and ACL models
- Automatic policy loading from database

**Setup via Manifest (Recommended):**

```json
{
  "auth_policy": {
    "provider": "casbin",
    "authorization": {
      "model": "rbac",
      "policies_collection": "casbin_policies",
      "default_roles": ["user", "admin"]
    }
  }
}
```

**Manual Setup:**

```python
from mdb_engine.auth import CasbinAdapter, create_casbin_enforcer

# Create Casbin enforcer with MongoDB adapter
enforcer = await create_casbin_enforcer(
    db=engine.get_database(),
    model="rbac",  # or "acl" or path to model file
    policies_collection="casbin_policies"
)

# Create adapter
authz_provider = CasbinAdapter(enforcer)

# Set on app state
app.state.authz_provider = authz_provider
```

**Casbin Model Types:**

- `"rbac"` - Role-Based Access Control (default)
- `"acl"` - Access Control List
- `"/path/to/model.conf"` - Custom model file

**Policy Format (RBAC):**

```
# Policies: subject (user/role), resource, action
p, admin, documents, read
p, admin, documents, write
p, user, documents, read

# Role assignments: user, role
g, alice@example.com, admin
g, bob@example.com, user
```

**Additional Methods:**

The `CasbinAdapter` also provides helper methods for policy management:

```python
# Add policy
await authz_provider.add_policy("admin", "documents", "delete")

# Add role for user
await authz_provider.add_role_for_user("alice@example.com", "admin")

# Check if policy exists
has_policy = await authz_provider.has_policy("admin", "documents", "read")

# Check if user has role
has_role = await authz_provider.has_role_for_user("alice@example.com", "admin")

# Remove role
await authz_provider.remove_role_for_user("alice@example.com", "admin")

# Clear cache
await authz_provider.clear_cache()
```

### OsoAdapter

**Location:** `mdb_engine.auth.provider.OsoAdapter`

**Description:** Implements authorization using [OSO Cloud](https://www.osohq.com/) or the OSO library, which uses the Polar policy language.

**Features:**
- OSO Cloud integration (SaaS)
- OSO library support (self-hosted)
- Built-in caching
- Resource-based authorization
- Fact-based policy management

**Setup via Manifest:**

```json
{
  "auth_policy": {
    "provider": "oso",
    "authorization": {
      "api_key": "${OSO_AUTH}",
      "url": "${OSO_URL}",
      "initial_roles": [
        {
          "user": "alice@example.com",
          "role": "admin",
          "resource": "documents"
        }
      ]
    }
  }
}
```

**Environment Variables:**

```bash
export OSO_AUTH="your-oso-api-key"
export OSO_URL="https://cloud.osohq.com"  # Optional, defaults to production
```

**Manual Setup:**

```python
from mdb_engine.auth import OsoAdapter
from oso_cloud import Oso

# Create OSO Cloud client
oso_client = Oso(api_key=os.getenv("OSO_AUTH"))

# Create adapter
authz_provider = OsoAdapter(oso_client)

# Set on app state
app.state.authz_provider = authz_provider
```

**OSO Policy Format (Polar):**

```polar
# Define resource types
actor User {}

resource Document {
    permissions = ["read", "write", "delete"];
    roles = ["admin", "editor", "viewer"];
}

# Define authorization rules
allow(actor: User, action, resource: Document) if
    has_role(actor, "admin", resource);

allow(actor: User, "read", resource: Document) if
    has_role(actor, "viewer", resource) or
    has_role(actor, "editor", resource);

allow(actor: User, "write", resource: Document) if
    has_role(actor, "editor", resource);
```

**Additional Methods:**

```python
# Add role for user (with resource context)
await authz_provider.add_role_for_user(
    "alice@example.com",
    "admin",
    "documents"  # resource
)

# Add permission policy
await authz_provider.add_policy("admin", "documents", "read")

# Clear cache
await authz_provider.clear_cache()
```

## Usage

### Basic Usage

**1. Get the Authorization Provider:**

```python
from mdb_engine.auth import get_authz_provider
from fastapi import Depends, Request

@app.get("/protected")
async def protected_route(
    request: Request,
    authz: AuthorizationProvider = Depends(get_authz_provider)
):
    # Use the provider
    allowed = await authz.check(
        subject="user@example.com",
        resource="documents",
        action="read"
    )
    
    if not allowed:
        raise HTTPException(status_code=403, detail="Access denied")
    
    return {"message": "Access granted"}
```

**2. Direct Access from App State:**

```python
@app.get("/protected")
async def protected_route(request: Request):
    authz = request.app.state.authz_provider
    
    allowed = await authz.check(
        subject="user@example.com",
        resource="documents",
        action="read"
    )
    
    if not allowed:
        raise HTTPException(status_code=403)
    
    return {"message": "OK"}
```

### FastAPI Dependencies

MDB_ENGINE provides several ready-to-use FastAPI dependencies:

**1. `require_permission` - Check Specific Permission:**

```python
from mdb_engine.auth import require_permission, get_current_user
from fastapi import Depends

@app.post("/documents")
async def create_document(
    document: dict,
    user: dict = Depends(require_permission("documents", "create"))
):
    # User is guaranteed to have "create" permission on "documents"
    return await create_document_in_db(document)
```

**2. `require_admin` - Require Admin Access:**

```python
from mdb_engine.auth import require_admin

@app.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    admin: dict = Depends(require_admin)
):
    # Only admins can access this route
    await delete_user_from_db(user_id)
    return {"deleted": user_id}
```

**3. `require_admin_or_developer` - Multiple Roles:**

```python
from mdb_engine.auth import require_admin_or_developer

@app.post("/apps")
async def upload_app(
    app_data: dict,
    user: dict = Depends(require_admin_or_developer)
):
    # Admins or developers can upload apps
    return await register_app(app_data)
```

**4. Custom Permission Check with User Context:**

```python
from mdb_engine.auth import get_authz_provider, get_current_user
from fastapi import Depends

@app.get("/documents/{doc_id}")
async def get_document(
    doc_id: str,
    user: dict = Depends(get_current_user),
    authz: AuthorizationProvider = Depends(get_authz_provider)
):
    # Check permission with full user context
    allowed = await authz.check(
        subject=user.get("email", "anonymous"),
        resource=f"document:{doc_id}",
        action="read",
        user_object=user  # Pass full user object for context-aware auth
    )
    
    if not allowed:
        raise HTTPException(status_code=403)
    
    return await get_document_from_db(doc_id)
```

### Manual Authorization Checks

You can also perform authorization checks manually:

```python
from mdb_engine.auth import get_authz_provider

async def can_user_access_resource(user_email: str, resource: str, action: str) -> bool:
    """Helper function to check authorization."""
    # Get provider from app state (requires app context)
    authz = app.state.authz_provider
    
    return await authz.check(
        subject=user_email,
        resource=resource,
        action=action
    )

# Usage
if await can_user_access_resource("alice@example.com", "documents", "write"):
    # Allow operation
    pass
else:
    # Deny operation
    raise PermissionError("Access denied")
```

## Configuration

### Manifest Configuration

Authorization is configured in your app's `manifest.json`:

**Casbin Configuration:**

```json
{
  "slug": "my_app",
  "auth": {
    "policy": {
      "provider": "casbin",
      "required": true,
      "allow_anonymous": false,
      "authorization": {
        "model": "rbac",
        "policies_collection": "casbin_policies",
        "default_roles": ["user", "admin", "editor"],
        "link_users_roles": true
      }
    }
  }
}
```

**OSO Configuration:**

```json
{
  "slug": "my_app",
  "auth_policy": {
    "provider": "oso",
    "required": true,
    "allow_anonymous": false,
    "authorization": {
      "api_key": "${OSO_AUTH}",
      "url": "${OSO_URL}",
      "initial_roles": [
        {
          "user": "admin@example.com",
          "role": "admin",
          "resource": "documents"
        },
        {
          "user": "editor@example.com",
          "role": "editor",
          "resource": "documents"
        }
      ]
    }
  }
}
```

**Custom Provider:**

```json
{
  "slug": "my_app",
  "auth_policy": {
    "provider": "custom",
    "required": true
  }
}
```

Then set the provider manually in your application code.

### Environment Variables

**For Casbin:**

No special environment variables required. Casbin uses MongoDB for storage.

**For OSO:**

```bash
# Required: OSO Cloud API key
export OSO_AUTH="your-api-key-here"

# Optional: OSO Cloud URL (defaults to production)
export OSO_URL="https://cloud.osohq.com"

# For OSO Dev Server (local development)
export OSO_URL="http://localhost:8000"
```

**General:**

```bash
# Secret key for JWT tokens (if using authentication)
export FLASK_SECRET_KEY="your-secret-key"

# Authorization cache TTL (seconds)
export AUTHZ_CACHE_TTL=300  # Default: 5 minutes
```

## Extending the System

### Creating a Custom Provider

To create a custom authorization provider, implement the `AuthorizationProvider` protocol:

**Example: Simple Rule-Based Provider**

```python
from typing import Dict, Any, Optional
from mdb_engine.auth import AuthorizationProvider

class SimpleRuleProvider:
    """
    Simple rule-based authorization provider.
    Implements AuthorizationProvider protocol.
    """
    
    def __init__(self, rules: Dict[str, Dict[str, list]]):
        """
        Initialize with rules.
        
        Args:
            rules: Dict mapping resources to actions to allowed subjects
                  Example: {"documents": {"read": ["user", "admin"]}}
        """
        self.rules = rules
    
    async def check(
        self,
        subject: str,
        resource: str,
        action: str,
        user_object: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Check if subject is allowed to perform action on resource.
        """
        # Get rules for resource
        resource_rules = self.rules.get(resource, {})
        
        # Get allowed subjects for action
        allowed_subjects = resource_rules.get(action, [])
        
        # Check if subject is in allowed list
        if subject in allowed_subjects:
            return True
        
        # Check if user has role in allowed list (from user_object)
        if user_object:
            user_role = user_object.get("role")
            if user_role and user_role in allowed_subjects:
                return True
        
        return False

# Usage
rules = {
    "documents": {
        "read": ["user", "admin", "viewer"],
        "write": ["admin", "editor"],
        "delete": ["admin"]
    },
    "admin_panel": {
        "access": ["admin"]
    }
}

custom_provider = SimpleRuleProvider(rules)
```

**Example: Database-Backed Provider**

```python
from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import Dict, Any, Optional

class DatabaseAuthProvider:
    """
    Authorization provider that checks permissions from MongoDB.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase, collection: str = "permissions"):
        self.db = db
        self.collection = db[collection]
    
    async def check(
        self,
        subject: str,
        resource: str,
        action: str,
        user_object: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Check permission from database.
        """
        # Query database for permission
        permission = await self.collection.find_one({
            "subject": subject,
            "resource": resource,
            "action": action
        })
        
        if permission:
            return permission.get("allowed", False)
        
        # Check role-based permissions if user_object provided
        if user_object:
            role = user_object.get("role")
            if role:
                role_permission = await self.collection.find_one({
                    "subject": role,  # Check role as subject
                    "resource": resource,
                    "action": action
                })
                if role_permission:
                    return role_permission.get("allowed", False)
        
        return False
```

**Example: External API Provider**

```python
import httpx
from typing import Dict, Any, Optional

class ExternalAPIAuthProvider:
    """
    Authorization provider that calls an external API.
    """
    
    def __init__(self, api_url: str, api_key: str):
        self.api_url = api_url
        self.api_key = api_key
        self.client = httpx.AsyncClient()
    
    async def check(
        self,
        subject: str,
        resource: str,
        action: str,
        user_object: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Check permission via external API.
        """
        try:
            response = await self.client.post(
                f"{self.api_url}/check",
                json={
                    "subject": subject,
                    "resource": resource,
                    "action": action,
                    "user_object": user_object
                },
                headers={"Authorization": f"Bearer {self.api_key}"}
            )
            response.raise_for_status()
            result = response.json()
            return result.get("allowed", False)
        except Exception as e:
            logger.error(f"Authorization check failed: {e}")
            return False  # Fail closed
    
    async def close(self):
        """Clean up HTTP client."""
        await self.client.aclose()
```

### Registering a Custom Provider

**Option 1: Via App State (Manual)**

```python
from fastapi import FastAPI

app = FastAPI()

# Create your custom provider
custom_provider = SimpleRuleProvider(rules)

# Set on app state
app.state.authz_provider = custom_provider
```

**Option 2: Via Engine (During App Registration)**

```python
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(mongo_uri="...", db_name="...")
await engine.initialize()

# Create custom provider
custom_provider = DatabaseAuthProvider(engine.get_database())

# Set on engine (will be available to all apps)
engine.authz_provider = custom_provider
```

**Option 3: Via Factory Function (Recommended)**

Create a factory function that can be called from `setup_auth_from_manifest`:

```python
# In your custom module
from mdb_engine.auth import AuthorizationProvider
from typing import Dict, Any

async def initialize_custom_provider_from_manifest(
    engine,
    app_slug: str,
    auth_config: Dict[str, Any]
) -> Optional[AuthorizationProvider]:
    """
    Factory function to create custom provider from manifest.
    
    This follows the same pattern as CasbinAdapter and OsoAdapter.
    """
    auth_policy = auth_config.get("auth_policy", {})
    provider = auth_policy.get("provider", "casbin")
    
    # Only proceed if provider is "custom"
    if provider != "custom":
        return None
    
    # Get configuration
    authorization = auth_policy.get("authorization", {})
    rules = authorization.get("rules", {})
    
    # Create and return provider
    return SimpleRuleProvider(rules)

# Then in your app setup:
from mdb_engine.auth import setup_auth_from_manifest

# Modify setup_auth_from_manifest to call your factory
# Or call it directly:
custom_provider = await initialize_custom_provider_from_manifest(
    engine, "my_app", config
)
if custom_provider:
    app.state.authz_provider = custom_provider
```

## Best Practices

### 1. Use the Protocol, Not Concrete Types

**✅ Good:**

```python
from mdb_engine.auth import AuthorizationProvider

async def check_access(
    authz: AuthorizationProvider,
    user: str,
    resource: str,
    action: str
) -> bool:
    return await authz.check(user, resource, action)
```

**❌ Bad:**

```python
from mdb_engine.auth import CasbinAdapter

async def check_access(
    authz: CasbinAdapter,  # Too specific!
    user: str,
    resource: str,
    action: str
) -> bool:
    return await authz.check(user, resource, action)
```

### 2. Cache Authorization Results

Built-in adapters include caching, but if you create a custom provider:

```python
import asyncio
import time
from typing import Dict, Tuple

class CachedAuthProvider:
    def __init__(self, base_provider: AuthorizationProvider, ttl: int = 300):
        self.base_provider = base_provider
        self.ttl = ttl
        self._cache: Dict[Tuple[str, str, str], Tuple[bool, float]] = {}
        self._cache_lock = asyncio.Lock()
    
    async def check(self, subject: str, resource: str, action: str, user_object=None) -> bool:
        cache_key = (subject, resource, action)
        current_time = time.time()
        
        # Check cache
        async with self._cache_lock:
            if cache_key in self._cache:
                result, cached_time = self._cache[cache_key]
                if current_time - cached_time < self.ttl:
                    return result
                del self._cache[cache_key]
        
        # Check with base provider
        result = await self.base_provider.check(subject, resource, action, user_object)
        
        # Cache result
        async with self._cache_lock:
            self._cache[cache_key] = (result, current_time)
        
        return result
```

### 3. Fail Closed (Deny by Default)

**✅ Good:**

```python
async def check(self, subject: str, resource: str, action: str, user_object=None) -> bool:
    try:
        # Check permission
        return await self._check_permission(subject, resource, action)
    except Exception as e:
        logger.error(f"Authorization check failed: {e}")
        return False  # Fail closed - deny access on error
```

**❌ Bad:**

```python
async def check(self, subject: str, resource: str, action: str, user_object=None) -> bool:
    try:
        return await self._check_permission(subject, resource, action)
    except Exception:
        return True  # Fail open - dangerous!
```

### 4. Use Context-Aware Authorization

Pass `user_object` for context-aware decisions:

```python
# Check with user context
allowed = await authz.check(
    subject=user["email"],
    resource="document:123",
    action="read",
    user_object={
        "email": user["email"],
        "role": user["role"],
        "tenant_id": user["tenant_id"],  # Multi-tenant context
        "permissions": user.get("permissions", [])
    }
)
```

### 5. Log Authorization Decisions

```python
import logging

logger = logging.getLogger(__name__)

async def check(self, subject: str, resource: str, action: str, user_object=None) -> bool:
    result = await self._check_permission(subject, resource, action)
    
    # Log decision
    logger.info(
        f"Authorization check: subject={subject}, resource={resource}, "
        f"action={action}, allowed={result}"
    )
    
    return result
```

### 6. Test with Mock Providers

```python
from unittest.mock import AsyncMock

# In tests
mock_provider = AsyncMock(spec=AuthorizationProvider)
mock_provider.check.return_value = True

# Use in tests
app.state.authz_provider = mock_provider
```

## API Reference

### AuthorizationProvider Protocol

```python
class AuthorizationProvider(Protocol):
    async def check(
        self,
        subject: str,
        resource: str,
        action: str,
        user_object: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Check if subject is allowed to perform action on resource."""
        ...
```

### CasbinAdapter

```python
class CasbinAdapter:
    def __init__(self, enforcer: 'casbin.AsyncEnforcer')
    
    async def check(...) -> bool
    async def add_policy(*params) -> bool
    async def add_role_for_user(*params) -> bool
    async def has_policy(*params) -> bool
    async def has_role_for_user(*params) -> bool
    async def remove_role_for_user(*params) -> bool
    async def save_policy() -> bool
    async def clear_cache() -> None
```

### OsoAdapter

```python
class OsoAdapter:
    def __init__(self, oso_client: Any)
    
    async def check(...) -> bool
    async def add_policy(*params) -> bool
    async def add_role_for_user(*params) -> bool
    async def has_policy(*params) -> bool
    async def has_role_for_user(*params) -> bool
    async def remove_role_for_user(*params) -> bool
    async def save_policy() -> bool
    async def clear_cache() -> None
```

### FastAPI Dependencies

```python
async def get_authz_provider(request: Request) -> AuthorizationProvider
async def require_permission(obj: str, act: str, force_login: bool = True)
async def require_admin(...) -> Dict[str, Any]
async def require_admin_or_developer(...) -> Dict[str, Any]
```

## Related Documentation

- [Authentication & Authorization README](../mdb_engine/auth/README.md) - Detailed auth module documentation
- [Quick Start Guide](QUICK_START.md) - Getting started with MDB_ENGINE
- [Project Structure](../PROJECT_STRUCTURE.md) - Project organization

## Examples

See the `examples/` directory for complete working examples:

- `examples/chit_chat/` - Chat application with Casbin authorization
- `examples/oso_hello_world/` - OSO-based authorization example
- `examples/interactive_rag/` - RAG application with authorization

