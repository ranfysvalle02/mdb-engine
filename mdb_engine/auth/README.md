# Authentication & Authorization Module

MongoDB-backed authentication and authorization conveniences for MDB_ENGINE applications. The engine provides building blocks (JWT tokens, pluggable authorization providers, session management) without imposing specific authentication flows. Apps implement their own authentication (including OAuth) while leveraging the engine's MongoDB-backed conveniences.

## Philosophy

The engine provides **MongoDB-backed conveniences** without imposing solutions:

- **Building blocks, not complete solutions**: The engine provides JWT token management, authorization providers (Casbin/OSO), and session management - but apps implement their own authentication flows
- **MongoDB-first**: All auth data (policies, sessions, tokens) is stored in MongoDB, leveraging the engine's scoping and isolation features
- **Pluggable authorization**: Choose Casbin (MongoDB-backed RBAC) or OSO (Cloud or library) - both auto-configured from manifest
- **App-level flexibility**: Apps can implement OAuth, custom auth flows, or use the provided app-level user management utilities
- **Two auth modes**: Choose between per-app isolation (`mode: "app"`) or shared user pool with SSO (`mode: "shared"`)

## Auth Modes

MDB_ENGINE supports two authentication modes, configured in manifest.json:

### Per-App Auth (`mode: "app"`) - Default

Each app has isolated authentication. Users, tokens, and sessions are specific to each app.

```json
{
  "auth": {
    "mode": "app",
    "token_required": true
  }
}
```

**When to use:**
- Apps are independent
- Each app manages its own users
- No need for SSO between apps

### Shared Auth (`mode: "shared"`) - SSO

All apps share a central user pool. Users authenticate once and can access any app (subject to role requirements).

```json
{
  "auth": {
    "mode": "shared",
    "auth_hub_url": "http://localhost:8000",
    "related_apps": {
      "dashboard": "http://localhost:8001"
    },
    "roles": ["viewer", "editor", "admin"],
    "default_role": "viewer",
    "require_role": "viewer",
    "public_routes": ["/health", "/api/public"]
  }
}
```

**When to use:**
- Building a platform with multiple related apps
- You want Single Sign-On (SSO)
- You need per-app role management
- Apps should share user identity

**Shared auth fields:**
| Field | Description |
|-------|-------------|
| `roles` | Available roles for this app |
| `auth_hub_url` | URL of the authentication hub for SSO apps. Used for redirecting unauthenticated users to login. Can be overridden via `AUTH_HUB_URL` environment variable |
| `related_apps` | Map of related app slugs to their URLs for cross-app navigation. Keys are app slugs, values are URLs. Can be overridden via `{APP_SLUG_UPPER}_URL` environment variables |
| `default_role` | Role assigned to new users |
| `require_role` | Minimum role required to access app |
| `public_routes` | Routes that don't require authentication |

**How it works:**
1. Users are stored in `_mdb_engine_shared_users` collection
2. JWT tokens work across all apps (SSO)
3. `SharedAuthMiddleware` is auto-configured by `engine.create_app()`
4. User info is available via `request.state.user`

```python
# Accessing user in shared auth mode
@app.get("/protected")
async def protected(request: Request):
    user = request.state.user  # Populated by middleware
    roles = request.state.user_roles
    return {"email": user["email"], "roles": roles}
```

See `examples/multi_app_shared/` for a complete SSO example.

## Features

- **JWT Token Management**: Access and refresh token pairs with automatic lifecycle management
- **Pluggable Authorization**: Support for Casbin (MongoDB-backed) and OSO (Cloud or library) authorization providers
- **FastAPI Integration**: Ready-to-use dependencies for authentication and authorization
- **Session Management**: Multi-device session tracking with activity monitoring
- **Token Blacklisting**: Secure token revocation and expiration handling
- **App-Level User Management**: Utilities for app-specific user accounts and anonymous sessions
- **Security Middleware**: Built-in security headers and CSRF protection
- **Cookie Management**: Secure cookie handling for web applications

## Installation

The auth module is part of MDB_ENGINE. No additional installation required, but you may need:

```bash
pip install pyjwt casbin  # For JWT and Casbin support
```

## Configuration

### Environment Variables

```bash
# Required: Secret key for JWT signing (must be set, engine will fail to start if missing)
# Generate a secure key: python -c 'import secrets; print(secrets.token_urlsafe(32))'
export FLASK_SECRET_KEY="your-secret-key-here"

# Optional: Token TTL (seconds)
export ACCESS_TOKEN_TTL=3600      # Default: 1 hour
export REFRESH_TOKEN_TTL=2592000  # Default: 30 days

# Optional: Session limits
export MAX_SESSIONS_PER_USER=5
export SESSION_INACTIVITY_TIMEOUT=86400  # 24 hours
```

