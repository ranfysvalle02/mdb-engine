# Security Deep Dive: MDB-Engine Security Architecture

**Comprehensive guide to security mechanisms, implementation details, and best practices**

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Security Architecture Overview](#security-architecture-overview)
3. [Database Scoping Security](#database-scoping-security)
4. [Routing Security](#routing-security)
5. [SSO Security](#sso-security)
6. [Multi-App Shared Authentication](#multi-app-shared-authentication)
7. [WebSocket Security](#websocket-security)
8. [Security Value Propositions](#security-value-propositions)
9. [Appendix A: Security Examples](#appendix-a-security-examples)
10. [Appendix B: Threat Model](#appendix-b-threat-model)
11. [Appendix C: Compliance Mapping](#appendix-c-compliance-mapping)

---

## Executive Summary

MDB-Engine implements a **defense-in-depth security architecture** with multiple layers protecting against common vulnerabilities and ensuring safe operation in multi-tenant environments. The security model is built on:

- **Database Scoping**: Automatic app-level data isolation with encrypted secrets
- **Routing Security**: Path-based isolation, middleware protection, and CSRF defense
- **SSO Security**: Centralized authentication with JWT tokens and role-based access
- **Multi-App Shared Auth**: Secure cross-app authentication with session binding

**Key Security Features:**
- ✅ Envelope encryption for app secrets (master key + DEK)
- ✅ Automatic `app_id` filtering (data isolation)
- ✅ Cross-app access control (manifest-based authorization)
- ✅ CSRF protection (double-submit cookie pattern)
- ✅ WebSocket ticket-based authentication
- ✅ Session binding (IP/fingerprint validation)
- ✅ Constant-time token comparison (timing attack prevention)

---

## Security Architecture Overview

### Defense-in-Depth Layers

```
┌─────────────────────────────────────────────────────────────┐
│                    Application Layer                         │
│  (Your FastAPI app using mdb-engine)                         │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│              Routing Security Layer                          │
│  • Path-based app isolation                                  │
│  • CSRF middleware (double-submit cookie)                    │
│  • Authentication middleware (JWT validation)                │
│  • Role-based access control                                 │
│  • Public route exemptions                                   │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│            Authentication & Authorization Layer              │
│  • SSO token validation (SharedAuthMiddleware)              │
│  • App-level token verification (AppSecretsManager)         │
│  • Role checking (per-app roles)                              │
│  • Session binding (IP/fingerprint)                         │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│              Database Scoping Layer                          │
│  • Collection name validation                               │
│  • Cross-app access control (read_scopes)                   │
│  • App token verification (constant-time)                   │
│  • Scope validation                                          │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│            Query Security Layer                              │
│  • Dangerous operator blocking ($where, $eval, etc.)        │
│  • Query complexity limits (depth, pipeline stages)          │
│  • Regex validation (ReDoS prevention)                       │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│          Resource Limits Layer                               │
│  • Query timeouts (default: 30s, max: 5min)                  │
│  • Result size limits (max: 10,000 docs)                    │
│  • Document size validation (16MB MongoDB limit)             │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│            Data Isolation Layer                              │
│  • Automatic app_id filtering (read operations)              │
│  • Automatic app_id injection (write operations)             │
│  • Multi-scope read support                                  │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│              MongoDB Layer                                  │
│  (Underlying MongoDB database)                              │
└──────────────────────────────────────────────────────────────┘
```

### Security Flow Diagram

```
User Request
    │
    ├─► Routing Layer
    │   ├─► Path validation (app isolation)
    │   ├─► CSRF token check (if POST/PUT/DELETE)
    │   └─► Public route check
    │
    ├─► Authentication Layer
    │   ├─► Extract JWT token (cookie or header)
    │   ├─► Validate token (SharedUserPool)
    │   ├─► Check session binding (IP/fingerprint)
    │   └─► Extract user roles (per-app)
    │
    ├─► Authorization Layer
    │   ├─► Check role requirements
    │   └─► Validate permissions (if using OSO/Casbin)
    │
    ├─► Database Access Layer
    │   ├─► Verify app_token (if app has secret)
    │   ├─► Validate read_scopes (cross-app access)
    │   ├─► Validate collection name (security)
    │   └─► Check cross-app authorization
    │
    ├─► Query Security Layer
    │   ├─► Validate query (dangerous operators)
    │   ├─► Check complexity (depth, stages)
    │   └─► Validate regex patterns
    │
    ├─► Resource Limits Layer
    │   ├─► Apply timeouts
    │   ├─► Cap result sizes
    │   └─► Validate document sizes
    │
    ├─► Data Isolation Layer
    │   └─► Inject app_id filters
    │
    └─► Execute Query
```

---

## Database Scoping Security

### Overview

Database scoping ensures **complete data isolation** between applications while enabling controlled cross-app data sharing. Every database operation is automatically scoped to the requesting app, preventing data leakage and unauthorized access.

### Core Mechanisms

#### 1. Automatic App ID Filtering

**Read Operations:**
All read operations automatically include an `app_id` filter, ensuring apps can only see their own data (or data from authorized apps).

```python
# User query:
docs = await db.users.find({"status": "active"})

# Actual query sent to MongoDB:
{
    "$and": [
        {"status": "active"},
        {"app_id": {"$in": ["my_app"]}}  # Automatically injected
    ]
}
```

**Write Operations:**
All write operations automatically add `app_id` to documents, ensuring data ownership.

```python
# User insert:
await db.users.insert_one({"name": "John", "email": "john@example.com"})

# Actual document inserted:
{
    "name": "John",
    "email": "john@example.com",
    "app_id": "my_app"  # Automatically added
}
```

#### 2. Envelope Encryption for App Secrets

**Architecture:**
```
Master Key (MK)
    │
    └─► Encrypts Data Encryption Keys (DEKs)
            │
            └─► Encrypts App Secrets
                    │
                    └─► Stored in _mdb_engine_app_secrets collection
```

**Security Properties:**
- **Master Key**: Stored in environment variable (`MDB_ENGINE_MASTER_KEY`)
- **DEK**: Per-app encryption key, encrypted with master key
- **App Secret**: 256-bit random secret, encrypted with DEK
- **Storage**: Encrypted secrets stored in `_mdb_engine_app_secrets` (not accessible via scoped wrapper)

**Implementation:**
```python
# App registration generates secret automatically
await engine.register_app({
    "slug": "my_app",
    "data_access": {
        "read_scopes": ["my_app", "shared_app"]
    }
})
# Secret generated: "xK9mP2qR7sT4uV6wY8zA1bC3dE5fG7hI9jK1lM3nO5pQ7rS9tU1vW3xY5zA"
# Stored encrypted in: _mdb_engine_app_secrets collection

# Runtime verification
db = engine.get_scoped_db(
    "my_app",
    app_token=os.getenv("MY_APP_SECRET")  # Required for apps with secrets
)
# Engine:
# 1. Retrieves encrypted secret from database
# 2. Decrypts DEK with master key
# 3. Decrypts secret with DEK
# 4. Compares provided token with decrypted secret (constant-time)
# 5. Returns scoped database if match
```

**Constant-Time Comparison:**
Uses `secrets.compare_digest()` to prevent timing attacks:

```python
# Secure comparison (constant-time)
if not secrets.compare_digest(provided_token, stored_secret):
    raise ValueError("Invalid app token")
```

#### 3. Cross-App Access Control

**Manifest-Level Authorization:**
Apps declare which other apps they can read from in their manifest:

```json
{
  "slug": "my_app",
  "data_access": {
    "read_scopes": ["my_app", "shared_app", "analytics_app"],
    "write_scope": "my_app",
    "cross_app_policy": "explicit"
  }
}
```

**Runtime Validation:**
When accessing another app's collection:

```python
# Access shared app's collection
shared_data = await db.get_collection("shared_app_users").find({})

# Engine automatically:
# 1. Extracts "shared_app" from collection name
# 2. Validates "shared_app" is in read_scopes
# 3. Includes "shared_app" in app_id filter
# 4. Logs cross-app access for audit
```

**Access Validation Flow:**
```
Collection Access Request
    │
    ├─► Verify app_token (if app has stored secret)
    │   ├─► Valid → Continue
    │   └─► Invalid → Raise ValueError
    │
    ├─► Extract app slug from collection name
    │   (e.g., "shared_app_data" → "shared_app")
    │
    ├─► Check if app is in read_scopes (from manifest)
    │   ├─► Yes → Allow access
    │   └─► No → Block access and log warning
    │
    └─► Log access (authorized or unauthorized)
```

#### 4. Collection Name Security

**Validation Rules:**
- **Format**: Alphanumeric, underscore, dot, hyphen
- **Length**: 1-255 characters
- **Start Character**: Must start with letter or underscore
- **Reserved Names**: Blocked (`apps_config`, `_mdb_engine_app_secrets`, etc.)
- **Reserved Prefixes**: Blocked (`system*`, `admin*`, `config*`, `local*`)
- **Path Traversal**: Blocked (`../`, `/`, `\`)

**Implementation:**
```python
# Collection name validation
COLLECTION_NAME_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.-]*$")

def _validate_collection_name(name: str) -> None:
    # Check format
    if not COLLECTION_NAME_PATTERN.match(name):
        raise ValueError(f"Invalid collection name format: '{name}'")
    
    # Check reserved names
    if name in RESERVED_COLLECTION_NAMES:
        raise ValueError(f"Collection name '{name}' is reserved")
    
    # Check reserved prefixes
    for prefix in RESERVED_COLLECTION_PREFIXES:
        if name.lower().startswith(prefix):
            raise ValueError(f"Collection name '{name}' uses reserved prefix '{prefix}'")
    
    # Check path traversal
    if ".." in name or "/" in name or "\\" in name:
        raise ValueError(f"Invalid collection name format: '{name}'")
```

### Security Benefits

1. **Complete Data Isolation**: Apps cannot access other apps' data without explicit authorization
2. **Encrypted Secrets**: App secrets never stored in plaintext
3. **Timing Attack Prevention**: Constant-time token comparison
4. **Audit Trail**: All cross-app access attempts logged
5. **Default Deny**: Unauthorized access blocked by default
6. **Defense in Depth**: Multiple layers (encryption, manifest config, runtime validation)

---

## Routing Security

### Overview

Routing security ensures that requests are properly isolated by app, authenticated, and protected against common web vulnerabilities like CSRF attacks.

### Core Mechanisms

#### 1. Path-Based App Isolation

**Multi-App Mounting:**
Apps are mounted at specific path prefixes, ensuring complete isolation:

```python
# Multi-app setup
app = engine.create_multi_app(
    apps=[
        {
            "slug": "auth-hub",
            "manifest": Path("./apps/auth-hub/manifest.json"),
            "path_prefix": "/auth-hub",  # Isolated at /auth-hub/*
        },
        {
            "slug": "app1",
            "manifest": Path("./apps/app1/manifest.json"),
            "path_prefix": "/app1",  # Isolated at /app1/*
        },
    ],
))
```

**Security Properties:**
- Each app has its own path prefix (`/auth-hub`, `/app1`, etc.)
- Routes are isolated - app1 cannot access app2's routes
- Each app has its own middleware stack
- Shared authentication works across apps via cookies

#### 2. CSRF Protection

**Double-Submit Cookie Pattern:**
MDB-Engine implements CSRF protection using the industry-standard double-submit cookie pattern:

```
1. Server sets CSRF token in cookie (HttpOnly=False, so JS can read)
2. Client includes same token in header (X-CSRF-Token) or form field
3. Server validates cookie token matches header/form token
4. Since attackers can't read cookies from other domains, they can't forge requests
```

**Implementation:**
```python
# CSRF middleware automatically enabled for shared auth mode
class CSRFMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        secret: str | None = None,
        exempt_routes: list[str] | None = None,
        cookie_name: str = "csrf_token",
        header_name: str = "X-CSRF-Token",
    ):
        # ...
    
    async def dispatch(self, request: Request, call_next):
        # Generate token if not present
        if not request.cookies.get(self.cookie_name):
            token = generate_csrf_token(self.secret)
            response.set_cookie(
                self.cookie_name,
                token,
                httponly=False,  # JS needs to read it
                samesite="lax",
                secure=True,  # HTTPS only
            )
        
        # Validate token for unsafe methods
        if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
            cookie_token = request.cookies.get(self.cookie_name)
            header_token = request.headers.get(self.header_name)
            
            if not cookie_token or cookie_token != header_token:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Invalid or missing CSRF token"}
                )
        
        return await call_next(request)
```

**Frontend Integration:**
```javascript
// Read CSRF token from cookie
function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
    return null;
}

// Include in requests
fetch('/api/data', {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': getCookie('csrf_token')  // Required
    },
    credentials: 'same-origin',
    body: JSON.stringify(data)
});
```

**Configuration:**
```json
{
  "auth": {
    "mode": "shared",
    "csrf_protection": {
      "enabled": true,
      "exempt_routes": ["/api/webhooks/*", "/health"],
      "token_ttl": 3600,
      "rotate_tokens": false
    }
  }
}
```

#### 3. Authentication Middleware

**SharedAuthMiddleware:**
Validates JWT tokens and populates user context:

```python
class SharedAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Extract token from cookie or header
        token = self._extract_token(request)
        
        if not token:
            if not is_public_route and require_role:
                return unauthorized_response("Authentication required")
            return await call_next(request)
        
        # Validate token
        user = await user_pool.validate_token(token)
        
        if not user:
            return unauthorized_response("Invalid or expired token")
        
        # Validate session binding (IP/fingerprint)
        binding_error = await self._validate_session_binding(request, token)
        if binding_error:
            return forbidden_response(binding_error)
        
        # Set user context
        request.state.user = user
        request.state.user_roles = get_user_roles_for_app(user, app_slug)
        
        # Check role requirement
        if require_role and not has_role(user, require_role):
            return forbidden_response("Insufficient permissions")
        
        return await call_next(request)
```

**Token Extraction:**
```python
def _extract_token(self, request: Request) -> str | None:
    # Try cookie first (preferred for web apps)
    token = request.cookies.get(self._cookie_name)  # "mdb_auth_token"
    if token:
        return token
    
    # Try Authorization header (for API clients)
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:]  # Remove "Bearer " prefix
    
    return None
```

#### 4. Public Route Exemptions

**Configuration:**
```json
{
  "auth": {
    "mode": "shared",
    "public_routes": [
      "/health",
      "/api/public/*",
      "/docs",
      "/openapi.json"
    ]
  }
}
```

**Implementation:**
```python
def _is_public_route(self, path: str) -> bool:
    """Check if path matches any public route pattern."""
    for pattern in self._public_routes:
        # Exact match
        if path == pattern:
            return True
        
        # Wildcard match
        if fnmatch.fnmatch(path, pattern):
            return True
        
        # Prefix match (for patterns ending with /*)
        if pattern.endswith("/*"):
            prefix = pattern[:-2]
            if path.startswith(prefix):
                return True
    
    return False
```

### Security Benefits

1. **Path Isolation**: Apps cannot access other apps' routes
2. **CSRF Protection**: Double-submit cookie pattern prevents CSRF attacks
3. **Token Validation**: JWT tokens validated on every request
4. **Session Binding**: IP/fingerprint validation prevents token theft
5. **Role-Based Access**: Per-app role checking
6. **Public Route Support**: Flexible public route configuration

---

## SSO Security

### Overview

Single Sign-On (SSO) allows users to authenticate once and access multiple applications. MDB-Engine implements SSO using centralized JWT tokens with per-app role management.

### Core Mechanisms

#### 1. Centralized User Pool

**Storage:**
Users are stored in `_mdb_engine_shared_users` collection:

```json
{
  "_id": "user123",
  "email": "user@example.com",
  "password_hash": "$2b$12$...",  // bcrypt hash
  "app_roles": {
    "auth-hub": ["admin"],
    "app1": ["viewer", "editor"],
    "app2": ["viewer"]
  },
  "created_at": ISODate("2024-01-01T12:00:00Z"),
  "last_login": ISODate("2024-01-15T10:30:00Z")
}
```

**Security Properties:**
- Passwords hashed with bcrypt (cost factor 12)
- Per-app roles stored in `app_roles` field
- JWT tokens signed with shared secret (`MDB_ENGINE_JWT_SECRET`)
- Tokens stored in HttpOnly cookies (XSS protection)

#### 2. JWT Token Management

**Token Structure:**
```json
{
  "user_id": "user123",
  "email": "user@example.com",
  "app_roles": {
    "auth-hub": ["admin"],
    "app1": ["viewer", "editor"],
    "app2": ["viewer"]
  },
  "iat": 1705315200,
  "exp": 1705401600,
  "session_id": "session-uuid"
}
```

**Token Generation:**
```python
# Generate JWT token
def create_jwt_token(user: dict, secret: str, expires_in: int = 86400) -> str:
    payload = {
        "user_id": user["_id"],
        "email": user["email"],
        "app_roles": user.get("app_roles", {}),
        "iat": int(time.time()),
        "exp": int(time.time()) + expires_in,
        "session_id": str(uuid.uuid4()),
    }
    
    token = jwt.encode(payload, secret, algorithm="HS256")
    return token
```

**Token Validation:**
```python
# Validate JWT token
def validate_token(token: str, secret: str) -> dict | None:
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        
        # Check expiration
        if payload["exp"] < time.time():
            return None
        
        # Retrieve user from database
        user = await db._mdb_engine_shared_users.find_one({"_id": payload["user_id"]})
        
        if not user:
            return None
        
        # Merge current app_roles from database (roles may have changed)
        user["app_roles"] = user.get("app_roles", {})
        
        return user
    except jwt.InvalidTokenError:
        return None
```

#### 3. Session Binding

**IP Binding:**
Prevents token theft by binding sessions to IP addresses:

```python
async def _validate_session_binding(self, request: Request, token: str) -> str | None:
    """Validate session binding (IP/fingerprint)."""
    if not self._session_binding:
        return None
    
    # Extract IP from token
    payload = jwt.decode(token, secret, algorithms=["HS256"], options={"verify_signature": False})
    token_ip = payload.get("ip_address")
    
    # Get current request IP
    client_ip = request.client.host
    
    # Check IP binding
    if self._session_binding.get("bind_ip") == "strict":
        if token_ip != client_ip:
            return "Session IP mismatch - re-authentication required"
    
    return None
```

**Fingerprint Binding:**
Optional browser fingerprint validation:

```python
# Browser fingerprint (sent from client)
fingerprint = request.headers.get("X-Client-Fingerprint")

if self._session_binding.get("bind_fingerprint") == "soft":
    token_fingerprint = payload.get("fingerprint")
    if token_fingerprint and token_fingerprint != fingerprint:
        logger.warning("Browser fingerprint mismatch")
```

**Configuration:**
```json
{
  "auth": {
    "mode": "shared",
    "session_binding": {
      "bind_ip": "strict",  // Reject if IP changes
      "bind_fingerprint": "soft",  // Log warning if fingerprint changes
      "allow_ip_change_with_reauth": true  // Allow IP change on re-authentication
    }
  }
}
```

#### 4. Role-Based Access Control

**Per-App Roles:**
Users can have different roles in different apps:

```python
# User roles structure
user = {
    "_id": "user123",
    "email": "user@example.com",
    "app_roles": {
        "auth-hub": ["admin"],  # Admin in auth-hub
        "app1": ["viewer", "editor"],  # Viewer and editor in app1
        "app2": ["viewer"]  # Only viewer in app2
    }
}

# Check role for current app
def get_user_roles_for_app(user: dict, app_slug: str) -> list[str]:
    return user.get("app_roles", {}).get(app_slug, [])

# Check if user has required role
def has_role(user: dict, app_slug: str, required_role: str) -> bool:
    user_roles = get_user_roles_for_app(user, app_slug)
    
    # Check direct role
    if required_role in user_roles:
        return True
    
    # Check role hierarchy (if configured)
    role_hierarchy = {
        "admin": ["editor", "viewer"],
        "editor": ["viewer"]
    }
    
    for role in user_roles:
        if required_role in role_hierarchy.get(role, []):
            return True
    
    return False
```

**Role Hierarchy:**
```json
{
  "auth": {
    "mode": "shared",
    "roles": ["viewer", "editor", "admin"],
    "role_hierarchy": {
      "admin": ["editor", "viewer"],
      "editor": ["viewer"]
    },
    "require_role": "viewer"
  }
}
```

### Security Benefits

1. **Centralized Authentication**: Single source of truth for user identity
2. **JWT Tokens**: Stateless authentication with expiration
3. **Session Binding**: IP/fingerprint validation prevents token theft
4. **Per-App Roles**: Fine-grained access control per application
5. **HttpOnly Cookies**: XSS protection for tokens
6. **Token Expiration**: Automatic token expiration reduces attack window

---

## Multi-App Shared Authentication

### Overview

Multi-app shared authentication enables SSO across multiple applications mounted under a single FastAPI instance, sharing authentication state via cookies and JWT tokens.

### Core Mechanisms

#### 1. Shared User Pool

**Architecture:**
```
┌─────────────────────────────────────────┐
│         Shared User Pool                │
│    (_mdb_engine_shared_users)          │
│                                         │
│  • Centralized user storage             │
│  • Per-app role management               │
│  • JWT token validation                 │
└──────────────┬──────────────────────────┘
               │
       ┌───────┴───────┐
       │               │
┌──────▼──────┐  ┌────▼──────┐
│   Auth Hub  │  │   App 1   │
│  (Port 8000)│  │ (Port 8001)│
│             │  │           │
│ • Login     │  │ • Validate│
│ • Register  │  │   tokens  │
│ • Manage    │  │ • Check   │
│   roles     │  │   roles   │
└─────────────┘  └───────────┘
```

**Implementation:**
```python
class SharedUserPool:
    def __init__(self, db: ScopedMongoWrapper, jwt_secret: str):
        self._db = db
        self._jwt_secret = jwt_secret
        self._collection = db._mdb_engine_shared_users
    
    async def validate_token(self, token: str) -> dict | None:
        """Validate JWT token and return user."""
        try:
            payload = jwt.decode(token, self._jwt_secret, algorithms=["HS256"])
            user_id = payload["user_id"]
            
            # Retrieve user from database
            user = await self._collection.find_one({"_id": user_id})
            
            if not user:
                return None
            
            # Merge app_roles from database (roles may have changed)
            user["app_roles"] = user.get("app_roles", {})
            
            return user
        except jwt.InvalidTokenError:
            return None
    
    async def create_user(self, email: str, password: str) -> dict:
        """Create new user."""
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(12))
        
        user = {
            "_id": str(uuid.uuid4()),
            "email": email,
            "password_hash": password_hash,
            "app_roles": {},
            "created_at": datetime.utcnow(),
        }
        
        await self._collection.insert_one(user)
        return user
    
    async def authenticate(self, email: str, password: str) -> dict | None:
        """Authenticate user and return user dict."""
        user = await self._collection.find_one({"email": email})
        
        if not user:
            return None
        
        if not bcrypt.checkpw(password.encode(), user["password_hash"]):
            return None
        
        return user
```

#### 2. Multi-App Mounting

**Setup:**
```python
# Create multi-app with shared authentication
app = engine.create_multi_app(
    apps=[
        {
            "slug": "auth-hub",
            "manifest": Path("./apps/auth-hub/manifest.json"),
            "path_prefix": "/auth-hub",
        },
        {
            "slug": "app1",
            "manifest": Path("./apps/app1/manifest.json"),
            "path_prefix": "/app1",
        },
        {
            "slug": "app2",
            "manifest": Path("./apps/app2/manifest.json"),
            "path_prefix": "/app2",
        },
    ],
)

# All apps share:
# - Same MongoDB database
# - Same JWT secret (MDB_ENGINE_JWT_SECRET)
# - Same user pool (_mdb_engine_shared_users)
# - Same cookie domain (for SSO)
```

**Manifest Configuration:**
```json
// auth-hub/manifest.json
{
  "slug": "auth-hub",
  "auth": {
    "mode": "shared",
    "roles": ["viewer", "editor", "admin"],
    "require_role": "viewer",
    "public_routes": ["/", "/login", "/register", "/health"]
  }
}

// app1/manifest.json
{
  "slug": "app1",
  "auth": {
    "mode": "shared",
    "auth_hub_url": "/auth-hub",  // Path prefix for SSO redirects
    "roles": ["viewer", "editor", "admin"],
    "require_role": "viewer",
    "public_routes": ["/health"]
  }
}
```

#### 3. SSO Flow

**Login Flow:**
```
1. User visits /auth-hub/login
2. Auth hub authenticates user (SharedUserPool.authenticate)
3. Auth hub generates JWT token (create_jwt_token)
4. Auth hub sets cookie: Set-Cookie: mdb_auth_token=<jwt>; HttpOnly; SameSite=Lax
5. User redirected to dashboard
```

**SSO Access Flow:**
```
1. User visits /app1/ (not logged in to app1)
2. App1 middleware checks for JWT cookie
3. Cookie found: mdb_auth_token=<jwt>
4. App1 validates token (SharedUserPool.validate_token)
5. Token valid → User authenticated
6. App1 checks user roles for app1
7. User has "viewer" role → Access granted
8. User sees app1 dashboard (no login required!)
```

**Logout Flow:**
```
1. User clicks logout in any app
2. App clears JWT cookie: Set-Cookie: mdb_auth_token=; Max-Age=0
3. User redirected to auth-hub login
4. All apps now see user as unauthenticated (cookie cleared)
```

#### 4. Cookie Configuration

**Security Settings:**
```python
# Set JWT cookie (secure by default)
response.set_cookie(
    "mdb_auth_token",
    jwt_token,
    httponly=True,  # XSS protection
    samesite="lax",  # CSRF protection
    secure=True,  # HTTPS only (auto-detected)
    max_age=86400,  # 24 hours
    path="/",  # Available to all apps
)
```

**SameSite=Lax:**
- Cookies sent on same-site requests (SSO works)
- Cookies NOT sent on cross-site requests (CSRF protection)
- Cookies sent on top-level navigation (login redirects work)

### Security Benefits

1. **Single Sign-On**: Login once, access all apps
2. **Shared Authentication**: Centralized user pool
3. **Per-App Roles**: Different roles per application
4. **Cookie Security**: HttpOnly, SameSite, Secure flags
5. **Token Validation**: JWT tokens validated on every request
6. **Session Binding**: IP/fingerprint validation (optional)

---

## WebSocket Security

### Overview

WebSocket connections require special security considerations. MDB-Engine implements ticket-based authentication for WebSocket connections, providing secure, single-use authentication tokens.

### Core Mechanisms

#### 1. Ticket-Based Authentication

**Architecture:**
```
┌─────────────────────────────────────────┐
│         Ticket Exchange Flow            │
│                                         │
│  1. User logs in → JWT cookie set       │
│  2. Client requests ticket → POST /auth/ticket
│  3. Server validates JWT → Generates ticket
│  4. Client connects WebSocket → ws://host/app/ws?ticket=<uuid>
│  5. Server validates ticket → Consumes ticket (single-use)
│  6. WebSocket connection established    │
└─────────────────────────────────────────┘
```

**Ticket Properties:**
- **Short TTL**: 10-second expiration reduces interception window
- **Single-Use**: Tickets consumed immediately after validation (prevents replay attacks)
- **In-Memory Storage**: No database lookups, faster validation
- **UUID Format**: Cryptographically secure UUID v4

**Implementation:**
```python
class WebSocketTicketStore:
    def __init__(self, ttl: int = 10):
        self._tickets: dict[str, dict] = {}
        self._ttl = ttl
    
    def create_ticket(self, user_id: str, user_email: str | None = None, app_slug: str | None = None) -> str:
        """Create a new ticket."""
        ticket_id = str(uuid.uuid4())
        
        self._tickets[ticket_id] = {
            "user_id": user_id,
            "user_email": user_email,
            "app_slug": app_slug,
            "exp": time.time() + self._ttl,
            "created_at": time.time(),
        }
        
        return ticket_id
    
    def validate_and_consume(self, ticket_id: str) -> dict | None:
        """Validate ticket and consume it (single-use)."""
        ticket = self._tickets.get(ticket_id)
        
        if not ticket:
            return None
        
        # Check expiration
        if ticket["exp"] < time.time():
            del self._tickets[ticket_id]
            return None
        
        # Consume ticket (single-use)
        del self._tickets[ticket_id]
        
        return ticket
```

**Ticket Endpoint:**
```python
@app.post("/auth/ticket")
async def get_websocket_ticket(request: Request):
    """Exchange JWT for WebSocket ticket."""
    # Extract JWT from cookie
    token = request.cookies.get("mdb_auth_token")
    
    if not token:
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required"}
        )
    
    # Validate JWT
    user = await user_pool.validate_token(token)
    
    if not user:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or expired token"}
        )
    
    # Generate ticket
    ticket = ticket_store.create_ticket(
        user_id=user["_id"],
        user_email=user.get("email"),
        app_slug=request.state.app_slug,
    )
    
    return JSONResponse({
        "ticket": ticket,
        "expires_in": 10
    })
```

**WebSocket Authentication:**
```python
async def authenticate_websocket(
    websocket: WebSocket,
    app_slug: str,
    require_auth: bool = True,
) -> tuple[str | None, str | None]:
    """Authenticate WebSocket connection using ticket."""
    # Extract ticket from query parameter or header
    ticket = websocket.query_params.get("ticket") or websocket.headers.get("X-WebSocket-Ticket")
    
    if not ticket:
        if require_auth:
            await websocket.close(code=1008, reason="Authentication required")
            return None, None
        return None, None
    
    # Validate and consume ticket
    ticket_data = ticket_store.validate_and_consume(ticket)
    
    if not ticket_data:
        await websocket.close(code=1008, reason="Invalid or expired ticket")
        return None, None
    
    # Return user info
    return ticket_data["user_id"], ticket_data.get("user_email")
```

#### 2. Origin Validation

**CORS Protection:**
WebSocket connections validate origin before accepting:

```python
async def validate_websocket_origin(
    websocket: WebSocket,
    app_slug: str,
    allowed_origins: list[str],
) -> bool:
    """Validate WebSocket origin against CORS config."""
    origin = websocket.headers.get("origin")
    
    if not origin:
        return False
    
    # Normalize origin
    origin_normalized = origin.rstrip("/")
    
    # Check against allowed origins
    for allowed in allowed_origins:
        allowed_normalized = allowed.rstrip("/")
        if origin_normalized == allowed_normalized:
            return True
    
    return False
```

**Configuration:**
```json
{
  "websockets": {
    "realtime": {
      "path": "/ws",
      "auth": {
        "required": true,
        "csrf_required": false  // Origin + SameSite provide sufficient protection
      }
    }
  },
  "cors": {
    "enabled": true,
    "allow_origins": [
      "https://yourdomain.com",
      "http://localhost:3000"
    ],
    "allow_credentials": true
  }
}
```

#### 3. App-Level Isolation

**Connection Management:**
Each app has its own `WebSocketConnectionManager` instance:

```python
class WebSocketConnectionManager:
    def __init__(self, app_slug: str):
        self._app_slug = app_slug
        self._connections: dict[str, WebSocket] = {}
    
    async def connect(self, websocket: WebSocket, user_id: str):
        """Connect WebSocket and store with user_id."""
        await websocket.accept()
        self._connections[user_id] = websocket
    
    async def disconnect(self, user_id: str):
        """Disconnect WebSocket."""
        if user_id in self._connections:
            await self._connections[user_id].close()
            del self._connections[user_id]
    
    async def broadcast_to_app(self, message: dict):
        """Broadcast message to all connections in this app."""
        for websocket in self._connections.values():
            try:
                await websocket.send_json(message)
            except Exception:
                pass  # Connection closed
```

### Security Benefits

1. **Ticket-Based Auth**: Short-lived, single-use tickets prevent replay attacks
2. **Origin Validation**: CORS validation prevents cross-origin attacks
3. **App Isolation**: Each app has its own connection manager
4. **No Database Lookups**: In-memory ticket storage for performance
5. **CSRF Protection**: Origin validation + SameSite cookies provide sufficient protection

---

## Security Value Propositions

### 1. Defense in Depth

**Multiple Security Layers:**
- Routing security (path isolation, CSRF)
- Authentication security (JWT validation, session binding)
- Database scoping (app isolation, cross-app access control)
- Query security (dangerous operator blocking, complexity limits)
- Resource limits (timeouts, result size limits)

**Benefit:** Even if one layer fails, other layers provide protection.

### 2. Zero-Trust Architecture

**Principle:** Never trust, always verify.

- **App Identity Verification**: Every app must provide valid `app_token`
- **User Authentication**: Every request validates JWT token
- **Cross-App Access**: Explicit authorization required (manifest `read_scopes`)
- **Collection Access**: Validated on every collection access

**Benefit:** Reduces attack surface and prevents unauthorized access.

### 3. Encrypted Secrets at Rest

**Envelope Encryption:**
- Master key encrypts DEKs
- DEKs encrypt app secrets
- Secrets never stored in plaintext

**Benefit:** Even if database is compromised, secrets remain encrypted.

### 4. Automatic Data Isolation

**App ID Filtering:**
- All reads automatically filtered by `app_id`
- All writes automatically tagged with `app_id`
- No manual filtering required

**Benefit:** Prevents accidental data leakage between apps.

### 5. Comprehensive Audit Logging

**Security Events Logged:**
- App token verification attempts
- Cross-app access attempts (authorized/unauthorized)
- Collection name validation failures
- Query validation failures
- Resource limit violations

**Benefit:** Enables security monitoring and incident response.

### 6. Secure Defaults

**Secure-by-Default Configuration:**
- CSRF protection enabled for shared auth
- App tokens required for apps with secrets
- Cross-app access denied by default
- Dangerous operators blocked
- Resource limits enforced

**Benefit:** Reduces configuration errors and security misconfigurations.

---

## Appendix A: Security Examples

### Example 1: Multi-App SSO Setup

**Complete working example:**

```python
# multi_app_main.py
from pathlib import Path
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_apps_db"
)
await engine.initialize()

