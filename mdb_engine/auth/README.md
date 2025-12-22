# Authentication & Authorization Module

Comprehensive authentication and authorization system for MDB_ENGINE applications with JWT tokens, pluggable authorization providers, session management, and FastAPI integration.

## Features

- **JWT Token Management**: Access and refresh token pairs with automatic lifecycle management
- **Pluggable Authorization**: Support for Casbin and OSO authorization providers
- **FastAPI Integration**: Ready-to-use dependencies for authentication and authorization
- **Session Management**: Multi-device session tracking with activity monitoring
- **Token Blacklisting**: Secure token revocation and expiration handling
- **Sub-Authentication**: App-level user management with anonymous and demo user support
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
# Required: Secret key for JWT signing
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
    "strategy": "app_users"
  }
}
```

**Key Features:**
- **Auto-created provider**: Casbin provider is automatically created from manifest (default)
- **MongoDB-backed**: Policies stored in MongoDB collection (default: `casbin_policies`)
- **Sub-auth integration**: Sub-auth users automatically get Casbin roles assigned
- **Zero boilerplate**: Just configure in manifest, everything works automatically

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
    # - Creates Casbin provider with MongoDB adapter
    # - Sets up token management
    # - Links sub_auth users to Casbin roles
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
    # Check permission using auto-created Casbin provider
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

#### Casbin Adapter (Auto-Created from Manifest)

**Recommended**: Configure in manifest and let the system auto-create:

```json
{
  "auth_policy": {
    "provider": "casbin",
    "authorization": {
      "model": "rbac",
      "policies_collection": "casbin_policies"
    }
  }
}
```

The provider is automatically created and available via `get_authz_provider` dependency.

**Manual Setup** (if needed):

```python
from mdb_engine.auth import CasbinAdapter, create_casbin_enforcer

# Create enforcer with MongoDB adapter
enforcer = await create_casbin_enforcer(
    db=engine.get_database(),
    model="rbac",
    policies_collection="casbin_policies"
)

# Create adapter
authz_provider = CasbinAdapter(enforcer)

# Set on app state
app.state.authz_provider = authz_provider
```

#### OSO Adapter

```python
from mdb_engine.auth import OsoAdapter
from oso import Oso

# Create OSO instance
oso = Oso()
oso.load_file("policy.polar")

# Create adapter
authz_provider = OsoAdapter(oso)

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
    get_app_sub_user,
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

```python
from mdb_engine.auth import create_security_middleware, add_security_middleware

# Add security middleware to FastAPI app
middleware = create_security_middleware()
add_security_middleware(app, middleware)

# Or use SecurityMiddleware directly
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
- `get_app_sub_user(db, app_slug, user_id)` - Get app sub-user
- `get_or_create_anonymous_user(db, app_slug, device_id)` - Get/create anonymous user
- `get_or_create_demo_user(db, app_slug, device_id)` - Get/create demo user

### Utilities

- `login_user(db, email, password, response)` - Login user and set cookies
- `register_user(db, email, password, **kwargs)` - Register new user
- `logout_user(response)` - Logout user and clear cookies
- `validate_password_strength(password)` - Validate password strength

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
2. **Set strong SECRET_KEY** - Use a cryptographically secure random key
3. **Use token blacklisting** - Implement token revocation for logout and security
4. **Monitor sessions** - Track active sessions and clean up inactive ones
5. **Implement refresh tokens** - Use refresh tokens for long-lived sessions
6. **Validate permissions** - Always check permissions before sensitive operations
7. **Use secure cookies** - Enable HttpOnly, Secure, and SameSite flags
8. **Handle token expiration** - Implement automatic token refresh
9. **Log authentication events** - Track login, logout, and permission failures
10. **Use sub-authentication** - Isolate app-level users for multi-tenant applications

## Security Considerations

- **Token Storage**: Store tokens in secure, HttpOnly cookies (not localStorage)
- **CSRF Protection**: Use SameSite cookies and CSRF tokens for state-changing operations
- **Password Hashing**: Always hash passwords using bcrypt or similar
- **Rate Limiting**: Implement rate limiting on authentication endpoints
- **Token Expiration**: Use short-lived access tokens (1 hour) and longer refresh tokens (30 days)
- **Token Blacklisting**: Implement token blacklisting for immediate revocation
- **Session Management**: Limit concurrent sessions per user
- **Device Tracking**: Track devices for security monitoring

## Integration with RuntimeEngine

The auth module integrates seamlessly with RuntimeEngine:

```python
from mdb_engine import RuntimeEngine
from mdb_engine.auth import setup_auth_from_manifest

engine = RuntimeEngine(mongo_uri="...", db_name="...")
await engine.initialize()

manifest = await engine.load_manifest("manifest.json")
await engine.register_app(manifest)

# Setup auth from manifest
await setup_auth_from_manifest(engine, manifest)
```

## Related Modules

- **`core/`** - RuntimeEngine integration
- **`database/`** - Database access for user storage
- **`observability/`** - Logging and metrics for auth events