### Manifest Configuration

Configure authentication and authorization in your `manifest.json`. The system automatically creates a Casbin provider with MongoDB-backed policies:

```json
{
  "slug": "my_app",
  "auth_required": true,
  "auth": {
    "policy": {
      "provider": "casbin",
      "required": true,
      "allow_anonymous": false,
      "authorization": {
        "model": "rbac",
        "policies_collection": "casbin_policies",
        "link_users_roles": true,
        "default_roles": ["user", "admin"]
      }
    },
    "users": {
      "enabled": true,
      "strategy": "app_users"
    }
  }
}
```

**Key Features:**
- **Auto-created provider**: Casbin provider is automatically created from manifest (default)
- **MongoDB-backed**: Policies stored in MongoDB collection (default: `casbin_policies`)
- **App-level user management**: App-level users automatically get Casbin roles assigned
- **Zero boilerplate**: Just configure in manifest, everything works automatically

**OSO Support**: The engine also supports OSO Cloud and OSO library. Configure `provider: "oso"` in your manifest with OSO Cloud credentials, and the engine will auto-create an OSO adapter just like Casbin.

## Usage

### 1. Unified Auth Setup (Recommended)

With the unified auth system, just configure your manifest and call `setup_auth_from_manifest()` - everything is auto-created:

```python
from mdb_engine.auth import setup_auth_from_manifest
from fastapi import FastAPI

app = FastAPI()

@app.on_event("startup")
async def startup():
    # This automatically:
    # - Creates authorization provider (Casbin or OSO) with MongoDB adapter
    # - Sets up token management
    # - Links app-level users to authorization roles
    # - Configures security middleware
    await setup_auth_from_manifest(app, engine, "my_app")
```

The authorization provider is automatically available via `get_authz_provider` dependency:

```python
from mdb_engine.auth import get_authz_provider, get_current_user
from fastapi import Depends

@app.get("/protected")
async def protected_route(
    user: dict = Depends(get_current_user),
    authz: AuthorizationProvider = Depends(get_authz_provider)
):
    # Check permission using auto-created authorization provider (Casbin or OSO)
    has_access = await authz.check(
        subject=user.get("email", "anonymous"),
        resource="my_app",
        action="access"
    )
    if not has_access:
        raise HTTPException(status_code=403, detail="Access denied")
    return {"user_id": user["user_id"]}
```

### 2. JWT Token Management

#### Generate Token Pairs

```python
from mdb_engine.auth import generate_token_pair, encode_jwt_token

# Generate access + refresh token pair
access_token, refresh_token = generate_token_pair(
    user_id="user123",
    user_email="user@example.com",
    secret_key=SECRET_KEY
)

# Or encode custom token
token = encode_jwt_token(
    payload={"user_id": "user123", "role": "admin"},
    secret_key=SECRET_KEY,
    expires_in=3600
)
```

#### Decode and Validate Tokens

```python
from mdb_engine.auth import decode_jwt_token, extract_token_metadata

# Decode token
payload = decode_jwt_token(token, SECRET_KEY)

# Extract metadata
metadata = extract_token_metadata(payload)
user_id = metadata.get("user_id")
```

### 3. FastAPI Dependencies

#### Get Current User

```python
from mdb_engine.auth import get_current_user
from fastapi import Depends

@app.get("/profile")
async def get_profile(
    user: dict = Depends(get_current_user)
):
    if not user:
        raise HTTPException(status_code=401)
    return {"email": user["user_email"]}
```

#### Require Authentication

```python
from mdb_engine.auth import require_auth

@app.post("/data")
@require_auth
async def create_data(
    user: dict = Depends(get_current_user),
    data: dict = None
):
    # User is guaranteed to be authenticated here
    return {"created_by": user["user_id"]}
```

#### Require Admin

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

#### Require Permission

```python
from mdb_engine.auth import require_permission, get_authz_provider
from fastapi import Depends

@app.post("/documents")
async def create_document(
    document: dict,
    user: dict = Depends(get_current_user),
    authz: AuthorizationProvider = Depends(get_authz_provider)
):
    # Check permission
    has_permission = await require_permission(
        user["user_id"],
        "documents",
        "create",
        authz_provider=authz
    )

    if not has_permission:
        raise HTTPException(status_code=403, detail="Permission denied")

    return await create_document_in_db(document)
```