app = engine.create_multi_app(
    apps=[
        {
            "slug": "auth-hub",
            "manifest": Path("./apps/auth-hub/manifest.json"),
            "path_prefix": "/auth-hub",
        },
        {
            "slug": "app1",
            "manifest": Path("./apps/app1/manifest.json"),
            "path_prefix": "/app1",
        },
    ],
)

# Run with: uvicorn multi_app_main:app --host 0.0.0.0 --port 8000
```

**auth-hub/manifest.json:**
```json
{
  "slug": "auth-hub",
  "auth": {
    "mode": "shared",
    "roles": ["viewer", "editor", "admin"],
    "require_role": "viewer",
    "public_routes": ["/", "/login", "/register", "/health"]
  },
  "cors": {
    "enabled": true,
    "allow_origins": ["https://yourdomain.com"],
    "allow_credentials": true
  }
}
```

**app1/manifest.json:**
```json
{
  "slug": "app1",
  "auth": {
    "mode": "shared",
    "auth_hub_url": "/auth-hub",
    "roles": ["viewer", "editor", "admin"],
    "require_role": "viewer",
    "public_routes": ["/health"]
  },
  "cors": {
    "enabled": true,
    "allow_origins": ["https://yourdomain.com"],
    "allow_credentials": true
  }
}
```

### Example 2: Cross-App Data Access

**App A can read from App B:**

```json
// app_a/manifest.json
{
  "slug": "app_a",
  "data_access": {
    "read_scopes": ["app_a", "app_b"],  // Can read from app_b
    "write_scope": "app_a"
  }
}
```

```python
# app_a code
db = engine.get_scoped_db(
    "app_a",
    app_token=os.getenv("APP_A_SECRET")
)

# Access app_b's data
app_b_users = await db.get_collection("app_b_users").find({})
# ✅ Works - app_b is in read_scopes

# Try to access app_c's data
app_c_data = await db.get_collection("app_c_data").find({})
# ❌ Fails - app_c not in read_scopes
# Raises: ValueError("Access to collection 'app_c_data' not authorized")
```

### Example 3: WebSocket with Ticket Authentication

**Client-side:**
```javascript
// Step 1: Login (existing flow)
await fetch('/auth-hub/login', {
    method: 'POST',
    credentials: 'include',
    body: JSON.stringify({ email, password })
});

// Step 2: Exchange JWT for ticket
async function getWebSocketTicket() {
    const response = await fetch('/auth/ticket', {
        method: 'POST',
        credentials: 'include',  // Sends JWT cookie
    });
    const data = await response.json();
    return data.ticket;
}

// Step 3: Connect WebSocket with ticket
const ticket = await getWebSocketTicket();
const ws = new WebSocket(`wss://api.example.com/app1/ws?ticket=${ticket}`);

ws.onopen = () => {
    console.log('WebSocket connected securely');
};

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    console.log('Received:', data);
};
```

**Server-side:**
```python
from mdb_engine.routing.websockets import register_message_handler

async def handle_realtime_message(websocket: WebSocket, message: dict):
    """Handle WebSocket messages."""
    user_id = websocket.state.user_id
    
    # Process message
    response = {"status": "received", "user_id": user_id}
    await websocket.send_json(response)