### 4. Authorization Providers

The engine supports two authorization providers, both auto-configured from manifest:

#### Casbin Adapter (Auto-Created from Manifest)

**Recommended**: Configure in manifest and let the system auto-create:

```json
{
  "auth": {
    "policy": {
      "provider": "casbin",
      "authorization": {
        "model": "rbac",
        "policies_collection": "casbin_policies"
      }
    }
  }
}
```

The provider is automatically created and available via `get_authz_provider` dependency.

**Manual Setup** (if needed):

```python
from mdb_engine.auth import CasbinAdapter, create_casbin_enforcer

# Create enforcer with MongoDB adapter (uses scoped database)
db = engine.get_scoped_db("my_app")
enforcer = await create_casbin_enforcer(
    db=db,
    model="rbac",
    policies_collection="casbin_policies"  # Will be app-scoped
)

# Create adapter
authz_provider = CasbinAdapter(enforcer)

# Set on app state
app.state.authz_provider = authz_provider
```

#### OSO Adapter (Auto-Created from Manifest)

**OSO Cloud** (Recommended): Configure in manifest with OSO Cloud credentials:

```json
{
  "auth": {
    "policy": {
      "provider": "oso",
      "authorization": {
        "api_key": "${OSO_AUTH}",
        "url": "${OSO_URL}",
        "initial_roles": [
          {"user": "admin@example.com", "role": "admin", "resource": "documents"}
        ]
      }
    }
  }
}
```

The OSO Cloud provider is automatically created from manifest, just like Casbin.

**OSO Library** (Manual Setup):

```python
from mdb_engine.auth import OsoAdapter
from oso import Oso

# Create OSO instance
oso = Oso()
oso.load_file("policy.polar")

# Create adapter
authz_provider = OsoAdapter(oso)

# Set on app state
app.state.authz_provider = authz_provider

# Check permission
allowed = await authz_provider.check(
    subject="user123",
    resource="documents",
    action="read"
)
```

### 5. Session Management

```python
from mdb_engine.auth import SessionManager

# Initialize session manager
session_mgr = SessionManager(db, collection_name="user_sessions")
await session_mgr.ensure_indexes()

# Create session
session = await session_mgr.create_session(
    user_id="user123",
    device_id="device456",
    refresh_jti="refresh_token_jti",
    device_info={"ip": "1.2.3.4", "user_agent": "Mozilla/5.0"}
)

# Update session activity
await session_mgr.update_activity("session_id", "device_id")

# Get user sessions
sessions = await session_mgr.get_user_sessions("user123")

# Revoke session
await session_mgr.revoke_session("session_id")
```

### 6. Token Blacklisting

```python
from mdb_engine.auth import TokenBlacklist

# Initialize blacklist
blacklist = TokenBlacklist(db, collection_name="token_blacklist")
await blacklist.ensure_indexes()

# Blacklist token
await blacklist.add_token("token_jti", expires_at=datetime.utcnow() + timedelta(hours=1))

# Check if token is blacklisted
is_blacklisted = await blacklist.is_blacklisted("token_jti")

# Cleanup expired tokens
await blacklist.cleanup_expired()
```

### 7. Token Lifecycle Management

```python
from mdb_engine.auth import (
    get_token_expiry_time,
    is_token_expiring_soon,
    should_refresh_token,
    refresh_access_token
)

# Check token expiry
expiry = get_token_expiry_time(token_payload)
is_expiring = is_token_expiring_soon(token_payload, threshold_seconds=300)

# Refresh token
if should_refresh_token(refresh_token_payload):
    new_access_token = await refresh_access_token(
        refresh_token=refresh_token,
        secret_key=SECRET_KEY,
        db=db
    )
```

### 8. Sub-Authentication (App-Level Users)

```python
from mdb_engine.auth import (
    create_app_user,
    authenticate_app_user,
    get_app_user,
    get_or_create_anonymous_user
)

# Create app-specific user
app_user = await create_app_user(
    db=db,
    app_slug="my_app",
    email="user@example.com",
    password="secure_password"
)

# Authenticate app user
user = await authenticate_app_user(
    db=db,
    app_slug="my_app",
    email="user@example.com",
    password="secure_password"
)

# Get or create anonymous user
anon_user = await get_or_create_anonymous_user(
    db=db,
    app_slug="my_app",
    device_id="device123"
)
```

### 9. Security Middleware

Security middleware is automatically added when using `setup_auth_from_manifest()`. If you need to add it manually:

```python
from mdb_engine.auth import SecurityMiddleware

app.add_middleware(SecurityMiddleware)
```