# Register handler
register_message_handler("app1", "realtime", handle_realtime_message)
```

### Example 4: Session Binding Configuration

**Strict IP binding:**
```json
{
  "auth": {
    "mode": "shared",
    "session_binding": {
      "bind_ip": "strict",  // Reject if IP changes
      "allow_ip_change_with_reauth": false
    }
  }
}
```

**Soft fingerprint binding:**
```json
{
  "auth": {
    "mode": "shared",
    "session_binding": {
      "bind_fingerprint": "soft"  // Log warning if fingerprint changes
    }
  }
}
```

---

## Appendix B: Threat Model

### Threat: Unauthorized Data Access

**Attack:** App A tries to access App B's data without authorization.

**Mitigation:**
1. App token verification (proves app identity)
2. Cross-app access control (manifest `read_scopes`)
3. Collection name validation (extracts app slug)
4. Runtime scope validation (checks authorization)
5. Audit logging (logs unauthorized attempts)

**Result:** Unauthorized access blocked and logged.

### Threat: CSRF Attacks

**Attack:** Malicious website makes requests to your API using user's cookies.

**Mitigation:**
1. CSRF middleware (double-submit cookie pattern)
2. SameSite=Lax cookies (prevents cross-site cookie sending)
3. Origin validation (CORS checks)
4. Token validation (cookie token must match header token)

**Result:** CSRF attacks prevented.

### Threat: Token Theft

**Attack:** Attacker steals JWT token and uses it to impersonate user.

**Mitigation:**
1. HttpOnly cookies (prevents JavaScript access)
2. Session binding (IP/fingerprint validation)
3. Token expiration (reduces attack window)
4. Secure cookies (HTTPS only)

**Result:** Token theft mitigated.

### Threat: NoSQL Injection

**Attack:** Attacker injects malicious MongoDB operators in queries.

**Mitigation:**
1. Dangerous operator blocking (`$where`, `$eval`, etc.)
2. Query complexity limits (depth, pipeline stages)
3. Regex validation (ReDoS prevention)
4. Input validation (collection names, query structure)

**Result:** Injection attacks blocked.

### Threat: Resource Exhaustion

**Attack:** Attacker sends expensive queries to exhaust server resources.

**Mitigation:**
1. Query timeouts (default: 30s, max: 5min)
2. Result size limits (max: 10,000 docs)
3. Document size validation (16MB limit)
4. Query complexity limits

**Result:** Resource exhaustion prevented.

### Threat: Cross-App Data Leakage

**Attack:** App accidentally accesses another app's data.

**Mitigation:**
1. Automatic `app_id` filtering (all reads scoped)
2. Automatic `app_id` injection (all writes tagged)
3. Cross-app access control (explicit authorization required)
4. Collection name validation (prevents path traversal)

**Result:** Data leakage prevented.

---

## Appendix C: Compliance Mapping

### SOC 2

**Requirements:**
- ✅ **Access Controls**: Collection name validation, cross-app access control
- ✅ **Query Validation**: Dangerous operator blocking, complexity limits
- ✅ **Resource Limits**: Timeouts, result size limits, document size validation
- ✅ **Audit Logging**: Security events logged with context
- ✅ **Data Isolation**: Automatic app-level scoping

**Evidence:**
- Security logs showing access control enforcement
- Query validation logs showing blocked dangerous operators
- Resource limit logs showing enforced limits
- Cross-app access logs showing authorization checks

### GDPR

**Requirements:**
- ✅ **Data Isolation**: Automatic app-level scoping ensures data separation
- ✅ **Access Controls**: Cross-app access restrictions
- ✅ **Audit Logging**: All access attempts logged
- ⚠️ **Right to Deletion**: Future feature
- ⚠️ **Data Export**: Future feature

**Evidence:**
- Data isolation logs showing app-level filtering
- Access control logs showing cross-app access attempts
- Audit logs showing all data access

### OWASP Top 10

**A03:2021 – Injection:**
- ✅ NoSQL injection prevention via query validation
- ✅ Dangerous operator blocking
- ✅ Input validation (collection names, query structure)

**A04:2021 – Insecure Design:**
- ✅ Defense in depth with multiple security layers
- ✅ Secure defaults (CSRF enabled, tokens required)
- ✅ Zero-trust architecture

**A05:2021 – Security Misconfiguration:**
- ✅ Secure defaults
- ✅ Validation and limits
- ✅ Configuration validation

**A09:2021 – Security Logging:**
- ✅ Comprehensive security event logging
- ✅ Access attempt logging
- ✅ Security violation logging

### HIPAA (Healthcare)

**Requirements:**
- ✅ **Access Controls**: Strict access controls and data isolation
- ✅ **Audit Logging**: Comprehensive audit trails
- ⚠️ **Encryption at Rest**: Future feature
- ⚠️ **Encryption in Transit**: TLS/SSL enforcement (infrastructure-level)

**Evidence:**
- Access control logs showing healthcare data access
- Audit logs showing all data access attempts
- Data isolation logs showing patient data separation

---

## Conclusion

MDB-Engine provides a comprehensive security architecture with multiple layers of defense, ensuring safe operation in multi-tenant environments. The security model is built on:

- **Database Scoping**: Complete data isolation with encrypted secrets
- **Routing Security**: Path-based isolation with CSRF protection
- **SSO Security**: Centralized authentication with JWT tokens
- **Multi-App Shared Auth**: Secure cross-app authentication

**Key Takeaways:**
1. Defense in depth provides multiple security layers
2. Zero-trust architecture ensures verification at every step
3. Encrypted secrets protect app credentials
4. Automatic data isolation prevents data leakage
5. Comprehensive audit logging enables security monitoring
6. Secure defaults reduce configuration errors

**Next Steps:**
- Review [Security Guide](SECURITY.md) for detailed security features
- Review [App Authentication Guide](APP_AUTHENTICATION.md) for app-level authentication
- Review [SSO Multi-App Setup Guide](guides/SSO_MULTI_APP_SETUP.md) for SSO configuration
- Review [WebSocket Security Guide](guides/WEBSOCKET_SECURITY_ELEGANT_SOLUTION.md) for WebSocket security

---

**Last Updated**: 2025-02-05  
**Version**: 1.0.0  
**Maintainer**: MongoDB Engine Team