### 10. Cookie Management

```python
from mdb_engine.auth import (
    set_auth_cookies,
    clear_auth_cookies,
    get_secure_cookie_settings
)
from fastapi.responses import Response

@app.post("/login")
async def login(response: Response, credentials: dict):
    # Authenticate user
    user = await authenticate_user(credentials)

    # Generate tokens
    access_token, refresh_token = generate_token_pair(
        user_id=user["_id"],
        user_email=user["email"],
        secret_key=SECRET_KEY
    )

    # Set secure cookies
    set_auth_cookies(
        response=response,
        access_token=access_token,
        refresh_token=refresh_token
    )

    return {"status": "logged_in"}

@app.post("/logout")
async def logout(response: Response):
    clear_auth_cookies(response)
    return {"status": "logged_out"}
```

## API Reference

### JWT Functions

- `encode_jwt_token(payload, secret_key, expires_in=None)` - Encode JWT token
- `decode_jwt_token(token, secret_key)` - Decode JWT token
- `generate_token_pair(user_id, user_email, secret_key)` - Generate access + refresh token pair
- `extract_token_metadata(payload)` - Extract metadata from token payload

### FastAPI Dependencies

- `get_current_user(request, token=None)` - Get current authenticated user
- `require_admin(request)` - Require admin role
- `require_admin_or_developer(request)` - Require admin or developer role
- `require_permission(subject, resource, action, authz_provider)` - Check permission
- `get_authz_provider(request)` - Get authorization provider
- `get_token_blacklist(request)` - Get token blacklist
- `get_session_manager(request)` - Get session manager

### Authorization Providers

- `AuthorizationProvider` - Protocol interface for authorization providers
- `CasbinAdapter(enforcer)` - Casbin authorization adapter
- `OsoAdapter(oso)` - OSO authorization adapter

### Session Management

- `SessionManager(db, collection_name)` - Session manager class
  - `create_session(user_id, device_id, refresh_jti, device_info)` - Create new session
  - `update_activity(session_id, device_id)` - Update session activity
  - `get_user_sessions(user_id)` - Get all sessions for user
  - `revoke_session(session_id)` - Revoke a session
  - `revoke_all_user_sessions(user_id)` - Revoke all user sessions

### Token Management

- `TokenBlacklist(db, collection_name)` - Token blacklist class
  - `add_token(jti, expires_at)` - Blacklist a token
  - `is_blacklisted(jti)` - Check if token is blacklisted
  - `cleanup_expired()` - Remove expired tokens

### Token Lifecycle

- `get_token_expiry_time(payload)` - Get token expiry time
- `is_token_expiring_soon(payload, threshold_seconds)` - Check if token expires soon
- `should_refresh_token(payload)` - Check if token should be refreshed
- `refresh_access_token(refresh_token, secret_key, db)` - Refresh access token
- `get_token_age(payload)` - Get token age in seconds
- `get_time_until_expiry(payload)` - Get time until expiry

### Sub-Authentication

- `create_app_user(db, app_slug, email, password, **kwargs)` - Create app user
- `authenticate_app_user(db, app_slug, email, password)` - Authenticate app user
- `get_app_user(db, app_slug, user_id)` - Get app-level user
- `get_or_create_anonymous_user(db, app_slug, device_id)` - Get/create anonymous user
- `get_or_create_demo_user(db, app_slug, device_id)` - Get/create demo user

### Shared Auth (SSO)

- `SharedUserPool(mongo_db, jwt_secret, jwt_public_key, jwt_algorithm, ...)` - Shared user pool for SSO
  - `create_user(email, password, app_roles)` - Create user in shared pool
  - `authenticate(email, password, ip_address, fingerprint, session_binding)` - Authenticate with session binding
  - `validate_token(token)` - Validate JWT and get user (checks blacklist)
  - `revoke_token(token, reason)` - Revoke token by adding JTI to blacklist
  - `revoke_all_user_tokens(user_id, reason)` - Revoke all user tokens
  - `update_user_roles(email, app_slug, roles)` - Update user's roles for an app
  - `user_has_role(user, app_slug, role)` - Check if user has role
  - `get_secure_cookie_config(request)` - Get secure cookie settings
  - `jwt_algorithm` - Property: configured algorithm (HS256, RS256, ES256)
  - `is_asymmetric` - Property: True if using asymmetric algorithm
- `JWTSecretError` - Raised when JWT secret is missing
- `JWTKeyError` - Raised when JWT key configuration is invalid
- `SharedAuthMiddleware` - ASGI middleware for shared auth (supports session binding)
- `create_shared_auth_middleware(pool, slug, manifest_auth)` - Factory for configured middleware
- `create_shared_auth_middleware_lazy(slug, manifest_auth)` - Lazy factory for engine integration

### Rate Limiting

- `AuthRateLimitMiddleware(app, limits, store)` - ASGI rate limiting middleware
- `RateLimit(max_attempts, window_seconds)` - Rate limit configuration
- `InMemoryRateLimitStore` - In-memory storage for rate limiting
- `MongoDBRateLimitStore(db)` - MongoDB-backed distributed rate limiting
- `create_rate_limit_middleware(manifest_auth, store)` - Factory from manifest config
- `@rate_limit(max_attempts, window_seconds)` - Decorator for individual endpoints

### Audit Logging

- `AuthAuditLog(mongo_db, retention_days=90)` - Audit logger for auth events
  - `log_event(action, success, user_email, ip_address, details)` - Log any event
  - `log_login_success(email, ip_address, ...)` - Log successful login
  - `log_login_failed(email, reason, ip_address, ...)` - Log failed login
  - `log_logout(email, ip_address, ...)` - Log logout
  - `log_register(email, ip_address, ...)` - Log registration
  - `log_role_change(email, app_slug, old_roles, new_roles, ...)` - Log role change
  - `log_token_revoked(email, reason, ...)` - Log token revocation
  - `get_recent_events(hours, action, success)` - Query recent events
  - `get_failed_logins(email, ip_address, hours)` - Query failed logins
  - `get_security_summary(hours)` - Get security statistics
- `AuthAction` - Enum of audit action types

### CSRF Protection

- `CSRFMiddleware(app, secret, exempt_routes, ...)` - CSRF protection middleware
- `create_csrf_middleware(manifest_auth)` - Factory from manifest config
- `generate_csrf_token(secret)` - Generate CSRF token
- `validate_csrf_token(token, secret, max_age)` - Validate CSRF token
- `get_csrf_token(request)` - FastAPI dependency for getting CSRF token

### Password Policy

- `validate_password_strength(password, config)` - Validate password strength
- `validate_password_strength_async(password, config, check_breaches)` - Async validation with breach check
- `calculate_password_entropy(password)` - Calculate entropy in bits
- `is_common_password(password)` - Check against common password list
- `check_password_breach(password)` - Check against HaveIBeenPwned (async)

### Utilities

- `login_user(db, email, password, response)` - Login user and set cookies
- `register_user(db, email, password, **kwargs)` - Register new user
- `logout_user(response)` - Logout user and clear cookies

## Decorators

### `@require_auth`

Require authentication for a route:

```python
from mdb_engine.auth import require_auth

@app.post("/data")
@require_auth
async def create_data(user: dict = Depends(get_current_user)):
    return {"created_by": user["user_id"]}
```

### `@token_security`

Add token security checks:

```python
from mdb_engine.auth import token_security

@app.post("/sensitive")
@token_security
async def sensitive_operation(user: dict = Depends(get_current_user)):
    return {"data": "sensitive"}
```

### `@rate_limit_auth`

Rate limit authentication endpoints:

```python
from mdb_engine.auth import rate_limit_auth

@app.post("/login")
@rate_limit_auth(max_attempts=5, window_seconds=300)
async def login(credentials: dict):
    return await authenticate(credentials)
```

## Best Practices

1. **Always use HTTPS in production** - JWT tokens should only be transmitted over HTTPS
2. **Set strong SECRET_KEY (Required)** - The engine requires `FLASK_SECRET_KEY` to be set. Use a cryptographically secure random key (minimum 32 characters). The engine will fail to start if this is not configured.
3. **Use token blacklisting** - Implement token revocation for logout and security
4. **Monitor sessions** - Track active sessions and clean up inactive ones
5. **Implement refresh tokens** - Use refresh tokens for long-lived sessions
6. **Validate permissions** - Always check permissions before sensitive operations
7. **Use secure cookies** - Enable HttpOnly, Secure, and SameSite flags
8. **Handle token expiration** - Implement automatic token refresh
9. **Log authentication events** - Track login, logout, and permission failures
10. **Use sub-authentication** - Isolate app-level users for multi-tenant applications

## Enterprise Security Features

MDB_ENGINE auth includes enterprise-grade security features for production deployments.

### JWT Secret Validation (Fail-Fast)

The `SharedUserPool` now **requires** a JWT secret - it will fail at startup if not configured:

```python
from mdb_engine.auth import SharedUserPool, JWTSecretError

# Production: Requires MDB_ENGINE_JWT_SECRET env var or explicit secret
try:
    pool = SharedUserPool(db)  # Will raise JWTSecretError if no secret
except JWTSecretError:
    print("Set MDB_ENGINE_JWT_SECRET environment variable!")

# Development: Use allow_insecure_dev for local testing (NOT for production!)
pool = SharedUserPool(db, allow_insecure_dev=True)  # Auto-generates ephemeral secret
```

**Generate a secure secret:**
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Token Revocation (JTI)

Tokens now include a unique JWT ID (JTI) that enables server-side revocation:

```python
# Revoke a specific token (e.g., on logout)
await user_pool.revoke_token(token, reason="logout")

# Token is now blacklisted and will fail validation
user = await user_pool.validate_token(token)  # Returns None

# Revoke all user tokens (e.g., password change)
await user_pool.revoke_all_user_tokens(user_id, reason="password_change")
```

**How it works:**
- Each token contains a `jti` (JWT ID) claim
- `revoke_token()` adds the JTI to a blacklist collection
- `validate_token()` checks the blacklist before accepting the token
- Blacklist entries auto-expire via MongoDB TTL index

### Rate Limiting

Protect auth endpoints from brute-force attacks with built-in rate limiting:

**Manifest Configuration:**
```json
{
  "auth": {
    "mode": "shared",
    "rate_limits": {
      "/login": {"max_attempts": 5, "window_seconds": 300},
      "/register": {"max_attempts": 3, "window_seconds": 3600}
    }
  }
}
```

**Programmatic Usage:**
```python
from mdb_engine.auth import AuthRateLimitMiddleware, RateLimit, rate_limit

# Via middleware (auto-configured by engine.create_app())
app.add_middleware(
    AuthRateLimitMiddleware,
    limits={"/login": RateLimit(max_attempts=5, window_seconds=300)}
)

# Via decorator
@app.post("/login")
@rate_limit(max_attempts=5, window_seconds=300)
async def login(request: Request):
    ...
```

**Features:**
- IP + email tracking for granular rate limiting
- Sliding window algorithm
- In-memory (single instance) or MongoDB (distributed) storage
- 429 responses with `Retry-After` header
- Automatic reset on successful login

### Audit Logging

Comprehensive audit trail for authentication events:

**Manifest Configuration:**
```json
{
  "auth": {
    "audit": {
      "enabled": true,
      "retention_days": 90
    }
  }
}
```

**Programmatic Usage:**
```python
from mdb_engine.auth import AuthAuditLog, AuthAction

audit = AuthAuditLog(db, retention_days=90)
await audit.ensure_indexes()

# Log authentication events
await audit.log_login_success(email="user@example.com", ip_address="192.168.1.1")
await audit.log_login_failed(email="user@example.com", reason="invalid_password")
await audit.log_logout(email="user@example.com")
await audit.log_register(email="new@example.com")
await audit.log_role_change(email="user@example.com", app_slug="my_app", 
                            old_roles=["viewer"], new_roles=["editor"])

# Query audit logs
failed_logins = await audit.get_failed_logins(email="user@example.com", hours=24)
user_activity = await audit.get_user_activity(email="user@example.com", hours=168)
summary = await audit.get_security_summary(hours=24)
```

**Audit Actions:**
| Action | Description |
|--------|-------------|
| `login_success` | Successful login |
| `login_failed` | Failed login attempt |
| `logout` | User logout |
| `register` | New user registration |
| `token_revoked` | Token was revoked |
| `role_granted` | User received new role |
| `role_revoked` | User role was removed |
| `rate_limit_exceeded` | Rate limit was hit |

### Secure Cookies

Auto-configured secure cookie settings based on environment:

```python
# Get secure cookie config (auto-detects HTTPS/production)
cookie_config = user_pool.get_secure_cookie_config(request)
response.set_cookie(value=token, **cookie_config)
```

**Cookie settings by environment:**
| Setting | Development | Production |
|---------|-------------|------------|
| `httponly` | True | True |
| `secure` | False | True |
| `samesite` | lax | strict |

### Security Checklist

Before deploying to production:

- [ ] `MDB_ENGINE_JWT_SECRET` is set to a secure, unique value
- [ ] Rate limiting is configured for `/login` and `/register`
- [ ] Audit logging is enabled
- [ ] HTTPS is enforced (cookie `secure` flag)
- [ ] Token expiry is appropriately short (default: 24h)
- [ ] Logout endpoints call `revoke_token()`

## Advanced Security Features

### CSRF Protection

CSRF protection is **auto-enabled for shared auth mode**. The middleware uses the double-submit cookie pattern.

**Manifest Configuration:**
```json
{
  "auth": {
    "mode": "shared",
    "csrf_protection": true
  }
}
```

**Advanced Configuration:**
```json
{
  "auth": {
    "csrf_protection": {
      "enabled": true,
      "exempt_routes": ["/api/*"],
      "rotate_tokens": false,
      "token_ttl": 3600
    }
  }
}
```

**How it works:**
1. GET requests receive a CSRF token in a cookie
2. POST/PUT/DELETE must include the token in `X-CSRF-Token` header
3. Token is validated using constant-time comparison
4. SameSite=Lax cookies provide additional protection

**Frontend Integration:**

Helper function for reading cookies:

```javascript
// Reusable cookie helper
function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
    return null;
}
```

Include in all state-changing requests:

```javascript
// POST request with CSRF token
async function createItem(data) {
    const response = await fetch('/api/items', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRF-Token': getCookie('csrf_token')
        },
        credentials: 'same-origin',
        body: JSON.stringify(data)
    });
    return response.json();
}

// DELETE request with CSRF token
async function deleteItem(id) {
    const response = await fetch(`/api/items/${id}`, {
        method: 'DELETE',
        headers: {
            'X-CSRF-Token': getCookie('csrf_token')
        },
        credentials: 'same-origin'
    });
    return response.json();
}
```

**Logout Must Be POST:**

For security, logout endpoints should use POST method, not GET:

```javascript
// Correct: POST with CSRF token
async function logout() {
    const response = await fetch('/logout', {
        method: 'POST',
        headers: {
            'X-CSRF-Token': getCookie('csrf_token')
        },
        credentials: 'same-origin'
    });
    
    const result = await response.json();
    if (result.success) {
        window.location.href = result.redirect || '/login';
    }
}
```

Backend endpoint:

```python
@app.post("/logout")
async def logout(request: Request):
    """Logout must be POST with CSRF token."""
    response = JSONResponse({"success": True, "redirect": "/login"})
    response = await logout_user(request, response)
    return response
```

**Login/Register JSON Pattern:**

Return JSON responses for AJAX forms:

```python
@app.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    """Login returning JSON for JavaScript frontend."""
    result = await authenticate_user(email, password)
    
    if result["success"]:
        json_response = JSONResponse({"success": True, "redirect": "/dashboard"})
        # Copy auth cookies from result
        for key, value in result["response"].headers.items():
            if key.lower() == "set-cookie":
                json_response.headers.append(key, value)
        return json_response
    
    return JSONResponse(
        {"success": False, "detail": result.get("error", "Login failed")},
        status_code=401
    )
```

**Error Handling:**

Handle CSRF validation failures (403 status):

```javascript
async function secureRequest(url, options = {}) {
    const response = await fetch(url, {
        ...options,
        headers: {
            ...options.headers,
            'X-CSRF-Token': getCookie('csrf_token')
        },
        credentials: 'same-origin'
    });
    
    if (response.status === 403) {
        const data = await response.json();
        if (data.detail?.includes('CSRF')) {
            // Token expired - refresh the page
            window.location.reload();
            return null;
        }
    }
    
    return response;
}
```

### HSTS (HTTP Strict Transport Security)

HSTS forces HTTPS connections in production, protecting against protocol downgrade attacks.

**Manifest Configuration:**
```json
{
  "auth": {
    "security": {
      "hsts": {
        "enabled": true,
        "max_age": 31536000,
        "include_subdomains": true,
        "preload": false
      }
    }
  }
}
```

**Header Output:**
```
Strict-Transport-Security: max-age=31536000; includeSubDomains
```

**Note:** Only enable `preload` if you're ready for permanent HTTPS commitment.

### JWT Algorithm Support

MDB_ENGINE supports multiple JWT signing algorithms for different security requirements.

| Algorithm | Type | Key | Use Case |
|-----------|------|-----|----------|
| HS256 | Symmetric | Shared secret | Default, simple deployments |
| RS256 | Asymmetric | RSA key pair | Microservices, token verification by multiple parties |
| ES256 | Asymmetric | ECDSA key pair | Modern alternative to RSA, smaller keys |

**Manifest Configuration:**
```json
{
  "auth": {
    "jwt": {
      "algorithm": "RS256",
      "token_expiry_hours": 24
    }
  }
}
```

**Environment Variables:**
```bash
# For HS256 (symmetric)
export MDB_ENGINE_JWT_SECRET="your-secret-key"

# For RS256/ES256 (asymmetric)
export MDB_ENGINE_JWT_SECRET="-----BEGIN RSA PRIVATE KEY-----..."
export MDB_ENGINE_JWT_PUBLIC_KEY="-----BEGIN PUBLIC KEY-----..."
```

**Programmatic Usage:**
```python
pool = SharedUserPool(
    db,
    jwt_secret=private_key,
    jwt_public_key=public_key,
    jwt_algorithm="RS256"
)

# Check algorithm
print(pool.jwt_algorithm)  # "RS256"
print(pool.is_asymmetric)  # True
```

### Password Policy

Configurable password strength requirements with entropy calculation and breach detection.

**Manifest Configuration:**
```json
{
  "auth": {
    "password_policy": {
      "min_length": 12,
      "min_entropy_bits": 50,
      "require_uppercase": true,
      "require_lowercase": true,
      "require_numbers": true,
      "require_special": false,
      "check_common_passwords": true,
      "check_breaches": false
    }
  }
}
```

**Programmatic Usage:**
```python
from mdb_engine.auth import (
    validate_password_strength,
    validate_password_strength_async,
    calculate_password_entropy,
    is_common_password,
)

# Synchronous validation
is_valid, errors = validate_password_strength(
    "MyPassword123",
    config=manifest.get("auth", {}).get("password_policy", {})
)

# Async validation with breach check
is_valid, errors = await validate_password_strength_async(
    "MyPassword123",
    config=config,
    check_breaches=True  # Queries HaveIBeenPwned
)

# Calculate entropy
entropy = calculate_password_entropy("MyP@ssw0rd!")
print(f"Entropy: {entropy} bits")  # ~65 bits

# Check common passwords
if is_common_password("password123"):
    print("Password is too common!")
```

**Entropy Guidelines:**
| Entropy (bits) | Strength | Example |
|----------------|----------|---------|
| < 28 | Very Weak | "password" |
| 28-35 | Weak | "Password1" |
| 36-59 | Fair | "P@ssw0rd" |
| 60-127 | Strong | "MyS3cur3P@ss!" |
| 128+ | Very Strong | Random 20+ char |

### Session Binding

Tie sessions to client characteristics to prevent session hijacking.

**Manifest Configuration:**
```json
{
  "auth": {
    "session_binding": {
      "bind_ip": false,
      "bind_fingerprint": true,
      "allow_ip_change_with_reauth": true
    }
  }
}
```

| Setting | Default | Description |
|---------|---------|-------------|
| `bind_ip` | false | Strict: reject if client IP changes |
| `bind_fingerprint` | true | Soft: log warning if fingerprint changes |
| `allow_ip_change_with_reauth` | true | Allow IP change if user re-authenticates |

**How it works:**
1. On login, IP and/or fingerprint are embedded in JWT claims
2. Middleware validates these claims on each request
3. IP binding = strict (rejects on mismatch)
4. Fingerprint binding = soft (logs warning, useful for security monitoring)

**Token Claims:**
```json
{
  "sub": "user123",
  "email": "user@example.com",
  "ip": "192.168.1.100",
  "fp": "sha256-of-browser-fingerprint",
  "jti": "unique-token-id",
  "exp": 1234567890
}
```

## Security Considerations

- **Token Storage**: Store tokens in secure, HttpOnly cookies (not localStorage)
- **CSRF Protection**: Auto-enabled for shared auth mode with double-submit cookie pattern
- **Password Hashing**: Always hash passwords using bcrypt (built-in)
- **Password Policy**: Enforce entropy requirements and check common passwords
- **Rate Limiting**: Configure via manifest for `/login` and `/register`
- **Token Expiration**: Use short-lived access tokens (default: 24h)
- **Token Blacklisting**: Revoke tokens on logout via JTI
- **Session Binding**: Bind sessions to IP/fingerprint for hijacking protection
- **HSTS**: Force HTTPS in production
- **Audit Logging**: Track all auth events for forensics

## Integration with MongoDBEngine

The auth module integrates seamlessly with MongoDBEngine:

```python
from mdb_engine import MongoDBEngine
from mdb_engine.auth import setup_auth_from_manifest

engine = MongoDBEngine(mongo_uri="...", db_name="...")
await engine.initialize()

manifest = await engine.load_manifest("manifest.json")
await engine.register_app(manifest)

# Setup auth from manifest
await setup_auth_from_manifest(engine, manifest)
```

## Related Modules

- **`core/`** - MongoDBEngine integration
- **`database/`** - Database access for user storage
- **`observability/`** - Logging and metrics for auth events
